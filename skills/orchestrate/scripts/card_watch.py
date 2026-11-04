#!/usr/bin/env python3
"""Screen watch over every agent terminal, for the cards no Run lists.

`orch wait` reads the screens of a program's own workers. A card started with `orca worktree create` (a
`dispatch-card` card, a lead) is not a worker, and a session with no program waits on its inbox with
`check --peek --wait`, which reads no screen: such cards sat for hours on a prompt only a human could answer.

Usage: orch watch-cards [--interval 180] [--idle-min 30] [--goal-lost-min 15] [--min-free-gb 15]
                        [--timeout-ms 3300000] [--once] [--self HANDLE] [--state PATH]
       orch watch-cards --card HANDLE [--within 240]

Each round reads every agent terminal but this session's own and exits on a new finding: a lost login or a
stopped goal (auth), a select menu, a permission prompt or an interrupted turn (Orca's agentWait decides whether
it waits on a human; the screen only where Orca has no verdict), a full disk, an API retry loop,
a `/goal` card with no output for --idle-min minutes, a card once seen under `/goal` that sits on an empty
prompt without it for --goal-lost-min minutes, or free disk under --min-free-gb. A finding still on screen is
not reported again, also across restarts (--state); once it clears, the next one is new. Each round stamps
`last_scan_at` in --state, which `orch inbox`, `orch wait`, `orch patrol` and the dashboard read to tell a watcher
that ended (a finding, QUIET, a killed session) from one still running. It never types into a
terminal: what to answer is the call of the orchestrator or lead that started the card. Exit 0 always; the
output says why it ended (STUCK lines, QUIET, OK or PARTIAL).

Each round also asks `claude agents --json` (agent_signal.py) and records both signals in <state>/agent-signal.jsonl.
That second signal only observes: it changes no finding and wakes nobody, and a STUCK line carries it as an `agents:`
evidence line. OK means the round saw everything: every screen judged, every Claude card with a signal. Otherwise
the same line opens PARTIAL and names what is missing ("신호 없음" for the signal); QUIET gets the same tail.

--card checks one card just created: it exits STUCK on the first finding, and at --within seconds prints
STARTED when the screen shows `/goal active` and the card has written output since the check began, ENDED
when `/goal` was on and is off now, CLOSED when its terminal went away after it was seen, else STUCK.
"""
import argparse, contextlib, hashlib, json, os, pathlib, re, shutil, sys, tempfile, time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import agent_signal, prog

STATE_DIR = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser() / "card-watch"
# Claude Code's own messages for a lost login and a goal it stopped. They must open the line (after the ⏺/⎿/⚠
# glyph), so a turn that quotes one mid-sentence does not match.
AUTH = re.compile(r"(?:Please run /login\b|(?:Not logged in|Login expired|OAuth token revoked) · Please run /login"
                  r"|Session expired\. Please run /login|API Error: 403\b"
                  r"|Goal cleared after an unrecoverable error \(|Goal paused · )")
GLYPHS = re.compile(r"^[\s⏺⎿⚠✗●◯○\ufe0f]+")  # Claude Code's own marks only: a "- " bullet, `, > or 「 is not stripped
# A turn in progress: tool output under ⎿ can carry the very same text as a real 403, so none counts while it runs.
# Only a live spinner: a finished turn leaves "✻ Cooked for 41s", which has neither mark.
SPINNER = re.compile(r"^\s*[✻✶✳✢✽·*]\s+\S.*(?:…|esc to interrupt)")
AUTH_QUIET_MIN = 2  # a dead session writes nothing; a working one quoting the text keeps writing
# Not sync.BACKGROUND: its "N agents" also shows on sessions `claude agents` calls idle.
SHELLS = re.compile(r"\b\d+ shells?\b|shells? still running")


def agent_terminals(listing, self_handle):
    return [t for t in listing if t.get("agentIdentity") and t.get("connected", True) and t.get("handle") != self_handle]


def auth_line(tail):
    """The newest auth or goal-stop message, unless an assistant turn (⏺) or a live spinner came after it.

    Only the lines above the input box count: the status area below it lists agents with the same ⏺ glyph."""
    box = max((i for i, l in enumerate(tail) if l.strip().startswith("❯")), default=len(tail))
    for l in reversed(tail[:box]):
        s = l.strip()
        if AUTH.match(GLYPHS.sub("", s)):
            return GLYPHS.sub("", s)
        if s.startswith("⏺") or SPINNER.match(l):
            return None
    return None


def under_goal(t, tail):
    return any("/goal active" in l for l in tail) or "/goal active" in (t.get("preview") or "")


def screen_state(tail, quiet_min, finding=""):
    """(busy | idle | blocked | unknown, basis): the screen's judgement in the words `claude agents` uses, for comparing.
    A live turn's spinner keeps redrawing, so a quiet empty prompt is what shows idle; no finding alone does not.
    `claude agents` says busy for a session whose turn ended while a background shell runs, so that counts as busy
    under its own basis, not as a disagreement on half the cards."""
    if finding:
        return "blocked", finding
    box = max((i for i, l in enumerate(tail) if l.strip().startswith("❯")), default=len(tail))
    if any(SPINNER.match(l) for l in tail[:box]):
        return "busy", "spinner"
    if any(SHELLS.search(l) for l in tail[box + 1:]):  # the status bar only: a turn's text can say the same words
        return "busy", "background running"
    if quiet_min >= AUTH_QUIET_MIN and any(l.strip() == "❯" for l in tail):
        return "idle", "quiet prompt"
    return "unknown", "no evidence"


def screen_view(t, lines, waited, now_ms):
    """(state, basis, finding) of a screen read elsewhere (patrol), by the rules scan applies to its own."""
    tail = (lines or [])[-25:]
    quiet = (now_ms - (t.get("lastOutputAt") or now_ms)) / 60000
    hit = prog.judge_wait(waited, lines)
    finding = "auth" if quiet >= AUTH_QUIET_MIN and auth_line(tail) else hit[0] if hit else ""
    return (*screen_state(tail, quiet, finding), finding)


def card_name(t):
    return (t.get("worktreePath") or "").rstrip("/").rsplit("/", 1)[-1] or t.get("handle")


def scan(terms, seen, read, now_ms, idle_min, free_gb, min_free_gb, goal_lost_min=15, waits=None, agents=None, states=None):
    """Lines for findings not in seen; seen becomes this round's findings, so a cleared one can come back.

    seen["goals"] lists the handles once seen under `/goal`: a `/goal` card's goal has no other record, and only
    such a card is expected to keep one. waits (handle -> Orca's agentWait, prog.agent_waits) decides whether a
    terminal waits on a human (prog.judge_wait); the screen only for what Orca does not judge or did not look at.
    agents (agent_signal.readings) adds an evidence line under a STUCK line and decides nothing; states, when given,
    collects each card's (state, basis, finding)."""
    waits = waits or {}
    states = {} if states is None else states
    out, now, retrying = [], {}, []
    goals = set(seen.get("goals") or []) & {t["handle"] for t in terms}

    def said(h):
        text = agent_signal.evidence((agents or {}).get(h), states.get(h, (None,))[0])
        return [f"  agents: {text}"] if text else []

    for t in terms:
        h = t["handle"]
        waited = waits.get(h, prog.NOT_LOOKED)
        try:
            lines = read(h)
        except (SystemExit, Exception):
            # Orca's agentWait needs no screen: a wait it found is reported even when the pane cannot be read.
            if not isinstance(waited, dict):
                states[h] = ("unknown", "unreadable", "")
                continue
            lines = None
        name = card_name(t)
        tail = (lines or [])[-25:]
        idle = (now_ms - (t.get("lastOutputAt") or now_ms)) / 60000
        auth = auth_line(tail) if idle >= AUTH_QUIET_MIN else None
        hit = prog.judge_wait(waited, lines)
        finding = "auth" if auth else hit[0] if hit else ""
        state, basis = screen_state(tail, idle, finding)
        states[h] = (state, "unreadable" if lines is None else basis, finding)  # a wait Orca found stands without the pane
        goal = under_goal(t, tail)
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
                    *said(h), f"  orca terminal read --terminal {h} --screen"]
    if len(retrying) >= 3:
        # Cards share one account's usage window: near its end they all retry at once, and no card is at fault.
        out.append(f"STUCK {len(retrying)} cards in an API retry loop at once (a usage limit, likely): "
                   + ", ".join(n for n, _ in retrying))
        out += [f"  {n}{line.strip().removeprefix('agents:')}" for n, h in retrying for line in said(h)]
    else:
        for n, h in retrying:
            out += [f"STUCK {n} (an API retry loop): read its screen", *said(h),
                    f"  orca terminal read --terminal {h} --screen"]
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
    if was_goal and not goal and auth_line(tail):  # a goal cleared by a 403 in the last two quiet minutes
        return [f'STUCK {name} (auth): "{auth_line(tail)}"', f"  orca terminal read --terminal {handle} --screen"]
    if was_goal and not goal:
        return [f"ENDED {name}: /goal was active and is off now (met or cleared); read its screen before starting it again",
                f"  orca terminal read --terminal {handle} --screen"]
    last = next((l.strip() for l in reversed(tail) if l.strip()), "")
    return [f'STUCK {name} (not started): no /goal active with new output; last line "{last}"',
            f"  orca terminal read --terminal {handle} --screen"]


DOWN_GRACE_S = 300  # the orchestrator restarts a watcher that ended in the same wake; this covers that turn
DOWN_RENOTIFY_S = 1800


def last_scan(handle, state_dir=None):
    """(time of the watcher's last finished round, its interval), or None when this terminal never ran one. A state
    file written before last_scan_at existed is stamped by its mtime, as every round saves it."""
    path = (state_dir or STATE_DIR) / f"{handle}.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    seen = load(path)
    return float(seen.get("last_scan_at") or mtime), int(seen.get("interval") or 180)


def down_line(handle, now=None, state_dir=None):
    """`WATCH-CARDS DOWN` when the watcher this terminal ran has not finished a round in two intervals and the grace,
    shown again every 30 minutes while it lasts; None otherwise, and for a terminal that never ran one."""
    now = now or time.time()
    last = handle and last_scan(handle, state_dir)
    if not last or now - last[0] <= 2 * last[1] + DOWN_GRACE_S:
        return None
    mark = (state_dir or STATE_DIR) / f".down-{handle}"
    try:
        shown = float(mark.read_text())
    except (OSError, ValueError):
        shown = 0
    if shown > last[0] and now - shown < DOWN_RENOTIFY_S:
        return None
    with contextlib.suppress(OSError):
        mark.write_text(str(now))
    return (f"WATCH-CARDS DOWN: its last round ended {int((now - last[0]) // 60)} min ago; start it again "
            "(orch watch-cards, run_in_background, no pipe)")


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


def missing(terms, reads, states, problem):
    """What this round could not judge, as an OK or QUIET line's tail; '' when it saw everything. Only a Claude card
    owes a screen verdict: Codex's spinner is not known here, so its unread pane alone counts."""
    blind = []
    for t in terms:
        state, basis = states.get(t["handle"], ("unknown", "unreadable", ""))[:2]
        if basis == "unreadable" or state == "unknown" and agent_signal.is_claude(t):
            blind.append(f"{card_name(t)} ({basis})")
    gaps =[f"{card_name(t)} ({reads[t['handle']]['why']})" for t in terms
            if t["handle"] in agent_signal.gaps(reads)]
    parts = [f"화면 판정 없음 {len(blind)}: " + ", ".join(blind)] if blind else []
    if problem:
        parts.append(f"{agent_signal.NO_SIGNAL}: claude agents failed: {problem}")
    elif gaps:
        parts.append(f"{agent_signal.NO_SIGNAL} {len(gaps)}: " + ", ".join(gaps))
    return "; ".join(parts)


def record(seen, memo, terms, reads, states, problem, raw, warned, by):
    """Both signals of one round into the comparison log; seen keeps what the next round compares with."""
    cards = {}
    for t in terms:
        state, basis, finding = states.get(t["handle"], ("unknown", "unreadable", ""))
        cards[t["handle"]] = agent_signal.compare(reads[t["handle"]], state, finding, basis)
    seen["signals"] = agent_signal.log_round("watch-cards", by, f"r-{int(time.time())}", cards, memo, problem, raw, warned,
                                             {t["handle"]: card_name(t) for t in terms})


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
        rows, problem, raw = agent_signal.read()
        reads, warned = agent_signal.readings(terms, rows, problem)
        states, memo = {}, seen.get("signals")
        free_gb = shutil.disk_usage(pathlib.Path.home()).free / 2**30
        lines = scan(terms, seen, prog.read_screen, time.time() * 1000, a.idle_min, free_gb, a.min_free_gb,
                     a.goal_lost_min, prog.agent_waits(), agents=reads, states=states)
        record(seen, memo, terms, reads, states, problem, raw, warned, a.self_handle)
        seen.update(last_scan_at=time.time(), interval=a.interval)
        save(state, seen)
        gap = missing(terms, reads, states, problem)
        if lines:
            print("\n".join(lines + ([f"PARTIAL {gap}"] if gap else []) + warned), flush=True)
            return
        if a.once:
            head = f"PARTIAL {len(terms)} agent terminals, nothing new; {gap}" if gap else f"OK {len(terms)} agent terminals, nothing new"
            print("\n".join([head] + warned), flush=True)
            return
        if time.time() + a.interval > deadline:
            print("\n".join([f"QUIET nothing new; start the watch again" + (f"; {gap}" if gap else "")] + warned), flush=True)
            return
        time.sleep(a.interval)


if __name__ == "__main__":
    main(sys.argv[1:])
