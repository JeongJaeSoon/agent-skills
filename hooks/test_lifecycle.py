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
# Only a whole receipt counts: an unread tail, an ok:true nested under a failure, or a list result with an unread
# part does not.
for text in ('{"ok": true}\n{unread tail', '{"ok": false, "result": {"ok": true}}', 'sent, messageId=msg_1 maybe',
             [{"type": "text", "text": '{"ok": true}'}, 7]):
    out = hook(later[:2] + [result("tu_done", text)] + later[3:], rm_event(), DENY)
    check(f"a worker_done result {str(text)[:24]!r} is no receipt", decision(out) == "deny", out)
out = hook(later[:2] + [result("tu_done", [{"type": "text", "text": '{"ok": true}'}])] + later[3:], rm_event(), DENY)
check("a receipt in text blocks counts", out == {}, out)
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
# The receipt is the exact send's own: another Dispatch id that shares a prefix, the id only in the body, or an ok
# printed by another command beside the send does not count.
out = hook([user(PREAMBLE), use("m1", "tu_done", DONE.replace("ctx_1", "ctx_10")), result("tu_done", '{"ok": true}'),
            use("m2", "tu_rm", RM)], rm_event(), DENY)
check("worker_done for ctx_10 does not open ctx_1's rm", decision(out) == "deny", out)
body = DONE.replace("--body x", "--body ctx_1").replace("--dispatch-id ctx_1", "--dispatch-id ctx_0")
out = hook([user(PREAMBLE), use("m1", "tu_done", body), result("tu_done", '{"ok": true}'), use("m2", "tu_rm", RM)],
           rm_event(), DENY)
check("ctx_1 in the body of another Dispatch's send does not count", decision(out) == "deny", out)
mixed = DONE + "; orca worktree list --json"
out = hook([user(PREAMBLE), use("m1", "tu_done", mixed), result("tu_done", 'refused\n{"ok": true}'),
            use("m2", "tu_rm", RM)], rm_event(), DENY)
check("an ok printed beside the send in one call does not count", decision(out) == "deny", out)
out = hook([user(PREAMBLE), use("m1", "tu_done", DONE + " --dispatch-id ctx_2"), result("tu_done", '{"ok": true}'),
            use("m2", "tu_rm", RM)], rm_event(), DENY)
check("a send naming two Dispatch ids does not count", decision(out) == "deny", out)
# A card whose session declared `ROLE: none` is still a dispatched card.
(tmp / "state" / "roles").mkdir(parents=True, exist_ok=True)
(tmp / "state" / "roles" / "s1.json").write_text('{"role": "none"}')
out = hook(same, rm_event(), DENY)
check("ROLE: none does not lift the exit order", decision(out) == "deny", out)
(tmp / "state" / "roles" / "s1.json").unlink()
# Only the caller's own card: a finished child removed by its path is not this card's end.
child = "orca worktree rm --worktree path:/wt/finished-child --json"
out = hook([user(PREAMBLE), use("m1", "tu_rm", child)], {**rm_event(cmd=child), "cwd": "/wt/me"}, DENY)
check("removing another card by path is not checked", out == {}, out)
own = "orca worktree rm --worktree path:/wt/me --json"
out = hook([user(PREAMBLE), use("m1", "tu_rm", own)], {**rm_event(cmd=own), "cwd": "/wt/me/sub"}, DENY)
check("removing its own card by path is checked", decision(out) == "deny", out)
for target in ("path:$PWD", "path:.", "path:/wt/me/sub/..", "path:~/wt/me"):
    cmd = f"orca worktree rm --worktree {target} --json"
    out = hook([user(PREAMBLE), use("m1", "tu_rm", cmd)], {**rm_event(cmd=cmd), "cwd": "/wt/me"}, DENY)
    check(f"{target} may be its own card and is checked", decision(out) == "deny", out)
# An unreadable transcript: the order is unknown, so the rm of its own card is refused at deny.
blind = {**rm_event(), "transcript_path": str(tmp / "missing.jsonl")}
(tmp / "cfg.json").write_text(json.dumps(DENY))
p = subprocess.run([sys.executable, str(HERE / "lifecycle.py")], capture_output=True, text=True, env=ENV, timeout=30,
                   input=json.dumps({"hook_event_name": "PreToolUse", "session_id": "s1", **blind}))
check("an unreadable transcript denies its own card's rm", decision(json.loads(p.stdout or "{}")) == "deny", p.stdout)
# An empty transcript, or a broken line before the card's prompt: its first prompt is unread, so the same.
for body in ("", "{not json\n" + json.dumps(user(CLOSE_CARD)) + "\n"):
    (tmp / "odd.jsonl").write_text(body)
    p = subprocess.run([sys.executable, str(HERE / "lifecycle.py")], capture_output=True, text=True, env=ENV, timeout=30,
                       input=json.dumps({"hook_event_name": "PreToolUse", "session_id": "s1", **rm_event(),
                                         "transcript_path": str(tmp / "odd.jsonl")}))
    check(f"an unread first prompt denies its own card's rm ({body[:9]!r})",
          decision(json.loads(p.stdout or "{}")) == "deny", p.stdout)
# A dispatched lead ends like a worker.
lead_worker = PREAMBLE + "\nMODE        Run `orchestrate` in program mode.\nROLE: lead acme\n"
out = hook([user(lead_worker), use("m1", "tu_done", DONE), use("m1", "tu_rm", RM)], rm_event(), DENY)
check("a dispatched lead's parallel rm is denied", decision(out) == "deny", out)
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
card = [user(CLOSE_CARD), use("m1", "tu_ec", EC), result("tu_ec", '{"verdict": "remove", "mode": "card", "reasons": []}'), use("m2", "tu_rm", RM)]
check("CLOSE card rm after end-check remove passes", hook(card, rm_event(), DENY) == {})
out = hook(card[:2] + [result("tu_ec", '{"verdict": "keep", "reasons": ["open PR"]}')] + card[3:], rm_event(), DENY)
check("CLOSE card rm after end-check keep is denied", decision(out) == "deny", out)
warned = card[:2] + [result("tu_ec", '[skills-sync end-check] warning: x\n{"verdict": "remove",\n "mode": "card", "reasons": []}')] + card[3:]
check("a warning line before the JSON is read past", hook(warned, rm_event(), DENY) == {})
stale = [user(CLOSE_CARD), use("m1", "tu_ec", EC + "; cat old.json"),
         result("tu_ec", '{"verdict": "keep"}\n{"verdict": "remove"}'), use("m2", "tu_rm", RM)]
check("end-check beside another command does not count", decision(hook(stale, rm_event(), DENY)) == "deny")
later_keep = card[:3] + [use("m2", "tu_ec2", EC), result("tu_ec2", '{"verdict": "keep", "mode": "card"}'),
                         use("m3", "tu_rm", RM)]
check("a later keep takes back an earlier remove", decision(hook(later_keep, rm_event(), DENY)) == "deny")
odd_msg = card[:3] + [{"type": "assistant", "message": {"id": "m9", "content": 7}}] + card[3:]
check("a message after the remove whose content is unread denies the rm",
      decision(hook(odd_msg, rm_event(), DENY)) == "deny")
null_text = card[:3] + [{"type": "user", "message": {"content": [{"type": "text", "text": None}]}}] + card[3:]
check("a user message after the remove whose text is unread denies the rm",
      decision(hook(null_text, rm_event(), DENY)) == "deny")
odd_cmd = card[:3] + [use("m9", "tu_x", 7)] + card[3:]
check("a Bash call after the remove whose command is unread denies the rm",
      decision(hook(odd_cmd, rm_event(), DENY)) == "deny")
# Output that is not wholly warnings and one JSON object is unread: a cut-off second verdict, a stray line first,
# or a result whose content is no text at all.
for name, text in (("a trailing unread verdict", '{"mode": "card", "verdict": "remove"}\n{unread later verdict'),
                   ("a line that is no warning", 'note\n{"mode": "card", "verdict": "remove"}'),
                   ("a number for content", 7),
                   ("a list with an unread part", [{"type": "text", "text": '{"mode": "card", "verdict": "remove"}'}, 7])):
    out = hook(card[:2] + [result("tu_ec", text)] + card[3:], rm_event(), DENY)
    check(f"{name} in end-check's result denies the rm", decision(out) == "deny", out)
# A broken line after an earlier remove may be a later keep, and bytes that are not UTF-8 are no transcript at all.
for name, tail in (("a broken line", b"{a later end-check result\n"), ("bad UTF-8", b'{"type": "user", "x": "\xff"}\n')):
    body = "".join(json.dumps(r) + "\n" for r in card[:3]).encode() + tail + (json.dumps(card[3]) + "\n").encode()
    (tmp / "odd.jsonl").write_bytes(body)
    p = subprocess.run([sys.executable, str(HERE / "lifecycle.py")], capture_output=True, text=True, env=ENV, timeout=30,
                       input=json.dumps({"hook_event_name": "PreToolUse", "session_id": "s1", **rm_event(),
                                         "transcript_path": str(tmp / "odd.jsonl")}))
    check(f"{name} after an earlier remove denies the rm", decision(json.loads(p.stdout or "{}")) == "deny", p.stdout)
asbrief = [user(CLOSE_CARD), use("m1", "tu_ec", "skills-sync end-check --brief --card"),
           result("tu_ec", '{"verdict": "remove", "mode": "user"}'), use("m2", "tu_rm", RM)]
check("an end-check that ran in user mode does not count", decision(hook(asbrief, rm_event(), DENY)) == "deny")
out = hook([user(PREAMBLE), use("m1", "tu_done", DONE + " --type"), result("tu_done", '{"ok": true}'),
            use("m2", "tu_rm", RM)], rm_event(), DENY)
check("a repeated --type with no value does not count", decision(out) == "deny", out)
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
start = {**agent["tool_input"], "prompt": "Read the diff.\n\n" + upd.get("prompt", "")[len("Read the diff.\n\n"):][:200]}
check("a prompt carrying only its start gets the whole paragraph",
      "updatedInput" in (hook([user(PREAMBLE)], {**agent, "tool_input": start}).get("hookSpecificOutput") or {}))
check("a general session's Agent call is left alone", hook([user("look at this")], agent) == {})
check("the key turns it off", hook([user(PREAMBLE)], agent, {"orchestrate": {"agent_forbidden": False}}) == {})
lead = "acme: lead\nMODE        Run `orchestrate` in program mode.\nROLE: lead acme\n"
check("a lead's Agent prompt gets it too", "updatedInput" in (hook([user(lead)], agent).get("hookSpecificOutput") or {}))
check("a broken input passes", subprocess.run([sys.executable, str(HERE / "lifecycle.py")], input="nope",
                                               capture_output=True, text=True, env=ENV).stdout == "")

print(f"\n{len(failures)} failure(s)" if failures else "\nall passed")
sys.exit(1 if failures else 0)
