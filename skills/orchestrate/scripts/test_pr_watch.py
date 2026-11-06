"""pr_watch.py: only the five changes that need a turn print; every other state change prints nothing.
Run: python3 test_pr_watch.py"""
import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import pr_watch


def pr(state="OPEN", dirty=False, fail=False, ready=False):
    return {"state": state, "dirty": dirty, "fail": fail, "ready": ready}


BASE = {"a/b#1": pr(), "a/b#2": pr()}


def lines(old, new):
    return list(pr_watch.events(old, new))


# The five prints.
assert lines(BASE, {**BASE, "a/b#1": pr("MERGED")}) == ["a/b#1 MERGED"]
assert lines(BASE, {**BASE, "a/b#1": pr("CLOSED")}) == ["a/b#1 CLOSED"]
assert lines(BASE, {**BASE, "a/b#1": pr(fail=True)}) == ["a/b#1 CI failed"]
assert lines(BASE, {**BASE, "a/b#1": pr(ready=True)}) == ["a/b#1 land-check READY"]
assert lines(BASE, {**BASE, "a/b#1": pr(dirty=True)}) == ["a/b#1 conflict (DIRTY)"]
assert lines({**BASE, "a/b#1": pr(dirty=True)}, BASE) == ["a/b#1 conflict resolved"]
assert lines(BASE, {**BASE, "a/b#3": pr()}) == ["a/b#3 opened"]

# Middle states print nothing: BLOCKED, UNKNOWN, CLEAN and UNSTABLE are all "not dirty", and a check that is
# pending, passing again or still failing is not a new failure.
assert lines(BASE, BASE) == []
assert lines({"a/b#1": pr(fail=True)}, {"a/b#1": pr(fail=True)}) == []
assert lines({"a/b#1": pr(fail=True)}, {"a/b#1": pr()}) == [], "CI turning green is not a turn"
assert lines({"a/b#1": pr(ready=True)}, {"a/b#1": pr()}) == [], "READY dropping back is not a turn"
assert lines({"a/b#1": pr(ready=True)}, {"a/b#1": pr(ready=True)}) == []
assert lines(BASE, {"a/b#1": pr()}) == [], "a PR that left the listing prints nothing"
assert lines(BASE, {**BASE, "a/b#3": pr("MERGED")}) == [], "a merged PR first seen now is not new work"
assert lines({"a/b#1": pr("MERGED")}, {"a/b#1": pr("MERGED", dirty=True, fail=True)}) == [], "a finished PR stays quiet"
assert lines({"a/b#1": pr("MERGED")}, {"a/b#1": pr("MERGED")}) == []
# Several changes at once keep a stable order.
assert lines(BASE, {"a/b#1": pr(fail=True), "a/b#2": pr("MERGED")}) == ["a/b#1 CI failed", "a/b#2 MERGED"]

# An hour of churn (12 rounds at 300 s): the old watcher printed one line per status change, this one only the
# single CI failure.
rounds = [pr(), pr(dirty=False), pr(fail=False), pr(), pr(), pr(fail=True), pr(fail=True), pr(), pr(), pr(), pr(), pr()]
printed = sum(len(lines({"a/b#1": a}, {"a/b#1": b})) for a, b in zip(rounds, rounds[1:]))
assert printed == 1, "only the one CI failure in the churn above prints"

# Round loop: the first round only records and prints nothing; a gh failure (None) keeps the old state.
snaps = iter([{"a/b#1": pr("MERGED")}, None, {"a/b#1": pr("MERGED")}])
printed, slept = [], []
pr_watch.watch(lambda: next(snaps), 300, printed.append, slept.append, rounds=3)
assert printed == [], "first round silent, and a failed round does not turn the next one into a first round"
assert slept == [300, 300], "sleeps between rounds, never after the last"
snaps = iter([{"a/b#1": pr()}, None, {"a/b#1": pr("MERGED")}, {"a/b#1": pr("MERGED")}])
pr_watch.watch(lambda: next(snaps), 300, printed.append, slept.append, rounds=4)
assert printed == ["a/b#1 MERGED"], "the change after a failed round still prints, once"

# snapshot() reads gh's JSON; branches outside --branch and PRs it cannot read are left out.
GH = json.dumps([
    {"number": 1, "state": "OPEN", "headRefName": "me/fix-x", "mergeStateStatus": "DIRTY",
     "statusCheckRollup": [{"conclusion": "FAILURE"}]},
    {"number": 2, "state": "OPEN", "headRefName": "me/other", "mergeStateStatus": "CLEAN", "statusCheckRollup": None},
    {"number": 3, "state": "OPEN", "headRefName": "me/fix-y", "mergeStateStatus": "CLEAN",
     "statusCheckRollup": [{"state": "SUCCESS"}, {"conclusion": "SUCCESS"}]},
])
calls = []


def run(args):
    calls.append(args)
    return "READY: gate ok\n" if args[0] == "orch" else GH


snap = pr_watch.snapshot(["a/b"], "fix-", None, 50, run)
assert snap == {"a/b#1": pr(dirty=True, fail=True), "a/b#3": pr()}, snap
assert all(c[0] == "gh" for c in calls), "no land-check without --land-check"
calls.clear()
snap = pr_watch.snapshot(["a/b"], "fix-", "prog", 50, run)
assert snap["a/b#3"]["ready"] and not snap["a/b#1"]["ready"], "land-check runs only for a CLEAN open PR"
assert [c for c in calls if c[0] == "orch"] == [["orch", "land-check", "prog", "--pr", "3"]], calls
assert pr_watch.snapshot(["a/b"], "", None, 50, lambda a: "not json") is None, "unreadable gh output skips the round"
assert pr_watch.snapshot(["a/b"], "", None, 50, lambda a: "") is None, "a gh failure skips the round"

# The interval floor: under 300 s is refused, not clamped silently.
assert pr_watch.parse(["--repo", "a/b"]).interval == 300
try:
    pr_watch.parse(["--repo", "a/b", "--interval", "60"])
except SystemExit as e:
    assert e.code == 2
else:
    raise AssertionError("an interval under 300 s must be refused")
print("test_pr_watch: ok")
