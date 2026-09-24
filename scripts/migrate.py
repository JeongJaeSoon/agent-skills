#!/usr/bin/env python3
"""Move an install made by the old scripts/install.py (symlinks in ~/.claude/skills, rules and a hook
in ~/.claude/settings.json) onto the agent-skills plugin, which ships the skills, the `orch` commands
and the permission hook itself.

Usage:
  python3 scripts/migrate.py            # dry run: print what would change
  python3 scripts/migrate.py --write    # apply, then add the marketplace and install the plugin

Run it when no program started under the old install is still running: its workers call the
~/.claude/skills paths that this removes. Only what install.py added is touched; deny and ask
rules are left as they are.
"""
import json, os, pathlib, shutil, subprocess, sys, time

REPO = pathlib.Path(__file__).resolve().parents[1]
SKILLS = pathlib.Path(os.environ.get("CLAUDE_SKILLS_DIR", "~/.claude/skills")).expanduser()
SETTINGS = pathlib.Path(os.environ.get("CLAUDE_SETTINGS", "~/.claude/settings.json")).expanduser()
# The marketplace is this checkout, not GitHub: `claude plugin update` then installs whatever it has committed,
# with no push. Run this from the checkout that should be the installed version (the main one, not a worktree).
PLUGIN_CMDS = [["claude", "plugin", "marketplace", "add", str(REPO)],
               ["claude", "plugin", "install", "agent-skills@jeongjaesoon"]]


def old_rule(rule):
    # install.py's allow rules: the orca one is now the plugin hook's, the rest point at ~/.claude/skills.
    return rule == "Bash(orca orchestration:*)" or "~/.claude/skills/" in rule


def links():
    names = {p.parent.name for d in ("skills", "legacy", "extras") for p in (REPO / d).glob("*/SKILL.md")}
    for dst in sorted(SKILLS.iterdir()) if SKILLS.is_dir() else []:
        if dst.is_symlink() and dst.name in names and "agent-skills" in os.readlink(dst):
            print(f"unlink  {dst} -> {os.readlink(dst)}")
            yield dst


def settings():
    d = json.loads(SETTINGS.read_text())
    allow = d.get("permissions", {}).get("allow", [])
    changes = [f"drop    allow {r}" for r in allow if old_rule(r)]
    allow[:] = [r for r in allow if not old_rule(r)]
    pre = d.get("hooks", {}).get("PreToolUse", [])
    kept = [m for m in pre if not any("mailbox_guard" in h.get("command", "") for h in m.get("hooks", []))]
    changes += ["drop    PreToolUse hook mailbox_guard.py"] * (len(pre) - len(kept))
    pre[:] = kept
    print("\n".join(changes) or "settings: nothing from install.py left")
    return d if changes else None


def main():
    write = "--write" in sys.argv
    stale = list(links())
    new = settings()
    if not write:
        print("then:   " + "\n        ".join(" ".join(c) for c in PLUGIN_CMDS))
        print("(dry run; add --write to apply)")
        return 0
    for dst in stale:
        dst.unlink()
    if new:
        backup = SETTINGS.with_name(f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(SETTINGS, backup)
        SETTINGS.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n")
        print(f"settings written; backup at {backup}")
    failed = 0
    for cmd in PLUGIN_CMDS:  # each runs even if the one before failed: the marketplace may already be added
        print("$ " + " ".join(cmd), flush=True)
        failed |= subprocess.run(cmd).returncode
    print("failed: see the output above" if failed else "done: in each running session, /reload-skills (drops the unlinked skills) then /reload-plugins")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
