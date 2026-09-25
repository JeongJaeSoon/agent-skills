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
