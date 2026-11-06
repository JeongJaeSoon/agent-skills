# How `schedule` builds the round's prompt

The prompt is built from `kinds`: each configured kind's `PROMPT_STEPS` entry (next to `COLLECTORS`
in `prs.py`; `None` when the message is the whole action) adds its step, in `PROMPT_STEPS` order
(`unreleased` last, since it gates `commit`), and `schedule` refuses a kind without an entry. Without
`review_wait`, `ready_to_merge` or `ownergate` the prompt forbids GitHub writes, and it forbids every
write the configured kinds do not need. `ready_to_merge`'s and `ownergate`'s entries are `None`
because their steps run before the send: merges and `verify` first, then `round`/`result`
(`PRE_SEND_STEP`), whose results are appended to the message. Every prompt ends with the rule that
a refused act is reported, never retried. Items of a kind with no `PROMPT_STEPS` entry never
reach `plan.json`, so they are neither sent nor recorded; the precheck output and the message list
them as uncovered. The round's own commands are `precheck`, `plan --body` and `commit`, plus `merges`
and `verify` with `ready_to_merge` and `round` and `result` with `ownergate`, so the
prompt is fixed when it is made: after `kinds` changes, rerun `schedule --edit <id>` before the
next round (check `orca automations list` first so other kinds' steps are not lost).
`after_hours`, `weekend_days`, `assign_reviewers` and `profiles` are read each round and need no
`--edit`, except that an automation made before its prompt carried the `github` step must be
rewritten once before `assign_reviewers` takes effect.
Every command starts with `python3`: a scheduled session can refuse a leading `env` or `VAR=`
assignment as a command of its own, so a non-default state goes in as `--state`.

The run's model is `prs.model`, or with none its `--workspace` checkout's settings; for
Claude, `--edit` sets the automation's extra agent arguments to that value, or clears them, so pass
`--provider` for another agent. `--edit` leaves the workspace as it is. For a runner worktree with its own model, see
[docs/automation.md](../../../docs/automation.md).

A GitHub 5xx (GraphQL's 10-second timeout, or an outage) is retried twice before the precheck counts it as
`gh` failed. `schedule` sets the precheck timeout to 180 seconds; Orca's default of 60 is shorter than a
precheck with a retry.

The default trigger is every 30 minutes: GitHub's GraphQL limit (5000 points per hour) is shared by
every session using the same token. The steady state is weekday business hours, e.g.
`*/30 9-19 * * 1-5` with `timezone` set; change `cron` in the config and run
`schedule --edit <id> --write` (it rewrites the trigger from the config, so a cron set only in Orca is lost). `orca automations run` skips the precheck, so it
proves nothing about it: test the precheck by running it directly with `--state` pointing at a
scratch folder, and judge the automation by a scheduled run in
`orca automations runs --id <id>`. Devices and their trade-offs:
[docs/automation.md](../../../docs/automation.md).
