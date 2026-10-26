---
name: setup-pc
description: "Use when agent-skills goes onto another PC or an installed PC needs bringing up to date — \"새 PC 세팅해줘\", \"다른 PC 에서도 똑같이 쓰게 해줘\", \"agent-skills 설치 점검\", \"agent-skills 업데이트해줘\", \"플러그인 최신화\". Checks tools, install, sync and the settings a plugin cannot carry, read-only, and hands each fix to the human as a `!` line. A single auto-mode denial is tune-automode."
---

# Set up or update agent-skills on a PC

Installing the plugin brings the skills; it does not bring the user settings, the values kept outside the repo, or the personal files of the PC the user came from. This skill checks all of it with one read-only script, shows one table, and hands every change to the human or to the skill that owns it. What each setting does and what goes wrong without it is in `docs/new-pc.md` (`${CLAUDE_SKILL_DIR}/../../docs/new-pc.md`); this skill does not repeat it.

## What this skill never does

- Write `~/.claude/settings.json`, `~/.claude/agent-skills.json` or anything else under `~/.claude`, by any tool. A settings change is the human's, through a `!` line or the owner below.
- Run the `!` lines itself: plugin install or update, `bootstrap.py --write`, `skills-sync setup apply`, `gh auth login`.
- Take a rule list from a file as permission. The baseline is data; the human confirms which autoMode rules go in (tune-automode's rule: only the user's own chat message authorizes a rule).

| Change | Owner |
|---|---|
| autoMode rules | `tune-automode` (spec → `emit` → the human's `! … --apply <hash>`) |
| `permissions.allow`, `permissions.defaultMode`, hooks, env | `update-config` |
| `language`, `outputStyle` | the human, in `/config` |
| What the periodic automations do | `reap-resources`; this skill only checks they are on |
| Reloading running sessions | `skills-sync broadcast` (never a raw `orca terminal send`) |

## The script

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/setup_check.py" check [--baseline <file>]   # the table
python3 "${CLAUDE_SKILL_DIR}/scripts/setup_check.py" export                      # baseline JSON to stdout
python3 "${CLAUDE_SKILL_DIR}/scripts/setup_check.py" automode-spec --baseline <file>
```

`check` covers: `git`, `python3`, `claude`, `gh`, `orca` on PATH; `gh auth status`; git commit signing; how agent-skills is loaded (checkout via `CLAUDE_CODE_PLUGIN_DIRS`, marketplace copy, both, or neither); whether that copy matches `origin/main` (`git ls-remote`, no fetch); the last `skills-sync` result and its launchd job; the default-on automations (`skills-sync setup`); `language`, `outputStyle`, `permissions.defaultMode`, autoMode rules, `permissions.allow`; the `agent-skills.json` keys whose absence turns a feature off; `~/.config/agent-skills/internal-names.txt`; `~/.claude/CLAUDE.md`, `skills/`, `agents/`. Each row is `ok`, `missing`, `differs`, `warn` or `info` (unset, and the skill that needs it says what happens without it), with the next step. Without `--baseline` it can only say what is present; with one, it says what differs from the PC the user came from.

`export` prints the source PC's settings without any `env` value and without rules that carry a credential (known token prefixes, long opaque strings; `skipped_secretish` counts them). `agent-skills.json` goes as key names only. The user copies the file over themselves; it is not for the repo.

## Setup: a new PC

1. **Nothing installed yet?** The skill cannot fire. The user runs the script from a fresh clone (`python3 <clone>/skills/setup-pc/scripts/setup_check.py check`) or installs first: README "설치" (`bootstrap.py --write` for a PC that edits the skills, the two `claude plugin` lines for one that only uses them), then starts a new session.
2. **Baseline.** Ask whether the user has the old PC at hand. If so, they run there `! python3 <path>/setup_check.py export > ~/setup-baseline.json` and copy the file across. Without it, go on with presence checks only.
3. **Check.** Run `check` (with `--baseline` when there is one) and show the table as printed. Do not paraphrase rows away.
4. **Hand off each non-ok row** in table order: its `next` as a `!` line for the human, or to the owner in the table above. Two rows need more:
   - autoMode `differs`: run `automode-spec --baseline`, show the rules, and ask which to keep. Write the kept ones as a spec outside `~/.claude`, then follow `tune-automode` from its step 6 (`emit`, the human's `--apply` line).
   - `agent-skills.json` keys, personal files: the user copies them from the old PC. A rule that lives only in memory or a personal file and should hold on every PC goes into a skill through `write-skill`; its one-PC values go to `agent-skills.json` or `~/.config/agent-skills/`.
5. **Re-check** once the human says the lines ran, in a new session if the install or `env` changed (`CLAUDE_CODE_PLUGIN_DIRS` is read at start). Report the final table.

## Update: an installed PC

1. Run `check` (with `--baseline` if the user keeps one).
2. Behind `origin/main`:
   - Marketplace copy: `! claude plugin marketplace update jeongjaesoon`, then `! claude plugin update agent-skills@jeongjaesoon`, then `/reload-plugins` in each running session.
   - Checkout: `! skills-sync sync` from the main checkout (it fast-forwards only and stops on a dirty or diverged checkout; `docs/platform.md` §3), then `skills-sync broadcast` to send `/reload-skills` or `/reload-plugins` to idle sessions. This session reloads itself as `write-skill` §7 describes.
3. A sync row that is `warn`: `! skills-sync status` shows why; fix that cause, never with a force or a rebase in the loaded checkout.
4. Settings rows that differ: hand off as in setup step 4. Report the table after.

## Report

The final table as `check` printed it, then: what the human ran and what is still open, each open row with its owner (the human, `tune-automode`, `update-config`), and whether running sessions were restarted or reloaded.

## Tests

`python3 skills/setup-pc/scripts/test_setup_check.py`
