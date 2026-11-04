#!/usr/bin/env python3
"""SessionStart (startup, resume, compact, fork) and UserPromptSubmit hook of the agent-skills plugin: tells an
orchestrator or a lead what it runs.

After an automatic compaction a lead kept draining its inbox but dropped a step it had run for hours
(removing each landed card), and eight cards stayed open. An orchestrator never loaded orchestrate for days
and answered progress questions by investigating on its own: it was judged by its terminal handle, which went stale
when the session resumed in another terminal, and the one injection it did get was buried under the next request.

A role is declared, never guessed from the terminal, and read in this order (roles.resolve, which every hook and
script shares; roles.session adds the worker and general roles this hook does not remind):
1. `agent_type` in the hook input: the session was started with `claude --agent agent-skills:orchestrator` or
   `--agent agent-skills:lead --name lead:<slug>`. Claude Code keeps it across resume, compact and fork, but leaves
   it out of SessionStart on resume and fork; there the session title (`--name`) stands in.
2. A prompt line `ROLE: orchestrator` or `ROLE: lead <slug>`, recorded in $AGENT_SKILLS_STATE/roles/<session_id>.json,
   for a session already running or a card whose launcher cannot pass `--agent`. `ROLE: none` silences the session,
   whatever started it. A fork gets a new session id and its input names no parent, so a fork of a session declared
   only this way is undeclared until it declares again.
3. Neither: no output.
A declared session gets the long paragraph at SessionStart and one line on every prompt. After a compaction the
paragraph also carries orchestrate's core rules as text: the skill is too large to come back with the compaction's own
re-injection, and the request to reload it went unanswered for weeks. An orchestrator whose session
title is not `orchestrator` is also told at SessionStart how to name it; a role visible in session lists is how the
human tells which session routes. Prompts run no external
command, so the hook stays fast.

On a prompt the line also carries one stage-table warning when the table is not keeping up with an active program
(the lead's own, or any for the orchestrator): no work row, or rows the fleet tick has not rewritten in the dashboard
config's `board.stage_warn_h` hours (stage_auto.warning). The Brief's stage table sat empty for weeks once while every
row waited on a hand-written `orch stage add`.

With `reflect.orchestrator_feedback` true in ~/.claude/agent-skills.json, an orchestrator session's transcript path is
registered in $AGENT_SKILLS_STATE/transcripts/<session_id>.json with the span it held the role (`since`, `until` once
`ROLE: none` clears it), so standing reflect reads the owner's corrections from that span only and never globs
~/.claude/projects. Off, nothing is written. The span lives apart from the roles file so the role logic above stays
the same whether or not the feature is on.

At SessionStart an interactive session in an Orca terminal records terminal -> session (roles.record_terminal), which
`skills-sync wake-target` reads to name the session behind a handle.
"""
import datetime, json, os, pathlib, re, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import roles  # noqa: E402  the role rules above live there, shared with every hook and script

HOME = pathlib.Path(os.environ.get("PROGRAMS_HOME", "~/.claude/programs")).expanduser()
STATE = roles.state()
TRANSCRIPTS = STATE / "transcripts"
CONFIG = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG", "~/.claude/agent-skills.json")).expanduser()
SESSION = roles.SESSION
declared, declare, resolve = roles.declared, roles.declare, roles.resolve
SKILL = pathlib.Path(__file__).resolve().parents[1] / "skills" / "orchestrate" / "SKILL.md"
# Core rules 1 and 2 of SKILL.md in one line; the per-prompt reminder must never say less than they do.
DELEGATE = ("Hand each instruction one layer down at once (a worker, a card, a background subagent); your own turn "
            "holds coordination, the acts only the human's session may take, answers from context and one `--json` "
            "read, and any other task work is a delegation.")


def program_for(slug):
    for path in HOME.glob("*/program.json"):
        try:
            cfg = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if cfg.get("slug") == slug:
            return cfg
    return {}


def program_message(slug, cfg, compacted):
    note = f", read the program note {cfg['note']}" if cfg.get("note") else ""
    lead = "Your context was just compacted, and you coordinate" if compacted else "You coordinate"
    rules = " Rules you followed before the compaction may be gone from your context." if compacted else ""
    of_run = f" (Orca Run {cfg['run']})" if cfg.get("run") else ""
    return (f"{lead} the program {slug}{of_run}.{rules} Before anything else: "
            f"load the agent-skills:orchestrate skill{note}, run `orch status {slug}` and act on every line it prints "
            f"(each LANDED-BUT-OPEN line is a card to close out). Drain only with `orch wait {slug}`: it prints what "
            f"to do with each worker_done's card.")


def core_rules(path=None):
    """The numbered "Core rules" list at the top of orchestrate's SKILL.md, verbatim with its wrapped lines; "" when
    it cannot be read."""
    try:
        lines = (path or SKILL).read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return ""
    start = next((i for i, line in enumerate(lines) if line.startswith("Core rules")), None)
    if start is None:
        return ""
    rules = []
    for line in lines[start + 1:]:
        if not line.strip():
            if rules:
                break
            continue
        if re.match(r"\d+\. ", line) or (rules and line[:1] in " \t"):
            rules.append(line)
        else:
            break
    return "\n".join(rules)


def with_core_rules(text):
    """A compaction drops the skill body, so its core rules come back as text, not as a request to reload it."""
    rules = core_rules()
    if not rules:
        return text
    return (f"{text}\nThese core rules of agent-skills:orchestrate hold now, before you reload it:\n{rules}")


def role_long(entry, compacted):
    if entry["role"] == "lead":
        slug = entry.get("slug")
        if not slug:
            return ("You are a lead (agent-skills:lead), but no program is named: ask the human for its slug, then "
                    "declare it with a prompt line `ROLE: lead <slug>`. Load agent-skills:orchestrate in program mode.")
        text = program_message(slug, program_for(slug), compacted)
        return with_core_rules(text) if compacted else text
    if compacted:
        return with_core_rules("Your context was just compacted. You are the top-level orchestrator. Load "
                               "agent-skills:orchestrate (references/top-level.md) for any step these rules do not "
                               "cover.")
    return ("You are the top-level orchestrator. Load agent-skills:orchestrate now. " + DELEGATE + " Status reports "
            "go through agent-skills:brief.")


def name_check(event):
    """At SessionStart: the orchestrator's name, for the prompt bar and the Orca tab, when it is not set yet."""
    if event.get("session_title") == "orchestrator":
        return ""
    tab = (" and name this Orca tab: `orca terminal rename --terminal $ORCA_TERMINAL_HANDLE --title orchestrator`"
           if os.environ.get("ORCA_TERMINAL_HANDLE") else "")
    return (" This session is not named `orchestrator`: ask the human to run `/rename orchestrator`, so the role shows "
            f"on the prompt bar and in session lists{tab}.")


def role_line(entry):
    if entry["role"] == "lead" and entry.get("slug"):
        slug = entry["slug"]
        note = program_for(slug).get("note")
        brief = f"; program note {note}" if note else ""
        return (f"ROLE: lead {slug}. Run agent-skills:orchestrate in program mode for {slug} (load it now if it is not "
                f"in your context){brief}; keep one `orch wait {slug}` running. {DELEGATE}")
    if entry["role"] == "lead":
        return role_long(entry, False)
    return ("ROLE: orchestrator. Run agent-skills:orchestrate as the top-level orchestrator (load it now if it is not "
            f"in your context). {DELEGATE} Status reports go through agent-skills:brief.")


def stage_warning(entry):
    """stage_auto.warning for this role, "" when there is nothing to say or it cannot tell."""
    if entry["role"] == "lead" and not entry.get("slug"):
        return ""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "skills" / "orchestrate" / "scripts"))
    try:
        import stage_auto
        return stage_auto.warning(entry.get("slug"))
    except Exception:  # the reminder still goes out without it
        return ""


def feedback_on():
    try:
        return json.loads(CONFIG.read_text()).get("reflect", {}).get("orchestrator_feedback") is True
    except (OSError, ValueError, AttributeError):
        return False


def register(event, session, entry, record):
    """Keeps the orchestrator span of this session's transcript for standing reflect, when the feature is on."""
    path = event.get("transcript_path")
    if not isinstance(path, str) or not path or not feedback_on():
        return
    file = TRANSCRIPTS / f"{session}.json"
    try:
        old = json.loads(file.read_text())
    except (OSError, ValueError):
        old = None
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if entry and entry["role"] == "orchestrator":
        # A span reopened after `ROLE: none` starts again now: the conversation in between was not the orchestrator's.
        since = old["since"] if old and old.get("since") and not old.get("until") else now
        new = {"role": "orchestrator", "transcript": path, "since": since, "until": None}
    elif old and not old.get("until") and ((record and record.get("role") == "none") or entry):
        # Cleared, or now another role. A prompt of a session named only by its title resolves to nothing and keeps
        # the span open.
        new = {**old, "until": now}
    else:
        return
    if new != old:
        TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(new))


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    kind, session = event.get("hook_event_name"), str(event.get("session_id") or "")
    if not SESSION.fullmatch(session):
        return 0
    text = None
    if kind == "UserPromptSubmit":
        had = declared(session)
        try:
            record = declare(session, event.get("prompt"))
        except OSError:
            record = had
        entry = resolve(event, record)
        try:
            register(event, session, entry, record)
        except OSError:
            pass
        if record and record.get("role") == "none":
            if had != record and resolve(event, had):
                text = "ROLE cleared: this session no longer gets the orchestrate reminder."
        elif entry and record and record != had:
            text = role_long(entry, False) + " `ROLE: none` in a prompt clears the role."
        elif entry:
            text = role_line(entry)
        if text and entry and (warn := stage_warning(entry)):
            text += " " + warn
    elif kind == "SessionStart" and event.get("source") in ("startup", "resume", "compact", "fork"):
        # A `claude -p` run from a card's shell inherits the handle and is not the terminal's session.
        if os.environ.get("CLAUDE_CODE_ENTRYPOINT") == "cli":
            try:
                roles.record_terminal(os.environ.get("ORCA_TERMINAL_HANDLE"), session, event.get("cwd"), time.time())
            except OSError:
                pass
        record = declared(session)
        entry = resolve(event, record)
        try:
            register(event, session, entry, record)
        except OSError:
            pass
        if entry:
            text = role_long(entry, event.get("source") == "compact")
            if entry["role"] == "orchestrator":
                text += name_check(event)
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": kind, "additionalContext": text}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
