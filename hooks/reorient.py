#!/usr/bin/env python3
"""SessionStart (startup, resume, compact) and UserPromptSubmit hook of the agent-skills plugin: tells a coordinator
what it coordinates.

After an automatic compaction a coordinator kept draining its inbox but dropped a step it had run for hours
(removing each landed card), and eight cards stayed open. A top-level coordinator with no program never loaded
orchestrate in seventeen days and answered progress questions by investigating on its own.

A role is declared, not guessed: a prompt line `ROLE: orchestrator`, `ROLE: lead <slug>` or `ROLE: none` is recorded in
$AGENT_SKILLS_STATE/roles/<session_id>.json. The session id survives a resume in another terminal, where the
terminal handle did not and the old guess turned false. A declared session gets the long paragraph at SessionStart and
one line on every prompt, because a single injection got buried under the next request.

Undeclared sessions fall back to the terminal: the current Run's `coordinator_handle`, or a handle listed in
~/.claude/agent-skills.json → orchestrate.top_level_handles. Every SessionStart judges again; a prompt is judged only
when $AGENT_SKILLS_STATE/reorient/<session_id>.json does not exist yet, so a session pays the orca call once.
"""
import json, os, pathlib, re, subprocess, sys

HOME = pathlib.Path(os.environ.get("PROGRAMS_HOME", "~/.claude/programs")).expanduser()
BASE = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser()
STATE, ROLES = BASE / "reorient", BASE / "roles"
CONFIG = pathlib.Path("~/.claude/agent-skills.json").expanduser()
SESSION = re.compile(r"[\w-]{1,128}")
ROLE = re.compile(r"^[ \t]*ROLE:?[ \t]+(orchestrator|lead[ \t]+([\w.-]+)|none)[ \t]*$", re.M)


def program_for(key, value):
    for path in HOME.glob("*/program.json"):
        try:
            cfg = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if cfg.get(key) == value:
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


def program_message(slug, cfg, run_id, compacted):
    note = f", read the program note {cfg['note']}" if cfg.get("note") else ""
    lead = "Your context was just compacted, and you coordinate" if compacted else "You coordinate"
    rules = " Rules you followed before the compaction may be gone from your context." if compacted else ""
    of_run = f" (Orca Run {run_id})" if run_id else ""
    return (f"{lead} the program {slug}{of_run}.{rules} Before anything else: "
            f"load the agent-skills:orchestrate skill{note}, run `orch status {slug}` and act on every line it prints "
            f"(each LANDED-BUT-OPEN line is a card to close out). Drain only with `orch wait {slug}`: it prints what "
            f"to do with each worker_done's card.")


def top_message(run_id):
    of_run = f" of Orca Run {run_id}" if run_id else ""
    return (f"You are the top-level coordinator{of_run}. Load agent-skills:orchestrate now. Your turn is routing "
            "and seconds-long reads, so the human's next message is answered in seconds; investigation, "
            "implementation, verification and browser work go one layer down. Status reports go through "
            "agent-skills:brief.")


def message(me, compacted):
    run = current_run()
    if run.get("coordinator_handle") == me and (cfg := program_for("run", run.get("id"))):
        return program_message(cfg.get("slug") or "<slug>", cfg, run["id"], compacted)
    if run.get("coordinator_handle") == me or me in top_level_handles():
        return top_message(run.get("id"))
    return None


def declared(session):
    try:
        return json.loads((ROLES / f"{session}.json").read_text())
    except (OSError, ValueError):
        return None


def declare(session, prompt):
    """Records the last ROLE line of the prompt and returns the session's entry; None when it never declared."""
    found = ROLE.findall(prompt or "")
    if not found:
        return declared(session)
    role, slug = found[-1]
    # "none" is kept, not deleted: without it the terminal fallback would remind the session again.
    entry = {"role": "lead", "slug": slug} if slug else {"role": role}
    ROLES.mkdir(parents=True, exist_ok=True)
    (ROLES / f"{session}.json").write_text(json.dumps(entry))
    return entry


def role_long(entry, compacted):
    if entry.get("role") == "lead":
        slug = entry.get("slug")
        cfg = program_for("slug", slug) or {}
        return f"You are declared the lead of program {slug} (ROLE: lead {slug}). " + \
            program_message(slug, cfg, cfg.get("run"), compacted)
    return "You are declared the top-level orchestrator (ROLE: orchestrator). " + top_message(None)


def role_line(entry):
    if entry.get("role") == "lead":
        slug = entry.get("slug")
        note = (program_for("slug", slug) or {}).get("note")
        brief = f"; program note {note}" if note else ""
        return (f"ROLE: lead {slug}. Run agent-skills:orchestrate in program mode for {slug} (load it now if it is not "
                f"in your context){brief}; keep one `orch wait {slug}` running; delegate investigation, implementation "
                f"and verification one layer down.")
    return ("ROLE: orchestrator. Run agent-skills:orchestrate as the top-level orchestrator (load it now if it is not "
            "in your context): route and do seconds-long reads; investigation, implementation, verification and "
            "browser work go one layer down; status reports go through agent-skills:brief.")


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    me = os.environ.get("ORCA_TERMINAL_HANDLE")
    kind, session = event.get("hook_event_name"), str(event.get("session_id") or "")
    if kind in ("SessionStart", "UserPromptSubmit") and SESSION.fullmatch(session):
        text = None
        if kind == "UserPromptSubmit":
            had = declared(session)
            try:
                entry = declare(session, event.get("prompt"))
            except OSError:
                entry = had
            if entry and entry.get("role") == "none":
                if had and had != entry:
                    text = "ROLE cleared: this session no longer gets the orchestrate reminder."
            elif entry and entry != had:
                text = role_long(entry, False) + " `ROLE: none` in a prompt clears the role."
            elif entry:
                text = role_line(entry)
        else:
            entry = declared(session) if event.get("source") in ("startup", "resume", "compact") else None
            if entry and entry.get("role") != "none":
                text = role_long(entry, event.get("source") == "compact")
        if text:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": kind, "additionalContext": text}}))
        if entry:
            return 0
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
