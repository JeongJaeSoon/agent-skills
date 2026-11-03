"""record_check.py: a merge, land or PR open asks for a record line until a report carries one; worker_done and a
card's close are checked; no tracker/notes config turns it off.
Run: python3 hooks/test_record_check.py"""
import datetime, json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(os.path.realpath(tempfile.mkdtemp()))
STATE = TMP / "state"
CONFIG = TMP / "agent-skills.json"
CARD = TMP / "card"
FAILS = []


def config(value):
    CONFIG.write_text(json.dumps(value))


def reset(cfg={"tracker": {"adapter": "linear"}, "notes": {"adapter": "markdown"}}):
    config(cfg)
    (STATE / "record-check.jsonl").unlink(missing_ok=True)


def run(event, session="s1", **kw):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(TMP), "AGENT_SKILLS_STATE": str(STATE),
           "AGENT_SKILLS_CONFIG": str(CONFIG), "ORCA_TERMINAL_HANDLE": "term_fake"}
    payload = {"hook_event_name": event, "session_id": session, "cwd": str(CARD), **kw}
    p = subprocess.run([sys.executable, HERE / "record_check.py"], input=json.dumps(payload),
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else {}


def post(cmd, tid="t1", failed=False, **kw):
    return run("PostToolUseFailure" if failed else "PostToolUse", tool_name="Bash", tool_input={"command": cmd},
               tool_use_id=tid, **kw)


def pre(cmd, tid="p1", **kw):
    return run("PreToolUse", tool_name="Bash", tool_input={"command": cmd}, tool_use_id=tid, **kw)


def stop(message, **kw):
    return run("Stop", last_assistant_message=message, **kw)


def rows():
    f = STATE / "record-check.jsonl"
    out = []
    for l in (f.read_text().splitlines() if f.exists() else []):
        try:
            r = json.loads(l)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def check(name, cond):
    if not cond:
        FAILS.append(name)
    print(("ok   " if cond else "FAIL ") + name)


def ctx(out, event):
    h = out.get("hookSpecificOutput") or {}
    return h.get("hookEventName") == event and "record_check" in (h.get("additionalContext") or "")


# A merge reminds the model at once and opens an obligation.
reset()
out = post("gh pr merge 12 --squash")
check("merge: PostToolUse reminder", ctx(out, "PostToolUse") and "record.md" in out["hookSpecificOutput"]["additionalContext"])
check("merge: obligation opened", [r["kind"] for r in rows() if r["event"] == "open"] == ["merge"])
check("merge: same tool_use_id opens once", post("gh pr merge 12 --squash") and len([r for r in rows() if r["event"] == "open"]) == 1)

# Stop without a record line: warn by default (telemetry, no block).
out = stop("머지했습니다.")
check("unrecorded stop: warn systemMessage, no block", "systemMessage" in out and "decision" not in out)
check("unrecorded stop: warn row", any(r["event"] == "warn" and r["where"] == "Stop" for r in rows()))

# A record line in the final reply passes and closes.
check("recorded stop: passes", stop("머지했습니다.\n\n기록: ABC-1 completed · worklog-abc") == {})
check("recorded stop: closed", stop("다음 일") == {})

# The MCP not authenticated case: `기록 못 함` passes.
reset()
post("orch land demo --pr 3", tid="t2")
check("not recorded with reason: passes", stop("- **기록 못 함:** 노트 저장소 MCP 미인증") == {})

# A record line inside a code fence or a quote is an example, not a report.
reset()
post("gh pr create --title x --body y", tid="t3")
check("fenced record line: still warns", "systemMessage" in stop("```\n기록: example\n```\n> 기록: quoted"))
check("empty record line: still warns", "systemMessage" in stop("기록:   "))
check("record label with text on the next line: still warns", "systemMessage" in stop("기록:\n다음 작업"))
check("empty bold record line: still warns", "systemMessage" in stop("**기록:**"))
check("bold record line with text: passes", stop("**기록:** ABC-1 completed") == {})
post("gh pr create --title x --body y", tid="t4")

# deny blocks the stop once; stop_hook_active passes and keeps the obligation.
config({"tracker": {"adapter": "linear"}, "guard": {"record_check": "deny"}})
out = stop("끝")
check("deny: Stop blocked", out.get("decision") == "block" and "record_check" in out.get("reason", ""))
check("deny: stop_hook_active passes", stop("끝", stop_hook_active=True) == {})
check("deny: obligation stays open", stop("끝").get("decision") == "block")

# No config, or a null tracker: off.
reset({})
check("no config: no reminder", post("gh pr merge 12") == {} and rows() == [])
reset({"tracker": None, "prs": {"merge_repos": []}})
check("null tracker: off", post("gh pr merge 12") == {} and stop("끝") == {})
reset({"tracker": {"adapter": "linear"}, "guard": {"record_check": "off"}})
check("off level: no reminder", post("gh pr merge 12") == {} and rows() == [])

# Notes-only config names only the notes store.
reset({"notes": {"adapter": "markdown"}})
msg = post("gh pr merge 12")["hookSpecificOutput"]["additionalContext"]
check("notes only: names notes, not tracker", "notes store" in msg and "the tracker" not in msg)

# Commands that are not milestones, or not run.
reset()
for cmd in ["gh pr create --help", "gh pr merge 3 --auto", "gh pr merge 3 --disable-auto", "orch land demo --dry-run",
            "echo 'gh pr merge 3'", "gh pr view 3", "git commit -m 'gh pr merge'", "# gh pr merge 3"]:
    check(f"not a milestone: {cmd}", post(cmd, tid=cmd) == {})
check("not a milestone: nothing opened", rows() == [])
for cmd in ["gh --repo acme/app pr merge 3", "/opt/bin/gh pr merge 3 --merge",
            "python3 skills/orchestrate/scripts/orch.py land demo --pr 3", "cd x && gh pr create -t a -b b"]:
    check(f"milestone: {cmd}", ctx(post(cmd, tid=cmd), "PostToolUse"))

# A failed call opens an obligation only when it held more than one command.
reset()
check("failed single merge: nothing", post("gh pr merge 3", failed=True) == {})
check("failed compound with merge: opens", ctx(post("gh pr merge 3 && false", tid="t9", failed=True), "PostToolUseFailure"))

# worker_done: the body needs a record line; warn lets it through with context, deny refuses.
reset()
ok_body = "orca orchestration send --type worker_done --subject done --body 'PR 올림.\n기록: 없음 (리드 reconcile)'"
bad_body = "orca orchestration send --type worker_done --subject '기록: x' --body 'PR 올림'"
var_body = 'orca orchestration send --type worker_done --body "$REPORT"'
check("worker_done with record line: passes", pre(ok_body) == {})
out = pre(bad_body)
check("worker_done without record line: warn context", ctx(out, "PreToolUse") and "permissionDecision" not in out["hookSpecificOutput"])
check("worker_done body from a variable: warn", ctx(pre(var_body), "PreToolUse"))
config({"tracker": {"adapter": "linear"}, "guard": {"record_check": "deny"}})
check("worker_done without record line: deny", pre(bad_body)["hookSpecificOutput"].get("permissionDecision") == "deny")
check("status send is not checked", pre("orca orchestration send --type status --body hi") == {})
config({"tracker": {"adapter": "linear"}, "guard": {"record_check": "ask"}})
check("worker_done without record line: ask", pre(bad_body)["hookSpecificOutput"].get("permissionDecision") == "ask")
reset()
tick_body = "orca orchestration send --type worker_done --body '`record_check.py` 추가.\n기록: 없음 (리드 reconcile)'"
check("worker_done literal body with backticks: passes", pre(tick_body) == {})
check("worker_done body with a substitution: warn",
      ctx(pre('orca orchestration send --type worker_done --body "기록: `date`"'), "PreToolUse"))

# worker_done closes as of the moment it was checked, and only when the send succeeded alone.
reset()
post("gh pr merge 5", tid="m1")
pre(ok_body, tid="w1")
post(ok_body, tid="w1")
check("worker_done sent: obligation closed", stop("idle") == {})
reset()
post("gh pr merge 5", tid="m1")
pre(ok_body + "; true", tid="w2")
post(ok_body + "; true", tid="w2")
check("worker_done in a compound call: does not close", "systemMessage" in stop("idle"))
reset()
post("gh pr merge 5", tid="m1")
pre(ok_body, tid="w3")
post(ok_body, tid="w3", failed=True)
check("worker_done send failed: does not close", "systemMessage" in stop("idle"))
reset()
post("gh pr merge 5", tid="m1")
pre(ok_body, tid="w4")
post("gh pr merge 6", tid="m2")
post(ok_body, tid="w4")
check("merge after the report was checked: stays open", "systemMessage" in stop("idle"))


def add_row(row):
    with open(STATE / "record-check.jsonl", "a") as f:
        f.write(json.dumps(row) + "\n")


# A close covers only opens strictly before it: an open in the same millisecond stays.
reset()
post("gh pr merge 5", tid="m1")
add_row({"at": rows()[-1]["at"], "event": "close", "session": "s1", "agent": "", "via": "worker_done"})
check("open at the close's millisecond: stays open", "systemMessage" in stop("idle"))
reset()
post("gh pr merge 5", tid="m1")
closed_at = datetime.datetime.strptime(rows()[-1]["at"], "%Y-%m-%dT%H:%M:%S.%fZ") + datetime.timedelta(seconds=1)
add_row({"at": closed_at.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z", "event": "close", "session": "s1", "agent": ""})
check("open before the close: closed", stop("idle") == {})

# A damaged state line skips that line only, and is logged.
reset()
post("gh pr merge 5", tid="m1")
(STATE / "guard-errors.jsonl").unlink(missing_ok=True)
add_row("x")
with open(STATE / "record-check.jsonl", "a") as f:
    f.write("{not json\n")
check("damaged state line: other obligations still read", "systemMessage" in stop("idle"))
check("damaged state line: logged", (STATE / "guard-errors.jsonl").exists()
      and "record_check" in (STATE / "guard-errors.jsonl").read_text())

# A subagent's obligation is its own: SubagentStop checks it, the parent's Stop does not.
reset()
post("gh pr merge 5", tid="m1", agent_id="a1")
check("subagent merge: SubagentStop warns",
      "systemMessage" in run("SubagentStop", agent_id="a1", last_assistant_message="끝"))
check("subagent merge: parent Stop not charged", stop("끝") == {})
check("subagent merge: SubagentStop record line passes",
      run("SubagentStop", agent_id="a1", last_assistant_message="기록: 없음 (리드 reconcile)") == {})

# Closing a card: an open obligation with no record line this turn is flagged; a recorded one passes.
reset()
post("gh pr merge 5", tid="m1")
out = pre("orca worktree rm --worktree current --json", tid="c1")
check("close with open obligation: warn", ctx(out, "PreToolUse"))
config({"tracker": {"adapter": "linear"}, "guard": {"record_check": "deny"}})
check("close with open obligation: deny",
      pre("orca terminal close --terminal $ORCA_TERMINAL_HANDLE --json", tid="c2")["hookSpecificOutput"]["permissionDecision"] == "deny")
# Another session's obligation under the closed path counts too.
reset()
post("gh pr merge 6", tid="m2", session="other")
check("close of a path holding another session's obligation: warn",
      ctx(pre(f"orca worktree rm --worktree path:{CARD} --json", session="lead", cwd=str(TMP)), "PreToolUse"))
check("close of an id selector: not judged", pre("orca worktree rm --worktree id:abc", session="lead") == {})
# This turn's report since the obligation clears the close.
reset()
post("gh pr merge 7", tid="m3")
opened = datetime.datetime.strptime(rows()[-1]["at"], "%Y-%m-%dT%H:%M:%S.%fZ")
later = (opened + datetime.timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
tx = TMP / "t.jsonl"
tx.write_text("\n".join(json.dumps(e) for e in [
    {"type": "user", "message": {"role": "user", "content": "머지하고 종료하자"}},
    {"type": "assistant", "timestamp": later, "message": {"role": "assistant", "content": [
        {"type": "text", "text": "머지 완료.\n기록: ABC-2 completed"}]}}]) + "\n")
check("close after a recorded report: passes", pre("orca worktree rm --worktree current", transcript_path=str(tx)) == {})
check("close after a recorded report: closed", stop("bye") == {})
# A milestone and a close in one call.
reset()
check("milestone and close in one call: warn", ctx(pre("gh pr merge 8 && orca worktree rm --worktree current"), "PreToolUse"))

# Bad input never fails the call.
p = subprocess.run([sys.executable, HERE / "record_check.py"], input="not json", capture_output=True, text=True,
                   env={"PATH": "/usr/bin:/bin", "AGENT_SKILLS_STATE": str(STATE), "AGENT_SKILLS_CONFIG": str(CONFIG)})
check("bad input: exit 0, silent", p.returncode == 0 and p.stdout == "")

print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
