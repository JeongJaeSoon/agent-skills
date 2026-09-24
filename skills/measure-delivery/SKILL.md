---
name: measure-delivery
description: Use when asked how a project or autonomous run actually went — "성과 측정", follow-up/derived ticket growth after the initial design, whether the backlog is converging, accepted changes, rework, escaped defects, or token cost per merged PR — and at the Close of an `orchestrate` program.
---

# Measure delivery

Numbers for one repo and one tracker project over a window, computed from the tracker, GitHub
and this machine's transcripts. The script only reads.

## Get the issues

Every "done" count keys on the issue's close time, so where the issues come from decides how
exact those counts are:

| Source | Close time | Use when |
|---|---|---|
| `--project P` (reads through `use-tracker`'s `tracker.py`) | Jira: exact (`resolutiondate`). Linear: `tracker.py` goes through `orca linear`, which has no `completedAt`, so closed issues get `updatedAt`, and the report says it approximated | Jira, or a quick Linear read where an edited-after-close issue shifting a block is acceptable |
| `--issues F` with a Linear MCP `list_issues` result | exact `completedAt` / `canceledAt` | Linear, when the 6h blocks must be right |
| `--issues F` with `orca linear list-issues --json` output | `updatedAt`; the report says it approximated | only when MCP is unavailable |

For the Linear MCP file: `list_issues` with the project, `includeArchived: true`, limit 250,
`fields: [createdAt, updatedAt, completedAt, canceledAt, statusType, labels, description]`
(the script needs `createdAt` on every issue), following the pagination until done. Write
the combined array to a file in the scratchpad (e.g. `issues.json`). `tracker.py list` output
saved to a file also works as `--issues`.

## Run

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/measure.py" \
  --repo OWNER/NAME (--project P | --issues issues.json) --since 2026-09-21T00:00:00+09:00 [--until …] \
  [--baseline-until <end of the initial design batch>] [--tz +09:00] [--bug-label Bug] \
  [--usage-match <dir fragment>] [--json out.json]
```

- `--issues` accepts a top-level list, `{"issues": [...]}` or `{"result": {"issues": [...]}}`.
- `--tz` sets the report's local time (default `+09:00`); `--json` also writes the headline
  numbers as JSON.
- `--usage-match` picks the Claude project dirs / Codex session cwds to count tokens for
  (default: the repo name).

It prints a Korean markdown report. Put it in the project's note (`use-notes`), not only in chat.

## What each number means

| Section | Definition | Read it as |
|---|---|---|
| 기준선 | Issues created up to `--baseline-until` (default: before the first 6h+ gap in creation times) | The plan as first designed. Pass the flag when the default cut is wrong |
| 파생 | Issues created after the baseline | Scope that arrived after planning. `follow-up` label / `파생:` first line counts how many were marked at filing |
| 파생÷완료 per 6h | Derived created ÷ issues completed in the block; blocks start at `--since` | Above 1 the backlog grows. A wave after each integration or QA pass is the pattern to look for |
| 머지 PR | PRs merged in the window | Accepted changes |
| 재작업 | Commits with an author date after the PR opened (survives rebase); PR-event CI runs on the PR's branch between its open and merge | How much work happened after "ready" |
| escaped | Bug-labeled issues created, main push CI failures, revert PRs | Candidates only: read each Bug to confirm it came from merged code |
| 비용 | Claude and Codex tokens from this machine's transcripts in the window, per merged PR. Codex sessions count only the growth inside the window | No prices applied; other machines are missing |

Checked against a hand analysis of a real three-day program: the ratio and the 6h blocks
matched. Counts differ by ±1–3 at window edges and because this script counts every issue, not
only leaves. GitHub caps one run query at 1000 results; the script splits the window until each
query fits, so on a busy repo it makes several calls.

## Report shape

Lead with the one number the user asked about, then the table, then what the numbers do not
cover (other machines, unmarked follow-ups, Bug candidates not yet confirmed, and close times
approximated from `updatedAt` when the issues came from `orca linear`, whether or not the report
says so). Numbers come from the script output, not from memory of the run. Write the prose
around them per `write-plainly`.
