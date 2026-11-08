# Adapter: markdown

Plain Markdown files under one root directory (`notes.markdown.root` in the config, default
`~/notes`). Use it on a machine without the Obsidian connector, or when a project keeps its notes
in the repo (set the root to e.g. `<repo>/docs/notes` in the program's config override).

| Operation | Tool |
|---|---|
| read | `Read` |
| create or replace | `Write` |
| edit in place | `Edit` |
| append a log line | `Edit` at the end of the section, or `printf >> file` |
| search | `grep -rn` under the root |
| archive a note | `mv -n` to the location the rules file names ("Removing a note" in SKILL.md), after checking the destination is unused; never `rm` |
| where things go | `<root>/README.md` if it exists; otherwise the layout below |

Layout when the root has no rules of its own: `Project/<project>/` for design docs, worklogs and
program notes, one file per topic, `#tag` lines at the top are optional.

If the root is inside a git repo, commit note changes with the work they describe; the notes
are then reviewed with the code.
