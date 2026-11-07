"""turn_check.py and claim_check's subagent warning: each check fires on its input, stays silent on the near miss, and
everything is silent while its key is unset.
Run: python3 hooks/test_turn_check.py"""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(tempfile.mkdtemp())
STATE, CFG = TMP / "state", TMP / "cfg.json"
os.environ.update(AGENT_SKILLS_STATE=str(STATE), AGENT_SKILLS_CONFIG=str(CFG), HOME=str(TMP))
sys.path.insert(0, str(HERE))

import claim_check, turn_check  # noqa: E402

PREAMBLE = "You are working inside Orca, a multi-agent IDE. You are a dispatched worker.\nTASK"
IDS = iter(range(10**6))


def prompt(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def say(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def call(name, inp, result="ok", error=False, background_id=None):
    tid = f"t{next(IDS)}"
    use = {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": tid, "name": name, "input": inp}]}}
    text = f"Command running in background with ID: {background_id}. Output is being written" if background_id else (
        error if isinstance(error, str) else result)
    block = {"type": "tool_result", "tool_use_id": tid, "content": text}
    if error:
        block["is_error"] = True
    return [use, {"type": "user", "message": {"role": "user", "content": [block]}}], tid


def bash(cmd, **kw):
    return call("Bash", {"command": cmd}, **kw)[0]


def notice(tid, status="completed", task="x"):
    return {"type": "queue-operation", "operation": "enqueue", "content":
            f"<task-notification> <task-id>{task}</task-id> <tool-use-id>{tid}</tool-use-id> <status>{status}</status> </task-notification>"}


def flat(*parts):
    out = []
    for p in parts:
        out += p if isinstance(p, list) else [p]
    return out


def run(entries, keys=None, first=None, event=None):
    """(parsed output or None, warning rows) of one in-process Stop with these keys on."""
    path = TMP / "t.jsonl"
    path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in ([prompt(first)] if first else []) + entries))
    CFG.write_text(json.dumps({"turn_check": keys} if keys is not None else {}))
    (STATE / "turn-warnings.jsonl").unlink(missing_ok=True)
    ev = {"session_id": "s", "transcript_path": str(path), "hook_event_name": "Stop", "cwd": str(TMP)}
    ev.update(event or {})
    out = turn_check.stop(ev, turn_check.config.load()) if ev["hook_event_name"] != "PreToolUse" else turn_check.telegram(ev, turn_check.config.load())
    f = STATE / "turn-warnings.jsonl"
    return out, [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []


ORCH = {"agent_type": "agent-skills:orchestrator"}
LEAD = {"agent_type": "agent-skills:lead"}
WORKER_FIRST = PREAMBLE


def fired(entries, key, first=None, event=None, extra=None):
    out, rows = run(entries, {key: True, **(extra or {})}, first, event)
    return out, [r["check"] for r in rows]


# ---- default off: nothing at all, whatever the turn holds ---------------------------------------------------------
BAD = flat(prompt("go"), say("말씀 주시면 티켓으로 만들겠습니다."))
assert run(BAD)[0] is None and run(BAD, {})[0] is None and run(BAD, {"offer_phrase": False})[0] is None
assert run(BAD, {"offer_phrase": "off"})[0] is None
assert not (STATE / "turn-warnings.jsonl").exists()

# ---- offer_phrase: any main session --------------------------------------------------------------------------------
out, checks = fired(BAD, "offer_phrase")
assert checks == ["offer_phrase"] and out["systemMessage"].startswith("turn_check: 떠넘기는"), out
assert fired(flat(prompt("go"), say("티켓 ABC-1을 만들었습니다.")), "offer_phrase")[0] is None
assert fired(flat(prompt("go"), say("원하시면 티켓으로 만들겠습니다.\n\n결과: 통과\n- a\n- b\n- c")), "offer_phrase")[0] is None  # not at the end
assert fired(flat(prompt("go"), say("> 말씀 주시면 진행하겠습니다.\n끝")), "offer_phrase")[0] is None  # a quote
assert fired(BAD, "offer_phrase", event={"agent_id": "a1"})[0] is None  # a subagent is not a main session
for key in ("deny", "warn", "ask"):  # nothing stronger than a warning exists: no block
    out, rows = run(BAD, {"offer_phrase": key})
    assert out and "decision" not in out and rows[0]["level"] == "warn", (key, out)

# ---- decision_registered: orchestrator and lead -------------------------------------------------------------------
ASK = say("A안과 B안 중 어느 쪽으로 갈까요?\n결정 필요: 범위")
assert fired(flat(prompt("go"), ASK), "decision_registered", event=ORCH)[1] == ["decision_registered"]
assert fired(flat(prompt("go"), ASK), "decision_registered", event=LEAD)[1] == ["decision_registered"]
assert fired(flat(prompt("go"), ASK), "decision_registered")[0] is None  # a general session
assert fired(flat(prompt("go"), bash("orch decide add --title x"), ASK), "decision_registered", event=ORCH)[0] is None
assert fired(flat(prompt("go"), bash("orch decide add --title x", error="<tool_use_error> no"), ASK), "decision_registered",
             event=ORCH)[1] == ["decision_registered"]  # a refused record is no record
assert fired(flat(prompt("go"), say("모두 끝냈습니다.")), "decision_registered", event=ORCH)[0] is None

# ---- brief_render: orchestrator and lead --------------------------------------------------------------------------
REPORT = say("## 요약\n- a\n\n🙋 지금 오너가 할 일\n- x\n\n⏳ 다른 사람 대기\n- y")
assert fired(flat(prompt("go"), REPORT), "brief_render", event=ORCH)[1] == ["brief_render"]
assert fired(flat(prompt("go"), bash("orch brief render --headline h --commit"), REPORT), "brief_render", event=ORCH)[0] is None
assert fired(flat(prompt("go"), say("## 요약\n- a")), "brief_render", event=ORCH)[0] is None  # one mark is not a report
assert fired(flat(prompt("go"), REPORT), "brief_render")[0] is None

# ---- wait_alive: orchestrator and lead ----------------------------------------------------------------------------
wait, wid = call("Monitor", {"command": "orch inbox --stream", "timeout_ms": 1800000})
assert fired(flat(prompt("go"), say("done")), "wait_alive", event=ORCH)[1] == ["wait_alive"]
assert fired(flat(prompt("go"), wait, say("waiting")), "wait_alive", event=ORCH)[0] is None
assert fired(flat(prompt("go"), wait, notice(wid), say("woke")), "wait_alive", event=ORCH)[1] == ["wait_alive"]
assert fired(flat(prompt("go"), wait, notice(wid, "running"), say("woke")), "wait_alive", event=ORCH)[0] is None
bg, bid = call("Bash", {"command": "orch wait prog", "run_in_background": True}, background_id="bk1")
assert fired(flat(prompt("go"), bg, say("ok")), "wait_alive", event=LEAD)[0] is None
stop_wait, _ = call("TaskStop", {"task_id": "bk1"})
assert fired(flat(prompt("go"), bg, stop_wait, say("ok")), "wait_alive", event=LEAD)[1] == ["wait_alive"]
# A notice that names only the task id (no tool-use id of ours) ends the wait it belongs to.
assert fired(flat(prompt("go"), bg, notice("other", task="bk1"), say("ok")), "wait_alive", event=LEAD)[1] == ["wait_alive"]
assert fired(flat(prompt("go"), bg, notice("other", task="bk2"), say("ok")), "wait_alive", event=LEAD)[0] is None
mon, _ = call("Monitor", {"command": "orch inbox --stream"}, result="Task ID: bk7")
assert fired(flat(prompt("go"), mon, notice("other", task="bk7"), say("ok")), "wait_alive", event=ORCH)[1] == ["wait_alive"]
assert fired(flat(prompt("go"), mon, say("ok")), "wait_alive", event=ORCH)[0] is None
fg, _ = call("Bash", {"command": "orch wait prog"})  # a foreground wait is no standing wait
assert fired(flat(prompt("go"), fg, say("ok")), "wait_alive", event=LEAD)[1] == ["wait_alive"]
assert fired(flat(prompt("go"), say("ok")), "wait_alive", first=PREAMBLE)[0] is None  # a worker keeps no wait
# A wait started turns ago, before the last prompt, still counts.
assert fired(flat(prompt("go"), wait, say("a"), prompt("again"), say("b")), "wait_alive", event=ORCH)[0] is None

# ---- stacks_down: any main session --------------------------------------------------------------------------------
up = bash("orch heavy - -- docker compose -p demo up -d")
assert fired(flat(prompt("go"), up, say("ok")), "stacks_down")[1] == ["stacks_down"]
assert "demo" in fired(flat(prompt("go"), up, say("ok")), "stacks_down")[0]["systemMessage"]
assert fired(flat(prompt("go"), up, bash("docker compose -p demo down"), say("ok")), "stacks_down")[0] is None
assert fired(flat(prompt("go"), up, bash("python3 reap.py ledger add --kind compose-stack --id demo --keep why --until x"),
                  say("ok")), "stacks_down")[0] is None
assert fired(flat(prompt("go"), up, bash("docker compose -p other down"), say("ok")), "stacks_down")[1] == ["stacks_down"]
assert fired(flat(prompt("go"), up, bash("docker compose -p demo down", error="Error: no such project"), say("ok")),
             "stacks_down")[1] == ["stacks_down"]  # a failed down took nothing down
assert fired(flat(prompt("go"), bash("echo compose -p demo up"), say("ok")), "stacks_down")[0] is None  # text, not a command
assert fired(flat(prompt("go"), up, bash("echo docker compose -p demo down"), say("ok")), "stacks_down")[1] == ["stacks_down"]
for fake_down in ("orch heavy - -- echo docker compose -p demo down", "docker run --rm alpine echo docker compose -p demo down",
                  "orch heavy - -- bash -c 'docker compose -p demo down'"):
    assert fired(flat(prompt("go"), up, bash(fake_down), say("ok")), "stacks_down")[1] == ["stacks_down"], fake_down
assert fired(flat(prompt("go"), up, bash("orch heavy agent-skills-ssot -- docker compose -p demo down"), say("ok")),
             "stacks_down")[0] is None
assert fired(flat(prompt("go"), bash("docker compose ps"), say("ok")), "stacks_down")[0] is None
assert fired(flat(prompt("go"), bash("docker compose -p demo up -d", error="Permission for this Bash was denied"),
                  say("ok")), "stacks_down")[0] is None
assert fired(flat(prompt("go"), up, say("ok")), "stacks_down", event={"agent_id": "a"})[0] is None

# ---- prs_actions --------------------------------------------------------------------------------------------------
acts = bash('python3 "$P/prs.py" actions', result='{"key":"a"}\n{"key":"b"}')
assert fired(flat(prompt("go"), acts, say("ok")), "prs_actions")[1] == ["prs_actions"]
assert fired(flat(prompt("go"), acts, bash("python3 prs.py log --key a --source x"), say("ok")), "prs_actions")[0] is None
assert fired(flat(prompt("go"), acts, bash("python3 prs.py log --key a --source x", error="disk full"), say("ok")),
             "prs_actions")[1] == ["prs_actions"]  # a log that failed recorded nothing
assert fired(flat(prompt("go"), bash("python3 prs.py actions", result=""), say("ok")), "prs_actions")[0] is None
assert fired(flat(prompt("go"), say("ok")), "prs_actions")[0] is None

# ---- review_idle: worker main and any subagent --------------------------------------------------------------------
review, _ = call("Skill", {"skill": "code-review"}, result="3 findings")
other, _ = call("Skill", {"skill": "tdd"}, result="ok")
agent, _ = call("Agent", {"subagent_type": "general-purpose", "description": "Codex review of the diff"}, result="report")
assert fired(flat(prompt("go"), review, say("리뷰 결과입니다.")), "review_idle", first=PREAMBLE)[1] == ["review_idle"]
assert fired(flat(prompt("go"), agent, say("보고 받음")), "review_idle", first=PREAMBLE)[1] == ["review_idle"]
assert fired(flat(prompt("go"), review, bash("git commit -m x"), say("fixed")), "review_idle", first=PREAMBLE)[0] is None
assert fired(flat(prompt("go"), other, say("ok")), "review_idle", first=PREAMBLE)[0] is None
challenge, _ = call("Skill", {"skill": "agent-skills:challenge"}, result="Act on: 1")
assert fired(flat(prompt("go"), challenge, say("판정입니다.")), "review_idle", first=PREAMBLE)[1] == ["review_idle"]
assert fired(flat(prompt("go"), review, say("x")), "review_idle")[0] is None  # a general main session
assert fired(flat(prompt("go"), review, say("x")), "review_idle", event={"agent_id": "a"})[1] == ["review_idle"]
codex = bash("codex exec review --base main", result="Act-on 0")
assert fired(flat(prompt("go"), codex, say("done")), "review_idle", first=PREAMBLE)[1] == ["review_idle"]
plain = bash("codex exec --sandbox read-only - < /tmp/p.md", result="notes")
assert fired(flat(prompt("go"), plain, say("done")), "review_idle", first=PREAMBLE)[0] is None  # not a review
bad_review = bash("codex exec review --base main", error="codex: not logged in")
assert fired(flat(prompt("go"), bad_review, say("done")), "review_idle", first=PREAMBLE)[0] is None  # a failed review
note = prompt("<task-notification> <tool-use-id>t9</tool-use-id> <status>completed</status> </task-notification>")
assert fired([note, say("알겠습니다")], "review_idle", first=PREAMBLE)[1] == ["review_idle"]
assert fired(flat(note, bash("gh pr view 1"), say("ok")), "review_idle", first=PREAMBLE)[0] is None

# ---- self_close: worker after worker_done, CLOSE card ---------------------------------------------------------------
done_cmd = 'orca orchestration send --from term_1 --type worker_done --subject s --body b --task-id t --outcome succeeded'
verdicts = []
turn_check.end_check_verdict = lambda mode, keep, cwd: verdicts.append((mode, keep)) or "close-terminal"
assert fired(flat(prompt("go"), bash(done_cmd), say("done")), "self_close", first=PREAMBLE)[1] == ["self_close"]
assert verdicts == [("worker", False)], verdicts
assert fired(flat(prompt("go"), say("done")), "self_close", first=PREAMBLE)[0] is None  # no worker_done yet
failed = done_cmd.replace("succeeded", "failed")
assert fired(flat(prompt("go"), bash(failed), say("done")), "self_close", first=PREAMBLE)[0] is None
assert fired(flat(prompt("go"), bash(done_cmd, error="<tool_use_error> x"), say("done")), "self_close",
             first=PREAMBLE)[0] is None
# A turn from before the worker_done still sees it: the transcript, not the turn, answers.
assert fired(flat(prompt("go"), bash(done_cmd), say("a"), prompt("next"), say("b")), "self_close", first=PREAMBLE)[1] == ["self_close"]
verdicts.clear()
CLOSE = "CLOSE   when merged\nKEEP    old api"
assert fired(flat(prompt("go"), say("ok")), "self_close", first="CLOSE        yes")[1] == ["self_close"]
assert verdicts == [("card", False)], verdicts
verdicts.clear()
assert fired(flat(prompt("go"), say("ok")), "self_close", first=CLOSE)[1] == ["self_close"]
assert verdicts == [("card", True)], verdicts  # a KEEP row is passed on
for verdict in ("keep", "ask", None):
    turn_check.end_check_verdict = lambda mode, keep, cwd, v=verdict: v
    assert fired(flat(prompt("go"), say("ok")), "self_close", first="CLOSE        yes")[0] is None, verdict
assert fired(flat(prompt("go"), say("ok")), "self_close")[0] is None  # a general session has no card to close
turn_check.end_check_verdict = lambda mode, keep, cwd: "close-terminal"
assert fired(flat(prompt("go"), say("ok")), "self_close", first="CLOSE        yes", event=ORCH)[0] is None

# end_check_verdict reads the JSON the script prints, and nothing else counts.
fake = TMP / "end_check.py"
fake.write_text('import sys\nprint(\'{"verdict": "close-terminal", "mode": "card"}\' if "--card" in sys.argv else "boom")\n')
real = turn_check.__dict__.copy()
import importlib  # noqa: E402
tc = importlib.reload(turn_check)
tc.END_CHECK = fake
assert tc.end_check_verdict("card", False, str(TMP)) == "close-terminal"
assert tc.end_check_verdict("worker", False, str(TMP)) is None

# ---- the checks are independent and a failing one does not hide the others ------------------------------------------
turn_check = tc
boom = lambda *a: 1 / 0  # noqa: E731
tc.check_offer = boom
out, rows = run(flat(prompt("go"), ASK), {"offer_phrase": True, "decision_registered": True}, event=ORCH)
assert out and [r["check"] for r in rows] == ["decision_registered"], (out, rows)  # the other check still warns
assert "turn_check.offer_phrase" in (STATE / "guard-errors.jsonl").read_text()
tc = importlib.reload(turn_check)
# A timeout of end-check and a warning row that cannot be written are logged, not swallowed.
(STATE / "guard-errors.jsonl").unlink()
tc.END_CHECK = TMP / "hang.py"
(TMP / "hang.py").write_text("import time\ntime.sleep(60)\n")
real_run = subprocess.run
subprocess.run = lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("x", 1))
try:
    assert tc.end_check_verdict("card", False, str(TMP)) is None
finally:
    subprocess.run = real_run
assert "turn_check.end_check" in (STATE / "guard-errors.jsonl").read_text()
tc = importlib.reload(turn_check)
(STATE / "guard-errors.jsonl").unlink()
(STATE / "turn-warnings.jsonl").unlink(missing_ok=True)
os.environ["AGENT_SKILLS_STATE"] = "/dev/null/nope"
tc.config.append_warning({"check": "x"})
os.environ["AGENT_SKILLS_STATE"] = str(STATE)
assert not (STATE / "turn-warnings.jsonl").exists()

# ---- Telegram PreToolUse: telegram_render --------------------------------------------------------------------------
tg = {"hook_event_name": "PreToolUse", "tool_name": "mcp__plugin_telegram_telegram__reply",
      "tool_input": {"chat_id": "1", "text": "## 요약\n🙋 지금 오너가 할 일\n- x\n⏳ 다른 사람 대기\n- y"}, **ORCH}
out, rows = run(flat(prompt("go"), say("sending")), {"telegram_render": True}, event=tg)
assert out and "orch brief render" in out["systemMessage"] and rows[0]["check"] == "telegram_render", (out, rows)
assert run(flat(prompt("go"), bash("orch brief render --telegram --headline h --commit"), say("x")),
           {"telegram_render": True}, event=tg)[0] is None
assert run(flat(prompt("go"), say("x")), {}, event=tg)[0] is None  # off
assert run(flat(prompt("go"), say("x")), {"telegram_render": True}, event={**tg, "tool_input": {"text": "ok, 확인"}})[0] is None
assert run(flat(prompt("go"), say("x")), {"telegram_render": True}, event={**tg, "tool_name": "Bash"})[0] is None
assert run(flat(prompt("go"), say("x")), {"telegram_render": True}, event={k: v for k, v in tg.items() if k != "agent_type"})[0] is None

# ---- claim_check.subagent ------------------------------------------------------------------------------------------
sub, _ = call("Agent", {"subagent_type": "general-purpose", "description": "fix it"}, result="done")
CLAIM = say("서브에이전트가 끝내서 모두 완료했습니다.")


def claim(entries, key):
    path = TMP / "c.jsonl"
    path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))
    CFG.write_text(json.dumps({"claim_check": {"subagent": key}} if key is not None else {}))
    (STATE / "turn-warnings.jsonl").unlink(missing_ok=True)
    p = subprocess.run([sys.executable, HERE / "claim_check.py"], capture_output=True, text=True, input=json.dumps(
        {"session_id": "s", "transcript_path": str(path), "hook_event_name": "Stop"}),
        env={"PATH": "/usr/bin:/bin", "AGENT_SKILLS_CONFIG": str(CFG), "AGENT_SKILLS_STATE": str(STATE), "HOME": str(TMP)})
    assert p.returncode == 0 and p.stderr == "", p
    f = STATE / "turn-warnings.jsonl"
    return (json.loads(p.stdout) if p.stdout else None), [json.loads(l)["check"] for l in f.read_text().splitlines()] if f.exists() else []


out, rows = claim(flat(prompt("go"), sub, CLAIM), True)
assert out and "subagent" in out["systemMessage"] and "decision" not in out and rows == ["claim_subagent"], (out, rows)
assert claim(flat(prompt("go"), sub, CLAIM), None) == (None, [])  # off until set
assert claim(flat(prompt("go"), sub, CLAIM), "off") == (None, [])
assert claim(flat(prompt("go"), sub, bash("pytest -q"), CLAIM), True) == (None, [])  # its own run after the subagent
assert claim(flat(prompt("go"), bash("pytest -q"), sub, CLAIM), True)[1] == ["claim_subagent"]  # a run before does not count
assert claim(flat(prompt("go"), sub, say("서브에이전트 보고입니다. 검증하지 않았습니다. 완료했습니다.")), True) == (None, [])
assert claim(flat(prompt("go"), sub, say("보고를 받았습니다.")), True) == (None, [])  # no claim
assert claim(flat(prompt("go"), CLAIM), True) == (None, [])  # no subagent

# ---- the whole hook as Claude Code runs it: silent while off, a JSON line when on ----------------------------------
def hook(entries, keys, event):
    path = TMP / "h.jsonl"
    path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))
    CFG.write_text(json.dumps({"turn_check": keys} if keys is not None else {}))
    ev = {"session_id": "s", "transcript_path": str(path), "hook_event_name": "Stop", **event}
    p = subprocess.run([sys.executable, HERE / "turn_check.py"], capture_output=True, text=True, input=json.dumps(ev),
                       env={"PATH": "/usr/bin:/bin", "AGENT_SKILLS_CONFIG": str(CFG), "AGENT_SKILLS_STATE": str(STATE),
                            "HOME": str(TMP)})
    assert p.returncode == 0 and p.stderr == "", p
    return p.stdout


assert hook(BAD, None, {}) == "" and hook(BAD, {}, {}) == "" and hook(BAD, {"offer_phrase": "off"}, {}) == ""
assert json.loads(hook(BAD, {"offer_phrase": True}, {}))["systemMessage"].startswith("turn_check:")
assert hook(BAD, {"offer_phrase": True}, {"stop_hook_active": True}) == ""
assert hook(BAD, {"offer_phrase": True}, {"transcript_path": "/nonexistent"}) == ""

print("ok")
