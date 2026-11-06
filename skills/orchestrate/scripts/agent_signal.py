#!/usr/bin/env python3
"""`claude agents --json` as a second signal beside the screen, recorded so the two can be compared.

It observes and judges nothing else: no card's activity, finding or wake-up depends on it. A query that failed, a
state outside KNOWN, or a card that cannot be tied to one session is "no signal", never healthy. Rows reach
<state>/agent-signal.jsonl as `round` (every round, the heartbeat), `card` (a card's reading changed, or a new log file
needs its baseline) and `gone` (a tracked terminal left the listing). A card's time in a state ends at the last round
row while it was tracked, not at its next change.

A terminal is tied to its session only by the SessionStart record (roles.session_for): a cwd or a process ancestor does
not prove which session a terminal runs, so a terminal with no record has no signal, whatever shares its path.
"""
import contextlib, fcntl, json, os, pathlib, subprocess, sys, time

sys.path.append(str(pathlib.Path(__file__).resolve().parents[3] / "hooks"))
from roles import session_for  # noqa: E402

STATE = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser()
LOG = STATE / "agent-signal.jsonl"
LOCK = STATE / "agent-signal.lock"
NO_SIGNAL = "신호 없음"
KNOWN = {"busy", "idle", "blocked"}
# Claude Code's `waiting` carries `waitingFor` (seen: "dialog open"): the session is parked on a prompt for a person.
ALIASES = {"waiting": "blocked"}
ROTATE_BYTES = 32 * 2**20
LOCK_WAIT_S = 2
RAW_LIMIT = 500


def read(run=subprocess.run, timeout=10):
    """(rows, problem, raw): the listed sessions, why there is no listing (None when there is), stdout's head."""
    try:
        r = run(["claude", "agents", "--json"], capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return [], f"{type(e).__name__}: {str(e)[:120]}", ""
    raw = (r.stdout or "")[:RAW_LIMIT]
    if r.returncode:
        return [], f"exit {r.returncode}: {(r.stderr or '').strip()[:120]}", raw
    try:
        rows = json.loads(r.stdout)
    except ValueError:
        return [], "not JSON", raw
    return (rows, None, raw) if isinstance(rows, list) else ([], "not a JSON array", raw)


def state_of(row):
    """A row's state: interactive rows carry `status`, background rows `state`."""
    return row.get("status") if "status" in row else row.get("state")


def _raw(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)[:RAW_LIMIT]


def reading_of(handle, rows, problem):
    """{agents, raw, why}: agents is a KNOWN value or None, and None always comes with a why."""
    if problem:
        return {"agents": None, "raw": None, "why": f"claude agents failed: {problem}"}
    row, why = session_for(handle, rows)
    if row is None:
        return {"agents": None, "raw": None, "why": why}
    if "status" not in row and "state" not in row:
        return {"agents": None, "raw": _raw(row), "why": "no status field"}
    value = state_of(row)
    if isinstance(value, str) and value in KNOWN:
        return {"agents": value, "raw": value, "why": None}
    if isinstance(value, str) and value in ALIASES:
        return {"agents": ALIASES[value], "raw": value, "why": None}
    return {"agents": None, "raw": _raw(value), "why": f"unknown state {_raw(value)[:40]}"}


def is_claude(t):
    return str(t.get("agentIdentity") or "").split(":")[0] == "claude"


def readings(terms, rows, problem):
    """({handle: reading}, warnings) over agent terminals. A reading with applies False is a card the list never holds
    (Codex), which is screen only and no gap. One warning line per kind of format trouble, no version branch."""
    out, objects = {}, [r for r in rows if isinstance(r, dict)]
    for t in terms:
        h = t.get("handle")
        if is_claude(t):
            out[h] = {**reading_of(h, objects, problem), "applies": True}
        else:
            out[h] = {"agents": None, "raw": None, "why": None, "applies": False}
    return out, warnings(rows, problem, out)


def warnings(rows, problem, read_out=None):
    out = [f"claude agents: {problem}; screen only this round"] if problem else []
    if any(not isinstance(r, dict) for r in rows):
        out.append("claude agents: a row is not an object; passed through")
    if any(isinstance(r, dict) and not isinstance(r.get("sessionId"), str) for r in rows):
        out.append("claude agents: a row has no sessionId; passed through")
    whys = {(v.get("why") or "") for v in (read_out or {}).values()}
    if "no status field" in whys:
        out.append("claude agents: a row has neither status nor state; passed through")
    if any(w.startswith("unknown state") for w in whys):
        out.append("claude agents: a state outside busy, idle, blocked; passed through")
    return out


def transcript_side(transcript):
    return {"idle": "idle", "tool": "busy", "working": "busy"}.get(transcript)


def compare(reading, screen, finding="", basis="", transcript=None, wait=None):
    """The card's row: both signals side by side. differs names the sides that disagree with agents; unknown compares
    with nothing. The transcript counts only where Orca looked and found no wait: it cannot see a prompt."""
    agents = reading.get("agents")
    differs = []
    if agents and screen in KNOWN and screen != agents:
        differs.append("screen")
    side = transcript_side(transcript)
    if agents and side and wait == "none" and side != agents:
        differs.append("transcript")
    return {"screen": screen, "finding": finding or "", "basis": basis or "", "transcript": transcript, "wait": wait,
            "agents": agents, "raw": reading.get("raw"), "why": reading.get("why"), "differs": differs}


def evidence(reading, screen=None):
    """The evidence line's text, '' for a card the list never holds."""
    if not reading or not reading.get("applies", True):
        return ""
    if not reading.get("agents"):
        return f"{NO_SIGNAL} ({reading.get('why')})"
    if screen in KNOWN and screen != reading["agents"]:
        return f"{reading['agents']} (screen {screen})"
    return reading["agents"]


def gaps(reads):
    """{handle: why} for the Claude cards with no signal."""
    return {h: r.get("why") for h, r in reads.items() if r.get("applies", True) and not r.get("agents")}


def _generation(path):
    try:
        return os.stat(path).st_ino
    except OSError:
        return None


def append(build, path=None, lock=None):
    """Rotate, read the file's inode, build the rows from it and write them, all under the lock: rows chosen outside it
    could land in a file another watcher just started, without their baseline. build(inode) -> rows; the inode is None
    for a new file. Returns the written file's inode. A lock held LOCK_WAIT_S raises OSError: observing never stalls
    a watcher."""
    path, lock = pathlib.Path(path or LOG), pathlib.Path(lock or LOCK)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as held:
        for _ in range(int(LOCK_WAIT_S * 10)):
            with contextlib.suppress(BlockingIOError):
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            time.sleep(0.1)
        else:
            raise OSError("agent-signal lock held")
        with contextlib.suppress(OSError):
            if path.stat().st_size >= ROTATE_BYTES:
                os.replace(path, path.with_name(path.name + ".1"))
        rows = build(_generation(path))
        with open(path, "a", encoding="utf-8") as f:
            f.write("".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows))
        return os.stat(path).st_ino


def log_round(source, by, rid, cards, memo, problem=None, raw=None, warned=(), names=None, now=None, path=None, lock=None):
    """Write one round and return the next memo. cards: {handle: compare() row}. memo: {"gen", "cards"}, the log file's
    inode and each card's last row. A changed inode means a new file, so every card is written again as its baseline
    and that file can be read alone. A failed write costs the round's rows, never the round."""
    path, names, memo = pathlib.Path(path or LOG), names or {}, memo or {}
    now = time.time() if now is None else now
    head = {"t": now, "round": rid, "source": source, "by": by}
    old = memo.get("cards") or {}
    summary = {"kind": "round", **head, "terminals": len(cards), "problem": problem,
               "unknown_screen": sum(1 for c in cards.values() if c["screen"] not in KNOWN),
               "no_signal": sum(1 for c in cards.values() if c["why"]),
               "differs": sum(1 for c in cards.values() if c["differs"]),
               **({"raw": (raw or "")[:RAW_LIMIT], "warnings": list(warned)} if problem or warned else {})}

    def build(gen):
        baseline = gen is None or gen != memo.get("gen")
        return [summary, *({"kind": "card", **head, "handle": h, "card": names.get(h, h), **c}
                           for h, c in cards.items() if baseline or old.get(h) != c),
                *({"kind": "gone", **head, "handle": h} for h in old if h not in cards)]

    try:
        return {"gen": append(build, path, lock), "cards": cards}
    except OSError:
        return memo
