"""`orch status` compares the program note's value lines with program.json and only says when they differ.
program.json is the source; the note is a copy, so nothing here writes either file.
Run: python3 test_note_values.py"""
import json, os, pathlib, shutil, sys, tempfile, atexit

tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-note-values-"))
atexit.register(shutil.rmtree, tmp, True)
os.environ.update(PROGRAMS_HOME=str(tmp / "programs"), AGENT_SKILLS_STATE=str(tmp / "state"), ORCH_DASH="off",
                  HOME=str(tmp / "home"), AGENT_SKILLS_CONFIG=str(tmp / "agent-skills.json"))
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import prog  # noqa: E402

NOTES = tmp / "notes"
CFG = {"slug": "demo", "repo": "acme/app", "run": "run_x", "predicate": ["ABC-1", "ABC-2"], "ceiling": 6,
       "merge_policy": "human-gate", "deadline": None, "created_at": "2026-11-01T00:00:00+00:00"}


def program(**extra):
    d = prog.HOME / "demo"
    d.mkdir(parents=True, exist_ok=True)
    (d / "program.json").write_text(json.dumps({**CFG, **extra}))
    (d / "ledger.jsonl").write_text("")
    return prog.Program("demo")


def note(text):
    (NOTES / "Project" / "acme").mkdir(parents=True, exist_ok=True)
    f = NOTES / "Project" / "acme" / "program-demo.md"
    f.write_text(text)
    return f


def configure(adapter="markdown"):
    pathlib.Path(os.environ["AGENT_SKILLS_CONFIG"]).write_text(
        json.dumps({"notes": {"adapter": adapter, "markdown": {"root": str(NOTES)}}}))


SAME = """#acme #program

## 개요

- 머지 정책: human-gate · 동시성 천장 6 · 마감 없음

## 완료 조건 (predicate)

- [ ] ABC-1 first
- [x] ABC-2 second
- [ ] 최종 확인: run it

범위 밖:

- 없음
"""

# No notes configuration: one line, no guess at a path.
(out,) = prog.note_lines(program())
assert out.startswith("note values: not compared") and "markdown" in out, out

configure("obsidian")
(out,) = prog.note_lines(program())
assert out.startswith("note values: not compared"), out

# Markdown root without the note.
configure()
(out,) = prog.note_lines(program())
assert out.startswith("note values: not compared") and "program-demo.md" in out, out

# Same values: one line that says how many were compared.
f = note(SAME)
before = f.read_text()
p = program()
assert prog.note_lines(p) == ["note values: match program.json (merge_policy, ceiling, deadline, predicate)"], prog.note_lines(p)
assert f.read_text() == before and json.loads((prog.HOME / "demo" / "program.json").read_text())["ceiling"] == 6

# Different values: each difference named, both values shown, nothing rewritten.
note(SAME.replace("human-gate", "autonomous").replace("천장 6", "천장 4").replace("마감 없음", "마감 2026-12-01T00:00:00Z")
     .replace("ABC-2 second", "ABC-9 other"))
(out,) = prog.note_lines(program())
for want in ("note values: DIFFER from program.json", "merge_policy: note autonomous, program.json human-gate",
             "ceiling: note 4, program.json 6", "deadline: note 2026-12-01T00:00:00Z, program.json none",
             "predicate: note only ABC-9, program.json only ABC-2", "program.json is the source"):
    assert want in out, (want, out)
assert f.read_text() != before  # the note is the test's own copy; prog never wrote it

# The same instant written two ways is no difference.
note(SAME.replace("마감 없음", "마감 2026-12-01T09:00:00+09:00"))
(out,) = prog.note_lines(program(deadline="2026-12-01T00:00:00Z"))
assert out.startswith("note values: match"), out

# A note without value lines (the target shape) says so, and a template placeholder is not a value.
note("#acme #program\n\n## 개요\n\n- 값은 `orch status`\n")
(out,) = prog.note_lines(program())
assert out == "note values: none in the note (values come from `orch status`)", out
note("- 머지 정책: autonomous | human-gate · 동시성 천장 6 · 마감 <ISO8601 또는 없음>\n")
(out,) = prog.note_lines(program())
assert out.startswith("note values: match") and "merge_policy" not in out and "ceiling" in out, out

# Only the comparable part differs: a line in another shape is left out, not reported.
note("- 머지 정책: 모름\n- [ ] ABC-1 outside the predicate section\n")
(out,) = prog.note_lines(program())
assert out == "note values: none in the note (values come from `orch status`)", out

# The program's own notes override beats the user's adapter.
pathlib.Path(os.environ["AGENT_SKILLS_CONFIG"]).write_text(json.dumps({"notes": {"adapter": "obsidian"}}))
note(SAME)
(out,) = prog.note_lines(program(notes={"adapter": "markdown", "root": str(NOTES)}))
assert out.startswith("note values: match"), out

print("ok")
