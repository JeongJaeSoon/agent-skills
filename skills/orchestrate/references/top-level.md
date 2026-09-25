# Top-level orchestrator

One session sits above everything the human runs in Claude Code and Orca: single-task sessions, and the coordinators of each program. The human talks to it. It routes, answers, and keeps the human's inbox honest. It does no task work itself (SKILL.md, "Stay answerable").

```text
orchestrator                      this session
  ├ task sessions 1..n            deliver-ticket, dispatch-card cards, sessions the human opened directly
  ├ program 1 coordinators A..D   orchestrate in program mode (ledger, land, roles, dashboard)
  ├ program 2 coordinator A
  └ program 3 coordinators A, B
```

The roster, adoption of sessions the human opened by hand, and the inbox/timeline screen belong to the orchestrator dashboard. This file covers what the orchestrator does with them.

## Routing

Every request leaves your turn within one short tool call.

| The request | Goes to |
|---|---|
| A question you can answer from what is already in context, or one `--json` read | You, now |
| Investigation, a review, a verification, anything that reads more than a couple of files | A background subagent (`run_in_background`); you answer when it reports |
| A change to one repo | A new Orca worker (`worker-start`), or `dispatch-card` when it needs its own card |
| More work for a session that already owns that topic | That session: `orca orchestration send --to dispatch:<id>` if it is a dispatch, otherwise the human's inbox ("send this to <session>") |
| Three or more tickets that must land together | A program coordinator (`orchestrate` program mode) in its own session |
| Work inside a running program | Its coordinator. Never steer its workers past it |

## Sessions you did not start

Once adopted they are on the roster, and you only read them. Never answer their permission prompts, trust prompts or questions, and never type into them except the reload in "Skills changed". What they need from the human goes to the inbox.

## Starting a worker

- `worker-start` returning `outcome_unknown` or `turn_start_unobserved` means "not confirmed", not success.
- Confirm the start from a background subagent, never a foreground loop. It reads `orca terminal read --screen` (the default stream read never showed the trust prompt) until one of these shows:
  - Claude working or idle on the task: done.
  - The folder trust prompt (`Yes, I trust this folder`): the first worker in a repo whose main clone was never trusted. Trust is recorded against the main clone's path, not the worktree's. Put it in the inbox as a login/confirm item and move on.
  - A shell with a bracketed-paste remnant (`^[[200~You are working inside Orca…`): the trust prompt ate the Enter and Claude exited. Send `^C`, save `orca orchestration dispatch-show --task <id> --preamble` to a file, check that the saved preamble still carries the `--dispatch-capability` value, and start `claude "<read that file and follow it>"` in the terminal.
- A worker whose preamble lost its capability value cannot send `heartbeat`, `ask` or `worker_done` ("The Dispatch capability is missing"). Resend it the value with `send --to dispatch:<id>`.

## Talking to sessions

- Check the exit status of every `orca orchestration send`. A completed dispatch refuses mail ("its worker will never read that mailbox"): send to `run:<id>`, or start a new dispatch for new work.
- `terminal send --wait-submit` can warn "no turn start was observed" when the turn did start. Read `--screen` before sending again, or the instruction lands twice.
- Put message bodies in a file and pass `--body "$(cat <file>)"`. A body that merely mentions kill, deploy or merge gets the whole command refused by the auto-mode classifier.
- Typing into another session's composer is only for the one-line nudge in SKILL.md and the reload below. Both pass the idle check first.

## Asking the human

- AskUserQuestion blocks your turn until the human answers; one question held a coordinator for 44 minutes while 16 worker messages piled up. Write the decision into your reply and the inbox, end the turn, and act when the answer arrives.
- Deploys, merges without review, killing processes, anything the classifier refuses: an inbox item of type "run a command", with the exact command. The human runs it with `!` or approves it in words; then you run it.

## Injected notices

Orca may type `You have N orchestration message(s). Run orca orchestration check …` into your composer and press Enter, even mid-sentence. When a human message ends with that notice, the text before it is the human's and may be cut off. Answer what is there, say in one line where it was cut, and hand the mailbox check to the background wait.

## Skills changed

After an `agent-skills` change reaches the loaded checkout, run `skills-sync broadcast` (`scripts/sync.py`, `docs/platform.md` §4). It sends `/reload-skills` or `/reload-plugins` only to sessions idle at an empty prompt and keeps the rest pending. Never type the reload into sessions yourself.
