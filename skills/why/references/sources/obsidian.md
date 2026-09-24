# Obsidian Notes

## What this source contains

- Design docs written before the code (`Project/<project>/*.md`)
- Worklogs (`worklog-<ticket-or-topic>.md`) updated as each piece landed: what was tried, what was dropped, and why
- Program notes (`program-<slug>.md`) recording milestone-level decisions
- Research notes that preceded a design

These are the author's own working notes, often the only place a rejected approach or a mid-task decision was written down.

## How to search it

Read the `use-notes` skill and its active adapter's reference first. It names the store and the tools that read it; for Obsidian that is the `mcp__claude_ai_Obsidian__*` tools, never the disk copy. The steps below name the Obsidian tools; use the adapter's equivalents otherwise.

1. **Search with `vault_search`** for the ticket ID, feature name, key symbols, PR number, and error strings.
2. **Read the project folder.** Once a hit lands in `Project/<project>/`, read that project's design doc and worklogs in full with `vault_read` or `vault_batch_read`. The decision is usually in a dated worklog entry, not the doc's summary.
3. **Match dates.** Line worklog entries up with commit and PR dates from the code anchor.

## Common pitfalls

- **Plans vs outcomes.** A design doc may describe a plan the code later abandoned. Look for the worklog entry that records the change of course, and report both.
- **Private shorthand.** Notes are terse. Quote them verbatim instead of expanding the shorthand into a claim.

## What to return

For each relevant note: its vault path, the dated entry or section, the quoted text, and whether it is a plan (design doc) or a record of what happened (worklog).
