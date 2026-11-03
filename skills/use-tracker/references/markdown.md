# Markdown: a local ticket folder

A folder of Markdown files is the tracker. Each subfolder is a project and each file is one
ticket. No server and no MCP: agents and scripts both use `tracker.py`. A human reads and edits
the same files in any Markdown editor; in Obsidian, the bundled Bases file is the board (below).

```json
{"tracker": {"adapter": "markdown", "markdown": {"root": "~/tickets", "link": "obsidian"}}}
```

`link` sets each ticket's `url`: `file` (default) gives `file://…`; `obsidian` gives
`obsidian://open?path=…`, which opens the ticket in Obsidian. A web page such as the orchestrate
dashboard cannot open `file://` links, so use `obsidian` when the folder is an Obsidian vault.

`root` must be an existing folder. `tracker.py` never runs git: if the folder is a git repository,
committing and pushing is the editor's or the user's job (in Obsidian, the Git plugin).

## Layout

```
<root>/
  abc/                 project "abc": --project abc
    ABC-1.md           ticket ABC-1
    ABC-2.md
    index.md           any other name is not a ticket and is left alone
  acme/
    ACME-1.md
```

- A project name is a folder name of letters, digits, `-` and `_`. `create` makes the folder;
  `list` on a missing folder is an error, so a typo never reads as an empty project.
- A ticket is a file directly in a project folder named `<FOLDER IN CAPITALS>-<n>.md`; `notes-1.md`
  or `abc-1.md` in `abc/` is not one. An id is that name without `.md`; a new one takes the number
  one above the highest file in that folder. `get` finds the folder from the id. Two folders whose names differ only in case
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

## Obsidian board

Open `root` as an Obsidian vault (1.14 or later, for the Bases Kanban view) and copy
[`../assets/Board.base`](../assets/Board.base) to `<root>/Board.base`. It has three views:

| View | Shows |
|---|---|
| Board | Kanban, one column per `state`; each card is headed by the id and the title, then `labels` and `parent` |
| By parent | Table grouped by `parent`; tickets without one sit under `(top level)` |
| All tickets | Every ticket in one table |

The board shows the same files `tracker.py` treats as tickets, and all three views sort by project
folder, then by the ticket number as a number (`ABC-2` before `ABC-10`).
Moving a card to another column rewrites that ticket's `state`; `tracker.py` reads the change
like any other edit. A ticket made in Obsidian needs a `<KEY>-<n>.md` name and a `state`, or
`tracker.py` and the board skip it; asking an agent to file it avoids both.

Writing tickets stays with `tracker.py`. With the Obsidian CLI turned on (Settings → General →
Advanced), an agent checks the result without writing anything:

```bash
obsidian vault=<vault> base:query path=Board.base view=Board format=json   # what the board holds
obsidian vault=<vault> dev:screenshot path=<file.png>                       # what the window shows
obsidian vault=<vault> dev:errors                                           # rendering errors
```

`dev:screenshot` needs no screen-recording permission, because the app captures its own window.

Change `Board.base` rarely, and not while it is open: Obsidian keeps an open board's settings in
memory and writes them back over an outside edit. Close its tab, edit, then reopen. Two limits of
the Bases engine (seen on 1.15.0) shaped the file:

- Grouping by a property that is empty on some notes breaks the table, so `By parent` groups by
  a formula that turns an empty `parent` into `(top level)`.
- Only the first `sort` key takes effect, so one formula joins the folder and the number.

## Dashboard

The orchestrate dashboard's Overview has a Tickets card for this adapter: per project folder, the count per `state` and the tickets in `started` or `review`, linked through `link`. It reads `tracker.py projects`, which lists the project folders and the board's link (null without `<root>/Board.base`).

## Errors

A file the adapter cannot read stops the command with the file's path: no frontmatter, a line it
cannot parse (a multi-line `|` value, for example), or a `state` outside the list above.
`true`/`false`, whole numbers and decimals keep their type when a file is rewritten. Fix the
file; the adapter never guesses a value. Ignore `.tracker.lock` and `.*.md.tmp` in git.
