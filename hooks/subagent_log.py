#!/usr/bin/env python3
"""SubagentStart and SubagentStop hook of the agent-skills plugin: records each subagent start and stop it observes.

One line per event goes to `subagents.jsonl` in the dashboard state directory (fleet.subagent_log), the only record
of subagents: the brief and the Sessions page show the started ones under their parent session from it. The line is
keyed by the hook's agent_id and $ORCA_TERMINAL_HANDLE, which Orca sets in every terminal it runs; outside Orca
nothing is written.

Hooks of one event run in parallel, so this one cannot see whether claim_check.py blocked the same stop. It asks
claim_check's own judge instead and writes no stop for a stop claim_check blocks: the subagent goes on working.

A subagent whose first prompt has an `ORDER: oN` line is also linked to that owner order (`agent:<id>`, orch order).

It prints nothing, so the subagent runs exactly as it would without it. Any error exits 0 silently, and so does a run
past BUDGET_S (a stdin left open, a slow disk), well inside the 5 s timeout hooks.json gives it.
"""
import json, os, pathlib, re, signal, sys

BUDGET_S = 3

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "skills" / "orchestrate" / "scripts"))
sys.path.insert(0, str(HERE))


def stop_passes(event):
    """False when claim_check.py or owner_check.py (at `deny`) would block this stop: the same guards as their main()
    functions, then their judges. Turning one check off does not turn the other off."""
    import claim_check
    if event.get("stop_hook_active"):
        return True
    path = event.get("agent_transcript_path") or event.get("transcript_path")
    if not isinstance(path, str) or not os.path.isfile(path):
        return True
    entries = claim_check.turn(path)
    claim_on = os.environ.get("AGENT_SKILLS_CLAIM_CHECK", "1") != "0"
    if claim_on and claim_check.judge(entries, event.get("last_assistant_message")) is not None:
        return False
    return not owner_blocks(entries, event)


def owner_blocks(entries, event):
    """True when owner_check.py would block this stop: only its `deny` level blocks."""
    import owner_check
    from lib import config
    if os.environ.get("AGENT_SKILLS_OWNER_CHECK", "1") == "0" or config.level(config.load(), owner_check.KEY, "warn") != "deny":
        return False
    items, recorded = owner_check.judge(entries, event.get("last_assistant_message"))
    return bool(items) and not recorded


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
    link_order(event, handle)


# A line of its own, so a prompt that only mentions an order id in prose links nothing.
ORDER_LINE = re.compile(r"(?im)^[ \t]*`?ORDER`?[ \t]*:[ \t]*(o\d+)[ \t]*$")


def link_order(event, handle):
    """A subagent whose first prompt has an `ORDER: oN` line carries that owner order: link `agent:<id>` to it. Start
    and stop both try, because the transcript may not exist yet at the start; the link is idempotent. A table that is
    not set up, a closed or unknown order, or a prompt that cannot be read links nothing, silently."""
    aid = event.get("agent_id")
    if not isinstance(aid, str):
        return
    import roles
    paths = [event.get("agent_transcript_path")]
    if isinstance(event.get("transcript_path"), str):
        paths.append(str(pathlib.Path(event["transcript_path"]).with_suffix("") / "subagents" / f"agent-{aid}.jsonl"))
    for path in paths:
        if m := ORDER_LINE.search(roles.first_prompt(path)):
            break
    else:
        return
    import store
    try:
        # An order that closed while the subagent ran still gets the link at its stop: the work started under it.
        store.order_get(m.group(1), require_open=event.get("hook_event_name") != "SubagentStop")
        store.order_link(m.group(1), f"agent:{aid}", handle)
    except (ValueError, store.NotMigrated):
        pass


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, lambda *_: os._exit(0))
    signal.alarm(BUDGET_S)
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
