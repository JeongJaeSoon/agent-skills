"""cardguard.py: the guard over fixed facts, observe() over replaced readers, the worker list, and the card lock
between two processes. Run: python3 test_cardguard.py"""
import json, os, pathlib, subprocess, sys, tempfile, time, types

tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["AGENT_SKILLS_STATE"] = str(tmp / "state")
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cardguard, reap  # noqa: E402

H = "a" * 40
OK = {"patrol": False, "head": H, "terminals": [{"handle": "t1", "agentIdentity": "claude"}, {"handle": "s1"}],
      "waits": {"t1": None}, "screens": {"t1": ["⏺ done", "❯ "]}, "motion": {"state": "idle", "parked": False}}


def check(handles=("t1", "s1"), final=False, **over):
    return cardguard.check({**OK, **over}, H, set(handles), final)


# Fixed facts: every part that cannot be seen, and every sign of life, keeps the card.
assert check() == (True, "확인함")
cases = [({"patrol": True}, "순찰"), ({"head": None}, "HEAD 를 읽지 못함"), ({"head": "b" * 40}, "HEAD 가 바뀜"),
         ({"terminals": None}, "터미널 목록"), ({"waits": None}, "agent-wait"),
         ({"waits": {"t1": {"evidence": "hook"}}}, "사람의 답"), ({"screens": {}}, "화면을 읽지 못함"),
         ({"screens": {"t1": []}}, "화면을 읽지 못함"), ({"screens": {"t1": ["Do you want to proceed?", "Esc to cancel"]}}, "질문"),
         ({"motion": None}, "세션 기록"), ({"motion": {"state": "idle", "parked": True}}, "parked")]
for over, why in cases:
    ok, got = check(**over)
    assert not ok and why in got, (over, got)
ok, got = check(handles=("t1",))
assert not ok and "새 터미널 s1" in got, got
# A handle Orca did not look at is left to the screen; a readable, quiet screen passes.
assert check(waits={})[0]
# With no agent terminal left nothing can answer a prompt or wake: wait, screen and motion are not needed.
assert check(terminals=[{"handle": "s1"}], waits=None, screens=None, motion=None)[0]
# After the close only patrol, HEAD and new terminals count: the closed sessions' last state does not.
assert check(final=True, waits=None, screens={}, motion={"parked": True})[0]
assert not check(final=True, terminals=[{"handle": "t1"}, {"handle": "n2"}])[0]
assert not check(final=True, patrol=True)[0] and not check(final=True, head="c" * 40)[0]
assert check(handles=("t1", "s1", "gone"), final=True)[0], "a handle already closed is fine"

# observe(): each reader that fails is None, which check() turns into a keep.
saved = reap.patrol_read, reap.run, reap.orca_json, cardguard._orch, cardguard.parked
reap.patrol_read = lambda now=None, activity=False: set()
reap.run = lambda cmd, **k: H + "\n"
reap.orca_json = lambda *a: {"terminals": OK["terminals"]}
prog = types.SimpleNamespace(agent_waits=lambda strict=False: {"t1": None}, read_screen=lambda h: ["❯ "])
cardguard._orch = lambda name: prog
cardguard.parked = lambda path: False
f = cardguard.observe("/w/x")
assert f["head"] == H and f["waits"] == {"t1": None} and f["screens"] == {"t1": ["❯ "]} and not f["patrol"], f
assert cardguard.guard("/w/x", H, ["t1", "s1"]) == (True, "확인함")


def boom(*a, **k):
    raise SystemExit("orca down")


for name, broken, why in (("agent_waits", boom, "agent-wait"), ("read_screen", boom, "화면을 읽지 못함")):
    setattr(prog, name, broken)
    ok, got = cardguard.guard("/w/x", H, ["t1", "s1"])
    assert not ok and why in got, (name, got)
    prog.agent_waits, prog.read_screen = (lambda strict=False: {"t1": None}), (lambda h: ["❯ "])
cardguard.parked = boom
assert "세션 기록" in cardguard.guard("/w/x", H, ["t1", "s1"])[1]
cardguard.parked = lambda path: True
assert "parked" in cardguard.guard("/w/x", H, ["t1", "s1"])[1]
cardguard.parked = lambda path: False
for answer in (None, {}, {"terminals": None}, {"terminals": [], "truncated": True}):
    reap.orca_json = lambda *a, r=answer: r
    assert "터미널 목록" in cardguard.guard("/w/x", H, ["t1", "s1"])[1], answer
reap.orca_json = lambda *a: {"terminals": OK["terminals"]}
reap.run = boom
assert "HEAD 를 읽지 못함" in cardguard.guard("/w/x", H, ["t1", "s1"])[1]
reap.run = lambda cmd, **k: H + "\n"
reap.patrol_read = lambda now=None, activity=False: {"/w/x"}
assert "순찰 판정:" in cardguard.guard("/w/x", H, ["t1", "s1"])[1]
reap.patrol_read = lambda now=None, activity=False: None
assert "순찰 판정 파일을 읽지 못함" in cardguard.guard("/w/x", H, ["t1", "s1"])[1]
reap.patrol_read, reap.run, reap.orca_json, cardguard._orch, cardguard.parked = saved

# reap.patrol_read: no file or a stale one is no protection; one that exists and cannot be read is None.
reap.STATE.mkdir(parents=True, exist_ok=True)
vf = reap.STATE / "patrol-verdicts.json"
assert reap.patrol_read() == set()
vf.write_text("{not json")
assert reap.patrol_read() is None and reap.patrol_working() == set()
vf.write_text(json.dumps({"t": time.time(), "cards": {"/w/x": "waiting", "/w/y": "idle"}}))
assert reap.patrol_read() == {reap.real("/w/x")}
vf.write_text(json.dumps({"t": time.time() - 7200, "cards": {"/w/x": "waiting"}}))
assert reap.patrol_read() == set()
vf.write_text(json.dumps({"t": time.time(), "cards": {"/w/x": "idle"},
                          "activity": {"/w/p": {"state": "parked"}, "/w/u": {"state": "unknown"}, "/w/i": {"state": "idle"}}}))
assert reap.patrol_read() == set(), "activity is read only by the guard before a close"
assert reap.patrol_read(activity=True) == {reap.real("/w/p"), reap.real("/w/u")}
for bad in ({"/w/x": "parked"}, {"/w/x": {}}, ["parked"], False, 0, "", []):
    vf.write_text(json.dumps({"t": time.time(), "cards": {}, "activity": bad}))
    assert reap.patrol_read() == set() and reap.patrol_read(activity=True) is None, bad
vf.write_text(json.dumps(["not", "a", "dict"]))
assert reap.patrol_read() is None
vf.unlink()

# The real transcript reader: a turn that armed ScheduleWakeup and ended is parked.
projects = tmp / "projects"
os.environ["CLAUDE_PROJECTS_DIR"] = str(projects)
d = projects / "-w-parked"
d.mkdir(parents=True)
rows = [{"type": "assistant", "timestamp": "2026-01-01T00:00:00Z",
         "message": {"content": [{"type": "tool_use", "id": "u1", "name": "ScheduleWakeup", "input": {}}]}},
        {"type": "user", "timestamp": "2026-01-01T00:00:01Z",
         "message": {"content": [{"type": "tool_result", "tool_use_id": "u1", "content": "ok"}]}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:02Z", "message": {"stop_reason": "end_turn", "content": []}}]
(d / "s.jsonl").write_text("{}\n" + "\n".join(json.dumps(r) for r in rows) + "\n")
motion, _ = cardguard.transcript_tail("/w/parked")
assert motion and motion["state"] == "idle" and motion["parked"], motion
assert cardguard.transcript_tail("/w/none", strict=True) == (None, None), "no transcript is not a failure"
assert cardguard.parked("/w/none") is False
(d / "s.jsonl").chmod(0)
try:
    cardguard.transcript_tail("/w/parked", strict=True)
    raise AssertionError("an unreadable transcript raises under strict")
except OSError:
    pass
assert cardguard.transcript_tail("/w/parked") == (None, None)
(d / "s.jsonl").chmod(0o644)
d.chmod(0)
try:
    cardguard.transcript_tail("/w/parked", strict=True)
    raise AssertionError("a folder that cannot be listed raises under strict, not 'no transcript'")
except OSError:
    pass
d.chmod(0o755)
# The cleaners read CLAUDE_CONFIG_DIR/projects; the guard reads there too, not only dash's CLAUDE_PROJECTS_DIR.
cfg_home = tmp / "cfg"
(cfg_home / "projects" / "-w-parked").mkdir(parents=True)
os.environ["CLAUDE_CONFIG_DIR"] = str(cfg_home)
assert cardguard.parked("/w/parked"), "parked in dash's folder"
# A newer, idle session in the other folder does not hide the parked one.
idle_rows = [{"type": "user", "timestamp": "2026-01-02T00:00:00Z", "message": {"content": "hi"}},
             {"type": "assistant", "timestamp": "2026-01-02T00:00:01Z", "message": {"stop_reason": "end_turn", "content": []}}]
(cfg_home / "projects" / "-w-parked" / "new.jsonl").write_text("{}\n" + "\n".join(json.dumps(r) for r in idle_rows) + "\n")
assert cardguard.parked("/w/parked")
(d / "s.jsonl").rename(cfg_home / "projects" / "-w-parked" / "s.jsonl")
os.utime(cfg_home / "projects" / "-w-parked" / "s.jsonl", (time.time() + 60, time.time() + 60))
assert cardguard.parked("/w/parked"), "parked in the cleaners' folder"
(cfg_home / "projects" / "-w-parked").chmod(0)
try:
    cardguard.parked("/w/parked")
    raise AssertionError("either folder unreadable raises")
except OSError:
    pass
(cfg_home / "projects" / "-w-parked").chmod(0o755)
del os.environ["CLAUDE_CONFIG_DIR"]
assert cardguard.transcript_tail("/w/parked") == (None, None), "patrol still reads dash's folder only"

# Shared facts: busy shells and child cards.
row = lambda pid, cmd, ppid=1: {"pid": pid, "ppid": ppid, "cmd": cmd}
procs = {10: row(10, "claude --permission-mode auto"), 11: row(11, "/bin/zsh -c sleep 600", 10),
         12: row(12, "node mcp-server.js", 10), 20: row(20, "claude"), 21: row(21, "-zsh", 20)}
assert cardguard.busy_shells("/w/a", procs, {10: "/w/a/sub", 20: "/w/b"}) == [11]
assert cardguard.busy_shells("/w/a", procs, {}) == []
kid = tmp / "kid"
kid.mkdir()
assert cardguard.child_cards({"childWorktreeIds": [f"r::{kid}", "r::/nowhere::workspace:1", None, "bad"]}) == [str(kid)]

# worker_pages: every page, over every Run; a page that fails or a list bound to one Run raises.
pages = {None: {"workers": [{"dispatchId": "a"}], "page": {"hasMore": True, "nextCursor": "c2"}},
         "c2": {"workers": [{"dispatchId": "b"}], "page": {"hasMore": False}}}
seen = []


def fake_every(args):
    seen.append(args)
    return pages[args[args.index("--cursor") + 1] if "--cursor" in args else None]


saved_every, cardguard._orca_every_run = cardguard._orca_every_run, fake_every
assert [w["dispatchId"] for w in cardguard.worker_pages()] == ["a", "b"] and len(seen) == 2
pages["c2"] = {"workers": [], "scope": {"source": "bound", "run": "r"}}
try:
    cardguard.worker_pages()
    raise AssertionError("a list bound to one Run is not every Run's")
except RuntimeError:
    pass
for page in ({"hasMore": True, "nextCursor": "c2"}, {"hasMore": True}):
    pages["c2"] = {"workers": [], "page": page}
    try:
        cardguard.worker_pages()
        raise AssertionError(f"a list cut short is a failure: {page}")
    except RuntimeError:
        pass
pages["c2"] = {"workers": [], "page": {}}
assert len(cardguard.worker_pages()) == 1, "no page info and no cursor is the last page"
# Under patrol's budget the deadline bounds the read, not the page cap: a fleet past 50 pages is read whole.
many = {None: {"workers": [{"dispatchId": "p0"}], "page": {"hasMore": True, "nextCursor": "1"}}}
many.update({str(i): {"workers": [{"dispatchId": f"p{i}"}], "page": {"hasMore": i < 60, "nextCursor": str(i + 1)}}
             for i in range(1, 61)})
cardguard._orca_every_run = lambda args: many[args[args.index("--cursor") + 1] if "--cursor" in args else None]
assert len(cardguard.worker_pages(deadline=time.time() + 120)) == 61
try:
    cardguard.worker_pages()
    raise AssertionError("without a deadline the page cap stops an endless list")
except RuntimeError:
    pass
try:
    cardguard.worker_pages(deadline=time.time() + 10)
    raise AssertionError("a spent budget is a failure")
except TimeoutError:
    pass
cardguard._orca_every_run = saved_every
saved_oj = reap.orca_json
reap.orca_json = lambda *a: None
assert reap.orca_workers() is None and reap.orca_workers("reclaimable") is None
reap.orca_json = lambda *a: {"workers": [{"dispatchId": "r"}], "scope": {"source": "bound"}}
assert reap.orca_workers("reclaimable") == [{"dispatchId": "r"}], "the janitor's own reclaim list keeps its scope"
reap.orca_json = saved_oj
# The real reader clears this terminal's handle, so worker-list is not bound to the caller's Run.
saved_run = cardguard.subprocess.run
got = {}


def fake_run(cmd, **kw):
    got.update(env=kw["env"], cwd=kw["cwd"])
    return types.SimpleNamespace(returncode=0, stdout='{"ok": true, "result": {"workers": []}}')


cardguard.subprocess.run = fake_run
os.environ["ORCA_TERMINAL_HANDLE"] = "term_me"
assert cardguard.worker_pages() == [] and got["env"]["ORCA_TERMINAL_HANDLE"] == "" and got["cwd"] == cardguard.REPO
cardguard.subprocess.run = saved_run

# The lock between two processes: both reach for the same card at once, one acts and the other skips.
race = tmp / "race"
race.mkdir()
child = f"""
import os, sys, time, pathlib
sys.path.insert(0, {str(HERE)!r})
import cardguard
go = pathlib.Path({str(race)!r}) / "go"
while not go.exists():
    time.sleep(0.01)
with cardguard.card_lock("/w/same") as mine:
    (pathlib.Path({str(race)!r}) / str(os.getpid())).write_text("acted" if mine else "skipped")
    if mine:
        time.sleep(1.0)
"""
env = {**os.environ, "AGENT_SKILLS_STATE": str(tmp / "state")}
procs = [subprocess.Popen([sys.executable, "-c", child], env=env) for _ in range(2)]
time.sleep(0.5)
(race / "go").write_text("")
assert all(p.wait(timeout=20) == 0 for p in procs)
results = sorted(f.read_text() for f in race.iterdir() if f.name != "go")
assert results == ["acted", "skipped"], results
# Another card is not held by it; the same card is free again once released.
with cardguard.card_lock("/w/same") as a, cardguard.card_lock("/w/other") as b:
    assert a and b
# A cleaner that took the card's lock through guard.hold at the top of its loop goes on into card_lock, which is the
# same lock file: a second flock on it in this process would fail and skip every card.
import guard  # noqa: E402
with guard.hold([("card", reap.real("/w/same"))]) as outer, cardguard.card_lock("/w/same") as inner:
    assert outer and inner
    assert guard.lock_file("card", reap.real("/w/same")).name.startswith("card-")
print("test_cardguard: ok")
