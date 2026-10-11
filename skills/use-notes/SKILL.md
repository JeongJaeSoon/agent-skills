---
name: use-notes
description: "Use whenever working docs are read or written — \"노트에 기록해줘\", \"노트에 업데이트해줘\", \"설계안을 노트에\", design docs, worklogs, program notes, research notes, \"정본\", \"worklog\", \"노트에 적어\", a vault path like Project/<name>/*.md, or showing such a doc to the user. Routes to the configured notes store (Obsidian vault or plain Markdown)."
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

## The store's rules file comes first

The store may carry its own rules file (the vault's `CLAUDE.md`, or `<root>/README.md`). Read it
through the adapter's read tool once per session before the first write, whether creating,
editing or appending, and apply it to every note you touch: folder, file name, tags, first
heading level, links, section order, the marker for finished work. Where it and this skill
differ, it wins. The names below are defaults for a store without rules: under a rule like
"titles in the user's language", `worklog-<ticket>` becomes a title in that language that still
names the ticket.

What it most often catches, because skill text reads like plain Markdown:

- Tags and the first heading where the rules put them, not in YAML or as `#` because a generic
  Markdown file would.
- Related notes linked the store's way (`[[title]]` in Obsidian), never as a backticked path: a
  note with no link in or out is lost.
- An appended section goes above a section the rules keep last (references).

When `notes.delegate_agent` is set in the config, or the user's own instructions name an agent
for writes to the store, hand every write to that agent with the facts. Reads stay here. The
agent reads the rules file itself on every run; a copy of the rules pasted into its definition
goes stale.

## Conventions every adapter keeps

- Default paths are `Project/<project>/<name>.md`. Worklogs (`worklog-<ticket-or-topic>`) are
  updated as each piece lands and pruned when the project wraps. Program notes are
  `program-<slug>` (`orchestrate`).
- A note is the source of truth; anything shown to the user (an Artifact page, a chat summary)
  is a view re-read from the store at that moment, never from an older copy.
- Prose follows the user's language rule in CLAUDE.md; identifiers stay as they are.
- Write the prose per `write-plainly`.
