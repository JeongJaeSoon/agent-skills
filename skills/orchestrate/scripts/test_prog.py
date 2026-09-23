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


# The dependency root goes first even when a newer, easy PR is ready: the 94S-288 starvation.
events = ready(10, "A-288", 1) + ready(11, "A-278", 1.5) + ready(12, "A-252", 1.2) + ready(13, "A-297", 0.2) \
    + [at(3, "dep", ticket="A-278", after=["A-288"]), at(3, "dep", ticket="A-252", after=["A-278"])]
order = prog.land_order(events, [row(10, "BEHIND"), row(11), row(12), row(13)], CFG, NOW)
assert [e["pr"] for e in order][0] == 10 and order[0]["unblocks"] == 2, order
assert {e["pr"]: e["state"] for e in order} == {10: "catching_up", 11: "waiting", 12: "waiting", 13: "ready"}
# Without a reservation the behind root does not hold the line: the ready PR may land.
assert prog.turn(order, 13) is None
# Once the root's lander has started catching up, the line is held for it.
events += [at(0.1, "land_check", pr=10, outcome="catching_up")]
order = prog.land_order(events, [row(10, "BEHIND"), row(11), row(12), row(13)], CFG, NOW)
assert order[0]["reserved_until"] and prog.turn(order, 13)["pr"] == 10
# ... but a reservation expires, so a dead lander cannot hold it forever.
events[-1] = at(1, "land_check", pr=10, outcome="catching_up")
order = prog.land_order(events, [row(10, "BEHIND"), row(11), row(12), row(13)], CFG, NOW)
assert prog.turn(order, 13) is None

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
assert prog.turn(order, 31) is None
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

print("prog.py land order: all pass")
