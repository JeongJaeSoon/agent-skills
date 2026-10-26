# Adapter: obsidian

Select the vault explicitly with `notes.obsidian.vault` or the user's request. No personal
vault is assumed.

## Access mode

The path the user's own instructions name, else `notes.obsidian.access`, is the one path to
the vault, for reads as well as writes. Nothing else touches the vault:

| Value | Path |
|---|---|
| `mcp` | The connected Obsidian MCP server's vault tools. No shell command (`cat`, `ls`, `grep`, `find`), file tool (Read, Write, Edit, Grep, Glob) or Obsidian CLI on the vault folder |
| `cli` | The local Obsidian CLI |
| `filesystem` | File tools on the vault folder |

`mcp` exists for vaults kept by a sync service: the folder on disk lags the running app, so a
read taken from it mid-sync is stale, and a file written there can be deleted when the app
next syncs. Under `mcp` there is no fallback: when the vault tools are not connected, stop and
report it. Neither the instructions nor the key name a path (with `adapter: obsidian`): stop
and ask which one the vault uses; never fall back to the filesystem. A delegate returns this
stop to its caller, which asks the user. Under `cli` or `filesystem`, confirm which copy is
authoritative when synchronization could lag.

The mode is an instruction, not a tool restriction: the plugin's `notes-writer` keeps the
file tools a `markdown` store needs. A user who wants the harness to enforce `mcp` points
`notes.delegate_agent` at their own agent whose `tools:` lists only the vault tools.

With Obsidian Sync on, a path Sync already deleted is deleted again when re-created: use a new
filename. The Obsidian CLI has no in-place edit, and a create that overwrites a large note can
hang: patch with an edit tool rather than rewriting the note whole.

## Operations

Read the vault's instructions before a write. Inspect the current note before editing,
check that a create path is unused, and preserve unrelated content. With the vault tools,
`vault_read`, `vault_search`, `vault_edit` (dry run first), `vault_write` and `vault_append`
are common operations; discover the actual tools available in this environment.

A downloaded copy is a snapshot, not permission to overwrite a newer note. Re-read before
writing after a pause or conflicting edit. Show the note through its normal link or the
user's requested view. Publishing an additional page requires authorization; it is not a
prerequisite for displaying a note.
