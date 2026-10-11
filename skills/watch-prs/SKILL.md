---
name: watch-prs
description: Use when the user wants the pull requests that are waiting on them picked up as work context — "리뷰 요청 온 PR 정리해줘", "나한테 리뷰 요청 온 거 있어?", "내가 리뷰해야 할 PR", "PR 감시", "watch my PRs" — and for each round of the watch-prs Orca automation. Lists PRs where the user was asked to review personally (not through a team or CODEOWNERS), ranks them, and sends the coordinator one bundled message only when something is new; a PR the user reviewed comes back once when it gets new commits without a re-request. In a repo that declares a dev path, the user's merged PRs not yet checked on dev start one release worker per repo. Writes nothing to GitHub itself. Not for writing a review (code-flow:review-pr) or for a program's own PRs (orchestrate).
---

# Watch PRs

GitHub's `review-requested:@me` mixes three things: a request to the user by name, a request to a
team they belong to, and a CODEOWNERS auto-assignment. On a busy account the last two drown the
first. This skill keeps only the personal ones, ranks them, and hands them on once per head commit.

It reads; it never writes to GitHub: no review, comment, re-request or label. The release workers it
starts for merged PRs do their own writing.

## Config

`~/.claude/agent-skills.json` → `"prs"`. The key must exist (an empty object is enough); every field
is optional. User-specific values (orgs, bot accounts, the coordinator's address) live here and
never in this repo.

```json
{"prs": {"orgs": [], "repos": [], "exclude_orgs": [], "exclude_repos": [],
         "self_bots": ["<the user's own bot account>"], "ignore_bots": ["<bot whose PRs are never a review>"],
         "inbox_to": "<orca address: run:<id> or a coordinator terminal handle>",
         "max_items": 10, "stale_days": 30, "cron": "*/15 * * * *", "timezone": "<IANA tz>",
         "kinds": ["review_request", "unreleased"],
         "deploy": {"<owner/repo>": {"dev_release": "auto-sync | <how main reaches dev>",
                                     "revision_cmd": "<prints the commit or ref dev runs>",
                                     "logs": "<where dev logs are read>", "verify": "<verify-* skill or smoke check>",
                                     "ticket": "<optional>", "orca_repo": "<optional Orca repo selector>",
                                     "lookback_days": 2}},
         "dispatch_run": "<optional Orca run id the release workers join>", "worker_agent": "claude",
         "release_approval": "<the owner's dev-deploy approval, quoted with its date>"}}
```

`orgs` and `repos` empty mean every repository; otherwise an `owner` in `orgs` or an `owner/name` in
`repos` is in. Bot logins match with or without the `[bot]` suffix. Other keys and defaults are in
`scripts/prs.py` (`DEFAULTS`). State lives in `~/.local/state/agent-skills/prs/`
(`--state <dir>` or `$AGENT_SKILLS_STATE` moves it): `ledger.json` (key → delivery time, kind, PR node id; no PR text),
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

Every item has a `kind`. `review_request` and `unreleased` are collected; `review_wait`,
`ready_to_merge` and `ownergate` are reserved in `KINDS`, and a later flow adds a collector to
`COLLECTORS` without changing the CLI. Listing an uncollected kind in `kinds` is an error.

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

1. If the output is empty, stop: send nothing and do not commit.
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
4. `python3 "$P" commit` records what `plan.json` held and removes it. Commit only after the send
   succeeded and each step-3 command either started a worker or was reported: an uncommitted run is retried once its `plan.json` is older than `lock_minutes`, a
   committed one is never sent again. To retry a failed send sooner, send `<state>/body.txt` again
   and then commit. `commit --kinds <k1,k2>` records only those kinds and leaves the rest for the
   next round; the scheduled round passes the kinds its prompt has steps for.

`python3 "$P" plan` without `--body` writes nothing and takes no lock. It prints the plan as JSON
for checking what the filter did: `new` (ranked items), `known` (keys already sent) and
`excluded` (repo#N and why).

## Scheduled run

```bash
python3 "$P" schedule            # prints the `orca automations create` command
python3 "$P" schedule --write    # creates it
```

Run `schedule` from the checkout that loads the skills, never a card's worktree: the precheck keeps
that path. The prompt's steps come from `kinds`: each kind's entry in `STEPS` (next to `COLLECTORS`
in `prs.py`) adds what the round does after the message, and `schedule` refuses a kind without one.
The round passes `--kinds` to `plan --body` and `commit`, so an item of a kind added to the config
after the automation was made is named in the message and stays unrecorded until the prompt is
regenerated. Every command starts with `python3`: a scheduled session can refuse a leading `env` or
`VAR=` assignment as a command of its own, so a non-default state goes in as `--state`.

The precheck (`prs.py precheck`) uses no model. It reads GitHub and exits:

| code | meaning |
|---|---|
| 0 | new items; the run goes ahead |
| 1 | nothing new, or a run still holds `plan.json` (younger than `lock_minutes`) |
| 2 | could not check: no `prs` key in the config, or `gh` failed |

The default trigger is every 15 minutes, for a proof window. The steady state is weekday business
hours, e.g. `*/15 9-19 * * 1-5` with `timezone` set; change it with
`orca automations edit <id> --trigger "<cron>"`. `orca automations run` skips the precheck, so it
proves nothing about it: test the precheck by running it directly with `--state` pointing at a
scratch folder, and judge the automation by a scheduled run in
`orca automations runs --id <id>`. Devices and their trade-offs:
[docs/automation.md](../../docs/automation.md).

Tests: `python3 scripts/test_prs.py`.
