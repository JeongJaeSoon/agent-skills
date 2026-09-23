---
name: ship-pr
description: This user's path from non-trivial work to a finished ticket — plan gate, test-with-change invariant, review by scope, GitHub stacks for dependent PRs, the E2E gate before pushing, the sticky test-results comment, and what "done" means. Read before starting multi-file work or opening a PR.
---

# Shipping a ticket

## 1. Plan

Non-trivial work — multi-file changes, architectural decisions, ambiguous scope — is planned
before the first edit. **Planning is not a checkpoint: do not enter plan mode and do not wait
for approval.** Write the plan where it stays readable — a comment on the ticket, the PR body,
the worklog — and start. The ticket, or the `/goal` that opened the card, is the approval for
exactly what it says.

Reading the repo can change the picture. If the scope turns out to be materially different from
the ticket, do not stop: either it still fits one reviewable PR, or you split the ticket (§5) and
say so. Ask only when a decision is genuinely blocked — `AskUserQuestion` with concrete options,
then keep going.

## 2. Implement

On a new branch in its own worktree — never in the main checkout. A handoff card already is
one; starting by hand, create one first. The ticket carries the what and the implementation
hints; how you get there is your call. One invariant: **no commit of non-trivial logic without a runnable
test** written alongside the change and actually run.

**Findings outside the ticket.** Work turns up things the ticket did not ask for — a latent
bug, a missing validation, a contract inconsistency. Decide yourself, at the moment you find
it, and never end a turn on "말씀 주시면 티켓으로 만들겠습니다":

- Fixable in this diff and inside the ticket's scope → fix it here.
- Needs its own investigation, decision, or diff → file it now with `write-ticket` (its
  approval gate does not apply); check `list_issues` first so you do not duplicate one.
  One ticket per independent diff; findings that must land together share one.
- Speculative, or not reproduced in the repo → write it in the worklog only, no ticket.

Name every ticket you filed in the report. The user's review happens on the ticket, not
before it exists.

## 3. Review

By scope, not by vendor:

- `/code-review` — the working diff, before a PR exists
- `/review` — once a PR exists
- `/security-review` — the diff touches auth, crypto, or untrusted input

**Codex is the second pair of eyes on anything non-trivial**, not an optional extra. The
`/codex:*` slash commands are user-only, but you run the same reviews yourself through the
companion:

```bash
node ~/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs review --background --base main --scope branch
node ~/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs adversarial-review --background --base main --scope branch
node ~/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs result <job-id>
```

`review` hunts defects; `adversarial-review` challenges the approach itself and earns its own
round whenever the design, not the defect count, is what you are unsure about.

**Which model.** Both reviews run on whatever `~/.codex/config.toml` sets, which is
`gpt-5.6-sol` — the default for everything. Escalate to `gpt-6-astra` only for the hard
problems: detailed design, an investigation with no obvious shape, orchestrating several
moving pieces. The reviews take no `--model`, so escalation goes through `task`:

```bash
node ~/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs task --background --model gpt-6-astra --effort xhigh "<the hard question>"
```

A design you are unsure of is exactly that case: run `adversarial-review` for the sweep, and
put the one question it cannot settle to astra as a `task`. Do not reach for astra by default —
sol is the workhorse, astra is the escalation. Start them in
the background and do other work while they run. Then apply every finding, or say in the PR
why you are rejecting it, and re-run until a pass comes back with nothing material — that
verdict, not your own reading of the diff, is what closes the review. For an investigation or
a fix you want Codex to drive end to end, use the `codex:rescue` skill.

## 4. Commit

Incrementally, with plain `git commit`. Never `git reset --hard` while uncommitted work
exists — commit it or copy it aside first.

## 5. Ship

Before pushing, run **both**:

- the test suite
- an E2E check against a running system — CLI / `curl` for backend and APIs, **Aside** for UI
  and web flows (the `aside-browser` skill; it is the browser for everything, logged-in sites
  included), IDE diagnostics for type and lint

Then push and open the PR with `gh pr create` (`--draft` for WIP, `Closes #n` to link an
issue). The PR body must carry a functional-verification section describing the exact test
method used.

**One ticket, one PR.** A ticket never spans two PRs: if the work will not review as a single
PR, split the ticket — the other half becomes its own ticket with its own PR. The reverse is
fine: one PR may close several tickets when the diff is inseparable. List every ticket id in
the body.

**Dependent PRs are a GitHub stack** — one ticket per layer, stacked because the lower one must
merge first — not just a PR whose base is another branch. After
opening them, link with the `gh stack` extension (`gh stack link <bottom> <top> --base main`,
or `gh stack init` / `add` / `submit` from scratch) and confirm the stack icon with
`gh pr view`. Each layer keeps its own verification section and sticky comment, verified on
top of the layer below. Stacked PRs reject `gh pr merge`; merge bottom-up with
`gh api -X PUT repos/<owner>/<repo>/pulls/<n>/merge-async -f merge_method=squash -f sha=<head>`,
then wait for the upper PR to auto-retarget to main and rerun CI before merging it the same way.

Post the results as a **sticky comment**:

```bash
bash ~/.claude/scripts/sticky-comment.sh <pr> <body-file>
```

It upserts on the `<!-- test-results -->` marker, so results never stack. Include suite
counts, E2E output, and an explicit "not covered" section. E2E belongs in that comment,
not in CI.

**A green check is not evidence.** Confirm from logs that the step actually ran
(`gh run view <id> --log`) — a skipped step, a rate-limited reviewer, and a real pass all
look identical in the checks list.

## 6. Finish the ticket — done means its acceptance criteria are met

**Done = every acceptance criterion on the ticket is met.** Not merge-ready, not PR-opened, and
not merged either: the merge is usually one of the criteria, never by itself the definition.
Re-read the ticket at this point and walk its acceptance-criteria section — whatever the current
Linear template calls it. Each criterion was written with the way to check it, so check it that
way and keep the output.

Getting there starts with the PR. Stay on it until **all CI checks are green and all review
threads are resolved** (`gh pr checks` / `gh pr view`).

- Review comments → respond and fix.
- CI failures → re-cycle through steps 1–4.

Then merge it.

```bash
gh pr merge <n> --squash --delete-branch      # a stack merges bottom-up, see §5
gh pr view <n> --json state,mergeCommit       # confirm MERGED and record the commit
```

Merging is yours to do; no need to ask. Ask first only when the user said to hold, when they
said they wanted to look at this one themselves, or when the state is off-script — a check that
keeps flaking, a thread you resolved on the author's behalf, a migration you cannot roll back.

After the merge commit is confirmed, finish whatever the ticket still asks for — a release, a
deploy, a migration run, a verification that only makes sense on main. A merged PR with a
criterion left open is not done, and the session stays open until that criterion is met or the
user says to stop.

Only then: move the Linear ticket to Done with a completion comment carrying the merge commit,
the PR link, and each criterion with the evidence that it holds. Update the Obsidian worklog.

**Say what the user should check themselves — without being asked.** First verify everything
that can be verified: if a check is only tedious, delegate it to a subagent and run it. What is
left for the user is what genuinely needs them — a judgment call you made on their behalf, how
something feels to use, data or an account only they have, a decision the ticket left open.
Name each one with the exact command or URL, and say why it is theirs. If nothing is left, say
that too. The same list goes into the Linear completion comment as "남은 확인 사항", so it
survives this session.

**The completion comment does not end the turn.** In the same turn, without waiting for a new
prompt, go to `handoff-ticket` §0 — what stays here, what becomes its own ticket, and which
card starts next. Closing with "say the word and I'll hand off" or "want me to file that?" is
stopping short: those are actions §0 already authorises, not questions to put to the user.
