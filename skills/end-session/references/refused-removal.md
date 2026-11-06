# A refused card removal

For whoever ran `orca worktree rm` on a card and had a permission layer (the auto-mode classifier, a hook) refuse it:
a cleaner, a lead removing its own card by `SKILL.md` §4, or a lead acting on a `CLOSE OUT` line.

The one-retry rule of `SKILL.md` §7 ("A refused close") holds. With nothing to add, the card goes to a person:

1. Record it: `python3 <reap-resources>/scripts/rm_refused.py add <card path>`. This is a file write under the state
   directory, not the refused act.
2. Report it: `orca orchestration send --to run:<runId> --type status --subject "card left: worktree rm refused" --body "<card path>"`.

A recorded card leaves every automatic cleanup: `cards.py`, `reap-hard`, the janitor's guard, and `orch wait`'s
`CLOSE OUT`, which prints a hand-off line with the one command to run instead of the cleaners' line. The person runs
that command; nobody else runs it, and the record ends with the card's folder.
