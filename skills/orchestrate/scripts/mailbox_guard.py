#!/usr/bin/env python3
"""PreToolUse hook: an agent reads only its own Orca mailbox.

`orca orchestration check` is allowed without a prompt so workers never stall on it, but
`check`/`inbox --terminal <handle>` would read, and with --ack consume, another agent's
messages. Static permission rules match prefixes only, so this hook refuses that one form.
"""
import json, re, sys

OTHER_MAILBOX = re.compile(r"\borca\s+orchestration\s+(check|inbox)\b[^;&|\n]*\s--terminal\b")


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if event.get("tool_name") != "Bash":
        return 0
    if OTHER_MAILBOX.search((event.get("tool_input") or {}).get("command") or ""):
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": "Reading another terminal's Orca mailbox (check/inbox --terminal) "
                                        "can consume its messages. Check your own: drop --terminal."}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
