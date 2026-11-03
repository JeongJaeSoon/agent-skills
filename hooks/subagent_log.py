#!/usr/bin/env python3
"""SubagentStart and SubagentStop hook of the agent-skills plugin: records each subagent start and stop it observes.

One line per event goes to `subagents.jsonl` in the dashboard state directory (fleet.subagent_log), the only record
of subagents: the brief and the Sessions page show the started ones under their parent session from it. The line is
keyed by the hook's agent_id and $ORCA_TERMINAL_HANDLE, which Orca sets in every terminal it runs; outside Orca
nothing is written.

Hooks of one event run in parallel, so this one cannot see whether claim_check.py blocked the same stop. It asks
claim_check's own judge instead and writes no stop for a stop claim_check blocks: the subagent goes on working.

It prints nothing, so the subagent runs exactly as it would without it. Any error exits 0 silently.
"""
import json, os, pathlib, re, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "skills" / "orchestrate" / "scripts"))
sys.path.insert(0, str(HERE))


def stop_passes(event):
    """False when claim_check.py would block this stop: the same guards as its main(), then its judge."""
    import claim_check
    if os.environ.get("AGENT_SKILLS_CLAIM_CHECK", "1") == "0" or event.get("stop_hook_active"):
        return True
    path = event.get("agent_transcript_path") or event.get("transcript_path")
    if not isinstance(path, str) or not os.path.isfile(path):
        return True
    return claim_check.judge(claim_check.turn(path), event.get("last_assistant_message")) is None


def main():
    handle = os.environ.get("ORCA_TERMINAL_HANDLE", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", handle):
        return
    event = json.load(sys.stdin)
    if not isinstance(event, dict):
        return
    if event.get("hook_event_name") == "SubagentStop" and not stop_passes(event):
        return
    import fleet
    fleet.subagent_log(event, handle)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
