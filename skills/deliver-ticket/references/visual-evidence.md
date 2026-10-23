# Visual evidence

A PR that changes what a person sees ships with screenshots in its sticky comment, taken, uploaded
and laid out the same way on every card, and a verdict from the lead. This file is that procedure.

## When it is needed

- A change to layout, a component, styling, a theme token, copy shown in the UI, or a rendered
  docs page.
- A bug fix whose symptom was visual: the before and after shots, on the same viewport and theme.
- Not for a change with no rendered output (an API, a CLI, a migration), and not as a substitute
  for the E2E check of §5: a screenshot shows the page was drawn, not that the flow works.

## Taking the shots

Use the project's verification skill or harness when it reaches the screen (a `verify-*` skill,
its fixture and its login). Otherwise drive the running app with `ego-browser`, which already holds
the browser's sign-ins:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/shots.py" capture --url http://127.0.0.1:5173/settings \
  --name settings --out <evidence dir>
```

- Defaults: widths `1440,390` (desktop and phone), themes `light,dark`, full page up to 8000 px.
  `--widths 1280,390` and the like match a project whose harness uses other sizes; keep 390 for
  the phone. `--wait-for <selector>` holds the shot until the content is there.
- Files land as `<name>-<width>-<theme>.png`. Keep that form for every shot, including the ones
  you take by hand; the sticky table is built from it. Names use letters, digits, `-` and `_`
  only. A retake gets a trailing tag (`settings-390-dark-final`), not a new subject name; it
  replaces the untagged shot in the table, and one cell takes at most one retake.
- It prints `scrollWidth/innerWidth` for each shot and exits 1 when the page is wider than the
  viewport. Fix the overflow, or record why it is intended.
- Beyond the default view, shoot each state the change touches: empty, loading, error, signed out,
  long text, many rows. A state the shot cannot reach goes under "not covered".
- Put the evidence directory outside the worktree (a scratchpad or temporary directory), never in
  the repo, and register it once it is on the PR: `reap.py ledger add --kind evidence --path <dir>
  --pr owner/repo#N` (`reap-resources` "Janitor").

## Uploading them

GitHub has no public API for image uploads. The new-comment box on the PR page does upload, so the
script attaches the files there in `ego-browser`, reads back the `user-attachments` URLs, clears the
box and never submits the comment:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/shots.py" upload --repo owner/repo --pr N \
  --out <evidence dir>/urls.json <evidence dir>/*.png
```

- It needs the PR to exist and `ego-browser` signed in to GitHub as someone who can comment. No
  token or cookie goes on the command line or into the output.
- It checks each file first: an image type, at most 10 MB, a name in the form above. It exits 1 when any
  file came back without a URL, when GitHub marked an upload failed, or when the comment box could
  not be cleared; the last case leaves a draft on the page, so clear it before going on.
- On a private repo the images are visible only to people with access, same as the PR.
- When the upload cannot run (no `ego-browser`, no sign-in), report the screenshots as NOT VERIFIED
  with the local paths. Do not commit images to the repo to get around it.

## Putting them in the sticky comment

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/shots.py" table <evidence dir>/urls.json > <shots.md>
```

It prints a `### 스크린샷` section: one table per subject, widths as rows (widest first), light and
dark as columns, and any file outside the naming form as its own image. Append it to the body file
below the E2E results, add the verdict line, and post the whole body with `sticky-comment.sh` as
§5 says. An uploaded URL answers 404 until a posted comment references it, so check the images
load after the sticky is up, not before. The comment then reads:

```markdown
### 화면 판정 (head `abc1234`, 판정: lead)
- **VERIFIED** settings: 1440·390 × light·dark, 겹침·잘림·넘침 없음, 빈 상태 확인
- **NOT VERIFIED** 오류 상태: fixture로 재현하지 못함

### 스크린샷
…
```

The verdict holds for the head it was judged on. A commit that changes the rendered output needs new
shots and a new verdict; one that does not (a test, a comment) keeps them, and the verdict line says
which head it was judged on.

## Judging them

The judge is the lead or coordinator that owns the PR, or this session when no one is above it. It
is not the user: a PR does not wait for the user to look at screenshots. Judge each shot against:

- [ ] The layout is the one the ticket or the approved mockup describes: order, alignment, grouping.
- [ ] Nothing overlaps, is clipped, or overflows; no horizontal scroll at 390.
- [ ] Empty, loading and error states render on purpose, with their copy, not as a blank area.
- [ ] Both themes are legible: contrast, borders and icons hold in light and in dark.
- [ ] The copy is right: wording, language, truncation, no placeholder or debug text.

A failed item goes back to the card as a fix, with the shot it failed on. When every item passes,
write the verdict line above.

A screen the user asked to see as a mockup before it is built gets that approval before
implementation, as its own step. The screenshots of the finished PR are still judged here, against
the approved mockup.
