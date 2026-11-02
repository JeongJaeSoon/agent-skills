"""Path overlap between open program PRs (orch overlap / status). Run: python3 test_overlap.py"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import prog

CFG = {"repo": "acme/app"}
T = "2026-11-02T00:00:00+00:00"


def row(n, head, base="main", title=""):
    return {"number": n, "headRefName": head, "baseRefName": base, "title": title}


def overlaps(files, prs, rows, after=None, stacks=None):
    return prog.path_overlaps("p", files, prs, CFG, rows, after or {}, stacks)


rows = [row(1, "abc-1-fix"), row(2, "abc-2-fix")]
prs = {1: "ABC-1", 2: "ABC-2"}

# Two unrelated PRs on one file: the later one is stacked on the earlier, with the commands to do it.
out = overlaps({1: ["skills/x/a.py", "README.md"], 2: ["skills/x/a.py"]}, prs, rows)
assert out == ["OVERLAP #1 #2 skills/x/a.py → stack #2 on #1 (orch dep): "
               "gh pr edit 2 --base abc-1-fix; orch dep p --ticket ABC-2 --after ABC-1"], out
# Several shared files: the first is named, the rest counted.
out = overlaps({1: ["a", "b"], 2: ["b", "a"]}, prs, rows)
assert out[0].startswith("OVERLAP #1 #2 a (+1 more) → stack #2 on #1"), out

# Nothing shared: no line.
assert overlaps({1: ["a"], 2: ["b"]}, prs, rows) == []

# A land-after edge in either direction, a GitHub stack, or a base on the other's head relates them.
assert overlaps({1: ["a"], 2: ["a"]}, prs, rows, after={"ABC-2": {"ABC-1"}}) == []
assert overlaps({1: ["a"], 2: ["a"]}, prs, rows, after={"ABC-1": {"ABC-2"}}) == []
assert overlaps({1: ["a"], 2: ["a"]}, prs, rows, stacks={"s": [1, 2]}) == []
assert overlaps({1: ["a"], 2: ["a"]}, prs, [row(1, "abc-1-fix"), row(2, "abc-2-fix", base="abc-1-fix")]) == []

# A chain is related end to end: C stacked on B, B on A, A and C both touching one file is fine.
chain = [row(1, "a1"), row(2, "b2", base="a1"), row(3, "c3", base="b2")]
assert overlaps({1: ["a"], 2: ["z"], 3: ["a"]}, {1: "A-1", 2: "B-2", 3: "C-3"}, chain) == []
assert overlaps({1: ["a"], 2: ["z"], 3: ["a"]}, {1: "A-1", 2: "B-2", 3: "C-3"}, rows,
                after={"C-3": {"B-2"}, "B-2": {"A-1"}}) == []

# Different files under one exclusive_paths glob still collide (two migrations), and the glob is named.
out = overlaps({1: ["db/migrations/001.sql"], 2: ["db/migrations/002.sql"]}, prs, rows)
assert out and out[0].startswith("OVERLAP #1 #2 *migrations/* → stack #2 on #1"), out

# A PR with no known ticket keeps a placeholder, never a blank the dep command would reject.
out = overlaps({1: ["a"], 2: ["a"]}, {1: "ABC-1", 2: None}, rows)
assert out[0].endswith("orch dep p --ticket <ticket> --after ABC-1"), out

# Program PRs: the ledger's, plus open PRs whose branch or title carries a program ticket, before any `ready`.
events = [{"ts": T, "ev": "ready", "pr": 5, "ticket": "ABC-5"},
          {"ts": T, "ev": "ready", "pr": 6, "ticket": "ABC-6"}, {"ts": T, "ev": "landed", "pr": 6}]
open_rows = [row(5, "x"), row(6, "y"), row(7, "abc-7-docs"), row(8, "z", title="ABC-1 follow-up"),
             row(9, "abc-70-other"), row(10, "unrelated")]
got = prog.program_prs(events, open_rows, {"ABC-5", "ABC-7", "ABC-1"})
assert got == {5: "ABC-5", 7: "ABC-7", 8: "ABC-1"}, got

# Pre-spawn: a planned file, directory or glob against open PRs, and a shared exclusive glob.
files = {1: ["skills/x/a.py"], 2: ["db/migrations/002.sql"], 3: ["docs/b.md"]}
out = prog.plan_overlaps("p", ["skills/x/"], files, prs, CFG, rows)
assert out == ["OVERLAP plan #1 skills/x/ → stack the unit on #1 (orch dep): "
               "Base: stacked on #1 (abc-1-fix); orch dep p --ticket <ticket> --after ABC-1"], out
assert prog.plan_overlaps("p", ["skills/*.py"], files, prs, CFG, rows)[0].startswith("OVERLAP plan #1 skills/*.py")
assert prog.plan_overlaps("p", ["db/migrations/003.sql"], files, prs, CFG, rows)[0].startswith(
    "OVERLAP plan #2 *migrations/*")
assert prog.plan_overlaps("p", ["src/"], files, prs, CFG, rows) == []

print("prog.py path overlap: all pass")
