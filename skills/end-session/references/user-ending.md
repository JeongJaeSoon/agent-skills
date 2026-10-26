# Ending on the user's request

The path for a session the user asks to end. §3, §4 and §6 are in `SKILL.md`; the section
numbers are shared, so "§4" below is that file's §4.

Ending is yours to do. The user asked; do not ask back unless the state is off-script (§4) or
the ending is `EndConversation`, which has its own rule (§5).

## 1. Is this even a request to end

| What the user said | What they mean | Do |
|---|---|---|
| "정리해줘", "마무리해줘" (no object) | leave the record, keep talking | `record-progress`, then report. **Do not end, do not ask to end.** |
| "종료하면 돼?", "종료해도 될까?" | is anything left undone? | Answer that. No prompt, no command. |
| "세션 정리", "카드 정리", "종료해줘" | end this session | Continue to §2. |

Users often ask for the record first (정리) and for the end (종료) several turns later. Do not
collapse them.

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

## 5. `EndConversation` asks first — the Orca paths do not

The asymmetry is not a style choice. The Orca commands run on the user's request alone. The
tool itself forbids being called without explicit confirmation that the end is permanent, so
ask once with `AskUserQuestion`, permanence stated in the question, two options and no third:

- **종료** — 이 대화를 끝냅니다. 되돌릴 수 없고 더 이상 메시지를 주고받을 수 없습니다.
- **계속** — 대화를 유지합니다.

Anything but choosing 종료 means keep going. An earlier "종료해줘" is not the confirmation; it
has to come *after* the warning.

## Common mistakes on this path

- **"제가 세션을 종료할 수는 없습니다" and pointing at `/exit`.** Orca can end it, and
  `EndConversation` can end the conversation.
- **Deciding from cwd instead of `orca worktree current`.** A non-git folder is still an
  Orca-managed workspace. Checking cwd is how you conclude "no card" and reach for the wrong tool.
- Offering "원하시면 지우겠습니다" on a clean tree after the user asked to end. Their request
  was the permission; run it.
- Running the git and `gh` lines in a folder context that is not a repo. There `branch` is
  empty and `isMainWorktree` alone decides.
- Calling `orca terminal close --all` "sleeping the card". It is the opposite: Sleep resumes,
  this drops the resume records for good.
- Calling `EndConversation` on the first "종료해줘", with no confirmation turn.
