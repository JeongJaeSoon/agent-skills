"""guard.py with lib/coordination.py: AskUserQuestion, foreground waits and a send to /dev/null, by role.
Run: python3 hooks/test_coordination.py"""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(os.path.realpath(tempfile.mkdtemp()))
CONFIG = TMP / "agent-skills.json"
STATE = TMP / "state"
PREAMBLE = "You are working inside Orca, a multi-agent IDE. You are a dispatched worker.\n=== TASK ===\nfix X\n"


def transcript(name, prompt):
    path = TMP / f"{name}.jsonl"
    path.write_text(json.dumps({"type": "user", "message": {"content": prompt}}))
    return str(path)


WORKER = transcript("worker", PREAMBLE)
CARD = transcript("card", "/goal fix X\nCLOSE: end-session §8\n")
HUMAN = transcript("human", "please look at the failing test")
ROLES = {"orchestrator": {"agent_type": "agent-skills:orchestrator"}, "lead": {"agent_type": "agent-skills:lead"},
         "worker": {"transcript_path": WORKER}, "card": {"transcript_path": CARD}, "general": {"transcript_path": HUMAN}}


LAST = {}


def hook(tool, tool_input, role, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "AGENT_SKILLS_"))}
    env.update(HOME=str(TMP), AGENT_SKILLS_STATE=str(STATE), AGENT_SKILLS_CONFIG=str(CONFIG),
               ORCA_TERMINAL_HANDLE="term_me")
    payload = {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": tool, "tool_input": tool_input,
               "cwd": str(TMP), **ROLES[role], **extra}
    out = subprocess.run([sys.executable, HERE / "guard.py"], input=json.dumps(payload), capture_output=True,
                         text=True, env=env).stdout
    parsed = json.loads(out) if out.strip() else {}
    LAST["warning"] = parsed.get("systemMessage") or ""
    spec = parsed.get("hookSpecificOutput") or {}
    return spec.get("permissionDecision") or "none"


def ask(role, **extra):
    return hook("AskUserQuestion", {"questions": []}, role, **extra)


def bash(cmd, role, background=False):
    return hook("Bash", {"command": cmd, "run_in_background": background}, role)


# AskUserQuestion: denied for an orchestrator, a lead and a worker (dispatched or a card); a human's session asks.
for role in ("orchestrator", "lead", "worker", "card"):
    assert ask(role) == "deny", role
assert ask("general") == "none"
assert ask("orchestrator", agent_id="a1") == "none", "a subagent is outside the rule"
CONFIG.write_text("{}")
STATE.joinpath("roles").mkdir(parents=True, exist_ok=True)
STATE.joinpath("roles", "s1.json").write_text(json.dumps({"role": "none"}))
assert ask("worker") == "none", "ROLE: none turns the role off"
STATE.joinpath("roles", "s1.json").unlink()
CONFIG.write_text(json.dumps({"orchestrate": {"ask_user_question": False}}))
assert ask("orchestrator") == "none", "off: as before"
CONFIG.write_text("{}")

# Foreground waits: denied for an orchestrator or a lead, fine in the background and for a worker.
WAITS = ("orch wait app", "orch inbox --wait", "orch watch-cards app", "orca orchestration check --terminal $ORCA_TERMINAL_HANDLE --wait",
         "orca orchestration inbox --wait --json")
for role in ("orchestrator", "lead"):
    for cmd in WAITS:
        assert bash(cmd, role) == "deny", (role, cmd)
        assert bash(cmd, role, background=True) in ("none", "allow"), (role, cmd)
    assert bash("orch wait app | tail -5", role, background=True) == "deny"
    assert bash("orch wait app | tail -5; orch status app", role, background=True) == "deny"
    assert bash("orch wait app | jq . | head -3", role, background=True) == "deny"
    assert bash("orch wait a | jq .; orch wait b | tail -5", role, background=True) == "deny"
    assert bash("orch wait app | jq .; tail -5 /tmp/status.log", role, background=True) != "deny"
    # Monitor runs beside the turn: its wait is a background one, and the pipe and /dev/null rules still hold.
    for cmd in WAITS:
        assert hook("Monitor", {"command": cmd, "description": "x"}, role) in ("none", "allow"), (role, cmd)
    assert hook("Monitor", {"command": "orch wait app | tail -5", "description": "x"}, role) == "deny"
    assert hook("Monitor", {"command": "orca orchestration send --to t --subject s > /dev/null",
                            "description": "x"}, role) == "deny"
    assert bash("orch status app", role) == "allow"
    assert bash("orch inbox --json", role) == "allow"
    assert bash("echo 'orch wait app'", role) == "none"
for role in ("worker", "card", "general"):
    assert bash("orca orchestration check --terminal $ORCA_TERMINAL_HANDLE --wait", role) == "allow", role
CONFIG.write_text(json.dumps({"orchestrate": {"wait_foreground": False}}))
assert bash("orch wait app", "lead") == "allow", "off: the old allow is back"
CONFIG.write_text("{}")

# A send or reply to /dev/null hides whether it went out.
for cmd in ("orca orchestration send --to run:r --text hi > /dev/null", "orca orchestration send --to run:r --text hi >/dev/null 2>&1",
            "orca orchestration reply --id m1 --body ok &>/dev/null", "orca orchestration send --text hi 1>/dev/null",
            "orca orchestration send --text '2' >/dev/null", "orca orchestration send --text 2 >/dev/null",
            "orca orchestration send --to run:r --text hi >& /dev/null"):
    for role in ("orchestrator", "lead", "worker"):
        assert bash(cmd, role) == "deny", (role, cmd)
    assert bash(cmd, "general") == "none", cmd
for cmd in ("orca orchestration send --to run:r --text hi 2>/dev/null", "orca orchestration send --to run:r --text '> /dev/null'",
            "orca orchestration check --json > /dev/null"):
    assert bash(cmd, "lead") in ("none", "allow"), cmd
CONFIG.write_text(json.dumps({"orchestrate": {"send_devnull": False}}))
assert bash("orca orchestration send --to run:r --text hi > /dev/null", "lead") == "none"
CONFIG.write_text("{}")


# A Claude start with no --model warns, and only once orchestrate.models is set.
def model_warning(cmd, role="lead"):
    bash(cmd, role)
    return LAST["warning"]


START = "orca orchestration worker-start --worktree new-child --agent claude"
assert "orchestrate.model_flag" not in model_warning(f"{START} --spec 'ABC-1 fix'")
CONFIG.write_text(json.dumps({"orchestrate": {"models": {"lead": "opus", "worker": "sonnet"}}}))
for role in ("lead", "general"):
    assert "orch model worker" in model_warning(f"{START} --spec 'ABC-1 fix'", role)
assert "orch model worker" in model_warning("orca agent launch --agent claude --worktree active")
assert "orch model worker" in model_warning("orca worktree create --repo id:r --name x --agent claude --prompt 'ABC-1 fix'")
for cmd in (f"{START} --spec 'x' --model sonnet", f"{START} --spec 'x' --model=Sonnet[1m]",
            f"{START} --spec 'x' --model claude-sonnet-5-5", f"{START} --terminal term_x --spec 'x'",
            "orca orchestration worker-start --terminal term_x --worktree id:w --spec 'x'",
            "orca orchestration worker-start --worktree new-child --agent codex --spec 'x'"):
    assert model_warning(cmd) == "", cmd
# A --model that differs from orchestrate.models.worker warns (never blocks); an unset worker key says it compared nothing.
for cmd in (f"{START} --spec 'x' --model=opus", f"{START} --spec 'x' --model claude-opus-4-1"):
    assert "differs from orchestrate.models.worker (sonnet)" in model_warning(cmd), cmd
    assert bash(cmd, "lead") in ("none", "allow"), "a warning does not block: " + cmd
CONFIG.write_text(json.dumps({"orchestrate": {"models": {"worker": "claude-sonnet-4-6"}}}))
assert "differs" in model_warning(f"{START} --spec 'x' --model claude-sonnet-4-5"), "two ids of one family are two models"
assert model_warning(f"{START} --spec 'x' --model claude-sonnet-4-6-20260101") == ""
assert model_warning(f"{START} --spec 'x' --model sonnet") == "", "a bare family matches its ids"
CONFIG.write_text(json.dumps({"orchestrate": {"models": {"worker": "sonnet"}}}))
assert model_warning(f"{START} --model claude-3-7-sonnet-20250219") == "", "version before the family"
CONFIG.write_text(json.dumps({"orchestrate": {"models": {"worker": "haiku"}}}))
assert model_warning(f"{START} --model claude-3-5-haiku-20241022") == ""
assert "differs" in model_warning(f"{START} --model claude-3-7-sonnet-20250219")
CONFIG.write_text(json.dumps({"orchestrate": {"models": {"worker": "claude-sonnet-4-6"}}}))
CONFIG.write_text(json.dumps({"orchestrate": {"models": {"worker": "opusplan"}}}))
assert "differs" in model_warning(f"{START} --spec 'x' --model opus"), "opusplan is not opus"
CONFIG.write_text(json.dumps({"orchestrate": {"models": {"lead": "opus", "worker": ""}}}))
assert "is not set" in model_warning(f"{START} --spec 'x' --model sonnet"), "an empty worker is unset"
CONFIG.write_text(json.dumps({"orchestrate": {"models": {"lead": "opus", "worker": "sonnet"}}}))
assert model_warning("orca agent launch --agent claude --worktree active --model opus") == "", "not a worker start"
assert model_warning(f"MODEL=sonnet {START} --model \"$MODEL\"") == "", "a value the shell expands is not judged"
assert model_warning(f"{START} --model claude-sonnet-4-?") == "", "a glob the shell may expand is not judged"
assert model_warning(f"{START} --model {{sonnet,}}") == "", "a brace expansion is not judged"
assert model_warning(f"{START} --model opus[1m]") == "", "an unquoted bracket may be a glob, so the value is not judged"
CONFIG.write_text(json.dumps({"orchestrate": {"models": {"worker": "sonnet"}}}))
assert model_warning(f"{START} --spec '--model=opus' --model sonnet") == "", "text in --spec is not the flag"
assert "orch model worker" in model_warning(f"{START} --spec '--model=sonnet'"), "and does not stand in for it"
for cmd in (f"{START} --spec '--prompt' --model sonnet", f"{START} --display-name '--model=opus' --spec fix --model sonnet",
            f"{START} --json --model sonnet", f"{START} --spec fix --model opus --model sonnet","orca orchestration worker-start --worktree new-child --spec '--agent=claude' --agent codex --model gpt-5",
            f"{START} --spec '--terminal=t' --model sonnet", f"{START} --model sonnet --spec '--model=opus'"):
    assert model_warning(cmd) == "", "the real --model is the one read: " + cmd
for cmd in (f"{START} --model '$MODEL'", f"{START} --model \\$MODEL", f"{START} --model ~/sonnet"):
    assert model_warning(cmd) == "", "a literal-looking $ is still not judged: " + cmd
assert "differs" in model_warning(f"{START} --json --model opus")
CONFIG.write_text(json.dumps({"orchestrate": {"models": {"worker": "claude-sonnet-4-6"}}}))
# Any shell-active character skips the comparison, quoted or not: the words arrive unquoted, so the guard is unsure.
for cmd in (f"{START} --model 'opus[1m]'", f"{START} --model=\"opus[1m]\"", f"{START} --model claude-sonnet-4-?>out",
            f"{START} --model \"claude-sonnet-\"*", f"{START} --model opus\\[1m\\]",
            f"{START} --spec \"Use --model x here\" --model claude-sonnet-4-*"):
    assert model_warning(cmd) == "", "a value with a shell-active character is not judged: " + cmd
CONFIG.write_text(json.dumps({"orchestrate": {"models": {"worker": "sonnet"}, "model_flag": "deny"}}))
assert bash(f"{START} --spec 'x' --model opus", "lead") in ("none", "allow"), "a comparison never blocks, whatever the level"
CONFIG.write_text(json.dumps({"orchestrate": {"models": {"lead": "opus"}}}))
assert "is not compared: orchestrate.models.worker is not set" in model_warning(f"{START} --spec 'x' --model opus")
assert model_warning(f"{START} --model opus; {START} --model sonnet").count("is not set") == 1, "one line per call"
assert "orch model worker" in model_warning(f"{START} --spec 'x'"), "no --model keeps the original warning"
CONFIG.write_text(json.dumps({"orchestrate": {"models": {"worker": "sonnet"}, "model_flag": "off"}}))
assert model_warning(f"{START} --spec 'x'") == ""
CONFIG.write_text("{}")


# orchestrate.pr_poll: a Monitor loop that polls gh pr list|view|checks with a sleep under 300 s warns, never blocks.
def monitor(cmd, role="general"):
    verdict = hook("Monitor", {"command": cmd, "description": "x", "timeout_ms": 1800000}, role)
    return verdict, LAST["warning"]


for sleep in ("60", "180", "299", "5m", "2m"):
    cmd = f"until gh pr view 5 --json state | grep -q MERGED; do sleep {sleep}; done"
    if sleep == "5m":
        assert monitor(cmd) == ("none", ""), "300 s exactly is allowed: " + cmd
        continue
    verdict, warning = monitor(cmd)
    assert verdict == "none" and "pr_watch.py" in warning and "orchestrate.pr_poll" in warning, (cmd, verdict, warning)
for cmd in ("while true; do gh pr list --state all --json number; sleep 120; done",
            "until gh pr checks 5 | grep -qv pending; do sleep 30; done"):
    assert "pr_watch.py" in monitor(cmd)[1], cmd
for cmd in ("while true; do gh pr view 5; sleep .5; done", "while true; do gh pr view 5; sleep 0; done",
            "while true; do sleep 60; gh pr view 5; done"):
    assert "pr_watch.py" in monitor(cmd)[1], cmd
for cmd in ("while true; do gh --repo a/b pr view 1; sleep 60; done", "while true; do gh -R a/b pr list; sleep 60; done",
            "while true; do gh --repo=a/b pr checks 1; sleep 60; done",
            "while true; do gh pr view 1; sleep 300; done & while true; do gh pr view 2; sleep 60; done; wait"):
    assert "pr_watch.py" in monitor(cmd)[1], "any short poll warns: " + cmd
assert monitor("while true; do gh -R a/b pr view 1; sleep 300; done") == ("none", "")
assert monitor("gh -R a/b run list; sleep 60") == ("none", "")
for cmd in ("sleep 300; while true; do gh pr view 5; sleep 60; done",
            "sleep 300; until gh pr view 5 | grep -q MERGED; do sleep 30; done; echo merged"):
    assert "pr_watch.py" in monitor(cmd)[1], "a sleep outside the loop is a one-off: " + cmd
for cmd in ("sleep 60; while true; do gh pr view 5; sleep 300; done",
            "while true; do for i in 1 2; do echo $i; sleep 1; done; gh pr view 5; sleep 300; done",
            "while true; do gh pr view 5; sleep 150; sleep 150; done",
            'N=60; while true; do gh pr view 5; sleep "$N"; done',
            "while true; do gh pr view 5; sleep 3m 2m; done"):
    assert monitor(cmd) == ("none", ""), "judged by the whole wait, not by one sleep: " + cmd
for cmd in ("until gh pr view 5 --json state | grep -q MERGED; do sleep 300; done",
            "while true; do gh pr list --json number; sleep 600; done",
            "until gh run view 9 | grep -q completed; do sleep 60; done",
            "gh pr view 5 --json state",
            "python3 skills/orchestrate/scripts/pr_watch.py --repo acme/app",
            "until gh pr view 5 | grep -q MERGED; do sleep $N; done",
            "echo 'gh pr view 5; sleep 60'"):
    assert monitor(cmd) == ("none", ""), "no warning: " + cmd
# A word that spells a loop keyword is not one; exclusive branches and a background sleep are not added up.
for cmd in ("while true; do gh pr view 1; echo done; sleep 60; done",
            "while true; do gh pr view 1; echo for; sleep 60; done",
            "while true; do gh pr view 1; if false; then sleep 300; else sleep 60; fi; done",
            "while true; do gh pr view 1; sleep 300 & sleep 60; done",
            "while true; do gh pr view 1; sleep 300 || sleep 60; done",
            "while true; do gh pr view 1; echo then done; sleep 60; done",
            "sleep 300; while true; do gh pr view 1; echo do for; sleep 60; done",
            'while true; do gh pr view 1; "done"; sleep 60; done',
            "while true; do gh pr view 1; sleep 60; done ; echo ok",
            "while true; do gh pr view 1; sleep 60; done > /dev/null",
            "while true; do gh pr view 1; sleep 60; done # keep",
            "while true; do gh pr view 1; sleep 300 & sleep 1 & wait $!; done",
            "while true; do gh pr view 1; sleep 300 & x=1 & wait $!; done",
            "while true; do gh pr view 1; (sleep 300 &); wait $!; done",
            "! while true; do gh pr view 1; sleep 60; done",
            "!while true; do gh pr view 1; sleep 60; done"):
    assert "pr_watch.py" in monitor(cmd)[1], "must warn: " + cmd
for cmd in ("gh pr view 1; sleep 60",
            "while true; do gh pr view 1; sleep 300 & wait; done",
            "while true; do gh pr view 1; sleep 300 & wait $!; done",
            "! while true; do gh pr view 1; sleep 300; done",
            "while true; do gh pr view 1; echo done; sleep 600; done",
            "while true; do gh pr view 1 && sleep 150 && sleep 150; done",
            "while true; do if gh pr view 1 | grep -q OPEN; then sleep 600; else sleep 900; fi; done"):
    assert monitor(cmd) == ("none", ""), "no warning: " + cmd
# Only Monitor: the same loop in a background Bash call is not a watcher the session keeps.
assert bash("until gh pr view 5 | grep -q MERGED; do sleep 60; done", "general", background=True) == "none"
assert LAST["warning"] == ""
CONFIG.write_text(json.dumps({"orchestrate": {"pr_poll": False}}))
assert monitor("until gh pr view 5 | grep -q MERGED; do sleep 60; done") == ("none", "")
CONFIG.write_text(json.dumps({"orchestrate": {"pr_poll": "deny"}}))
assert monitor("until gh pr view 5 | grep -q MERGED; do sleep 60; done")[0] == "none", "a warning never blocks, whatever the level"
CONFIG.write_text("{}")
print("coordination: all pass")
