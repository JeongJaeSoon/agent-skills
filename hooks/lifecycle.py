#!/usr/bin/env python3
"""PreToolUse hook of the agent-skills plugin: a card's end, and what a role session's subagents are told.

Exit order (key `guard.exit_order`, default `warn`): an `orca worktree rm` from a dispatched, CLOSE or KEEP card's
main session (roles.py; a dispatched lead included) before its `worker_done` came back. A dispatched card's rm needs,
in an earlier assistant message than the rm's, a Bash call that is only `orca orchestration send --type worker_done
--dispatch-id <its current Dispatch>` (the last `--dispatch-id` its prompts name) whose result is not an error and
shows `"ok": true` or a `messageId=`. A `/goal` card with a CLOSE row and no
Dispatch needs instead an earlier `skills-sync end-check --card` result whose verdict is `remove`. Calls sent in one
message run together, so a worker_done beside the rm has no result yet: the rm would remove the card even when the
send was refused, and its report never arrives (end-session §7). A KEEP row on the card refuses the rm outright.
`deny` refuses such an rm, `warn` lets it through with a warning, `off` skips the check.

Agent prompts (key `orchestrate.agent_forbidden`, default on): an Agent call from an orchestrator, a lead or a
worker (its subagents included) whose prompt lacks the refused-act paragraph of orchestrate references/brief.md
FORBIDDEN gets it appended (`updatedInput`, returned with `allow`, which Claude Code needs to apply it; deny and ask
rules still hold). The brief says to paste it into every subagent prompt; `skills-sync card` covers card briefs,
this covers the Agent tool. `false` or `"off"` turns it off.

An rm of the session's own card whose transcript cannot be read is treated as unread order: warned or denied at the
key's level. Any other check that raises lets the call through (a line in guard-errors.jsonl).
"""
import json, os, pathlib, re, sys

import roles
from lib import config, shell

ROOT = pathlib.Path(__file__).resolve().parents[1]
BRIEF = ROOT / "skills" / "orchestrate" / "references" / "brief.md"
PARAGRAPH = re.compile(r"An act refused by a permission prompt.*?fix\s+the\s+cause\s+and\s+run\s+it\s+again\.", re.S)
DISPATCH = re.compile(r"--dispatch-id[ =]+(ctx_\w+)\b")
OK = re.compile(r'"ok"\s*:\s*true|\bmessageId=msg_')
SOURCE = "(agent-skills hooks/lifecycle.py, {key})"


def paragraph():
    m = PARAGRAPH.search(BRIEF.read_text())
    return " ".join(m.group(0).split()) if m else None


def entries(path):
    """The transcript's entries in order; [] when it cannot be read."""
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict):
                    out.append(e)
    except (OSError, TypeError):
        return None
    return out


def blocks(e):
    content = (e.get("message") or {}).get("content") if isinstance(e.get("message"), dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def text_of(e):
    content = (e.get("message") or {}).get("content") if isinstance(e.get("message"), dict) else None
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text") or "" for b in blocks(e) if b.get("type") == "text")


def result_text(b):
    c = b.get("content")
    return c if isinstance(c, str) else " ".join(x.get("text") or "" for x in c or [] if isinstance(x, dict))


def calls(rows):
    """[(message id, tool_use id, command, result text or None, is_error)] for each Bash call, in order."""
    uses, results = [], {}
    for e in rows:
        mid = (e.get("message") or {}).get("id") if isinstance(e.get("message"), dict) else None
        for b in blocks(e):
            if b.get("type") == "tool_use" and b.get("name") == "Bash":
                cmd = (b.get("input") or {}).get("command") if isinstance(b.get("input"), dict) else None
                uses.append((mid or e.get("uuid"), b.get("id"), cmd if isinstance(cmd, str) else ""))
            elif b.get("type") == "tool_result" and isinstance(b.get("tool_use_id"), str):
                results[b["tool_use_id"]] = (result_text(b), bool(b.get("is_error")))
    return [(m, i, c, *results.get(i, (None, False))) for m, i, c in uses]


def this_message(rows, tool_use_id):
    """The assistant message id the call under check belongs to: the one holding its tool_use, else the newest."""
    newest = None
    for e in rows:
        if e.get("type") != "assistant":
            continue
        mid = (e.get("message") or {}).get("id") if isinstance(e.get("message"), dict) else None
        newest = mid or e.get("uuid")
        if any(b.get("type") == "tool_use" and b.get("id") == tool_use_id for b in blocks(e)):
            return newest
    return newest


def flag(words, name):
    """The value of `name` in argv words (`--x v` or `--x=v`) when it is given exactly once, else None."""
    at = [i for i, w in enumerate(words) if w == name or w.startswith(name + "=")]
    if len(at) != 1:
        return None
    w = words[at[0]]
    return w[len(name) + 1:] if w != name else words[at[0] + 1] if at[0] + 1 < len(words) else None


def worker_done_for(cmd, dispatch):
    """True when `cmd` is one `orca orchestration send --type worker_done` for exactly this Dispatch and nothing
    else, so its output is that send's own receipt."""
    cs = shell.commands(cmd)
    return (len(cs) == 1 and cs[0].name == "orca" and cs[0].words[1:3] == ["orchestration", "send"]
            and flag(cs[0].words, "--type") == "worker_done" and flag(cs[0].words, "--dispatch-id") == dispatch)


def removes(cmd):
    return any(c.name == "orca" and c.words[1:3] == ["worktree", "rm"] for c in shell.commands(cmd))


def removes_own(cmd, cwd):
    """True when an `orca worktree rm` in `cmd` may target the caller's own card: `current`, `active`, no target, a
    `path:` at or above the session's cwd, or an `id:` (not comparable without asking Orca). A lead removing a
    finished child card by its path is not its own end."""
    for c in shell.commands(cmd):
        if not (c.name == "orca" and c.words[1:3] == ["worktree", "rm"]):
            continue
        target = flag(c.words, "--worktree")
        if target in (None, "current", "active") or not target.startswith("path:"):
            return True
        path = target[len("path:"):]
        # A path the shell still expands ($PWD, ~, `…`) or a relative one may well be this card.
        if not path.startswith("/") or any(ch in path for ch in "$`~*?[") or not isinstance(cwd, str):
            return True
        path, here = os.path.normpath(path), os.path.normpath(cwd)
        if here == path or here.startswith(path.rstrip("/") + "/"):
            return True
    return False


def is_end_check(cmd):
    return any(c.name == "skills-sync" and c.words[1:2] == ["end-check"] for c in shell.commands(cmd))


def end_check_remove(cmd, out):
    """True when `cmd` is only a `skills-sync end-check` and the JSON it printed says mode `card`, verdict `remove`
    (the mode it really ran in, not a `--card` word that was some flag's value)."""
    cs = shell.commands(cmd)
    if not (len(cs) == 1 and cs[0].name == "skills-sync" and cs[0].words[1:2] == ["end-check"]):
        return False
    m = re.search(r"(?m)^\{", out or "")  # warnings on stderr come first, one line each
    try:
        res = json.JSONDecoder().raw_decode(out, m.start())[0] if m else None
    except ValueError:
        return False
    return isinstance(res, dict) and res.get("mode") == "card" and res.get("verdict") == "remove"


def exit_order(event, s, lvl):
    """(decision, reason) for an `orca worktree rm`, or (None, None)."""
    cmd = (event.get("tool_input") or {}).get("command")
    if not isinstance(cmd, str) or not removes_own(cmd, event.get("cwd")):
        return None, None
    key = SOURCE.format(key="guard.exit_order")
    if s.keep:
        return lvl, (f"This card's brief has a KEEP row: the session above plans to reuse it, so it is not removed "
                     f"(end-session §7). Send worker_done, then idle. {key}")
    rows = entries(event.get("transcript_path"))
    if rows is None:
        return lvl, f"`orca worktree rm` while this card's transcript cannot be read: its order is unknown. {key}"
    mine = this_message(rows, event.get("tool_use_id"))
    earlier = [c for c in calls(rows) if c[0] != mine and c[3] is not None and not c[4]]
    if s.supervised:
        ids = [m for e in rows if e.get("type") == "user" for m in DISPATCH.findall(text_of(e))]
        current = ids[-1] if ids else None
        if current and any(worker_done_for(c[2], current) and OK.search(c[3]) for c in earlier):
            return None, None
        return lvl, (f"`orca worktree rm` before this Dispatch's worker_done came back"
                     f"{f' ({current})' if current else ''}. Send worker_done as its own call, and only once its "
                     "output shows \"ok\": true, in a later message, remove the card; a send and an rm in one message "
                     f"run together and the rm wins even when the send is refused (end-session §7). {key}")
    if s.close:
        # The latest end-check decides: a later keep or unread fact takes back an earlier remove.
        checks = [c for c in calls(rows) if c[0] != mine and is_end_check(c[2])]
        if checks and checks[-1][3] is not None and not checks[-1][4] and end_check_remove(checks[-1][2], checks[-1][3]):
            return None, None
        return lvl, ("`orca worktree rm` from a CLOSE card before `skills-sync end-check --card` said `remove` "
                     f"(end-session §8). Run it first, as its own call. {key}")
    return None, None


def attach(event, cfg):
    """The Agent call's input with the refused-act paragraph appended, or None when it has it or is not covered."""
    value = config.get(cfg, "orchestrate.agent_forbidden", True)
    if value is False or value == "off":
        return None
    args = event.get("tool_input")
    if not isinstance(args, dict) or not isinstance(args.get("prompt"), str):
        return None
    text = paragraph()
    if not text or text in " ".join(args["prompt"].split()):
        return None
    return {**args, "prompt": args["prompt"].rstrip() + "\n\n" + text}


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(event, dict) or event.get("hook_event_name", "PreToolUse") != "PreToolUse":
        return 0
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse"}}
    try:
        cfg = config.load()
        tool = event.get("tool_name")
        cmd = (event.get("tool_input") or {}).get("command") if isinstance(event.get("tool_input"), dict) else None
        if tool == "Bash" and not (isinstance(cmd, str) and "worktree" in cmd and removes(cmd)):
            return 0  # the role is read from the transcript only for an rm
        s = roles.session(event)
        lvl = config.level(cfg, "guard.exit_order", "warn")
        # Unread, the transcript cannot say whether this is a card that must report first: it does not pass as one
        # that need not.
        blind = (tool == "Bash" and lvl != "off" and not event.get("agent_id") and removes_own(cmd, event.get("cwd"))
                 and entries(event.get("transcript_path")) is None)
        # A dispatched or CLOSE/KEEP card ends the same way whatever role it declared (a dispatched lead included).
        if blind or (tool == "Bash" and roles.applies("exit_order", s) and (s.supervised or s.close or s.keep)):
            decision, reason = (lvl, "`orca worktree rm` of this card while its transcript cannot be read, so whether "
                                     "its worker_done or end-check came back first is unknown (end-session §7, §8). "
                                + SOURCE.format(key="guard.exit_order")) if blind else \
                exit_order(event, s, lvl) if lvl != "off" else (None, None)
            if decision == "deny":
                out["hookSpecificOutput"].update(permissionDecision="deny", permissionDecisionReason=reason)
            elif decision:
                out["systemMessage"] = out["hookSpecificOutput"]["additionalContext"] = reason
            else:
                return 0
        elif tool in ("Agent", "Task") and roles.applies("agent_forbidden", s):
            updated = attach(event, cfg)
            if not updated:
                return 0
            out["hookSpecificOutput"].update(
                permissionDecision="allow", updatedInput=updated,
                permissionDecisionReason="agent-skills: the refused-act paragraph of orchestrate references/brief.md "
                                         "FORBIDDEN was appended to the subagent prompt "
                                         + SOURCE.format(key="orchestrate.agent_forbidden"))
        else:
            return 0
    except Exception as e:
        config.log_error("lifecycle", e)
        return 0
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
