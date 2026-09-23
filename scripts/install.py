#!/usr/bin/env python3
"""Install this repo's skills into ~/.claude/skills as symlinks, and optionally merge the
permission rules and hook the orchestrate workflow needs into ~/.claude/settings.json.

Usage:
  python3 scripts/install.py              # dry run: print what would change
  python3 scripts/install.py --write      # link skills, retire stale links from this repo
  python3 scripts/install.py --settings   # dry run of the settings merge
  python3 scripts/install.py --settings --write

Link from the checkout you want installed: the links point at this checkout's skills/.
A real directory in ~/.claude/skills (a skill managed elsewhere) is never replaced.
Skills listed in .install-ignore stay in the repo but are not linked.
"""
import json, os, pathlib, shutil, sys, time

REPO = pathlib.Path(__file__).resolve().parents[1]
TARGET = pathlib.Path(os.environ.get("CLAUDE_SKILLS_DIR", "~/.claude/skills")).expanduser()
SETTINGS = pathlib.Path(os.environ.get("CLAUDE_SETTINGS", "~/.claude/settings.json")).expanduser()
# Old name → directory under legacy/ that keeps the old name resolvable while sessions started
# before the rename still reference it. Drop an entry once nothing running uses the old name.
LEGACY = {"use-obsidian": "legacy/use-obsidian", "ship-pr": "legacy/ship-pr", "dispatch-work": "legacy/dispatch-work"}

O = "orca orchestration "
S = "~/.claude/skills"
ALLOW = (
    [f"Bash({O}{v}:*)" for v in (
        # worker contract: own mailbox, reports, questions
        "check", "send", "ask", "reply", "inbox", "dispatch-show", "run-current",
        # coordinator loop
        "run-show", "run-list", "run-create", "run-use", "task-create", "task-list", "task-update", "dispatch",
        "worker-start", "worker-show", "worker-read", "worker-release", "worker-list", "gate-create", "gate-list")]
    + ["Bash(orca terminal rename:*)", "Bash(orca terminal read:*)", "Bash(orca terminal list:*)"]
    + [f"Bash(orca linear {v}:*)" for v in (
        "issue", "list-issues", "list", "search", "team list", "team members", "team states", "team labels",
        "project list")]
    # prog.py land is the program's merge gate (verdict patch-id, CI at head, land order, lock)
    + [f"Bash(python3 {S}/orchestrate/scripts/prog.py:*)"]
    + [f"Bash(python3 {S}/orchestrate/scripts/dash.py {v}:*)" for v in ("collect", "note", "serve")]
    + [f"Bash(python3 {S}/use-tracker/scripts/tracker.py {v}:*)" for v in ("list", "get")]
    + [f"Bash(gh {v}:*)" for v in (
        "pr view", "pr list", "pr checks", "pr diff", "pr update-branch", "run view", "run list", "run watch",
        "issue view", "issue list")]
)
DENY = [f"Bash({O}reset:*)", f"Bash({O}worker-abandon:*)"]
ASK = ["Bash(orca terminal send:*)", f"Bash({O}gate-resolve:*)"]
HOOK_CMD = f'f="$HOME/.claude/skills/orchestrate/scripts/mailbox_guard.py"; [ -f "$f" ] && python3 "$f" || true'


def skills():
    ignore = set()
    if (REPO / ".install-ignore").exists():
        ignore = {l.strip() for l in (REPO / ".install-ignore").read_text().splitlines() if l.strip() and not l.startswith("#")}
    found = {d.name: d for d in sorted((REPO / "skills").iterdir()) if (d / "SKILL.md").exists() and d.name not in ignore}
    found.update({name: REPO / rel for name, rel in LEGACY.items() if (REPO / rel / "SKILL.md").exists()})
    return found


def install(write):
    TARGET.mkdir(parents=True, exist_ok=True)
    want = skills()
    for name, src in want.items():
        dst = TARGET / name
        if dst.is_symlink():
            if pathlib.Path(os.readlink(dst)) == src:
                continue
            print(f"relink  {name} -> {src}")
            if write:
                dst.unlink(); dst.symlink_to(src)
        elif dst.exists():
            print(f"skip    {name}: a real directory is installed there (managed elsewhere)")
        else:
            print(f"link    {name} -> {src}")
            if write:
                dst.symlink_to(src)
    for dst in sorted(TARGET.iterdir()):
        if dst.is_symlink() and dst.name not in want and "agent-skills" in os.readlink(dst):
            print(f"retire  {dst.name} (was {os.readlink(dst)})")
            if write:
                dst.unlink()


def merge_settings(write):
    d = json.loads(SETTINGS.read_text())
    perm = d.setdefault("permissions", {})
    changes = []
    for key, rules in (("allow", ALLOW), ("deny", DENY), ("ask", ASK)):
        cur = perm.setdefault(key, [])
        for r in rules:
            if r not in cur:
                cur.append(r); changes.append(f"{key:5} {r}")
    pre = d.setdefault("hooks", {}).setdefault("PreToolUse", [])
    if not any("mailbox_guard" in h.get("command", "") for m in pre for h in m.get("hooks", [])):
        pre.append({"matcher": "Bash", "hooks": [{"type": "command", "command": HOOK_CMD, "timeout": 5}]})
        changes.append("hook  PreToolUse Bash mailbox_guard.py")
    print("\n".join(changes) or "settings already up to date")
    if write and changes:
        backup = SETTINGS.with_name(f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(SETTINGS, backup)
        SETTINGS.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
        print(f"written; backup at {backup}")


if __name__ == "__main__":
    write = "--write" in sys.argv
    if "--settings" in sys.argv:
        merge_settings(write)
    else:
        install(write)
    if not write:
        print("(dry run; add --write to apply)")
