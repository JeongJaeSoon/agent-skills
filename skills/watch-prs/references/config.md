# watch-prs config

`~/.claude/agent-skills.json` → `"prs"`. The key must exist (an empty object is enough); every field
is optional. User-specific values (orgs, bot accounts, the orchestrator's or lead's address) live here and
never in this repo. A change to these keys goes through `skills-config`: the agent emits, the user applies.
`prs.model` names the rounds' Claude model: `schedule` passes it with `--extra-agent-args`. On its own it does not
turn the watch on.

```json
{"prs": {"orgs": [], "repos": [], "exclude_orgs": [], "exclude_repos": [],
         "exclude_title_patterns": ["^chore: sync shared config"],
         "self_bots": ["<the user's own bot account>"], "ignore_bots": ["<bot whose PRs are never a review>"],
         "inbox_to": "run:<id>",
         "max_items": 10, "stale_days": 30, "cron": "*/30 * * * *", "timezone": "<IANA tz>",
         "kinds": ["review_request", "review_wait", "ready_to_merge", "unreleased", "ownergate"],
         "ownergate": [<gate>, ...],
         "channel": "<default Slack channel id>", "repo_channels": {"<owner/repo>": "<channel id>"},
         "reviewers": {"<github login>": {"slack": "<Slack user id>", "channel": "<optional override>"}},
         "review_bots": {"<bot login>": "<the bot's re-review trigger comment; its verdict lines also count for ready_to_merge>"},
         "holidays": ["<YYYY-MM-DD>"], "urgent_labels": ["<label>"],
         "night_hour": 21, "morning_hour": 9, "weekend_days": [5, 6],
         "after_hours": "schedule-all | schedule-certain-only | send-now", "assign_reviewers": false,
         "profiles": [{"match": "acme/*", "after_hours": "send-now", "night_hour": 23}],
         "must_pattern": "<regex for a must-fix label; default \\[MUST\\]>",
         "author_wake_prefix": {"<bot login>": "<word a bot author answers to at the start of a comment>"},
         "deploy": {"<owner/repo>": {"dev_release": "auto-sync | <how main reaches dev>",
                                     "revision_cmd": "<prints the commit or ref dev runs>",
                                     "logs": "<where dev logs are read>", "verify": "<verify-* skill or smoke check>",
                                     "ticket": "<optional>", "orca_repo": "<optional Orca repo selector>",
                                     "lookback_days": 2}},
         "dispatch_run": "<Orca run id the release workers report to; needed unless inbox_to is run:<id>>", "worker_agent": "claude",
         "release_approval": "<the owner's dev-deploy approval, quoted with its date>",
         "merge_repos": ["<owner/name>"], "merge_deny_repos": ["<repo whose merge deploys to production>"],
         "merge_methods": ["squash", "merge", "rebase"], "hold_labels": ["hold", "do-not-merge", "wip"],
         "required_bots": ["<review bot that must review each head>"],
         "review_verdict_pattern": "<regex with one (pass|fail) group; default in DEFAULTS>", "ci_wait_minutes": 15}}
```

`inbox_to` takes a Run address, `run:<id>`. A `term_` handle is still accepted but changes when its
terminal restarts, and `orch patrol` raises one Orca no longer lists.

`orgs` and `repos` empty mean every repository; otherwise an `owner` in `orgs` or an `owner/name` in
`repos` is in. `exclude_title_patterns` drops a PR whose title matches any of its regexes
(`re.search`, case-sensitive unless the pattern starts with `(?i)`) in every kind, at collection: it
is never listed, reminded, merged or dispatched. Use it for a batch of PRs that other teams open
across many repositories, where `exclude_repos` would also hide that repository's real review
requests. An invalid pattern fails every command with exit 2. Bot logins match with or without
the `[bot]` suffix. `mentions_seen` overrides where the Slack side is read (default:
`watch-mentions`' `seen.json` under the same state root).

Business hours run from `morning_hour` to `night_hour` on days that are neither in `weekend_days`
(Monday 0 … Sunday 6) nor in `holidays`. `after_hours` and `assign_reviewers` are described in
[review-wait.md](review-wait.md). `profiles` sets those six keys per
repository: the first entry whose `match` (a glob on `owner/name`, or on the owner alone when it has
no `/`) fits a PR's repository overrides the top-level values for it; any other key in a profile is
a config error. `prs.py policy <owner/name>` prints the values that apply to one repository.
Other keys and defaults are in `scripts/prs.py` (`DEFAULTS`). State lives in `~/.local/state/agent-skills/prs/`
(`--state <dir>` or `$AGENT_SKILLS_STATE` moves it): `ledger.json` (key → delivery time, kind, PR node id; no PR text),
`plan.json` (the run in progress, which doubles as the run lock) and `body.txt` (its message).

The notification watcher's own keys, `prs.notify`, are in [notify.md](notify.md).
