#!/usr/bin/env python3
"""Target locks, the ownership file and the shared guards (guard.py), and how reap.py, cards.py, reap_hard.py and
preview_watch.py act through them. Machine reads are stubbed; nothing here closes, kills or deletes."""
import contextlib, io, json, os, pathlib, subprocess, sys, tempfile, time

tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["AGENT_SKILLS_STATE"] = str(tmp / "state")
os.environ["AGENT_SKILLS_CONFIG"] = str(tmp / "config.json")
os.environ["PREVIEW_WATCH_STATE"] = str(tmp / "pw")
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts"))
import guard, reap  # noqa: E402

STATE = pathlib.Path(os.environ["AGENT_SKILLS_STATE"])
STATE.mkdir(parents=True)
NOW = time.time()


@contextlib.contextmanager
def other_process_holds(kind, key):
    """A second process holding one target lock until the block ends."""
    p = guard.lock_file(kind, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    child = subprocess.Popen([sys.executable, "-c", "import fcntl,sys; f=open(sys.argv[1],'w'); "
                              "fcntl.flock(f, fcntl.LOCK_EX); print('held', flush=True); sys.stdin.read()", str(p)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "held"
        yield
    finally:
        child.stdin.close()
        child.wait(10)


def free(kind, key):
    """Whether another process could take the lock now."""
    r = subprocess.run([sys.executable, "-c", "import fcntl,sys; f=open(sys.argv[1],'w'); "
                        "fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)", str(guard.lock_file(kind, key))],
                       stderr=subprocess.DEVNULL)
    return r.returncode == 0


# ---------------------------------------------------------------- locks

with guard.hold([("card", "/ws/a")]) as got:
    assert got and not free("card", "/ws/a")
    with guard.hold([("card", "/ws/a"), ("serve", "4600")]) as inner:  # re-entrant: the outer lock stays this one's
        assert inner and not free("serve", "4600")
    assert not free("card", "/ws/a") and free("serve", "4600")
assert free("card", "/ws/a") and not guard.held()

with other_process_holds("card", "/ws/a"):
    with guard.hold([("card", "/ws/a")]) as got:
        assert got is False
assert not guard.held()

# Another thread of this process is another cleaner: the lock it holds is not this thread's to re-enter.
import threading  # noqa: E402
got_in_thread = []
with guard.hold([("card", "/ws/t")]) as got:
    th = threading.Thread(target=lambda: got_in_thread.append(guard.hold([("card", "/ws/t")]).__enter__()))
    th.start()
    th.join()
assert got and got_in_thread == [False]

# A forked child is another process: the lock its parent holds is refused, not re-entered.
with guard.hold([("card", "/ws/f")]) as got:
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        with guard.hold([("card", "/ws/f")]) as child_got:
            os.write(w, b"1" if child_got else b"0")
        os._exit(0)
    os.waitpid(pid, 0)
    assert got and os.read(r, 1) == b"0"

# Always in LOCK_ORDER whatever order a caller names them, so two cleaners never wait on each other in a cycle.
order = []
real_lock_file = guard.lock_file
guard.lock_file = lambda kind, key: (order.append(kind), real_lock_file(kind, key))[1]
with guard.hold([("serve", "1"), ("other", "x"), ("proc", "9"), ("volume", "v"), ("container", "i"), ("project", "p"), ("card", "/c")]):
    pass
guard.lock_file = real_lock_file
assert order == list(guard.LOCK_ORDER), order

# One lock of the set taken by another process: the ones this call took are let go at once.
with other_process_holds("serve", "4600"):
    with guard.hold([("serve", "4600"), ("card", "/ws/b")]) as got:
        assert got is False and free("card", "/ws/b")
assert not guard.held()

# A lock file that cannot be made is not a lock held: the target is left.
saved_lf = guard.lock_file
guard.lock_file = lambda kind, key: tmp / "no-such-dir-file" / "x" / "y.lock" if kind == "serve" else saved_lf(kind, key)
(tmp / "no-such-dir-file").write_text("")  # a file where the lock directory should be
with guard.hold([("card", "/ws/c"), ("serve", "1")]) as got:
    assert got is False and free("card", "/ws/c")
guard.lock_file = saved_lf
assert not guard.held()

assert guard.targets({"kind": "card", "name": "/ws/a"}) == [("card", reap.real("/ws/a"))]
assert guard.targets({"kind": "compose-stack", "name": "p1"}) == [("project", "p1")]
assert guard.targets({"kind": "serve", "port": 4600}) == [("serve", "4600")]
assert guard.targets({"kind": "orphan", "key": "proc:12@x", "pid": 12}) == [("proc", "12")]
assert guard.targets({"kind": "docker", "key": "volume:v1", "name": "volume v1"}) == \
    guard.targets({"kind": "stack-volume", "key": "stack-volume:v1", "name": "v1"}) == \
    guard.targets({"kind": "labeled-volume", "key": "labeled-volume:v1", "name": "v1"}) == \
    guard.targets({"kind": "volume", "id": "v1", "name": "v1"}) == [("volume", "v1")]
assert guard.targets({"kind": "stopped-container", "cid": "abc"}) == guard.targets({"kind": "container", "cid": "abc"}) == \
    [("container", "abc")]
assert guard.LOCK_ORDER.index("project") < guard.LOCK_ORDER.index("container") < guard.LOCK_ORDER.index("volume")
assert guard.rule({"kind": "stopped-container"}) == guard.rule({"kind": "container"}, "hard") == "container:stopped"
assert guard.rule({"kind": "labeled-volume"}) == guard.rule({"kind": "volume"}, "hard") == "volume:labeled"
SHUT = {"port": {4999}, "pid": set(), "path": [], "project": {"example-held"}}
saved_owner = reap.owner_facts, reap.owner_hold
reap.owner_facts, reap.owner_hold = dict, lambda owners, facts: None  # owner_hold is test_reap.py's
for kind in ("stopped-container", "container", "labeled-volume", "volume"):
    assert guard.item({"kind": kind, "name": "example-verify-1", "project": "", "ports": []}, SHUT) is None
    assert guard.item({"kind": kind, "name": "example-held", "project": ""}, SHUT), kind
    assert guard.item({"kind": kind, "name": "example-verify-1", "project": "example-held"}, SHUT), kind
    assert guard.item({"kind": kind, "name": "example-verify-1", "project": "", "ports": [4999]}, SHUT) == "보호 포트 4999", kind
reap.owner_facts, reap.owner_hold = saved_owner

# ---------------------------------------------------------------- ownership

OWN, LAST = STATE / "sweep-owner.json", STATE / "sweep-last.json"
assert guard.ownership() is None and guard.old_acts("card:landed", None) == (True, None)
assert guard.check_owner("card:landed") is None
OWN.write_text(json.dumps({"generation": 2, "rules": {"card:landed": "sweep"}, "interval": 600}))
LAST.write_text(json.dumps({"t": NOW - 60}))
assert guard.check_owner("card:close-idle") is None  # a rule the sweep does not own stays the old cleaner's
assert "sweep 소유(세대 2)" in guard.check_owner("card:landed")
LAST.write_text(json.dumps({"t": NOW - 3 * 600 - 60}))  # the sweep missed three rounds: the old cleaner takes over
notes = []
assert guard.check_owner("card:landed", notes) is None and "옛 주체" in notes[0], notes
LAST.unlink()
assert guard.check_owner("card:landed") is None  # a sweep that never had a good round owns nothing yet
for bad in ("{", "{}", json.dumps({"t": "bad"}), json.dumps({"t": True}), '{"t": -1e999}', '{"t": NaN}',
            '{"t": 1' + "0" * 400 + '}', b"\xff\xfe"):  # a last round that cannot be read
    LAST.write_bytes(bad if isinstance(bad, bytes) else bad.encode())
    assert "읽지 못해" in guard.check_owner("card:landed"), bad
    assert guard.check_owner("card:close-idle") is None, bad
LAST.unlink()
for bad in ("{", "[]", json.dumps({"generation": "2", "rules": {}}), json.dumps({"rules": []}),
            json.dumps({"rules": {}, "interval": "x"}), json.dumps({"rules": {}, "interval": 0}),
            '{"rules": {}, "interval": 1e999}', '{"rules": {}, "interval": 1' + "0" * 400 + '}', b"\xff\xfe{}",
            json.dumps({"rules": {"card:strict": []}}), json.dumps({"rules": {"card:strict": None}})):
    OWN.write_bytes(bad if isinstance(bad, bytes) else bad.encode())
    try:
        guard.ownership()
        raise AssertionError(bad)
    except guard.Unreadable:
        pass
    assert "소유권 파일을 읽지 못함" in guard.check_owner("card:close-idle"), bad
OWN.unlink()

# ---------------------------------------------------------------- settings the guards read

CONFIG = tmp / "config.json"
assert guard.protect_file() == "~/.config/agent-skills/reap-protect.txt" and guard.always_on() == set()  # no file
CONFIG.write_text("{not json")
try:  # a settings file that cannot be read is not "no setting": the person's protect_file may be in it
    guard.protect_file()
    raise AssertionError("unreadable settings read as defaults")
except guard.NoProtect:
    pass
assert guard.always_on() == set()  # as before guard.py
for bad in ({"reap": {"pressure": True}}, {"reap": ["x"]}, {"reap": {"pressure": {"always_on": False}}},
            {"reap": {"pressure": {"always_on": {}}}}, {"reap": {"pressure": {"always_on": ""}}}, {"reap": 0}, {"reap": {"pressure": {"always_on": "db"}}},
            {"reap": {"pressure": {"always_on": [1]}}}):
    CONFIG.write_text(json.dumps(bad))
    try:
        guard.always_on()
        raise AssertionError(bad)
    except ValueError:
        pass
    assert guard.keep({"kind": "compose-stack", "name": "x"}, {"project": set()}, rule_name="") is not None, bad
CONFIG.write_text(json.dumps({"reap": {"pressure": {"always_on": ["db"]}}}))
assert guard.always_on() == {"db"} and guard.project("db", {"project": set()})
CONFIG.unlink()

# ---------------------------------------------------------------- guards keep, never pass

PROTECT = {"port": {4601}, "pid": {77}, "path": [reap.real(str(tmp / "keep"))], "project": {"kept"}}
CARD = reap.real(str(tmp / "ws" / "card"))
saved = {n: getattr(reap, n) for n in ("processes", "human_in_tree")}

# One guard that raises keeps that target; it never ends the cleaner's round.
reap.processes = lambda: (_ for _ in ()).throw(RuntimeError("ps: failed"))
assert "검사를 마치지 못함(RuntimeError" in guard.keep({"kind": "orphan", "key": "proc:5@s", "pid": 5, "start": "s"},
                                                      PROTECT)
assert guard.item({"kind": "card", "name": CARD, "verdict": "삭제"}, PROTECT) is None  # cardguard.guard's, in act
reap.processes = lambda: {}

assert guard.item({"kind": "compose-stack", "name": "kept"}, PROTECT) == "보호 목록·상시 서비스의 프로젝트"
assert guard.item({"kind": "serve", "port": 443, "local": 4601}, PROTECT) == "보호 포트"
assert guard.item({"kind": "tmpdir", "name": str(tmp / "keep" / "x")}, PROTECT) == "보호 목록의 경로"
assert guard.item({"kind": "listener", "pid": 77, "start": "s"}, PROTECT) == "보호 pid"
reap.processes = lambda: {5: {"pid": 5, "start": "s", "cmd": "node a"}}
reap.human_in_tree = lambda pid, rows: []
assert guard.item({"kind": "orphan", "key": "proc:5@s", "pid": 5, "start": "s", "cmd": "node b"}, PROTECT) == \
    "사라졌거나 다른 프로세스"
assert guard.item({"kind": "orphan", "key": "proc:5@s", "pid": 5, "start": "s", "cmd": "node a"}, PROTECT) is None
assert guard.item({"kind": "listener", "pid": 5, "start": "s", "cmd": "other"}, PROTECT, "hard") is None
reap.human_in_tree = lambda pid, rows: [9]
assert "pid 9" in guard.item({"kind": "listener", "pid": 5, "start": "s"}, PROTECT, "hard")
for n, f in saved.items():
    setattr(reap, n, f)

# ---------------------------------------------------------------- reap --plan through the guards


def reap_plan(items, kinds):
    plan = tmp / "plan.json"
    plan.write_text(json.dumps({"items": items, "hours": 6, "repos": [], "kinds": kinds}))
    out, argv = io.StringIO(), sys.argv
    sys.argv = ["reap.py", "reap", "--plan", str(plan)]
    CODE[0] = 0
    try:
        with contextlib.redirect_stdout(out):
            try:
                reap.main()
            except SystemExit as e:  # the exit status a shell would see
                CODE[0] = e.code
    finally:
        sys.argv = argv
    return out.getvalue()


CODE = [0]


ITEM = {"kind": "tmpdir", "key": "tmpdir:/x", "name": str(tmp / "x"), "target": True, "why": "w"}
acted = []
saved_scan, saved_act, saved_record = reap.scan, reap.act, reap.record_reap
reap.scan = lambda kinds, hours, cfg, repos: ([dict(ITEM)], [])
reap.act = lambda i, notes, done=None: (acted.append(i["key"]), "휴지통")[1]
reap.record_reap = lambda *a: None
assert "휴지통" in reap_plan([ITEM], []) and acted == ["tmpdir:/x"]
logged = [json.loads(l) for l in (STATE / "reap-actions.jsonl").read_text().splitlines()]
assert [(r["key"], r["ok"]) for r in logged] == [("tmpdir:/x", True)]  # what `sweep diff` matches against
reap.act = lambda i, notes, done=None: (_ for _ in ()).throw(ValueError("unexpected"))
try:
    reap_plan([ITEM], [])
    raise AssertionError("an unexpected error was swallowed")
except ValueError:
    pass
last = json.loads((STATE / "reap-actions.jsonl").read_text().splitlines()[-1])
assert (last["key"], last["ok"]) == ("tmpdir:/x", False), last  # logged before it went up
(STATE / "reap-actions.jsonl").write_text("\n".join(json.dumps(r) for r in logged) + "\n")
reap.act = lambda i, notes, done=None: (acted.append(i["key"]), "휴지통")[1]
with other_process_holds("other", "tmpdir:/x"):
    out = reap_plan([ITEM], [])
assert "대상 잠금" in out and acted == ["tmpdir:/x"], out
OWN.write_text(json.dumps({"generation": 1, "rules": {"trash:tmpdir": "sweep"}}))
LAST.write_text(json.dumps({"t": time.time()}))
out = reap_plan([ITEM], [])
assert "sweep 소유" in out and acted == ["tmpdir:/x"], out
OWN.write_text("{")
out = reap_plan([ITEM], [])
assert "아무것도 하지 않았다" in out and acted == ["tmpdir:/x"], out
OWN.unlink()
(tmp / "protect.txt").write_text(f"path {tmp / 'x'}\n")
(tmp / "config.json").write_text(json.dumps({"reap-hard": {"protect_file": str(tmp / "protect.txt")}}))
out = reap_plan([ITEM], [])
assert "보호 목록의 경로" in out and acted == ["tmpdir:/x"], out
(tmp / "protect.txt").write_text("nonsense line\n")
out = reap_plan([ITEM], [])
assert "아무것도 하지 않았다" in out and acted == ["tmpdir:/x"], out
(tmp / "protect.txt").unlink()  # no protect file: the janitor reaps as before it read one
assert "휴지통" in reap_plan([ITEM], []) and acted == ["tmpdir:/x"] * 2
for bad in ({"reap-hard": "invalid"}, {"reap-hard": {"protect_file": 3}}, {"reap-hard": []}, {"reap-hard": False},
            {"reap-hard": {"protect_file": None}}, {"reap-hard": {"protect_file": ""}}, []):
    (tmp / "config.json").write_text(json.dumps(bad))
    out = reap_plan([ITEM], [])
    assert "아무것도 하지 않았다" in out and acted == ["tmpdir:/x"] * 2, out
(tmp / "config.json").unlink()

# An error in a check before the act (PermissionError injected) never reaches act, and the run ends non-zero.
# A clean round still exits 0.
before = len(acted)
assert "휴지통" in reap_plan([ITEM], []) and CODE[0] == 0 and len(acted) == before + 1
before = len(acted)


def denied(*a, **k):
    raise PermissionError(13, "Permission denied")


# (1) the re-scan itself cannot read: nothing is acted on and the error is not swallowed.
reap.scan = denied
try:
    reap_plan([ITEM], [])
    raise AssertionError("a scan PermissionError was swallowed")
except PermissionError:
    pass
reap.scan = lambda kinds, hours, cfg, repos: ([dict(ITEM)], [])
# (2) the ownership file exists but cannot be read (mode 000 is the real shape; the stub covers a root-run test).
saved_ownership = guard.ownership
guard.ownership = lambda: (_ for _ in ()).throw(guard.Unreadable("sweep-owner.json: Permission denied"))
out = reap_plan([ITEM], [])
assert "아무것도 하지 않았다" in out and CODE[0] == 1 and len(acted) == before, (out, CODE[0])
guard.ownership = saved_ownership
# (3) the per-target check raises PermissionError after the lock: this target is left, the others still run, exit 1.
OTHER = {**ITEM, "key": "tmpdir:/y", "name": str(tmp / "y")}
reap.scan = lambda kinds, hours, cfg, repos: ([dict(ITEM), dict(OTHER)], [])
saved_item = guard.item
guard.item = lambda i, protect, cleaner="reap": denied() if i["key"] == "tmpdir:/x" else None
out = reap_plan([ITEM, OTHER], [])
assert "검사를 마치지 못함(PermissionError" in out and CODE[0] == 1, (out, CODE[0])
assert acted[before:] == ["tmpdir:/y"], acted
guard.item = saved_item
# (4) the ownership re-read inside the lock fails: same.
before = len(acted)
saved_check = guard.check_owner
guard.check_owner = lambda rule_name, notes=None: "소유권 파일을 읽지 못함: sweep-owner.json: Permission denied"
out = reap_plan([ITEM], [])
assert "소유권 파일을 읽지 못함" in out and CODE[0] == 1 and len(acted) == before, (out, CODE[0])
guard.check_owner = saved_check
# (5) the real path: the sweep owns the rule and sweep-last.json cannot be read (PermissionError from read_text).
OWN.write_text(json.dumps({"generation": 1, "rules": {"trash:tmpdir": "sweep"}}))
before = len(acted)
saved_read = pathlib.Path.read_text
pathlib.Path.read_text = lambda self, *a, **k: denied() if self.name == "sweep-last.json" else saved_read(self, *a, **k)
try:
    out = reap_plan([ITEM], [])
finally:
    pathlib.Path.read_text = saved_read
    OWN.unlink()
assert "소유권 파일을 읽지 못함" in out and CODE[0] == 1 and len(acted) == before, (out, CODE[0])
# (6) the protect list cannot be read: nothing acted on, exit 1.
(tmp / "protect.txt").write_text("nonsense line\n")
(tmp / "config.json").write_text(json.dumps({"reap-hard": {"protect_file": str(tmp / "protect.txt")}}))
out = reap_plan([ITEM], [])
assert "아무것도 하지 않았다" in out and CODE[0] == 1 and len(acted) == before, (out, CODE[0])
# A guard that merely keeps a target (protect list) is not an error.
(tmp / "protect.txt").write_text(f"path {tmp / 'x'}\n")
(tmp / "config.json").write_text(json.dumps({"reap-hard": {"protect_file": str(tmp / "protect.txt")}}))
reap.scan = lambda kinds, hours, cfg, repos: ([dict(ITEM)], [])
out = reap_plan([ITEM], [])
assert "보호 목록의 경로" in out and CODE[0] == 0 and len(acted) == before, (out, CODE[0])
(tmp / "protect.txt").unlink()
(tmp / "config.json").unlink()
reap.scan, reap.act, reap.record_reap = saved_scan, saved_act, saved_record

# ---------------------------------------------------------------- cards apply through the guards

import cards  # noqa: E402


def cards_apply(items):
    plan = tmp / "cards.json"
    text = json.dumps({"items": items})
    plan.write_text(text)
    cards.SEAL_DIR = tmp / "seals"
    cards.seal_plan(text)  # as scan --plan does
    cards.cards_listing = lambda repo_list=(): [{"path": CARD}]
    cards.preflight = lambda items, cfg: None  # the preflight is tested in test_cards.py; these cases are the per-card loop
    out, argv = io.StringIO(), sys.argv
    sys.argv = ["cards.py", "apply", "--plan", str(plan)]
    try:
        with contextlib.redirect_stdout(out):
            try:
                cards.main(sys.argv)
            except SystemExit as e:
                code = e.code
    finally:
        sys.argv = argv
    return out.getvalue(), code


ROW = {"path": CARD, "name": "card", "verdict": cards.CLOSE, "close": ["t1"], "head": "h"}
done_paths = []
saved_cact = cards.act
cards.act = lambda item, cfg, done=None: (done_paths.append(item["path"]), "터미널 1개 닫음")[1]
out, code = cards_apply([ROW])
assert code == 0 and done_paths == [CARD], out
logged = [json.loads(l) for l in (STATE / "reap-actions.jsonl").read_text().splitlines()]
assert (logged[-1]["key"], logged[-1]["ok"]) == (f"card:{CARD}", True), logged[-1]
with other_process_holds("card", CARD):
    out, code = cards_apply([ROW])
assert "대상 잠금" in out and done_paths == [CARD], out
OWN.write_text(json.dumps({"generation": 3, "rules": {"card:close-idle": "sweep"}}))
LAST.write_text(json.dumps({"t": time.time()}))
out, code = cards_apply([ROW])
assert "sweep 소유" in out and done_paths == [CARD], out
OWN.write_text("not json")
out, code = cards_apply([ROW])
assert code and done_paths == [CARD], (out, code)
OWN.unlink()


def act_then_fails(item, cfg, done=None):
    done["closed"] = ["t1"]  # a terminal closed, then a read failed in a way no handler names
    raise ValueError("bad JSON from gh")


cards.act = act_then_fails
try:
    cards_apply([ROW])
    raise AssertionError("an unexpected error was swallowed")
except ValueError:
    pass
last = json.loads((STATE / "reap-actions.jsonl").read_text().splitlines()[-1])
assert (last["ok"], last["handles"]) == (False, ["t1"]), last  # the close it did is still on record
cards.act = saved_cact

# cards.act tells its caller which terminals it really closed: the plan's, less those already gone.
import cardguard  # noqa: E402
saved_reread, saved_run2, saved_cg = cards.reread, reap.run, cardguard.guard
cardguard.guard = lambda *a, **k: (True, "확인함")
cards.reread = lambda path, cfg: (cards.CLOSE, "w", ["B"], {"head": "h"})
reap.run = lambda cmd, **k: ""
done = {}
assert cards.act({"path": CARD, "verdict": cards.CLOSE, "head": "h", "close": ["A", "B"]}, {}, done=done) == \
    "터미널 1개 닫음, worktree 는 남김" and done["closed"] == ["B"]
# A card folder made again since the plan (another inode) is left; the one acted on is the one logged.
INO_CARD = str(tmp / "ino-card")
os.makedirs(INO_CARD)
ino = os.stat(INO_CARD).st_ino
done = {}
assert cards.act({"path": INO_CARD, "verdict": cards.CLOSE, "head": "h", "close": ["B"], "ino": ino + 1}, {}, done=done) == \
    "건너뜀: 판정 뒤 카드 폴더가 다시 만들어짐" and done["closed"] == []
done = {}
cards.act({"path": INO_CARD, "verdict": cards.CLOSE, "head": "h", "close": ["B"], "ino": ino}, {}, done=done)
assert done == {"closed": ["B"], "identity": {"ino": ino}}, done
cards.reread, reap.run, cardguard.guard = saved_reread, saved_run2, saved_cg

# ---------------------------------------------------------------- reap-hard acts on what it judged, and only that

import reap_hard as rh  # noqa: E402

closed_terms, downs = [], []
saved_run = reap.run
reap.run = lambda cmd, **k: (closed_terms.append(cmd[-2]) if cmd[:3] == ["orca", "terminal", "close"]
                             else downs.append(cmd) if cmd[0] == "docker" else None, "")[1]
saved_ract = reap.act
STACK = {"running": 0, "stopped": 1, "first": NOW - 9e4, "last": NOW - 9e4, "ports": set(),
         "dirs": [str(tmp / "gone")], "nodir": 0}
stacks = {"s1": dict(STACK)}
saved_cs = reap.compose_stacks
reap.compose_stacks = lambda: stacks
done = {}
assert rh.act({"kind": "stack", "id": "s1"}, {}, PROTECT, done).startswith("docker compose down")
assert done == {"identity": {"start": str(STACK["last"])}}  # the stack read again, the one really brought down
assert downs == [["docker", "compose", "-p", "s1", "down"]]
(tmp / "gone").mkdir()  # the folder came back since the scan
assert rh.act({"kind": "stack", "id": "s1"}, {}, PROTECT).startswith("건너뜀: 다시 보니") and len(downs) == 1
# A working directory that cannot be looked at is not gone.
saved_stat = rh.os.stat
rh.os.stat = lambda d, *a, **k: (_ for _ in ()).throw(PermissionError(13, "denied")) if "gone" in str(d) \
    else saved_stat(d, *a, **k)
(tmp / "gone").rmdir()
assert rh.act({"kind": "stack", "id": "s1"}, {}, PROTECT).startswith("건너뜀: 다시 보니") and len(downs) == 1
rh.os.stat = saved_stat
assert reap.dir_gone(str(tmp / "gone")) and not reap.dir_gone(str(tmp))
stacks.clear()
assert "다시 읽지 못함" in rh.act({"kind": "stack", "id": "s1"}, {}, PROTECT) and len(downs) == 1
# An always-on list of the wrong shape keeps every stack in the scan and in the act, and the pass goes on.
stacks["s1"] = dict(STACK)
(tmp / "config.json").write_text(json.dumps({"reap": {"pressure": {"always_on": "db"}}}))
saved_cli = rh.pw.tailscale_cli
rh.pw.tailscale_cli = lambda cfg: None
items, notes, _ = rh.scan_rest({**rh.DEFAULTS, "workspaces": str(tmp / "nows")}, PROTECT, set(), [],
                               {"rows": {}, "cwd_pairs": {}, "listening": {}}, NOW)
assert [(i["id"], i["target"]) for i in items] == [("s1", False)] and "always_on" in notes[0], (items, notes)
try:
    rh.act({"kind": "stack", "id": "s1"}, {}, PROTECT)
    raise AssertionError("acted on an unreadable always-on list")
except ValueError:  # run_pass records it as the row's error
    pass
assert len(downs) == 1
(tmp / "config.json").unlink()
rh.pw.tailscale_cli = saved_cli
reap.run, reap.act, reap.compose_stacks = saved_run, saved_ract, saved_cs

# A pass whose target another cleaner holds leaves it; an unreadable ownership file stops the pass.
cfg = {**rh.DEFAULTS, "protect_file": str(tmp / "rh-protect.txt")}
(tmp / "rh-protect.txt").write_text("")
rh.scan_cards = lambda *a, **k: ([{"kind": "card", "id": CARD, "target": True, "why": "w", "head": "h",
                                   "handles": ["t1"]}], [], {})
rh.scan_rest = lambda *a, **k: ([], [], set())
hard_acts = []
rh.act = lambda item, cfg, protect, done=None: (hard_acts.append(item["id"]), "터미널 닫음")[1]
with other_process_holds("card", CARD):
    res, code = rh.run_pass(False, cfg, now=NOW)
assert hard_acts == [] and "대상 잠금" in res["items"][0]["result"], res
OWN.write_text(json.dumps({"generation": 1, "rules": {"card:strict": "sweep"}}))
LAST.write_text(json.dumps({"t": time.time()}))
res, code = rh.run_pass(False, cfg, now=NOW)
assert hard_acts == [] and "sweep 소유" in res["items"][0]["result"], res
OWN.write_text("{")
res, code = rh.run_pass(False, cfg, now=NOW)
assert hard_acts == [] and code and "error" in res, res
OWN.unlink()
res, code = rh.run_pass(False, cfg, now=NOW)
assert hard_acts == [CARD] and code == 0, res


def closes_then_breaks(item, cfg, protect, done=None):
    done["closed"] = ["t1"]
    raise KeyError("facts")


# An error no handler names ends the pass, but the row of what it closed first is logged, with the handles.
rh.act = closes_then_breaks
rh.LOG.unlink(missing_ok=True)
try:
    rh.run_pass(False, cfg, now=NOW)
    raise AssertionError("the KeyError was swallowed")
except KeyError:
    pass
row = json.loads(rh.LOG.read_text().splitlines()[-1])
assert row["id"] == CARD and row["handles"] == ["t1"] and row["error"].startswith("KeyError") and "t" in row, row

# ---------------------------------------------------------------- preview-watch leaves a port reap-hard holds

import preview_watch as pw  # noqa: E402

offs = []
pw.ts = lambda cli, *a: (offs.append(a), subprocess.CompletedProcess(a, 0, "", ""))[1]
pw.serve_status = lambda cli: {}
pw.entries = lambda status: {4600: "http://127.0.0.1:4600"}
st = pw.load_state()
st["owned"] = {4600: "http://127.0.0.1:4600"}
pw.save_state(st)
with other_process_holds("serve", "4600"):
    assert pw.close_owned("cli") == [4600] and offs == []
OWN.write_text("{")  # an ownership file that cannot be read stops this cleaner too
assert pw.close_owned("cli") == [4600] and offs == []
OWN.unlink()
(tmp / "config.json").write_text(json.dumps({"reap-hard": {"protect_file": str(tmp / "pw-protect.txt")}}))
(tmp / "pw-protect.txt").write_text("port 4600\n")
assert pw.close_owned("cli") == [4600] and offs == []
(tmp / "pw-protect.txt").write_bytes(b"port \xff\n")  # not UTF-8
assert pw.close_owned("cli") == [4600] and offs == []
(tmp / "pw-protect.txt").write_text("port ²\n")  # a digit int() does not read
assert pw.close_owned("cli") == [4600] and offs == []
(tmp / "pw-protect.txt").write_text("pid " + "9" * 5000 + "\n")  # past int()'s digit limit
assert pw.close_owned("cli") == [4600] and offs == []
(tmp / "pw-protect.txt").write_text("bad\n")
assert pw.close_owned("cli") == [4600] and offs == []
(tmp / "config.json").unlink()
assert pw.close_owned("cli") == [] and offs == [("serve", "--https=4600", "off")]
print("ok")
