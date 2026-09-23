# Landing: one steward, one PR at a time

The coordinator lands verified PRs itself. pstack Orchestrate allows exactly this ("Mechanically landing a verified unit … is bookkeeping the coordinator may do itself"), and one steward removes the slot locks, grant messages and timeouts that multi-card merging needs. Rules below follow pstack Shipping (MIT, Lauren Tan): "CI green is not a verdict", and a verdict holds across a rebase only while the patch-id is unchanged.

## On a worker_done

1. Validate it against the expected Dispatch (Orca skill), then release or reuse the worker before the ack.
2. `--outcome failed` → decide retry (smaller scope, different model, or park) and record why in `decisions.tsv`.
3. Succeeded → check the report's evidence against GitHub, not the prose: the PR exists, is not draft, and the head SHA matches. Then `prog.py record <slug> ready --pr N --ticket T`.
4. Record the verdict: `prog.py verdict <slug> --pr N --source <who>`. `<who>` names the non-author that produced it: `codex-review` (the worker's review loop), `verifier:codex` (a verifier unit), or `live:<verify-skill feature>`. A verdict from the author alone is not a verdict.

## Landing loop

Pick the oldest ready PR; urgent defects first, but never hold a ready PR for an urgent one that is not ready.

```bash
python3 ~/.claude/skills/run-program/scripts/prog.py land-check <slug> --pr N
```

- `HOLD: behind base` → `gh pr update-branch N --repo R`, wait for CI, check again. If the patch-id changed (conflict resolution, a renumbered migration), the verdict is void: spawn a review or fix task.
- `HOLD: conflicts` → a fix task placed in that PR's worktree (`worker-start --worktree branch:<head branch>`), not your own edit.
- Migration numbering collisions: before granting, list the base branch's migrations and put the next free number into the fix task.
- `LAND:` → read the CI log lines it printed (a green check is not proof), then run the exact merge command. `--match-head-commit` makes GitHub refuse a head you did not check.
- If the repo has a merge queue, the merge command enqueues; watch the queue instead of re-merging.

After the merge:

```bash
python3 ~/.claude/skills/run-program/scripts/prog.py landed <slug> --pr N
gh run watch <main run id> --repo R --exit-status   # run_in_background
```

Exit 0 → `record main_green --pr N` (raises the cap). Non-zero → read the log; a real failure is `record main_red --pr N` and one fix task; a flake gets `gh run rerun --failed` and a second watch. Then update the ticket (Linear Done with a completion comment, if the worker did not) and remove the landed worktree when its worker is released.

## human-gate

`land-check` holds every PR until `prog.py record <slug> approved --pr N`, which you write only after the user's go for that PR. Keep the waiting list in the program note's digest.
