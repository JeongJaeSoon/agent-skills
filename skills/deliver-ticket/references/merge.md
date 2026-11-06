# Merging your own PR

Read this only on the branch of `deliver-ticket` §6 where you merge the PR yourself: a standalone
card whose repo has the land executor off, or a session the user started. A program's PR lands
with `orch land`, and a standalone card under the executor reports READY instead.

When the ticket, the brief or the project's standing orders name a merge procedure (a merge queue,
a landing order, an exclusive lane), follow that procedure instead of merging at will.

Otherwise merge when the PR is ready at its current head — CI green (read the log), review done,
no conflict — whether or not it is behind. Spend one minute first on
`git diff --name-only <CI base>..origin/main`: if it touches a contract or test premise this PR
relies on, merge main in and let CI rerun. Do not rebase only because the branch is behind.

Checks green but `mergeStateStatus` BLOCKED: before concluding a review is missing, look for a
required check whose run at the current head was cancelled (`gh run list --commit <head sha>`;
concurrent events cancel one another) and rerun it with `gh run rerun <id>`.

Only in a repository whose branch protection or ruleset requires a review may an approved PR still
waiting on checks get `gh pr merge <n> --auto --match-head-commit <approved sha>`; before pushing a
new head to it, run `gh pr merge <n> --disable-auto` so that head does not land unreviewed. Once it
has merged that way, read its CI logs all the same (`deliver-ticket` §5, "A green check is not
evidence"). Elsewhere merge by hand once it is ready:

```bash
gh pr merge <n> --squash --delete-branch      # the merge method the repo allows; not for a stack, which lands with `gh stack merge` (pull-request.md)
gh pr view <n> --json state,mergeCommit       # confirm MERGED and record the commit
```
