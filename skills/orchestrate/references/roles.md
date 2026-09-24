# Standing roles

A program of more than a handful of tickets runs two standing workers beside the ticket workers, the main guardian and the QA lead, plus a flow improver on demand. Each standing worker is an Orca worker with its own worktree and brief, spawned at Scale and kept until Close. They report to the Run inbox like any worker, and the coordinator records each with `orch record <slug> spawned --role <name> --note <dispatchId>` so they stay outside the concurrency cap. None of them lands feature work.

| Role | Owns | Does not |
|---|---|---|
| Coordinator (this session) | Order, deps, briefs, triage, gates, the digest | Diagnose red main, run QA, rework the process |
| Main guardian | Red main: flake or defect, freeze, hotfix or revert, notify | Pick up tickets |
| QA lead | Ticket verification, periodic E2E on main, design-vs-code audit | Fix what it finds (it files tickets) |
| Flow improver (a subagent or a separate session, on demand) | Turning the human's process feedback into skill or standing-order changes | Touch the running program |

They came from the user's own calls on a real program (2026-09-24):

- On red main: "무조건 revert 하기보다는 별도의 agent에 위임해서 revert/hotfix를 자율 판단·대응하고 결과를 너에게 보고 + 필요한 세션에 공유, 너는 조율만".
- On QA: "중간중간 동작확인 QA, e2e 테스트, 설계구현 정합성 확인도 전문 QA 오케스트레이터로 해야 한다".
- On process feedback: the coordinator stays on the mission. Feedback about how the work flows goes to a separate session or subagent, which improves the skills and shares the result.

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
