---
name: setup-pc
description: "Use when agent-skills goes onto another PC or an installed PC needs bringing up to date — \"새 PC 세팅해줘\", \"다른 PC 에서도 똑같이 쓰게 해줘\", \"agent-skills 설치 점검\", \"agent-skills 업데이트해줘\", \"플러그인 최신화\", \"set up agent-skills on a new PC\", \"update agent-skills\". Dry-runs `scripts/setup.py` (a diff per file, nothing written) and hands the human its one `--apply <hash>` line, which writes the settings and turns on sync; the check alone is read-only. A single auto-mode denial is tune-automode."
---

# Set up or update agent-skills on a PC

Installing the plugin brings the skills; it does not bring the user settings, the minimal `agent-skills.json` keys, the user's personal config or the automations. `scripts/setup.py` in the checkout (`${CLAUDE_SKILL_DIR}/../../scripts/setup.py`) does it from the repo's recommended defaults (`scripts/recommended-settings.json`) and the user's personal folder, in two halves: the files, which a dry run shows as a diff and the human applies with one `--apply <hash>` line, and the steps that write no Claude settings (`--actions-only`), which this skill runs behind a permission dialog. What each setting does is in `docs/new-pc.md`, and why the line is split there in `docs/zero-command-setup.md`; this skill does not repeat them.

## What this skill never does

- Write `~/.claude/settings.json`, `~/.claude/agent-skills.json` or anything else under `~/.claude`, by any tool, and never run `setup.py --apply`, `bootstrap.py --write` or `gh auth login`. Those are the human's `!` lines. The auto-mode classifier refuses an agent's edit to its own settings, and the human reading the diff is what authorizes the bundled rules.
- Run any setup step other than `python3 <checkout>/scripts/setup.py --actions-only [--orca-run]`, which the plugin's guard hook turns into a permission dialog. A dialog the human declines or a classifier denial is reported with the line for the human, never retried in another form.
- Edit the personal repo's files. It is the user's; this skill only clones it and fast-forwards it.
- Carry one PC's or one company's values: internal name lists, a personal `CLAUDE.md`, personal skills, agents or autoMode rules, and chat channels, tracker projects or notes paths from anywhere but the personal repo (step 4). Without them each feature keeps its default (off, or asks on first use).

| Change after setup | Owner |
|---|---|
| A rule for one action auto mode denied | `tune-automode` |
| `permissions.allow` or hooks beyond the bundle | `update-config` |
| What the periodic automations do | `reap-resources`; this skill only checks they are on |
| Reloading running sessions | `skills-sync broadcast` (never a raw `orca terminal send`) |

## Setup: a new PC

The plugin's SessionStart hook (`hooks/onboard.py`) starts this on a PC that was never set up: its paragraph carries the values the human gave the install dialog (`language`, `checkout`, `personal_repo`).

1. **Nothing installed yet?** The skill cannot fire. The human installs from inside a session, `/plugin marketplace add JeongJaeSoon/agent-skills` then `/plugin install agent-skills@jeongjaesoon`, so the install dialog asks for those values (the shell's `claude plugin install` never asks), and opens a new session.
2. **`gh` logged in first.** The rules about the user's repositories and the personal repo need it; if `gh auth status` fails, hand over `! gh auth login` and wait. **Language**: the install dialog's value, else ask (default: the system locale, which `setup.py` reads). Ask whether this session is the orchestrator's Orca terminal; if so, step 7 adds `--orca-run`, which binds this terminal to a new Run when it has none.
3. **The checkout.** At the `checkout` path (default `~/src/agent-skills`, expanded to an absolute path), clone `https://github.com/JeongJaeSoon/agent-skills` when nothing is there. An existing checkout is used as it is; a `refused:` line in step 5 says what the human fixes first.
4. **The personal folder.** With a `personal_repo`, `git clone <url> ~/.config/agent-skills/personal`, or `git -C ~/.config/agent-skills/personal pull --ff-only` when it is there. It holds the personal `agent-skills.json` and the tracker site rules Markdown that `tracker.rules_file` names (`docs/zero-command-setup.md`); personal agents and autoMode rules are not carried. Without one, skip this step: each personal feature keeps its default.
5. **Dry run** from the checkout: `python3 <checkout>/scripts/setup.py --settings-only --language <name>`. It writes nothing. Show the diff as printed; do not paraphrase rules away. A `refused:` line (a worktree, a branch other than `main`, `CLAUDE_CODE_PLUGIN_DIRS` pointing at another checkout, a malformed personal file) is fixed first, by the human.
6. **Hand over the one line** it printed: `! python3 <checkout>/scripts/setup.py … --apply <hash>`. It ends with a rollback line per file. A hash that no longer matches means something changed since the diff: dry run again.
7. **The steps**, once step 6 is applied: run `python3 <checkout>/scripts/setup.py --actions-only [--orca-run]` as one standalone command with the absolute path. The guard hook shows the human a dialog naming what it does: the sync job, the default-on automations (watch-prs and watch-mentions only when the personal `prs` and `mentions` keys are in, which step 6 put there), Orca, then the check table.
8. **Read the table** it ended with. `missing`, `differs` and `warn` rows are left to do; each row's `next` goes to the human as a `!` line or to the owner above. `gh auth login` and git commit signing are the human's.
9. **New session.** `CLAUDE_CODE_PLUGIN_DIRS` and the settings are read at start, so sessions running before the apply are restarted, not reloaded.

The human may refuse part of the bundle: they edit the rule out after applying (the rollback line restores the old file), and later runs do not offer it again.

## Update: an installed PC

1. Dry run `python3 <checkout>/scripts/setup.py --update`. Like any dry run it writes and fetches nothing: it plans the recommended items this PC has not been offered before from the checkout as it is, and lists the fast-forward as the `first` step.
2. Hand over the `--apply` line as in setup step 6. It fast-forwards the checkout first (`skills-sync sync`; stops on a dirty or diverged checkout, `docs/platform.md` §3), then checks the plan again; a release that brought new recommendations makes the hash no longer match, so the human dry runs again (the checkout is already fast-forwarded by then). With `no change` on the files, the line only runs the fast-forward and the launchd and automation steps.
3. Running sessions: `skills-sync broadcast` sends `/reload-skills` or `/reload-plugins` to idle ones. This session reloads itself as `write-skill` §7 describes.
4. A marketplace install instead of a checkout: `! claude plugin marketplace update jeongjaesoon`, `! claude plugin update agent-skills@jeongjaesoon`, then `/reload-plugins`. `setup.py` is for the checkout install.

## Check only

`python3 "${CLAUDE_SKILL_DIR}/scripts/setup_check.py" check [--json]` prints the table alone, read-only: tools on PATH, `gh auth`, commit signing, how agent-skills is loaded and whether it matches `origin/main`, the sync job and its last result, the default-on automations, Orca Run binding and terminal title, `language`, `outputStyle`, `permissions.defaultMode`, autoMode rules, `permissions.allow`, the `agent-skills.json` keys whose absence turns a feature off, and personal files. Rows are `ok`, `missing`, `differs`, `warn`, `info` (unset; the skill that needs it says what happens without it), `disabled` (an automation the user turned off) or `unsupported` (macOS-only, on another OS). Only `missing`, `differs` and `warn` count as left to do.

**Optional: compare with another PC.** When the human wants this PC to match a specific old one, they run there `! python3 <path>/setup_check.py export > ~/setup-baseline.json` (no `env` values, no rules that look like credentials, `agent-skills.json` key names only) and copy the file over. `check --baseline <file>` then marks what differs. Missing autoMode rules: `automode-spec --baseline <file>` writes a spec; the human picks the rules to keep and `tune-automode` takes them from its step 6. Personal files and `agent-skills.json` values are copied by the human.

## Report

The table `setup.py` or `check` ended with, then: the line the human ran, what is still open with its owner, and whether running sessions were restarted or reloaded.

## Tests

`python3 scripts/test_setup.py` and `python3 skills/setup-pc/scripts/test_setup_check.py`
