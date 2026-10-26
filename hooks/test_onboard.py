"""onboard.py: a PC that was never set up gets the setup-pc nudge; a set-up one gets nothing.
Run: python3 hooks/test_onboard.py"""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
HOME = pathlib.Path(tempfile.mkdtemp())
CFG, STATE = HOME / ".claude", HOME / "state"
CFG.mkdir()


def run(*args, **env):
    base = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_PLUGIN_OPTION_", "AGENT_SKILLS_", "CLAUDE_SETTINGS"))}
    base.update({"HOME": str(HOME), "CLAUDE_CONFIG_DIR": str(CFG), "AGENT_SKILLS_STATE": str(STATE), **env})
    out = subprocess.run([sys.executable, HERE / "onboard.py", *args], input=json.dumps({"source": "startup"}),
                         capture_output=True, text=True, env=base).stdout
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip().startswith("{") else None


said = run(CLAUDE_PLUGIN_OPTION_LANGUAGE="Korean", CLAUDE_PLUGIN_OPTION_PERSONAL_REPO=" ")
assert "agent-skills:setup-pc" in said and "language=Korean" in said and "personal_repo" not in said, said
assert "values from the plugin's install dialog: none" in run().lower()
assert run(AGENT_SKILLS_ONBOARD="0") is None
said = run(CLAUDE_PLUGIN_OPTION_PERSONAL_REPO="https://me:ghp_FAKE0000@git.example.test/me/conf.git")
assert "personal_repo=https://git.example.test/me/conf.git" in said and "ghp_FAKE" not in said, said
said = run(CLAUDE_PLUGIN_OPTION_PERSONAL_REPO="git@git.example.test:me/conf.git")
assert "personal_repo=git@git.example.test:me/conf.git" in said, said

# A declined offer stays declined in this profile only; another profile is still asked.
assert "--decline" in run()
run("--decline")
assert run() is None, "the human declined in this profile"
OTHER = HOME / "other"
OTHER.mkdir()
assert run(CLAUDE_CONFIG_DIR=str(OTHER)) is not None, "another profile was never asked"
(STATE / "onboard-declined.json").unlink()

(CFG / "settings.json").write_text("not json")
assert run() is not None, "an unreadable settings file is not a set-up PC"
(CFG / "settings.json").write_text(json.dumps({"env": {"CLAUDE_CODE_PLUGIN_DIRS": "/x/agent-skills"}}))
assert run() is None, "bootstrap.py already loads a checkout"
(CFG / "settings.json").unlink()
STATE.mkdir(exist_ok=True)
(STATE / "setup-applied.json").write_text("{}")
assert run() is not None, "another Orca profile's apply record does not set this profile up"
print("onboard: all pass")
