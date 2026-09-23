# Landing: parallel development, one priority-ordered line into each base

Development, review, conflict fixes and CI all run in parallel. Only the merge into a base branch is serial, because strict branch protection makes every landing push the others behind. Each worker lands its own PR through `prog.py land`. That command is the lock, the line and the checks in one, so no coordinator has to hand out merge slots.

This design replaced two that failed on a real program (2026-09-23):

- **A coordinator that granted each merge.** It became the slowest part of the system. Ready PRs waited on the coordinator's turn, not on CI.
- **"Whoever is ready lands next."** A migration chain (288 → 278 → 252) waited more than five hours while eleven newer, easier PRs landed ahead of it. Each of those landings put the chain's root behind again and cost it another CI cycle. That is starvation, and the priority and dependency order existed only in the coordinator's head.

The rules below come from pstack Shipping (MIT, Lauren Tan). "CI green is not a verdict." A verdict holds across a rebase only while the patch-id is unchanged.

## The land order

`prog.py queue <slug>` prints the order and why each PR is not moving. `prog.py status` prints its first line.

1. **Class:** `main-fix` → `gate` → `urgent` → `normal`. `record reprioritized --pr N --class urgent` moves a PR.
2. **Aging.** A PR that has waited `aging_hours` (default 2) ranks with urgent ones. Nothing starves behind a stream of fresh PRs.
3. **Unblocking.** Within a class, the PR that more open tickets depend on goes first. Dependencies are `prog.py dep <slug> --ticket A --after B`, recorded when you spawn A. Then waiting the longest.

Each entry is in one of four states:

- `ready`
- `catching_up`: behind, or CI pending
- `blocked`: conflicts, CI failed, draft, no verdict, protection. It is passed over and never holds the line.
- `waiting`: on a dependency, on its stack's top, or on the human gate.

The first `catching_up` PR holds the line for `reserve_minutes` (default 40) from its lander's last attempt. It needs one update and one CI cycle without being pushed behind again. A dead lander loses the reservation by itself.

## GitHub stacks: several PRs, one merge

When PRs must land in order (a dependency chain, consecutive migrations, layers of one feature), make them a GitHub stack. Don't land them one by one with a rebase and a CI cycle between each. The top layer's CI tests the whole chain, and `prog.py land --pr <top>` merges every layer in one `merge-async` call.

- Create it with `gh stack link <bottom> <top> [--base main]`, or `gh stack init` / `add` / `submit`.
- Confirm it with `gh pr view` (stack icon) or `gh api repos/<o>/<r>/pulls/<n> --jq .stack`.
- In the land order a lower layer is `waiting` ("lands with its stack from #top") while the top is not blocked. The top is `ready` only when every open layer below it is ready at its own head.
- A blocked top releases the lower layers to land alone.
- Two independent READY PRs can ride one merge the same way: stack B on A (rebase onto A's branch, `gh stack link A B`, push). When B's CI is green, landing B lands both. `land` suggests this when it makes a PR yield to another ready one.
- Stacked PRs refuse `gh pr merge`. `land` uses `merge-async` and polls until every layer is MERGED.
- `gh stack merge` (v0.0.3) only prints guidance.

## Feature integration branches

A series of cards reworking the same area (a big refactor, a directory move) works on `feat/<topic>`, which is branched from main and unprotected. Sub-PRs take `feat/<topic>` as their base. They land into it with the same `prog.py land`, which uses a per-base lock and line, so they never wait for main. The branch merges `origin/main` periodically. When the series is done, one `feat/<topic>` → main PR takes its turn in main's line. main never holds a half-finished state.

## What a worker does: `prog.py land <slug> --pr N [--wait-minutes 50]`

Run it under `run_in_background` with `--wait-minutes`. It sleeps through yields and returns when something needs you.

| Exit | Meaning | Do |
|---|---|---|
| 0 | Landed. It printed the merge commit and the main-CI watch command. | Watch main CI, `record main_green\|main_red --sha`, finish the ticket, `worker_done` |
| 2 | Yield: not your turn, or CI is running on your held turn | Nothing. Run it again, or keep the `--wait-minutes` loop |
| 3 | Act: your PR needs something now. The message says what (conflicts, failed CI, a changed patch that needs re-review, or "your turn: update the branch") | Do it, then `land` again |
| 1 | Refused: STOP line, main red (only `--class main-fix` lands), or the human gate | Report and stop |

Readiness is re-checked at the current head, under the base's lock, right before the merge:

- a passing verdict whose patch-id matches the PR now
- CI completed and passing (read the logs of the runs it prints; a green check is not proof)
- not BEHIND or DIRTY
- every lower stack layer the same

Rebase conflicts change the patch-id. That voids the verdict, so the resolution gets re-reviewed. Past greens and earlier heads are not evidence.

Do not update your branch while you are yielding. Another landing would push it behind again and spend the CI budget. `land` tells you when it is your turn.

## What the coordinator does

- Record `dep` edges when spawning, and pass the same edges to Orca as `worker-start --deps`.
- Plan dependent chains as stacks from the start. The brief says "stack on #N".
- Read `status` on every drain. `STALE` lines are PRs that have waited `stale_hours` (default 3). Each one gets an action on the reason `queue` gives: a fix task, stacking the chain, reprioritizing, or releasing a blocker. More parallel work does not fix a stale PR.
- A landing backlog over `max_backlog_hours` (default 2) means **stop starting, start finishing**. `status` says so, and spawning waits.
- Red main: the one fix task lands with `--class main-fix`, then `record main_green --sha`.
- Merges you did not make: on each wake, `gh pr list --state merged --search "merged:>=<last drain>"`. `land` also records a PR that was merged outside it.
- Tune a program in `program.json` under `"landing"`: `aging_hours`, `stale_hours`, `reserve_minutes`, `max_backlog_hours`, `land_interval_minutes`, `lock_stale_minutes`.

## human-gate

Workers stop at READY. `land` refuses them with exit 1, and they report. You open the gate with `prog.py gate <slug> --pr N`, which creates an Orca decision gate on a coordinator-owned Task. The user resolves it in Orca. You then run `prog.py land <slug> --pr N`: it sees the resolution, records `approved`, and lands in order.

## Permissions

Merging inside `prog.py land` is what lets a worker land without a per-merge approval prompt. The allow rule covers `python3 ~/.claude/skills/orchestrate/scripts/prog.py`, and the checks above are the review gate. A worker that calls `gh pr merge` or `gh api … merge-async` by hand is doing it outside the gate. The classifier rightly refuses that as "Merge Without Review": use `land`.
