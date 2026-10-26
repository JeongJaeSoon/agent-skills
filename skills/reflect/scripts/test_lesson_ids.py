"""Offline tests for lesson_ids.py against a throwaway git repo.

Run: python3 test_lesson_ids.py
"""
import pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
from lesson_ids import history_ids, ledger_ids, report

LEDGER = """| ID | First seen | Learning |
|---|---|---|
| L-1 | 2026-01-01 | a |
| L-3 | 2026-01-02 | b, see L-40 |

## History

- 2026-01-02 L-3 new; L-99 is prose, not a row.
"""
assert ledger_ids(LEDGER) == {1, 3}

with tempfile.TemporaryDirectory() as repo:
    git = lambda *a: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True)
    git("init", "-q")
    for msg in ["fix: x (L-3)", "reflect: y (L-5, L-6)\n\n- L-6: body cites it too", "docs: no lesson"]:
        git("-c", "user.name=t", "-c", "user.email=t@example.test", "-c", "commit.gpgsign=false",
            "commit", "-q", "--allow-empty", "-m", msg)
    assert history_ids(repo, "HEAD") == {3, 5, 6}
    out = subprocess.run([sys.executable, str(HERE / "lesson_ids.py"), "--repo", repo],
                         input=LEDGER, capture_output=True, text=True, check=True).stdout
    assert out.splitlines() == ["next L-7", "foreign L-5, L-6"], out

assert report({1, 9}, {2}) == ["next L-10", "foreign L-2"]
assert report(set(), set()) == ["next L-1"]
assert report({1, 2}, {2}) == ["next L-3"]
