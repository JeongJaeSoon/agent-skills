---
name: end-session
description: Ending the current session — deciding which ending applies, leaving the record behind, and running it. Triggers, all meaning this skill: "종료해줘", "세션 종료해줘", "대화 종료해줘", "이제 종료하자", "끝내줘", "그만하자", "여기서 마치자"; "세션 정리해줘", "이 세션 닫아줘", "카드 정리해줘", "worktree 정리해줘"; "end this session", "close this session", "end the conversation". Also when `handoff-ticket` reaches the point where this session should disappear. **Bare "정리해줘" / "마무리해줘" with no object is not this skill** — that means wrap up the record and keep talking. The object decides: 세션·카드·worktree 정리 ends it, 정리 alone does not.
---

# Ending the session

Claude Code on this machine runs inside an Orca terminal, so ending a session is an `orca`
command. `EndConversation` is the fallback for what Orca cannot reach, not the default.

Ending is yours to do. The user asked; do not ask back unless the state is off-script (§4) or
the ending is `EndConversation`, which has its own rule (§5).

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

It answers in **every** session, including a plain folder context with no git repo —
`~/workspace/memo` reports a worktree with `isMainWorktree: true` and an empty `branch`. "There
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

- Linear ticket linked to this card → final state (Done, or In Review with the PR link) and a
  completion comment with "남은 확인 사항". If the latest comment already says this, do not repeat it.
- Obsidian worklog for the project (find it per `use-obsidian`) → one line on what this session
  did, if not already written.
- No ticket (a local diagnosis, a probe) → the worklog line is enough. Do not file a ticket
  just to close it.
- Anything learned here that outlives the task → memory, now, not "later".
- Temp files you remember making outside the worktree (scratchpad excluded) → delete them.

If the record is already written, say so in one line and move on. Do not rewrite it.

## 4. Removing a worktree card

A `run-program` worker (Orca preamble with Task and Dispatch IDs) does not remove its own
worktree: the coordinator releases it and removes the worktree after landing. It ends with
`worker_done` and idles.

Only for `isMainWorktree: false`. Read the state with real output, not memory:

```bash
git fetch -q origin main
git status --short                       # must be empty
git log --oneline origin/main..HEAD      # must be empty
orca worktree current --json             # linkedLinearIssue, linkedPR; workspaceStatus does not decide anything
gh pr list --head "$(git branch --show-current)" --state open --json number,url
```

| State | Action |
|---|---|
| Tree clean, 0 commits ahead of `origin/main`, no open PR, ticket Done or no ticket | **Remove**: `orca worktree rm --worktree current --json` |
| A criterion still open, PR still open, or the user said to hold | **Keep the worktree.** Close its terminals with `orca terminal close --worktree current --all --json` only if the user wants this agent stopped — that is not Sleep, the terminals do not come back. If the work resumes later, leave them alone and say Sleep is theirs from the app. |
| Dirty tree, unpushed commits, or an unexpected state | **Stop and ask** with `AskUserQuestion`: show the exact `git status` / `git log` lines and offer commit-and-push, discard, or keep |

`orca worktree rm` also deletes the local branch when Orca can prove it is merged; a branch it
cannot prove merged is kept, which is fine. No separate `git branch -d`, no `git worktree remove`.
Never pass `--force` to get past a dirty tree — that is the ask-first case.

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
up "next turn", and never write or think anything after `EndConversation`.

The report says, in Korean: what this session delivered, where the record lives (worklog,
ticket, PR), what the user should still check themselves — or plainly that nothing is left,
never an invented item — and which command is about to run.

Ending the session is not closing the terminal app. If they want the CLI window gone too, that
is theirs: `/exit` or `Ctrl+D`. One clause, not a paragraph.

## Common mistakes

- **"제가 세션을 종료할 수는 없습니다" and pointing at `/exit`.** Orca can end it, and
  `EndConversation` can end the conversation. This skill exists because that answer was given.
- **Deciding from cwd instead of `orca worktree current`.** A non-git folder is still an
  Orca-managed workspace. Checking cwd is how you conclude "no card" and reach for the wrong tool.
- **`orca worktree rm` on `isMainWorktree: true`.** That selector is the repo checkout or the
  scratch workspace, not a disposable card.
- Offering "원하시면 지우겠습니다" on a clean tree. The request was the permission; run it.
- Checking `origin/main..HEAD` without `git fetch` first — a stale ref hides unpushed commits.
- Running the git and `gh` lines in a folder context that is not a repo. There `branch` is
  empty and `isMainWorktree` alone decides.
- Removing when a PR is still open — the review fix would then need a new worktree.
- Calling `orca terminal close --all` "sleeping the card". It is the opposite: Sleep resumes,
  this drops the resume records for good.
- Calling `EndConversation` on the first "종료해줘", with no confirmation turn.
- Ending because a task finished, stalled, or went badly. Only the user's request ends a session.
