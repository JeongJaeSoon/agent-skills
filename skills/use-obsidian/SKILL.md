---
name: use-obsidian
description: How to read, write and show this user's Obsidian vault (`Private`) for design docs, worklogs and project notes. Use whenever a prompt points at Obsidian, a vault path like Project/<name>/*.md, "정본", "worklog", or working docs.
---

# Obsidian vault

The vault is the source of truth for design docs and worklogs. It syncs across devices, so the
copy on disk under `~/workspace/obsidian_vault/Private` may lag. Only the
`mcp__claude_ai_Obsidian__*` tools see the synced state.

## Which tool touches the vault

- Read with `vault_read` / `vault_batch_read`, write with `vault_write` / `vault_edit`.
  Never open the vault folder with `cat`, `Read`, `grep`, or the Obsidian CLI for content.
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
  and give the link. The vault note stays the 정본; the artifact is a view.

## Folder layout, syntax, tags, file names

One source: the vault's own `CLAUDE.md` at the vault root. Read it with `vault_read("CLAUDE.md")`
before creating a note or when unsure where something goes. It holds the PARA folder table,
heading/tag/link rules, file-name patterns and section order. Do not copy those rules here.

Worklogs for a project go under `Project/<name>/worklog-<ticket>.md`, updated as each piece
lands, pruned when the project wraps.
