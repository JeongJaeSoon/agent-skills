---
name: reflect
description: "Spawn three parallel review subagents over the active transcript or a finished program, surface learnings, record them in the lessons ledger, and route each to a concrete edit on an existing skill. Use when the user says reflect, \"스킬에 반영해줘\", \"스킬이 왜 안 떴어\", \"스킬 갱신이 필요해\", \"이 세션 돌아보고 스킬 개선해줘\", \"개선 이력 보여줘\"; at orchestrate's Close (program mode); each round of the flow improver or a `/loop` (standing mode); and from end-session when the human corrected the work (session mode)."
---

# Reflect

Mine the work for durable learnings, record each in the lessons ledger, then route them into skill edits.

## When to invoke

| Mode | Invoked by | Input | Who approves edits |
|---|---|---|---|
| session | the user ("reflect", "/reflect"), or `end-session` when the human corrected the work | this conversation's transcript | the user, in this session |
| program | `orchestrate` Close, unattended | the program's evidence pack (step 1) | the user, later, through one draft PR |
| standing | the flow improver each round (`orchestrate` `references/roles.md`), or `/loop` in a session when no flow improver runs, unattended | what is new since the last round (step 1) | the user, later, through one draft PR |

Skip when the work is trivial, off-topic, or already covered by an existing skill the parent followed correctly. One-offs are not learnings: a finding changes a skill only once it has two independent occurrences (step 4).

"개선 이력 보여줘" and similar only read the ledger: show its open candidates and the last applied lessons with their outcome, and stop.

## The lessons ledger

One note in the notes store (`use-notes`): `Project/agent-skills/learnings.md`. It lives there, not in the public repo, because evidence names private projects and tickets. Create it on first use with this table and a `## History` section under it:

| ID | First seen | Source | Kind | Evidence | Occurrences | Learning | Target | Status | Change | Verification | Outcome |
|---|---|---|---|---|---|---|---|---|---|---|---|

- **ID** `L-<n>`, never reused. **Source** the program slug or session. **Kind** a signal kind (`human_correction`, `brief_gap`, `stall`, `tooling`), `land_failed`, `main_red`, `verdict_fail`, `review` for a finding the reviewers drew from the whole record rather than one signal, or `usage` for a skill the usage collector flagged (Source `skill-usage`, Learning `<skill>: <flag>`). **Evidence** pointers only (message ID, PR comment URL, transcript path and line), never copied text; a program-ledger row with no `evidence` field is pointed to as `<slug>:<ev>:<ts>:<pr or sha>`, in every mode, so the same row is never counted twice.
- **Status** is `candidate`, then `proposed` (a draft PR exists), or in standing mode `waiting (standing PR)` for an Accepted row until step 5 opens its PR, then `applied` (merged) or `rejected (<reason>)`; `backlog (<ticket>)` for a finding filed to the tracker for the human; `retired` once its target is gone or it stopped being true.
- **Verification** holds only what a script, CI or `measure-delivery` printed (trigger-probe counts before and after, test names). A reviewer's opinion is not verification.
- **Outcome** is filled by later programs: `recurred (<source>)`, or `held through <n> programs (<slug>, ...)` listing each program counted, so no program counts twice.
- A change to a row edits that row in place and appends one dated line to `## History` (`2026-09-25 L-4 candidate to proposed: <PR URL>`), so the history survives.

## Process

### 1. Gather the input

**Program mode.** Build an evidence pack at `~/.claude/programs/<slug>/reflect/pack.md`:
- From the ledger: every `signal` row, every `land_failed`, `main_red` and failed `verdict`, each with its ticket, PR and evidence pointer. A `main_red` row carries only a sha: its PRs are every `landed` row with that sha (a stack lands several at once), and each PR's ticket is on its `ready` row or, when the ledger has none, in the PR's title, branch or body (`gh pr view`); write "no ticket" when none names one. When the ledger has no `verdict` rows at all, write "verdicts not recorded", not 0.
- Rows with note `backfill` were recorded by `orch backfill` for merges before the program was registered. They carry no process evidence: give their count on one line, and leave them out of everything below, the stop rule included.
- The decision trail (`decisions.tsv`, or the `decision` rows in `dashboard/notes.jsonl`), the program note's digest and standing orders as they ended, and the `measure-delivery` output from this Close, saved as `reflect/delivery.md` beside the pack. Write "missing" for any that do not exist.
- The transcripts behind the signals: the coordinator's own (reflect runs in the coordinator's session, so find it as in session mode below), and a worker's when a signal names it (its worktree path encoded the same way).
- Every `applied` ledger row whose Outcome is not `recurred`. Update its Outcome now, judged against the lesson's own Learning, not its Kind: `recurred (<slug>)` when this program repeats the failure that lesson targets; add this slug to its `held through` list when the program ran the step the lesson changed and the failure did not return. A program that never ran that step leaves the row unchanged.

Check each signal against the skills at current HEAD of the `agent-skills` checkout and mark in the pack the ones HEAD already fixes (with the file and line that fixes it), so the reviewers and the synthesizer do not propose them again. `git log --oneline <skills_commit>..HEAD -- skills/`, with `skills_commit` from `program.json` minus any `+local` suffix, lists what changed since the program started.

With no signal and no failure left open after that, stop after the Outcome update. Otherwise pass the pack path to the reviewers in place of the transcript path.

**Standing mode.** Rounds do not overlap: `mkdir -p ~/.claude/programs/_standing/reflect`, then take `mkdir ~/.claude/programs/_standing/reflect/lock` and write the start time into it. Skip the round while the lock exists, unless it started more than 4 hours ago (stale: remove it). Remove it when the round ends.

Then settle the last standing PR, the newest in the `agent-skills` repo whose branch starts `reflect/standing-` (`gh pr list -R <repo> --state all --limit 100 --json number,state,headRefName,createdAt`): merged, its rows go `applied`; closed unmerged, `rejected (PR closed)`.

New means a `signal`, `land_failed`, `main_red` or failed `verdict` row at or after the cursor, whose pointer the lessons ledger does not hold yet, in any `~/.claude/programs/*/ledger.jsonl`, the usage collector's `_skill-usage/ledger.jsonl` included (its `skill_usage` signals carry evidence `skill-usage:<skill>:<flag>@<window>`). The cursor, `~/.claude/programs/_standing/reflect/cursor.json`, holds the last `ts` each ledger gave a finished round; a ledger it does not list yet starts at its newest row.

Standing mode writes an Accepted row as `waiting (standing PR)` in step 4, and step 5 takes every waiting row once no standing PR is open: the HEAD check first (a row HEAD already fixes goes `retired`), then the edits, the PR, and `proposed`. While a standing PR is open, step 5 still files Backlog tickets and opens no PR.

With nothing new and no waiting row for step 5, stop and send nothing. With only waiting rows, go straight to step 5. Otherwise build the pack at `~/.claude/programs/_standing/reflect/pack-<UTC timestamp>.md` over the new rows only: program mode's ledger and backfill bullets, the transcripts each row's evidence points to, and the HEAD check, which also catches a lesson the mail path already committed. The decision trail, `measure-delivery` and the Outcome update belong to Close. Continue from step 2, and move the cursor once step 4 has written the lessons ledger, so a round that dies early is read again.

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
| Tooling | Codex: `node <codex plugin>/scripts/codex-companion.mjs task --background "$(cat <filled prompt file>)"`, the configured default model, then wait with `status <job id> --wait --timeout-ms 1800000` as a Bash call with `run_in_background`, which wakes you when the job ends, and read `result <job id>`. Ending the turn to wait instead ends an unattended run for good. Read-only without `--write`. Find the script with `ls ~/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs`. Codex sees only its own MCP servers, so inline any ticket or thread it will need. | `references/tooling-reviewer.md` |
| Divergent | `Agent`, `model: "opus"` | `references/divergent-reviewer.md` |

Pass each template verbatim, substituting the transcript path or digest where marked. Reviewers return findings in their final response (Codex: `result <job id>`).

### 3. Synthesize

One `Agent` call, `subagent_type: "general-purpose"`, `model: "opus"`. The synthesizer's quality check includes spot-verifying citations, which can require MCP access. The read-only agents lack it. Use `references/synthesizer.md` verbatim, with each reviewer's full output inlined where marked. The synthesizer returns a structured Accepted / Rejected / Backlog list.

### 4. Record, count, and check structure

Write every Accepted, Backlog and Rejected finding to the lessons ledger. A finding that matches an existing row adds its evidence and raises Occurrences instead of making a new row; evidence the row already holds raises nothing.

- An Accepted finding stays Accepted only with two or more independent occurrences (different tickets, workers or sessions), or when it is a reproduced security or data defect. A `usage` row is a measurement: a later window of the same skill and flag raises Occurrences on its row, its two occurrences are two windows that do not overlap, and a window on a `rejected` row adds its evidence and leaves it rejected. Count occurrences from the ledger's rows and from the `signal` rows in other programs' ledgers in the store, since a program that ran before the ledger existed left no rows. Otherwise it stays `candidate` in the ledger and waits for a second occurrence.
- For any item that would be enforced more reliably by a lint rule, script, metadata flag, or runtime check, move it from Accepted to Backlog. See the **encode-lessons-in-structure** principle skill.
- Never propose changes to `hooks/guard.py`, the `orch land` gate in `skills/orchestrate/scripts/prog.py`, tests or eval graders, or this skill. A finding about one of them goes to Backlog for the human.

### 5. Apply

**Program and standing mode** run with nobody to approve, so they propose instead of applying:
1. Make the Accepted edits in one worktree branch of the `agent-skills` checkout (routing below). An edit that changes a command, flag or term greps all of `skills/` and changes every copy in the same branch.
2. Verify each against the change it makes, and record the printed result in Verification.
   - A description change: `bash scripts/trigger-probe.sh` on the old and the new checkout, at least 3 runs each, with one prompt that should fire the skill and one that should not.
   - A body or script change: `claude plugin validate <checkout>` and a check of the changed path itself: a test under the skill that fails without the change, or the incident replayed with the tools it needs in a scratch copy of the repo, with the asserted result. `trigger-probe.sh` denies writes and shell commands and reports only which skills fired, so it verifies triggering, never behavior.
   - With no such check, write `Verification: none` in the row and the PR body.
3. Open at most one draft PR (`gh pr create --draft`) titled with the lesson IDs, body per `write-plainly`. Never merge it. Standing mode opens it from a branch `reflect/standing-<UTC timestamp>`.
4. Mark the rows `proposed`, and put one line per lesson plus the PR link in the program digest for approval (standing mode: in the round's report).

A `usage` row about a skill that other skills invoke (the development flow) or that runs only at a rare moment (Close, a legacy alias) is rejected with that reason. For the rest, judge why the skill goes unused; the flag picks the route and the collector's `suggest` is only a hint. `misses` and `slash_only` go to `tune description`; `unused_30d` to a merge into the skill it overlaps (a substantive edit), or with no overlap to Backlog, a retirement ticket for the human.

With no Accepted finding, skip the PR and write one digest line saying why (standing mode: nothing; a round reports only a PR or a ticket it filed).

**Session mode.** Before applying any Accepted edit, present the synthesizer's full Accepted/Rejected/Backlog output to the user and wait for explicit approval. The user picks which subset to apply and may redirect routings. Skill changes reach every session that loads this plugin, so nothing is applied without that approval.

In every mode, Backlog items file to the user's ticket tracker (the **use-tracker** skill) automatically, and the ticket goes in the row's Status. Only the Accepted list waits for approval.

This plugin's skills (`agent-skills`) come from the `JeongJaeSoon/agent-skills` repo. Make every edit in a checkout of it, on a worktree branch, never in the installed copy under `~/.claude/plugins/cache/`, which an update replaces. `claude --plugin-dir <checkout>` loads the edited copy for a test. A skill from another plugin is not edited in place; record the finding as Backlog instead.

For each approved Accepted item, follow the Routing field exactly:

- Trivial existing-skill edit (a one-line bullet, a tightened sentence, a stale fact corrected): parent does directly.
- Substantive existing-skill edit (a new section, a new pattern table, more than ~10 lines): hand to the **skill-creator** skill and run its draft / test / iterate loop.
- `tune description: <skill path>` (the skill exists but didn't trigger when it should have): hand to **skill-creator** and run its description-optimization loop.
- `new skill via skill-creator: <kebab-name>`: hand creation to **skill-creator**. Do not invent the shape ad hoc.

Before declaring done, run the `write-skill` steps on the change (prompt audit, internal-name grep, tests, `claude plugin validate`) and fix what they report. Put the lesson IDs in the commit message (`L-4`), and mark the rows `applied` once the change reaches main.

### 6. Summarize for the user

Short list, no preamble:

- Edits applied or proposed: `<skill path>` (`L-<n>`). What changed, one line each, with the PR in program and standing mode.
- Candidates waiting for a second occurrence: `L-<n>`, one line each.
- New skills created: `<skill path>`. One line each (rare).
- Backlog filed to the tracker: `<issue title>` (`<tags>`). One line each.
- Dropped: one line per rejected finding + reason from the synthesizer.
