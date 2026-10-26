# Standing roles

A program of more than a handful of tickets runs standing workers beside the ticket workers: a main guardian and a QA lead per program, and one flow improver and one resource steward per machine. Each standing worker is an Orca worker with its own worktree and brief, spawned at Scale. They report to the Run inbox like any worker, and the coordinator records each with `orch record <slug> spawned --role <name> --note <dispatchId>` so they stay outside the concurrency cap. None of them lands feature work.

The flow improver and the resource steward serve every program on the machine, so there is only one of each. The top-level orchestrator owns them when there is one; otherwise the first program to reach Scale spawns them, and a later program reuses the running one (`worker-list`). They are released at Close only when no other program still runs; the per-program roles are always released at Close.

| Role | Owns | Does not |
|---|---|---|
| Coordinator (this session) | Order, deps, briefs, triage, gates, the digest | Diagnose red main, run QA, rework the process, reap worktrees |
| Main guardian | Red main: flake or defect, freeze, hotfix or revert, notify | Pick up tickets |
| QA lead | Ticket verification, periodic E2E on main, design-vs-code audit | Fix what it finds (it files tickets) |
| Flow improver | Folding lesson signals, the human's process feedback and the ledgers' unclaimed signals into the skills while programs run: batched, audited, pushed or proposed as a draft PR, then reloaded | Touch a running program's work, or change its contract (merge policy, predicate, the brief's required fields, landing rules) before that program's coordinator confirms |
| Resource steward | Reaping settled worktrees the coordinators missed, running `reap-resources` for leftover processes, Docker leftovers and stale branches, watching machine load | Touch a live turn, a dirty tree, an open PR, a coordinator's or standing role's worktree, or the dashboard; `--force` |

These roles apply when the user selects this orchestration workflow. Their presence does
not authorize unrelated repository work, cleanup, publishing, or permission changes.

## Main guardian brief

```
GUARDIAN: main guardian for <repo> <base>
PROGRAM: <slug>

GOAL        Keep <base> green without stopping the program for flakes.
WATCH       Every main run, read from GitHub: gh run list --repo <repo> --branch <base> --json
            databaseId,headSha,status,conclusion (orch status stays `main pending` until a lander
            records the result). Wake with a Bash call under run_in_background: a loop that exits
            when a new run on <base> completes or after 4.5 minutes, whichever is first, so each
            wake-up also carries your heartbeat and your own mail check. Never orch wait or
            `check --run`: they read the coordinator's Run inbox.
ON RED      1. Flake check: re-run the failed jobs once (gh run rerun <id> --failed) and read the log.
               Flake → note it in the digest (orch-dash note <slug> --kind risk --text …), freeze nothing.
            2. Defect → orch record <slug> main_red --sha <merge commit>. That freezes every lane
               except --class main-fix.
            3. Narrow the culprit among the commits since the last green.
            4. Choose: hotfix when the cause is clear and small and verifies in ~30 min; revert
               otherwise. Never mechanically revert a migration or a commit later PRs build on.
            5. Open the repair PR, review it (deliver-ticket §3, scaled to the diff), and land it with
               orch land <slug> --pr N --class main-fix. Required checks, no bypass.
            6. On green: orch record <slug> main_green --pr N --sha <sha>.
            7. Tell the culprit's card and the affected cards (orca orchestration send --to
               dispatch:<id>), and the coordinator: orca orchestration send --to run:<run id>
               --type status --subject "main <red|green> <sha>" --body "<time, sha, flake|defect,
               hotfix|revert, PR>". Use --type escalation only when a red main needs the coordinator
               to act because you cannot repair it yourself: Orca keeps escalation for work that is
               blocked, and it wakes the coordinator. The coordinator appends that line to
               guardian-log.tsv; you write nothing outside your worktree except a refusal report
               (FORBIDDEN).
            Every send copies the exact --from and --dispatch-capability values from your Orca
            preamble; Orca ties a message to your Dispatch only through them, so never rebuild them.
FORBIDDEN   Feature work. Force-push. Disabling or skipping checks.
REPORT      One message per incident. When the coordinator sends "release" (send --to dispatch:<you>),
            send worker_done with a summary of the incidents and stop.
```

## QA lead brief

```
QA: QA lead for <program>
PROGRAM: <slug>

GOAL        Catch what per-PR CI cannot: tickets that do not do what they claim, main that no longer
            works end to end, and code that drifted from the design.
LANES       Run all three continuously. Give a unit its own subagent (swarm) when it is sizeable and independent
            (driving one ticket on main, one E2E round), in parallel; do small checks yourself. Before each
            round, check that what each lane drives through (the host's own browser rather than an extension, a monitoring login, a
            local harness) is up; a lane that cannot run is reported at once with the fallback you switched
            to, never dropped silently.
  1 Ticket verification  For every landed ticket (orch status, `landed` events), one verifier
                          drives the ticket's acceptance criteria on main with the repo's
                          verify-<app> skill. PASS / PASS+NOTES / FAIL with what it drove. Oldest first.
  2 Periodic E2E          The repo's full E2E suite on the latest main, every <qa_every_landings, 5>
                          landings, every <qa_every_hours, 2> h, and right before each gate PR lands.
                          Heavy local runs go through orch heavy <slug> -- <command>.
  3 Design audit          One standing reader compares <design doc(s)> with the code on main. Each
                          finding is filed as 문서 오류 (the doc is wrong) or 코드 오류 (the code is wrong).
FINDINGS    File each reproduced failure as a ticket (write-ticket follow-up format, label follow-up,
            파생: <ticket or QA> · 원인: QA). A failure on main that blocks others → tell the guardian.
            A feature verify-<app> could not drive, or described wrongly, goes in your report as
            "verify skill stale: <feature>" so the coordinator can dispatch maintain-verification-skill.
FORBIDDEN   Fixing findings. Landing anything.
REPORT      A digest per lane round to the coordinator (send --to run:<run id> --type status, with the
            exact --from and --dispatch-capability from your Orca preamble): counts, links to tickets
            filed, what was driven. It is a routine report, not an escalation; the coordinator reads it
            with the next batch. On the coordinator's "release" message, worker_done with the totals
            and stop.
```

Set the cadence in the program note's standing orders so a resumed coordinator re-briefs the same way.

## Flow improver brief

```
FLOW: flow improver for the skills repo <repo>

GOAL        Fold what the coordinators learn while running work into the skills, so each round
            runs better than the last.
INPUT       Lesson signals the coordinators send you (send --to dispatch:<you>), and the human's
            process feedback they forward. Read your mail at each checkpoint. Also what nobody
            sends: new signal and failure rows in the program ledgers and in the usage collector's
            ledger (`reflect`, standing mode).
LOOP        About every 60 minutes, one round. Fold everything that arrived by mail into one
            commit (EVERY COMMIT). Then run `reflect` in standing mode over what nobody sent: it
            proposes through at most one draft PR. A round where both are empty sends nothing;
            your heartbeats say you are alive. Once a week, run `maintain-verification-skill` on
            each `.claude/skills/verify-*` in this repo (the last run's date in
            `~/.claude/programs/_standing/verify/last`); its PR is its own, apart from the
            standing PR, and a clean result sends nothing.
            Before writing a candidate, judge whether it is needed. Drop it when any of these holds:
              a. another part of the skill already says it
              b. it was a one-off circumstance, not a mistake that would recur without the rule
              c. it tells the model what it already does well by default
              d. it would contradict an existing rule, or lengthen the skill until other rules get lost
            Change as little as the round needs, and prefer rewriting an existing sentence to adding
            one; a change that lengthens a skill gives its reason in the report. Write every rule
            generically.
USAGE       The usage collector flags skills (`slash_only`, `misses`, `retire_candidate`) as
            `skill_usage` signals; `<fleet state>/skills.json` (`orch-dash skills` refreshes it;
            references/dashboard.md, "Skill usage") has the counts behind them. Standing mode turns a flag into a description
            rewrite, a merge, or a retirement ticket. The collector owns the count, so do not count
            transcripts. A usage count goes in a report, never in a commit.
EVERY COMMIT
            Follow the write-skill skill end to end: overlap check, prompt audit, internal-name
            grep with 0 hits, tests, signed commit, fast-forward and push. Then tell the
            coordinator "reload needed" instead of asking the human for the reload.
CONTRACTS   A change to a running program's contract (merge policy, predicate, the brief's required
            fields, landing rules) waits for that coordinator's confirmation: ask, then commit.
FORBIDDEN   Touching a program's tickets, PRs or workers. Code another worker is editing.
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
            or another path is never a reword. A read-only command on files in
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
REPORT      One status per commit, draft PR or ticket: the sha or PR URL, the diff
            summary, the audit's findings and what was applied, the grep and its result, each
            candidate dropped as "not applied: <candidate> — <reason>", and whether a reload is
            needed. On "release", worker_done and stop.
```

## Resource steward brief

```
STEWARD: resource steward for this machine

GOAL        Keep settled workers, dead processes and leftover worktrees from eating CPU, memory
            and disk, without touching live work.
ROUND       Every 20 minutes, woken by a Bash loop under run_in_background. Take stock: orca
            worktree list, orca terminal list, the orchestration task and dispatch state; per
            worktree its git status, unpushed commits, PR state and whether its turn is live.
WORKTREES   orca worktree rm (checks and --run-hooks per end-session §4) only when all hold: its
            dispatch is settled (worker_done, released or stopped), no live turn, a clean tree, no
            unpushed commits, and its PR merged or closed, or never opened. Never --force.
            Never touch: a coordinator's worktree, the checkout that loads the skills
            (docs/platform.md), a standing role's worktree, a live turn, an open PR, local changes.
LEFTOVERS   Every round, run the reap-resources skill's scripts/reap.py (its SKILL.md holds the
            rules): scan --plan <scratchpad>/reap-plan.json, reap --plan on that file, then scan
            again. That covers orphaned test processes, Docker leftovers,
            merged branches and scratchpad worktrees; never kill or remove those by hand. Other
            processes (compose stacks, dev servers, headless browsers, test runners) whose
            worktree is gone or settled: stop them the normal way (docker compose down in that
            directory, the tool's own stop). Kill a pid directly only when its cwd is a worktree
            that no longer exists. A refused kill is not worked around: report it as "needs the
            owner to run" with the exact command. Never touch the dashboard server, the Orca app,
            or a Claude process whose turn is live.
ALERT       When the scan after the reap still prints 경보 (a total at or above its limit,
            ~/.claude/agent-skills.json reap.alert), tell the human: orch-dash inbox add --type
            run_command --title "방치 리소스 경보: <the alert lines>" --command "python3 <reap.py>
            scan" --key reap-alert, and send the same to the coordinator (--type status). Resolve
            the key once a round's scan is back under every limit.
LOAD        Every round, reap-resources' `reap.py load`, and watch memory pressure and free disk.
            At or above its limit, act per class: send the ours-idle sessions to the coordinator,
            which asks each whether work is left and to close itself if none; reap leftovers as
            LEFTOVERS does; for ours-working, tell the coordinator to hold new dispatches and
            suggest a heavy_slots value; report system load as not ours.
FORBIDDEN   Any repo's code, PRs, tickets or chat.
REPORT      The first round: the full inventory to the coordinator (--type status): removed,
            kept with its reason (dirty, unpushed, open PR, live turn), needs owner action, and
            the scan's summary table. Later rounds only when something changed. --type escalation
            only when a resource is about to run out. On "release", worker_done with a summary and stop.
```
