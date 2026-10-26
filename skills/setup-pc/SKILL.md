---
name: setup-pc
description: "Use when agent-skills goes onto another PC or an installed PC needs bringing up to date — \"새 PC 세팅해줘\", \"다른 PC 에서도 똑같이 쓰게 해줘\", \"agent-skills 설치 점검\", \"agent-skills 업데이트해줘\", \"플러그인 최신화\", \"set up agent-skills on a new PC\", \"update agent-skills\". Dry-runs `scripts/setup.py` (a diff per file, nothing written) and hands the human its one `--apply <hash>` line, which writes the settings and turns on sync; the check alone is read-only. A single auto-mode denial is tune-automode."
---

# Set up or update agent-skills on a PC

Installing the plugin brings the skills; it does not bring the user settings, the minimal `agent-skills.json` keys or the automations. `scripts/setup.py` in the checkout (`${CLAUDE_SKILL_DIR}/../../scripts/setup.py`) does all of it in one run from the repo's recommended defaults (`scripts/recommended-settings.json`): a dry run shows a diff per file and prints one `--apply <hash>` line, and the human runs that line. What each setting does is in `docs/new-pc.md`; this skill does not repeat it.

## What this skill never does

- Write `~/.claude/settings.json`, `~/.claude/agent-skills.json` or anything else under `~/.claude`, by any tool, and never run `setup.py --apply`, `bootstrap.py --write`, `skills-sync setup apply` or `gh auth login`. Those are the human's `!` lines. The auto-mode classifier refuses an agent's edit to its own settings, and the human reading the diff is what authorizes the bundled rules.
- Carry one PC's or one company's values: internal name lists, chat channels, tracker projects, notes paths, a personal `CLAUDE.md`, personal skills or agents. Without them each feature keeps its default (off, or asks on first use).

| Change after setup | Owner |
|---|---|
| A rule for one action auto mode denied | `tune-automode` |
| `permissions.allow` or hooks beyond the bundle | `update-config` |
| What the periodic automations do | `reap-resources`; this skill only checks they are on |
| Reloading running sessions | `skills-sync broadcast` (never a raw `orca terminal send`) |

## Setup: a new PC

1. **Nothing installed yet?** The skill cannot fire. The human follows README "설치": clone, then the dry run and the `--apply` line it prints. Then a new session.
2. **`gh` logged in first.** The rules about the user's repositories are filled from the `gh` login and left out without it; if `gh auth status` fails, hand over `! gh auth login` before the dry run. **Ask the language** the human wants answers in (default: the system locale, which `setup.py` reads). Ask whether this session is the orchestrator's Orca terminal; if so, add `--orca-run`, which binds this terminal to a new Run when it has none.
3. **Dry run** from the main checkout: `python3 <checkout>/scripts/setup.py --language <name> [--orca-run]`. It writes nothing. Show the diff as printed; do not paraphrase rules away. A `refused:` line (a worktree, a branch other than `main`, `CLAUDE_CODE_PLUGIN_DIRS` pointing at another checkout) is fixed first, by the human.
4. **Hand over the one line** it printed: `! python3 <checkout>/scripts/setup.py … --apply <hash>`. What it does after that is in `docs/new-pc.md`; it ends with a rollback line per file and the check table. A hash that no longer matches means something changed since the diff: dry run again.
5. **Read the table** it ended with. `missing`, `differs` and `warn` rows are left to do; each row's `next` goes to the human as a `!` line or to the owner above. `gh auth login` and git commit signing are the human's.
6. **New session.** `CLAUDE_CODE_PLUGIN_DIRS` and the settings are read at start, so sessions running before the apply are restarted, not reloaded.

The human may refuse part of the bundle: they edit the rule out after applying (the rollback line restores the old file), and later runs do not offer it again.

## Update: an installed PC

1. Dry run `python3 <checkout>/scripts/setup.py --update`. Like any dry run it writes and fetches nothing: it plans the recommended items this PC has not been offered before from the checkout as it is, and lists the fast-forward as the `first` step.
2. Hand over the `--apply` line as in setup step 4. It fast-forwards the checkout first (`skills-sync sync`; stops on a dirty or diverged checkout, `docs/platform.md` §3), then checks the plan again; a release that brought new recommendations makes the hash no longer match, so the human dry runs again (the checkout is already fast-forwarded by then). With `no change` on the files, the line only runs the fast-forward and the launchd and automation steps.
3. Running sessions: `skills-sync broadcast` sends `/reload-skills` or `/reload-plugins` to idle ones. This session reloads itself as `write-skill` §7 describes.
4. A marketplace install instead of a checkout: `! claude plugin marketplace update jeongjaesoon`, `! claude plugin update agent-skills@jeongjaesoon`, then `/reload-plugins`. `setup.py` is for the checkout install.

## Check only

`python3 "${CLAUDE_SKILL_DIR}/scripts/setup_check.py" check [--json]` prints the table alone, read-only: tools on PATH, `gh auth`, commit signing, how agent-skills is loaded and whether it matches `origin/main`, the sync job and its last result, the default-on automations, Orca Run binding and terminal title, `language`, `outputStyle`, `permissions.defaultMode`, autoMode rules, `permissions.allow`, the `agent-skills.json` keys whose absence turns a feature off, and personal files. Rows are `ok`, `missing`, `differs`, `warn`, `info` (unset; the skill that needs it says what happens without it), `disabled` (an automation the user turned off) or `unsupported` (macOS-only, on another OS). Only `missing`, `differs` and `warn` count as left to do.

**Optional: compare with another PC.** When the human wants this PC to match a specific old one, they run there `! python3 <path>/setup_check.py export > ~/setup-baseline.json` (no `env` values, no rules that look like credentials, `agent-skills.json` key names only) and copy the file over. `check --baseline <file>` then marks what differs. Missing autoMode rules: `automode-spec --baseline <file>` writes a spec; the human picks the rules to keep and `tune-automode` takes them from its step 6. Personal files and `agent-skills.json` values are copied by the human.

## Report

The table `setup.py` or `check` ended with, then: the line the human ran, what is still open with its owner, and whether running sessions were restarted or reloaded.

## Tests

`python3 scripts/test_setup.py` and `python3 skills/setup-pc/scripts/test_setup_check.py`
