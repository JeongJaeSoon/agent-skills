"""claim_check.py: blocks a stop once when the turn edited code, claims completion, and ran nothing after the last edit.
Run: python3 hooks/test_claim_check.py"""
import json, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(tempfile.mkdtemp())


def prompt(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def say(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def tool(name, **args):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "name": name, "input": args}]}}


def result():
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}}


def edit(path):
    return [tool("Edit", file_path=path), result()]


def bash(cmd="python3 -m pytest"):
    return [tool("Bash", command=cmd), result()]


def hook(entries, env=None, event=None, raw=None):
    path = TMP / "t.jsonl"
    path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries) + "not json\n")
    ev = {"session_id": "s", "transcript_path": str(path), "hook_event_name": "Stop", "stop_hook_active": False}
    ev.update(event or {})
    out = subprocess.run([sys.executable, HERE / "claim_check.py"], input=raw if raw is not None else json.dumps(ev),
                         capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", **(env or {})})
    assert out.returncode == 0 and out.stderr == "", out  # never a loud failure
    if not out.stdout:
        return None
    d = json.loads(out.stdout)
    assert d["decision"] == "block", d
    return d["reason"]


OLD = [prompt("earlier task"), *edit("/r/old.py"), say("고쳤습니다.")]  # a previous turn never counts

# Blocks: code edited, a past-tense claim, no run after the last edit. The reason names the file and the claim.
r = hook(OLD + [prompt("fix it"), *edit("/r/app.py"), say("버그를 고쳤습니다.")])
assert r and "app.py" in r and "고쳤" in r and "old.py" not in r and "AGENT_SKILLS_CLAIM_CHECK=0" in r, r
assert hook([prompt("x"), *edit("/r/a.py"), say("수정했고 모든 테스트가 통과했습니다.")])
assert hook([prompt("x"), *edit("/r/a.ts"), say("I've fixed the race in the loader.")])
assert hook([prompt("x"), *edit("/r/a.go"), say("Done. All tests pass.")])
assert hook([prompt("x"), tool("Write", file_path="/r/new.py"), result(), say("구현 완료했습니다.")])
# A run before the last edit is stale: the run that counts comes after it.
assert hook([prompt("x"), *edit("/r/a.py"), *bash(), *edit("/r/a.py"), say("고쳤습니다.")])
# A Telegram reply sent this turn counts as a claim, even when the final text says nothing.
tg = tool("mcp__plugin_telegram_telegram__reply", chat_id="1", text="배포 버그 해결했습니다")
assert hook([prompt("x"), *edit("/r/a.py"), tg, result(), say("보냈습니다.")])
# SubagentStop reads the subagent's own transcript, and last_assistant_message wins over the transcript's text.
sub = TMP / "sub.jsonl"
sub.write_text("".join(json.dumps(e) + "\n" for e in [prompt("task"), *edit("/r/s.py"), say("Fixed it.")]))
assert hook([prompt("x")], event={"hook_event_name": "SubagentStop", "agent_transcript_path": str(sub)})
assert hook([prompt("x"), *edit("/r/a.py"), say("이 파일을 봤다.")], event={"last_assistant_message": "고쳤다."})

# Passes.
assert hook([prompt("x"), *edit("/r/a.py"), *bash(), say("고쳤습니다. 테스트 12개 통과.")]) is None    # ran after the edit
assert hook([prompt("x"), *edit("/r/README.md"), say("문서를 고쳤습니다.")]) is None                 # prose-only turn
assert hook([prompt("x"), *bash("rg foo"), say("원인을 찾아 해결했습니다.")]) is None                # no edit: investigation
assert hook([prompt("x"), *edit("/r/a.py"), say("고쳤지만 테스트는 돌리지 않았습니다.")]) is None      # says unverified
assert hook([prompt("x"), *edit("/r/a.py"), say("Changed the parser; not yet tested.")]) is None
assert hook([prompt("x"), *edit("/r/a.py"), say("남은 것은 완료 조건 확인이다.")]) is None             # noun, not a claim
assert hook([prompt("x"), *edit("/r/a.py"), say("a fixed-width column and a fixed size")]) is None
assert hook([prompt("x"), *edit("/r/a.py"), say("```\nfixed\n```\n`done`\n> 고쳤습니다\n바꿨다.")]) is None  # code, quotes
assert hook([prompt("x"), *edit("/r/a.py"), say("고쳤습니다.")], event={"stop_hook_active": True}) is None  # blocks once
assert hook([prompt("x"), *edit("/r/a.py"), say("고쳤습니다.")], env={"AGENT_SKILLS_CLAIM_CHECK": "0"}) is None
# A tool result or injected context mid-turn is not a new prompt: the edit before it still counts.
meta = {"type": "user", "isMeta": True, "message": {"role": "user", "content": [{"type": "text", "text": "skill body"}]}}
assert hook([prompt("x"), *edit("/r/a.py"), meta, say("고쳤습니다.")])
# Bad input never blocks.
assert hook([], raw="not json") is None
assert hook([], event={"transcript_path": str(TMP / "missing.jsonl")}) is None
print("test_claim_check: ok")
