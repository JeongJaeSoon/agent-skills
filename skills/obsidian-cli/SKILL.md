---
name: obsidian-cli
description: >
  Expert guide for the Obsidian CLI (command-line interface, v1.12+). Helps users construct correct
  CLI commands, write shell scripts and automation workflows, choose the right command from 100+
  available commands, and troubleshoot setup issues. Use this skill whenever users mention Obsidian CLI,
  automating Obsidian from the terminal, scripting vault operations, managing notes/tasks/plugins/themes
  via command line, daily notes CLI, vault search from terminal, or Obsidian CLI setup and troubleshooting.
  Also use when users want to interact with Obsidian programmatically, pipe vault data to other tools,
  or build integrations around Obsidian — even if they don't explicitly say "CLI".
---

# Obsidian CLI

You are an expert on the Obsidian CLI. Help users construct commands, write automation scripts, and troubleshoot issues.

## How the CLI works

Obsidian CLI is a **client that talks to a running Obsidian app**. Commands are not standalone — they send instructions to the Obsidian desktop application, which must be running (the CLI will launch it if needed). This means:

- Every command operates on a real vault that Obsidian has open
- Changes made via CLI (creating files, setting properties, toggling plugins) are immediately reflected in the GUI
- If Obsidian isn't running, the CLI starts it first, which adds a brief delay

## Command syntax

Obsidian CLI uses its own syntax — it does **not** follow POSIX/GNU conventions:

```
obsidian [vault=<name|id>] <command> [param=value ...] [flags]
```

**Key rules:**
- **Parameters** use `param=value` (no dashes, no spaces around `=`)
- **Flags** are bare words that act as boolean switches (e.g., `total`, `open`, `overwrite`)
- **Subcommands** use colon notation: `daily:append`, `sync:status`, `property:set`
- **Multiline content** uses `\n` for newlines and `\t` for tabs
- **Clipboard**: append `--copy` to any command to copy its output
- **Vault targeting**: prefix with `vault=MyVault` when you have multiple vaults

**`file=` vs `path=`**: The `file=` parameter matches by filename (without extension), while `path=` matches the full path from vault root. When a file name is unique, `file=` is simpler. Use `path=` when files share names across folders.

### Examples

```bash
# Create a note with content
obsidian create name="Meeting Notes" path="Work/Meetings" content="## Agenda\n- Item 1\n- Item 2" open

# Append a task to today's daily note
obsidian daily:append content="- [ ] Review PR #42" inline

# Search across the vault, output as JSON
obsidian search query="project alpha" format=json limit=20

# Set a property on a specific file
obsidian property:set name="status" value="done" type=text file="My Note"

# Target a specific vault
obsidian vault=Work daily:read

# List all orphan notes (no incoming links)
obsidian orphans total

# Install and enable a community plugin
obsidian plugin:install id=dataview enable
```

## Command reference

For the full catalog of 100+ commands organized by category, read `references/commands.md` in this skill's directory. Categories include:

1. **General** — `help`, `version`, `reload`, `restart`
2. **File & Folder** — `create`, `read`, `append`, `prepend`, `open`, `move`, `rename`, `delete`, `file`, `files`, `folder`, `folders`
3. **Daily Notes** — `daily`, `daily:path`, `daily:read`, `daily:append`, `daily:prepend`
4. **Search & Links** — `search`, `search:context`, `backlinks`, `links`, `unresolved`, `orphans`, `deadends`
5. **Tags & Properties** — `tags`, `tag`, `properties`, `property:set`, `property:remove`, `property:read`, `aliases`
6. **Tasks** — `tasks`, `task`
7. **Bookmarks & Bases** — `bookmarks`, `bookmark`, `bases`, `base:views`, `base:create`, `base:query`
8. **History & Sync** — `diff`, `history`, `history:read`, `history:restore`, `sync`, `sync:status`, `sync:history`, etc.
9. **Plugins & Themes** — `plugins`, `plugin:install`, `plugin:enable`, `themes`, `theme:set`, `snippets`, etc.
10. **Templates** — `templates`, `template:read`, `template:insert`, `unique`
11. **Workspace & Tabs** — `workspaces`, `workspace:save`, `workspace:load`, `tabs`, `tab:open`, `recents`
12. **Publishing** — `publish:site`, `publish:list`, `publish:status`, `publish:add`, `publish:remove`
13. **Command Palette & Hotkeys** — `commands`, `command`, `hotkeys`, `hotkey`
14. **Outline & Metadata** — `outline`, `wordcount`, `random`, `vault`, `vaults`
15. **Developer** — `devtools`, `dev:console`, `dev:dom`, `dev:css`, `dev:screenshot`, `eval`, etc.

When a user asks about a specific command or category, read the relevant section from the reference file so you can give precise parameter details.

## Writing automation scripts

When helping users build shell scripts around the Obsidian CLI, keep these patterns in mind:

### Capturing output
Most commands support `format=json` for machine-readable output, which is ideal for piping to `jq` or other tools:

```bash
# Get all tags as JSON and extract the top 5 by count
obsidian tags format=json counts sort=count | jq '.[0:5]'

# List incomplete tasks in JSON
obsidian tasks todo format=json
```

### Composing commands
The CLI is designed for Unix-style composition. Common patterns:

```bash
# Morning routine: open daily note and append a template
obsidian daily open
obsidian daily:append content="## Morning checklist\n- [ ] Review inbox\n- [ ] Plan priorities"

# Batch-set a property across files in a folder
obsidian files folder="Projects/Active" | while read -r f; do
  obsidian property:set name="status" value="active" path="$f"
done

# Weekly review: find orphan notes created this week
obsidian orphans | while read -r f; do
  echo "Orphan: $f"
done
```

### Quoting and escaping
Content with spaces, special characters, or newlines needs shell quoting:

```bash
# Wrap content in quotes; use \n for newlines
obsidian append file="Journal" content="Had a great day.\n\nKey takeaway: keep it simple."

# Single quotes to avoid shell expansion
obsidian create name='$dollar signs' content='Price: $100'
```

## Platform setup & troubleshooting

### macOS
- Registration adds the binary path to `~/.zprofile`
- If you use Bash or Fish instead of Zsh, manually add the path to `~/.bash_profile` or `~/.config/fish/config.fish`
- After enabling, restart your terminal or run `source ~/.zprofile`

### Windows
- Requires Obsidian installer version 1.12.4+
- The installer places `Obsidian.com` (a terminal redirector) in the installation folder
- If `obsidian` isn't recognized, ensure the installation folder is in your `PATH`

### Linux
- Creates a symlink at `/usr/local/bin/obsidian`
- **AppImage**: may need a manual symlink — `ln -s /path/to/Obsidian.AppImage /usr/local/bin/obsidian`
- **Snap**: set `XDG_CONFIG_HOME="$HOME/snap/obsidian/current/.config"` before running
- **Flatpak**: create a symlink to the Flatpak-exported binary

### Common issues
- **"Command not found"**: Restart your terminal after enabling CLI in Settings > General. Verify your shell config file has the right PATH entry.
- **Commands hang or time out**: Obsidian must be running. The CLI will attempt to launch it, but this can be slow on first run.
- **Wrong vault**: Use `obsidian vaults` to list known vaults and their IDs, then target with `vault=<name>`.
- **Installer version**: Run `obsidian version` — CLI requires 1.12+. Update the installer (not just the app) if needed.

## TUI (interactive) mode

Running `obsidian` with no arguments enters the Terminal UI — an interactive REPL with autocomplete, command history, and reverse search (`Ctrl+R`). This is great for exploring commands. Key shortcuts:

- `Tab` — autocomplete
- `Up/Down` — command history
- `Ctrl+R` — reverse search
- `Ctrl+C` / `Ctrl+D` — exit

## Guidelines for helping users

1. **Always give the exact command** — don't just describe what to do; write the actual `obsidian ...` invocation they can paste into their terminal.
2. **Prefer `format=json`** when the user wants to process output programmatically.
3. **Use `file=` for simple cases**, `path=` when disambiguation is needed.
4. **Warn about destructive operations** — `delete permanent` bypasses trash. Mention this if relevant.
5. **Check platform** — if the user mentions setup issues, ask what OS they're on so you can give platform-specific guidance.
6. **Mention the TUI** — if a user seems to be exploring or learning, suggest they try the interactive mode.
