---
name: recall
description: "Reconstruct recent working context from past Claude Code sessions, live state, and the shared record (tracker, PRs, worklogs, reported symptoms, reverted fixes), then hand back a tight current-state brief. Use for 'recall my work on X', 'catch me up', \"어디까지 했지\", \"X 작업 어디까지 했더라\", \"최근 작업 정리해줘\", \"이번 주에 뭐 했지\", before starting or resuming work that earlier sessions touched."
---

# Recall

**Before you start or resume work, rebuild the user's recent working context and hand back a tight capsule of where things stand now and what to do next.**

Keep it tight and on-topic. Read only what the in-scope threads need, then stop.

Context lives in two records. Past sessions hold what was done and decided. The shared record holds everything that happened around the same code under other names: tickets, PRs that shipped and got reverted, symptoms users keep reporting, worklogs. That second record is what the **why** skill searches. A feature with a long bug tail keeps most of its story there, so don't reconstruct it from transcripts alone.

## Where past sessions live

Claude Code writes each session to `~/.claude/projects/<dir>/<session-id>.jsonl`, where `<dir>` is the session's working directory with every `/` and `.` replaced by `-`. One JSON object per line. Orca cards each run in their own worktree, so one repo's history spans many `<dir>`s: its main checkout, every path in `git worktree list`, and cards already removed, whose `<dir>` starts with the encoded parent folder of the repo's worktrees (for Orca, `~/orca/workspaces/<repo>/`).

When `orca search --index-status` reports `enabled: true`, `orca search "<topic>" --since <iso> --sort newest --json` finds the matching sessions faster than grep. Otherwise grep the `<dir>`s above.

## Steps

1. Classify, then route. Resuming one specific earlier session is `claude --resume`, not this. If the user already gave a full state capsule (paths, branch, the change), use it and skip the mining.
2. Lock the scope before searching. Pin the window (default the last 7 days), the topic if named, and the repo (default the current one; never read another repo's sessions without being asked). State the scope back. Never quietly turn "all" into "recent N".
3. Mine past sessions. For one or two sessions, search directly. For more, spawn parallel `Agent` subagents with `model: "haiku"`, each taking a slice of the `<dir>`s. Tell each to order candidates by modification time (`ls -t`), grep the topic first, read only the matching sessions and only their relevant regions, and skip the current session plus noise (`subagents/` files, eval and probe sessions under a scratchpad or `/tmp` path). Each returns one block per session: topic, the user's goal, decisions, open threads, struggles and corrections, and artifacts (PRs, tickets, branches), citing the session file. The raw transcripts stay in the subagents.
4. Sweep the shared record whenever the topic names a feature, file, subsystem, area, or bug. This is the default, and "my work on X" does not exempt it. Hand it to the **why** skill's source investigators, with the question steered from "why was this built this way" to "what is the current state, what has been tried and didn't hold, and what are users still reporting". Run them in parallel with step 3. Also read the topic's worklog and design notes through `use-notes` and its tickets through `use-tracker`. Skip this step only for pure activity recall with no named target ("이번 주에 뭐 했지").
5. Verify against live state. Check the PRs, branches, tickets and cards that steps 3 and 4 surfaced with `git`, `gh`, `use-tracker` and `orca worktree list`. When the answer hinges on what an agent actually did (tools it ran, files it read, errors it hit), read the full transcript, not a summary of it.
6. Write the brief to the contract below, grouped by thread, on the named topic.

## Output contract

Lead with the capsule, then the thread status, then the problems, then the next move. Deeper detail goes below or gets cut.

- **Capsule.** At most 5 bullets. What this work is and where it stands overall.
- **Threads.** One line each, prefixed with exactly one status tag: `[merged #N]`, `[open PR #N]`, `[in flight <branch>]`, `[verified, uncommitted]`, `[reverted #N]`, `[planned, not started]`, or `[unverified]` when step 5 could not check it. A thread with no tag is not done yet, so tag it.
- **Problems.** At most 5, the recurring ones. Include the symptoms users keep reporting and any fix that shipped and was reverted, so the next attempt starts where the last one failed.
- **Next move.** The single most useful next action, concrete.

A source you could not read goes in one closing line as a gap; never turn it into a claim ("nothing changed since"). An adjacent feature or ticket stays out unless it blocks this one. When the brief outgrows a screen, cut detail before threads. Write it with `write-plainly`, in the user's language. Cite session findings by session file and shared-record findings by source (PR `owner/repo#N`, ticket ID, note path). Leave private context out of anything that will be posted publicly.
