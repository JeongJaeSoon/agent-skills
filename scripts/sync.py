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
import fcntl, json, os, pathlib, platform, re, subprocess, sys, time

REPO = pathlib.Path(__file__).resolve().parents[1]
STATE = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE", "~/.local/state/agent-skills")).expanduser()
ORCA = os.environ.get("SKILLS_SYNC_ORCA", "orca")
BRANCH = "main"
RELOAD = {"skills": "/reload-skills", "plugins": "/reload-plugins"}
SETTLE_S = float(os.environ.get("SKILLS_SYNC_SETTLE_S", "1.5"))
CONFIRM_S = float(os.environ.get("SKILLS_SYNC_CONFIRM_S", "3"))


class Stop(Exception):
    """A condition under which sync must change nothing and tell the human."""


def git(*args, timeout=60):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    p = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=timeout, env=env)
    if p.returncode:
        raise Stop(f"git {' '.join(args)} failed: {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout.strip()


def reload_kind(paths):
    # Only SKILL.md is cached by a session; references and scripts are read from disk on every use.
    if any(p.startswith(("hooks/", ".claude-plugin/", "agents/")) for p in paths):
        return "plugins"
    if any(re.fullmatch(r"(skills|legacy)/[^/]+/SKILL\.md", p) for p in paths):
        return "skills"
    return None


def pull_or_push():
    """Return (message, changed paths). Raises Stop on anything that is not a clean fast-forward or push."""
    if git("rev-parse", "--abbrev-ref", "HEAD") != BRANCH:
        raise Stop(f"checkout is not on {BRANCH}")
    if git("status", "--porcelain"):
        raise Stop("checkout has uncommitted changes")
    if git("remote", "get-url", "origin").startswith("http"):
        try:
            git("config", "--get", "credential.helper")
        except Stop:
            raise Stop("origin is HTTPS but no credential.helper is set; a fetch would wait for a password")
    git("fetch", "--quiet", "origin", BRANCH)
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
        git("push", "--quiet", "origin", BRANCH)
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


def sync(broadcast_after=True):
    STATE.mkdir(parents=True, exist_ok=True)
    with open(STATE / "sync.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another sync is running")
            return 0
        prev = read_json(STATE / "sync.json", {})
        try:
            msg, paths = pull_or_push()
            ok = True
        except (Stop, subprocess.TimeoutExpired) as e:
            msg, paths, ok = str(e), [], False
        write_json(STATE / "sync.json", {"ok": ok, "message": msg, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                         "head": git("rev-parse", "--short", "HEAD")})
        print(("ok: " if ok else "stopped: ") + msg)
        if not ok and (prev.get("ok", True) or prev.get("message") != msg):
            notify(f"stopped: {msg}")  # only on a change of state, so a lasting stop does not repeat
        kind = reload_kind(paths)
        if kind:
            queue_reload(kind)
    if broadcast_after and (STATE / "reload-pending.json").exists():
        broadcast(None, dry_run=False)
    return 0 if ok else 1


# --- broadcast -------------------------------------------------------------------------------------

RULE = re.compile(r"^─{10,}$")
SPINNER = re.compile(r"^[·✢✳✶✻✽*] \S+…")  # "✶ Contemplating… (32s", "· Crunching… (running hooks"
DIALOG = ("Do you want to proceed?", "Esc to cancel", "Tab to amend", "Enter to confirm", "trust this folder",
          "Enter to select", "↑/↓ to navigate", "Chat about this")
CHOICE_CURSOR = re.compile(r"^❯ \d+\.")
PLACEHOLDER = re.compile(r'^Try ".*"$')


def classify(lines, title, draft=""):
    """Return None when the screen is a Claude session idle at an empty prompt, else the reason it is not.
    Anything not positively recognized is a reason: a stray Enter answers whatever dialog is open."""
    lines = [l.replace("\xa0", " ").rstrip() for l in lines]
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
        if SPINNER.match(l):
            return f"turn in progress: {l[:60]}"
    rules = [i for i, l in enumerate(lines) if RULE.match(l.strip())]
    if len(rules) < 2:
        return "no prompt box"
    box = [l.strip() for l in lines[rules[-2] + 1:rules[-1]] if l.strip()]
    if len(box) != 1 or not box[0].startswith("❯"):
        return "prompt box not recognized"
    text = box[0][1:].strip()
    if text and not PLACEHOLDER.match(text):
        return "the human is typing"
    return None


def orca(*args):
    p = subprocess.run([ORCA, *args, "--json"], capture_output=True, text=True, timeout=30)
    if p.returncode:
        raise Stop(f"orca {' '.join(args[:2])} failed: {(p.stderr or p.stdout).strip()[:200]}")
    return json.loads(p.stdout)["result"]


def screen(handle):
    t = orca("terminal", "read", "--terminal", handle, "--screen")["terminal"]
    if t.get("source") not in (None, "screen"):
        raise Stop(f"no rendered screen ({t.get('source')})")
    return t.get("tail") or [], t.get("draft") or ""


def reloaded(after):
    return any(l.strip().startswith("⎿") and "Reloaded" in l for l in after)


def turn_started(after):
    return classify(after, "✳") is not None  # no longer idle at an empty prompt: the text was taken


def try_send(term, command, confirmed=reloaded):
    handle = term["handle"]
    first, draft = screen(handle)
    reason = classify(first, term.get("title") or "", draft)
    if reason:
        return reason
    time.sleep(SETTLE_S)
    second, draft = screen(handle)
    if second != first or draft:
        return "screen changed between two reads (someone may be typing)"
    orca("terminal", "send", "--terminal", handle, "--text", command, "--enter")
    time.sleep(CONFIRM_S)
    after, _ = screen(handle)
    if not confirmed(after):
        return "sent, but the screen does not show it was taken"
    return None


def nudge(handle, text):
    """Type one line into another session, under the same idle checks as the reload broadcast."""
    try:
        term = next(t for t in orca("terminal", "list")["terminals"] if t["handle"] == handle)
        if term.get("agentIdentity") != "claude" or not term.get("writable"):
            raise Stop("not a writable Claude terminal")
        reason = try_send(term, text, confirmed=turn_started)
    except (Stop, StopIteration, subprocess.TimeoutExpired, ValueError, KeyError) as e:
        reason = str(e) or "no such terminal"
    print(reason or "sent")
    return 1 if reason else 0


def broadcast(kind, dry_run):
    path = STATE / "reload-pending.json"
    pending = read_json(path, {})
    kind = kind or pending.get("kind")
    if not kind:
        print("nothing to reload")
        return 0
    try:
        terms = [t for t in orca("terminal", "list")["terminals"]
                 if t.get("agentIdentity") == "claude" and t.get("connected") and t.get("writable")]
    except (Stop, subprocess.TimeoutExpired, ValueError, KeyError) as e:
        print(f"cannot list Orca terminals, reload stays pending: {e}")
        return 1
    done, failed = set(pending.get("done", [])), {}
    for term in terms:
        h = term["handle"]
        if h in done:
            continue
        if dry_run:
            first, draft = screen(h)
            print(f"{h}  {classify(first, term.get('title') or '', draft) or 'would send ' + RELOAD[kind]}")
            continue
        try:
            reason = try_send(term, RELOAD[kind])
        except (Stop, subprocess.TimeoutExpired, ValueError, KeyError) as e:
            reason = str(e)
        if reason:
            failed[h] = {"title": term.get("title"), "reason": reason}
        else:
            done.add(h)
        print(f"{h}  {reason or 'sent ' + RELOAD[kind]}")
    if dry_run:
        return 0
    live = {t["handle"] for t in terms}
    if failed:
        write_json(path, dict(pending, kind=kind, done=sorted(done & live), failed=failed))
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
