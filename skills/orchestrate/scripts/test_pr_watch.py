"""pr_watch.py: only the five changes that need a turn print; every other state change prints nothing.
Run: python3 test_pr_watch.py"""
import json, os, pathlib, sys, tempfile
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


def gh_pr(n, merge="CLEAN", rollup=None, state="OPEN"):
    return {"number": n, "state": state, "headRefName": "me/fix", "mergeStateStatus": merge, "statusCheckRollup": rollup}


def replay(statuses, land=None):
    """Run the round loop over a list of per-round gh listings; return what it printed."""
    out, listings = [], iter(statuses)
    pr_watch.watch(lambda: pr_watch.snapshot(["a/b"], "", land, 50, lambda a: next(listings) if a[0] == "gh" else
                                             land[0] if land else ""),
                   300, out.append, lambda s: None, rounds=len(statuses))
    return out


# The mergeStateStatus churn the ticket saw, and a DIRTY that flickers to UNKNOWN while GitHub recomputes.
churn = [json.dumps([gh_pr(1, m)]) for m in ("BLOCKED", "UNKNOWN", "CLEAN", "UNSTABLE", "BLOCKED", "UNKNOWN", "CLEAN")]
assert replay(churn) == [], "an hour of status churn prints nothing"
flap = [json.dumps([gh_pr(1, m)]) for m in ("DIRTY", "UNKNOWN", "DIRTY", "UNKNOWN", "CLEAN")]
assert replay(flap) == ["a/b#1 conflict resolved"], "UNKNOWN keeps the last known conflict state"
# A rerun that passes supersedes its failed run; a later failure of the rerun is a new failure.
def chk(conclusion, started, **kw):
    return {"workflowName": "ci", "name": "tests", "status": "COMPLETED", "conclusion": conclusion,
            "startedAt": f"2026-11-06T{started}:00Z", "completedAt": f"2026-11-06T{started}:30Z", **kw}


# Which run is the latest is prog.ci_summary's call (the one `orch land-check` uses): a rerun that passes supersedes
# its failed run, and a later failure of the rerun is a new failure.
old_fail, rerun_ok, rerun_bad = chk("FAILURE", "01:00"), chk("SUCCESS", "02:00"), chk("FAILURE", "03:00")
runs = [json.dumps([gh_pr(1, rollup=r)]) for r in ([old_fail], [old_fail, rerun_ok], [old_fail, rerun_ok, rerun_bad])]
assert replay(runs) == ["a/b#1 CI failed"], "the first listing already failed, so only the last one is a change"
assert pr_watch.failing([old_fail, rerun_ok]) is False and pr_watch.failing([old_fail, rerun_ok, rerun_bad]) is True
lint = [{"context": "lint", "state": "ERROR", "startedAt": "2026-11-06T01:00:00Z"},
        {"context": "lint", "state": "SUCCESS", "startedAt": "2026-11-06T02:00:00Z"}]
assert pr_watch.failing(lint) is False
# Conclusions beyond FAILURE that still need a person.
for odd in ("ACTION_REQUIRED", "STARTUP_FAILURE", "CANCELLED", "TIMED_OUT"):
    assert pr_watch.failing([chk(odd, "01:00")]) is True, odd
# A rerun still running (gh prints completedAt 0001-01-01 for it) is pending, not a failure; when it ends badly
# the same entry turns FAILURE, and that is the new failure.
running = chk("", "03:00", status="IN_PROGRESS", completedAt="0001-01-01T00:00:00Z")
ended_bad = chk("FAILURE", "03:00")
runs = [json.dumps([gh_pr(1, rollup=r)]) for r in ([rerun_ok], [rerun_ok, running], [rerun_ok, ended_bad])]
assert replay(runs) == ["a/b#1 CI failed"], "success, rerun running, rerun failed: one failure line"
runs = [json.dumps([gh_pr(1, rollup=r)]) for r in ([old_fail], [old_fail, running], [old_fail, ended_bad])]
assert replay(runs) == ["a/b#1 CI failed"], "failed, rerun running, failed again: the rerun's failure is new"
# land-check says "state unknown" when gh gave it nothing: no verdict, so READY, unknown, READY is one READY.
verdicts = iter(["HOLD: x\n", "READY: ok\n", "HOLD: PR state unknown (no state from gh); run land-check again\n",
                 "READY: ok\n"])
out, listings = [], iter([json.dumps([gh_pr(1, "BLOCKED")])] * 4)
pr_watch.watch(lambda: pr_watch.snapshot(["a/b"], "", "prog", 50,
                                         lambda a: next(listings) if a[0] == "gh" else next(verdicts)),
               300, out.append, lambda s: None, rounds=4)
assert out == ["a/b#1 land-check READY"], out
# land-check: READY that blips to "no answer" and back is not a new READY; BLOCKED is no reason to skip the check.
verdicts = iter(["HOLD: x\n", "READY\n", "", "READY\n"])
out, listings = [], iter([json.dumps([gh_pr(1, "BLOCKED")])] * 4)
pr_watch.watch(lambda: pr_watch.snapshot(["a/b"], "", "prog", 50,
                                         lambda a: next(listings) if a[0] == "gh" else next(verdicts)),
               300, out.append, lambda s: None, rounds=4)
assert out == ["a/b#1 land-check READY"], out

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
for bad in (["--repo", "a/b", "--interval", "60"], ["--repo", "a/b", "--repo", "a/c", "--land-check", "prog"]):
    try:
        pr_watch.parse(bad)
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError(f"must be refused: {bad}")

# --land-check judges the program's repo, so a watched repo that differs (or a program that is missing) is refused.
with tempfile.TemporaryDirectory() as home:
    os.environ["PROGRAMS_HOME"] = home
    (pathlib.Path(home) / "prog").mkdir()
    (pathlib.Path(home) / "prog" / "program.json").write_text(json.dumps({"repo": "a/b"}))
    (pathlib.Path(home) / "broken").mkdir()
    (pathlib.Path(home) / "broken" / "program.json").write_text("{not json")
    assert pr_watch.parse(["--repo", "a/b", "--land-check", "prog"]).land_check == "prog"
    assert pr_watch.parse(["--repo", "A/B", "--land-check", "prog"]).land_check == "prog", "repo names compare case-blind"
    for bad in (["--repo", "a/other", "--land-check", "prog"], ["--repo", "a/b", "--land-check", "missing"],
                ["--repo", "a/b", "--land-check", "broken"]):
        try:
            pr_watch.parse(bad)
        except SystemExit as e:
            assert e.code == 2
        else:
            raise AssertionError(f"must be refused: {bad}")

# A rerun that started later is the latest run even when the older run finished after it began.
slow_old = chk("FAILURE", "01:00", completedAt="2026-11-06T03:00:00Z")
fast_new = chk("SUCCESS", "02:00")
assert pr_watch.failing([slow_old, fast_new]) is False and pr_watch.failing([fast_new, slow_old]) is False

# A PR that reopens prints: directly after CLOSED, and after a round where it was missing from the list.
assert replay([json.dumps([gh_pr(1, state="CLOSED")]), json.dumps([gh_pr(1)])]) == ["a/b#1 reopened"]
assert replay([json.dumps([gh_pr(1, state="CLOSED")]), json.dumps([]), json.dumps([gh_pr(1)])]) == ["a/b#1 opened"]
# Memory stays bounded: finished PRs that left the list are not carried.
kept = pr_watch.carry({"a/b#1": pr("MERGED"), "a/b#2": pr()}, {})
assert list(kept) == ["a/b#2"], kept

# A PR missing from one listing keeps its last state: it does not reopen, and a merge seen after it returns prints.
gone = [json.dumps([gh_pr(1)]), json.dumps([]), json.dumps([gh_pr(1)])]
assert replay(gone) == [], "OPEN, missing, OPEN is not a new PR"
gone = [json.dumps([gh_pr(1)]), json.dumps([]), json.dumps([gh_pr(1, state="MERGED")])]
assert replay(gone) == ["a/b#1 MERGED"], "a merge seen after a missing round still prints"

# Only a READY or HOLD line is a verdict: a note alone must not reset READY.
verdicts = iter(["HOLD: x\n", "READY: ok\n", "note: main CI for the last landing has not reported yet\n", "READY: ok\n"])
out, listings = [], iter([json.dumps([gh_pr(1, "BLOCKED")])] * 4)
pr_watch.watch(lambda: pr_watch.snapshot(["a/b"], "", "prog", 50,
                                         lambda a: next(listings) if a[0] == "gh" else next(verdicts)),
               300, out.append, lambda s: None, rounds=4)
assert out == ["a/b#1 land-check READY"], out

# The command the docs and the hook warning name runs from any repo, not only from this checkout: bin/pr-watch finds the
# script by its own location. Run it from a directory that holds nothing of this plugin.
import subprocess
WRAPPER = pathlib.Path(__file__).resolve().parents[3] / "bin" / "pr-watch"
assert os.access(WRAPPER, os.X_OK), "bin/pr-watch must be executable"
with tempfile.TemporaryDirectory() as elsewhere:
    helped = subprocess.run([str(WRAPPER), "--help"], cwd=elsewhere, capture_output=True, text=True)
    assert helped.returncode == 0 and "--repo" in helped.stdout and "pr-watch" in helped.stdout, helped
    refused = subprocess.run([str(WRAPPER), "--repo", "a/b", "--interval", "60"], cwd=elsewhere, capture_output=True, text=True)
    assert refused.returncode == 2 and "300 seconds or more" in refused.stderr, refused
    # A symlink to the wrapper (a user's ~/bin entry) must still find the script through the link's target, not the link's dir.
    link = pathlib.Path(elsewhere) / "pr-watch"
    link.symlink_to(WRAPPER)
    linked = subprocess.run([str(link), "--help"], cwd=elsewhere, capture_output=True, text=True)
    assert linked.returncode == 0 and "--repo" in linked.stdout, linked
print("test_pr_watch: ok")
