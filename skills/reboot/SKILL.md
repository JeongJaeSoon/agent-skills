---
name: reboot
description: "Use first after this session, Orca or the machine restarted, or after a compaction — \"재시작했어\", \"재부팅했어\", \"Orca 재시작했어\", \"세션 다시 띄웠어\", \"복구 점검해줘\", `/agent-skills:reboot` — to put back what the restart dropped (Run binding, inbox wait, workers' terminals). What the work was is recall."
---

# Recover after a restart

A restart (this session, `claude --continue` in a new tab, Orca, the machine) or a compaction leaves the work where it was but can drop what keeps it moving: the terminal's Run binding, its handle and tab title, the background inbox wait and card watch, the terminals of workers, a heavy start the pressure gate held, background tasks. This skill checks those in one pass and puts back what is missing. Each rule lives where this skill cites it; this skill holds only the order and the check.

## 1. Run the check

The role comes from the reorient hook's reminder: `ROLE: lead <slug>`, `ROLE: orchestrator`, or none for a worker or a plain session. A session that held a role and gets no reminder declares it again first (`orchestrate` "Roles").

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/reboot.py" --slug <slug>   # a lead
python3 "${CLAUDE_SKILL_DIR}/scripts/reboot.py" --top           # the orchestrator
python3 "${CLAUDE_SKILL_DIR}/scripts/reboot.py"                 # a worker or a plain session
```

The orchestrator's own turn may not run it (the plugin's delegate gate allows only coordination commands there), so the orchestrator delegates the `--top` line to a subagent (`orchestrate` core rule 1) that returns its output unchanged; a subagent runs inside the same session process, so it counts the same waits.

It only reads: Orca's terminal, Run and worker records, this session's process tree, the pressure queue. It prints one line per finding, `ok`, `info`, or `do` with the command to run. Exit 0: nothing to put back. Exit 1: run each `do` line in the order printed, each in its own call. Exit 2: Orca did not answer; once the Orca app is up, run the check again, and report a second exit 2.

## 2. What the `do` lines put back

- **A stale handle.** Orca restarted under this session, so `ORCA_TERMINAL_HANDLE` names a terminal Orca no longer knows, and a message sent from it is refused. The line lists the live terminals of this worktree. Name the one this session runs in with `--from` or `--terminal` on every `orca` call, or start the session again in it with `claude --continue`. Until then the check skips the Run and worker checks.
- **The Run binding:** `orchestrate` `references/top-level.md` "Your Run" for the orchestrator, `orchestrate` "Resume" step 2 for a lead. A lead started as a dispatched worker binds nothing; the check then reads its Run from its own Dispatch and checks only the workers under it.
- **A session that never reloaded.** An `info` line says `reload needed` when this session loaded another `hooks/hooks.json` than the checkout has now (the SessionStart record in `terminals/<handle>.json`); the session lacks the newer hooks and cannot warn itself. Restart it with `claude --continue` in this terminal. A session with no record gets an `info` line saying so, not a verdict. It never sets exit 1 and blocks nothing.
- **The tab title,** the orchestrator's only (`references/top-level.md`, its opening paragraph).
- **Waits,** counted against `orchestrate` core rule 6. Start a missing one under `run_in_background`. Stop an extra one with `TaskStop` on the background task that started it; the pids in the line only tell them apart.
- **A worker that lost its terminal.** The line prints the stop and retry pair from `orchestrate` Drain (the `outcome_unknown` line), with the model Orca recorded; when it recorded none, the line says where to find it. A worker under another lead gets no such pair: the line names that lead, which restarts its own worker (`orchestrate` core rule 4). When Orca refuses the retry, follow `orca skills get orchestration --reference recovery-and-cleanup`.
- **An abandoned worker.** A restart that takes a worker's terminal makes Orca settle its Dispatch as `abandoned` (or `interrupted`), so `worker-stop` and `--retry-of` have nothing to act on. The line prints the resume of "Resume a worker" below. A worker that reported (`completed`, `failed`) is not woken: the check only counts it in an `info` line. A worker already resumed (a newer Dispatch on its worktree, or, from another Run, a live agent terminal there that has a Dispatch) gets an `info` line, not a resume. A live agent terminal with no Dispatch is a resume cut short between steps 2 and 3 below: the line asks only for the `worker-start --terminal`. If you ended an abandoned worker on purpose, leave its line.
- **A dispatched lead that lost its Dispatch.** Its program names a `lead_dispatch` Orca no longer answers for, and the Run in `program.json` is its coordinator's, so the check does not send it to `run-use` there (that would take the Run's binding from the coordinator). It prints `orca orchestration run-create` for a Run of its own, then `orch set <slug> run <the new Run>`, which accepts only the Run this terminal is bound to, refuses a terminal that is still a worker of it, and drops `lead_dispatch`. Run the check again afterwards. A coordinator that resumed the lead with `worker-start` gives it a new Dispatch the program does not name; the check treats that the same way. Units the lead started under the old Dispatch stay on the old Run, which the lead's check no longer reads: list them with `worker-list --run <old Run>` and resume the abandoned ones as below. `worker-start` from the lead's terminal attaches the new Dispatch to the lead's own Run.
- **A held heavy start** (`reap-resources` "Pressure"). `waiting`: its Monitor on `queue wait` again. `gave_up`: it never ran, and its brief's rule for a held start decides, otherwise report it to whoever gave the work. `retrying` or `failed`: the retry was started, so find its result before running anything again. A `gave_up` or `failed` entry leaves the check `rowstate.QUEUE_ENDED_H` hours after it ended, by the rule its row's ❔ follows (`rowstate.queue_over`); `pressure.py queue` still lists it.

## Resume a worker

For a worker the check printed as abandoned, in this order, each in its own call:

1. Find its session id (a Claude worker's; the line names another agent's own resume command, whose session store you read instead): the newest transcript under `~/.claude/projects/<the worktree path with every character other than a letter or digit written as ->/` (`/work/my_app` is `-work-my-app`), the file name without `.jsonl`. Check that it is this worker's: its first lines match the brief's task, and its last write is not later than the restart. Another session opened in the same worktree since (a diagnosis, a person) has a newer file; resume the worker's, not that one. With no match, ask whoever gave the work instead of guessing. Its model alias is the one the line prints (`--model`), else the one in its brief.
2. Open a terminal in its worktree that resumes that session: `orca terminal create --worktree id:<worktree id> --command "claude --resume <session-id> --model <alias>"`. It prints the new terminal's handle. The line gives the worktree as its whole id (`<repo>::<path>[::workspace:<n>]`), not the path: workspaces can share a path, and a `path:` selector could pick another card.
3. Write what the worker should do next to a file (read what it was doing from its brief and its last turns; a restart is no reason to restate the whole brief), then attach a new Dispatch to that terminal: `orca orchestration worker-start --spec-file <that file> --worktree id:<worktree id> --terminal <the new handle>`. That, not the terminal alone, gives the worker its mailbox and `worker_done` back.
4. Run the check again: the worker's newest Dispatch is now `dispatched`, so the line is gone.

The janitor leaves the card of an abandoned worker alone while it holds uncommitted changes or an open PR (`reap-resources` `references/cards.md`), so the worktree is still there to resume in.

## 3. What the check cannot see

- **Background tasks and subagents.** A process restart ends them; a compaction keeps them. For each one the transcript shows started with no completion notice, read its output file or its transcript first, and start again only the ones with no result.
- **The program itself.** A lead then does `orchestrate` "Resume" steps 1, 3 and 4 and goes on at Drain; the orchestrator runs `orch doctor` (`references/top-level.md` "Your Run") and goes on with the inbox. A worker reads mail sent meanwhile with `orca orchestration check` and goes on with its brief.
- **A machine restart** also ends every lead and worker terminal, and a resumed lead comes back without its `/goal`. The orchestrator starts each lead again on its own worktree with `claude --continue`, confirms its screen shows the goal active (sends `/goal` again where it does not), and has it run this skill. Anything a program needs after a restart lives under `~/.claude/programs/<slug>/` or another persistent state directory, never `/tmp`, which a reboot wipes.
- **A compaction.** The reorient hook prints `orchestrate`'s core rules again and tells a declared lead what to reload. Run this check before those steps; it mostly confirms the waits survived.

## Report

One line per `do` line: what ran and its output. Then what the check could not see and what was done about it, and what is still open.
