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
for phrase in ("check --peek --wait", "run_in_background", "orch wait <slug>", "Never both", "same turn",
               "compaction", "claude --continue", "restart", "sleep loop"):
    assert phrase in rule, f"one-wait rule lost {phrase!r}"

assert sum(t.count("check --peek --wait") for t in texts.values()) == 2, \
    "the --peek wait command is stated outside its home (SKILL.md) and the notice paragraph pointer"
for name, text in texts.items():
    if name != "SKILL.md":
        assert '"One inbox wait, always"' in text, f"{name}: no pointer to the one-wait rule"
assert not re.search(r"orch wait`? passes `--types`, so it does not count", top), "top-level.md still says orch wait does not count"
assert "keep both" not in top.split("## Injected notices", 1)[1].split("##", 1)[0], "notice paragraph asks for a second wait"

lead = brief[brief.index("## Lead"):brief.index("## Rules for filling it")]
assert re.search(r"^INBOX\s", lead, re.M), "Lead brief template lost its INBOX line"
assert "`INBOX`" in lead.split("```", 1)[0], "Lead first prompt does not carry INBOX"

for name in ("SKILL.md", "brief.md"):
    assert "first-write confirmation hook" in texts[name], f"{name}: lost the policy-skill-before-first-write rule"
assert "takes the lead's own terminal input" in skill, "SKILL.md lost who answers a lead's permission dialog"

print("test_wait_rule: ok")
