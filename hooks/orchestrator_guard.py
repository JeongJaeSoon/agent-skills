#!/usr/bin/env python3
"""PreToolUse hook of the agent-skills plugin: keeps the top-level orchestrator's own hands off task work.

A top-level orchestrator kept a rule to route and delegate in its instructions, in reminders on every prompt and in
memory lines, and still read an admin page in a browser for ten calls, mapped owners with gh and a chat tool, and
wrote and sent a chat post itself. A watcher it ran in the background was piped through `tail`, so the findings it
printed never surfaced. Text did not hold; this hook refuses the call instead.

Only the orchestrator's main thread is checked:
- `agent_type` is `agent-skills:orchestrator` (`claude --agent agent-skills:orchestrator`) and there is no `agent_id`.
  A subagent carries an `agent_id`, so the subagents it delegates to are never checked.
- or the session declared `ROLE: orchestrator` (the reorient hook's record) and the call carries neither `agent_id`
  nor `agent_type`. Subagents share the session id; `agent_id` tells them apart.
`ROLE: none` turns the hook off for that session; AGENT_SKILLS_ORCHESTRATOR_GUARD=0 turns it off everywhere.

In that thread:
- Deny browser, desktop and chat-send tools, WebFetch and WebSearch: each is a subagent's or a worker's job.
- Deny Edit/Write/NotebookEdit outside the state directories: ~/.local/state, $AGENT_SKILLS_STATE, $PROGRAMS_HOME,
  the memory directories under $CLAUDE_CONFIG_DIR/projects, the temp and scratchpad directories, and the
  write_dirs below.
- Deny a background Bash call piped into `tail` or `head` (not `-f`): a watcher's lines stay in the pipe until it
  exits, so its findings never reach the output file that wakes the session.
- Count work calls per turn: Read outside the state directories, Grep, Glob, any MCP tool not denied above, and
  Bash running anything besides routing commands (a filter after a pipe, a redirect into a state directory).
  After `work_calls` of them (default 3) the next one is denied. A turn is one `prompt_id`, and a gap of GAP seconds
  also starts one, for wakes that may not carry a new id; a call without a `prompt_id` is not counted.
Routing tools (Agent, SendMessage, AskUserQuestion, Skill, Cron*, Monitor, TaskStop, ...) are never counted or
denied, so a wrong count leaves the session able to delegate. Malformed input, state or config lets the call through.

Config, all optional: ~/.claude/agent-skills.json → "orchestrator_guard": {"work_calls": 3, "write_dirs": [...],
"route_commands": [...], "deny_tools": [regex, ...]}; lists extend the defaults. State:
$AGENT_SKILLS_STATE/orchestrator-guard/<session_id>.json.
"""
import json, os, pathlib, re, shlex, sys, tempfile, time

STATE = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser()
PROGRAMS = pathlib.Path(os.environ.get("PROGRAMS_HOME", "~/.claude/programs")).expanduser()
CLAUDE_DIR = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()
CONFIG = pathlib.Path("~/.claude/agent-skills.json").expanduser()
ORCH = "agent-skills:orchestrator"
SESSION = re.compile(r"[\w-]{1,128}")
GAP = 600
DENY_TOOLS = [r"mcp__claude-in-chrome__", r"mcp__.*[Bb]rowser", r"mcp__computer-use__", r"mcp__remote-devices__",
              r"mcp__.*[Ss]lack.*(send|schedule|post|add_reaction|create_canvas|update_canvas)", r"^Web(Fetch|Search)$"]
WRITE_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
WORK_TOOLS = {"Read", "Grep", "Glob", "LSP"}
ROUTE_COMMANDS = {"orca", "orch", "orch-dash", "skills-sync", "date", "sleep", "echo", "true", "cd"}
FILTERS = {"jq", "head", "tail", "grep", "wc", "sort", "uniq", "cut", "tr", "column", "tee"}
SEPARATORS = {";", ";;", "&", "&&", "||", "|", "|&"}
PIPES = {"|", "|&"}
REDIRECTS = {">", ">>", ">|", "&>", "&>>"}
BODY = re.compile(r"\$\(cat\s+[^\s()`$;|&<>]+\)")
FOLLOW = re.compile(r"-[A-Za-z]*[fF][A-Za-z]*|--follow(=.*)?")
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


def listed(cfg, key):
    """A config list; anything else is ignored, so a typo cannot widen what is allowed."""
    value = cfg.get(key)
    return [str(v) for v in value] if isinstance(value, list) else []


def is_orchestrator(event, session):
    if event.get("agent_id"):  # a subagent, whatever its type
        return False
    agent = event.get("agent_type")
    if agent and agent != ORCH:
        return False
    try:
        role = json.loads((STATE / "roles" / f"{session}.json").read_text()).get("role")
    except (OSError, ValueError, AttributeError):
        role = None
    if role == "none":
        return False
    return agent == ORCH or role == "orchestrator"


def in_state_dirs(path, cfg):
    try:
        p = pathlib.Path(str(path)).expanduser().resolve() if path else None
    except (OSError, RuntimeError, ValueError):
        p = None
    if p is None:
        return False
    projects = (CLAUDE_DIR / "projects").resolve()
    if p.is_relative_to(projects) and p.relative_to(projects).parts[1:2] == ("memory",):
        return True
    dirs = [pathlib.Path("~/.local/state").expanduser(), STATE, PROGRAMS]
    dirs += [pathlib.Path(d).expanduser() for d in listed(cfg, "write_dirs")]
    return any(p.is_relative_to(d.resolve()) for d in dirs)


def commands(cmd):
    """The simple commands of a command line as (after a pipe, words), or None when it runs a substitution or does
    not parse. A newline outside quotes separates commands; one inside a quoted message does not."""
    cmd = BODY.sub("BODY", cmd)  # `--body "$(cat <file>)"`, the message form top-level.md prescribes
    text, quote = [], None
    for i, c in enumerate(cmd):
        if quote == "'":
            quote = None if c == "'" else quote
        elif c == "`" or c == "$" and cmd[i + 1:i + 2] == "(":
            return None
        elif quote == '"':
            quote = None if c == '"' else quote
        elif c in "'\"":
            quote = c
        elif c == "\n":
            c = ";"
        text.append(c)
    lex = shlex.shlex("".join(text), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        tokens = list(lex)
    except ValueError:
        return None
    out, words, piped = [], [], False
    for t in tokens:
        if t in SEPARATORS:
            out.append((piped, words))
            words, piped = [], t in PIPES
        else:
            words.append(t)
    out.append((piped, words))
    return out


def bash_work(cmd, cfg):
    """False when every command is a routing command, or a filter after a pipe, and writes only to state dirs."""
    parsed = commands(cmd)
    if parsed is None:
        return True
    allowed = ROUTE_COMMANDS | set(listed(cfg, "route_commands"))
    for piped, words in parsed:
        for op, target in zip(words, words[1:]):
            if op in REDIRECTS and target != "/dev/null" and not in_state_dirs(target, cfg):
                return True
        while words and re.fullmatch(r"[A-Za-z_]\w*=\S*", words[0]):
            words = words[1:]
        if words and words[0] not in allowed and not (piped and words[0] in FILTERS):
            return True
    return False


def watch_tail(cmd):
    """A pipe into tail or head without follow: nothing reaches the output file until the command exits."""
    return any(piped and words[:1] in (["tail"], ["head"]) and not any(FOLLOW.fullmatch(w) for w in words)
               for piped, words in commands(cmd) or [])


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))


def count(session, event):
    """Adds one work call to this turn's count and returns it; 0 when the call names no turn."""
    turn = event.get("prompt_id")
    if not turn:
        return 0
    path = STATE / "orchestrator-guard" / f"{session}.json"
    now = time.time()
    try:
        rec = json.loads(path.read_text())
        n = rec["n"] if rec.get("turn") == turn and now - float(rec.get("at", 0)) <= GAP else 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        n = 0
    n = int(n) + 1
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"turn": turn, "n": n, "at": now}))
        os.replace(tmp, path)
    except OSError:
        pass
    return n


def check(event, session):
    cfg = config()
    # The session's own scratch space holds message bodies and drafts, never task files.
    temp = [tempfile.gettempdir(), "/tmp", event.get("scratchpad_dir")]
    cfg["write_dirs"] = listed(cfg, "write_dirs") + [str(d) for d in temp if d]
    tool, args = str(event.get("tool_name") or ""), event.get("tool_input")
    args = args if isinstance(args, dict) else {}
    patterns = DENY_TOOLS + listed(cfg, "deny_tools")
    if any(re.search(p, tool) for p in patterns if valid(p)):
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
        if args.get("run_in_background") is True and watch_tail(cmd):
            return deny("A background command piped into tail or head holds every line until it exits, so a "
                        "watcher's findings never reach its output file. Run it without the pipe (filter inside the "
                        "script, or read the output file when it wakes you), or hand the watching to a subagent.")
        work = bash_work(cmd, cfg)
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


def valid(pattern):
    try:
        re.compile(pattern)
        return True
    except re.error:
        return False


def main():
    if os.environ.get("AGENT_SKILLS_ORCHESTRATOR_GUARD") == "0":
        return 0
    try:
        event = json.load(sys.stdin)
        session = str(event.get("session_id") or "")
        if SESSION.fullmatch(session) and is_orchestrator(event, session):
            check(event, session)
    except Exception:  # a guardrail: a malformed input or state file lets the call through, never blocks the session
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
