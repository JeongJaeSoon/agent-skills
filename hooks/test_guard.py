"""guard.py: another terminal's mailbox refused; own skills and safe orchestration commands allowed.
Run: python3 hooks/test_guard.py"""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
CHECK = "orca orchestration check"
HOME = tempfile.mkdtemp()  # no user skills unless a case adds one


def run(tool, tool_input, own, transcript=None, agent=None, session="s0", field="permissionDecision", **extra):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT" and not k.startswith("CLAUDE_PLUGIN_OPTION_")}
    env.update(extra)
    env["AGENT_SKILLS_STATE"] = f"{HOME}/state"
    env["ORCA_TERMINAL_HANDLE"] = own
    env["HOME"] = HOME
    out = subprocess.run([sys.executable, HERE / "guard.py"], input=json.dumps(
        {"tool_name": tool, "tool_input": tool_input, "cwd": HOME, "transcript_path": transcript, "agent_type": agent,
         "session_id": session}),
        capture_output=True, text=True, env=env).stdout
    return json.loads(out)["hookSpecificOutput"].get(field, "none") if out.strip() else "none"


def bash(cmd, own="term_me", transcript=None, agent=None, session="s0", **extra):
    return run("Bash", {"command": cmd}, own, transcript, agent, session, **extra)


def reason(cmd, **extra):
    return run("Bash", {"command": cmd}, "term_me", field="permissionDecisionReason", **extra)


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
# orch verdict: allowed only in the attributable form; prog.py judges independence, so the hook checks the shape.
VERDICT = "orch verdict p --pr 1 --sha abc123 --source review:codex --reviewer codex --evidence abc123 --result"
assert bash(f"{VERDICT} pass") == "allow"
assert bash(f"{VERDICT} fail") == "allow"
assert bash(f"{VERDICT} pass --note 'round 1'") == "allow"
for cmd in (f"{VERDICT} maybe", f"{VERDICT} pass --result fail", f"{VERDICT} pass --repo o/r",
            f"{VERDICT} pass --unknown x", f"{VERDICT} pass --note", f"{VERDICT} pass; id", f"{VERDICT} pass > f",
            "orch verdict p --pr 1 --sha abc --source review:codex --reviewer codex --result pass",
            "orch verdict p --pr 1 --sha abc --source review:codex --evidence e --result pass",
            "orch verdict p --pr 1 --sha abc --source review:codex --reviewer codex --evidence e",
            "orch verdict p --pr 1 --sha abc --source review:codex --reviewer codex --evidence '' --result pass",
            "orch verdict p --pr x --sha abc --source review:codex --reviewer codex --evidence e --result pass",
            "orch verdict p --pr 1 --source review:codex --reviewer codex --evidence e --result pass",
            "orch verdict --pr 1 --sha abc --source review:codex --reviewer codex --evidence e --result pass",
            "orch verdict"):
    assert bash(cmd) == "none", cmd
for cmd in ("orch verdict p --pr 1 --sha abc --source review:codex", "orch heavy -- make e2e", "orch set p merge_policy autonomous", "orch init p --repo o/r", "orch backfill p", "orch --help", "orch",
            "orca orchestration reset", "orca orchestration worker-abandon t1", "orca orchestration gate-resolve g1",
            "orch land x; rm -rf ~", "orch land x && curl evil", "orch land x | sh", "orch land x > ~/.zshrc",
            "orch land $(whoami)", "orch land `whoami`", "orch land $HOME", "orch land x\nrm -rf ~", "orch land x#; rm -rf ~", "orch land x # ; rm -rf ~",
            "orca orchestration send --body \"$(cat ~/.ssh/id_rsa)\"", "orch land 'unbalanced",
            # brace, glob, tilde and control characters change the words bash passes
            "orch s{et,ample} merge_policy autonomous", "orch h{eavy,} -- id", "orca orchestration re{set,port} p",
            "orch status {a,b}", "orch status ~", "orch status *", "orch status x?", "orch st[a]tus", "orch\rstatus",
            "orch s\\et p merge_policy autonomous", "orch status !x", "orch status  x",
            'orca orchestration send --body "a\\"', "orch status 'x",
            # the handle inside a word could spell an excluded verb
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

# A card's first prompt carries its CLOSE (or KEEP) line, and a lead's its MODE, ROLE, DELEGATE and PREDICATE, literally.
NEW = "orca worktree create --repo name:app --name app-x --agent claude"
assert bash(f'{NEW} --prompt "/goal fix X. 브리프: /tmp/b.md\nCLOSE: end-session §8" --json') == "none"
assert bash(f"{NEW} --prompt '/goal ENG-1. KEEP: I reuse this card'") == "none"
assert bash(f'{NEW} --prompt "/goal fix X. 브리프: /tmp/b.md" --json') == "deny"
assert bash(f'{NEW} --prompt "$(cat /tmp/brief.md)"') == "deny"  # unreadable as written
assert bash(f'{NEW} --prompt "/goal run app as lead\nMODE orchestrate\nCLOSE: §8"') == "deny"
LEAD = "MODE orchestrate\nROLE: lead app\nDELEGATE nothing yourself\nPREDICATE ENG-1, ENG-2 Done\nCLOSE: §8"
assert bash(f'{NEW} --prompt "/goal run app\n{LEAD}"') == "none"
assert bash(f'{NEW} --prompt "/goal run app\n{LEAD.replace("DELEGATE", "WHO")}"') == "deny"
assert bash(f"{NEW} --prompt 'ROLE: lead app\n{LEAD.replace(chr(10) + 'ROLE: lead app', '')}'") == "none"
assert bash(f"{NEW} --prompt '/goal run app\\nROLE: lead app\n{LEAD.replace('ROLE: lead app', '')}'") == "deny"  # literal \\n
assert bash(f'{NEW} --prompt "/goal orchestrate resume app\nROLE: lead app\nCLOSE: §8"') == "deny"  # no MODE
for role in ("", "ROLE: none", "ROLE: lead <slug>", "ROLE lead", "ROLE: lead app (project lead)",
             "see ROLE: lead app"):
    assert bash(f'{NEW} --prompt "/goal run app\n{LEAD.replace("ROLE: lead app", role)}"') == "deny", role
assert bash(f"{NEW} --prompt 'app: lead\nGOAL ship X\nROLE: lead app\nDELEGATE all\nPREDICATE ENG-1 Done\nCLOSE: §8'") == "deny"  # no MODE
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
RESUMED = transcript("resumed", said(GOAL.replace("run widgets. 브리프: /tmp/b.md", "orchestrate resume widgets")
                                     + "ROLE: lead widgets</command-args>"))
assert bash(LEAD_CARD, transcript=RESUMED) == "deny", "a lead resumed by its ROLE line is still a lead"
assert bash(WORKER_CARD, transcript=RESUMED) == "none"
assert bash(LEAD_CARD, agent="agent-skills:lead") == "deny", "a lead started with --agent"
assert bash(LEAD_CARD, agent="agent-skills:orchestrator") == "none" and bash(WORKER_CARD, agent="agent-skills:lead") == "none"
(pathlib.Path(HOME) / "state/roles").mkdir(parents=True)
(pathlib.Path(HOME) / "state/roles/joined.json").write_text(json.dumps({"role": "lead", "slug": "widgets"}))
(pathlib.Path(HOME) / "state/roles/top.json").write_text(json.dumps({"role": "orchestrator"}))
assert bash(LEAD_CARD, session="joined") == "deny", "a session that joined as a lead with a later ROLE line"
assert bash(LEAD_CARD, session="top") == "none" and bash(WORKER_CARD, session="joined") == "none"
assert bash(LEAD_CARD, session="../joined") == "none"

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

# Both alternatives the sleep-loop deny names pass this hook: the same command in the background, and Monitor.
# Claude Code hands a Monitor call to a hook whose matcher meets it only through `Bash` as a foreground Bash call
# ({command, description}), which the sleep-loop gate refuses; naming Monitor in the matcher keeps its own input.
said = reason(f"until [ -s {O} ]; do sleep 5; done")
assert "run_in_background: true" in said and "Monitor tool" in said, said
wired = json.loads((HERE / "hooks.json").read_text())["hooks"]["PreToolUse"]
matcher = next(e["matcher"] for e in wired if any("guard.py" in h["command"] for h in e["hooks"]))
assert "Monitor" in matcher.split("|"), matcher
for cmd in (f"until [ -s {O} ]; do sleep 5; done", f"while true; do grep -q done {O} && break; sleep 30; done",
            "until ! gh pr checks 304 | grep -q pending; do\n  sleep 30\ndone"):
    assert run("Monitor", {"command": cmd, "description": "wait", "timeout_ms": 600000}, "term_me") != "deny", cmd
# A Monitor command is still a shell command: the danger rules and the mailbox rule hold for it.
assert run("Monitor", {"command": "until pkill -f node; do sleep 5; done", "description": "x"}, "term_me") == "deny"
assert run("Monitor", {"command": f"{CHECK} --terminal term_other --wait", "description": "x"}, "term_me") == "deny"

# setup.py's steps that write no settings go to a permission dialog; anything else about setup.py gets no decision.
SETUP = HERE.parent / "scripts" / "setup.py"
assert bash(f"python3 {SETUP} --actions-only") == "ask"
assert bash(f"python3 {SETUP} --actions-only --orca-run") == "ask"
said = json.loads(subprocess.run(
    [sys.executable, HERE / "guard.py"], capture_output=True, text=True, env={**os.environ, "HOME": HOME},
    input=json.dumps({"tool_name": "Bash", "tool_input": {"command": f"python3 {SETUP} --actions-only"}, "cwd": HOME})).stdout)
assert said == {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                       "permissionDecisionReason": said["hookSpecificOutput"]["permissionDecisionReason"]}}, said
assert "writes no Claude settings" in said["hookSpecificOutput"]["permissionDecisionReason"], said
# --apply is the human's line (guard.deny.plugin_apply, test_danger.py).
for cmd in (f"python3 {SETUP} --settings-only --apply 0123456789ab", f"python3 {SETUP} --actions-only --apply x"):
    assert bash(cmd) == "deny", cmd
for cmd in (f"python3 {SETUP} --actions-only; rm -rf /tmp/x", f"python3 {HOME}/scripts/setup.py --actions-only",
            "python3 scripts/setup.py --actions-only", f"bash -c 'python3 {SETUP} --actions-only'"):
    assert bash(cmd) == "none", cmd
# Another clone's setup.py still asks, but the dialog vouches for what it does only when the install dialog named it.
CLONE = pathlib.Path(HOME) / "clone"
(CLONE / "skills" / "setup-pc").mkdir(parents=True)
(CLONE / "scripts").mkdir()
CLONE_SETUP = f"python3 {CLONE}/scripts/setup.py --actions-only"
assert bash(CLONE_SETUP) == "ask"
said = reason(CLONE_SETUP)
assert "writes no Claude settings" not in said and f"{CLONE}/scripts/setup.py" in said and "may do anything" in said, said
assert bash(CLONE_SETUP, CLAUDE_PLUGIN_OPTION_CHECKOUT=str(CLONE)) == "ask"
assert "writes no Claude settings" in reason(CLONE_SETUP, CLAUDE_PLUGIN_OPTION_CHECKOUT=str(CLONE))
(pathlib.Path(HOME) / "src").mkdir()
(pathlib.Path(HOME) / "src" / "agent-skills").symlink_to(CLONE)
assert bash(CLONE_SETUP) == "ask"
assert "writes no Claude settings" in reason(CLONE_SETUP), "the dialog's default, ~/src/agent-skills"


# Only the sleep-loop deny carries the gate marker the refused-act rule keys on.
def reason(cmd):
    env = {**os.environ, "HOME": HOME, "AGENT_SKILLS_STATE": f"{HOME}/state", "ORCA_TERMINAL_HANDLE": "term_me"}
    out = subprocess.run([sys.executable, HERE / "guard.py"], input=json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": HOME, "session_id": "s0"}),
        capture_output=True, text=True, env=env).stdout
    return json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]


assert reason(f"while [ ! -s {O} ]; do sleep 5; done").startswith("[gate:sleep-loop] (agent-skills hooks/guard.py) ")
assert "[gate:" not in reason(f"{CHECK} --terminal term_other --json")
# `skills-sync card <brief>`: the hook opens the brief and applies the helper's checks and the lead test to it.
H = pathlib.Path(HOME)
(H / "worker.md").write_text("/goal fix X\nAPPROVED: none\nCLOSE: §8\n")
(H / "noclose.md").write_text("/goal fix X\nAPPROVED: none\n## Close\n§8\n")
(H / "noapproved.md").write_text("/goal fix X\nCLOSE: §8\n")
(H / "lead.md").write_text("app: lead\n/goal run app\nMODE: orchestrate\nROLE: lead app\nINBOX  orch wait app\n"
                           "DELEGATE  nothing yourself\nPREDICATE  ENG-1 Done\nMERGE  merge_policy autonomous\n"
                           "TASKS  task-create --deps\nGATE  gate-create\nAPPROVED: none\nCLOSE: §8\n")
HELPER = "skills-sync card {} --repo name:app --name app-x"
assert bash(HELPER.format("worker.md")) == "none"  # relative to the event's cwd
assert bash(HELPER.format(H / "worker.md"), session="joined") == "none"
assert bash(HELPER.format("noclose.md")) == "deny" and "CLOSE or KEEP" in reason(HELPER.format("noclose.md"))
assert bash(HELPER.format("noapproved.md")) == "deny" and "`APPROVED:` row" in reason(HELPER.format("noapproved.md"))
assert bash(HELPER.format("absent.md")) == "deny"
assert bash(HELPER.format("$BRIEF")) == "deny"
assert bash(HELPER.format("lead.md")) == "none"
assert bash(HELPER.format("lead.md"), session="joined") == "deny", "a lead starting a lead through the helper"
# After a cd the event's cwd is not where a relative path points. The hook does not follow a cd (CDPATH, `cd -L`,
# subshells and `||` all change where it lands), so a relative path asks for an absolute one; `~` and `$HOME` are fine.
for pre in ("cd /x && ", f"cd {H} && ", "cd safe && ", "(cd safe); ", "false && cd safe; ", "pushd safe; popd; ", "cd safe || true; ", "cd safe; ",
            "cd $SOMEWHERE && ", "cd - && "):
    assert "absolute path" in reason(pre + HELPER.format("worker.md")), pre
assert bash("cd /x && " + HELPER.format(H / "worker.md")) == "none"
assert bash("cd /x && " + HELPER.format("~/worker.md")) == "none"
assert bash("cd /x && " + HELPER.format("$HOME/lead.md"), session="joined") == "deny"
assert bash("pushd /x; " + HELPER.format(H / "lead.md"), agent="agent-skills:lead") == "deny"
# A set variable or `~` in the brief's path is expanded the way the shell would; anything else still is not.
assert bash(HELPER.format("$HOME/worker.md")) == "none"
assert bash(HELPER.format("${HOME}/noclose.md")) == "deny" and "CLOSE or KEEP" in reason(HELPER.format("${HOME}/noclose.md"))
assert bash(HELPER.format('"$HOME/worker.md"')) == "none"
assert bash(HELPER.format("$UNSET_BRIEF_VAR")) == "deny" and "cannot expand" in reason(HELPER.format("$UNSET_BRIEF_VAR"))
assert bash("export HOME=/x; " + HELPER.format("$HOME/worker.md")) == "deny"  # rebound in the same command
assert bash(HELPER.format("$(echo worker.md)")) == "deny"
assert bash(HELPER.format("$HOME/../worker.md")) == "deny"  # the parent of HOME has no worker.md
# What the hook cannot prove is the file the shell reads stays denied (independent review of this change).
assert bash(HELPER.format("~no_such_guard_user_468/worker.md")) == "deny"  # `~user` is not read, and does not crash the hook
assert bash("cd ~no_such_guard_user_468 || true; " + HELPER.format("worker.md")) == "deny"
for rebind in ("BRIEF=x; ", "unset BRIEF; "):
    assert bash(rebind + "BRIEF+=.other; " + HELPER.format('"$BRIEF"'), BRIEF=str(H / "worker.md")) == "deny"
# $HOME and ~ are read only when nothing can run before the helper, so no spelling of a rebind can matter.
for rebind in ("HOME+=/x; ", "unset HOME; HOME=/x; ", "read HOME; ", "export HOME=/x; ", 'export HO""ME=/x; ', "IFS=,; "):
    assert bash(rebind + HELPER.format('"$HOME/worker.md"')) == "deny", rebind
    assert bash(rebind + HELPER.format("$HOME/worker.md")) == "deny", rebind
    assert bash(rebind + HELPER.format("~/worker.md")) == "deny", rebind
assert bash(HELPER.format("'$HOME/worker.md'")) == "deny"  # single quotes pass the text through literally
# The helper expands a leading ~ itself, quoted or not, so the hook reads the same HOME file.
(H / "~").mkdir()
(H / "~" / "worker.md").write_text((H / "lead.md").read_text())  # a literal "~" directory in cwd holds a lead brief
assert bash(HELPER.format("'~/worker.md'"), session="joined") == "none"  # HOME's worker, not the literal ~/worker.md
assert bash(HELPER.format('"~/worker.md"'), session="joined") == "none"
assert bash(HELPER.format("~/worker.md"), session="joined") == "none" and bash(HELPER.format('"${HOME}/worker.md"')) == "none"
# The brief is one plain word: a quoted space, a quote inside the name, braces and brackets are not read (round 3).
(H / "worker.md lead.md").write_text((H / "lead.md").read_text())
(H / "brief1.md").write_text((H / "lead.md").read_text())
for arg in ('"worker.md lead.md"', "\"worker.md'\"", "brief{1..1}.md", "brief[1].md", '"worker.md\tlead.md"'):
    assert bash(HELPER.format(arg)) == "deny", arg
assert bash("HOME=/other;bin/" + HELPER.format('"$HOME/worker.md"')) == "deny"  # `\S*/` once swallowed the earlier statement
assert bash("bin/" + HELPER.format('"$HOME/worker.md"')) == "none"
# Round 5: `$HOME` after a `~` in single quotes is literal, and bash splits words on blanks only, not on NBSP or VT.
(pathlib.Path(str(H) + str(H))).mkdir(parents=True)
(pathlib.Path(str(H) + str(H)) / "worker.md").write_text((H / "worker.md").read_text())
assert bash(HELPER.format("'~/$HOME/worker.md'")) == "deny"
for blank in (" ", "\x0b"):
    assert bash(HELPER.format(f"worker.md{blank}lead.md")) == "deny", repr(blank)
    assert bash(HELPER.format(f"{blank}worker.md")) == "deny", repr(blank)  # the blank leads the word
assert bash(HELPER.format("\\x" + str(H / "worker.md"))) == "deny"  # bash passes the relative `x/…`, the masked text shows `/…`
assert bash(f"skills-sync card \\ {H / 'worker.md'} --repo name:app --name app-x") == "deny"  # bash passes " /…", relative
assert bash("skills-sync card \\x--brief --repo name:app --name app-x") == "deny"  # an escape before what looks like an option
assert bash(HELPER.format("--help")) == "none"
# Round 4: a later argument can rebind HOME, and a cd has spellings no scan of earlier text catches.
assert bash(HELPER.format("'~/worker.md'") + ' --x "$((HOME=0))"', session="joined") == "deny"
assert bash(HELPER.format("$HOME/worker.md") + " --x `true`") == "deny"
for pre in ("! cd /x; ", 'c"d" /x && ', "f() { cd /x; }; f; ", "echo hi; "):
    assert "absolute path" in reason(pre + HELPER.format("worker.md")), pre
    assert bash(pre + HELPER.format(H / "worker.md")) == "none", pre
(H / "physical" / "child").mkdir(parents=True)
(H / "physical" / "brief2.md").write_text((H / "lead.md").read_text())
(H / "brief2.md").write_text("/goal fix X\nCLOSE: §8\n")
os.symlink(H / "physical" / "child", H / "link")
assert bash(HELPER.format("link/../brief2.md"), session="joined") == "deny"  # `..` after a symlink is its target's parent
# Every call is checked, and only a call whose own argument is --help is skipped.
assert bash(HELPER.format("--help")) == "none"
assert bash(HELPER.format("lead.md") + " --comment --help", session="joined") == "deny"
assert bash(HELPER.format("lead.md") + "; echo --help", session="joined") == "deny"
assert bash(HELPER.format("worker.md") + " && " + HELPER.format("lead.md"), session="joined") == "deny"
assert bash(HELPER.format("worker.md") + " && " + HELPER.format("noclose.md")) == "deny"
# A check that cannot run (here: no helper to import) denies instead of letting the card through.
EMPTY = tempfile.mkdtemp()
assert bash(HELPER.format("worker.md"), CLAUDE_PLUGIN_ROOT=EMPTY) == "deny"
assert "could not check" in run("Bash", {"command": HELPER.format("worker.md")}, "term_me",
                                field="permissionDecisionReason", CLAUDE_PLUGIN_ROOT=EMPTY)
assert bash('grep "skills-sync card" notes.md') == "none"  # quoted words are not the helper being run
# A line of a quoted argument, a heredoc body or a comment that starts with the words is text, not a command.
PROMPT_ARG = 'node companion.mjs task "Review card.py:\nskills-sync card / reads the brief\nfoo; skills-sync card /" --wait'
assert bash(PROMPT_ARG) == "none"
assert bash("node companion.mjs task 'Review:\nskills-sync card /' --wait") == "none"
assert bash("cat <<'EOF' > notes.md\nskills-sync card /\nEOF\necho ok") == "none"
assert bash("cat <<EOF > notes.md\nskills-sync card /\nEOF") == "none"
assert bash("cat <<-EOF > notes.md\n\tskills-sync card /\n\tEOF") == "none"
assert bash("echo don\\'t # it's skills-sync card /\nls") == "none"
# Where bash runs it, the helper is checked as before, inside a substitution and after a heredoc too.
assert bash(f'echo "$(skills-sync card {H / "noclose.md"})"') == "deny"
assert bash(f"echo `skills-sync card {H / 'noclose.md'}`") == "deny"
assert bash(f"cat <<EOF\n$(skills-sync card {H / 'noclose.md'})\nEOF") == "deny"
assert bash(f"cat <<'EOF'\ntext\nEOF\nskills-sync card {H / 'noclose.md'}") == "deny"
assert bash(f"echo 'a' # note\nskills-sync card {H / 'noclose.md'}") == "deny"
assert bash(f"echo \"it's\"; skills-sync card {H / 'noclose.md'}") == "deny"
assert bash(f"skills-sync card '{H / 'noclose.md'}'") == "deny"
assert bash(f"echo $((1<<2)); ((x <<= 1))\nskills-sync card {H / 'noclose.md'}") == "deny"  # a shift, not a heredoc
assert bash(f"cat <<<hello\nskills-sync card {H / 'noclose.md'}") == "deny"  # a here-string
assert bash(f"echo $[1<<2]\nskills-sync card {H / 'noclose.md'}") == "deny"
# A heredoc whose terminator line is not found takes nothing after it for a body.
assert bash(f"cat <<EO\\\nF\ntext\nEOF\nskills-sync card {H / 'noclose.md'}") == "deny"
for delim in ('E"OF"', "E\\OF", "'EOF'", '"EOF"', "\\EOF"):  # bash's delimiter is EOF in each
    assert bash(f"cat <<{delim}\ntext\nEOF\nskills-sync card {H / 'noclose.md'}") == "deny", delim
    assert bash(f"cat <<{delim}\nskills-sync card /\nEOF") == "none", delim
assert bash(f"if false; then\ncat <<$$'EOF'\ntext\n$$EOF\nfi\nskills-sync card {H / 'noclose.md'}\n$EOF") == "deny"
assert bash(f"(cat <<'EOF'\nit's ready\nEOF\n)\nskills-sync card {H / 'noclose.md'}") == "deny"  # a subshell's heredoc
assert bash(f"cat << \\\n'EOF'\nit's ready\nEOF\nskills-sync card {H / 'noclose.md'}") == "deny"  # an unread delimiter
assert bash(f"echo \\ #; skills-sync card {H / 'noclose.md'}") == "deny"  # an escaped space does not start a comment
assert bash(f"echo \\\n# '\nskills-sync card {H / 'noclose.md'}\n# '") == "deny"  # a comment after a line continuation
assert bash(f"echo \\\\ # don't\nskills-sync card {H / 'noclose.md'}") == "deny"  # `\\` is one backslash, then a comment
assert bash(f"echo a\\\\\n# don't\nskills-sync card {H / 'noclose.md'}") == "deny"  # `\\` then a real newline
assert bash(f"echo `# don't\nskills-sync card {H / 'noclose.md'}\n`") == "deny"  # a comment opening a substitution
assert bash(f"echo $(# don't\nskills-sync card {H / 'noclose.md'}\n)") == "deny"
assert bash(f"(true)# don't\nskills-sync card {H / 'noclose.md'}") == "deny"  # a subshell's ) ends a word
# Past a `${…}` (its quotes nest) or a `case` in `$(…)`, nothing is blanked.
assert bash(f"echo \"${{x:-\"it's ready\"}}\"\nskills-sync card {H / 'noclose.md'}") == "deny"
assert bash(f"echo ${{x:-\"it's\"}}\nskills-sync card {H / 'noclose.md'}") == "deny"
assert bash(f"echo \"`echo \\\"it's ready\\\"`\"\nskills-sync card {H / 'noclose.md'}") == "deny"  # \" in backticks
for sub in ("echo '", "printf \"%s"):  # an unclosed quote ends with the backtick substitution
    assert bash(f"echo `{sub}`\nskills-sync card {H / 'noclose.md'}") == "deny", sub
assert bash(f"echo \"$(case a in a) echo \"it's\";; esac)\"\nskills-sync card {H / 'noclose.md'}") == "deny"
assert bash(f"echo \\$'a\\'\nskills-sync card {H / 'noclose.md'}") == "deny"  # \$ is not ANSI-C quoting
assert bash(f"echo $$'a\\'\nskills-sync card {H / 'noclose.md'}") == "deny"  # nor is $$ (the PID) before a quote
for arith in ("$(( '{}' ))", "$[ '{}' ]", "(( '{}' ))", "\"$(( '{}' ))\"", "\"$[ '{}' ]\""):  # bash runs a substitution in arithmetic quotes
    assert bash("echo " + arith.format(f"$(skills-sync card {H / 'noclose.md'})")) == "deny", arith
for word in ("`x`", "$(x)", "<(true)", "a\\\\\\ "):  # a # inside a word is not a comment
    assert bash(f"echo {word}#; skills-sync card {H / 'noclose.md'}") == "deny", word
assert bash(f"cat <<true\ntr\\\nue\nskills-sync card {H / 'noclose.md'}\ntrue") == "deny"  # bash ends the body at tr\ue
# A delimiter bash rewrites through escapes is not read, so a later line spelling it raw blanks nothing.
for delim, raw in (("$'E\\x4fF'", "E\\x4fF"), ('"E\\OF"', "E\\OF")):
    assert bash(f"cat <<{delim}\ntext\nEOF\nskills-sync card {H / 'noclose.md'}\n{raw}") == "deny", delim
# A directory is named as one, not as an unreadable file.
assert bash("skills-sync card /") == "deny" and "names a directory" in reason("skills-sync card /")
assert "skills-sync card <brief>" in reason(f'{NEW} --prompt "/goal fix X"'), "the deny names the helper"
# skills-sync subcommands: read-only and fixed-text ones are allowed, the rest go to the classifier (no decision).
for cmd in ("skills-sync status", "skills-sync broadcast", "skills-sync broadcast --skills", "skills-sync broadcast --plugins --dry-run",
            "skills-sync broadcast --skills --terminal term_x", "skills-sync broadcast --retry",
            "skills-sync broadcast --skills --terminal $ORCA_TERMINAL_HANDLE",
            "skills-sync end-check --card --outcome succeeded --keep", "skills-sync end-check --worker --outcome failed --no-keep --json"):
    assert bash(cmd) == "allow", cmd
for cmd in ("skills-sync sync", "skills-sync sync --no-broadcast", "skills-sync setup --apply", "skills-sync setup enable x",
            "skills-sync nudge term_x hello", "skills-sync card --supersede x", "skills-sync status extra",
            "skills-sync end-check --card --apply", "skills-sync end-check --apply", "skills-sync end-check --card --trash-caches",
            "skills-sync end-check -x", "skills-sync broadcast --text hi", "skills-sync broadcast /reload-skills",
            "skills-sync broadcast --terminal", "skills-sync broadcast --terminal --skills",
            "skills-sync wake-reset term_x", "skills-sync", "skills-sync status; rm x", "skills-sync status && skills-sync sync",
            "skills-sync end-check --card > out", "skills-sync broadcast --skills $(id)",
            "skills-sync end-check --brief --apply", "skills-sync end-check --dispatch --apply",
            "/tmp/skills-sync end-check --apply", "./bin/skills-sync status"):
    assert bash(cmd) == "none", cmd
# What an allowed broadcast can type is this closed set (sync.py RELOAD and STALE_COMMAND); a new command there must be
# reviewed against the allow in guard.sync_allowed before this test is changed.
sys.path.insert(0, str(HERE.parent / "scripts"))
import sync
assert set(sync.RELOAD.values()) | set(sync.STALE_COMMAND.values()) == {"/reload-skills", "/reload-plugins", "/restart"}
print("guard: all pass")
