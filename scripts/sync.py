#!/usr/bin/env python3
"""Keep this checkout in step with origin/main and tell running Claude sessions to reload it.

Usage:
  skills-sync sync [--no-broadcast]      fetch, fast-forward or push, then broadcast a needed reload
  skills-sync broadcast [--skills|--plugins] [--dry-run] [--terminal <handle>]
                                         send the pending (or the given) reload to idle Claude sessions,
                                         or to one of them; first /restart (or /reload-plugins) each idle one
                                         whose screen shows it runs on what changed under it, once
  skills-sync nudge <terminal> <one line>
                                         type one line into a session, only if it is idle at an empty prompt
  skills-sync close-setup <worktree path>
                                         close a new card's setup terminal once Orca's setup script finished
  skills-sync card <brief.md> --repo <selector> --name <name> [--display-name <title>] [--comment <text>]
                   [--parent-worktree <selector> | --no-parent]
                                         create a card whose first prompt carries the brief's /goal, CLOSE/KEEP
                                         and lead lines written out (scripts/card.py)
  skills-sync status                     print the last sync result and the pending reload
  skills-sync setup [status|apply|enable NAME|disable NAME]
                                         periodic automations: list, turn on defaults, turn one on or off
                                         (scripts/automations.py)

The checkout is loaded live through CLAUDE_CODE_PLUGIN_DIRS (docs/platform.md), so a sync changes what
every session reads. It never rebases, merges, forces or commits: anything but a fast-forward or a push of
signed commits stops it, and the reason goes to the state file and a desktop notification.
"""
import contextlib, datetime, fcntl, json, os, pathlib, platform, re, subprocess, sys, time

REPO = pathlib.Path(__file__).resolve().parents[1]
STATE = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE", "~/.local/state/agent-skills")).expanduser()
ORCA = os.environ.get("SKILLS_SYNC_ORCA", "orca")
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

RULE = re.compile(r"^─{10,}$")
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
# Claude Code's own wording, matched loosely; a screen with neither is not judged (stale_signal).
RESTART_NOTICE = re.compile(r"(?<![\w/])/restart\b.*\bto apply\b", re.I)
HOOK_ERROR = re.compile(r"\bhook (?:error|returned (?:a )?blocking error)\b", re.I)
# A guard's deny reads "hook error" too; only a hook that could not find its own file is stale.
MISSING_FILE = re.compile(r"can't open file|No such file or directory|\bENOENT\b|Cannot find module", re.I)
MESSAGE_MARKS = ("⏺", "●", "❯", "⎿")  # a reply, a prompt, a tool's result
# A finished turn's summary ("✻ Worked for 2m 7s · done 5:48 AM"): a mark, one word, "for" and a duration.
TURN_SUMMARY = re.compile(r"^[✻✶✳✢✽*] \S+ for \d+(?:\.\d+)?\s?[hms]\b")
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
    if last is None or not HOOK_ERROR.search(body[last]):
        return None
    result = [body[last]]  # the result and the lines it wraps onto, up to the next message or blank line
    for l in body[last + 1:last + 3]:
        if not l.strip() or l.strip()[:1] in MESSAGE_MARKS:
            break
        result.append(l)
    return ("hook", body[last].strip()[:160]) if MISSING_FILE.search(" ".join(result)) else None


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


def send_to(handle, text, confirmed):
    """Return None once `text` was typed into an idle session and the screen shows it was taken."""
    first, reason = screen(handle)
    if reason:
        return reason
    time.sleep(SETTLE_S)
    second, reason = screen(handle)
    if reason:
        return reason
    if second != first:
        return "screen changed between two reads (someone may be typing)"
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
    p = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True, timeout=10)
    return any(path in l for l in p.stdout.splitlines()[1:])


def close_setup(worktree, timeout_s=1800, grace_s=120):
    """Close each of the card's terminals without an agent once the setup script it ran has finished.
    The setup terminal is a shell that outlives its script, so `orca terminal wait --for exit` never fires.
    A shell that never showed the script is someone's own and stays."""
    start = time.monotonic()
    try:
        handles = [t["handle"] for t in orca("terminal", "list", "--worktree", f"path:{worktree}")["terminals"]
                   if not t.get("agentIdentity")]
    except (Stop, KeyError, TypeError) as e:
        print(f"cannot list the card's terminals: {e}")
        return 1
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


def opened_after(since, terms):
    """Handles of `terms` whose Orca worktree was created after `since`: their agent started after the change and
    already loaded it. Orca keeps no terminal creation time, and a worktree predates its terminals, so this never
    skips a session that is older than the change. Anything unreadable leaves the terminal a target."""
    try:
        cutoff = datetime.datetime.strptime(since, "%Y-%m-%dT%H:%M:%S%z").timestamp() * 1000
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
    lasts. A busy one waits: it is written to restarts.json as waiting, and the next sync runs the broadcast again.
    An entry goes once its screen no longer shows the signal, so a later one is new; an unreadable screen keeps it,
    and a terminal Orca does not list now keeps it RESTART_KEEP_S (WAITING_KEEP_S when waiting). keep_others leaves
    the entries of terminals not in `terms` as they are. Returns the handles typed into (in a dry run, those it would
    type into)."""
    path = STATE / "restarts.json"
    seen = read_json(path, {})
    seen = {h: v for h, v in seen.items() if isinstance(v, dict)} if isinstance(seen, dict) else {}
    # A terminal missing from this list may be back later with the same screen: its entry stays a while, as one this
    # pass could not read does. A waiting one stays less, since each sync reruns the broadcast for it.
    out = {h: v for h, v in seen.items() if h not in terms and (
        keep_others or _stamp(v) >= time.time() - (WAITING_KEEP_S if "waiting" in v else RESTART_KEEP_S))}
    typed = set()
    for h in terms:
        old = seen.get(h) or {}
        try:
            lines, reason = screen(h)
        except Stop as e:
            lines, reason = [], str(e)
        # Unread, or no prompt box to read it by (a dialog): no evidence the signal went.
        if not lines or not prompt_box(norm(lines)):
            out.update({h: old} if old else {})
            print(f"{h}  stale check not read: {reason}")
            continue
        sig = stale_signal(lines)
        if not sig:
            continue
        kind, line = sig
        cmd = STALE_COMMAND[kind]
        if old.get("sent"):
            out[h] = old
            print(f"{h}  {cmd} was sent at {old['sent']}; not again: {line[:60]}")
            continue
        if dry_run:
            print(f"{h}  {reason and 'waits: ' + reason or 'would send ' + cmd}: {line[:60]}")
            typed |= set() if reason else {h}
            continue
        try:
            reason = reason or send_to(h, cmd, stale_cleared)
        except Stop as e:
            reason = str(e)
        if reason and reason != UNCONFIRMED:
            out[h] = {"waiting": reason, "since": old.get("since") or _now(), "kind": kind, "line": line}
            print(f"{h}  waits before {cmd}: {reason}")
            continue
        out[h] = {"sent": _now(), "command": cmd, "kind": kind, "line": line,
                  **({"unconfirmed": True} if reason else {})}
        typed.add(h)
        print(f"{h}  {reason or 'sent ' + cmd}: {line[:60]}")
    if not dry_run and (out or path.exists()):
        write_json(path, out)
    return typed


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
    restarted = restart_stale(terms, dry_run, keep_others=bool(only))
    if not kind:
        print("nothing to reload")
        return 0
    newer = opened_after(pending.get("since"), listed)
    done, failed = set(pending.get("done", [])) | restarted, {}
    for h in terms:
        if h in done:
            continue
        if h in newer:
            print(f"{h}  opened after the change, already has it")
            continue
        try:
            if dry_run:
                reason = screen(h)[1]
                print(f"{h}  {reason or 'would send ' + RELOAD[kind]}")
                continue
            reason = send_to(h, RELOAD[kind], reloaded)
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
            write_json(path, dict(pending, done=sorted(done), failed={**rest, **failed}))
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
    if cmd == "sync":
        return sync(broadcast_after="--no-broadcast" not in argv)
    if cmd == "broadcast":
        STATE.mkdir(parents=True, exist_ok=True)
        kind = "plugins" if "--plugins" in argv else "skills" if "--skills" in argv else None
        only = argv[argv.index("--terminal") + 1] if "--terminal" in argv[:-1] else None
        return broadcast(kind, dry_run="--dry-run" in argv, only=only)
    if cmd == "nudge" and len(argv) == 3:
        return nudge(argv[1], argv[2])
    if cmd == "close-setup" and len(argv) == 2:
        return close_setup(argv[1])
    if cmd == "card":
        from card import main as card
        return card(argv[1:])
    if cmd == "status":
        return status()
    if cmd == "setup":
        from automations import main as setup
        return setup(argv[1:])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
