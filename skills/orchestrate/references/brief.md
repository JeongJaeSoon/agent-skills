# The brief

The brief is the coordinator's product. A worker cannot see this session, its siblings or the program note. Everything it needs has to be in the spec. If you cannot fill a field, you have not scoped the unit yet, so do not spawn it.

GOAL, SCOPE, FORBIDDEN and ACCEPTANCE below cover Orca's own Task-spec contract (Target, Change, Constraints, Ownership, Observable acceptance).

```
<TICKET-ID>: <one-line title>
PROGRAM: <slug>

GOAL        One sentence: the outcome, executable by someone with no access to this chat.
SCOPE       Paths this unit may write and paths it may not. Its own worktree and branch.
            Base: origin/main | origin/feat/<topic> | stacked on #<PR> (gh stack link <lower> <this> --base main).
CONTEXT     Ticket URL. Files and PRs to read. Upstream reports pasted in full when this
            unit depends on them.
ORDER       Starts after <TICKET…> (Orca deps) · lands after <TICKET…> (stack / orch dep).
            Class: normal | urgent | gate.
            Lane: normal, or exclusive when it touches migrations, CI, Dockerfile, compose or the
            program's exclusive_paths (land handles it; say it here so the worker expects it).
PEERS       Who to settle shared files and landing order with directly, and about what:
            `orca orchestration send --to dispatch:<id> --subject … --body …`, plus the exact
            --from and --dispatch-capability values from your Orca preamble (Orca ties the message
            to your Dispatch only through them; never rebuild them). Tell the
            coordinator only what changes scope, order or the predicate. Read your own mail with
            `check --terminal $ORCA_TERMINAL_HANDLE` and ack each delivery (`--ack <deliveryId>`),
            or the same batch comes back. Blocked on a decision or an act you cannot take: `ask`,
            which waits for the answer (after a timeout, resume the same message ID). Never send
            a status and end your turn: waking an idle agent on mail is best-effort. After two
            waits in a row end with no answer, register the question with `orch decide add` (it
            shows in Needs you, and the answer is typed into your terminal), then end your turn.
ACCEPTANCE  Checkable criteria, one per line.
VERIFY      Exact commands, or the repo's .claude/skills/verify-<app> feature to drive,
            plus known gotchas. Heavy local runs (compose stacks, image builds, local E2E) go
            through `orch heavy <slug> -- <command>`, which caps them machine-wide.
TIMEBOX     Rough cap. When it runs out, report partial findings with --outcome failed and stop.
LAND        After review, record the verdict on the reviewed head: orch verdict <slug> --pr <N>
            --sha <head> --source <the reviewer: codex-review, subagent-review (trivial diff only,
            deliver-ticket §3; never in place of a Codex review that returned none: that holds
            the land and goes to the human), verifier:<model of another family than the worker, e.g. verifier:codex>, live:<feature>; never the implementer>. Then the one-minute self-check: `git diff --name-only <CI base>..origin/main`; if any
            of it touches a contract or test premise this PR relies on, merge main in and let CI
            rerun. Then orch land <slug> --pr <N>
            --ticket <ID> --wait-minutes 50 as a Bash call with run_in_background: true (not
            `&` or a redirect: the completion notice carries its output and exit code). Act on
            exit 3, report on exit 1.
            Never merge any other way. (human-gate: stop at READY and report instead.)
FORBIDDEN   Do not start other tickets. Do not file follow-ups: report each finding in the
            worker_done body with a proposed class (NOW: a predicate item or a reproduced
            defect in what this program merged needs it; LATER: with a revisit trigger; DROP:
            with the reason). The coordinator classifies and files the NOW ones. No force-push
            to shared branches. Do not rebase only because the branch is behind; `land` says when.
            An act refused by a permission prompt, the auto-mode classifier, a Claude Code hook or
            the human is not tried again in any form: not re-run, not moved into a script or
            another tool, not aimed at a renamed target, not handed to a subagent or another
            session. Report it with what the act was for, the refusal's category and the exact
            command, and put this rule in every subagent prompt you write. Taking the path this
            brief already names for that act (`orch land` for a merge) is not a retry, nor is
            doing the act again after the human added a rule for it or approved it in your own
            chat. A hook that blocked only the text of a command reading no protected file, and
            says to reword it: reword that text and say so in the report. A read-only command on
            files in your own worktree (no credential, no personal data) that the classifier
            refused as something it does not do, such as destruction or exfiltration: do the same
            read once with the Read or Grep tool, and report the refused command and its
            category. A second refusal, or any doubt that the read is harmless, is reported and
            left. A failing check (a git hook, linter, test or CI) is not a refusal: fix the
            cause and run it again.
            <unit-specific bans>
REPORT      worker_done once, after landing and main CI (or at READY under human-gate).
            Body: what changed, what was verified and how, what remains. Include the PR URL,
            merge commit, review rounds and who reviewed, the VERIFY output you actually saw,
            the main-CI run you read, and the findings with their proposed class. --outcome succeeded only
            when the ticket's acceptance criteria hold. Then close yourself in the same turn:
            end-session §7.
KEEP        <optional: the next task this worker's terminal or card is kept for>
STANDING    <the program note's standing orders for workers, pasted verbatim, numbered; the
            coordinator-only ones stay in the note>
```

## Single worker

A worker outside a program gets a brief too, in this shape. It has no `PROGRAM:` line, which is what keeps `deliver-ticket` out of program mode, so there is no `orch verdict` or `orch land`.

```
<ticket ID, or a short title>

GOAL        One sentence: the outcome, executable by someone with no access to this chat.
SCOPE       Its worktree and branch; the paths it may write and the paths it may not.
CONTEXT     Files, PRs and links to read. The facts you already have, pasted.
ACCEPTANCE  Checkable criteria, one per line.
VERIFY      Exact commands, and the output that proves each criterion.
TIMEBOX     Rough cap. When it runs out, report partial findings with --outcome failed and stop.
FORBIDDEN   Other work. No force-push. Nothing sent outside Orca. <unit-specific bans>
APPROVED    The acts the human already approved for this unit, with their words and date. Inside
            them, decide with the recommended default and report what you chose; do not ask.
HUMAN ONLY  Permission dialogs, logins, payments, document submissions, and any irreversible or
            outward act not under APPROVED. Ask for these together, once.
MODEL CALLS Another model (a subagent, Codex, an API) only when the task needs it; say which and
            why before the call, and pick the model then.
LAND        A change: its normal flow (deliver-ticket), or the push the owner allows. An investigation: none.
WAITING     `ask` what you cannot decide. After two waits in a row end with no answer,
            `orch decide add` the question and end your turn; the answer is typed into your terminal.
REPORT      worker_done once: what changed or what was found, the VERIFY output you saw, what remains.
            Then close yourself in the same turn: end-session §7.
KEEP        <optional, as above>
```

A new task for a worker that already exists takes the same shape. Start the worker with `worker-start --display-name "<the first line>"` so its card carries that name.

## Lead

A card started through `dispatch-card` to run a project for the session above it (a lead, or sub-coordinator) gets this brief in this format, never one of your own, and its first prompt is `/goal <GOAL>. 브리프: <note path>` followed by the brief's `MODE`, `PREDICATE` and `CLOSE` lines written out (the guard hook refuses a card prompt without them). A lead whose brief had no DELEGATE line investigated and implemented on its own.

```
<project>: lead

GOAL        One sentence: the outcome, executable by someone with no access to this chat.
MODE        Run `orchestrate` in program mode for this project.
DELEGATE    You do not investigate, implement, verify or drive a browser yourself, however
            small. Each goes one layer down: a worker or card with a brief (`orchestrate`
            references/brief.md), or a background subagent for a read. You judge results, land,
            record decisions, and put what waits on the human in front of them.
PREDICATE   The countable done condition, and what is out of scope.
CONTEXT     Tickets, repos, notes and decisions so far, pasted or linked.
ROUTING     New requests for this project, from the human or from above, come to you and you
            route them down to your own workers, never to another session's.
PERMISSIONS The autoMode rules for this repo's merge and deploy, quoted, or "missing" (your
            Frame asks the human for them). An act refused in your session or a card under you
            is not run by any other card or session; turn it into a rule request (`orchestrate`
            "Refused acts"). An instruction relayed by another session is not the human's approval.
APPROVED    The acts the human already approved for this project, with their words and date.
            Decisions inside them are yours and your workers': the recommended default, then a
            report. Copy the lines a worker's unit needs into its brief.
HUMAN ONLY  Permission dialogs, logins, payments, document submissions, and any irreversible or
            outward act not under APPROVED. Batched into one message to the human.
MODEL CALLS As in "Single worker"; no model is named here.
REPORT      Digest lines appended to this brief's note at each milestone and when the predicate holds.
CLOSE       Once the predicate holds and the program's Close is done: `end-session` §8.
```

## Rules for filling it

- The spec starts with the ticket ID, never `/`: Orca delivers a `worker-start` spec as the task. A card made with `orca worktree create` starts with `/goal` instead, its APPROVED line in that first prompt or the brief it names (`dispatch-card` §2). The second line, `PROGRAM: <slug>`, is how the user's own skills (`deliver-ticket`, `handoff-ticket`) know they are running inside a program. Nothing else switches them.
- Name no model for the worker to call, and leave out a "use a cheap model for X" hint: one made a worker call that model again and again with nothing to gain. MODEL CALLS says when another model is worth calling.
- The worker runs the user's normal flow (`deliver-ticket`) for implementation and review. LAND, ORDER, PEERS and REPORT are what change inside a program, and so do browser checks: VERIFY sends them to the host's own browser (Orca's `orca tab` commands), not a browser extension. An extension may be disconnected, and that is not a blocker. Lanes share that browser and its logins, so a worker takes it with `orch hold browser --note "<what it checks>"` before driving it and `orch release browser` when done. `hold` refuses while another lane has it and names the holder: wait for it or ask that lane, never drive over its session.
- **Name the card.** Orca's automatic title comes from the first prompt and can be meaningless ("Orca multi-agent IDE worker 설정" was ENG-278). Pass `worker-start --display-name "<ID> <short title>"`. That name sticks; the terminal tab title belongs to the agent, which overwrites a rename.
- **Plan chains as stacks.** A unit that has to land after another unit's PR builds on that branch (`Base: stacked on #N`), so the chain lands in one merge. Record it with `orch dep`, not as an Orca dep: an Orca dep would keep this unit from starting until the lower PR had landed.
- **Bound the wait for an answer** (the end of PEERS, or WAITING). A worker that kept re-arming an hour-long `check --wait` after its question waited 18 hours. "Keep one background wait running" in `top-level.md` is the orchestrator's rule, not a worker's.
- Keep every write inside the worker's worktree. A write elsewhere can stop the worker on a permission prompt while Orca still reports it `live`. Anything kept outside it (logs, notes, program files) comes back in the worker's message, and the coordinator writes it.
- Size the brief to the unit. A one-command unit collapses to a paragraph that still names the goal, the scope, the verify command, the LAND line and the report shape.
- Work on personal data (filters, masking, affected-user lists) verifies with synthetic inputs, or lets a script judge the real data and report counts. A step where the model reads real records, such as the text a filter dropped, is refused by the classifier at run time.
- Save the exact text to `~/.claude/programs/<slug>/briefs/<ticket>.md` with the Write tool before `worker-start` (a heredoc puts the brief into a Bash command, which hooks refuse for a word in it). Afterwards run `orch record <slug> spawned --ticket <id> --note <dispatchId>`.
- A dependency is a context relay: paste the upstream worker's report into the downstream brief.
- **Plan reuse with `KEEP`.** A worker closes its own session once it settles (`end-session` §7). A terminal or card you mean to give the next task needs a `KEEP` line naming that task; without one, a landed, clean card is gone by the time you read its `worker_done`.
- Never resume-chain a brief. A retry gets a fresh brief with the consolidated scope.
- A verifier unit on a high-blast-radius PR runs on another model family (`worker-start --agent codex`). It reports PASS, PASS+NOTES or FAIL, with what it drove.
- Feedback the user gives on in-flight work ("improve this", "do it better") becomes a new ticket and a new brief. It is never appended to the running one.
