"""inbox_stream.py: `orch inbox --stream` against a fake Orca, clock, card watch and store; nothing real is called.

Run: python3 test_inbox_stream.py
"""
import io, os, pathlib, sys, tempfile

root = pathlib.Path(tempfile.mkdtemp(prefix="test-inbox-stream-"))
os.environ.update({"AGENT_SKILLS_STATE": str(root), "AGENT_SKILLS_CONFIG": str(root / "none.json"),
                   "ORCH_FLEET_STATE": str(root / "dash")})
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import inbox_stream as ist  # noqa: E402
import prog  # noqa: E402

ME = "term_me"


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Orca:
    """Answers prog.check: a take pops the next batch, a peek-wait advances the clock, an ack is recorded."""

    def __init__(self, clock, batches):
        self.clock, self.batches, self.calls = clock, list(batches), []

    def __call__(self, *args):
        self.calls.append(args)
        if "--peek" in args:
            self.clock.t += int(args[args.index("--timeout-ms") + 1]) / 1000
            return {}
        if "--ack" in args:
            return {}
        return self.batches.pop(0) if self.batches else {"deliveryId": None, "messages": []}


class Store:
    def __init__(self, last=None):
        self.last, self.rows = last, []

    def last_session_event(self, key, ev):
        return self.last

    def session_event(self, key, ev, data, by=None):
        self.rows.append((key, ev, data))
        self.last = ("2026-01-01T00:00:00Z", data)


def stream(batches, **kw):
    clock = Clock()
    orca = Orca(clock, batches)
    prog.check = orca
    out = io.StringIO()
    defaults = dict(debounce=30, ttl_ms=600_000, cards=False, threshold=0, out=out, clock=clock, now=clock,
                    session=lambda me: "s1", ctx=lambda s: None, store=Store(), role_key=lambda s: "orchestrator")
    s = ist.Stream(ME, **{**defaults, **kw})
    return s, orca, out, clock


prog.wake_lines = lambda me: []
prog.watch_lines = lambda me, unowned: []
ist._scanned = lambda me: None

s, orca, out, clock = stream([{"deliveryId": "dv1", "messages": [{"id": "m1", "type": "status", "body": "PR ready\nsee it"},
                                                                 {"id": "h", "type": "heartbeat"}]},
                              {"deliveryId": "dv2", "messages": [{"id": "m2", "type": "worker_done", "body": "done"}]}])
assert s.mail(deadline=600) is True
lines = out.getvalue().splitlines()
assert lines[0].startswith("INBOX id=m1 type=status") and lines[0].endswith("body=PR ready ⏎ see it"), lines
assert lines[1].startswith("INBOX id=m2 type=worker_done") and lines[2].startswith("2 message(s) to act on"), lines
assert len(lines) == 3, lines
assert ("--ack", "dv1") in orca.calls and ("--ack", "dv2") in orca.calls
assert any("--peek" in c and "--types" not in c for c in orca.calls), "the debounce wait is a peek with no --types"

s, orca, out, clock = stream([{"deliveryId": "dv3", "messages": [{"id": "m3", "type": "question", "body": "which?"}]}])
s.mail(deadline=600)
assert not any("--peek" in c for c in orca.calls) and "type=question" in out.getvalue()
for m in ({"id": "w", "type": "worker_done"}, {"id": "r", "type": "status", "subject": "acme/app#1 READY"}):
    s, orca, out, clock = stream([{"deliveryId": "dv", "messages": [m]}])
    s.mail(deadline=600)
    assert not any("--peek" in c for c in orca.calls) and f"id={m['id']}" in out.getvalue(), m
assert not ist.urgent([{"type": "status", "subject": "already merged"}])

s, orca, out, clock = stream([{"deliveryId": "dv4", "messages": [{"id": "h", "type": "heartbeat"}]}])
assert s.mail(deadline=600) is False and out.getvalue() == "" and ("--ack", "dv4") in orca.calls


class Child:
    def __init__(self, text, code=0, err=""):
        self.text, self.returncode, self.err = text, code, err

    def poll(self):
        return self.returncode

    def communicate(self):
        return self.text, self.err

    def terminate(self):
        pass


spawned = []
children = [Child("STUCK term_x: a permission prompt\n"), Child("QUIET nothing new; start the watch again\n")]
s, orca, out, clock = stream([], cards=True, spawn=lambda ms: (spawned.append(ms), children.pop(0) if children else
                                                                                   Child(None))[1])
s.watch_cards(deadline=600)
s.watch_cards(deadline=600)
assert out.getvalue().splitlines() == ["STUCK term_x: a permission prompt",
                                       "CARDS: act on the card lines above; the card watch started again by itself"], out.getvalue()
s.watch_cards(deadline=600)
assert out.getvalue().count("CARDS:") == 1 and len(spawned) == 3, spawned
s.child = Child("", code=1, err="Traceback\nOSError: boom")
s.watch_cards(deadline=600)
assert "orch watch-cards exited 1 (OSError: boom); started again" in out.getvalue()

ctx = {"v": 150_000}
s, orca, out, clock = stream([{"deliveryId": "d", "messages": [{"id": "q", "type": "question"}]}] * 3,
                             threshold=130_000, ctx=lambda sid: ctx["v"])
s.mail(600)
s.mail(600)
assert out.getvalue().count("RESTART DUE: context 150k > 130k") == 1, out.getvalue()
ctx["v"] = 176_000
s.mail(600)
assert out.getvalue().count("RESTART DUE") == 2
s, orca, out, clock = stream([], threshold=130_000, ctx=lambda sid: 150_000)
assert s.due() == []
clock.t += ist.QUIET_S
assert s.due()[0].startswith("RESTART DUE")
# Only the orchestrator is asked to restart: a lead, a session with no role, and a role that changed across a /clear
# are not, however large the context.
for who in ("lead:app", None):
    s, orca, out, clock = stream([], threshold=130_000, ctx=lambda sid: 150_000, role_key=lambda sid, who=who: who)
    assert s.due(force=True) == [], who
s, orca, out, clock = stream([], threshold=130_000, ctx=lambda sid: 150_000,
                             role_key=lambda sid: "orchestrator" if sid == "s1" else "lead:app")
assert s.due(force=True)[0].startswith("RESTART DUE")
s.session = "s2"
s.announced.clear()
assert s.due(force=True) == []

never = lambda me, still=None: (_ for _ in ()).throw(AssertionError("typed"))  # noqa: E731
req = ("2026-01-01T00:00:00Z", {"stage": "requested", "handle": ME, "session": "s1"})
# auto_clear off (the default): a stored request is disarmed, never typed.
st = Store(req)
s, orca, out, clock = stream([], store=st, clear=never)
assert s.restart()[0].startswith("RESTART OFF") and st.rows[-1][2]["stage"] == "off"
(root / "none.json").write_text('{"orchestrate": {"auto_clear": true}}')

typed = []
st = Store(req)
s, orca, out, clock = stream([], store=st, clear=lambda me, still: still() or typed.append(me) or None)
s.now = lambda: 1767225600.0 + 60
s.restart()
s.restart()
assert typed == [ME] and [r[2]["stage"] for r in st.rows] == ["typing", "sent"] and st.rows[-1][2]["confirmed"], st.rows
# The claim is written before typing: if it cannot be written, nothing is typed.
class Broken(Store):
    def session_event(self, key, ev, data, by=None):
        raise OSError("disk full")
s, orca, out, clock = stream([], store=Broken(req), clear=never)
s.now = lambda: 1767225600.0 + 60
try:
    s.restart()
    raise AssertionError("a failed claim must stop the restart")
except OSError:
    pass
# A busy screen: nothing typed, the request goes back to requested with its first time, and expires from that.
st = Store(req)
s, orca, out, clock = stream([], store=st, clear=lambda me, still: "screen changed between two reads (someone may be typing)")
s.now = lambda: 1767225600.0 + 60
assert s.restart() == [] and [r[2]["stage"] for r in st.rows] == ["typing", "requested"], st.rows
assert st.rows[-1][2]["requested_at"] == "2026-01-01T00:00:00Z"
s.now = lambda: 1767225600.0 + ist.REQUEST_TTL_S + 1
assert s.restart()[0].startswith("RESTART EXPIRED") and st.rows[-1][2]["stage"] == "expired"
for other in ({"stage": "requested", "handle": "term_other", "session": "s1"}, {"stage": "requested", "handle": ME, "session": "s0"}):
    st = Store(("2026-01-01T00:00:00Z", other))
    s, orca, out, clock = stream([], store=st, clear=never)
    assert s.restart() == []
# The live session decides, not the one the stream saw last: a /clear typed meanwhile makes the request stale.
live = {"v": "s1"}
st = Store(req)
s, orca, out, clock = stream([], store=st, clear=never, session=lambda me: live["v"])
live["v"] = "s2"
assert s.restart() == [] and st.rows[-1][2]["stage"] == "stale"
# A human /clear while the typing lock reads the screen: the check under the lock stops the send.
live["v"] = "s1"
st = Store(req)
def clear_after_human(me, still):
    live["v"] = "s2"
    return still() or "typed"
s, orca, out, clock = stream([], store=st, clear=clear_after_human, session=lambda me: live["v"])
assert s.restart() == [] and [r[2]["stage"] for r in st.rows] == ["typing", "stale"], st.rows
(root / "none.json").unlink()

# An Orca error inside the debounce window: what was already taken still goes out, then the error.
s, orca, out, clock = stream([{"deliveryId": "dv9", "messages": [{"id": "m9", "type": "status", "body": "x"}]}])
real = prog.check
for boom in (SystemExit("orca down"), ValueError("bad JSON from orca")):
    s, orca, out, clock = stream([{"deliveryId": "dv9", "messages": [{"id": "m9", "type": "status", "body": "x"}]}])
    real = prog.check
    def flaky(*args, boom=boom, real=real):
        if "--peek" in args:
            raise boom
        return real(*args)
    prog.check = flaky
    try:
        s.mail(deadline=600)
        raise AssertionError("the error must still end the round")
    except type(boom):
        pass
    assert "INBOX id=m9" in out.getvalue() and ("--ack", "dv9") in orca.calls and not ist.holding(ME), boom
# The mark lasts the stream's own window, longer than the default: a 600 s debounce still holds near its end.
ist.hold(ME, 600 + ist.TICK_S * 2)
real_time = ist.time.time
ist.time.time = lambda: real_time() + 590
assert ist.holding(ME)
ist.time.time = lambda: real_time() + 600 + ist.TICK_S * 3
assert not ist.holding(ME), "a dead stream's mark expires"
ist.time.time = real_time
# Extending a mark never leaves the file empty or missing for a reader in between.
real_write = ist.pathlib.Path.write_text
seen_mid = []
def watching(self, data, *a, **kw):
    self.open("w").close()  # what write_text does first: the file it writes is empty now
    seen_mid.append(ist.holding(ME))
    return real_write(self, data, *a, **kw)
ist.pathlib.Path.write_text = watching
ist.hold(ME, 60)
ist.pathlib.Path.write_text = real_write
assert seen_mid == [True] and ist.holding(ME) and not list(ist.hold_path(ME).parent.glob("*.tmp")), seen_mid
ist.hold(ME, 0)
# A take that fails before anything is printed leaves no mark behind.
s, orca, out, clock = stream([])
prog.check = lambda *a: (_ for _ in ()).throw(SystemExit("orca down"))
try:
    s.mail(deadline=600)
except SystemExit:
    pass
assert not ist.holding(ME)
# While the window is open the stream marks that it holds mail.
seen = []
s, orca, out, clock = stream([{"deliveryId": "dv8", "messages": [{"id": "m8", "type": "status"}]}])
inner = prog.check
prog.check = lambda *a: (seen.append(ist.holding(ME)), inner(*a))[1]
s.mail(deadline=600)
assert True in seen and not ist.holding(ME), seen

sid = {"v": "s1"}
s, orca, out, clock = stream([], session=lambda me: sid["v"])
assert s.cleared() == []
sid["v"] = "s2"
got = s.cleared()
assert got[0].startswith("SESSION CLEARED") and "agent-skills:reboot --top" in got[0] and s.cleared() == []

prog.wake_lines = lambda me: ["WAKE term_x: unconfirmed"]
s, orca, out, clock = stream([])
s.side()
s.side()
clock.t += ist.REPEAT_S
s.side()
assert out.getvalue().count("WAKE term_x") == 2, out.getvalue()
prog.wake_lines = lambda me: []

s, orca, out, clock = stream([], ttl_ms=150_000)
assert s.run() == 0
assert out.getvalue().splitlines()[-1].startswith("STREAM END after 2 min: start it again now with the Monitor tool")
s, orca, out, clock = stream([], ttl_ms=10_000_000)
prog.check = lambda *a: (_ for _ in ()).throw(SystemExit("orca down"))
ist.time.sleep = lambda x: setattr(clock, "t", clock.t + x)
assert s.run() == 1 and out.getvalue().count("STREAM ERROR") == ist.ERRORS_MAX

with ist.only_stream(ME) as got:
    assert got and ist.running(ME)
    with ist.only_stream(ME) as second:
        assert second is False
assert not ist.running(ME)
print("test_inbox_stream: ok")
