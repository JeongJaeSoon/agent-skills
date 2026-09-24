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
  performance change, one primary number in `before → after` form with its unit.

Attach a screenshot or video when it proves a claim. No `## Summary` / `## Test plan` template,
SHAs, file-by-file checklists, or review-round recitals; those belong in the sticky comment or
the worklog. The body becomes the squash commit body, so keep it within about 40 lines. The
title is Conventional Commits, `type(scope): subject`, imperative, no trailing period, naming a
real symbol when one carries the change. Write the title and body with `write-plainly`, and list
every ticket id the PR closes.

## Stacks

Dependent PRs are a GitHub stack (one ticket per layer, stacked because the lower one must
merge first), not just a PR whose base is another branch. After opening them, link them with
the `gh stack` extension (`gh stack link <bottom> <top> --base main`, or `gh stack init` /
`add` / `submit` from scratch) and confirm with
`gh api repos/<owner>/<repo>/pulls/<top> --jq .stack` (`gh pr view` does not show stacks).

Each layer keeps its own 검증 section and sticky comment, verified on top of the layer below.
A stack lands from its top in one merge: once every layer is verified and green,
`gh api -X PUT repos/<owner>/<repo>/pulls/<top>/merge-async -f merge_method=squash -f sha=<top head>`
merges the top and every layer below it; confirm each layer MERGED with `gh pr view`. Stacked
PRs reject `gh pr merge`. Inside a program, `orch land <slug> --pr <top>` does this for you;
never call merge-async by hand there.
