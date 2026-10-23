# UI rules

Each rule came from a reviewer pointing out the same kind of problem more than once on product
screens and mockups. Each has its evidence (what was pointed out, in general terms), how it is
judged — **measured** by `scripts/ui_measure.py` (check id in brackets), walked in the
**scenario** (`walkthrough.md`), or judged by **eye** — and its exceptions.

The product's design system comes first. Where it defines spacing, control sizes, colors or a
component, that definition is the rule and these defaults step aside; turn it into flags
(`--grid 8`, `--allow 6`) or an exception line rather than forking the rule.

## Layout and balance

**L1. Rows of one list or table have one height.** [`row-height`, measured]
- Evidence: one row in a list was taller than the rest because a helper line ("you cannot
  disable yourself") or a status reason sat inside it; one item of a getting-started list had a
  different height.
- Judged: rows of a table body, a list, a definition list or repeated siblings; a row more than
  1px off the height most rows share fails. Rows of plain wrapping text and stacked form groups
  are not rows.
- Exception: a row that expands on purpose (an open detail row). Put the helper text in a
  tooltip, a footnote or a fixed second line every row has.

**L2. Controls in one column have one width and one right edge.** [`control-column`, measured]
- Evidence: the same action button had different widths from row to row; a toggle and a save
  button sat at different horizontal positions in one settings block.
- Judged: buttons and fields counted from the right of each row; width or right edge off by more
  than 1px (2px for the edge) from the rest fails.
- Exception: a column that holds different actions on purpose. Give the button a fixed width
  instead of fitting its label.

**L3. Stacked blocks share their left edge, and full-width ones their right edge.**
[`edge-alignment`, measured]
- Evidence: sections whose left or right edges did not line up; a block slightly narrower than
  the one above it.
- Judged: siblings stacked vertically whose edges differ by more than 0.5px and at most
  `--near-miss` (6px). A larger offset reads as a deliberate indent.

**L4. Things in one row sit on one center line.** [`vertical-center`, measured]
- Evidence: the number and the label of a stepper step did not line up; icons, labels and
  toggles in a row sat at different heights.
- Judged: the drawn content (text lines, icons, controls) of each cell of a row; centers spread
  over more than `--center-tol` (1px) fail. A multi-line cell passes when either its block or its
  first line is on the row's center.

**L5. Spacing is on the grid.** [`spacing-grid`, measured]
- Evidence: padding and gaps that differed slightly between similar blocks.
- Judged: padding, gaps and margins of 3px or more that are not a multiple of `--grid` (4px).
  Margins above 64px are treated as `auto`.
- Exception: values the design system defines (`--allow`).

**L6. Nothing is wider than the screen.** [`horizontal-overflow`, measured]
- Judged: the document or an element not inside a scroll container wider than the viewport.

**L7. A single value stays on one line.** [`value-wrap`, warn; `row-height` for definition lists]
- Evidence: an event URL in a settings summary was shown on two lines.
- Judged: a URL, path, address or token that wraps. Truncate it in the middle with a copy
  button, or give its column the room.

**L8. A screen is not mostly empty.** [`sparse-screen`, warn; eye]
- Evidence: a list page with a few rows down the left of a wide screen; a page laid out as one
  long column top to bottom.
- Judged: share of the first screen covered by text, controls and filled or bordered boxes,
  under `--sparse` (20%). Decide by eye: a narrower max width, a grid or a side panel, or an
  empty state with a next step.

## Components and consistency

**C1. A screen uses the existing screens' components.** [`baseline`, `field-consistency`,
`table-head`, measured; eye]
- Evidence: a dropdown that did not match the other controls; a new list screen that looked
  unlike the existing list screens; table headers that differed slightly between tables; the
  question whether the design system's components were used at all.
- Judged: with `--baseline`, button, field, table header and table row signatures (height,
  radius, font) the baseline does not use fail. Without it, fields of one page that differ in
  height or radius fail, and header cells of different style fail.
- Exception: a variant the design system defines that the baseline happens not to show (pass a
  baseline that has it, or name the variant).

**C2. Underline marks a link in running text, and only there.** [`link-underline`, measured]
- Evidence: names in a team list were underlined.
- Judged: an underlined link outside a paragraph (list, table, nav, card) fails, and so does a
  link inside a paragraph that looks like the text around it.

**C3. Color carries meaning.** [`no-color`, warn; eye]
- Evidence: screens with almost no color, where status, the current step and the primary action
  were all gray.
- Judged: no element with a non-gray color is a warning. By eye: status (ok, warning, error),
  the current step and the primary action use the design system's colors.

**C4. A glyph or icon alone is not a label or a status.** [`bare-glyph`, measured]
- Evidence: "…" and "!" marks whose meaning the reviewer had to ask about.
- Judged: an element whose whole text is `…`, `...`, `!` or `?`, or an icon standing alone,
  with no accessible name or tooltip. A status says its state in words.

**C5. An action names its object.** [`generic-action`, warn]
- Evidence: a "new" button at the top right of a page that did not say what it creates.
- Judged: a button or link whose whole label is a bare verb ("New", "Add", "새로 만들기").
  Say "New agent", or put the button where its object is obvious.

**C6. A toggle is a switch.** [`toggle-as-button`, measured; eye]
- Evidence: an on/off setting drawn as a button labeled "On" next to a separate save button.
- Judged: a button or switch whose label is a state word ("On", "Off", "켜짐", "꺼짐") and that
  draws no track or thumb fails, whatever its ARIA role: the role serves screen readers, not what
  people see. By eye: the switch sits at the right edge of its row
  and applies at once or saves with its block, never with a save button of its own.

## Flow and structure

**F1. People do not see steps the system does.** [scenario]
- Evidence: setup screens that listed creating an app or registering an address as the user's
  steps when the system does them.
- Judged: each step in the walkthrough is marked "person" or "system"; a system step shown as a
  task fails. Show its result or its failure, not the step.

**F2. One choice, one place.** [scenario]
- Evidence: two options that turned on the same thing under different words; a settings
  section that overlapped another menu.
- Judged: the walkthrough lists every option with what it changes; two that change the same
  thing, or a setting reachable from two menus with different controls, fail.

**F3. Many settings are grouped, not stacked.** [scenario, eye]
- Evidence: settings of several kinds laid out top to bottom on one page; things the system
  created mixed into the user's own settings.
- Judged: more than one kind of setting gets sections, tabs or a sub-menu; generated items get
  their own place.

**F4. A status says why somewhere else.** [eye; `row-height` shows the symptom]
- Evidence: the reason for a connection state written inside the state badge.
- Judged: the status is a short label; the reason and the fix go in a detail line, a tooltip or
  a callout under the block.

**F5. Use it before showing it.** [scenario]
- Evidence: a layout whose balance was off in a way that using it once would have shown.
- Judged: the walkthrough is done by actually clicking through the screen (or the mockup), not
  from the code.

## Adding a rule

When a person points out a UI problem that no rule covers, or one a rule should have caught:

1. Fix the screen first.
2. A rule that holds for any product goes here, by PR to this skill: the rule as one sentence,
   the evidence in general terms (what was pointed out, not who said it or where), how it is
   judged, the exceptions. The person's own words, when worth keeping, go to
   `~/.config/agent-skills/ui-review/evidence.md`, outside the repo. A measurable rule gets a check in `scripts/measure.js` and a failing
   case in `scripts/testdata/fail.html`, so the test proves it fires.
3. A rule for one product (its tokens, a layout decision) goes to that product's repo, or to
   `~/.config/agent-skills/ui-review/rules.md` when it is the user's own. Read that file, when it
   exists, with this one at step 1 of the procedure.
4. A rule that kept missing a real problem gets its threshold or its judged set changed, not an
   exception.
