#!/usr/bin/env python3
"""PermissionDenied, PreToolUse, Stop and SubagentStop hook of the agent-skills plugin: the refused-act ledger
(lib/refusals.py). An act the auto mode classifier refused with a category, or the human refused in the permission
dialog, is refused again for every session until the human runs `orch refusal clear <id>` as a `!` line.

Recording:
- PermissionDenied (auto mode refusals) records the call at once.
- The human's refusals never reach PermissionDenied. They, and a classifier refusal when that event did not come, are
  read from the transcript's tail on the next PreToolUse (any tool) and at Stop. Both paths key on the tool use id,
  so a refusal is written once.
Limit of the transcript path: calls sent in the same message as the refused one run before it is read, so they are
not blocked. A refusal whose text Claude Code words differently is not recorded.

Not recorded: a hook's block, a `<tool_use_error>`, a read-only tool, and a classifier refusal with no category (one
warning line instead: the parent may take such an edit back, orchestrate's refused-act rule). A refused call whose
target this ledger cannot read is not recorded either, with a warning.

Blocking: PreToolUse denies a call whose (verb, target) an open refusal holds, whichever session refused it.
Key `guard.refusal_registry` in ~/.claude/agent-skills.json: `deny` (default), `warn` (record, and only warn on a
match) or `off` (neither). A broken input or a check that raises lets the call through with a warning and a line in
guard-errors.jsonl.
"""
import json, os, sys

from lib import config, refusals

SOURCE = "(agent-skills hooks/refusals.py, guard.refusal_registry)"


def say(event_name, warnings, decision=None, reason=None):
    out = {}
    if warnings:
        out["systemMessage"] = "\n".join(dict.fromkeys(warnings))
    if event_name == "PreToolUse" and (decision or warnings):
        out["hookSpecificOutput"] = {"hookEventName": "PreToolUse"}
        if decision:
            out["hookSpecificOutput"].update(permissionDecision=decision, permissionDecisionReason=reason)
        if warnings:
            out["hookSpecificOutput"]["additionalContext"] = out["systemMessage"]
    if out:
        print(json.dumps(out, ensure_ascii=False))


def store(event_id, tool, args, cwd, session, k, warnings):
    """Records one refused call; a warning when it cannot be recorded."""
    if k == "uncategorized":
        if refusals.record(event_id, [], session):
            warnings.append(f"A {tool or 'tool'} call was refused by auto mode with no category (no verdict); the "
                            f"refused-act ledger does not record it. Report it as the refused-act rule says. {SOURCE}")
        return
    found = refusals.acts(tool, args, cwd)
    if not found and tool is not None and not refusals.changes(tool):
        refusals.record(event_id, [], session)
        return
    written = refusals.record(event_id, found, session, os.environ.get("ORCA_TERMINAL_HANDLE"),
                              refusals.run_id() if found else None)
    if written and not found:
        warnings.append(f"A refused {tool or 'tool'} call was not recorded in the refused-act ledger: its target "
                        f"could not be read. Do not do it again in any form; report it. {SOURCE}")


def backfill(event, warnings):
    path = event.get("agent_transcript_path") or event.get("transcript_path")
    if not isinstance(path, str) or not os.path.isfile(path):
        return
    done = refusals.seen()
    for event_id, tool, args, cwd, session, k, _ in refusals.denials(refusals.tail(path)):
        if event_id not in done:
            store(event_id, tool, args, cwd or event.get("cwd"), session or event.get("session_id"), k, warnings)


def permission_denied(event, warnings):
    event_id, tool, args = event.get("tool_use_id"), event.get("tool_name"), event.get("tool_input")
    if not isinstance(event_id, str) or not isinstance(tool, str) or not isinstance(args, dict):
        warnings.append("The PermissionDenied input had no tool_use_id, tool_name or tool_input, so the refusal was "
                        f"not recorded now; the next tool call reads it from the transcript. {SOURCE}")
        return
    k, _ = refusals.reason_kind(event.get("reason"))
    # A reason with no bracketed category may still be a categorized refusal worded differently: leave it to the
    # transcript, which has the full text.
    if k == "refused":
        store(event_id, tool, args, event.get("cwd"), event.get("session_id"), k, warnings)


def pre_tool_use(event, lvl, warnings):
    found = refusals.acts(event.get("tool_name"), event.get("tool_input"), event.get("cwd"))
    hits = refusals.blocked(found) if found else []
    if not hits:
        return None, None
    lines = [f"- {r.get('verb')} {r.get('target')}: refused at {r.get('at')} in session {r.get('session') or '?'}"
             + (f" (terminal {r['terminal']})" if r.get("terminal") else "") + f", id {r['id']}" for r in hits]
    reason = ("This call does again an act that was refused (by auto mode or by the human) and is in the refused-act "
              "ledger:\n" + "\n".join(lines) + "\nNo session does it again in any form. Report it to the human, who "
              "sees it with `orch refusal report`; only the human clears it, typing `!orch refusal clear <id>`. "
              + SOURCE)
    if lvl == "deny":
        return "deny", reason
    warnings.append(reason)
    return None, None


def main():
    warnings = []
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            raise ValueError("hook input is not an object")
    except ValueError as e:
        config.log_error("refusals", e)
        print(json.dumps({"systemMessage": "agent-skills hooks/refusals.py could not read its input and let this "
                                           "through unchecked."}))
        return
    name = event.get("hook_event_name")
    lvl = refusals.level(config.load())
    if lvl == "off":
        return
    decision = reason = None
    try:
        if name == "PermissionDenied":
            permission_denied(event, warnings)
        elif name in ("PreToolUse", "Stop", "SubagentStop"):
            backfill(event, warnings)
            if name == "PreToolUse":
                decision, reason = pre_tool_use(event, lvl, warnings)
    except Exception as e:
        config.log_error("refusals", e)
        warnings.append(f"agent-skills hooks/refusals.py could not finish ({type(e).__name__}) and let this through "
                        "unchecked; see guard-errors.jsonl in the agent-skills state directory.")
    say(name, warnings, decision, reason)


if __name__ == "__main__":
    main()
    sys.exit(0)
