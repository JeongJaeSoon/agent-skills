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

It only reads: Orca's terminal, Run and worker records, this session's process tree, the pressure queue. It prints one line per finding, `ok`, `info`, or `do` with the command to run. Exit 0: nothing to put back. Exit 1: run each `do` line in the order printed, each in its own call. Exit 2: Orca did not answer; once the Orca app is up, run the check again, and report a second exit 2.

## 2. What the `do` lines put back

- **A stale handle.** Orca restarted under this session, so `ORCA_TERMINAL_HANDLE` names a terminal Orca no longer knows, and a message sent from it is refused. The line lists the live terminals of this worktree. Name the one this session runs in with `--from` or `--terminal` on every `orca` call, or start the session again in it with `claude --continue`. Until then the check skips the Run and worker checks.
- **The Run binding:** `orchestrate` `references/top-level.md` "Your Run" for the orchestrator, `orchestrate` "Resume" step 2 for a lead.
- **The tab title,** the orchestrator's only (`references/top-level.md`, its opening paragraph).
- **Waits,** counted against `orchestrate` core rule 6. Start a missing one under `run_in_background`. Stop an extra one with `TaskStop` on the background task that started it; the pids in the line only tell them apart.
- **A worker that lost its terminal.** The line prints the stop and retry pair from `orchestrate` Drain (the `outcome_unknown` line). When Orca refuses the retry, follow `orca skills get orchestration --reference recovery-and-cleanup`.
- **A held heavy start.** A `waiting` entry needs its Monitor on `queue wait` again (`reap-resources` "Pressure"). A `failed` or `gave_up` entry never ran: its brief's rule for a held start decides, otherwise report it to whoever gave the work.

## 3. What the check cannot see

- **Background tasks and subagents.** A process restart ends them; a compaction keeps them. For each one the transcript shows started with no completion notice, read its output file or its transcript first, and start again only the ones with no result.
- **The program itself.** A lead then does `orchestrate` "Resume" steps 1, 3 and 4 and goes on at Drain; the orchestrator runs `orch doctor` (`references/top-level.md` "Your Run") and goes on with the inbox. A worker reads mail sent meanwhile with `orca orchestration check` and goes on with its brief.
- **A machine restart** also ends every lead and worker terminal, and a resumed lead comes back without its `/goal`. The orchestrator starts each lead again on its own worktree with `claude --continue`, confirms its screen shows the goal active (sends `/goal` again where it does not), and has it run this skill. Anything a program needs after a restart lives under `~/.claude/programs/<slug>/` or another persistent state directory, never `/tmp`, which a reboot wipes.
- **A compaction.** The reorient hook prints `orchestrate`'s core rules again and tells a declared lead what to reload. Run this check before those steps; it mostly confirms the waits survived.

## Report

One line per `do` line: what ran and its output. Then what the check could not see and what was done about it, and what is still open.
