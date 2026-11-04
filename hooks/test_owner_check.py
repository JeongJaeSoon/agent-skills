"""owner_check.py: warns (or at `deny`, blocks once) when a final reply names owner items and the turn recorded none.
Run: python3 hooks/test_owner_check.py"""
import json, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(tempfile.mkdtemp())
STATE = TMP / "state"
CFG = TMP / "cfg.json"


def prompt(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def say(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


IDS = iter(range(10**6))


def bash(cmd, error=None):
    tid = f"t{next(IDS)}"
    use = {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": cmd}}]}}
    block = {"type": "tool_result", "tool_use_id": tid, "content": error or "ok"}
    if error:
        block["is_error"] = True
    return [use, {"type": "user", "message": {"role": "user", "content": [block]}}]


def rows():
    f = STATE / "owner-check.jsonl"
    out = [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []
    f.unlink(missing_ok=True)
    return out


def hook(entries, level=None, env=None, event=None):
    """(stdout json or None, state rows)."""
    path = TMP / "t.jsonl"
    path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))
    CFG.write_text(json.dumps({"guard": {"owner_check": level}} if level is not None else {}))
    ev = {"session_id": "s", "transcript_path": str(path), "hook_event_name": "Stop", "stop_hook_active": False}
    ev.update(event or {})
    out = subprocess.run([sys.executable, HERE / "owner_check.py"], input=json.dumps(ev), capture_output=True,
                         text=True, env={"PATH": "/usr/bin:/bin", "AGENT_SKILLS_CONFIG": str(CFG),
                                         "AGENT_SKILLS_STATE": str(STATE), **(env or {})})
    assert out.returncode == 0 and out.stderr == "", out
    return (json.loads(out.stdout) if out.stdout else None), rows()


CAUGHT = "작업은 끝났습니다.\n\n🙋 지금 오너가 할 일\n- 🙋 프로덕션 배포 승인 · 대시보드 → 릴리즈\n- 🙋 로그인 갱신 · 브라우저 → 수집"

# A reply with owner items and no record call: a one-line warning, a row, no block (the default level).
out, r = hook([prompt("go"), say(CAUGHT)])
assert out == {"systemMessage": "owner_check: 기록 안 된 오너 항목 3"}, out
assert len(r) == 1 and r[0]["items"] == 3 and r[0]["level"] == "warn" and r[0]["session"] == "s", r
assert hook([prompt("go"), say(CAUGHT)], level="warn")[0] == out
assert hook([prompt("go"), say(CAUGHT)], level="ask")[0] == out  # ask acts as warn at Stop

# Other phrasings count.
for text in ("이건 오너에게 물어봐야 합니다.", "오너 결정 필요: 범위를 줄일지.", "We should ask the owner about this.",
             "Owner approval needed before the merge."):
    out, r = hook([prompt("go"), say(text)])
    assert out and "기록 안 된 오너 항목 1" in out["systemMessage"] and r, text

# A record call earlier in the turn covers the reply.
for cmd in ('orch decide add --title "범위" --options a,b', 'orch board now "승인 · 대시보드 → 릴리즈"',
            "orch board set d3 --owner 오너", "orch board set d3 --owner=오너", "orch gate acme/app#1",
            "orch-dash inbox add --type approval --title x --key k", "python3 skills/orchestrate/scripts/prog.py decide add --title x",
            "cd /r && orca orchestration gate-create --task t1"):
    assert hook([prompt("go"), *bash(cmd), say(CAUGHT)]) == (None, []), cmd

# A call that does not store an item, or that was refused, covers nothing.
for cmd in ("orch board show", "orch board set d3 --after acme/app#1", "orch decide list", "echo orch decide add",
            "orch-dash inbox list", "ls"):
    assert hook([prompt("go"), *bash(cmd), say(CAUGHT)])[0], cmd
assert hook([prompt("go"), *bash("orch decide add --title x", error="The user doesn't want to proceed with this tool use."),
             say(CAUGHT)])[0]
# A record call from an earlier turn does not count.
assert hook([prompt("old"), *bash("orch decide add --title x"), say("ok"), prompt("go"), say(CAUGHT)])[0]

# Plain replies pass: nothing about the owner, no items, the legend, a table row, code and quotes.
for text in ("고쳤습니다. 테스트 통과.", "", "🙋 지금 오너가 할 일 — 없음", "🙋 오너 할 일: 없음.",
             "🔄 진행 · 🙋 오너 차례 · ⏳ 다른 사람 대기 · ⏸️ 보류",
             "| 🙋 | 알림 정리 | #50 | ↑ 오너 |", "```\n🙋 지금 오너가 할 일\n```", "> 🙋 인용된 줄",
             "`orch board now` 로 `🙋` 줄을 남긴다", "The owner of this module is the platform team."):
    assert hook([prompt("go"), say(text)]) == (None, []), text

# deny blocks once and writes no row; stop_hook_active, off and the env switch pass.
out, r = hook([prompt("go"), say(CAUGHT)], level="deny")
assert out["decision"] == "block" and "기록 안 된 오너 항목 3" in out["reason"] and "orch decide add" in out["reason"] and r == [], out
assert hook([prompt("go"), say(CAUGHT)], level="deny", event={"stop_hook_active": True}) == (None, [])
assert hook([prompt("go"), say(CAUGHT)], level="off") == (None, [])
assert hook([prompt("go"), say(CAUGHT)], level=False) == (None, [])
assert hook([prompt("go"), say(CAUGHT)], env={"AGENT_SKILLS_OWNER_CHECK": "0"}) == (None, [])
assert hook([prompt("go"), *bash("orch decide add --title x"), say(CAUGHT)], level="deny") == (None, [])

# SubagentStop reads the subagent's transcript; last_assistant_message wins over the transcript's text.
sub = TMP / "sub.jsonl"
sub.write_text("".join(json.dumps(e) + "\n" for e in [prompt("task"), say(CAUGHT)]))
out, r = hook([prompt("x")], event={"hook_event_name": "SubagentStop", "agent_transcript_path": str(sub), "agent_id": "a1"})
assert out and r[0]["agent"] == "a1" and r[0]["event"] == "SubagentStop", (out, r)
assert hook([prompt("go"), say("평범한 답변")], event={"last_assistant_message": CAUGHT})[0]
assert hook([prompt("go"), say(CAUGHT)], event={"last_assistant_message": "평범한 답변"}) == (None, [])

# A bad transcript path or stdin lets the stop through silently.
assert hook([prompt("go")], event={"transcript_path": str(TMP / "missing.jsonl")}) == (None, [])
bad = subprocess.run([sys.executable, HERE / "owner_check.py"], input="not json", capture_output=True, text=True,
                     env={"PATH": "/usr/bin:/bin", "AGENT_SKILLS_STATE": str(STATE)})
assert bad.returncode == 0 and bad.stdout == "" and bad.stderr == "", bad

# subagent_log writes no stop for a stop owner_check blocks at deny, and one at warn.
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "skills" / "orchestrate" / "scripts"))
import os
import subagent_log
os.environ["AGENT_SKILLS_CONFIG"] = str(CFG)
sub_event = {"hook_event_name": "SubagentStop", "agent_transcript_path": str(sub)}
CFG.write_text(json.dumps({"guard": {"owner_check": "deny"}}))
assert subagent_log.stop_passes(sub_event) is False
CFG.write_text("{}")
assert subagent_log.stop_passes(sub_event) is True
print("ok")
