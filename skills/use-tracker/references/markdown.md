# Markdown: a local ticket folder

A folder of Markdown files is the tracker. Each subfolder is a project and each file is one
ticket. No server and no MCP: agents and scripts both use `tracker.py`. A human reads and edits
the same files in any Markdown editor; in Obsidian, a Bases Kanban view grouped by `state` is the
board.

```json
{"tracker": {"adapter": "markdown", "markdown": {"root": "~/tickets"}}}
```

`root` must be an existing folder. `tracker.py` never runs git: if the folder is a git repository,
committing and pushing is the editor's or the user's job (in Obsidian, the Git plugin).

## Layout

```
<root>/
  abc/                 project "abc": --project abc
    ABC-1.md           ticket ABC-1
    ABC-2.md
    index.md           any other file is not a ticket and is left alone
  acme/
    ACME-1.md
```

- A project name is a folder name of letters, digits, `-` and `_`. `create` makes the folder;
  `list` on a missing folder is an error, so a typo never reads as an empty project.
- An id is the folder name in capitals, `-`, and a number one above the highest file in that
  folder. `get` finds the folder from the id. Two folders whose names differ only in case
  (`abc`, `ABC`) would share a key, so every command refuses them until one is renamed.

## A ticket file

```markdown
---
id: "ABC-2"
title: "Expire the session"
state: "started"
labels: ["auth"]
parent: "[[ABC-1]]"
created_at: "2026-11-03T10:00:00Z"
updated_at: "2026-11-03T12:00:00Z"
---
## Done when
- [ ] an expired token gets 401

## Comments

### 2026-11-03T12:00:00Z

PR opened: acme/app#12
```

| Field | Meaning |
|---|---|
| `state` | `triage`, `backlog`, `todo`, `started`, `review`, `completed` or `canceled`. `todo` is the `unstarted` type and `review` a `started` one |
| `parent`, `related` | ids as Obsidian links, `[[ABC-1]]`; `tracker.py` returns them without the brackets |
| `labels`, `assignee`, `priority` | as in the shared issue schema |
| `created_at`, `updated_at`, `completed_at`, `canceled_at` | UTC; `tracker.py` sets them on create, each write and close |

The body before `## Comments` is the description. `comment` appends a `### <time>` entry under
that heading and adds the heading the first time.

## Writes

| Operation | `tracker.py` |
|---|---|
| File | `create --project P --title T --body-file F [--label L] [--parent ID] [--related ID]`; starts in `backlog` |
| Add labels | `label ABC-1 --add L` |
| Comment | `comment ABC-1 --body-file F` |
| Move | `transition ABC-1 --to started\|review\|completed\|canceled`; never moves a ticket back |

Each write rewrites the whole file under a lock on `<root>/.tracker.lock` and replaces it in one
step, and a new ticket is written in full before it is linked into place, so an editor or a git
commit never sees half a file. `transition` decides on the state it reads under that lock. Frontmatter keys the adapter does not
know (an Obsidian property, a tag) are kept. `tracker.py` writes every value in JSON-style quotes;
it reads what Obsidian writes back (plain words, single quotes, block lists).

## Errors

A file the adapter cannot read stops the command with the file's path: no frontmatter, a line it
cannot parse (a multi-line `|` value, for example), or a `state` outside the list above.
`true`/`false`, whole numbers and decimals keep their type when a file is rewritten. Fix the
file; the adapter never guesses a value. Ignore `.tracker.lock` and `.*.md.tmp` in git.
