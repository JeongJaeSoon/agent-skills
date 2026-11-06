#!/usr/bin/env python3
"""`orch db migrate`: the whole cutover to agent-skills.db in one command, safe to run again.

Run it yourself from a plain terminal, never from a Claude session. It stops what writes the state (the dashboard
LaunchAgent and agent-skills-sync if an older setup left them loaded, a dashboard server nobody manages, the
janitor, watch-prs, sync and orch-dash automations), refuses
while a session, process or cron line that runs old code remains (it prints a table and changes nothing), runs the
import, checks the result, and puts back what it stopped, also when it fails. Already migrated: it says so and
touches nothing. `--check` lists what it would stop and what stands in the way, and writes nothing.

A killed run leaves `cutover-recover.json`; the next run puts those services back first. The values in a
LaunchAgent's ORCH_* and PROGRAMS_HOME env are used for the run when this shell does not set them; the plist is never
written.
"""
import json, os, pathlib, subprocess, sys, time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fleet  # noqa: E402
import store  # noqa: E402

ENV_KEYS = ("PROGRAMS_HOME", "ORCH_DASH_LABEL", "ORCH_FLEET", "ORCH_FLEET_STATE", "ORCH_SKILLS", "ORCH_PR_STATS",
            "ORCH_CLAUDE_HOMES")
RECOVER = "cutover-recover.json"
# The automations whose rounds write while the cutover runs; sync and orch-dash are what the LaunchAgents became.
JOBS = ("janitor", "janitor-load", "watch-prs", "sync", "orch-dash")


def sh(cmd):
    return store._sh(cmd)


def _norm(v):
    v = str(v)
    return str(pathlib.Path(v).expanduser().resolve()) if v.startswith(("/", "~")) else v


# ---------------------------------------------------------------- the env the dashboard really runs with

def settle_env():
    """({key: value} this shell lacks and a LaunchAgent has, [conflicts]). Values are never printed."""
    seen, add, bad = {}, {}, []
    for label, (_, spec) in store.launch_plists().items():
        for k, v in (spec.get("EnvironmentVariables") or {}).items():
            if k in ENV_KEYS:
                seen.setdefault(k, {})[label] = str(v)
    for k, by in seen.items():
        vals = {_norm(v) for v in by.values()}
        if len(vals) > 1:
            bad.append(f"{k} differs between LaunchAgents ({', '.join(sorted(by))}): set it in this shell to the one to migrate")
        elif k in os.environ and _norm(os.environ[k]) not in vals:
            bad.append(f"{k} in this shell is not the one the LaunchAgent uses: unset it, or set it to the state you mean")
        elif k not in os.environ:
            add[k] = next(iter(by.values()))
    return add, bad


def ensure_env(sub, argv):
    """None when this shell's env already matches the LaunchAgents', else the refusal text; or, when the shell lacks
    values they have, starts `store.py <sub>` again with them (prog.HOME and the state directory are read when the
    modules load, so setting os.environ late is not enough). Never returns in that case."""
    add, conflicts = settle_env()
    if conflicts:
        return "refused: " + "; ".join(conflicts)
    if add:
        os.execve(sys.executable, [sys.executable, str(HERE / "store.py"), sub, *argv], {**os.environ, **add})
    return None


# ---------------------------------------------------------------- what is done already (reads only)

def _after_import():
    """What the import just committed, read through a normal connection: peek_steps opens the file immutable, which
    ignores a -wal that a reader still holding the DB keeps from being checkpointed, and would call a good import failed."""
    return store.SCHEMA_VERSION, store.migrated_steps()


def status(read=None):
    """("migrated" | "pending" | "conflict", detail), reading the DB without touching it (store.peek_steps)."""
    sd, p = fleet.state_dir(), store.path()
    try:
        v, steps = (read or store.peek_steps)()
    except store.Refused as e:
        return "conflict", str(e)
    if v > store.SCHEMA_VERSION:
        return "conflict", f"{p} has schema {v}, newer than this code ({store.SCHEMA_VERSION}): update this checkout first"
    if v == store.SCHEMA_VERSION and all(steps.get(s) == "tombstoned" for s in store.NEEDS):
        gone = [n for s in store.NEEDS for n in store.LEGACY[s] if not (sd / n).is_dir()]
        if gone:
            return "conflict", (f"migrated, but {', '.join(gone)} is a file again or gone: old code wrote after the cutover. "
                                "Compare it with the backup in the state directory and move it aside by hand")
        return "migrated", f"{p}: {', '.join(store.NEEDS)} tombstoned"
    return "pending", ", ".join(f"{s}: {steps.get(s, 'not imported')}" for s in store.NEEDS)


# ---------------------------------------------------------------- what runs, and what puts it back

def _uid():
    return os.getuid()


def loaded(label):
    return sh(["launchctl", "print", f"gui/{_uid()}/{label}"]).returncode == 0


def _jobs():
    """{name: (automation, orca's record)} for JOBS that exist; Refused when Orca cannot be read."""
    sys.path.insert(0, str(store.REPO / "scripts"))
    import automations
    out = {}
    for a in automations.AUTOMATIONS:
        if a.name in JOBS:
            found, err = a.find()
            if found is None:
                raise store.Refused(f"cannot read the {a.name} automation ({err}): nothing was changed")
            if found:
                out[a.name] = (automations, found)
    return out


def snapshot():
    """The services up now, as recovery steps, in the order they come back."""
    steps = []
    if sys.platform == "darwin":
        for label, (p, _) in store.launch_plists().items():
            if loaded(label):
                if not p:  # nothing to load it from afterwards: do not take it down
                    raise store.Refused(f"LaunchAgent {label} is loaded but has no plist in ~/Library/LaunchAgents, so it "
                                        "could not be put back: stop it yourself (launchctl bootout) or restore the plist; "
                                        "nothing was changed")
                steps.append({"do": "bootstrap", "label": label, "plist": str(p)})
    for name, (_, rec) in _jobs().items():
        if rec.get("enabled"):
            steps.append({"do": "enable", "name": name, "id": rec["id"]})
    return steps


def _plan_path():
    return fleet.state_dir() / RECOVER


def load_plan():
    return fleet.read_json(_plan_path(), []) or []


def save_plan(steps):
    p = _plan_path()
    if not steps:
        p.unlink(missing_ok=True)
        return
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(steps, indent=1))
    os.replace(tmp, p)


def _key(s):
    return (s["do"], s.get("label") or s.get("id") or s.get("port"))


def _exit(r):
    """How a command ended, for an error line: a negative code is the signal that killed it (-9: SIGKILL, which
    an endpoint security agent sends to a blocked process)."""
    how = f"killed by signal {-r.returncode}" if r.returncode < 0 else f"exit {r.returncode}"
    err = (r.stderr or "").strip()[:120]
    return f"{how}{': ' + err if err else ''}"


def _stop_one(s):
    """Stop the thing a step puts back, and check that it stopped. Raises Refused when it did not."""
    if s["do"] == "bootstrap":
        if not loaded(s["label"]):  # nothing to stop: do not run launchctl at all
            return
        r = sh(["launchctl", "bootout", f"gui/{_uid()}/{s['label']}"])
        for _ in range(25):
            if not loaded(s["label"]):
                return
            time.sleep(0.2)
        raise store.Refused(f"LaunchAgent {s['label']} is still loaded after bootout ({_exit(r)}): stop it yourself, "
                            f"then run this again")
    if s["do"] == "enable":
        automations, rec = _jobs().get(s["name"], (None, None))
        if rec is None or rec.get("id") != s["id"]:  # gone or replaced since it was recorded: not this one to stop
            return
        r = sh([automations.ORCA, "automations", "edit", "--id", s["id"], "--disabled", "--json"])
        after = _jobs().get(s["name"])
        if r.returncode or (after and after[1].get("enabled")):  # a round that already started still finishes
            raise store.Refused(f"could not turn the {s['name']} automation off: {(r.stderr or r.stdout).strip()[:120]}")


def note_server(plan):
    """Record a dashboard server that is up now, before any stop: the server ends itself once the lock is held, and
    a server that is gone by the time it is looked for would leave no way back."""
    import dash
    port = dash.default_port()
    h = dash.health(port)
    if h and h.get("store") == str(dash.home()) and not any(s["do"] == "server" for s in plan):
        plan.append({"do": "server", "port": port})
    return h


def add_yielded_server(wait=5.0):
    """Put a server that ended itself because this run held cutover.lock into the plan (see dash.watch_migrate). The
    mark is written just before the server ends, so wait until it is down: `ensure` on a server still up would succeed
    and drop the step. The mark is removed only after the step is saved, so a run killed in between still finds it."""
    port = store.dash_yielded()
    if port is not None:
        import dash
        end = time.monotonic() + wait
        up = dash.health(port)
        while up and time.monotonic() < end:
            time.sleep(0.1)
            up = dash.health(port)
        if up:  # still up: it has not ended yet, and ensure would call it done and drop the step
            print(f"the dashboard on port {port} left its mark but is still up; the mark stays for the next run", file=sys.stderr)
            return
        plan = load_plan()
        if not any(s["do"] == "server" for s in plan):
            save_plan(plan + [{"do": "server", "port": port}])
    store.clear_dash_yielded()


def stop_server(plan):
    """A dashboard server nobody manages (started by `ensure`): stop it; its recovery step is already in the plan."""
    import dash
    port = dash.default_port()
    h = note_server(plan)
    if not h or h.get("store") != str(dash.home()):
        return
    save_plan(plan)
    dash.stop(h, port)
    if dash.health(port):
        raise store.Refused(f"the dashboard server on port {port} is still up after SIGTERM")


def _undo(s):
    """(ok, line) for one recovery step. Safe to repeat."""
    if s["do"] == "bootstrap":
        if loaded(s["label"]):
            return True, f"{s['label']}: already loaded"
        if not s.get("plist") or not pathlib.Path(s["plist"]).is_file():
            return False, f"{s['label']}: no plist file to load; start it by hand"
        for _ in range(5):  # right after bootout, bootstrap can fail until the old job is gone
            r = sh(["launchctl", "bootstrap", f"gui/{_uid()}", s["plist"]])
            if r.returncode == 0 or loaded(s["label"]):
                return True, f"{s['label']}: loaded again"
            time.sleep(1)
        return False, f"{s['label']}: launchctl bootstrap failed ({_exit(r)})"
    if s["do"] == "server":
        r = sh([sys.executable, str(HERE / "dash.py"), "ensure", "--port", str(s["port"])])
        return r.returncode == 0, f"dashboard server on port {s['port']}: {(r.stdout or r.stderr).strip()[-120:]}"
    if s["do"] == "enable":
        try:
            _, rec = _jobs().get(s["name"], (None, None))
        except store.Refused as e:
            return False, str(e)
        sys.path.insert(0, str(store.REPO / "scripts"))
        import automations
        if rec is None or rec.get("id") != s["id"]:
            # The name no longer leads to the recorded automation: it was renamed, replaced, or deleted. Look by id.
            autos, err = automations.orca_automations()
            if autos is None:
                return False, f"{s['name']}: cannot read Orca's automations ({err}); it stays in the plan"
            rec = next((a for a in autos if a.get("id") == s["id"]), None)
            if rec is None:  # gone, or the name now belongs to another one: never touch that
                return True, f"{s['name']}: the automation recorded is gone; nothing to turn on"
        if rec.get("enabled"):
            return True, f"{s['name']}: already on"
        r = sh([automations.ORCA, "automations", "edit", "--id", s["id"], "--enabled", "--json"])
        return r.returncode == 0, f"{s['name']}: " + ("on again" if r.returncode == 0 else f"orca edit failed: {r.stderr.strip()[:120]}")
    return False, f"unknown step {s}"


def recover():
    """Try every saved step; the failed ones stay in the file for the next run. (all ok, lines)."""
    plan = load_plan()
    if not plan:
        return True, []
    left, lines = [], []
    for s in plan:
        ok, line = _undo(s)
        lines.append(("put back: " if ok else "NOT put back: ") + line)
        if not ok:
            left.append(s)
    save_plan(left)
    return not left, lines


# ---------------------------------------------------------------- the table of what stands in the way

def classify(reason):
    """(kind, what to do) for a preflight reason."""
    if reason.startswith("session "):
        return "session", "end it and start a new process (/reload-plugins and /restart do not count: they keep the pid and start time, and only a process that started after the update passes)"
    if reason.startswith("pid "):
        return "process", "let it end, or end the session that started it; the next run sees it gone"
    if reason.startswith("LaunchAgent "):
        return "LaunchAgent", "this command stops it itself"
    if reason.startswith("crontab"):
        return "cron", "comment the line out with `crontab -e`, then run again"
    return "other", "see the text; fix it, then run again"


def table(reasons, own=()):
    rows = [(*classify(r), r) for r in reasons if r not in own]
    if not rows:
        return ""
    width = max(len(k) for k, _, _ in rows)
    return "\n".join(f"  {k.ljust(width)}  {r}\n  {' ' * width}  -> {what}" for k, what, r in rows)


def guard_owner():
    """The stop and start acts are the owner's: refuse inside a Claude session (its own marker, and any session among
    this process's ancestors). A guard against doing it by mistake; it cannot stop a determined caller."""
    if os.environ.get("CLAUDECODE"):
        raise store.Refused("run this from a plain terminal, not from a Claude session: the owner runs it")
    try:
        procs = fleet.ps_table()
    except fleet.SourceError as e:
        raise store.Refused(f"cannot tell whether this runs inside a session: {e}") from e
    mine = store._ancestors(procs)
    if any(p in mine for s in fleet.live_sessions(procs).values() for p in s.get("pids") or []):
        raise store.Refused("run this from a plain terminal, not from a Claude session: the owner runs it")


# ---------------------------------------------------------------- the run

def check_only():
    state, detail = status()
    print(f"state: {state} ({detail})")
    if state != "pending":
        return 0 if state == "migrated" else 1
    steps = snapshot()
    for s in steps:
        print("would stop: " + (f"LaunchAgent {s['label']}" if s["do"] == "bootstrap" else f"automation {s['name']}"))
    why = store.preflight()
    own = [r for r in why if r.startswith("LaunchAgent ")]
    t = table(why, own)
    try:  # the import refuses on these too, so a --check that skipped them would promise a run that fails
        store._check_sources(fleet.state_dir(), store.BOARD_STEP, store.peek_steps()[1])
    except store.Refused as e:
        print(f"blocked by the data:\n{e}")
        if t:
            print("blocked by:\n" + t)
        return 1
    print("blocked by:\n" + t if t else "nothing stands in the way: `" + store.migrate_command() + "` would run the import")
    return 1 if t else 0


def run():
    state, detail = status()
    if state == "conflict":
        raise store.Refused(detail)
    if state == "migrated":
        print(f"already migrated: nothing was changed ({detail})")
        return 0
    guard_owner()
    plan = load_plan()
    have = {_key(s) for s in plan}
    plan += [s for s in snapshot() if _key(s) not in have]
    note_server(plan)
    save_plan(plan)  # before the first stop: a killed run leaves the way back
    for s in plan:
        if s["do"] != "server":
            _stop_one(s)
    stop_server(plan)
    print("stopped: " + (", ".join(s.get("label") or s.get("name") or "dashboard server" for s in plan) or "nothing was running"))
    why = store.preflight()
    if why:
        print("refused: old code could still write the state. Nothing was imported; what was stopped is put back.\n" + table(why))
        return 1
    stop_server(plan)  # once more: a server that started between the first check and now is stopped before the import
    print(store.import_apply())
    state, detail = status(_after_import)
    if state != "migrated":
        raise store.Refused(f"the import ended but the state reads {state}: {detail}")
    print("migration: " + ", ".join(f"{k} {v}" for k, v in store.migrated_steps().items()))
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), "board", "show"], capture_output=True, text=True)
    if r.returncode:
        raise store.Refused(f"`orch board show` fails after the import: {(r.stderr or r.stdout).strip()[:200]}")
    print(f"orch board show: ok ({len(r.stdout.splitlines())} lines)")
    print("state step: " + store.state_setup(auto=True))  # acks and the stage table, so a fresh migration ends in one place
    return 0


def main(argv):
    bad = [a for a in argv if a != "--check"]
    if bad:
        sys.exit("usage: orch db migrate [--check]")
    if why := ensure_env("migrate", argv):
        print(why, file=sys.stderr)
        return 1
    try:
        if argv:
            return check_only()
    except store.Refused as e:
        print(e, file=sys.stderr)
        return 1
    try:
        with store.cutover_lock(store.RUN_LOCK):  # the whole run: a second migrate cannot read this plan while it is put back
            code = 1
            try:
                # Before cutover.lock: once it is held a running server ends itself, and one that is gone leaves no trace.
                seen = load_plan()
                if note_server(seen) and seen:
                    save_plan(seen)
                with store.cutover_lock():  # the dashboard server stays down while this one is held
                    code = run()
            except (Exception, KeyboardInterrupt) as e:  # whatever went wrong, what was stopped is put back below
                print(f"stopped: {e}" if str(e) else "interrupted", file=sys.stderr)
            try:
                add_yielded_server()  # a server that ended itself under the lock, wherever the probes above fell
            except (Exception, KeyboardInterrupt) as e:  # the other services are still put back; the mark stays for next time
                print(f"could not read the dashboard's mark: {e}" if str(e) else "interrupted", file=sys.stderr)
                code = code or 1
            ok, lines = recover()  # cutover.lock is released: the dashboard may start again now
            for line in lines:
                print(line)
            if not ok:
                print("some services were not put back; run `orch db migrate` again to retry them", file=sys.stderr)
            elif code:
                print("run it again after fixing the cause above: it carries on from where it stopped")
            return code or (0 if ok else 1)
    except store.Refused as e:  # cutover-run.lock is held by another run
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
