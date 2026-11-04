#!/usr/bin/env python3
"""Stop and SubagentStop hook of the agent-skills plugin: warns when a final reply names something only the owner can
do or decide and the turn recorded none of it.

An owner item that lives only in a report sentence is lost at the next compaction: the board, the decision register
and the dashboard inbox are where the owner finds it. This hook counts the owner items in the final reply and looks
for a call in the same turn that stores one. A turn is everything after the last prompt a person typed.

An owner item is a line of the reply (outside code, quotes and table rows) with 🙋, "오너 할 일", or a phrase that asks
the owner (`오너에게 묻`, `오너 결정 필요`, `ask the owner`, `owner approval needed`). A line that says there are none
(`— 없음`) and the status legend (a line with 🔄 and 🙋) are not items. A table row is not one: its status is derived
from records.

Recorded means a Bash call this turn that was not refused and ran one of `orch decide add`, `orch board now`,
`orch board set … --owner`, `orch gate`, `orca orchestration gate-create` or `orch-dash inbox add`. One such call
covers the whole reply: the hook counts items, it does not match them to records.

Level `guard.owner_check` in ~/.claude/agent-skills.json:
  - unset or `warn` (default): a `systemMessage` line "기록 안 된 오너 항목 N" for the human and a row in
    `$AGENT_SKILLS_STATE/owner-check.jsonl`, so the false-positive rate can be read before anything stronger. The model
    sees nothing, because a systemMessage does not reach it.
  - `deny`: blocks the stop once with the same count, as claim_check.py does, so the model records the items or
    drops the claim. `stop_hook_active` passes. Only this key turns it on.
  - `off`, or AGENT_SKILLS_OWNER_CHECK=0: nothing.
Any error exits 0 silently (and logs to guard-errors.jsonl).
"""
import datetime, json, os, re, sys

import claim_check
from lib import config, shell

KEY = "guard.owner_check"
STATE_FILE = "owner-check.jsonl"

OWNER = re.compile(
    r"🙋|오너\s*할\s*일"
    r"|오너(?:에게|께|한테)\s*(?:\S{0,6}\s*)?(?:묻|물어|여쭤|확인|질문|요청|문의)"
    r"|오너\s*(?:결정|승인|확인|답변|판단)\s*(?:이\s*|가\s*)?(?:필요|대기|요청)"
    r"|\bask(?:ing)?\s+the\s+owner\b|\bowner\s+(?:decision|approval|action|input)\s+(?:is\s+)?(?:needed|required)\b",
    re.I)
NONE = re.compile(r"(?:[—–:：-]\s*|할\s*일\s*)(?:없음|없습니다|none|n/a)\s*[.。]?\s*$", re.I)
STATUS_LEGEND = re.compile(r"🔄.*⏳|⏳.*🔄")
UNWRAP = {"orch.py": "orch", "prog.py": "orch", "dash.py": "orch-dash", "orch_dash.py": "orch-dash"}


def owner_items(text):
    """The lines of `text` that name an owner item."""
    out = []
    for line in claim_check.strip(text).splitlines():
        s = line.strip()
        if not s or s.startswith("|") or not OWNER.search(s):
            continue
        if NONE.search(s) or ("🙋" in s and STATUS_LEGEND.search(s)):
            continue
        out.append(s)
    return out


def records(words):
    """True for a command that stores an owner item: a decision, a board owner line, a gate or an inbox item."""
    if not words or any(w in ("--help", "-h", "--dry-run") for w in words[1:]):
        return False
    head, args = os.path.basename(words[0]), words[1:]
    if head.startswith("python"):
        while args and args[0].startswith("-"):
            args = args[1:]
        if args:
            head, args = os.path.basename(args[0]), args[1:]
    head = UNWRAP.get(head, head)
    if head == "orch":
        return (args[:2] in (["decide", "add"], ["board", "now"]) or args[:1] == ["gate"]
                or (args[:2] == ["board", "set"] and any(a == "--owner" or a.startswith("--owner=") for a in args)))
    if head == "orch-dash":
        return args[:2] == ["inbox", "add"]
    return head == "orca" and args[:2] == ["orchestration", "gate-create"]


def judge(entries, last_message=None):
    """(items, recorded): the owner items in the final reply and whether this turn stored one."""
    skip = claim_check.refused(entries)
    recorded, final = False, []
    for entry in entries:
        if entry.get("type") == "user":
            final = []  # the final reply is the text after the last tool result
        if entry.get("type") != "assistant":
            continue
        for b in claim_check.blocks(entry):
            if b.get("type") == "text":
                final.append(b.get("text") or "")
            elif b.get("type") == "tool_use" and b.get("name") == "Bash" and b.get("id") not in skip:
                raw = str((b.get("input") or {}).get("command") or "")
                recorded = recorded or any(records(c.words) for c in shell.commands(raw))
    text = last_message if isinstance(last_message, str) else "\n".join(final)
    return owner_items(text), recorded


def append(row):
    config.state().mkdir(parents=True, exist_ok=True)
    with open(config.state() / STATE_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main():
    if os.environ.get("AGENT_SKILLS_OWNER_CHECK", "1") == "0":
        return
    lvl = config.level(config.load(), KEY, "warn")
    if lvl == "off":
        return
    event = json.load(sys.stdin)
    if event.get("stop_hook_active"):
        return
    path = event.get("agent_transcript_path") or event.get("transcript_path")
    if not path or not os.path.isfile(path):
        return
    items, recorded = judge(claim_check.turn(path), event.get("last_assistant_message"))
    if not items or recorded:
        return
    msg = f"기록 안 된 오너 항목 {len(items)}"
    if lvl == "deny":
        print(json.dumps({"decision": "block", "reason": (
            f"owner_check: {msg}. The reply names {len(items)} thing(s) only the owner can do or decide, and this turn "
            f"ran no `orch decide add`, `orch board now`, `orch board set --owner`, `orch gate` or `orch-dash inbox "
            f"add`. Record each item where the owner reads it, then say so; or reword a line that is not an owner "
            f"item. ({KEY}: deny; AGENT_SKILLS_OWNER_CHECK=0 turns this check off.)")}, ensure_ascii=False))
        return
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    append({"at": now, "event": event.get("hook_event_name"), "session": event.get("session_id"),
            "agent": event.get("agent_id") or "", "items": len(items), "level": lvl})
    print(json.dumps({"systemMessage": f"owner_check: {msg}"}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        config.log_error("owner_check", exc)
    sys.exit(0)
