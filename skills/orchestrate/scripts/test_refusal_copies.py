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
assert "use Monitor only when the one you report to says so" in brief, "Monitor exclusion no longer says when it may be used"
assert "report before you use it" not in brief, "Monitor exclusion is back to the wording that left the wait open"
assert ("Waiting on the pressure gate's `pressure.py queue wait <id>` or `turns --wait <n>`, run with "
        "`run_in_background: true` or watched with Monitor") in brief, \
    "Monitor exclusion reaches the pressure gate's own wait"
assert "queue wait <id>" in (hooks / "pressure_gate.py").read_text(), "the wait the paragraph names is not the gate's"
assert "while its queue entry still waits" in brief and "after its entry gave up: report and stop" in brief, \
    "a repeat pressure deny no longer waits within the entry's deadline, or no longer stops after it"
assert "the form to run in" not in brief, "gate exception reopened the form to run in"
assert "from any other hook, does not count" in brief, "gate exception no longer ties the markers to the two hooks only"
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

# One retry rule: every place states it in one sentence set, and the old "never, in any form" wording is gone.
repo = refs.parents[2]
CORE = ("is not run again, except once, in the same form, by the refused session (a restart that loads a new rule counts "
        "as that session), and only when the human allowed it in that session's own chat or added a rule for it, or when "
        "a classifier or hook refusal stated a reason and you added evidence that answers it, never for a human's refusal "
        "or a production sync. Report that retry as `재시도 1회: <what you added>`. A second refusal stops the act and you "
        "report it. Reaching the same result through another session, script, tool, wrapper or target stays forbidden.")
KO_CORE = ("거부된 행동은 다시 하지 않는다. 예외는 한 번뿐이고, 거부된 세션(새 규칙을 읽으려고 다시 시작한 경우 포함)이 같은 형태로 하며, "
           "사람이 그 세션 채팅에서 허락했거나 규칙을 넣었을 때, 또는 분류기나 hook이 사유를 말했고 그 사유에 맞는 근거를 보강했을 때만이다.")
COPIES = ("skills/orchestrate/SKILL.md", "skills/orchestrate/references/brief.md", "skills/orchestrate/references/roles.md",
          "skills/orchestrate/references/top-level.md", "skills/deliver-ticket/SKILL.md",
          "skills/deliver-ticket/references/post-merge-release.md", "skills/end-session/SKILL.md",
          "skills/setup-pc/SKILL.md", "skills/skills-config/SKILL.md")
OLD = re.compile(r"in any form|in another form|다른 형태로|어떤 형태로도|別の形で再試行")
for name in (*COPIES, "skills/tune-automode/SKILL.md", "README.md", "README.ko.md", "README.ja.md", "docs/skills.md",
             "docs/skills.en.md", "docs/skills.ja.md", "docs/operations.md"):
    assert not OLD.search((repo / name).read_text()), f"{name} still words the old rule"
for name in COPIES:
    assert CORE in " ".join((repo / name).read_text().split()), f"{name} lost the one-retry rule's text"
assert KO_CORE in (repo / "skills/tune-automode/SKILL.md").read_text(), "tune-automode lost the Korean rule"
assert "[gate:self-land]" in brief and "uses up no retry" in brief, "the self-land block no longer says it is no refused report"

print("test_refusal_copies: ok")
