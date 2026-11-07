# agent-skills

English · [日本語](README.ja.md) · [한국어](README.ko.md)

Claude Code skills that take a ticket to a merged PR, with you deciding where a human approves.

[![License: MIT](https://img.shields.io/github/license/JeongJaeSoon/agent-skills)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/JeongJaeSoon/agent-skills)](https://github.com/JeongJaeSoon/agent-skills/releases/latest)

## What it is

agent-skills is a Claude Code plugin: 39 skills, 4 agent definitions, 16 hooks and a few commands. It covers two flows. One takes a single ticket from the first edit to a merged PR and a closed ticket. The other runs a whole project on several Orca workers at once, one ticket each, and lands their PRs in order.

It is for developers who hand ticket work in GitHub repositories to Claude Code and want to choose, point by point, whether a human approves each step (human in the loop, HITL) or the agent goes ahead while the human watches and steps in on exceptions (human on the loop, HOTL). Writes to your user settings and irreversible actions always wait for you.

macOS with [Orca](https://github.com/stablyai/orca) is the primary environment. The single-ticket skills work anywhere Claude Code runs. The multi-session skills, the dashboard and the scheduled rounds need Orca, and some automations need macOS.

## Explainer video

A 4-minute overview of the parts, the two flows and where you stay in charge.

https://github.com/user-attachments/assets/1e240200-0d28-4f8b-b13c-ae25a68984f9

Subtitles: [English (.srt)](docs/video/agent-skills-explainer-en.srt). The video is also available in [Japanese](https://github.com/user-attachments/assets/206c4624-dca8-448d-8179-69c08ecc511c) and [Korean](https://github.com/user-attachments/assets/4989f7f2-54d3-493a-8692-e0a4157199b0).

## What you get

- **One ticket, start to finish.** `deliver-ticket` plans, writes tests, gets a review from someone other than the author, opens the PR, waits for CI, merges, and closes the ticket with evidence for each acceptance criterion.
- **A project on many workers.** `orchestrate` gives each Orca worker one ticket and keeps a program ledger. PRs merge only through the `orch land` gate or a merge you approve, and PRs that touch shared files merge one at a time.
- **A place to watch.** `brief` reports what is in progress, waiting on others, waiting on you, and on hold. The `orch-dash` dashboard shows open decisions, permission prompts and an inbox.
- **A refusal gets one honest retry.** An action denied by a permission prompt, the auto-mode classifier, a hook or a person is reported. It runs again once, in the same form, only after you allow it in that session's chat or add a rule, or after the agent adds evidence that answers a classifier's or hook's stated reason. Evidence never answers a person's refusal or a production sync. A second refusal stops it, and no other session, script or tool may do it instead.
- **No config, no guessing.** Notes, the lessons ledger, personal folders and chat channels are used only where your config names them. A missing key turns the feature off or makes the skill ask.
- **A clean machine.** `reap-resources` finds processes, worktrees and containers that finished sessions left behind, and removes only what it listed.

## Requirements

Required:

- Claude Code
- `git`
- `python3` 3.9 or later (macOS's `/usr/bin/python3` works)

Full setup only:

- `gh`, logged in to github.com

Optional:

- [Orca](https://github.com/stablyai/orca), for the multi-session skills and scheduled rounds
- Linear or Jira, for the tracker skills (Linear unless configured otherwise)
- Obsidian or a plain Markdown folder, for the notes skills
- tailscale and caddy, for `preview-watch` and `share-server`

The full setup turns on the sync job and the default automations as Orca automations; it creates no LaunchAgent. They run while the Orca app is open. If an older setup left LaunchAgents, `setup-pc` reports them and you remove them yourself.

## Install

### Ask your agent

Paste this into a Claude Code session:

```text
Set up the Claude Code plugin at https://github.com/JeongJaeSoon/agent-skills for me. Read https://raw.githubusercontent.com/JeongJaeSoon/agent-skills/main/INSTALL.md and follow it: check my environment first, ask me before each choice, and give me any command I have to run myself. Don't edit my settings files directly.
```

[INSTALL.md](INSTALL.md) is the procedure the agent follows. It checks your environment, then asks whether you want the marketplace plugin only or the full setup, which values to set (reply language, checkout path, personal config repo), and for your approval of the two commands it runs.

### Do it yourself

1. In a Claude Code session, add the marketplace and install the plugin:

   ```text
   /plugin marketplace add JeongJaeSoon/agent-skills
   /plugin install agent-skills@jeongjaesoon
   ```

   The install dialog asks for a reply language, a checkout path (default `~/src/agent-skills`) and a personal config repo URL. All three can stay empty.
2. Start a new session. Plugins load at session start, so `/reload-plugins` is not enough here.
3. Each new session offers `setup-pc` until you accept or decline. Declining stops the offer for that profile. Accept it for the full setup: the agent clones the checkout and shows a diff for each settings file. It never writes them itself. You read the diff and paste the `! python3 ... --apply <hash>` line it gives you.
4. Approve the permission prompt that turns on the sync job and automations, then restart the session.

The full guide, with a shell-only path, updates, the complete table of approval points, and uninstalling, is [docs/install.md](docs/install.md).

## First steps

Skills fire on their own when the situation in their description comes up. You can also call one by name as `/agent-skills:<name>`.

- "Finish ABC-1" runs `deliver-ticket` from the plan to the merge and the closed ticket. This needs a tracker: Linear by default, or set `tracker.adapter` (see [Configuration](#configuration)).
- "Give me a status report" runs `brief`. Claude Code has a built-in `/brief`, so call this one directly as `/agent-skills:brief`. `/brief-pane` opens the same report in a panel beside the chat that refreshes every 30 seconds while it is open (it docks at 110+ columns; narrower terminals show it above the prompt).
- In Orca, start a top-level session with `claude --agent agent-skills:orchestrator --name orchestrator` and ask it to take over every session. The orchestrator hands each project to a lead session, and each lead runs its workers. Once the orchestrator session has auto-compacted 3 times, a compaction stopped shrinking its context, or two came less than 30 minutes apart (`orchestrate.compact_restart`, on by default; `restart_at` is an optional token ceiling, off unless set), and nothing waits on it, a band above the prompt says `/clear` is safe; the next session picks up its role and a saved handoff. `restart_at` no longer defaults to 130k, so a setup that relied on that default is now prompted by compaction history instead. Leads rely on auto-compact and get no band; a lead's `orch handoff save` goes to `lead:<slug>`, and the orchestrator's key is refused to it. Typing `/clear` stays yours: `orchestrate.auto_clear: true` lets the session type it when the ceiling is passed, and `orchestrate.compact_restart.auto_clear: true` when the compaction rules fire.

## Where you stay in charge

- **Your settings.** `setup-pc`, `tune-automode` and `skills-config` produce a diff and a `!` command. You run it. This cannot be turned off.
- **Prompts and denials.** A permission prompt waits for you. When the auto-mode classifier denies an action, the agent reports and stops. To let a kind of action through from then on, you add a `permissions.allow` entry or an autoMode rule that `tune-automode` drafts.
- **Irreversible actions.** Destructive migrations, force-pushing a shared branch, production deploys and sending anything outside always stop and ask.
- **Card work habits.** A dispatched card that writes a file from Bash (`sed -i`, a redirect), chains a commit or `rm` with other commands, edits outside its worktree or in a main checkout, writes your auto-memory, or pushes before any test ran gets a warning line and a row in `turn-warnings.jsonl`. Nothing is blocked; the `worker.*` keys in `docs/skills.en.md` turn each check off or up to `deny`.
- **Merges.** An `orchestrate` card lands its own PR through `orch land` when its first prompt approves the merge, then closes itself; a repo with no CI needs `orchestrate.no_ci_repos` first. `deliver-ticket` outside a program merges on its own only when an autoMode rule allows merging in that repo. Without one it asks. The `human-gate` policy, or saying you will look first, makes them stop before every merge.
- **Scheduled cleanup.** `janitor` and `pressure-watch` clean up after finished agents on a schedule. Turn either off with `skills-sync setup disable <name>`. Auto-approving or auto-merging your PRs in `watch-prs` stays off until you configure it.

Every approval point with its default and how to change it is in [docs/install.md](docs/install.md#safety-rails).

## Skills

When each skill fires, what it does and the files it ships are in the [skill catalog](docs/skills.en.md).

| Group | Skills | What they do |
|---|---|---|
| [One ticket](docs/skills.en.md#one-ticket) | `write-ticket`, `deliver-ticket`, `ui-review`, `dispatch-card`, `end-session` | Write a ticket, deliver it, hand the next one to a fresh card, record progress and close the session |
| [One project](docs/skills.en.md#one-project-several-orca-workers) | `orchestrate`, `reboot` | Land many tickets in parallel on Orca workers, put back what a restart dropped, then measure how it actually went (`reflect`'s measure mode) |
| [One machine](docs/skills.en.md#one-machine) | `reap-resources`, `secure-fill`, `tune-automode`, `skills-config`, `setup-pc` | Clean up leftovers, fill secrets the agent never sees, draft autoMode rules, change config keys, install and update |
| [Chat and reviews](docs/skills.en.md#chat-and-reviews) | `watch-mentions`, `watch-prs` | Turn chat and PRs that concern you into work |
| [Adapters](docs/skills.en.md#adapters) | `use-tracker`, `use-notes` | Linear and Jira tickets, Obsidian and Markdown notes |
| [Writing](docs/skills.en.md#writing) | `brief`, `map-features`, `write-plainly`, `prune-comments`, `write-skill` | Status reports, stage tables, to-do and hold queues (`brief` modes), feature maps, plain prose, comment cleanup, skill authoring checks |
| [pstack](docs/skills.en.md#pstack-skills-lauren-tan-mit) | `architect`, `challenge`, `fan-out`, `figure-it-out`, `how`, `principles`, `recall`, `reflect`, `show-me-your-work`, `tdd`, `teach`, `verification-skill`, `why` | Design, comparison, impact analysis, verification, explanation, retrospectives |

The pstack group comes from Lauren Tan's [pstack](https://github.com/cursor/plugins) (MIT). These skills are now maintained here on their own: `vendor/pstack` keeps the original files as upstream had them, and an optional daily job reports upstream changes, which are ported by hand when worth it. With several sessions, two layers sit above the workers: the orchestrator (one top-level session) and leads (one per project). See the [Roles table in orchestrate](skills/orchestrate/SKILL.md#roles).

## Renamed skills

Some skills were merged into another skill as a mode. The old name is gone, so `/agent-skills:<old name>` no longer runs anything: use the new command.

| Old command | New command / mode |
|---|---|
| `/agent-skills:measure-delivery` | `/agent-skills:reflect`, measure mode |
| `/agent-skills:swarm` | `/agent-skills:fan-out`, sweep mode |
| `/agent-skills:arena` | `/agent-skills:fan-out`, compete mode |
| `/agent-skills:handoff-ticket` | `/agent-skills:dispatch-card`, next-ticket mode |
| `/agent-skills:create-verification-skill` | `/agent-skills:verification-skill`, create mode |
| `/agent-skills:maintain-verification-skill` | `/agent-skills:verification-skill`, maintain mode |
| `/agent-skills:blast-radius` | `/agent-skills:challenge`, blast-radius mode |
| `/agent-skills:interrogate` | `/agent-skills:challenge`, interrogate mode |
| `/agent-skills:stage` | `/agent-skills:brief`, stage table mode (`/agent-skills:brief 단계표 보여줘`) |
| `/agent-skills:todo` | `/agent-skills:brief`, to-do mode (`/agent-skills:brief 할 일 보여줘`) |

## Configuration

Nothing is required. Skills read their keys from `~/.claude/agent-skills.json`, one section per skill, and lists and secrets from `~/.config/agent-skills/`. When a key is missing, the feature is off or the skill asks before acting. The tracker adapter is Linear unless `tracker.adapter` says otherwise. The `skills-config` skill writes keys for you as a script you run. Keys and their defaults are in [docs/configuration-migration.md](docs/configuration-migration.md) (Korean).

## Documentation

- [docs/install.md](docs/install.md): install, update, approval points, uninstall, troubleshooting
- [INSTALL.md](INSTALL.md): the install procedure an agent follows
- [docs/skills.en.md](docs/skills.en.md): skill catalog
- [docs/operations.md](docs/operations.md) (Korean): terms, scheduled automations, usage scenarios, safety rails and hooks, changing a skill
- [docs/new-pc.md](docs/new-pc.md) (Korean): setting up a new PC in depth
- [docs/automation.md](docs/automation.md) (Korean): which mechanism runs which job
- [docs/platform.md](docs/platform.md) (Korean): how the checkout is loaded and synced
- [docs/orca-migration.md](docs/orca-migration.md): what moved from orch to Orca on 2026-11-01, running beside older sessions, the switch-over order and how to undo it
- [docs/token-usage.md](docs/token-usage.md) (Korean): measuring the tokens this plugin adds to context and the tokens each feature used

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for setting up a checkout and the rules for this public repo. Agents working in the repo also read [AGENTS.md](AGENTS.md). Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md), not in a public issue.

## Tests

There is no CI. Run every test file before sending a change:

```bash
for t in skills/orchestrate/scripts/test_*.py skills/use-tracker/scripts/test_tracker.py skills/reflect/scripts/test_measure.py skills/reap-resources/scripts/test_reap.py skills/reap-resources/scripts/test_cards.py skills/reap-resources/scripts/test_pressure.py skills/watch-mentions/scripts/test_mentions.py skills/watch-prs/scripts/test_prs.py skills/watch-prs/scripts/test_review_wait.py skills/watch-prs/scripts/test_notify.py skills/secure-fill/scripts/test_secure_fill.py skills/tune-automode/scripts/test_automode_rule.py skills/skills-config/scripts/test_config_emit.py skills/setup-pc/scripts/test_setup_check.py scripts/test_setup.py skills/brief/scripts/test_table_width.py skills/reflect/scripts/test_lesson_ids.py skills/deliver-ticket/scripts/test_shots.py skills/deliver-ticket/scripts/test_review_pick.py skills/ui-review/scripts/test_ui_measure.py skills/reboot/scripts/test_reboot.py hooks/test_*.py scripts/test_sync.py scripts/test_card.py scripts/test_preview_watch.py scripts/test_automations.py scripts/test_share_server.py scripts/test_reap_hard.py scripts/test_public_shapes.py scripts/test_skill_tokens.py scripts/test_allowed_tools.py; do python3 "$t"; done
bash scripts/pstack-sync-test.sh
```

## License

MIT. `vendor/pstack` and the skills taken from it keep their original author's MIT license (`vendor/pstack/LICENSE`).
