"""install_hint.py: the orch-panel install hint reaches only a machine that runs orch and lacks the mod.
Run: python3 hooks/test_install_hint.py"""
import json, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(tempfile.mkdtemp())
STATE, CONFIG, CWD = TMP / "state", TMP / "config", TMP / "work"
for d in (STATE, CONFIG / "plugins", CWD / ".claude"):
    d.mkdir(parents=True)
(STATE / "state.json").write_text("{}")


def setup(installed=False, user=None, project=None):
    plugins = {"orch-panel@jeongjaesoon": [{"scope": "user", "version": "0.4.1"}]} if installed else {}
    (CONFIG / "plugins/installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": plugins}))
    (CONFIG / "settings.json").write_text(json.dumps({"enabledPlugins": user or {}}))
    (CWD / ".claude/settings.json").write_text(json.dumps({"enabledPlugins": project or {}}))


def hook(source="startup", state=STATE, plugin_dirs=""):
    env = {"PATH": "/usr/bin:/bin", "ORCH_FLEET_STATE": str(state), "CLAUDE_CONFIG_DIR": str(CONFIG),
           "CLAUDE_CODE_PLUGIN_DIRS": plugin_dirs, "HOME": str(TMP)}
    out = subprocess.run([sys.executable, HERE / "install_hint.py"], input=json.dumps({"source": source, "cwd": str(CWD)}),
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)["systemMessage"] if out.stdout.strip() else None


setup()
assert "claude plugin install orch-panel@jeongjaesoon" in hook()
assert hook(source="resume") is None and hook(source="compact") is None and hook(source="clear") is None, "once per session"
assert hook(state=TMP / "no-orch") is None, "a machine without the fleet collector never hears of it"
assert hook(plugin_dirs=f"/x/agent-skills:{TMP}/checkout/mods/orch-panel") is None, "loaded from a checkout"
setup(installed=True)
assert hook() is None, "installed, enabled by default"
setup(installed=True, user={"orch-panel@jeongjaesoon": False})
assert "claude plugin enable orch-panel@jeongjaesoon" in hook()
setup(installed=True, user={"orch-panel@jeongjaesoon": False}, project={"orch-panel@jeongjaesoon": True})
assert hook() is None, "a project setting overrides the user's"
print("install_hint: all pass")
