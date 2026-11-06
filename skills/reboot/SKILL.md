---
name: reboot
description: "Use first after this session, Orca or the machine restarted, or after a compaction — \"재시작했어\", \"재부팅했어\", \"Orca 재시작했어\", \"세션 다시 띄웠어\", \"복구 점검해줘\" — to restore the Run binding, inbox wait and workers' terminals. What the work was is recall."
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

It only reads: `orca status` first, then Orca's terminal, Run and worker records, this session's process tree, the pressure queue. It prints one line per finding, `ok`, `info`, or `do` with the command to run. Exit 0: nothing to put back. Exit 1: run each `do` line's command as printed, in the order printed, each in its own call. Exit 2: Orca did not answer or is not ready; once the Orca app is up, run the check again, and report a second exit 2.

## 2. What the `do` lines do not say

- **A stale handle.** Until this session speaks for a live terminal (`--from` or `--terminal` on every `orca` call, or `claude --continue` in that terminal), the check skips the Run and worker checks.
- **A dispatched lead.** One started as a dispatched worker binds nothing; the check reads its Run from its own Dispatch. One that lost its Dispatch gets a Run of its own, never `run-use` on its coordinator's Run, which would take that Run's binding from the coordinator. Units it started under the old Dispatch stay on the old Run, which its check no longer reads: list them with `worker-list --run <old Run>` and resume the abandoned ones as below.
- **The session name** (the orchestrator's only): the tab title is compared without Claude Code's status glyph (`◐`, `✳`, a braille frame). A session resumed without `--name` has no name; the line asks for `/rename orchestrator`, and a restart is `claude --continue --name orchestrator`. A name the check cannot read gives no line.
- **`reload needed`** (`info`): restart this session with `claude --continue` in this terminal. It never sets exit 1.
- **Waits** are counted against `orchestrate` core rule 6. Stop an extra one with `TaskStop` on the background task that started it; the pids in the line only tell them apart.
- **A worker that lost its terminal** under another lead is that lead's to restart (`orchestrate` core rule 4). When Orca refuses a retry, follow `orca skills get orchestration --reference recovery-and-cleanup`.
- **An abandoned worker** is resumed as below. A worker that reported (`completed`, `failed`) is not woken. If you ended one on purpose, leave its line.

## Resume a worker

For a worker the check printed as abandoned, in this order, each in its own call:

1. Find its session id (a Claude worker's; the line names another agent's own resume command, whose session store you read instead): `python3 "${CLAUDE_SKILL_DIR}/scripts/reboot.py" sessions <its worktree path> --limit 5` lists the sessions that ran there, newest first, with each one's last write and first prompt. The worker's is the one whose first prompt is its brief and whose last write is not later than the restart; another session opened there since (a diagnosis, a person) is newer, so do not take the top line on trust. With no match, ask whoever gave the work instead of guessing. Its model alias is the one the line prints (`--model`), else the one in its brief.
2. Open a terminal in its worktree that resumes that session: `orca terminal create --worktree id:<worktree id> --command "claude --resume <session-id> --model <alias>"`. It prints the new terminal's handle. The line gives the worktree as its whole id (`<repo>::<path>[::workspace:<n>]`), not the path: workspaces can share a path, and a `path:` selector could pick another card.
3. Write what the worker should do next to a file (read what it was doing from its brief and its last turns; a restart is no reason to restate the whole brief), then attach a new Dispatch to that terminal: `orca orchestration worker-start --spec-file <that file> --worktree id:<worktree id> --terminal <the new handle>`. That, not the terminal alone, gives the worker its mailbox and `worker_done` back.
4. Run the check again: the worker's newest Dispatch is now `dispatched`, so the line is gone.

The janitor leaves the card of an abandoned worker alone while it holds uncommitted changes or an open PR (`reap-resources` `references/cards.md`), so the worktree is still there to resume in.

## 3. What the check cannot see

- **Background tasks and subagents.** A process restart ends them; a compaction keeps them. For each one the transcript shows started with no completion notice, read its output file or its transcript first, and start again only the ones with no result.
- **The program itself.** A lead then does `orchestrate` "Resume" steps 1, 3 and 4 and goes on at Drain; the orchestrator runs `orch doctor` and goes on with the inbox. A worker reads mail sent meanwhile with `orca orchestration check` and goes on with its brief.
- **A machine restart** also ends every lead and worker terminal, and a resumed lead comes back without its `/goal`. The orchestrator gives one subagent the list of leads: it starts each again on its own worktree with `claude --continue`, confirms its screen shows the goal active (sends `/goal` again where it does not), has it run this skill, and returns one line per lead. Anything a program needs after a restart lives under `~/.claude/programs/<slug>/` or another persistent state directory, never `/tmp`, which a reboot wipes.
- **A `/clear`.** The reorient hook carries the role over to the new session and adds the handoff summary (`orch handoff`). The check's `info` line prints the handoff whole with the mail taken since it was saved; act on that mail first, then the handoff's `next`. The Monitor and background tasks survive a `/clear`, so the waits are mostly already there.
- **A compaction.** The reorient hook prints `orchestrate`'s core rules again and tells a declared lead what to reload. Run this check before those steps; it mostly confirms the waits survived.

## Report

One line per `do` line: what ran and its output. Then what the check could not see and what was done about it, and what is still open.
