"""guard.py: another terminal's mailbox refused; own skills and safe orchestration commands allowed.
Run: python3 hooks/test_guard.py"""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
CHECK = "orca orchestration check"
HOME = tempfile.mkdtemp()  # no user skills unless a case adds one


def run(tool, tool_input, own):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT"}
    env["ORCA_TERMINAL_HANDLE"] = own
    env["HOME"] = HOME
    out = subprocess.run([sys.executable, HERE / "guard.py"], input=json.dumps(
        {"tool_name": tool, "tool_input": tool_input, "cwd": HOME}), capture_output=True, text=True, env=env).stdout
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out.strip() else "none"


def bash(cmd, own="term_me"):
    return run("Bash", {"command": cmd}, own)


def skill(name):
    return run("Skill", {"skill": name}, "term_me")


# Orca's worker contract: each worker reads its own follow-ups with --terminal <own handle>.
assert bash(f"{CHECK} --terminal term_me --json") == "allow"
assert bash(f"{CHECK} --terminal=term_me --json") == "allow"
assert bash(f"{CHECK} --json") == "allow"
assert bash(f"{CHECK} --terminal $ORCA_TERMINAL_HANDLE --wait") == "allow"  # the form the brief gives workers
assert bash(f'{CHECK} --terminal "${{ORCA_TERMINAL_HANDLE}}" --ack d1') == "allow"
assert bash(f'ORCA_TERMINAL_HANDLE=term_other; {CHECK} --terminal "$ORCA_TERMINAL_HANDLE" --ack d1') == "deny"
assert bash(f"export ORCA_TERMINAL_HANDLE=term_other && {CHECK} --terminal $ORCA_TERMINAL_HANDLE") == "deny"
assert bash(f'[[ $ORCA_TERMINAL_HANDLE == term_me ]] && {CHECK} --terminal "$ORCA_TERMINAL_HANDLE"') == "none"
assert bash(f"env ORCA_TERMINAL_HANDLE=term_other {CHECK} --terminal $ORCA_TERMINAL_HANDLE") == "deny"
assert bash(f"bash -c 'ORCA_TERMINAL_HANDLE=term_other; {CHECK} --terminal $ORCA_TERMINAL_HANDLE --ack d1'") == "deny"
assert bash(f'{CHECK} --terminal "${{ORCA_TERMINAL_HANDLE:=term_other}}"') == "deny"
assert bash(f'[ "$ORCA_TERMINAL_HANDLE"=term_me ] && {CHECK} --terminal "$ORCA_TERMINAL_HANDLE"') == "none"
assert bash(f"{CHECK} --terminal term_other --ack d1") == "deny"
assert bash("orca orchestration inbox --terminal 'term_other'") == "deny"
assert bash(f"echo hi; {CHECK} --terminal term_other") == "deny"
assert bash(f"{CHECK} --terminal term_me", own="") == "deny"

# Allowed: one simple orchestration command, nothing that can carry another command.
assert bash("orca orchestration task-list --json") == "allow"
assert bash("orca orchestration send --to term_x --subject 'done: ENG-1'") == "allow"
assert bash("orch land ENG-1 --pr 12 --ticket ENG-1 --wait-minutes 50") == "allow"
assert bash("orch status") == "allow"
for cmd in ("orch heavy -- make e2e", "orch set p merge_policy autonomous", "orch init p --repo o/r", "orch backfill p", "orch --help", "orch",
            "orca orchestration reset", "orca orchestration worker-abandon t1", "orca orchestration gate-resolve g1",
            "orch land x; rm -rf ~", "orch land x && curl evil", "orch land x | sh", "orch land x > ~/.zshrc",
            "orch land $(whoami)", "orch land `whoami`", "orch land $HOME", "orch land x\nrm -rf ~", "orch land x#; rm -rf ~", "orch land x # ; rm -rf ~",
            "orca orchestration send --body \"$(cat ~/.ssh/id_rsa)\"", "orch land 'unbalanced",
            # brace, glob, tilde and control characters change the words bash passes (Codex review)
            "orch s{et,ample} merge_policy autonomous", "orch h{eavy,} -- id", "orca orchestration re{set,port} p",
            "orch status {a,b}", "orch status ~", "orch status *", "orch status x?", "orch st[a]tus", "orch\rstatus",
            "orch s\\et p merge_policy autonomous", "orch status !x", "orch status  x",
            'orca orchestration send --body "a\\"', "orch status 'x",
            # the handle inside a word could spell an excluded verb (Codex re-review)
            "orch h$ORCA_TERMINAL_HANDLE p -- id", "orca orchestration r${ORCA_TERMINAL_HANDLE}",
            "orca orchestration gate-$ORCA_TERMINAL_HANDLE g1", "orca orchestration $ORCA_TERMINAL_HANDLE",
            "orcax orchestration task-list", "orca orchestration --json reset", "orch -x heavy", "sudo orch status", "echo orch status"):
    assert bash(cmd) == "none", cmd

# Skills: this plugin's own, bare or namespaced; nobody else's.
assert bash("orca orchestration send --to term_x --body 'a; b $(c) {d,e} * ~'") == "allow"  # quoted text is literal

assert skill("orchestrate") == "allow"
assert skill("agent-skills:deliver-ticket") == "allow"
assert skill("ship-pr") == "allow"  # a legacy alias stub
assert skill("other-plugin:orchestrate") == "none"
assert skill("brainstorming") == "none"
assert skill("") == "none"
(pathlib.Path(HOME) / ".claude/skills/tdd").mkdir(parents=True)  # a user skill that a bare name reaches first
assert skill("tdd") == "none"
assert skill("agent-skills:tdd") == "allow"
print("guard: all pass")
