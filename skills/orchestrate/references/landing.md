# Landing: parallel by default, exclusive where a merge can break what it does not touch

Development, review, CI and landing all run in parallel. Each worker lands its own PR through `orch land` the moment it is ready, whether or not the PR is behind its base. Only changes that can break a merge they do not textually touch take the **exclusive lane** and land one at a time on the latest base. No coordinator hands out merge slots.

This design replaced three that failed on a real program (2026-09-23/24):

- **A coordinator that granted each merge.** It became the slowest part of the system. Ready PRs waited on the coordinator's turn, not on CI.
- **"Whoever is ready lands next."** A migration chain waited more than five hours while eleven newer, easier PRs landed ahead of it. The priority and dependency order existed only in the coordinator's head.
- **One baton, strict up-to-date branches.** Every landing put every other PR behind, which meant a rebase and another 30–40 minute CI run each. The user's verdict: "동시에 여러 pr 을 빠르게 머지하면서도 적절히 안정적으로… 1개씩 확인하고 머지하는건 너무 별로야". They turned off "require branches to be up to date" and kept the required checks. Landings went parallel.

"CI green is not a verdict." A verdict holds across a rebase only while the patch-id is unchanged.

## Repository settings this assumes

- Branch protection keeps the required checks, with **"require branches to be up to date" off**. No admin bypass.
- Squash merges. Squashing did not cause conflicts; strict mode plus serial landing did.
- If a repo keeps strict mode on, GitHub reports behind PRs as `BEHIND`. `land` then asks for `gh pr update-branch` before merging, which is correct but slow. Say so in the program note's digest.

## Two lanes

**Normal lane** (most PRs). `land` merges as soon as all of these hold at the current head:

- a passing verdict whose patch-id matches the PR now
- CI completed and green. No checks yet counts as pending, and a green check is not proof, so read the logs of the runs it prints.
- no conflict, not a draft
- its dependencies landed
- main is not red

Behind is fine. The one mechanical self-check: if the base changed any file this PR also changes since the PR branched, `land` exits 3 and asks you to `gh pr update-branch`, re-check your contract and test assumptions, and let CI run again. Before calling `land`, the worker also spends a minute on the judgment the tool cannot make. Run `git diff --name-only <CI base>..origin/main` and ask whether any of it touches a contract or a test premise this PR relies on.

**Exclusive lane.** A PR is in it if any of these hold:

- it touches `exclusive_paths`. The defaults are `*migrations/*`, `.github/*`, `*Dockerfile*` and `*compose*.y*ml`. Add shared contracts per program with `orch set <slug> exclusive_paths "a/*,b.md"`.
- its class is `gate`

The lane works like this:

- One PR per repo base at a time, across every program on the machine. The lock lives in `~/.claude/programs/_locks/`.
- Among waiting exclusive PRs, the land order decides who takes the lane next.
- The holder keeps the lane from its first turn through the update, the restack (migrations) and CI, until it lands. Each attempt refreshes the lock. A holder idle for `exclusive_stale_minutes` (90) is presumed dead, and its lock is broken.
- It lands only on the latest base: not behind at all.

Normal-lane PRs never wait for the exclusive lane.

Proposals to serialize the normal lane for safety are rejected by default ("one CI at a time", "land one per N minutes"). They kill throughput, and GitHub already queues CI jobs. The safety comes from the self-check and the exclusive lane.

## The land order

`orch queue <slug>` prints the order and why each PR is not moving. `orch status` prints its first line. The order decides who takes the exclusive lane, and what the coordinator unsticks first.

1. **Class:** `main-fix` → `gate` → `urgent` → `normal`. `record reprioritized --pr N --class urgent` moves a PR.
2. **Aging.** A PR that has waited `aging_hours` (default 2) ranks with urgent ones.
3. **Unblocking.** The PR that more open tickets depend on goes first, then the one that has waited the longest.

Each entry is in one of four states:

- `ready`
- `catching_up`: CI pending, or behind on a strict base
- `blocked`: conflicts, CI failed, draft, no verdict, protection
- `waiting`: on a dependency, on its stack's top, or on the human gate

## Dependencies: start-after or land-after

Two kinds of edge, declared in two places. Pick per edge:

| B needs A… | Edge | Where |
|---|---|---|
| **before B can start**: A's result on main, A's report, an API A creates that B cannot stub | start-after | Orca task deps. Orca keeps B out of `task-list --ready` until A's task completes |
| **only to land first**: B can be written on top of A's branch now | land-after | A GitHub stack (B on A's branch) plus `orch dep <slug> --ticket B --after A`. No Orca dep: it would hold B back until A had landed, and the chain could never ride one merge |

Start-after edges in Orca:

- `task-create --deps '["<task id>", …]'` or `worker-start --deps`, and start work from `task-list --ready`.
- `orch` reads the Run's task deps and keys them by the ticket ID that starts each task's display name. A dependent PR `waiting` in the land order ("lands after X") is the same edge Orca holds.
- Deps are immutable once a task exists. To add a prerequisite to a task that has no dispatch yet, create a new task with the full deps list and retire the old one: `task-update --status failed --result '{"superseded_by":"<new id>"}'`. `orch` skips failed tasks.
- A dependency found after dispatch cannot be added in Orca. Record it with `orch dep <slug> --ticket A --after B`.
- A condition that needs judgment ("after the human checks X") is an Orca gate (`gate-create`), not a dep.

## GitHub stacks: several PRs, one merge

When PRs must land in order (a dependency chain, consecutive migrations, layers of one feature), make them a GitHub stack. The top layer's CI tests the whole chain, and `orch land <slug> --pr <top>` merges every layer in one `merge-async` call.

- Create it with `gh stack link <bottom> <top> --base main`, or `gh stack init` / `add` / `submit`. Confirm it with `gh api repos/<owner>/<repo>/pulls/<top> --jq .stack` (`gh pr view` does not show stacks, and `gh stack view` needs a locally tracked stack).
- Bring a stack onto the latest base bottom-up: `gh pr update-branch` merges a PR's own base into it, so on the top it pulls only the layer below. `land` prints the sequence.
- A lower layer is `waiting` ("lands with its stack from #top") while the top is not blocked. The top is `ready` only when every open layer below it is ready at its own head. A blocked top releases the lower layers to land alone.
- Main CI runs once, on the top's merge commit. Record `main_green` or `main_red` for that sha only; the lower commits have no run of their own.
- The layers of one chain count once against the concurrency cap, since they land as one merge. Start the whole chain together, even in the pilot.
- Stacked PRs refuse `gh pr merge`. `land` uses `merge-async` and polls until every layer is MERGED. If any layer is exclusive, the stack takes the exclusive lane as one unit.

## Feature integration branches

A series of cards reworking one area works on `feat/<topic>`, branched from main and unprotected. Sub-PRs take it as their base and land into it with the same `orch land` (lanes are per base). The branch merges `origin/main` periodically. When the series is done, one `feat/<topic>` → main PR lands.

## What a worker does: `orch land <slug> --pr N [--wait-minutes 50]`

Run it under `run_in_background` with `--wait-minutes`. It sleeps through yields and returns when something needs you.

| Exit | Meaning | Do |
|---|---|---|
| 0 | Landed. It printed the merge commit and the main-CI watch command. | Watch main CI, `record main_green\|main_red --sha`, finish the ticket, `worker_done` |
| 2 | Yield: CI still running, a dependency or the stack top first, or another PR holds the exclusive lane | Nothing. Keep the `--wait-minutes` loop |
| 3 | Act: the message says what (conflicts, failed CI, a changed patch that needs re-review, the base changed your files, or "you hold the exclusive lane: update onto the latest base") | Do it, then `land` again |
| 1 | Refused: STOP line, main red (only `--class main-fix` lands), or the human gate | Report and stop |

Rebase conflicts change the patch-id. That voids the verdict, so the resolution gets re-reviewed.

## Main red: the guardian decides, the coordinator coordinates

A standing **main guardian** worker (`references/roles.md`) owns red main. It re-runs a failed main run once and reads the logs.

- **Flake.** Record it in the program note's digest and freeze nothing.
- **Real defect.** It freezes landing (`orch record <slug> main_red --sha <merge commit>`) and narrows the culprit to a commit since the last green. It then chooses:
  - **hotfix** when the cause is clear and small, and the fix verifies in about 30 minutes
  - **revert** otherwise

  It never mechanically reverts a migration or a commit later PRs depend on. It lands the repair with `--class main-fix`, records `main_green`, and tells the culprit's card, the affected cards and the coordinator.

## What the coordinator does

- Declare start-after edges as Orca task deps when creating tasks; plan land-after chains as stacks from the start, with `orch dep`.
- Read `status` on every drain:
  - `STALE` lines are PRs that have waited `stale_hours` (default 3). Each gets an action on the reason `queue` gives.
  - `LANDED-BUT-OPEN` lines are workers whose PR landed. Release them and remove their worktrees in the same turn.
- A landing backlog over `max_backlog_hours` means **stop starting, start finishing**.
- Merges you did not make: `land` records a PR that was merged outside it.
- Tune a program in `program.json` under `"landing"`: `aging_hours`, `stale_hours`, `max_backlog_hours`, `land_interval_minutes` (only the backlog estimate before two landings exist, never a pacing limit), `exclusive_stale_minutes`, `exclusive_paths`, `heavy_slots`.
- Shared state is append-only (`ledger.jsonl`) or replaced whole: write a temp file and rename it. A shared JSON file overwritten in place was once left empty mid-write.

## human-gate

Workers stop at READY. `land` refuses them with exit 1, and they report. You open the gate with `orch gate <slug> --pr N`, which creates an Orca decision gate on a coordinator-owned Task. The user resolves it in Orca. You then run `orch land <slug> --pr N`: it sees the resolution, records `approved`, and lands. No worker settles that `Land #N` Task, so `land` closes it: completed when the PR lands, failed when the user resolved the gate as hold or the PR was closed. Gating the PR again makes a new Task.

## Permissions

Merging inside `orch land` is what lets a worker land without a per-merge approval prompt. The plugin's PreToolUse hook (`hooks/guard.py`) makes the permission decisions a program needs, for every session and subagent where the plugin is enabled:

- allow this plugin's skills, `orca orchestration <verb>` except reset, worker-abandon and gate-resolve, and `orch <subcommand>` except heavy, set, init and backfill (the checks above are the review gate)
- only for one simple command: an operator, redirection or substitution gets no decision
- deny reading another terminal's Orca mailbox

Everything else goes to the normal permission flow: the user's own deny and ask rules, then auto mode's classifier. A skill's `allowed-tools` cannot replace the hook, because it lasts only for the turn that loads the skill and a worker runs LAND from its brief hours later.

A worker that calls `gh pr merge` or `gh api … merge-async` by hand is merging outside the gate. The classifier rightly refuses that as "Merge Without Review": use `land`.
