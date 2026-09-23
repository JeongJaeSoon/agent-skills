---
name: measure-delivery
description: Use when asked how a project or autonomous run actually went — "성과 측정", follow-up/derived ticket growth after the initial design, whether the backlog is converging, accepted changes, rework, escaped defects, or token cost per merged PR — and at the Close of a run-program.
---

# Measure delivery

Numbers for one repo and one Linear project over a window, computed from Linear, GitHub and local transcripts. The script only reads.

## Get the issues first

`completedAt` decides every "done" count, and `orca linear list-issues` does not return it. Fetch with the Linear MCP instead and save the raw result:

- `list_issues` with the project, `includeArchived: true`, limit 250, following the cursor until done.
- Write the combined array to a file in the scratchpad (e.g. `issues.json`).

An `orca linear list-issues --project <p> --include-archived --json` file also works; the report then says completion times are approximated from `updatedAt`.

## Run

```bash
python3 ~/.claude/skills/measure-delivery/scripts/measure.py \
  --repo OWNER/NAME --issues issues.json --since 2026-09-21T00:00:00+09:00 [--until …] \
  [--baseline-until <end of the initial design batch>] [--bug-label Bug] [--usage-match <dir fragment>]
```

It prints a Korean markdown report. Put it in the project's Obsidian note (`use-obsidian`), not only in chat.

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

Reproduced on agent-platform 2026-09-21~23: derived 94, completed 84, ratio 1.12, and the 6h blocks match the hand analysis. Counts differ by ±1–3 at window edges and because this script counts every issue, not only leaves. GitHub caps one run query at 1000 results; the script splits the window until each query fits, so on a busy repo it makes several calls.

## Report shape

Lead with the one number the user asked about, then the table, then what the numbers do not cover (other machines, unmarked follow-ups, Bug candidates not yet confirmed). Numbers come from the script output, not from memory of the run.
