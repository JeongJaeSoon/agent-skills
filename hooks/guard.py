#!/usr/bin/env python3
"""PreToolUse hook of the agent-skills plugin: the permission rules a plugin cannot ship as settings.

Deny: `orca orchestration check|inbox --terminal <handle>` for a handle that is not the caller's own
($ORCA_TERMINAL_HANDLE, set in every Orca terminal). It would read, and with --ack consume, another
agent's messages.

Allow, so workers never stall on a prompt hours after they loaded a skill:
- this plugin's skills
- `orca orchestration <verb>`, except reset, worker-abandon and gate-resolve
- `orch <subcommand>` (the program ledger and the landing gate), except heavy, which runs any command, and
  set and init, which can change a program's merge policy

An allow here skips the auto mode classifier, so it covers only one simple command whose words bash passes
as written: outside quotes, plain characters only (no operators, redirections, substitutions, expansions or
variables other than $ORCA_TERMINAL_HANDLE), so the allowed prefix cannot carry another command or verb. Everything else gets no decision and takes the normal permission flow.
"""
import json, os, pathlib, re, shlex, string, sys

ROOT = pathlib.Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or pathlib.Path(__file__).resolve().parents[1])
PLUGIN = "agent-skills"
MAILBOX = re.compile(r"\borca\s+orchestration\s+(?:check|inbox)\b[^;&|\n]*?\s--terminal(?:\s+|=)(\S+)")
OWN_VAR = ("$ORCA_TERMINAL_HANDLE", "${ORCA_TERMINAL_HANDLE}")
# Only as a whole value (`--terminal $X`, `--terminal="$X"`): inside a word it could spell an excluded verb.
OWN_REF = re.compile(r"(?<![^\s\"'=])(?:\$\{ORCA_TERMINAL_HANDLE\}|\$ORCA_TERMINAL_HANDLE)(?![^\s\"'])")
PLAIN = set(string.ascii_letters + string.digits + " \t_./:=,@%+-")
# The hook sees unexpanded text: the variable is the caller's own handle unless the command rebinds it
# anywhere, nested shells and quotes included. A guardrail against mistakes, not a sandbox.
REBINDS = re.compile(r"(?<![$\w{])ORCA_TERMINAL_HANDLE=(?!=)|\$\{ORCA_TERMINAL_HANDLE:?=|"
                     r"\b(?:read|printf\s+-v)\b[^;&|\n]*\bORCA_TERMINAL_HANDLE")
ORCA_ASK = {"reset", "worker-abandon", "gate-resolve"}
ORCH_ASK = {"heavy", "set", "init"}


def decide(decision, reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                             "permissionDecisionReason": reason}}))


def simple_words(cmd):
    """The words of one simple command, or None when bash could split or expand the text into anything else.
    Outside quotes only plain characters pass: no operators, and no brace, glob, tilde or history expansion."""
    text, quote = OWN_REF.sub("H", cmd), None
    for c in text:
        if c < " " and c != "\t":  # newline, carriage return and other controls
            return None
        if quote:  # '...' is literal; in "..." only $ ` \ are special
            if c == quote:
                quote = None
            elif quote == '"' and c in "$`\\":
                return None
        elif c in "'\"":
            quote = c
        elif c not in PLAIN:
            return None
    return None if quote else shlex.split(cmd) or None


def own_skills():
    names = set()
    for d in ("skills", "legacy"):
        names |= {p.parent.name for p in (ROOT / d).glob("*/SKILL.md")}
    return names


def bash(cmd):
    own = os.environ.get("ORCA_TERMINAL_HANDLE")
    for handle in MAILBOX.findall(cmd):
        handle = handle.strip("'\"")
        if handle != own and (REBINDS.search(cmd) or handle not in OWN_VAR):
            return decide("deny", f"check/inbox --terminal {handle} reads another terminal's Orca mailbox and can "
                                  f"consume its messages. Your own handle is {own or 'unknown'}.")
    words = simple_words(cmd)
    if not words or REBINDS.search(cmd):
        return
    # The verb must come first: a leading flag could put an excluded verb out of sight.
    if words[:2] == ["orca", "orchestration"] and len(words) > 2 and words[2] not in ORCA_ASK and words[2][:1].isalpha():
        return decide("allow", f"{PLUGIN}: orca orchestration {words[2]}")
    if words[0] == "orch" and len(words) > 1 and words[1] not in ORCH_ASK and words[1][:1].isalpha():
        return decide("allow", f"{PLUGIN}: orch {words[1]} (the program ledger and landing gate)")


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    tool, args = event.get("tool_name"), event.get("tool_input") or {}
    if tool == "Bash":
        bash(args.get("command") or "")
    elif tool == "Skill":
        skill = str(args.get("skill") or "")
        name = skill.removeprefix(f"{PLUGIN}:")
        # A bare name may resolve to a user or project skill of the same name instead of this plugin's.
        shadowed = name == skill and any((pathlib.Path(d) / ".claude/skills" / name).exists()
                                         for d in (pathlib.Path.home(), event.get("cwd") or "."))
        if name in own_skills() and not shadowed:
            decide("allow", f"{PLUGIN} skill {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
