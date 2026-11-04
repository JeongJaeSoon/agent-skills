#!/usr/bin/env python3
"""Session patrol: one round over every agent card, by rules fixed in code, with no model in the loop.

Usage: orch patrol [--once] [--if-due] [--dry-run] [--json]
       orch patrol list [--all]
       orch patrol ack <id>... | --all
       orch patrol timing

A round reads Orca first (terminal list, worktree ps --agent-wait, worker-list over every Run), then the
collector's state.json for what Orca does not hold (each card's parent, linked PR and PR details, the board), the
tail of each card's session transcript, the `orch sent` log and the last pressure round. It judges:

  I  idle      a card outside any Run whose session ended its turn 15+ min ago with nothing armed to wake it
               (dash.step_motion: ScheduleWakeup, Monitor or a background command park it) and no prompt waiting
               on a human (that is `orch watch-cards`'s)
  X  exited    a Run worker Orca sees as exited with its task still in progress; a card outside a Run with no
               agent left whose transcript ends on an API error or an error notice
  S  superseded  a card an `orch sent add --kind supersede --to <handle|card>` named 30+ min ago that still has an
               agent terminal
  O  orphan PR  a card with no agent and an open PR, 24h so, with no review requested and no CI running
  D  no ref    a board doing item naming no PR and no card, unchanged 24h; tidy shows it as ❔
  E  error loop  the same error line in three rounds in a row, each with new output (shown as ❔, never acted on)
  R  repeat    a card outside any Run, not waiting on a human, whose transcript ends in 3+ finished turns over 30+ min
               that tell the same thing with the same work (dash.repeat_turns: one error, one last message, one
               prompt such as a nudge, or /reload-* only); its card is "stalled", not "working", unless parked. Shown
               once to its owner, and never raised again for the same session and text (shown as ❔)
  H  stale     an agent card, Run workers included, whose session ended its turn, not waiting on a human, with a
               screen that shows it runs on what changed under it (sync.stale_signal: a hook error in the last turn
               saying its file is missing, as when its hooks.json names a script an update removed, or Claude
               Code's "/restart to apply"; a guard's deny is not one);
               its card is "stalled" (a Run worker stays "run"). A screen without that wording is not judged.
               `skills-sync broadcast` types the fix once the session is idle (shown as ❔)
  B  bg wait   an agent card, Run workers included, whose turn ended 15+ min ago, not waiting on a human, whose last
               message says it waits on a background task (bgwait_signal), while nothing will wake it: the transcript
               owes no armed background call or Monitor a result (not parked) and Claude Code's status line shows no
               shell, monitor, agent or task running. A screen without that wording is not judged (shown as ❔)
  L  load      at pressure warn or danger, a top consumer whose card is judged I, X, S or O
  W  watcher   an orchestrator whose `orch watch-cards` has not finished a round in an hour (reported here; the
               collector raises it on the dashboard)

Patrol records and shows; it never types into a terminal, closes one, stops a worker or removes a card. Findings
go to <state>/patrol.jsonl with detected_at, are cleared there when a later round no longer sees them, and are acked
by whoever dealt with them (`orch patrol ack`). `orch inbox` and `orch wait` show the unacked ones routed to them
(the card's parent session, else the orchestrator) every 30 minutes. X, E, L and O also go to the dashboard's
Needs you until cleared or acked. Card verdicts go to <state>/patrol-verdicts.json: `cards` holds each card's activity
(run, waiting, working, idle, exited, stalled), `hints` what a finding said of it (superseded, pr_open_no_session,
idle, exited, stalled), `check` the cards H, B and R found, each with the reason the brief shows on its ❔ row, and
`failed` the sources this round could not read. A finding never overwrites run, waiting or working in
`cards`. reap-hard, the pressure round and the janitor read `cards` only to keep a card judged working; no verdict
makes anything a target, and a card missing from it, in a failed round above all, is not evidence it is safe.

After judging, a round asks `claude agents --json` (agent_signal.py) and writes `signals` to that file and both
signals to <state>/agent-signal.jsonl. It only observes: nothing in `cards`, `failed`, `sources` or any rule reads it.
A finding's title ends in what the signal said. A screen is compared only where this round read one; the transcript
goes beside it as its own field. "OK nothing found" means the round missed nothing: every source and screen read,
every Claude card with a signal. Otherwise the line opens PARTIAL and names what is missing ("신호 없음" for the signal).

--if-due runs only when the last round is 25+ minutes old or the pressure level rose to warn or danger since it
(reap-hard calls this every pass, the pressure round when its level changes). The round gives up at 120 seconds.

The same file also carries the cleanup v2 fields, which nothing reads yet: schema, round, complete, sources, activity
(parked, unknown), timings, and candidates_t (when the scans started) with candidates/reports/alerts from ../../reap-resources/scripts/candidates.py. That phase
runs after the round lets go of its lock, within 240 seconds, is skipped at pressure danger, and writes only while the
file still holds its round. `orch patrol timing` shows p50 and p95 per source from <state>/patrol-rounds.jsonl.
"""
import contextlib, datetime as dt, fcntl, hashlib, json, os, pathlib, re, secrets, signal, subprocess, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(1, str(pathlib.Path(__file__).resolve().parents[2] / "reap-resources" / "scripts"))
import agent_signal, card_watch, cardguard, dash, fleet, prog  # noqa: E402
from card_watch import BACKGROUND  # noqa: E402
try:  # dash put the repo's scripts/ on the path
    from sync import STALE_COMMAND, stale_signal  # noqa: E402
except ImportError:  # an install without the repo's scripts/: H judges nothing
    STALE_COMMAND = {}

    def stale_signal(lines):
        return None

STATE = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser()
LOG = STATE / "patrol.jsonl"
VERDICTS = STATE / "patrol-verdicts.json"
MEMO = STATE / "patrol-state.json"
SHOWN = STATE / "patrol-shown.json"
PRESSURE = STATE / "pressure.json"
ROUNDS = STATE / "patrol-rounds.jsonl"
REAP_SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "reap-resources" / "scripts"

DUE_S = 25 * 60
BUDGET_S = 120
CANDIDATE_BUDGET_S = 240
AGENTS_MARGIN_S = 15  # agent_signal.read's 10 s, and the round's own writes after it
ROUNDS_KEEP = 1000
CURRENT = {}  # the running round's timings and failed sources, read by mark_cut
OWN_SOURCES = ("terminals", "waits", "workers", "state", "board", "sent", "consumers")
IDLE_MIN = dash.IDLE_MIN
SUPERSEDE_MIN = 30
ORPHAN_H = 24
NOREF_H = 24
ERROR_ROUNDS = 3
WATCH_DOWN_MIN = 60
RENOTIFY = dt.timedelta(minutes=30)
SCREEN_READS = 30
NEEDS_YOU = {"X", "E", "L", "O"}
LABEL = {"I": "idle", "X": "exited", "S": "superseded", "O": "orphan PR", "D": "no ref", "E": "error loop",
         "L": "load", "W": "watcher down", "R": "repeat", "H": "stale", "B": "background wait"}
BGWAIT_WHY = "백그라운드 작업을 기다리지만 깨울 작업이 없음"  # B's reason on the brief's ❔ row
VERDICT_OF = {"I": "idle", "X": "exited", "S": "superseded", "O": "pr_open_no_session", "R": "stalled", "H": "stalled"}
# H's two kinds: what the owner's line says, and the reason on the brief's ❔ row (Korean, as the brief is).
STALE_WHAT = {"hook": ("a hook error blocks its tools", "hook 오류로 도구가 막힘"),
              "restart": ("a Claude Code update waits on /restart", "업데이트가 /restart 를 기다림")}
REPEAT_WORD = {"error": "에러", "assistant": "답", "user": "입력", "reload": "reload"}
SHOW_ONCE = {"R"}  # pending_lines shows these once, not every RENOTIFY
# A stalled card loses patrol's protection, so reap closes it within days; no session stays open this long.
REPEATS_KEEP_DAYS = 90
ACTIVE = ("run", "waiting", "working")  # what a card's activity verdict keeps; reap.PATROL_KEEP reads the same three
# An error a tool or the CLI printed, at the start of a line once Claude Code's marks are gone.
ERROR_LINE = re.compile(r"^(?:Error\b|error(?:\[\w+\])?:|ERROR\b|FAILED\b|FAIL\b|Traceback\b|fatal:|panic:|"
                        r"API Error\b|.*\b(?:Permission denied|command not found|Exit code [1-9]\d*)\b)")
VOLATILE = re.compile(r"\d{4}-\d\d-\d\dT[\d:.]+Z?|\b\d{1,2}:\d\d(?::\d\d)?\b|\b\d+(?:\.\d+)?\s?(?:ms|s|sec|m|min)\b")


# ---------------------------------------------------------------- reading

def all_workers(deadline):
    """Every page of worker-list over every Run; a list cut short by the budget is a failure, not a smaller fleet."""
    return cardguard.worker_pages(deadline=deadline)


def card_name(path):
    return (path or "").rstrip("/").rsplit("/", 1)[-1]


transcript_tail = cardguard.transcript_tail


def background(lines):
    """Claude Code's status line shows a background shell, monitor or agent still running. A message typed in after
    the command was armed resets the transcript's record of it, so only the screen still shows it."""
    return bool(lines) and any(BACKGROUND.search(l) for l in lines[-6:])


# A last message that says the session waits on a background task still to finish (English or Korean). A launch
# ("started it in the background"), a report of one that finished ("waited for it, then applied", "기다리던 중 완료
# 알림을 받아") or a wait denied ("no need to wait") is not one: only a present or future wait counts.
# English counts a wait only as the session's state now: at the sentence's start, or after a present or future marker
# ("I'm", "still", "I'll"), so "after waiting …" or "while waiting …" is not one.
BG_WAIT = re.compile(r"(?:^|\b(?:I'm|I am|am|is|are|still|now|I'll|I will|will|keep|just)\s+)wait(?:ing|s)?\s+"
                     r"(?:on|for|until)\b.{0,60}?\bbackground\b"
                     r"|\b(?:until|once|when)\b.{0,20}?\bbackground\b.{0,60}?\b(?:finishes|completes|is done|ends)\b"
                     r"|백그라운드.{0,60}?(?:기다립니다|기다리는 중|기다리고 있|기다릴게|기다린다|대기 중|끝나면|끝나기를 기다"
                     r"|완료되면)", re.I)
# Judged per sentence, so a denied or past wait in one sentence neither makes nor hides a wait in another.
NO_WAIT = re.compile(r"\b(?:no need to|not|don't|do not|no longer|stopped)\s+(?:\w+\s+)?wait|\b(?:was|were|had been)\s+"
                     r"waiting\b|\bwaited\b|기다리지 않|기다릴 필요", re.I)
SENTENCE = re.compile(r"(?<=[.!?;。])\s+|;\s*")


def bgwait_signal(lines):
    """The session's last message, when it says it waits on a background task; else None. Only the last ⏺ block
    above the input box counts, its wrapped lines joined."""
    if not lines:
        return None
    box = max((i for i, l in enumerate(lines) if l.strip().startswith("❯")), default=len(lines))
    body = lines[:box]
    start = max((i for i, l in enumerate(body) if l.strip().startswith("⏺")), default=None)
    if start is None:
        return None
    text = " ".join(card_watch.GLYPHS.sub("", l.strip()) for l in body[start:] if l.strip() and not set(l.strip()) <= {"─"})
    text = text.replace("’", "'")  # a typographic apostrophe: "I’m waiting", "don’t wait"
    waits = any(BG_WAIT.search(s.strip()) and not NO_WAIT.search(s) for s in SENTENCE.split(text))
    return text[:300] if waits else None


def screen_of(facts, handle):
    """The screen read this round, else one read now (only an idle candidate needs it); None when unreadable."""
    if handle not in facts["screens"] and facts.get("read"):
        try:
            facts["screens"][handle] = facts["read"](handle)
        except (SystemExit, Exception):
            facts.setdefault("unread", set()).add(handle)
            return None
    return facts["screens"].get(handle)


def error_end(row):
    """The text of an API error or error notice that a transcript's last row is, else None."""
    if not row:
        return None
    if row.get("isApiErrorMessage") or (row.get("type") == "system" and row.get("level") == "error"):
        msg = row.get("message") if isinstance(row.get("message"), dict) else {}
        content = msg.get("content") if msg else row.get("content")
        if isinstance(content, list):
            content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
        return (str(content or "error").strip().splitlines() or ["error"])[0][:160]
    return None


def error_keys(lines):
    """Normalised error lines of the current turn: below the last ⏺, above the input box, none while a tool runs."""
    box = max((i for i, l in enumerate(lines) if l.strip().startswith("❯")), default=len(lines))
    body = lines[:box]
    if any(card_watch.SPINNER.match(l) for l in body[-3:]):
        return set()
    start = max((i for i, l in enumerate(body) if l.strip().startswith("⏺")), default=-1)
    out = set()
    for l in body[start + 1:]:
        s = card_watch.GLYPHS.sub("", l.strip())
        if ERROR_LINE.match(s):
            out.add(VOLATILE.sub("#", s)[:200])
    return out


def sent_supersedes(now, days=3):
    import sent
    since = now - dt.timedelta(days=days)
    try:
        return [r for r in sent.read_log(since) if r.get("kind") == "supersede"]
    except OSError:
        return []


def pressure_level():
    st = fleet.read_json(PRESSURE, {}) or {}
    return st.get("level") or "ok", st.get("t") or 0


def consumers():
    """pressure.consumers over the pressure settings: top processes and compose projects, each with its card."""
    sys.path.insert(0, str(REAP_SCRIPTS))
    import pressure
    procs, projects = pressure.consumers(pressure.config())
    return ([{"what": f"pid {i['pid']} {i['cmd'][:50]}", "cpu": i.get("cpu"), "card": i.get("card")} for i in procs]
            + [{"what": f"compose {g['project']}", "cpu": g.get("cpu"), "card": g.get("card")} for g in projects])


def read_board():
    """board.json, {} when there is none; an unreadable one raises, so D keeps its findings and clocks."""
    try:
        text = (fleet.state_dir() / "board.json").read_text()
    except FileNotFoundError:
        return {}
    board = json.loads(text)
    if not isinstance(board, dict):
        raise ValueError("board.json is not an object")
    return board


def gather(now, deadline, memo):
    """Everything a round judges, read once. Each source that fails leaves its part empty and says so in notes."""
    facts, notes = {"now": now}, []

    failed = facts["failed"] = set()
    timings = facts["timings"] = CURRENT["timings"] = {}  # CURRENT: what a budget cut still records
    CURRENT["failed"] = failed

    def take(name, fn, default, source=None):
        t0 = time.monotonic()
        try:
            return fn()
        except (SystemExit, Exception) as e:  # one unreadable source must not cost the rest of the round
            notes.append(f"{name} not read: {type(e).__name__}: {str(e)[:120]}")
            if source:
                failed.add(source)
            return default
        except Budget:  # the source the budget cut is not read either
            if source:
                failed.add(source)
            raise
        finally:
            if source:
                timings[source] = round(timings.get(source, 0) + time.monotonic() - t0, 2)

    facts["terminals"] = take("orca terminal list", lambda: prog.orca_json("terminal", "list")["terminals"], [], "terminals")
    facts["waits"] = take("orca worktree ps --agent-wait", lambda: prog.agent_waits(strict=True), {}, "waits")
    facts["workers"] = take("orca worker-list", lambda: all_workers(deadline), [], "workers")
    t0 = time.monotonic()
    st = fleet.read_json(fleet.state_dir() / "state.json", {}) or {}
    timings["state"] = round(time.monotonic() - t0, 2)
    at = fleet.parse(st.get("generated_at")) if st.get("generated_at") else None
    if not at or (fleet.utcnow() - at).total_seconds() > 600:
        notes.append("collector state.json missing or 10+ min old: parents, PRs and the board not judged")
        failed.add("state")
        st = {}
    facts["sessions"] = st.get("sessions") or []
    facts["prs"] = {p.get("key"): p for p in st.get("prs") or [] if p.get("key")}
    facts["board"] = take("board.json", read_board, {}, "board") if st else {}
    facts["state"] = st
    facts["supersedes"] = take("orch sent log", lambda: sent_supersedes(fleet.utcnow()), [], "sent")
    facts["level"], _ = pressure_level()
    facts["consumers"] = take("pressure consumers", consumers, [], "consumers") if facts["level"] in ("warn", "danger") else []
    paths = {t.get("worktreePath") for t in facts["terminals"] if t.get("worktreePath")}
    paths |= {s.get("path") for s in facts["sessions"] if s.get("path")}
    t0 = time.monotonic()
    facts["tails"] = {p: transcript_tail(p) for p in paths if p}
    timings["tails"] = round(time.monotonic() - t0, 2)
    # Screens only for agent terminals that wrote since the last round, and only while the budget lasts.
    seen_out, screens = memo.get("last_output") or {}, {}
    t0 = time.monotonic()
    for t in facts["terminals"]:
        h = t.get("handle")
        if not (t.get("agentIdentity") and h) or (t.get("lastOutputAt") or 0) <= seen_out.get(h, 0):
            continue
        if len(screens) >= SCREEN_READS or time.time() > deadline - 20:
            notes.append("screen reads cut short by the round's budget")
            facts["cut_short"] = True
            break
        screens[h] = take(f"screen {h}", lambda: prog.read_screen(h), None)
    timings["screens"] = round(time.monotonic() - t0, 2)
    facts["screens"] = {h: s for h, s in screens.items() if s is not None}
    facts["unread"] = {h for h, s in screens.items() if s is None}
    facts["fresh"] = set(facts["screens"])  # screens with new output; an idle candidate's read later is not
    facts["read"] = prog.read_screen
    facts["agents_fn"] = lambda: agents_read(deadline)
    facts["watchers"] = {}
    for s in facts["sessions"]:
        if s.get("kind") == "orchestrator":
            for t in s.get("terminals") or []:
                if t.get("agent") and t.get("handle"):
                    facts["watchers"][t["handle"]] = card_watch.last_scan(t["handle"])
    return facts, notes


# ---------------------------------------------------------------- judging (pure)

def fid(code, *key):
    return code + hashlib.sha1("|".join(map(str, key)).encode()).hexdigest()[:7]


def owners(sessions):
    """card path -> (session id, owner handle): the agent terminal of the session that started the card, None when
    it has none left (the finding goes to the orchestrator)."""
    by_id = {s["id"]: s for s in sessions if s.get("id")}
    out = {}
    for s in sessions:
        parent = by_id.get(s.get("parent") or s.get("orca_parent"))
        handle = next((t["handle"] for t in (parent or {}).get("terminals") or [] if t.get("agent")), None)
        out[s.get("path")] = (s.get("id"), handle)
    return out


def judge(facts, memo):
    """(findings, verdicts, memo). A finding: {id, code, card, path, handle, session, owner, title}. memo carries
    what one round cannot see alone: first sightings (O, D), error-line history (E), output marks. verdicts["cards"]
    is activity and verdicts["hints"] what findings said; a finding lands in hints, never over an ACTIVE card."""
    now = facts["now"]
    tnow = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    terms = facts["terminals"]
    agents = [t for t in terms if t.get("agentIdentity") and t.get("connected", True)]
    agent_paths = {t.get("worktreePath") for t in agents}
    own = owners(facts["sessions"])
    run_paths, findings, verdicts, hints, act, check = set(), [], {}, {}, {}, {}
    failed = facts.get("failed") or set()
    fresh = facts.get("fresh", set(facts["screens"]))
    # An output mark moves only past a screen this round read; one skipped by the budget or a failed read is read next.
    seen = memo.get("last_output") or {}
    marks = {t["handle"]: seen[t["handle"]] for t in agents if t.get("handle") in seen}
    marks.update({t["handle"]: t.get("lastOutputAt") or 0 for t in agents if t.get("handle") in fresh})
    new_memo = {"last_output": marks, "first_seen": {}, "errors": {}, "level": facts["level"], "t": now}
    # R raised once per session and text: an entry stays (live False) after its streak ends, so a recurrence is not new.
    old_repeats = memo.get("repeats") or {}
    new_memo["repeats"] = {k: {**v, "live": False} for k, v in old_repeats.items()}

    def add(code, path, key, title, handle=None, session=None):
        sid, owner = own.get(path, (None, None))
        findings.append({"id": fid(code, path or handle, *key), "code": code, "card": card_name(path) or handle,
                         "path": path, "handle": handle, "session": session or sid, "owner": owner, "title": title})
        if code in VERDICT_OF and path:
            hints[path] = VERDICT_OF[code]

    def activity(path, v):
        if verdicts.get(path) not in ACTIVE:
            verdicts[path] = v

    def first_seen(key):
        at = (memo.get("first_seen") or {}).get(key, now)
        new_memo["first_seen"][key] = at
        return (now - at) / 3600

    # X for Run workers: Orca's own verdict.
    for w in facts["workers"]:
        pj = w.get("projection") or {}
        path = ((w.get("resource") or {}).get("worktreeId") or "").partition("::")[2] or None
        if pj.get("outcome") == "in_progress":
            run_paths.add(path)
            if path:
                verdicts[path] = "run"  # its lead's `orch wait` judges it (STUCK, STALLED)
            if (pj.get("liveness") or {}).get("verdict") == "exited":
                add("X", path, (w.get("dispatchId"),), f"worker {w.get('dispatchId')} exited with its task in progress: "
                    f"worker-read --source transcript, then worker-stop or worker-abandon on positive evidence only",
                    prog.worker_terminal(w))  # its card stays "run": the lead decides, not a cleaner
    # H: an ended turn whose screen says the session runs on what changed under it. A Run worker too: one whose
    # every tool call a hook blocks cannot send worker_done.
    stale_paths = set()  # H's: an I found on the same card keeps them stalled, the more telling verdict
    for t in agents if "waits" not in failed else ():
        path, handle = t.get("worktreePath"), t.get("handle")
        motion = (facts["tails"].get(path) or (None, None))[0]
        if not (path and handle) or not motion or motion.get("state") != "idle" or motion.get("parked") \
                or isinstance(facts["waits"].get(handle), dict):
            continue
        sig = stale_signal(screen_of(facts, handle))
        if not sig:
            continue
        kind, text = sig
        what, why = STALE_WHAT[kind]
        add("H", path, (kind, VOLATILE.sub("#", text)), f"❔ {what}: {text[:120]} · `skills-sync broadcast --terminal "
            f"{handle}` types {STALE_COMMAND.get(kind, 'the fix')} once it is idle", handle)
        check[path] = why
        activity(path, "stalled")
        stale_paths.add(path)
    # B: an ended turn that says it waits on a background task while nothing will wake it: the transcript owes no
    # armed call a result (parked) and Claude Code's status line shows nothing running. A Run worker too.
    for t in agents if "waits" not in failed else ():
        path, handle = t.get("worktreePath"), t.get("handle")
        motion = (facts["tails"].get(path) or (None, None))[0]
        if not (path and handle) or not motion or motion.get("state") != "idle" or motion.get("parked") \
                or isinstance(facts["waits"].get(handle), dict):
            continue
        mins = (tnow - prog.parse_ts(motion["since"])).total_seconds() / 60
        lines = screen_of(facts, handle) if mins >= IDLE_MIN else None
        text = None if not lines or background(lines) else bgwait_signal(lines)
        if text:
            add("B", path, (motion["since"],), f"❔ its last message waits on a background task, but nothing armed or "
                f"running will wake it ({int(mins)} min): {text[:120]}", handle)
            check.setdefault(path, BGWAIT_WHY)
    for path, (motion, last) in facts["tails"].items():
        if path in run_paths or not path or "workers" in failed:  # without the Run list, a worker looks like a card
            if path and path not in run_paths:
                act[path] = {"state": "unknown", "why": "worker-list not read: a Run worker cannot be told apart"}
            continue
        if motion and motion.get("parked"):
            act[path] = {"state": "parked", "why": "a ScheduleWakeup, Monitor or background command will wake it"}
        handle = next((t["handle"] for t in agents if t.get("worktreePath") == path), None)
        if path not in agent_paths:
            err = error_end(last)
            if err and last.get("timestamp", "") >= fleet.iso(tnow - dt.timedelta(days=1)):
                add("X", path, (last.get("timestamp"),), f"its session ended on an error and nothing came after: {err}")
                activity(path, "exited")
            continue
        wait = facts["waits"].get(handle)
        if isinstance(wait, dict):
            verdicts[path] = "waiting"
            continue
        # Without Orca's wait list a prompt for a human cannot be ruled out: such a card stays working.
        if "waits" in failed:
            act[path] = {"state": "unknown", "why": "agent-wait list not read: a prompt for a human cannot be ruled out"}
        if (rep := (motion or {}).get("repeat")) and "waits" not in failed:  # before I: an idle one is told too
            key = f"{rep.get('session')}:{rep['kind']}:{rep['digest']}"
            old = old_repeats.get(key)
            if old is None or old.get("live"):
                add("R", path, (key,), f"❔ the same {rep['kind']} in {rep['count']} turns since {rep['since'][:16]}, "
                    f"nothing changed in between: {rep['text'][:120]}", handle)
            new_memo["repeats"][key] = {"at": (old or {}).get("at", now), "seen": now,
                                        "live": old is None or bool(old.get("live"))}
            if not motion.get("parked"):  # a wake-up it armed may still bring news: parked stays protected
                activity(path, "stalled")
                check.setdefault(path, f"같은 {REPEAT_WORD.get(rep['kind'], rep['kind'])} {rep['count']}턴 반복")
        if motion and motion.get("state") == "idle" and not motion.get("parked") and "waits" not in failed:
            mins = (tnow - prog.parse_ts(motion["since"])).total_seconds() / 60
            if mins >= IDLE_MIN and not background(screen_of(facts, handle)):
                add("I", path, (motion["since"],), f"turn ended {int(mins)} min ago, nothing armed to wake it", handle)
                if path in stale_paths:
                    hints[path] = "stalled"
                else:
                    activity(path, "idle")
                continue
        verdicts.setdefault(path, "working")
    # S: what a coordinator logged as superseded and still has an agent.
    for r in facts["supersedes"]:
        at = fleet.parse(r["ts"])
        if not at or (tnow - at).total_seconds() < SUPERSEDE_MIN * 60:
            continue
        hit = next((t for t in agents if r["to"] in (t.get("handle"), t.get("worktreePath"), card_name(t.get("worktreePath")))), None)
        if hit:
            add("S", hit.get("worktreePath"), (r["ts"],), f"superseded {int((tnow - at).total_seconds() // 60)} min ago "
                f"and still open: close its terminal (SKILL.md, superseded cards)", hit["handle"])
    # O: a card with no agent and an open PR nobody waits on, for a day.
    for s in facts["sessions"]:
        pr = (s.get("linked_pr") or {}).get("key")
        det = facts["prs"].get(pr) or {}
        if s.get("agents") or s.get("path") in agent_paths or (det.get("state") or "").upper() != "OPEN":
            continue
        if any(r.get("requested") for r in det.get("reviewers") or []) or str(det.get("ci") or "").lower() in ("pending", "running", "in_progress", "queued"):
            continue
        h = first_seen(f"O:{s.get('path')}:{pr}")
        if h >= ORPHAN_H:
            add("O", s.get("path"), (pr, tnow.date().isoformat()), f"{pr} open with no session for {int(h)}h and no "
                "review or CI running: hand it on, hold it, or close it", session=s.get("id"))
    # D: board doing items naming nothing tidy can follow.
    import tidy
    cards = tidy.cards_of(facts["state"]) if facts["state"] else {}
    doing_noref, shut = [], tidy.closed() if facts["board"] else {}
    for d in (facts["board"].get("doing") or []):
        text = f"{d.get('title')} {d.get('next') or ''}"
        if d.get("waiting") or fleet.pr_refs(text) or tidy.named(text, cards):
            continue
        iid = tidy.item_id("doing", d)
        if iid in shut:
            continue
        if first_seen(f"D:{iid}") >= NOREF_H:
            doing_noref.append(iid)
            add("D", None, (iid,), f"board doing item names no PR or card for {NOREF_H}h+: {str(d.get('title'))[:80]}",
                handle=iid)
    # E: one error line in ERROR_ROUNDS rounds in a row, each with new output.
    hist = memo.get("errors") or {}
    for t in agents:
        h = t.get("handle")
        if h in fresh:
            runs = [set(x) for x in (hist.get(h) or [])][-(ERROR_ROUNDS - 1):] + [error_keys(facts["screens"][h])]
        elif h in hist:  # no new output: the run neither grows nor breaks, and a standing E stays
            runs = [set(x) for x in hist[h]]
        else:
            continue
        new_memo["errors"][h] = [sorted(x) for x in runs]
        for k in sorted(set.intersection(*runs) if len(runs) >= ERROR_ROUNDS else ()):
            add("E", t.get("worktreePath"), (k,), f"❔ the same error in {ERROR_ROUNDS} rounds: {k[:120]}", h)
    # L: at warn or danger, a top consumer held by a card that is not working.
    load = []
    for c in facts["consumers"]:
        v = hints.get(c.get("card")) or verdicts.get(c.get("card")) or ("no card" if not c.get("card") else "working")
        load.append({**c, "verdict": v})
        if v in VERDICT_OF.values():
            add("L", c["card"], (c["what"], facts["level"]), f"PC {facts['level']}: {c['what']} "
                f"(CPU {c.get('cpu') or 0:.0f}%) held by a card judged {v}")
    # A rule that could not look keeps its day-long clocks; one that looked keeps only what it saw.
    blind = ("O:", "D:") if "state" in failed else ("D:",) if "board" in failed else ()
    for k, at in (memo.get("first_seen") or {}).items():
        if k.startswith(blind):
            new_memo["first_seen"].setdefault(k, at)
    if "terminals" in failed:  # no terminal list: keep the error runs and output marks for the next round
        new_memo["errors"], new_memo["last_output"] = dict(hist), dict(seen)
    if DEPENDS["R"] & failed:  # a round that could not look ends no streak
        new_memo["repeats"] = dict(old_repeats)
    # No count cap: a cap would forget a session that is still open. A key goes once its streak is long over.
    new_memo["repeats"] = {k: v for k, v in new_memo["repeats"].items()
                           if v.get("live") or now - v.get("seen", v.get("at", now)) < REPEATS_KEEP_DAYS * 86400}
    # W: the orchestrator's screen watch.
    for h, last in facts["watchers"].items():
        if not last or now - last[0] > WATCH_DOWN_MIN * 60:
            add("W", None, (int(last[0]) if last else 0,), "orch watch-cards " + (
                f"last finished a round {int((now - last[0]) // 60)} min ago" if last else "never ran here")
                + ": the orchestrator restarts it (run_in_background, no pipe)", h)
    return findings, {"cards": verdicts, "hints": hints, "check": check, "failed": sorted(failed),
                      "doing_noref": doing_noref, "load": load, "activity": act}, new_memo


# ---------------------------------------------------------------- the log

def log_rows():
    return [r for r in fleet.read_lines(LOG) if isinstance(r, dict) and r.get("id")]


def standing(rows=None):
    """id -> finding row for each occurrence not yet cleared, acked ones with "acked" set. An ack covers the
    occurrence: once its condition clears, the same id can be found again."""
    out = {}
    for r in rows if rows is not None else log_rows():
        if r.get("op") == "found":
            out[r["id"]] = r
        elif r.get("op") in ("owner", "ack") and r["id"] in out:
            out[r["id"]] = {**out[r["id"]], **({"owner": r.get("owner")} if r["op"] == "owner" else {"acked": True})}
        elif r.get("op") == "clear":
            out.pop(r["id"], None)
    return out


def open_findings(rows=None):
    """id -> finding row, for those neither cleared nor acked."""
    return {i: r for i, r in standing(rows).items() if not r.get("acked")}


def sync_needs_you(rows=None):
    """Bring the dashboard's patrol items in step with the log: one cut short by the budget is added, one acked or
    cleared elsewhere is resolved. An item once added and dismissed on the dashboard is not added again."""
    # One key per occurrence, so a recurrence after a clear is a new item.
    want = {f"patrol:{i}:{f.get('detected_at')}": f for i, f in open_findings(rows).items() if f.get("code") in NEEDS_YOU}
    added = {r.get("key") for r in fleet.read_lines(fleet.state_dir() / "inbox-external.jsonl")
             if r.get("op") == "add" and str(r.get("key", "")).startswith("patrol:")}
    shown = {i["key"][4:] for i in fleet.external_items() if i["key"].startswith("ext:patrol:")}
    for key, f in want.items():
        if key not in added:
            fleet.inbox_add("patrol", f"순찰 {f['code']} {f['card']}: {f['title']}", session=f.get("session"),
                            command=f"orch patrol ack {f['id']}", key=key, source="patrol")
    for key in shown - set(want):
        fleet.inbox_resolve(key)


# The sources each rule reads: a finding whose source failed this round is kept, not cleared.
DEPENDS = {"I": {"terminals", "workers", "waits"}, "X": {"terminals", "workers"}, "S": {"terminals", "sent"},
           "O": {"state", "terminals"}, "D": {"state", "board"}, "E": {"terminals"}, "L": {"consumers", "terminals", "workers", "waits"},
           "W": {"state"}, "R": {"terminals", "workers", "waits"}, "H": {"terminals", "waits"}, "B": {"terminals", "waits"}}


def write_round(findings, verdicts, dry, now, failed=()):
    """Append what is new, clear what went away, keep Needs you in step, and move a finding to its current owner
    (the session that started its card, or nobody once that session is gone). (new, cleared)."""
    rows = log_rows()
    live = standing(rows)
    at = fleet.iso(dt.datetime.fromtimestamp(now, dt.timezone.utc))
    ids = {f["id"] for f in findings}
    new = [f for f in findings if f["id"] not in live]
    gone = [r for i, r in live.items() if i not in ids and not DEPENDS.get(r.get("code"), set()) & set(failed)]
    moved = [f for f in findings if f["id"] in live and live[f["id"]].get("owner") != f.get("owner")]
    if dry:
        return new, gone
    for f in moved:
        fleet.append_line(LOG, {"op": "owner", "id": f["id"], "owner": f.get("owner"), "at": at})
    for f in new:
        fleet.append_line(LOG, {"op": "found", **f, "title": fleet.mask(f["title"], 300), "detected_at": at})
    for r in gone:
        fleet.append_line(LOG, {"op": "clear", "id": r["id"], "at": at})
    sync_needs_you()
    fleet.write_atomic(VERDICTS, json.dumps({"at": at, "t": now, **verdicts}, ensure_ascii=False))
    return new, gone


def ack(ids, now=None):
    live = open_findings()
    at = fleet.iso(dt.datetime.fromtimestamp(now or time.time(), dt.timezone.utc))
    done = []
    for i in (list(live) if ids == ["--all"] else ids):
        if i not in live:
            continue
        fleet.append_line(LOG, {"op": "ack", "id": i, "at": at})
        done.append(i)
    sync_needs_you()
    return done


@contextlib.contextmanager
def round_lock(wait):
    """patrol.lock, held by a round and by ack so neither writes between the other's read and write. Yields False
    when not waiting and another holds it."""
    STATE.mkdir(parents=True, exist_ok=True)
    with open(STATE / "patrol.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        yield True


def line(f):
    who = f" → {f['owner']}" if f.get("owner") else ""
    return f"PATROL {f['code']} {f['id']} {f['card']}{who}: {f['title']}"


def pending_lines(me, unowned, now=None):
    """Unacked findings routed to terminal `me` (and those routed to nobody, for the orchestrator), each shown again
    every 30 minutes until acked or cleared; a SHOW_ONCE code only the first time."""
    now = now or fleet.utcnow()
    mine = [f for f in open_findings().values() if f.get("owner") == me or (unowned and not f.get("owner"))]
    shown = fleet.read_json(SHOWN, {}) or {}
    seen = shown.get(me or "-") or {}
    due = [f for f in mine if f["id"] not in seen
           or f.get("code") not in SHOW_ONCE and now - fleet.parse(seen[f["id"]]) >= RENOTIFY]
    if due:
        keep = {f["id"]: seen.get(f["id"]) for f in mine if seen.get(f["id"])}
        keep.update({f["id"]: fleet.iso(now) for f in due})
        shown[me or "-"] = keep
        with contextlib.suppress(OSError):
            fleet.write_atomic(SHOWN, json.dumps(shown))
    return [line(f) for f in due] + ([f"  after dealing with each: orch patrol ack <id> (all {len(due)}: "
                                      f"orch patrol ack {' '.join(f['id'] for f in due)})"] if due else [])


# ---------------------------------------------------------------- round

def due(memo, level, now):
    rose = level in ("warn", "danger") and memo.get("level") != level
    return rose or now - (memo.get("t") or 0) >= DUE_S


class Budget(BaseException):
    """The round's 120 seconds ran out. Not an Exception, so no per-source handler swallows it."""


def round_id(now=None):
    return f"r-{int(now or time.time())}-{secrets.token_hex(3)}"


def give_up(*_):
    raise Budget()


def agents_read(deadline, read=agent_signal.read, clock=time.time):
    """The query, unless the round's budget could run out inside it: the alarm would cost the verdicts already judged."""
    if deadline - clock() < AGENTS_MARGIN_S:
        return [], "skipped: the round's budget is nearly spent", ""
    return read()


def signal_card(t, facts, reading):
    """One card's row for the comparison: the screen only where this round read one or Orca found a wait, the transcript
    as its own side, and whether Orca looked at all (a handle missing from its list was not looked at)."""
    h, waits = t.get("handle"), facts["waits"]
    waited = waits.get(h, prog.NOT_LOOKED)
    wait = "failed" if "waits" in (facts.get("failed") or ()) else "found" if isinstance(waited, dict) \
        else "none" if h in waits else "not looked"
    lines = facts["screens"].get(h)
    if lines is not None or isinstance(waited, dict):
        screen, basis, finding = card_watch.screen_view(t, lines, waited, facts["now"] * 1000)
    else:
        screen, basis, finding = "unknown", "unreadable" if h in (facts.get("unread") or ()) else "not read", ""
    motion = (facts["tails"].get(t.get("worktreePath")) or (None, None))[0] if agent_signal.is_claude(t) else None
    return agent_signal.compare(reading, screen, finding, basis, (motion or {}).get("state"), wait)


def signal_round(facts, findings, verdicts, memo, memo2, notes, dry, rid):
    """Ask `claude agents` once the round's screens are all read, so the query costs them none of the budget. Changes
    no judgement; a finding's title only gains what the signal said. Returns what the round missed, '' when nothing."""
    t0 = time.monotonic()
    rows, problem, raw = facts["agents_fn"]()
    facts["timings"]["agents"] = round(time.monotonic() - t0, 2)
    terms = [t for t in facts["terminals"] if t.get("agentIdentity") and t.get("connected", True) and t.get("handle")]
    reads, warned = agent_signal.readings(terms, rows, problem)
    cards = {t["handle"]: signal_card(t, facts, reads[t["handle"]]) for t in terms}
    for f in findings:
        if text := agent_signal.evidence(reads.get(f.get("handle")), cards.get(f.get("handle"), {}).get("screen")):
            f["title"] += f" · agents: {text}"
    notes += warned
    verdicts["signals"] = {"problem": problem, "cards": cards}
    if not dry:
        memo2["signals"] = agent_signal.log_round("patrol", None, rid, cards, memo.get("signals"), problem, raw, warned,
                                                  {t["handle"]: card_name(t.get("worktreePath")) or t["handle"] for t in terms})
    blind = sorted(facts.get("failed") or ()) + sorted(facts.get("unread") or ())
    blind += ["screens cut short"] if facts.get("cut_short") else []
    blind += [t["handle"] for t in terms if agent_signal.is_claude(t) and cards[t["handle"]]["basis"] == "no evidence"]
    parts = [f"못 읽은 곳 {len(blind)}: " + ", ".join(blind)] if blind else []
    gaps = agent_signal.gaps(reads)
    if problem:
        parts.append(f"{agent_signal.NO_SIGNAL}: claude agents failed: {problem}")
    elif gaps:
        parts.append(f"{agent_signal.NO_SIGNAL} {len(gaps)}: " + ", ".join(f"{h} ({w})" for h, w in gaps.items()))
    return "; ".join(parts)


def round_once(dry=False, if_due=False, now=None, gather_fn=gather, rid=None):
    now = now or time.time()
    memo = fleet.read_json(MEMO, {}) or {}
    level, _ = pressure_level()
    if if_due and not due(memo, level, now):
        return None
    t0 = time.monotonic()
    facts, notes = gather_fn(now, time.time() + BUDGET_S, memo)
    findings, verdicts, memo2 = judge(facts, memo)
    rid = rid or round_id(now)
    partial = signal_round(facts, findings, verdicts, memo, memo2, notes, dry, rid) if facts.get("agents_fn") else ""
    verdicts.update(schema=2, round=rid, complete=False,
                    sources=own_sources(facts), timings=dict(facts.get("timings") or {}),
                    candidates=[], reports=[], alerts=[])
    new, gone = write_round(findings, verdicts, dry, now, facts.get("failed") or ())
    if not dry:
        fleet.write_atomic(MEMO, json.dumps(memo2))
    agents = [t for t in facts["terminals"] if t.get("agentIdentity")]
    return {"at": fleet.iso(dt.datetime.fromtimestamp(now, dt.timezone.utc)), "seconds": round(time.monotonic() - t0, 1),
            "terminals": len(agents), "workers": len(facts["workers"]), "screens": len(facts["screens"]),
            "level": facts["level"], "findings": findings, "new": [f["id"] for f in new],
            "cleared": [r["id"] for r in gone], "verdicts": verdicts, "notes": notes, "dry_run": dry, "partial": partial,
            "owners": {p: o for p, (_, o) in owners(facts["sessions"]).items() if p and o}}


def mark_cut(rid, seconds):
    """A round the budget cut short: the file keeps the last round's activity fields as before, under this round's
    id, incomplete, with this round's own sources and timings so far, so nothing takes the earlier round's
    candidates or timings for this one. The cut round is logged for `orch patrol timing` too."""
    on_file = fleet.read_json(VERDICTS, {}) or {}
    failed = CURRENT.get("failed") or set()
    timings = dict(CURRENT.get("timings") or {})
    sources = {k: "failed" if k in failed else "ok" if k in timings else "skipped" for k in OWN_SOURCES}
    out = {**on_file, "schema": 2, "round": rid, "complete": False, "candidates": [], "reports": [], "alerts": [],
           "sources": {**sources, "round": "timeout"}, "timings": timings}
    fleet.write_atomic(VERDICTS, json.dumps(out, ensure_ascii=False, default=str))
    log_round(out, seconds, 0)


def own_sources(facts):
    """Each of this round's own reads as ok, failed or skipped (not read this round)."""
    failed = facts.get("failed") or set()
    read = set(facts.get("timings") or ()) | set(failed)
    return {s: "failed" if s in failed else "ok" if s in read else "skipped" for s in OWN_SOURCES}


# ---------------------------------------------------------------- v2 candidates (nothing reads them yet)

def candidates_phase(res, dry=False, collect=None):
    """Fill the round's candidates, reports and alerts from today's cleaners' own judging and mark the round complete.
    Runs after the round's lock is let go, so an ack never waits on it; the file is written only while it still holds
    this round. At pressure danger the scans are skipped and the round stays incomplete. Returns what it wrote."""
    v = res["verdicts"]
    t0, started = time.monotonic(), time.time()
    got = {"sources": dict(v["sources"]), "timings": {}, "candidates": [], "reports": [], "alerts": [], "notes": []}
    complete = False
    partial = {}
    try:
        if res["level"] == "danger":
            got["sources"]["candidates"] = "skipped"
            got["notes"].append("pressure danger: cleanup scans skipped this round")
        else:
            sys.path.insert(0, str(REAP_SCRIPTS))
            import candidates, reap
            reap.RUN_TIMEOUT = candidates.RUN_TIMEOUT_S
            got = (collect or candidates.collect)(v["sources"], progress=partial)
            for a in got["alerts"]:
                a["to"] = res.get("owners", {}).get(a.get("path"))
            complete = True
    except Budget:
        got["sources"].update(partial.get("sources") or {})
        got["timings"] = dict(partial.get("timings") or {})
        got["sources"]["candidates"] = "timeout"
        got["notes"].append(f"cleanup scans gave up at {CANDIDATE_BUDGET_S}s")
    finally:
        signal.alarm(0)  # the write below must not be cut off halfway
        seconds = round(time.monotonic() - t0, 1)
        # candidates_t: the scans started here, so no act before it can be on what they saw
        out = {**v, "complete": complete, "candidates_t": started, "sources": got["sources"],
               "timings": {**v.get("timings", {}), **got["timings"], "candidates": seconds},
               "candidates": got["candidates"], "reports": got["reports"], "alerts": got["alerts"]}
        res["notes"] += got["notes"]
        if not dry:
            with round_lock(wait=True):
                on_file = fleet.read_json(VERDICTS, {}) or {}
                if on_file.get("round") == v["round"]:
                    fleet.write_atomic(VERDICTS, json.dumps({**on_file, **out}, ensure_ascii=False, default=str))
                log_round(out, res["seconds"], seconds)
        res["verdicts"] = out
    return out


def log_round(v, seconds, candidate_s, now=None):
    """One line per round in <state>/patrol-rounds.jsonl, the last ROUNDS_KEEP kept: what `orch patrol timing` reads."""
    row = {"round": v["round"], "t": now or time.time(), "seconds": seconds, "candidates_s": candidate_s,
           "complete": v["complete"], "timings": v["timings"],
           "failed": sorted(k for k, x in v["sources"].items() if x not in ("ok", "skipped"))}
    with contextlib.suppress(OSError):
        rows = fleet.read_lines(ROUNDS)[-(ROUNDS_KEEP - 1):] + [row]
        fleet.write_atomic(ROUNDS, "".join(json.dumps(r) + "\n" for r in rows))


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def timing_lines(rows=None):
    """p50 and p95 of whole rounds and of each source, slowest first: whether a shorter interval fits the budget."""
    rows = [r for r in (fleet.read_lines(ROUNDS) if rows is None else rows) if isinstance(r, dict)]
    if not rows:
        return ["patrol timing: no round recorded yet"]
    total = [(r.get("seconds") or 0) + (r.get("candidates_s") or 0) for r in rows]
    out = [f"patrol timing over {len(rows)} rounds: total p50 {pct(total, .5):.0f}s p95 {pct(total, .95):.0f}s "
           f"(budget {BUDGET_S}s + {CANDIDATE_BUDGET_S}s) · complete {sum(1 for r in rows if r.get('complete'))}/{len(rows)}"]
    series = {k: [(r.get("timings") or {}).get(k, 0) for r in rows] for r0 in rows for k in r0.get("timings") or {}}
    for k, xs in sorted(series.items(), key=lambda kv: -pct(kv[1], .95)):
        out.append(f"  {k}: p50 {pct(xs, .5):.1f}s p95 {pct(xs, .95):.1f}s")
    return out


def report(res):
    if res is None:
        return ["patrol: not due (last round under 25 min ago, pressure level unchanged)"]
    v = res["verdicts"]
    counts = {}
    for x in v["cards"].values():
        counts[x] = counts.get(x, 0) + 1
    out = [f"PATROL {res['at']} · {res['seconds']}s · agent terminals {res['terminals']} · workers {res['workers']} · "
           f"screens read {res['screens']} · pressure {res['level']}" + (" · dry-run" if res["dry_run"] else ""),
           "verdicts: " + (", ".join(f"{k} {n}" for k, n in sorted(counts.items())) or "none")]
    new = set(res["new"])
    for f in res["findings"]:
        out.append(("NEW " if f["id"] in new else "    ") + line(f))
    for c in v.get("load") or []:
        out.append(f"load: {c['what']} CPU {c.get('cpu') or 0:.0f}% · card {card_name(c.get('card')) or '-'} ({c['verdict']})")
    if res["cleared"]:
        out.append(f"cleared: {' '.join(res['cleared'])}")
    out += [f"note: {n}" for n in res["notes"]]
    missed = res.get("partial")
    if not res["findings"]:
        out.append(f"PARTIAL nothing found; {missed}" if missed else "OK nothing found")
    elif missed:
        out.append(f"PARTIAL {missed}")
    return out


def main(argv):
    if argv[:1] == ["ack"]:
        ids = argv[1:]
        if not ids:
            sys.exit("usage: orch patrol ack <id>... | --all")
        with round_lock(wait=True):
            done = ack(ids)
        print(f"acked {len(done)}: {' '.join(done)}" if done else "nothing open by those ids")
        return
    if argv[:1] == ["timing"]:
        print("\n".join(timing_lines()))
        return
    if argv[:1] == ["list"]:
        rows = open_findings()
        if "--all" not in argv:
            me = os.environ.get("ORCA_TERMINAL_HANDLE")
            rows = {i: f for i, f in rows.items() if not f.get("owner") or f.get("owner") == me}
        print("\n".join(line(f) + f" (since {f.get('detected_at')})" for f in rows.values()) or "no open finding")
        return
    if set(argv) - {"--once", "--if-due", "--dry-run", "--json"}:
        sys.exit(__doc__)
    prog.RUN_TIMEOUT = 60  # one hung orca or gh fails its source, within the round's own budget
    with round_lock(wait=False) as got:
        if not got:
            print("patrol: another round is running")
            return
        signal.signal(signal.SIGALRM, give_up)
        signal.alarm(BUDGET_S)
        rid, t0 = round_id(), time.monotonic()
        try:
            res = round_once("--dry-run" in argv, "--if-due" in argv, rid=rid)
        except Budget:
            signal.alarm(0)
            if "--dry-run" not in argv:
                mark_cut(rid, round(time.monotonic() - t0, 1))
            print(f"patrol: gave up at {BUDGET_S}s; log rows already written stay, and the next round brings "
                  "Needs you in step with them")
            return
        finally:
            signal.alarm(0)
    if res is not None:
        signal.alarm(CANDIDATE_BUDGET_S)
        try:
            candidates_phase(res, "--dry-run" in argv)
        finally:
            signal.alarm(0)
    print(json.dumps(res, ensure_ascii=False, indent=1) if "--json" in argv else "\n".join(report(res)))


if __name__ == "__main__":
    main(sys.argv[1:])
