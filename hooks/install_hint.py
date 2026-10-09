#!/usr/bin/env python3
"""SessionStart hook (source: startup) of the agent-skills plugin: tells the person, once, that orch-panel is missing.

Only on a machine that runs orch: the fleet collector's state.json exists (the file the mod reads). Elsewhere, and
wherever orch-panel is installed and enabled or loaded from CLAUDE_CODE_PLUGIN_DIRS, it prints nothing.
"""
import json, os, pathlib, sys

PLUGIN = "orch-panel@jeongjaesoon"


def read_json(path):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return {}


def state_dir():
    return pathlib.Path(os.environ.get("ORCH_FLEET_STATE") or "~/.local/state/agent-skills/dashboard").expanduser()


def from_plugin_dirs():
    dirs = os.environ.get("CLAUDE_CODE_PLUGIN_DIRS", "").split(os.pathsep)
    return any(pathlib.Path(d).expanduser().name == "orch-panel" for d in dirs if d)


def hint(cwd):
    if not (state_dir() / "state.json").is_file() or from_plugin_dirs():
        return None
    home = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()
    installed = (read_json(home / "plugins/installed_plugins.json").get("plugins") or {}).get(PLUGIN)
    if not installed:
        return f"orch-panel 이 설치돼 있지 않다. `claude plugin install {PLUGIN}` 후 새 세션에서 /orch-panel 로 결정 대기·작업 진행을 본다."
    # Later settings files override earlier ones, as Claude Code layers them.
    enabled = None
    for path in (home / "settings.json", pathlib.Path(cwd) / ".claude/settings.json", pathlib.Path(cwd) / ".claude/settings.local.json"):
        value = (read_json(path).get("enabledPlugins") or {}).get(PLUGIN)
        if value is not None:
            enabled = value
    if enabled is False:
        return f"orch-panel 이 꺼져 있다. `claude plugin enable {PLUGIN}` 후 새 세션에서 /orch-panel 로 연다."
    return None


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if event.get("source") != "startup":
        return 0
    text = hint(event.get("cwd") or os.getcwd())
    if text:
        print(json.dumps({"systemMessage": text}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
