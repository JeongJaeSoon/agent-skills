---
name: use-notes
description: "Use whenever working docs are read or written — \"obs 에 기록해줘\", \"obs 에 업데이트해줘\", \"설계안을 obs 에\", design docs, worklogs, program notes, research notes, \"정본\", \"worklog\", \"노트에 적어\", a vault path like Project/<name>/*.md, or showing such a doc to the user. Routes to the configured notes store (Obsidian vault or plain Markdown)."
---

# Working notes

Design docs, worklogs and program notes live in one notes store. Which store is active comes
from `~/.claude/agent-skills.json`:

```json
{"notes": {"adapter": "obsidian", "obsidian": {"vault": "Private"}, "markdown": {"root": "~/notes"}}}
```

No file, or no `notes` key → `obsidian`. A program may override it in its `program.json`
(`"notes": {"adapter": "markdown", "root": "<repo>/docs/notes"}`).

Read the adapter's reference before the first read or write in a session:

- `obsidian` → `references/obsidian.md`
- `markdown` → `references/markdown.md`

## Conventions every adapter keeps

- Paths are `Project/<project>/<name>.md`. Worklogs are `worklog-<ticket-or-topic>.md`, updated
  as each piece lands, pruned when the project wraps. Program notes are `program-<slug>.md`
  (`orchestrate`).
- The store's own rules file (the vault's `CLAUDE.md`, or `<root>/README.md`) wins over these
  conventions for folders, tags, headings and file names. Read it before creating a note.
- A note is the source of truth; anything shown to the user (an Artifact page, a chat summary)
  is a view re-read from the store at that moment, never from an older copy.
- Prose follows the user's language rule in CLAUDE.md; identifiers stay as they are.
