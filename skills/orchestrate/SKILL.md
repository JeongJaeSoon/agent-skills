---
name: orchestrate
description: Use when one session must drive a project or milestone to done through several Orca workers — "PM 겸 오케스트레이터로 끝까지", 3+ tickets in parallel with their PRs landed, follow-up tickets kept from swallowing the plan, a progress dashboard — or when taking over ("resume") a program another coordinator ran, or asked why a program's PRs are not moving.
---

# Orchestrate a program

You own the program, not the code. You frame it, write briefs, drain the inbox, keep dependencies and the land order honest, and decide. This is pstack's Orchestrate (Lauren Tan) moved onto Orca. Orca already owns the mechanics: Run, Task and its deps, Dispatch, `check --wait`/ack, `ask`/`reply`, gates, the worker contract, recovery and release. This skill adds only the program layer.

**REQUIRED BACKGROUND:** at the start of every coordinator session, run `orca skills get orchestration` and read it. Every Orca command here comes from it; never compose one from memory.

## When not to use

- One agent could finish inside the budget → do it in this session, or give it to one card with `handoff-ticket`. A program costs a coordinator.
- A single ticket handed to another session → `handoff-ticket`.

## Where each fact lives

| Fact | Owner |
|---|---|
| Goal | Orca Run `objective` (`run-create`), shown as the dashboard headline |
| Standing orders, predicate, merge policy, decisions, human digest | Program note in the notes store (`use-notes`), template `references/program-note.md` |
| Tickets, their state, which are derived | The tracker, through `use-tracker` (`tracker.py`) |
| PRs, CI, merges, stacks | GitHub |
| Runs, tasks and their deps (the dependency graph), workers, questions, gates | Orca |
| Verdicts, lanes, land order, landings, main red/green, the concurrency cap, deps found after dispatch | `~/.claude/programs/<slug>/ledger.jsonl`, written only through `prog.py` |
| Briefs as sent | `~/.claude/programs/<slug>/briefs/<ticket>.md` |
| Decision trail | `~/.claude/programs/<slug>/decisions.tsv` (`show-me-your-work`) |
| What the human watches | `dash.py` dashboard (`references/dashboard.md`) |
| Who does what beside the ticket workers | Standing roles: main guardian, QA lead (`references/roles.md`) |

`prog.py` means `python3 ~/.claude/skills/orchestrate/scripts/prog.py`, and `dash.py` sits next to it. Run either with no arguments for usage.

## Steps

1. **Frame.**
   - Read every ticket in scope.
   - Write the predicate as countable ticket IDs plus a final check on the real artifact ("A-246, 247, 117 Done and `verify-<app>` drives the quickstart on main"). Derive it yourself. If the tickets have no countable end, write your best predicate, mark it proposed in the digest, and go on.
   - Copy the human's goal into the standing orders verbatim, one sentence per order.
   - Map the dependencies between tickets and split them by kind (`references/landing.md`): **start-after** (B needs A's result before it can begin) becomes an Orca task dep; **land-after** (B can be built on A's branch now) becomes a GitHub stack plus `prog.py dep`, never an Orca dep.
   - Check that the repo can land in parallel: required checks on, "require branches to be up to date" off, squash merges. Add the program's shared contracts to `exclusive_paths`. If strict mode has to stay, put its cost in the digest.
   - Pick the merge policy.
   - Create the Run with the goal as `--objective`.
   - `prog.py init <slug> --repo … --run … --tracker-project … --predicate <IDs> --final-check "<the real-artifact check>" --note <program note path>`
   - `dash.py serve` in the background, unless one is already running (it serves every program; this one appears in its sidebar). Give the human the URL once.
2. **Verification first.** If the target repo has no `.claude/skills/verify-*`, the pilot task is "read and follow `~/.claude/skills/create-verification-skill/SKILL.md`".
3. **Pilot.** Run one worker through brief → PR → verdict → `prog.py land` → main green. Fix the brief, the unit size and VERIFY from whatever broke. Also confirm that the chosen worker model gets through its first `orca orchestration` call and its `prog.py land` without a permission prompt.
4. **Scale.**
   - Spawn the standing roles (`references/roles.md`): a main guardian, and a QA lead once the first tickets land. Record each with `prog.py record <slug> spawned --role <guardian|qa> --note <dispatchId>`; roles do not count against the cap.
   - Then spawn ticket workers up to the cap that `prog.py status` prints. The cap starts at 1, grows by one per landing proven green on main, and halves on red.

   For each ticket worker:
   - Write the brief per `references/brief.md`. The spec starts with the ticket ID, never `/goal`.
   - Create its task with its start-after prerequisites as Orca deps (`task-create --deps '[…]'`), and start what `task-list --ready` offers. A land-after layer starts at once, stacked on the lower layer's branch, with `prog.py dep`. A prerequisite found after dispatch goes in `prog.py dep`.
   - Run `worker-start --task <id> --worktree new-top-level --repo <selector> --base-branch <main, feat/<topic> or the lower layer's branch> --name <ticket id, lowercase> --display-name "<ID> <title>" --agent claude [--model <id>]`, or `--spec "<the brief>" --deps …` instead of `--task`. The model is the program note's worker model (default: the coordinator's own); a verifier runs on another family (`--agent codex`). The display name is the card's durable name; the terminal tab title is the agent's own and it overwrites any rename.
   - Close its setup terminal once setup exits. In `orca terminal list --worktree <card> --json` it is the row without `agentIdentity`: run `orca terminal wait --terminal <h> --for exit`, then `orca terminal close --terminal <h>`. Finished setup terminals left open made Orca itself slow (22 of 50 terminals in one run).
5. **Drain.**
   - Wait only with `prog.py wait <slug>` under `run_in_background`. It wakes on worker_done, escalation or question, and acks batches that hold only heartbeats. Keep exactly one wait running.
   - Process every message in the batch, decide each settled worker's next owner (reuse or release), then ack.
   - On a worker's `worker_done` after landing, in the same turn: `worker-release`, close its terminals, and `orca worktree rm` it (checks in `end-session` §4). `status` lists any you missed as `LANDED-BUT-OPEN`. Leftover cards made Orca itself slow.
   - A start that ended `outcome_unknown` with an empty composer: `worker-stop --dispatch <id>`, then `worker-start --retry-of <id> --task <task> --worktree <that card> --agent <agent> [--model <id>]` (a retry inherits neither placement nor model).
   - End every drain with `prog.py status`; its lines are how a drain ends. `dash.py collect <slug>` runs after it, and `dash.py note` records a risk or decision the dashboard should show.
   - Under `/goal`, a running background task defers the Stop hook's goal check. If the hook re-prompts anyway with no new event, answer in one line with no tool call. If it fires back-to-back, switch to a foreground `prog.py wait <slug> --rounds 1 --timeout-ms 540000` (Bash timeout 600000).
6. **Triage.** A new ticket from review, QA or discovery parks by default: label `follow-up`, then `prog.py record <slug> parked --ticket X`. Admit it only if it blocks a named predicate item, or if it is a reproduced correctness, security or data defect in code this program merged. Then `record admitted` and name the item or defect.
7. **Land** (`references/landing.md`). Workers land their own PRs with `prog.py land`:
   - Normal PRs land in parallel the moment they are ready.
   - Migrations, CI files and other `exclusive_paths` land one at a time, on the latest base.
   - Dependencies land first.

   You keep it honest:
   - start-after edges in Orca, land-after chains stacked with `prog.py dep`
   - `STALE` PRs unstuck
   - classes set (`record reprioritized`)

   You land only under human-gate, after the Orca gate resolves.
8. **Verify main.**
   - The lander watches its merge's main CI and records `main_green`.
   - Red main belongs to the main guardian: flake check, freeze, hotfix or revert, notify (`references/roles.md`). While main is red, only `--class main-fix` lands.
   - The QA lead verifies each landed ticket and audits design against code. It also runs the E2E suite on main every 5 landings, every 2 h, and before each gate PR.
9. **Close.**
   - When `status` says the tickets are done, run the predicate's final check on the real artifact (the QA lead drives `verify-<app>` on main).
   - `prog.py record <slug> predicate_verified --note <evidence>`.
   - Release the standing roles: `orca orchestration send --to dispatch:<role> --subject release --body "program closing: send worker_done"`, then `worker-release` once its `worker_done` arrives (Orca releases only settled workers). Release any remaining workers and remove any worktree still left (checks in `end-session` §4).
   - Run `measure-delivery` and audit the trail per `show-me-your-work`.
   - Write the lessons into standing orders, skills or memory.

## How the human's words change the program

| The human says | Do, in the same turn |
|---|---|
| "I'll watch X myself, don't bother" (usage, a metric, a channel) | Delete X from the standing orders and from every judgment rule now, and never defer work on X's account again |
| "Can it go faster?" | Offer structural levers: parallelize the critical path, stack dependent chains, reorder the land order, narrow `exclusive_paths`, risk-tiered review depth, split CI jobs by role. Apply the approved ones at once. Not "work harder", and not serializing for safety (one CI at a time, one landing per N minutes) |
| Feedback about how the work flows, not about the product | Hand it to a flow improver (a subagent or a separate session). It changes the skills or standing orders and shares the result, while you stay on the program |
| "Make it better" about in-flight work | A new ticket with its own brief. Never append it to the running task |
| "Make a ticket for …" inside another question | Split it out and file it immediately, then answer the question |
| Doubts your report ("is that right?", "불안하다") | A read-only verifier agent re-checks it. Your self-report is not evidence |
| "Check" (점검) one section | Check the whole related scope. "Check and align" (점검하고 얼라인) is two tracked steps: findings, then the alignment changes, each reported done separately |
| "Why is #N not moving?" / priority doubts | `prog.py queue <slug>`: answer with its order and reasons, then fix the reason (stack, Orca dep, class, fix task) |
| "Update the dashboard and summarize" | `dash.py collect`, then a human summary of the same numbers, as one action |

## Merge policy

- `autonomous` (default): workers land with `prog.py land`.
- `human-gate`: workers stop at READY. You `prog.py gate <slug> --pr N` (an Orca decision gate), and after the user resolves it, `prog.py land`.
- Either way, stop and ask only before irreversible acts: destructive migrations, force-pushing shared branches, sending anything outside, changing repo or branch protection.

## What reaches the human

The dashboard, plus the digest line in the program note, batched. The digest holds open gates, a proposed predicate, irreversible acts, and any standing order that contradicts reality. Never retries, CI flakes, review-thread triage, rebases, or "should I continue".

## Traps seen in real runs

| Trap | Instead |
|---|---|
| A coordinator granting each merge | Workers land through `prog.py land`. The coordinator became the slowest lane |
| "Whoever is ready lands next" | Dependencies in Orca, and the land order for the exclusive lane. A dependency root starved for 5h behind newer PRs |
| One merge baton under strict up-to-date branches | A parallel normal lane with strict off. Every landing had pushed every other PR behind for another 30–40 min of CI |
| Landing a dependency chain one PR at a time (rebase + CI between each) | Stack it; the top's `land` merges the chain in one `merge-async` |
| Workers idling for hours "waiting for the merge slot" with nothing to do | `land --wait-minutes` in the background. `status` STALE lines and backlog hours make the wait visible and actionable |
| A worker calling `gh api … merge-async` itself and the classifier refusing it as "Merge Without Review" | Merges happen only inside `prog.py land`, which is the review gate and the allowed command |
| Reverting every red main by reflex, or the coordinator debugging it | The main guardian: flake check first, then hotfix or revert by the rule in `references/roles.md` |
| Cards named by Orca's automatic title ("Orca 외부 카드 진행 추적" for ENG-252) | `--display-name "<ID> <title>"` on every start; a terminal rename does not stick |
| A spec starting with `/goal` | Start the spec with the ticket ID; Orca already delivers it as the worker's task |
| Waiting with `sleep`, a hand-built watcher, or invented `orca … events/status` | `prog.py wait` in the background. One empty wait is a checkpoint; after three, run `worker-list --run` |
| Nudge-only turns ("You have N orchestration messages") burning coordinator turns | End such a turn with no tool call; real work arrives through `prog.py wait` |
| A worker shows `live` but has done nothing for many minutes | It may be sitting on a permission prompt (`status` lists `idle-waiting`): `worker-read --dispatch <id> --source terminal`. You cannot approve it for the worker, and waiting output does not prove the agent stopped, so do not stop or retry it. Put it in the digest for the human and keep draining |
| A handoff prompt that retypes the rules | The note is the handoff; the prompt is one line |
| Follow-ups spawned the moment they are filed | Park them; `status` shows derived tickets per predicate item |
| Parallel CI jobs named `1/3 2/3 3/3` | Name jobs by what they check (typecheck, lint, unit, db, api…) |
| Several cards running docker compose stacks and image builds at once; the machine and the Orca UI crawled | Heavy local runs go through `prog.py heavy`, 2 machine-wide slots (`landing.heavy_slots`) |
| Finished setup terminals and landed cards left open (50 terminals) | Close setup terminals when they exit; `worktree rm` in the turn a worker reports done |
| A shared state file overwritten in place and left empty | Append-only ledger; anything else is written to a temp file and renamed |
| Typing `/model` into a worker terminal | Pass `--model` to `worker-start`; `/model` changes the global setting |

## Resume (handoff)

The new coordinator's whole prompt is `/goal orchestrate resume <slug>`. On resume:

1. Read the program note.
2. `orca orchestration run-use --id <run>`.
3. Make `program.json` match the note: `prog.py set <slug> merge_policy|ceiling|deadline|predicate|exclusive_paths …` for any difference. The note wins.
4. `prog.py status`.
5. Continue at Drain.

Whenever the note's policy, ceiling, deadline, predicate or exclusive paths change mid-run, mirror them with `prog.py set` in the same turn. Workers report to the Run inbox, not to a session name, so nothing needs re-pointing. The old coordinator ends only after the new one has printed `status`.
