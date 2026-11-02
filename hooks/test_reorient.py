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
         handle="term_me", transcript=None):
    env = {"PATH": f"{TMP / 'bin'}:/usr/bin:/bin", "HOME": str(TMP / "home"), "AGENT_SKILLS_STATE": str(TMP / "state"),
           "PROGRAMS_HOME": str(TMP / "programs"), "ORCA_TERMINAL_HANDLE": handle,
           "ORCH_FLEET_STATE": str(TMP / "fleet"), "ORCH_FLEET_CONFIG": str(TMP / "none.json")}
    payload = {"hook_event_name": event, "session_id": session}
    if event == "SessionStart":
        payload["source"] = source
    if event == "UserPromptSubmit":
        payload["prompt"] = prompt
    if agent:
        payload["agent_type"] = agent
    if title:
        payload["session_title"] = title
    if transcript:
        payload["transcript_path"] = transcript
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
# Named `orchestrator`: no naming line. Unnamed: /rename for the human, the Orca tab title for itself.
assert "/rename" not in hook(**START, agent=ORCH, title="orchestrator")
unnamed = hook(**START, agent=ORCH, session="n1")
assert "/rename orchestrator" in unnamed and "orca terminal rename" in unnamed, unnamed
assert "orca terminal rename" not in hook(**START, agent=ORCH, session="n1", handle="")
assert "/rename" not in hook(agent=ORCH, session="n1"), "prompts stay one line"
assert "/rename" not in hook(**START, source="compact", agent=LEAD, title="lead:p", session="l0")
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

# 3a. After a compaction an orchestrator or a lead gets orchestrate's core rules as text, verbatim from SKILL.md.
SKILL_TEXT = (HERE.parent / "skills/orchestrate/SKILL.md").read_text()
block = SKILL_TEXT[SKILL_TEXT.index("\n1. Delegate.") + 1:SKILL_TEXT.index("\n\n", SKILL_TEXT.index("\n1. Delegate."))]
RULES = block.splitlines()
assert 8 <= len(RULES) <= 12 and RULES[-1].startswith(f"{len(RULES)}. "), RULES
assert RULES[0].startswith("1. Delegate.") and "one layer down" in RULES[0], RULES[0]
sys.path.insert(0, str(HERE))
import reorient
fixture = TMP / "SKILL.md"
fixture.write_text("# x\n\nCore rules. Intro:\n\n1. One\n   wrapped.\n2. Two.\n\nBody, not a rule.\n3. Not a rule.\n")
assert reorient.core_rules(fixture) == "1. One\n   wrapped.\n2. Two.", reorient.core_rules(fixture)
assert reorient.core_rules(TMP / "missing.md") == ""
fixture.write_text("# x\nno list here\n")
assert reorient.core_rules(fixture) == ""
assert any("skills-sync card" in r and "new-child" in r and "new-top-level" in r for r in RULES)
assert any("orch brief render" in r for r in RULES) and any("one `--json` read" in r for r in RULES)
for agent, title, session in ((ORCH, "orchestrator", "c1"), (LEAD, "lead:p", "c2")):
    text = hook(**START, source="compact", agent=agent, title=title, session=session)
    assert "\n".join(RULES) in text and "just compacted" in text, text
    # Claude Code caps a hook's additionalContext at 10,000 characters; stay well under it.
    assert len(text.encode()) < 4000, len(text.encode())
assert "\n".join(RULES) in hook(**START, source="compact", session="r1")
for source in ("startup", "resume", "fork"):
    assert RULES[0] not in hook(**START, source=source, agent=ORCH, title="orchestrator", session="c1"), source
for session in ("u9", "a1"):  # undeclared, and ROLE: none
    assert hook(**START, source="compact", session=session) is None
assert hook(**START, source="compact", agent=ORCH, session="a1") is None
# The per-prompt line repeats rules 1 and 2 and says nothing looser.
for line in (hook(agent=ORCH, session="c1"), hook(agent=LEAD, title="lead:p", session="c2")):
    assert "one layer down at once" in line and "one `--json` read" in line and "coordination" in line \
        and "only the human's session may take" in line and "seconds-long" not in line, line
assert "Acts only the human's session may take" in RULES[1], "rule 2 keeps the human-session acts"
# A lead hands its own project to its workers; rule 4 must not forbid that.
assert any("a lead sends its own project's work to its workers" in r for r in RULES)
assert any("--worktree id:" in r for r in RULES), "attaching a Dispatch to an existing card stays allowed"

# 3. Nothing declared: nothing said, whatever the terminal, and no external command run.
assert hook(session="u1") is None and hook(**START, session="u1") is None
for text in ("use ROLE: orchestrator here", "ROLE: lead <slug>", "ROLE: admin", "ROLES: orchestrator"):
    assert hook(session="u2", prompt=text) is None, text
assert not (TMP / "state/roles/u2.json").exists()
assert hook(session="../u3", prompt="ROLE: orchestrator") is None
assert not (TMP / "state/u3.json").exists()
assert hook(event="Stop") is None
assert not (TMP / "orca-called").exists(), "the hook ran orca"

# 4. reflect.orchestrator_feedback: an orchestrator's transcript span is registered only when it is on.
REG = TMP / "state/transcripts"
CFG = TMP / "home/.claude/agent-skills.json"
CFG.parent.mkdir(parents=True)


def reg(session):
    path = REG / f"{session}.json"
    return json.loads(path.read_text()) if path.exists() else None


T = "/x/t.jsonl"
assert hook(session="f1", prompt="ROLE: orchestrator", transcript=T).startswith("You are the top-level")
assert reg("f1") is None, "no config: nothing registered"
CFG.write_text(json.dumps({"reflect": {"orchestrator_feedback": False}}))
assert hook(session="f1", transcript=T) == hook(session="f1") and reg("f1") is None, "off: same output, no file"
CFG.write_text(json.dumps({"reflect": {"orchestrator_feedback": True}}))
assert hook(session="f1", transcript=T).startswith("ROLE: orchestrator."), "the reminder is unchanged"
first = reg("f1")
assert first["role"] == "orchestrator" and first["transcript"] == T and first["until"] is None, first
assert first["since"].endswith("Z") and len(first["since"]) == 20, first
assert json.loads((TMP / "state/roles/f1.json").read_text()) == {"role": "orchestrator"}, "the roles file is untouched"
assert hook(session="f1", prompt="next", transcript=T) and reg("f1")["since"] == first["since"], "since stays"
hook(**START, source="resume", agent=ORCH, session="f2", transcript=T)
assert reg("f2")["role"] == "orchestrator", "agent_type at SessionStart"
hook(**START, source="resume", title="orchestrator", session="f3", transcript=T)
assert reg("f3")["transcript"] == T, "title only, at SessionStart"
hook(title="orchestrator", session="f3b", transcript=T)
assert reg("f3b") is None, "a prompt with only the title is not an orchestrator"
assert "ROLE cleared" in hook(session="f1", prompt="ROLE: none", transcript=T)
cleared = reg("f1")
assert cleared["until"] and cleared["since"] == first["since"], cleared
assert hook(session="f1", transcript=T) is None and reg("f1") == cleared, "until is written once"
hook(session="f1", prompt="ROLE: orchestrator", transcript=T)
assert reg("f1")["until"] is None and reg("f1")["since"] >= cleared["until"], "a reopened span starts now"
hook(session="f8", prompt="ROLE: orchestrator", transcript=T)
hook(session="f8", prompt="ROLE: lead p", transcript=T)
assert reg("f8")["until"], "turning lead closes the span"
hook(**START, source="resume", title="orchestrator", session="f9", transcript=T)
hook(session="f9", prompt="next", transcript=T)
assert reg("f9")["until"] is None, "a title-only session's prompt resolves to nothing and keeps the span"
hook(session="f4", prompt="ROLE: lead p", transcript=T)
hook(agent=LEAD, title="lead:p", session="f5", transcript=T)
assert reg("f4") is None and reg("f5") is None, "a lead is not registered"
assert hook(session="f6", prompt="ROLE: orchestrator").startswith("You are the top-level") and reg("f6") is None, \
    "no transcript_path: as before"
hook(session="u9", prompt="hello", transcript=T)
assert reg("u9") is None, "undeclared"
CFG.write_text("not json")
hook(session="f7", prompt="ROLE: orchestrator", transcript=T)
assert reg("f7") is None, "unreadable config counts as off"
print("reorient: all pass")
