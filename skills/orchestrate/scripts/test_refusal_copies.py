"""The refused-act paragraph in brief.md and roles.md is one rule pasted verbatim into subagent prompts; the copies must not drift.

Run: python3 test_refusal_copies.py
"""
import pathlib, re

refs = pathlib.Path(__file__).resolve().parent.parent / "references"


def paragraph(name):
    text = (refs / name).read_text()
    m = re.search(r"An act refused by a permission prompt.*?fix\s+the\s+cause\s+and\s+run\s+it\s+again\.", text, re.S)
    assert m, f"{name}: refused-act paragraph not found"
    return " ".join(m.group(0).split())


brief = paragraph("brief.md")
assert paragraph("roles.md") == brief, "roles.md's refused-act paragraph differs from brief.md's"
assert 'from "An act refused" through "run it again."' in brief, "paste boundary no longer names the paragraph's ends"
for name in ("../SKILL.md", "top-level.md"):
    text = (refs / name).read_text()
    assert "no-verdict restart" in text, f"{name}: exception list lost the no-verdict restart"
    assert "a gate's named wait, step or form" in text, f"{name}: exception list lost the gate's named wait, step or form"

print("test_refusal_copies: ok")
