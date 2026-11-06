#!/usr/bin/env python3
"""Keep this checkout in step with origin/main and tell running Claude sessions to reload it.

Usage:
  skills-sync sync [--no-broadcast]      fetch, fast-forward or push, then broadcast a needed reload
  skills-sync broadcast [--skills|--plugins] [--dry-run] [--terminal <handle>]
                                         send the pending (or the given) reload to idle Claude sessions,
                                         or to one of them; first /restart (or /reload-plugins) each idle one
                                         whose screen shows it runs on what changed under it, once.
                                         A session opened after the later of the agent-skills change and the
                                         last plugin update is skipped, except one named with --terminal
  skills-sync nudge <terminal> <one line>
                                         type one line into a session, only if it is idle at an empty prompt
  skills-sync wake-target <terminal>     say how to wake a Claude card: the session and one-line signal for the SendMessage
                                         tool (which only a session can call), or why the terminal path applies
  skills-sync wake-check <terminal> [--wait <seconds>]
                                         whether that signal started a turn, from the receiving session's transcript
  skills-sync wake-reset <terminal>      forget a signal that was not sent, or that a human has resolved
  skills-sync close-setup <worktree path> [--agent <handle>]
                                         close a new card's setup terminal once Orca's setup script finished;
                                         with --agent, first report a trust or permission dialog, or no start
  skills-sync card <brief.md> --repo <selector> --name <name> [--display-name <title>] [--comment <text>]
                   [--parent-worktree <selector> | --no-parent] [--role impl|verify|review] [--supersede]
                   [--agent claude|codex]
                                         create a card whose first prompt carries the brief's /goal, CLOSE/KEEP
                                         and lead lines written out, its FORBIDDEN refreshed, its setup closed in
                                         the background, and no second card on the same key (scripts/card.py)
  skills-sync handoff <TICKET> [--title <short title>] [--note <text>] [--base-branch <b>]
                                         the next ticket's /goal card under this card's parent, its start read
                                         from the screen (scripts/card.py)
  skills-sync end-check [--worker --outcome <o> | --card] [--brief <f> | --keep | --no-keep] [--apply]
                                         end-session §4/§7/§8's table as one verdict (scripts/end_check.py)
  skills-sync status                     print the last sync result and the pending reload
  skills-sync setup [status|apply|enable NAME|disable NAME]
                                         periodic automations: list, turn on defaults, turn one on or off
                                         (scripts/automations.py)

The checkout is loaded live through CLAUDE_CODE_PLUGIN_DIRS (docs/platform.md), so a sync changes what
every session reads. It never rebases, merges, forces or commits: anything but a fast-forward or a push of
signed commits stops it, and the reason goes to the state file and a desktop notification.
"""
import contextlib, datetime, fcntl, glob, json, os, pathlib, platform, re, secrets, subprocess, sys, time

REPO = pathlib.Path(__file__).resolve().parents[1]
STATE = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE", "~/.local/state/agent-skills")).expanduser()
CONFIG = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG", "~/.claude/agent-skills.json")).expanduser()
ORCA = os.environ.get("SKILLS_SYNC_ORCA", "orca")
CLAUDE = os.environ.get("SKILLS_SYNC_CLAUDE", "claude")
BRANCH = "main"
RELOAD = {"skills": "/reload-skills", "plugins": "/reload-plugins"}
RELOAD_TAGS = tuple(f"<command-name>{c}</command-name>" for c in RELOAD.values())
CAVEAT_TAG = "<local-command-caveat>"
SETTLE_S = float(os.environ.get("SKILLS_SYNC_SETTLE_S", "1.5"))
CONFIRM_S = float(os.environ.get("SKILLS_SYNC_CONFIRM_S", "3"))
INTERVAL_S = 900  # launchd StartInterval written by bootstrap.py; readers use it to tell a stalled sync


class Stop(Exception):
    """A condition under which sync must change nothing and tell the human."""


def git(*args, timeout=60, env=None):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", **(env or {}))
    # No tty and no stdin: under launchd, or run by hand, nothing may stop to ask for a password or host key.
    p = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=timeout, env=env,
                       stdin=subprocess.DEVNULL, start_new_session=True)
    if p.returncode:
        raise Stop(f"git {' '.join(args)} failed: {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout.strip()


def reload_row(row):
    """Whether a transcript row is what a typed /reload-skills or /reload-plugins leaves behind: its command row, its
    output row, or the caveat Claude Code writes before any local command. None of them is work by the session, so
    the broadcast's own reloads must not read as activity. The caveat says nothing about which command follows; the
    command row after it still counts for every other command."""
    if not isinstance(row, dict):
        return False
    if row.get("type") == "system" and row.get("subtype") == "local_command":
        run = row.get("commandRun") if isinstance(row.get("commandRun"), dict) else {}
        return "/" + str(run.get("command") or "") in RELOAD.values()
    msg = row.get("message") if isinstance(row.get("message"), dict) else {}
    content = msg.get("content")
    if row.get("type") != "user" or not isinstance(content, str):
        return False
    text = content.lstrip()
    return text.startswith(RELOAD_TAGS) or bool(row.get("isMeta")) and text.startswith(CAVEAT_TAG)


def reload_kind(paths):
    # Only SKILL.md and the plugin wiring are cached by a session; hook scripts, references and scripts are
    # read from disk on every use.
    if any(p in ("hooks/hooks.json", ".mcp.json") or p.startswith((".claude-plugin/", "agents/")) for p in paths):
        return "plugins"
    if any(re.fullmatch(r"skills/[^/]+/SKILL\.md", p) for p in paths):
        return "skills"
    return None


def pull_or_push():
    """Return (message, changed paths). Raises Stop on anything that is not a clean fast-forward or push."""
    if git("rev-parse", "--abbrev-ref", "HEAD") != BRANCH:
        raise Stop(f"checkout is not on {BRANCH}")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise Stop("checkout has uncommitted changes")
    url = git("remote", "get-url", "origin")
    remote_env = {}
    if url.startswith("http"):
        try:
            helper = git("config", "--get-urlmatch", "credential.helper", url)  # sees `credential.<url>.helper` too
        except Stop:
            helper = ""
        if not helper:
            raise Stop("origin is HTTPS but no credential helper is set; a fetch would wait for a password")
    elif not (os.environ.get("GIT_SSH_COMMAND") or os.environ.get("GIT_SSH")):
        try:
            git("config", "--get", "core.sshCommand")
        except Stop:
            remote_env = {"GIT_SSH_COMMAND": "ssh -o BatchMode=yes"}
    git("fetch", "--quiet", "origin", BRANCH, env=remote_env)
    ahead, behind = map(int, git("rev-list", "--left-right", "--count", f"{BRANCH}...origin/{BRANCH}").split())
    if ahead and behind:
        raise Stop(f"{BRANCH} and origin/{BRANCH} diverged ({ahead} ahead, {behind} behind)")
    if behind:
        old = git("rev-parse", "HEAD")
        git("merge", "--ff-only", "--quiet", f"origin/{BRANCH}")
        paths = git("diff", "--name-only", old, "HEAD").splitlines()
        return f"pulled {behind} commit(s)", paths
    if ahead:
        # N: no signature, B: bad one. E (cannot check) is allowed: a new PC may lack allowedSignersFile.
        for line in git("log", "--format=%G? %h", f"origin/{BRANCH}..{BRANCH}").splitlines():
            mark, sha = line.split()
            if mark in ("N", "B"):
                raise Stop(f"commit {sha} is not signed ({mark}); check your commit-signing agent and re-sign it")
        git("push", "--quiet", "origin", BRANCH, env=remote_env)
        return f"pushed {ahead} commit(s)", []
    return "up to date", []


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return default


def write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def notify(text):
    if platform.system() == "Darwin" and not os.environ.get("SKILLS_SYNC_NO_NOTIFY"):
        script = f"display notification {json.dumps(text)} with title \"agent-skills sync\""
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)


def queue_reload(kind):
    path = STATE / "reload-pending.json"
    pending = read_json(path, {})
    if pending.get("kind") != "plugins":  # a plugin reload also reloads skills
        pending["kind"] = kind
    pending.update(since=time.strftime("%Y-%m-%dT%H:%M:%S%z"), done=[], failed={}, head=head_or_none())
    write_json(path, pending)


def head_or_none():
    try:
        return git("rev-parse", "HEAD")
    except (Stop, subprocess.TimeoutExpired):
        return None


@contextlib.contextmanager
def locked(wait_s=0.0):
    """Yield True while holding the lock that sync, broadcast and nudge share, or False if it stayed busy.
    Held through a broadcast too: two of them would type into the same session, and a sync queueing a
    reload mid-broadcast would have it overwritten."""
    STATE.mkdir(parents=True, exist_ok=True)
    with open(STATE / "sync.lock", "w") as f:
        deadline = time.monotonic() + wait_s
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(0.5)
        yield True


def sync(broadcast_after=True):
    with locked() as got:
        if not got:
            print("another sync or broadcast is running")
            return 0
        prev = read_json(STATE / "sync.json", {})
        try:
            msg, paths = pull_or_push()
            ok = True
        except (Stop, subprocess.TimeoutExpired) as e:
            msg, paths, ok = str(e), [], False
        try:
            head = git("rev-parse", "--short", "HEAD")
        except (Stop, subprocess.TimeoutExpired):
            head = None
        write_json(STATE / "sync.json", {"ok": ok, "message": msg, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                         "head": head, "interval": INTERVAL_S})
        print(("ok: " if ok else "stopped: ") + msg)
        if not ok and (prev.get("ok", True) or prev.get("message") != msg):
            notify(f"stopped: {msg}")  # only on a change of state, so a lasting stop does not repeat
        kind = reload_kind(paths)
        if kind:
            queue_reload(kind)
        restarts = read_json(STATE / "restarts.json", {})
        waiting = isinstance(restarts, dict) and any(isinstance(v, dict) and "waiting" in v for v in restarts.values())
        if broadcast_after and ((STATE / "reload-pending.json").exists() or waiting):
            broadcast_locked(None)
    return 0 if ok else 1


# --- broadcast -------------------------------------------------------------------------------------

# A session started with `--name` draws its name into the prompt box's top rule: `──── orchestrator ─`.
RULE = re.compile(r"^─{10,}(?: [^─]+ ─+)?$")
# "✶ Contemplating… (32s", "· Crunching… (running hooks", "✶ Compacting conversation… (12s"
SPINNER = re.compile(r"^[·✢✳✶✻✽*] .*…")
BUSY = ("esc to interrupt",)
DIALOG = ("Do you want to proceed?", "Esc to cancel", "Tab to amend", "Enter to confirm", "trust this folder",
          "Enter to select", "↑/↓ to navigate", "Chat about this", "Press Enter", "Esc to close", "Esc to exit",
          "(y/n)", "Resume this session with")
CHOICE_CURSOR = re.compile(r"^❯ \d+\.")
PLACEHOLDER = re.compile(r'^Try ".*"$')
ORDER = [None, "skills", "plugins"]  # a plugin reload also reloads skills
UNCONFIRMED = "sent, but the screen does not show it was taken"
GONE = "what the command was for is no longer on the screen"
# Claude Code's own wording, matched loosely; a screen with neither is not judged (stale_signal).
RESTART_NOTICE = re.compile(r"(?<![\w/])/restart\b.*\bto apply\b", re.I)
# Only Claude Code's own result line ("⎿  PreToolUse:Bash hook error: …"); a tool's output that quotes it does not count.
HOOK_ERROR = re.compile(r"^⎿\s+\w+:\w+ hook (?:error|returned (?:a )?blocking error)\b", re.I)
# Claude Code's status line while a background shell, monitor or agent of the session still runs (shared with
# card_watch.py). Not a bare "N agent": "← N agent" counts sessions elsewhere waiting on a human, so idle ones show it.
BACKGROUND = re.compile(r"\b\d+ (?:shells?|(?:Artifact comment )?monitors?|local agents?|teams?|cloud sessions?|MCP tasks?"
                        r"|(?:background|remote) dynamic workflows?|background tasks?)\b|shells? still running")
# The panel that lists the session's agents instead. Only under the prompt box: a reply can read the same.
AGENT_PANEL = re.compile(r"^\s*(?:⏺\s+main\s*$|◯\s)")
# The line under the prompt box that counts those: the permission mode, or the shortcuts hint in default mode.
MODE_LINE = re.compile(r"\bmode on\b|\? for shortcuts")
# A guard's deny reads "hook error" too; only a hook that could not find its own file is stale.
MISSING_FILE = re.compile(r"can't open file|No such file or directory|\bENOENT\b|Cannot find module", re.I)
MESSAGE_MARKS = ("⏺", "●", "❯", "⎿")  # a reply, a prompt, a tool's result
# A finished turn's summary ("✻ Worked for 2m 7s · done 5:48 AM"): a mark, one word, "for", a duration and the
# clock time it was done at.
TURN_SUMMARY = re.compile(r"^[✻✶✳✢✽*] \S+ for (?:\d+(?:\.\d+)?[hms] ?)+· done \d{1,2}:\d\d\b")
# The notice names /restart; a hook error alone names no command, and /reload-plugins re-reads hooks.json.
STALE_COMMAND = {"restart": "/restart", "hook": RELOAD["plugins"]}


def norm(lines):
    return [l.replace("\xa0", " ").rstrip() for l in lines]


def prompt_box(lines):
    """Return (top, bottom) indices of the last two horizontal rules, or None."""
    rules = [i for i, l in enumerate(lines) if RULE.match(l.strip())]
    return (rules[-2], rules[-1]) if len(rules) >= 2 else None


def classify(lines, title, draft=""):
    """Return None when the screen is a Claude session idle at an empty prompt, else the reason it is not.
    Anything not positively recognized is a reason: a stray Enter answers whatever dialog is open."""
    lines = norm(lines)
    body = [l.strip() for l in lines if l.strip()]
    if not title.startswith("✳"):
        return "title shows the agent is busy"
    if draft:
        # Orca's own composer, not the terminal: the screen's prompt box can be empty while it holds text.
        return f"Orca composer holds a draft (not on screen): {draft[:40]!r}"
    if any("\x1b[200~" in l or "^[[200~" in l for l in lines):
        return "shell, not Claude"
    for l in body:
        if any(d in l for d in DIALOG) or CHOICE_CURSOR.match(l):
            return f"dialog open: {l[:60]}"
        if SPINNER.match(l) or any(b in l for b in BUSY):
            return f"turn in progress: {l[:60]}"
    box = prompt_box(lines)
    if not box:
        return "no prompt box"
    # Below the box Claude draws only its indented status lines (which may end in "%"); an unindented line
    # is another program's screen, such as the shell Claude exited to.
    for l in lines[box[1] + 1:]:
        if l.strip() and (not l.startswith(" ") or "❯" in l):
            return f"unrecognized line below the prompt box: {l.strip()[:60]}"
    inside = [l.strip() for l in lines[box[0] + 1:box[1]] if l.strip()]
    if len(inside) != 1 or not inside[0].startswith("❯"):
        return "prompt box not recognized"
    text = inside[0][1:].strip()
    if text and not PLACEHOLDER.match(text):
        return "the human is typing"
    return None


def stale_signal(lines):
    """(kind, line) when a session's screen shows it runs on what changed under it, else None.

    "restart": Claude Code says an installed update waits on /restart where it writes its own notices: the line right
    under the finished turn's summary, last above the prompt box, or a status line below the box; never the
    conversation. "hook": the last tool result of the last turn shown (the last `⎿` line below the last `❯ <prompt>`)
    is a hook error that says its file is missing (on that line or the two it wraps onto, short of the next message
    or a blank line), as on every tool call of a session whose hooks.json
    names a script an update removed; a tool that ran after it means the session got past it. Only that wording
    counts: a screen without it, or a guard's deny, is not judged."""
    lines = norm(lines or [])
    box = prompt_box(lines)
    if not box:
        return None
    body = lines[:box[0]]
    notices = [l.strip() for l in lines[box[1] + 1:] if l.startswith(" ")]
    # Above the box, only the line right under the finished turn's summary: a reply always comes before that line.
    filled = [l.strip() for l in body if l.strip()]
    if len(filled) >= 2 and TURN_SUMMARY.match(filled[-2]):
        notices.append(filled[-1])
    for l in notices:
        if RESTART_NOTICE.search(l):
            return "restart", l[:160]
    start = max((i for i, l in enumerate(body) if l.strip().startswith("❯ ")), default=-1)
    last = max((i for i in range(start + 1, len(body)) if body[i].strip().startswith("⎿")), default=None)
    if last is None or not HOOK_ERROR.search(body[last].strip()):
        return None
    result = [body[last]]  # the result and the lines it wraps onto, up to the next message or blank line
    for l in body[last + 1:last + 3]:
        if not l.strip() or l.strip()[:1] in MESSAGE_MARKS:
            break
        result.append(l)
    return ("hook", body[last].strip()[:160]) if MISSING_FILE.search(" ".join(result)) else None


def background_running(lines):
    """Why a /restart could end work this screen may be running, else None: a background shell, monitor or agent shown under
    the prompt box or on the finished turn's summary, or no mode line to show them on (unknown is not none)."""
    lines = norm(lines or [])
    box = prompt_box(lines)
    if not box:
        return "no prompt box"
    below = [l.strip() for l in lines[box[1] + 1:] if l.strip()]
    # Above the box only the summary, which may sit under a notice: other output there counts installed agents too.
    summary = [l for l in [l.strip() for l in lines[:box[0]] if l.strip()][-2:] if TURN_SUMMARY.match(l)]
    if any(BACKGROUND.search(l) for l in below + summary) or any(AGENT_PANEL.search(l) for l in below):
        return "background shells or agents still running"
    if not any(MODE_LINE.search(l) for l in below):
        return "no mode line to show background work on"
    return None


def stale_cleared(before, after, text):
    """After a /restart or /reload-plugins: the screen changed and either the signal is gone or the command was
    taken as the last message."""
    i, inside = last_message(after)
    taken = i is not None and norm(after)[i].strip() == f"❯ {text}" and inside != text
    return after != before and (taken or stale_signal(after) is None)


def last_message(lines):
    """The last `❯ <text>` history line above the prompt box, and what the box holds now."""
    lines = norm(lines)
    box = prompt_box(lines)
    if not box:
        return None, None
    sent = [i for i, l in enumerate(lines[:box[0]]) if l.strip().startswith("❯ ")]
    inside = " ".join(l.strip() for l in lines[box[0] + 1:box[1]] if l.strip())
    return (sent[-1] if sent else None), inside[1:].strip()


def turn_started(before, after, text):
    """The screen shows `text` was submitted: a turn is running, or it is the last message above the box."""
    if after == before:
        return False
    if any(SPINNER.match(l.strip()) or any(b in l for b in BUSY) for l in norm(after)):
        return True
    i, inside = last_message(after)
    head = text.strip()[:30]
    return i is not None and norm(after)[i].strip()[2:].startswith(head) and not inside.startswith(head)


def reloaded(before, after, command):
    """The command is the last message above an empty box and its `⎿ Reloaded…` output follows it. An older
    reload still on screen does not count: it would not be the last message."""
    i, inside = last_message(after)
    lines = norm(after)
    return (after != before and i is not None and lines[i].strip() == f"❯ {command}" and inside != command
            and any(l.strip().startswith("⎿") and "Reloaded" in l for l in lines[i + 1:]))


def orca(*args):
    try:
        p = subprocess.run([ORCA, *args, "--json"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Stop(f"orca {' '.join(args[:2])} failed: {e}")
    if p.returncode:
        raise Stop(f"orca {' '.join(args[:2])} failed: {(p.stderr or p.stdout).strip()[:200]}")
    try:
        return json.loads(p.stdout)["result"]
    except (ValueError, KeyError) as e:
        raise Stop(f"orca {' '.join(args[:2])} returned unexpected output: {e}")


def screen(handle):
    """Return (tail, reason) read fresh, with the terminal's own metadata; reason is None when sendable."""
    meta = orca("terminal", "show", "--terminal", handle)
    meta = meta.get("terminal", meta)
    if meta.get("agentIdentity") != "claude" or not meta.get("connected") or not meta.get("writable"):
        return [], "not a connected, writable Claude terminal"
    t = orca("terminal", "read", "--terminal", handle, "--screen")["terminal"]
    if t.get("source") not in (None, "screen"):
        return [], f"no rendered screen ({t.get('source')})"
    tail = t.get("tail") or []
    return tail, classify(tail, meta.get("title") or "", t.get("draft") or "")


def send_to(handle, text, confirmed, still=None):
    """Return None once `text` was typed into an idle session and the screen shows it was taken. `still(screen)`,
    when given, runs on the last read before typing: a reason it returns is returned, with nothing typed."""
    first, reason = screen(handle)
    if reason:
        return reason
    time.sleep(SETTLE_S)
    second, reason = screen(handle)
    if reason:
        return reason
    if second != first:
        return "screen changed between two reads (someone may be typing)"
    reason = still and still(second)
    if reason:
        return reason
    orca("terminal", "send", "--terminal", handle, "--text", text, "--enter")
    time.sleep(CONFIRM_S)
    try:
        after, _ = screen(handle)
    except Stop:  # typed already: an unread confirmation must not read as never sent
        return UNCONFIRMED
    if not confirmed(second, after, text):
        return UNCONFIRMED
    return None


def try_send(handle, text, confirmed=turn_started, wait_s=10):
    """Type one line into another session under the reload broadcast's idle checks. Returns (ok, reason).
    Also used by the orchestrator dashboard's chat box. `confirmed(before, after, text)` judges the result."""
    with locked(wait_s) as got:
        if not got:
            return False, "a sync or broadcast is running; try again shortly"
        try:
            reason = send_to(handle, " ".join(text.splitlines()), confirmed)
        except Stop as e:
            reason = str(e)
    return reason is None, reason


def nudge(handle, text):
    ok, reason = try_send(handle, text)
    print("sent" if ok else reason)
    return 0 if ok else 1


# --- wake ------------------------------------------------------------------------------------------

# A typed line can be cut, tangle with Esc under /goal and takes the terminal over; a message does none of these, but
# only a session can send one and its success means nothing about delivery. So these commands pick the target and
# judge the result, and the session calls SendMessage itself (orchestrate, "Waking a worker"). The line takes no text:
# a message reaches the receiver as not from the user, so it must never carry an instruction or approval.
WAKE_LINE = "wake {nonce}: run your orchestration check"
WAKE_WAIT_S = float(os.environ.get("SKILLS_SYNC_WAKE_WAIT_S", "20"))
WAKE_PENDING_S = float(os.environ.get("SKILLS_SYNC_WAKE_PENDING_S", "1800"))
WAKE_POLL_S = float(os.environ.get("SKILLS_SYNC_WAKE_POLL_S", "1"))
HOMES = (os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude"), "~/.claude", "~/Library/Application Support/orca/claude-profiles/*/home")
MODE_ROW = re.compile(r"^(?:⏵⏵|⏸) (auto mode|accept edits|bypass permissions|plan mode) on\b")
MODE_CLASS = {"auto mode": "prompting", "accept edits": "prompting", "bypass permissions": "bypass", "plan mode": None}


def _roles():
    """hooks/roles.py (terminal -> session, shared with the card watchers), loaded only here: sync itself must keep
    running on a checkout whose hooks are broken."""
    sys.path.insert(0, str(REPO / "hooks"))
    import roles
    return roles


def wake_file(handle):
    return STATE / "wakes" / f"{handle}.json"


@contextlib.contextmanager
def wake_lock(handle):
    """One reservation per terminal: two callers must not both get `route: sendmessage`, and a slow checker must not
    remove the attempt that replaced the one it watched."""
    path = wake_file(handle).with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def wake_entry(path):
    """The attempt kept at path: None when there is none, False when a file is there that cannot be trusted, which is
    not the same thing, since a signal may have gone out."""
    try:
        entry = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return False
    valid = isinstance(entry, dict) and all(isinstance(entry.get(k), str) for k in ("session", "to", "nonce")) \
        and isinstance(entry.get("at"), (int, float)) and isinstance(entry.get("offset"), int)
    return entry if valid else False


def wake_transport():
    """"sendmessage" or "terminal" from agent-skills.json; off by default until a card has run it for real."""
    cfg = read_json(CONFIG, {})
    wake = cfg.get("wake", {}) if isinstance(cfg, dict) else {}
    value = wake.get("transport", "terminal") if isinstance(wake, dict) else wake
    if value not in ("sendmessage", "terminal"):
        print(f"warning: unknown wake.transport {value!r}, using terminal", file=sys.stderr)
        return "terminal"
    return value


def permission_class(lines):
    """ "bypass" or "prompting" from the mode row under the prompt box, the two classes Claude Code compares to hold a
    message; None when no row, several rows, or plan mode, whose class depends on what the session has available."""
    lines = norm(lines or [])
    box = prompt_box(lines)
    if not box:
        return None
    below = [l.strip() for l in lines[box[1] + 1:] if l.strip()]
    found = [MODE_ROW.match(l).group(1) for l in below if MODE_ROW.match(l)]
    found += ["default"] * sum(l.startswith("? for shortcuts") for l in below)
    return None if len(found) != 1 else {**MODE_CLASS, "default": "prompting"}[found[0]]


def agent_rows():
    """(rows, problem) from `claude agents --json`."""
    try:
        p = subprocess.run([CLAUDE, "agents", "--json"], capture_output=True, text=True, timeout=15)
        rows = json.loads(p.stdout) if p.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        return [], f"claude agents failed: {e}"
    return (rows, None) if isinstance(rows, list) else ([], "claude agents gave no list")


def wake_plan(handle):
    """(session row, None) when a SendMessage can reach this terminal's session unheld, else (None, why)."""
    if wake_transport() == "terminal":
        return None, "wake.transport is terminal"
    me = os.environ.get("ORCA_TERMINAL_HANDLE")
    if not me or me == handle:
        return None, "the sender has no other terminal to read its permission mode from"
    try:
        target, reason = screen(handle)
        if not target:
            return None, reason or "no screen"  # a Codex card lands here too
        mine, _ = screen(me)
    except Stop as e:
        return None, str(e)
    rows, problem = agent_rows()
    if problem:
        return None, problem
    row, why = _roles().session_for(handle, rows)
    if not row:
        return None, why
    name = row.get("name")
    if not name or sum(isinstance(r, dict) and r.get("name") == name for r in rows) != 1:
        return None, "the session has no name of its own"
    sender, receiver = permission_class(mine), permission_class(target)
    if sender is None or receiver is None or sender != receiver:
        return None, f"permission modes do not match or are unknown ({sender} -> {receiver})"
    return row, None


def transcript_of(session):
    """The session's transcript under any config home; only the homes' own `*` is a pattern."""
    for home in HOMES:
        for root in glob.glob(os.path.expanduser(home)):
            for hit in pathlib.Path(root).glob(f"projects/*/{session}.jsonl"):
                return hit
    return None


def wake_envelope(nonce):
    """The whole of this wake as Claude Code wraps it: whoever merely quotes the nonce is not delivering it. An idle
    receiver's row carries a prefix and a trailing note, a running turn's does not."""
    return re.compile(r'(?:Another Claude session sent a message:\n)?<cross-session-message from="[^"\n]*"[^\n]*>\n'
                      + re.escape(WAKE_LINE.format(nonce=nonce)) + r"\n</cross-session-message>(?:\n|\Z)")


def enqueued(row, envelope):
    content = row.get("content")
    return row.get("type") == "queue-operation" and row.get("operation") == "enqueue" \
        and isinstance(content, str) and envelope.match(content) is not None


def delivered(row, envelope):
    """The row hands this wake to the receiving Claude: a user row, or the attachment a running turn takes it in."""
    attached = row.get("attachment") if isinstance(row.get("attachment"), dict) else {}
    if row.get("type") == "attachment" and attached.get("type") == "queued_command":
        texts = [attached.get("prompt")]
    elif row.get("type") == "user":
        content = row.get("message", {}).get("content") if isinstance(row.get("message"), dict) else None
        texts = [content] if isinstance(content, str) else [
            b.get("text") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]
    else:
        return False
    return any(isinstance(t, str) and envelope.match(t) for t in texts)


def wake_state(entry):
    """"started" (delivered, then assistant activity), "delivered", "accepted" (queued) or None, read from where the
    transcript ended when the attempt was reserved. Only those shapes advance the state, in that order: an unknown
    transcript reads as None, never as a start. The activity may be a turn already under way: the signal was seen,
    which is all a wake can prove."""
    path = transcript_of(entry["session"])
    if not path:
        return None
    envelope = wake_envelope(entry["nonce"])
    state = None
    try:
        with open(path, "rb") as f:
            if os.fstat(f.fileno()).st_size < entry["offset"]:
                return None
            f.seek(entry["offset"])
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                if state == "delivered" and row.get("type") == "assistant":
                    return "started"
                if state == "accepted" and delivered(row, envelope):
                    state = "delivered"
                elif not state and enqueued(row, envelope):
                    state = "accepted"
    except OSError:
        return None
    return state


def wake_target(handle):
    """Prints the route, one `key: value` per line. Always exits 0 on a decision, so a shell chain does not end on
    `route: terminal`."""
    if not _roles().SESSION.fullmatch(handle):
        print("not a terminal handle")
        return 2
    path = wake_file(handle)
    check = f"check: skills-sync wake-check {handle}"
    with wake_lock(handle):
        pending = wake_entry(path)
        if pending is False:
            print(f"route: report\nreason: the wake record cannot be read and a signal may have gone out\n{check}")
            return 0
        if pending:
            if wake_state(pending) != "started":
                old = time.time() - pending["at"] >= WAKE_PENDING_S
                print(f"route: {'report' if old else 'pending'}\nreason: wake {pending['nonce']} is not confirmed; "
                      f"another signal could arrive beside it\n{check}")
                return 0
            path.unlink(missing_ok=True)
        row, why = wake_plan(handle)
        transcript = transcript_of(row["sessionId"]) if row else None
        if row and not transcript:
            why = "the session has no transcript to confirm a wake in"
        try:
            offset = transcript.stat().st_size if transcript else None
        except OSError as e:
            offset, why = None, f"cannot read the transcript: {e}"
        if offset is None:
            print(f"route: terminal\nreason: {why}")
            return 0
        nonce = secrets.token_hex(8)
        write_json(path, {"session": row["sessionId"], "to": row["name"], "nonce": nonce, "at": time.time(),
                          "offset": offset, "from": os.environ.get("ORCA_TERMINAL_HANDLE")})
    print(f"route: sendmessage\nto: {row['name']}\nmessage: {WAKE_LINE.format(nonce=nonce)}\n{check}")
    return 0


def wake_check(handle, wait_s):
    path = wake_file(handle) if _roles().SESSION.fullmatch(handle) else None
    entry = wake_entry(path) if path else None
    if not entry:
        print("unconfirmed: the wake record cannot be read" if entry is False
              else "unconfirmed: no wake recorded for this terminal")
        return 1
    deadline = time.monotonic() + wait_s
    while True:
        state = wake_state(entry)
        if state == "started":
            with wake_lock(handle):
                current = wake_entry(path)
                if current and current["nonce"] == entry["nonce"]:
                    path.unlink(missing_ok=True)
            print("started")
            return 0
        if time.monotonic() >= deadline:
            print(f"{'accepted' if state else 'unconfirmed'}: wake {entry['nonce']} "
                  f"{'reached the session; no turn followed yet' if state else 'is not in the session transcript'}")
            return 1
        time.sleep(WAKE_POLL_S)


def wake_reset(handle):
    if not _roles().SESSION.fullmatch(handle):
        print("not a terminal handle")
        return 2
    with wake_lock(handle):
        wake_file(handle).unlink(missing_ok=True)
    print("reset")
    return 0


# --- setup terminals -------------------------------------------------------------------------------

SETUP_RUNNER = re.compile(r"(/\S+/orca/setup-runner\.sh)")
SETUP_POLL_S = float(os.environ.get("SKILLS_SYNC_SETUP_POLL_S", "10"))


def setup_runner(lines):
    """The path of Orca's setup script when these lines show it was started, else None."""
    for l in norm(lines):
        m = SETUP_RUNNER.search(l)
        if m:
            return m.group(1)
    return None


def running(path):
    """Whether a process runs the script; a failed `ps` raises, so the terminal stays open."""
    p = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True, timeout=10)
    lines = p.stdout.splitlines()
    # A real listing has its header and at least `ps` itself; anything less is unread, not "nothing runs".
    if p.returncode != 0 or not lines or lines[0].strip() != "COMMAND" or not any(l.strip() for l in lines[1:]):
        raise OSError(f"ps failed or printed no process list: {(p.stderr or p.stdout).strip()[:80]}")
    return any(path in l for l in lines[1:])


def start_state(handle):
    """(state, detail) of a new card's agent from its screen: `started` (a turn runs, or a message was taken),
    `trust` (the folder trust dialog), `dialog` (a permission or other dialog), or `unconfirmed` (anything else,
    an empty prompt included: a silent screen is not a start)."""
    tail = orca("terminal", "read", "--terminal", handle, "--screen")["terminal"].get("tail") or []
    # Only the screen counts: a title without Claude's idle mark (a missing one included) proves no start.
    reason = classify(tail, "✳")
    if reason and reason.startswith("dialog open"):
        return ("trust" if "trust this folder" in reason else "dialog"), reason
    if reason and reason.startswith("turn in progress"):
        return "started", reason
    if reason is None and last_message(tail)[0] is not None:
        return "started", "a message was taken"
    return "unconfirmed", reason or "idle at an empty prompt"


def watch_start(handle, grace_s):
    """Poll the agent's screen until it started or grace_s passed. Returns (state, detail)."""
    start, state = time.monotonic(), ("unconfirmed", "not read")
    while True:
        try:
            state = start_state(handle)
        except (Stop, KeyError, TypeError) as e:
            state = ("unconfirmed", f"screen unreadable: {e}")
        if state[0] != "unconfirmed" or time.monotonic() - start >= grace_s:
            return state
        time.sleep(SETUP_POLL_S)


def close_setup(worktree, timeout_s=1800, grace_s=120, agent=None):
    """Close each of the card's terminals without an agent once the setup script it ran has finished.
    The setup terminal is a shell that outlives its script, so `orca terminal wait --for exit` never fires.
    A shell that never showed the script is someone's own and stays.
    With `agent` (the card's agent handle) it first watches that agent start: a trust dialog, a permission dialog or
    no visible start within grace_s is printed and makes the exit code 1, since a setup that waits on such a dialog
    never ends. The last line is always `result: …`."""
    start = time.monotonic()
    rc = 0
    if agent:
        state, detail = watch_start(agent, grace_s)
        print(f"{agent}  agent {state}: {detail}")
        if state != "started":
            rc = 1
    try:
        terms = orca("terminal", "list", "--worktree", f"path:{worktree}")["terminals"]
        # Only a row that says it has no agent is a shell; one with no agentIdentity field at all is left open.
        handles = [t["handle"] for t in terms if "agentIdentity" in t and t["agentIdentity"] is None]
        unknown = [t.get("handle") for t in terms
                   if "agentIdentity" not in t
                   or not (t["agentIdentity"] is None or isinstance(t["agentIdentity"], str) and t["agentIdentity"])]
        if unknown:
            print(f"warning: no agentIdentity on {', '.join(map(str, unknown))}; left open")
    except (Stop, KeyError, TypeError) as e:
        print(f"cannot list the card's terminals: {e}")
        print("result: failed")
        return 1
    rc = close_setup_terminals(handles, start, timeout_s, grace_s) or rc
    print(f"result: {'ok' if rc == 0 else 'failed'}")
    return rc


def close_setup_terminals(handles, start, timeout_s, grace_s):
    rc = 0
    for h in handles:
        path, idle = None, 0
        while True:
            try:
                if not path:
                    for flag in (("--screen",), ("--limit", "5000")):  # the script's line may have scrolled off
                        path = path or setup_runner(orca("terminal", "read", "--terminal", h, *flag)["terminal"].get("tail") or [])
                # Twice in a row: the command line is echoed a moment before the script starts.
                idle = idle + 1 if path and not running(path) else 0
                if idle >= 2:
                    orca("terminal", "close", "--terminal", h)
                    print(f"{h}  closed")
                    break
            except (Stop, KeyError, TypeError, OSError, subprocess.TimeoutExpired) as e:
                print(f"{h}  left open: {e}")
                rc = 1
                break
            waited = time.monotonic() - start
            if (not path and waited >= grace_s) or waited >= timeout_s:
                print(f"{h}  left open: {'setup still running' if path else 'no setup script seen'}")
                rc = rc if not path else 1
                break
            time.sleep(SETUP_POLL_S)
    return rc


def broadcast(kind, dry_run, only=None):
    if dry_run:
        return broadcast_locked(kind, dry_run=True, only=only)
    with locked() as got:
        if not got:
            print("another sync or broadcast is running; the reload stays pending")
            return 1
        return broadcast_locked(kind, only=only)


def plugins_updated():
    """The latest `lastUpdated` among the installed plugins (Claude Code's installed_plugins.json), as a Unix time,
    or 0 when the file or a stamp is unreadable. A plugin updated after the change leaves a session that opened
    before that update on the old code, whichever plugin it is, so no name is read."""
    path = pathlib.Path(os.environ.get("AGENT_SKILLS_INSTALLED_PLUGINS",
                                       "~/.claude/plugins/installed_plugins.json")).expanduser()
    try:
        plugins = list(read_json(path, {}).get("plugins", {}).values())
    except (OSError, AttributeError, TypeError):
        return 0
    latest = 0
    for entries in plugins:  # one malformed plugin or entry must not hide the others
        for e in entries if isinstance(entries, list) else []:
            try:
                latest = max(latest, datetime.datetime.fromisoformat(e["lastUpdated"].replace("Z", "+00:00")).timestamp())
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
    return latest


def opened_after(since, terms, plugins=None):
    """Handles of `terms` whose Orca worktree was created after the later of `since` (when agent-skills changed) and
    the last plugin update: their agent started after the change and already loaded it. Orca keeps no terminal
    creation time, and a worktree predates its terminals, so this never skips a session that is older than the
    change. Anything unreadable leaves the terminal a target."""
    try:
        cutoff = max(datetime.datetime.strptime(since, "%Y-%m-%dT%H:%M:%S%z").timestamp(),
                     plugins_updated() if plugins is None else plugins) * 1000
        created = {w.get("worktreeId"): w.get("createdAt") for w in orca("worktree", "ps", "--limit", "500")["worktrees"]}
    except (Stop, KeyError, TypeError, ValueError):
        return set()
    return {t["handle"] for t in terms if isinstance(created.get(t.get("worktreeId")), (int, float))
            and created[t.get("worktreeId")] > cutoff}


RESTART_KEEP_S, WAITING_KEEP_S = 7 * 86400, 86400


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _stamp(entry):
    """When a restarts.json entry was written (sent, else since waiting), as a Unix time; 0 when unreadable."""
    try:
        return datetime.datetime.strptime(entry.get("sent") or entry.get("since") or "", "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except (AttributeError, ValueError):
        return 0


def restart_stale(terms, dry_run=False, keep_others=False):
    """Type STALE_COMMAND into each idle session whose screen shows stale_signal, once per session while its signal
    lasts, of each kind. A busy one, or one whose background work the command would end (background_running), waits:
    it is written to restarts.json as waiting, and the next sync runs the broadcast again.
    An entry goes once its screen no longer shows the signal, so a later one is new; an unreadable screen keeps it,
    and a terminal Orca does not list now keeps it RESTART_KEEP_S (WAITING_KEEP_S when waiting). keep_others leaves
    the entries of terminals not in `terms` as they are. Returns the handles typed into (in a dry run, those it would
    type into) and {handle: reason} for those left waiting."""
    path = STATE / "restarts.json"
    seen = read_json(path, {})
    seen = {h: v for h, v in seen.items() if isinstance(v, dict)} if isinstance(seen, dict) else {}
    # A terminal missing from this list may be back later with the same screen: its entry stays a while, as one this
    # pass could not read does. A waiting one stays less, since each sync reruns the broadcast for it.
    out = {h: v for h, v in seen.items() if h not in terms and (
        keep_others or _stamp(v) >= time.time() - (WAITING_KEEP_S if "waiting" in v else RESTART_KEEP_S))}
    typed, held = set(), {}
    for h in terms:
        old = seen.get(h) or {}
        try:
            lines, reason = screen(h)
        except Stop as e:
            lines, reason = [], str(e)
        # Unread, or no prompt box to read it by (a dialog): no evidence the signal went.
        if not lines or not prompt_box(norm(lines)):
            out.update({h: old} if old else {})
            held.update({h: old["waiting"]} if "waiting" in old else {})  # still unknown: the reload waits with it
            print(f"{h}  stale check not read: {reason}")
            continue
        sig = stale_signal(lines)
        if not sig:
            continue
        kind, line = sig
        cmd = STALE_COMMAND[kind]
        if old.get("sent") and old.get("kind") == kind:
            out[h] = old
            print(f"{h}  {cmd} was sent at {old['sent']}; not again: {line[:60]}")
            continue
        # Either command restarts or reloads the session under its background shells and agents.
        reason = reason or background_running(lines)
        if dry_run:
            print(f"{h}  {reason and 'waits: ' + reason or 'would send ' + cmd}: {line[:60]}")
            typed |= set() if reason else {h}
            held.update({h: reason} if reason else {})
            continue
        try:
            reason = reason or send_to(h, cmd, stale_cleared, still=lambda s: (
                GONE if (stale_signal(s) or ("",))[0] != kind else background_running(s)))
        except Stop as e:
            reason = str(e)
        if reason == GONE:  # cleared between the reads: nothing to fix, nothing to keep
            print(f"{h}  {reason}")
            continue
        if reason and reason != UNCONFIRMED:
            out[h] = {"waiting": reason, "since": old.get("since") or _now(), "kind": kind, "line": line}
            held[h] = reason
            print(f"{h}  waits before {cmd}: {reason}")
            continue
        out[h] = {"sent": _now(), "command": cmd, "kind": kind, "line": line,
                  **({"unconfirmed": True} if reason else {})}
        typed.add(h)
        print(f"{h}  {reason or 'sent ' + cmd}: {line[:60]}")
    if not dry_run and (out or path.exists()):
        write_json(path, out)
    return typed, held


def broadcast_locked(kind, dry_run=False, only=None):
    path = STATE / "reload-pending.json"
    pending = read_json(path, {})
    head = head_or_none()
    if head and pending.get("head") != head:
        # Sessions marked done reloaded an older checkout, as after a fast-forward pushed by hand, which sync
        # never sees as a pull.
        pending = dict(pending, done=[], failed={}, head=head, since=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    queued = pending.get("kind")
    kind = max(kind, queued if queued in ORDER else "plugins", key=ORDER.index)  # unreadable: the safe superset
    stamp = plugins_updated()
    try:
        updated = datetime.datetime.strptime(pending.get("since"), "%Y-%m-%dT%H:%M:%S%z").timestamp() < stamp
    except (TypeError, ValueError):
        updated = False
    if kind and updated:
        # A plugin updated after the change needs /reload-plugins, which /reload-skills does not do. Sessions marked
        # done before that update reloaded without it, so a new update stamp clears them.
        kind = "plugins"
        if pending.get("plugins_at") != stamp:
            pending = dict(pending, done=[], plugins_at=stamp)
    if queued in ORDER[1:] and ORDER.index(kind) > ORDER.index(queued):
        pending = dict(pending, done=[])  # those done got the lesser reload
    try:
        listed = [t for t in orca("terminal", "list")["terminals"]
                  if t.get("agentIdentity") == "claude" and t.get("connected") and t.get("writable")]
        terms = [t["handle"] for t in listed]
    except (Stop, KeyError, TypeError) as e:
        print(f"cannot list Orca terminals{', reload stays pending' if kind else ''}: {e}")
        return 1
    if only:
        if only not in terms:
            print(f"{only}  not a connected, writable Claude terminal")
            return 1
        terms = [only]
    # Both commands load the plugin afresh, so a session typed into here needs no reload after it.
    restarted, held = restart_stale(terms, dry_run, keep_others=bool(only))
    if only and not kind:  # asked for by name with nothing pending: the superset, as for an unreadable queue
        kind = "plugins"
    if not kind:
        print("nothing to reload")
        return 0
    newer = opened_after(pending.get("since"), listed, stamp)  # the stamp that chose the kind, not a later read
    # A named terminal is sent to whatever the queue says: the caller knows it needs the reload.
    done, failed = (restarted if only else set(pending.get("done", [])) | restarted), {}
    for h in terms:
        if h in done:
            continue
        if h in held:  # a reload now would reach the work the restart waits on; the restart reloads it anyway
            failed[h] = {"reason": f"waits for its restart: {held[h]}"}
            print(f"{h}  {failed[h]['reason']}")
            continue
        if h in newer:
            if not only:
                print(f"{h}  opened after the change, already has it")
                continue
            print(f"{h}  warning: opened after the change, so it may already have it; sending as asked")
        try:
            if dry_run:
                s, reason = screen(h)
                reason = reason or (stale_signal(s) and background_running(s))
                print(f"{h}  {reason or 'would send ' + RELOAD[kind]}")
                continue
            # A screen still showing a stale signal is one the restart may wait on, by whatever path it got here.
            reason = send_to(h, RELOAD[kind], reloaded, still=lambda s: stale_signal(s) and background_running(s))
        except Stop as e:
            reason = str(e)
        if reason:
            failed[h] = {"reason": reason}
        else:
            done.add(h)
        print(f"{h}  {reason or 'sent ' + RELOAD[kind]}")
    if dry_run:
        return 0
    if only:  # the other sessions were not tried: leave their entries as they are
        if path.exists():
            rest = {h: v for h, v in pending.get("failed", {}).items() if h != only}
            write_json(path, dict(pending, kind=kind, done=sorted((done | set(pending.get("done", []))) - set(failed)),
                                  failed={**rest, **failed}))
        return 1 if failed else 0
    if failed:
        write_json(path, dict(pending, kind=kind, done=sorted(done & set(terms)), failed=failed))
        print(f"{len(failed)} session(s) left pending in {path}")
        return 1
    path.unlink(missing_ok=True)
    return 0


def status():
    print(json.dumps({"sync": read_json(STATE / "sync.json", None),
                      "reload_pending": read_json(STATE / "reload-pending.json", None)}, indent=2, ensure_ascii=False))
    return 0


def main(argv):
    cmd = argv[0] if argv else ""
    if any(a in ("-h", "--help") for a in argv):  # every subcommand acts on its own; help must never reach one
        print(__doc__)
        return 0
    if cmd == "sync":
        return sync(broadcast_after="--no-broadcast" not in argv)
    if cmd == "broadcast":
        args, only = list(argv[1:]), None
        if "--terminal" in args:
            i = args.index("--terminal")
            only = args[i + 1] if i + 1 < len(args) else ""
            del args[i:i + 2]
        # A mistyped --dry-run, or --terminal without its handle, must not broadcast for real.
        if only == "" or (only or "").startswith("-") or any(a not in ("--plugins", "--skills", "--dry-run", "--retry") for a in args):
            print(__doc__)
            return 2
        STATE.mkdir(parents=True, exist_ok=True)
        kind = "plugins" if "--plugins" in args else "skills" if "--skills" in args else None
        return broadcast(kind, dry_run="--dry-run" in argv, only=only)
    if cmd == "nudge" and len(argv) == 3:
        return nudge(argv[1], argv[2])
    if cmd == "wake-target" and len(argv) == 2:
        return wake_target(argv[1])
    if cmd == "wake-check" and len(argv) == 2:
        return wake_check(argv[1], WAKE_WAIT_S)
    if cmd == "wake-check" and len(argv) == 4 and argv[2] == "--wait" and argv[3].isdigit():
        return wake_check(argv[1], float(argv[3]))
    if cmd == "wake-reset" and len(argv) == 2:
        return wake_reset(argv[1])
    if cmd == "close-setup" and len(argv) == 2:
        return close_setup(argv[1])
    if cmd == "close-setup" and len(argv) == 4 and argv[2] == "--agent":
        return close_setup(argv[1], agent=argv[3])
    if cmd == "card":
        from card import main as card
        return card(argv[1:])
    if cmd == "handoff":
        from card import handoff
        return handoff(argv[1:])
    if cmd == "end-check":
        from end_check import main as end_check
        return end_check(argv[1:])
    if cmd == "status":
        return status()
    if cmd == "setup":
        from automations import main as setup
        return setup(argv[1:])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
