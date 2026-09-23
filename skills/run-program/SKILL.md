---
name: run-program
description: Use when one session must drive a project or milestone to done through several Orca workers — "PM 겸 오케스트레이터로 끝까지", supervising 3+ tickets in parallel, landing their PRs, keeping follow-up tickets from swallowing the plan — or when taking over ("resume") a program another coordinator was running.
---

# Run a program

You own the program, never the code: frame it, write briefs, drain the inbox, land verified PRs, decide. This is pstack's Orchestrate (Lauren Tan) moved onto Orca. Orca already owns the mechanics — Run, Task, Dispatch, `check --wait`/ack, `ask`/`reply`, the worker contract, recovery and release — so this skill adds only the program layer.

**REQUIRED BACKGROUND:** run `orca skills get orchestration` and read it at the start of every coordinator session. Every Orca command here comes from it; never compose one from memory.

## When not to use

- One agent could finish inside the budget → do it in this session, or give it to one card with `handoff-ticket`. A program costs a coordinator.
- A single ticket handed to another session → `handoff-ticket`.

## Where each fact lives

| Fact | Owner |
|---|---|
| Standing orders, predicate, merge policy, decisions, human digest | Obsidian `Project/<project>/program-<slug>.md` (template: `references/program-note.md`) |
| Tickets, their state, which are derived | Linear |
| PRs, CI, merges | GitHub |
| Runs, tasks, workers, questions | Orca |
| Verdicts, landings, main red/green, the concurrency cap | `~/.claude/programs/<slug>/ledger.jsonl`, written only through `prog.py` |
| Briefs as sent | `~/.claude/programs/<slug>/briefs/<ticket>.md` |
| Decision trail | `~/.claude/programs/<slug>/decisions.tsv` (`show-me-your-work`) |

`prog.py` is `python3 ~/.claude/skills/run-program/scripts/prog.py`; run it with no arguments for usage.

## Steps

1. **Frame.** Read every ticket in scope. Write the predicate as countable ticket IDs plus a final check on the real artifact ("94S-246, 247, 117, 134, 135 Done and `verify-<app>` drives the quickstart on main"). Derive it yourself; if the tickets have no countable end, write your best predicate, mark it proposed in the digest, and go on. Copy the human's goal into standing orders sentence by sentence, verbatim. Pick the merge policy. Create the Run (`run-create`), then `prog.py init <slug> --repo … --run … --linear-project … --predicate …`.
2. **Verification first.** No `.claude/skills/verify-*` in the target repo → the pilot task is "read and follow `~/.claude/skills/create-verification-skill/SKILL.md`".
3. **Pilot.** One worker through brief → PR → verdict → landing → main green. Fix the brief, the unit size, and VERIFY from what broke.
4. **Scale.** Spawn up to the cap `prog.py status` prints (it starts at 1, grows by one per landing proven green on main, halves on red). Each spawn: write the brief per `references/brief.md`, then `worker-start --spec "<the brief>"`. Keep the spec's first character a letter, never `/`.
5. **Drain.** Wait only with `prog.py wait <slug>` under `run_in_background` (it wakes on worker_done, escalation, or question, and acks heartbeat-only batches). Keep exactly one running. Process every message in the batch, decide each settled worker's next owner (reuse, release), ack, then print `prog.py status` — its lines are how a drain ends. On each wake, one `gh pr list --state merged --search "merged:>=<last drain>"` catches merges you did not make.
   - Under `/goal`, a running background task defers the Stop hook's goal check (agent-platform handoff §9). If the hook re-prompts anyway with no new event, answer in one line with no tool call; if it fires back-to-back, switch to a foreground `prog.py wait <slug> --rounds 1 --timeout-ms 540000` (Bash timeout 600000) so the turn stays busy.
6. **Triage.** A new ticket from review or discovery parks by default: Linear label `follow-up`, `prog.py record <slug> parked --ticket X`. Admit it only if it blocks a named predicate item or is a reproduced correctness, security, or data defect in code this program merged; `record admitted` and say which item or defect.
7. **Land** (`references/landing.md`). A worker's task ends at a PR with a verdict. You are the single merge steward: `prog.py verdict`, then `prog.py land-check`, then the exact merge command it prints, then `prog.py landed`. One PR at a time.
8. **Verify main.** Watch each merge's main CI in the background and record `main_green` or `main_red`. Red → nothing lands until a fix task makes it green. Every 3 landings, and before claiming the predicate, a verifier worker drives main with the repo's `verify-<app>` skill.
9. **Close.** Confirm the predicate on the real artifact, release remaining workers, remove landed PRs' worktrees (checks in `end-session` §4), run `measure-delivery`, audit the trail per `show-me-your-work`, and write lessons into standing orders, skills, or memory.

## Resume (handoff)

The new coordinator's whole prompt is `/goal run-program resume <slug>`. On resume: read the program note, `orca orchestration run-use --id <run>`, `prog.py status`, then continue at Drain. Workers report to the Run inbox, not to a session name, so nothing has to be re-pointed. The old coordinator ends only after the new one has printed `status`.

## Merge policy

- `autonomous` (default): you land after `land-check` passes.
- `human-gate`: verdict'd PRs wait in the digest's "사람 착륙 대기"; land one only after the user says so and `prog.py record approved --pr N`.
- Either way, stop and ask only before irreversible acts: destructive migrations, force-push to shared branches, sending anything outside, changing repo or branch protection.

## What reaches the human

Only the digest line in the program note, batched: open gates, proposed predicate, irreversible acts, a standing order that contradicts reality. Never: retries, CI flakes, review-thread triage, rebases, "should I continue".

## Traps seen in real runs

| Trap | Instead |
|---|---|
| Waiting with `sleep`, a hand-built watcher, or invented `orca … events/status` | `prog.py wait` in the background; one empty wait is a checkpoint, three → `worker-list --run` |
| Nudge-only turns ("You have N orchestration messages") burning coordinator turns | End such a turn with no tool call; real work arrives through `prog.py wait` |
| Worker shows `live` but has done nothing for many minutes | It may sit on a permission prompt (`status` lists `idle-waiting`): `worker-read --dispatch <id> --source terminal`. You cannot approve it for the worker; stop it and fix the cause. Keep worker writes inside its own worktree, and confirm in the pilot that the chosen worker model gets through its first `orca orchestration` call — a Haiku worker stalled on an approval prompt for `check` in a 2026-09-24 probe |
| Handoff prompt that retypes the rules | The note is the handoff; one-line prompt |
| Follow-ups spawned the moment they are filed | Park; `status` shows derived tickets per predicate item |
| Cards merging themselves in parallel behind strict protection | One steward, one landing at a time |
| Typing `/model` into a worker terminal | Pass `--model` to `worker-start`; `/model` changes the global setting |
