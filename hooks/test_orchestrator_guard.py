"""orchestrator_guard.py: the orchestrator's own thread is held to routing; its subagents and every other session are not.
Run: python3 hooks/test_orchestrator_guard.py"""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(tempfile.mkdtemp()).resolve()
HOME = TMP / "home"
STATE = HOME / ".local/state/agent-skills"
ORCH = "agent-skills:orchestrator"


def run(tool, tool_input=None, agent=ORCH, agent_id=None, session="s1", prompt="p1", env_extra=None):
    env = {k: v for k, v in os.environ.items() if k not in ("AGENT_SKILLS_ORCHESTRATOR_GUARD", "PROGRAMS_HOME")}
    env.update({"HOME": str(HOME), "AGENT_SKILLS_STATE": str(STATE)}, **(env_extra or {}))
    event = {"hook_event_name": "PreToolUse", "session_id": session, "tool_name": tool, "tool_input": tool_input or {}}
    if prompt:
        event["prompt_id"] = prompt
    if agent:
        event["agent_type"] = agent
    if agent_id:
        event["agent_id"] = agent_id
    out = subprocess.run([sys.executable, HERE / "orchestrator_guard.py"], input=json.dumps(event),
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    if not out.stdout.strip():
        return "none"
    spec = json.loads(out.stdout)["hookSpecificOutput"]
    return spec["permissionDecision"] + ": " + spec["permissionDecisionReason"]


def bash(cmd, background=False, **kw):
    return run("Bash", {"command": cmd, "run_in_background": background}, **kw)


def denied(result):
    return result.startswith("deny")


# Browser, desktop, chat-send and web tools: always the orchestrator's delegate's job.
for tool in ("mcp__claude-in-chrome__navigate", "mcp__Claude_Browser__click", "mcp__computer-use__screenshot",
             "mcp__plugin_x_slack__slack_send_message", "mcp__plugin_x_slack__slack_schedule_message",
             "WebFetch", "WebSearch"):
    r = run(tool, session=f"b-{tool}")
    assert denied(r) and "Agent" in r and "orca worktree create" in r, (tool, r)
# The subagents it delegates to, and any other session, are never checked.
assert run("mcp__claude-in-chrome__navigate", agent="general-purpose", agent_id="a1") == "none"
assert run("WebFetch", agent=None, agent_id="a1") == "none"
assert run("WebFetch", agent=None) == "none", "an undeclared session"
assert run("WebFetch", agent="agent-skills:lead") == "none"

# Writes: only its own state.
(STATE / "roles").mkdir(parents=True, exist_ok=True)
assert run("Write", {"file_path": str(HOME / ".local/state/messages/cards/x.md")}) == "none"
assert run("Edit", {"file_path": str(HOME / ".claude/projects/-repo/memory/MEMORY.md")}) == "none"
assert run("Write", {"file_path": str(HOME / ".claude/programs/p/program.json")}) == "none"
for path in (str(TMP / "repo/app.py"), str(HOME / ".local/state/../../repo/app.py"), "", None):
    assert denied(run("Edit", {"file_path": path})), path
assert denied(run("NotebookEdit", {"notebook_path": str(TMP / "repo/n.ipynb")}))
(HOME / ".claude").mkdir(parents=True, exist_ok=True)
(HOME / ".claude/agent-skills.json").write_text(json.dumps({"orchestrator_guard": {"write_dirs": [str(TMP / "notes")]}}))
assert run("Write", {"file_path": str(TMP / "notes/a.md")}) == "none"

# Watchers: a background pipe into tail/head never surfaces its lines.
assert denied(bash("python3 stuck_watch.py | tail -5", background=True, session="w"))
assert denied(bash("orch watch-cards p 2>&1 | head -n 20", background=True, session="w"))
assert bash("orca orchestration check --peek --wait --json | tail -f", background=True, session="w") == "none"
assert bash("orca orchestration check --peek --wait --json", background=True, session="w") == "none"

# Routing never counts: orca, orch, skills-sync, and filters after a pipe.
for i in range(10):
    for cmd in ("orch status p", "orca orchestration check --json | jq .messages", "orca worker-list 2>&1 | tail -3",
                "cd /tmp && orch wait p", "skills-sync broadcast --skills"):
        assert bash(cmd, session="r") == "none", cmd
    for tool in ("Agent", "SendMessage", "AskUserQuestion", "Skill", "CronCreate", "Monitor", "TaskStop"):
        assert run(tool, session="r") == "none", tool
    assert run("Read", {"file_path": str(HOME / ".local/state/messages/briefs-x.md")}, session="r") == "none"

# Work calls: three per turn, the fourth is refused, a new prompt starts over.
work = [("Read", {"file_path": str(TMP / "repo/app.py")}), ("Grep", {"pattern": "x"}),
        ("Bash", {"command": "gh pr view 1 --json state"})]
for tool, args in work:
    assert run(tool, args, session="c") == "none", tool
for tool, args in (("Bash", {"command": "orca x --title \"$(gh pr list)\""}), ("mcp__x_slack__slack_read_thread", {}),
                   ("Glob", {"pattern": "*"})):
    r = run(tool, args, session="c")
    assert denied(r) and "limit 3" in r, (tool, r)
assert run("Grep", {"pattern": "x"}, session="c", prompt="p2") == "none", "a new turn"
assert run("Agent", session="c", prompt="p2") == "none", "routing still works after the limit"
# A call with no prompt_id and a stale record starts a turn too.
rec = STATE / "orchestrator-guard/c.json"
data = json.loads(rec.read_text())
data.update(n=99, at=0)
rec.write_text(json.dumps(data))
assert run("Grep", {"pattern": "x"}, session="c", prompt="p2") == "none", "a long gap"
(HOME / ".claude/agent-skills.json").write_text(json.dumps({"orchestrator_guard": {"work_calls": 0}}))
assert denied(run("Grep", {"pattern": "x"}, session="z"))
(HOME / ".claude/agent-skills.json").unlink()

# Declared by a ROLE line: the main thread is checked, its subagents (agent_id, own agent_type) are not.
(STATE / "roles/d1.json").write_text(json.dumps({"role": "orchestrator"}))
assert denied(run("WebFetch", agent=None, session="d1"))
assert run("WebFetch", agent=None, agent_id="sub", session="d1") == "none"
assert run("WebFetch", agent="Explore", agent_id="sub", session="d1") == "none"
# ROLE: none turns it off, even for --agent; so does the env switch.
(STATE / "roles/n1.json").write_text(json.dumps({"role": "none"}))
assert run("WebFetch", session="n1") == "none"
assert run("WebFetch", session="e1", env_extra={"AGENT_SKILLS_ORCHESTRATOR_GUARD": "0"}) == "none"
assert run("WebFetch", session="../x") == "none"
print("orchestrator_guard: all pass")
