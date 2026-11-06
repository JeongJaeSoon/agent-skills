# Landing: parallel by default, exclusive where a merge can break what it does not touch

Development, review, CI and landing all run in parallel. Each worker lands its own PR through `orch land` the moment it is ready, whether or not the PR is behind its base, then closes itself. The lead holds the judgment and the order, not the merge. Only changes that can break a merge they do not textually touch take the **exclusive lane** and land one at a time on the latest base. No lead hands out merge slots.

This design replaced three that failed:

- **A lead that granted each merge.** It became the slowest part of the system. Ready PRs waited on the lead's turn, not on CI.
- **"Whoever is ready lands next."** A migration chain waited more than five hours while eleven newer, easier PRs landed ahead of it. The priority and dependency order existed only in the lead's head.
- **One baton, strict up-to-date branches.** Every landing put every other PR behind, which meant a rebase and another 30–40 minute CI run each. Turning off "require branches to be up to date" while keeping the required checks made landings parallel.

"CI green is not a verdict." A verdict holds across a rebase only while the patch-id is unchanged.

One PR's own facts (state, draft, merge state, CI, verdict against the patch, STOP, hold) are classified in one place, `scripts/landjudge.py`. `land-check` lists its reasons and the brief's "next" cell shows its first state. The program-wide checks below (main red, stacks, threads, evidence) stay with `land-check` and `land`.

## Repository settings this assumes

- Branch protection keeps the required checks, with **"require branches to be up to date" off**. No admin bypass.
- Squash merges. Squashing did not cause conflicts; strict mode plus serial landing did.
- If a repo keeps strict mode on, GitHub reports behind PRs as `BEHIND`. `land` then asks for `gh pr update-branch` before merging, which is correct but slow. Say so in the program note's digest.

## Two lanes

**Normal lane** (most PRs). `land` merges as soon as all of these hold at the current head:

- a passing verdict whose patch-id matches the PR now
- CI completed and green, with every workflow run at the head finished. No checks yet counts as pending (a repo with no workflows lands only through the no-CI lane below), and so does a rollup that holds only a bot's status or the first jobs of a run (jobs behind `needs` appear later). A green check is not proof, so read the logs of the runs it prints.
- zero unresolved review threads, read right before the merge; unreadable threads hold it
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
- The lock does not stop normal-lane PRs, from this program or any other, from moving the base. So the holder, or any PR whose acceptance needs a proof at its exact head (live check, E2E), cannot land while landings on its base come faster than one CI cycle or one proof. After its first exit 3 for a moved base, stop retrying: ask the layer above to hold other landings on that base for one CI cycle or proof plus land, and record the hold in decisions.tsv. This is a window for one PR, not a serialized lane.

Normal-lane PRs never wait for the exclusive lane. A `land` that ends at exit 1 frees its PR's own lock, and a merge refused inside the lane parks the PR (`record parked`), so the next PR takes its turn; `record admitted --pr N` brings it back.

Only a PR with a live `land` call takes a turn. Passed over, keeping its place in the order:

- a PR on hold (`record parked --pr N`), and the stack above it, since the merge would take that layer; `record admitted` brings it back. This ledger hold is the one `land` reads. The board's `todo`/`later` PR hold only quiets the owner's report.
- a READY-stop PR: one with a verdict and no `land` call, or whose last `land` ended at exit 1 (a human gate, a STOP line). It is merged outside `land` or is waiting, so a turn would go unused. `orch queue` marks it `READY-stop`.

When such a PR calls `land`, it contends from its old place, ahead of the PRs that passed it. Recording a hold does not free a lock the PR already has, because a `land` may be running under it: the yield says `on hold`, and that PR's next `land` or `exclusive_stale_minutes` ends it.

**No-CI lane.** For a repo with no CI at all. The human lists it in `~/.claude/agent-skills.json`:

```json
{"orchestrate": {"no_ci_repos": ["acme/app"]}}
```

The key is a user setting because `orch set` raises no dialog and a worker must not drop CI from its own landing. It goes through `skills-config`. A repo not listed, or a file that cannot be read, stays on the rules above. For a listed repo:

- A confirmed empty rollup and an empty workflow-run list count as nothing to wait for. A check that did report still counts, failed or pending, and a rollup `gh` did not return stays unknown.
- Every PR takes the exclusive lane, whatever its files: nothing tests the merge, so nothing else may move the base under it. This does not cover a person merging outside `land` in that window, which no client check can stop.
- `land` needs a passing verdict at exactly the head (same patch-id at another head is not enough), a head that contains its base (`compare` says behind by 0, the same answer as `git merge-base --is-ancestor`), zero unresolved threads, and the merge is pinned to that head (`--match-head-commit`, or `sha` on a stack's merge-async).
- So an update onto the base needs a new review of the new head and a new `orch verdict`.
- A stack lands here like any stack, and every layer it lands has to meet all of the above. A verdict, base or thread short on one layer holds the whole unit with nothing merged.
- No run proves main, so the lander runs the brief's VERIFY commands at the merge commit before it records `main_green` or `main_red`.

Proposals to serialize the normal lane for safety are rejected by default ("one CI at a time", "land one per N minutes"). They kill throughput, and GitHub already queues CI jobs. The safety comes from the self-check and the exclusive lane.

## The land order

`orch queue <slug>` prints the order and why each PR is not moving. `orch status` prints its first line. The order decides who takes the exclusive lane, and what the lead unsticks first.

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
| **only to land first**: B can be written on top of A's branch now, or B changes a path A changes (`orch overlap`) | land-after | A GitHub stack (B on A's branch) plus `orch dep <slug> --ticket B --after A`; `land` checks the two match ("Dependent PRs are a stack"). No Orca dep: it would hold B back until A had landed, and the chain could never ride one merge |

Start-after edges in Orca:

- `task-create --deps '["<task id>", …]'` or `worker-start --deps`, and start work from `task-list --ready`.
- `orch` reads the Run's task deps and keys them by the ticket ID that starts each task's display name. A dependent PR `waiting` in the land order ("lands after X") is the same edge Orca holds.
- Deps are immutable once a task exists. To add a prerequisite to a task that has no dispatch yet, create a new task with the full deps list and retire the old one: `task-update --status failed --result '{"superseded_by":"<new id>"}'`. `orch` skips failed tasks.
- A dependency found after dispatch cannot be added in Orca. Record it with `orch dep <slug> --ticket A --after B`.
- A condition that needs judgment ("after the human checks X") is an Orca gate (`gate-create`), not a dep.

## GitHub stacks: several PRs, one merge

When PRs must land in order (a dependency chain, consecutive migrations, layers of one feature), make them a GitHub stack. The top layer's CI tests the whole chain, and `orch land <slug> --pr <top>` merges every layer in one `merge-async` request. A workflow limited to PRs into the default branch (`on: pull_request: branches: [main]`) runs on every layer once they are a stack ([about stacked PRs](https://docs.github.com/en/pull-requests/get-started/about-stacked-prs): no run on the upper PR before linking, a run after, with its base still the lower branch). A PR merely based on the lower branch gets none of this, so `land` on a PR still based on a lower tracked PR's branch answers exit 3 with the `gh stack link` line instead of squashing it into that branch; `--onto-lower` folds it into the lower branch on purpose (top-down). `orch dep` is per ticket: the layers of one ticket's split get no dep (it would wait on itself, and `dep` refuses it); they land bottom-up in this order.

- Create it with `gh stack link <bottom> <top> --base main`, or `gh stack init` / `add` / `submit`. Confirm it with `gh api repos/<owner>/<repo>/pulls/<top> --jq .stack` (`gh pr view` does not show stacks, and `gh stack view` needs a locally tracked stack).
- A stack merges only as a linear chain ([merging stacked PRs](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/merging-stacked-pull-requests)). `gh pr update-branch` on a lower layer alone, with or without `--rebase`, makes the merge fail with `Stack needs to be rebased`; `gh pr update-branch <n> --rebase` on every layer from the bottom clears it, even with a merge commit already in the chain. `land` prints the sequence. A base that moved is no reason to do this: a stack behind its base still merges, so rebase when the repository requires it or a conflict does.
- A lower layer is `waiting` ("lands with its stack from #top") while the top is not blocked. The top is `ready` only when every open layer below it is ready at its own head. A blocked top releases the lower layers to land alone.
- Main CI runs once, on the top's merge commit. Record `main_green` or `main_red` for that sha only; the lower commits have no run of their own.
- The layers of one chain count once against the concurrency cap, since they land as one merge. Start the whole chain together, even in the pilot.
- On a repo whose ruleset dismisses stale approvals, merging a lower layer on its own dismisses the approval of every PR whose branch contains it: the retarget and the changed merge base both count, even with the head unchanged. Where a human approves each layer, map containment first (`git merge-base --is-ancestor`) and merge each chain from its top, which marks the contained layers merged. When a lower layer must merge alone, say in the first review request that the layers above will need approval again, and ask for those re-approvals once, bundled.
- `land` merges with [`merge-async`](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request-asynchronously), the only API the docs give for a stack: the synchronous endpoint "does not support stacked pull requests", and `gh pr merge` refuses every stacked PR, the last open layer too. It polls the request's uuid, not the PR. `failed` ends the land with GitHub's message. `enqueued` is the normal end on a merge queue branch and is not a merge: exit 2, and the `land` after the queue has merged it records every layer. A request still `pending` after 15 minutes ends the same way. If any layer is exclusive, the stack takes the exclusive lane as one unit.
- When a lower layer merges alone, GitHub moves the next layer onto the base and rebases it (`automatic_base_change_succeeded` and `head_ref_force_pushed` in its timeline). Its head changes, so CI runs again. Do it by hand only when neither event appears: [a report](https://github.com/github/gh-stack/discussions/463) saw no rebase through `merge-async`.
- Outside `land` the command is `gh stack merge <top> --squash --yes` ([reference](https://docs.github.com/en/pull-requests/reference/stacked-prs-cli-commands)), and never in a program.

### Dependent PRs are a stack

`land` and `land-check` compare the land-after edges (`orch dep`, Orca deps) with the real stacks. For two open PRs joined by an edge, on either PR's land:

| Found | warn (default) | deny | off |
|---|---|---|---|
| Not in one stack | a `warning:` line with the `gh stack link` command, the land goes on | exit 3 (`land-check`: HOLD) | nothing |
| In one stack, upside down: neither could ever land | exit 3 | exit 3 | exit 3 |
| The stack listing cannot be read | one warning line, the land goes on | same | same |

The level is `orchestrate.stack_dependent` in `~/.claude/agent-skills.json`; the owner promotes it (`skills-config`). `--class main-fix` is never held by it. The `hooks/stack_gate.py` hook reads the same key at `gh pr create`, where it reads the brief's `Lands after` and the branch's history and no stack exists yet to check. Two PRs that change one path and need nothing from each other are not an edge until the lead makes them one: `OVERLAP` lines stay what they are, a prompt to decide.

## Feature integration branches

A series of cards reworking one area works on `feat/<topic>`, branched from main and unprotected. Sub-PRs take it as their base and land into it with the same `orch land` (lanes are per base). The branch merges `origin/main` periodically, through a sync PR (main into `feat/<topic>`) that lands as a merge commit: `gh pr merge <N> --merge --match-head-commit <head>`, then `orch landed <slug> --pr <N>`. `orch land` always squashes, and a squashed sync drops main's history from the branch. A CI that runs on push only for the default branch starts no run when a sub-PR lands there, so after each landing start it with `gh workflow run <ci workflow file> --ref feat/<topic>` and watch that run before `record main_green`; `land` prints this line when the base is not `main` or `master`. Where the CI workflow has no `workflow_dispatch` trigger, that run cannot start: compare `git rev-parse <merge commit>^{tree}` with `<PR head>^{tree}`, and when they are equal and the base did not move between the head's CI run and the merge, `record main_green` with the head's run as evidence and a note that the merge itself got no CI. Every landing gets a `main_green` row or a recorded gap. When the series is done, one `feat/<topic>` → main PR lands.

## What a worker does: `orch land <slug> --pr N [--wait-minutes 50]`

Run it under `run_in_background` with `--wait-minutes`. It sleeps through yields and returns when something needs you. The land is the card's own job, approved in its first prompt: after it, record main and close by `end-session` §7. A card stops at READY only for a `READY-WAIT:` kind (`references/brief.md` LAND).

| Exit | Meaning | Do |
|---|---|---|
| 0 | Landed. It printed the merge commit and the main-CI watch command. | Watch main CI, `record main_green\|main_red --sha`, finish the ticket, `worker_done` |
| 2 | Yield: CI still running, a dependency or the stack top first, another PR holds the exclusive lane, or GitHub did not answer (timeout, 5xx) | Nothing. Keep the `--wait-minutes` loop |
| 3 | Act: the message says what (conflicts, failed CI, a changed patch that needs re-review, the base changed your files, missing evidence in the test-results comment, or "you hold the exclusive lane: update onto the latest base") | Do it, then `land` again. On "update onto the latest base", also ask for a hold before that update (Exclusive lane above), so the next attempt is not overtaken again |
| 1 | Refused: STOP line, main red (only `--class main-fix` lands), the human gate, or a merge GitHub or the no-CI lane refused | Report and stop with `READY-WAIT: land-refused`. No retry |

Rebase conflicts change the patch-id. That voids the verdict, so the resolution gets re-reviewed.

## Land executor

With `orchestrate.land_executor` on for the program's repo, `orch land` merges nothing. It records a request for the PR's current head in `agent-skills.db` (`land_request`) and waits up to `--wait-minutes` for the result. Exit codes stay those of the table above. The merge itself runs in the `land-executor` automation: an Orca automation whose precheck runs `land_exec.py round` every minute through `scripts/orca_job.py` and never starts an agent (`docs/automation.md`): no agent turn merges, so no merge reaches the auto mode classifier.

```json
{"orchestrate": {"land_executor": {"enabled": true, "repos": ["OWNER/*"], "exclude": [], "verdict_sources": ["review:codex"], "max_wait_hours": 24}}}
```

The owner sets the key (`skills-config`) and turns the automation on (`skills-sync setup enable land-executor` from the main checkout, which also records the `5-land-queue` step). With the key off, `orch land` merges in its own process. A settings file that names the key but cannot be parsed, or whose `max_wait_hours` is not a positive number of hours or `verdict_sources` not a list, stops `land` with exit 1 instead of falling back to a direct merge, and the executor refuses its queued requests the same way.

The scope (`skills/orchestrate/scripts/land_scope.py`, read the same way by `orch land`, watch-prs and the `gh pr merge` hook):

- `repos` takes `OWNER/NAME` for one repo and `OWNER/*` for every repo of that owner, new and renamed ones included. `OWNER/*` is the recommended value for your own account: a repo made or renamed later needs no edit. Names match case-insensitively. Any other wildcard (`*`, `*/*`, `OWNER/app-*`) is a settings error, never a wider match.
- `exclude` lists `OWNER/NAME` repos that stay out even when `OWNER/*` covers them. An excluded repo is off for the executor: `orch land` merges it directly, and a standalone card merges its own PR there. `orch land` decides executor or direct merge on the name GitHub gives the repo now (one `gh api repos/…` call), so a program made under an old name never falls to a direct merge; when GitHub does not answer, it yields (exit 2).
- A fork or an archived repo that only `OWNER/*` covers is out; list it by name to put it in. The round and the standalone program's creation check this (`gh api repos/OWNER/NAME`, REST, which follows a rename), so `orch land` still sends it to the executor, which refuses it, never to a direct merge.
- The scope is judged on the name GitHub gives the repo now. A rename inside `OWNER/*` changes nothing. An `OWNER/NAME` entry under an old name stops covering the repo until the new name is listed; an `exclude` entry under either name keeps it out. A repo transferred to another owner is refused unless the new name is covered. A repo the recorded name covers but GitHub's current name does not (a rename or transfer moved it out) is refused by `orch land` itself (exit 1), never merged directly; list the new name or exclude it. The `gh pr merge` hook finds the program that holds a PR under either name, so a program made before a rename keeps its gate.
- `OWNER/*` covers the `gh` account's own repos only (`gh api user`'s login). An org's `ORG/*` is refused: the round refuses its requests, the standalone program is never created, the hook treats it as out of scope, and drift warns about it. An org's repo gets in only as an `OWNER/NAME` entry, and even then the executor merges nothing GitHub blocks: a branch protection or ruleset that requires an approving review keeps the request waiting (`BLOCKED`, never `--admin`) until a person approves.

`orchestrate.land_route` (deny, warn or off; default deny) is `hooks/land_route_gate.py`. It reads a `gh pr merge` from an orchestrator, a lead, a worker or a session whose brief has a `PROGRAM:` line, while the executor is on. A covered repo is denied with the `orch land` line for the program that holds the PR (else the brief's PROGRAM, else `<name>-standalone --repo OWNER/NAME`) and the `orch verdict` line to run only after an independent review of that head. Only a covered repo is denied, plus a program's PR whose repo a rename moved out of the scope (`orch land` refuses it until the new name is listed). Your own repo that the scope leaves out passes with a note: the skills-config spec that adds it (that repo, or `OWNER/*`) and the `config_emit.py emit` line, or for a program's PR its `orch land` line, which merges directly. An org's or another user's repo out of scope passes with nothing said, so its human review and approval flow stays as it was. An excluded repo and a call with `--disable-auto` or `--help` pass. A call that changes directory before `gh pr merge` is denied until `--repo` names the repo. Settings that cannot be read deny; a PR GitHub does not answer for passes with one warning line. A merge inside a script, `bash -c` or `gh api` is not seen: the round's checks are what hold.

`skills-sync setup status` ends with `land_exec.py drift`'s warning lines while the executor is on: one line per owned repo with a PR of yours from the last 7 days that the scope leaves out, one per `repos` entry GitHub now names differently when no entry covers the new name, one per `ORG/*` the round refuses, and one line listing the covered repos (each `OWNER/*` owner's source repos that are not archived, and the named entries) with no `<name>-standalone` program. One line says so when GitHub does not answer.

Each round takes the queued requests oldest first. For each one, the first check that fails leaves the PR unmerged:

| Check | When it fails |
|---|---|
| The key is on and covers the repo, under the name GitHub gives it now; `OWNER/*` covers only the `gh` account's login, and a fork or archive only `OWNER/*` covers is out | refused, exit 1 |
| program.json is readable, names the same repo, and `merge_policy` is `autonomous` | refused, exit 1 |
| The request is younger than `max_wait_hours` | refused, exit 1 (expired) |
| The PR is open, not a draft, and its head is the requested sha | refused, exit 3 (head moved: review it, `orch verdict`, land again) |
| The latest verdict is a pass at exactly that sha, from a source in `verdict_sources` | refused, exit 3. A matching patch-id is not enough here: this is stricter than the direct path |
| That verdict row names the PR's author sessions, the reviewer, the evidence (a link or sha) and the recorder, and the reviewer is not an author session | refused, exit 3 (record it again with `orch verdict --reviewer <who> --evidence <link or sha>`). An orchestrator records a review someone else ran with `--reviewer <who ran it>`; the author sessions are the terminals of the card holding the PR's branch, read when the verdict is recorded. `orch verdict` stores a pass only when all four are present and none of the reviewer, the `--source` reviewer or the recorder is an author session, so a pass that fails these never reaches the ledger; `land-check` reports the same problem while the executor is on. A row recorded before this check, without the fields, is refused here: record it again for each PR still open |
| A `<!-- test-results -->` comment exists | refused, exit 3 |
| `mergeStateStatus` is not DIRTY or BEHIND | refused, exit 3 |
| GitHub's BLOCKED (a required review or check is missing) is not the only reason left | wait (queued until `max_wait_hours`) when it is: an approval or a finished check lands it without a new request. Any other reason (a failed run, a thread) still refuses first |
| Everything `orch land` checks (STOP, main red, hold, deps, stack order, human gate, evidence, lanes, base overlap, CI at the head, threads), with the merge pinned to the requested sha by `--match-head-commit` and a clean merge state | wait (queued, next round) or refused with land's own exit code |

Stacks and `--onto-lower` are refused (exit 1): the executor pins one PR at one sha, and merge-async would also take the layers below. The refusal says what to do. The bottom layer gets a `do:` line: `gh pr edit <layer above> --base <base> --repo <repo>`, then `orch land` again. The executor never retargets (it edits no other PR), and the retargeted layer's diff changes, so its patch-id no longer matches its verdict and it needs a new review. Any other layer is told to land the bottom first. A GitHub or stack lookup that does not answer (`gh api user`, the stack listing) keeps the request queued, never refuses it. Right before the merge call the latest verdict is read again, and one that changed during the checks (another source, another sha, a fail) refuses with exit 3; the request row records the verdict read there. A round first reconciles: a request that a dead round left running goes back to the queue, and a PR GitHub shows merged is recorded once (`landed`), not refused, before the setting or the expiry is read. The waiting `orch land` reads the outcome from the request row and exits with it; a merge is also in the program's ledger (`landed`). Each refusal goes once to the dashboard inbox (`머지 거절 <repo>#N (exit N): <reason>`), so one the requester stopped waiting for is still seen. A precheck has no sender terminal, so the executor sends no Orca mail. A round's last line and exit go to `<state>/jobs/land-executor.json`, which `setup-pc` checks for failures and stalls. Main CI after the merge stays with whoever watches it today (the worker after exit 0, or the guardian).

`orch land-exec queue` lists the requests with their reason and merge sha; each row keeps the verdict it merged on, the checked head and base sha, and the executor's git revision. The checks catch a skipped or wrongly recorded step. They separate who wrote, who reviewed and who recorded, so a plain self-review and an unattributable row do not merge, but they prove nothing against a forged claim: every agent runs as the same user with the same token and can write any verdict source or reviewer name. `land-exec` is left out of the hook's `orch` allow, and `round` refuses inside an agent session (`CLAUDECODE`); both are guardrails, not identity checks.

A standalone card does not land its own PR: it reports READY, and the orchestrator lands it through the repo's standing program `<name>-standalone` (`name` is the part of `OWNER/NAME` after the slash). `orch verdict` and `orch land` create that program when it is missing, as `autonomous`, if all hold: the command carries `--repo OWNER/NAME`, the executor covers the name GitHub gives that repo now, and the slug is `<current name>-standalone` (an old name's slug is refused with the right command). Its Run is `--run` when given, else the one Run the other `*-standalone` programs share, else a placeholder; it never records a lead. With the executor off nothing is created and the card merges its own PR. For example `orch verdict <name>-standalone --repo OWNER/NAME --pr N --sha <head> --source review:codex`, then `orch land <name>-standalone --repo OWNER/NAME --pr N --wait-minutes 50`; a program that exists is used as it is.

Main CI is every push-triggered run at the merge commit. Watch it with the lines `land` prints (`gh run list --commit <sha>`, then `gh run watch <id> --exit-status --interval 60` for each run id, one watcher per run), and record `main_green` or `main_red` only after every one has completed. A watcher's exit is not a finished run, and a failed lookup or API error leaves main unknown: `main_red` needs `gh run view <id> --json status,conclusion` to show `completed` with a non-success conclusion (the no-CI lane has no run: its VERIFY commands decide). A run cancelled by a newer push is no result; any other cancelled job is read as below before it counts. The "CI runs" ids `land` prints are the PR's runs, never main's. A watcher script of your own that parses run text can record red over green runs (a workflow name with a space shifts the columns); when one gives a false signal, fix or delete it in the same wake and record from the per-run conclusions.

`land` and `land-check` also read the PR's sticky `<!-- test-results -->` comment (`deliver-ticket` §5):

- A PR that changes a file matching `visual_paths` (default `apps/web/*`, `*.css`, `*.stories.*`, `packages/ui/*`) needs at least one image that comment renders: markdown `![](https://…)` or `<img src="https://…">`, outside code blocks. Text saying screenshots were taken does not count. Only a person exempts a PR, with the label `no-visual-change`; `land` then prints the exemption. A worker never adds that label: `land` sees only that the label is there, not who added it. The first comment carrying the marker counts, the one `sticky-comment.sh` updates.
- Every failed run (`failure`, `timed_out`, `startup_failure`) at one of the PR's commits needs a line in that comment naming the run id and what resolved it. A run re-run to green shows only its last attempt, and a run at a commit a rebase dropped is no longer the PR's, so neither is listed.

## Main red: the guardian decides, the lead coordinates

A standing **main guardian** worker (`references/roles.md`) owns red main. It reads red main as the paragraph after this list says, then reads the logs.

- **Flake.** Record it in the program note's digest and freeze nothing.
- **Real defect.** It freezes landing (`orch record <slug> main_red --sha <merge commit>`) and narrows the culprit to a commit since the last green. It then chooses:
  - **hotfix** when the cause is clear and small, and the fix verifies in about 30 minutes
  - **revert** otherwise

  It never mechanically reverts a migration or a commit later PRs depend on. It lands the repair with `--class main-fix`, records `main_green`, and tells the culprit's card, the affected cards and the lead.

The guardian, or with none running whoever is about to record `main_red`, reads red main this way first: re-run a failed run once, a run cancelled by a newer push is not red, a job cancelled with no newer push gets the same single re-run, and a deploy refused because another deploy was in progress gets one re-run before it counts. GitHub can report a job that hit its `timeout-minutes` as `cancelled`, so compare that job's duration with its limit: at or past the limit, it is a time budget running out, not an unrelated flake. If the re-run is green and every other main run at that sha has completed green, record `main_green` and put the timeout in the digest as LATER with its trigger; if the re-run also times out or fails, record `main_red`. A red only in a deploy or advisory job is recorded as that in the digest and freezes nothing. When a fix landed outside `land` clears main, the ledger's `main_red` still stops every lane: record the fix with `orch landed <slug> --pr <fix>`, then `orch record <slug> main_green --pr <fix> --sha <its merge commit>` once main CI at that sha is green.

## What the lead does

- Declare start-after edges as Orca task deps when creating tasks; plan land-after chains as stacks from the start, with `orch dep`.
- Read `status` on every drain:
  - `STALE` lines are PRs that have waited `stale_hours` (default 3). Each gets an action on the reason `queue` gives.
  - `LANDED-BUT-OPEN` lines are workers whose PR landed (a card spawned on the ticket after that landing is follow-up work and is not listed). Release them and remove their worktrees in the same turn.
  - `HELD` lines are PRs on hold (`record parked`). They do not count as stale, so they never block spawning; close a superseded one on GitHub so it leaves the land order.
- A landing backlog over `max_backlog_hours` means **stop starting, start finishing**.
- Merges you did not make: `land` records a PR that was merged outside it.
- `orchestrate.self_land` in `~/.claude/agent-skills.json` (`warn` by default, `deny` to enforce, `off`) sets what `hooks/self_land_gate.py` does when a card with a `PROGRAM` row and an APPROVED line naming `orch land` closes over its open PR.
- Tune a program in `program.json` under `"landing"`: `aging_hours`, `stale_hours`, `max_backlog_hours`, `land_interval_minutes` (only the backlog estimate before two landings exist, never a pacing limit), `exclusive_stale_minutes`, `exclusive_paths`, `visual_paths` (`orch set <slug> visual_paths a/*,b/*`), `heavy_slots`.
- Shared state is append-only (`ledger.jsonl`) or replaced whole: write a temp file and rename it, since a shared JSON file overwritten in place can be read empty mid-write.

## human-gate

Workers stop at READY. `land` refuses them with exit 1, and they report. You open the gate with `orch gate <slug> --pr N`, which creates an Orca decision gate on a lead-owned Task. The user resolves it in Orca. You then run `orch land <slug> --pr N`: it sees the resolution, records `approved`, and lands. No worker settles that `Land #N` Task, so `land` closes it: completed when the PR lands, failed when the user resolved the gate as hold or the PR was closed. Gating the PR again makes a new Task.

A gate answered hold becomes a held item naming the PR (`orch board later add "<work> <PR>" --trigger <condition>`), which the brief shows as ⏸️. Before gating that PR again, close the held item (`orch board done <later id>` or `orch board later drop <n>`): the brief reads a waiting gate as 🙋 and an approved one as 🔄 only once no held item names the PR, so a gate opened over a live held item leaves the row ⏸️ after approval. A gate the brief cannot confirm open or approved (not in the collector's open gates, no `approved` after its `gate_opened`) is ❔ until `orch land` reads its answer.

## Permissions

Merging inside `orch land`, or in the land executor where it is on, is what lets a worker land without a per-merge approval prompt. The plugin's PreToolUse hook (`hooks/guard.py`) makes the permission decisions a program needs, for every session and subagent where the plugin is enabled:

- allow this plugin's skills, `orca orchestration <verb>` except reset, worker-abandon and gate-resolve, and `orch <subcommand>` except heavy, set, init, backfill and land-exec (the checks above are the review gate)
- only for one simple command: an operator, redirection or substitution gets no decision
- deny reading another terminal's Orca mailbox

Everything else goes to the normal permission flow: the user's own deny and ask rules, then auto mode's classifier. A skill's `allowed-tools` cannot replace the hook, because it lasts only for the turn that loads the skill and a worker runs LAND from its brief hours later.

A worker that calls `gh pr merge`, `gh stack merge` or `gh api … merge-async` by hand is merging outside the gate. The classifier rightly refuses that as "Merge Without Review": use `land`.

Two things let a card merge, and they do different work. Its first prompt's APPROVED line is the human's approval: it tells the card the merge is its job, and a later message cannot stand in for it. The hook's allow is what reaches the runtime: `orch land …` as one simple command skips the classifier, so no autoMode rule is needed; with an operator or a redirection the allow is gone and the classifier judges the call. An autoMode rule (`tune-automode`) is a backstop, not the path. After the land the card closes its own terminal by `end-session` §7, and the cleaners take the worktree.
