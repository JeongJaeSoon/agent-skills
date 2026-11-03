"""hooks/lifecycle.py run as Claude Code runs it: the event JSON on stdin, a transcript file beside it.
Run: python3 hooks/test_lifecycle.py"""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-lifecycle-"))
ENV = dict(os.environ, AGENT_SKILLS_STATE=str(tmp / "state"), AGENT_SKILLS_CONFIG=str(tmp / "cfg.json"))
PREAMBLE = ("You are working inside Orca, a multi-agent IDE. You are a dispatched worker.\n"
            "orca orchestration send --type worker_done --task-id task_1 --dispatch-id ctx_1 --outcome succeeded\n"
            "=== TASK ===\nGOAL ship it")
CLOSE_CARD = "/goal ABC-1.\nCLOSE: 완료 기준을 채우고 결과를 적으면 end-session §8대로 같은 턴에 스스로 닫는다"
DONE = "orca orchestration send --type worker_done --subject done --body x --task-id task_1 --dispatch-id ctx_1 --outcome succeeded"
RM = "orca worktree rm --worktree current --json"
failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def use(mid, tid, cmd):
    return {"type": "assistant", "message": {"id": mid, "role": "assistant", "content": [
        {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": cmd}}]}}


def result(tid, text, error=False):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text, **({"is_error": True} if error else {})}]}}


def hook(rows, event, cfg=None):
    path = tmp / "t.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    (tmp / "cfg.json").write_text(json.dumps(cfg or {}))
    event = {"hook_event_name": "PreToolUse", "session_id": "s1", "transcript_path": str(path), **event}
    p = subprocess.run([sys.executable, str(HERE / "lifecycle.py")], input=json.dumps(event), capture_output=True,
                       text=True, env=ENV, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else {}


def rm_event(tid="tu_rm", cmd=RM):
    return {"tool_name": "Bash", "tool_use_id": tid, "tool_input": {"command": cmd}}


DENY = {"guard": {"exit_order": "deny"}}


def decision(out):
    return (out.get("hookSpecificOutput") or {}).get("permissionDecision")


# A worker_done and an rm sent in one message: the rm has no worker_done result before it. Denied at deny level.
same = [user(PREAMBLE), use("m1", "tu_done", DONE), use("m1", "tu_rm", RM)]
out = hook(same, rm_event(), DENY)
check("parallel worker_done + rm is denied", decision(out) == "deny" and "ctx_1" in out["hookSpecificOutput"]["permissionDecisionReason"], out)
# Even when the worker_done's result was written first: it is still the same message.
out = hook(same[:2] + [result("tu_done", '{"ok": true}'), same[2]], rm_event(), DENY)
check("same-message rm is denied though worker_done's result is in", decision(out) == "deny", out)
# The result came back in an earlier message: the rm passes.
later = [user(PREAMBLE), use("m1", "tu_done", DONE), result("tu_done", '{"ok":true,"result":{}}'), use("m2", "tu_rm", RM)]
out = hook(later, rm_event(), DENY)
check("rm after worker_done's ok result passes", out == {}, out)
out = hook(later[:2] + [result("tu_done", "messageId=msg_123")] + later[3:], rm_event(), DENY)
check("the plain `messageId=` receipt counts as ok", out == {}, out)
# A refused or failed worker_done does not count.
out = hook(later[:2] + [result("tu_done", "Permission denied", error=True)] + later[3:], rm_event(), DENY)
check("an errored worker_done does not open the rm", decision(out) == "deny", out)
# The worker_done of an earlier Dispatch does not count for the current one.
old = [user(PREAMBLE), use("m1", "tu_done", DONE), result("tu_done", '{"ok": true}'),
       user(PREAMBLE.replace("ctx_1", "ctx_2")), use("m3", "tu_rm", RM)]
out = hook(old, rm_event(), DENY)
check("an old Dispatch's worker_done does not count", decision(out) == "deny" and "ctx_2" in out["hookSpecificOutput"]["permissionDecisionReason"], out)
# worker_done and rm chained in one command: no earlier result.
chained = [user(PREAMBLE), use("m1", "tu_rm", DONE + " && " + RM)]
out = hook(chained, rm_event(cmd=DONE + " && " + RM), DENY)
check("worker_done && rm in one command is denied", decision(out) == "deny", out)
# The default level is warn: the call goes through with the warning.
out = hook(same, rm_event())
check("default warn lets it through with a warning", decision(out) is None and "worker_done" in out.get("systemMessage", ""), out)
out = hook(same, rm_event(), {"guard": {"exit_order": "off"}})
check("off skips it", out == {}, out)
# A KEEP row refuses the rm whatever came before.
out = hook([user(PREAMBLE + "\nKEEP        the review round")] + later[1:], rm_event(), DENY)
check("a KEEP card's rm is denied", decision(out) == "deny" and "KEEP" in out["hookSpecificOutput"]["permissionDecisionReason"], out)
# A CLOSE card: rm only after end-check --card said remove, in an earlier message.
EC = "skills-sync end-check --card --no-keep"
card = [user(CLOSE_CARD), use("m1", "tu_ec", EC), result("tu_ec", '{"verdict": "remove", "reasons": []}'), use("m2", "tu_rm", RM)]
check("CLOSE card rm after end-check remove passes", hook(card, rm_event(), DENY) == {})
out = hook(card[:2] + [result("tu_ec", '{"verdict": "keep", "reasons": ["open PR"]}')] + card[3:], rm_event(), DENY)
check("CLOSE card rm after end-check keep is denied", decision(out) == "deny", out)
out = hook([user(CLOSE_CARD), use("m2", "tu_rm", RM)], rm_event(), DENY)
check("CLOSE card rm without end-check is denied", decision(out) == "deny" and "end-check" in out["hookSpecificOutput"]["permissionDecisionReason"], out)
# Not a worker: a human's session, or a subagent, is not checked; other commands are not either.
check("a general session's rm is not checked", hook([user("clean up this card"), use("m1", "tu_rm", RM)], rm_event(), DENY) == {})
check("a subagent's rm is not checked", hook(same, {**rm_event(), "agent_id": "a1"}, DENY) == {})
check("other commands pass", hook(same, rm_event(cmd="git status"), DENY) == {})
check("quoted rm text is not an rm", hook(same, rm_event(cmd="echo 'orca worktree rm --worktree current'"), DENY) == {})

# Agent prompts from a role session get the refused-act paragraph appended, once.
agent = {"tool_name": "Agent", "tool_input": {"description": "probe", "prompt": "Read the diff.", "subagent_type": "Explore"}}
out = hook([user(PREAMBLE)], agent)
hs = out.get("hookSpecificOutput") or {}
upd = hs.get("updatedInput") or {}
check("a worker's Agent prompt gets the paragraph", hs.get("permissionDecision") == "allow"
      and upd.get("prompt", "").startswith("Read the diff.\n\nAn act refused by a permission prompt")
      and upd["prompt"].rstrip().endswith("run it again.") and upd.get("subagent_type") == "Explore", out)
check("a prompt already carrying it is left alone", hook([user(PREAMBLE)], {**agent, "tool_input": upd}) == {})
check("a general session's Agent call is left alone", hook([user("look at this")], agent) == {})
check("the key turns it off", hook([user(PREAMBLE)], agent, {"orchestrate": {"agent_forbidden": False}}) == {})
lead = "acme: lead\nMODE        Run `orchestrate` in program mode.\nROLE: lead acme\n"
check("a lead's Agent prompt gets it too", "updatedInput" in (hook([user(lead)], agent).get("hookSpecificOutput") or {}))
check("a broken input passes", subprocess.run([sys.executable, str(HERE / "lifecycle.py")], input="nope",
                                               capture_output=True, text=True, env=ENV).stdout == "")

print(f"\n{len(failures)} failure(s)" if failures else "\nall passed")
sys.exit(1 if failures else 0)
