---
name: reflect
description: "Spawn three parallel review subagents over the active transcript or a finished program, surface learnings, record them in the lessons ledger, and route each to a concrete edit on an existing skill. Use when the user says reflect, \"스킬에 반영해줘\", \"스킬이 왜 안 떴어\", \"스킬 갱신이 필요해\", \"이 세션 돌아보고 스킬 개선해줘\", \"개선 이력 보여줘\"; at orchestrate's Close (program mode); and from end-session when the human corrected the work (session mode)."
---

# Reflect

Mine the work for durable learnings, record each in the lessons ledger, then route them into skill edits.

## When to invoke

| Mode | Invoked by | Input | Who approves edits |
|---|---|---|---|
| session | the user ("reflect", "/reflect"), or `end-session` when the human corrected the work | this conversation's transcript | the user, in this session |
| program | `orchestrate` Close, unattended | the program's evidence pack (step 1) | the user, later, through one draft PR |

Skip when the work is trivial, off-topic, or already covered by an existing skill the parent followed correctly. One-offs are not learnings: a finding changes a skill only once it has two independent occurrences (step 4).

"개선 이력 보여줘" and similar only read the ledger: show its open candidates and the last applied lessons with their outcome, and stop.

## The lessons ledger

One note in the notes store (`use-notes`): `Project/agent-skills/learnings.md`. It lives there, not in the public repo, because evidence names private projects and tickets. Create it on first use with this table and a `## History` section under it:

| ID | First seen | Source | Kind | Evidence | Occurrences | Learning | Target | Status | Change | Verification | Outcome |
|---|---|---|---|---|---|---|---|---|---|---|---|

- **ID** `L-<n>`, never reused. **Source** the program slug or session. **Kind** a signal kind (`human_correction`, `brief_gap`, `stall`, `tooling`) or `land_failed`, `main_red`, `verdict_fail`. **Evidence** pointers only (message ID, PR comment URL, transcript path and line), never copied text.
- **Status** is `candidate`, then `proposed` (a draft PR exists), then `applied` (merged) or `rejected`; `retired` once its target is gone or it stopped being true.
- **Verification** holds only what a script, CI or `measure-delivery` printed (trigger-probe counts before and after, test names). A reviewer's opinion is not verification.
- **Outcome** is filled by later programs: `recurred (<source>)`, or `held through <n> programs (<slug>, ...)` listing each program counted, so no program counts twice.
- A change to a row edits that row in place and appends one dated line to `## History` (`2026-09-25 L-4 candidate to proposed: <PR URL>`), so the history survives.

## Process

### 1. Gather the input

**Program mode.** Build an evidence pack at `~/.claude/programs/<slug>/reflect/pack.md`:
- From the ledger: every `signal` row, every `land_failed`, `main_red` and failed `verdict`, each with its ticket, PR and evidence pointer.
- `decisions.tsv`, the program note's digest and standing orders as they ended, and the `measure-delivery` report.
- The transcripts behind the signals: the coordinator's own, and a worker's when a signal names it (its worktree path encoded as below).
- Every `applied` ledger row whose Outcome is not `recurred`. Update its Outcome now, judged against the lesson's own Learning, not its Kind: `recurred (<slug>)` when this program repeats the failure that lesson targets; add this slug to its `held through` list when the program ran the step the lesson changed and the failure did not return. A program that never ran that step leaves the row unchanged.

With no signal and no failure in the pack, stop after the Outcome update. Otherwise pass the pack path to the reviewers in place of the transcript path.

**Session mode.** The parent finds its own transcript file before fanning out. Claude Code writes it under `~/.claude/projects/<cwd with every / and . replaced by ->/`, for this session's working directory. Use that path. Do not glob across `~/.claude/projects/*/`. That crosses workspace boundaries and reads private chats from unrelated projects.

```bash
ls -t <project-dir>/*.jsonl <project-dir>/*/subagents/*.jsonl 2>/dev/null | head -10
```

Two transcript layouts: session (`<session-id>.jsonl`) and subagent (`<session-id>/subagents/agent-<id>.jsonl`).

For each candidate, find the first JSONL line with `"type":"user"` and check that its `message.content` contains the conversation's opening user prompt. Take the matching path. If no path resolves, write a tight digest of the session and pass that instead.

### 2. Spawn three reviewers in parallel

One message, three reviewers launched together. Claude reviewers use the `Agent` tool with `subagent_type: "general-purpose"` and an explicit `model`, not the read-only `Explore`/`Plan` agents: reviewers need MCP access for context lookups (tickets, chat threads, observability traces referenced in the transcript), and the read-only agents lack it. The templates already forbid edits.

| Lens | Runner | Prompt template |
|---|---|---|
| Judgment | `Agent`, `model: "opus"` | `references/judgment-reviewer.md` |
| Tooling | Codex: `node <codex plugin>/scripts/codex-companion.mjs task --background "$(cat <filled prompt file>)"`, the configured default model. Read-only without `--write`. Find the script with `ls ~/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs`. Codex sees only its own MCP servers, so inline any ticket or thread it will need. | `references/tooling-reviewer.md` |
| Divergent | `Agent`, `model: "opus"` | `references/divergent-reviewer.md` |

Pass each template verbatim, substituting the transcript path or digest where marked. Reviewers return findings in their final response (Codex: `result <job id>`).

### 3. Synthesize

One `Agent` call, `subagent_type: "general-purpose"`, `model: "opus"`. The synthesizer's quality check includes spot-verifying citations, which can require MCP access. The read-only agents lack it. Use `references/synthesizer.md` verbatim, with each reviewer's full output inlined where marked. The synthesizer returns a structured Accepted / Rejected / Backlog list.

### 4. Record, count, and check structure

Write every Accepted, Backlog and Rejected finding to the lessons ledger. A finding that matches an existing row adds its evidence and raises Occurrences instead of making a new row.

- An Accepted finding stays Accepted only with two or more independent occurrences (different tickets, workers or sessions), or when it is a reproduced security or data defect. Otherwise it stays `candidate` in the ledger and waits for a second occurrence.
- For any item that would be enforced more reliably by a lint rule, script, metadata flag, or runtime check, move it from Accepted to Backlog. See the **encode-lessons-in-structure** principle skill.
- Never propose changes to `hooks/guard.py`, the `orch land` gate in `skills/orchestrate/scripts/prog.py`, tests or eval graders, or this skill. A finding about one of them goes to Backlog for the human.

### 5. Apply

**Program mode** runs with nobody to approve, so it proposes instead of applying:
1. Make the Accepted edits in one worktree branch of the `agent-skills` checkout (routing below).
2. Verify each against the change it makes, and record the printed result in Verification.
   - A description change: `bash scripts/trigger-probe.sh` on the old and the new checkout, at least 3 runs each, with one prompt that should fire the skill and one that should not.
   - A body or script change: `claude plugin validate <checkout>` and a check of the changed path itself: a test under the skill that fails without the change, or the incident replayed with the tools it needs in a scratch copy of the repo, with the asserted result. `trigger-probe.sh` denies writes and shell commands and reports only which skills fired, so it verifies triggering, never behavior.
   - With no such check, write `Verification: none` in the row and the PR body.
3. Open at most one draft PR (`gh pr create --draft`) titled with the lesson IDs, body per `write-plainly`. Never merge it.
4. Mark the rows `proposed`, and put one line per lesson plus the PR link in the program digest for approval.

With no Accepted finding, skip the PR and write one digest line saying why.

**Session mode.** Before applying any Accepted edit, present the synthesizer's full Accepted/Rejected/Backlog output to the user and wait for explicit approval. The user picks which subset to apply and may redirect routings. Skill changes reach every session that loads this plugin, so nothing is applied without that approval.

Backlog items file to the user's ticket tracker (the **use-tracker** skill) automatically. Only the Accepted list waits for approval.

This plugin's skills (`agent-skills`) come from the `JeongJaeSoon/agent-skills` repo. Make every edit in a checkout of it, on a worktree branch, never in the installed copy under `~/.claude/plugins/cache/`, which an update replaces. `claude --plugin-dir <checkout>` loads the edited copy for a test. A skill from another plugin is not edited in place; record the finding as Backlog instead.

For each approved Accepted item, follow the Routing field exactly:

- Trivial existing-skill edit (a one-line bullet, a tightened sentence, a stale fact corrected): parent does directly.
- Substantive existing-skill edit (a new section, a new pattern table, more than ~10 lines): hand to the **skill-creator** skill and run its draft / test / iterate loop.
- `tune description: <skill path>` (the skill exists but didn't trigger when it should have): hand to **skill-creator** and run its description-optimization loop.
- `new skill via skill-creator: <kebab-name>`: hand creation to **skill-creator**. Do not invent the shape ad hoc.

Before declaring done, run `claude plugin validate <checkout>` and fix what it reports. Put the lesson IDs in the commit message (`L-4`), and mark the rows `applied` once the change reaches main.

### 6. Summarize for the user

Short list, no preamble:

- Edits applied or proposed: `<skill path>` (`L-<n>`). What changed, one line each, with the PR in program mode.
- Candidates waiting for a second occurrence: `L-<n>`, one line each.
- New skills created: `<skill path>`. One line each (rare).
- Backlog filed to the devex tracker: `<issue title>` (`<tags>`). One line each.
- Dropped: one line per rejected finding + reason from the synthesizer.
