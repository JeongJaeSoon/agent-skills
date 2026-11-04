"""decision_relay.py: warns once an orchestrator or lead relays the owner's words with no decision record behind them.
Run: python3 hooks/test_decision_relay.py"""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(os.path.realpath(tempfile.mkdtemp()))
STATE = TMP / "dashboard"
CONFIG = TMP / "agent-skills.json"
os.environ["ORCH_FLEET_STATE"] = str(STATE)
sys.path.insert(0, str(HERE.parent / "skills" / "orchestrate" / "scripts"))
import fleet, store  # noqa: E402

STATE.mkdir()
store.import_apply(checks=False)
did = fleet.decision_add("Ship?")["id"]
fleet.decision_close(did, "done", "진행하지 마세요", by="term_0FAKE1")
PROMPT = {"type": "user", "message": {"content": "placeholder prompt"}, "timestamp": "2026-11-04T00:00:00Z"}
FAILS = []


def transcript(*calls):
    """A transcript of this turn: (command, is_error or None for no result yet) in order."""
    lines = [PROMPT]
    for i, (cmd, err) in enumerate(calls):
        lines.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": cmd}}]}})
        if err is not None:
            lines.append({"type": "user", "toolUseResult": {}, "message": {"content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "is_error": err, "content": "x"}]}})
    path = TMP / f"tx-{abs(hash(calls))}.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines))
    return str(path)


def hook(cmd, agent="agent-skills:orchestrator", tx=None, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "AGENT_SKILLS_", "ORCA_"))}
    env.update(HOME=str(TMP), AGENT_SKILLS_STATE=str(TMP / "state"), AGENT_SKILLS_CONFIG=str(CONFIG))
    event = {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Bash", "tool_input": {"command": cmd},
             "cwd": str(TMP), "transcript_path": tx or transcript(), **extra}
    if agent:
        event["agent_type"] = agent
    r = subprocess.run([sys.executable, HERE / "decision_relay.py"], input=json.dumps(event), capture_output=True,
                       text=True, env=env)
    assert r.returncode == 0 and r.stderr == "", r
    return json.loads(r.stdout) if r.stdout else None


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


def warned(*a, **k):
    out = hook(*a, **k)
    return bool(out) and "orch decide answer" in out["hookSpecificOutput"]["additionalContext"] \
        and "permissionDecision" not in out["hookSpecificOutput"]


SEND = "orca orchestration send --to dispatch:ctx_x --subject s --body "
QUOTED = """'오너 답: "placeholder words"'"""

check("orchestration send, owner and a quote, no record: warns", warned(SEND + QUOTED))
check("orchestration reply: warns", warned("orca orchestration reply --id m1 --body " + QUOTED))
check("terminal send: warns", warned("orca terminal send --terminal t1 --enter --text " + QUOTED))
check("double-quoted body with escaped quotes: warns", warned(r'''orca orchestration reply --id m1 --body "오너 답: \"placeholder words\""'''))
check("curly quotes: warns", warned(SEND + "'오너: “placeholder words”'"))
check("corner quotes: warns", warned(SEND + "'오너: 「placeholder words」'"))
check("blockquote on the next line: warns", warned(SEND + "$'오너 원문:\\n> placeholder words'") or
      warned(SEND + "'오너 원문:\n> placeholder words'"))
check("a lead warns too", warned(SEND + QUOTED, agent="agent-skills:lead"))

body = TMP / "body.md"
body.write_text('오너 답: "placeholder words"\n')
check("body in a file: warns", warned(SEND + f'"$(cat {body})"'))
check("body file is not executed or echoed", 'placeholder' not in json.dumps(hook(SEND + f'"$(cat {body})"')))
check("single-quoted '$(cat)' is literal text, no file read", hook(SEND + f"'$(cat {body})'") is None)
check("a file body of a command that is no relay: quiet", hook(f'echo --body "$(cat {body})"') is None)
check("a file body of worker_done: quiet", hook(f'orca orchestration send --type worker_done --body "$(cat {body})"') is None)
check("worker_done beside a relay reads only the relay", warned(f'orca orchestration send --type worker_done --body "$(cat {body})"; {SEND}{QUOTED}'))
check("missing body file: no verdict", hook(SEND + f'"$(cat {TMP}/nope.md)"') is None)

check("no quote: quiet", hook(SEND + "'오너 승인 대기 중'") is None)
check("no owner: quiet", hook(SEND + """'리뷰어 답: "placeholder words"'""") is None)
check("a quote far from the owner line: quiet", hook(SEND + """'오너 승인 대기\n\n세부\n\n명령 "orch land" 실행'""") is None)
check("worker_done: quiet", hook("orca orchestration send --type worker_done --body " + QUOTED) is None)
check("another command: quiet", hook("echo " + QUOTED) is None)
check("a worker: quiet", hook(SEND + QUOTED, agent=None) is None)
check("a subagent: quiet", hook(SEND + QUOTED, agent_id="a1") is None)

ok = """'오너(%s, 2026-11-04): "진행하지 마세요"'""" % did
check("D3 citation of the stored answer, other turn: quiet", hook(SEND + ok) is None)
check("a part of the stored answer is not it: warns", warned(SEND + """'오너(%s, 2026-11-04): "진행"'""" % did))
check("an id with another answer: warns", warned(SEND + """'오너(%s, 2026-11-04): "placeholder words"'""" % did))
check("an id that is not done: warns", warned(SEND + """'오너(d999, 2026-11-04): "진행하지 마세요"'"""))
check("a bare id elsewhere does not excuse: warns", warned(SEND + """'이전 %s 참고. 오너: "진행하지 마세요"'""" % did))

ANSWER = "orch decide answer d1 --quote-file /x/q.md"
check("recorded earlier this turn: quiet", hook(SEND + QUOTED, tx=transcript((ANSWER, False))) is None)
check("`done` counts: quiet", hook(SEND + QUOTED, tx=transcript(("orch decide done d1 --answer x", False))) is None)
check("through python: quiet", hook(SEND + QUOTED, tx=transcript(("python3 /x/prog.py decide answer d1 --quote x", False))) is None)
check("a failed record: warns", warned(SEND + QUOTED, tx=transcript((ANSWER, True))))
check("a record the call's result cannot vouch for: warns", warned(SEND + QUOTED, tx=transcript((f"{ANSWER} || true", False))))
check("a record with no result yet: warns", warned(SEND + QUOTED, tx=transcript((ANSWER, None))))
check("the same call is no evidence: warns", warned(f"{ANSWER} && " + SEND + QUOTED))
check("a command that only mentions it: warns", warned(SEND + QUOTED, tx=transcript(("echo orch decide answer", False))))
check("a record of an earlier turn: warns", warned(SEND + QUOTED, tx=(lambda p: (pathlib.Path(p).write_text(
    pathlib.Path(p).read_text() + "\n" + json.dumps(PROMPT)), p)[1])(transcript((ANSWER, False)))))

CONFIG.write_text(json.dumps({"guard": {"decision_relay": "off"}}))
check("off: quiet", hook(SEND + QUOTED) is None)
CONFIG.unlink()
broken = subprocess.run([sys.executable, HERE / "decision_relay.py"], input="not json", capture_output=True, text=True,
                        env={"HOME": str(TMP), "AGENT_SKILLS_STATE": str(TMP / "state"), "PATH": "/usr/bin:/bin"})
check("a check that raises lets the call through", broken.returncode == 0 and broken.stdout == "")

sys.exit(1 if FAILS else 0)
