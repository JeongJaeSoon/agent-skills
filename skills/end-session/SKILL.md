---
name: end-session
description: "Use to end, close or archive this session, card or worktree — \"종료해줘\", \"세션 종료해줘\", \"현재 세션 정리해줘\", \"아카이브해줘\", \"머지하고 종료하자\", \"end this session\" — before an Orca worker's worker_done, or when a handoff or the brief says to close. Bare \"정리해줘\"/\"마무리해줘\" or \"작업 기록 남겨줘\" records and keeps the session."
allowed-tools: Bash(orca terminal close --terminal $ORCA_TERMINAL_HANDLE --json)
---

# Ending the session

Ending is an `orca` command: Claude Code here runs inside an Orca terminal. `EndConversation`
is the fallback for what Orca cannot reach, not the default. Only the four rows below end a session;
a task that finished, stalled or went badly does not.

| Who ends this session | Read |
|---|---|
| The user asked ("종료해줘", "세션 정리해줘", "종료해도 될까?") | [`references/user-ending.md`](references/user-ending.md) first: §1 (is it a request to end), §2 (what `orca worktree current` says this session is), §5 (`EndConversation`), then §3, §4, §6 here |
| An Orca worker settling its task | §7 here, no user phrase needed |
| A card whose brief has a `CLOSE` line, at the brief's done | §8 here, no user phrase needed |
| `dispatch-card` next-ticket mode §5, once its successor card has started | §3 and §6, then `orca terminal close --terminal $ORCA_TERMINAL_HANDLE --json`; the card stays for "Who cleans up" |

## 3. Leave the record before anything dies

Orca metadata and this transcript die with the session. Before the command runs, the record must
already exist where it survives: read [`references/record.md`](references/record.md) now and
record by it (what goes to the tracker, what to the notes store, what not at all), and end the
report with its §4 record line:

- Every ticket this session touched → `use-tracker` "Reconcile", so none is left behind its PR
  or deploy. The ticket linked to this card → final state (moved to completed, or left
  `started` in review with the PR link) and a completion comment with "남은 확인 사항". If the
  latest comment already says this, do not repeat it.
- The project's worklog (`use-notes`) → one line on what this session did, if not already
  written.
- An orchestrator or lead → `orch handoff save --next "<first step for the next session>"` (the
  key comes from the session's role; a lead's lands on `lead:<slug>`) with every `--waiting`, `--question` and `--reply` that still
  holds, since each save replaces the last; `agent-skills:reboot` reads it back (`orchestrate`
  `references/top-level.md`, "Restart with a handoff").
- No ticket (a local diagnosis, a probe) → the worklog line is enough. Do not file a ticket
  just to close it.
- The ticket has a parent epic → re-check its exit condition (principle **Work to an Exit
  Condition**, step 5): report "N/M 완료 조건 충족"; for each ticket this session filed under
  it, name the exit item it serves or move it to `⏸️ 보류`; close the epic when every exit item
  is met.
- Anything learned here that outlives the task → memory, now, not "later". An Orca worker (§7)
  or a dispatched card (§8) writes nothing to auto-memory: the memory directory is outside its
  worktree, so each write stops it on a permission prompt. It puts the lesson in its report,
  and the session above decides what to keep.
- The human corrected how the work was done (not what to build), and no program's Close
  covers this session → run `reflect` in session mode before ending. It records the lessons in
  the lessons ledger; apply only what the user approves now. A worker or a dispatched card
  does not run `reflect`: it puts each correction in its report, for the session above.
- Temp files you remember making outside the worktree (scratchpad excluded) → delete them.
- What this session started must not outlive it unseen. Background subagents and background
  shell tasks still running → stop each with `TaskStop`; if one's result is still needed, do
  not end yet. Git worktrees this session added (Agent `isolation: worktree` ones under
  `<repo>/.claude/worktrees/agent-*`, `git worktree add` in the scratchpad) → `git worktree
  remove <path>` for each that is clean with its work on `origin/main` or given up; list the
  others in the report. Cards this session dispatched (`orca worktree ps --json`: rows whose
  `parentWorktreeId` is this card) → name each one's state in the report; "Who cleans up" below
  says who takes them.

If the record is already written, say so in one line and move on. Do not rewrite it.

## 4. Removing a worktree card

§7 and §8 read this table to judge whether a card is done, and leave the removal to the cleaners ("Who cleans up"). Only for `isMainWorktree: false`. Read the state with real output, not memory: run `skills-sync end-check` (no flags; never `--apply` here). It fetches `origin/main` first (a stale ref hides unpushed commits, so a check by hand starts with `git fetch`) and prints `remove`, `keep`, `ask`, or `close-terminal` (the main checkout, or another agent on the card: keep the worktree and close only this session's terminal) with its reasons. It reads which ticket is linked but not its state or criteria: check the table's ticket and criterion rows yourself before you act on `remove`.

| State | Action |
|---|---|
| No tracked changes; the branch's work is on `origin/main` (its PR merged, which decides after a squash merge; with no PR, `git diff origin/main...HEAD` empty); no open PR; ticket completed or no ticket | **Remove**: move untracked build caches (`__pycache__`, `.pytest_cache`, `node_modules/.cache`) and regenerated skill copies (an untracked `.agents/skills/<name>/` whose `.claude/skills/<name>` is tracked: Orca rebuilds it) to the trash first, the way `reap-resources` does (`/usr/bin/trash` on macOS, else its fallback), since `orca worktree rm` refuses any untracked file, then `orca worktree rm --worktree current --json` |
| A criterion still open, PR still open, or the user said to hold | **Keep the worktree.** Close its terminals with `orca terminal close --worktree current --all --json` only if the user wants this agent stopped — that is not Sleep, the terminals do not come back. If the work resumes later, leave them alone and say Sleep is theirs from the app. |
| Tracked changes, untracked files that are neither build caches nor regenerated copies, unpushed commits with no merged PR, or an unexpected state | **Stop and ask**, only when the user asked to end (a worker or a card keeps the card instead, §7, §8), by the owner-ask gate (`orchestrate` [`references/owner-ask-gate.md`](../orchestrate/references/owner-ask-gate.md)): show the exact `git status` / `git log` lines and offer commit-and-push, discard, or keep |

`orca worktree rm` also deletes the local branch when Orca can prove it is merged; a branch it
cannot prove merged is kept, which is fine. No separate `git branch -d` or `git worktree remove` for the card.
Never pass `--force` to get past a dirty tree — that is the ask-first case. A permission layer
refused `orca worktree rm`: [`references/refused-removal.md`](references/refused-removal.md).

`orca worktree rm` skips the repo's `orca.yaml` archive hooks unless you pass `--run-hooks`. Pass it
when the repo's `orca.yaml` defines an archive hook, so the cleanup the repo asked for runs. A failed
hook then blocks the removal and changes nothing; treat that as the ask-first case too, not a reason
for `--allow-failed-archive-hook`.

**Run each cleanup command alone.** `orca worktree rm`, `orca terminal close`, `orca orchestration
worker-release` and `worker-stop`, `claude rm`, `gh pr merge`, `git worktree remove` and branch
deletion each take a Bash call of their own, written as the skill shows them: no pipe, `;`, `&&` or
`$()`, and no `ls` or `grep` added to check the result. A call passes without review only when each
part of it is approved on its own; a part that is not can leave the whole call to the auto-mode
classifier or a prompt, which can refuse a deletion its allow rule alone would pass. When the session
outlives the command, check the result in the next tool call; a command that ends this session stays
the turn's last call (§6). Do this from the first run: splitting a call that was already refused is
a retry (§7). Other skills link here for this rule.

## 6. Order inside the final turn

Write the entire report as text **first**, then run the ending command or call
`EndConversation` as the **last** tool call of that turn, and stop. Every ending here closes the
channel — the Orca ones kill the process, `EndConversation` seals the conversation — so whatever
you planned to say afterwards is never delivered. Never end a turn promising to clean
up "next turn", and never write anything after `EndConversation`.

The report says what this session delivered, where the record lives (worklog,
ticket, PR), what the user should still check themselves — or plainly that nothing is left,
never an invented item — and which command is about to run. Write it per `write-plainly`. Its
layout follows the `brief` skill, which leaves these items in place. When the user
writes from Telegram, send the report with the Telegram reply tool before the ending command: text
left only on the terminal never reaches them.

Ending the session is not closing the terminal app. If they want the CLI window gone too, that
is theirs: `/exit` or `Ctrl+D`. One clause, not a paragraph.

A report spanning several cards or leads is the `orch brief render` output pasted whole (`--telegram` for
Telegram), with at most one line before it, never a rewrite of it; `brief` names the cases written by hand.

## 7. An Orca worker closes itself when it settles

A worker is a session Orca dispatched: its prompt carries an Orca preamble with a `worker_done`
command. Nobody says "종료해줘" to it, and an orchestrator or lead whose inbox wait has lapsed never
closes it, so the worker closes itself as the last step of its task. The preamble's "take no
further actions after worker_done" still holds: only the closing calls follow `worker_done`, in
the same turn, and nothing runs after them.

Before sending `worker_done`, run `skills-sync end-check --worker --outcome <succeeded|failed>
--dispatch <the preamble's --dispatch-id> --brief <brief path> --trash-caches` (`--keep` or `--no-keep` when the task
has no brief file; without `--dispatch` it looks the Dispatch up and keeps the card when it cannot). It reads §4's
facts, the card's terminals and the questions this terminal left unanswered, and prints the row
below as `close-terminal` or `keep` with its reasons; a fact it could not read is `keep`. It also
moves the build caches to the trash now, on either verdict, unless this is the main checkout or
another agent works on the card: the cleaners keep a card with an untracked one, and the session
above may close this terminal once `worker_done` arrives. Exit 4 means that move failed: the card
holds, so name that in `worker_done`. Then pick the row:

| State | The turn's last calls |
|---|---|
| Outcome `succeeded`; §4's Remove row holds (clean tree apart from its build caches and regenerated copies, the work on `origin/main`, no open PR), or the card is `isMainWorktree: true` or another agent works on it; no `ask` or `orch decide` question waiting; the brief has no `KEEP` line | The report text, `worker_done` as its own call, then, once its output shows `"ok": true`, `orca terminal close --terminal $ORCA_TERMINAL_HANDLE --json`: this session goes, and the card stays for "Who cleans up". |
| Anything else: outcome `failed` (the orchestrator or lead that dispatched it retries on this card), an open PR (a wait named by a `READY-WAIT:` line, READY under human-gate), a `KEEP` line, a question waiting, a dirty tree | `worker_done` naming what holds the card, as its own call, then close the card's setup terminal with `skills-sync close-setup <card path>` in the background (it can wait up to 30 minutes; never chain it before `worker_done` with `;` or `&&`), and idle. Never `AskUserQuestion`: nobody sees it. |

- **The close waits for the send.** Calls in one message run together, so a `worker_done` and a
  `terminal close` sent side by side end this session even when a hook refused the send, and the
  report never arrives. Close only on the `"ok": true` in the send's own output. A send refused only on its body text is reworded in the same command;
  after any other refusal the card and this session stay, idle, for the orchestrator or lead above it to find.
- **A refused close** (by the auto-mode classifier or a hook, not Orca). A refused act is not run again,
  except once, in the same form, by the refused session (a restart that loads a new rule counts as that session), and only when the human allowed it in that session's own chat or added a rule for it, or when a classifier or hook refusal stated a reason and you added evidence that answers it, never for a human's refusal or a production sync. Report that retry as `재시도 1회: <what you added>`. A second refusal stops the act and you report it. Reaching the same result through another session, script, tool, wrapper or target stays forbidden. With nothing to add, leave it: `orca orchestration send --to run:<runId> --type status --subject "terminal left: close refused" --body "<handle>"` (the `runId` from `orca orchestration check --terminal $ORCA_TERMINAL_HANDLE --json`; after `worker_done` a send without `--to` finds no Run), then idle. The session above puts it to the human and does not close it: its own close would be the refused act run by another session.
- **The pre-approved close.** `allowed-tools` covers only this session's own close, written exactly as above, and only in the turn that invoked this skill. In a later turn, invoke the skill again first.
- **`KEEP`** is the brief's opt-out: the orchestrator or lead above it plans to reuse this terminal or card.
- **An open PR** of a card whose first prompt approved the merge is the card's to land first
  (`orchestrate` `references/landing.md`); `hooks/self_land_gate.py` speaks when it does not. It
  keeps the worker only until it lands. A later wake (a review fix, a message)
  runs this table again and closes if the PR has merged or closed since. Nothing else wakes an
  idle worker; the `SETTLED` sweep of the session above it is the backstop.
- **`Rejected worker_done`** after someone typed into the terminal (Orca marks it taken over)
  still settles the task: write the report as text on screen and close by the table. A refusal
  for a missing capability does not: the session above it never got the report, so keep the card.
- Inside a program the lead reconciles the tickets on `worker_done`, so §3's tracker
  and worklog steps are its, not the worker's. §6's order holds. The `worker_done` body still
  carries the record line ([`references/record.md`](references/record.md) §4): what this worker
  wrote, `기록: 없음 (리드 reconcile)`, or `기록 못 함: <reason>`.

## 8. A dispatched card closes itself when its brief is done

A card started by another session with a brief (`dispatch-card`, an orchestrator's or lead's `orca worktree
create`) has no `worker_done`, and its parent looks at it only when its PRs merge or the parent
itself ends, which can be days later. Its brief's `CLOSE` line (`dispatch-card` §2) is the
request to end: when the brief's done is reached, close in that same turn, without asking.
Idling on "tell me what to do next", on "the parent session (or the orchestrator) does the cleanup",
or on an inbox wait for a reply the brief never asked for is not an ending: the parent's check is
the backstop, not the plan.

Done means the brief's deliverable is complete and recorded where the brief says (its PRs
merged, the note's `## 결과` written), and nothing waits on an answer: no question you asked,
no open PR, unless the open PR is the deliverable itself (the row below that says so). Follow-up ideas are not a reason to stay: write them in the report and the note for
the parent to decide. Then run §3 and pick by §4's table on real output: `skills-sync end-check
--card --brief <brief path> --trash-caches` (or `--keep`/`--no-keep`; `--ticket-done` once `use-tracker`
shows the linked ticket completed) prints it and moves its build caches to the trash, as in §7.

| State | The turn's last calls |
|---|---|
| §4's Remove row holds (a document-only card with no commits passes: `git diff origin/main...HEAD` is empty), or the card is `isMainWorktree: true` or another agent works on it | The report text, then `orca terminal close --terminal $ORCA_TERMINAL_HANDLE --json` |
| A `superseded by <card>` message from the session that started this card | Stop the work and push nothing more. The report names what is uncommitted or unpushed, then `orca terminal close --terminal $ORCA_TERMINAL_HANDLE --json`. A question you left waiting is withdrawn |
| The brief's deliverable is the open PR itself (its `CLOSE` line says to close once the PR is up and reported; merge and review are not this card's) | The report text, then `orca terminal close --terminal $ORCA_TERMINAL_HANDLE --json`. The worktree stays for the PR (§4); a review round starts a new card on it, and "Who cleans up" takes it once the PR merges |
| A question waiting on the human, an open PR that is not the deliverable, a dirty tree, or a `KEEP` line | Stay. Say in the report and the note what holds the card. A later wake (a review fix, a message) runs this table again; otherwise the parent's check closes it |

A card whose prompt carries Orca's worker preamble closes by §7, not here. A refused close follows
§7, and the note says the terminal is left for the human. When a row keeps the worktree after the brief's whole done (its PRs merged, any release, deploy
or check it names finished), first set the board column, as its own call: `orca worktree set
--worktree current --workspace-status completed --json`; not for a superseded card or an open-PR
deliverable (its column stays `in-review`).

**Who cleans up.** This is the one rule; other skills link here.

1. In the turn the work is done, whoever started a resource stops it: the compose stacks it brought
   up (the verify skill's teardown, else `docker compose -p <project> down`, never `-v`), a
   container it started outside compose (`docker stop` with the full ID it recorded), its dev server,
   and the `ego-browser` task spaces it opened: `await task.finish({ keep: [] })` on each, run through
   `ego-browser nodejs` with `const task = await taskSpace(<the spaceId it printed>)`. An open tab keeps
   its renderer alive after the card ends.
2. Ending a session goes as far as its own terminal: a worker after `worker_done` (§7), a `CLOSE`
   card after its report (§8), a lead after its Close. A finished card still idle at its prompt:
   the session that started it closes that agent terminal, in the wake its report arrives, unless
   the card reported its close refused (§7).
3. The rest is `reap-hard`'s and the `reap-resources` janitor's: the worktree, shell terminals,
   volumes, stopped containers. No session runs `orca worktree rm` as routine, waits for them, or
   retries what they refused.

The session that started a card runs §4 itself in two cases. A lead's card: neither cleaner
removes it. A machine with no cleaner: `reap-hard status` says not loaded and the janitor
automation is off.

A person's "종료해줘" stays §3, §4 and §6, and what lives inside one session (subagents, background
tasks, scratchpad git worktrees) stays that session's (§3): the cleaners cannot know when those are
done. `KEEP` holds only this session's own close and neither cleaner reads it; a card that must
outlive its merge goes in the protect file as a `path` line (`reap-resources`, "Hard pass").
