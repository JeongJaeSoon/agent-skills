#!/usr/bin/env python3
"""What a role session leaves for the session after its restart, and the request to restart (`orch handoff`).

Usage:
  orch handoff save --next TEXT [--waiting TEXT] [--question TEXT] [--reply TEXT] [--note TEXT] [--key KEY] [--restart]
  orch handoff show [--key KEY]
  orch handoff ready --session ID --context TOKENS [--agents N] [--asking]

KEY is `orchestrator` or `lead:<slug>`, taken from the session's role (key_for); a different --key is refused, and so is
a session with no role. The key is the role, not the session or the terminal, since a /clear gives a
new session id and a role can move to another terminal. Outside an Orca terminal only `--key lead:<slug>` is accepted.
`show` without --key reads the session's own key. The record is an event row in agent-skills.db
(store.session_event); the reorient hook adds it to the context of the session that starts after a /clear, and
`reboot.py --top` prints it with the mail taken since.

The model writes only what no store holds: --next (what to do first), --waiting (dispatches or answers it waits on),
--question (asked and unanswered), --reply (where the owner's last request came from and goes back to, such as a chat
channel), --note. The rest is filled from the stores when the session reads it: the Run the terminal is bound to and
the open `orch decide` items.

`ready` is what the plugin's clear band (hooks/clear_band.tsx) runs after each turn: it prints one JSON line saying
whether a restart is due (compactions.due: too many auto compactions in the transcript, a compaction that no longer
shrinks the context, two close together, or the context over `orchestrate.restart_at`; an orchestrator session only,
leads rely on auto-compact), why (`due`), and whether a /clear is safe now (its inbox empty, its last answer not a
question, no subagent running). When it is safe, it saves
a handoff from the stores with no model turn (store.save_auto_handoff), carrying the fields of the newest handoff. A
handoff another terminal saved for the same key in the last 10 minutes stops the save. The band then tells the human `/clear` is safe; the reorient hook gives
the next session the handoff.

--restart is the orchestrator's only (the stream reads requests under that key) and asks the terminal's `orch inbox --stream` to type `/clear` once the screen is idle; it is refused when no
stream runs for this terminal. The stream then prints `SESSION CLEARED`, whose notice starts the new session's first
turn (agent-skills:reboot). Off unless `orchestrate.auto_clear: true` (restart on the context ceiling) or
`orchestrate.compact_restart.auto_clear: true` (restart on the compaction rules) in ~/.claude/agent-skills.json: by
default the band only suggests and the human types the /clear.
"""
import json, os, pathlib, re, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "hooks"))
import compactions  # noqa: E402
import roles  # noqa: E402
import store  # noqa: E402

FIELDS = ("next", "waiting", "question", "reply", "note")
SUMMARY_CHARS = 1500


AUTO_EVERY_S = 600


def auto_clear_on():
    """Whether the stream may type /clear: `orchestrate.auto_clear` (the ceiling) or `compact_restart.auto_clear`."""
    p = compactions.policy()
    return p.auto_clear or (p.compact and p.auto_compact)


def opt(argv, name):
    if name not in argv:
        return None
    i = argv.index(name) + 1
    if i >= len(argv) or argv[i].startswith("--"):
        sys.exit(f"{name} needs a value")
    return argv[i]


def bound_run():
    try:
        p = subprocess.run(["orca", "orchestration", "run-current", "--json"], capture_output=True, text=True, timeout=20)
        return (json.loads(p.stdout or "{}").get("result") or {}).get("runId")
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return None


def lines(key, run=None):
    """The handoff as `key: value` lines, the stored fields first, then what the stores say now; [] without one."""
    run = run or bound_run
    last = store.last_session_event(key, "handoff")
    if not last:
        return []
    ts, data = last
    how = ", saved by the clear band from the stores" if (data or {}).get("auto") else ""
    out = [f"handoff {ts} ({key}{how}):"] +[f"  {k}: {data[k]}" for k in FIELDS if (data or {}).get(k)]
    if r := run():
        out.append(f"  run: {r} (bound to this terminal)")
    opened = store.decisions(status="open")
    if opened:
        out.append("  open decisions: " + ", ".join(f"{d['id']} {d.get('title') or ''}".strip() for d in opened[:8])
                   + (f" (+{len(opened) - 8})" if len(opened) > 8 else ""))
    restart = store.last_session_event(key, "restart")
    if restart and isinstance(restart[1], dict) and restart[0] >= ts:
        out.append(f"  restart {restart[0]}: {restart[1].get('stage')}")
    return out


def text(key):
    """The reorient hook's handoff paragraph after a /clear, "" without one or when the DB cannot be read."""
    try:
        got = lines(key)
    except (store.NotMigrated, OSError, ValueError):
        return ""
    if not got:
        return ""
    body = "\n".join(got)
    if len(body) > SUMMARY_CHARS:
        body = body[:SUMMARY_CHARS] + " …"
    return f"The previous session left this handoff (`orch handoff show --key {key}` prints it whole):\n{body}"


def role_key(session):
    """The handoff key of an orchestrator or lead session (a ROLE line or `--agent`), else None."""
    entry = roles.role_of(session) or {}
    if entry.get("role") == "orchestrator":
        return "orchestrator"
    if entry.get("role") == "lead" and entry.get("slug"):
        return f"lead:{entry['slug']}"
    return None


LEAD_KEY = re.compile(r"lead:[\w.-]+")


def key_for(session, asked):
    """(key, error). The key comes from the session's role; an asked key that differs is refused, so a lead cannot
    write the orchestrator's record. With no known session (outside an Orca terminal) only a well-formed
    `lead:<slug>` is taken. Shared by save, show and ready."""
    if session:
        key = role_key(session)
        if not key:
            return None, "this session has no orchestrator or lead role, so it has no handoff key"
        if asked and asked != key:
            return None, f"this session's handoff key is {key}; --key {asked} is refused"
        return key, ""
    if asked and LEAD_KEY.fullmatch(asked):
        return asked, ""
    return None, "the session is unknown, so only --key lead:<slug> is accepted"


def terminal_of(session):
    """The Orca terminal this session last started in, from the reorient hook's terminal records."""
    for f in sorted((roles.state() / "terminals").glob("*.json")):
        try:
            if json.loads(f.read_text()).get("session_id") == session:
                return f.stem
        except (OSError, ValueError, AttributeError):
            continue
    return None


def unread(key, handle):
    """Messages waiting in this role's inbox (a peek: nothing is marked read), heartbeats left out."""
    import prog
    if key == "orchestrator" or (prog.Program(key[5:]).cfg.get("lead_dispatch")):
        box = ["--terminal", handle] if handle else []
    else:
        box = ["--run", prog.Program(key[5:]).cfg["run"]]
    _, msgs = prog.batch(prog.check(*box, "--peek"))
    return [m for m in msgs if m.get("type") != "heartbeat" and not prog.revoked_echo(m)]


def ready(session, context, asking, now=None, peek=unread, agents=0, verdict=compactions.due):
    """The clear band's verdict for this session, as a dict; saves the handoff when a /clear is safe."""
    import inbox_stream
    key = key_for(session, None)[0]
    out = {"show": False, "ready": False, "context": context, "due": "", "key": key, "reason": ""}
    if key != "orchestrator":  # leads rely on auto-compact
        return out
    out["due"] = verdict(session, context, compactions.policy())
    if not out["due"]:
        return out
    out["show"] = True
    if asking:
        out["reason"] = "your last answer asks a question"
        return out
    if agents:
        out["reason"] = f"{agents} subagent(s) running"
        return out
    handle = os.environ.get("ORCA_TERMINAL_HANDLE") or terminal_of(session)
    try:
        waiting = peek(key, handle)
    except (SystemExit, KeyError, OSError) as e:
        out["reason"] = f"inbox not read ({str(e)[:80]})"
        return out
    if waiting:
        out["reason"] = f"{len(waiting)} message(s) in the inbox"
        return out
    if handle and inbox_stream.holding(handle):
        out["reason"] = "the inbox stream holds mail it has not shown yet"
        return out
    verdict, other = store.save_auto_handoff(
        key, {"auto": True, "session": session, "handle": handle, "context": context}, handle, session, AUTO_EVERY_S,
        FIELDS, "act on the mail taken since this handoff, then the open decisions", at=now)
    if verdict == "conflict":
        out["reason"] = (f"another terminal ({other}) saved this key's handoff in the last {AUTO_EVERY_S // 60} min: "
                         "two sessions share the orchestrator role")
        return out
    out.update(ready=True, reason="handoff saved")
    return out


def main(argv):
    if not argv or argv[0] not in ("save", "show", "ready"):
        sys.exit(__doc__)
    asked = opt(argv, "--key")
    handle = os.environ.get("ORCA_TERMINAL_HANDLE")
    session = (roles.read_terminal(handle) or {}).get("session_id") if handle else None
    try:
        if argv[0] == "ready":
            session, context = opt(argv, "--session"), opt(argv, "--context")
            if not session or not (context or "").isdigit():
                sys.exit("orch handoff ready needs --session ID and --context TOKENS")
            agents = opt(argv, "--agents") or "0"
            print(json.dumps(ready(session, int(context), "--asking" in argv,
                                   agents=int(agents) if agents.isdigit() else 0)))
            return
        if argv[0] == "show":  # reading is harmless: a session with no key of its own reads the orchestrator's
            key = asked or key_for(session, None)[0] or "orchestrator"
            print("\n".join(lines(key)) or f"no handoff recorded for {key}")
            return
        data = {f: opt(argv, f"--{f}") for f in FIELDS}
        if not data["next"]:
            sys.exit("orch handoff save needs --next: what the session after the restart does first")
        key, err = key_for(session, asked)
        if err:
            sys.exit(f"{err}. Nothing saved")
        restart = "--restart" in argv
        if restart and key != "orchestrator":  # the stream reads restart requests under the orchestrator key only
            sys.exit(f"--restart is for the orchestrator; this key is {key}, and no stream would type its /clear. "
                     "Nothing saved")
        if restart:
            import inbox_stream
            if not auto_clear_on():
                sys.exit("restart off (neither orchestrate.auto_clear nor compact_restart.auto_clear is true): nothing saved. Save without --restart; the "
                         "clear band tells the human when a /clear is safe")
            if not handle or not roles.SESSION.fullmatch(handle):
                sys.exit("restart needs ORCA_TERMINAL_HANDLE: only a session in an Orca terminal restarts itself")
            if not inbox_stream.running(handle):
                sys.exit("no `orch inbox --stream` runs for this terminal, and it is what types the /clear: start it "
                         "with the Monitor tool first. Nothing saved")
        store.session_event(key, "handoff", {**data, "handle": handle, "session": session}, by=key)
        print(f"handoff saved for {key}")
        if restart:
            store.session_event(key, "restart", {"stage": "requested", "handle": handle, "session": session}, by=key)
            print("restart requested: end this turn now. The inbox stream types /clear once the screen is idle and "
                  "then prints SESSION CLEARED, which starts the next session with agent-skills:reboot")
    except store.NotMigrated as e:
        sys.exit(f"handoff not stored: {e}")


if __name__ == "__main__":
    main(sys.argv[1:])
