"""reorient.py: an orchestrator or lead, declared by `--agent` or a ROLE line, is reminded at session start and on
every prompt; anyone else gets nothing.
Run: python3 hooks/test_reorient.py"""
import json, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(tempfile.mkdtemp())
(TMP / "bin").mkdir()
(TMP / "programs/p").mkdir(parents=True)
(TMP / "programs/p/program.json").write_text(json.dumps({"slug": "p", "run": "run_a", "note": "Project/p/note.md"}))
# Prompts must stay fast: any external command the hook ran would leave this file behind.
orca = TMP / "bin/orca"
orca.write_text(f"#!/bin/sh\ntouch {TMP / 'orca-called'}\n")
orca.chmod(0o755)
ORCH, LEAD = "agent-skills:orchestrator", "agent-skills:lead"


def hook(event="UserPromptSubmit", session="s1", prompt="hi", source="startup", agent=None, title=None,
         handle="term_me"):
    env = {"PATH": f"{TMP / 'bin'}:/usr/bin:/bin", "HOME": str(TMP / "home"), "AGENT_SKILLS_STATE": str(TMP / "state"),
           "PROGRAMS_HOME": str(TMP / "programs"), "ORCA_TERMINAL_HANDLE": handle}
    payload = {"hook_event_name": event, "session_id": session}
    if event == "SessionStart":
        payload["source"] = source
    if event == "UserPromptSubmit":
        payload["prompt"] = prompt
    if agent:
        payload["agent_type"] = agent
    if title:
        payload["session_title"] = title
    out = subprocess.run([sys.executable, HERE / "reorient.py"], input=json.dumps(payload),
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    if not out.stdout.strip():
        return None
    spec = json.loads(out.stdout)["hookSpecificOutput"]
    assert spec["hookEventName"] == event, spec
    return spec["additionalContext"]


START = dict(event="SessionStart")

# 1. --agent: the hook input's agent_type decides, on every prompt and at every SessionStart that carries it.
assert "top-level orchestrator" in hook(**START, agent=ORCH, title="orchestrator")
assert hook(agent=ORCH).startswith("ROLE: orchestrator.")
assert hook(agent=ORCH, session="s2").startswith("ROLE: orchestrator."), "no record needed"
text = hook(**START, source="compact", agent=LEAD, title="lead:p", session="l1")
assert "coordinate the program p" in text and "just compacted" in text and "orch status p" in text \
    and "Project/p/note.md" in text and "Orca Run run_a" in text, text
line = hook(agent=LEAD, title="lead:p", session="l1")
assert line.startswith("ROLE: lead p.") and "orch wait p" in line and "Project/p/note.md" in line and "\n" not in line
# Resume and fork leave agent_type out of SessionStart; the title stands in.
assert "coordinate the program p" in hook(**START, source="resume", title="lead:p", session="l1")
assert "coordinate the program p" in hook(**START, source="fork", title="lead:p", session="l9")
assert "top-level orchestrator" in hook(**START, source="resume", title="orchestrator", session="o9")
assert hook(**START, source="resume", title="fix login bug", session="x9") is None
assert hook(title="lead:p", session="x9") is None, "a prompt is decided by agent_type or a ROLE line, not the title"
# A lead with no name gets told how to name its program; a ROLE line can.
assert "no program is named" in hook(agent=LEAD, session="l2")
assert "coordinate the program q" in hook(agent=LEAD, session="l2", prompt="ROLE: lead q")
assert hook(agent=LEAD, session="l2").startswith("ROLE: lead q.")
assert hook(agent=LEAD, session="l2", title="lead:p").startswith("ROLE: lead p."), "the title names the program first"
assert hook(agent="Explore", session="s3") is None and hook(**START, agent="Explore", session="s3") is None

# 2. A ROLE line: bound to the session id, whatever terminal the session is in now.
first = hook(session="r1", handle="term_old", prompt="/goal steer everything\nROLE: orchestrator\nCLOSE: §8")
assert "top-level orchestrator" in first and "ROLE: none" in first, first
assert json.loads((TMP / "state/roles/r1.json").read_text()) == {"role": "orchestrator"}
for handle in ("term_old", "term_new", ""):
    assert hook(session="r1", handle=handle).startswith("ROLE: orchestrator.")
for source in ("startup", "resume", "compact"):
    assert "top-level orchestrator" in hook(**START, source=source, session="r1", handle="term_new")
assert hook(**START, source="clear", session="r1") is None
lead = hook(session="r3", prompt="/goal ship p\nMODE orchestrate\nROLE        lead p\nCLOSE: §8")
assert "coordinate the program p" in lead and "orch status p" in lead and "Project/p/note.md" in lead, lead
assert "just compacted" in hook(**START, source="compact", session="r3")
assert hook(session="r3", prompt="ROLE: lead q").startswith("You coordinate the program q")
assert "orch wait q" in hook(session="r3")

# ROLE: none: one notice, then silence, even for a session started with --agent.
assert "ROLE cleared" in hook(session="r3", prompt="ok\nROLE: none")
assert hook(session="r3") is None and hook(**START, source="resume", session="r3") is None
assert hook(session="r3", prompt="ROLE: none") is None, "already cleared"
assert "ROLE cleared" in hook(agent=ORCH, session="a1", prompt="ROLE: none")
assert hook(agent=ORCH, session="a1") is None and hook(**START, source="compact", agent=ORCH, session="a1") is None
assert hook(**START, source="resume", title="orchestrator", session="a1") is None

# 3. Nothing declared: nothing said, whatever the terminal, and no external command run.
assert hook(session="u1") is None and hook(**START, session="u1") is None
for text in ("use ROLE: orchestrator here", "ROLE: lead <slug>", "ROLE: admin", "ROLES: orchestrator"):
    assert hook(session="u2", prompt=text) is None, text
assert not (TMP / "state/roles/u2.json").exists()
assert hook(session="../u3", prompt="ROLE: orchestrator") is None
assert not (TMP / "state/u3.json").exists()
assert hook(event="Stop") is None
assert not (TMP / "orca-called").exists(), "the hook ran orca"
print("reorient: all pass")
