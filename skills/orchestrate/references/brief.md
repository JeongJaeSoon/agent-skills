# The brief

The brief is the coordinator's product. A worker cannot see this session, its siblings or the program note. Everything it needs has to be in the spec. If you cannot fill a field, you have not scoped the unit yet, so do not spawn it. (After pstack Orchestrate, "The brief"; MIT, Lauren Tan.)

GOAL, SCOPE, FORBIDDEN and ACCEPTANCE below cover Orca's own Task-spec contract (Target, Change, Constraints, Ownership, Observable acceptance).

```
<TICKET-ID>: <one-line title>
PROGRAM: <slug>

GOAL        One sentence: the outcome, executable by someone with no access to this chat.
SCOPE       Paths this unit may write and paths it may not. Its own worktree and branch.
            Base: main | feat/<topic> | stacked on #<PR> (gh stack link <lower> <this>).
CONTEXT     Ticket URL. Files and PRs to read. Upstream reports pasted in full when this
            unit depends on them.
ORDER       Starts after <TICKET…> (Orca deps) · lands after <TICKET…> (stack / prog.py dep).
            Class: normal | urgent | gate.
            Lane: normal, or exclusive when it touches migrations, CI, Dockerfile, compose or the
            program's exclusive_paths (land handles it; say it here so the worker expects it).
PEERS       Who to settle shared files and landing order with directly, and about what:
            `orca orchestration send --to dispatch:<id> --subject … --body …`. Tell the
            coordinator only what changes scope, order or the predicate.
ACCEPTANCE  Checkable criteria, one per line.
VERIFY      Exact commands, or the repo's .claude/skills/verify-<app> feature to drive,
            plus known gotchas. Heavy local runs (compose stacks, image builds, local E2E) go
            through `prog.py heavy <slug> -- <command>`, which caps them machine-wide.
TIMEBOX     Rough cap. When it runs out, report partial findings with --outcome failed and stop.
LAND        After review, record the verdict on the reviewed head: prog.py verdict <slug> --pr <N>
            --sha <head> --source <the reviewer: codex-review, subagent-review (trivial diff,
            deliver-ticket §3), verifier:<model>, live:<feature>; never the implementer>. Then the one-minute self-check: `git diff --name-only <CI base>..origin/main`; if any
            of it touches a contract or test premise this PR relies on, merge main in and let CI
            rerun. Then python3 ~/.claude/skills/orchestrate/scripts/prog.py land <slug> --pr <N>
            --ticket <ID> --wait-minutes 50 as a Bash call with run_in_background: true (not
            `&` or a redirect: the completion notice carries its output and exit code). Act on
            exit 3, report on exit 1.
            Never merge any other way. (human-gate: stop at READY and report instead.)
FORBIDDEN   Do not start other tickets. File follow-ups with the label `follow-up` and the first
            body line `파생: <this ticket> · 원인: <분류>`, and do not work on them. No force-push
            to shared branches. Do not rebase only because the branch is behind; `land` says when.
            <unit-specific bans>
REPORT      worker_done once, after landing and main CI (or at READY under human-gate).
            Body: what changed, what was verified and how, what remains. Include the PR URL,
            merge commit, review rounds and who reviewed, the VERIFY output you actually saw,
            the main-CI run you read, and any follow-up tickets filed. --outcome succeeded only
            when the ticket's acceptance criteria hold.
STANDING    <the program note's standing orders for workers, pasted verbatim, numbered; the
            coordinator-only ones stay in the note>
```

## Rules for filling it

- The spec starts with the ticket ID, never `/`. The second line, `PROGRAM: <slug>`, is how the user's own skills (`deliver-ticket`, `handoff-ticket`) know they are running inside a program. Nothing else switches them.
- The worker runs the user's normal flow (`deliver-ticket`) for implementation and review. LAND, ORDER, PEERS and REPORT are what change inside a program.
- **Name the card.** Orca's automatic title comes from the first prompt and can be meaningless ("Orca multi-agent IDE worker 설정" was ENG-278). Pass `worker-start --display-name "<ID> <short title>"`. That name sticks; the terminal tab title belongs to the agent, which overwrites a rename.
- **Plan chains as stacks.** A unit that has to land after another unit's PR builds on that branch (`Base: stacked on #N`), so the chain lands in one merge. Record it with `prog.py dep`, not as an Orca dep: an Orca dep would keep this unit from starting until the lower PR had landed.
- Keep every write inside the worker's worktree. A write elsewhere can stop the worker on a permission prompt while Orca still reports it `live`. Anything kept outside it (logs, notes, program files) comes back in the worker's message, and the coordinator writes it.
- Size the brief to the unit. A one-command unit collapses to a paragraph that still names the goal, the scope, the verify command, the LAND line and the report shape.
- Save the exact text to `~/.claude/programs/<slug>/briefs/<ticket>.md` before `worker-start`. Afterwards run `prog.py record <slug> spawned --ticket <id> --note <dispatchId>`.
- A dependency is a context relay: paste the upstream worker's report into the downstream brief.
- Never resume-chain a brief. A retry gets a fresh brief with the consolidated scope.
- A verifier unit on a high-blast-radius PR runs on another model family (`worker-start --agent codex`). It reports PASS, PASS+NOTES or FAIL, with what it drove.
- Feedback the user gives on in-flight work ("improve this", "do it better") becomes a new ticket and a new brief. It is never appended to the running one.
