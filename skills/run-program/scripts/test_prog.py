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
