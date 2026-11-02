"""request_log.py is retired: hooks.json no longer runs it, and a session that still does gets no output and exit 0.
Run: python3 hooks/test_request_log.py"""
import json, pathlib, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent

out = subprocess.run([sys.executable, HERE / "request_log.py"], capture_output=True, text=True,
                     input=json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s-1", "prompt": "ROLE: orchestrator\nmerge it"}))
assert (out.returncode, out.stdout, out.stderr) == (0, "", ""), out
assert "request_log" not in (HERE / "hooks.json").read_text()
assert not (HERE.parent / "skills" / "orchestrate" / "scripts" / "owner_requests.py").exists()
print("ok")
