"""permission.py: records the permission request for the dashboard, masked, and never decides or fails.
Run: python3 hooks/test_permission.py"""
import json, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
STATE = pathlib.Path(tempfile.mkdtemp()) / "dashboard"
SECRET = "gh" + "p_" + "b2" * 18  # split so the literal is not itself a token


def hook(stdin, handle="term_demo1"):
    env = {"PATH": "/usr/bin:/bin", "ORCH_FLEET_STATE": str(STATE)}
    if handle is not None:
        env["ORCA_TERMINAL_HANDLE"] = handle
    out = subprocess.run([sys.executable, HERE / "permission.py"], input=stdin, capture_output=True, text=True, env=env)
    assert out.returncode == 0 and out.stdout == "" and out.stderr == "", out  # no decision, never a loud failure
    return out


event = {"session_id": "s-1", "cwd": "/work/app", "hook_event_name": "PermissionRequest", "tool_name": "Bash",
         "tool_input": {"command": "GH_TOKEN=" + SECRET + " gh release create v1", "description": "Cut a release", "timeout": 60000}}
hook(json.dumps(event))
rec = json.loads((STATE / "prompts" / "term_demo1.json").read_text())
assert rec["handle"] == "term_demo1" and rec["tool"] == "Bash" and rec["session_id"] == "s-1" and rec["id"] and rec["at"], rec
assert rec["input"] == {"command": "GH_TOKEN=[mas" "ked] gh release create v1", "description": "Cut a release"}, rec
assert SECRET not in (STATE / "prompts" / "term_demo1.json").read_text()

hook(json.dumps(event), handle=None)  # outside Orca: nothing to key the record by
hook(json.dumps(event), handle="../escape")
hook("not json", handle="term_demo2")
assert sorted(p.name for p in (STATE / "prompts").iterdir()) == ["term_demo1.json"]
print("test_permission: ok")
