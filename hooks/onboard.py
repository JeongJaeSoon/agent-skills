#!/usr/bin/env python3
"""SessionStart (startup, clear) hook of the agent-skills plugin: on a PC where agent-skills was installed but never
set up, tells the session to run setup-pc (docs/zero-command-setup.md).

Set up means setup.py has recorded an apply ($AGENT_SKILLS_STATE/setup-applied.json) or the user settings already
load a checkout (env.CLAUDE_CODE_PLUGIN_DIRS, from bootstrap.py). Neither: the session gets one paragraph with the
values the human gave the install dialog (userConfig, as CLAUDE_PLUGIN_OPTION_*). AGENT_SKILLS_ONBOARD=0 silences it.
"""
import json, os, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from setup import ENV_KEY, paths  # noqa: E402

OPTIONS = ("language", "checkout", "personal_repo")


def set_up():
    p = paths()
    if p["record"].exists():
        return True
    try:
        return bool(json.loads(p["settings"].read_text()).get("env", {}).get(ENV_KEY))
    except (OSError, ValueError, AttributeError):
        return False


def message():
    given = {k: os.environ.get(f"CLAUDE_PLUGIN_OPTION_{k.upper()}", "").strip() for k in OPTIONS}
    values = ", ".join(f"{k}={v}" for k, v in given.items() if v) or "none"
    return ("agent-skills is installed, but its setup has never been applied on this PC. "
            "Before the user's first request is done, tell them in one line and offer to run the agent-skills:setup-pc "
            "skill's \"Setup: a new PC\" now; follow it if they agree. Values from the plugin's install dialog: "
            f"{values}. Never write Claude settings yourself; setup-pc hands the one settings line to the human.")


def main():
    if os.environ.get("AGENT_SKILLS_ONBOARD") == "0" or set_up():
        return 0
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": message()}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
