# Adapter: obsidian

Select the vault explicitly with `notes.obsidian.vault` or the user's request. No personal
vault is assumed. Use the connected vault tools, local Obsidian CLI, or filesystem access
that the user selected and the environment permits; when the user's instructions allow one
path only (for example, vault tools only), reads go through it too. Confirm which copy is
authoritative when synchronization could lag; do not assume one tool is always fresher.

With Obsidian Sync on:

- A file written to the vault folder behind the app's back may be deleted when the app next
  starts, because Sync removes files missing from its index. Write through the app's tools.
- A path Sync already deleted is deleted again when re-created. Use a new filename.
- The Obsidian CLI has no in-place edit, and a create that overwrites a large note can hang.
  Patch with an edit tool rather than rewriting the note whole.

Read the vault's instructions before a write. Inspect the current note before editing,
check that a create path is unused, and preserve unrelated content. With connected tools,
`vault_read`, `vault_search`, `vault_edit` (dry run first), `vault_write` and `vault_append`
are common operations; discover the actual tools available in this environment.

A downloaded copy is a snapshot, not permission to overwrite a newer note. Re-read before
writing after a pause or conflicting edit. Show the note through its normal link or the
user's requested view. Publishing an additional page requires authorization; it is not a
prerequisite for displaying a note.
