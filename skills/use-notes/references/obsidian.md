# Adapter: obsidian

The vault is the source of truth for design docs and worklogs. It syncs across devices, so the
copy on disk may lag. Only the `mcp__claude_ai_Obsidian__*` tools see the synced state.
The vault name comes from `notes.obsidian.vault` in the config (default `Private`).

| Operation | Tool |
|---|---|
| read | `vault_read` / `vault_batch_read` |
| create or replace | `vault_write` |
| edit in place | `vault_edit` on the current content |
| append a log line | `vault_append` |
| search | `vault_search` |
| where things go | `vault_read("CLAUDE.md")` — the vault's own rules (folders, tags, headings, file names) |

## Which tool touches the vault

- Never open the vault folder with `cat`, `Read`, `grep`, or the Obsidian CLI for content.
- The `obsidian:obsidian-cli` plugin skill reads the disk copy and its "read, create, search"
  triggers overlap with MCP; for content, MCP wins. Use the CLI only for plugin/theme
  development or app-level actions (reload, screenshot, run command).
- A large MCP result gets saved to `tool-results/` and you may split it into the scratchpad
  to read in pieces. That copy is a snapshot. Before editing code against a design doc, or after
  any pause longer than a few minutes, re-read the doc through MCP; do not trust the scratchpad.
- Never write back from a scratchpad copy. Edit the vault doc with `vault_edit` on the current
  content.

## Showing a doc to the user

- The local Obsidian app and the Orca editor both read the disk copy, which may lag behind sync.
  Never open a vault note with `open obsidian://…` or `orca file open`.
- To show a doc: re-read it with `vault_read`, publish an Artifact HTML page from that content,
  and give the link. The vault note stays the source of truth; the artifact is a view.
