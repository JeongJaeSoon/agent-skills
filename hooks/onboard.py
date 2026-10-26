#!/usr/bin/env python3
"""SessionStart (startup, clear) hook of the agent-skills plugin: on a PC where agent-skills was installed but never
set up, tells the session to run setup-pc (docs/zero-command-setup.md).

Set up means this profile's user settings load a checkout (env.CLAUDE_CODE_PLUGIN_DIRS, which setup.py --apply and
bootstrap.py write). The state dir is shared by every Orca profile, so its apply record does not count. Not set up: the
session gets one paragraph with the values the human gave the install dialog (userConfig, as
CLAUDE_PLUGIN_OPTION_*), credentials stripped from a URL. AGENT_SKILLS_ONBOARD=0 silences it everywhere;
`onboard.py --decline`, which the session runs when the human says no, silences it for this profile only.
"""
import json, os, pathlib, sys, urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from setup import ENV_KEY, paths  # noqa: E402

OPTIONS = ("language", "checkout", "personal_repo")


def set_up():
    try:
        return bool(json.loads(paths()["settings"].read_text()).get("env", {}).get(ENV_KEY))
    except (OSError, ValueError, AttributeError):
        return False


def declined_file():
    return paths()["state"] / "onboard-declined.json"


def profile():
    return str(paths()["settings"].resolve())


def declined():
    try:
        return profile() in json.loads(declined_file().read_text())
    except (OSError, ValueError, TypeError):
        return False


def decline():
    f = declined_file()
    try:
        have = json.loads(f.read_text())
    except (OSError, ValueError):
        have = []
    if profile() not in have:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(have + [profile()], indent=2) + "\n")
    print(f"onboard: no more setup-pc offers for {profile()}; remove that entry from {f} to get them back")


def strip_credentials(url):
    """`https://user:token@host/x` → `https://host/x`: the paragraph lands in the transcript, the clone in .git/config."""
    parts = urllib.parse.urlsplit(url)
    if not parts.scheme or "@" not in parts.netloc:
        return url
    return urllib.parse.urlunsplit(parts._replace(netloc=parts.netloc.rpartition("@")[2]))


def message():
    given = {k: os.environ.get(f"CLAUDE_PLUGIN_OPTION_{k.upper()}", "").strip() for k in OPTIONS}
    given["personal_repo"] = strip_credentials(given["personal_repo"])
    values = ", ".join(f"{k}={v}" for k, v in given.items() if v) or "none"
    return ("agent-skills is installed, but its setup has never been applied on this PC. "
            "Before the user's first request is done, tell them in one line and offer to run the agent-skills:setup-pc "
            "skill's \"Setup: a new PC\" now; follow it if they agree. If they decline, run "
            f"`python3 {pathlib.Path(__file__).resolve()} --decline` so this profile is not asked again. "
            f"Values from the plugin's install dialog: {values}. "
            "Never write Claude settings yourself; setup-pc hands the one settings line to the human.")


def main(argv):
    if argv == ["--decline"]:
        decline()
        return 0
    if os.environ.get("AGENT_SKILLS_ONBOARD") == "0" or set_up() or declined():
        return 0
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": message()}}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
