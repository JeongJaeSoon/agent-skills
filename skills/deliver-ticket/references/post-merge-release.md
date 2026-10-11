# Post-merge release to dev

A merged PR is not done in a repo that ships: the change has to run in the dev environment, behave
as intended there, and leave clean logs. This file is the procedure, for a session finishing its
own ticket and for a release worker that `watch-prs` started for merges it found (kind
`unreleased`).

Production is never part of it. A production step runs only on the owner's instruction typed into
the session that holds it, and this procedure ends at dev.

## The declaration

Each repo that ships declares its dev path in the `prs` config (`~/.claude/agent-skills.json` →
`prs.deploy["<owner/repo>"]`); the values are private and never written into a skill.

| Field | Meaning |
|---|---|
| `dev_release` | How main reaches dev: `auto-sync`, or the workflow/command and the rules around it (batching, approval boundary) |
| `revision_cmd` | A shell command that prints the commit or ref dev runs now. The model-free precheck runs it, so it must finish within 60 s without a login prompt |
| `logs` | Where dev logs are read and how to bound them to a time window |
| `verify` | The repo's `verify-*` skill, or the smoke check that exercises the changed paths |
| `ticket` | Optional: how the tracker is updated for this repo |
| `orca_repo` | Optional: the Orca repo selector for the release worker (default `name:<repo name>`) |
| `lookback_days` | How far back merges count (default 2) |

A worker started by `watch-prs` gets these values in its brief, with the PR list, each merge
commit, and whether dev already contains it.

## Steps

1. **Is a release due.** Run `revision_cmd`. A merge is in dev when GitHub's
   `compare/<merge commit>...<deployed>` says `ahead` or `identical`. If every merge is in, skip
   to step 3: the release already happened, the check has not.
2. **Release to dev.** Follow `dev_release` exactly. For `auto-sync`, there is nothing to trigger:
   wait. Otherwise trigger the declared path once for all the merges in the batch — one release
   per batch, not one per PR. The trigger needs an approval in the brief or from the owner; with
   none, ask and wait. If the trigger is refused by a permission prompt, the auto-mode classifier,
   a hook or a human, do not try it again in any form (re-run, script, other tool, renamed target,
   another session); report what it was for, the refusal and the exact command.
3. **Wait until dev runs the merge.** The release finishing is not the environment running it: a
   GitOps app applies desired state later. Poll `revision_cmd` until it contains every merge, then
   read the environment's own live state (e.g. the GitOps app's last sync time and revision) and
   note the time the merge went live. That time opens the log window.
4. **Check behaviour.** Drive `verify`: the repo's `verify-*` skill, or the smoke check, against
   dev, on the paths each PR changed. A health endpoint answering is not a behaviour check. Keep
   the real requests and responses (secrets masked), screenshots or a recording.
5. **Read the logs for the window.** From the go-live time to now, read the declared `logs` for the
   changed paths: errors, warnings, the new log lines the PR added. Quote the lines you read and
   the query or filter that produced them. "No errors" without the query is not evidence.
6. **Post the evidence** on each PR as one sticky comment (`code-flow:sticky-comment`): the dev
   revision and when it went live, how it got there (release run link, or sync), the verify steps
   and their output, the log query and lines, and anything not checked with the reason.
7. **Tracker.** Comment on the ticket with the sticky comment link and move it to done only when
   steps 3–5 held (`use-tracker`).

A step that cannot be done — no access to logs, a verify path that needs the owner's account —
is written into the sticky comment as not checked, with the reason, and reported; it is never
filled with a guess.
