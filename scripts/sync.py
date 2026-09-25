#!/usr/bin/env python3
"""Keep this checkout in step with origin/main and tell running Claude sessions to reload it.

Usage:
  skills-sync sync [--no-broadcast]      fetch, fast-forward or push, then broadcast a needed reload
  skills-sync broadcast [--skills|--plugins] [--dry-run]
                                         send the pending (or the given) reload to idle Claude sessions
  skills-sync nudge <terminal> <one line>
                                         type one line into a session, only if it is idle at an empty prompt
  skills-sync status                     print the last sync result and the pending reload

The checkout is loaded live through CLAUDE_CODE_PLUGIN_DIRS (docs/platform.md), so a sync changes what
every session reads. It never rebases, merges, forces or commits: anything but a fast-forward or a push of
signed commits stops it, and the reason goes to the state file and a desktop notification.
"""
import contextlib, fcntl, json, os, pathlib, platform, re, subprocess, sys, time

REPO = pathlib.Path(__file__).resolve().parents[1]
STATE = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE", "~/.local/state/agent-skills")).expanduser()
ORCA = os.environ.get("SKILLS_SYNC_ORCA", "orca")
BRANCH = "main"
RELOAD = {"skills": "/reload-skills", "plugins": "/reload-plugins"}
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


def reload_kind(paths):
    # Only SKILL.md and the plugin wiring are cached by a session; hook scripts, references and scripts are
    # read from disk on every use.
    if any(p in ("hooks/hooks.json", ".mcp.json") or p.startswith((".claude-plugin/", "agents/")) for p in paths):
        return "plugins"
    if any(re.fullmatch(r"(skills|legacy)/[^/]+/SKILL\.md", p) for p in paths):
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
                raise Stop(f"commit {sha} is not signed ({mark}); check the 1Password SSH agent and re-sign it")
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
    pending.update(since=time.strftime("%Y-%m-%dT%H:%M:%S%z"), done=[], failed={})
    write_json(path, pending)


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
        if broadcast_after and (STATE / "reload-pending.json").exists():
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
        return "composer holds a draft"
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
    after, _ = screen(handle)
    if not confirmed(second, after, text):
        return "sent, but the screen does not show it was taken"
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


def broadcast(kind, dry_run):
    if dry_run:
        return broadcast_locked(kind, dry_run=True)
    with locked() as got:
        if not got:
            print("another sync or broadcast is running; the reload stays pending")
            return 1
        return broadcast_locked(kind)


def broadcast_locked(kind, dry_run=False):
    path = STATE / "reload-pending.json"
    pending = read_json(path, {})
    kind = max(kind, pending.get("kind"), key=ORDER.index)
    if not kind:
        print("nothing to reload")
        return 0
    try:
        terms = [t["handle"] for t in orca("terminal", "list")["terminals"]
                 if t.get("agentIdentity") == "claude" and t.get("connected") and t.get("writable")]
    except (Stop, KeyError, TypeError) as e:
        print(f"cannot list Orca terminals, reload stays pending: {e}")
        return 1
    done, failed = set(pending.get("done", [])), {}
    for h in terms:
        if h in done:
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
        return broadcast(kind, dry_run="--dry-run" in argv)
    if cmd == "nudge" and len(argv) == 3:
        return nudge(argv[1], argv[2])
    if cmd == "status":
        return status()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
