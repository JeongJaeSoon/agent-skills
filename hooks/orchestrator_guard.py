#!/usr/bin/env python3
"""PreToolUse hook of the agent-skills plugin: keeps the top-level orchestrator's own hands off task work.

A top-level orchestrator kept a rule to route and delegate in its instructions, in reminders on every prompt and in
memory lines, and still read an admin page in a browser for ten calls, mapped owners with gh and a chat tool, and
wrote and sent a chat post itself. A watcher it ran in the background was piped through `tail`, so the findings it
printed never surfaced. Text did not hold; this hook refuses the call instead.

Only the orchestrator's main thread is checked:
- `agent_type` is `agent-skills:orchestrator` (`claude --agent agent-skills:orchestrator`). A subagent reports its
  own type there, so the subagents it delegates to are never checked.
- or the session declared `ROLE: orchestrator` (the reorient hook's record) and the call carries neither `agent_id`
  nor `agent_type`. Subagents share the session id; `agent_id` tells them apart.
`ROLE: none` turns the hook off for that session; AGENT_SKILLS_ORCHESTRATOR_GUARD=0 turns it off everywhere.

In that thread:
- Deny browser, desktop and chat-send tools, WebFetch and WebSearch: each is a subagent's or a worker's job.
- Deny Edit/Write/NotebookEdit outside the state directories (the write_dirs below).
- Deny a background Bash call piped into `tail` or `head` (not `-f`): a watcher's lines stay in the pipe until it
  exits, so its findings never reach the output file that wakes the session.
- Count work calls per turn: Read outside the state directories, Grep, Glob, any MCP tool not denied above, and
  Bash with a command outside the routing list. After `work_calls` of them (default 3) the next one is denied.
  A turn is one `prompt_id`; a gap of GAP seconds also starts one, for wakes that may not carry a new id.
Routing tools (Agent, SendMessage, AskUserQuestion, Skill, Cron*, Monitor, TaskStop, ...) are never counted or
denied, so a wrong count leaves the session able to delegate.

Config, all optional: ~/.claude/agent-skills.json → "orchestrator_guard": {"work_calls": 3, "write_dirs": [...],
"route_commands": [...], "deny_tools": [regex, ...]}; lists extend the defaults. State:
$AGENT_SKILLS_STATE/orchestrator-guard/<session_id>.json.
"""
import json, os, pathlib, re, shlex, sys, time

STATE = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser()
PROGRAMS = pathlib.Path(os.environ.get("PROGRAMS_HOME", "~/.claude/programs")).expanduser()
CONFIG = pathlib.Path("~/.claude/agent-skills.json").expanduser()
ORCH = "agent-skills:orchestrator"
SESSION = re.compile(r"[\w-]{1,128}")
GAP = 600
DENY_TOOLS = [r"mcp__claude-in-chrome__", r"mcp__.*[Bb]rowser", r"mcp__computer-use__", r"mcp__remote-devices__",
              r"mcp__.*[Ss]lack.*(send|schedule|post|reaction|canvas)", r"^Web(Fetch|Search)$"]
WRITE_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
WORK_TOOLS = {"Read", "Grep", "Glob", "LSP"}
ROUTE_COMMANDS = {"orca", "orch", "orch-dash", "skills-sync", "date", "sleep", "echo", "true", "cd"}
FILTERS = {"jq", "head", "tail", "grep", "wc", "sort", "uniq", "cut", "tr", "column"}
REDIRECT = re.compile(r"\d*>&\d+")
SPLIT = re.compile(r"&&|\|\||[;\n&]")
WATCH_TAIL = re.compile(r"\|\s*(?:tail|head)\b(?![^|;&\n]*\s-[a-zA-Z]*[fF])")
MEMORY = re.compile(r"/projects/[^/]+/memory(?:/|$)")
DELEGATE = ("Delegate it: a background subagent (Agent with run_in_background), `orca worktree create` or "
            "`dispatch-card` for work in one repo, the project's lead through `orca orchestration send`, or `orch …` "
            "for a program (orchestrate references/top-level.md, Routing). Do not reach the same result through another "
            "tool: that launders the refusal.")


def config():
    try:
        cfg = json.loads(CONFIG.read_text()).get("orchestrator_guard") or {}
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def is_orchestrator(event, session):
    agent = event.get("agent_type")
    if agent and agent != ORCH:
        return False
    if not agent and event.get("agent_id"):
        return False
    try:
        role = json.loads((STATE / "roles" / f"{session}.json").read_text()).get("role")
    except (OSError, ValueError, AttributeError):
        role = None
    if role == "none":
        return False
    return agent == ORCH or role == "orchestrator"


def resolved(path):
    try:
        return pathlib.Path(str(path)).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def in_state_dirs(path, cfg):
    p = resolved(path) if path else None
    if p is None:
        return False
    dirs = [pathlib.Path("~/.local/state").expanduser(), STATE, PROGRAMS]
    dirs += [pathlib.Path(str(d)).expanduser() for d in cfg.get("write_dirs") or []]
    return MEMORY.search(str(p)) is not None or any(p.is_relative_to(d.resolve()) for d in dirs)


def routing_bash(cmd, cfg):
    """True when every command in the line is a routing command, or a filter after a pipe."""
    if "$(" in cmd or "`" in cmd:  # a substitution runs a command of its own
        return False
    allowed = ROUTE_COMMANDS | set(cfg.get("route_commands") or [])
    for part in SPLIT.split(REDIRECT.sub(" ", cmd)):
        for i, stage in enumerate(part.split("|")):
            try:
                words = shlex.split(stage)
            except ValueError:
                return False
            while words and re.fullmatch(r"[A-Za-z_]\w*=\S*", words[0]):
                words = words[1:]
            if not words:
                continue
            if words[0] not in allowed and not (i and words[0] in FILTERS):
                return False
    return True


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))


def count(session, event):
    """Adds one work call to this turn's count; returns the count including this call."""
    path = STATE / "orchestrator-guard" / f"{session}.json"
    now = time.time()
    try:
        rec = json.loads(path.read_text())
    except (OSError, ValueError):
        rec = {}
    turn = event.get("prompt_id")
    if rec.get("turn") != turn or now - rec.get("at", 0) > GAP:
        rec = {"turn": turn, "n": 0}
    rec["n"] = rec.get("n", 0) + 1
    rec["at"] = now
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rec))
    except OSError:
        pass
    return rec["n"]


def check(event, session):
    cfg = config()
    tool, args = str(event.get("tool_name") or ""), event.get("tool_input") or {}
    if any(re.search(p, tool) for p in DENY_TOOLS + list(cfg.get("deny_tools") or [])):
        return deny(f"You are the top-level orchestrator: {tool} is a subagent's or a worker's job, not yours. "
                    f"{DELEGATE} For a chat post, hand the subagent the facts, not the finished text.")
    if tool in WRITE_TOOLS:
        path = args.get("file_path") or args.get("notebook_path")
        if not in_state_dirs(path, cfg):
            return deny(f"You are the top-level orchestrator: {tool} on {path} edits task files. You write only your "
                        f"own state (~/.local/state, the programs and memory directories, or "
                        f"orchestrator_guard.write_dirs in ~/.claude/agent-skills.json). {DELEGATE}")
        return
    work = tool in WORK_TOOLS or tool.startswith("mcp__")
    if tool == "Read" and in_state_dirs(args.get("file_path"), cfg):
        work = False
    if tool == "Bash":
        cmd = str(args.get("command") or "")
        if args.get("run_in_background") is True and WATCH_TAIL.search(cmd):
            return deny("A background command piped into tail or head holds every line until it exits, so a "
                        "watcher's findings never reach its output file. Run it without the pipe (filter inside the "
                        "script, or read the output file when it wakes you), or hand the watching to a subagent.")
        work = not routing_bash(cmd, cfg)
    if not work:
        return
    limit = cfg.get("work_calls", 3)
    if not isinstance(limit, int) or limit < 0:
        limit = 3
    n = count(session, event)
    if n > limit:
        return deny(f"You are the top-level orchestrator, and this is direct work call {n} this turn "
                    f"(limit {limit}): reading code, pages, PRs or chats yourself keeps the human waiting. Stop "
                    f"investigating here. {DELEGATE}")


def main():
    if os.environ.get("AGENT_SKILLS_ORCHESTRATOR_GUARD") == "0":
        return 0
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    session = str(event.get("session_id") or "")
    if SESSION.fullmatch(session) and is_orchestrator(event, session):
        check(event, session)
    return 0


if __name__ == "__main__":
    sys.exit(main())
