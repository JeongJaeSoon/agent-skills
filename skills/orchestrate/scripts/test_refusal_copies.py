"""The refused-act paragraph in brief.md and roles.md is one rule pasted verbatim into subagent prompts; the copies must not drift.

Run: python3 test_refusal_copies.py
"""
import pathlib, re

refs = pathlib.Path(__file__).resolve().parent.parent / "references"
hooks = refs.parents[2] / "hooks"
MARKS = ("[gate:pressure]", "[gate:sleep-loop]")


def paragraph(name):
    text = (refs / name).read_text()
    m = re.search(r"An act refused by a permission prompt.*?fix\s+the\s+cause\s+and\s+run\s+it\s+again\.", text, re.S)
    assert m, f"{name}: refused-act paragraph not found"
    return " ".join(m.group(0).split())


brief = paragraph("brief.md")
assert "/orch-refusal/" in brief, "refused-act paragraph lost the report file's fixed path"
assert "/orch-refusal/" in (refs / "../SKILL.md").read_text(), "SKILL.md lost the report file's fixed path"
# The guardian brief has no refused-act paragraph, so its outside-the-worktree exception names the path itself.
guardian = re.search(r"you write nothing outside your worktree except (.*?)\.\n", (refs / "roles.md").read_text(), re.S)
assert guardian and "/orch-refusal/" in guardian[1] and "Write" in guardian[1], "guardian brief lost the refusal-report path"
assert paragraph("roles.md") == brief, "roles.md's refused-act paragraph differs from brief.md's"
assert 'from "An act refused" through "run it again."' in brief, "paste boundary no longer names the paragraph's ends"

# The gate exception keys on the markers the two hooks put first in their deny reasons.
for mark in MARKS:
    assert f"`{mark}`" in brief, f"refused-act paragraph no longer names {mark}"
for hook in ("`hooks/pressure_gate.py`", "`hooks/guard.py`"):
    assert hook in brief, f"refused-act paragraph no longer ties the markers to {hook}"
assert "the same Bash command with `run_in_background: true`" in brief, "gate exception lost its one form"
assert "The form keeps the act" not in brief, "gate exception reopened the form to other wrappers"
assert "Monitor tool, which that block also offers, is not part of this exception" in brief, "gate exception no longer excludes Monitor"
assert 'MARK = "[gate:pressure]"' in (hooks / "pressure_gate.py").read_text(), "pressure_gate.py lost its marker"
assert 'SLEEP_MARK = "[gate:sleep-loop]"' in (hooks / "guard.py").read_text(), "guard.py lost its sleep-loop marker"

# Short exception lists point at FORBIDDEN; the one full list in SKILL.md names what the paragraph exempts.
skill = " ".join((refs / "../SKILL.md").read_text().split())
for name, text in (("../SKILL.md", skill), ("top-level.md", (refs / "top-level.md").read_text())):
    assert "FORBIDDEN names as not a retry" in text, f"{name}: exception list no longer points at FORBIDDEN"
    assert "a gate's" not in text, f"{name}: names a gate generically instead of its two markers"
full = re.search(r"What the FORBIDDEN paragraph names as not a retry is not a breach and not a signal: (.*?)\. ", skill)
assert full, "SKILL.md lost the full not-a-retry list"
for in_skill, in_brief in (("the brief's own path", "Taking the path this brief already names"),
                           ("a redo after the human's rule", "after the human added a rule for it"),
                           ("the text-only reword", "reword that text inside the same command"),
                           (f"`{MARKS[0]}` or `{MARKS[1]}` block", f"`{MARKS[0]}`"),
                           ("the harmless read", "do the same read once with the Read or Grep tool"),
                           ("the no-verdict restart", "Nor is the restart of a unit")):
    assert in_skill in full[1], f"SKILL.md not-a-retry list lost: {in_skill}"
    assert in_brief in brief, f"refused-act paragraph lost: {in_brief}"

print("test_refusal_copies: ok")
