---
name: swarm
description: "Fan out N parallel workers, drain them, and return one report. Use for /swarm, 'swarm this', or parallel coverage, races, gauntlets, and exploration."
---

# Swarm

Fan out N parallel workers. They may cover separate slices, race the same brief, or mix both. The parent waits, aggregates, and returns one report.

## Phase A: Frame

1. State the done predicate and the artifact or report the swarm must return.
2. Choose the shape. Partition into slices, race N workers on identical briefs, or mix both. For a race or mixed shape, declare `first pass`, `rank all`, or `best-of` before spawning.
3. Set N from the user or derive it from the shape. N is total workers, not the concurrency limit.
4. Pick the worker model up front: an `Agent` worker takes `model: "sonnet"`, `"opus"`, or `"fable"`; an Orca worker takes `--model`. For a model race, name each arm's model up front. A Codex arm runs through `codex-companion.mjs task` (see the **interrogate** skill), one Codex job at a time.
5. Give each worker its own writable output when it writes. When workers verify or measure commits, each brief names the exact SHAs. A measurement brief also names the method (sample count, what one sample is, order). The worker records both in its result.

## Phase B: Fan out

Spawn all N workers in one message with the `Agent` tool: `subagent_type: "general-purpose"`, `isolation: "worktree"`, `run_in_background: true`, and the chosen model. Drop `isolation` only when the worker reads and never writes. When workers must outlive this session or run long enough to need their own terminals, start them as Orca workers instead: read `orca skills get orchestration` first, then run `orca orchestration worker-start` once per worker.

When a worker must start from a non-default pushed branch, name it in the brief and have the worker check it out in its worktree first, or pass `--base-branch` to `orca orchestration worker-start`. `--base-branch` only works with `--worktree new-top-level` or `new-child`, since it names the base of the worktree Orca creates; Orca rejects it for `current` or an existing worktree.

Every brief stands alone. Include the goal, scope, exact slice or race arm, how to verify, and what to report. Reports use `PASS`, `ISSUES`, or `BLOCKED` with evidence. A worker that can prove a defect reports `ISSUES` and lists every issue it can prove, not only the first.

If a worker drops out, proceed with N-1 and note it.

## Phase C: Aggregate

Read the terminal results. Drop a result that does not record the SHAs and method its brief names, and rerun that worker once. After a second miss, record a gap. A gap does not count as a pass. For coverage, every required slice needs a result. For a race, apply the selection rule declared up front. Use first pass, rank all, or best-of. Do not paste raw worker dumps.

Keep a compact result table, one-line evidenced issues, and explicit gaps or dropouts.

## Phase D: Report

Return one consolidated in-chat report with the table, issue one-liners, gaps or dropouts, and the race rule when used.
