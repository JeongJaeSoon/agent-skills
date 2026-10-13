"""The inbox-wait rule lives once in SKILL.md Drain; top-level.md and the Lead brief point to it and must not restate a rival rule.

Run: python3 test_wait_rule.py
"""
import pathlib, re

root = pathlib.Path(__file__).resolve().parent.parent
skill = (root / "SKILL.md").read_text()
top = (root / "references" / "top-level.md").read_text()
brief = (root / "references" / "brief.md").read_text()
texts = {"SKILL.md": skill, "top-level.md": top, "brief.md": brief}

home = "**One inbox wait, always.**"
assert skill.count(home) == 1, "SKILL.md Drain lost its one-wait rule"
rule = skill[skill.index(home):].split("\n", 1)[0]
for phrase in ("check --peek --wait", "run_in_background", "check --ack <deliveryId>", "orch wait <slug>", "Never both", "same turn",
               "compaction", "claude --continue", "restart", "sleep loop"):
    assert phrase in rule, f"one-wait rule lost {phrase!r}"

for name, n in (("SKILL.md", 1), ("top-level.md", 1), ("brief.md", 0)):
    assert texts[name].count("check --peek --wait") == n, f"{name}: `check --peek --wait` stated {texts[name].count('check --peek --wait')} times, want {n}"
for name, text in texts.items():
    if name != "SKILL.md":
        assert '"One inbox wait, always"' in text, f"{name}: no pointer to the one-wait rule"
assert not re.search(r"orch wait`? passes `--types`, so it does not count", top), "top-level.md still says orch wait does not count"
assert "keep both" not in top.split("## Injected notices", 1)[1].split("##", 1)[0], "notice paragraph asks for a second wait"

lead = brief[brief.index("## Lead"):brief.index("## Rules for filling it")]
assert re.search(r"^INBOX\s", lead, re.M), "Lead brief template lost its INBOX line"
assert "`INBOX`" in lead.split("```", 1)[0], "Lead first prompt does not carry INBOX"
card = (root.parent / "dispatch-card" / "SKILL.md").read_text()
assert "`MODE`, `ROLE: lead <slug>`, `INBOX`, `DELEGATE` and `PREDICATE` lines" in card, \
    "dispatch-card: a lead's prompt does not carry ROLE and INBOX"
assert re.search(r"^ROLE: lead <slug>$", lead, re.M), "Lead brief template lost its ROLE line"

for name in ("SKILL.md", "brief.md"):
    assert "first-write confirmation hook" in texts[name], f"{name}: lost the policy-skill-before-first-write rule"
assert "takes the lead's own terminal input" in skill, "SKILL.md lost who answers a lead's permission dialog"
assert "HUMAN ONLY  Permission dialogs," not in brief, "brief.md HUMAN ONLY still gives every permission dialog to the human"

print("test_wait_rule: ok")
