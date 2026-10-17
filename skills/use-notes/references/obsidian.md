# Adapter: obsidian

Select the vault explicitly with `notes.obsidian.vault` or the user's request. No personal
vault is assumed. Use the connected vault tools, local Obsidian CLI, or filesystem access
that the user selected and the environment permits. Confirm which copy is authoritative
when synchronization could lag; do not assume one tool is always fresher.

Read the vault's instructions before a write. Inspect the current note before editing,
check that a create path is unused, and preserve unrelated content. With connected tools,
`vault_read`, `vault_search`, `vault_edit` (dry run first), `vault_write` and `vault_append`
are common operations; discover the actual tools available in this environment.

A downloaded copy is a snapshot, not permission to overwrite a newer note. Re-read before
writing after a pause or conflicting edit. Show the note through its normal link or the
user's requested view. Publishing an additional page requires authorization; it is not a
prerequisite for displaying a note.
