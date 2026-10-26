---
name: use-notes
description: "Use whenever working docs are read or written — \"노트에 기록해줘\", \"설계안을 노트에\", design docs, worklogs, program notes, \"정본\", a project note path, or showing such a doc to the user. Routes to the configured notes store (Obsidian vault or plain Markdown). What to record, and when, is record-progress's."
---

# Working notes

Design docs, worklogs and program notes live in one notes store. Which store is active comes
from `~/.claude/agent-skills.json`:

```json
{"notes": {"adapter": "obsidian", "obsidian": {"vault": "<vault>", "access": "mcp"}, "markdown": {"root": "~/notes"}}}
```

When upgrading, report an unset destination explicitly; see the [configuration migration guide](../../docs/configuration-migration.md). Never move existing notes or replace unrelated configuration keys.

No `notes` configuration: use an explicit destination supplied by the user; otherwise keep
the result in the current task and ask for a destination before any external write. A program may override it in its `program.json`
(`"notes": {"adapter": "markdown", "root": "<repo>/docs/notes"}`).

Read the adapter's reference before the first read or write in a session:

- `obsidian` → `references/obsidian.md`. The path the user's instructions name, else
  `notes.obsidian.access` (`mcp`, `cli` or `filesystem`), is the only path to the vault, reads
  included; with neither, stop and ask rather than read the folder on disk.
- `markdown` → `references/markdown.md`

## The store's rules file comes first

The store may carry its own rules file (the vault's `CLAUDE.md`, or `<root>/README.md`). Read it
through the adapter's read tool once per session before the first write, whether creating,
editing or appending. A new note follows all of it: folder, file name, tags, first heading
level, links, section order, the marker for finished work. A note you edit or append to follows
its content rules in the part you write; a name or folder that breaks the rules is reported,
and moved or renamed only with the user's approval. Where the rules file and this skill
differ, it wins. The names below are defaults for a store without rules: under a rule like
"titles in the user's language", `worklog-<ticket>` becomes a title in that language that still
names the ticket.

What it most often catches, because skill text reads like plain Markdown:

- Tags and the first heading where the rules put them, not in YAML or as `#` because a generic
  Markdown file would.
- Related notes linked the store's way (`[[title]]` in Obsidian), never as a backticked path: a
  note with no link in or out is lost.
- An appended section goes above a section the rules keep last (references).

Hand every write to the store to the agent the user's own instructions name, else
`notes.delegate_agent` in the config, else `agent-skills:notes-writer`, with the facts. Reads
stay here. `"none"` writes in this session; so does the delegate itself. The
agent reads the rules file itself on every run; a copy of the rules pasted into its definition
goes stale.

## Conventions every adapter keeps

- Paths follow the configured store or the user's requested destination. Worklogs (`worklog-<ticket-or-topic>`) are
  updated as each piece lands and pruned when the project wraps. Program notes are
  `program-<slug>` (`orchestrate`).
- A note is the source of truth; anything shown to the user (an Artifact page, a chat summary)
  is a view re-read from the store at that moment, never from an older copy.
- Prose follows the user's language rule in CLAUDE.md; identifiers stay as they are.
- Write the prose per `write-plainly`.
