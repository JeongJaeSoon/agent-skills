---
name: watch-prs
description: Use when the user wants the pull requests that are waiting on them picked up as work context — "리뷰 요청 온 PR 정리해줘", "나한테 리뷰 요청 온 거 있어?", "내가 리뷰해야 할 PR", "PR 감시", "watch my PRs" — and for each round of the watch-prs Orca automation. Lists PRs where the user was asked to review personally (not through a team or CODEOWNERS), ranks them, and sends the coordinator one bundled message only when something is new; a PR the user reviewed comes back once when it gets new commits without a re-request. Reads GitHub only, except that a round configured with `ready_to_merge` merges the user's own PRs that are approved or need no review, CLEAN and green — "머지 가능한 내 PR 머지해줘". Not for writing a review (code-flow:review-pr) or for a program's own PRs (orchestrate).
---

# Watch PRs

GitHub's `review-requested:@me` mixes three things: a request to the user by name, a request to a
team they belong to, and a CODEOWNERS auto-assignment. On a busy account the last two drown the
first. This skill keeps only the personal ones, ranks them, and hands them on once per head commit.

It reads; it never writes to GitHub (no review, comment, re-request or label), with one exception: when
`kinds` includes `ready_to_merge`, the round merges the owner's own PRs that pass every gate in
[Ready to merge](#ready-to-merge).

## Config

`~/.claude/agent-skills.json` → `"prs"`. The key must exist (an empty object is enough); every field
is optional. User-specific values (orgs, bot accounts, the coordinator's address) live here and
never in this repo.

```json
{"prs": {"orgs": [], "repos": [], "exclude_orgs": [], "exclude_repos": [],
         "self_bots": ["<the user's own bot account>"], "ignore_bots": ["<bot whose PRs are never a review>"],
         "inbox_to": "<orca address: run:<id> or a coordinator terminal handle>",
         "max_items": 10, "stale_days": 30, "cron": "*/15 * * * *", "timezone": "<IANA tz>",
         "kinds": ["review_request", "ready_to_merge"],
         "merge_repos": ["<owner/name>"], "merge_deny_repos": ["<repo whose merge deploys to production>"],
         "merge_methods": ["squash", "merge", "rebase"], "hold_labels": ["hold", "do-not-merge", "wip"],
         "required_bots": ["<review bot that must review each head>"], "review_bots": ["<bot whose pass verdict counts>"],
         "review_pass_pattern": "<regex, default ^\\W*(결론|verdict)\\W*pass\\b>", "ci_wait_minutes": 15,
         "programs_home": "~/.claude/programs", "program_days": 7}}
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

Every item has a `kind`. `review_request` and `ready_to_merge` are collected; `review_wait`,
`unreleased` and `ownergate` are reserved in `KINDS`, and a later flow adds a collector to
`COLLECTORS` without changing the CLI. Listing an uncollected kind in `kinds` is an error.

## Ready to merge

The owner's open PRs (`author:@me`) in `merge_repos` are checked against these gates in order; the
first that holds a PR back is its reason in `excluded`. Unlike `repos`, an empty `merge_repos` means
nothing is merged.

1. its repository is not in `merge_deny_repos` (repos where a merge deploys to production) and is in
   `merge_repos`;
2. not a draft, and no label from `hold_labels` (case-insensitive);
3. no one else is driving it: its head branch is not checked out in a live (unarchived) Orca
   worktree — a worker's, or a lead's or other card's — and its number is not in the ledger of an
   orchestrate program under `programs_home` (default `$PROGRAMS_HOME`) active within
   `program_days`. If Orca cannot be read, nothing is ready. A PR you want merged by the round lives
   on a branch no worktree has checked out;
4. its base is the default branch: a stacked child waits for its parent;
5. no reviewer's latest non-comment review is CHANGES_REQUESTED or DISMISSED (an approval a retarget
   or push dismissed belongs to review_wait);
6. `mergeStateStatus` is CLEAN and `reviewDecision` is APPROVED or empty (no review required);
7. where no review is required, the head still passed a review: an approval on that commit from
   someone other than the user, or a comment or review body that names the full head sha and matches
   `review_pass_pattern` (default: a line `결론: pass` or `Verdict: pass`, an independent review's
   sticky result) from the user or one of `review_bots`. A self-reply, a failing verdict, or any other
   bot printing the sha does not count. Without one the PR belongs to review_wait;
8. the head's check rollup is SUCCESS (no checks is not green), and no review thread is unresolved;
9. each of `required_bots` left an APPROVED or COMMENTED review on this head. A bot that was
   rate-limited or skipped left a comment, not a review;
10. the repository allows one of `merge_methods`, read from the repository in this round; the first
    allowed one is used.

A ready item carries `gh pr merge <N> -R <owner/name> --<method> --match-head-commit <head>`. The
round never adds `--admin` or `--auto` and never approves. The ledger records the head whether the
merge went through or was refused, so a refused or failed merge is reported once and not retried at
that head.

## Run

```bash
P="${CLAUDE_SKILL_DIR}/scripts/prs.py"
python3 "$P" plan --body    # prints the message, or nothing; keeps it in <state>/body.txt and the items in plan.json
```

1. If the output is empty, stop: send nothing and do not commit.
2. If `kinds` includes `ready_to_merge`: `python3 "$P" merges` prints, for each ready item, a
   comment line (PR, gates passed, method) and its `gh pr merge` command. Write the comment line in
   the chat, then run the command on its own, once. A merge the auto-mode classifier or a hook
   refuses is reported in the message, not retried in any form. Then `python3 "$P" verify` reads each
   PR back (MERGED or not), waits up to `ci_wait_minutes` for the default branch CI run on the merge
   commit, and rewrites `<state>/body.txt` with the outcome; a red run's URL is in the line. A merge
   commit with no checks after two minutes is reported as a branch without CI. If `verify` fails,
   send nothing and do not commit.
3. Send it as one message:

   ```bash
   orca orchestration send --to "<inbox_to>" --type status --subject "watch-prs: <N>건" --body "$(cat <state>/body.txt)" --json
   ```

   From an Orca worker, omit `--to` and add the worker's `--from` and capability: the message goes
   to its Run's coordinator.
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
```

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

Tests: `python3 scripts/test_prs.py`.
