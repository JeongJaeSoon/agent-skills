"""orch overlap --table: the hot files of the open PRs and the order to land them in. Run: python3 test_overlap_table.py"""
import json, os, pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from overlap_table import hot_table
import prog


def pr(verdict="none", ci="ok", ms="CLEAN", draft=False, base="main"):
    return {"verdict": verdict, "ci": ci, "merge_state": ms, "draft": draft, "base": base}


READY = pr("current")

# No file in two PRs: one line says so, with the counts.
assert hot_table({1: ["a"], 2: ["b"]}, {1: READY, 2: READY}) == ["no hot file among 2 open PR(s)"]
assert hot_table({}, {}) == ["no hot file among 0 open PR(s)"]

# A ready PR lands before a lower-numbered PR that is not ready; the first ready one is marked.
out = hot_table({5: ["x.py"], 9: ["x.py"]}, {5: pr("none"), 9: READY})
assert out == ["HOT x.py: 2 open PRs",
               "  1 #9 ready: verdict current, CI ok, CLEAN  <- land first",
               "  2 #5 wait: verdict none, CI ok, CLEAN"], out

# Among equals the lower number goes first, and nothing ready means no marker.
out = hot_table({7: ["x"], 3: ["x"]}, {7: pr("none"), 3: pr("none")})
assert out == ["HOT x: 2 open PRs", "  1 #3 wait: verdict none, CI ok, CLEAN",
               "  2 #7 wait: verdict none, CI ok, CLEAN", "  no PR is ready"], out

# What keeps a PR from ready: a stale or failed verdict, CI not ok, a merge state other than clean, a draft.
for facts in (pr("stale"), pr("fail"), pr("none"), pr("current", ci="pending"), pr("current", ci="failed"),
              pr("current", ms="DIRTY"), pr("current", ms="BEHIND"), pr("current", ms="UNSTABLE"), pr("current", draft=True)):
    out = hot_table({1: ["f"], 2: ["f"]}, {1: facts, 2: pr("none")})
    assert "  no PR is ready" in out and not any("<- land first" in x for x in out), (facts, out)
assert any("ready:" in x for x in hot_table({1: ["f"], 2: ["f"]}, {1: pr("current", ms="HAS_HOOKS"), 2: pr()}))

# Files are listed by how many PRs touch them, then by name; a PR on another PR's branch shows its base.
files = {1: ["a", "b", "c"], 2: ["b", "c"], 3: ["c"], 4: ["a"]}
rows = {1: READY, 2: pr(base="feat/one"), 3: pr(), 4: pr()}
heads = [x for x in hot_table(files, rows) if x.startswith("HOT")]
assert heads == ["HOT c: 3 open PRs", "HOT a: 2 open PRs", "HOT b: 2 open PRs"], heads
assert any("#2 wait: verdict none, CI ok, CLEAN, base feat/one" in x for x in hot_table(files, rows))

# max_open: an OVER line for each file above the limit, after the tables; none at or below it, none when unset.
out = hot_table(files, rows, max_open=2)
assert [x for x in out if x.startswith("OVER")] == ["OVER c: 3 open PRs, max_open 2"], out
assert not any(x.startswith("OVER") for x in hot_table(files, rows, max_open=3))
assert not any(x.startswith("OVER") for x in hot_table(files, rows))

# A PR whose files or facts could not be read is named, never dropped silently.
out = hot_table({1: ["f"], 2: ["f"]}, {1: READY, 2: pr()}, unread=[7], unknown=["program ledger acme"])
assert "UNKNOWN #7: its file list could not be read" in out and "UNKNOWN program ledger acme: could not be read" in out, out
# A PR with files but no facts is a wait with unknown facts, not a crash.
out = hot_table({1: ["f"], 2: ["f"]}, {1: READY})
assert "  2 #2 wait: facts unknown" in out, out

# The adapter: facts from a PR listing and the programs' ledgers of the repo.
H = "a" * 40
passing = [{"name": "tests", "status": "COMPLETED", "conclusion": "SUCCESS"}]
failing = [{"name": "tests", "status": "COMPLETED", "conclusion": "FAILURE"}]
listing = [
    {"number": 1, "headRefOid": H, "isDraft": False, "mergeStateStatus": "CLEAN", "baseRefName": "main", "statusCheckRollup": passing},
    {"number": 2, "headRefOid": H, "isDraft": True, "mergeStateStatus": "DIRTY", "baseRefName": "b", "statusCheckRollup": failing},
    {"number": 3, "headRefOid": H, "isDraft": False, "mergeStateStatus": "CLEAN", "baseRefName": "main", "statusCheckRollup": []},
    {"number": 4, "headRefOid": H, "isDraft": False, "mergeStateStatus": "CLEAN", "baseRefName": "main", "statusCheckRollup": passing},
]
verdicts = {1: {"result": "pass", "sha": H}, 2: {"result": "pass", "sha": "b" * 40}, 4: {"result": "fail", "sha": H}}
got = prog.table_facts(listing, verdicts)
assert got[1] == pr("current"), got[1]
assert got[2] == pr("stale", ci="failed", ms="DIRTY", draft=True, base="b"), got[2]
assert got[3] == pr("none", ci="pending"), got[3]  # no check reported yet is not green
assert got[4] == pr("fail"), got[4]

with tempfile.TemporaryDirectory() as tmp:
    home = pathlib.Path(tmp)

    def program(slug, repo, events, raw=None):
        d = home / slug
        d.mkdir()
        (d / "program.json").write_text(json.dumps({"repo": repo}))
        (d / "ledger.jsonl").write_text(raw if raw is not None else "".join(json.dumps(e) + "\n" for e in events))

    def verdict(pr_, sha, result, ts):
        return {"ts": ts, "ev": "verdict", "pr": pr_, "sha": sha, "result": result, "source": "review:codex", "patch_id": "p"}

    program("one", "acme/app", [verdict(1, H, "pass", "2026-11-01T00:00:00+00:00"),
                                verdict(2, H, "pass", "2026-11-01T00:00:00+00:00")])
    program("two", "Acme/App", [verdict(2, H, "fail", "2026-11-02T00:00:00+00:00")])  # a later verdict in another program
    program("other", "acme/other", [verdict(9, H, "pass", "2026-11-03T00:00:00+00:00")])
    program("broken", "acme/app", [], raw="{not json\n")
    found, unknown = prog.repo_verdicts("acme/app", home)
    assert found[1]["result"] == "pass" and found[2]["result"] == "fail", found
    assert 9 not in found, "another repo's program is not read"
    assert unknown == ["program ledger broken"], unknown

    # The same second: the failing verdict wins whichever program comes first; a bad timestamp makes the ledger unknown.
    T = "2026-11-05T00:00:00+00:00"
    program("tie-a", "acme/tie", [verdict(5, H, "fail", T)])
    program("tie-b", "acme/tie", [verdict(5, H, "pass", T)])
    found, unknown = prog.repo_verdicts("acme/tie", home)
    assert found[5]["result"] == "fail" and unknown == [], (found, unknown)
    program("badts", "acme/ts", [verdict(6, H, "pass", "bad timestamp")])
    found, unknown = prog.repo_verdicts("acme/ts", home)
    assert 6 not in found and unknown == ["program ledger badts"], (found, unknown)
    # A ledger that fails halfway adds none of its verdicts, the ones before the bad line included, and a
    # timestamp without a zone is a bad one.
    program("half-a", "acme/half", [verdict(1, H, "pass", T), verdict(2, H, "pass", T)])
    program("half-b", "acme/half", [verdict(3, H, "pass", T), verdict(2, H, "fail", "2026-11-06T00:00:00")])
    found, unknown = prog.repo_verdicts("acme/half", home)
    assert set(found) == {1, 2} and found[2]["result"] == "pass" and unknown == ["program ledger half-b"], (found, unknown)
    # A program.json that cannot be read is named, not skipped.
    program("locked", "acme/lock", [])
    (home / "locked" / "program.json").chmod(0)
    try:
        found, unknown = prog.repo_verdicts("acme/lock", home)
    finally:
        (home / "locked" / "program.json").chmod(0o644)
    assert unknown == ["program ledger locked"] or os.geteuid() == 0, unknown
    # Bytes that are not text in program.json or the ledger: the program is unknown, the command goes on.
    program("binary", "acme/bin", [])
    (home / "binary" / "program.json").write_bytes(b"\xff\xfe\x00")
    assert prog.repo_verdicts("acme/bin", home)[1] == ["program ledger binary"]
    program("binled", "acme/binled", [])
    (home / "binled" / "ledger.jsonl").write_bytes(b"\xff\xfe\x00")
    got = prog.repo_verdicts("acme/binled", home)[1]
    # The program whose config cannot be read stays unknown for every repo asked about: its repo is not known.
    assert got == ["program ledger binary", "program ledger binled"], got
    # A program with no ledger yet is fine; a missing programs directory is an empty answer.
    program("fresh", "acme/fresh", [])
    (home / "fresh" / "ledger.jsonl").unlink()
    found, unknown = prog.repo_verdicts("acme/fresh", home)
    assert found == {} and "program ledger fresh" not in unknown, (found, unknown)
assert prog.repo_verdicts("acme/app", pathlib.Path("/nonexistent-orch-home")) == ({}, [])

# An empty rollup is pending even when the repo declares no CI.
assert prog.table_facts([dict(listing[2], statusCheckRollup=[])], {})[3]["ci"] == "pending"

# The command: a listing that fails, a file list that fails or hits the cap, each named, never a crash.
import io, contextlib
class P:
    cfg = {"repo": "acme/app"}
def run_table(rows_fn, files_fn, argv=("x", "--table")):
    saved = (prog.open_rows, prog.rest_files, prog.repo_verdicts)
    prog.open_rows, prog.rest_files, prog.repo_verdicts = rows_fn, files_fn, lambda repo: ({}, [])
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            prog.cmd_overlap_table(P, list(argv))
    finally:
        prog.open_rows, prog.rest_files, prog.repo_verdicts = saved
    return buf.getvalue()
def boom(*a):
    raise SystemExit("gh failed")
assert "UNKNOWN open PR listing: could not be read" in run_table(boom, lambda r, n: [])
assert "UNKNOWN #1: its file list could not be read" in run_table(lambda r: listing[:2], lambda r, n: None)
def oserr(r, n):
    raise OSError("no gh")
assert "UNKNOWN #1:" in run_table(lambda r: listing[:2], oserr)
cap = ["f%d" % i for i in range(prog.REST_FILES_CAP)]
out = run_table(lambda r: listing[:2], lambda r, n: cap if n == 1 else ["f1"])
assert "UNKNOWN #1:" in out, out
assert "HOT f" in run_table(lambda r: listing[:2], lambda r, n: ["f"])
try:
    run_table(lambda r: listing[:2], lambda r, n: ["f"], ("x", "--table", "--max-open", "0"))
    raise AssertionError("max-open 0 must be refused")
except SystemExit:
    pass
print("ok")
