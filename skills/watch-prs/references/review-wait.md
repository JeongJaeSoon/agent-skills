# Own PRs waiting on people (`review_wait`)

The user's open PRs, plus PRs by a `self_bots` app that are assigned to the user (a bot app is often
shared across an organisation, so authorship alone is not enough). Out: drafts, out-of-scope
repositories, no update for more than `stale_days`, and PRs a live orchestrate program drives (a PR
number in a `~/.claude/programs/*/ledger.jsonl` written within `stale_days`, whose `program.json`
names the same repo and that is not stopped (no `stop` after its last `resume`); `orch wait`
follows those, and a parked one is held on purpose). A stopped program's PRs come back to reminders.

Each PR gets its states from GitHub alone:

| state | meaning | becomes |
|---|---|---|
| `no_reviewer` | review required, nobody requested or reviewing | `inbox_to` notice |
| `waiting` | a user reviewer requested and silent | `request`, later `remind` |
| `unanswered` | an open thread whose last comment is someone else's, after the last push | `inbox_to` notice |
| `approval_stale` | an approval dismissed, or on an older head, while the decision is `REVIEW_REQUIRED` | `rerequest` |
| `bot_stale` | a review bot's `CHANGES_REQUESTED` with none of its threads still open | `retrigger` |

`decide()` turns states, the send-log and the clock into the actions due now:

- `request`: batched per reviewer; due when that reviewer has `batch_min` (2) waiting, the oldest
  waited `batch_wait_hours` (2), or one carries an `urgent_labels` label. One per PR per reviewer:
  once a row exists, a new push on a still-waiting PR does not request again. With
  `assign_reviewers` on (default off) it carries `github`, a `gh pr edit` that sets that reviewer as
  reviewer and assignee.
- `remind`: one business day after the request (or after its scheduled post), once per request, as a
  reply in the request's thread, one per PR.
- `rerequest`: a voided approval, once per head, in the earlier thread: the same reviewer hears about
  the same PR again only after a new push.
- `retrigger`: the bot's `review_bots` comment, once per head, posted at any hour.
- `remind_author`: see [Authors the user is waiting on](#authors-the-user-is-waiting-on).
- Outside business hours ([config.md](config.md)), `after_hours` decides. Under the default,
  `schedule-all`, a Slack action carries `post_at`, the next business morning, and is scheduled
  instead of sent. A scheduled message cannot be edited, the chat tool may give no way to delete it,
  and the round cannot know a PR will still wait by then (a reviewer may approve overnight), so
  `schedule-certain-only` holds every Slack action and `remind_author` until the first round in
  business hours decides them again. `send-now` sends at any hour. While a scheduled request to a
  reviewer is pending, their next batch waits: two would arrive at once.
- A reviewer not in `reviewers` (or without a `slack` id there) gets no Slack action, only a notice: who to ask is not delegated. The
  channel is the reviewer's, else `repo_channels`, else `channel`.

The send-log (`<state>/sendlog.jsonl`: pr, reviewer, head, action, source, at, post_at, thread,
scheduled id, channel) is what decides actions, and what `brief`'s ⏳ line reads for the last request or reminder; the ledger does not record them (`once: false`), so an action
that failed to send stays due. `source` is `sent`, `scheduled`, `found` (someone already asked in the
channel; `--at` is that message's time), `no_thread` (no thread to reply in, so nothing was posted),
`skipped` (moot by the time of sending: that action at that head is not offered again) or `refused`.
A `refused` row stops every later action for that PR and reviewer. A row appended by hand for a request sent outside the round
(`pr`, `reviewer`, `head` and an ISO `date` or a `slack_ts`, no `action`) counts as a sent `request`.

## Handling the round's actions

Each line of `prs.py actions` is handled and its outcome recorded with
`prs.py log --key <key> --source <source> [--thread <channel>:<ts>] [--scheduled <id>] [--post-at <epoch>] [--at <epoch>]`.
Slack actions follow the user's Slack-posting rules (read that skill first): a request is one
message per reviewer in the channel, unless the channel already has a request for that PR
(`found`); a reminder or re-request is a thread reply only, never broadcast; with no thread known,
search the channel for one, and with none found post nothing (`no_thread`). With `post_at`, schedule
for that time. A `request` with `github` runs that command on its own before the Slack send, then
checks `gh pr view <url> --json reviewRequests,assignees`; a refused or empty result is reported,
not retried, and the Slack request still goes. `retrigger` is `gh pr comment <url> --body <comment>`. `remind_author` with a
`thread` is a reply there mentioning `slack_user`; without one it is `gh pr comment <url>` whose body
starts with `prefix` when set, logged `sent` (it never searches for a thread or logs `no_thread`). An action already moot (approved
or merged meanwhile, no Slack id for the reviewer) is logged `skipped`, not sent. An action refused by a
permission prompt, the classifier or a hook is logged `refused` and reported, never retried.

## A request or reminder sent by hand

A request or reminder you send by hand, outside the round, keeps the round's rules, read for the
PR's repository from `prs.py policy <owner/name>`:

- Bundle per reviewer, by the same `batch_*` and business-hours rules, and list only the PRs the
  people it mentions are asked to review. A PR that is not theirs goes in your report, not in the
  message.
- Request the reviewer on GitHub when you ask (`gh pr edit <n> --add-reviewer <login>`, plus
  `--add-assignee <login>` with `assign_reviewers` on), then confirm with
  `gh pr view <n> --json reviewRequests,assignees` that each is there: `--add-reviewer` can exit 0
  and add no one.
- A request posted as a reply in an existing thread, first or later, is not broadcast to the
  channel.
- When the human tells you to send now, send now, even after hours and whatever `after_hours` says. Leave out each PR whose request
  or last ping went out under three hours ago, and each reviewer the human excludes, and say which
  you left out.
- Schedule only an ask whose PR will surely still be waiting at the send time, even where
  `after_hours` is `schedule-all`: unlike the round, a hand-sent ask is never scheduled on a guess. A scheduled message cannot be edited, the chat tool may give no way to delete it, and an
  empty scheduled-message list does not prove it is gone: one listed nowhere has still posted at
  its time. After hours, an ask whose PR may not be waiting by then is decided again at the next
  business-hours start, and sent then if the PR still waits; with `send-now`, it goes now.
- Log a row for each ask, as above, so the next round does not ask again.

## Authors the user is waiting on

The same round reminds the author of someone else's PR to answer the user's review. A PR is a
candidate when it is open, not a draft, in scope (`exclude_*`, not by a `self_bots` or `ignore_bots`
author), updated within `stale_days`, and the user reviewed it. The ask is the user's latest review
that is `CHANGES_REQUESTED` or matches `must_pattern` in its body or inline comments, with no
approval or dismissal after it. The PR is due when:

- the head is still the commit the ask reviewed. A push since then means the author answered with
  code: the PR goes back to the user as a re-review (`review_request`), never a reminder;
- the author has not commented on the PR or in a review thread since the ask;
- one business day has passed since the ask, and the round runs in business hours (a PR comment
  cannot be scheduled) or `after_hours` is `send-now`.

`remind_author` goes once per PR and head; its send-log row names the author as `reviewer`. Where it
goes:

- a person whose PR has a Slack review request (`watch-mentions`' ledger) and a `slack` id in
  `reviewers`: a reply in that request's thread, mentioning them, never broadcast;
- anyone else, every bot included: a PR comment. A bot that answers only comments starting with a
  wake word gets it from `author_wake_prefix` (the action's `prefix`), and the comment starts with it.
