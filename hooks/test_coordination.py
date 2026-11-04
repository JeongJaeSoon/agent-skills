"""guard.py with lib/coordination.py: AskUserQuestion, foreground waits and a send to /dev/null, by role.
Run: python3 hooks/test_coordination.py"""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(os.path.realpath(tempfile.mkdtemp()))
CONFIG = TMP / "agent-skills.json"
STATE = TMP / "state"
PREAMBLE = "You are working inside Orca, a multi-agent IDE. You are a dispatched worker.\n=== TASK ===\nfix X\n"


def transcript(name, prompt):
    path = TMP / f"{name}.jsonl"
    path.write_text(json.dumps({"type": "user", "message": {"content": prompt}}))
    return str(path)


WORKER = transcript("worker", PREAMBLE)
CARD = transcript("card", "/goal fix X\nCLOSE: end-session §8\n")
HUMAN = transcript("human", "please look at the failing test")
ROLES = {"orchestrator": {"agent_type": "agent-skills:orchestrator"}, "lead": {"agent_type": "agent-skills:lead"},
         "worker": {"transcript_path": WORKER}, "card": {"transcript_path": CARD}, "general": {"transcript_path": HUMAN}}


LAST = {}


def hook(tool, tool_input, role, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "AGENT_SKILLS_"))}
    env.update(HOME=str(TMP), AGENT_SKILLS_STATE=str(STATE), AGENT_SKILLS_CONFIG=str(CONFIG),
               ORCA_TERMINAL_HANDLE="term_me")
    payload = {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": tool, "tool_input": tool_input,
               "cwd": str(TMP), **ROLES[role], **extra}
    out = subprocess.run([sys.executable, HERE / "guard.py"], input=json.dumps(payload), capture_output=True,
                         text=True, env=env).stdout
    parsed = json.loads(out) if out.strip() else {}
    LAST["warning"] = parsed.get("systemMessage") or ""
    spec = parsed.get("hookSpecificOutput") or {}
    return spec.get("permissionDecision") or "none"


def ask(role, **extra):
    return hook("AskUserQuestion", {"questions": []}, role, **extra)


def bash(cmd, role, background=False):
    return hook("Bash", {"command": cmd, "run_in_background": background}, role)


# AskUserQuestion: denied for an orchestrator, a lead and a worker (dispatched or a card); a human's session asks.
for role in ("orchestrator", "lead", "worker", "card"):
    assert ask(role) == "deny", role
assert ask("general") == "none"
assert ask("orchestrator", agent_id="a1") == "none", "a subagent is outside the rule"
CONFIG.write_text("{}")
STATE.joinpath("roles").mkdir(parents=True, exist_ok=True)
STATE.joinpath("roles", "s1.json").write_text(json.dumps({"role": "none"}))
assert ask("worker") == "none", "ROLE: none turns the role off"
STATE.joinpath("roles", "s1.json").unlink()
CONFIG.write_text(json.dumps({"orchestrate": {"ask_user_question": False}}))
assert ask("orchestrator") == "none", "off: as before"
CONFIG.write_text("{}")

# Foreground waits: denied for an orchestrator or a lead, fine in the background and for a worker.
WAITS = ("orch wait app", "orch inbox --wait", "orch watch-cards app", "orca orchestration check --terminal $ORCA_TERMINAL_HANDLE --wait",
         "orca orchestration inbox --wait --json")
for role in ("orchestrator", "lead"):
    for cmd in WAITS:
        assert bash(cmd, role) == "deny", (role, cmd)
        assert bash(cmd, role, background=True) in ("none", "allow"), (role, cmd)
    assert bash("orch wait app | tail -5", role, background=True) == "deny"
    assert bash("orch wait app | tail -5; orch status app", role, background=True) == "deny"
    assert bash("orch wait app | jq . | head -3", role, background=True) == "deny"
    assert bash("orch wait a | jq .; orch wait b | tail -5", role, background=True) == "deny"
    assert bash("orch wait app | jq .; tail -5 /tmp/status.log", role, background=True) != "deny"
    # Monitor runs beside the turn: its wait is a background one, and the pipe and /dev/null rules still hold.
    for cmd in WAITS:
        assert hook("Monitor", {"command": cmd, "description": "x"}, role) in ("none", "allow"), (role, cmd)
    assert hook("Monitor", {"command": "orch wait app | tail -5", "description": "x"}, role) == "deny"
    assert hook("Monitor", {"command": "orca orchestration send --to t --subject s > /dev/null",
                            "description": "x"}, role) == "deny"
    assert bash("orch status app", role) == "allow"
    assert bash("orch inbox --json", role) == "allow"
    assert bash("echo 'orch wait app'", role) == "none"
for role in ("worker", "card", "general"):
    assert bash("orca orchestration check --terminal $ORCA_TERMINAL_HANDLE --wait", role) == "allow", role
CONFIG.write_text(json.dumps({"orchestrate": {"wait_foreground": False}}))
assert bash("orch wait app", "lead") == "allow", "off: the old allow is back"
CONFIG.write_text("{}")

# A send or reply to /dev/null hides whether it went out.
for cmd in ("orca orchestration send --to run:r --text hi > /dev/null", "orca orchestration send --to run:r --text hi >/dev/null 2>&1",
            "orca orchestration reply --id m1 --body ok &>/dev/null", "orca orchestration send --text hi 1>/dev/null",
            "orca orchestration send --text '2' >/dev/null", "orca orchestration send --text 2 >/dev/null",
            "orca orchestration send --to run:r --text hi >& /dev/null"):
    for role in ("orchestrator", "lead", "worker"):
        assert bash(cmd, role) == "deny", (role, cmd)
    assert bash(cmd, "general") == "none", cmd
for cmd in ("orca orchestration send --to run:r --text hi 2>/dev/null", "orca orchestration send --to run:r --text '> /dev/null'",
            "orca orchestration check --json > /dev/null"):
    assert bash(cmd, "lead") in ("none", "allow"), cmd
CONFIG.write_text(json.dumps({"orchestrate": {"send_devnull": False}}))
assert bash("orca orchestration send --to run:r --text hi > /dev/null", "lead") == "none"
CONFIG.write_text("{}")


# A Claude start with no --model warns, and only once orchestrate.models is set.
def model_warning(cmd, role="lead"):
    bash(cmd, role)
    return LAST["warning"]


START = "orca orchestration worker-start --worktree new-child --agent claude"
assert "orchestrate.model_flag" not in model_warning(f"{START} --spec 'ABC-1 fix'")
CONFIG.write_text(json.dumps({"orchestrate": {"models": {"lead": "opus", "worker": "sonnet"}}}))
for role in ("lead", "general"):
    assert "orch model worker" in model_warning(f"{START} --spec 'ABC-1 fix'", role)
assert "orch model worker" in model_warning("orca agent launch --agent claude --worktree active")
assert "orch model worker" in model_warning("orca worktree create --repo id:r --name x --agent claude --prompt 'ABC-1 fix'")
for cmd in (f"{START} --spec 'x' --model sonnet", f"{START} --spec 'x' --model=Sonnet[1m]",
            f"{START} --spec 'x' --model claude-sonnet-5-5", f"{START} --terminal term_x --spec 'x'",
            "orca orchestration worker-start --terminal term_x --worktree id:w --spec 'x'",
            "orca orchestration worker-start --worktree new-child --agent codex --spec 'x'"):
    assert model_warning(cmd) == "", cmd
# A --model that differs from orchestrate.models.worker warns (never blocks); an unset worker key says it compared nothing.
for cmd in (f"{START} --spec 'x' --model=opus", f"{START} --spec 'x' --model claude-opus-4-1"):
    assert "differs from orchestrate.models.worker (sonnet)" in model_warning(cmd), cmd
    assert bash(cmd, "lead") in ("none", "allow"), "a warning does not block: " + cmd
CONFIG.write_text(json.dumps({"orchestrate": {"models": {"worker": "claude-sonnet-4-6"}}}))
assert "differs" in model_warning(f"{START} --spec 'x' --model claude-sonnet-4-5"), "two ids of one family are two models"
assert model_warning(f"{START} --spec 'x' --model claude-sonnet-4-6-20260101") == ""
assert model_warning(f"{START} --spec 'x' --model sonnet") == "", "a bare family matches its ids"
CONFIG.write_text(json.dumps({"orchestrate": {"models": {"worker": "opusplan"}}}))
assert "differs" in model_warning(f"{START} --spec 'x' --model opus"), "opusplan is not opus"
CONFIG.write_text(json.dumps({"orchestrate": {"models": {"lead": "opus", "worker": ""}}}))
assert "is not set" in model_warning(f"{START} --spec 'x' --model sonnet"), "an empty worker is unset"
CONFIG.write_text(json.dumps({"orchestrate": {"models": {"lead": "opus", "worker": "sonnet"}}}))
assert model_warning("orca agent launch --agent claude --worktree active --model opus") == "", "not a worker start"
assert model_warning(f"MODEL=sonnet {START} --model \"$MODEL\"") == "", "a value the shell expands is not judged"
assert model_warning(f"{START} --model claude-sonnet-4-?") == "", "a glob the shell may expand is not judged"
assert model_warning(f"{START} --model {{sonnet,}}") == "", "a brace expansion is not judged"
CONFIG.write_text(json.dumps({"orchestrate": {"models": {"worker": "sonnet"}, "model_flag": "deny"}}))
assert bash(f"{START} --spec 'x' --model opus", "lead") in ("none", "allow"), "a comparison never blocks, whatever the level"
CONFIG.write_text(json.dumps({"orchestrate": {"models": {"lead": "opus"}}}))
assert "is not compared: orchestrate.models.worker is not set" in model_warning(f"{START} --spec 'x' --model opus")
assert model_warning(f"{START} --model opus; {START} --model sonnet").count("is not set") == 1, "one line per call"
assert "orch model worker" in model_warning(f"{START} --spec 'x'"), "no --model keeps the original warning"
CONFIG.write_text(json.dumps({"orchestrate": {"models": {"worker": "sonnet"}, "model_flag": "off"}}))
assert model_warning(f"{START} --spec 'x'") == ""
CONFIG.write_text("{}")
print("coordination: all pass")
