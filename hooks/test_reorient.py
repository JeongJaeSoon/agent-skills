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
         handle="term_me", transcript=None, entrypoint=None):
    env = {"PATH": f"{TMP / 'bin'}:/usr/bin:/bin", "HOME": str(TMP / "home"), "AGENT_SKILLS_STATE": str(TMP / "state"),
           "PROGRAMS_HOME": str(TMP / "programs"), "ORCA_TERMINAL_HANDLE": handle,
           "ORCH_FLEET_STATE": str(TMP / "fleet"), "ORCH_FLEET_CONFIG": str(TMP / "none.json"),
           **({"CLAUDE_CODE_ENTRYPOINT": entrypoint} if entrypoint else {})}
    payload = {"hook_event_name": event, "session_id": session, "cwd": "/work"}
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
assert "just cleared" in hook(**START, source="clear", session="r1") and "reboot --top" in hook(**START, source="clear", session="r1")
hook(**START, session="c1", handle="term_c", entrypoint="cli")
hook(session="c1", handle="term_c", prompt="ROLE: orchestrator")
cleared = hook(**START, source="clear", session="c2", handle="term_c", entrypoint="cli")
assert "just cleared" in cleared and "These core rules" in cleared, cleared
assert json.loads((TMP / "state/roles/c2.json").read_text()) == {"role": "orchestrator"}
assert json.loads((TMP / "state/terminals/term_c.json").read_text())["session_id"] == "c2"
hook(**START, session="n1", handle="term_n", entrypoint="cli")
hook(agent=ORCH, session="n1", handle="term_n", prompt="ROLE: none")
assert hook(**START, source="clear", agent=ORCH, session="n2", handle="term_n", entrypoint="cli") is None, "ROLE: none carries"
assert hook(**START, source="clear", session="u2", handle="term_u", entrypoint="cli") is None, "nothing to carry"
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

# Terminal -> session: only an interactive session at its own start, and nothing but the four fields.
term = TMP / "state/terminals/term_t.json"
hook(session="t1", handle="term_t", source="startup", entrypoint="sdk-cli", **START)
assert not term.exists(), "a nested `claude -p` inherits the handle and must not take the terminal"
hook(session="t1", handle="term_t", source="startup", **START)
assert not term.exists(), "no entrypoint is unknown, not interactive"
hook(session="t1", handle="term_t", source="startup", entrypoint="cli", **START)
record = json.loads(term.read_text())
assert set(record) == {"handle", "session_id", "cwd", "at", "started_at", "plugin_version", "hooks_sha"}, record
assert (record["handle"], record["session_id"], record["cwd"]) == ("term_t", "t1", "/work"), record
sys.path.insert(0, str(HERE))
import roles  # noqa: E402
assert record["hooks_sha"] == roles.hooks_sha(HERE.parent), "the sha of the plugin root's hooks.json"
# A compaction reloads nothing: the same session keeps what it recorded, a different session's record is not borrowed.
term.write_text(json.dumps({**record, "hooks_sha": "old", "at": 1.0, "started_at": 1.0}))
hook(session="t1", handle="term_t", source="compact", entrypoint="cli", **START)
assert json.loads(term.read_text())["hooks_sha"] == "old", "compact keeps the loaded sha"
after_compact = json.loads(term.read_text())
assert after_compact["at"] > 1.0 and after_compact["started_at"] == 1.0, "compact moves `at` but not the session's start"
hook(session="t1", handle="term_t", source="clear", entrypoint="cli", **START)
assert json.loads(term.read_text())["started_at"] == 1.0, "a /clear is not a start either"
term.write_text(json.dumps({**record, "hooks_sha": "old"}))
hook(session="t5", handle="term_t", source="compact", entrypoint="cli", **START)
assert "hooks_sha" not in json.loads(term.read_text()), "another session's sha is not inherited"
hook(session="t1", handle="term_t", source="resume", entrypoint="cli", **START)
assert json.loads(term.read_text())["hooks_sha"] == record["hooks_sha"], "a resume is a new process: it reads again"
# roles.reload_state: stale only on a recorded digest that differs.
os_env = {"AGENT_SKILLS_STATE": str(TMP / "state")}
import os  # noqa: E402
os.environ.update(os_env)
assert roles.reload_state(["term_t"], record["hooks_sha"]) == "ok"
assert roles.reload_state(["term_t"], "other") == "stale"
assert roles.reload_state(["term_none"], "other") == "unknown", "no record is not stale"
assert roles.reload_state(["term_t"], None) == "unknown", "an unreadable reference says nothing"
term.write_text(json.dumps({**record, "hooks_sha": None}))
assert roles.reload_state(["term_t"], "other") == "unknown", "a record without a digest is not stale"
term.write_text(json.dumps({**record, "hooks_sha": ""}))
assert roles.reload_state(["term_t"], record["hooks_sha"]) == "unknown", "a malformed digest is not a digest"
term.write_text(json.dumps(record))
assert roles.reload_state(["term_t"], "other", {record["session_id"]}) == "stale"
assert roles.reload_state(["term_t"], "other", {"another-session"}) == "unknown", \
    "the record of a session that is not alive here says nothing about the live ones"
term.write_text(json.dumps({**record, "session_id": []}))
assert roles.reload_state(["term_t"], "other", {"x"}) == "unknown", "a session_id that is not a string is not a session"
term.write_text("{not json")
assert roles.reload_state(["term_t"], "other") == "unreadable", "an unreadable record is not a missing one"
hook(session="t2", handle="term_t", source="resume", entrypoint="cli", **START)
assert json.loads(term.read_text())["session_id"] == "t2", "a resume takes the terminal"
hook(session="t3", handle="term_t", prompt="hi", entrypoint="cli")
assert json.loads(term.read_text())["session_id"] == "t2", "a prompt writes nothing"
hook(session="t4", handle="", source="startup", entrypoint="cli", **START)
# Failure injection: a record that cannot be written or read must not change what the hook prints (the ROLE paragraph)
# nor its exit status. The reminder is the hook's job; the record is a side effect.
import os, shutil  # noqa: E402,F811
want = hook(**START, agent=ORCH, title="orchestrator", session="fi0", handle="term_fi", entrypoint="cli")
assert want and "top-level orchestrator" in want, want
terms = TMP / "state/terminals"


want_compact = hook(**START, source="compact", agent=ORCH, title="orchestrator", session="fi0", handle="term_fi", entrypoint="cli")
assert want_compact and want_compact != want and "just compacted" in want_compact


def same_output(label, source="startup"):
    got = hook(**START, source=source, agent=ORCH, title="orchestrator", session="fi0", handle="term_fi", entrypoint="cli")
    assert got == (want_compact if source == "compact" else want), (label, got)


# 1. terminals/ cannot be created or written: a plain file stands where the directory should be, then a read-only dir.
shutil.rmtree(terms)
terms.write_text("not a directory")
same_output("terminals is a file")
same_output("terminals is a file, compact", "compact")
terms.unlink()
terms.mkdir()
# The temp file the record is written through is a directory: the write fails the same way for every user, root too.
(terms / "term_fi.tmp").mkdir()
same_output("temp file is a directory")
same_output("temp file is a directory, compact", "compact")
(terms / "term_fi.tmp").rmdir()
# A read-only directory, where this user's permissions are enforced (root and some filesystems ignore the mode).
terms.chmod(0o500)
try:
    enforced = not os.access(terms, os.W_OK)
    if enforced:
        same_output("terminals is read-only")
        same_output("terminals is read-only, compact", "compact")
        assert not (terms / "term_fi.json").exists(), "nothing could be written there"
finally:
    terms.chmod(0o700)
# 2. A corrupt record, injected again before each call: a startup rewrites the record, so a compaction after it would
# read a good one. (A compaction reads the old record to keep its digest.)
for junk in ("{not json", "[]", '{"session_id": []}', ""):
    for source in ("startup", "compact"):
        (terms / "term_fi.json").write_text(junk)
        same_output(f"corrupt record {junk!r}, {source}", source)
# 3. A record that is a directory, so reading it fails with an OSError other than "missing" (and replacing it fails).
(terms / "term_fi.json").unlink()
for source in ("startup", "compact"):
    (terms / "term_fi.json").mkdir()
    same_output(f"record is a directory, {source}", source)
    shutil.rmtree(terms / "term_fi.json")
# 4. An unreadable hooks.json (plugin root without one): still the same paragraph, and a warning on stderr only.
bare = TMP / "bare-root"
bare.mkdir()
event = json.dumps({"hook_event_name": "SessionStart", "session_id": "fi1", "cwd": "/work", "source": "startup",
                    "agent_type": ORCH, "session_title": "orchestrator"})
out = subprocess.run([sys.executable, HERE / "reorient.py"], input=event, capture_output=True, text=True,
                     env={"PATH": "/usr/bin:/bin", "HOME": str(TMP / "home"), "AGENT_SKILLS_STATE": str(TMP / "state"),
                          "PROGRAMS_HOME": str(TMP / "programs"), "ORCA_TERMINAL_HANDLE": "term_fi",
                          "CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_PLUGIN_ROOT": str(bare)})
assert out.returncode == 0 and json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"] == want, out
assert "unreadable" in out.stderr, out.stderr
assert json.loads((terms / "term_fi.json").read_text())["hooks_sha"] is None, "recorded as no digest, not a wrong one"
print("reorient: all pass")
