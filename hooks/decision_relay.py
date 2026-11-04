#!/usr/bin/env python3
"""PreToolUse (Bash) hook of the agent-skills plugin: warns when an orchestrator or lead relays the owner's words and no
decision record stands behind them. The owner's answers went to leads as messages and were recorded nowhere.

A relay is `orca terminal send`, `orca orchestration reply` or `orca orchestration send` (not worker_done). Its body is
the literal --text or --body, or the file of `--body "$(cat /abs/path)"`, read and never run. It warns when a line holds
`오너` and that line or the next holds a quote ("…", “…”, 「…」, or `>` lines) that is not recorded. Recorded means:
- `오너(dN` in the body names a done decision whose stored answer equals the quote, spaces aside. A part of the answer
  does not count: "go" inside "do not go". Or
- an earlier call of this turn ran `orch decide answer` or `orch decide done` as its only command and got a result
  without an error. The current call is no evidence: a hook before it knows neither the order inside it nor its outcome.

It only warns (`guard.decision_relay`: warn, default, or off); it is a prompt to record, and misses a quote that spans
lines inside quotation marks. A check that raises lets the call through and logs to guard-errors.jsonl.
"""
import json, os, pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "skills" / "orchestrate" / "scripts"))

import claim_check, record_check, roles
from lib import config, shell

KEY = "guard.decision_relay"
RELAYS = (["terminal", "send"], ["orchestration", "reply"], ["orchestration", "send"])
BODY_MAX = 64 * 1024
# The same form delegate_gate reads: double quotes, so it is a substitution, and an absolute literal path.
BODY_FILE = re.compile(r'--(?:body|text)(?:=|[ \t]+)"\$\(cat[ \t]+(/[^\s"\'`$()\\;&|<>*?\[\]{}~]+)\)"')
# A relay in the raw text, up to its separator: the place to look for that form, as shell.commands gives no offsets.
RELAY_TEXT = re.compile(r"(?<![\w/.-])orca[ \t]+(?:terminal[ \t]+send|orchestration[ \t]+(?:reply|send))(?=[ \t])[^;&|\n]*")
QUOTE = re.compile(r'"([^"\n]{2,}?)\\?"|“([^”\n]{2,})”|「([^」\n]{2,})」')
CITE = re.compile(r"오너\s*\(\s*(d\d+)\b")
DECIDE = {"orch", "orch.py", "prog.py"}


def relay_bodies(raw):
    """The bodies of the relays in this Bash call. A body the shell would substitute comes from its file, or not at all."""
    bodies, substituted = [], False
    for c in shell.commands(raw):
        w = c.words
        if not (w and os.path.basename(w[0]) == "orca" and w[1:3] in RELAYS) or record_check.is_worker_done(w):
            continue
        body = record_check.option(w, "--body") or record_check.option(w, "--text") or ""
        if "$" in body:  # shell.commands leaves `$` only where bash expands, and drops the substitution's text
            substituted = True
        else:
            bodies.append(body)
    if substituted:
        for segment in RELAY_TEXT.findall(raw):
            m = BODY_FILE.search(segment)
            p = pathlib.Path(m.group(1)) if m and "worker_done" not in segment else None
            if p and p.is_file() and p.stat().st_size <= BODY_MAX:
                bodies.append(p.read_text(errors="replace"))
    return bodies


def quotes(body):
    """The quotes on a line with 오너 or the line after it; consecutive `>` lines are one quote."""
    lines = [l for l in body.splitlines() if l.strip()]
    found = []
    for i, line in enumerate(lines):
        if "오너" not in line:
            continue
        found += ["".join(m.groups(default="")) for part in lines[i:i + 2] for m in QUOTE.finditer(part)]
        block = []
        for part in lines[i + 1:]:
            if not part.lstrip().startswith(">"):
                break
            block.append(part.lstrip(" >"))
        if block:
            found.append(" ".join(block))
    return found


def cited_answers(body):
    ids = set(CITE.findall(body))
    if not ids:
        return set()
    import store
    return {" ".join((d.get("answer") or "").split()) for d in store.decisions("done") if d["id"] in ids}


def records(command):
    """True when this Bash command is `orch decide answer` or `done` alone: the call's result says nothing about one
    command of several (`… || true`)."""
    cmds = shell.commands(str(command))
    w = cmds[0].words if len(cmds) == 1 else []
    if w and os.path.basename(w[0]).startswith("python"):
        w = w[1:]
    return bool(w) and os.path.basename(w[0]) in DECIDE and w[1:3] in (["decide", "answer"], ["decide", "done"])


def recorded_this_turn(path):
    if not path or not os.path.isfile(path):
        return False
    entries = claim_check.turn(path)
    results = [b for e in entries if e.get("type") == "user" for b in claim_check.blocks(e) if b.get("type") == "tool_result"]
    ok = {b.get("tool_use_id") for b in results if not b.get("is_error")}
    return any(b.get("type") == "tool_use" and b.get("id") in ok and b.get("name") == "Bash"
               and records((b.get("input") or {}).get("command"))
               for e in entries if e.get("type") == "assistant" for b in claim_check.blocks(e))


def unrecorded(body):
    found = quotes(body)
    if not found:
        return False
    answers = cited_answers(body)
    return any(" ".join(q.split()) not in answers for q in found)


def main():
    event = json.load(sys.stdin)
    if event.get("hook_event_name") != "PreToolUse" or event.get("tool_name") != "Bash":
        return
    if config.level(config.load(), KEY, "warn") == "off" or not roles.applies("decision_relay", roles.session(event)):
        return
    bodies = relay_bodies(str((event.get("tool_input") or {}).get("command") or ""))
    if not any(unrecorded(b) for b in bodies) or recorded_this_turn(event.get("transcript_path")):
        return
    msg = ("decision_relay: this relay quotes the owner and no decision record backs it. Run `orch decide answer <id> "
           "--quote-file <file>` (or `--title T` for an order nobody asked) as a call of its own first, then cite it: "
           f'오너(d150, 2026-11-04): "<words>". ({KEY}: warn)')
    print(json.dumps({"systemMessage": msg, "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg}},
                     ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        config.log_error("decision_relay", exc)
    sys.exit(0)
