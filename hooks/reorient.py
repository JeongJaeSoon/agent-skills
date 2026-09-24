#!/usr/bin/env python3
"""SessionStart hook (source: compact) of the agent-skills plugin: puts a coordinator's program back in view.

After an automatic compaction a coordinator kept draining its inbox but dropped a step it had run for hours
(removing each landed card), and eight cards stayed open. Only this terminal's own Run decides: a session that
coordinates no program registered with `orch init` gets nothing.
"""
import json, os, pathlib, subprocess, sys

HOME = pathlib.Path(os.environ.get("PROGRAMS_HOME", "~/.claude/programs")).expanduser()


def program_for(run_id):
    for path in HOME.glob("*/program.json"):
        try:
            cfg = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if cfg.get("run") == run_id:
            return cfg
    return None


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    me = os.environ.get("ORCA_TERMINAL_HANDLE")
    if event.get("source") != "compact" or not me or not any(HOME.glob("*/program.json")):
        return 0
    try:
        out = subprocess.run(["orca", "orchestration", "run-current", "--json"],
                             capture_output=True, text=True, timeout=5).stdout
        run = ((json.loads(out) or {}).get("result") or {}).get("run") or {}
    except (OSError, ValueError, subprocess.TimeoutExpired, AttributeError):
        return 0
    cfg = program_for(run.get("id")) if run.get("coordinator_handle") == me else None
    if not cfg:
        return 0
    slug = cfg.get("slug") or "<slug>"
    note = f", read the program note {cfg['note']}" if cfg.get("note") else ""
    text = (f"Your context was just compacted, and you coordinate the program {slug} (Orca Run {run['id']}). "
            f"Rules you followed before the compaction may be gone from your context. Before anything else: "
            f"load the agent-skills:orchestrate skill{note}, run `orch status {slug}` and act on every line it prints "
            f"(each LANDED-BUT-OPEN line is a card to close out). Drain only with `orch wait {slug}`: it prints what "
            f"to do with each worker_done's card.")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
