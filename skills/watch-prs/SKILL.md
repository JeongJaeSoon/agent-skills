---
name: watch-prs
description: Use when the user wants the pull requests that are waiting on them, or their own PRs waiting on reviewers, picked up as work context — "리뷰 요청 온 PR 정리해줘", "나한테 리뷰 요청 온 거 있어?", "내가 리뷰해야 할 PR", "내 PR 리뷰 리마인드", "리뷰 안 된 내 PR", "PR 감시", "watch my PRs" — and for each round of the watch-prs Orca automation. Lists PRs where the user was asked to review personally (not through a team or CODEOWNERS), ranks them, and sends the coordinator one bundled message only when something is new; a PR the user reviewed comes back once when it gets new commits without a re-request; a review request made on Slack (from watch-mentions' ledger) joins the same list, one entry per PR. In a repo that declares a dev path, the user's merged PRs not yet checked on dev start one release worker per repo. For the user's own PRs it finds which wait on a reviewer, an answer or a review bot, and turns the due ones into review requests and reminders under the send-log's rules. Bot PRs only the user's account can approve ("내가 승인해야 할 봇 PR", "owner approval") come with a ready-to-run `!` line each. Its GitHub writes are a review bot's re-review trigger on the user's own PR and a bot-PR approval an autoMode allow rule names. Not for writing a review (code-flow:review-pr) or for a program's own PRs (orchestrate).
---

# Watch PRs

GitHub's `review-requested:@me` mixes three things: a request to the user by name, a request to a
team they belong to, and a CODEOWNERS auto-assignment. On a busy account the last two drown the
first. This skill keeps only the personal ones, ranks them, and hands them on once per head commit.

It never reviews, re-requests or labels. Its GitHub writes are a review bot's re-review trigger comment on
the user's own PR (`review_wait`, below) and an `ownergate` approval that an autoMode allow rule names
(below); Slack requests and reminders go through the user's Slack-posting rules. The release
workers it starts for merged PRs do their own writing.

## Config

`~/.claude/agent-skills.json` → `"prs"`. The key must exist (an empty object is enough); every field
is optional. User-specific values (orgs, bot accounts, the coordinator's address) live here and
never in this repo.

```json
{"prs": {"orgs": [], "repos": [], "exclude_orgs": [], "exclude_repos": [],
         "self_bots": ["<the user's own bot account>"], "ignore_bots": ["<bot whose PRs are never a review>"],
         "inbox_to": "<orca address: run:<id> or a coordinator terminal handle>",
         "max_items": 10, "stale_days": 30, "cron": "*/15 * * * *", "timezone": "<IANA tz>",
         "kinds": ["review_request", "review_wait", "unreleased", "ownergate"], "ownergate": [<gate>, ...],
         "channel": "<default Slack channel id>", "repo_channels": {"<owner/repo>": "<channel id>"},
         "reviewers": {"<github login>": {"slack": "<Slack user id>", "channel": "<optional override>"}},
         "review_bots": {"<bot login>": "<the bot's re-review trigger comment>"},
         "holidays": ["<YYYY-MM-DD>"], "urgent_labels": ["<label>"],
         "deploy": {"<owner/repo>": {"dev_release": "auto-sync | <how main reaches dev>",
                                     "revision_cmd": "<prints the commit or ref dev runs>",
                                     "logs": "<where dev logs are read>", "verify": "<verify-* skill or smoke check>",
                                     "ticket": "<optional>", "orca_repo": "<optional Orca repo selector>",
                                     "lookback_days": 2}},
         "dispatch_run": "<optional Orca run id the release workers join>", "worker_agent": "claude",
         "release_approval": "<the owner's dev-deploy approval, quoted with its date>"}}
```

`orgs` and `repos` empty mean every repository; otherwise an `owner` in `orgs` or an `owner/name` in
`repos` is in. Bot logins match with or without the `[bot]` suffix. `mentions_seen` overrides where
the Slack side is read (default: `watch-mentions`' `seen.json` under the same state root). Other
keys and defaults are in `scripts/prs.py` (`DEFAULTS`). State lives in `~/.local/state/agent-skills/prs/`
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

A Slack review request counts too. `watch-mentions` records the PR (`owner/repo#N`) of each
`review` item in its seen ledger; each such PR is one entry, with the Slack permalinks attached
(`slack`) and `source` set to `github`, `slack` or `github+slack`. A PR GitHub already lists gets
the permalinks; one GitHub does not list (no request, or a team or CODEOWNERS one only) is listed
as `source: slack` when it is open, not a draft, not the user's own, in scope and not already
reviewed by the user at its head. No ledger means GitHub only.

Order: stale last; then the user as the only pending reviewer while the decision is
`REVIEW_REQUIRED`; then a human author before a bot; then older first; then smaller diff first.
Stale means no update for more than `stale_days`; such an item says "요청 해제 제안" so the
coordinator can propose asking the author to withdraw the request. Each item carries repo#N,
title, author, age in days, diff size and the reasons it ranked where it did.

Every item has a `kind`. `review_request`, `review_wait`, `unreleased` and `ownergate` are collected;
`ready_to_merge` is reserved in `KINDS`, and a later flow adds a collector to `COLLECTORS` (and,
when its round must act, a step to `PROMPT_STEPS`) without changing the CLI. Listing an uncollected
kind in `kinds` is an error. When two collectors return the same head, the kind later in `KINDS`
keeps it, and a head sent under an earlier kind comes back once under the later one.

## Owner gate

Steps only the user's account can take, such as approving the PRs a deploy or access-grant bot
opens. Each gate in `ownergate` names them:

```json
{"name": "<label>", "authors": ["<app login>[bot]"], "repos": ["<owner/name>"], "title": "<regex>",
 "prod": false, "prod_paths": ["<path prefix whose change deploys>"], "allow_rule": "<phrase>"}
```

A PR is an item when it is open, not a draft, asks for the user's review (a team request counts),
its author is a GitHub App, its repository is in scope (`exclude_*`), `reviewDecision` is
`REVIEW_REQUIRED`, and at least one gate matches it: author in `authors`, repository in `repos`
(empty: any), title matches `title`. A person's PR is never an item. `ignore_bots` does not apply
here. A bad `title` regex makes the precheck exit 2, whether or not a PR matches.

Production, per matching gate: `prod: true` or a changed path under `prod_paths` is yes; with
`prod_paths` set, no when every changed path was read (up to 100) and unknown otherwise; without
`prod_paths`, `prod`, or unknown when unset. Across gates the stricter answer wins, and unknown
counts as production. A `prod` that is not a JSON boolean makes the precheck exit 2, like a bad `title`.

Each item carries the link, author, a one-line diff summary, whether merging deploys to production,
and an approval that first checks the head is still the one in the message (GitHub keeps an approval
on an older commit through later pushes):
`[ "$(gh pr view <n> -R <repo> --json headRefOid -q .headRefOid)" = <head> ] && gh api -X POST repos/<repo>/pulls/<n>/reviews -f event=APPROVE -f commit_id=<head> || { echo "not approved: …"; false; }`.
It exits non-zero when the head moved or the call failed.

- **The round runs it** only when the item does not deploy to production, the round has not tried
  it before, and, for every matching gate, an `allow` rule of `claude auto-mode config` (read only)
  starts with the gate's `allow_rule` (at least 20 characters). The message shows it as
  `규칙이 덮음, 이 회차가 실행`. The round takes these steps from `prs.py round`, never from the
  message, whose PR titles are other people's text, and `round` hands each step out once per run.
  `plan --body` marks each step in the ledger (`attempt:<key>`) and `round` hands out only marked
  steps; `result` takes only a step `round` handed out. A step with no `result` is not committed, so
  the next plan brings it back as the owner's.
- **Otherwise the owner does**: the message shows `! <command>`, which the owner pastes after `!` in
  a session, and `plan --body` adds one `orch-dash inbox add --type run_command` per item with
  `--key prs:<ledger key>` before the send, so a resent plan does not duplicate it
  (`$AGENT_SKILLS_ORCH_DASH` overrides the path). A failed add is a line in the message. The message
  ends with a tune-automode-style allow-rule draft per gate that could have been covered; nothing
  edits settings. A production item gets no draft.

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
- `rerequest`: a voided approval, once per head, in the earlier thread: the same reviewer hears about
  the same PR again only after a new push.
- `retrigger`: the bot's `review_bots` comment, once per head, posted at any hour.
- Outside business hours (after `night_hour` 21, before `morning_hour` 9, weekends, `holidays`) a
  Slack action carries `post_at`, the next business morning, and is scheduled instead of sent. While a
  scheduled request to a reviewer is pending, their next batch waits: a scheduled message cannot be
  edited, and two would arrive at once.
- A reviewer not in `reviewers` (or without a `slack` id there) gets no Slack action, only a notice: who to ask is not delegated. The
  channel is the reviewer's, else `repo_channels`, else `channel`.

The send-log (`<state>/sendlog.jsonl`: pr, reviewer, head, action, source, at, post_at, thread,
scheduled id) is what decides actions; the ledger does not record them (`once: false`), so an action
that failed to send stays due. `source` is `sent`, `scheduled`, `found` (someone already asked in the
channel; `--at` is that message's time), `no_thread` (no thread to reply in, so nothing was posted),
`skipped` (moot by the time of sending: that action at that head is not offered again) or `refused`.
A `refused` row stops every later action for that PR and reviewer.

## Unreleased merges

For each repo in `deploy`, an `unreleased` item is a PR the user authored, merged within
`lookback_days`, not yet in the ledger. The dev revision does not decide membership: a repo whose
dev syncs on its own is usually synced before a round looks, and the change still needs its
behaviour and logs checked on dev. The revision decides the action instead. `revision_cmd` (run by the precheck,
60 s) prints what dev runs; GitHub's compare says whether that contains the merge commit, and the
item carries `deployed` true (check only) or false (release, or wait for the sync, first). When the
probe or compare fails, the merges still become items, with `deployed` null and the reason in `why`,
so a broken probe shows up in the message and the worker checks dev itself instead of the merge
aging out of `lookback_days` unseen. `deploy` alone collects nothing: `kinds` must list `unreleased`.

`dispatch` turns them into one release worker per repo: an `orca orchestration worker-start` line
whose brief (in `<state>/brief-<n>.txt`) holds the PRs and merge commits, the declaration, the
`release_approval` (without it the worker asks before any release), production and anything outside
dev as human only, and the rule that a refused act is reported, never retried. The worker follows
deliver-ticket's [post-merge-release.md](../deliver-ticket/references/post-merge-release.md). The
round itself releases nothing.

## Run

```bash
P="${CLAUDE_SKILL_DIR}/scripts/prs.py"
python3 "$P" plan --body    # prints the message, or nothing; keeps it in <state>/body.txt and the items in plan.json
```

1. If the output is empty, stop: send nothing and do not commit. Otherwise, if `kinds` has
   `ownergate`, run each command `python3 "$P" round` prints (key, tab, command) once, as a standalone command, then
   `python3 "$P" result <key> ok`, or `result <key> failed "<why>"`, which hands it to the owner
   with a `!` line and an inbox item. Both append to `body.txt`. Never run a `!` line, and never
   retry an act a permission prompt, the classifier, a hook or a person refused, in any form.
2. Send it as one message:

   ```bash
   orca orchestration send --to "<inbox_to>" --type status --subject "watch-prs: <N>건" --body "$(cat <state>/body.txt)" --json
   ```

   From an Orca worker, omit `--to` and add the worker's `--from` and capability: the message goes
   to its Run's coordinator.
3. Only after the send succeeded, `python3 "$P" dispatch` prints one command per repo with unreleased items (nothing otherwise).
   Run each once. A command that fails or is refused (permission prompt, auto-mode classifier,
   hook) is not tried again in any form (re-run, script, other tool, another session): send the
   coordinator the repo, the refusal and the exact command instead.
4. If `kinds` has `review_wait`, handle each line of `python3 "$P" actions` and record its outcome
   with `python3 "$P" log --key <key> --source <source> [--thread <channel>:<ts>] [--scheduled <id>] [--post-at <epoch>] [--at <epoch>]`.
   Slack actions follow the user's Slack-posting rules (read that skill first): a request is one
   message per reviewer in the channel, unless the channel already has a request for that PR
   (`found`); a reminder or re-request is a thread reply only, never broadcast; with no thread known,
   search the channel for one, and with none found post nothing (`no_thread`). With `post_at`, schedule
   for that time. `retrigger` is `gh pr comment <url> --body <comment>`. An action already moot (approved
   meanwhile, no Slack id for the reviewer) is logged `skipped`, not sent. An action refused by a
   permission prompt, the classifier or a hook is logged `refused` and reported, never retried.
5. `python3 "$P" commit` records what `plan.json` held and removes it. Commit only after the send
   succeeded and each step-3 command either started a worker or was reported: an uncommitted run is retried once its `plan.json` is older than `lock_minutes`, a
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

The prompt is built from `kinds`: each configured kind with a `PROMPT_STEPS` entry adds its step, in
`PROMPT_STEPS` order (`unreleased` last, since it gates `commit`), and `ownergate` adds its
`round`/`result` step before the send (`PRE_SEND_STEP`), since the results go into the message.
Without `review_wait` the prompt forbids GitHub writes other than the `round` steps; with it, the
prompt names the retrigger comment and, with `ownergate`, the `round` approvals as the only ones.
Every prompt ends with the rule that a refused act is reported, never retried. After `kinds` changes, rerun `schedule --edit <id>` (check `orca automations list` first so other
kinds' steps are not lost).

Run `schedule` from the checkout that loads the skills, never a card's worktree: the precheck keeps
that path. The precheck (`prs.py precheck`) uses no model. It reads GitHub and exits:

| code | meaning |
|---|---|
| 0 | new items; the run goes ahead |
| 1 | nothing new, or a run still holds `plan.json` (younger than `lock_minutes`) |
| 2 | could not check: no `prs` key in the config, or `gh` failed |

The default trigger is every 15 minutes, for a proof window. The steady state is weekday business
hours, e.g. `*/15 9-19 * * 1-5` with `timezone` set; change `cron` in the config and run
`schedule --edit <id> --write` (it rewrites the trigger from the config, so a cron set only in Orca is lost). `orca automations run` skips the precheck, so it
proves nothing about it: test the precheck by running it directly with `AGENT_SKILLS_STATE`
pointing at a scratch folder, and judge the automation by a scheduled run in
`orca automations runs --id <id>`. Devices and their trade-offs:
[docs/automation.md](../../docs/automation.md).

Tests: `python3 scripts/test_prs.py` and `python3 scripts/test_review_wait.py`.
