---
name: ui-review
description: "Use before a mockup, a screen or a UI PR is shown to a person, and after a person points out a UI problem — the gate that measures layout and consistency (row heights, column widths, alignment, spacing, links, color, the existing screens) and walks the flow (system steps, duplicated options, grouping). \"UI 리뷰해줘\", \"시안 점검해줘\", \"화면 밸런스 봐줘\", \"보여주기 전에 UI 확인해줘\". Taking and posting screenshots is deliver-ticket's visual-evidence."
---

# UI review

A person should not be the first to notice that one row is taller, a button narrower or a step
pointless. This skill is the gate before they look: measure what can be measured, walk the
scenario for what cannot, and show the result with the screen. The rules and the reason for each
are in [references/rules.md](references/rules.md).

## When

- Before a mockup goes to the user for approval.
- Before a PR that changes what people see asks for review, and again when a commit changes the
  rendered output.
- When the user flags a UI problem: fix it, then add the rule (rules.md, "Adding a rule") so the
  next screen is measured for it.

Not for a change with no rendered output, and not instead of deliver-ticket's §5 E2E check: a
page can pass every rule and still not work.

## Procedure

1. **Find the reference.** The product's design system (tokens, components, Storybook) wins over
   every rule here: read its spacing scale, control heights and colors, and turn them into flags
   (`--grid`, `--allow`). Pick an existing screen of the same kind (a list for a list, a settings
   page for a settings page) as the baseline. No design system and no existing screen: the
   defaults in rules.md apply, and say so in the result.
2. **Measure** each screen and each state the change touches (empty, error, many rows, long
   text), at 1280 and 390:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/ui_measure.py" <url or file.html> \
     --baseline <existing screen> --json <out>/<screen>.json > <out>/<screen>.md
   ```

   - `--before '<js>'` runs page code before measuring: open a tab, pick a mockup state, sign
     nothing in. `--scope <sel>` limits the check to the part under review; `--exclude <sel>`
     drops a mockup control bar. `data-ui-review-ignore` on an element skips it, with the reason
     in the result.
   - It needs Chrome (`CHROME` overrides the path) and only the standard library. It starts one
     headless Chrome with a throwaway profile and stops that PID. A page behind a login is
     measured only where a session already exists (an exported static page, a dev fixture);
     signing in is the human's step.
   - Exit 1 means a FAIL. WARN items are judgment calls: decide each one and write why.
3. **Walk the scenario** with [references/walkthrough.md](references/walkthrough.md): the user's
   task from entry to done, counted in screens, clicks and scrolls, with every step the system
   could do itself and every option that repeats another marked. This catches what no
   measurement does: wrong grouping, system steps shown to people, unclear actions.
4. **Fix and rerun** until no FAIL is left that is not an accepted exception. An exception names
   the rule and the reason ("the design system's chip padding is 6px").
5. **Report** with the screen, in the PR's sticky comment or next to the mockup link:

   ```markdown
   ### UI 리뷰 (head `abc1234`, 1280·390, 기준 화면: /projects)
   | 규칙 | 판정 | 근거 |
   |---|---|---|
   | row-height | PASS | |
   | control-column | FAIL→수정 | "비활성화" 76px vs 68px, 폭 고정 |
   | walkthrough | PASS | 3화면·4클릭, 시스템 단계 0, 중복 옵션 0 |
   | 예외 | spacing-grid 6px | 디자인 시스템 chip 토큰 |
   ```

   A mockup is not sent to the user while a FAIL stands without its exception. Its link is a
   tailnet one on its own port, checked for 200 before it is sent
   (`deliver-ticket` `references/visual-evidence.md`, "A live page for a person").

## Verdict

PASS when every measured check is PASS or an accepted exception, every WARN has a decision, and
the walkthrough has no open item. The verdict holds for the head or the mockup version it was
made on.

## Boundaries

- `deliver-ticket` §3 (Review): a diff that changes rendered output runs this before the code
  review closes; the review row in the PR body names it. §5 and
  `references/visual-evidence.md` take the screenshots and the lead's verdict; that verdict
  starts from this table.
- `orchestrate`: a lead runs this before a mockup or screen goes to the human, and judges a
  PR's screenshots with this table in hand.
- Product-specific rules (one product's tokens, a team's layout choice) live in that product's
  repo or the user's local rules file (rules.md, "Adding a rule"), not here.
