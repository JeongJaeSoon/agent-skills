# The brief

The brief is the coordinator's product. A worker cannot see this session, its siblings or the program note. Everything it needs has to be in the spec. If you cannot fill a field, you have not scoped the unit yet, so do not spawn it.

GOAL, SCOPE, FORBIDDEN and ACCEPTANCE below cover Orca's own Task-spec contract (Target, Change, Constraints, Ownership, Observable acceptance).

```
<TICKET-ID>: <one-line title>
PROGRAM: <slug>

GOAL        One sentence: the outcome, executable by someone with no access to this chat.
SCOPE       Paths this unit may write and paths it may not. Its own worktree and branch.
            Scratch root: <none, or the one path outside the worktree it may write, e.g. a
            directory a VM or container mounts>.
            Downloaded logs and artifacts go to <approved system temporary directory>, outside
            every git worktree. Resolve the platform temporary root (for example TMPDIR) and
            name a task-specific directory in this SCOPE; do not assume an unset variable is a path.
            Base: origin/main | origin/feat/<topic> | stacked on #<PR> (gh stack link <lower> <this> --base main).
CONTEXT     Ticket URL. Files and PRs to read. Upstream reports pasted in full when this
            unit depends on them. A fact you state here (something is absent from the base,
            an evidence id) comes from a check that could have come out the other way:
            `git merge-base --is-ancestor <commit> <base>`, or a search that also finds a
            symbol known to exist. An empty `git grep -E` proves nothing (`\b` is a GNU
            extension that BSD/macOS regex does not honour; use `-w`). A corrected fact reopens the decisions built on it.
DESIGN      Only when later units will build on a contract this unit sets (a function boundary,
            a schema, a config key, a message format): name the contract and those units. Run
            `architect` on it before the first edit and put the sketch in the ticket's plan
            comment (deliver-ticket §1); the units that build on it get that sketch in CONTEXT.
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
ACCEPTANCE  Checkable criteria, one per line. A criterion that rests on how a party outside the
            repo behaves (a client, an upstream) adds a line observing that real party, on dev
            or in a recorded trace; a test built on the brief's model of it shares the
            assumption and passes on the bug.
VERIFY      Exact commands, or the repo's .claude/skills/verify-<app> feature to drive,
            plus known gotchas. Heavy local runs (compose stacks, image builds, local E2E) go
            through `orch heavy <slug> -- <command>`, which caps them machine-wide.
            Once verification is done, down the stacks you started in the same turn, with the
            verify skill's own teardown when VERIFY names one (the app may start containers and
            networks outside compose that `compose down` leaves), else `docker compose -p
            <project> down`; either way keep volumes (never `-v`). Teardown runs bare, never
            through `orch heavy`: the load gate refuses it exactly when the machine needs it.
            A stack that has to stay
            gets a keep reason and deadline in the reap ledger (reap-resources, compose-stack).
            A browser check runs `orch hold browser --note "<what it checks>"` first and opens
            the browser only once that prints held; `orch release browser` when done.
            A check that needs a read a guard blocks (env inside a pod or container, a file that
            holds secrets) is planned here by the brief's writer: a script the coordinator puts
            to the human as a run_command, which writes only the derived result (present or
            absent, a match against an expected value the human gave or that is no secret
            itself), never the values, to a log outside the worktree. The worker waits for that
            log and reads it; it never writes or runs that script. Such a read found mid-run with
            no plan here is reported to whoever gave the worker this work, not scripted on the
            spot. A read already refused follows FORBIDDEN's refused-act paragraph: no plan is
            written here for it afterwards.
TIMEBOX     Rough cap. When it runs out, report partial findings with --outcome failed and stop.
LAND        After review, record the verdict on the reviewed head: orch verdict <slug> --pr <N>
            --sha <head> --source <independent-review, review:<reviewer>, verifier:<reviewer>,
            or live:<feature>; never the implementer>. When this brief has you wait for the
            coordinator's OK before landing, record the verdict and run `orch land` only after
            that OK names the same head. Then the one-minute self-check: `git diff --name-only <CI base>..origin/main`; if any
            of it touches a contract or test premise this PR relies on, merge main in and let CI
            rerun. Then orch land <slug> --pr <N>
            --ticket <ID> --wait-minutes 50 as a Bash call with run_in_background: true (not
            `&` or a redirect: the completion notice carries its output and exit code, and
            wakes you, so end your turn to wait; never a sleep loop over its output file). Act on
            exit 3, report on exit 1. Pending checks are waited on the same way, or with a
            `Monitor` until-loop on `gh pr checks` (deliver-ticket review-loop).
            A wait on a human review or another outside event reads the PR at most once every
            60 minutes, or not at all: end your turn or `ask`, and the notice comes through
            watch-prs or the coordinator. GitHub's GraphQL limit (5000 points per hour) is shared
            by every session using the same token. On a rate-limit error, retry nothing: wait
            until the reset time (the error's `retry-after`, else `gh api rate_limit`).
            Never merge any other way. (human-gate: stop at READY and report instead.)
            A base that needs a merge method `land` lacks is a blocked land: report it and stop.
            The one exception is a plain merge the human approved, under SKILL.md "Merge policy":
            a verdict at the exact head, a new one for every later commit, zero unresolved threads
            read right before `gh pr merge --match-head-commit <head>`, then
            `orch landed <slug> --pr <N>`.
FORBIDDEN   Do not start other tickets. Do not file follow-ups: report each finding in the
            worker_done body with a proposed class (NOW: a predicate item or a reproduced
            defect in what this program merged needs it; LATER: with a revisit trigger; DROP:
            with the reason). The coordinator classifies and files the NOW ones. No force-push
            to shared branches. Do not rebase only because the branch is behind; `land` says when.
            Edit files with the Edit and Write tools, never Bash (heredoc, `cat >`, `sed -i`, a
            python one-liner), even where the harness allows small shell edits: a Bash-made patch
            can pass tests that re-applying it with Edit shows broken. A formatter or generator
            VERIFY names (`--write`, `--fix`) is a tool run, not a hand edit. Give each Bash act
            that changes a file or its mode its own call, so a refusal names it. Never chain a
            check or probe with other acts in one Bash call either: a refusal takes every act in
            the call with it, and each of them then counts as refused.
            Write no file or script outside the worktree except under SCOPE's scratch root, its
            named temporary download directory, or the one refusal-report file below (which you
            leave in place); list what you put there (checkouts, files, compose projects) in the report and tear it down
            before worker_done by moving that one named directory to the trash. A script's own
            temporary files go there too, never loose in the system temporary root. Never `rm` a
            glob or a path built from a variable: it stops on a permission prompt, and an empty
            variable widens the target (any other path built from one is written `${VAR:?}`).
            No wrapper scripts for evidence: run the commands VERIFY names, one by one.
            An act refused by a permission prompt, the auto-mode classifier (including an error that
            returns no verdict), a Claude Code hook or the human is not tried again in any form: not
            re-run, not moved into a script or another tool (Bash after an Edit got no verdict), not
            aimed at a renamed target, not handed to a subagent or another session. Report it in the
            same turn to whoever gave you this work, with what the act was for, the refusal's
            category (`no verdict` when the classifier gave none) and the exact command: a worker
            reports to its coordinator as an orchestration message of type escalation (its terminal
            is not read), a subagent to the session that started it, in its result; a card with no
            orchestration channel to the human in its own chat, with its Human only batch. A message
            sent from Bash carries the command as the path of a file you wrote with the Write tool,
            never quoted: a hook that matched the command's text blocks a message quoting it. That
            file goes under <temp>/orch-refusal/<your worktree's directory name>/, where <temp> is
            the absolute path TMPDIR holds, or /tmp when TMPDIR is unset, with a name no other
            report there uses (a timestamp); whatever else your brief says about writing outside
            the worktree, you may write it there. You do not tear it down: the reader moves it to
            the trash after reading. If that Write is itself refused, report the refused act's tool
            and target only, without its command. Paste
            this paragraph, from "An act refused" through "run it again.", verbatim into every
            subagent prompt you write, including tracker and notes delegates: a summary drops
            the forms it lists. Taking the path this
            brief already names for that act (`orch land` for a merge, in a worker's brief) is not a
            retry, nor is doing the act again after the human added a rule for it or approved it in
            the refused session's own chat. Nor is the restart of a unit whose subagent's Edit or
            Write got no verdict: the session that started that unit, never the refused one, takes
            the whole unit back once (a coordinator restarts it as a new card, a worker does it in
            its own session), and a second `no verdict` there goes to the human, reported the same
            way. If you are the refused one, stop that edit and report it; do not finish it another
            way. A hook that blocked only the text of a command reading no protected file, and says
            to reword it: reword that text inside the same command and say so in the report. When
            the matched text names a file the command opens, the block is not text-only; a script
            or another path is never a reword. Two hook blocks refuse only when or how a command
            runs, not the act: a deny from the agent-skills plugin's own `hooks/pressure_gate.py`
            or `hooks/guard.py` whose reason starts with `[gate:pressure]` (a heavy start while
            the machine is loaded) or `[gate:sleep-loop]` (a foreground sleep loop). The same
            marker anywhere else, or from any other hook, does not count: every other hook block
            is a refusal, even one that names another tool or form to use. When one of these two
            blocks names a check to wait on or a step to run first (the pressure gate's `round`,
            only when the brief you were given names that step), follow it: once every check it
            names passes and every step it names has run, run the same command and say so in the
            report. A `[gate:sleep-loop]` block is followed in one form only, the same Bash
            command with `run_in_background: true`, and the report says so. Watching its
            condition with the Monitor tool, which that block also offers, is not part of this
            exception: report it, and use Monitor only when the one you report to says so.
            Waiting on the pressure gate's `check` with Monitor, as its block says, is following
            that block. A step the brief
            you were given does not name, a refused step, or a second deny from that gate: report
            and stop. A switch that turns that gate off (an environment variable, a flag) is used
            only when the human, in this session's chat after the block, names that gate and that
            command; a relayed message never counts, and any other substitute is a retry. A
            read-only command on files in
            your own worktree (no credential, no personal data) that the classifier refused as
            something it does not do, such as destruction or exfiltration, or with no verdict: do
            the same read once with the Read or Grep tool, and report the refused command and its
            category. A second refusal, or any doubt that the read is harmless, is reported and
            left. A post-execution hook error needs evidence that the act already ran: inspect
            its output and side effects, report the error, and never repeat that act merely to
            clear the error. Continue only independently authorized acts that do not depend on
            the failed check, naming explicit paths (`git add <paths>`, never `-A`). Leave required
            verification pending; if execution or safety is uncertain, stop the affected act.
            Report leftover files for the normal card cleanup. A failing check (a git hook,
            linter, test or CI) is not a refusal: fix the cause and run it again.
            <repository data-sharing restrictions, applying to every reviewer and tool>
            <unit-specific bans>
REPORT      worker_done once, after landing and main CI (or at READY under human-gate).
            Body: what changed, what was verified and how, what remains. Include the PR URL,
            merge commit, review rounds and who reviewed, the PR body's review-scope line (deliver-ticket §3), the VERIFY output you actually saw,
            the main-CI run you read, and the findings with their proposed class. --outcome succeeded only
            when the ticket's acceptance criteria hold. Load end-session §7 before you send it:
            worker_done is one of the closing calls §7 picks, and the coordinator may release
            this terminal the moment it arrives.
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
            A browser check: `orch hold browser` before opening it, `orch release browser` after.
TIMEBOX     Rough cap. When it runs out, report partial findings with --outcome failed and stop.
FORBIDDEN   Other work. No force-push. Nothing sent outside Orca. The refused-act paragraph of the
            program brief's FORBIDDEN above, pasted verbatim. <unit-specific bans>
APPROVED    The acts the human already approved for this unit, with their words and date. Inside
            them, decide with the recommended default and report what you chose; do not ask.
HUMAN ONLY  Permission dialogs outside APPROVED, logins, payments, document submissions, and
            any irreversible or outward act not under APPROVED. Ask for these together, once.
MODEL CALLS Another model (a subagent or an API) only when the task needs it; say which and
            why before the call, and pick the model then.
LAND        A change: its normal flow (deliver-ticket), or the push the owner allows. An investigation: none.
            Waiting on a review or another outside event: the program brief's LAND sentences from
            "A wait on a human review" through "rate_limit`).", pasted verbatim.
WAITING     `ask` what you cannot decide. After two waits in a row end with no answer,
            `orch decide add` the question and end your turn; the answer is typed into your terminal.
REPORT      worker_done once: what changed or what was found, the VERIFY output you saw, what remains.
            Load end-session §7 before you send it; it picks worker_done and the closing calls.
KEEP        <optional, as above>
```

A new task for a worker that already exists takes the same shape. Start the worker with `worker-start --display-name "<the first line>"` so its card carries that name.

## Lead

A card started through `dispatch-card` to run a project for the session above it (a lead, or sub-coordinator) gets this brief in this format, never one of your own; when to start one, and what the layer above does after, is `references/top-level.md` "Starting a lead". Its first prompt is `/goal <GOAL>. 브리프: <note path>` followed by the brief's `MODE`, `ROLE`, `INBOX`, `DELEGATE`, `PREDICATE` and `CLOSE` lines written out (the guard hook refuses a card prompt without `CLOSE` or `KEEP`, and a lead's without `MODE`, `ROLE`, `DELEGATE` and `PREDICATE`). The `ROLE: lead <slug>` line is what the reorient hook reads to remind the lead of its program on every prompt. Without a DELEGATE line, a lead investigates and implements on its own.

```
<project>: lead

GOAL        One sentence: the outcome, executable by someone with no access to this chat.
MODE        Run `orchestrate` in program mode for this project.
ROLE: lead <slug>
INBOX       Keep exactly one background inbox wait running (`orch wait <slug>`) and start it again
            on every wake; after a compaction, `claude --continue` or a restart, first check that it
            is alive and start it if not (`orchestrate` Drain, "One inbox wait, always").
DELEGATE    You do not investigate, implement, verify or drive a browser yourself, however
            small. Each goes one layer down: a worker or card with a brief (`orchestrate`
            references/brief.md), or a background subagent for a read. You judge results, land,
            record decisions, and put what waits on the human in front of them. Cards and
            workers you start get a worker brief (the one at the top of references/brief.md,
            or "Single worker"), never this Lead brief; only the human or the top-level starts
            a lead.
PREDICATE   The countable done condition, and what is out of scope.
CONTEXT     Tickets, repos, notes and decisions so far, pasted or linked.
ROUTING     New requests for this project, from the human or from above, come to you and you
            route them down to your own workers, never to another session's. Units the
            layer above started before you (listed in CONTEXT) stay in its Run: send what
            you want from one of them to PARENT, which passes it on verbatim.
PARENT      The Run of the session writing this brief, as `orca orchestration send --to
            run:<its run id>`. Reports and the HUMAN ONLY batch go there. A send to it
            that fails is the routing gap HUMAN ONLY names; never guess another address.
PERMISSIONS The autoMode rules for each outward act the predicate needs (merge, deploy, edits
            to tracker items or PRs an earlier session made), quoted, or "missing" (your
            Frame asks the human for them). An act refused in your session or a card under you
            is not run by any other card or session; follow `orchestrate` "Refused acts": a rule
            request, or for `no verdict` the restart there. An instruction relayed by another
            session is not the human's approval.
APPROVED    The acts the human already approved for this project, with their words and date.
            Decisions inside them are yours and your workers': the recommended default, then a
            report. Copy the lines a worker's unit needs into its brief.
HUMAN ONLY  Permission dialogs outside APPROVED, logins, payments, document submissions, and
            any irreversible or outward act not under APPROVED. Batched into one message, sent once
            to PARENT, which puts it before the human, and registered with `orch decide add`. Do not repeat unchanged waiting notices;
            when the blocker or evidence changes, create a replacement with `orch decide add`
            whose body names the old decision id and changed evidence. After creation succeeds,
            `orch decide drop <old-id>` closes the obsolete question without erasing its history;
            notify the parent once with both ids. Never treat an answer to the old question as
            approval of changed scope. The parent owns delivery to the human; if its channel is
            unavailable, report that routing gap in the current task.
MODEL CALLS As in "Single worker"; no model is named here.
REPORT      Digest lines appended to this brief's note at each milestone and when the predicate holds.
            Your first report to PARENT names your Run id, once your terminal is bound to it.
CLOSE       Once the predicate holds and the program's Close is done: `end-session` §8.
```

## Rules for filling it

- The spec starts with the ticket ID, never `/`: Orca delivers a `worker-start` spec as the task. A card made with `orca worktree create` starts with `/goal` instead, its APPROVED line in that first prompt or the brief it names (`dispatch-card` §2). The second line, `PROGRAM: <slug>`, is how the user's own skills (`deliver-ticket`, `handoff-ticket`) know they are running inside a program. Nothing else switches them.
- Name no model for the worker to call, and leave out a "use a cheap model for X" hint: a worker then calls that model again and again with nothing to gain. MODEL CALLS says when another model is worth calling.
- The worker runs the user's normal flow (`deliver-ticket`) for implementation and review. LAND, ORDER, PEERS and REPORT are what change inside a program, and so do browser checks: VERIFY sends them to the host's own browser (Orca's `orca tab` commands), not a browser extension. An extension may be disconnected, and that is not a blocker. Lanes share that browser and its logins, so a worker takes it with `orch hold browser --note "<what it checks>"` before driving it and `orch release browser` when done (the VERIFY line in both templates). `hold` refuses while another lane has it and names the holder: wait for it or ask that lane, never drive over its session.
- A unit whose result a person opens as a live page (a mockup, a branch preview) gets in VERIFY: its own free port in the preview watcher's range, the server bound to 127.0.0.1, and the tailnet link fetched for 200 before it is reported, by `deliver-ticket` `references/visual-evidence.md` ("A live page for a person"). Two lanes on one port show each other's page. The card never runs `tailscale serve` and never asks for a port to be opened.
- **Name the card.** Orca's automatic title comes from the first prompt and can be meaningless, unrelated to the ticket. Pass `worker-start --display-name "<ID> <short title>"`. That name sticks; the terminal tab title belongs to the agent, which overwrites a rename.
- **Plan chains as stacks.** A unit that has to land after another unit's PR builds on that branch (`Base: stacked on #N`), so the chain lands in one merge. Set Base from `gh pr list --state open`, not memory: an open PR the unit needs, or one already doing its scope, is its base. Record it with `orch dep`, not as an Orca dep: an Orca dep would keep this unit from starting until the lower PR had landed.
- **Bound the wait for an answer** (the end of PEERS, or WAITING). "One inbox wait, always" (SKILL.md Drain) is the rule of the layers above a worker, not a worker's.
- An agent that will use a write tool whose host asks it to load a policy skill before the first write gets, in its brief or prompt, an instruction to load that skill before its first write; otherwise the confirmation comes back in every session and stops it on a dialog.
- Keep every write inside the worker's worktree except SCOPE's named temporary download directory. A write elsewhere can stop the worker on a permission prompt while Orca still reports it `live`. Anything kept outside it (logs, notes, program files, auto-memory lessons) comes back in the worker's message, and the coordinator writes it.
- Size the brief to the unit. A one-command unit collapses to a paragraph that still names the goal, the scope, the verify command, the LAND line and the report shape.
- Work on personal data (filters, masking, affected-user lists) verifies with synthetic inputs, or lets a script judge the real data and report counts. A step where the model reads real records, such as the text a filter dropped, is refused by the classifier at run time.
- Save the exact text to `~/.claude/programs/<slug>/briefs/<ticket>.md` with the Write tool before `worker-start` (a heredoc puts the brief into a Bash command, which hooks refuse for a word in it). Afterwards run `orch record <slug> spawned --ticket <id> --note <dispatchId>`.
- A dependency is a context relay: paste the upstream worker's report into the downstream brief.
- Direction in CONTEXT is labelled by origin. The owner's words are quoted verbatim as the owner's; a proposal of yours, a lead's or a reviewer's is labelled as such, never as the owner's recommendation. No advice on getting an act past a prompt, the classifier or a hook, even when the layer above calls it the owner's intent ("Refused acts").
- **Plan reuse with `KEEP`.** A worker closes its own session once it settles (`end-session` §7). A terminal or card you mean to give the next task needs a `KEEP` line naming that task; without one, a landed, clean card is gone by the time you read its `worker_done`.
- Never resume-chain a brief. A retry gets a fresh brief with the consolidated scope.
- A verifier unit on a high-blast-radius PR runs independently of the implementer using the configured agent. It reports PASS, PASS+NOTES or FAIL, with what it drove.
- Feedback the user gives on in-flight work ("improve this", "do it better") becomes a new ticket and a new brief. It is never appended to the running one.
