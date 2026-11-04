"""No hook keeps a ledger of refused acts: after the classifier refused a call, the same call sent again passes every
hook hooks.json registers, the other guard rules still deny, and these hooks leave a refusals.jsonl already on disk
unchanged.
Run: python3 hooks/test_refusal_retry.py"""
import json, os, pathlib, re, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
TMP = pathlib.Path(os.path.realpath(tempfile.mkdtemp()))
HOME = TMP / "home"
STATE = TMP / "state"
(HOME / ".claude").mkdir(parents=True)
STATE.mkdir()
SPEC = json.loads((HERE / "hooks.json").read_text())["hooks"]

assert "PermissionDenied" not in SPEC, "nothing records a refusal any more"
assert "refusals" not in (HERE / "hooks.json").read_text()
assert not (HERE / "refusals.py").exists() and not (HERE / "lib" / "refusals.py").exists()

# A ledger the removed hook left behind is user data: no hook reads, rewrites or deletes it.
OLD = STATE / "refusals.jsonl"
OLD_TEXT = '{"op": "refuse", "id": "toolu_1", "verb": "merge", "target": "github.com/acme/app#12"}\n'
OLD.write_text(OLD_TEXT)

CLASSIFIER = ("Permission for this action was denied by the Claude Code auto mode classifier. Reason: [Production "
              "Deploy]. If you have other tasks that don't depend on this action, continue working on those.")
MERGE = "gh pr merge 12 --repo acme/app --squash"


def env():
    e = {k: v for k, v in os.environ.items()
         if not k.startswith(("CLAUDE_", "AGENT_SKILLS_", "XDG_")) and k != "ORCA_TERMINAL_HANDLE"}
    e.update(HOME=str(HOME), AGENT_SKILLS_STATE=str(STATE), AGENT_SKILLS_CONFIG=str(HOME / ".claude/agent-skills.json"),
             CLAUDE_PLUGIN_ROOT=str(ROOT))
    return e


def commands(event, tool=None):
    for group in SPEC.get(event, []):
        matcher = group.get("matcher")
        if tool is None or not matcher or re.fullmatch(matcher, tool):
            yield from (h["command"] for h in group["hooks"])


def run(command, payload):
    p = subprocess.run(["sh", "-c", command], input=json.dumps(payload), capture_output=True, text=True, env=env(),
                       cwd=TMP, timeout=60)
    assert p.returncode == 0, (command, p.stderr)
    return json.loads(p.stdout) if p.stdout.strip() else {}


def decision(out):
    return (out.get("hookSpecificOutput") or {}).get("permissionDecision") or out.get("decision") or "none"


# The session's transcript: the classifier refused the merge once.
TRANSCRIPT = TMP / "session.jsonl"
TRANSCRIPT.write_text("".join(json.dumps(x) + "\n" for x in (
    {"type": "assistant", "sessionId": "s1", "cwd": str(TMP),
     "message": {"content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": MERGE}}]}},
    {"type": "user", "sessionId": "s1", "cwd": str(TMP), "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "is_error": True, "content": CLASSIFIER}]}})))
BASE = {"session_id": "s1", "cwd": str(TMP), "transcript_path": str(TRANSCRIPT)}

# The turn ends after the refusal: no Stop or SubagentStop hook blocks or records it.
for event in ("Stop", "SubagentStop"):
    for command in commands(event):
        out = run(command, {"hook_event_name": event, "stop_hook_active": False, **BASE})
        assert decision(out) != "block", (event, command, out)


def pre(tool, tool_input):
    return {command: decision(run(command, {"hook_event_name": "PreToolUse", "tool_name": tool,
                                            "tool_input": tool_input, **BASE}))
            for command in commands("PreToolUse", tool)}


# The same merge, sent again once the evidence is in: no PreToolUse hook denies it.
got = pre("Bash", {"command": MERGE})
assert got and all(d != "deny" for d in got.values()), got

# The other guard rules still deny.
GUARD = next(c for c in commands("PreToolUse", "Bash") if "guard.py" in c)
for cmd in ("pkill -f node", "docker system prune -af", "orch exempt visual 12"):
    assert pre("Bash", {"command": cmd})[GUARD] == "deny", cmd
assert pre("Write", {"file_path": str(HOME / ".claude/settings.json")})[GUARD] == "deny"

assert OLD.read_text() == OLD_TEXT, "the old ledger was changed"
print("test_refusal_retry: ok")
