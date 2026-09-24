"""Edge cases of prog.py's ledger arithmetic. Run: python3 test_prog.py"""
import subprocess, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import prog

T = "2026-09-24T00:00:00+00:00"  # one timestamp on purpose: order must come from the ledger, not the clock


def ev(kind, **kw):
    return {"ts": T, "ev": kind, **kw}


# A replayed green for one landing raises the cap once.
events = [ev("landed", pr=1, sha="a")] + [ev("main_green", pr=1, sha="a")] * 5
assert prog.cap_from(events, 6) == 2

# A green for a commit that never landed does not count.
assert prog.cap_from([ev("main_green", sha="zzz")], 6) == 1

# An older commit's late green does not clear a newer red.
events = [ev("landed", pr=1, sha="a"), ev("landed", pr=2, sha="b"), ev("main_red", pr=2, sha="b"), ev("main_green", pr=1, sha="a")]
assert prog.main_state(events) == "red"
# The repair landing and its green clear it.
events += [ev("landed", pr=3, sha="c"), ev("main_green", pr=3, sha="c")]
assert prog.main_state(events) == "green"
# Newest landing without a result yet is pending.
assert prog.main_state(events + [ev("landed", pr=4, sha="d")]) == "pending"
# Red stays red while the repair that landed after it is still pending.
events = [ev("landed", pr=1, sha="a"), ev("main_red", pr=1, sha="a"), ev("landed", pr=2, sha="b")]
assert prog.main_state(events) == "red"
assert prog.main_state(events + [ev("main_green", pr=2, sha="b")]) == "green"
# A legacy red without a sha still stops landing.
assert prog.main_state([ev("landed", pr=1, sha="a"), ev("main_red", pr=1)]) == "red"

# A predicate edit or a later landing voids the final check.
events = [ev("landed", pr=1, sha="a"), ev("predicate_verified")]
assert prog.final_check_current(events)
assert not prog.final_check_current(events + [ev("config", note="predicate=A-1,A-2")])
assert not prog.final_check_current(events + [ev("landed", pr=2, sha="b")])
assert prog.final_check_current(events + [ev("config", note="ceiling=4")])

# stop then resume in the same second resumes.
assert prog.stopped([ev("stop"), ev("resume")]) is False
assert prog.stopped([ev("resume"), ev("stop")]) is True

# A failed landing stays in the ready queue.
events = [ev("ready", pr=7), ev("verdict", pr=7, sha="x"), ev("land_failed", pr=7)]
assert [s["pr"] for s in prog.ready_prs(events)] == [7]
assert prog.ready_prs(events + [ev("landed", pr=7, sha="m")]) == []

# Indentation-only edits change the patch id.
def pid(diff):
    return subprocess.run(["git", "patch-id", "--verbatim"], input=diff, capture_output=True, text=True).stdout.split()[0]
head = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n if ok:\n"
assert pid(head + "-    run()\n+    stop()\n") != pid(head + "-    run()\n+stop()\n")

print("prog.py ledger edge cases: all pass")

# --- land order ---------------------------------------------------------------------------
import datetime as dt
NOW = dt.datetime(2026, 9, 24, 6, 0, tzinfo=dt.timezone.utc)
CFG = {"merge_policy": "autonomous"}


def at(hours_ago, kind, **kw):
    return {"ts": (NOW - dt.timedelta(hours=hours_ago)).isoformat(), "ev": kind, **kw}


def row(n, mss="CLEAN", head="h", checks=("SUCCESS",), base="main", draft=False):
    return {"number": n, "headRefOid": f"{head}{n}", "mergeStateStatus": mss, "isDraft": draft, "baseRefName": base,
            "statusCheckRollup": [{"name": f"c{i}", "status": "COMPLETED", "conclusion": c} for i, c in enumerate(checks)]}


def ready(n, t, hours_ago, **kw):
    return [at(hours_ago, "ready", pr=n, ticket=t, **kw), at(hours_ago, "verdict", pr=n, sha=f"h{n}", result="pass", patch_id="p")]


# The dependency root goes first even when a newer, easy PR is ready: the migration-chain starvation.
events = ready(10, "A-288", 1) + ready(11, "A-278", 1.5) + ready(12, "A-252", 1.2) + ready(13, "A-297", 0.2) \
    + [at(3, "dep", ticket="A-278", after=["A-288"]), at(3, "dep", ticket="A-252", after=["A-278"])]
order = prog.land_order(events, [row(10, "BEHIND"), row(11), row(12), row(13)], CFG, NOW)
assert [e["pr"] for e in order][0] == 10 and order[0]["unblocks"] == 2, order
assert {e["pr"]: e["state"] for e in order} == {10: "catching_up", 11: "waiting", 12: "waiting", 13: "ready"}
# Orca task deps feed the same order as ledger deps.
order = prog.land_order(ready(60, "F-1", 1) + ready(61, "F-2", 1), [row(60), row(61)], CFG, NOW, deps={"F-2": {"F-1"}})
assert {e["pr"]: e["state"] for e in order} == {60: "ready", 61: "waiting"}
tasks = [{"id": "t1", "display_name": "F-1 root", "status": "completed", "deps": "[]"},
         {"id": "t2", "task_title": "F-2", "status": "failed", "deps": "[]"},
         {"id": "t3", "task_title": "F-2", "status": "pending", "deps": '["t1"]'}]
assert prog.ticket_of(tasks[0]) == "F-1" and prog.ticket_of({"spec": "/goal x"}) is None
assert prog.ticket_of({"display_name": "W-372", "spec": "94S-372 https://linear.app/x — fix"}) == "94S-372"
assert prog.ticket_of({"display_name": "W-362", "spec": "CI 위생 묶음: 94S-362 https://…"}) == "94S-362"

# The exclusive lane: only exclusive entries contend, in land order, per base; normal ones never wait on it.
events = ready(70, "G-1", 1) + ready(71, "G-2", 0.5) + ready(72, "G-3", 2) \
    + [at(1, "lane", pr=70, exclusive=True), at(0.5, "lane", pr=71, exclusive=True)]
order = prog.land_order(events, [row(70, "BEHIND"), row(71), row(72)], CFG, NOW)
assert [e["exclusive"] for e in order if e["pr"] in (70, 71)] == [True, True]
assert prog.exclusive_turn(order, 71)["pr"] == 70 and prog.exclusive_turn(order, 70) is None
assert prog.exclusive_turn(order, 72) is None
assert prog.is_exclusive({}, ["db/migrations/0003.sql", "src/a.py"]) == ["db/migrations/0003.sql"]
assert prog.is_exclusive({"landing": {"exclusive_paths": ["contracts/*"]}}, ["src/a.py", "contracts/x.md"]) == ["contracts/x.md"]

# Aging: a normal PR that waited past aging_hours ranks with urgent ones, ahead of a fresh urgent.
events = ready(20, "B-1", 3) + ready(21, "B-2", 0.1, klass="urgent")
order = prog.land_order(events, [row(20), row(21)], CFG, NOW)
assert [e["pr"] for e in order] == [20, 21]
# A gate outranks both; main-fix outranks everything.
events += ready(22, "B-3", 0.1, klass="gate") + ready(23, "B-4", 0.05, klass="main-fix")
assert [e["pr"] for e in prog.land_order(events, [row(n) for n in (20, 21, 22, 23)], CFG, NOW)] == [23, 22, 20, 21]

# A blocked PR is passed over, with the reason; other bases never contend.
events = ready(30, "C-1", 2) + ready(31, "C-2", 1)
order = prog.land_order(events, [row(30, "DIRTY"), row(31, base="feat/x")], CFG, NOW)
assert order[0]["state"] == "blocked" and "conflicts with base" in order[0]["reasons"]
failed = prog.land_order(events, [row(30, checks=("FAILURE",)), row(31)], CFG, NOW)
assert failed[0]["state"] == "blocked" and failed[0]["reasons"][0].startswith("CI failed")

# A stack lands from its top: the bottom waits for it, the top waits on the bottom's readiness.
events = ready(40, "D-1", 1) + ready(41, "D-2", 1)
rows = [row(40), row(41, base="branch-40")]
order = {e["pr"]: e for e in prog.land_order(events, rows, CFG, NOW, {9: [40, 41]})}
assert order[40]["state"] == "waiting" and order[40]["reasons"][0] == "lands with its stack from #41"
assert order[41]["state"] == "ready" and order[41]["base"] == "main" and order[41]["stack"] == [40, 41]
order = {e["pr"]: e for e in prog.land_order(events, [row(40, "BEHIND"), rows[1]], CFG, NOW, {9: [40, 41]})}
assert order[41]["state"] == "catching_up" and order[41]["reasons"][0].startswith("stack layer #40")
# A blocked top releases the bottom to land alone.
order = {e["pr"]: e for e in prog.land_order(events, [row(40), row(41, "DIRTY", base="branch-40")], CFG, NOW, {9: [40, 41]})}
assert order[40]["state"] == "ready"
# Stacks whose other layers already merged are plain PRs.
assert prog.land_order(events, [row(41)], CFG, NOW, {9: [40, 41]})[0]["stack"] is None

# A dependency chain stacked in order does not deadlock: the lower layer satisfies the dep.
events = ready(50, "E-288", 1) + ready(51, "E-278", 1) + [at(2, "dep", ticket="E-278", after=["E-288"])]
order = {e["pr"]: e for e in prog.land_order(events, [row(50), row(51, base="b50")], CFG, NOW, {3: [50, 51]})}
assert order[51]["state"] == "ready" and order[50]["state"] == "waiting", order

# The next move, shared by `status` and the dashboard. A verified predicate closes even when the tracker
# cannot answer (the dashboard once said "may spawn 4 more" after Close because the tracker call failed).
NCFG = {"merge_policy": "autonomous", "ceiling": 4, "created_at": at(5, "x")["ts"]}
done = [at(1, "landed", pr=1, sha="m1"), at(0.5, "predicate_verified")]
nm = lambda ev, td, live=0, order=(): prog.next_move(NCFG, ev, NOW, tickets_done=td, live=live, human=0, order=list(order))[0]
assert nm(done, None) == nm(done, True) == "predicate met and verified: Close"
assert nm(done, False).startswith("may spawn")  # the tracker says a ticket reopened
assert nm(done + [at(0.1, "landed", pr=2, sha="m2")], None).startswith("may spawn"), nm(done + [at(0.1, "landed", pr=2, sha="m2")], None)  # a later landing voids the check
assert nm([], True).startswith("tickets done")
assert nm([], None, live=4) == "at cap: drain and land"
assert nm([], None, order=[{"age_h": 3.5}]).startswith("unstick first: 1 PR")
# Released cards still open go before spawning, never before a safety stop, Close or a stale PR.
left = lambda ev, td=None, live=0, order=(): prog.next_move(NCFG, ev, NOW, tickets_done=td, live=live, human=0,
                                                              order=list(order), leftover=8)[0]
assert left([]).startswith("close out 8 released card(s)") and left([], live=4).startswith("close out 8")
assert left(done, True) == "predicate met and verified: Close"
assert left([], order=[{"age_h": 3.5}]).startswith("unstick first")
assert left([at(1, "landed", pr=1, sha="m1"), at(0.5, "main_red", sha="m1")]).startswith("SAFETY STOP")

# A land-after chain counts once against the cap; a start-after or unrelated worker counts on its own.
ev = [at(1, "spawned", ticket="S-1", note="ctx_a1"), at(1, "spawned", ticket="S-2", note="ctx_b2"),
      at(1, "spawned", ticket="S-3", note="ctx_c3"), at(1, "dep", ticket="S-2", after=["S-1"])]
assert prog.live_units(ev, ["ctx_a1", "ctx_b2", "ctx_c3"]) == 2
assert prog.live_units(ev, ["ctx_b2", "ctx_c3", "ctx_ff"]) == 3  # the lower layer landed; an unrecorded worker counts

print("prog.py land order: all pass")

# landed_but_open: a released worker's card still counts until the worktree is removed
ev = [{"ev": "landed", "pr": 1, "ticket": "A-1", "ts": "2026-09-24T00:00:00+00:00"},
      {"ev": "landed", "pr": 2, "ticket": "A-2", "ts": "2026-09-24T00:00:00+00:00"}]
tasks = [{"id": "t1", "display_name": "A-1 x"}, {"id": "t2", "display_name": "A-2 y"}, {"id": "t3", "display_name": "A-3 z"},
         {"id": "tq", "display_name": "QA lead"}]
wts = [{"id": "main", "path": "/r", "isMainWorktree": True}, {"id": "w1", "path": "/w/1"}, {"id": "w2", "path": "/w/2"},
       {"id": "w3", "path": "/w/3"}, {"id": "wq", "path": "/w/q"}]
workers = [
    {"dispatchId": "d1", "taskId": "t1", "terminalState": "released", "resource": {"worktreeId": "w1"}},
    {"dispatchId": "d2", "taskId": "t2", "terminalState": "active", "resource": {"worktreeId": "w2"}},
    {"dispatchId": "d3a", "taskId": "t1", "terminalState": "released", "resource": {"worktreeId": "w3"}},
    {"dispatchId": "d3b", "taskId": "t3", "terminalState": "active", "resource": {"worktreeId": "w3"}},  # card reused for A-3
    {"dispatchId": "dq", "taskId": "tq", "terminalState": "active", "resource": {"worktreeId": "wq"}},
    {"dispatchId": "d9", "taskId": "t2", "terminalState": "released", "resource": {"worktreeId": "gone"}},  # already removed
]
assert prog.landed_but_open(ev, workers, tasks, wts) == [("A-1", "d1", False, "/w/1"), ("A-2", "d2", True, "/w/2")], \
    prog.landed_but_open(ev, workers, tasks, wts)
# orch wait says what to do with each worker_done's card; it offers removal only for a finished card nobody uses.
def co(payload, workers=workers):
    return prog.close_out([{"type": "worker_done", "payload": payload}], workers, wts + [{"id": "wx", "path": "/w/a b"}])
assert co({"dispatchId": "d1", "outcome": "succeeded"})[-1] == "  orca worktree rm --worktree path:/w/1"
assert co('{"dispatchId": "d1", "outcome": "succeeded"}') == co({"dispatchId": "d1", "outcome": "succeeded"})
assert not any("worktree rm" in l for l in co({"dispatchId": "d3a", "outcome": "succeeded"})), "the card was reused for A-3"
failed = co({"dispatchId": "d1", "outcome": "failed"})
assert "--retry-of d1 --task t1 " in failed[0] and not any(l.strip().startswith("orca worktree rm") for l in failed), failed
assert "not in `orca worktree list`" in co({"dispatchId": "d9", "outcome": "succeeded"})[0]
spaced = [{"dispatchId": "dx", "terminalState": "active", "resource": {"worktreeId": "wx"}}]
assert co({"dispatchId": "dx", "outcome": "succeeded"}, spaced)[-1] == "  orca worktree rm --worktree path:'/w/a b'"
assert prog.close_out([{"type": "question", "payload": {"dispatchId": "d2"}}, {"type": "worker_done", "payload": None},
                       {"type": "worker_done", "payload": "[1]"}], workers, wts) == []
# worker-list pages at 100, newest first: the oldest cards are on the last page.
pages = {None: {"workers": [{"dispatchId": "new"}], "page": {"hasMore": True, "nextCursor": "c1"}},
         "c1": {"workers": [{"dispatchId": "old"}], "page": {"hasMore": False, "nextCursor": None}}}
real, prog.orca_json = prog.orca_json, lambda *a: pages[a[a.index("--cursor") + 1] if "--cursor" in a else None]
assert [w["dispatchId"] for w in prog.run_workers("run_x")] == ["new", "old"]
prog.orca_json = real
print("prog.py landed_but_open: all pass")

# --- backfill ----------------------------------------------------------------------------
import io, json, tempfile, contextlib
prog.HOME = pathlib.Path(tempfile.mkdtemp(prefix="test-backfill-"))
d = prog.HOME / "bf"
d.mkdir()
(d / "program.json").write_text(json.dumps({"slug": "bf", "repo": "o/r", "run": "run_x", "predicate": ["T-9"],
                                            "ceiling": 6, "merge_policy": "autonomous", "created_at": "2026-09-21T00:00:00+00:00"}))
OWN = [{"ts": "2026-09-21T00:00:00+00:00", "ev": "spawned", "role": "qa", "note": "ctx_q"},
       {"ts": "2026-09-21T01:00:00+00:00", "ev": "landed", "pr": 5, "sha": "m5", "ticket": "T-5"}]
(d / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in OWN))


def merged(n, at, title="x", branch="b"):
    return {"number": n, "title": title, "headRefName": branch, "mergedAt": at, "mergeCommit": {"oid": f"m{n}"}}


def run_(n, concl, status="completed", at="2026-09-20T05:00:00Z"):
    return {"headSha": f"m{n}", "status": status, "conclusion": concl, "updatedAt": at}


MERGED = [merged(1, "2026-09-20T01:00:00Z", "feat: T-1 first half"),
          merged(2, "2026-09-20T02:00:00Z", branch="owner/t-2-lower-layer"),  # a stack's lower layer: base was a branch
          merged(3, "2026-09-20T03:00:00Z", "chore: no ticket, mentions PR-12"),
          merged(6, "2026-09-20T04:00:00Z", "feat: T-1 second half"),
          merged(5, "2026-09-20T23:00:00Z", "T-5 already recorded"),
          merged(7, "2026-09-22T00:00:00Z", "T-7 merged after registration")]
RUNS = [run_(1, "success"), run_(1, "failure", at="2026-09-20T01:20:00Z"),  # one red workflow makes the commit red
        run_(2, "success", at="2026-09-20T02:10:00Z"), run_(2, "cancelled"),  # a superseded run does not count
        run_(3, "cancelled"),                                                  # nothing but a cancelled run: no result
        run_(6, "success"), run_(6, None, status="in_progress")]              # still running: no result yet
calls = []


def fake_gh(*args):
    calls.append(args)
    if args[:2] == ("repo", "view"):
        return {"defaultBranchRef": {"name": "trunk"}}
    return MERGED if args[:2] == ("pr", "list") else RUNS


prog.gh_json = fake_gh


def backfill(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        prog.cmd_backfill(["bf", *(argv or ("--since", "2026-09-01"))])
    return out.getvalue()


def rows():
    return [json.loads(l) for l in (d / "ledger.jsonl").read_text().splitlines()]


try:
    backfill("--dry-run")
    raise AssertionError("expected an exit")
except SystemExit as e:
    assert "needs --since" in str(e), "a repo's older merges are not the program's landings"
out = backfill("--since", "2026-09-01", "--dry-run")
assert "#3 2026-09-20T03:00:00Z chore: no ticket" in out and "#5 " not in out, "a dry run lists what it would add"
assert "backfill 4 landings (3 with a ticket" in out and "1 main green, 1 main red" in out, out
assert rows() == OWN and not list(d.glob("ledger.jsonl.bak-*")), "a dry run writes nothing"
backfill()
got = rows()
# In time order ahead of the program's own rows; a commit's result lands when its last workflow ends.
assert [(r["ev"], r.get("pr") or r.get("sha")) for r in got] == [
    ("landed", 1), ("landed", 2), ("main_green", "m2"), ("landed", 3), ("landed", 6), ("main_red", "m1"),
    ("spawned", None), ("landed", 5), ("config", None)], got
assert [r.get("ticket") for r in got if r["ev"] == "landed"] == ["T-1", "T-2", None, "T-1", "T-5"], got
assert all(r["note"] == "backfill" for r in got[:6]) and got[6:8] == OWN, got
assert json.loads((d / "program.json").read_text())["created_at"] == "2026-09-20T01:00:00+00:00"
assert got[-1]["note"] == "created_at=2026-09-20T01:00:00+00:00 (backfill)"
assert next(c for c in calls if c[:2] == ("run", "list"))[5] == "trunk", "CI of the repo's default branch"
assert [p.read_text() for p in d.glob("ledger.jsonl.bak-*")] == ["".join(json.dumps(r) + "\n" for r in OWN)]
# History counts: m2's green raised the cap and m1's later red halved it; #5 of the program's own is still pending.
assert prog.main_state(got) == "pending" and prog.cap_from(got, 6) == 1
# A rerun adds nothing; an earlier --since adds only what is older than the program's (moved) start.
assert backfill().startswith("nothing to backfill") and rows() == got
# A result that was still running at the first backfill is added by a rerun.
RUNS[-1] = run_(6, "success", at="2026-09-21T02:00:00Z")
assert "0 landings (0 with a ticket), 1 main green" in backfill()
assert [(r["ev"], r.get("sha")) for r in rows()][-3:] == [("landed", "m5"), ("main_green", "m6"), ("config", None)]
assert "nothing to backfill" in backfill()
MERGED.append(merged(8, "2026-09-19T12:00:00+09:00", "T-8 older"))
assert backfill("--since", "2026-09-20").startswith("nothing to backfill"), "--since bounds it (T-8 is 03:00Z on the 19th)"
assert "backfill 1 landings" in backfill("--since", "2026-09-19")
assert [r["pr"] for r in rows() if r["ev"] == "landed"] == [8, 1, 2, 3, 6, 5]
# A lander appending while backfill runs is not overwritten.
MERGED.append(merged(9, "2026-09-18T00:00:00Z", "T-9"))
before = (d / "ledger.jsonl").read_text()
prog.gh_json = lambda *a: (open(d / "ledger.jsonl", "a").write('{"ts": "2026-09-24T00:00:00+00:00", "ev": "ready", "pr": 10}\n'),
                           fake_gh(*a))[1] if a[:2] == ("run", "list") else fake_gh(*a)
try:
    backfill("--since", "2026-09-01")
    raise AssertionError("expected an exit")
except SystemExit as e:
    assert "changed while backfilling" in str(e)
assert (d / "ledger.jsonl").read_text() == before + '{"ts": "2026-09-24T00:00:00+00:00", "ev": "ready", "pr": 10}\n'
assert not (d / "ledger.jsonl.tmp").exists()

# An append waits while backfill holds the ledger, so it lands in the new file, not the replaced one.
import threading, time as _time
held = threading.Event()
def hold():
    with prog.Program("bf").locked():
        held.set()
        _time.sleep(0.5)
threading.Thread(target=hold).start()
held.wait()
t = _time.monotonic()
prog.Program("bf").append("resume")
assert _time.monotonic() - t >= 0.4 and rows()[-1]["ev"] == "resume"

# A setting changed while backfill fetched is kept when created_at moves.
(d / "program.json").write_text(json.dumps(dict(json.loads((d / "program.json").read_text()), created_at="2026-09-21T00:00:00+00:00")))
stale = prog.Program("bf")
(d / "program.json").write_text(json.dumps(dict(json.loads((d / "program.json").read_text()), merge_policy="human-gate")))
stale.update(lambda cfg: cfg.__setitem__("created_at", "2026-09-20T01:00:00+00:00"))
assert json.loads((d / "program.json").read_text())["merge_policy"] == "human-gate"

# A run stopped after the ledger but before program.json is repaired by the next run.
MERGED.pop()
prog.gh_json = fake_gh
cfg = json.loads((d / "program.json").read_text())
(d / "program.json").write_text(json.dumps(dict(cfg, created_at="2026-09-21T00:00:00+00:00")))
assert "created_at moved to 2026-09-19T03:00:00+00:00" in backfill()

# A result that came in after the program's own rows takes its place in time: cap_from reads in order.
d2 = prog.HOME / "bf2"
d2.mkdir()
(d2 / "program.json").write_text(json.dumps(dict(cfg, slug="bf2", created_at="2026-09-21T00:00:00+00:00")))
own2 = [{"ts": "2026-09-21T01:00:00+00:00", "ev": "landed", "pr": 5, "sha": "m5"},
        {"ts": "2026-09-21T02:00:00+00:00", "ev": "main_green", "sha": "m5"}]
(d2 / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in own2))
MERGED[:], RUNS[:] = [merged(1, "2026-09-20T23:00:00Z", "T-1")], [run_(1, "failure", at="2026-09-21T03:00:00Z")]
prog.gh_json = fake_gh
with contextlib.redirect_stdout(io.StringIO()):
    prog.cmd_backfill(["bf2", "--since", "2026-09-01"])
got2 = [json.loads(l) for l in (d2 / "ledger.jsonl").read_text().splitlines()]
assert [(r["ev"], r.get("sha")) for r in got2] == [("landed", "m1"), ("landed", "m5"), ("main_green", "m5"),
                                                    ("main_red", "m1"), ("config", None)], got2
assert prog.cap_from(got2, 6) == 1

# A ticket that takes several PRs is not LANDED-BUT-OPEN while the tracker says it is open.
tasks = [{"id": "task_1", "display_name": "T-1 both halves"}]
workers = [{"dispatchId": "ctx_1", "taskId": "task_1", "terminalState": "active", "resource": {"worktreeId": "w1"}}]
wts = [{"id": "w1", "path": "/w/1"}]
open_ = lambda issues: prog.landed_but_open(rows(), workers, tasks, wts, issues)
assert open_({"T-1": {"state_type": "started"}}) == []
assert open_({"T-1": {"state_type": "completed"}}) == open_({}) == open_(None) == [("T-1", "ctx_1", True, "/w/1")], \
    "a closed ticket, or no tracker: the landing is the end"
released = [dict(workers[0], terminalState="released")]
assert prog.landed_but_open(rows(), released, tasks, wts, {"T-1": {"state_type": "started"}}) == [("T-1", "ctx_1", False, "/w/1")], \
    "a released worker's card is left behind whatever the ticket's state"

print("prog.py backfill: all pass")

# --- human-gate Land task -----------------------------------------------------------------
d3 = prog.HOME / "gt"
d3.mkdir()
(d3 / "program.json").write_text(json.dumps(dict(cfg, slug="gt", merge_policy="human-gate")))
(d3 / "ledger.jsonl").write_text(json.dumps({"ts": T, "ev": "gate_opened", "pr": 12, "task": "task_g", "gate": "g1"}) + "\n")
updates, real_run, real_view = [], prog.run, prog.pr_view
prog.run = lambda cmd, check=True: (updates.append(cmd[cmd.index("--id") + 1:cmd.index("--status") + 2]),
                                   subprocess.CompletedProcess(cmd, 0, "{}", ""))[1]
views = {12: {"state": "MERGED", "mergeCommit": {"oid": "m12"}}, 13: {"state": "CLOSED"}}
prog.pr_view = lambda repo, n: views[n]
with contextlib.redirect_stdout(io.StringIO()):
    prog.cmd_landed(["gt", "--pr", "12"])
assert updates == [["task_g", "--status", "completed"]], "no worker settles the gate's Land task; the landing does"
try:
    prog.cmd_landed(["gt", "--pr", "13"])
except SystemExit:
    pass
assert len(updates) == 1, "a PR without a gate has no Land task to close"
prog.Program("gt").append("gate_opened", pr=14, task="task_h", gate="g2")
real_res, prog.gate_resolution = prog.gate_resolution, lambda cfg, events, pr: "hold"
assert prog.attempt(prog.Program("gt"), 14, "normal") == (1, "human-gate: the user resolved the gate as 'hold'")
assert updates[-1] == ["task_h", "--status", "failed"], "a held gate closes its Land task; a new gate makes a new one"
prog.run, prog.pr_view, prog.gate_resolution = real_run, real_view, real_res

print("prog.py gate task: all pass")

# --- signal ---------------------------------------------------------------------------------
with contextlib.redirect_stdout(io.StringIO()):
    prog.cmd_record(["gt", "signal", "--kind", "brief_gap", "--evidence", "msg_1", "--ticket", "T-9"])
assert prog.Program("gt").events()[-1] == {**prog.Program("gt").events()[-1], "ev": "signal", "kind": "brief_gap", "evidence": "msg_1"}
for bad in (["--kind", "vibes", "--evidence", "x"], ["--kind", "stall"]):
    try:
        prog.cmd_record(["gt", "signal", *bad])
        raise AssertionError(f"signal accepted {bad}")
    except SystemExit as e:
        assert "--kind" in str(e), e

print("prog.py signal: all pass")
