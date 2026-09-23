# The brief

The brief is the coordinator's product. A worker cannot see this session, its siblings, or the program note, so everything it needs is in the spec. A field you cannot fill is a unit you have not scoped yet: do not spawn it. (After pstack Orchestrate, "The brief"; MIT, Lauren Tan.)

Orca's own Task-spec contract (Target, Change, Constraints, Ownership, Observable acceptance) is covered by GOAL, SCOPE, FORBIDDEN and ACCEPTANCE below.

```
<TICKET-ID>: <one-line title>

GOAL        One sentence: the outcome, executable by someone with no access to this chat.
SCOPE       Paths this unit may write; paths it may not. Its own worktree and branch.
CONTEXT     Ticket URL. Files and PRs to read. Upstream reports pasted in full when this
            unit depends on them.
ACCEPTANCE  Checkable criteria, one per line.
VERIFY      Exact commands, or the repo's .claude/skills/verify-<app> feature to drive,
            plus known gotchas.
TIMEBOX     Rough cap. On expiry, report partial findings with --outcome failed and stop.
FORBIDDEN   Do not merge. Do not start other tickets. File follow-ups with the label
            `follow-up` and first body line `파생: <this ticket> · 원인: <분류>`, and do not
            work on them. No force-push to shared branches. <unit-specific bans>
REPORT      worker_done once. Body: what changed, what was verified and how, what remains.
            --outcome succeeded only when the PR is open, ready (not draft), its review
            loop ended with no Act-on finding, and CI passed at the reported head.
            Put in the body: PR URL, head SHA, review rounds and who reviewed,
            the VERIFY output you actually saw, follow-up tickets filed.
STANDING    <the program note's standing orders, pasted verbatim, numbered>
```

## Rules for filling it

- The first character of the spec is the ticket ID, never `/`.
- The worker runs the user's normal flow (`ship-pr`) for implementation and review. FORBIDDEN and REPORT are what change inside a program: the worker stops at merge-ready and reports; the coordinator lands.
- Keep every write inside the worker's worktree. A write elsewhere can stop the worker on a permission prompt while Orca still reports it `live`.
- Size the brief to the unit. A one-command unit collapses to a paragraph that still names goal, scope, the verify command, and the report shape.
- Save the exact text to `~/.claude/programs/<slug>/briefs/<ticket>.md` before `worker-start`, and `prog.py record <slug> spawned --ticket <id> --note <dispatchId>` after.
- A dependency is a context relay: paste the upstream worker's report into the downstream brief.
- Never resume-chain a brief. A retry gets a fresh brief with the consolidated scope.
- A verifier unit on a high-blast-radius PR runs on another model family (`worker-start --agent codex`) and reports PASS, PASS+NOTES or FAIL with what it drove.
