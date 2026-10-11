"""reorient.py: only a coordinator is told what it coordinates, at session start and once on its first prompt.
Run: python3 hooks/test_reorient.py"""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(tempfile.mkdtemp())
(TMP / "bin").mkdir()
(TMP / "home/.claude").mkdir(parents=True)
(TMP / "programs/p").mkdir(parents=True)
(TMP / "programs/p/program.json").write_text(json.dumps({"slug": "p", "run": "run_a", "note": "Project/p/note.md"}))
orca = TMP / "bin/orca"
orca.write_text("#!/bin/sh\ncat \"$FAKE_RUN\"\n")
orca.chmod(0o755)
RUN_A = json.dumps({"ok": True, "result": {"run": {"id": "run_a", "coordinator_handle": "term_me"}}})
NO_RUN = json.dumps({"ok": True, "result": {"run": None}})


def hook(source="compact", own="term_me", reply=RUN_A, programs="programs", event="SessionStart", session="s1"):
    (TMP / "reply.json").write_text(reply)
    env = {"PATH": f"{TMP / 'bin'}:/usr/bin:/bin", "HOME": str(TMP / "home"), "AGENT_SKILLS_STATE": str(TMP / "state"),
           "PROGRAMS_HOME": str(TMP / programs), "FAKE_RUN": str(TMP / "reply.json")}
    if own:
        env["ORCA_TERMINAL_HANDLE"] = own
    payload = {"hook_event_name": event, "session_id": session}
    if event == "SessionStart":
        payload["source"] = source
    out = subprocess.run([sys.executable, HERE / "reorient.py"], input=json.dumps(payload),
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    if not out.stdout.strip():
        return None
    spec = json.loads(out.stdout)["hookSpecificOutput"]
    assert spec["hookEventName"] == event, spec
    return spec["additionalContext"]


text = hook()
assert "just compacted" in text and "orch status p" in text and "orch wait p" in text and "Project/p/note.md" in text \
    and "run_a" in text, text
for source in ("startup", "resume"):
    text = hook(source=source)
    assert "orch status p" in text and "compacted" not in text, text
assert hook(source="clear") is None
assert hook(own="term_other") is None, "a worker or another coordinator is not this program's coordinator"
assert hook(own=None) is None, "not an Orca terminal"
assert hook(reply="not json") is None

# A Run with no registered program, or no programs at all: its coordinator is the top-level coordinator.
top = hook(reply=json.dumps({"ok": True, "result": {"run": {"id": "run_b", "coordinator_handle": "term_me"}}}))
assert "top-level coordinator of Orca Run run_b" in top and "agent-skills:orchestrate" in top \
    and "agent-skills:brief-status" in top, top
assert "top-level coordinator of Orca Run run_a" in hook(programs="none")
assert hook(reply=json.dumps({"ok": False, "error": {"code": "no_run"}})) is None
assert hook(reply=NO_RUN) is None

# Listed in the local config, no Run needed.
(TMP / "home/.claude/agent-skills.json").write_text(json.dumps({"orchestrate": {"top_level_handles": ["term_top"]}}))
top = hook(own="term_top", reply=NO_RUN)
assert top.startswith("You are the top-level coordinator.") and "Run" not in top, top
assert hook(own="term_other", reply=NO_RUN) is None

# A prompt is judged once per session; SessionStart judges again every time.
assert "orch status p" in hook(event="UserPromptSubmit", session="u1")
assert hook(event="UserPromptSubmit", session="u1") is None, "second prompt of the same session"
assert hook(event="UserPromptSubmit", session="u2", own="term_other") is None
assert hook(event="UserPromptSubmit", session="u2") is None, "the cache records a worker too"
assert "orch status p" in hook(event="UserPromptSubmit", session="u3")
assert "orch status p" in hook(session="u1", source="resume")
assert hook(event="UserPromptSubmit", session="../x") is None, "an id that is not a file name"
assert not (TMP / "x.json").exists() and not (TMP / "state/x.json").exists()
assert hook(event="Stop") is None
print("reorient: all pass")
