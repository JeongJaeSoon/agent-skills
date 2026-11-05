"""deliver-ticket load check (key `guard.deliver_load`, default `warn`), called by guard.py for Edit, Write, MultiEdit
and NotebookEdit.

A card (roles.py `worker`, main conversation) that edits a file inside its working directory before it has loaded
`deliver-ticket` gets one warning line per such edit, until it loads the skill. No state is kept, so a repeat costs
one line and a session that never loads it is told each time. `deny` and `ask` block or ask on the same edits;
`false` turns the check off. Not targets: orchestrators, leads, human-opened sessions and subagents; a path outside the working
directory or under `.git`; a card whose first prompt has a `LOAD: none` row (a read-only card).

Loaded means the main conversation has a `deliver-ticket` Skill call whose result is not an error, or a
`/deliver-ticket` slash command. A call with no result yet, a transcript that cannot be read in time, or an
oversize record is unknown, and unknown never warns or blocks. The check only looks at the working directory the
event names, so a card whose cwd is a subdirectory misses edits in sibling directories.
"""
import json, os, pathlib, re, time

import roles
from lib import config
from lib.danger import Finding

KEY = "guard.deliver_load"
NAMES = ("deliver-ticket", "agent-skills:deliver-ticket")
SLASH = re.compile(r"<command-name>/?(?:agent-skills:)?deliver-ticket</command-name>")
# `LOAD: none` or the template's `LOAD  none`; not `none-other`, not a LOAD row that only mentions none.
EXEMPT = re.compile(r"(?m)^[ \t]*`?LOAD`?(?:[ \t]*:|[ \t]{2,})[ \t]*none(?![\w-])")
BUDGET_S = 1.5
MAX_LINE = 2 << 20
REASON = ("This card edits files but has not loaded deliver-ticket (its brief's LOAD row): load it with the Skill "
          "tool before the next edit. A read-only card says `LOAD: none`. (agent-skills hooks/guard.py, " + KEY + ")")


def inside(file_path, cwd):
    """The file is inside the working directory the event names, outside `.git`, symlinks and `..` resolved."""
    if not (isinstance(file_path, str) and file_path and isinstance(cwd, str) and cwd):
        return False
    root = os.path.realpath(cwd)
    p = pathlib.Path(file_path).expanduser()
    full = pathlib.Path(os.path.realpath(p if p.is_absolute() else os.path.join(root, p)))
    try:
        rel = full.relative_to(root)
    except ValueError:
        return False
    return ".git" not in rel.parts


def result_failed(block):
    content = block.get("content")
    if isinstance(content, list):
        content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return bool(block.get("is_error")) or (isinstance(content, str) and content.lstrip().startswith("Unknown skill"))


def loaded(path):
    """True when the main conversation loaded deliver-ticket, False when it did not, None when that is unknown."""
    start, pending = time.monotonic(), set()
    try:
        with open(path, "rb") as f:
            for raw in f:
                if time.monotonic() - start > BUDGET_S:
                    return None
                if not (b"deliver-ticket" in raw or any(i.encode() in raw for i in pending)):
                    continue
                if len(raw) > MAX_LINE:
                    return None
                e = json.loads(raw)
                if not isinstance(e, dict) or e.get("isSidechain"):
                    continue
                content = (e.get("message") or {}).get("content")
                if isinstance(content, str):
                    content = [{"type": "text", "text": content}]
                for b in content if isinstance(content, list) else ():
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and e.get("type") == "user" and SLASH.search(b.get("text") or ""):
                        return True
                    if b.get("type") == "tool_use" and b.get("name") == "Skill":
                        if isinstance(b.get("input"), dict) and b["input"].get("skill") in NAMES:
                            pending.add(str(b.get("id")))
                    elif b.get("type") == "tool_result" and b.get("tool_use_id") in pending:
                        pending.discard(b["tool_use_id"])
                        if not result_failed(b):
                            return True
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return None if pending else False


def check_write(event, s, cfg):
    """[Finding] for an edit by a card that has not loaded deliver-ticket."""
    if not roles.applies("deliver_load", s):
        return []
    lvl = config.level(cfg, KEY, "warn")
    args = event.get("tool_input")
    if lvl == "off" or not isinstance(args, dict):
        return []
    if not inside(args.get("file_path") or args.get("notebook_path"), event.get("cwd")):
        return []
    transcript = event.get("transcript_path")
    if EXEMPT.search(roles.first_prompt(transcript)) or loaded(transcript) is not False:
        return []
    return [Finding(KEY, lvl, REASON)]
