---
name: prune-comments
description: "Use to cut comments in a diff down to what the code cannot say: /prune-comments, a request to clean up or remove comments (\"주석 정리해줘\"), and deliver-ticket's step before review. Scoped to the diff or the files the caller names; for prose in docs and PR bodies, use write-plainly."
---

# Prune comments

A fresh reader prunes the comments in scope; you check its work and act on its flags. The author of a comment is the worst judge of whether it earns its place, so the pruner is a subagent.

## Scope

The caller's files or diff. Otherwise the current branch against its base (default `origin/main`) plus the working tree: `git diff $(git merge-base origin/main HEAD)`, and every new file from `git ls-files --others --exclude-standard`, which that diff leaves out. Within a diff, the scope is the comments on added or changed lines, plus any nearby comment the diff made false. Untouched code elsewhere is out of scope, however noisy.

If the diff adds or changes no comment, and no existing comment sits beside the code it changed, say "no comments in scope" and stop.

## Steps

1. Spawn one `Agent` (`subagent_type: "general-purpose"`) with the scope and the absolute path of `references/pruner.md`. Its brief is that file; do not restate it.
2. Check its edits with `git diff`. Revert any change to code rather than comments, and any edit outside the scope. Restore a deleted comment only when it matches a keep in the brief and you can point to the proof. If a report gets its facts wrong, rerun once with the error named; a second bad report means you finish the pass by hand and say so.
3. Act on each **RESHAPE** flag (a comment was explaining a surprise in our own code). A rename, an extracted function, or a type that fits inside the diff's files: do it now and run the tests. Anything larger follows deliver-ticket's rule for findings outside the ticket.
4. Act on each **CONSTRAINT** flag (a comment claims a rule about our own code, like "do not remove" or "keep this order"). When the cheapest encoding the pruner named (a test, a type, a runtime assertion, a lint rule) fits inside the diff's files, add it, watch it fail when the rule is broken, then delete the comment. Otherwise keep the comment and report the constraint as unenforced.

## Report

The deletion count, the comments you restored and why, the reshapes done and left open, the constraints encoded, and the constraints still enforced only by a comment.
