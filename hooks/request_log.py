#!/usr/bin/env python3
"""UserPromptSubmit hook of the agent-skills plugin: logs each human request an orchestrator or lead receives.

In a long coordinator session the owner's small requests (check this, merge that, reply there) got lost when several
arrived at once. Each human prompt of a session declared orchestrator or lead becomes an open item in the request
ledger (skills/orchestrate/scripts/owner_requests.py) and the hook tells the coordinator its id and what is still open,
so it can `orch req link|done|drop` it. Other sessions log nothing.

The role is read as reorient.py reads it (agent_type, the prompt's own ROLE line, the role record). reorient.py runs
on the same prompt at the same time and may be writing the record, so the prompt's ROLE line is read here directly.

Not a human request, so not logged: harness rows starting with `<` (task notifications, command output; a
`<pasted_content` block is the human's), a slash command as typed (`/brief`), an answer to a dashboard decision
(`d3: yes`, which decision.py records), a card's relayed report (`[card-name] ...`; `[Image #1]` and
`[Pasted text #1]` are the human's), the compaction preamble, a prompt that only declares a ROLE, and an empty
prompt. Orca's "You have N orchestration message(s). Run ..." nudge is cut out wherever Orca typed it, even mid-sentence,
and what is left is judged and logged. Any error exits 0, so a prompt is never blocked.
"""
import json, os, pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "skills" / "orchestrate" / "scripts"))
import decision, reorient  # noqa: E402  beside this file

RELAY = re.compile(r"\[(?!Image #|Pasted text)[^\]\s]{1,80}\]")
NOT_HUMAN = ("This session is being continued from a previous conversation",)
NUDGE = re.compile(r"You have \d+ orchestration messages?(?:\.?\s*Run `[^`\n]*`\.?|[^\n.]*\.?)", re.I)
LISTED = 5


def human(prompt):
    head = prompt.lstrip()
    if not head:
        return False
    if head.startswith("/") or head.startswith("<") and not head.startswith("<pasted_content"):
        return False
    if RELAY.match(head) or head.startswith(NOT_HUMAN):
        return False
    return bool(reorient.ROLE.sub("", head).strip())


def role_of(event):
    session = str(event.get("session_id") or "")
    if not reorient.SESSION.fullmatch(session):
        return None
    found = reorient.ROLE.findall(event.get("prompt") or "")
    record = ({"role": "lead", "slug": found[-1][1]} if found[-1][1] else {"role": found[-1][0]}) if found \
        else reorient.declared(session)
    return reorient.resolve(event, record)


def answers_decision(prompt, fleet):
    # Any known decision, not only open ones: decision.py may already have closed it on this same prompt.
    m = decision.ID_RE.fullmatch(next((l.strip() for l in prompt.splitlines() if l.strip()), ""))
    return bool(m) and m.group(1).lower() in (fleet.read_json(fleet.state_dir() / "decisions.json", {}) or {}).get("decisions", {})


def main():
    event = json.load(sys.stdin)
    prompt = NUDGE.sub("", event.get("prompt") if isinstance(event.get("prompt"), str) else "")
    if not human(prompt) or not role_of(event):
        return
    import owner_requests as req
    if answers_decision(prompt, req.fleet):
        return
    row = req.add(reorient.ROLE.sub("", prompt), str(event.get("session_id")), os.environ.get("ORCA_TERMINAL_HANDLE"))
    s = req.summary()
    # Ids only: the routing commands are in SKILL.md and `orch req list` has the text, so each prompt pays ~70 bytes.
    text = f"Owner request {row['id']} logged" + (" (same as before)" if row.get("ev") != "open" else "")
    if s["open"] > 1:
        text += f" · {s['open']} open, oldest {req.ago(s['oldest_min'])}"
    if stale := [q["id"] for q in s["unlinked"] if q["id"] != row["id"]]:
        text += f" · unlinked 30m+: {len(stale)} (newest: {' '.join(stale[::-1][:LISTED])})"
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}},
                     ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
