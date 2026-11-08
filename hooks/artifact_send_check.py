#!/usr/bin/env python3
"""PreToolUse hook of the agent-skills plugin: refuse to send a message that names a backed-up Artifact without its
backup link. Covers the Telegram send tools and `orca terminal send`, `orca orchestration send|reply` in a Bash call
(worker_done included), the paths a link pair takes between sessions and to the owner. A page that went private, or has
no backup, is never demanded a link for.

An orca body is the literal --body or --text, or the file of `--body "$(cat /abs/path)"`, read and never run; a body
this hook cannot read is let through. Level `guard.artifact_links` in ~/.claude/agent-skills.json: `warn` (default; one
line to the model and the human, the call goes through), `deny` (only by this key), or `off`. A check that raises lets
the call through and logs to guard-errors.jsonl.
"""
import json, os, pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "skills" / "orchestrate" / "scripts"))

import decision_relay, record_check
from lib import artifact_links as al, config, shell

KEY = "guard.artifact_links"
SEND_TOOLS = re.compile(r"^mcp__.*telegram.*__(?:reply|send_message)$", re.I)


def orca_bodies(raw):
    bodies, substituted = [], False
    for c in shell.commands(raw):
        w = c.words
        if not (w and os.path.basename(w[0]) == "orca" and w[1:3] in decision_relay.RELAYS):
            continue
        body = record_check.option(w, "--body") or record_check.option(w, "--text") or ""
        if "$" in body:
            substituted = True
        else:
            bodies.append(body)
    if substituted:
        for segment in decision_relay.RELAY_TEXT.findall(decision_relay.SINGLE_QUOTED.sub("''", raw)):
            m = decision_relay.BODY_FILE.search(segment)
            p = pathlib.Path(m.group(1)) if m else None
            if p and p.is_file() and p.stat().st_size <= decision_relay.BODY_MAX:
                bodies.append(p.read_text(errors="replace"))
    return bodies


def outgoing(data):
    tool, inp = data.get("tool_name") or "", data.get("tool_input") or {}
    if SEND_TOOLS.match(tool):
        return [json.dumps(inp, ensure_ascii=False)]
    if tool == "Bash" and isinstance(inp.get("command"), str):
        return orca_bodies(inp["command"])
    return []


def run(data):
    mode = al.level()
    if mode == "off":
        return None
    owed = []
    for text in outgoing(data):
        owed += [link for link in al.owed_links(text) if link not in owed]
    if not owed:
        return None
    text = al.reason(owed)
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse"}}
    if mode == "deny":
        out["hookSpecificOutput"].update(permissionDecision="deny", permissionDecisionReason=text)
    else:
        out["systemMessage"] = text
        out["hookSpecificOutput"]["additionalContext"] = text
    return out


def main():
    if out := run(json.load(sys.stdin)):
        print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        config.log_error("artifact_send_check", e)
    sys.exit(0)
