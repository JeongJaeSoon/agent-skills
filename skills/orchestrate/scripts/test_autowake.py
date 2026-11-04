#!/usr/bin/env python3
"""orch wait wakes a stopped worker once, N minutes after its first mark, and takes the second stop to a human.

Run: python3 test_autowake.py
"""
import contextlib, datetime as dt, io, json, os, pathlib, sys, tempfile

tmp = pathlib.Path(tempfile.mkdtemp())
os.environ.update({"PROGRAMS_HOME": str(tmp / "programs"), "AGENT_SKILLS_CONFIG": str(tmp / "agent-skills.json"),
                   "ORCH_FLEET_STATE": str(tmp / "state" / "dashboard"), "ORCH_DASH": "off",
                   "AGENT_SKILLS_STATE": str(tmp / "state")})
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import autowake, fleet, prog  # noqa: E402

T0 = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.timezone.utc)
mins = lambda m: T0 + dt.timedelta(minutes=m)
config = pathlib.Path(os.environ["AGENT_SKILLS_CONFIG"])


def row(d="ctx_1", **kw):
    pj = {"outcome": "in_progress", "liveness": {"verdict": "live"}, "stage": {"activity": "idle"},
          "nextAction": {"kind": "none", "argv": []}, "attention": {"requiresAction": False}}
    pj.update(kw.pop("projection", {}))
    return {"dispatchId": d, "taskId": "task_1", "agentTerminalHandle": "term_1", "terminalState": "active",
            "projection": pj, **kw}


class World:
    """One program on disk with Orca, the wake commands and the human inbox replaced by records."""
    count = 0

    def __init__(self, stalls=None, workers=None, mail=(), gates=(), routes=("route: terminal\nreason: off",), nudge=0):
        World.count += 1
        self.slug = f"p{World.count}"
        d = prog.HOME / self.slug
        (d / "briefs").mkdir(parents=True)
        (d / "dashboard").mkdir()
        (d / "briefs" / "ABC-1.md").write_text("brief")
        (d / "program.json").write_text(json.dumps({"run": "run_x", "repo": "acme/app"}))
        self.p = prog.Program(self.slug)
        self.stalls = [{"dispatch": "ctx_1", "ticket": "ABC-1", "kind": "idle", "minutes": 16}] if stalls is None else stalls
        self.workers, self.mail, self.gates = [row()] if workers is None else workers, list(mail), list(gates)
        self.routes, self.nudge = list(routes), nudge
        self.sent, self.synced, self.inbox, self.fail = [], [], [], set()
        self.write_state()

    def write_state(self):
        (self.p.dir / "dashboard" / "state.json").write_text(json.dumps({
            "generated_at": prog.now(), "summary": {"stalls": self.stalls},
            "workers": [{"dispatch": "ctx_1", "since": mins(-60).isoformat()}]}))

    def orca(self, *a):
        if a[:2] in self.fail:
            raise SystemExit("orca failed")
        if a[:2] == ("orchestration", "send"):
            self.sent.append(a)
            return {}
        if a[:2] == ("orchestration", "inbox"):
            return {"messages": self.mail}
        if a[:2] == ("orchestration", "gate-list"):
            return {"gates": self.gates}
        raise AssertionError(a)

    def sync(self, *a):
        self.synced.append(a)
        if a[0] == "wake-target":
            return 0, self.routes.pop(0)
        return (self.nudge, "refused at a dialog") if a[0] == "nudge" else (0, "reset")

    def sweep(self, m):
        saved = autowake.orca, autowake.sync, prog.all_workers, fleet.inbox_add
        autowake.orca, autowake.sync, prog.all_workers = self.orca, self.sync, lambda run: self.workers
        fleet.inbox_add = lambda kind, title, key=None: self.inbox.append((kind, title, key))
        try:
            self.write_state()
            return autowake.sweep(self.p, 10, mins(m))
        finally:
            autowake.orca, autowake.sync, prog.all_workers, fleet.inbox_add = saved

    def rows(self, ev):
        return [e for e in self.p.events() if e["ev"] == ev]


NUDGED = ("nudge", "term_1", "run your orchestration check")

# The key: a positive int turns it on, anything else leaves it off.
assert autowake.minutes() is None, "no file"
for bad in ('{}', '{"orchestrate": {}}', '{"orchestrate": {"auto_wake_min": 0}}', '{"orchestrate": {"auto_wake_min": -1}}',
            '{"orchestrate": {"auto_wake_min": true}}', '{"orchestrate": {"auto_wake_min": "10"}}', 'not json',
            '{"orchestrate": []}', '[]'):
    config.write_text(bad)
    assert autowake.minutes() is None, bad
config.write_text('{"orchestrate": {"auto_wake_min": 10}}')
assert autowake.minutes() == 10

# The first mark sets the timer and wakes nobody; the wake waits N minutes from it, not from the turn's end.
w = World()
r = w.sweep(0)
assert not w.sent and not w.synced and r.skip == {"ctx_1"} and not r.stop, r
assert r.next_s == 600, r
assert any("AUTO-WAKE ctx_1" in l and "12:10" in l for l in r.lines), r.lines
r = w.sweep(9)
assert not w.sent and r.skip == {"ctx_1"} and not r.lines and r.next_s == 60, r

# N minutes on: one content message, then wake-target, then the typed signal, and one ledger row.
r = w.sweep(10)
assert [a[:3] for a in w.synced] == [("wake-target", "term_1"), NUDGED], w.synced
assert len(w.sent) == 1, w.sent
sent = w.sent[0]
assert sent[sent.index("--to") + 1] == "dispatch:ctx_1", sent
body = sent[sent.index("--body") + 1]
assert body == f"Brief: {w.p.dir / 'briefs' / 'ABC-1.md'}. Continue with the next step from where you stopped.", body
assert "/goal" not in body and "\n" not in body
done = w.rows("auto_wake")
assert len(done) == 1, done
e = done[0]
assert e["dispatch"] == "ctx_1" and e["ticket"] == "ABC-1" and "16 min" in e["reason"] and e["route"] == "terminal", e
assert prog.parse_ts(e["marked_at"]) == mins(0) and prog.parse_ts(e["woken_at"]) == mins(10), e
assert r.skip == {"ctx_1"}, "the wake just went out: no STALLED line tells the lead to wake it by hand"

# The same idle, still shown by a collector that has not caught up: no second wake, no human yet.
r = w.sweep(15)
assert len(w.sent) == 1 and len(w.synced) == 2 and not w.inbox and r.skip == {"ctx_1"} and r.next_s == 300, r

# Second stop: no wake; the human gets one item, once, and the ledger says so.
r = w.sweep(20)
assert len(w.sent) == 1 and len(w.synced) == 2, "a second idle wakes nobody"
assert len(w.inbox) == 1 and w.inbox[0][0] == "approval" and w.inbox[0][2] == "auto-wake:ctx_1" and "ABC-1" in w.inbox[0][1], w.inbox
assert len(w.rows("auto_wake_escalated")) == 1 and r.stop and r.lines, r
r = w.sweep(40)
assert len(w.inbox) == 1 and len(w.rows("auto_wake_escalated")) == 1 and not r.stop and not r.skip, r

# A worker that went back to work and stopped again a day later still gets no second wake.
w = World()
w.sweep(0)
w.sweep(10)
stall, w.stalls = w.stalls, []
assert not w.sweep(12).skip
w.stalls = stall
w.sweep(30)
assert len(w.sent) == 1 and len(w.inbox) == 1, "the ledger, not the mark, says it was woken"

# A mark goes when the stall goes: a later idle starts its own timer.
w = World()
w.sweep(0)
stall, w.stalls = w.stalls, []
w.sweep(5)
w.stalls = stall
w.sweep(11)
assert not w.sent, "resumed at 5, stopped again at 11: the timer starts at 11"
w.sweep(21)
assert len(w.sent) == 1

# A stale collector changes nothing: no wake, and the marks stay.
w = World()
w.sweep(0)
saved, prog.dash_state = prog.dash_state, lambda pdir: None
r = w.sweep(30)
prog.dash_state = saved
assert not w.sent and not r.skip
w.sweep(31)
assert len(w.sent) == 1, "the mark survived the stale round"


def excluded(name, **kw):
    """Past the due time the world says no: nothing is sent, nothing claimed, and the reason shows once."""
    w = World(**kw.pop("world", {}))
    for ev, fields in kw.pop("events", ()):
        w.p.append(ev, **fields)
    w.sweep(0)
    r = w.sweep(10)
    assert not w.sent and not w.synced and not w.inbox, (name, w.sent, w.synced)
    assert not w.rows("auto_wake") and not w.rows("auto_wake_claimed"), name
    assert "ctx_1" not in r.skip, f"{name}: the lead keeps today's STALLED line"
    assert any("not woken" in l for l in r.lines), (name, r.lines)
    assert not any("not woken" in l for l in w.sweep(11).lines), f"{name}: the reason repeats every round"
    return w


# The five the brief names.
excluded("worker_done", world={"workers": [row(projection={"outcome": "succeeded"})]})
excluded("released", world={"workers": [row(terminalState="released")]})
excluded("release pending", world={"workers": [row(terminalState="release_pending")]})
excluded("no row", world={"workers": []})
# A kept-open worker is Orca's `retained` and still in progress: it is woken like an active one.
w = World(workers=[row(terminalState="retained")])
w.sweep(0)
w.sweep(10)
assert len(w.sent) == 1, "retained"
ask = {"id": "m1", "type": "question", "from_handle": "dispatch:ctx_1", "subject": "which?", "body": "?",
       "created_at": mins(-5).isoformat(), "payload": None}
excluded("ask mail", world={"mail": [ask]})
excluded("ask next action", world={"workers": [row(projection={"nextAction": {"kind": "reply", "argv": ["orca", "x"]}})]})
excluded("attention", world={"workers": [row(projection={"attention": {"requiresAction": True}})]})
excluded("open gate", world={"gates": [{"id": "g1", "taskId": "task_1", "status": "pending"}]})
excluded("held ticket", events=[("parked", {"ticket": "ABC-1"})])
excluded("held PR", events=[("spawned", {"ticket": "ABC-1", "pr": 7}), ("parked", {"pr": 7})])
excluded("READY in the ledger", events=[("spawned", {"ticket": "ABC-1", "pr": 7}), ("ready", {"pr": 7})])
excluded("verdict in the ledger", events=[("spawned", {"ticket": "ABC-1", "pr": 7}), ("verdict", {"pr": 7})])
ready = {"id": "m2", "type": "status", "from_handle": "term_1", "subject": "READY at abc", "body": "x",
         "created_at": mins(-5).isoformat(), "payload": json.dumps({"dispatchId": "ctx_1"})}
excluded("READY mail", world={"mail": [ready]})
# Beyond the brief's five: what a wake cannot help or must not reach.
excluded("program stopped", events=[("stop", {})])
excluded("not idle to Orca", world={"workers": [row(projection={"stage": {"activity": "working"}})]})
excluded("exited", world={"workers": [row(projection={"liveness": {"verdict": "exited"}})]})
excluded("no terminal", world={"workers": [row(agentTerminalHandle=None)]})
excluded("no brief", world={"stalls": [{"dispatch": "ctx_1", "ticket": "ZZZ-9", "kind": "idle", "minutes": 16}]})
w = World()
(w.p.dir / "stuck.json").write_text(json.dumps({"ctx_1": "abc"}))
w.sweep(0)
assert "ctx_1" not in w.sweep(10).skip and not w.sent, "STUCK is a prompt a wake cannot answer"
# What cannot be read is not a yes.
w = World()
w.fail.add(("orchestration", "inbox"))
w.sweep(0)
assert not w.sweep(10).skip and not w.sent
w.fail.clear()
w.sweep(11)
assert len(w.sent) == 1, "an unreadable round is retried, it uses up nothing"
# A full page that stops after the dispatch began cannot rule its ask out.
w = World(mail=[{"id": f"m{i}", "type": "status", "from_handle": "x", "created_at": mins(-1).isoformat(), "payload": None}
                for i in range(autowake.INBOX_LIMIT)])
w.sweep(0)
assert not w.sweep(10).skip and not w.sent, "the page does not reach back to the dispatch's start"
# An answered question, and mail of other workers, do not hold it back.
answer = {"id": "m3", "type": "status", "from_handle": "term_lead", "thread_id": "m1", "subject": "Re", "body": "yes",
          "created_at": mins(-1).isoformat(), "payload": None}
other = {**ready, "id": "m4", "payload": json.dumps({"dispatchId": "ctx_2"})}
w = World(mail=[answer, ask, other])
w.sweep(0)
w.sweep(10)
assert len(w.sent) == 1, "answered"

# silent alone never starts a timer, and a dispatch in the stall list as long_tool neither.
w = World(stalls=[{"dispatch": "ctx_1", "ticket": "ABC-1", "kind": "silent", "minutes": 21}])
w.sweep(0)
assert not w.sweep(30).skip and not w.sent

# The second stop waits on the same exclusions: a READY after the wake is no stop to report.
w = World()
w.sweep(0)
w.sweep(10)
w.p.append("spawned", ticket="ABC-1", pr=7)
w.p.append("ready", pr=7)
r = w.sweep(20)
assert not w.inbox and not w.rows("auto_wake_escalated"), r
# A wake that is still a silent row after N minutes is that same second stop.
w = World()
w.sweep(0)
w.sweep(10)
w.stalls[0]["kind"] = "silent"
w.workers[0]["projection"]["stage"]["activity"] = "idle"
w.sweep(20)
assert len(w.inbox) == 1


def failed(why, **kw):
    w = World(**kw)
    w.sweep(0)
    r = w.sweep(10)
    assert len(w.inbox) == 1 and r.stop, (why, w.inbox, r)
    assert not w.rows("auto_wake") and len(w.rows("auto_wake_failed")) == 1, why
    assert why in w.rows("auto_wake_failed")[0]["why"], w.rows("auto_wake_failed")
    w.sweep(25)
    w.sweep(50)
    assert len(w.inbox) == 1 and len(w.sent) <= 1, "one attempt, one human item"
    return w


# A failed attempt is the human's at once and is never tried again.
failed("refused at a dialog", nudge=1)
failed("pending", routes=("route: pending\nreason: wake abc is not confirmed\ncheck: x",))
failed("report", routes=("route: report\nreason: stayed unconfirmed\ncheck: x",))
w = World()
w.sweep(0)
saved, w.orca = w.orca, lambda *a: (_ for _ in ()).throw(SystemExit("send failed")) if a[1] == "send" else saved(*a)
r = w.sweep(10)
assert not w.synced, "no signal without the content"
assert len(w.inbox) == 1 and "send failed" in w.rows("auto_wake_failed")[0]["why"]

# route: sendmessage: this process has no SendMessage, which waking.md step 3 sends to wake-reset and the typed signal.
w = World(routes=("route: sendmessage\nto: worker\nmessage: wake abc: run your orchestration check\ncheck: x",))
w.sweep(0)
w.sweep(10)
assert [a[0] for a in w.synced] == ["wake-target", "wake-reset", "nudge"], w.synced
assert w.synced[1] == ("wake-reset", "term_1") and w.synced[2] == NUDGED, w.synced
assert w.rows("auto_wake")[0]["route"] == "sendmessage", w.rows("auto_wake")

# Claim before act: a crash after the claim leaves no wake, then the human, never a second wake.
w = World()
w.sweep(0)
w.sync = lambda *a: (_ for _ in ()).throw(KeyboardInterrupt())
try:
    w.sweep(10)
except KeyboardInterrupt:
    pass
del w.sync
assert len(w.rows("auto_wake_claimed")) == 1 and not w.rows("auto_wake")
w.sweep(11)
assert len(w.sent) == 1 and not w.synced, "the claim holds: no second attempt"
w.sweep(20)
assert len(w.inbox) == 1, w.inbox
# Two rounds racing for one dispatch: the lock lets one claim through, and it does not deadlock inside append.
w = World()
assert w.p.append_once("auto_wake_claimed", lambda e: e.get("dispatch") == "d", dispatch="d")
assert w.p.append_once("auto_wake_claimed", lambda e: e.get("dispatch") == "d", dispatch="d") is None
assert len(w.rows("auto_wake_claimed")) == 1

# new_stalls leaves out what auto-wake holds; next_moves says the wake is coming instead of asking for one.
w = World()
stall = {"dispatch": "ctx_1", "ticket": "ABC-1", "kind": "idle", "minutes": 16}
assert prog.new_stalls(w.p.dir, skip={"ctx_1"}) == []
assert len(prog.new_stalls(w.p.dir)) == 1, "the skipped one was not marked as shown"
idle = {"dispatchId": "w4", "agentTerminalHandle": "h4",
        "projection": {"outcome": "in_progress", "nextAction": {"kind": "none"}, "stage": {"activity": "idle"}}}
assert prog.next_moves([idle], {"w4"}) == ["  w4 idle: auto-wake holds it (orch wait)"]
assert "wake-target" in prog.next_moves([idle])[0]


def wait(w, cfg, rounds=1):
    """cmd_wait over one empty round; returns (printed, the --timeout-ms it blocked with)."""
    config.write_text(cfg)
    seen = []
    saved = prog.check, prog.ensure_dash, prog.print_stuck, prog.print_sweep, prog.print_reviews, prog.watch_lines, \
        prog.run_workers, prog.own_worker, prog.all_workers, prog.new_leftovers, autowake.orca, autowake.sync
    prog.check = lambda *a: (seen.append(a), {"deliveryId": None, "messages": []})[1]
    prog.ensure_dash, prog.print_stuck, prog.print_sweep = lambda: None, lambda *a, **k: [], lambda *a, **k: None
    prog.print_reviews, prog.watch_lines = lambda p: None, lambda *a: []
    prog.run_workers = prog.all_workers = lambda run: w.workers
    prog.own_worker, prog.new_leftovers = lambda *a: None, lambda *a: []
    autowake.orca, autowake.sync = w.orca, w.sync
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            prog.cmd_wait([w.slug, "--rounds", str(rounds)])
    finally:
        prog.check, prog.ensure_dash, prog.print_stuck, prog.print_sweep, prog.print_reviews, prog.watch_lines, \
            prog.run_workers, prog.own_worker, prog.all_workers, prog.new_leftovers, autowake.orca, autowake.sync = saved
    waits = [a[a.index("--timeout-ms") + 1] for a in seen if "--wait" in a]
    return out.getvalue(), waits


# No key: today's STALLED ending, plus exactly one line saying auto-wake is off; nothing is marked or sent.
w = World()
text, _ = wait(w, "{}")
assert "STALLED ABC-1 (ctx_1)" in text and "wake it now" in text, text
assert text.count(autowake.OFF_LINE) == 1, text
assert not w.sent and not w.synced and not (w.p.dir / "auto-wake.json").exists()
# No stall, no warning: the line belongs to a mark.
w = World(stalls=[])
assert autowake.OFF_LINE not in wait(w, "{}")[0]

# With the key the first mark does not end the wait with "wake it now", and the wait ends by the due time.
w = World()
text, waits = wait(w, '{"orchestrate": {"auto_wake_min": 10}}')
assert "AUTO-WAKE ctx_1" in text and "wake it now" not in text and "STALLED" not in text.replace("STALLED:", "") , text
assert autowake.OFF_LINE not in text
assert int(waits[0]) <= 600000 and "auto-wake holds it" in text, (waits, text)
# Actionable mail returning first does not starve the wake: it runs before each wait.
marks = w.p.dir / "auto-wake.json"
marks.write_text(json.dumps({"ctx_1": {"at": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=11)).isoformat()}}))
text, _ = wait(w, '{"orchestrate": {"auto_wake_min": 10}}')
assert len(w.sent) == 1 and w.synced[-1] == NUDGED, text
# A sweep that breaks does not cost the wait.
w = World()
saved, autowake.sweep = autowake.sweep, lambda *a: 1 / 0
config.write_text('{"orchestrate": {"auto_wake_min": 10}}')
text, waits = wait(w, '{"orchestrate": {"auto_wake_min": 10}}')
autowake.sweep = saved
assert "auto-wake failed" in text and waits, text

print("test_autowake: ok")
