"""delegate_gate.py: an orchestrator session's own tool calls outside coordination are denied; subagents, other
sessions and the off switch pass.
Run: python3 hooks/test_delegate_gate.py"""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(os.path.realpath(tempfile.mkdtemp()))
HOME = TMP / "home"
STATE = TMP / "state"
CONFIG = TMP / "agent-skills.json"
ORCH = "agent-skills:orchestrator"
SCRATCH = pathlib.Path(f"/private/tmp/claude-{os.getuid()}/-proj/sess/scratchpad")


def run(tool, tool_input, agent=ORCH, session="s1", agent_id=None, field="permissionDecision"):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(HOME), "AGENT_SKILLS_STATE": str(STATE),
           "AGENT_SKILLS_CONFIG": str(CONFIG), "TMPDIR": str(TMP / "t"), "ORCH_FLEET_STATE": str(TMP / "fleet")}
    payload = {"hook_event_name": "PreToolUse", "session_id": session, "tool_name": tool, "tool_input": tool_input}
    if agent:
        payload["agent_type"] = agent
    if agent_id:
        payload["agent_id"] = agent_id
    out = subprocess.run([sys.executable, HERE / "delegate_gate.py"], input=json.dumps(payload),
                         capture_output=True, text=True, env=env, check=True).stdout
    return json.loads(out)["hookSpecificOutput"].get(field, "none") if out.strip() else "none"


def bash(cmd, **kw):
    return run("Bash", {"command": cmd}, **kw)


def role(session, name):
    (STATE / "roles").mkdir(parents=True, exist_ok=True)
    (STATE / "roles" / f"{session}.json").write_text(json.dumps({"role": name}))


# Coordination passes: the gate stays silent and the normal permission flow decides.
for cmd in ("orch status p", "orch inbox --wait", "skills-sync card /x/brief.md --repo r --name n",
            "skills-sync handoff ABC-1 --brief /x/brief.md",
            "/opt/x/bin/skills-sync broadcast", "orca orchestration send --to run:r --text 'a > b; $(not run)'",
            'orca orchestration check --terminal "$ORCA_TERMINAL_HANDLE" --json',
            "orca orchestration check --terminal=$ORCA_TERMINAL_HANDLE --wait",
            "orca orchestration inbox --terminal ${ORCA_TERMINAL_HANDLE} --json",
            "orca terminal read --terminal t1 --json 2>&1", "orca worktree list --json",
            "gh pr merge 12 --squash --match-head-commit abc", "gh pr ready 12",
            "gh pr view 12 --json state,mergeable", "gh pr checks 12 --json name,state | jq '.[].state'",
            "cd /x && orch status p", "orch landed p --pr acme/app#1", "git worktree remove /x/.claude/worktrees/agent-1",
            'orca orchestration send --to run:r --text "line one\nline two"',
            "orca skills get orchestration", "orch-dash inbox resolve --key x", "orch-dash ensure",
            f'orca orchestration send --to run:r --body "$(cat {HOME}/.local/state/msg-1.md)"',
            f'orca orchestration send --to run:r --body="$(cat {SCRATCH}/msg.md)"',
            "orch status p | jq -r '.x'", "orch status p | jq -rc --sort-keys ."):
    assert bash(cmd) == "none", cmd

# Task work in Bash is denied, with the delegation paths in the reason.
for cmd in ("ls", "cat README.md", "docker compose up -d", "grep -r x .", "orch heavy -- npm test",
            "orca goto https://example.test", "orca computer click", "gh pr view 12", "gh pr create",
            "gh api repos/acme/app", "git status", "orch status p > out.txt", "orch status $(cat f)",
            "orch status `id`", 'orca orchestration send --text "$(cat f)"', "orch status p; rm -rf x",
            "orch status p | sh", "jq . file.json", "(orch status p)", "X=1 orch status p", "orch status 'p",
            "python3 scripts/card.py b.md", "",
            # bash and shlex disagree on these: each spells `orch heavy` to bash
            "orch $'heavy' - -- ls", 'orch "hea\\\nvy" - -- ls', "orch hea\\\nvy - -- ls", "orch $X - -- ls",
            'orch "$X" - -- ls', "orch hea?y - -- ls", "orch [h]eavy - -- ls", "orch ${X} - -- ls",
            "orca skills share x", "orch-dash serve", "orch status p | jq . /repo/config.json",
            "orch status p | jq --rawfile a /repo/x .", "orch status p | jq -f /repo/prog.jq",
            f'orca orchestration send --body "$(cat {TMP}/repo/src/app.py)"',
            f'orca orchestration send --text "$(cat {HOME}/.local/state/msg-1.md)"',
            f'orca orchestration send --body "$(cat {HOME}/.local/state/msg-1.md; ls)"',
            "orch hea${ORCA_TERMINAL_HANDLE}vy - -- ls", "orch $ORCA_TERMINAL_HANDLE heavy - -- ls",
            "orca $ORCA_TERMINAL_HANDLE goto https://example.test", "orch status # '\nls\n# '",
            "orch status #x",
            f'orca orchestration send --body "$(cat {HOME}/.local/state/*.md)"',
            'cd /repo && orca orchestration send --body "$(cat .local/state/msg-1.md)"',
            'orca orchestration send --body "$(cat ~/.local/state/msg-1.md)"',
            f'orca orchestration send --body "$(cat\n{HOME}/.local/state/msg-1.md)"'):
    assert bash(cmd) == "deny", cmd
REASON = bash("ls", field="permissionDecisionReason")
for part in ("[gate:delegate]", "`skills-sync card <brief>` (dispatch-card)", "`skills-sync handoff`", "background Agent",
             "`orca orchestration send`", "\"Routing\"", "`!`"):
    assert part in REASON, part
assert len(REASON) <= 560, len(REASON)  # printed on every refusal

# Delegation tools pass; investigation tools and other MCP calls do not.
for tool in ("Agent", "SendMessage", "TaskStop", "Skill", "ToolSearch", "CronCreate",
             "mcp__plugin_telegram_telegram__reply"):
    assert run(tool, {}) == "none", tool
for tool in ("Grep", "Glob", "WebFetch", "WebSearch", "NotebookEdit", "Monitor", "mcp__linear__save_issue", "mcp__claude_ai_Linear__save_issue",
             "mcp__plugin_telegram_telegram__other"):
    assert run(tool, {}) == "deny", tool

# Read: own state, transcripts, task output and this plugin; not a repo.
for path in (HOME / ".claude/projects/p/s/subagents/agent-1.jsonl", HOME / ".local/state/agent-skills/x.json",
             HOME / "share/owner-checks/a/brief.md", SCRATCH.parent / "tasks/b1.output",
             HERE.parent / "skills/orchestrate/references/top-level.md"):
    assert run("Read", {"file_path": str(path)}) == "none", path
for path in (TMP / "repo/src/app.py", HOME / "workspace/x/README.md", HOME / ".claude/../workspace/x.md"):
    assert run("Read", {"file_path": str(path)}) == "deny", path

# Edit/Write: briefs and memory only.
for tool in ("Edit", "Write"):
    for path in (HOME / ".claude/projects/-p/memory/MEMORY.md", HOME / ".claude/programs/p/briefs/u1.md",
                 HOME / "share/owner-checks/x/brief.md", HOME / ".local/state/agent-skills/brief-last/a.md",
                 HOME / ".local/state/card-20261103.md", SCRATCH / "card-brief.md", SCRATCH / "msg.txt"):
        assert run(tool, {"file_path": str(path)}) == "none", (tool, path)
    for path in (TMP / "repo/src/app.py", HOME / ".claude/settings.json", HOME / ".claude/agent-skills.json",
                 HOME / ".claude/projects/-p/s.jsonl", SCRATCH / "brief.py", HOME / "share/other.html",
                 TMP / "brief.md", STATE / "roles/s1.json", HOME / ".local/state/agent-skills/roles/s1.json",
                 HOME / ".claude/programs/p/program.json", HOME / ".claude/projects/-p/memory/../x.md"):
        assert run(tool, {"file_path": str(path)}) == "deny", (tool, path)
    assert run(tool, {}) == "deny"
# A symlink inside an allowed directory that points into a repo is judged by its target.
link = HOME / ".local/state/link.md"
link.parent.mkdir(parents=True, exist_ok=True)
(TMP / "repo").mkdir(exist_ok=True)
(TMP / "repo/x.md").write_text("x")
link.symlink_to(TMP / "repo/x.md")
assert run("Write", {"file_path": str(link)}) == "deny"
assert "`!`" in bash("gh workflow run deploy", field="permissionDecisionReason")

# A subagent of the orchestrator shares its session id and still passes, whatever its type.
assert bash("docker compose up -d", agent="general-purpose", agent_id="a1") == "none"
assert run("Edit", {"file_path": str(TMP / "repo/x.py")}, agent=ORCH, agent_id="a1") == "none"
role("s2", "orchestrator")
assert bash("ls", agent=None, session="s2", agent_id="a2") == "none"

# Other sessions pass: no role, a lead, a declared orchestrator cleared with ROLE: none.
assert bash("ls", agent=None, session="plain") == "none"
assert bash("ls", agent="agent-skills:lead") == "none"
assert bash("ls", agent=None, session="s2") == "deny"  # declared by a ROLE line
role("s3", "none")
assert bash("ls", agent=ORCH, session="s3") == "none"
role("s4", "lead")
assert bash("ls", agent=None, session="s4") == "none"

# The off switch.
CONFIG.write_text(json.dumps({"orchestrate": {"delegate_gate": False}}))
assert bash("ls") == "none"
CONFIG.write_text(json.dumps({"orchestrate": {"delegate_gate": True}}))
assert bash("ls") == "deny"
CONFIG.write_text("not json")
assert bash("ls") == "deny"

# The pressure gate's wait: this plugin's pressure.py, `queue wait <id>` or `turns --wait <n>`, in the background.
PRESSURE = HERE.parent / "skills" / "reap-resources" / "scripts" / "pressure.py"
for args in ("queue wait q-66a001f1", "turns --wait 15", "turns --wait 7.5"):
    cmd = f"python3 {PRESSURE} {args}"
    assert run("Bash", {"command": cmd, "run_in_background": True}) == "none", cmd
    assert run("Bash", {"command": cmd}) == "deny", ("foreground", cmd)
OTHER = TMP / "skills" / "reap-resources" / "scripts" / "pressure.py"
for cmd in (f"python3 {PRESSURE} round", f"python3 {PRESSURE} check", f"python3 {PRESSURE} queue wait q-66a001f1 x",
            f"python3 {PRESSURE} queue wait q-XYZ", f"python3 {OTHER} queue wait q-66a001f1",
            f"python3 {PRESSURE} queue wait q-66a001f1; rm -rf x", f"python {PRESSURE} queue wait q-66a001f1",
            f"python3 {PRESSURE} turns '--wait 15'",
            f"cd {HERE.parent} && python3 skills/reap-resources/scripts/pressure.py queue wait q-66a001f1"):
    assert run("Bash", {"command": cmd, "run_in_background": True}) == "deny", cmd
assert run("Monitor", {"command": f"python3 {PRESSURE} queue wait q-66a001f1"}) == "deny"
assert run("Monitor", {"command": "orch inbox --stream", "description": "inbox", "timeout_ms": 1800000}) == "none"
for args in ({"command": "tail -f /tmp/x.log"}, {"command": "orch heavy - -- make test"},
             {"ws": {"url": "wss://example.test/s"}}, {"command": "orch inbox --stream", "ws": {"url": "wss://example.test"}}):
    assert run("Monitor", args) == "deny", args

# A ticket milestone without the task-tracker subagent: this plugin's tracker.py, transition, comment or attach,
# exact shapes.
TRACKER = HERE.parent / "skills" / "use-tracker" / "scripts" / "tracker.py"
BODY_MD = HOME / ".claude" / "programs" / "app" / "done.md"
PR_URL = "https://example.test/acme/app/pull/1"
for cmd in (f"python3 {TRACKER} transition --to review ABC-1", f"python3 {TRACKER} transition ABC-1 --to completed",
            f"python3 {TRACKER} --adapter linear transition --to started ABC-1",
            f"python3 {TRACKER} comment --body-file {BODY_MD} ABC-1",
            f"python3 {TRACKER} attach ABC-1 --url {PR_URL}", f"python3 {TRACKER} attach --url {PR_URL} ABC-1",
            f"python3 {TRACKER} --adapter jira attach ABC-1 --url {PR_URL}",
            f"python3 {TRACKER} reconcile ABC-1 --repo acme/app",
            f"python3 {TRACKER} reconcile ABC-1 --repo acme/app --repo acme/api --head feat/abc-1"):
    assert run("Bash", {"command": cmd}) == "none", cmd
OTHER_TRACKER = TMP / "skills" / "use-tracker" / "scripts" / "tracker.py"
for cmd in (f"python3 {TRACKER} create --title x", f"python3 {TRACKER} label ABC-1 --add x",
            f"python3 {TRACKER} transition --to done ABC-1", f"python3 {TRACKER} transition --to review",
            f"python3 {TRACKER} --config {TMP}/other.json transition --to review ABC-1",
            f"python3 {TRACKER} comment --body-file /etc/passwd ABC-1",
            f"python3 {TRACKER} comment --body-file {HOME}/.claude/agent-skills.json ABC-1",
            f"python3 {OTHER_TRACKER} transition --to review ABC-1",
            f"cd {HERE.parent} && python3 skills/use-tracker/scripts/tracker.py transition --to review ABC-1",
            f"python3 {TRACKER} transition --to review ABC-1; rm -rf x", "orca linear create --title x",
            f"python3 {TRACKER} attach ABC-1 --url {PR_URL} --title PR", f"python3 {TRACKER} attach ABC-1 --url",
            f"python3 {TRACKER} attach ABC-1 --url ftp://example.test/x", f"python3 {TRACKER} attach --url {PR_URL}",
            f"python3 {TRACKER} attach ABC-1 --body-file {BODY_MD}",
            f"orca linear attach ABC-1 --url {PR_URL} --title PR",
            f"python3 {TRACKER} reconcile ABC-1", f"python3 {TRACKER} reconcile --repo acme/app",
            f"python3 {TRACKER} reconcile ABC-1 --head b", f"python3 {TRACKER} reconcile ABC-1 --repo acme/app --head a --head b",
            f"python3 {TRACKER} reconcile ABC-1 --repo acme/app --config {TMP}/other.json",
            f"python3 {TRACKER} reconcile ABC-1 --repo acme/app --apply x", f"python3 {TRACKER} reconcile ABC-1 --repo -x/y"):
    assert run("Bash", {"command": cmd}) == "deny", cmd

# A delegation with no owner order behind it is warned about, never blocked, and only once `orch order setup` ran.
BRIEF_PLAIN = TMP / "plain.md"
BRIEF_PLAIN.write_text("/goal x\nORDER       Starts after: none. Class: normal.\nCLOSE: x\n")
BRIEF_ORDER = TMP / "ordered.md"
BRIEF_ORDER.write_text("/goal x\nORDER: o3\nCLOSE: x\n")
AGENT = {"prompt": "look at the logs", "description": "logs"}
note = lambda tool, args, **kw: run(tool, args, field="additionalContext", **kw)
card_cmd = lambda *a: {"command": "skills-sync card " + " ".join(a) + " --repo r --name n"}
for tool, args in (("Agent", AGENT), ("Bash", card_cmd(str(BRIEF_PLAIN))), ("Bash", card_cmd(str(TMP / "missing.md")))):
    assert note(tool, args) == "none", (tool, "no tables yet, no warning")
os.environ.update(ORCH_FLEET_STATE=str(TMP / "fleet"), AGENT_SKILLS_STATE=str(STATE), AGENT_SKILLS_CONFIG=str(CONFIG),
                  HOME=str(HOME), ORCA_TERMINAL_HANDLE="term_gate")
(TMP / "fleet").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(HERE.parent / "skills" / "orchestrate" / "scripts"))
import store  # noqa: E402
store.import_apply(checks=False)
store.order_setup()
for tool, args in (("Agent", AGENT), ("Task", AGENT), ("Bash", card_cmd(str(BRIEF_PLAIN))),
                   ("Bash", card_cmd(str(TMP / "missing.md")))):
    assert "carries no owner order" in note(tool, args), tool
    assert run(tool, args) == "none", "a warning is not a decision"
for tool, args in (("Agent", {"prompt": "look at the logs\nORDER: o3\nthen report"}),
                   ("Bash", card_cmd(str(BRIEF_ORDER))), ("Bash", card_cmd(str(BRIEF_PLAIN), "--order o3")),
                   ("Bash", {"command": "orch status p"}), ("Read", {"file_path": str(BRIEF_PLAIN)})):
    assert note(tool, args) == "none", (tool, args)
BRIEF_FENCED = TMP / "fenced.md"
BRIEF_FENCED.write_text("/goal x\n```\nORDER: o3\n```\nCLOSE: x\n")
assert "carries no owner order" in note("Bash", card_cmd(str(BRIEF_FENCED))), "a quoted row is not the brief's own"
assert "carries no owner order" in note("Agent", {"prompt": "example:\n```\nORDER: o3\n```\nlook at the logs"}), "a fenced example is not an order"
assert note("Agent", {"prompt": "example:\n```\nORDER: o3\n```\nORDER: o7\nlook"}) == "none"
assert note("Agent", {"prompt": "see ORDER: o3 in prose, ORDER o3 too"}).startswith("[gate:delegate]"), "a mention is not a line"
assert note("Agent", AGENT, agent=None) == "none" and note("Agent", AGENT, agent_id="a1") == "none", "only the orchestrator's own call"
assert bash("rm -rf x") == "deny", "a denied call stays denied"

# The hook is wired for every tool.
sys.path.insert(0, str(HERE))
import dispatch  # noqa: E402
assert dispatch.ENTRIES["PreToolUse"][0][:2] == ("delegate_gate", None), dispatch.ENTRIES["PreToolUse"][0]
assert dispatch.HOOK_MATCHER["PreToolUse"] is None, "the dispatch entry has no matcher, so it meets every tool"
print("ok")
