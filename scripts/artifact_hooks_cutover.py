#!/usr/bin/env python3
"""Move the Artifact backup hooks from user settings.json to the plugin. Run by the owner after the plugin version
that carries hooks/artifact_*.py is installed; it never runs on its own.

  artifact_hooks_cutover.py --base-url URL [--old-hook PATH] [--apply]

Without --apply it prints what it would change and writes nothing. With it:
  - removes the settings.json hook entries that run artifact-archive.py or artifact-links-stop.py (the plugin registers
    artifact_archive.py, artifact_links_stop.py and artifact_send_check.py itself, with a 30 s PostToolUse timeout),
  - sets `artifact-backup.base_url` in agent-skills.json, and `artifact-backup.categories` from the old hook's
    CATEGORY_RULES when --old-hook names it, so the index keeps its grouping.
Each file it changes is copied to `<file>.bak-<date>` first. Paths default to ~/.claude/settings.json and
~/.claude/agent-skills.json; --settings and --config override them.
"""
import argparse, datetime, json, pathlib, re, shutil, sys

OLD = ("artifact-archive.py", "artifact-links-stop.py")
RULE = re.compile(r"\(\s*'([^']+)'\s*,\s*re\.compile\(r'((?:[^'\\]|\\.)*)'")


def prune(settings):
    """(settings without the old hook entries, the commands removed)."""
    removed = []
    for event, groups in list((settings.get("hooks") or {}).items()):
        kept = []
        for group in groups:
            hooks = []
            for h in group.get("hooks") or []:
                if any(name in (h.get("command") or "") for name in OLD):
                    removed.append(h["command"])
                else:
                    hooks.append(h)
            if hooks or not group.get("hooks"):
                kept.append(dict(group, hooks=hooks) if "hooks" in group else group)
        settings["hooks"][event] = kept
    if "hooks" in settings:
        settings["hooks"] = {k: v for k, v in settings["hooks"].items() if v}
    return settings, removed


def categories(old_hook):
    text = pathlib.Path(old_hook).expanduser().read_text(encoding="utf-8")
    start = text.index("CATEGORY_RULES")
    block = text[start:text.index("\n]\n", start)]
    return [[name, rx] for name, rx in RULE.findall(block)]


def save(path, data, stamp):
    shutil.copy2(path, f"{path}.bak-{stamp}")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base-url", required=True, help="URL that serves artifact-backup.public_dir")
    ap.add_argument("--old-hook", help="the old artifact-archive.py, to carry its category rules over")
    ap.add_argument("--settings", default="~/.claude/settings.json")
    ap.add_argument("--config", default="~/.claude/agent-skills.json")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    settings_path = pathlib.Path(args.settings).expanduser()
    config_path = pathlib.Path(args.config).expanduser()
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    settings, removed = prune(settings)
    section = dict(config.get("artifact-backup") or {}, base_url=args.base_url)
    if args.old_hook:
        section["categories"] = categories(args.old_hook)
    config["artifact-backup"] = section
    print(f"{settings_path}: remove {len(removed)} hook entr{'y' if len(removed) == 1 else 'ies'}")
    for cmd in removed:
        print(f"  - {cmd}")
    print(f"{config_path}: artifact-backup = {json.dumps(section, ensure_ascii=False)}")
    if not args.apply:
        print("dry run; add --apply to write")
        return 0
    stamp = datetime.date.today().strftime("%Y%m%d")
    save(settings_path, settings, stamp)
    if config_path.exists():
        save(config_path, config, stamp)
    else:
        config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("applied; backups are <file>.bak-" + stamp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
