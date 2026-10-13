"""reorient.py: a declared orchestrator or lead is reminded on every prompt; an undeclared coordinator terminal is told
what it coordinates at session start and once on its first prompt; anyone else gets nothing.
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


def hook(source="compact", own="term_me", reply=RUN_A, programs="programs", event="SessionStart", session="s1",
         prompt="hi"):
    (TMP / "reply.json").write_text(reply)
    env = {"PATH": f"{TMP / 'bin'}:/usr/bin:/bin", "HOME": str(TMP / "home"), "AGENT_SKILLS_STATE": str(TMP / "state"),
           "PROGRAMS_HOME": str(TMP / programs), "FAKE_RUN": str(TMP / "reply.json")}
    if own:
        env["ORCA_TERMINAL_HANDLE"] = own
    payload = {"hook_event_name": event, "session_id": session}
    if event == "SessionStart":
        payload["source"] = source
    if event == "UserPromptSubmit":
        payload["prompt"] = prompt
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
    and "agent-skills:brief." in top, top
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

# A declared role is bound to the session id: every prompt gets one line, whatever terminal or Run it is in now.
P = dict(event="UserPromptSubmit", reply=NO_RUN)
first = hook(session="r1", own="term_old", prompt="/goal steer everything\nROLE: orchestrator\nCLOSE: §8", **P)
assert "declared the top-level orchestrator" in first and "agent-skills:orchestrate" in first and "ROLE: none" in first
assert json.loads((TMP / "state/roles/r1.json").read_text()) == {"role": "orchestrator"}
for own in ("term_old", "term_new", None):
    line = hook(session="r1", own=own, **P)
    assert line.startswith("ROLE: orchestrator.") and "\n" not in line, line
assert "declared the top-level orchestrator" in hook(session="r1", own="term_new", source="resume", reply=NO_RUN)
assert "just compacted" not in hook(session="r1", source="compact", reply=NO_RUN), "top-level has no program to restate"
assert hook(session="r1", source="clear", reply=NO_RUN) is None

# An earlier false judgement of the terminal does not hide a later declaration.
assert hook(session="r2", own="term_other", **P) is None
assert json.loads((TMP / "state/reorient/r2.json").read_text())["coordinator"] is False
assert "declared the top-level orchestrator" in hook(session="r2", own="term_other", prompt="ROLE: orchestrator", **P)
assert hook(session="r2", own="term_other", **P).startswith("ROLE: orchestrator.")

# A lead names its program; the slug finds the program note without a Run.
lead = hook(session="r3", own=None, prompt="/goal ship p\nMODE orchestrate\nROLE        lead p\nCLOSE: §8", **P)
assert "declared the lead of program p" in lead and "orch status p" in lead and "Project/p/note.md" in lead \
    and "Orca Run run_a" in lead, lead
line = hook(session="r3", own=None, **P)
assert line.startswith("ROLE: lead p.") and "orch wait p" in line and "Project/p/note.md" in line, line
assert "just compacted" in hook(session="r3", own=None, source="compact", reply=NO_RUN)
assert hook(session="r3", own=None, prompt="ROLE: lead q", **P).startswith("You are declared the lead of program q")
assert "orch wait q" in hook(session="r3", own=None, **P)

# Clearing: one notice, then silence. Text that only mentions a role is not a declaration.
assert "ROLE cleared" in hook(session="r3", own=None, prompt="ok\nROLE: none", **P)
assert not (TMP / "state/roles/r3.json").exists()
assert hook(session="r3", own=None, **P) is None
assert hook(session="r3", own=None, source="resume", reply=NO_RUN) is None
for text in ("use ROLE: orchestrator here", "ROLE: lead <slug>", "ROLE: admin", "ROLES: orchestrator"):
    assert hook(session="r4", own=None, prompt=text, **P) is None, text
assert not (TMP / "state/roles/r4.json").exists()
assert hook(session="../r5", own=None, prompt="ROLE: orchestrator", **P) is None
assert not (TMP / "state/r5.json").exists()
print("reorient: all pass")
