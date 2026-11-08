# The brief

The brief is the product of the session that writes it, the orchestrator or a lead. A worker cannot see this session, its siblings or the program note. Everything it needs has to be in the spec. If you cannot fill a field, you have not scoped the unit yet, so do not spawn it.

GOAL, SCOPE, FORBIDDEN and ACCEPTANCE below cover Orca's own Task-spec contract (Target, Change, Constraints, Ownership, Observable acceptance).

```
<TICKET-ID>: <one-line title>
PROGRAM: <slug>

GOAL        One sentence: the outcome, executable by someone with no access to this chat.
SCOPE       Paths this unit may write and paths it may not. Its own worktree and branch.
            Scratch root: <none, or the one path outside the worktree it may write, e.g. a
            directory a VM or container mounts>. Never none when VERIFY bind-mounts host files
            into a container or VM: name a directory the VM really shares (read its mount list),
            since a worktree or temporary path it does not share mounts as an empty directory.
            Downloaded logs and artifacts go to <approved system temporary directory>, outside
            every git worktree. Resolve the platform temporary root (for example TMPDIR) and
            name a task-specific directory in this SCOPE; do not assume an unset variable is a path.
            Base: origin/main | origin/feat/<topic> | stacked on #<PR> (gh stack link <lower> <this> --base main).
CONTEXT     Ticket URL. Files and PRs to read. Upstream reports pasted in full when this
            unit depends on them. When the unit changes a subsystem no upstream report
            covers, name `explain-code` how (simple) for the worker; when it deletes or loosens
            code whose reason is unknown, name `explain-code` why (narrow mode). A fact you state here (something is absent from the base,
            an evidence id) comes from a check that could have come out the other way:
            `git merge-base --is-ancestor <commit> <base>`, or a search that also finds a
            symbol known to exist. An empty `git grep -E` proves nothing (`\b` is a GNU
            extension that BSD/macOS regex does not honour; use `-w`). A corrected fact reopens the decisions built on it. A ticket ID is read from the tracker and written with its title; a correction goes to everyone who already received the wrong one.
            A value the worker would otherwise look up (the Orca executable as your preamble
            names it, an image tag) is written here as a literal, with the line "never probe
            the environment for these".
            A subagent's file:line citation is a claim, not that check. So is a contract value
            you write into acceptance criteria or a VERIFY row (a status or error code, refused
            versus allowed, a field or column name) until the route, schema or design sentence
            that defines it shows it, and an owner decision's technical premise, checked when the
            decision enters the predicate. A retry of the unit re-reads these as well. Before
            telling a worker to delete, rename or empty a file, search for what reads it; its
            contents say nothing about its readers. A verification step you prescribe (how the
            system uploads, pushes, routes) comes from reading that code path, not from the
            owner's approval of the act. A unit that runs on a stack or service the program did
            not start states, from a read-only probe you run before dispatch, what it needs
            there: a session the worker can reach without a human, the loaded config or catalog
            accepting the target, and the source and commit the service actually runs. A probe
            that fails holds the dispatch. A fact left unchecked is marked unverified.
DESIGN      Only when later units will build on a contract this unit sets (a function boundary,
            a schema, a config key, a message format): name the contract and those units. Before
            the first edit, run `explain-code` how (simple) on the existing code paths the contract
            changes or plugs into (skip what an upstream report covers), then `architect` on the
            contract. Put the sketch in the ticket's plan comment (deliver-ticket §1); the units
            that build on it get that sketch in CONTEXT.
CHANGES     Optional, like the next two rows: write what you already know and leave out the
            rest. The files and functions to change and how, and what to reuse. Where this and
            the code disagree, the code wins.
PITFALLS    What has already gone wrong here: an approach that failed, an order that matters,
            what the tests miss.
EXAMPLES    An input with its expected output, or a command with what it should print.
ORDER: oN   Optional, on a line of its own, when an owner order (`orch order add`) started this work. `skills-sync card`
            links the card to it and puts a copy of the order's words in the prompt: do not copy them into the brief.
            The next row is another thing, the start order of tickets.
ORDER       Starts after <TICKET…> (Orca deps) · stacked on <TICKET|#PR> (stack) · lands after
            <TICKET…> (orch dep). Pick by what ties the two:
            - code (this uses the lower one's symbols or schema): `stacked on`, one ticket. The
              card starts from the lower branch; the stack is linear, at most 3 layers, one layer
              per base (no fan-out), and a layer takes no merge commit (rebase only).
            - the same files or exclusive_paths, no code: `starts after`, so it begins once the
              earlier PR has landed. An overlap only in a hotspot (a
              generated file) stays parallel.
            - only a deploy or run order, no shared files: `lands after`. Base stays the default
              branch; the order is an `orch dep` edge, never a stack.
            - none: nothing here; it runs in parallel.
            These rules win where `orch overlap` output, the orchestrate Steps or `landing.md`
            "GitHub stacks" still say otherwise (they follow once the stack-flow change lands). The order with another program's PR is no stack (PARENT
            settles it): write it in PEERS.
            Class: normal | urgent | gate.
            Lane: normal, or exclusive when it touches migrations, CI, Dockerfile, compose or the
            program's exclusive_paths (land handles it; say it here so the worker expects it).
PEERS       Who to settle shared files and landing order with directly, and about what:
            `orca orchestration send --to dispatch:<id> --subject … --body "$(cat <file>)"`, every
            message body in a file written with the Write tool, plus the exact
            --from and --dispatch-capability values from your Orca preamble (Orca ties the message
            to your Dispatch only through them; never rebuild them). Tell the
            lead only what changes scope, order or the predicate. Read your own mail with
            `check --terminal $ORCA_TERMINAL_HANDLE` and ack each delivery (`--ack <deliveryId>`),
            or the same batch comes back. A decision the lead must answer: `ask --question
            --options`, which waits for the lead's `reply`. A timeout (exit 1; `"timedOut": true`
            under `--json`) is not a failure: run the resume command Orca prints, or the
            preamble's `ask` with `--resume <messageId> --timeout-ms 1800000` (the most one wait
            allows), until it is answered, never a second question. While
            it waits it shows in the dashboard's Needs you. `--type escalation` is for a blocker
            the lead must act on, not for a choice. Never send a question as a status and end
            your turn: a status wakes no wait, and waking an idle agent on mail is best-effort.
LINEAGE     A card or helper you start sits under you: `skills-sync card <brief>` (dispatch-card
            §3), or a raw `orca orchestration worker-start` with `--worktree new-child` only.
            No `new-top-level`, `--no-parent` or `path:` parent unless this brief names it. When
            the helper stops (exit 6, or a 4 that says nothing was created), report its choices; do
            not pick `--no-parent` or `--card` to get past it.
ACCEPTANCE  Checkable criteria, one per line. A criterion that rests on how a party outside the
            repo behaves (a client, an upstream) adds a line observing that real party, on dev
            or in a recorded trace; a test built on the brief's model of it shares the
            assumption and passes on the bug.
VERIFY      Exact commands, or the repo's .claude/skills/verify-<app> feature to drive,
            plus known gotchas. Heavy local runs (compose stacks, image builds, the whole test
            suite, local E2E, headless browser runs) go through `orch heavy <slug> -- <command>`,
            which caps them machine-wide; a single test file runs bare.
            Once verification is done, down the stacks you started in the same turn, with the
            verify skill's own teardown when VERIFY names one (the app may start containers and
            networks outside compose that `compose down` leaves), else `docker compose -p
            <project> down`; either way keep volumes (never `-v`). Teardown runs bare, never
            through `orch heavy`: the load gate refuses it exactly when the machine needs it.
            A stack that has to stay
            gets a keep reason and deadline in the reap ledger (reap-resources, compose-stack).
            A browser check runs `orch hold browser --note "<what it checks>"` first and opens
            the browser only once that prints held; `orch release browser` when done.
            A step on a running stack gets its address and keys from the command VERIFY names
            for them (the verify skill's helper sourced with the run id, then its variables),
            written here before the first stack command. A command never names the run's state
            file: a secret-file deny pattern can match its name as text, and switching to the
            helper after that block is a retry.
            A check that needs a read a guard blocks (env inside a pod or container, a file that
            holds secrets) is planned here by the brief's writer: a script the lead puts
            to the human as a run_command, which writes only the derived result (present or
            absent, a match against an expected value the human gave or that is no secret
            itself), never the values, to a log outside the worktree. The worker waits for that
            log and reads it; it never writes or runs that script. Such a read found mid-run with
            no plan here is reported to whoever gave the worker this work, not scripted on the
            spot. A check that needs an act a guard may block (restarting one service of a
            running stack, a request that carries a credential) names its exact command here,
            a credential by its source (an env var, a secret path), never its value: the form
            the verify skill documents, or the one an earlier worker's report shows passing.
            With neither, plan it as the human's run_command, as for a blocked read; the worker
            waits for the result that run logs.
            A read or act already refused follows FORBIDDEN's refused-act paragraph: no plan is
            written here for it afterwards.
TIMEBOX     Rough cap on working time only. Time spent waiting on something outside the worker
            (land order, a heavy slot, CI, a review, a human) does not count. A wait you cannot
            end is reported, not spent against the cap: at READY as a LAND `READY-WAIT: <kind>`
            line, before READY as a message to whoever dispatched you (an escalation, or
            `--type question` for a card without a Dispatch). When working time runs out, report
            partial findings with --outcome failed and stop.
LOAD        Before the first edit, load `deliver-ticket` (the Skill tool; a worker on another CLI
            reads its SKILL.md). Inside a program this brief replaces two of its steps: findings
            go in the worker_done body, not in new tickets (§2), and LAND and REPORT replace
            its merge path (§6). Plan, review and commit still apply. A card that only reads
            and reports writes `LOAD: none` and why. `hooks/guard.py` warns (key
            `guard.deliver_load`) at each edit a card makes before the load.
LAND        After review, record the verdict on the reviewed head: orch verdict <slug> --pr <N>
            --sha <head> --source <independent-review, review:<reviewer>, verifier:<reviewer>,
            or live:<feature>; never the implementer> --reviewer <who reviewed, if the source
            does not name them> --evidence <link or sha of the review>. A session that wrote the PR is
            refused as the recorder: send the reviewed head, the reviewer and the evidence to the lead or
            orchestrator, who records it. When this brief has you wait for the
            lead's OK before landing, record the verdict and run `orch land` only after
            that OK names the same head. Then the one-minute self-check: `git diff --name-only <CI base>..origin/main`; if any
            of it touches a contract or test premise this PR relies on, merge main in and let CI
            rerun. Then orch land <slug> --pr <N>
            --ticket <ID> --wait-minutes 50 as a Bash call with run_in_background: true (not
            `&` or a redirect: the completion notice carries its output and exit code, and
            wakes you, so end your turn to wait; never a sleep loop over its output file). Act on
            exit 3, report on exit 1. Pending checks are waited on the same way, or with a
            `Monitor` until-loop on `gh pr checks` (deliver-ticket review-loop). A run watched
            by id is `orch ci-wait <run-id> -R <owner/repo> --deadline-min M`, in the background: one
            poller per run however many sessions ask, 60 s apart at least (`gh run watch` spends GitHub's core limit at its default interval). A finished watch is not the result;
            read `gh pr checks` after it.
            A wait on a human review or another outside event reads the PR at most once every
            60 minutes, or not at all: end your turn or `ask`, and the notice comes through
            watch-prs or the lead. GitHub's GraphQL limit (5000 points per hour) is shared
            by every session using the same token. On a rate-limit error, retry nothing: wait
            until the reset time (the error's `retry-after`, else `gh api rate_limit`).
            You land your own PR: independent review Act-on 0, zero unresolved threads, required
            checks green (`gh pr checks <N> --required` lists the ones that count), or on a declared no-CI repo the lane in `references/landing.md`. Then
            record main_green or main_red and close (REPORT). Never merge any other way. Stop at
            READY only for a wait you cannot end, and name it in the report as one line
            `READY-WAIT: <kind>`: owner-merge (the owner said they will merge it), human-review (a
            person's approval is required), land-refused (`land` exit 1, a refusal of the merge, or
            a merge method `land` lacks; do not retry it, the session above decides), land-after (an
            earlier PR, a stack, a lead's hold or OK), external-wait (an automated review still
            running, a rate-limit reset). (human-gate: stop at READY and report instead.)
            A base that needs a merge method `land` lacks is a blocked land: report it and stop.
            The one exception is a plain merge the human approved, under SKILL.md "Merge policy":
            a verdict at the exact head, a new one for every later commit, zero unresolved threads
            read right before `gh pr merge --match-head-commit <head>`, then
            `orch landed <slug> --pr <N>`.
FORBIDDEN   Do not start other tickets. Do not file follow-ups: report each finding in the
            worker_done body with a proposed class (NOW: a predicate item or a reproduced
            defect in what this program merged needs it; LATER: with a revisit trigger; DROP:
            with the reason). The lead classifies and files the NOW ones. No force-push
            to shared branches. Do not rebase only because the branch is behind; `land` says when.
            Edit files with the Edit and Write tools, never Bash (heredoc, `cat >`, `sed -i`, a
            python one-liner), for any file in or out of the repo (a merge-conflict resolution, a
            scratch file, a PR-body draft and an `rm -rf` on a variable path all count), even where
            the harness allows small shell edits: a Bash-made patch
            can pass tests that re-applying it with Edit shows broken. A formatter or generator
            VERIFY names (`--write`, `--fix`) is a tool run, not a hand edit. Give each Bash act
            that changes a file or its mode its own call, so a refusal names it. Never chain a
            check or probe with other acts in one Bash call either: a refusal takes every act in
            the call with it, and each of them then counts as refused. Hooks match the whole
            command text, so text a hook may read as a command (a prompt for another agent CLI,
            a hook's test inputs) goes in a file written with the Write tool and passed on stdin
            (`codex exec - < prompt.md`) or named in a short inline line that says to read it;
            the text itself is never inline. Bash runs the user's shell; under zsh, quote a word
            that starts with `=`, and call each item explicitly rather than splitting an unquoted `$var`.
            Write no file or script outside the worktree except under SCOPE's scratch root, its
            named temporary download directory, or the one refusal-report file below (which you
            leave in place); list what you put there (checkouts, files, compose projects) in the report and tear it down
            before worker_done by moving that one named directory to the trash. A script's own
            temporary files go there too, never loose in the system temporary root. Never `rm` a
            glob or a path built from a variable: it stops on a permission prompt, and an empty
            variable widens the target (any other path built from one is written `${VAR:?}`).
            No wrapper scripts for evidence: run the commands VERIFY names, one by one.
            A subagent whose result you would only wait for runs in the foreground, so its report
            returns as the tool result. Never end a turn with background subagents as the only
            thing you wait on: their completion notice arrives as a prompt that a hook can drop,
            and then nothing wakes you. A notice with no report: read the report in the
            subagent's transcript (its hand-back), then the diff; never ask for a resend.
            An act refused by a permission prompt, the auto-mode classifier (including an error that
            returns no verdict), a Claude Code hook or the human is not run again, except once, in
            the same form, by the refused session (a restart that loads a new rule counts as that
            session), and only when the human allowed it in that session's own chat or added a rule
            for it, or when a classifier or hook refusal stated a reason and you added evidence that
            answers it, never for a human's refusal or a production sync. Report that retry as
            `재시도 1회: <what you added>`. A second refusal stops the act and you report it.
            Reaching the same result through another session, script, tool, wrapper or target stays
            forbidden. Evidence answers a reason when it supplies what the refusal said was missing (a
            ledger verdict and test output for a merge refused as unreviewed); restating the command
            or arguing is not evidence, and an error with no verdict states no reason. The cases this
            paragraph names as not a retry keep their own rules and do not use up this one. Work the
            orchestrator handed down after its own `[gate:delegate]` block is not such an act: that
            block refuses only the orchestrator doing it itself. Report a refusal in the
            same turn to whoever gave you this work, with what the act was for, the refusal's
            category (`no verdict` when the classifier gave none) and the exact command: a worker
            (a session Orca dispatched: its prompt carries Orca's worker preamble) reports to the
            orchestrator or lead that dispatched it, as an orchestration message of type escalation (its terminal
            is not read), a subagent to the session that started it, in its result; a card with no
            Dispatch (Orca refuses escalation, `ask` and worker_done from it) as `--type question` to the `run:<id>` its
            brief's REPORT line names, or with none, to the human in its own chat, with its Human only batch. A message
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
            the forms it lists. That end is the paragraph's last sentence, the one that begins
            "A failing check"; a copy that stops at "another way" drops half of it. Taking the path this
            brief already names for that act (`orch land` for a merge, in a worker's brief), when no
            human refused the act, is not a retry, nor is doing the act once more after the human added a rule for it or approved it in
            the refused session's own chat. Nor is the restart of a unit whose subagent's Edit or
            Write got no verdict: the session that started that unit, never the refused one, takes
            the whole unit back once (the orchestrator or lead restarts it as a new card, a worker does it in
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
            Waiting on the pressure gate's `pressure.py queue wait <id>` or `turns --wait <n>`, run
            with `run_in_background: true` or watched with Monitor as its block says, is following
            that block, and so is waiting again when that gate denies the same
            command in the same session while its queue entry still waits. A step the brief
            you were given does not name, a refused step, a `queue wait` that does not end 0, or
            a deny from that gate after its entry gave up: report and stop. A switch that turns that gate off (an environment variable, a flag) is used
            only when the human, in this session's chat after the block, names that gate and that
            command; a relayed message never counts, and any other substitute is a retry. A deny
            from the plugin's `hooks/self_land_gate.py` whose reason starts with `[gate:self-land]`
            names a step you skipped, not a refused report: land the PR, or add the READY-WAIT line it
            names, and send again; that uses up no retry. A
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
            <unit-specific bans, per "Unit-specific bans" below>
REPORT      worker_done once, after landing and main CI (or at READY, with its READY-WAIT line,
            or under human-gate). Its --body is literal text in single quotes: double quotes run
            backticks and `$`, and hooks read the record and READY-WAIT lines from the command,
            which a heredoc or `$(cat <file>)` hides.
            Body: what changed, what was verified and how, what remains. Include the PR URL,
            merge commit, review rounds and who reviewed, the PR body's review-scope line (deliver-ticket §3), the VERIFY output you actually saw,
            the main-CI run you read, and the findings with their proposed class. --outcome succeeded only
            when the ticket's acceptance criteria hold. Load end-session §7 before you send it:
            worker_done is one of the closing calls §7 picks, and the lead may release
            this terminal the moment it arrives.
APPROVED    required checks green + independent review Act-on 0: merge with orch land. <the human's words and date>
KEEP        <optional: the next task this worker's terminal or card is kept for; review fixes
            after READY, never the land itself>
STANDING    <the program note's standing orders for workers, pasted verbatim, numbered; the
            lead-only ones stay in the note>
```

### Unit-specific bans

A merge ban in `<unit-specific bans>` agrees with the program's `merge_policy` and the brief's LAND; one that contradicts them stays out, because a worker cannot follow both. Under `autonomous` with a LAND tail that uses `orch land`, ban only merges outside it (`gh pr merge` run directly, `--admin`, `--auto`), and leave LAND's approved plain-merge exception standing. Under `human-gate`, use the LAND tail that stops at READY. When you copy a ban list from a brief above or an earlier brief, align its merge ban with the current `merge_policy`.

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
TIMEBOX     Rough cap on working time only. Time spent waiting on something outside the worker
            (land order, a heavy slot, CI, a review, a human) does not count. A wait you cannot
            end is reported, not spent against the cap: at READY as a LAND `READY-WAIT: <kind>`
            line, before READY as a message to whoever dispatched you (an escalation, or
            `--type question` for a card without a Dispatch). When working time runs out, report
            partial findings with --outcome failed and stop.
LOAD        A change: before the first edit, load `deliver-ticket` (the Skill tool; a worker on
            another CLI reads its SKILL.md). An investigation that only reads and reports:
            `LOAD: none` and why. `hooks/guard.py` warns (key `guard.deliver_load`) at each edit
            a card makes before the load.
FORBIDDEN   Other work. No force-push. Nothing sent outside Orca. The refused-act paragraph of the
            program brief's FORBIDDEN above, pasted verbatim. <unit-specific bans>
APPROVED    The acts the human already approved for this unit, with their words and date, recorded
            with `orch decide answer` and cited as `오너(d150, 2026-11-04): "<words>"`. Inside
            them, decide with the recommended default and report what you chose; do not ask.
HUMAN ONLY  Permission dialogs outside APPROVED, logins, payments, document submissions, and
            any irreversible or outward act not under APPROVED. Ask for these together, once.
MODEL CALLS Another model (a subagent or an API) only when the task needs it.
LAND        A change: its normal flow (deliver-ticket), or the push the owner allows. An investigation: none.
            Waiting on a review or another outside event: the program brief's LAND sentences from
            "A wait on a human review" through "rate_limit`).", pasted verbatim.
WAITING     `ask` what you cannot decide, and after a timeout (exit 1) the resume command Orca
            prints, or the preamble's `ask` with `--resume <messageId> --timeout-ms 1800000`,
            until it is answered.
LINEAGE     As above.
REPORT      worker_done once: what changed or what was found, the VERIFY output you saw, what remains.
            Load end-session §7 before you send it; it picks worker_done and the closing calls.
KEEP        <optional, as above>
```

A new task for a worker that already exists takes the same shape. Start the worker with `worker-start --display-name "<the first line>"` so its card carries that name.

## Lead

A session started through `dispatch-card` to run a project for the session above it (a lead) gets this brief in this format, never one of your own; when to start one, whether it starts as a dispatched worker or a `/goal` card, and what the layer above does after, is `references/top-level.md` "Starting a lead". Its first prompt is `/goal <GOAL>. 브리프: <note path>` (a dispatched worker's spec is the same text, `/goal` line included) followed by the brief's `MODE`, `ROLE`, `INBOX`, `DELEGATE`, `PREDICATE`, `MERGE`, `TASKS`, `GATE` and `CLOSE` lines written out (the guard hook refuses a card prompt without `CLOSE` or `KEEP`, and a lead's without `MODE`, `ROLE`, `DELEGATE` and `PREDICATE`; `skills-sync card` writes them all out from the brief file and refuses a lead's brief missing any). The `ROLE: lead <slug>` line is what the reorient hook reads to remind the lead of its program on every prompt. Without a DELEGATE line, a lead investigates and implements on its own.

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
MERGE       merge_policy autonomous, or merge_policy human-gate when each landing waits on the
            human's Orca gate; keep the one that applies, the value `orch init --merge-policy`
            gets. `skills-sync card` reads this line and starts a human-gate lead as a `/goal`
            card with its own Run: a dispatched worker can create no task or gate on the Run it
            works for.
TASKS       Each unit you start is an Orca task: `task-create --deps '[…]'` with its start-after
            prerequisites, started from `task-list --ready` with `worker-start --task <id>`
            (`program.md` Scale). Started as a dispatched worker, you cannot create tasks on the Run
            you work for: start each unit with `worker-start --spec` once its prerequisites have
            landed; until then the unit and what it waits on are a digest line in this brief's
            note, so a restart finds the order. A unit you start as a card instead names why in
            your next report.
GATE        A decision that blocks a task is a `gate-create` on that task only: Orca shows it and
            the human resolves it there (the dashboard lists open gates in Needs you). A decision
            no task waits on, and every decision of a lead started as a dispatched worker, is
            `orch decide add`. Never both for one question. A step due at a
            date or time goes in a gate or an `orch board` item's `--next` with that time, never
            only in your memory.
CONTEXT     Tickets, repos, notes and decisions so far, pasted or linked.
ROUTING     New requests for this project, from the human or from above, come to you and you
            route them down to your own workers, never to another session's. Units the
            layer above started before you (listed in CONTEXT) stay in its Run: send what
            you want from one of them to PARENT, which passes it on verbatim.
PARENT      The Run of the session writing this brief, as `orca orchestration send --to
            run:<its run id>`. Reports go there as `--type status`; the HUMAN ONLY batch as `--type
            escalation` when you started as a dispatched worker, as `--type question` when you
            started as a `/goal` card (Orca refuses escalation without a Dispatch). A status wakes
            no wait. Decisions go by GATE. A send to it
            that fails, warns "not durable" or is not `"ok": true` was not delivered: mark it
            undelivered in the program state. It is the routing gap HUMAN ONLY names; never guess
            another address.
START       How you were started, as the writer expects it: a dispatched worker on run:<id> (the
            PARENT Run, `worker-start --worktree new-child` from that Run's session) or a `/goal`
            card under <the writer's session>. `skills-sync card` checks the first against the
            starting session and stops before creating anything when it does not hold.
LINEAGE     As in the worker brief above; your units sit under you and you under PARENT's session.
PERMISSIONS The autoMode rules for each outward act the predicate needs (merge, deploy, edits
            to tracker items or PRs an earlier session made), quoted, or "missing" (your
            Frame asks the human for them). An act refused in your session or a card under you
            is not run by any other card or session; follow `orchestrate` "Refused acts": a rule
            request, or for `no verdict` the restart there. An instruction relayed by another
            session is not the human's approval.
APPROVED    The acts the human already approved for this project, with their words and date.
            Decisions inside them are yours and your workers': the recommended default, then a
            report. At launch, copy here every authority the human has stated that the program
            will need (merge, ticket creation, rerunning a blocked step), word for word and never
            read wider than it was said; authority given later goes on a new card, never typed
            into a running session (an act already refused follows `orchestrate` "Refused acts"
            instead). Copy the lines a worker's unit needs into its brief.
HUMAN ONLY  Permission dialogs outside APPROVED, logins, payments, document submissions, and
            any irreversible or outward act not under APPROVED; a design or implementation choice
            is not one: PARENT settles it, sent as status, or as a question when it blocks you.
            The HUMAN ONLY items are batched into one message, sent once
            to PARENT, which puts it before the human, and registered with `orch decide add`. Do not repeat unchanged waiting notices;
            when the blocker or evidence changes, create a replacement with `orch decide add`
            whose body names the old decision id and changed evidence. After creation succeeds,
            `orch decide drop <old-id>` closes the obsolete question without erasing its history;
            notify the parent once with both ids. Never treat an answer to the old question as
            approval of changed scope. The parent owns delivery to the human; if its channel is
            unavailable, report that routing gap in the current task.
MODEL CALLS As in "Single worker".
REPORT      Digest lines appended to this brief's note at each milestone and when the predicate holds.
            Your first report to PARENT names your address: your Run id once your terminal is
            bound to it, or your dispatch id when you started as a dispatched worker.
            Your last report to PARENT goes out before the closing calls, as `worker_done` when
            you started as a dispatched worker, else `--type status`. It names: predicate N/M;
            the `predicate_verified` record and the commit it checked (`--sha`), the final check's
            command and the output you saw; where that evidence lives once this card is gone (the program note or
            `~/.claude/programs/<slug>/`, never the worktree); admitted tickets and their state;
            the `보류` lines with their owners; the `reflect` PR, or why there is none.
CLOSE       Once the predicate holds and the program's Close is done: `end-session` §7 when you
            started as a dispatched worker, §8 when you started as a `/goal` card.
```

## Rules for filling it

- A spec you write by hand starts with the ticket ID, never `/`: Orca delivers a `worker-start` spec as the task. (The spec `skills-sync card` composes from a brief file keeps the brief's `/goal` line, which states the goal; Orca's preamble keeps it from running as a command, so for a Claude worker the card also types that line once the session's first turn ends, from a detached helper that gives up after an hour.) A card made with `orca worktree create` starts with `/goal` instead, its APPROVED line in that first prompt or the brief it names (`dispatch-card` §2). The second line, `PROGRAM: <slug>`, is how the user's own skills (`deliver-ticket`, `dispatch-card`'s next-ticket mode) know they are running inside a program. Nothing else switches them.
- The worker's own model comes from `--model` at its start, else from the brief's `KIND: design|edit|lookup — <reason>` row (opus, sonnet, haiku; `skills-sync card` refuses another kind or no reason). Name no model for the worker to call, and leave out a "use a cheap model for X" hint: a worker then calls that model again and again with nothing to gain. MODEL CALLS says when another model is worth calling.
- The worker runs the user's normal flow (`deliver-ticket`) for implementation and review. LAND, ORDER, PEERS and REPORT are what change inside a program, and so do browser checks: VERIFY sends them to the host's own browser (Orca's `orca tab` commands), not a browser extension. An extension may be disconnected, and that is not a blocker. Lanes share that browser and its logins, so a worker takes it with `orch hold browser --note "<what it checks>"` before driving it and `orch release browser` when done (the VERIFY line in both templates). `hold` refuses while another lane has it and names the holder: wait for it or ask that lane, never drive over its session.
- A unit whose result a person opens as a live page (a mockup, a branch preview) gets in VERIFY: its own free port in the preview watcher's range, the server bound to 127.0.0.1, and the tailnet link fetched for 200 before it is reported, by `deliver-ticket` `references/visual-evidence.md` ("A live page for a person"). Two lanes on one port show each other's page. The card never runs `tailscale serve` and never asks for a port to be opened.
- **Name the card.** Orca's automatic title comes from the first prompt and can be meaningless, unrelated to the ticket. Pass `worker-start --display-name "<ID> <short title>"`. That name sticks; the terminal tab title belongs to the agent, which overwrites a rename.
- **Stack only a code dependency.** A unit that uses another unit's code builds on that branch (`Base: stacked on #N`), so the chain lands in one merge; record it with `orch dep`, not as an Orca dep, which would keep it from starting until the lower PR had landed. Keep the chain linear and at most 3 layers, with one layer per base. Set Base from `gh pr list --state open`, not memory: an open PR the unit needs, or one already doing its scope, is its base. An open PR that only changes one of the paths in this unit's SCOPE (`orch overlap <slug> --paths <SCOPE paths>` prints `OVERLAP plan #N`) is not a base: start the unit after it lands (an Orca dep), unless the overlap is only a hotspot such as a generated file. A unit that only has to deploy after another keeps the default branch as Base and gets an `orch dep` edge (ORDER).
- **A worker's question is an `ask`** (the end of PEERS, or WAITING), resumed after each timeout. Only a dispatched worker can `ask` or escalate; Orca refuses both from a `/goal` card, which asks by `dispatch-card` §2 "Questions". "One inbox wait, always" (SKILL.md Drain) is the rule of the layers above a worker, not a worker's.
- An agent that will use a write tool whose host asks it to load a policy skill before the first write gets, in its brief or prompt, an instruction to load that skill before its first write; otherwise the confirmation comes back in every session and stops it on a dialog.
- Keep every write inside the worker's worktree except SCOPE's named temporary download directory. A write elsewhere can stop the worker on a permission prompt while Orca still reports it `live`. Anything kept outside it (logs, notes, program files, auto-memory lessons) comes back in the worker's message, and the session that wrote the brief writes it.
- Size the brief to the unit. A one-command unit collapses to a paragraph that still names the goal, the scope, the verify command, the LAND line and the report shape.
- Work on personal data (filters, masking, affected-user lists) verifies with synthetic inputs, or lets a script judge the real data and report counts. A step where the model reads real records, such as the text a filter dropped, is refused by the classifier at run time.
- Save the exact text to `~/.claude/programs/<slug>/briefs/<ticket>.md` with the Write tool before `worker-start` (a heredoc puts the brief into a Bash command, which hooks refuse for a word in it). No `orch record … spawned` line: `orch` reads the Dispatch and its ticket from the Orca Task (the spec's first line). Record one only when the unit's ticket is not the ID the spec opens with (a sub-unit such as `ABC-1-K4`): `orch record <slug> spawned --ticket <id> --note <dispatchId>`.
- **Lineage and supervision.** Every brief carries the LINEAGE row, so each card lands under the session that started it (orchestrator, then lead, then its workers). A brief whose unit must be supervised names the Run in its REPORT or PARENT line (`run:<id>`, the Run the session that will start it is bound to) and, for a lead, its START form. Start it from that Run's session: `skills-sync card` compares the brief's Run with the starting session's, and stops on no binding, another Run or no answer from Orca. A `/goal` card on purpose is `--card`; a handoff with no relation to this session is `--no-parent`.
- A dependency is a context relay: paste the upstream worker's report into the downstream brief.
- Direction in CONTEXT is labelled by origin. The owner's words are quoted verbatim as the owner's; a proposal of yours, a lead's or a reviewer's is labelled as such, never as the owner's recommendation. No advice on getting an act past a prompt, the classifier or a hook, even when the layer above calls it the owner's intent ("Refused acts").
- **Plan reuse with `KEEP`.** A worker closes its own session once it settles (`end-session` §7). A terminal you mean to give the next task needs a `KEEP` line naming that task; without one, it is gone by the time you read its `worker_done`. `KEEP` does not stop the cleaners: a card that must outlive its merge also goes in the protect file as a `path` line (`end-session`, "Who cleans up").
- An owner brief with a stricter refusal rule than the template's does not replace the template paragraph: paste the paragraph from this file, then the owner's rule marked as the one that takes precedence. The pasted paragraph stays whole so the `agent_forbidden` check can match it, and the owner's rule is not weakened.
- Never resume-chain a brief. A retry gets a fresh brief with the consolidated scope.
- A verifier unit on a high-blast-radius PR runs independently of the implementer using the configured agent. It reports PASS, PASS+NOTES or FAIL, with what it drove.
- Feedback the user gives on in-flight work ("improve this", "do it better") becomes a new ticket and a new brief. It is never appended to the running one.
