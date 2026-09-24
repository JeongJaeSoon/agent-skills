# Obsidian CLI — Complete Command Reference

All commands follow the syntax: `obsidian [vault=<name|id>] <command> [param=value ...] [flags]`

Parameters use `param=value`. Flags are bare boolean switches. Append `--copy` to any command to copy output to clipboard.

---

## Table of Contents

1. [General](#general)
2. [File & Folder Operations](#file--folder-operations)
3. [Daily Notes](#daily-notes)
4. [Search & Links](#search--links)
5. [Tags & Properties](#tags--properties)
6. [Tasks](#tasks)
7. [Bookmarks & Bases](#bookmarks--bases)
8. [File History & Sync](#file-history--sync)
9. [Plugins & Themes](#plugins--themes)
10. [Templates & Content Creation](#templates--content-creation)
11. [Workspace & Tabs](#workspace--tabs)
12. [Publishing & Web](#publishing--web)
13. [Command Palette & Hotkeys](#command-palette--hotkeys)
14. [Outline & Metadata](#outline--metadata)
15. [Developer Commands](#developer-commands)

---

## General

| Command | Description |
|---------|-------------|
| `help [<command>]` | Display available commands, or help for a specific command |
| `version` | Show Obsidian version |
| `reload` | Reload the app window |
| `restart` | Restart the application |

---

## File & Folder Operations

### Files

| Command | Parameters | Flags | Description |
|---------|-----------|-------|-------------|
| `file` | `file=<name>` or `path=<path>` | — | Show file information |
| `files` | `folder=<path>`, `ext=<extension>` | `total` | List vault files |
| `create` | `name=<name>`, `path=<path>`, `content=<text>`, `template=<name>` | `open`, `overwrite` | Create a new file |
| `read` | `file=<name>` or `path=<path>` | — | Read file contents |
| `append` | `file=<name>` or `path=<path>`, `content=<text>` | `inline` | Add content to end of file |
| `prepend` | `file=<name>` or `path=<path>`, `content=<text>` | `inline` | Add content after frontmatter |
| `open` | `file=<name>` or `path=<path>` | `newtab` | Open file in editor |
| `move` | `file=<name>` or `path=<path>`, `to=<path>` | — | Move/rename file (updates links) |
| `rename` | `file=<name>` or `path=<path>`, `name=<name>` | — | Rename file |
| `delete` | `file=<name>` or `path=<path>` | `permanent` | Delete file (trash by default) |

**Notes:**
- `file=` matches by filename (without extension). `path=` matches the full path from vault root.
- `create` with `template=` uses a template from the configured templates folder.
- `append` with `inline` appends to the last line instead of adding a new line.
- `prepend` inserts content after frontmatter (YAML between `---` delimiters), not at the very top.
- `delete` sends to system trash by default. The `permanent` flag permanently deletes — use with caution.
- `move` automatically updates all internal links pointing to the moved file.

### Folders

| Command | Parameters | Flags | Description |
|---------|-----------|-------|-------------|
| `folder` | `path=<path>`, `info=files\|folders\|size` | — | Show folder information |
| `folders` | `folder=<path>` | `total` | List vault folders |

---

## Daily Notes

| Command | Parameters | Flags | Description |
|---------|-----------|-------|-------------|
| `daily` | `paneType=tab\|split\|window` | — | Open today's daily note |
| `daily:path` | — | — | Get daily note path (even if not yet created) |
| `daily:read` | — | — | Read daily note contents |
| `daily:append` | `content=<text>`, `paneType=tab\|split\|window` | `inline`, `open` | Append to daily note |
| `daily:prepend` | `content=<text>`, `paneType=tab\|split\|window` | `inline`, `open` | Prepend to daily note (after frontmatter) |

**Notes:**
- The Daily Notes core plugin must be enabled for these commands to work.
- `daily:path` returns the path the daily note would have, even if it hasn't been created yet — useful for scripting.
- `paneType` controls how the note opens: `tab` (new tab), `split` (side-by-side), `window` (new OS window).

---

## Search & Links

### Search

| Command | Parameters | Flags | Description |
|---------|-----------|-------|-------------|
| `search` | `query=<text>`, `path=<folder>`, `limit=<n>`, `format=text\|json` | `case`, `total` | Search vault contents |
| `search:context` | `query=<text>`, `path=<folder>`, `limit=<n>`, `format=text\|json` | `case` | Grep-style search showing surrounding context |
| `search:open` | `query=<text>` | — | Open search in the Obsidian UI |

### Links

| Command | Parameters | Flags | Description |
|---------|-----------|-------|-------------|
| `backlinks` | `file=<name>` or `path=<path>`, `format=json\|tsv\|csv` | `counts`, `total` | List files that link to this file |
| `links` | `file=<name>` or `path=<path>` | `total` | List files this file links to |
| `unresolved` | `format=json\|tsv\|csv` | `total`, `counts`, `verbose` | List broken/unresolved links |
| `orphans` | — | `total` | List files with no incoming links |
| `deadends` | — | `total` | List files with no outgoing links |

**Notes:**
- `search` returns filenames matching the query. `search:context` includes surrounding text for each match (like `grep -C`).
- `orphans` are files nobody links to. `deadends` are files that don't link to anything. Both are useful for vault maintenance.
- The `case` flag makes search case-sensitive (default is case-insensitive).

---

## Tags & Properties

### Tags

| Command | Parameters | Flags | Description |
|---------|-----------|-------|-------------|
| `tags` | `file=<name>` or `path=<path>`, `format=json\|tsv\|csv` | `sort=count`, `total`, `counts`, `active` | List tags (vault-wide or per-file) |
| `tag` | `name=<tag>` | `total`, `verbose` | Get info about a specific tag |

### Properties (frontmatter)

| Command | Parameters | Flags | Description |
|---------|-----------|-------|-------------|
| `properties` | `file=<name>` or `path=<path>`, `name=<name>`, `format=yaml\|json\|tsv` | `sort=count`, `total`, `counts`, `active` | List properties |
| `property:set` | `name=<name>`, `value=<value>`, `type=text\|list\|number\|checkbox\|date\|datetime`, `file=<name>` or `path=<path>` | — | Set a property value |
| `property:remove` | `name=<name>`, `file=<name>` or `path=<path>` | — | Remove a property |
| `property:read` | `name=<name>`, `file=<name>` or `path=<path>` | — | Read a property value |
| `aliases` | `file=<name>` or `path=<path>` | `total`, `verbose`, `active` | List aliases |

**Notes:**
- Properties are stored in YAML frontmatter. `property:set` creates the frontmatter block if it doesn't exist.
- The `type=` parameter on `property:set` determines how Obsidian interprets the value (e.g., `checkbox` expects `true`/`false`).
- The `active` flag scopes the command to the currently active file in Obsidian's editor.
- Tag names should not include the `#` prefix when used with `tag name=`.

---

## Tasks

| Command | Parameters | Flags | Description |
|---------|-----------|-------|-------------|
| `tasks` | `file=<name>` or `path=<path>`, `status="<char>"`, `format=json\|tsv\|csv\|text` | `total`, `done`, `todo`, `verbose`, `active`, `daily` | List tasks |
| `task` | `ref=<path:line>`, `file=<name>` or `path=<path>`, `line=<n>`, `status="<char>"` | `toggle`, `daily`, `done`, `todo` | View or modify a single task |

**Notes:**
- Tasks are Markdown checkboxes: `- [ ] todo` and `- [x] done`.
- `status="<char>"` filters or sets to a custom status character (e.g., `status="/"` for in-progress).
- `toggle` flips a task between done and todo.
- The `daily` flag scopes tasks to today's daily note.
- `ref=<path:line>` precisely identifies a task by its file path and line number.

---

## Bookmarks & Bases

### Bookmarks

| Command | Parameters | Flags | Description |
|---------|-----------|-------|-------------|
| `bookmarks` | `format=json\|tsv\|csv` | `total`, `verbose` | List all bookmarks |
| `bookmark` | `file=<path>`, `subpath=<subpath>`, `folder=<path>`, `search=<query>`, `url=<url>`, `title=<title>` | — | Add a bookmark |

### Bases

| Command | Parameters | Flags | Description |
|---------|-----------|-------|-------------|
| `bases` | — | — | List `.base` files |
| `base:views` | — | — | List base views |
| `base:create` | `file=<name>` or `path=<path>`, `view=<name>`, `name=<name>`, `content=<text>` | `open`, `newtab` | Create a base item |
| `base:query` | `file=<name>` or `path=<path>`, `view=<name>`, `format=json\|csv\|tsv\|md\|paths` | — | Query a base |

**Notes:**
- Bases are Obsidian's database-like feature (`.base` files).
- `bookmark` can bookmark files, folders, searches, or URLs — use whichever parameter matches what you want to save.

---

## File History & Sync

### Local History

| Command | Parameters | Flags | Description |
|---------|-----------|-------|-------------|
| `diff` | `file=<name>` or `path=<path>`, `from=<n>`, `to=<n>`, `filter=local\|sync` | — | Compare file versions |
| `history` | `file=<name>` or `path=<path>` | — | List local history versions for a file |
| `history:list` | — | — | List all files that have local history |
| `history:read` | `file=<name>` or `path=<path>`, `version=<n>` | — | Read a specific history version |
| `history:restore` | `file=<name>` or `path=<path>`, `version=<n>` | — | Restore a file to a previous version |
| `history:open` | `file=<name>` or `path=<path>` | — | Open file recovery UI |

### Obsidian Sync

| Command | Parameters | Flags | Description |
|---------|-----------|-------|-------------|
| `sync` | `on` or `off` | — | Pause or resume sync |
| `sync:status` | — | — | Show sync status and storage usage |
| `sync:history` | `file=<name>` or `path=<path>` | `total` | List sync history versions |
| `sync:read` | `file=<name>` or `path=<path>`, `version=<n>` | — | Read a sync version |
| `sync:restore` | `file=<name>` or `path=<path>`, `version=<n>` | — | Restore from sync version |
| `sync:open` | `file=<name>` or `path=<path>` | — | Open sync history UI |
| `sync:deleted` | — | `total` | List files deleted in sync |

**Notes:**
- Obsidian Sync is a paid feature. These commands only work if Sync is configured.
- Version numbers for `history:restore` and `sync:restore` come from the corresponding list commands.
- `diff` can compare local versions, sync versions, or across both — use `filter=` to narrow.

---

## Plugins & Themes

### Plugins

| Command | Parameters | Flags | Description |
|---------|-----------|-------|-------------|
| `plugins` | `filter=core\|community`, `format=json\|tsv\|csv` | `versions` | List all plugins |
| `plugins:enabled` | `filter=core\|community`, `format=json\|tsv\|csv` | `versions` | List enabled plugins |
| `plugins:restrict` | `on` or `off` | — | Toggle restricted mode |
| `plugin` | `id=<plugin-id>` | — | Get plugin info |
| `plugin:enable` | `id=<id>`, `filter=core\|community` | — | Enable a plugin |
| `plugin:disable` | `id=<id>`, `filter=core\|community` | — | Disable a plugin |
| `plugin:install` | `id=<id>` | `enable` | Install community plugin |
| `plugin:uninstall` | `id=<id>` | — | Uninstall a plugin |
| `plugin:reload` | `id=<id>` | — | Reload plugin (for development) |

### Themes

| Command | Parameters | Flags | Description |
|---------|-----------|-------|-------------|
| `themes` | — | `versions` | List installed themes |
| `theme` | `name=<name>` | — | Show active theme or get theme info |
| `theme:set` | `name=<name>` | — | Set the active theme |
| `theme:install` | `name=<name>` | `enable` | Install a community theme |
| `theme:uninstall` | `name=<name>` | — | Uninstall a theme |

### CSS Snippets

| Command | Parameters | Flags | Description |
|---------|-----------|-------|-------------|
| `snippets` | — | — | List CSS snippets |
| `snippets:enabled` | — | — | List enabled CSS snippets |
| `snippet:enable` | `name=<name>` | — | Enable a snippet |
| `snippet:disable` | `name=<name>` | — | Disable a snippet |

**Notes:**
- Plugin IDs are the directory names, not display names. Use `plugins` to discover IDs.
- `plugin:install` fetches from the community plugin registry. Add `enable` to activate immediately.
- `plugin:reload` is useful during plugin development — it reloads without restarting Obsidian.
- `plugins:restrict on` enables restricted mode (disables all community plugins).

---

## Templates & Content Creation

| Command | Parameters | Flags | Description |
|---------|-----------|-------|-------------|
| `templates` | — | `total` | List available templates |
| `template:read` | `name=<template>`, `title=<title>` | `resolve` | Read template contents |
| `template:insert` | `name=<template>` | — | Insert template into the active file |
| `unique` | `name=<text>`, `content=<text>`, `paneType=tab\|split\|window` | `open` | Create a unique (Zettelkasten-style) note |
| `create` | `path=<path>`, `template=<name>` | — | Create a file from a template |

**Notes:**
- Templates come from the folder configured in Settings > Templates.
- `template:read` with `resolve` processes template variables (like `{{date}}`, `{{title}}`).
- `unique` creates a note with a timestamp-based unique name — useful for Zettelkasten workflows.

---

## Workspace & Tabs

### Workspaces

| Command | Parameters | Flags | Description |
|---------|-----------|-------|-------------|
| `workspace` | — | `ids` | Show current workspace layout tree |
| `workspaces` | — | `total` | List saved workspaces |
| `workspace:save` | `name=<name>` | — | Save current layout as a workspace |
| `workspace:load` | `name=<name>` | — | Load a saved workspace |
| `workspace:delete` | `name=<name>` | — | Delete a saved workspace |

### Tabs

| Command | Parameters | Flags | Description |
|---------|-----------|-------|-------------|
| `tabs` | — | `ids` | List open tabs |
| `tab:open` | `group=<id>`, `file=<path>`, `view=<type>` | — | Open a new tab |
| `recents` | — | `total` | List recently opened files |

---

## Publishing & Web

| Command | Parameters | Flags | Description |
|---------|-----------|-------|-------------|
| `publish:site` | — | — | Show publish site info (slug, URL) |
| `publish:list` | — | `total` | List published files |
| `publish:status` | — | `total`, `new`, `changed`, `deleted` | Show pending publish changes |
| `publish:add` | `file=<name>` or `path=<path>` | `changed` | Publish a file (or all changed files) |
| `publish:remove` | `file=<name>` or `path=<path>` | — | Unpublish a file |
| `publish:open` | `file=<name>` or `path=<path>` | — | Open published file in browser |
| `web` | `url=<url>` | `newtab` | Open a URL in Obsidian's web viewer |

**Notes:**
- Obsidian Publish is a paid feature. These commands only work if Publish is configured.
- `publish:add changed` publishes all files with pending changes at once.

---

## Command Palette & Hotkeys

| Command | Parameters | Flags | Description |
|---------|-----------|-------|-------------|
| `commands` | `filter=<prefix>` | — | List available command IDs |
| `command` | `id=<command-id>` | — | Execute an Obsidian command |
| `hotkeys` | `format=json\|tsv\|csv` | `total`, `verbose` | List all configured hotkeys |
| `hotkey` | `id=<command-id>` | `verbose` | Get the hotkey for a specific command |

**Notes:**
- `commands` lists IDs like `editor:toggle-bold`, `app:go-back`. Use these IDs with `command id=`.
- `command` lets you trigger any Obsidian action from the terminal — including those from plugins.
- Use `commands filter=editor` to find all editor-related commands.

---

## Outline & Metadata

| Command | Parameters | Flags | Description |
|---------|-----------|-------|-------------|
| `outline` | `file=<name>` or `path=<path>`, `format=tree\|md\|json` | `total` | Show heading outline |
| `wordcount` | `file=<name>` or `path=<path>` | `words`, `characters` | Count words and/or characters |
| `random` | `folder=<path>` | `newtab` | Open a random note |
| `random:read` | `folder=<path>` | — | Read a random note (prints path + content) |
| `vault` | `info=name\|path\|files\|folders\|size` | — | Show vault info |
| `vaults` | — | `total`, `verbose` | List all known vaults |

---

## Developer Commands

| Command | Parameters | Flags | Description |
|---------|-----------|-------|-------------|
| `devtools` | — | — | Toggle Electron developer tools |
| `dev:debug` | `on` or `off` | — | Attach/detach Chrome DevTools Protocol debugger |
| `dev:cdp` | `method=<CDP.method>`, `params=<json>` | — | Run a CDP command |
| `dev:errors` | — | `clear` | Show or clear JavaScript errors |
| `dev:screenshot` | `path=<filename>` | — | Take a screenshot (returns base64 PNG) |
| `dev:console` | `limit=<n>`, `level=log\|warn\|error\|info\|debug` | `clear` | Show console messages |
| `dev:css` | `selector=<css>`, `prop=<name>` | — | Inspect computed CSS with source locations |
| `dev:dom` | `selector=<css>`, `attr=<name>`, `css=<prop>` | `total`, `text`, `inner`, `all` | Query the DOM |
| `dev:mobile` | `on` or `off` | — | Toggle mobile emulation |
| `eval` | `code=<javascript>` | — | Execute JavaScript and return result |

**Notes:**
- These commands are useful for plugin development, theming, and debugging.
- `eval` runs arbitrary JavaScript in Obsidian's renderer process — handle with care.
- `dev:screenshot` without `path=` returns base64-encoded PNG to stdout.
- `dev:cdp` gives direct access to the Chrome DevTools Protocol for advanced automation.
