#!/usr/bin/env python3
"""PreToolUse hook: an agent reads only its own Orca mailbox.

`orca orchestration check` is allowed without a prompt so workers never stall on it. Orca's worker
contract has each worker read its follow-ups with `check --terminal <its own handle>`, but the same
form with another terminal's handle would read, and with --ack consume, that agent's messages.
Static permission rules match prefixes only, so this hook refuses a handle that is not the caller's
own ($ORCA_TERMINAL_HANDLE, set in every Orca terminal).
"""
import json, os, re, sys

MAILBOX = re.compile(r"\borca\s+orchestration\s+(?:check|inbox)\b[^;&|\n]*?\s--terminal(?:\s+|=)(\S+)")


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if event.get("tool_name") != "Bash":
        return 0
    own = os.environ.get("ORCA_TERMINAL_HANDLE")
    for handle in MAILBOX.findall((event.get("tool_input") or {}).get("command") or ""):
        if handle.strip("'\"") != own:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": f"check/inbox --terminal {handle} reads another terminal's Orca mailbox "
                                            f"and can consume its messages. Your own handle is {own or 'unknown'}."}}))
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
