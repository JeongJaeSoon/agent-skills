#!/usr/bin/env python3
"""PermissionRequest hook of the agent-skills plugin: records the prompt for the orchestrator dashboard.

The dashboard shows what a session is waiting on and, when the human clicks Approve or Deny there, answers the
dialog in this terminal (fleet.answer_prompt). The record is keyed by $ORCA_TERMINAL_HANDLE, which Orca sets in
every terminal it runs; outside Orca nothing is written.

It prints no decision, so the permission flow is exactly what it would be without it. Any error exits 0.
"""
import json, os, pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "skills" / "orchestrate" / "scripts"))


def main():
    handle = os.environ.get("ORCA_TERMINAL_HANDLE", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", handle):
        return
    event = json.load(sys.stdin)
    import fleet
    raw = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    tool_input = {k: fleet.mask(v, 2000) for k, v in raw.items() if isinstance(v, str)}
    at = fleet.iso(fleet.utcnow())
    tool = str(event.get("tool_name") or "")[:200]
    fleet.write_atomic(fleet.state_dir() / "prompts" / f"{handle}.json", json.dumps({
        "v": 1, "id": fleet.digest(at, handle, tool, json.dumps(tool_input, sort_keys=True)), "at": at,
        "handle": handle, "session_id": event.get("session_id"), "cwd": event.get("cwd"), "tool": tool,
        "input": tool_input}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
