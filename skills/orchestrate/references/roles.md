# Standing roles

A program of more than a handful of tickets runs standing workers beside the ticket workers: a main guardian and a QA lead per program, and one flow improver and one resource steward per machine. Each standing worker is an Orca worker with its own worktree and brief, spawned at Scale. They report to the Run inbox like any worker, and the coordinator records each with `orch record <slug> spawned --role <name> --note <dispatchId>` so they stay outside the concurrency cap. None of them lands feature work.

The flow improver and the resource steward serve every program on the machine, so there is only one of each. The top-level orchestrator owns them when there is one; otherwise the first program to reach Scale spawns them, and a later program reuses the running one (`worker-list`). They are released at Close only when no other program still runs; the per-program roles are always released at Close.

| Role | Owns | Does not |
|---|---|---|
| Coordinator (this session) | Order, deps, briefs, triage, gates, the digest | Diagnose red main, run QA, rework the process, reap worktrees |
| Main guardian | Red main: flake or defect, freeze, hotfix or revert, notify | Pick up tickets |
| QA lead | Ticket verification, periodic E2E on main, design-vs-code audit | Fix what it finds (it files tickets) |
| Flow improver | Folding lesson signals and the human's process feedback into the skills while programs run: batched, audited, pushed, then reloaded | Touch a running program's work, or change its contract (merge policy, predicate, the brief's required fields, landing rules) before that program's coordinator confirms |
| Resource steward | Reaping settled worktrees the coordinators missed, stopping orphaned heavy processes, watching machine load | Touch a live turn, a dirty tree, an open PR, a coordinator's or standing role's worktree, or the dashboard; `--force` |

They came from the user's own calls on a real program (2026-09-24):

- On red main: "무조건 revert 하기보다는 별도의 agent에 위임해서 revert/hotfix를 자율 판단·대응하고 결과를 너에게 보고 + 필요한 세션에 공유, 너는 조율만".
- On QA: "중간중간 동작확인 QA, e2e 테스트, 설계구현 정합성 확인도 전문 QA 오케스트레이터로 해야 한다".
- On process feedback: the coordinator stays on the mission. Feedback about how the work flows goes to a separate session, which improves the skills while programs run and shares the result (2026-09-26).

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
               guardian-log.tsv; you write nothing outside your worktree.
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
            (driving one ticket on main, one E2E round), in parallel; do small checks yourself.
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
            "verify skill stale: <feature>" so the coordinator can ask for /maintain-verification-skill.
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
            process feedback they forward. Read your mail at each checkpoint.
LOOP        About every 60 minutes, take everything that arrived and fold it in as one commit.
            Before writing a candidate, judge whether it is needed. Drop it when any of these holds:
              a. another part of the skill already says it
              b. it was a one-off circumstance, not a mistake that would recur without the rule
              c. it tells the model what it already does well by default
              d. it would contradict an existing rule, or lengthen the skill until other rules get lost
            Change as little as the round needs, and prefer rewriting an existing sentence to adding
            one; a change that lengthens a skill gives its reason in the report. Write every rule
            generically.
USAGE       Periodically count Skill tool calls per skill name in the local transcripts (name, count,
            last use; never read or copy their content). For a skill outside the development flow
            that goes unused, judge why: its description misses the trigger, it overlaps another
            skill, or it is no longer needed. Fix the first two with the smallest description change;
            only propose deleting the third. The count goes in the report, never in a commit.
EVERY COMMIT
            - Run the claude-api skill's prompt audit on the changed skill files and apply what it finds.
            - Keep the repo's translation markers current (the catalog's translated-from lines).
            - The repo's tests pass. The commit is signed.
            - Grep the diff for internal names (the local list, never committed); 0 hits.
            - Fast-forward the loaded checkout and push; then tell the coordinator "reload needed".
CONTRACTS   A change to a running program's contract (merge policy, predicate, the brief's required
            fields, landing rules) waits for that coordinator's confirmation: ask, then commit.
FORBIDDEN   Touching a program's tickets, PRs or workers. Code another worker is editing.
REPORT      One status per commit: the sha, the diff summary, the audit's findings and what was
            applied, the grep and its result, each candidate dropped as
            "not applied: <candidate> — <reason>", and whether a reload is needed. On "release",
            worker_done and stop.
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
PROCESSES   Compose stacks, dev servers, headless browsers and test runners whose worktree is
            gone or settled: stop them the normal way (docker compose down in that directory, the
            tool's own stop). Kill a pid directly only when its cwd is a worktree that no longer
            exists. A refused kill is not worked around: report it as "needs the owner to run"
            with the exact command. Never touch the dashboard server, the Orca app, or a Claude
            process whose turn is live.
LOAD        Watch load average, memory pressure and free disk. When heavy local runs pile up,
            tell the coordinator and suggest a heavy_slots value.
FORBIDDEN   Any repo's code, PRs, tickets or chat.
REPORT      The first round: the full inventory to the coordinator (--type status): removed,
            kept with its reason (dirty, unpushed, open PR, live turn), needs owner action. Later
            rounds only when something changed. --type escalation only when a resource is about
            to run out. On "release", worker_done with a summary and stop.
```
