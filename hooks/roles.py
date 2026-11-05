"""Who makes a hook call and in which role: the one place hooks and scripts of this plugin read it.

Role, one of ROLES (orchestrate's role contract):
1. A `ROLE: none` record turns every role off: the session is `general`, whatever started it.
2. A declared role comes first: `agent_type` (`claude --agent agent-skills:orchestrator|lead`), else the
   `ROLE: orchestrator|lead <slug>` record reorient.py keeps per session, else (SessionStart only) the session title
   (`orchestrator`, `lead:<slug>`), else a first prompt that is a lead's brief (a MODE line, a `ROLE: lead <slug>`
   line or the template's `<project>: lead` head). A lead Orca dispatched is still a lead.
3. Undeclared, a session whose first prompt carries Orca's Dispatch preamble, or a card's CLOSE or KEEP row, is a
   `worker`; a `/goal` card is a worker as much as a dispatched one.
4. Anything else is `general`. A session a human opened never becomes a worker.

Supervision (the Dispatch preamble), CLOSE and KEEP are attributes of the Session, not roles. Orca's execution depth
and a worktree's lineage are neither: a `/goal` card lead is depth 0 of its own Run, so nothing here reads them.

Actor: `subagent` when the input has an `agent_id`, else `main`. A subagent shares its parent's session id and
transcript, so it gets the parent's role; `applies` decides which rules reach it.
"""
import dataclasses, hashlib, json, os, pathlib, re

ROLES =("orchestrator", "lead", "worker", "general")
SESSION = re.compile(r"[\w-]{1,128}")
# A prompt line reorient.py records; the last one in a prompt wins.
ROLE_LINE = re.compile(r"^[ \t]*ROLE:?[ \t]+(orchestrator|lead[ \t]+([\w.-]+)|none)[ \t]*$", re.M)
LEAD_TITLE = re.compile(r"lead:([\w.-]+)")
AGENTS = {"agent-skills:orchestrator": "orchestrator", "agent-skills:lead": "lead"}

# A lead's brief. The head is a line of its own, or opens the prompt; "take the lead" or "(lead: x)" in a worker's
# text is neither. LEAD_ROLE is a whole line, the prompt's closing quote (or a transcript's `</command-args>`) allowed
# after it; the template's unfilled `<slug>`, `ROLE: none`, trailing words and a `\n` escape do not count.
MODE = re.compile(r"""(?m)(?:^|--prompt[\s=]+["']?)\s*MODE\b""")
LEAD_HEAD = re.compile(r"""(?m)(?:^|--prompt[\s=]+)\s*["']?[\w.-]+:\s*lead\s*(?:["']|$)""")
LEAD_ROLE = re.compile(r"""(?m)(?:^|--prompt[\s=]+["']?)[ \t]*ROLE:?[ \t]+lead[ \t]+([\w.-]+)[ \t]*(?:$|["']|</command-args>)""")
# The opening Orca writes into every dispatched worker's first prompt.
PREAMBLE = re.compile(r"You are working inside Orca, a multi-agent IDE\.\s+You are a dispatched worker\b")
# A row as scripts/card.py reads it: a colon or the template's column gap, so "KEEP the old API" in prose is not one.
CARD_ROW = re.compile(r"(?m)^[ \t]*`?(CLOSE|KEEP)`?(?:[ \t]*:|[ \t]{2,}\S)")

# Rule -> (actors, roles) it applies to. A rule not listed applies to nobody.
MATRIX = {
    "danger": ({"main", "subagent"}, set(ROLES)),
    "ask_user_question": ({"main"}, {"orchestrator", "lead", "worker"}),
    "wait_foreground": ({"main"}, {"orchestrator", "lead"}),
    "send_devnull": ({"main"}, {"orchestrator", "lead", "worker"}),
    "worker_only": ({"main"}, {"worker"}),
    "exit_order": ({"main"}, set(ROLES)),  # lifecycle.py also needs a Dispatch, CLOSE or KEEP on the card
    "agent_forbidden": ({"main", "subagent"}, {"orchestrator", "lead", "worker"}),
    "decision_relay": ({"main"}, {"orchestrator", "lead"}),
    "deliver_load": ({"main"}, {"worker"}),
}


@dataclasses.dataclass(frozen=True)
class Session:
    role: str = "general"
    actor: str = "main"
    slug: str = None
    declared: bool = False
    supervised: bool = False
    close: bool = False
    keep: bool = False


def state():
    return pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser()


def roles_dir():
    """Where reorient.py records each session's ROLE line, one <session id>.json per session."""
    return state() / "roles"


def terminal_file(handle):
    return state() / "terminals" / f"{handle}.json"


def record_terminal(handle, session, cwd, now, loaded=None, same_process=False):
    """Ties an Orca terminal to the session that just started in it and returns the record it replaced. Orca's own
    data has no session id, and a cwd match can name another session in the same worktree. `loaded` (loaded_fields)
    is what that session's process read of the plugin when it started; None keeps what the same session recorded
    before (a compaction reloads nothing). same_process: a /clear, which gives the process a new session id and
    reloads nothing either, so the previous session's fields carry over."""
    if not SESSION.fullmatch(handle or "") or not SESSION.fullmatch(session or ""):
        return None
    path = terminal_file(handle)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"handle": handle, "session_id": session, "cwd": cwd, "at": now}
    try:
        old = read_terminal(handle)
    except (OSError, ValueError):
        old = None
    if loaded is None:
        keep = old and (same_process or old.get("session_id") == session)
        loaded = {k: old[k] for k in LOADED_KEYS if k in old} if keep else {}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({**entry, **loaded}))
    tmp.replace(path)
    return old


def carry_role(old_session, new_session):
    """A /clear starts a new session id in the same process: its ROLE record carries over, unless the new session
    already has one. Returns the record now in force for new_session, or None."""
    have = declared(new_session)
    if have is not None or not SESSION.fullmatch(new_session or ""):
        return have
    record = declared(old_session)
    if record is None:
        return None
    roles_dir().mkdir(parents=True, exist_ok=True)
    (roles_dir() / f"{new_session}.json").write_text(json.dumps(record))
    return record


LOADED_KEYS = ("plugin_version", "hooks_sha")


def read_terminal(handle):
    """The terminal's record, None when there is none or it is not a record. An unreadable one (permissions, bad JSON)
    raises OSError/ValueError: reload_state tells that from a missing record."""
    try:
        record = json.loads(terminal_file(handle).read_text())
    except FileNotFoundError:
        return None
    return record if isinstance(record, dict) else None


def hooks_sha(root):
    """sha256 of <plugin root>/hooks/hooks.json, None when it cannot be read."""
    try:
        return hashlib.sha256((pathlib.Path(root) / "hooks" / "hooks.json").read_bytes()).hexdigest()
    except (OSError, TypeError):
        return None


def loaded_fields(root):
    """What a session start records of the plugin it loaded: the manifest's version (None when it names none) and the
    hooks.json digest. Claude Code reads hooks.json once per process, so this is what the session runs until it is
    restarted."""
    try:
        version = json.loads((pathlib.Path(root) / ".claude-plugin" / "plugin.json").read_text()).get("version")
    except (OSError, ValueError, TypeError, AttributeError):
        version = None
    return {"plugin_version": version, "hooks_sha": hooks_sha(root)}


DIGEST = re.compile(r"[0-9a-f]{64}")


def reload_state(handles, current, sessions=None):
    """'stale' when a session recorded in one of the terminals loaded another hooks.json than `current`, 'ok' when
    every recorded one matches, 'unknown' when none recorded a digest (older than this record, or not an Orca
    terminal): never 'stale' on a missing record. 'unreadable' when a record exists and cannot be read.
    `sessions`, when given, is the set of session ids alive in the place being judged: a terminal record names the
    session that started last in it, so a record of any other session says nothing about the ones running now."""
    shas = []
    for h in handles:
        if not SESSION.fullmatch(h or ""):
            continue
        try:
            record = read_terminal(h)
        except (OSError, ValueError):
            return "unreadable"
        if not record:
            continue
        if sessions is not None and not (isinstance(record.get("session_id"), str) and record["session_id"] in sessions):
            continue
        if isinstance(record.get("hooks_sha"), str) and DIGEST.fullmatch(record["hooks_sha"]):
            shas.append(record["hooks_sha"])
    if not shas or not current:
        return "unknown"
    return "stale" if any(s != current for s in shas) else "ok"


def session_for(handle, rows):
    """(row, None) for the `claude agents --json` row of the session this terminal recorded, else (None, why). A record
    older than the row's process is stale: the session was resumed somewhere else."""
    if not SESSION.fullmatch(handle or ""):
        return None, "not a terminal handle"
    try:
        record = json.loads(terminal_file(handle).read_text())
        session, at = record["session_id"], record["at"]
    except (OSError, ValueError, KeyError, TypeError):
        return None, "no session record for this terminal"
    row = next((r for r in rows if isinstance(r, dict) and r.get("sessionId") == session), None)
    if row is None:
        return None, "its recorded session is not listed"
    if row.get("kind") != "interactive":
        return None, "its recorded session is not interactive"
    started = row.get("startedAt")
    if not isinstance(started, (int, float)) or not isinstance(at, (int, float)) or at < started / 1000:
        return None, "the record is older than the session"
    return row, None


def lead_text(text):
    """The text is a lead's brief or first prompt."""
    return bool(MODE.search(text) or LEAD_HEAD.search(text) or LEAD_ROLE.search(text))


def declared(session):
    """The session's ROLE record ({"role": …, "slug"?: …}), or None."""
    if not SESSION.fullmatch(session or ""):
        return None
    try:
        record = json.loads((roles_dir() / f"{session}.json").read_text())
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def declare(session, prompt):
    """Records the prompt's last ROLE line and returns the session's record; None when it never declared."""
    found = ROLE_LINE.findall(prompt or "")
    if not found:
        return declared(session)
    role, slug = found[-1]
    # "none" is kept, not deleted: it also turns off a role from --agent.
    entry = {"role": "lead", "slug": slug} if slug else {"role": role}
    roles_dir().mkdir(parents=True, exist_ok=True)
    (roles_dir() / f"{session}.json").write_text(json.dumps(entry))
    return entry


def title_role(title):
    """The role a session title names: `orchestrator` or `lead:<slug>`; None otherwise."""
    title = str(title or "")
    if title == "orchestrator":
        return {"role": "orchestrator"}
    if m := LEAD_TITLE.fullmatch(title):
        return {"role": "lead", "slug": m.group(1)}
    return None


def resolve(event, record):
    """The declared orchestrator or lead entry in force, or None: `agent_type`, else the ROLE record, else (SessionStart
    only) the session title. A `ROLE: none` record answers None."""
    if record and record.get("role") == "none":
        return None
    by_title = title_role(event.get("session_title"))
    role = AGENTS.get(event.get("agent_type"))
    if role == "lead":
        slug = by_title["slug"] if by_title and by_title["role"] == "lead" else (record or {}).get("slug")
        return {"role": "lead", "slug": slug}
    if role:
        return {"role": role}
    if record and record.get("role") in ("orchestrator", "lead"):
        return record
    if event.get("hook_event_name") == "SessionStart":
        return by_title
    return None


def declared_role(event):
    """resolve() on the event's own session record: the cheap check for a declared orchestrator or lead."""
    return resolve(event, declared(str(event.get("session_id") or "")))


def first_prompt(transcript):
    """The text of the transcript's first user prompt, "" when there is none or it cannot be read."""
    if not isinstance(transcript, str) or not transcript:
        return ""
    try:
        with open(transcript, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                if not isinstance(rec, dict) or rec.get("type") != "user" or rec.get("isMeta"):
                    continue
                content = (rec.get("message") or {}).get("content")
                if isinstance(content, list):  # tool results carry no text block
                    content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
                if isinstance(content, str) and content:
                    return content
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return ""


def session(event, prompt=None):
    """The Session of a hook event. `prompt` stands in for the transcript's first prompt when given."""
    actor = "subagent" if event.get("agent_id") else "main"
    record = declared(str(event.get("session_id") or ""))
    first = first_prompt(event.get("transcript_path")) if prompt is None else prompt
    # The card's own facts (a Dispatch, a CLOSE or KEEP row) hold whatever role the session declared or dropped.
    attrs = {"supervised": bool(PREAMBLE.search(first)), "close": False, "keep": False}
    for m in CARD_ROW.finditer(first):
        attrs[m.group(1).lower()] = True
    if record and record.get("role") == "none":
        return Session("general", actor, declared=True, **attrs)
    # A subagent's event carries its own agent_type, not its parent's: the parent's role comes from the rest.
    entry = resolve({k: v for k, v in event.items() if k != "agent_type"} if actor == "subagent" else event, record)
    if entry:
        return Session(entry["role"], actor, entry.get("slug"), True, **attrs)
    if lead_text(first):
        m = LEAD_ROLE.search(first)
        return Session("lead", actor, m.group(1) if m else None, True, **attrs)
    if attrs["supervised"] or attrs["close"] or attrs["keep"]:
        return Session("worker", actor, **attrs)
    return Session("general", actor)


def applies(rule, s):
    actors, roles = MATRIX.get(rule, (set(), set()))
    return s.actor in actors and s.role in roles
