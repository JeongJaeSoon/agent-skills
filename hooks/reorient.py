#!/usr/bin/env python3
"""SessionStart (startup, resume, compact) and UserPromptSubmit hook of the agent-skills plugin: tells a coordinator
what it coordinates.

After an automatic compaction a coordinator kept draining its inbox but dropped a step it had run for hours
(removing each landed card), and eight cards stayed open. A top-level coordinator with no program never loaded
orchestrate in seventeen days and answered progress questions by investigating on its own.

Only this terminal decides: it is the current Run's `coordinator_handle`, or it is listed in
~/.claude/agent-skills.json → orchestrate.top_level_handles. Every SessionStart judges again; a prompt is judged only
when $AGENT_SKILLS_STATE/reorient/<session_id>.json does not exist yet, so a session pays the orca call once.
"""
import json, os, pathlib, re, subprocess, sys

HOME = pathlib.Path(os.environ.get("PROGRAMS_HOME", "~/.claude/programs")).expanduser()
STATE = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser() / "reorient"
CONFIG = pathlib.Path("~/.claude/agent-skills.json").expanduser()
SESSION = re.compile(r"[\w-]{1,128}")


def program_for(run_id):
    for path in HOME.glob("*/program.json"):
        try:
            cfg = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if cfg.get("run") == run_id:
            return cfg
    return None


def current_run():
    try:
        out = subprocess.run(["orca", "orchestration", "run-current", "--json"],
                             capture_output=True, text=True, timeout=5).stdout
        return ((json.loads(out) or {}).get("result") or {}).get("run") or {}
    except (OSError, ValueError, subprocess.TimeoutExpired, AttributeError):
        return {}


def top_level_handles():
    try:
        return (json.loads(CONFIG.read_text()).get("orchestrate") or {}).get("top_level_handles") or []
    except (OSError, ValueError, AttributeError):
        return []


def message(me, compacted):
    run = current_run()
    if run.get("coordinator_handle") == me and (cfg := program_for(run.get("id"))):
        slug = cfg.get("slug") or "<slug>"
        note = f", read the program note {cfg['note']}" if cfg.get("note") else ""
        lead = "Your context was just compacted, and you coordinate" if compacted else "You coordinate"
        rules = " Rules you followed before the compaction may be gone from your context." if compacted else ""
        return (f"{lead} the program {slug} (Orca Run {run['id']}).{rules} Before anything else: "
                f"load the agent-skills:orchestrate skill{note}, run `orch status {slug}` and act on every line it prints "
                f"(each LANDED-BUT-OPEN line is a card to close out). Drain only with `orch wait {slug}`: it prints what "
                f"to do with each worker_done's card.")
    if run.get("coordinator_handle") == me or me in top_level_handles():
        of_run = f" of Orca Run {run['id']}" if run.get("id") else ""
        return (f"You are the top-level coordinator{of_run}. Load agent-skills:orchestrate now. Your turn is routing "
                "and seconds-long reads, so the human's next message is answered in seconds; investigation, "
                "implementation, verification and browser work go one layer down. Status reports go through "
                "agent-skills:brief.")
    return None


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    me = os.environ.get("ORCA_TERMINAL_HANDLE")
    kind, session = event.get("hook_event_name"), str(event.get("session_id") or "")
    if not me:
        return 0
    if kind == "SessionStart":
        if event.get("source") not in ("startup", "resume", "compact"):
            return 0
    elif kind == "UserPromptSubmit":
        if not SESSION.fullmatch(session) or (STATE / f"{session}.json").exists():
            return 0
    else:
        return 0
    text = message(me, event.get("source") == "compact")
    if SESSION.fullmatch(session):
        try:
            STATE.mkdir(parents=True, exist_ok=True)
            (STATE / f"{session}.json").write_text(json.dumps({"handle": me, "coordinator": text is not None}))
        except OSError:
            pass
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": kind, "additionalContext": text}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
