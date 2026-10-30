#!/usr/bin/env python3
"""Screen watch over every agent terminal, for the cards no Run lists.

`orch wait` reads the screens of a program's own workers. A card started with `orca worktree create` (a
`dispatch-card` card, a lead) is not a worker, and a session with no program waits on its inbox with
`check --peek --wait`, which reads no screen: such cards sat for hours on a prompt only a human could answer.

Usage: orch watch-cards [--interval 180] [--idle-min 30] [--goal-lost-min 15] [--min-free-gb 15]
                        [--timeout-ms 3300000] [--once] [--self HANDLE] [--state PATH]
       orch watch-cards --card HANDLE [--within 240]

Each round reads every agent terminal but this session's own and exits on a new finding: a lost login or a
stopped goal (auth), a select menu, a permission prompt, an interrupted turn, a full disk, an API retry loop,
a `/goal` card with no output for --idle-min minutes, a card once seen under `/goal` that sits on an empty
prompt without it for --goal-lost-min minutes, or free disk under --min-free-gb. A finding still on screen is
not reported again, also across restarts (--state); once it clears, the next one is new. It never types into a
terminal: what to answer is the call of the orchestrator or lead that started the card. Exit 0 always; the
output says why it ended (STUCK lines, QUIET or OK).

--card checks one card just created: it exits STUCK on the first finding, and at --within seconds prints
STARTED when the screen shows `/goal active` and the card has written output since the check began, ENDED
when `/goal` was on and is off now, CLOSED when its terminal went away after it was seen, else STUCK.
"""
import argparse, hashlib, json, os, pathlib, re, shutil, sys, tempfile, time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import prog

STATE_DIR = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser() / "card-watch"
# Claude Code's own messages for a lost login and a goal it stopped. They must open the line (after the ⏺/⎿/⚠
# glyph), so a turn that quotes one mid-sentence does not match.
AUTH = re.compile(r"(?:Please run /login\b|(?:Not logged in|Login expired|OAuth token revoked) · Please run /login"
                  r"|Session expired\. Please run /login|API Error: 403\b"
                  r"|Goal cleared after an unrecoverable error \(|Goal paused · )")
GLYPHS = re.compile(r"^[\s⏺⎿⚠✗●◯○\ufe0f]+")  # Claude Code's own marks only: a "- " bullet, `, > or 「 is not stripped
# A turn in progress: tool output under ⎿ can carry the very same text as a real 403, so none counts while it runs.
SPINNER = re.compile(r"^\s*(?:[✻✶✳✢✽]\s|[·*]\s+\w+…)")
AUTH_QUIET_MIN = 2  # a dead session writes nothing; a working one quoting the text keeps writing


def agent_terminals(listing, self_handle):
    return [t for t in listing if t.get("agentIdentity") and t.get("connected", True) and t.get("handle") != self_handle]


def auth_line(tail):
    """The newest auth or goal-stop message, unless an assistant turn (⏺) came after it or a turn is still running.

    Only the lines above the input box count: the status area below it lists agents with the same ⏺ glyph."""
    box = max((i for i, l in enumerate(tail) if l.strip().startswith("❯")), default=len(tail))
    if any(SPINNER.match(l) for l in tail[:box]):
        return None
    for l in reversed(tail[:box]):
        s = l.strip()
        if AUTH.match(GLYPHS.sub("", s)):
            return GLYPHS.sub("", s)
        if s.startswith("⏺"):
            return None
    return None


def under_goal(t, tail):
    return any("/goal active" in l for l in tail) or "/goal active" in (t.get("preview") or "")


def scan(terms, seen, read, now_ms, idle_min, free_gb, min_free_gb, goal_lost_min=15):
    """Lines for findings not in seen; seen becomes this round's findings, so a cleared one can come back.

    seen["goals"] lists the handles once seen under `/goal`: a `/goal` card's goal has no other record, and only
    such a card is expected to keep one."""
    out, now, retrying = [], {}, []
    goals = set(seen.get("goals") or []) & {t["handle"] for t in terms}
    for t in terms:
        h = t["handle"]
        try:
            lines = read(h)
        except (SystemExit, Exception):
            continue
        name = (t.get("worktreePath") or "").rstrip("/").rsplit("/", 1)[-1] or h
        tail = lines[-25:]
        idle = (now_ms - (t.get("lastOutputAt") or now_ms)) / 60000
        auth = auth_line(tail) if idle >= AUTH_QUIET_MIN else None
        hit, goal = prog.screen_prompt(lines), under_goal(t, tail)
        if goal:
            goals.add(h)
        if auth:
            kind, question, key = "auth", auth, "auth:" + hashlib.sha1(auth.encode()).hexdigest()[:12]
        elif hit:
            kind, question = hit
            key = kind + ":" + hashlib.sha1("\n".join(prog.prompt_block(tail)).encode()).hexdigest()[:12]
        elif goal and idle > idle_min:
            kind, question, key = f"a /goal card silent {int(idle)} min", "", f"idle:{t.get('lastOutputAt')}"
        elif not goal and h in goals and idle > goal_lost_min and any(l.strip() == "❯" for l in tail):
            kind, question, key = f"goal lost, idle {int(idle)} min", "", f"lost:{t.get('lastOutputAt')}"
        else:
            continue
        now[h] = key
        if seen.get(h) == key:
            continue
        if kind == "an API retry loop":
            retrying.append((name, h))
        else:
            out += [f"STUCK {name} ({kind}): " + (f'"{question}"' if question else "read its screen"),
                    f"  orca terminal read --terminal {h} --screen"]
    if len(retrying) >= 3:
        # Cards share one account's usage window: near its end they all retry at once, and no card is at fault.
        out.append(f"STUCK {len(retrying)} cards in an API retry loop at once (a usage limit, likely): "
                   + ", ".join(n for n, _ in retrying))
    else:
        for n, h in retrying:
            out += [f"STUCK {n} (an API retry loop): read its screen", f"  orca terminal read --terminal {h} --screen"]
    if free_gb < min_free_gb:
        now["disk"] = "low"
        if seen.get("disk") != "low":
            out.append(f"STUCK disk (free {free_gb:.1f} GiB, under {min_free_gb}): run reap-resources")
    if goals:
        now["goals"] = sorted(goals)
    seen.clear()
    seen.update(now)
    return out


def check_start(handle, read, listing, within, interval, clock=time.time, sleep=time.sleep):
    """One card just created: STUCK on the first finding, else at the deadline STARTED, ENDED, CLOSED or STUCK.

    `/goal active` shows the moment `/goal` is typed, and cards have lost their login a few turns later, so it
    is judged at the end of the window, not on first sight. A short task can finish inside the window: a card
    seen under `/goal` whose goal is now off, or whose terminal is gone, ended rather than failed to start."""
    def tail_of(h):
        try:
            return read(h)[-25:]
        except (SystemExit, Exception):
            return []
    start_ms, deadline, seen, was_goal = clock() * 1000, clock() + within, None, False
    while True:
        t = next((t for t in listing() if t.get("handle") == handle), None)
        if t:
            seen = t
            was_goal = was_goal or under_goal(t, tail_of(handle))
            out = scan([t], {}, read, clock() * 1000, 10**9, 1.0, 0)
            if out:
                return out
        if clock() + interval > deadline:
            break
        sleep(interval)
    name = ((seen or {}).get("worktreePath") or "").rstrip("/").rsplit("/", 1)[-1] or handle
    if not t:
        if seen:
            return [f"CLOSED {name}: its terminal is gone after it started; read its report or PR before starting it again"]
        return [f"STUCK {handle} (not started): no agent terminal with this handle"]
    tail = tail_of(handle)
    goal = under_goal(t, tail)
    if goal and (t.get("lastOutputAt") or 0) > start_ms:
        return [f"STARTED {name}: /goal active, output since the check began"]
    if was_goal and not goal:
        return [f"ENDED {name}: /goal was active and is off now (met or cleared); read its screen before starting it again",
                f"  orca terminal read --terminal {handle} --screen"]
    last = next((l.strip() for l in reversed(tail) if l.strip()), "")
    return [f'STUCK {name} (not started): no /goal active with new output; last line "{last}"',
            f"  orca terminal read --terminal {handle} --screen"]


def load(path):
    try:
        seen = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return seen if isinstance(seen, dict) else {}


def save(path, seen):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".card-watch-")
    with os.fdopen(fd, "w") as f:
        json.dump(seen, f)
    os.replace(tmp, path)


def main(argv):
    ap = argparse.ArgumentParser(prog="orch watch-cards", description=__doc__.split("\n\n")[0])
    ap.add_argument("--interval", type=int, default=180, help="seconds between rounds")
    ap.add_argument("--idle-min", type=int, default=30)
    ap.add_argument("--goal-lost-min", type=int, default=15)
    ap.add_argument("--min-free-gb", type=float, default=15)
    ap.add_argument("--timeout-ms", type=int, default=3300000)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--self", dest="self_handle", default=os.environ.get("ORCA_TERMINAL_HANDLE"))
    ap.add_argument("--state", type=pathlib.Path)
    ap.add_argument("--card", help="the agentTerminalHandle of a card just created: check its start only")
    ap.add_argument("--within", type=int, default=240, help="--card: seconds before judging the start")
    a = ap.parse_args(argv)
    listing = lambda: agent_terminals(prog.orca_json("terminal", "list").get("terminals", []), a.self_handle)
    if a.card:
        print("\n".join(check_start(a.card, prog.read_screen, listing, a.within, min(a.interval, 30))), flush=True)
        return
    state = a.state or STATE_DIR / f"{a.self_handle or 'default'}.json"
    seen, deadline = load(state), time.time() + a.timeout_ms / 1000
    while True:
        terms = listing()
        free_gb = shutil.disk_usage(pathlib.Path.home()).free / 2**30
        lines = scan(terms, seen, prog.read_screen, time.time() * 1000, a.idle_min, free_gb, a.min_free_gb,
                     a.goal_lost_min)
        save(state, seen)
        if lines:
            print("\n".join(lines), flush=True)
            return
        if a.once:
            print(f"OK {len(terms)} agent terminals, nothing new", flush=True)
            return
        if time.time() + a.interval > deadline:
            print("QUIET nothing new; start the watch again", flush=True)
            return
        time.sleep(a.interval)


if __name__ == "__main__":
    main(sys.argv[1:])
