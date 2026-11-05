"""guard.py with lib/skillload.py: a card's first edit before it loaded deliver-ticket.
Run: python3 hooks/test_skillload.py"""
import json, os, pathlib, subprocess, sys, tempfile, time

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(os.path.realpath(tempfile.mkdtemp()))
CONFIG = TMP / "agent-skills.json"
STATE = TMP / "state"
WORK = TMP / "work"
WORK.mkdir()
PREAMBLE = "You are working inside Orca, a multi-agent IDE. You are a dispatched worker.\n=== TASK ===\nfix X\n"
LAST = {}
N = [0]


def write_transcript(name, *records):
    path = TMP / f"{name}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return str(path)


def user(content):
    return {"type": "user", "message": {"content": content}}


def skill_call(tid, name="deliver-ticket", **extra):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid, "name": "Skill", "input": {"skill": name}}]}, **extra}


def skill_result(tid, text="Launching skill: deliver-ticket", error=False, **extra):
    return user([{"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": error}]) | extra


def hook(prompt, *records, tool="Edit", path=None, agent_type=None, agent_id=None, session=None, **extra):
    N[0] += 1
    session = session or f"s{N[0]}"
    transcript = write_transcript(f"t{N[0]}", user(prompt), *records)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "AGENT_SKILLS_"))}
    env.update(HOME=str(TMP), AGENT_SKILLS_STATE=str(STATE), AGENT_SKILLS_CONFIG=str(CONFIG),
               ORCA_TERMINAL_HANDLE="term_me")
    payload = {"hook_event_name": "PreToolUse", "session_id": session, "tool_name": tool,
               "tool_input": {"file_path": path or str(WORK / "a.py")}, "cwd": str(WORK),
               "transcript_path": transcript, "agent_type": agent_type, "agent_id": agent_id, **extra}
    out = subprocess.run([sys.executable, HERE / "guard.py"], input=json.dumps(payload), capture_output=True,
                         text=True, env=env).stdout
    parsed = json.loads(out) if out.strip() else {}
    LAST["warning"] = parsed.get("systemMessage") or ""
    return (parsed.get("hookSpecificOutput") or {}).get("permissionDecision") or "none"


def config(value):
    if value is None:
        CONFIG.unlink(missing_ok=True)
    else:
        CONFIG.write_text(json.dumps({"guard": {"deliver_load": value}}))


def warned():
    return "guard.deliver_load" in LAST["warning"]


CARD = "/goal fix X\nCLOSE: end-session §8\n"
config(None)

# No key: the edit goes through with one warning line, on every such edit until the skill is loaded.
assert hook(CARD, session="once") == "none" and warned()
assert hook(CARD, session="once") == "none" and warned() and LAST["warning"].count("\n") == 0
assert hook(PREAMBLE) == "none" and warned(), "a dispatched worker"
assert hook(CARD + "KEEP: next\n", tool="Write") == "none" and warned()

# Loaded: a Skill call with a good result, either name, or the slash command.
assert hook(CARD, skill_call("c1"), skill_result("c1")) == "none" and not warned()
assert hook(CARD, skill_call("c2", "agent-skills:deliver-ticket"), skill_result("c2")) == "none" and not warned()
assert hook(CARD, user("<command-name>/deliver-ticket</command-name>\n<command-message>x</command-message>")) == "none"
assert not warned()
assert hook(CARD, user("<command-name>/agent-skills:deliver-ticket</command-name>")) == "none" and not warned()
# Not loaded: a failed or unknown-skill result, another skill, a lookalike command, a subagent's call, an example.
assert hook(CARD, skill_call("c3"), skill_result("c3", "Unknown skill: deliver-ticket", True)) == "none" and warned()
assert hook(CARD, skill_call("c4"), skill_result("c4", "Unknown skill: deliver-ticket")) == "none" and warned()
assert hook(CARD, skill_call("c5", "write-ticket"), skill_result("c5", "ok")) == "none" and warned()
assert hook(CARD, user("<command-name>/deliver-ticket-extra</command-name>")) == "none" and warned()
assert hook(CARD, skill_call("c6", isSidechain=True), skill_result("c6", isSidechain=True)) == "none" and warned()
assert hook(CARD, user("run deliver-ticket first")) == "none" and warned()
# Unknown never warns: a call whose result is not there yet, an oversize record, an unreadable transcript.
assert hook(CARD, skill_call("c7")) == "none" and not warned()

# Not targets.
assert hook(CARD, agent_type="agent-skills:orchestrator") == "none" and not warned()
assert hook(CARD, agent_type="agent-skills:lead") == "none" and not warned()
assert hook("please fix the failing test") == "none" and not warned(), "a human-opened session"
assert hook(CARD, agent_id="sub1") == "none" and not warned(), "a subagent"
assert hook(CARD, path=str(TMP / "elsewhere.md")) == "none" and not warned(), "outside the working directory"
assert hook(CARD, path=str(WORK / ".git" / "config")) == "none" and not warned()
assert hook(CARD, path=str(WORK / ".." / "outside.md")) == "none" and not warned()
(TMP / "linked").symlink_to(TMP)
assert hook(CARD, path=str(WORK / "x" / ".." / ".." / "linked" / "n.md")) == "none" and not warned()
assert hook(CARD, path="rel/new.py") == "none" and warned(), "a relative path is read from the event's cwd"
assert hook(CARD + "LOAD: none (read-only)\n") == "none" and not warned()
assert hook(CARD + "LOAD  none\n") == "none" and not warned()
assert hook(CARD + "LOAD: none-other\n") == "none" and warned()
assert hook(CARD + "LOAD: deliver-ticket, never none\n") == "none" and warned()
assert hook(CARD, tool="Bash") == "none" and not warned(), "only edit tools are checked"

# Keys: deny holds every edit, ask asks, false and "off" skip, true is the default.
config("deny")
assert hook(CARD, session="d") == "deny" and warned() is False
assert hook(CARD, session="d") == "deny"
assert hook(CARD, skill_call("d1"), skill_result("d1")) == "none"
assert hook(CARD, skill_call("d2")) == "none", "unknown never blocks"
assert hook(CARD, path=str(TMP / "elsewhere.md")) == "none"
assert hook(CARD + "LOAD: none\n") == "none"
config("ask")
assert hook(CARD) == "ask"
for off in (False, "off"):
    config(off)
    assert hook(CARD) == "none" and not warned(), off
config(True)
assert hook(CARD) == "none" and warned()
config(None)

# Unknown, judged on the module: an unreadable file, an oversize record, a call whose result never came.
sys.path.insert(0, str(HERE))
from lib import skillload  # noqa: E402

assert skillload.loaded(str(TMP / "absent.jsonl")) is None
assert skillload.loaded(write_transcript("none", user("hi"))) is False
huge = write_transcript("huge", user("deliver-ticket " + "x" * (skillload.MAX_LINE + 1)))
assert skillload.loaded(huge) is None
unrelated = write_transcript("unrelated", user("x" * (skillload.MAX_LINE + 1)), user("hi"))
assert skillload.loaded(unrelated) is None, "an oversize record with no keyword is unknown too"
late = write_transcript("late", user("hi"))
assert skillload.loaded(late, time.monotonic() - skillload.BUDGET_S - 1) is None, "the budget counts from `start`"
assert skillload.loaded(late, time.monotonic()) is False
(TMP / "trunc.jsonl").write_text(json.dumps(user("hi")) + '\n{"type": "assistant", "mess')
assert skillload.loaded(str(TMP / "trunc.jsonl")) is False, "a half-written last line has no deliver-ticket in it"
(TMP / "trunc2.jsonl").write_text(json.dumps(user("hi")) + '\n{"type": "assistant", "name": "deliver-ticket", "mess')
assert skillload.loaded(str(TMP / "trunc2.jsonl")) is None, "a half-written line that may hold the call"

# Its own fault never drops a danger finding: the user-settings deny holds when the check raises.
import guard  # noqa: E402

os.environ.update(HOME=str(TMP), AGENT_SKILLS_STATE=str(STATE), AGENT_SKILLS_CONFIG=str(CONFIG))
(TMP / ".claude").mkdir(exist_ok=True)


def boom(*a, **k):
    raise RuntimeError("boom")


skillload.check_write = boom
event = {"tool_name": "Write", "tool_input": {"file_path": str(TMP / ".claude" / "settings.json")}, "cwd": str(WORK)}
found = guard.rules("Write", event["tool_input"], event, lambda: None)
assert any(f.level == "deny" for f in found), found
other = {"tool_name": "Write", "tool_input": {"file_path": str(WORK / "a.py")}, "cwd": str(WORK)}
assert guard.rules("Write", other["tool_input"], other, lambda: None) == []
print("ok")
