"""subagent_log.py: appends one subagents.jsonl line per observed subagent start and stop, writes no stop for one
claim_check blocks, and never blocks or prints, whatever its input or the disk does (names invented).
Run: python3 hooks/test_subagent_log.py"""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="test-subagent-log-"))
STATE = TMP / "dashboard"
LOG = STATE / "subagents.jsonl"
ENV = {"PATH": "/usr/bin:/bin", "ORCH_FLEET_STATE": str(STATE), "ORCA_TERMINAL_HANDLE": "term_0FAKE1"}


def hook(event=None, raw=None, env=None):
    out = subprocess.run([sys.executable, HERE / "subagent_log.py"], input=raw if raw is not None else json.dumps(event),
                         capture_output=True, text=True, env={**ENV, **(env or {})}, timeout=10)
    assert out.returncode == 0 and out.stdout == "" and out.stderr == "", out
    return out


def rows():
    return [json.loads(l) for l in LOG.read_text().splitlines()] if LOG.exists() else []


# A parent transcript, with Claude Code's per-agent meta file where it keeps one (not a documented hook field).
parent = TMP / "proj" / "sess-1.jsonl"
(parent.with_suffix("") / "subagents").mkdir(parents=True)
parent.write_text("")
(parent.with_suffix("") / "subagents" / "agent-a1.meta.json").write_text(json.dumps({"description": "Find stale cards"}))
start = {"session_id": "sess-1", "transcript_path": str(parent), "cwd": "/w/app", "hook_event_name": "SubagentStart",
         "agent_id": "a1", "agent_type": "Explore"}

hook(start)
r = rows()
assert len(r) == 1 and r[0]["event"] == "start" and r[0]["agent"] == "a1" and r[0]["claude_session"] == "sess-1", r
assert r[0]["type"] == "Explore" and r[0]["handle"] == "term_0FAKE1" and r[0]["cwd"] == "/w/app", r
assert r[0]["desc"] == "Find stale cards" and r[0]["v"] == 1 and r[0]["at"].endswith("Z"), r
assert oct(LOG.stat().st_mode & 0o777) == "0o600", oct(LOG.stat().st_mode)

# The stop: its own transcript beside the meta file. A turn that only read passes claim_check, so it is written.
sub = parent.with_suffix("") / "subagents" / "agent-a1.jsonl"
sub.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "look"}}) + "\n")
stop = {**start, "hook_event_name": "SubagentStop", "stop_hook_active": False, "agent_transcript_path": str(sub),
        "last_assistant_message": "Found two."}
hook(stop)
assert [x["event"] for x in rows()] == ["start", "stop"] and rows()[1]["desc"] == "Find stale cards", rows()

# A stop claim_check blocks (edited code, claims a fix, ran nothing after) is not an end: the subagent goes on.
claim = [{"type": "user", "message": {"role": "user", "content": "fix"}},
         {"type": "assistant", "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "e1", "name": "Edit", "input": {"file_path": "/w/app/a.py"}}]}},
         {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "e1", "content": "ok"}]}}]
sub2 = parent.with_suffix("") / "subagents" / "agent-a2.jsonl"
sub2.write_text("".join(json.dumps(e) + "\n" for e in claim))
blocked = {**stop, "agent_id": "a2", "agent_transcript_path": str(sub2), "last_assistant_message": "버그를 고쳤습니다."}
hook(blocked)
assert len(rows()) == 2, rows()
# The same stop once claim_check already blocked it, or with claim_check off, is written.
hook({**blocked, "stop_hook_active": True})
hook(blocked, env={"AGENT_SKILLS_CLAIM_CHECK": "0"})
assert [(x["agent"], x["event"]) for x in rows()[2:]] == [("a2", "stop"), ("a2", "stop")], rows()

# Fields the event lacks are null; no agent_id, a bad one, another event, or bad input writes nothing.
hook({"hook_event_name": "SubagentStart", "agent_id": "a3"})
assert rows()[-1] == {**rows()[-1], "agent": "a3", "claude_session": None, "type": None, "cwd": None, "desc": None}
n = len(rows())
for bad in ({"hook_event_name": "SubagentStart"}, {"hook_event_name": "SubagentStart", "agent_id": "../x"},
            {"hook_event_name": "SubagentStart", "agent_id": 7}, {"hook_event_name": "Stop", "agent_id": "a4"}, [1]):
    hook(bad)
hook(raw="not json")
hook(raw="")
assert len(rows()) == n, rows()[n:]
# Outside Orca (no terminal handle) nothing is written.
hook({**start, "agent_id": "a5"}, env={"ORCA_TERMINAL_HANDLE": ""})
assert len(rows()) == n

# A write that fails (the state directory is a file) ends quietly.
blocker = TMP / "not-a-dir"
blocker.write_text("")
hook({**start, "agent_id": "a6"}, env={"ORCH_FLEET_STATE": str(blocker)})
hook({**start, "agent_id": "a6"}, env={"ORCH_FLEET_STATE": str(blocker / "sub")})

# Past 1 MiB the log moves to .1 and the starts with no stop come along, so a long run outlives the rotation.
sys.path.insert(0, str(HERE.parent / "skills" / "orchestrate" / "scripts"))
os.environ["ORCH_FLEET_STATE"] = str(STATE)
import fleet  # noqa: E402
filler = json.dumps({"v": 1, "event": "stop", "at": "2026-10-13T10:00:00Z", "agent": "old", "claude_session": "x" * 80})
with open(LOG, "a") as f:
    f.write((filler + "\n") * (fleet.SUBAGENTS_ROTATE // len(filler) + 1))
open_before = {a for a, v in fleet.subagents(LOG).items() if v["started_at"] and not v["stopped_at"]}
assert open_before == {"a3"}, open_before
hook({**start, "agent_id": "a7"})
kept = [json.loads(l) for l in LOG.read_text().splitlines()]
assert [(x["agent"], x["event"]) for x in kept] == [("a3", "start"), ("a7", "start")], kept
assert (STATE / "subagents.jsonl.1").stat().st_size > fleet.SUBAGENTS_ROTATE
assert set(fleet.subagents(LOG)) == {"a3", "a7"}

# A rotation that cannot finish (.1 cannot be written) leaves the whole log where it was.
before = LOG.read_bytes()
filled = before + ((filler + "\n") * (fleet.SUBAGENTS_ROTATE // len(filler) + 1)).encode()
LOG.write_bytes(filled)
(STATE / "subagents.jsonl.1").unlink()
(STATE / "subagents.jsonl.1").mkdir()
hook({**start, "agent_id": "a8"})
assert LOG.read_bytes() == filled and set(fleet.subagents(LOG)) >= {"a3", "a7"}
(STATE / "subagents.jsonl.1").rmdir()
LOG.write_bytes(before)

# A held lock costs the hook at most SUBAGENT_LOCK_S, then it drops its event and still exits 0 with no output.
import fcntl, time  # noqa: E402
with open(STATE / "subagents.jsonl.lock", "a") as held:
    fcntl.flock(held, fcntl.LOCK_EX)
    t0 = time.monotonic()
    hook({**start, "agent_id": "a9"})
    assert time.monotonic() - t0 < 4 and "a9" not in fleet.subagents(LOG)
hook({**start, "agent_id": "a9"})
assert "a9" in fleet.subagents(LOG)

# A meta file that is not an object costs only the description.
(parent.with_suffix("") / "subagents" / "agent-a10.meta.json").write_text("[1, 2]")
hook({**start, "agent_id": "a10"})
assert fleet.subagents(LOG)["a10"]["desc"] is None and fleet.subagents(LOG)["a10"]["started_at"]

# A log unreadable for a moment gives the last good fold and is read again once readable.
good = fleet.subagents(LOG)
os.chmod(LOG, 0)
try:
    if not os.access(LOG, os.R_OK):  # root reads it anyway
        assert fleet.subagents(LOG) == good
        fleet._subagents.clear()  # nothing read yet: empty, and not kept for the unchanged file
        assert fleet.subagents(LOG) == {}
finally:
    os.chmod(LOG, 0o600)
assert fleet.subagents(LOG) == good
fleet.append_line(LOG, {"v": 1, "event": "stop", "at": "2026-11-03T00:00:00Z", "agent": "a9"})
assert fleet.subagents(LOG)["a9"]["stopped_at"] == "2026-11-03T00:00:00Z"

# Registered for both events, after claim_check on SubagentStop.
reg = json.loads((HERE / "hooks.json").read_text())["hooks"]
cmd = lambda ev: [h["command"] for m in reg[ev] for h in m["hooks"]]  # noqa: E731
assert any("subagent_log.py" in c for c in cmd("SubagentStart")), cmd("SubagentStart")
assert any("subagent_log.py" in c for c in cmd("SubagentStop")) and any("claim_check.py" in c for c in cmd("SubagentStop"))

print("test_subagent_log: ok")
