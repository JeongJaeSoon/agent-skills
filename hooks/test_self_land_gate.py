"""self_land_gate.py from stdin fixtures against a fake gh: a program card given the land does not settle over its own
open PR; a READY-WAIT line, a landed PR, a card never given the land and every other session pass; what cannot be read
passes with one warning line; deny only where the key says so.
Run: python3 hooks/test_self_land_gate.py"""
import json, os, pathlib, shutil, stat, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(os.path.realpath(tempfile.mkdtemp()))
HOME = TMP / "home"
CONFIG = TMP / "agent-skills.json"
PROGRAMS = TMP / "programs"
BIN = TMP / "bin"
CARD = TMP / "card"
BIN.mkdir()
CARD.mkdir()
(HOME / ".claude").mkdir(parents=True)
for args in (["init", "-q", "-b", "card-branch"], ["config", "user.email", "t@example.test"], ["config", "user.name", "t"],
             ["config", "commit.gpgsign", "false"], ["commit", "-q", "--allow-empty", "-m", "x"]):
    subprocess.run(["git", "-C", str(CARD), *args], check=True)

FAKE_GH = """#!/bin/sh
echo "$@" >> "$FAKE_GH_LOG"
[ -n "$FAKE_GH_FAIL" ] && exit 1
cat "$FAKE_GH_PRS"
"""
(BIN / "gh").write_text(FAKE_GH)
(BIN / "gh").chmod((BIN / "gh").stat().st_mode | stat.S_IEXEC)
PRS, LOG = TMP / "prs.json", TMP / "gh.log"
OPEN = '[{"number": 7, "url": "https://example.test/acme/app/pull/7"}]'

WORKER = "You are working inside Orca, a multi-agent IDE.\nYou are a dispatched worker\n"
BRIEF = WORKER + "PROGRAM: demo\nGOAL  x\nAPPROVED: required checks green + review clean: orch land merges it\n"
OLD_BRIEF = WORKER + "PROGRAM: demo\nGOAL  x\nAPPROVED    pushing this branch\n"
FREE = "fix the typo please\n"


def transcript(text, name="t.jsonl"):
    path = TMP / name
    path.write_text(json.dumps({"type": "user", "message": {"content": text}}) + "\n")
    return str(path)


def program(policy="autonomous", slug="demo"):
    (PROGRAMS / slug).mkdir(parents=True, exist_ok=True)
    (PROGRAMS / slug / "program.json").write_text(json.dumps({"repo": "acme/app", "merge_policy": policy}))


def run(cmd, prompt=BRIEF, gh=OPEN, cwd=CARD, fail=False, **fields):
    PRS.write_text(gh)
    LOG.write_text("")
    env = {"PATH": f"{BIN}:/usr/bin:/bin", "HOME": str(HOME), "AGENT_SKILLS_CONFIG": str(CONFIG), "PROGRAMS_HOME": str(PROGRAMS),
           "FAKE_GH_PRS": str(PRS), "FAKE_GH_LOG": str(LOG), "AGENT_SKILLS_STATE": str(TMP / "state")}
    if fail:
        env["FAKE_GH_FAIL"] = "1"
    payload = {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Bash", "cwd": str(cwd),
               "tool_input": {"command": cmd}, "transcript_path": transcript(prompt) if prompt else str(TMP / "gone"), **fields}
    r = subprocess.run([sys.executable, HERE / "self_land_gate.py"], input=json.dumps(payload), capture_output=True,
                       text=True, env=env, check=True)
    return json.loads(r.stdout) if r.stdout.strip() else {}


def decision(out):
    return out.get("hookSpecificOutput", {}).get("permissionDecision")


DONE = "orca orchestration send --type worker_done --subject s --body 'merged nothing' --outcome succeeded"
RM = "orca worktree rm --worktree current --json"
program()

# An open PR at worker_done: warned by default, with the PR, the way out and the key; never blocked.
out = run(DONE)
assert decision(out) is None and "#7" in out["systemMessage"] and "orch land" in out["systemMessage"], out
assert "READY-WAIT" in out["systemMessage"] and "orchestrate.self_land" in out["systemMessage"], out
assert out["hookSpecificOutput"]["additionalContext"] == out["systemMessage"], "the model reads it too"
assert "--repo acme/app --head card-branch --state open" in LOG.read_text(), LOG.read_text()
# The same at `orca worktree rm`, which has no exception line to give.
assert "#7" in run(RM)["systemMessage"]
assert "#7" in run("/opt/x/orca worktree rm --worktree path:" + str(CARD))["systemMessage"], "an absolute orca, this card by path"
assert "#7" in run('orca orchestration send --type=worker_done --outcome=succeeded --subject s --body x')["systemMessage"]
assert "#7" in run('orca orchestration send --type "worker_done" --outcome succeeded --subject s')["systemMessage"]

# Promoted: refused, and the reason leads with the marker the refused-act paragraph names.
CONFIG.write_text(json.dumps({"orchestrate": {"self_land": "deny"}}))
out = run(DONE)
assert decision(out) == "deny", out
reason = out["hookSpecificOutput"]["permissionDecisionReason"]
assert reason.startswith("[gate:self-land] (agent-skills hooks/self_land_gate.py") and "--outcome failed" in reason, reason
assert decision(run(RM)) == "deny"

# Each way out passes at deny, without a warning.
for name, cmd, kw in (
        ("a READY-WAIT line", "orca orchestration send --type worker_done --outcome succeeded --subject s "
                              "--body 'READY at abc\nREADY-WAIT: human-review\nnext line'", {}),
        ("a READY-WAIT in a quoted heredoc", "orca orchestration send --type worker_done --outcome succeeded --subject s "
                                             "--body \"$(cat <<'EOF'\nREADY-WAIT: land-after\nEOF\n)\"", {}),
        ("the PR merged", DONE, {"gh": "[]"}),
        ("a failed report", "orca orchestration send --type worker_done --subject s --outcome failed", {}),
        ("a status", "orca orchestration send --type status --subject s --body x", {}),
        ("a card without the land", DONE, {"prompt": OLD_BRIEF}),
        ("a card outside any program", DONE, {"prompt": WORKER + "APPROVED: orch land when green\n"}),
        ("a human's session", DONE, {"prompt": FREE}),
        ("a subagent", DONE, {"agent_id": "a1"}),
        ("another worktree", "orca worktree rm --worktree path:/elsewhere", {}),
        ("a quoted example", "echo 'orca worktree rm --worktree current'", {}),
        ("another command", "git status", {})):
    assert run(cmd, **kw) == {}, name
for value in ("owner-merge", "land-refused", "external-wait"):
    assert run(f"orca orchestration send --type worker_done --outcome succeeded --subject s --body 'READY-WAIT: {value}'") == {}
# A value outside the list is no exception, and a worktree rm reads no line.
assert decision(run("orca orchestration send --type worker_done --outcome succeeded --subject s --body 'READY-WAIT: later'")) == "deny"
assert decision(run(RM + " # READY-WAIT: owner-merge")) == "deny"
assert LOG.read_text() != "", "the last case did read gh"

# A human-gate program waits on the human by policy: no gh read at all.
program("human-gate")
assert run(DONE) == {} and LOG.read_text() == ""
program()

# What cannot be read is passed with one warning line, at deny too.
for name, kw, why in (("gh fails", {"fail": True}, "gh pr list failed"),
                      ("no first prompt", {"prompt": None}, "first prompt"),
                      ("no program file", {"prompt": BRIEF.replace("demo", "gone")}, "program gone"),
                      ("detached head", {"cwd": TMP}, "branch")):
    out = run(DONE, **kw)
    assert decision(out) is None and why in out["systemMessage"] and "\n" not in out["systemMessage"], (name, out)
out = run('orca orchestration send --type worker_done --outcome succeeded --subject s --body "$BODY"')
assert decision(out) is None and "shell value" in out["systemMessage"], out

# off reads nothing; the default is warn.
CONFIG.write_text(json.dumps({"orchestrate": {"self_land": "off"}}))
assert run(DONE) == {} and LOG.read_text() == ""
CONFIG.write_text("{}")
assert decision(run(DONE)) is None and run(DONE)["systemMessage"]

spec = json.loads((HERE / "hooks.json").read_text())["hooks"]["PreToolUse"]
wired = [h for g in spec if g.get("matcher") == "Bash" for h in g["hooks"] if "self_land_gate.py" in h["command"]]
assert wired and wired[0]["timeout"] >= 10, "registered with room for the gh read"
assert "[gate:self-land]" in (HERE.parent / "skills/orchestrate/references/brief.md").read_text()
shutil.rmtree(TMP)
print("self_land_gate: all pass")
