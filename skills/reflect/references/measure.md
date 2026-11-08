# Measure mode

Numbers for one repo and one tracker project over a window, computed from the tracker, GitHub
and this machine's transcripts. The script only reads. Run it in a subagent that returns only
the report, so the issue lists and script output stay out of the caller's context.

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
  [--usage-match <dir fragment>] [--program <slug>] [--json out.json]
```

- `--issues` accepts a top-level list, `{"issues": [...]}` or `{"result": {"issues": [...]}}`.
- `--tz` sets the report's local time (default UTC, `+00:00`); `--json` also writes the headline
  numbers as JSON.
- `CLAUDE_CONFIG_DIR` selects the transcript adapter root (default `~/.claude`). No matching usage is unmeasured, not zero.
- `--usage-match` picks the configured project directories to count tokens for
  (default: the repo name).
- `--program` names an orchestrate program. The tickets of its `spawned` ledger rows join the usage match, so a
  card whose worktree name lacks the repo name is still counted, and its `main_flake` events in the window are
  listed. Pass it whenever the window is a program's Close.

It prints a Korean markdown report. Put it in the project's note (`use-notes`), not only in chat;
a result for the owner is shown as an Artifact only (`use-notes`).

## What each number means

| Section | Definition | Read it as |
|---|---|---|
| 기준선 | Issues created up to `--baseline-until` (default: before the first 6h+ gap in creation times) | The plan as first designed. Pass the flag when the default cut is wrong |
| 파생 | Issues created after the baseline | Scope that arrived after planning. `follow-up` label / `파생:` first line counts how many were marked at filing |
| 파생÷완료 per 6h | Derived created ÷ issues completed in the block; blocks start at `--since` | Above 1 the backlog grows. A wave after each integration or QA pass is the pattern to look for |
| 머지 PR | PRs merged in the window | Accepted changes |
| 재작업 | Commits with an author date after the PR opened (survives rebase); PR-event CI runs on the PR's branch between its open and merge | How much work happened after "ready" |
| escaped | Bug-labeled issues created, main push CI failures, revert PRs | Candidates only: read each Bug to confirm it came from merged code |
| 재실행 초록 | Main push runs whose attempt 1 failed and whose latest attempt is green (read per run from the attempts API), and the ledger's `main_flake` events | A red a rerun hid. `gh run list` shows only the last attempt, so the failure count above misses these; record each with `orch record <slug> main_flake --run ID --attempt N` |
| 비용 | Tokens from this machine's available transcripts in the window, per merged PR | No prices applied; other machines are missing |

Counts differ by ±1–3 from a hand count at window edges, and because the script counts every
issue, not only leaves.

## Report shape

Lead with the one number the user asked about, then the table, then what the numbers do not
cover (other machines, unmarked follow-ups, Bug candidates not yet confirmed, and close times
approximated from `updatedAt` when the issues came from `orca linear`, whether or not the report
says so). Numbers come from the script output, not from memory of the run. Write the prose
around them per `write-plainly`.
