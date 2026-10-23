#!/usr/bin/env python3
"""SessionStart (startup, resume, compact, fork) and UserPromptSubmit hook of the agent-skills plugin: tells an
orchestrator or a lead what it runs.

After an automatic compaction a coordinator kept draining its inbox but dropped a step it had run for hours
(removing each landed card), and eight cards stayed open. A top-level coordinator never loaded orchestrate for days
and answered progress questions by investigating on its own: it was judged by its terminal handle, which went stale
when the session resumed in another terminal, and the one injection it did get was buried under the next request.

A role is declared, never guessed from the terminal, and read in this order:
1. `agent_type` in the hook input: the session was started with `claude --agent agent-skills:orchestrator` or
   `--agent agent-skills:lead --name lead:<slug>`. Claude Code keeps it across resume, compact and fork, but leaves
   it out of SessionStart on resume and fork; there the session title (`--name`) stands in.
2. A prompt line `ROLE: orchestrator` or `ROLE: lead <slug>`, recorded in $AGENT_SKILLS_STATE/roles/<session_id>.json,
   for a session already running or a card whose launcher cannot pass `--agent`. `ROLE: none` silences the session,
   whatever started it. A fork gets a new session id and its input names no parent, so a fork of a session declared
   only this way is undeclared until it declares again.
3. Neither: no output.
A declared session gets the long paragraph at SessionStart and one line on every prompt. An orchestrator whose session
title is not `orchestrator` is also told at SessionStart how to name it; a role visible in session lists is how the
human tells which session routes. Prompts run no external
command, so the hook stays fast.
"""
import json, os, pathlib, re, sys

HOME = pathlib.Path(os.environ.get("PROGRAMS_HOME", "~/.claude/programs")).expanduser()
ROLES = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser() / "roles"
SESSION = re.compile(r"[\w-]{1,128}")
ROLE = re.compile(r"^[ \t]*ROLE:?[ \t]+(orchestrator|lead[ \t]+([\w.-]+)|none)[ \t]*$", re.M)
LEAD_TITLE = re.compile(r"lead:([\w.-]+)")
AGENTS = {"agent-skills:orchestrator": "orchestrator", "agent-skills:lead": "lead"}


def program_for(slug):
    for path in HOME.glob("*/program.json"):
        try:
            cfg = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if cfg.get("slug") == slug:
            return cfg
    return {}


def program_message(slug, cfg, compacted):
    note = f", read the program note {cfg['note']}" if cfg.get("note") else ""
    lead = "Your context was just compacted, and you coordinate" if compacted else "You coordinate"
    rules = " Rules you followed before the compaction may be gone from your context." if compacted else ""
    of_run = f" (Orca Run {cfg['run']})" if cfg.get("run") else ""
    return (f"{lead} the program {slug}{of_run}.{rules} Before anything else: "
            f"load the agent-skills:orchestrate skill{note}, run `orch status {slug}` and act on every line it prints "
            f"(each LANDED-BUT-OPEN line is a card to close out). Drain only with `orch wait {slug}`: it prints what "
            f"to do with each worker_done's card.")


def role_long(entry, compacted):
    if entry["role"] == "lead":
        slug = entry.get("slug")
        if not slug:
            return ("You are a lead (agent-skills:lead), but no program is named: ask the human for its slug, then "
                    "declare it with a prompt line `ROLE: lead <slug>`. Load agent-skills:orchestrate in program mode.")
        return program_message(slug, program_for(slug), compacted)
    return ("You are the top-level orchestrator. Load agent-skills:orchestrate now. Your turn is routing "
            "and seconds-long reads, so the human's next message is answered in seconds; investigation, "
            "implementation, verification and browser work go one layer down. Status reports go through "
            "agent-skills:brief.")


def name_check(event):
    """At SessionStart: the orchestrator's name, for the prompt bar and the Orca tab, when it is not set yet."""
    if event.get("session_title") == "orchestrator":
        return ""
    tab = (" and name this Orca tab: `orca terminal rename --terminal $ORCA_TERMINAL_HANDLE --title orchestrator`"
           if os.environ.get("ORCA_TERMINAL_HANDLE") else "")
    return (" This session is not named `orchestrator`: ask the human to run `/rename orchestrator`, so the role shows "
            f"on the prompt bar and in session lists{tab}.")


def role_line(entry):
    if entry["role"] == "lead" and entry.get("slug"):
        slug = entry["slug"]
        note = program_for(slug).get("note")
        brief = f"; program note {note}" if note else ""
        return (f"ROLE: lead {slug}. Run agent-skills:orchestrate in program mode for {slug} (load it now if it is not "
                f"in your context){brief}; keep one `orch wait {slug}` running; delegate investigation, implementation "
                f"and verification one layer down.")
    if entry["role"] == "lead":
        return role_long(entry, False)
    return ("ROLE: orchestrator. Run agent-skills:orchestrate as the top-level orchestrator (load it now if it is not "
            "in your context): route and do seconds-long reads; investigation, implementation, verification and "
            "browser work go one layer down; status reports go through agent-skills:brief.")


def declared(session):
    try:
        return json.loads((ROLES / f"{session}.json").read_text())
    except (OSError, ValueError):
        return None


def declare(session, prompt):
    """Records the prompt's last ROLE line and returns the session's record; None when it never declared."""
    found = ROLE.findall(prompt or "")
    if not found:
        return declared(session)
    role, slug = found[-1]
    # "none" is kept, not deleted: it also silences a session started with --agent.
    entry = {"role": "lead", "slug": slug} if slug else {"role": role}
    ROLES.mkdir(parents=True, exist_ok=True)
    (ROLES / f"{session}.json").write_text(json.dumps(entry))
    return entry


def resolve(event, record):
    """The role in force: `agent_type`, else the ROLE record, else (SessionStart only) the session title."""
    if record and record.get("role") == "none":
        return None
    title = str(event.get("session_title") or "")
    lead_title = LEAD_TITLE.fullmatch(title)
    role = AGENTS.get(event.get("agent_type"))
    if role == "lead":
        slug = lead_title.group(1) if lead_title else (record or {}).get("slug")
        return {"role": "lead", "slug": slug}
    if role:
        return {"role": role}
    if record:
        return record
    if event.get("hook_event_name") == "SessionStart":
        if lead_title:
            return {"role": "lead", "slug": lead_title.group(1)}
        if title == "orchestrator":
            return {"role": "orchestrator"}
    return None


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    kind, session = event.get("hook_event_name"), str(event.get("session_id") or "")
    if not SESSION.fullmatch(session):
        return 0
    text = None
    if kind == "UserPromptSubmit":
        had = declared(session)
        try:
            record = declare(session, event.get("prompt"))
        except OSError:
            record = had
        entry = resolve(event, record)
        if record and record.get("role") == "none":
            if had != record and resolve(event, had):
                text = "ROLE cleared: this session no longer gets the orchestrate reminder."
        elif entry and record and record != had:
            text = role_long(entry, False) + " `ROLE: none` in a prompt clears the role."
        elif entry:
            text = role_line(entry)
    elif kind == "SessionStart" and event.get("source") in ("startup", "resume", "compact", "fork"):
        if entry := resolve(event, declared(session)):
            text = role_long(entry, event.get("source") == "compact")
            if entry["role"] == "orchestrator":
                text += name_check(event)
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": kind, "additionalContext": text}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
