"""refusals.py: an act auto mode (with a category) or the human refused is refused again for every session until the
human clears it. Run: python3 hooks/test_refusals.py"""
import json, multiprocessing, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
TMP = pathlib.Path(tempfile.mkdtemp())
STATE = TMP / "state"
WT = TMP / "worktrees" / "card-a"
WT.mkdir(parents=True)
LEDGER = STATE / "refusals.jsonl"

# Tool result texts as Claude Code writes them (2.1.x transcripts), the classifier's and the human's.
CLASSIFIER = ("Permission for this action was denied by the Claude Code auto mode classifier. Reason: [Irreversible "
              "Local Destruction]. If you have other tasks that don't depend on this action, continue working on those. "
              "IMPORTANT: You *may* attempt to accomplish this action using other tools that might naturally be used to "
              "accomplish this goal, e.g. using head instead of cat. But you *should not* attempt to work around this "
              "denial in malicious ways")
NO_EXPLANATION = ("Permission for this action was denied by the Claude Code auto mode classifier. Reason: The server-side "
                  "auto mode classifier judged this action dangerous (it gave no explanation). If you have other tasks "
                  "that don't depend on this action, continue working on those.")
NO_VERDICT = ("The server-side auto mode classifier gave no verdict (error), so auto mode could not check this action.")
HUMAN = ("The user doesn't want to proceed with this tool use. The tool use was rejected (eg. if it was a file edit, "
         "the new_string was NOT written to the file). STOP what you are doing and wait for the user to tell you how "
         "to proceed.")
HOOK = "PreToolUse:Bash hook error: [gate:pressure] (agent-skills hooks/pressure_gate.py) the machine is loaded"
TOOL_ERROR = "<tool_use_error>InputValidationError: command is missing</tool_use_error>"


def env(**extra):
    e = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PLUGIN_ROOT", "ORCA_TERMINAL_HANDLE")}
    e.update(AGENT_SKILLS_STATE=str(STATE), AGENT_SKILLS_CONFIG=str(TMP / "cfg.json"), HOME=str(TMP), **extra)
    return e


def hook(event, script="refusals.py", **extra):
    p = subprocess.run([sys.executable, HERE / script], input=event if isinstance(event, str) else json.dumps(event),
                       capture_output=True, text=True, env=env(**extra))
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else {}


def pre(tool, tool_input, session="s-b", transcript=None, cwd=None):
    out = hook({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "session_id": session,
                "cwd": str(cwd or TMP), "transcript_path": transcript})
    return (out.get("hookSpecificOutput") or {}).get("permissionDecision", "none"), out


def bash(cmd, **kw):
    return pre("Bash", {"command": cmd}, **kw)


def denied(tool_use_id, tool, tool_input, reason="[Irreversible Local Destruction]", session="s-a"):
    return hook({"hook_event_name": "PermissionDenied", "tool_name": tool, "tool_input": tool_input,
                 "tool_use_id": tool_use_id, "reason": reason, "session_id": session, "cwd": str(TMP)})


def transcript(name, calls):
    """A transcript of calls (id, tool, input, result text) each refused with that text."""
    path = TMP / f"{name}.jsonl"
    lines = []
    for i, tool, args, text in calls:
        lines.append({"type": "assistant", "sessionId": "s-a", "cwd": str(TMP),
                      "message": {"content": [{"type": "tool_use", "id": i, "name": tool, "input": args}]}})
        lines.append({"type": "user", "sessionId": "s-a", "cwd": str(TMP), "message": {"content": [
            {"type": "tool_result", "tool_use_id": i, "is_error": True, "content": text}]}})
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return str(path)


def rows():
    return [json.loads(l) for l in LEDGER.read_text().splitlines()] if LEDGER.exists() else []


def reset(cfg=None):
    if LEDGER.exists():
        LEDGER.unlink()
    (TMP / "cfg.json").write_text(json.dumps(cfg or {}))


def orch(*args):
    return subprocess.run([sys.executable, ROOT / "skills/orchestrate/scripts/prog.py", "refusal", *args],
                          capture_output=True, text=True, env=env(), stdin=subprocess.DEVNULL)


# 1. PermissionDenied: the classifier refused `rm -rf <worktree>` in session a; session b's `git worktree remove` of the
#    same worktree, a deletion by another verb, is refused.
reset()
assert denied("toolu_1", "Bash", {"command": f"SECRET_TOKEN=abc123 rm -rf {WT}"}) == {}
assert bash(f"git worktree remove --force {WT}")[0] == "deny"
assert bash(f"cd {WT.parent} && rm -rf card-a")[0] == "deny"
assert bash(f"orca worktree rm --worktree path:{WT}")[0] == "deny"
assert bash(f"rm -rf {WT.parent}")[0] == "deny"  # its parent holds it
assert bash(f"rm -rf {WT}/build")[0] == "none"  # a path inside it is another target
assert bash(f"ls {WT}")[0] == "none"
assert pre("Read", {"file_path": str(WT / "x")})[0] == "none"
reason = bash(f"rm -r {WT}")[1]["hookSpecificOutput"]["permissionDecisionReason"]
assert "toolu_1" in reason and "s-a" in reason and "orch refusal report" in reason and "!orch refusal clear" in reason
# The ledger keeps the act, never the command text or another argument's value.
raw = LEDGER.read_text()
assert "abc123" not in raw and "SECRET_TOKEN" not in raw and "rm -rf" not in raw
assert set(rows()[0]) == {"op", "at", "id", "session", "terminal", "run", "verb", "target"}, rows()[0]
assert rows()[0]["verb"] == "delete" and rows()[0]["target"] == os.path.realpath(WT)

# 2. The transcript path: the next PreToolUse of the refusing session records it, and dedupes against PermissionDenied.
reset()
denied("toolu_2", "Bash", {"command": f"rm -rf {WT}"})
t = transcript("t2", [("toolu_2", "Bash", {"command": f"rm -rf {WT}"}, CLASSIFIER),
                      ("toolu_3", "Edit", {"file_path": str(TMP / "notes.txt")}, HUMAN),
                      ("toolu_4", "Read", {"file_path": str(TMP / "secret.txt")}, CLASSIFIER),
                      ("toolu_5", "Bash", {"command": f"rm -rf {TMP / 'other'}"}, HOOK),
                      ("toolu_6", "Bash", {"command": f"rm -rf {TMP / 'other'}"}, TOOL_ERROR),
                      ("toolu_7", "Bash", {"command": f"rm -rf {TMP / 'other'}"}, NO_EXPLANATION),
                      ("toolu_8", "Bash", {"command": f"rm -rf {TMP / 'other'}"}, NO_VERDICT)])
decision, out = bash("git status", session="s-a", transcript=t)
assert decision == "none"
assert "no category" in out["systemMessage"], out
refused = [r for r in rows() if r["op"] == "refuse"]
assert [(r["id"], r["verb"]) for r in refused] == [("toolu_2", "delete"), ("toolu_3", "write")], refused
assert sum(r["id"] == "toolu_2" for r in rows()) == 1  # PermissionDenied and the transcript wrote it once
assert not any(r["id"] in ("toolu_5", "toolu_6") for r in rows())  # a hook's block and an input error are not refusals
assert {r["id"] for r in rows() if r["op"] == "skip"} == {"toolu_4", "toolu_7", "toolu_8"}
# The human's refused Edit blocks a Write of the same file from any session; reading it stays allowed.
assert pre("Write", {"file_path": str(TMP / "notes.txt"), "content": "x"}, session="s-c")[0] == "deny"
assert pre("Read", {"file_path": str(TMP / "notes.txt")}, session="s-c")[0] == "none"
assert bash(f"rm -rf {TMP / 'other'}")[0] == "none"
# Read again: nothing new, no second warning.
decision, out = bash("git status", session="s-a", transcript=t)
assert "systemMessage" not in out and len(rows()) == 5

# 3. PRs and branches by their canonical key, whatever form names them.
reset()
denied("toolu_9", "Bash", {"command": "gh pr merge 12 --squash -R acme/app"}, reason="[Merge Without Review]")
assert bash("gh pr merge https://github.com/acme/app/pull/12 --merge")[0] == "deny"
assert bash("gh api -X PUT repos/acme/app/pulls/12/merge")[0] == "deny"
assert bash("gh pr merge 13 -R acme/app")[0] == "none"
assert bash("gh pr view 12 -R acme/app")[0] == "none"
denied("toolu_10", "Bash", {"command": "git push --force https://github.com/acme/app.git feature-x"})
assert bash("git push -f git@github.com:acme/app.git HEAD:refs/heads/feature-x")[0] == "deny"
assert bash("git push https://github.com/acme/app.git feature-x")[0] == "none"  # a plain push is another verb

# 4. A refused call whose target cannot be read is not recorded, with a warning; a PermissionDenied with no category
#    leaves it to the transcript.
reset()
out = denied("toolu_11", "Bash", {"command": "curl -X POST https://example.test/hook"})
assert "not recorded" in out["systemMessage"] and rows()[0]["op"] == "skip"
assert denied("toolu_12", "Bash", {"command": f"rm -rf {WT}"}, reason="judged dangerous (it gave no explanation)") == {}
assert not any(r["id"] == "toolu_12" for r in rows())
out = denied("toolu_13", "Bash", "not a dict")
assert "not recorded now" in out["systemMessage"]

# 5. Broken input lets the call through with a warning.
for bad in ("{not json", "[1, 2]"):
    out = hook(bad)
    assert "could not read its input" in out["systemMessage"] and "hookSpecificOutput" not in out
assert hook({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": 5}}) == {}

# 6. The key: off records and blocks nothing, warn records and only warns, deny (default) refuses.
reset({"guard": {"refusal_registry": "off"}})
denied("toolu_14", "Bash", {"command": f"rm -rf {WT}"})
assert rows() == []
reset({"guard": {"refusal_registry": "warn"}})
denied("toolu_15", "Bash", {"command": f"rm -rf {WT}"})
decision, out = bash(f"git worktree remove {WT}")
assert decision == "none" and "toolu_15" in out["systemMessage"]
(TMP / "cfg.json").write_text(json.dumps({"guard": {"refusal_registry": "off"}}))
assert bash(f"git worktree remove {WT}") == ("none", {})
(TMP / "cfg.json").write_text(json.dumps({"guard": {"refusal_registry": "deny"}}))
assert bash(f"git worktree remove {WT}")[0] == "deny"

# 7. Only the human clears: the guard refuses `orch refusal clear` and a direct write of the ledger from an agent.
guard = lambda tool, args: (hook({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": args,
                                  "session_id": "s-b", "cwd": str(TMP)}, "guard.py").get("hookSpecificOutput") or {}
                            ).get("permissionDecision", "none")
assert guard("Bash", {"command": "orch refusal clear toolu_15"}) == "deny"
assert guard("Bash", {"command": "orch refusal report"}) == "allow"
assert guard("Bash", {"command": f"echo '{{}}' >> {LEDGER}"}) == "deny"
assert guard("Write", {"file_path": str(LEDGER), "content": ""}) == "deny"
assert guard("Edit", {"file_path": str(TMP / "notes.txt")}) == "none"

# 8. `orch refusal report` for the human, then the human's clear (a `!` line runs no hook) lifts the block.
p = orch("report")
assert p.returncode == 0 and "toolu_15" in p.stdout and "delete" in p.stdout and "!orch refusal clear" in p.stdout, p
print(p.stdout)
assert orch("clear", "toolu_nope").returncode != 0
p = orch("clear", "toolu_15")
assert p.returncode == 0 and "cleared toolu_15" in p.stdout, p
assert rows()[-1] == {**rows()[-1], "op": "clear", "id": "toolu_15", "tty": False}
assert bash(f"git worktree remove {WT}")[0] == "none"
assert "no open refusals" in orch("report").stdout
# A clear over 30 days old leaves the file at the next clear, its refusal with it.
denied("toolu_16", "Bash", {"command": f"rm -rf {TMP / 'b'}"})
LEDGER.write_text(LEDGER.read_text().replace('"op": "clear", "at": "20', '"op": "clear", "at": "19'))
orch("clear", "toolu_16")
assert not any(r["id"] == "toolu_15" for r in rows()) and any(r["id"] == "toolu_16" for r in rows())

# 9. Concurrent appends under the lock: every row whole, each event id once.
sys.path.insert(0, str(HERE))
os.environ["AGENT_SKILLS_STATE"] = str(STATE)
from lib import refusals  # noqa: E402


def write(i):
    refusals.record(f"toolu_c{i % 40}", [("delete", f"/tmp/c{i}")], "s", None, None)


reset()
with multiprocessing.get_context("fork").Pool(16) as pool:
    pool.map(write, range(200))
ids = [r["id"] for r in rows()]
assert len(ids) == 40 and len(set(ids)) == 40, len(ids)

print("ok")
