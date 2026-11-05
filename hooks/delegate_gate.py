#!/usr/bin/env python3
"""PreToolUse hook of the agent-skills plugin: the top-level orchestrator's own turn holds only coordination.

The reorient reminder told the orchestrator to hand every instruction one layer down, and it kept investigating,
writing files and running docker in its own turn anyway. This hook denies every tool call of an orchestrator session
outside the list below, with a reason that names the delegation paths.

It acts only when all of these hold:
- the call is the main conversation's, not a subagent's or a teammate's: the input has no `agent_id`. Subagents share
  the parent's `session_id`, so the role record alone would catch them.
- the session is the orchestrator by roles.py: `agent_type` agent-skills:orchestrator, or a `ROLE: orchestrator`
  record; a `ROLE: none` record clears it.
- `orchestrate.delegate_gate` in ~/.claude/agent-skills.json is not `false`. The orchestrator cannot flip it from its
  own turn: that file is outside the write list and no Bash command reaches it.

Allowed (orchestrate references/top-level.md, "Routing", and SKILL.md "Stay answerable"):
- delegation and session tools: Agent, SendMessage, TaskStop, TaskOutput, tasks, cron, Skill, ToolSearch, plan
  mode, Telegram reply/react/edit/download. AskUserQuestion is left to guard.py: it denies the tool for every role
  session (top-level.md "Asking the human"), and its key `orchestrate.ask_user_question` turns that off.
- Bash, when every command in it is coordination: `orch` (not `heavy`, which runs any command), `orch-dash
  inbox|ensure|note`, `skills-sync`, `orca` coordination groups and `orca skills get|list` (not browser, computer use
  or emulator), `gh pr merge|ready`, a `gh … --json` read, `git worktree list|remove`, `cd`, `jq <filter>` after
  a pipe, and the pressure gate's wait (`python3 <this plugin>/skills/reap-resources/scripts/pressure.py queue wait
  q-<id>` or `turns --wait <n>`) with run_in_background, which pressure_gate.py's block names. Monitor, when
  its command passes the same check (the inbox stream, `orch inbox --stream`), and never with a ws source. The only expansions are $ORCA_TERMINAL_HANDLE as an option's whole value and top-level.md's
  `--body "$(cat <absolute path>)"` of a file this hook lets the session write; no other substitution, variable, glob, backtick, `$'…'`, line continuation, or
  redirection other than `2>&1` and to /dev/null. guard.py still denies a send or reply to /dev/null
  (`orchestrate.send_devnull`).
- Read under the session's own state: ~/.claude, ~/.local/state/agent-skills, ~/share, background task output
  (claude-<uid> temp dirs) and this plugin.
- Edit/Write of a Markdown or text file (a brief, a message body, a memory) under ~/.claude/projects/*/memory,
  ~/.claude/programs, ~/.local/state, ~/share/owner-checks or the claude-<uid> temp dirs. JSON state, the role
  records this hook reads among it, stays out of reach.
Everything else, other MCP tools included, is denied.

A guardrail against the orchestrator's habit, not a sandbox: an allowed command's arguments are not checked.
"""
import json, os, pathlib, re, shlex, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import roles  # noqa: E402
from lib import config  # noqa: E402

MARK = "[gate:delegate]"
SOURCE = "(agent-skills hooks/delegate_gate.py)"
TOOLS = {"Agent", "Task", "SendMessage", "TaskStop", "TaskOutput", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
         "TodoWrite", "CronCreate", "CronDelete", "CronList", "Skill", "ToolSearch", "EnterPlanMode", "ExitPlanMode",
         "AskUserQuestion"}  # guard.py's rule, not this gate, decides AskUserQuestion
TELEGRAM = re.compile(r"mcp__plugin_telegram_telegram__(reply|react|edit_message|download_attachment)")
ORCA = {"orchestration", "terminal", "worktree", "file", "status", "agent", "host", "reference", "automations"}
DASH = {"inbox", "ensure", "note"}
# jq after a pipe filters its input; a file operand or a file option would read a file the Read check refuses.
JQ_FLAGS = re.compile(r"-[rcejsSMCn]+|--(raw-output|compact-output|exit-status|join-output|sort-keys|monochrome-output)")
# Only as an option's whole value: unset, it vanishes, and inside a word it could spell an excluded verb.
OWN_REF = re.compile(r"""(--[\w-]+[ =])(["']?)(?:\$\{ORCA_TERMINAL_HANDLE\}|\$ORCA_TERMINAL_HANDLE)\2(?![^\s;&|])""")
# An absolute literal path: a glob or a relative path after `cd` would read another file than the one checked.
BODY = re.compile(r'--body(=|[ \t]+)"\$\(cat[ \t]+(/[^\s"\'`$()\\;&|<>*?\[\]{}~]+)\)"')
GH_READ = {"view", "list", "checks", "status"}
# Whole tokens that only merge or drop output; anything else with < or > writes or reads a file.
QUIET = re.compile(r"(?<!\S)(?:2>&1|[12]?>\s*/dev/null)(?!\S)")
SPLIT = re.compile(r"(\|\||&&|[;&|\n])")


def home(*parts):
    return pathlib.Path(os.path.expanduser("~"), *parts)


def real(path):
    return pathlib.Path(os.path.realpath(os.path.expanduser(str(path))))


def under(path, root):
    try:
        path.relative_to(real(root))
        return True
    except ValueError:
        return False


def temp_roots():
    """Background task output and session scratchpads live in claude-<uid> under the temp dir."""
    uid = os.getuid()
    bases = {"/tmp", "/private/tmp", os.environ.get("TMPDIR") or "/tmp"}
    return [real(pathlib.Path(b) / f"claude-{uid}") for b in bases]


def readable(path):
    roots = [home(".claude"), home(".local/state/agent-skills"), home("share"),
             pathlib.Path(__file__).resolve().parents[1], *temp_roots()]
    return any(under(path, r) for r in roots)


def writable(path):
    if path.suffix not in (".md", ".txt"):
        return False
    projects = real(home(".claude/projects"))
    if under(path, projects) and path.relative_to(projects).parts[1:2] == ("memory",):
        return True
    return any(under(path, r) for r in (home(".claude/programs"), home(".local/state"), home("share/owner-checks"),
                                         *temp_roots()))


def body_file(match):
    return f"--body{match.group(1)}BODY" if writable(real(match.group(2))) else match.group(0)


def segments(cmd):
    """The commands of a Bash call as word lists, each tagged with whether a pipe feeds it; None when the text holds
    anything this gate does not follow (substitution, backticks, redirection, subshell, brace or unclosed quote)."""
    text = OWN_REF.sub(r"\1\2H\2", BODY.sub(body_file, QUIET.sub(" ", cmd)))
    if "\\\n" in text:  # bash joins the lines, inside double quotes too; shlex does not
        return None
    out, buf, quote, piped, i = [], [], None, False, 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == quote:
                quote = None
            elif quote == '"' and c in "`$":
                return None
            elif quote == '"' and c == "\\":
                buf.append(text[i:i + 2])
                i += 2
                continue
            buf.append(c)
        elif c in "'\"":
            quote = c
            buf.append(c)
        elif c == "\\":
            buf.append(text[i:i + 2])
            i += 2
            continue
        elif c in "`<>(){}$*?[" or (c == "#" and (not buf or buf[-1] in " \t")):  # `#` opens a comment
            return None
        elif (m := SPLIT.match(text, i)):
            out.append((piped, "".join(buf)))
            buf, piped = [], m.group(1) == "|"
            i = m.end()
            continue
        else:
            buf.append(c)
        i += 1
    if quote:
        return None
    out.append((piped, "".join(buf)))
    try:
        return [(p, shlex.split(s)) for p, s in out if s.strip()]
    except ValueError:
        return None


# The pressure gate's wait, the one way it leaves an orchestrator to wait for a queued heavy run: this plugin's
# pressure.py by real path, only these two forms, and only in the background (as every wait of a role session).
PRESSURE = pathlib.Path(__file__).resolve().parents[1] / "skills" / "reap-resources" / "scripts" / "pressure.py"


def pressure_wait(words, background):
    """The path must be absolute: a `cd` earlier in the command would move a relative one."""
    if not (background and words[:1] == ["python3"] and len(words) == 5 and os.path.isabs(words[1])
            and real(words[1]) == real(PRESSURE)):
        return False
    verb, flag, value = words[2:]
    return ((verb, flag) == ("queue", "wait") and bool(re.fullmatch(r"q-[0-9a-f]{8}", value))
            or (verb, flag) == ("turns", "--wait") and bool(re.fullmatch(r"\d+(\.\d+)?", value)))


def coordination(words, piped, background=False):
    if pressure_wait(words, background):
        return True
    name = os.path.basename(words[0])
    if "/" in words[0] and name not in ("orch", "skills-sync"):
        return False
    args = words[1:]
    if name == "cd":
        return True
    if name == "jq":
        return piped and len([a for a in args if not JQ_FLAGS.fullmatch(a)]) == 1 and not args[-1].startswith("-")
    if name == "orch-dash":
        return bool(args) and args[0] in DASH
    if name == "orch":
        return "heavy" not in args
    if name == "skills-sync":
        return True
    if name == "orca":
        return bool(args) and (args[0] in ORCA or args[:2] in (["skills", "get"], ["skills", "list"]))
    if name == "gh":
        if args[:2] in (["pr", "merge"], ["pr", "ready"]):
            return True
        return len(args) >= 2 and args[1] in GH_READ and any(a == "--json" or a.startswith("--json=") for a in args)
    if name == "git":
        return args[:2] in (["worktree", "list"], ["worktree", "remove"])
    return False


def bash_ok(cmd, background=False):
    parts = segments(cmd)
    return bool(parts) and all(coordination(words, piped, background) for piped, words in parts)


def allowed(tool, args):
    if tool in TOOLS or TELEGRAM.fullmatch(tool or ""):
        return True
    if tool == "Bash":
        return bash_ok(str(args.get("command") or ""), args.get("run_in_background") is True)
    if tool == "Monitor":  # the inbox stream (`orch inbox --stream`); a ws source or any other command is task work
        # Not as a background command: the pressure gate's wait stays a Bash run_in_background (its own rule).
        return "ws" not in args and bash_ok(str(args.get("command") or ""), False)
    path = args.get("file_path")
    if not isinstance(path, str) or not path:
        return False
    if tool == "Read":
        return readable(real(path))
    if tool in ("Edit", "Write"):
        return writable(real(path))
    return False


def gate_on():
    return config.get(config.load(), "orchestrate.delegate_gate") is not False


def is_orchestrator(event):
    if event.get("agent_id"):
        return False
    entry = roles.declared_role(event)
    return bool(entry) and entry.get("role") == "orchestrator"


def reason(tool):
    return (f"{MARK} {SOURCE} This session is the top-level orchestrator, and {tool} here is task work. Hand it one "
            "layer down now, with the facts you already have: a background subagent (Agent, run_in_background) for a "
            "lookup, a read or a review; `skills-sync card <brief>` or `orca orchestration worker-start` for a change "
            "or an investigation in one repo; `orca orchestration send` to the lead or session that owns the topic. "
            "Your own turn keeps coordination only (orchestrate references/top-level.md, \"Routing\"). An act only the "
            "human's session may take (a production release, an approved guard change) is not delegated: ask the "
            "human to run that exact command as a `!` line in this chat.")


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(event, dict) or not is_orchestrator(event) or not gate_on():
        return 0
    tool, args = str(event.get("tool_name") or ""), event.get("tool_input") or {}
    if not isinstance(args, dict) or not allowed(tool, args):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": reason(tool)}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
