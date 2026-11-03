#!/usr/bin/env python3
"""Session patrol: one round over every agent card, by rules fixed in code, with no model in the loop.

Usage: orch patrol [--once] [--if-due] [--dry-run] [--json]
       orch patrol list [--all]
       orch patrol ack <id>... | --all

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
  L  load      at pressure warn or danger, a top consumer whose card is judged I, X, S or O
  W  watcher   an orchestrator whose `orch watch-cards` has not finished a round in an hour (reported here; the
               collector raises it on the dashboard)

Patrol records and shows; it never types into a terminal, closes one, stops a worker or removes a card. Findings
go to <state>/patrol.jsonl with detected_at, are cleared there when a later round no longer sees them, and are acked
by whoever dealt with them (`orch patrol ack`). `orch inbox` and `orch wait` show the unacked ones routed to them
(the card's parent session, else the orchestrator) every 30 minutes. X, E, L and O also go to the dashboard's
Needs you until cleared or acked. Card verdicts go to <state>/patrol-verdicts.json: `cards` holds each card's activity
(run, waiting, working, idle, exited), `hints` what a finding said of it (superseded, pr_open_no_session, idle,
exited), and `failed` the sources this round could not read. A finding never overwrites run, waiting or working in
`cards`. reap-hard, the pressure round and the janitor read `cards` only to keep a card judged working; no verdict
makes anything a target, and a card missing from it, in a failed round above all, is not evidence it is safe.

--if-due runs only when the last round is 25+ minutes old or the pressure level rose to warn or danger since it
(reap-hard calls this every pass, the pressure round when its level changes). The round gives up at 120 seconds.
"""
import contextlib, datetime as dt, fcntl, hashlib, json, os, pathlib, re, signal, subprocess, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(1, str(pathlib.Path(__file__).resolve().parents[2] / "reap-resources" / "scripts"))
import card_watch, cardguard, dash, fleet, prog  # noqa: E402

STATE = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser()
LOG = STATE / "patrol.jsonl"
VERDICTS = STATE / "patrol-verdicts.json"
MEMO = STATE / "patrol-state.json"
SHOWN = STATE / "patrol-shown.json"
PRESSURE = STATE / "pressure.json"
REAP_SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "reap-resources" / "scripts"

DUE_S = 25 * 60
BUDGET_S = 120
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
         "L": "load", "W": "watcher down"}
VERDICT_OF = {"I": "idle", "X": "exited", "S": "superseded", "O": "pr_open_no_session"}
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


# Claude Code's status line while a background shell or agent of the session still runs. A message typed in after the
# command was armed resets the transcript's record of it, so only the screen still shows it.
BACKGROUND = re.compile(r"\b\d+ (?:shells?|agents?|background tasks?)\b|shells? still running")


def background(lines):
    return bool(lines) and any(BACKGROUND.search(l) for l in lines[-6:])


def screen_of(facts, handle):
    """The screen read this round, else one read now (only an idle candidate needs it); None when unreadable."""
    if handle not in facts["screens"] and facts.get("read"):
        try:
            facts["screens"][handle] = facts["read"](handle)
        except (SystemExit, Exception):
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

    def take(name, fn, default, source=None):
        try:
            return fn()
        except (SystemExit, Exception) as e:  # one unreadable source must not cost the rest of the round
            notes.append(f"{name} not read: {type(e).__name__}: {str(e)[:120]}")
            if source:
                failed.add(source)
            return default

    facts["terminals"] = take("orca terminal list", lambda: prog.orca_json("terminal", "list")["terminals"], [], "terminals")
    facts["waits"] = take("orca worktree ps --agent-wait", lambda: prog.agent_waits(strict=True), {}, "waits")
    facts["workers"] = take("orca worker-list", lambda: all_workers(deadline), [], "workers")
    st = fleet.read_json(fleet.state_dir() / "state.json", {}) or {}
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
    facts["tails"] = {p: transcript_tail(p) for p in paths if p}
    # Screens only for agent terminals that wrote since the last round, and only while the budget lasts.
    seen_out, screens = memo.get("last_output") or {}, {}
    for t in facts["terminals"]:
        h = t.get("handle")
        if not (t.get("agentIdentity") and h) or (t.get("lastOutputAt") or 0) <= seen_out.get(h, 0):
            continue
        if len(screens) >= SCREEN_READS or time.time() > deadline - 20:
            notes.append("screen reads cut short by the round's budget")
            break
        screens[h] = take(f"screen {h}", lambda: prog.read_screen(h), None)
    facts["screens"] = {h: s for h, s in screens.items() if s is not None}
    facts["fresh"] = set(facts["screens"])  # screens with new output; an idle candidate's read later is not
    facts["read"] = prog.read_screen
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
    run_paths, findings, verdicts, hints = set(), [], {}, {}
    failed = facts.get("failed") or set()
    fresh = facts.get("fresh", set(facts["screens"]))
    # An output mark moves only past a screen this round read; one skipped by the budget or a failed read is read next.
    seen = memo.get("last_output") or {}
    marks = {t["handle"]: seen[t["handle"]] for t in agents if t.get("handle") in seen}
    marks.update({t["handle"]: t.get("lastOutputAt") or 0 for t in agents if t.get("handle") in fresh})
    new_memo = {"last_output": marks, "first_seen": {}, "errors": {}, "level": facts["level"], "t": now}

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
    for path, (motion, last) in facts["tails"].items():
        if path in run_paths or not path or "workers" in failed:  # without the Run list, a worker looks like a card
            continue
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
        if motion and motion.get("state") == "idle" and not motion.get("parked") and "waits" not in failed:
            mins = (tnow - prog.parse_ts(motion["since"])).total_seconds() / 60
            if mins >= IDLE_MIN and not background(screen_of(facts, handle)):
                add("I", path, (motion["since"],), f"turn ended {int(mins)} min ago, nothing armed to wake it", handle)
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
    # W: the orchestrator's screen watch.
    for h, last in facts["watchers"].items():
        if not last or now - last[0] > WATCH_DOWN_MIN * 60:
            add("W", None, (int(last[0]) if last else 0,), "orch watch-cards " + (
                f"last finished a round {int((now - last[0]) // 60)} min ago" if last else "never ran here")
                + ": the orchestrator restarts it (run_in_background, no pipe)", h)
    return findings, {"cards": verdicts, "hints": hints, "failed": sorted(failed), "doing_noref": doing_noref,
                      "load": load}, new_memo


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
           "W": {"state"}}


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
    every 30 minutes until acked or cleared."""
    now = now or fleet.utcnow()
    mine = [f for f in open_findings().values() if f.get("owner") == me or (unowned and not f.get("owner"))]
    shown = fleet.read_json(SHOWN, {}) or {}
    seen = shown.get(me or "-") or {}
    due = [f for f in mine if f["id"] not in seen or now - fleet.parse(seen[f["id"]]) >= RENOTIFY]
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


def round_once(dry=False, if_due=False, now=None, gather_fn=gather):
    now = now or time.time()
    memo = fleet.read_json(MEMO, {}) or {}
    level, _ = pressure_level()
    if if_due and not due(memo, level, now):
        return None
    t0 = time.monotonic()
    facts, notes = gather_fn(now, time.time() + BUDGET_S, memo)
    findings, verdicts, memo2 = judge(facts, memo)
    new, gone = write_round(findings, verdicts, dry, now, facts.get("failed") or ())
    if not dry:
        fleet.write_atomic(MEMO, json.dumps(memo2))
    agents = [t for t in facts["terminals"] if t.get("agentIdentity")]
    return {"at": fleet.iso(dt.datetime.fromtimestamp(now, dt.timezone.utc)), "seconds": round(time.monotonic() - t0, 1),
            "terminals": len(agents), "workers": len(facts["workers"]), "screens": len(facts["screens"]),
            "level": facts["level"], "findings": findings, "new": [f["id"] for f in new],
            "cleared": [r["id"] for r in gone], "verdicts": verdicts, "notes": notes, "dry_run": dry}


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
    if not res["findings"]:
        out.append("OK nothing found")
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
    if argv[:1] == ["list"]:
        rows = open_findings()
        if "--all" not in argv:
            me = os.environ.get("ORCA_TERMINAL_HANDLE")
            rows = {i: f for i, f in rows.items() if not f.get("owner") or f.get("owner") == me}
        print("\n".join(line(f) + f" (since {f.get('detected_at')})" for f in rows.values()) or "no open finding")
        return
    if set(argv) - {"--once", "--if-due", "--dry-run", "--json"}:
        sys.exit(__doc__)
    with round_lock(wait=False) as got:
        if not got:
            print("patrol: another round is running")
            return

        def give_up(*_):
            raise Budget()
        signal.signal(signal.SIGALRM, give_up)
        signal.alarm(BUDGET_S)
        try:
            res = round_once("--dry-run" in argv, "--if-due" in argv)
        except Budget:
            print(f"patrol: gave up at {BUDGET_S}s; log rows already written stay, and the next round brings "
                  "Needs you in step with them")
            return
        finally:
            signal.alarm(0)
    print(json.dumps(res, ensure_ascii=False, indent=1) if "--json" in argv else "\n".join(report(res)))


if __name__ == "__main__":
    main(sys.argv[1:])
