"""candidates.py: patrol's v2 cleanup candidates are exactly today's cleaners' targets, and nothing more.
Run: python3 test_candidates.py"""
import json, os, pathlib, sys, tempfile, time, types

tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["AGENT_SKILLS_STATE"] = str(tmp)
os.environ["AGENT_SKILLS_LEDGER"] = str(tmp / "ledger.jsonl")
os.environ["AGENT_SKILLS_PATROL"] = "off"
CFG = tmp / "config.json"
os.environ["AGENT_SKILLS_CONFIG"] = str(CFG)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "scripts"))
import candidates, cards, pressure, reap, reap_hard  # noqa: E402

OK = {"terminals": "ok", "waits": "ok", "workers": "ok"}

# ---------------------------------------------------------------- mapping, on fixed scan results

REAP_ITEMS = [
    {"kind": "card", "key": "card:/w/a", "name": "/w/a", "target": True, "verdict": "삭제", "why": "PR merged", "head": "h1", "close": ["t1"]},
    {"kind": "card", "key": "card:/w/b", "name": "/w/b", "target": True, "verdict": "터미널만 닫기", "why": "열린 PR", "head": "h2", "close": ["t2"]},
    {"kind": "card", "key": "card:/w/c", "name": "/w/c", "target": False, "verdict": "남김", "why": "턴 진행 중", "head": "h3"},
    {"kind": "card", "key": "card:/w/d", "name": "/w/d", "target": False, "verdict": "사람 결정", "why": "질문 화면", "head": "h4"},
    {"kind": "compose-stack", "key": "compose-stack:s1", "name": "s1", "target": True, "why": "PR 끝"},
    {"kind": "compose-stack", "key": "compose-stack:s2", "name": "s2", "target": False, "why": "keep"},
    {"kind": "stack-volume", "key": "stack-volume:v1", "name": "v1", "target": True, "why": "컨테이너 없음", "created": 5.0},
    {"kind": "chrome-window", "key": "chrome-window:c1", "name": "c1", "target": True, "why": "PR 끝", "url": "https://example.test"},
    {"kind": "evidence", "key": "evidence-stray:/tmp/x", "name": "/tmp/x", "target": False, "why": "보고만"},
    {"kind": "orphan", "key": "proc:42", "pid": 42, "start": "Mon", "cmd": "node x", "target": True, "why": "고아"},
    {"kind": "orca-worker", "key": "orca-worker:d1", "name": "d1", "target": True, "why": "reclaimable"},
    {"kind": "program", "key": "program:p", "name": "/p", "target": True, "why": "7일"},
]
HARD = {"protect": "ok", "reap_hard_cards": "ok", "reap_hard_rest": "ok", "notes": [],
        "reap_hard_cards_items": [{"kind": "card", "id": "/w/a", "target": True, "why": "strict", "head": "h1"},
                                  {"kind": "card", "id": "/w/e", "target": False, "why": "열린 PR"}],
        "reap_hard_rest_items": [{"kind": "stack", "id": "s3", "target": True, "why": "폴더 없음"},
                                 {"kind": "listener", "id": "9:4510", "pid": 9, "start": "Tue", "cmd": "vite", "target": True, "why": "고아 리스너"},
                                 {"kind": "serve", "id": "--https=4510", "flag": "--https", "port": 4510, "local": 4510, "target": False, "why": "한 번 어두움"}]}


def collect(sources=OK, scan=None, hard=None, live=None, notes=()):
    return candidates.collect(sources, now=time.time(), reap_scan=scan or (lambda kinds: (REAP_ITEMS, list(notes))),
                              hard=hard or (lambda t: HARD), pressure_kinds=["orphan", "compose-stack"], state_dir=tmp,
                              live=live or (lambda targets, notes: targets))


before = time.time()
got = collect()
# Each candidate carries when the scan it came from started: an act before that was on what came before it.
assert all(before <= c["seen"] <= time.time() for c in got["candidates"]), got["candidates"]
assert all(isinstance(v, (int, float)) for v in got["timings"].values()), got["timings"]
assert json.loads(json.dumps(got["timings"])) == dict(got["timings"])
marks = {}


def timed_hard(t):
    marks["before_cards"] = time.time()
    candidates.timed(t, "reap_hard_cards", lambda: time.sleep(0.05))
    marks["after_cards"] = time.time()
    return HARD


got2 = collect(hard=timed_hard)
seen_of = {c["rule"]: c["seen"] for c in got2["candidates"]}
# Each source's own start: the card scan's as timed() recorded it; the rest scan, not timed by this stand-in, the
# time before the hard scans began (never later than the scan, so never after an act it could have seen).
assert marks["before_cards"] <= seen_of["card:strict"] <= marks["after_cards"], (marks, seen_of)
assert seen_of["stack:gone"] == seen_of["proc:listener"] <= marks["before_cards"], (marks, seen_of)
# The janitor scan runs beside the hard scans, so its start is only bounded by the call.
assert seen_of["card:landed"] <= marks["after_cards"], seen_of
# Two collect() calls at once keep their own start times.
import threading  # noqa: E402
slow_started, release, results = threading.Event(), threading.Event(), {}


def slow_hard(t):
    candidates.timed(t, "reap_hard_cards", lambda: (slow_started.set(), release.wait(5)))
    return HARD


th = threading.Thread(target=lambda: results.setdefault("slow", collect(hard=slow_hard)))
th.start()
assert slow_started.wait(5)
time.sleep(0.05)
results["fast"] = collect(hard=timed_hard)  # runs whole while the slow call is held inside its card scan
assert "slow" not in results
release.set()
th.join()
slow_t = {c["rule"]: c["seen"] for c in results["slow"]["candidates"]}["card:strict"]
fast_t = {c["rule"]: c["seen"] for c in results["fast"]["candidates"]}["card:strict"]
assert slow_t < fast_t - 0.04, (slow_t, fast_t)
pairs = {(c["rule"], c["key"]) for c in got["candidates"]}
assert pairs == {("card:landed", "card:/w/a"), ("card:close-idle", "card:/w/b"), ("stack:registered", "compose-stack:s1"),
                 ("volume:prefixed", "stack-volume:v1"), ("proc:orphan", "proc:42"), ("worker:reclaimable", "orca-worker:d1"),
                 ("program:archive", "program:p"), ("card:strict", "card:/w/a"), ("stack:gone", "stack:s3"),
                 ("proc:listener", "listener:9:4510")}, pairs
assert [r["key"] for r in got["reports"]] == ["chrome-window:c1", "evidence-stray:/tmp/x"], got["reports"]
assert all("보고만" in r["why"] for r in got["reports"])
assert got["alerts"] == [{"kind": "card", "key": "card:/w/d", "path": "/w/d", "decide": "질문 화면"}], got["alerts"]
a = next(c for c in got["candidates"] if c["rule"] == "card:landed")
assert a["identity"] == {"head": "h1", "verdict": "삭제", "close": ["t1"]} and a["needs"] == sorted(
    {"reap_scan", "reap_scan:card", "terminals", "waits", "workers"}), a
o = next(c for c in got["candidates"] if c["rule"] == "proc:orphan")
assert o["when"] == "pressure" and "pressure_live" in o["needs"], "orphan is the pressure round's alone"
assert "when" not in next(c for c in got["candidates"] if c["rule"] == "stack:registered"), "the janitor reaps stacks too"
assert next(c for c in got["candidates"] if c["rule"] == "proc:listener")["needs"] == ["protect", "reap_hard_cards", "reap_hard_rest"]
assert got["sources"]["reap_scan"] == "ok" and got["sources"]["pressure_live"] == "ok" and got["sources"]["protect"] == "ok"
json.dumps(got)  # the verdicts file holds it as is

# A candidate whose source failed this round is only reported.
got = collect({**OK, "waits": "failed"})
assert {c["kind"] for c in got["candidates"]} == {"compose-stack", "stack-volume", "orphan", "orca-worker", "program",
                                                  "stack", "listener"}, got["candidates"]
held = {r["key"] for r in got["reports"] if r.get("rule")}
assert held == {"card:/w/a", "card:/w/b"} and all("waits" in r["why"] for r in got["reports"] if r.get("rule"))
# A scan that returned but noted an unreadable part fails that part, or the whole scan for a note naming no kind.
got = collect(notes=["compose-stack: docker 를 읽지 못함, 건너뜀"])
assert got["sources"]["reap_scan:compose-stack"] == "failed" and got["sources"]["reap_scan"] == "ok"
assert "compose-stack:s1" not in {c["key"] for c in got["candidates"]} and "card:/w/a" in {c["key"] for c in got["candidates"]}
got = collect(notes=["orca worktree·워커·automation 목록이나 프로세스 cwd 를 읽지 못함: 카드는 판정하지 않는다"])
assert got["sources"]["reap_scan:card"] == "failed" and not any(c["rule"].startswith("card:l") for c in got["candidates"])
got = collect(notes=["something 을 읽지 못함"])
assert got["sources"]["reap_scan"] == "failed" and not any(c["rule"] in candidates.RULE_OF.values() for c in got["candidates"])
assert collect(notes=["orphan: reap.orphan_paths 가 비어 있어 cwd 가 사라진 고아만 본다"])["sources"]["reap_scan:orphan"] == "ok"
# The pressure round's live-card filter: unreadable, its kinds are only reported.
got = collect(live=lambda targets, notes: None)
assert got["sources"]["pressure_live"] == "failed" and "proc:42" not in {c["key"] for c in got["candidates"]}


def boom(*_):
    raise RuntimeError("docker hung")


got = collect(scan=boom)
assert got["sources"]["reap_scan"] == "failed" and {c["rule"] for c in got["candidates"]} == {
    "card:strict", "stack:gone", "proc:listener"}, got
assert any("docker hung" in n for n in got["notes"])
got = collect(hard=lambda t: {"protect": "failed", "notes": ["reap-hard: 보호 파일 없음"]})
assert not any(c["rule"] in candidates.HARD_RULE.values() for c in got["candidates"])
assert got["sources"]["protect"] == "failed" and got["sources"]["reap_hard_rest"] == "failed"
got = collect(hard=boom)
assert got["sources"]["reap_hard_cards"] == "failed" and not any(c["rule"] == "card:strict" for c in got["candidates"])
got = collect(hard=lambda t: {**HARD, "reap_hard_cards": "failed"})
assert {c["rule"] for c in got["candidates"]} & set(candidates.HARD_RULE.values()) == {"stack:gone"}, \
    "a listener leans on the card scan's machine read"
# reap-hard kinds with no shadow rule: reported, and the other kinds' candidates still come through.
leftover = [{"kind": "container", "id": "example-cid", "target": True, "why": "멈춤"},
            {"kind": "volume", "id": "example-verify-data", "target": True, "why": "라벨"}]
got = collect(hard=lambda t: {**HARD, "reap_hard_rest_items": HARD["reap_hard_rest_items"] + leftover})
assert "stack:gone" in {c["rule"] for c in got["candidates"]}, got["candidates"]
assert not {c["kind"] for c in got["candidates"]} & {"container", "volume"}, got["candidates"]
assert {r["key"] for r in got["reports"] if "규칙 없음" in r["why"]} >= {
    "container:example-cid", "volume:example-verify-data"}, got["reports"]
# Progress: what was read is there even if the round is cut before collect returns.
prog = {}
try:
    candidates.collect(OK, reap_scan=lambda kinds: (REAP_ITEMS, []), hard=lambda t: 1 / 0, pressure_kinds=[],
                       state_dir=tmp, progress=prog)
except ZeroDivisionError:
    raise AssertionError("a hard scan error is a failed source, not a raise")
assert prog["sources"]["reap_scan"] == "ok" and "reap_scan" in prog["timings"]

# The janitor's daily kinds are scanned only when due, as its precheck does; the stamp is only read.
assert "program" in candidates.scan_kinds({}, [], tmp, time.time())
reap.stamp_user_folder(tmp, kind="program")
before = (tmp / "program-last.json").read_text()
kinds = candidates.scan_kinds({}, ["orphan"], tmp, time.time())
assert "program" not in kinds and "orphan" in kinds and "card" in kinds and (tmp / "program-last.json").read_text() == before

# ---------------------------------------------------------------- real judging, on fixed machine reads
# Today's paths run for real: reap.scan for the janitor's kinds, pressure.plan_targets for its safe kinds and
# reap_hard.run_pass --dry-run. Candidates must be exactly their targets (pressure after its live-card filter), and
# reap-hard's minus what only a card closed in the same pass would free.

ROWS = {42: {"pid": 42, "ppid": 1, "uid": 501, "start": "Mon Nov  2 10:00:00 2026", "age": 99999, "rss": 1, "cpu": 0.0,
             "cmd": "node /x/a.js"},
        43: {"pid": 43, "ppid": 1, "uid": 501, "start": "Mon Nov  2 10:00:01 2026", "age": 99999, "rss": 1, "cpu": 0.0,
             "cmd": "node /x/b.js"},
        44: {"pid": 44, "ppid": 1, "uid": 501, "start": "Mon Nov  2 10:00:02 2026", "age": 10, "rss": 1, "cpu": 0.0,
             "cmd": "node /x/c.js"}}
CWD = {42: ("/w/live/gone", 1), 43: ("/w/dead/gone", 1), 44: ("/w/dead/gone2", 1)}
CARD_FACTS = {
    "/w/a": {"terminals": [], "pr": "acme/app#1", "state": "MERGED", "carried": True, "dirty": False, "unpushed": False, "quiet": 99999},
    "/w/b": {"terminals": [{"handle": "t2", "agent": True, "silent": 99999, "screen": None}], "pr": "acme/app#2",
             "state": "OPEN", "quiet": 99999},
    "/w/d": {"terminals": [{"handle": "t4", "agent": True, "silent": 99999, "screen": "question"}], "pr": "acme/app#3",
             "state": "MERGED", "carried": True, "quiet": 99999},
    "/w/f": {"terminals": [], "pr": "acme/app#4", "state": "MERGED", "carried": True, "dirty": True, "quiet": 99999}}
reap.processes = lambda: ROWS
reap.cwds = lambda inodes=False: dict(CWD) if inodes else {p: c for p, (c, _) in CWD.items()}
reap.orca_workers = lambda state=None: [{"dispatchId": "d9", "taskId": "t9"}]
reap.compose_stacks = lambda: {}
reap.repos = lambda given: []
reap.pr_reader = lambda gh=None: (lambda ref: None)
reap.config = lambda: {}
ev = tmp / "evroot"
ev.mkdir()
(ev / "shot.png").write_bytes(b"x")
os.utime(ev / "shot.png", (1, 1))
real_stray = reap.stray_evidence
reap.stray_evidence = lambda roots, registered, hours, now: real_stray([str(ev)], registered, hours, now)
reap.PROGRAMS = tmp / "programs"
# cards.judge runs for real; only what gather reads from git, gh and Orca is fixed.
cards.read_machine = lambda cfg, now=None: {"st": cards.settings({}), "cwd_of": {}, "workers_unread": False, "runners": set()}
cards.cards_listing = lambda repo_list=(): [{"path": p} for p in CARD_FACTS]
cards.gather = lambda w, m: {"path": w["path"], "name": w["path"][3:], "repo": "r", "branch": w["path"][3:],
                             "head": "h-" + w["path"][3:], "regenerated": [], **CARD_FACTS[w["path"]]}
LIVE = [{"path": "/w/live", "live": True}, {"path": "/w/dead", "live": False}]
PRESSURE_KINDS = ["orphan", "compose-stack", "gui-app", "orca-worker"]


def keys(items):
    return {i["key"] for i in items if i["target"]}


janitor, _ = reap.scan(set(reap.SCHEDULED), 6.0, {}, [])
plan = pressure.plan_targets(PRESSURE_KINDS, tmp / "pressure-plan.json",
                             run=lambda cmd, **k: types.SimpleNamespace(stdout=json.dumps(
                                 {"items": reap.scan(set(PRESSURE_KINDS), 6.0, {}, [])[0], "notes": []})),
                             cards=LIVE, cwd_of={p: c for p, (c, _) in CWD.items()})[0]
assert keys(janitor) == {"card:/w/a", "card:/w/b", "orca-worker:d9"}, keys(janitor)
assert {i["key"] for i in plan} == {"proc:43", "orca-worker:d9"}, plan
before = sorted((p.name, p.stat().st_mtime) for p in tmp.iterdir())
got = candidates.collect(OK, pressure_kinds=PRESSURE_KINDS, state_dir=tmp, hard=lambda t: {"protect": "failed"},
                         live=lambda t, n: pressure.keep_live(t, n, cards=LIVE, cwd_of={p: c for p, (c, _) in CWD.items()}))
assert {c["key"] for c in got["candidates"]} == keys(janitor) | {i["key"] for i in plan}, got["candidates"]
assert {(c["rule"], c["key"]) for c in got["candidates"] if c["kind"] == "card"} == {
    ("card:landed", "card:/w/a"), ("card:close-idle", "card:/w/b")}
assert next(c for c in got["candidates"] if c["key"] == "card:/w/b")["identity"]["close"] == ["t2"]
assert "proc:42" not in {c["key"] for c in got["candidates"]}, "an orphan in a card with a live terminal stays"
assert [a["key"] for a in got["alerts"]] == ["card:/w/d"], "the question screen is a person's call"
assert [r["key"] for r in got["reports"]] == [f"evidence-stray:{ev}"], "today's report-only rows stay reports"
assert sorted((p.name, p.stat().st_mtime) for p in tmp.iterdir()) == before, "no stamp, plan or ledger written"

# reap-hard: judge_card, judge_stack, judge_listener and judge_serve run for real on fixed machine reads.
ws = pathlib.Path(os.path.realpath(tmp)) / "ws"
c1, c2 = ws / "r" / "c1", ws / "r" / "c2"
c1.mkdir(parents=True)
c2.mkdir(parents=True)
(tmp / "protect.txt").write_text("# nothing\n")
CFG.write_text(json.dumps({"reap-hard": {"workspaces": str(ws), "protect_file": str(tmp / "protect.txt")}}))
HROWS = {9: {"pid": 9, "ppid": 1, "uid": 501, "start": "Tue", "age": 99999, "rss": 1, "cpu": 0.0, "cmd": "node vite"}}
HCWD = {9: ("/nonexistent/vite-gone", 1)}
PRS = {"acme/app#1": {"state": "MERGED", "headRefOid": "h1"}, "acme/app#2": {"state": "OPEN", "headRefOid": "h2"}}
reap_hard.read_machine = lambda now: {"rows": HROWS, "cwd_pairs": HCWD, "cwd_of": {p: c for p, (c, _) in HCWD.items()},
                                      "listening": {4510: 9}, "pr_state": PRS.get, "quiet": lambda p: 99999}


def fake_orca(*args):
    if args[:2] == ("worktree", "list"):
        return {"worktrees": [{"path": str(c1), "hostId": "local"}, {"path": str(c2), "hostId": "local"}]}
    return {"terminals": []}


reap_hard.orca = fake_orca
reap.worktree_facts = lambda p, *a, **k: {"pr": "acme/app#1", "head": "h1"} if p.endswith("c1") else {"pr": "acme/app#2", "head": "h2"}
reap.compose_stacks = lambda: {n: {"ports": set(), "dirs": d, "nodir": 0}
                               for n, d in (("s-gone", {"/nonexistent/s"}), ("s-c1", {str(c1)}), ("s-c2", {str(c2)}))}
reap_hard.pw.tailscale_cli = lambda cfg: "ts"
reap_hard.pw.serve_status = lambda cli: {"Web": {"h:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:4511"}}}}}
reap_hard.port_open = lambda port: False
reap_hard.read_last = lambda: {"dark": [reap_hard.serve_key(("--https", 443, 4511))]}
dry, _ = reap_hard.run_pass(True)
hard_targets = {("card:" if i["kind"] == "card" else i["kind"] + ":") + i["id"] for i in dry["items"] if i["target"]}
assert hard_targets == {f"card:{c1}", "stack:s-gone", "stack:s-c1", "listener:9:4510", "serve:--https=443"}, hard_targets
got = candidates.collect(OK, pressure_kinds=[], state_dir=tmp, reap_scan=lambda kinds: ([], []))
hard_cands = {c["key"] for c in got["candidates"]}
# A stack freed only by a card closed in the same pass waits for the round after that card is gone.
assert hard_cands == hard_targets - {"stack:s-c1"}, (hard_cands, hard_targets)
assert {c["rule"] for c in got["candidates"]} == set(candidates.HARD_RULE.values())

# reap.run's timeout is off unless a caller sets it; set, a hung command is a failure, not a hang.
assert reap.RUN_TIMEOUT is None
reap.RUN_TIMEOUT = 0.2
try:
    assert reap.run(["sleep", "2"], check=False) is None
    try:
        reap.run(["sleep", "2"])
        raise AssertionError("no timeout")
    except RuntimeError as e:
        assert "timed out" in str(e)
finally:
    reap.RUN_TIMEOUT = None

# The janitor scan and the hard scans run side by side: two 0.6 s scans take well under their sum.
slow = lambda kinds: (time.sleep(0.6), (REAP_ITEMS, []))[1]
slow_h = lambda t: (time.sleep(0.6), HARD)[1]
t0 = time.monotonic()
got = collect(scan=slow, hard=slow_h)
assert time.monotonic() - t0 < 1.0 and got["sources"]["reap_scan"] == "ok" and got["candidates"], time.monotonic() - t0
# A round cut short (patrol's SIGALRM, delivered to the calling thread only) merges the part that finished and marks
# the one still running as timed out; nothing of the unfinished part leaks into the result.
import signal


class Cut(BaseException):
    pass


def cut(*_):
    raise Cut()


progress = {}
signal.signal(signal.SIGALRM, cut)
signal.setitimer(signal.ITIMER_REAL, 0.4)
try:
    candidates.collect(OK, now=time.time(), reap_scan=lambda kinds: (REAP_ITEMS, []), hard=lambda t: (time.sleep(3), HARD)[1],
                       pressure_kinds=["orphan", "compose-stack"], state_dir=tmp, live=lambda targets, notes: targets,
                       progress=progress)
    raise AssertionError("not cut")
except Cut:
    pass
finally:
    signal.setitimer(signal.ITIMER_REAL, 0)
assert progress["sources"]["reap_scan"] == "ok" and progress["sources"]["reap_hard_cards"] == "timeout", progress["sources"]
assert progress["sources"]["protect"] == "timeout" and "reap_scan" in progress["timings"], progress

# A cut while a thread waits on real commands: one through reap.run, one past it whose shell leaves a grandchild
# holding its pipe. Run as patrol runs (leading its own process group, so this part reruns this file in a new
# session): each is killed, the thread starts nothing more through reap.run, collect returns once the thread has,
# and nothing of the group outlives the round. The part reads as timed out; the calling thread still runs commands.
import subprocess  # noqa: E402

if sys.argv[1:] == ["cut-child"]:
    events = []

    def hung_hard(t):
        events.append(("first", reap.run(["sleep", "30"], check=False) is None))
        p = subprocess.run(["sh", "-c", "sleep 31 & sleep 32"], capture_output=True)  # a path that skips reap.run
        events.append(("bypass", p.returncode))
        events.append(("second", reap.run(["sleep", "33"], check=False)))
        return HARD

    progress = {}
    signal.setitimer(signal.ITIMER_REAL, 0.3)
    t0 = time.monotonic()
    try:
        candidates.collect(OK, now=time.time(), reap_scan=lambda kinds: (REAP_ITEMS, []), hard=hung_hard,
                           pressure_kinds=["orphan", "compose-stack"], state_dir=tmp,
                           live=lambda targets, notes: targets, progress=progress)
    except Cut:
        pass
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    print(json.dumps({"events": events, "took": time.monotonic() - t0, "left": sorted(candidates.group_members()),
                      "cancelled": len(reap.CANCELLED), "source": progress["sources"]["reap_hard_cards"],
                      "after": reap.run(["true"], check=False) is not None}))
    sys.exit(0)

child = subprocess.run([sys.executable, __file__, "cut-child"], capture_output=True, text=True, timeout=60,
                       start_new_session=True)
got = json.loads(child.stdout.strip().splitlines()[-1])
assert got["events"] == [["first", True], ["bypass", -signal.SIGKILL], ["second", None]], got
assert got["took"] < candidates.CUT_GRACE_S + 3 < candidates.CUT_WAIT_S, got
assert got["left"] == [] and got["cancelled"] == 0 and got["source"] == "timeout" and got["after"], got
assert candidates.group_members() == set(), "run from a shell, the group is not this process's to stop"

print("test_candidates: ok")
