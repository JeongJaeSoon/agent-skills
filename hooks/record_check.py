#!/usr/bin/env python3
"""PreToolUse, PostToolUse, PostToolUseFailure, Stop and SubagentStop hook of the agent-skills plugin: a PR opened,
merged or landed, a `worker_done` and a card's close carry a record line (end-session references/record.md §4).

Merges went unrecorded: no skill loads at the moment of a merge unless one names it, and a message from the lead
does not reach a running card. This hook asks at that moment, and keeps asking until the report says what was
recorded.

- PostToolUse (Bash): a `gh pr merge` (not `--auto`/`--disable-auto`), `gh pr create` or `orch land` in the call
  opens an obligation for this session and agent and adds a reminder to the model's context. PostToolUseFailure opens
  one only when the call held more than one command, since the target may have run before a later one failed.
- An obligation closes on a record line: `기록: …`, `기록 못 함: <reason>`, `Recorded: …` or `Not recorded: …` at the
  start of a line with text after it (emphasis marks alone are not text), outside code blocks and quotes. Read from the final reply at Stop, from the body of a
  `worker_done` sent as the only command of its call (closed when the send succeeded, as of the moment it was
  checked), and from this turn's text when a close command is checked.
- PreToolUse (Bash) checks, at `guard.record_check`: a `worker_done` whose `--body` has no record line (a body that
  is not plain quoted text, such as a variable, a substitution or `$'…'`, counts as none), a target and a close command (`orca worktree rm`, `orca terminal close`) in
  one call, a `cd` before any close command in one call, and a close command while an obligation of this session, or of a session whose directory is under the
  closed path, is open with no record line in this turn's text since.
- Stop and SubagentStop: an open obligation and no record line in the final reply.

Levels: `warn` (default) reminds the model before a send or a close and writes a `warn` row at Stop, where it is only
telemetry (a systemMessage reaches the human, not the model); `deny` refuses the call and blocks the Stop once (with
`stop_hook_active` it passes and the obligation stays open); `ask` shows a dialog on PreToolUse and acts as `warn` at
Stop; `off` does nothing. The hook is off unless ~/.claude/agent-skills.json has a `tracker` or `notes` object.

It checks that the outcome of recording was reported, never that the record was written: the line is the agent's own
word, and the lead's reconcile checks it. It reads no tracker, notes store, Orca or GitHub state, so a merge started
in the background, a close target named by id and a stopped Stop are not followed. A check that raises lets the call
through and logs to guard-errors.jsonl.
"""
import datetime, json, os, pathlib, re, sys

import claim_check
from lib import config, shell

KEY = "guard.record_check"
STATE_FILE = "record-check.jsonl"
ROOT = pathlib.Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or pathlib.Path(__file__).resolve().parents[1])
RECORD_MD = ROOT / "skills" / "end-session" / "references" / "record.md"
LINE = re.compile(r"^[ \t]*(?:[-*+][ \t]+)?[*_]*(?:기록[ \t]*못[ \t]*함|기록|not recorded|recorded)[*_]*[ \t]*[:：](.*)$",
                  re.I | re.M)
EMPHASIS = re.compile(r"[*_~\s]+")
NEGATION = re.compile(r"(?:^|[\s;&|(`{])!(?=\s|$)")
CHDIR = {"cd", "pushd", "popd"}
GH_VALUE_OPTS = {"-R", "--repo", "--hostname"}


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def has_line(text):
    """A record line with content on the same line once emphasis marks are dropped. Fences and quote lines are
    examples; inline code after the label is content (a ticket key, a note path)."""
    if not isinstance(text, str):
        return False
    text = "\n".join(l for l in claim_check.FENCE.sub(" ", text).splitlines() if not l.lstrip().startswith(">"))
    return any(EMPHASIS.sub("", m.group(1)) for m in LINE.finditer(text))


def target(words):
    """'merge', 'create' or 'land' for a command that records a milestone; None otherwise."""
    if not words or any(w in ("--help", "-h", "--dry-run") for w in words[1:]):
        return None
    head, args = os.path.basename(words[0]), words[1:]
    if head == "gh":
        rest, skip = [], False
        for w in args:
            if skip:
                skip = False
            elif w in GH_VALUE_OPTS:
                skip = True
            elif not w.startswith("-"):
                rest.append(w)
        if rest[:1] == ["pr"] and rest[1:2] == ["create"]:
            return "create"
        flags = {n for w in args for n, _, v in [w.partition("=")]
                 if n in ("--auto", "--disable-auto") and v.lower() not in ("false", "0")}
        if rest[:1] == ["pr"] and rest[1:2] == ["merge"] and not flags:
            return "merge"
        return None
    if head.startswith("python") and args and os.path.basename(args[0]) == "orch.py":
        head, args = "orch", args[1:]
    if head in ("orch", "orch.py") and args[:1] == ["land"]:
        return "land"
    return None


def option(words, name):
    """The value of `--name v` or `--name=v`, else None."""
    for i, w in enumerate(words):
        if w == name and i + 1 < len(words):
            return words[i + 1]
        if w.startswith(name + "="):
            return w[len(name) + 1:]
    return None


def is_worker_done(words):
    return (bool(words) and os.path.basename(words[0]) == "orca" and words[1:3] == ["orchestration", "send"]
            and option(words, "--type") == "worker_done")


def close_target(words, cwd):
    """For `orca worktree rm` or `orca terminal close`: (closes this session?, a directory it removes or None).
    None when the command is neither."""
    if not words or os.path.basename(words[0]) != "orca":
        return None
    if words[1:3] not in (["worktree", "rm"], ["terminal", "close"]):
        return None
    sel = option(words, "--worktree") or ""
    term = option(words, "--terminal") or ""
    own_term = term in ("$ORCA_TERMINAL_HANDLE", "${ORCA_TERMINAL_HANDLE}") or (
        term and term == os.environ.get("ORCA_TERMINAL_HANDLE"))
    path = sel[5:] if sel.startswith("path:") else sel if sel.startswith("/") else None
    if sel == "current":
        path = cwd
    own = bool(own_term) or (path is not None and cwd is not None and _under(cwd, path))
    return own, path


def _under(child, parent):
    try:
        return os.path.commonpath([os.path.realpath(child), os.path.realpath(parent)]) == os.path.realpath(parent)
    except ValueError:
        return False


def valid(r):
    text = lambda k: isinstance(r.get(k), str)
    maybe = lambda k: r.get(k) is None or text(k)
    return (isinstance(r, dict) and r.get("event") in ("open", "close", "report", "warn") and text("at")
            and all(maybe(k) for k in ("session", "agent", "cwd", "kind", "id", "via", "where")))


def rows():
    """Every readable row; a damaged line is skipped, not the file."""
    out, bad = [], None
    try:
        with open(config.state() / STATE_FILE, encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    if not valid(r):
                        raise ValueError("record-check row of the wrong shape")
                except ValueError as exc:
                    bad = exc
                    continue
                out.append(r)
    except OSError:
        pass
    if bad is not None:
        config.log_error("record_check", bad)
    return out


def append(row):
    """One row, appended whole (one write of one line under O_APPEND)."""
    config.state().mkdir(parents=True, exist_ok=True)
    with open(config.state() / "record-check.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def pending(all_rows, keep):
    """Open obligations per (session, agent) for which keep(row) holds: the opens at or after that key's last close
    (a close covers only what came strictly before it)."""
    last, opens = {}, {}
    for r in all_rows:
        k = (r.get("session"), r.get("agent") or "")
        if r.get("event") == "close":
            last[k] = max(last.get(k, ""), r.get("at") or "")
        elif r.get("event") == "open" and keep(r):
            opens.setdefault(k, []).append(r)
    out = {k: [o for o in v if (o.get("at") or "") >= last.get(k, "")] for k, v in opens.items()}
    return {k: v for k, v in out.items() if v}


def destinations(cfg):
    out = []
    if isinstance(cfg.get("tracker"), dict):
        out.append("the tracker (the ticket's state, the PR link, the merge commit: use-tracker \"Milestones\")")
    if isinstance(cfg.get("notes"), dict):
        out.append("the notes store (the worklog line: use-notes)")
    return out


def reminder(cfg, kinds):
    return (f"record_check: {', '.join(sorted(kinds))} ran. Read {RECORD_MD} and record it now in "
            f"{' and '.join(destinations(cfg))}, where this workflow authorizes the write. Then put a record line in "
            f"the report (and in any worker_done body): `기록: <ticket and transition> · <note path>`, "
            f"`기록: 없음 (<why>)`, or `기록 못 함: <reason>` when a store is not connected, the write is not "
            f"authorized, or the command is still running in the background.")


def turn_text_since(path, at):
    """This turn's assistant text written after `at`."""
    if not path or not os.path.isfile(path):
        return ""
    out = []
    for entry in claim_check.turn(path):
        if entry.get("type") == "assistant" and (entry.get("timestamp") or "") > at:
            out += [b.get("text") or "" for b in claim_check.blocks(entry) if b.get("type") == "text"]
    return "\n".join(out)


def final_text(event, path):
    msg = event.get("last_assistant_message")
    if isinstance(msg, str):
        return msg
    final = []
    if path and os.path.isfile(path):
        for entry in claim_check.turn(path):
            if entry.get("type") == "user":
                final = []
            elif entry.get("type") == "assistant":
                final += [b.get("text") or "" for b in claim_check.blocks(entry) if b.get("type") == "text"]
    return "\n".join(final)


def post_tool(event, cfg, failed):
    raw = str((event.get("tool_input") or {}).get("command") or "")
    cmds = shell.commands(raw)
    session, agent, tid = event.get("session_id"), event.get("agent_id") or "", event.get("tool_use_id") or ""
    existing = rows()
    # `!` inverts an exit status and shell.commands drops it; with one anywhere (`( ! x )`, `time ! x`), the call's
    # outcome says nothing about a command's.
    negated = bool(NEGATION.search(shell.command_text(raw)))
    if not failed and not negated and len(cmds) == 1 and is_worker_done(cmds[0].words):
        for r in existing:
            if r.get("event") == "report" and r.get("id") == tid and tid:
                append({"at": r["at"], "event": "close", "session": session, "agent": agent, "via": "worker_done"})
                break
    if failed and len(cmds) < 2 and not negated:
        return None
    kinds = {k for c in cmds if (k := target(c.words))}
    if not kinds:
        return None
    if not (tid and any(r.get("event") == "open" and r.get("id") == tid for r in existing)):
        for k in sorted(kinds):
            append({"at": now(), "event": "open", "session": session, "agent": agent, "cwd": event.get("cwd"),
                    "kind": k, "id": tid})
    name = "PostToolUseFailure" if failed else "PostToolUse"
    return {"hookSpecificOutput": {"hookEventName": name, "additionalContext": reminder(cfg, kinds)}}


def pre_tool(event, cfg, lvl):
    cmds = shell.commands(str((event.get("tool_input") or {}).get("command") or ""))
    session, agent, tid = event.get("session_id"), event.get("agent_id") or "", event.get("tool_use_id") or ""
    cwd, path = event.get("cwd"), event.get("transcript_path")
    problems = []
    targets = [c for c in cmds if target(c.words)]
    closes = [(c, t) for c in cmds if (t := close_target(c.words, cwd))]
    for c in cmds:
        if not is_worker_done(c.words):
            continue
        body = option(c.words, "--body")
        # shell.commands leaves `$` only where bash expands (a substitution becomes `$___`); quoted `$` is a stand-in.
        if body is None or "$" in body or not has_line(body):
            problems.append("this worker_done body has no record line it can read (`기록: …`, `기록: 없음 (<why>)` or "
                            "`기록 못 함: <reason>` at the start of a line, in a literal --body)")
        elif len(cmds) == 1 and tid:
            append({"at": now(), "event": "report", "session": session, "agent": agent, "id": tid})
    if targets and closes:
        problems.append("this call both records a milestone and closes a session; run the close as its own call after "
                        "the report")
    elif closes and any(c.words and os.path.basename(c.words[0]) in CHDIR
                        for c in cmds[:max(cmds.index(c) for c, _ in closes)]):
        problems.append("this call changes directory before a close, so the closed path cannot be checked; run the "
                        "close as its own call")
    for _, (own, closed) in closes:
        keep = lambda r: ((r.get("session"), r.get("agent") or "") == (session, agent) and own) or (
            closed is not None and r.get("cwd") and _under(r["cwd"], closed))
        open_ = pending(rows(), keep)
        mine = open_.pop((session, agent), None)
        if mine and has_line(turn_text_since(path, max(o.get("at") or "" for o in mine))):
            append({"at": now(), "event": "close", "session": session, "agent": agent, "via": "close-check"})
            mine = None
        if mine or open_:
            n = len(mine or []) + sum(len(v) for v in open_.values())
            problems.append(f"{n} merge, land or PR-open event(s) in the session(s) this closes have no record line "
                            "yet; write the report with its record line first")
    if not problems:
        return None
    msg = f"record_check: {'; '.join(problems)}. See {RECORD_MD} §4. (guard.record_check: {lvl})"
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse"}}
    if lvl in ("deny", "ask"):
        out["hookSpecificOutput"].update(permissionDecision=lvl, permissionDecisionReason=msg)
    else:
        out["systemMessage"] = msg
        out["hookSpecificOutput"]["additionalContext"] = msg
        append({"at": now(), "event": "warn", "session": session, "agent": agent, "where": "PreToolUse"})
    return out


def stop(event, cfg, lvl):
    session, agent = event.get("session_id"), event.get("agent_id") or ""
    open_ = pending(rows(), lambda r: (r.get("session"), r.get("agent") or "") == (session, agent)).get((session, agent))
    if not open_:
        return None
    path = event.get("agent_transcript_path") or event.get("transcript_path")
    if has_line(final_text(event, path)):
        append({"at": now(), "event": "close", "session": session, "agent": agent, "via": "stop"})
        return None
    if event.get("stop_hook_active"):
        return None
    kinds = ", ".join(sorted({o.get("kind") or "?" for o in open_}))
    msg = (f"record_check: {kinds} ran in this session and no report since carries a record line. "
           f"{reminder(cfg, {o.get('kind') or '?' for o in open_})}")
    if lvl == "deny":
        return {"decision": "block", "reason": msg}
    append({"at": now(), "event": "warn", "session": session, "agent": agent, "where": "Stop"})
    return {"systemMessage": msg}


def main():
    event = json.load(sys.stdin)
    cfg = config.load()
    if not (isinstance(cfg.get("tracker"), dict) or isinstance(cfg.get("notes"), dict)):
        return
    lvl = config.level(cfg, KEY, "warn")
    if lvl == "off":
        return
    name = event.get("hook_event_name")
    if name in ("PostToolUse", "PostToolUseFailure") and event.get("tool_name") == "Bash":
        out = post_tool(event, cfg, name == "PostToolUseFailure")
    elif name == "PreToolUse" and event.get("tool_name") == "Bash":
        out = pre_tool(event, cfg, lvl)
    elif name in ("Stop", "SubagentStop"):
        out = stop(event, cfg, lvl)
    else:
        out = None
    if out:
        print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        config.log_error("record_check", exc)
    sys.exit(0)
