# notify.py: which notifications fire

Each item prints once:

- `review_requested`, `assign` and `state_change` are keyed per thread (`<id>:<reason>`): every
  re-request, reassignment or state change bumps the thread's `updated_at`, so a key on it would fire
  the same notification again each time.
- Other reasons are keyed `<id>@<updated_at>`, so a new comment on the same thread fires again.
- With `personal_review_only` (default on), `review_requested` fires only when the user's login is in
  `GET <pull>/requested_reviewers` → `users`; a request that reaches them through a team does not. A
  team-only request is recorded per `updated_at`, so it is looked up again only after a re-request,
  and fires once if the user is later asked by name. A lookup that fails skips only that item until
  the next poll; a 404 records it as team-only, silently.
- With `human_activity_only` (default on), `author` and `comment` fire only when the latest activity
  comes from a person other than the user, so their own pushes and comments, CI and review bots stay
  silent. The actor is the author of `subject.latest_comment_url`, or of the last entry on the last
  page of `GET <pull>/reviews` when that URL is the PR itself (a review). No URL (a push) fires
  nothing, nor does an actor who is the user, has `type: Bot` or a login ending in `[bot]`. A failed
  lookup is retried next poll; a 404 (a deleted comment) records the item silently.
  `mention` follows the same rule when `latest_comment_url` is a comment, so a bot that repeats the
  user's handle on each event stays silent; a mention in a PR or issue body (no comment URL, or the
  URL is the thread itself) always fires. The check sees who acted last, not who mentioned, so a bot
  comment landing after a person's mention within one poll silences that mention.
