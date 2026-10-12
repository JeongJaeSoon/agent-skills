"""guard.py: another terminal's mailbox refused; own skills and safe orchestration commands allowed.
Run: python3 hooks/test_guard.py"""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
CHECK = "orca orchestration check"
HOME = tempfile.mkdtemp()  # no user skills unless a case adds one


def run(tool, tool_input, own, transcript=None):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT"}
    env["ORCA_TERMINAL_HANDLE"] = own
    env["HOME"] = HOME
    out = subprocess.run([sys.executable, HERE / "guard.py"], input=json.dumps(
        {"tool_name": tool, "tool_input": tool_input, "cwd": HOME, "transcript_path": transcript}), capture_output=True, text=True, env=env).stdout
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out.strip() else "none"


def bash(cmd, own="term_me", transcript=None):
    return run("Bash", {"command": cmd}, own, transcript)


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
assert skill("ship-pr") == "none"  # a removed alias
assert skill("other-plugin:orchestrate") == "none"
assert skill("brainstorming") == "none"
assert skill("") == "none"
(pathlib.Path(HOME) / ".claude/skills/tdd").mkdir(parents=True)  # a user skill that a bare name reaches first
assert skill("tdd") == "none"
assert skill("agent-skills:tdd") == "allow"

# A card's first prompt carries its CLOSE (or KEEP) line, and a lead's its MODE, DELEGATE and PREDICATE, literally.
NEW = "orca worktree create --repo name:app --name app-x --agent claude"
assert bash(f'{NEW} --prompt "/goal fix X. 브리프: /tmp/b.md\nCLOSE: end-session §8" --json') == "none"
assert bash(f"{NEW} --prompt '/goal ENG-1. KEEP: I reuse this card'") == "none"
assert bash(f'{NEW} --prompt "/goal fix X. 브리프: /tmp/b.md" --json') == "deny"
assert bash(f'{NEW} --prompt "$(cat /tmp/brief.md)"') == "deny"  # unreadable as written
assert bash(f'{NEW} --prompt "/goal run app as lead\nMODE orchestrate\nCLOSE: §8"') == "deny"
LEAD = "MODE orchestrate\nDELEGATE nothing yourself\nPREDICATE ENG-1, ENG-2 Done\nCLOSE: §8"
assert bash(f'{NEW} --prompt "/goal run app\n{LEAD}"') == "none"
assert bash(f'{NEW} --prompt "/goal run app\n{LEAD.replace("DELEGATE", "WHO")}"') == "deny"
assert bash(f"{NEW} --prompt 'app: lead\nGOAL ship X\nDELEGATE all\nPREDICATE ENG-1 Done\nCLOSE: §8'") == "deny"  # no MODE
assert bash(f"{NEW} --prompt 'app: lead\n{LEAD}'") == "none"
assert bash(f'{NEW} --prompt "/goal take the lead on X. CLOSE: §8"') == "none"  # the word alone is not a lead brief
assert bash(f'{NEW} --prompt "/goal fix X\nPROGRAM app (lead: term_x)\nNote: lead review first\nCLOSE: §8"') == "none"  # a worker
assert bash("orca worktree create --repo name:app --name app-x --json") == "none"  # no agent task
assert bash("orca worktree create --help") == "none"
assert bash(f'cd /r && {NEW} --prompt "/goal fix X"') == "deny"
assert bash('grep -rn "orca worktree create\\|--prompt" skills') == "none"
assert bash("echo 'run orca worktree create --prompt x'") == "none"

# A lead never starts another lead: the caller's own first prompt tells whether it is one.
def transcript(name, *records):
    path = pathlib.Path(HOME) / f"{name}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return str(path)


def said(content, **extra):
    return {"type": "user", "message": {"role": "user", "content": content}, **extra}


GOAL = "<command-name>/goal</command-name>\n<command-message>goal</command-message>\n<command-args>run widgets. 브리프: /tmp/b.md\n"
AS_LEAD = transcript("lead", {"type": "attachment"}, said(GOAL + LEAD + "</command-args>"), said("next"))
HEAD_LEAD = transcript("head", said([{"type": "text", "text": "widgets: lead\n" + LEAD}]))
TOP = transcript("top", said("오케스트레이터로 모든 세션 관리해줘"), said(f"/goal x\n{LEAD}"))
WORKER = transcript("worker", said("/goal fix X\nPROGRAM widgets (lead: term_x)\nCLOSE: §8"))
META = transcript("meta", said("MODE injected", isMeta=True), said([{"type": "tool_result", "content": "MODE x"}]), said("hi"))
LEAD_CARD, WORKER_CARD = f'{NEW} --prompt "/goal run app\n{LEAD}"', f'{NEW} --prompt "/goal fix X\nCLOSE: §8"'
assert bash(LEAD_CARD, transcript=AS_LEAD) == "deny"
assert bash(LEAD_CARD, transcript=HEAD_LEAD) == "deny"
assert bash(f"{NEW} --prompt 'app: lead\n{LEAD}'", transcript=AS_LEAD) == "deny"
for t in (TOP, WORKER, META, None, str(pathlib.Path(HOME) / "absent.jsonl"), transcript("bad", "x")):
    assert bash(LEAD_CARD, transcript=t) == "none", t  # a later lead-shaped prompt does not count; unreadable fails open
assert bash(WORKER_CARD, transcript=AS_LEAD) == "none"
assert bash(f'{NEW} --prompt "{LEAD}"', transcript=AS_LEAD) == "deny"  # MODE opening the prompt, no /goal

# A foreground sleep loop is refused; the same loop in the background, and a sleep outside a loop, are not.
O = "/tmp/t/b1.output"
for cmd in (f"until grep -q '^exit=' {O}; do sleep 10; done; tail -3 {O}",
            f"for i in $(seq 1 115); do grep -q 'exited with code' {O} && break; sleep 5; done; cat {O}",
            f"while [ ! -s {O} ]; do sleep 5; done",
            "until ! gh pr checks 304 | grep -q pending; do\n  sleep 30\ndone",
            f"cd /w && until [ -s {O} ]; do sleep $N; done"):
    assert bash(cmd) == "deny", cmd
    assert run("Bash", {"command": cmd, "run_in_background": True}, "term_me") == "none", cmd
for cmd in ("sleep 5; cat /tmp/x", "for i in 1 2 3; do curl -fs x && break; sleep 2; done", "for f in *.py; do python3 $f; done", "grep -rn 'sleep 5' skills",
            "echo 'until x; do sleep 1; done'", "git log --format=%s | grep -c 'do not sleep'"):
    assert bash(cmd) != "deny", cmd
print("guard: all pass")
