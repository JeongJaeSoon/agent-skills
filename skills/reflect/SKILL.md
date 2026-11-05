---
name: reflect
description: "Review the active transcript or a finished program with three parallel reviewers, record the learnings in the lessons ledger, and route each to an edit on an existing skill. Use for reflect, \"스킬에 반영해줘\", \"스킬이 왜 안 떴어\", \"이 세션 돌아보고 스킬 개선해줘\", \"개선 이력 보여줘\"; at orchestrate's Close; and each flow-improver or /loop round."
---

# Reflect

Mine the work for durable learnings, record each in the lessons ledger, then route them into skill edits.

## When to invoke

| Mode | Invoked by | Input | Who approves edits |
|---|---|---|---|
| session | the user ("reflect", "/reflect"), or `end-session` when the human corrected the work | this conversation's transcript | the user, in this session |
| program | `orchestrate` Close, unattended | the program's evidence pack (step 1) | the user, later, through one draft PR |
| standing | the flow improver each round (`orchestrate` `references/roles.md`), the daily Orca automation whose precheck wakes it only on something new (below), or `/loop` in a session when neither runs, unattended | what is new since the last round (step 1) | the user, later, through one draft PR |

Skip when the work is trivial, off-topic, or already covered by an existing skill the parent followed correctly. One-offs are not learnings: a finding changes a skill only once it has two independent occurrences (step 4).

"개선 이력 보여줘" and similar only read the ledger: show its open candidates and the last applied lessons with their outcome, and stop. The dashboard's Skill changes page has the commits, open PRs and usage signals beside them.

## The lessons ledger

Use `reflect.ledger` in the skill configuration or an explicit path supplied for this task.
`reflect.orchestrator_feedback` (default `false`) adds the orchestrator's conversation to standing mode's input
(step 1); it reads nothing without `reflect.ledger`. `reflect.model` names the daily round's Claude model: `schedule`
passes it with `--extra-agent-args`.
Keep private evidence outside public repositories. If no destination is configured, report
findings in the current task; do not invent a shared note location. Existing ledgers stay at
their current path and retain their IDs and history. A new ledger uses this table and a
`## History` section:

| ID | First seen | Source | Kind | Evidence | Occurrences | Learning | Target | Status | Change | Verification | Outcome |
|---|---|---|---|---|---|---|---|---|---|---|---|

- **ID** `L-<n>`, never reused, and minted only by writing the row to the ledger: the next ID is the one `scripts/lesson_ids.py --repo <skill checkout> --rev origin/main < <ledger text>` prints, which also counts IDs this repo's history cites; the IDs it lists as `foreign` were cited without a ledger row, and a relayed row keeps the ID its commit cites. A session that cannot write the ledger names its findings `<source>-<n>` in files, commits and PR text; they get their `L-<n>` when relayed. **Source** the program slug or session. **Kind** a signal kind (`human_correction`, `brief_gap`, `stall`, `tooling`), `land_failed`, `main_red`, `verdict_fail`, `review` for a finding the reviewers drew from the whole record rather than one signal, or `usage` for a skill the usage collector flagged (Source `skill-usage`, Learning `<skill>: <flag>`). **Evidence** pointers only (message ID, PR comment URL, transcript path and line), never copied text; a program-ledger row with no `evidence` field is pointed to as `<slug>:<ev>:<ts>:<pr or sha>`, in every mode, so the same row is never counted twice.
- **Status** is `candidate`, then `proposed` (a draft PR exists), or in standing mode `waiting (standing PR)` for an Accepted row until step 5 opens its PR, then `applied` (merged) or `rejected (<reason>)`; `backlog (<ticket>)` for a finding filed to the tracker for the human; `retired` once its target is gone or it stopped being true.
- **Verification** holds only what a script, CI or `measure-delivery` printed (`claude plugin eval` scores before and after, test names). A reviewer's opinion is not verification.
- **Outcome** is filled by later programs: `recurred (<source>)`, or `held through <n> programs (<slug>, ...)` listing each program counted, so no program counts twice.
- A change to a row edits that row in place and appends one dated line to `## History` (`2026-09-25 L-4 candidate to proposed: <PR URL>`), so the history survives.

## Process

### 1. Gather the input

**Program mode.** Build the evidence pack by [references/program-pack.md](references/program-pack.md): the ledger's signals and failures, the decision trail, the transcripts behind them, the Outcome update of applied lessons, and the HEAD check. With no signal and no failure left open after it, stop after the Outcome update; otherwise pass the pack path to the reviewers in place of the transcript path.

**Standing mode.** Each command below is `python3 <checkout>/skills/reflect/scripts/reflect_round.py <command>`. Rounds do not overlap: start with `begin`, which takes the lock (exit 1: another round holds it, so stop and send nothing), lists the new rows with an evidence pointer each in `round-rows.jsonl`, and snapshots each ledger's newest row for the cursor. New means a `signal`, `land_failed`, `main_red` or failed `verdict` row after the cursor in any program ledger or the usage collector's (its `skill_usage` signals carry evidence `skill-usage:<skill>:<flag>@<window>`); drop the rows whose pointer the lessons ledger already holds, which only you can read.

Then `settle` prints the last standing PR: `merged <url>`, its rows go `applied`; `closed <url>`, `rejected (PR closed)`; `open <url>` or `none` changes nothing. Exit 2 (gh failed, or no repository): stop with a plain `end`, so the next round reads the same rows.

Standing mode writes an Accepted row as `waiting (standing PR)` in step 4, and step 5 takes every waiting row once no standing PR is open: the HEAD check first (a row HEAD already fixes goes `retired`), then the edits, the PR, and `proposed`. While a standing PR is open, step 5 still files Backlog tickets and opens no PR, and ends the round with `end --advance --waiting <that PR's url>`: the waiting rows live in the lessons ledger, which the precheck cannot read, so that file is what wakes a round once the PR closes. The round that opens the PR taking the waiting rows, or finds none left, ends with `end --advance --clear-waiting`.

**Orchestrator feedback.** An orchestrator session runs for weeks and never ends, so the owner's corrections there reach no other mode. With `reflect.orchestrator_feedback` on, run `python3 <checkout>/skills/reflect/scripts/orchestrator_feedback.py wake` after the ledgers are read, and `digest` when it exits 0 (a session caught up is read once a day, a pending one at once). digest prints a round ID, the `expect` round, and a JSONL digest: one row per owner prompt of each registered orchestrator span since its own cursor (`orchestrator-cursor.json`), with the assistant text before it, the tools called and the card, ticket and PR identifiers seen. When either prints `skipped` for any reason but the feature being off, put that line in the round's report; exit 2 means the digest would land in the skill repository, so stop the round. Add the digest path and row count to the pack under "Orchestrator feedback", and give the reviewers both. Most owner prompts are instructions: a row becomes a finding only when the reviewers read it as a correction of how the work was done (Source `orchestrator`, Kind `human_correction`, Evidence the row's `event`). After step 4 has written the lessons ledger, run `orchestrator_feedback.py advance --round <round> --expect <expect>`, which also moves the digest to the trash; a round that dies earlier runs no advance, and the next one reads the same range. Commits and PRs carry lesson IDs and the generalized rule, never prompt text or event IDs: the skill repository may be public.

With nothing new (no finding row and no digest row) and no waiting row for step 5, run `advance` for a digest round and `end --advance`, then stop and send nothing; a cursor left behind wakes the automation again on the same rows. With only waiting rows, run `advance` for a digest round and go straight to step 5. Otherwise build the pack at `~/.claude/programs/_standing/reflect/pack-<UTC timestamp>.md` over the new rows only: the ledger and backfill bullets and the HEAD check of [references/program-pack.md](references/program-pack.md) (which also catches a lesson the mail path already committed), and the transcripts each row's evidence points to. The decision trail, `measure-delivery` and the Outcome update belong to Close. Continue from step 2. The round's last act, after step 5, is `end --advance` with the waiting flag step 5 names; a round that dies before it moves no cursor, and the next one reads its rows again (the pointer check keeps the rows step 4 wrote from counting twice).

The daily automation: `python3 <checkout>/skills/orchestrate/scripts/skill_usage.py schedule` prints the `orca automations create` command (`--write` runs it; `--workspace path:<runner>` runs it in a runner worktree whose settings pick the model, [docs/automation.md](../../docs/automation.md)). Its precheck, `skill_usage.py precheck`, collects usage, then exits 0 only when no round holds the lock and either some ledger has a finding row after the cursor, `waiting.json` names a standing PR that is no longer open (or whose state `gh` cannot read), or `orchestrator_feedback.py wake` names a session to read (pending, new, replaced, or grown and last read 24 hours ago), with no model call; Orca records any other exit as a skipped run. The precheck cannot read the lessons ledger, so a woken round may still find nothing new. A round it wakes runs from this step to step 5, opens at most the one draft PR, and merges nothing.

**Session mode.** The parent finds its own transcript file before fanning out. Claude Code writes it under `~/.claude/projects/<cwd with every / and . replaced by ->/`, for this session's working directory. Use that path. Do not glob across `~/.claude/projects/*/`. That crosses workspace boundaries and reads private chats from unrelated projects.

```bash
ls -t <project-dir>/*.jsonl <project-dir>/*/subagents/*.jsonl 2>/dev/null | head -10
```

Two transcript layouts: session (`<session-id>.jsonl`) and subagent (`<session-id>/subagents/agent-<id>.jsonl`).

For each candidate, find the first JSONL line with `"type":"user"` and check that its `message.content` contains the conversation's opening user prompt. Take the matching path. If no path resolves, write a tight digest of the session and pass that instead.

### 2. Spawn three reviewers in parallel

One message, three reviewers launched together. Each reviewer is an `Agent` call with `subagent_type: "general-purpose"`, not the read-only `Explore`/`Plan` agents: reviewers need MCP access for context lookups (tickets, chat threads, observability traces referenced in the transcript), and the read-only agents lack it.

| Lens | Runner | Prompt template |
|---|---|---|
| Judgment | Independent reviewer; the template forbids edits | `references/judgment-reviewer.md` |
| Tooling | Independent reviewer; the template forbids edits | `references/tooling-reviewer.md` |
| Divergent | Independent reviewer; the template forbids edits | `references/divergent-reviewer.md` |

Pass each template verbatim, with `<REVIEWER_COMMON>` replaced by the whole text of `references/reviewer-common.md` (the rules all three share, so each prompt stays self-contained) and the transcript path or digest substituted where marked. Reviewers return findings in their final response.

### 3. Synthesize

One independent synthesizer using the host's available delegation tool. The synthesizer's quality check includes spot-verifying citations, which can require MCP access. The read-only agents lack it. Use `references/synthesizer.md` verbatim, with each reviewer's full output inlined where marked. The synthesizer returns a structured Accepted / Rejected / Backlog list.

### 4. Record, count, and check structure

Write every Accepted, Backlog and Rejected finding to the lessons ledger. A finding that matches an existing row adds its evidence and raises Occurrences instead of making a new row; evidence the row already holds raises nothing. Before comparing, rewrite evidence that points at a transcript line (`<path>:<line>`) as that line's `<sessionId>:<uuid>`, the form digest rows carry, so a correction a lead recorded as a `signal` and the digest found counts once; message IDs and PR URLs stay as they are, and a memory file is never evidence.

- An Accepted finding stays Accepted only with two or more independent occurrences (different tickets, workers or sessions), or when it is a reproduced security or data defect. A `usage` row is a measurement: a later window of the same skill and flag raises Occurrences on its row, its two occurrences are two windows that do not overlap, and a window on a `rejected` row adds its evidence and leaves it rejected. Count occurrences from the ledger's rows and from the `signal` rows in other programs' ledgers in the store, since a program that ran before the ledger existed left no rows. For Source `orchestrator`, where one session spans weeks, an occurrence is an incident: corrections about different cards, tickets, PRs or programs (the rows' `context`) are independent, and a repeat about the same incident adds evidence only, whatever the date; a case you cannot tell stays `candidate`. Otherwise it stays `candidate` in the ledger and waits for a second occurrence.
- For any item that would be enforced more reliably by a lint rule, script, metadata flag, or runtime check, move it from Accepted to Backlog. See `principles` → Encode Lessons in Structure.
- Never propose changes to `hooks/guard.py`, the `orch land` gate in `skills/orchestrate/scripts/prog.py`, tests or eval graders, or this skill. A finding about one of them goes to Backlog for the human.

### 5. Apply

**Program and standing mode** run with nobody to approve, so they propose instead of applying:
1. Make the Accepted edits in one worktree branch of the configured source checkout (routing below). An edit that changes a command, flag or term greps all of `skills/` and changes every copy in the same branch.
2. Verify each against the change it makes, and record the printed result in Verification.
   - A description change: `claude plugin eval <checkout> --case <skill> --ablation none --no-publish --trust-plugin` on the old and the new checkout (3 runs each by default), with a case under `evals/<skill>/` whose prompt should fire the skill and a grader for a neighbouring skill that should not; add the case if it is missing, in both checkouts.
   - A body or script change: `claude plugin validate <checkout>` and a check of the changed path itself: a test under the skill that fails without the change, or the incident replayed with the tools it needs in a scratch copy of the repo, with the asserted result. The trigger cases grant only read tools and grade only which skills fired, so they verify triggering, never behavior.
   - With no such check, write `Verification: none` in the row and the PR body.
3. Open at most one draft PR (`gh pr create --draft`) titled with the lesson IDs, body per `write-plainly`. Never merge it. Standing mode opens it from a branch `reflect/standing-<UTC timestamp>`.
4. Get it an independent review: `python3 <checkout>/skills/deliver-ticket/scripts/review_pick.py --repo <worktree>` names the reviewer (Codex, or a fresh-context Claude reviewer), and `deliver-ticket` §3 and its `references/independent-review.md` say how to run it, fix each Act-on finding and review again. Put the verdict, its reviewer (`review:codex` or `review:claude`) and the reviewed head in the PR body; program mode also records `orch verdict <slug> --pr <n> --sha <head> --source review:<reviewer>` when the program's repository is the skill repository. Exit 3 (Codex required and unavailable): write "independent review pending" in the PR body.
5. Give the PR its board row, so it has a next step and an owner: `orch board set reflect-<n> --repo <owner/repo> --pr <owner/repo>#<n> --owner owner --next "draft PR review"`.
6. Mark the rows `proposed`, and put one line per lesson plus the PR link in the program digest for approval (standing mode: in the round's report).

A `usage` row is routed by [references/usage-findings.md](references/usage-findings.md). Never delete a skill: a retirement is only ever a Backlog ticket for the human.

With no Accepted finding, skip the PR and write one digest line saying why (standing mode: nothing; a round reports only a PR or a ticket it filed).

**Session mode.** Before applying any Accepted edit, present the synthesizer's full Accepted/Rejected/Backlog output to the user and wait for explicit approval. The user picks which subset to apply and may redirect routings. Skill changes reach every session that loads this plugin, so nothing is applied without that approval.

File Backlog items only when the user's task or configured workflow authorizes tracker writes; otherwise keep them as proposed follow-ups in the report.

Choose the source checkout explicitly with `reflect.skill_repository` or the user's task.
Edit only that authorized repository, on a worktree branch, never an installed plugin cache.
If the source repository is unknown, report the proposed edit without changing an installed
copy. Use the target platform's validation and isolated loading mechanism for testing.

For each approved Accepted item, follow the Routing field exactly:

- Trivial existing-skill edit (a one-line bullet, a tightened sentence, a stale fact corrected): parent does directly.
- Substantive existing-skill edit (a new section, a new pattern table, more than ~10 lines): follow `write-skill` from its step 1 (fold in before adding).
- `tune description: <skill path>` (the skill exists but didn't trigger when it should have): `write-skill`, with its §5 eval on the old and the new checkout.
- `new skill via write-skill: <kebab-name>`: `write-skill` decides whether an existing skill is the home first. Do not invent the shape ad hoc.

Before declaring done, run the `write-skill` steps on the change (prompt audit, internal-name grep, tests, `claude plugin validate`) and fix what they report. Put the lesson IDs in the commit message (`L-4`), and mark the rows `applied` once the change reaches main.

### 6. Summarize for the user

Short list, no preamble:

- Edits applied or proposed: `<skill path>` (`L-<n>`). What changed, one line each, with the PR in program and standing mode.
- Candidates waiting for a second occurrence: `L-<n>`, one line each.
- New skills created: `<skill path>`. One line each (rare).
- Backlog filed to the tracker: `<issue title>` (`<tags>`). One line each.
- Dropped: one line per rejected finding + reason from the synthesizer.
