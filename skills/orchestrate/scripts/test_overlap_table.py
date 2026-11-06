"""orch overlap --table: the hot files of the open PRs and the order to land them in. Run: python3 test_overlap_table.py"""
import json, pathlib, sys, tempfile
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
got = prog.table_facts(listing, verdicts, no_ci=False)
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
print("ok")
