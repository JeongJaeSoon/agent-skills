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
               "in #2's worktree git merge origin/abc-1-fix and push; gh stack link 1 2 --base main;"
               " orch dep p --ticket ABC-2 --after ABC-1"], out
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
# The middle layer need not be a program PR (or have listable files) to tie the ends together.
assert overlaps({1: ["a"], 3: ["a"]}, {1: "A-1", 3: "C-3"}, chain) == []
assert overlaps({1: ["a"], 3: ["a"]}, {1: "A-1", 3: "C-3"}, rows, stacks={"s": [1, 2, 3]}) == []

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

# Across programs: this program's open PRs against other programs' open PRs in the same repo. Warnings only:
# no stack or `orch dep` command, since neither lead can tie its PR to another program's ticket.
import json, tempfile, traceback

xrows = [row(1, "abc-1-fix"), row(2, "abc-2-fix"), row(40, "other-40"), row(41, "other-41")]
WARN = "→ warning: settle the landing order with the other lead through PARENT"


def no_command(lines):
    assert lines and not any("orch dep" in x or "stack" in x or "git merge" in x for x in lines), lines


def test_cross_program_overlap():
    # One line per shared file, and no command.
    out = prog.cross_overlaps({1: ["a", "b", "c"], 2: ["z"]}, {"other": {40: ["b", "a"], 41: ["y"]}}, xrows)
    assert out == [f"OVERLAP other #1 #40 a {WARN}", f"OVERLAP other #1 #40 b {WARN}"], out
    no_command(out)


def test_cross_program_plan_overlap():
    # Planned paths (--paths) against another program's open PRs, one line per planned path it touches.
    out = prog.cross_overlaps({}, {"other": {40: ["skills/x/a.py", "skills/x/b.py", "d"]}}, xrows,
                              paths=["skills/x/", "skills/*.py", "c"])
    assert out == [f"OVERLAP other plan #40 skills/x/a.py {WARN}", f"OVERLAP other plan #40 skills/x/b.py {WARN}"], out
    no_command(out)


def test_cross_program_no_overlap():
    assert prog.cross_overlaps({1: ["a"]}, {"other": {40: ["b"]}}, xrows) == []
    assert prog.cross_overlaps({1: ["a"]}, {"other": {}}, xrows) == []


def test_cross_program_stack_excluded():
    # Already related through a GitHub stack, or one PR's base being the other's head: no line.
    assert prog.cross_overlaps({1: ["a"]}, {"other": {40: ["a"]}}, xrows, stacks={"s": [1, 40]}) == []
    assert prog.cross_overlaps({1: ["a"]}, {"other": {40: ["a"]}},
                               [row(1, "abc-1-fix"), row(40, "o", base="abc-1-fix")]) == []


def test_cross_program_unreadable_is_unknown():
    # A file list gh could not read, or a program whose state could not be read, is UNKNOWN, never "no overlap".
    out = prog.cross_overlaps({1: ["a"]}, {"other": {40: None, 41: ["a"]}}, xrows)
    assert out == ["OVERLAP UNKNOWN other #40 → its file list could not be read; compare it by hand",
                   f"OVERLAP other #1 #41 a {WARN}"], out
    assert prog.cross_overlaps({1: ["a"]}, {"broken": None}, xrows) == [
        "OVERLAP UNKNOWN broken → its program state could not be read; compare it by hand"]
    # This program's own PR whose file list could not be read, while another program has an open PR.
    out = prog.cross_overlaps({}, {"other": {40: ["a"]}}, xrows, unread=[2])
    assert out == ["OVERLAP UNKNOWN #2 → this program's file list for it could not be read; compare it by hand"], out
    assert prog.cross_overlaps({}, {"other": {}}, xrows, unread=[2]) == []


def test_other_programs_from_their_state():
    # Same repo only; open PRs the other program's ledger names and has not seen land. A PR that only carries a
    # ticket-like name is not counted: short ids repeat across programs.
    with tempfile.TemporaryDirectory() as d:
        home = pathlib.Path(d)

        def program(slug, repo, ledger, predicate=()):
            (home / slug).mkdir()
            (home / slug / "program.json").write_text(json.dumps({"slug": slug, "repo": repo, "predicate": list(predicate)}))
            (home / slug / "ledger.jsonl").write_text("".join(json.dumps(e) + "\n" for e in ledger))
        program("me", "acme/app", [{"ts": T, "ev": "ready", "pr": 1, "ticket": "ABC-1"}])
        program("other", "acme/app", [{"ts": T, "ev": "ready", "pr": 40, "ticket": "XYZ-1"},
                                      {"ts": T, "ev": "ready", "pr": 41, "ticket": "XYZ-2"},
                                      {"ts": T, "ev": "landed", "pr": 41},
                                      {"ts": T, "ev": "ready", "pr": 99, "ticket": "XYZ-3"}], predicate=["ABC-2"])
        program("elsewhere", "acme/web", [{"ts": T, "ev": "ready", "pr": 40, "ticket": "W-1"}])
        program("broken", "acme/app", [])
        (home / "broken" / "ledger.jsonl").write_text("{not json\n")
        (home / "_archive").mkdir()
        rows_ = [row(1, "abc-1-fix"), row(2, "abc-2-fix"), row(40, "xyz-1"), row(41, "xyz-2")]
        calls = []

        def listed(repo, n):
            calls.append((repo, n))
            return {40: ["a"]}.get(n)
        old, prog.HOME = prog.HOME, home
        try:
            got = prog.other_program_files("me", "acme/app", {1}, rows_, listed)
            # A PR this program already counts as its own is not compared with itself.
            mine = prog.other_program_files("me", "acme/app", {1, 40}, rows_, listed)
        finally:
            prog.HOME = old
        # #2 is not claimed by `other` though its predicate names ABC-2; #41 landed; #99 is not open.
        assert got == {"broken": None, "other": {40: ["a"]}}, got
        assert calls == [("acme/app", 40)], calls
        assert mine == {"broken": None, "other": {}}, mine
        # A ledger row without the shape the ledger has is unreadable state too, whatever its repo.
        program("shapeless2", "acme/app", [{"pr": 40}])
        prog.HOME = home
        try:
            assert prog.other_program_files("me", "acme/app", {1}, rows_, listed)["shapeless2"] is None
        finally:
            prog.HOME = old


def test_other_ledger_wins_over_ticket_guess():
    # #40's branch carries this program's ticket, but another program's ledger names it: it is theirs, compared
    # across programs and kept out of this program's own OVERLAP lines; #3 could not be listed and is unread.
    with tempfile.TemporaryDirectory() as d:
        home = pathlib.Path(d)
        (home / "other").mkdir()
        (home / "other" / "program.json").write_text(json.dumps({"repo": "acme/app"}))
        (home / "other" / "ledger.jsonl").write_text(json.dumps({"ts": T, "ev": "ready", "pr": 40}) + "\n")
        old, prog.HOME = prog.HOME, home
        try:
            got = prog.cross_inputs("me", "acme/app", [{"ts": T, "ev": "ready", "pr": 1, "ticket": "ABC-1"}],
                                    [row(1, "abc-1-a"), row(3, "abc-1-c"), row(40, "abc-1-b")],
                                    {1: "ABC-1", 3: "ABC-1", 40: "ABC-1"}, {1: ["a"], 40: ["a"]},
                                    lambda repo, n: ["a"])
        finally:
            prog.HOME = old
        assert got == ({1: "ABC-1", 3: "ABC-1"}, {1: ["a"]}, {"other": {40: ["a"]}}, [3]), got


failed = []
for name, fn in [(k, v) for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]:
    try:
        fn()
    except Exception:
        failed.append(name)
        print(f"FAIL {name}\n{traceback.format_exc(limit=1)}")
if failed:
    sys.exit(f"{len(failed)} failed: {', '.join(failed)}")
print("prog.py path overlap: all pass")
