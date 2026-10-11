# Running the Codex reviews

The `/codex:*` slash commands are user-only, but you run the same reviews yourself through the
companion:

```bash
node ~/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs review --background --base main --scope branch
node ~/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs adversarial-review --background --base main --scope branch
node ~/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs result <job-id>
```

Run them as Bash calls with `run_in_background`: some companion versions answer inline instead
of returning a job id, and the background call keeps you working either way. Run one Codex job
at a time; two concurrent jobs kill each other.

`review` hunts defects. `adversarial-review` challenges the approach itself and earns its own
round whenever the design, not the defect count, is what you are unsure about.

On security code (a guard, a scanner, a redactor), a free-form `task` or subagent prompt that
asks for an attack, such as an input that leaks a value or slips past a check, can be refused
by the model's cyber-safety filter even though the code is defensive; the diff-scoped `review`
and `adversarial-review` still run. Put the attack cases in a test oracle you run yourself (generated
inputs, a must-fail control), and treat a refusal like any other: report it, do not reword it
and retry.

## Which model

Both reviews run on whatever `~/.codex/config.toml` sets, which is `gpt-6-sol` (reasoning
`high`), the default for everything. Drop sol to `medium` for routine or narrow checks. Escalate
to `gpt-6-astra` only for the hard problems: detailed design, an investigation with no obvious
shape, orchestrating several moving pieces. The reviews take no `--model`, so escalation goes
through `task`:

```bash
node ~/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs task --background --model gpt-6-astra --effort medium "<the hard question>"
```

After a `--background` job, wait with `status <job id> --wait --timeout-ms 1800000` as a Bash call with `run_in_background`, which wakes you when the job ends, and read `result <job id>`. A companion that answered inline needs no `status`: its own background call wakes you. With such a call running, ending your turn is how you wait; do not loop `sleep` over its output file. Ending the turn with nothing running in the background ends an unattended run for good.

A design you are unsure of is exactly that case: run `adversarial-review` for the sweep, and put
the one question it cannot settle to astra as a `task`. Astra runs at `medium`, or `low` for a
bounded question, never `high` or `xhigh` (the user's call).

For an investigation or a fix you want Codex to drive end to end, use the `codex:rescue` skill.

## When Codex returns no review

A run can exit 0, and even fill the verdict its output schema requires, without having reviewed
anything. Read the body: when it says Codex could not read the repository or the diff (tool or
repository-access timeouts, "unable to review"), the run is no review, whatever the exit code
and verdict say. So is a job that never gets past "Starting Codex review thread".

No review does not close the review. A Claude reviewer that carries the implementing session's
context (a `fork`, or a subagent briefed on how and why the change was made) does not stand in
for it; step 3's context-free review is the one exception. Get Codex to run instead:

1. **Diagnose**, a few minutes at most:
   - `codex login status`. Logged out or expired: Codex is unavailable, and logging in again
     is the human's. Step 3 still lands the work so an unattended run does not stop on it; the
     PR body's review-scope line and the final report both say "Codex login expired: the
     owner must run `codex login`", so it reaches the human.
   - Stuck at "Starting Codex review thread": that workspace's broker is stale. Kill the pid in
     `~/.claude/plugins/data/codex-openai-codex/state/<workspace>-<hash>/broker.json` and the
     hung companion; a fresh broker starts with the next run. A review that started and then
     timed out reading the repository is not this.
   - Reproduce small in the same worktree, stdin closed (`</dev/null`):
     `codex exec --sandbox read-only "Run git diff --stat <base>...HEAD 2>/dev/null and print its output"`.
     On macOS the sandboxed git prints xcrun cache errors on stderr; they are noise. If this
     fails too, run `codex exec --skip-git-repo-check --sandbox read-only "Reply ok"` from a
     scratch directory. That one failing points at the network or the Codex service; only the
     repository one failing points at the sandbox's access to this checkout.
   - Keep the line that names the cause (the spend-cap or usage-limit message, the service
     error, the `login status` output). The next steps cite it as evidence.
2. **Fix what the diagnosis found and rerun the same review command.** Up to three retries,
   waiting 2, 5 and 10 minutes before them (a Bash `sleep` with `run_in_background`). When the
   diagnosis shows Codex itself is unavailable, that schedule is replaced: a spend-cap,
   usage-limit or service error gets a single rerun after 5 minutes, a failed login none; if
   it still fails, go to step 3.
3. **Codex itself unavailable: an independent Claude Code review stands in.** Only when step 1
   kept a line that puts the cause on Codex's side: a spend-cap or usage-limit message, a
   service error, a failed `login status`. Not when Codex ran and returned findings, not when
   only the repository repro failed or the run timed out reading the checkout, and not when
   "Reply ok" failed on a local network or proxy error: those go to step 4.
   - **Launch** a reviewer that shares no context with the implementing session: a new
     subagent (not a `fork`, which inherits your context) or a separate headless `claude`
     process. Its prompt carries the repository, the PR number and the head sha, nothing about
     how or why the change was made, and has it run Claude Code's own code review (the
     `code-review` skill, `/code-review <PR>`) on that head and report the sha it actually
     reviewed. A different sha means the head moved: review again.
   - **Sort** its findings as `SKILL.md` §3 does. Every Act-on finding is a blocker: fix it,
     push, and review the new head with a fresh reviewer the same way; §3's round cap applies
     as it does to Codex. Try Codex again before each round and before the land; once it
     reviews, its result closes the review and its Act-on findings get fixed, whatever the
     stand-in found.
   - **Record** one PR comment (a sticky comment where the repo uses one) with the review
     summary, the blocker list or "none", the reviewed head sha, and the Codex line from step 1.
   - **Land** with zero blockers, CI green and merge state CLEAN: record the verdict on that
     head with source `claude-review:independent` and the Codex line as its note, and land
     under the merge approval the work already has; this path adds no approval of its own and
     replaces only the Codex row, not a triggered row of §3. If a permission prompt or the
     auto-mode classifier refuses the land, report the refusal like any other; do not retry it
     in another form.
4. **Still no review, and step 3 does not apply: hold the land and go to the human.** Record no
   verdict and do not merge. Report the failing runs' job ids and the body line that says no
   review, the diagnosis output, and what you tried; in an orchestrated program, as an `ask` to
   the coordinator. A Claude subagent may review the diff meanwhile as an extra reviewer, and
   its Act-on findings get fixed, but its result is not recorded in Codex's place.
