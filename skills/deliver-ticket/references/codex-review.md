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

## Which model

Both reviews run on whatever `~/.codex/config.toml` sets, which is `gpt-6-sol` (reasoning
`high`), the default for everything. Drop sol to `medium` for routine or narrow checks. Escalate
to `gpt-6-astra` only for the hard problems: detailed design, an investigation with no obvious
shape, orchestrating several moving pieces. The reviews take no `--model`, so escalation goes
through `task`:

```bash
node ~/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs task --background --model gpt-6-astra --effort medium "<the hard question>"
```

After a `--background` job, wait with `status <job id> --wait --timeout-ms 1800000` as a Bash call with `run_in_background`, which wakes you when the job ends, and read `result <job id>`. Ending the turn to wait instead ends an unattended run for good.

A design you are unsure of is exactly that case: run `adversarial-review` for the sweep, and put
the one question it cannot settle to astra as a `task`. Astra runs at `medium`, or `low` for a
bounded question, never `high` or `xhigh` (the user's call).

For an investigation or a fix you want Codex to drive end to end, use the `codex:rescue` skill.

## When Codex returns no review

A run can exit 0, and even fill the verdict its output schema requires, without having reviewed
anything. Read the body: when it says Codex could not read the repository or the diff (tool or
repository-access timeouts, "unable to review"), the run is no review, whatever the exit code
and verdict say. So is a job that never gets past "Starting Codex review thread".

No review does not close the review, and a Claude subagent's review does not stand in for it.
Get Codex to run instead:

1. **Diagnose**, a few minutes at most:
   - `codex login status`. Logged out or expired is the human's to fix; go to step 3.
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
2. **Fix what the diagnosis found and rerun the same review command.** Up to three retries,
   waiting 2, 5 and 10 minutes before them (a Bash `sleep` with `run_in_background`).
3. **Still no review: hold the land and go to the human.** Record no verdict and do not merge.
   Report the failing runs' job ids and the body line that says no review, the diagnosis
   output, and what you tried; in an orchestrated program, as an `ask` to the coordinator. A
   Claude subagent may review the diff meanwhile as an extra reviewer, and its Act-on findings
   get fixed, but its result is not recorded in Codex's place.
