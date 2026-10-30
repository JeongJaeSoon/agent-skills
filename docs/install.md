# Installing agent-skills

English · [日本語](install.ja.md) · [한국어](install.ko.md)

This guide is for a person installing the plugin by hand. To have an agent do it, give it [INSTALL.md](../INSTALL.md), which holds the same steps with the rules the agent follows.

There are three ways in:

- **A. Marketplace only.** The plugin as published. Good for using the skills without changing them.
- **B. Full setup from a session.** A live checkout of this repo, the recommended user settings, a sync job and the default automations. It starts with A and finishes in the next session.
- **C. Full setup from a shell.** The same result as B without a Claude Code session.

The outputs shown come from a run in a throwaway environment (temporary `HOME`, `CLAUDE_CONFIG_DIR` and `CLAUDE_CODE_PLUGIN_DIRS`) on Claude Code 2.1.314. That run covered path A, the marketplace uninstall, and path C up to `--settings-only --apply`. It did not run the steps that install the sync job and automations (`--actions-only`, a full `--apply`) or the `launchctl` and automation rows under Uninstall, because launchd is shared with the real user.

## Prerequisites

| | What | Why |
|---|---|---|
| Required | Claude Code (checked on 2.1.314) | The plugin loads only there |
| Required | `git` | Install, checkout and sync |
| Required | `python3` 3.9 or later | Every script. macOS's `/usr/bin/python3` (3.9.6) works |
| Required for B and C | `gh`, logged in to github.com | The recommended rules about your own repositories, and cloning a private personal repo |
| For B and C | macOS | The sync job and some automations are LaunchAgents. On Linux, setup prints a cron line for you to add, and those automations show as `unsupported` |
| Optional | Orca | The multi-session skills (`orchestrate`, `dispatch-card`, `handoff-ticket`) and the scheduled rounds |
| Optional | Linear or Jira | Tracker skills. Linear unless `tracker.adapter` says otherwise |
| Optional | Obsidian or a Markdown folder | Notes skills. Off until `notes.adapter` is set |
| Optional | tailscale, caddy | `preview-watch` and `share-server`, both off by default |

## Path A: marketplace only

Inside a Claude Code session:

```
/plugin marketplace add JeongJaeSoon/agent-skills
/plugin install agent-skills@jeongjaesoon
```

The install dialog asks for three values. All are optional:

- `language`: the language for replies.
- `checkout`: where path B clones this repo (default `~/src/agent-skills`).
- `personal_repo`: a private repo holding your `agent-skills.json`. Do not put a token in the URL; the value is stored in `settings.json`.

The same from a shell, with the values as `--config`:

```bash
claude plugin marketplace add JeongJaeSoon/agent-skills
claude plugin install agent-skills@jeongjaesoon --config language=English --config checkout=$HOME/src/agent-skills
```

```
✔ Successfully added marketplace: jeongjaesoon (declared in user settings)
✔ Successfully installed plugin: agent-skills@jeongjaesoon (scope: user)
1 userConfig option not yet set — run /plugin configure agent-skills@jeongjaesoon in Claude Code, or pass --config KEY=VALUE.
```

The last line only says one value was left blank. Start a new session: plugins load at session start. Each new session offers to run `setup-pc` until you accept or decline. Say yes for path B; declining stops the offer for that profile.

## Path B: full setup from a session

1. Do path A, then start a new session and accept the `setup-pc` offer (or ask "set up agent-skills on a new PC").
2. If `gh` is not logged in, run `! gh auth login`. The agent does not log in for you.
3. The agent clones the checkout and your personal repo, runs a dry run and shows a diff per file. Nothing is written yet. Read the diff.
4. Paste the one line it gives you: `! python3 -I <checkout>/scripts/setup.py --settings-only … --apply <hash>`. The hash covers the whole plan, so if anything changed since the diff, it refuses and you dry run again. It prints a rollback line per file.
5. The agent runs `setup.py --actions-only`. A permission dialog appears: this step installs the sync job and turns on the default automations, and writes no Claude settings. Approve it.
6. Restart the session. Do not use `/reload-plugins` here: in a session started before the apply, it drops the marketplace copy without loading the checkout, and leaves you with no agent-skills until you restart.

## Path C: full setup from a shell

```bash
git clone https://github.com/JeongJaeSoon/agent-skills ~/src/agent-skills
python3 ~/src/agent-skills/scripts/setup.py --language English       # dry run: a diff per file and the --apply line
python3 -I ~/src/agent-skills/scripts/setup.py --language English --apply <hash>
```

- The checkout must be the main checkout on branch `main`. A worktree or another branch is refused.
- The dry run writes nothing. Its first line, `target <path>`, is the `settings.json` it will write. Its last line is the exact `--apply` command, starting with `! ` for pasting into a Claude Code session; in a shell, drop the `!`.
- Without `--settings-only`, `--apply` also installs the sync job, runs one sync and turns on the default automations, then prints the check table.
- If the marketplace copy is installed, the diff turns it off (`enabledPlugins` to `false`). With both on, every hook runs twice. It is the one existing value setup changes.

Options, the personal repo layout and what each recommended setting does are in [new-pc.md](new-pc.md) (Korean).

## Minimal configuration

Nothing is required. Without `~/.claude/agent-skills.json`, each feature is off or asks the first time it is needed; none guesses a destination. Path B and C add two keys (`reflect.skill_repository`, `reap.user_folders: []`).

An example that sets a tracker and a notes folder:

```json
{
  "tracker": {"adapter": "jira"},
  "notes": {"adapter": "markdown", "markdown": {"root": "~/notes"}}
}
```

Without `tracker.adapter`, the tracker skills use Linear. The check table lists `tracker.adapter` as `info` when it is unset, which is not an error. Every key and its default is in [configuration-migration.md](configuration-migration.md) (Korean) and in each skill's configuration section; the `skills-config` skill changes them for you.

## Check

`claude plugin list` after path A:

```
  ❯ agent-skills@jeongjaesoon
    Version: <commit>
    Scope: user
    Status: ✔ enabled
```

After path B or C, the checkout is loaded and the marketplace copy is off:

```
  ❯ agent-skills@jeongjaesoon
    Status: ✘ disabled

Session-only plugins (--plugin-dir / --plugin-url):
  ❯ agent-skills@inline
    Path: <checkout>
    Status: ✔ loaded
```

Then `python3 <checkout>/skills/setup-pc/scripts/setup_check.py check`, which is read-only. Rows marked `missing`, `differs` or `warn` are left to do, and the last column says how. Commit signing and `gh auth login` are always yours.

In a session, `/agent-skills:brief` should answer with a status report. Bare `/brief` is a Claude Code built-in.

## Update

- **Marketplace copy:** `claude plugin marketplace update jeongjaesoon`, then `claude plugin update agent-skills@jeongjaesoon`, then `/reload-plugins` in running sessions.
- **Checkout:** the sync job fast-forwards it every 15 minutes, so the skills stay current on their own. New recommended settings come from `python3 <checkout>/scripts/setup.py --update` (a dry run) and the `--apply` line it prints, or ask a session to "update agent-skills". If you keep a personal repo, `git -C ~/.config/agent-skills/personal pull --ff-only` first; nothing else pulls it.

## Safety rails

Where a human approves (HITL, human in the loop) and where the agent goes ahead while you watch (HOTL, human on the loop), by default, and how to change it.

| Point | Default | How to change it |
|---|---|---|
| Applying user settings (`setup-pc`, `tune-automode`, `skills-config`) | HITL: the agent writes only a diff and a `!` line; you run it | Cannot be changed |
| `setup.py --actions-only` (sync job and automations) | HITL: the `guard.py` hook shows a permission dialog even in auto mode | Cannot be changed |
| Other permission dialogs | HITL: you approve | Add a `permissions.allow` entry and that command goes ahead without asking (`skills-config` for memory paths and MCP tool names in user settings, `update-config` for Bash rules and project settings) |
| A denial by the auto-mode classifier | HITL: the agent reports and stops | Apply an autoMode rule drafted by `tune-automode` and that kind of action goes ahead. A denial with no verdict cannot be turned into a rule |
| Irreversible acts (destructive migrations, force-pushing a shared branch, production deploys, sending things outside) | HITL: the agent stops and asks | Cannot be changed |
| `orchestrate` merge policy | HOTL: `autonomous`; workers merge through `orch land`, when an autoMode rule allows merging in that repo (otherwise they ask) | With `human-gate`, workers stop at READY and you release the gate |
| A merge by `deliver-ticket` on a single card | HOTL: a ready PR is merged without asking, when an autoMode rule allows that merge (otherwise it asks) | Put a hold on it or say you will look first, and it asks before merging. It also asks on exceptions such as a check that keeps flaking or an irreversible migration |
| `watch-prs` `ownergate` (approving bot PRs) | Off. Add it to `prs.kinds` with gate entries in `prs.ownergate` and it is HITL: you approve with a `!` line | Outside production, and when an autoMode allow rule covers it, the round runs it (HOTL) |
| `watch-prs` `ready_to_merge` | Off | Add it to `prs.kinds` and your PRs in `merge_repos` that pass every gate are merged (HOTL) |
| Filling secrets with `secure-fill` | HITL: Touch ID every time | Set an allowlist entry to `"approval": "none"` and it fills without Touch ID (an item created with `-T ""` still gets the Keychain prompt) |
| `janitor`, `pressure-watch` | HOTL: finished resources agents left behind are cleaned up on a schedule | `<checkout>/bin/skills-sync setup disable <name>` |

Under HOTL you watch through `brief` status reports and the dashboard's decisions, permission dialogs and inbox. The dashboard, `janitor` and the watch rounds assume Orca. More in [operations.md](operations.md#안전-경계) (Korean).

## Uninstall

The marketplace copy:

```bash
claude plugin uninstall agent-skills@jeongjaesoon
claude plugin marketplace remove jeongjaesoon
```

This also clears its `enabledPlugins`, `extraKnownMarketplaces` and `pluginConfigs` entries.

A full setup (path B or C) also needs:

| What setup changed | How to undo it |
|---|---|
| `env.CLAUDE_CODE_PLUGIN_DIRS` and the other recommended settings in `settings.json` | Run the rollback line `--apply` printed (`cp settings.json.bak-setup-<time> settings.json`), or delete the keys you do not want. Restart running sessions |
| The sync job (macOS) | `launchctl bootout gui/$(id -u)/io.github.jeongjaesoon.agent-skills-sync`, then delete `~/Library/LaunchAgents/io.github.jeongjaesoon.agent-skills-sync.plist` |
| The automations | `<checkout>/bin/skills-sync setup disable <name>` for each; for a LaunchAgent one it also unloads the agent. `<checkout>/bin/reap-hard uninstall` and `<checkout>/bin/orch-dash uninstall-agent` if you installed those by hand |
| State | Delete `~/.local/state/agent-skills/` |

Two things are left behind: the Orca automation registrations, which `disable` turns off but does not delete, and `~/.claude/agent-skills.json`, which no step deletes; delete it yourself if you want it gone.

## Troubleshooting

- **The skills vanished after `/reload-plugins`.** The session started before the full setup was applied. Restart it.
- **Setup wrote a different `settings.json` from the one my session reads.** Claude Code reads `$CLAUDE_CONFIG_DIR/settings.json` when that variable is set (Orca sets one per profile), else `~/.claude/settings.json`. The dry run's first line, `target <path>`, says which one setup plans for; `--settings <file>` picks another. With several profiles, do the setup once per profile. `agent-skills.json` is one file for all of them.
- **`refused: … is a worktree` or `… is on 'feature'`.** Run setup from the main checkout on `main`.
- **Hooks run twice.** Both the marketplace copy and a checkout are enabled. Run the setup dry run; its diff turns the marketplace copy off.
- **Linux.** Add the cron line setup prints. LaunchAgent automations are unsupported.
