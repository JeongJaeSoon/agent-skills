<!-- translated-from: 3970052 -->
# Skill catalog

This page covers the 27 skills, 3 aliases, 3 commands (`orch`, `orch-dash`, `skills-sync`), and 3 hooks that the `agent-skills` plugin ships. The model invokes a skill on its own when the situation described in its description comes up. The exceptions are `create-verification-skill` and `maintain-verification-skill`: they are `disable-model-invocation`, so you have to invoke them yourself. To invoke a skill directly, use `/agent-skills:<name>`; plain `/<name>` also works when no other plugin uses the same name.

## Flow

```text
One ticket  write-ticket → deliver-ticket → handoff-ticket → end-session
              (dispatch-card: send work elsewhere while this session keeps working)
Project     orchestrate ─ deliver-ticket in each worker ─ orch land
              ─ main guardian · QA lead ─ dashboard
              └ measure-delivery when it's done
Anywhere    use-tracker (tickets) · use-notes (notes)
              · pstack skills (design · review · verification · retrospective)
```

## One ticket

### write-ticket
- **When:** "티켓 만들어줘" (make a ticket), "티켓 기표해줘" (file a ticket), "Linear 티켓으로 만들어줘" (make it a Linear ticket), "티켓으로 남겨두고 종료하자" (leave it as a ticket and let's stop). One-line requests mixed into another question, feedback on work in progress, and follow-up tickets found during work also come here.
- **What it does:**
  - First works out where the request came from. A request mixed into a question gets filed first and the question answered after; feedback on work in progress becomes its own ticket.
  - Ticket types are Feature, Bug, Improvement, and Spike. A Spike's title gets `[조사]` (investigation).
  - For the skeleton it uses the tracker template from the config (`tracker.<adapter>.templates`) first, and the bundled template if there is none. If reading a template fails, it stops.
  - The `🛠 구현 힌트` (implementation hints) section lists only paths it actually opened.
  - Titles start with a verb, every acceptance criterion says how to check it, and `🚫 범위 밖` (out of scope) is always filled in.
  - Dependencies are set as tracker relations, and work is split so that one ticket = one PR. PRs that depend on each other are planned as a GitHub stack.
  - Shows the whole draft once and files it after approval. Mixed-in requests and follow-ups the session raises on its own are filed first.
- **Bundled:** `references/templates.md` (skeletons for development and investigation tickets).
- **Related:** Every tracker operation goes through `use-tracker`. `orchestrate` and `measure-delivery` count tickets in the follow-up format.

### deliver-ticket (formerly `ship-pr`)
- **When:** From before the first multi-file edit until the ticket is done. Creating, updating and merging PRs ("pr 작성까지" (up to writing the PR), "머지까지 진행해줘" (take it all the way to the merge)), releases, answering review comments, Codex cross-checks, "동작확인" (check that it works), "내가 확인할 거 있어?" (anything I need to check?), "이 티켓 끝내줘" (finish this ticket), and stack work all belong here.
- **What it does:**
  - **Plan:** It doesn't enter plan mode or wait for approval. The ticket gets a single plan comment (edited when the plan changes); progress goes in the worklog and the result in a completion comment. It calls other skills depending on the shape of the work: `figure-it-out` for big migrations or multi-step changes, `architect` (→ `arena`) for design decisions that are hard to undo, `how` for unfamiliar code, and `why` for code whose reason is unclear.
  - **Build:** Only on a worktree branch. Non-trivial logic isn't committed without a runnable test. A bug starts with a failing reproduction test. Anything found outside the scope is fixed in this diff, filed as a follow-up ticket, or only noted in the worklog.
  - **Evidence rules:**
    - Every line that ships needs runtime evidence, and changes that came from a disproved hypothesis are reverted.
    - Bugs are reproduced and confirmed on the screen the user saw (Aside for the browser).
    - If a PR or commit already claims to fix it, it doesn't write a competing fix; it verifies by running baseline and patched twice each on the same data.
    - Refactors pin behavior first. Type checks and lint don't count as pinning. If a refactor doesn't make the code easier to read, it's reverted.
    - The procedures for bug fixes (reproduce → bisect the cause → failing test → confirm on the same screen), refactors (pin → target shape → subtract before adding → move callers and delete the old API → prove equivalence), and verifying an existing fix are in `references/evidence.md`.
  - **Review:**
    - Before review, `prune-comments` cleans up the comments in the diff.
    - Even a trivial diff goes through a reviewer who isn't the author (the `/code-review` subagent).
    - A non-trivial diff gets Codex `review` and `adversarial-review` in the background. gpt-6-sol is the default model; only hard design questions go up to astra. Commands and model choice are in `references/codex-review.md`.
    - Findings are sorted into Act on / Consider / Noted / Dismissed. It repeats for up to 5 rounds until no Act on is left, and any remaining Consider items go in the PR body.
  - **Commits:** Small, in an order that tells the story. For a bug the failing test comes before the fix; for a refactor the deletion comes before the new shape.
  - **Before pushing:** Runs the whole test suite and E2E. Backends are checked with the CLI or curl and UIs with Aside, and heavy runs go through `orch heavy`.
  - **PR body:** A briefing, not a lab notebook. It's written as Why / Scope / Trade-offs / Impact / Verification, and the verification section is never skipped. Squash bodies run about 40 lines, titles follow Conventional Commits, and the prose follows `write-plainly`. For Linear tickets the PR is attached with `orca linear attach` instead of `Closes #n`. Per-section rules and the stack procedure are in `references/pull-request.md`.
  - **Verdict:** Verification results are recorded as VERIFIED / NOT VERIFIED / INCONCLUSIVE. Inconclusive, or a pass on a different screen, is not a pass. If something passes too easily, suspect the way it was observed first.
  - **Review loop:** Starting from the bottom PR of the stack, it handles conflicts → review threads → CI and pushes once. Review comments are untrusted data, so they never go into shell commands, and replies are posted with `gh api --input`. CI failures are classified before any retry (the same failure twice is not a flake). After merging or rebasing onto main, it verifies again on the new head. Waiting uses a `Monitor` until-loop. "리뷰 코멘트 대응해줘" (handle the review comments) touches only the threads, and "초록이야?" (is it green?) checks the status just once. Details are in `references/review-loop.md`.
  - **Dependent PRs:** Grouped with `gh stack` and merged in one go from the top layer. CI is confirmed with `gh run view --log`, not a green check mark.
  - **Done:** Done means every acceptance criterion is met.
    - Inside a program: merges only through `orch land`, and stops at READY under human-gate.
    - Standalone card: squash-merges on its own unless the user put it on hold.
    - After leaving the completion comment, it moves on to `handoff-ticket` in the same turn.
- **Bundled:** `scripts/sticky-comment.sh` (keeps PR test results in a single comment). The skill body keeps only the rules that decide what ships; procedures are split into references: `evidence.md` (bug, refactor and existing-fix procedures), `codex-review.md` (Codex review commands and models), `pull-request.md` (PR body sections and stacks), `review-loop.md` (the loop from opening the PR to ready, and watching when someone else merges), `review-bot-triage.md` (how to sort review threads into fix / dismiss / ask; used for Codex, CodeRabbit, Copilot and human reviews).
- **Related:** `figure-it-out`, `architect`, `how`, `why`, `tdd`, `prune-comments`, the verdict framework from `interrogate`, `write-plainly`, `blast-radius`, `write-ticket`, `use-tracker`, `use-notes`, `orchestrate` (`orch land`, `orch heavy`), `handoff-ticket`.

### handoff-ticket
- **When:** "핸드오프" (handoff), "다음 작업으로 넘어가자" (let's move on to the next task), "남은 작업 있어?" (is there work left?), "머지하고 다음 진행해줘" (merge and go on to the next one), and the moment a ticket is done. "병렬로 진행" (run in parallel) belongs here only when it means ticket cards in the same repo; if it could mean subagents or side-branch cards, that is settled first. If this session has to keep working, use `dispatch-card`.
- **What it does:**
  - Picks what to do next on its own. Loose ends are handled here, and independent work becomes a ticket. Next tickets are ranked by priority, dependencies and file conflicts. The only thing it may ask is "which ticket is next".
  - A program worker (one whose brief has a `PROGRAM:` line) sends worker_done and then waits for the coordinator's decision.
  - After confirming the ticket is done, it opens a new card with `orca worktree create --prompt "/goal <ID>"`. Even for a parallel request, it's one card per ticket.
  - It confirms the new card started with `orca terminal wait` and `read`, then closes this session with `end-session`.
- **Related:** The step after `deliver-ticket`. It always ends with `end-session`.

### dispatch-card (formerly `dispatch-work`)
- **When:** "별도의 세션을 만들어서 ~ 해줘" (spin up a separate session and do ~), "~ repo에 작업 지시해줘" (give the ~ repo a task): sending work to another repo or a side branch while this session keeps working.
- **What it does:**
  - Confirms the target repo with `orca repo list`.
  - Writes the brief (background, source, deliverables and done criteria, what not to do, "follow that repo's CLAUDE.md") to a note, and puts only the note path and a summary in the card prompt.
  - Checks for duplicate cards, then creates the card.
  - Confirms the start just once and goes back to its own work. It doesn't wait for the result and doesn't call `end-session`.
- **Related:** `use-notes`, `write-ticket`. Closing this session and handing over is `handoff-ticket`'s job.

### end-session
- **When:** "세션 종료해줘" (end the session), "현재 세션 정리해줘" (wrap up this session), "아카이브해줘" (archive it), "머지하고 종료하자" (merge and let's finish), and the last step of `handoff-ticket`. A bare "정리해줘" (tidy up) with no object only records the state and keeps the conversation going.
- **What it does:**
  - First records the ticket's final state, the completion comment, the worklog and memory.
  - Picks how to close based on the output of `orca worktree current --json`.
    - Card: if the worktree check passes, it removes the card with `orca worktree rm`. If the repo's orca.yaml has an archive hook it adds `--run-hooks`, and if the hook fails it asks instead of forcing.
    - Main checkout: closes only the terminal. It never uses `worktree rm`.
    - Outside Orca: uses `EndConversation`. That is permanent, so it asks for confirmation once.
  - Asks the user about uncommitted changes and never uses `--force`. Program workers don't remove their own worktree.
  - Writes the report first and runs the close command as its last tool call.

## One project (several Orca workers)

### orchestrate
- **When:** This is the top-level orchestrator session the human talks to ("오케스트레이터로", "모든 세션 관리해줘" (manage every session), "전체 태스크 현황" (status of all tasks)), or one session drives a milestone to done through several Orca workers (usually 3 or more); "대시보드 갱신해줘" (update the dashboard); "전체 진행상황 몇 퍼센트" (how many percent done overall); taking over a program another coordinator was running; asking why PRs aren't moving.
- **What it does:** The coordinator owns the program, not the code. Every session starts by reading `orca skills get orchestration`.
  - **Stay answerable:** inside its turn it only routes, runs checks that take seconds, and answers the human. Investigation, implementation, verification, long waits and monitoring go to a background subagent or an Orca worker the moment they arrive; no foreground loops, `sleep`, or `check --wait` outside the background. Completion arrives as a notification.
  - **Top-level mode** (`references/top-level.md`): the session above single-task sessions and program coordinators. A routing table; sessions the human opened are read only; worker starts are confirmed with `--screen` in the background (a trust prompt on a worker it just started gets ↓, a check, then Enter; if blocked, the inbox); mail for a finished dispatch goes to `run:`; message bodies go through files; the dashboard inbox (`orch-dash inbox add`) and registered decisions (`orch decide add`, then `done` once answered) instead of AskUserQuestion; one unfiltered background `check --wait` keeps notices out of the human's typing; reactions to PR review events (`pr-events.jsonl`); `skills-sync broadcast` when skills change.
  - Steps 1–8 below are **program mode**.
  1. **Frame:** The done condition (predicate) is set as countable ticket IDs plus checks on the real deliverables. Human instructions are carried over verbatim as standing orders. Dependencies are split into start order (Orca task deps) and landing order (GitHub stack, `orch dep`). It creates a Run and registers it with `orch init`.
  2. **Verification setup and Pilot:** If there is no verify skill, the first digest asks the user to run `/create-verification-skill`. Until then it verifies by hand and runs one worker end to end as a trial.
  3. **Scale:** Starts the standing roles (main guardian, QA lead). The concurrency cap for ticket workers starts at 1, goes up by 1 with each green landing on main (default ceiling 6), and halves on red.
  4. **Drain:** Runs exactly one `orch wait` in the background. When worker_done arrives, it handles `CLOSE OUT` in the same turn. Every pass ends with `orch status`, acting on the STALLED, SPARE, LEDGER GAP and LANDED-BUT-OPEN lines. When the way of working went wrong rather than the product (a human correction, a question the brief should have answered, a stall caused by process, a flaw in a skill or script), it leaves a single line with `orch record <slug> signal` and saves the analysis for Close.
  5. **Triage:** Follow-ups are parked by default. Only those that block the predicate or are reproduced defects are admitted. Briefs tell workers to link follow-ups as related, not as children of a parent. Orca defaults to parent, but then the counts and stage bars treat them as planned work of the original ticket.
  6. **Land:** Workers land their own PRs with `orch land`. Ordinary PRs merge in parallel; shared files such as migrations, CI, Dockerfiles and compose merge one at a time per base in an exclusive lane.
  7. **Main verification:** When main goes red, the guardian first checks whether it's a flake, then picks a hotfix or a revert; until main is fixed, only fixes for main land. The QA lead handles ticket verification, periodic E2E runs and design-consistency audits.
  8. **Close:** Does a final check on the new main and records it with `record predicate_verified`. Then it releases the roles, runs `measure-delivery`, and runs `reflect` in program mode. It isn't finished until `worker-list --terminal-state reclaimable` is empty.
  - Under human-gate, `orch land` closes the "Land #N" Task as completed when the PR lands, and as failed when it is put on hold or the PR is closed. `orch land` never merges a PR that was decided to be held.
  - For a worker that has ended its turn, the content goes through `orchestration send --to dispatch:<id>`, and the terminal gets just one line: "run orchestration check". Workers without a terminal follow Orca's recovery procedure.
  - Standing roles send routine reports with `--type status`. Escalation is only for when the coordinator needs to step in.
  - Deliberate departures from Orca's default rules (starting the concurrency cap at 1, worker model defaults, a worktree per ticket, a background `orch wait`, a long `worker_done`, a one-line `terminal send` nudge) are listed with reasons in a table in SKILL.md.
  - There are two merge policies: autonomous (the default) and human-gate. Humans get only the dashboard and summaries.
  - Taking over goes in this order: program note → `run-use` → `orch set` (aligns the ledger with the policy in the note) → `orch status`.
- **Bundled:**
  - `references/`
    - `brief.md`: worker brief template.
    - `landing.md`: lanes, landing order, stacks, `land` exit codes.
    - `roles.md`: guardian, QA lead, flow improver.
    - `program-note.md`: program note template.
    - `dashboard.md`: guide to the dashboard.
    - `top-level.md`: the top-level orchestrator mode.
  - `scripts/`
    - `prog.py`: the `orch` implementation.
    - `dash.py`: the `orch-dash` implementation.
    - `dash_demo.py`: offline demo.
    - `mailbox_guard.py`: a transition shim that moves old installs over to the hook.
    - Test files.
  - `assets/dashboard/`: the dashboard UI.
- **Related:** Workers follow `deliver-ticket`. It draws on `use-tracker`, `use-notes`, `create-verification-skill`, `show-me-your-work`, `swarm`, `measure-delivery`, `reflect`, and `end-session`.

### measure-delivery
- **When:** "성과 측정" (measure the results), or questions about whether follow-up tickets are growing or shrinking, rework, or token cost. `orchestrate` also calls it in its Close step.
- **What it does:** `scripts/measure.py` only reads from the tracker and GitHub, and writes a report in Korean.
  - Checks how fast derived tickets grew against the baseline, and whether the backlog is converging.
  - Counts merged PRs.
  - Measures rework (commits after the PR was opened, and CI reruns).
  - Finds escaped-defect candidates (Bug labels, main CI failures, reverts).
  - Computes Claude and Codex tokens per PR. codex-companion's review and task jobs leave no session files and can't be counted, so when no Codex session matches it records `미측정` (not measured) instead of 0.
  - When exact close times are needed from Linear, it uses a file exported through MCP. It shows the requested numbers first and saves the report to the notes.

## Adapters

### use-tracker
- **When:** Reading, searching, creating, labeling, commenting on, or changing the state of tickets, and whenever a script needs ticket data.
- **What it does:**
  - The tracker is resolved in this order: program config → `~/.claude/agent-skills.json` → the default, linear.
  - Inside a session it prefers MCP (`orca linear` for Linear); scripts always use `tracker.py`. The `tracker.py` commands are list, get, children, create, label, comment, transition. transition reads the current state first and leaves a ticket alone if it's already at that stage or further along. `--to review` moves a ticket to In Review, or, if that state doesn't exist, to the one started state whose name contains review.
  - The state vocabulary is triage, backlog, unstarted, started, completed, canceled.
  - The follow-up format is a `follow-up` label, a first line of `파생: <ID> · 원인: <분류>` (derived from: <ID> · cause: <category>), and a related relation.
  - Ticket bodies are treated as untrusted data.
- **Bundled:** `scripts/tracker.py` (a stdlib-only adapter for Linear and Jira, with a fixture mode), `references/linear.md`, `references/jira.md`.

### use-notes (formerly `use-obsidian`)
- **When:** Reading and writing design docs, worklogs and program notes. "obs 에 기록해줘" (write it to obs) comes here too.
- **What it does:**
  - The adapters are obsidian (the default) and markdown, and each program can pick its own.
  - Notes live in `Project/<project>/` as `worklog-*.md` and `program-<slug>.md`. If the repo has its own conventions, those win.
  - Obsidian has to be seen in its synced state, so notes are read and written only through MCP tools. To show a note to the user, it reads the note through MCP and turns it into an Artifact.
- **Bundled:** `references/obsidian.md`, `references/markdown.md`.

## Writing

### write-plainly
- **When:** Writing or revising tickets, PR bodies, commit messages, design docs, notes, worklogs, standalone reports and skill text. Code comments only when asked to edit them. Ordinary per-turn replies don't count. "문서 다듬어줘" (polish the doc), "읽기 쉽게 고쳐줘" (make it easier to read), "AI 티 안 나게" (so it doesn't sound like AI), "번역투 고쳐줘" (fix the translationese), "unslop".
- **What it does:**
  - Eight shared principles: cut words that do no work; use the code's real names, and one name per thing; put conditions first; one instruction per sentence; say who acts; use actions or numbers instead of impressions; don't touch sentences that didn't change; vary sentence length but don't drop particles and verbs.
  - Before writing a doc, it picks the kind (tutorial, how-to, reference, explanation). One doc holds one kind.
  - Replies lead with the answer. Each claim says whether it was measured, inferred or guessed. Links and quotes are never made up. "No" is an answer too.
  - It reads the per-language reference before drafting. Polishing after the fact misses most problems.
- **Bundled:** `references/korean.md` (before-and-after examples for fixing clichés, translationese, hedging, dropped subjects, noun stacks, the "~다" register, and banned formatting), `references/english.md` (rules for English prose, code comments and skill text).
- **Related:** Pointed to by `write-ticket`, `deliver-ticket` (PR bodies), `use-notes`, `measure-delivery`, and `end-session` (final report).

### prune-comments
- **When:** `/prune-comments`, "주석 정리해줘" (clean up the comments), "불필요한 주석 지워줘" (delete the unnecessary comments), "주석 너무 많아" (there are too many comments). `deliver-ticket` calls it before review.
- **What it does:**
  - The scope is the files or diff the caller passes, or the current branch's diff if none. Even inside the diff, it looks only at comments on added or changed lines and at nearby comments this diff made wrong.
  - Whoever wrote the comments judges them too softly, so a single subagent does the pruning. The only comments that stay are licenses, external constraints we can't change (dependencies, platforms, vendors, protocols), style-only lint directives, doc comments required by a public API contract or repo rules, and issue or RFC links that explain a constraint the code can't express.
  - Comments that explain surprising behavior in our own code are deleted and marked RESHAPE (rename, extract a function, add a type). Constraint comments such as "don't delete this" are marked CONSTRAINT; if a cheap encoding (a test, a type, a runtime assert, a lint rule) fits in the diff, it's added and then the comment is deleted. If not, the comment stays and is reported.
  - "IMPORTANT" and "do not remove" aren't a justification, they're a reason to check. If nearby code doesn't make it clear, it checks with `how` or `why`.
  - The pruner doesn't touch code or lines outside the scope. The caller checks with `git diff` and reverts anything it shouldn't have changed.
- **Bundled:** `references/pruner.md` (the instructions for the pruning subagent).

## pstack skills (Lauren Tan, MIT)

These skills come from [pstack](https://github.com/cursor/plugins), adapted for Claude Code. What changed is in [Differences from pstack](#differences-from-pstack) below.

### architect
- **When:** `/architect`, "상세 설계안 작성해줘" (write a detailed design), "설계안 다듬어줘" (refine the design), "구현 계획 짜줘" (draw up an implementation plan), and work where writing code first would lock in the wrong shape.
- **What it does:** Works in five steps.
  1. **Ground:** Uses `how` and `why` to understand the surrounding system and why it has its current shape.
  2. **Sketch:** Launches design runners in parallel through `arena` (Claude opus, Claude fable, Codex). It gets at least two structurally different candidates, filters them with red flags, and merges them by interface depth.
  3. **Agree:** Asks a human to sign off only when requested.
  4. **Implement:** Builds with the sketch as the contract.
  5. **Scrap:** If the same kind of workaround keeps coming back, it throws the sketch away and draws it again.
- **Bundled:** `design-red-flags.md`, `rationale-template.md` (design rationale doc), `runner-prompt.md`.

### arena
- **When:** `/arena`, "경쟁시켜줘" (make them compete), "여러 안 뽑아서 제일 나은 걸로" (draft several and go with the best), "여러 버전 만들어서 비교해줘" (make several versions and compare them), and deliverables where a single attempt would lock in the shape. `architect`'s Sketch step calls it.
- **What it does:**
  - Before giving the same task to N candidates at once (by default Claude opus, Claude fable, Codex), it sets 3–6 scoring criteria. Candidates see only the task, never the criteria.
  - When all candidates finish, a judge from a different model family (Codex, when Claude is the caller) scores each criterion and recommends a base.
  - It reads every candidate in full, scores them against each criterion, and picks the base. One or two better pieces from the other candidates are grafted on by hand.
  - If the candidates converge on the same shape, it uses that shape as is; if they diverge sharply, it goes back and redefines the task.
  - It returns one deliverable plus a short synthesis note recording the base, what was grafted, what was dropped, and the verification result.
- **How it differs from swarm:** swarm splits the work up, sweeps it, and returns a report. arena makes candidates compete on the same work and produces one deliverable.

### blast-radius
- **When:** "이거 바꾸면 뭐가 깨져?" (what breaks if I change this?), and small diffs you don't trust.
- **What it does:**
  - Finds the one fact that makes the change safe, and proves it with a script that runs the real code.
  - Looks where grep can't see (library source, wire formats, execution timing).
  - Pulls in the PRs and commits behind the changed code, following `why`'s procedure.
  - Cross-checks big changes with Codex.
  - Reports what the change does, why it's safe, the risks, what was checked, and what to check before merging.

### figure-it-out
- **When:** `/figure-it-out`, "알아서 설계해서 진행해줘" (design it yourself and get going), "큰 마이그레이션" (a big migration), "자리 비운 동안 끝내줘" (finish it while I'm away), and large multi-step work with no procedure that fits. `deliver-ticket`'s planning step calls it. Running several tickets as parallel cards is `orchestrate`'s job.
- **What it does:** Designs the order of the work before the code.
  1. **Frame:** Writes the done condition as a falsifiable statement, puts numbers on the scope, and sets how strict to be based on the risk. The frame goes in the ticket's plan comment or the worklog, and reversible work doesn't wait for anyone. Only irreversible steps get a one-time approval on the spot.
  2. **Design:** Splits the work into units that can land separately and starts with the least understood one. It first builds a verification harness that captures values from before the change. Designs that are hard to undo are settled with `architect` (→ `arena`). Parallel work only where boundaries separate cleanly, with a worktree for each worker.
  3. **Loop:** For each unit: a hypothesis, the smallest change, a measurement on the real artifact, then keep or revert. Delegated work is judged by reading the result directly or by Codex. Verdicts are VERIFIED / NOT VERIFIED / INCONCLUSIVE.
  4. **Trail:** Keeps a decision record with `show-me-your-work` and usually commits it so it can be read in the PR.
  5. **Verify:** Checks the whole result against the Frame's done condition in the real product, and turns corrections that kept recurring into checks or scripts.
- **Relation to deliver-ticket:** Review, commits and pushes for each unit follow `deliver-ticket`'s rules.

### how
- **When:** "X는 어떻게 동작해?" (how does X work?), "이건 어디에 둬야 해?" (where should this go?)
- **What it does:**
  - Simple questions: the session explores and explains on its own.
  - Complex questions: launches 2–4 `Explore` explorers in parallel, then one opus synthesizes their findings.
  - Explanations follow the order Overview, Key Concepts, How It Works, Where Things Live, Gotchas.
- **Bundled:** `explorer-prompt.md`, `explainer-prompt.md`.
- **Related:** Motivation and history ("왜 이렇게 됐어" (how did it end up like this?)) go to `why`.

### why
- **When:** "왜 이렇게 됐어" (how did it end up like this?), "이거 왜 이렇게 짰어" (why was this written this way?), "이 결정 배경이 뭐야" (what's the background to this decision?), "이 값은 어디서 나왔어" (where did this value come from?), and looking for the reason behind the current shape before changing code. How something works is `how`'s job.
- **What it does:**
  - A question about one line or one commit ends in narrow mode, answered from git blame → commit → PR alone, and the answer names the sources it didn't search.
  - Otherwise it launches one investigator per source in parallel (git and `gh`, Linear/Jira, Notion, Google Drive, Obsidian, Slack). Each investigator digs only in its own source and just notes leads that point elsewhere. Empty results are recorded as findings too.
  - Datadog, Sentry and the warehouse have no connection, so they're recorded as "no access". Calendar is used only to narrow a date range.
  - The synthesis sorts findings into five levels, Direct / Supported / Inferred / Speculative / Unknown, with citations. Code is never taken as evidence of its own intent, and hypotheses built into the question are treated only as candidates.
  - The answer goes What We Found, Reasonably Infer, Competing Hypotheses, What We Don't Know, Sources Consulted. If the question is about changing code, it adds Preserve / Change / Avoid / Risk.
- **Bundled:** `epistemics.md` (confidence criteria), `investigator-prompt.md`, `synthesizer-prompt.md`, `source-playbook.md`, `sources/` (code-archaeology, linear, notion, google-drive, obsidian, slack, incident-postmortem).
- **Related:** The counterpart of `how`. Called by `blast-radius` and `architect`. Reads trackers through `use-tracker` and notes through `use-notes`.

### interrogate
- **When:** "적대적 리뷰" (adversarial review), "codex 교차 검증" (codex cross-check), "codex 로 설계안 점검" (have codex check the design), "빈틈 찾아줘" (find the gaps). PRs headed for main are covered by `deliver-ticket`'s Codex review instead.
- **What it does:**
  - The core idea is model-family diversity. Claude (opus) and Codex review separately with the same prompt and rubric.
  - A lead merges the results, rules on each finding as Act on / Consider / Noted / Dismissed, and writes a map of where the reviewers agree.
  - When the intent is ambiguous it doesn't ask; it marks what it assumed. Fixes are never applied automatically.
- **Bundled:** `reviewer-prompt.md`, `rubric.md`, `code-quality-review.md`, `lead-judgment.md`. `deliver-ticket`'s review triage uses this verdict framework.

### principles
- **When:** A design, refactor, verification or delegation decision needs a named principle.
- **What it does:** An index of 23 principles (Core, Architecture, Verification, Delegation, Meta). Delegation also carries one line this repo added, Stay Answerable (the `orchestrate` rule). For a principle that applies, it reads the leaf file in full. Examples: fix the root cause, test behavior, subtract before adding, don't wait for humans.
- **Bundled:** 23 files, `references/principle-*.md`.

### recall
- **When:** "어디까지 했지" (where did I leave off?), "X 작업 어디까지 했더라" (how far did I get with X?), "최근 작업 정리해줘" (sum up my recent work), "이번 주에 뭐 했지" (what did I do this week?), 'catch me up'. Also before starting or resuming work that an earlier session touched.
- **What it does:**
  - States the scope first (period, 7 days by default; topic; repo). Sessions from other repos are neither asked about nor read.
  - Past sessions are found under `~/.claude/projects/<encoded path>/`. Orca cards have a different path for each worktree, so it checks the main checkout, `git worktree list`, and cards that were already removed (same parent folder). If the `orca search` index is on, it uses that first. The index can only be turned on in the Orca app, under Settings → Agent Session Search → Search inside sessions, with the switch for this computer (there's no CLI). If it's off, it searches with grep and mentions this setting in one line at the end of the answer.
  - With many sessions, haiku subagents split the reading and return, for each session, the goal, decisions, remaining work, blockers and outputs. The raw text stays inside the subagents.
  - If the topic points at a feature, file or bug, it also checks `why`'s source investigation, the worklog (`use-notes`) and tickets (`use-tracker`). That's where reverted fixes and symptoms that keep being reported turn up.
  - It checks the current state of PRs, branches, tickets and cards with `git`, `gh`, the tracker, and `orca worktree list`.
  - The answer is a summary of 5 lines or fewer, a status tag for each thread (`[merged #N]`, `[open PR #N]`, `[in flight <branch>]`, and so on), at most 5 recurring problems, and one next step.

### reflect
- **When:** "reflect", "스킬에 반영해줘" (put this into the skills), "스킬이 왜 안 떴어" (why didn't the skill trigger?), "이 세션 돌아보고 스킬 개선해줘" (look back on this session and improve the skills), "개선 이력 보여줘" (show me the improvement history). It's called automatically in program mode from `orchestrate`'s Close, and in session mode from `end-session` when a human corrected how the work was done.
- **What it does:**
  - Session mode reads this session's transcript. Program mode reads an evidence bundle: the ledger's `signal` entries, merge failures, main reds, failed verdicts, decision records, and the `measure-delivery` results. There are three reviewers (judgment: opus, tool use: Codex, divergent thinking: opus).
  - A synthesizer (opus) sorts the learnings into Accepted / Rejected / Backlog. Every one of them gets a line in the learnings ledger (`Project/agent-skills/learnings.md` in the notes store). For each learning, the ledger records the signal, evidence pointers, how often it happened, what was fixed, the change (PR or commit), the verification numbers a script produced, and whether it recurred later.
  - A skill changes only after two or more distinct cases (reproduced security or data defects are the exception). A single occurrence stays a candidate until it happens again.
  - Session mode applies changes after the user approves. Program mode has no human around, so it fixes and verifies in a worktree, opens a single draft PR, and leaves an approval request in the digest. A human does the merge. Description changes are checked for triggering with `trigger-probe.sh`; body and script changes are checked for behavior with tests that exercise the changed path or by reproducing the case. Without any verification it records `none`.
  - Before calling the reviewers, it checks for each signal whether the current HEAD already fixed it, and marks it (changes since `skills_commit` in `program.json`). `signal` entries in other programs' ledgers count toward the occurrence count too. A fix that changes a command or flag also updates every copy of it across `skills/`.
  - It runs at every Close, even for a program with no signals and no failures, and records whether the applied learnings held up in this program (`held through`, deduplicated by program slug).
  - It never proposes changes to the permission hook, the `orch land` gate, tests and graders, or reflect itself; those go to Backlog.
  - In both modes, Backlog items are filed right away through `use-tracker` and recorded in the ledger's Status as `backlog (<ticket>)`. The Codex reviewer is awaited with `status --wait` running in the background. Ending the turn to wait would end an unattended run right there.

### show-me-your-work
- **When:** Long-running work, or work done while a human is away.
- **What it does:**
  - Records each decision as one TSV row: what, why, evidence, result. The log is append-only; a wrong row is fixed by appending a correction row.
  - When the work is done, it audits the log against the transcript.
  - Codex cross-reviews it, and the end of the response says who reviewed it.
- **Bundled:** `scripts/log.sh` (appends rows), `references/decision-log-template.tsv`.

### swarm
- **When:** `/swarm`, "병렬로 훑어줘" (sweep it in parallel), "나눠서 동시에 확인해줘" (split it up and check it all at once), broad split-up sweeps, or races to see who gets a result first. Competing and merging into one deliverable is `arena`.
- **What it does:**
  - First sets the done condition and the shape (split, race, or a mix). A race is decided by the first to pass (first pass) or by a full ranking (rank all).
  - Launches workers as `Agent` (worktree isolation, in the background), and long-running jobs as Orca workers. An Orca worker's `--base-branch` is accepted only for new-top-level and new-child placements.
  - Workers report PASS / ISSUES / BLOCKED. A report missing the commit and the method is rerun once.
  - Collects the results into one table.

### teach
- **When:** 'teach me this', "이거 제대로 이해하고 싶어" (I want to really understand this), "이 변경 이해시켜줘" (help me understand this change), "처음 보는데 이 구조 설명해줘" (I'm new to this, explain the structure). A single how-does-it-work question is `how`, and a why question is `why`.
- **What it does:**
  - Depending on why you're asking (you want to change it, you're reviewing it, or it's your first look), it settles on a few takeaways, calls `how` and `why` in parallel, and weaves them into one explanation. `why`'s confidence wording is kept as is.
  - It starts from the general definition and then connects it to the code at hand. It gives the smallest complete answer first and goes deeper when asked.
  - With three or more moving parts, it draws diagrams that add one piece at a time (mermaid, ASCII).
  - The writing follows `write-plainly`, in the user's language.

### tdd
- **When:** TDD or a failing test is explicitly requested, or a bug has an obvious cheap local test target.
- **What it does:**
  - Runs the failing test first to confirm why it fails, makes the smallest fix, and confirms it passes.
  - If a test would be unrealistic, it says why and uses the closest runnable check. It never weakens existing assertions.

### create-verification-skill (user-invoked only)
- **What it does:**
  - Surveys the repo and builds a repo-specific skill, `.claude/skills/verify-<app>/`, that launches, drives and observes the app the way a user would. It has Launch, Doctor, Drive, Evidence and Cleanup steps.
  - Also builds a feature map of the 3–5 main features.
  - Proves the skill by running it end to end once itself.
  - The model can't invoke it, so `deliver-ticket` (remaining checks) and `orchestrate` (first digest) suggest that the user run it when needed.
- **Bundled:** `references/feature-map-example/` (the feature map of an example app).

### maintain-verification-skill (user-invoked only)
- **What it does:**
  - Reads the source for each feature, actually drives every feature, and compares the results with the feature map.
  - Sorts problems into doc drift, harness defects and product defects. Product defects are only reported.
  - The result is one of clean, changed (a single PR with only proven fixes), or blocked.
  - When the verify skill can't handle a feature or describes it wrongly, `deliver-ticket` and the QA lead's report lead to suggesting that the user run it.

## Aliases

A session that started before a rename can still call the old name and gets routed to the new skill (`legacy/`).

| Old name | New name |
|---|---|
| `ship-pr` | `deliver-ticket` |
| `dispatch-work` | `dispatch-card` |
| `use-obsidian` | `use-notes` |

## Commands

### `orch` (program ledger and landing gate)
Program state lives in `~/.claude/programs/<slug>/`: `program.json`, the append-only `ledger.jsonl`, and `briefs/`.

| Command | What it does |
|---|---|
| `init` | Registers a program (repo, Run, tracker, predicate, merge policy, ceiling, deadline, note). The skills version is recorded as a commit that exists in the repo (for an unpushed checkout, the pushed base commit + `+local`). Starts the dashboard |
| `set` | When the note changes, brings policy, ceiling, deadline, predicate and exclusive paths in line |
| `status` | Predicate progress, main status, in-flight/cap, next action. STALE, LANDED-BUT-OPEN, STALLED, SPARE and LEDGER GAP lines |
| `record` | Records ledger events (spawned, parked, admitted, main_green/red, predicate_verified, and so on). Rejects main_green/red for a commit in a lower stack layer, because main CI runs only once, on the top layer's commit |
| `verdict` | Records the verdict for a reviewed head. It accepts exactly one source: `codex-review`, `subagent-review`, `verifier:<another model family>`, or `live:<feature>` |
| `gate` | Opens a decision gate for human-gate |
| `dep` | Records a landing-order dependency |
| `queue` | Landing order, and why each PR is stuck |
| `land` | The only way to land. Exit codes: 0 landed, 2 yielded, 3 needs action, 1 rejected |
| `land-check` | Diagnoses readiness without merging |
| `landed` | Records a merge that happened elsewhere |
| `backfill` | Adds PRs merged before registration, and their main CI, to the ledger |
| `heavy` | Runs heavy commands (compose, image builds) limited to 2 slots across the machine |
| `wait` | Coordinator only. Waits until there's a message to handle and prints `CLOSE OUT` lines |
| `decide` | Registers (`add`), lists (`list`) and closes (`done`, `drop`) a decision the coordinator is waiting on the user for. It shows in the dashboard's Needs you with a button per option; a button records the answer and closes the decision, then sends one line, `decision <id>: <answer>`, to the coordinator's terminal, once it is idle if it is busy |

### `orch-dash` (dashboard)
- **How it runs:** `orch init` and `orch status` call `orch-dash ensure`, which starts it automatically. There's one server per repo, and it's replaced when newer code is installed.
- **What it shows:**
  - Now: whether each worker is running a tool, thinking, waiting for input, or in a wait it set for itself.
  - Needs attention: stalled workers, idle slots, ledger gaps, cards to clean up.
  - Stages: progress for each top-level tracker issue.
  - Landing order (only when a PR is waiting to land or holds the exclusive lane) and a burn-up chart.
  - Click a column header to sort a table. A second click reverses the order, a third restores the original order, and the browser remembers your choice.
- **Load:** The browser polls every 5 seconds, but when nothing changed the request ends with a 304 and no body, and it doesn't poll while the tab is hidden. The server collects the ledger every 3 seconds, Orca every 20 seconds, and the tracker and GitHub every 60 seconds; for a finished program (one with a valid final-check record) it reads only the ledger.
- **Commands:** `collect`, `serve`, `ensure`, `note` (one line for a risk or decision), `demo`. The environment variables are `ORCH_DASH_PORT` and `ORCH_DASH=off`. Details are in `skills/orchestrate/references/dashboard.md`.

### `skills-sync` (sync and reload broadcast)
- **What it does:** Keeps the main checkout that sessions load directly in step with origin/main, and sends a reload to running Claude sessions when something changed. The whole setup is in `docs/platform.md`.
- **Commands:**
  - `sync`: fetch, then an ff-only pull or a push of signed commits. Off main, uncommitted changes, diverged, an unsigned commit, or no credential helper: it changes nothing, stops, and leaves a macOS notification and `~/.local/state/agent-skills/sync.json`. launchd runs it every 15 minutes and at login.
  - `broadcast [--skills|--plugins] [--dry-run]`: `/reload-skills` when a `SKILL.md` changed, `/reload-plugins` when the hook wiring (`hooks/hooks.json`) or the plugin manifest changed. Sent only to sessions with an empty prompt, no spinner, no permission, trust or question dialog, no shell below the prompt box, and the same screen on two reads 1.5 s apart; the rest stay in `reload-pending.json` for the next try.
  - `nudge <terminal> <one line>`: sends one line under the same checks and confirms the turn started.
  - `status`: the last sync result and the pending reload.

## Config file

`~/.claude/agent-skills.json` chooses the tracker and the notes store. Without the file, it uses Linear and the Obsidian vault `Private`. Values set in a program's `program.json` override this file.

```json
{
  "tracker": {
    "adapter": "linear",
    "linear": {"workspace": null, "team": "ENG", "project": null,
               "templates": {"dev": "개발 티켓", "spike": "조사 티켓"}},
    "jira": {"base_url": "https://…", "email_env": "JIRA_EMAIL", "token_env": "JIRA_API_TOKEN", "issue_type": "Task"}
  },
  "notes": {"adapter": "obsidian", "obsidian": {"vault": "Private"}, "markdown": {"root": "~/notes"}}
}
```

## Hooks

### Permission decisions
`hooks/guard.py` (PreToolUse, `Bash|Skill`). A plugin can't ship permission rules as settings, so this hook makes the decision instead. The point is that a worker which read a skill hours earlier doesn't stall at a confirmation prompt when it finally runs the command.

| Decision | Applies to |
|---|---|
| Allow | Calls to this plugin's skills and aliases. If an unprefixed name is shadowed by a skill of the same name in `~/.claude/skills` or the project's `.claude/skills`, no decision is made |
| Allow | `orca orchestration <command>`, except reset, worker-abandon and gate-resolve |
| Allow | `orch <command>`, except `heavy` (runs arbitrary commands), `set` and `init` (can change the merge policy), and `backfill` (rewrites the ledger) |
| Deny | `check`/`inbox --terminal <someone else's handle>`, which reads another terminal's Orca mailbox, including when the same command rebinds `$ORCA_TERMINAL_HANDLE` |

Allow is given only to a single command with no shell operators, redirections, substitutions or variables. The one variable allowed is `$ORCA_TERMINAL_HANDLE`. The subcommand must come right after the command name; if a flag comes first, no decision is made. Calls without a decision go through the normal permission flow (the auto mode classifier and the user's own rules).

### Re-orienting after compaction
`hooks/reorient.py` (SessionStart, `compact`). Right after the context is compacted (automatically or with `/compact`), and only when this terminal is the coordinator of a program registered with `orch init`, it adds one paragraph. The paragraph says four things:
- Invoke `agent-skills:orchestrate` again and read it.
- Read the program note.
- Run `orch status` and act on every line it prints.
- From then on, drain only with `orch wait`.

### Recording permission prompts
`hooks/permission.py` (PermissionRequest). When a permission dialog opens, it writes the tool name and its masked arguments to `prompts/` in the dashboard's state directory, keyed by `$ORCA_TERMINAL_HANDLE`. It makes no decision, so the permission flow is unchanged. The dashboard adds Approve, Deny and Open terminal buttons to the inbox item only while that terminal's screen shows the dialog for the same request. When a person clicks one, the server reads the screen again right before sending and presses a single digit: the one-time `Yes` or `No`. It never picks an option that saves a rule or changes the mode, such as "don't ask again", "always allow" or "switch to auto mode". Details are in `skills/orchestrate/references/dashboard.md`.

## Differences from pstack

### At a glance

| | pstack (cursor/plugins) | agent-skills |
|---|---|---|
| Target | Cursor plugin | Claude Code plugin |
| Entry point | A single `/poteto-mode` is always on, matches each task to one of 23 playbooks, and follows its steps | No mode router. Each skill's description is its trigger |
| Skill invocation | Almost all are invoked only by the user or by poteto-mode (`disable-model-invocation`) | The model invokes them on its own, except the two verify skills |
| Workers | Cursor Task subagents, cloud workers | Claude Code `Agent` (worktree isolation), Orca workers |
| Models | Per-role model rules (`pstack-models.mdc`): grok for code, opus for judgment | Claude (opus, fable) and Codex (gpt-6-sol by default, astra only for hard design questions) |
| Cross-review | A multi-model panel | Claude + the Codex companion. Codex runs one job at a time |
| Running projects | The orchestrate, autopilot and shipping playbooks, and `orch.ts` (bun, TSV ledger) | The `orchestrate` skill, `orch` (Python, JSONL ledger), landing gate, exclusive lane, main guardian, QA lead, dashboard |
| Tickets and notes | No assumptions | `use-tracker` (Linear, Jira), `use-notes` (Obsidian, Markdown) |
| Permissions | Cursor settings | `hooks/guard.py` decides instead |
| Language | English, with the unslop style rules | Skill text in English; docs, tickets and PRs in Korean. Style follows `write-plainly` (Korean and English) |

### What we took
Of pstack's 47 skills (23 principles and 24 others), we took all 23 principles and 15 of the others. The pinned commit is `b42effe` (0.15.3), and the file list is in `vendor/pstack/manifest.json`.

- **Taken almost unchanged:** the 23 principles, the review and exploration prompts, the design red flags, and the feature map example.
  - In pstack the principles were 23 separate skills; here they are bundled as reference files of a single `principles` skill. The index (`principles/SKILL.md`) was written fresh here.
- **Taken with changes:** architect, arena, blast-radius, figure-it-out, how, why, interrogate, recall, reflect, show-me-your-work, swarm, tdd, teach, create-verification-skill, maintain-verification-skill. Four changes apply to all of them.
  - The model can invoke them on its own.
  - Cursor-specific pieces were replaced with their Claude Code equivalents: paths `.cursor/` → `.claude/`, workers `generalPurpose`/cloud → `Agent`/Orca workers, transcripts `agent-transcripts` → `~/.claude/projects`.
  - The model panel became Claude + Codex.
  - Calls to a skill we don't install (`unslop`) now point to `write-plainly`. `why` and `arena` were brought in on 2026-09-25, which restored the original links between skills (architect → arena).
  - `why` picks its sources from the MCP tools available in the session instead of Cursor's MCP discovery, and gained a narrow mode for one-line questions. The source files for Datadog, Sentry and the warehouse, which have no connection here, were dropped, and new source files for Google Drive and Obsidian were written.
- **Further changes on 2026-09-25:** The prompts were audited and revised again for Opus 5.5 (`6eec9fa`). The per-step to-do lists in architect and swarm are gone, and how now handles simple questions directly. reflect no longer has a minimum reviewer count, and show-me-your-work became an append-only log. Per-file changes are listed in `vendor/pstack/NOTICE.md`.
- **Derived:** Parts written fresh by carrying over only the rules, not whole files. They aren't in `manifest.json` and aren't synced; when upstream changes, a person reads it and ports what matters.
  - `write-plainly`: merges the reply rules from unslop, technical-writing and poteto-mode, with newly written rules for Korean.
  - `deliver-ticket`: carries over rules from opening-a-pr (PR body, commit order), babysit (review loop, per-request modes), shipping (a verdict holds only for one head), bugbot-triage (sorting review threads), bug-fix and refactoring (evidence rules and procedures), figure-it-out (verdict terms), and benny (verifying an existing fix).
  - `prune-comments`: carries over the keep list, markers and checking procedure from no-comments and the Comment Sicko agent, narrowed to the diff.

### What we left out

| pstack skill | What it does | Why we left it out |
|---|---|---|
| poteto-mode | An always-on mode router with 23 playbooks | Per-skill triggers do this job. A Claude Code output style could imitate the mode, but forcing it on would override the user's settings and making it optional means nobody turns it on, so we didn't build it. The operating rules moved to `orchestrate`, the PR, review and evidence playbooks to `deliver-ticket`, and the reply rules to `write-plainly` |
| no-comments | Removes comments | Depends on Cursor's Comment Sicko agent. Its rules moved to `prune-comments`. unslop and technical-writing were merged into `write-plainly` |
| typescript-best-practices | TypeScript rules | Not a general-purpose skill |
| setup-pstack, make-bot-ui, bro, automate-me, benny automation | Cursor model settings, Grok Bot, and so on | Tied to Cursor or Grok |

### Not in pstack
- **Ticket flow:** `write-ticket`, `deliver-ticket`, `handoff-ticket`, `dispatch-card`, `end-session`. One ticket from start to finish, built around Orca cards and a tracker.
- **Running projects:** `orchestrate`'s `orch` ledger, the landing gate and exclusive lane, human-gate, the main guardian, the QA lead, and the `orch-dash` dashboard. The shape follows pstack's playbooks, but it was built fresh on top of Orca Runs and GitHub stacks.
- **Measurement and adapters:** `measure-delivery`, `use-tracker`, `use-notes`.
- **Hooks:** permission decisions (`guard.py`), re-orienting after compaction (`reorient.py`), and recording permission prompts for the dashboard to answer (`permission.py`).

### Upstream sync status
- **Commits since the pin:** upstream `main` (0.15.5) is two commits ahead of the pin.
  - #419: Removed 19 instructions that Opus 5.5 doesn't need. It goes the same way as our `6eec9fa`, and the tdd and reflect reviewers were changed in the same places.
  - #422: Unified how model rules are read, and made show-me-your-work write a `start` row for each run.
- **Dry-run result:** Running `python3 scripts/pstack-sync.py --to origin/main` brings in 4 principles and 3 interrogate references cleanly. tdd and tooling-reviewer merge automatically. The 9 files we changed (interrogate, architect, swarm, reflect, how, why, show-me-your-work, and two reviewers) conflict.
- **What the conflicts are:** Mostly Cursor model-rule lines, so keeping our side is fine. `--write` only writes when there are zero conflicts, so these have to be merged by hand.
