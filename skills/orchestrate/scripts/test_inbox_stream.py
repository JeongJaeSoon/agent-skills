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
                    session=lambda me: "s1", ctx=lambda s: None, store=Store())
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

typed = []
req = ("2026-01-01T00:00:00Z", {"stage": "requested", "handle": ME, "session": "s1"})
st = Store(req)
s, orca, out, clock = stream([], store=st, clear=lambda me: typed.append(me) or None)
s.now = lambda: __import__("time").time() if False else 1767225600.0 + 60
s.restart()
s.restart()
assert typed == [ME] and st.rows[-1][2]["stage"] == "sent" and st.rows[-1][2]["confirmed"] is True, st.rows
st = Store(req)
s, orca, out, clock = stream([], store=st, clear=lambda me: "screen changed between two reads (someone may be typing)")
s.now = lambda: 1767225600.0 + 60
assert s.restart() == [] and st.rows == []
s.now = lambda: 1767225600.0 + ist.REQUEST_TTL_S + 1
assert s.restart()[0].startswith("RESTART EXPIRED") and st.rows[-1][2]["stage"] == "expired"
for other in ({"stage": "requested", "handle": "term_other", "session": "s1"}, {"stage": "requested", "handle": ME, "session": "s0"}):
    st = Store(("2026-01-01T00:00:00Z", other))
    s, orca, out, clock = stream([], store=st, clear=lambda me: (_ for _ in ()).throw(AssertionError("typed")))
    assert s.restart() == []

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
