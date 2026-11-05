# Opening the pull request

## The body is a briefing, not the lab notebook

A reviewer who has the diff should learn why the change exists, what it leaves out, and how you
proved it works. Use these sections in order and drop any with nothing to say, except 검증:

- `## 왜`: intent and approach, one or two short paragraphs.
- `## 범위`: real symbols and paths, both sides of a rename; in and out only where the boundary
  matters.
- `## 트레이드오프`: only rejected alternatives a reviewer would otherwise ask about.
- `## 영향 범위`: in one to three sentences, who or what it touches and why that is safe.
- `## 검증`: required. Each real run path, the exact test method, and its verdict. For a
  performance change, one primary number in `before → after` form with its unit, both measured
  under the machine's load limit (`reap-resources` "Load"); a number taken above it is not evidence.

Screenshots go in the sticky comment, not the body, as
[visual-evidence.md](visual-evidence.md) says; a video that proves a claim goes there too, and its
local copy is registered the same way. No `## Summary` / `## Test plan` template,
SHAs, file-by-file checklists, or review-round recitals; those belong in the sticky comment or
the worklog. The body becomes the squash commit body, so keep it within about 40 lines. The
title is Conventional Commits, `type(scope): subject`, imperative, no trailing period, naming a
real symbol when one carries the change. Write the title and body with `write-plainly`, and list
every ticket id the PR closes.

## Stacks

Before opening any PR, list the open ones (`gh pr list --state open --json number,title,headRefName,baseRefName,files`)
and decide whether this change belongs on top of one:

- An open PR already does this scope: do not open a second PR for it. Build on that PR's
  branch and stack on it, or add to it when it is yours.
- This change uses code from an open PR (its symbols or schema): stack it.
  `gh pr create --base <lower branch>`, and the body's first line is `Stacked on #<lower>`
  with the land order (`#<lower> → #<this>`). Keep the stack linear and at most 3 layers, with one
  layer on a base: a second PR on the same base is a fan-out, which lands neither as one stack nor
  as two independent PRs.
- It only touches the same files as an open PR and needs nothing from it: do not stack. Open it
  after that PR lands, unless the overlap is only a hotspot (a `translated-from` line, a generated
  file), which stays parallel.
- It only has to deploy or run after an open PR: base it on the default branch; the order is an
  `orch dep` edge in a program, else a line in the body, never a stack.
- None of these: base it on the default branch.

Dependent PRs are a GitHub stack (one ticket per layer, stacked because the upper one uses the
lower one's code), not just a PR whose base is another branch. After opening them, link them with
the `gh stack` extension (`gh stack link <bottom> <top> --base main`, or `gh stack init` /
`add` / `submit` from scratch) and confirm with
`gh api repos/<owner>/<repo>/pulls/<top> --jq .stack` (`gh pr view` does not show stacks). The
link is what lands them in one merge. Whether CI runs on an unlinked upper layer depends on the
repository's workflow triggers: read `gh pr checks` on that layer rather than assume either way.
`hooks/stack_gate.py` warns at `gh pr create` when a brief's ORDER `stacked on` or the branch's
history shows a dependency its base does not build on (`orchestrate.stack_dependent`: warn, deny
or off).

Each layer keeps its own 검증 section and sticky comment, verified on top of the layer below.
A stack lands from its top in one merge ([CLI reference](https://docs.github.com/en/pull-requests/reference/stacked-prs-cli-commands)):
once every layer is verified and green, `gh stack merge <top> --squash --yes` merges the top and
every layer below it, and on a merge queue branch adds them to the queue. A queued layer has not
merged: it is done when `gh pr view` says MERGED. Do not use `gh pr merge`: the docs say the
synchronous merge "does not support stacked pull requests", and it refuses every stacked PR, the
last open layer too. Inside a program, `orch land <slug> --pr <top>` does this
for you; never `gh stack merge` or call merge-async by hand there.

When a lower layer changes, rebase every layer from the bottom, `gh pr update-branch <n> --rebase`
one after the other, and re-verify them. A stack merges only as a linear chain: a merge commit,
or rebasing the lower layer alone, fails the merge with `Stack needs to be rebased`. So a layer
branch takes no merge commit, `origin/main` included: the exception to SKILL.md §4's "merge main
in". When `update-branch` is refused, report it rather than merge.

When a lower layer merges on its own, GitHub moves the next one onto the base and rebases it
([merging stacked PRs](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/merging-stacked-pull-requests)).
Its head changes, so CI runs again; change nothing by hand. Only if its timeline shows no
`automatic_base_change_succeeded` and its head stays the same (a
[report](https://github.com/github/gh-stack/discussions/463) for `merge-async`), run
`gh pr update-branch <next> --rebase`.
