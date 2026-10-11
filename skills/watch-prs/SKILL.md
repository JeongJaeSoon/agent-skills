---
name: watch-prs
description: Use when the user wants the pull requests that are waiting on them, or their own PRs waiting on reviewers, picked up as work context — "리뷰 요청 온 PR 정리해줘", "나한테 리뷰 요청 온 거 있어?", "내가 리뷰해야 할 PR", "내 PR 리뷰 리마인드", "리뷰 안 된 내 PR", "PR 감시", "watch my PRs" — and for each round of the watch-prs Orca automation. Lists PRs where the user was asked to review personally (not through a team or CODEOWNERS), ranks them, and sends the coordinator one bundled message only when something is new; a PR the user reviewed comes back once when it gets new commits without a re-request. For the user's own PRs it finds which wait on a reviewer, an answer or a review bot, and turns the due ones into review requests and reminders under the send-log's rules. Not for writing a review (code-flow:review-pr) or for a program's own PRs (orchestrate).
---

# Watch PRs

GitHub's `review-requested:@me` mixes three things: a request to the user by name, a request to a
team they belong to, and a CODEOWNERS auto-assignment. On a busy account the last two drown the
first. This skill keeps only the personal ones, ranks them, and hands them on once per head commit.

It never reviews, re-requests or labels. Its only GitHub write is a review bot's re-review trigger comment on
the user's own PR (`review_wait`, below); Slack requests and reminders go through the user's Slack-posting rules.

## Config

`~/.claude/agent-skills.json` → `"prs"`. The key must exist (an empty object is enough); every field
is optional. User-specific values (orgs, bot accounts, the coordinator's address) live here and
never in this repo.

```json
{"prs": {"orgs": [], "repos": [], "exclude_orgs": [], "exclude_repos": [],
         "self_bots": ["<the user's own bot account>"], "ignore_bots": ["<bot whose PRs are never a review>"],
         "inbox_to": "<orca address: run:<id> or a coordinator terminal handle>",
         "max_items": 10, "stale_days": 30, "cron": "*/15 * * * *", "timezone": "<IANA tz>",
         "kinds": ["review_request", "review_wait"],
         "channel": "<default Slack channel id>", "repo_channels": {"<owner/repo>": "<channel id>"},
         "reviewers": {"<github login>": {"slack": "<Slack user id>", "channel": "<optional override>"}},
         "review_bots": {"<bot login>": "<the bot's re-review trigger comment>"},
         "holidays": ["<YYYY-MM-DD>"], "urgent_labels": ["<label>"]}}
```

`orgs` and `repos` empty mean every repository; otherwise an `owner` in `orgs` or an `owner/name` in
`repos` is in. Bot logins match with or without the `[bot]` suffix. Other keys and defaults are in
`scripts/prs.py` (`DEFAULTS`). State lives in `~/.local/state/agent-skills/prs/`
(`$AGENT_SKILLS_STATE` moves it): `ledger.json` (key → delivery time, kind, PR node id; no PR text),
`plan.json` (the run in progress, which doubles as the run lock) and `body.txt` (its message).

## What counts

A PR is an item when all hold:

- one of its pending review requests is the user as a User reviewer with `asCodeOwner` false. The
  search qualifier is only the starting set; this check decides;
- it is not a draft;
- its author is not in `self_bots` or `ignore_bots`, and its repository is in scope.

A PR in the ledger that left the search comes back once when the user's latest review is on an
older commit than the head: new commits after a review, with no re-request. The ledger key is
`owner/repo#N@headRefOid`, so each head is sent at most once.

Order: stale last; then the user as the only pending reviewer while the decision is
`REVIEW_REQUIRED`; then a human author before a bot; then older first; then smaller diff first.
Stale means no update for more than `stale_days`; such an item says "요청 해제 제안" so the
coordinator can propose asking the author to withdraw the request. Each item carries repo#N,
title, author, age in days, diff size and the reasons it ranked where it did.

Every item has a `kind`. `review_request` and `review_wait` are collected; `ready_to_merge`,
`unreleased` and `ownergate` are reserved in `KINDS`, and a later flow adds a collector to
`COLLECTORS` (and, when its round must act, a step to `PROMPT_STEPS`) without changing the CLI.
Listing an uncollected kind in `kinds` is an error.

## Own PRs waiting on people (`review_wait`)

The user's open PRs, plus PRs by a `self_bots` app that are assigned to the user (a bot app is often
shared across an organisation, so authorship alone is not enough). Out: drafts, out-of-scope
repositories, no update for more than `stale_days`, and PRs a live orchestrate program drives (a PR
number in a `~/.claude/programs/*/ledger.jsonl` written within `stale_days`, whose `program.json`
names the same repo; `orch wait` follows those, and a parked one is held on purpose).

Each PR gets its states from GitHub alone:

| state | meaning | becomes |
|---|---|---|
| `no_reviewer` | review required, nobody requested or reviewing | coordinator notice |
| `waiting` | a user reviewer requested and silent | `request`, later `remind` |
| `unanswered` | an open thread whose last comment is someone else's, after the last push | coordinator notice |
| `approval_stale` | an approval dismissed, or on an older head, while the decision is `REVIEW_REQUIRED` | `rerequest` |
| `bot_stale` | a review bot's `CHANGES_REQUESTED` with none of its threads still open | `retrigger` |

`decide()` turns states, the send-log and the clock into the actions due now:

- `request`: batched per reviewer; due when that reviewer has `batch_min` (2) waiting, the oldest
  waited `batch_wait_hours` (2), or one carries an `urgent_labels` label. One per PR per reviewer:
  once a row exists, a new push on a still-waiting PR does not request again.
- `remind`: one business day after the request (or after its scheduled post), once per request, as a
  reply in the request's thread, one per PR.
- `rerequest`: a voided approval, once per head, in the earlier thread. So within a day the same
  reviewer hears about the same PR again only after a new push.
- `retrigger`: the bot's `review_bots` comment, once per head, posted at any hour.
- Outside business hours (after `night_hour` 21, before `morning_hour` 9, weekends, `holidays`) a
  Slack action carries `post_at`, the next business morning, and is scheduled instead of sent. While a
  scheduled request to a reviewer is pending, their next batch waits: a scheduled message cannot be
  edited, and two would arrive at once.
- A reviewer not in `reviewers` gets no Slack action, only a notice: who to ask is not delegated. The
  channel is the reviewer's, else `repo_channels`, else `channel`.

The send-log (`<state>/sendlog.jsonl`: pr, reviewer, head, action, source, at, post_at, thread,
scheduled id) is what decides actions; the ledger does not record them (`once: false`), so an action
that failed to send stays due. `source` is `sent`, `scheduled`, `found` (someone already asked in the
channel; `--at` is that message's time), `no_thread` (no thread to reply in, so nothing was posted),
`skipped` (moot by the time of sending: that action at that head is not offered again) or `refused`.
A `refused` row stops every later action for that PR and reviewer.

## Run

```bash
P="${CLAUDE_SKILL_DIR}/scripts/prs.py"
python3 "$P" plan --body    # prints the message, or nothing; keeps it in <state>/body.txt and the items in plan.json
```

1. If the output is empty, stop: send nothing and do not commit.
2. Send it as one message:

   ```bash
   orca orchestration send --to "<inbox_to>" --type status --subject "watch-prs: <N>건" --body "$(cat <state>/body.txt)" --json
   ```

   From an Orca worker, omit `--to` and add the worker's `--from` and capability: the message goes
   to its Run's coordinator.
3. If `kinds` has `review_wait`, handle each line of `python3 "$P" actions` and record its outcome
   with `python3 "$P" log --key <key> --source <source> [--thread <channel>:<ts>] [--scheduled <id>] [--post-at <epoch>] [--at <epoch>]`.
   Slack actions follow the user's Slack-posting rules (read that skill first): a request is one
   message per reviewer in the channel, unless the channel already has a request for that PR
   (`found`); a reminder or re-request is a thread reply only, never broadcast; with no thread known,
   search the channel for one, and with none found post nothing (`no_thread`). With `post_at`, schedule
   for that time. `retrigger` is `gh pr comment <url> --body <comment>`. An action already moot (approved
   meanwhile, no Slack id for the reviewer) is logged `skipped`, not sent. An action refused by a
   permission prompt, the classifier or a hook is logged `refused` and reported, never retried.
4. `python3 "$P" commit` records what `plan.json` held and removes it. Commit only after the send
   succeeded: an uncommitted run is retried once its `plan.json` is older than `lock_minutes`, a
   committed one is never sent again. To retry a failed send sooner, send `<state>/body.txt` again
   and then commit.

`python3 "$P" plan` without `--body` writes nothing and takes no lock. It prints the plan as JSON
for checking what the filter did: `new` (ranked items), `known` (keys already sent) and
`excluded` (repo#N and why).

## Scheduled run

```bash
python3 "$P" schedule            # prints the `orca automations create` command
python3 "$P" schedule --write    # creates it
python3 "$P" schedule --edit <id> --write   # rewrites an existing automation's trigger, prompt and precheck, keeping its id
```

The prompt is built from `kinds`: each configured kind with a `PROMPT_STEPS` entry adds its step, so
after `kinds` changes, rerun `schedule --edit <id>` (check `orca automations list` first so other
kinds' steps are not lost).

Run `schedule` from the checkout that loads the skills, never a card's worktree: the precheck keeps
that path. The precheck (`prs.py precheck`) uses no model. It reads GitHub and exits:

| code | meaning |
|---|---|
| 0 | new items; the run goes ahead |
| 1 | nothing new, or a run still holds `plan.json` (younger than `lock_minutes`) |
| 2 | could not check: no `prs` key in the config, or `gh` failed |

The default trigger is every 15 minutes, for a proof window. The steady state is weekday business
hours, e.g. `*/15 9-19 * * 1-5` with `timezone` set; change it with
`orca automations edit <id> --trigger "<cron>"`. `orca automations run` skips the precheck, so it
proves nothing about it: test the precheck by running it directly with `AGENT_SKILLS_STATE`
pointing at a scratch folder, and judge the automation by a scheduled run in
`orca automations runs --id <id>`. Devices and their trade-offs:
[docs/automation.md](../../docs/automation.md).

Tests: `python3 scripts/test_prs.py` and `python3 scripts/test_review_wait.py`.
