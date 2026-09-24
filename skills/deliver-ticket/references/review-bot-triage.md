# Review-thread triage

For threads on an open PR — Codex, CodeRabbit, Copilot, or a person. Findings from the reviews
you run yourself before the PR exists (§3) are sorted with `interrogate`'s lead-judgment instead.
The aim is not to ignore bots by default; it is to stop treating every comment as a required
code change.

## Classify each thread before acting

- **fix** — a plausible correctness, security, privacy, data-loss, auth, billing, migration,
  idempotency, race, or shipped-behavior problem. Prove it red first, fix it in the lowest PR
  that owns the code, reply with the commit SHA, resolve the thread.
- **dismiss** — the comment matches a documented low-risk pattern below and the current code
  proves no change is needed. Reply with the concrete disproof and resolve the thread.
- **ask** — novel, high-severity, or ambiguous. Ask the user (`AskUserQuestion`; inside a
  program, report it to the coordinator) and keep working the other threads.

When in doubt, ask: skipping a noisy style comment is cheap, skipping a real data or security
bug is not. From a bot's third pass on, lean toward dismissing documented patterns, but the
categories below still go to ask.

## Ask by default

Never dismiss these on your own, even when a similar comment was dismissed before:

- Security, privacy, auth, billing, data retention, and permission-boundary findings.
- High-severity findings.
- Migration, schema, idempotency, concurrency, and cross-system behavior findings.
- A comment whose suggested fix is small and clearly lowers risk without changing intent — take
  the fix or ask; do not dismiss it.

A human who once dismissed a security or data-flow comment made an owner's judgment call, not a
rule for the next PR.

## Learned patterns

A pattern enters this file only after it has held on real threads. Shape:

```markdown
### <short pattern name>

- Confidence: candidate | recurring | strong
- Skip when: <conditions that must all be true>
- Do not skip when: <risk boundaries>
- Example signal: <phrases or code context that identify it>
- Source: <PR or comment URL>
```

`candidate` for one or two examples, `recurring` after several real dismissals, `strong` only
when the pattern is narrow, repeatedly verified, and low-risk. After a PR lands, offer any
pattern that would have saved time as an addition here through a PR to the agent-skills repo;
do not keep it only in memory.

### Usage the bot cannot see upstack

- Confidence: candidate
- Skip when: the bot calls an export, helper, or file unused, and a later PR in the same stack
  uses it.
- Do not skip when: the PR is not in a stack, the symbol is public API, or you cannot point at
  the upstack use.
- Example signal: "exported X is never used".

### An existing invariant already covers the warning

- Confidence: candidate
- Skip when: a shared component, framework contract, type, or single source of truth visible in
  the diff or nearby code already guarantees what the comment asks for.
- Do not skip when: the invariant is assumed rather than enforced, depends on timing, or crosses
  an async or state boundary where values can diverge.
- Example signal: "may be null" where the checked value and the passed value share one source.

### Widening a deliberately narrow error condition

- Confidence: candidate
- Skip when: the comment asks to broaden a specific errno, error code, or status class into a
  catch-all, and the narrowness encodes a real distinction — a fallback gated on `ENOENT`
  exists for a missing binary, not for a command that ran and failed.
- Do not skip when: the narrow condition misses a case in the same category (`EACCES` for an
  unusable binary), the unhandled path loses data or leaves partial state, or the retry is
  idempotent and still surfaces the original error.
- Example signal: "only retries on ENOENT, never tries the fallback when …".

### A finding already fixed later in the same PR

- Confidence: candidate
- Skip when: the comment reports a missing check that the PR's current head clearly has, with a
  test, usually added after the review ran.
- Do not skip when: the check runs after the side effect it guards, is a no-op for the case in
  question, or has no test for it.
- Example signal: a "missing authorization check" finding against a commit older than the head.

### Claims that a test drifted from what it pins

- Confidence: candidate
- Skip when: never skip the check itself — run the named test on the PR head first. Red confirms
  the claim; green is the disproof for the reply.
- Do not skip when: n/a. Repeat-pass leniency misfires here, because earlier fix rounds are
  exactly what makes pinned prose drift.
- Example signal: "the contract test no longer matches the doc".
