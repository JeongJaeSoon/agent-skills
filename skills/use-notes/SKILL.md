---
name: use-notes
description: "Use whenever working docs are read or written — \"노트에 기록해줘\", \"노트에 남겨줘\", \"KB에 정리해줘\", \"설계안을 노트에\", design docs, worklogs, program notes, \"정본\", a note path. Routes to the configured notes store."
---

# Working notes

Design docs, worklogs and program notes live in one notes store. Which store is active comes
from `~/.claude/agent-skills.json`:

```json
{"notes": {"adapter": "obsidian", "obsidian": {"vault": "<vault>", "access": "mcp"}, "markdown": {"root": "~/notes"}}}
```

Never move existing notes or replace unrelated configuration keys ([configuration migration guide](../../docs/configuration-migration.md)).

No `notes` configuration: use an explicit destination supplied by the user; otherwise keep
the result in the current task and ask for a destination before any external write. A program may override it in its `program.json`
(`"notes": {"adapter": "markdown", "root": "<repo>/docs/notes"}`).

Read the adapter's reference before the first read or write in a session:

- `obsidian` → `references/obsidian.md`. The path the user's instructions name, else
  `notes.obsidian.access` (`mcp`, `cli` or `filesystem`), is the only path to the vault, reads
  included; with neither, stop and ask rather than read the folder on disk. When that path is
  down (the MCP server is not connected), the folder on disk is still off limits, but the task
  goes on: finish the work, keep what the note would get in the report, and say which write is
  pending.
- `markdown` → `references/markdown.md`

## The store's rules file comes first

The store may carry its own rules file (the vault's `CLAUDE.md`, or `<root>/README.md`). Read it
through the adapter's read tool once per session before the first write, whether creating,
editing or appending. A new note follows all of it: folder, file name, tags, first heading
level, links, section order, the marker for finished work. A note you edit or append to follows
its content rules in the part you write; a name or folder that breaks the rules is reported,
and moved or renamed only with the user's approval. Where the rules file and this skill
differ, it wins. The names below are defaults for a store without rules; a renamed note still
names its ticket.

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
  updated as each piece lands; when the project wraps, edit out expired status inside the
  note, and archive the note itself only under "Removing a note" below. Program notes are
  `program-<slug>` (`orchestrate`).
- **Removing a note.** A note is never deleted (no delete tool, no `rm`, no empty overwrite).
  One that is superseded, duplicated or finished moves to the archive location the store's
  own rules file names; read that location from the rules file on every run, and never
  assume a folder name. When the rules file names none, do not move the note: stop and ask
  the user. Never overwrite at the destination: when a note of that name is already there,
  stop and report it, or add a date suffix to the moved note's name; write which one you did
  in the report. Move with the adapter's move operation, then read the destination back.
- A note is the source of truth; anything shown to the user (an Artifact page, a chat summary)
  is a view re-read from the store at that moment, never from an older copy.
- A document written for the owner to read (a review, a summary, a design note) is published
  as an Artifact only: no local `.md` copy beside it, and the report gives its link, not a file
  path. Read it for secrets (tokens, keys, credentials, personal data) before publishing; one
  found is removed, never masked and shipped.
- What to record, and when, is `end-session` [references/record.md](../end-session/references/record.md):
  read it when an investigation or design concludes (the note gets what was chosen, what was
  ruled out and why, the trap; the ticket gets a link to the note) or when the user asks for a note.
- Write the prose per `write-plainly`; identifiers stay as they are.
