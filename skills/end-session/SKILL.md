---
name: end-session
description: "Use when the user asks to end, close or archive this session, card or worktree — \"종료해줘\", \"세션 종료해줘\", \"현재 세션 정리해줘\", \"아카이브해줘\", \"세션을 마무리짓자\", \"머지하고 종료하자\", \"티켓 정리하고 종료해줘\", \"종료해도 될까?\", \"끝낸 orca 세션·worktree 정리해줘\", \"end this session\" — when handoff-ticket reaches the point where this session should disappear, or when this session is an Orca worker about to send worker_done, or a card whose brief says to close itself when done (no user phrase needed). Not bare \"정리해줘\"/\"마무리해줘\" with no object: that means wrap up the record and keep talking."
---

# Ending the session

Triggers, all meaning this skill: "종료해줘", "세션 종료해줘", "대화 종료해줘", "이제 종료하자",
"끝내줘", "그만하자", "여기서 마치자"; "세션 정리해줘", "이 세션 닫아줘", "카드 정리해줘",
"worktree 정리해줘"; "end this session", "close this session", "end the conversation".
**Bare "정리해줘" / "마무리해줘" with no object is not this skill** — the object decides:
세션·카드·worktree 정리 ends it, 정리 alone does not.

Claude Code on this machine runs inside an Orca terminal, so ending a session is an `orca`
command. `EndConversation` is the fallback for what Orca cannot reach, not the default.

Ending is yours to do. The user asked; do not ask back unless the state is off-script (§4) or
the ending is `EndConversation`, which has its own rule (§5). An Orca worker is not asked at
all: settling its task is the request (§7). Nor is a card whose brief has a `CLOSE` line:
reaching the brief's done is the request (§8).

## 1. Is this even a request to end

| What the user said | What they mean | Do |
|---|---|---|
| "정리해줘", "마무리해줘" (no object) | leave the record, keep talking | Report. **Do not end, do not ask to end.** |
| "종료하면 돼?", "종료해도 될까?" | is anything left undone? | Answer that. No prompt, no command. |
| "세션 정리", "카드 정리", "종료해줘" | end this session | Continue to §2. |

This user says 정리 for the record and 종료 for the end, and often says 정리 first and 종료
several turns later. Do not collapse them.

## 2. Ask Orca what this session is

```bash
orca worktree current --json    # read isMainWorktree and branch
```

It answers in **every** session, including a plain folder context with no git repo — a memo
or scratch folder reports a worktree with `isMainWorktree: true` and an empty `branch`. "There
is no card here" is almost never true, and cwd never proves it. Run the command.

| `orca worktree current` | What this session is | Ending |
|---|---|---|
| `isMainWorktree: false` | a worktree card Orca cut for a branch | §4 — the card goes away |
| `isMainWorktree: true` | the repo's own checkout, or a folder context (memo, scratch) | `orca terminal close --worktree current --all --json`. **Never `worktree rm`** — that selector points at the checkout itself. |
| errors, or Orca is not running | not under Orca | `EndConversation` (§5) |

`orca terminal close --worktree current --all` stops every terminal process the workspace owns
and **durably removes its tabs, layouts and resume records**. That kills this process and the
session does not come back. Orca's Sleep is the resumable one, and it is a UI action with no CLI
equivalent — `orca --help` has no `sleep`. So when the session has to resume later, run nothing
and say that Sleep is theirs to do from the app.

`EndConversation` is also the right answer in any row when the user wants the *conversation*
over but the terminal left standing. Killing terminals is not a polite goodbye; pick by what
they actually asked for.

## 3. Leave the record before anything dies

Orca metadata and this transcript die with the session. Before the command runs, the record must
already exist where it survives:

- Every ticket this session touched → `use-tracker` "Reconcile", so none is left behind its PR
  or deploy. The ticket linked to this card → final state (moved to completed, or left
  `started` in review with the PR link) and a completion comment with "남은 확인 사항". If the
  latest comment already says this, do not repeat it.
- The project's worklog (`use-notes`) → one line on what this session did, if not already
  written.
- No ticket (a local diagnosis, a probe) → the worklog line is enough. Do not file a ticket
  just to close it.
- The ticket has a parent epic → re-check its exit condition (principle **Work to an Exit
  Condition**, step 5): report "N/M 완료 조건 충족"; for each ticket this session filed under
  it, name the exit item it serves or move it to `⏸ 보류`; close the epic when every exit item
  is met.
- Anything learned here that outlives the task → memory, now, not "later".
- The human corrected how the work was done (not what to build), and no program's Close
  covers this session → run `reflect` in session mode before ending. It records the lessons in
  the lessons ledger; apply only what the user approves now.
- Temp files you remember making outside the worktree (scratchpad excluded) → delete them.
- What this session started must not outlive it unseen. Background subagents and background
  shell tasks still running → stop each with `TaskStop`; if one's result is still needed, do
  not end yet. Git worktrees this session added (Agent `isolation: worktree` ones under
  `<repo>/.claude/worktrees/agent-*`, `git worktree add` in the scratchpad) → `git worktree
  remove <path>` for each that is clean with its work on `origin/main` or given up; list the
  others in the report. Cards this session dispatched (`orca worktree ps --json`: rows whose
  `parentWorktreeId` is this card) → name each one's state in the report, and remove one only
  when §4's Remove row holds in it and its last report says nothing is left.

If the record is already written, say so in one line and move on. Do not rewrite it.

## 4. Removing a worktree card

An Orca worker reads this table too, through §7. Only for `isMainWorktree: false`. Read the state with real output, not memory:

```bash
git fetch -q origin main
git status --short --untracked-files=no # must be empty (tracked changes)
git status --short --ignored             # untracked leftovers: build caches like __pycache__ only?
git diff --stat origin/main...HEAD       # for a branch with no PR; after a squash merge this still shows the branch's diff
gh pr list --head "$(git branch --show-current)" --state merged --json number,mergeCommit
orca worktree current --json             # linked ticket (linkedLinearIssue / linkedIssue / linkedWorkItem), linkedPR; workspaceStatus does not decide anything
gh pr list --head "$(git branch --show-current)" --state open --json number,url
```

| State | Action |
|---|---|
| No tracked changes; the branch's work is on `origin/main` (its PR merged, which decides after a squash merge; with no PR, `git diff origin/main...HEAD` empty); no open PR; ticket completed or no ticket | **Remove**: delete untracked build caches (`__pycache__`, `.pytest_cache`, `node_modules/.cache`) first, since `orca worktree rm` refuses any untracked file, then `orca worktree rm --worktree current --json` |
| A criterion still open, PR still open, or the user said to hold | **Keep the worktree.** Close its terminals with `orca terminal close --worktree current --all --json` only if the user wants this agent stopped — that is not Sleep, the terminals do not come back. If the work resumes later, leave them alone and say Sleep is theirs from the app. |
| Tracked changes, untracked files that are not build caches, unpushed commits with no merged PR, or an unexpected state | **Stop and ask** with `AskUserQuestion`: show the exact `git status` / `git log` lines and offer commit-and-push, discard, or keep |

`orca worktree rm` also deletes the local branch when Orca can prove it is merged; a branch it
cannot prove merged is kept, which is fine. No separate `git branch -d` or `git worktree remove` for the card.
Never pass `--force` to get past a dirty tree — that is the ask-first case.

`orca worktree rm` skips the repo's `orca.yaml` archive hooks unless you pass `--run-hooks`. Pass it
when the repo's `orca.yaml` defines an archive hook, so the cleanup the repo asked for runs. A failed
hook then blocks the removal and changes nothing; treat that as the ask-first case too, not a reason
for `--allow-failed-archive-hook`.

## 5. `EndConversation` asks first — the Orca paths do not

The asymmetry is not a style choice. The Orca commands run on the user's request alone. The
tool itself forbids being called without explicit confirmation that the end is permanent, so
ask once with `AskUserQuestion`, permanence stated in the question, two options and no third:

- **종료** — 이 대화를 끝냅니다. 되돌릴 수 없고 더 이상 메시지를 주고받을 수 없습니다.
- **계속** — 대화를 유지합니다.

Anything but choosing 종료 means keep going. An earlier "종료해줘" is not the confirmation; it
has to come *after* the warning.

## 6. Order inside the final turn

Write the entire report as text **first**, then run the ending command or call
`EndConversation` as the **last** tool call of that turn, and stop. Every ending here closes the
channel — the Orca ones kill the process, `EndConversation` seals the conversation — so whatever
you planned to say afterwards is never delivered. Never end a turn promising to clean
up "next turn", and never write anything after `EndConversation`.

The report says, in Korean: what this session delivered, where the record lives (worklog,
ticket, PR), what the user should still check themselves — or plainly that nothing is left,
never an invented item — and which command is about to run. Write it per `write-plainly`.

Ending the session is not closing the terminal app. If they want the CLI window gone too, that
is theirs: `/exit` or `Ctrl+D`. One clause, not a paragraph.

## 7. An Orca worker closes itself when it settles

A worker is a session Orca dispatched: its prompt carries an Orca preamble with a `worker_done`
command. Nobody says "종료해줘" to it, and a coordinator that is not running `orch wait` never
closes it, so the worker closes itself as the last step of its task. The preamble's "take no
further actions after worker_done" still holds: only the closing calls follow `worker_done`, in
the same turn, and nothing runs after them.

Before sending `worker_done`, read the state with §4's commands,
`orca terminal list --worktree current --json`, and the `runId` in
`orca orchestration check --terminal $ORCA_TERMINAL_HANDLE --json` (the fallback below needs
it), then pick the row:

| State | The turn's last calls |
|---|---|
| Outcome `succeeded`; `isMainWorktree: false` and §4's Remove row holds (clean tree, the work on `origin/main`, no open PR); no other agent terminal on the card; no `ask` or `orch decide` question waiting; the brief has no `KEEP` line | The report text, `worker_done`, then `orca worktree rm --worktree current --json` (`--run-hooks` per §4). The card's terminals, the setup one included, go with it. |
| The same, but `isMainWorktree: true` or another agent works on the card | The report text, `worker_done`, then `orca terminal close --terminal $ORCA_TERMINAL_HANDLE --json`: only this session goes. |
| Anything else: outcome `failed` (the coordinator retries on this card), an open PR (READY under human-gate, a review still running), a `KEEP` line, a question waiting, a dirty tree | Close the card's setup terminal (`skills-sync close-setup <card path>`), then `worker_done` naming what holds the card, and idle. Never `AskUserQuestion`: nobody sees it. |

- **A refused `worktree rm`.** The host's permission layer (the auto-mode classifier, a hook),
  not Orca, often refuses `orca worktree rm` as irreversible. Do not run it again in any form:
  not in smaller pieces, not through another tool or a script, not through a subagent. Fall back
  to the second row and leave the card: `orca orchestration send --to run:<runId> --type status
  --subject "card left: worktree rm refused by permission layer" --body "<card path>"` (after
  `worker_done` a send without `--to` finds no Run), then
  `orca terminal close --terminal $ORCA_TERMINAL_HANDLE --json`. The coordinator's `CLOSE OUT`
  line removes the card, or puts it to the human.
- **`KEEP`** is the brief's opt-out: the coordinator plans to reuse this terminal or card.
- **An open PR** keeps the worker only until it lands. A later wake (a review fix, a message)
  runs this table again and closes if the PR has merged or closed since. Nothing else wakes an
  idle worker; the coordinator's `SETTLED` sweep is the backstop.
- **`Rejected worker_done`** after someone typed into the terminal (Orca marks it taken over)
  still settles the task: write the report as text on screen and close by the table. A refusal
  for a missing capability does not: the coordinator never got the report, so keep the card.
- Inside a program the coordinator reconciles the tickets on `worker_done`, so §3's tracker
  and worklog steps are its, not the worker's. §6's order holds.

## 8. A dispatched card closes itself when its brief is done

A card started by another session with a brief (`dispatch-card`, a coordinator's `orca worktree
create`) has no `worker_done`, and its parent looks at it only when its PRs merge or the parent
itself ends, which can be days later. Its brief's `CLOSE` line (`dispatch-card` §2) is the
request to end: when the brief's done is reached, close in that same turn, without asking.
"worktree 정리는 부모 세션이 한다" is not an ending: the parent's check is the backstop, not the
plan.

Done means the brief's deliverable is complete and recorded where the brief says (its PRs
merged, the note's `## 결과` written), and nothing waits on an answer: no question you asked,
no open PR. Follow-up ideas are not a reason to stay: write them in the report and the note for
the parent to decide. Then run §3 and pick by §4's table on real output:

| State | The turn's last calls |
|---|---|
| §4's Remove row holds (a document-only card with no commits passes: `git diff origin/main...HEAD` is empty) | The report text, then `orca worktree rm --worktree current --json` (`--run-hooks` per §4) |
| `isMainWorktree: true`, or another agent works on the card | The report text, then `orca terminal close --terminal $ORCA_TERMINAL_HANDLE --json` |
| A question waiting on the human, an open PR, a dirty tree, or a `KEEP` line | Stay. Say in the report and the note what holds the card. A later wake (a review fix, a message) runs this table again; otherwise the parent's check closes it |

A refused `worktree rm` is not retried in any form (§7); close only this terminal and say in the
note that the card is left for the parent or the human.

## Common mistakes

- **"제가 세션을 종료할 수는 없습니다" and pointing at `/exit`.** Orca can end it, and
  `EndConversation` can end the conversation.
- **Deciding from cwd instead of `orca worktree current`.** A non-git folder is still an
  Orca-managed workspace. Checking cwd is how you conclude "no card" and reach for the wrong tool.
- **`orca worktree rm` on `isMainWorktree: true`.** That selector is the repo checkout or the
  scratch workspace, not a disposable card.
- Offering "원하시면 지우겠습니다" on a clean tree after the user asked to end. Their request
  was the permission; run it. This covers user-initiated endings only: a worker ends by §7, a
  `CLOSE` card by §8, and a refusal there ends in §7's fallback.
- Checking `origin/main..HEAD` without `git fetch` first — a stale ref hides unpushed commits.
- Running the git and `gh` lines in a folder context that is not a repo. There `branch` is
  empty and `isMainWorktree` alone decides.
- Removing when a PR is still open — the review fix would then need a new worktree.
- Calling `orca terminal close --all` "sleeping the card". It is the opposite: Sleep resumes,
  this drops the resume records for good.
- Calling `EndConversation` on the first "종료해줘", with no confirmation turn.
- Ending because a task finished, stalled, or went badly. Only the user's request ends a
  session, or, for an Orca worker, settling its task by §7, or a brief's `CLOSE` line (§8).
- A dispatched card that reached its done and idles on "다음은 무엇을 할지 정해 주세요" or
  "정리는 오케스트레이터가 합니다". §8 closes it in that turn.
- An Orca worker sending `worker_done` and idling on a clean, landed card. Nothing closes it
  after that; §7 closes it in the same turn.
