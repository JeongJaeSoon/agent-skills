"""Offline tests for cutover.py (`orch db migrate`) and `orch db init`: rerun safety, what is stopped and put back,
a failure half way, a killed run, the lock that keeps the dashboard down, and a PC with no state. launchctl and
Orca are replaced by a fake; every state directory is a temporary one with invented data.

Run: python3 test_cutover.py
"""
import contextlib, hashlib, io, json, os, pathlib, plistlib, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts"))
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-cutover-"))
os.environ.update({"ORCH_FLEET_STATE": str(tmp / "a" / "dashboard"), "ORCH_FLEET_CONFIG": str(tmp / "none.json"),
                   "AGENT_SKILLS_CONFIG": str(tmp / "none.json"), "TZ": "UTC", "HOME": str(tmp / "home"),
                   "ORCH_DASH_LABEL": "t.orch-dash", "ORCH_DASH": "off", "ORCH_DASH_PORT": "1"})
os.environ.pop("CLAUDECODE", None)
for k in ("PROGRAMS_HOME", "ORCH_FLEET", "ORCH_SKILLS", "ORCH_PR_STATS", "ORCH_CLAUDE_HOMES"):
    os.environ.pop(k, None)
import automations, bootstrap, cutover, dash, fleet, store  # noqa: E402  (dash first: urllib needs the real sys.platform)

assert store.SYNC_LABEL == bootstrap.LABEL

agents = pathlib.Path(os.environ["HOME"]) / "Library" / "LaunchAgents"
agents.mkdir(parents=True)
for label in ("t.orch-dash", store.SYNC_LABEL):
    (agents / f"{label}.plist").write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": ["/bin/true"]}))
LEGACY = {"next": 2, "decisions": {"d1": {"id": "d1", "title": "Export format", "body": "", "options": [], "recommend": None,
          "link": None, "handle": None, "created_at": "2026-10-01T00:00:00Z", "status": "open"}}}


class World:
    """launchctl, orca and the dashboard server, as far as cutover.py sees them."""

    def __init__(self):
        self.loaded = {"t.orch-dash", store.SYNC_LABEL}
        self.jobs = {"agent-skills janitor": True, "agent-skills janitor-load": False, "agent-skills watch-prs": True}
        self.calls, self.fail = [], set()

    def sh(self, cmd):
        self.calls.append(cmd)
        done = lambda rc=0, out="", err="": __import__("subprocess").CompletedProcess(cmd, rc, out, err)  # noqa: E731
        if cmd[0] == "launchctl":
            if cmd[1] == "print":
                return done(0 if cmd[2].rsplit("/", 1)[-1] in self.loaded else 113)
            label = cmd[2].rsplit("/", 1)[-1] if cmd[1] == "bootout" else plistlib.loads(pathlib.Path(cmd[3]).read_bytes())["Label"]
            if f"{cmd[1]} {label}" in self.fail:
                return done(5, err="fake failure")
            (self.loaded.discard if cmd[1] == "bootout" else self.loaded.add)(label)
            return done()
        if cmd[1:3] == ["automations", "edit"]:
            name = next(n for n in self.jobs if f"id-{n}" == cmd[4])
            if f"edit {name}" in self.fail:
                return done(1, err="fake failure")
            self.jobs[name] = "--enabled" in cmd
            return done(0, "{}")
        return done()

    def listing(self):
        return [{"id": f"id-{n}", "name": n, "enabled": on} for n, on in self.jobs.items()], None

    def plan(self):
        return sorted(self.loaded), dict(self.jobs)


@contextlib.contextmanager
def world(*, reasons=(), state_name=None, legacy=True, guard=False, server=False):
    """A fresh state directory with the fake services up. guard=True keeps the real owner check; server=True keeps
    the real stop of an unmanaged dashboard server."""
    w = World()
    state_name = state_name or f"s{len(list(tmp.iterdir()))}"
    saved = (cutover.sh, automations.orca_automations, store.preflight, cutover.guard_owner, cutover.stop_server,
             sys.platform, os.environ["ORCH_FLEET_STATE"], cutover.note_server)
    cutover.sh, automations.orca_automations = w.sh, w.listing
    store.preflight = lambda: list(reasons)
    if not guard:
        cutover.guard_owner = lambda: None
    if not server:
        cutover.stop_server = lambda plan: None
        cutover.note_server = lambda plan: None
    sys.platform = "darwin"
    os.environ["ORCH_FLEET_STATE"] = str(tmp / state_name / "dashboard")
    sd = fleet.state_dir()
    sd.mkdir(parents=True, exist_ok=True)
    if legacy:
        (sd / "decisions.json").write_text(json.dumps(LEGACY))
    try:
        yield w, sd
    finally:
        (cutover.sh, automations.orca_automations, store.preflight, cutover.guard_owner, cutover.stop_server,
         sys.platform, os.environ["ORCH_FLEET_STATE"], cutover.note_server) = saved


def run(argv=()):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cutover.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def fingerprint(sd):
    return {str(p.relative_to(sd)): hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir"
            for p in sorted(sd.rglob("*")) if "backup-db-" not in str(p) and p.suffix != ".lock"}  # an empty lock file is no state


# ---- a refusal stops nothing for good: the table names what stands in the way, and every service comes back
reasons = ["session s1 (/w/x, home /h) started before the update: end or restart it",
           "pid 42 started Mon before this checkout's update: python3 prog.py wait"]
with world(reasons=reasons) as (w, sd):
    before, want = fingerprint(sd), w.plan()
    code, out, err = run()
    assert code == 1 and "refused" in out and "session" in out and "restart" in out and "/reload-plugins" in out, (out, err)
    assert "process" in out and "Nothing was imported" in out, out
    assert w.plan() == want, (w.plan(), want)  # dashboard, sync, janitor and watch-prs are back as they were
    assert "bootout" in " ".join(" ".join(c) for c in w.calls)  # ...after they were stopped
    assert not store.path().exists() and not (sd / cutover.RECOVER).exists()
    assert (sd / "decisions.json").is_file() and not (sd / "board.json").exists()
    assert all(not (sd / n).is_dir() for n in ("decisions.json",))

# ---- the run: stop, import, check, put back; a second run changes nothing and stops nothing
with world() as (w, sd):
    want = w.plan()
    code, out, err = run()
    assert code == 0 and "stopped:" in out and "orch board show: ok" in out and "put back:" in out, (out, err)
    assert w.plan() == want and not (sd / cutover.RECOVER).exists()
    assert store.migrated_steps() == {"4a-decisions": "tombstoned", "4b-board": "tombstoned"}, store.migrated_steps()
    assert [d["id"] for d in fleet.open_decisions()] == ["d1"]
    n_calls, before = len(w.calls), fingerprint(sd)
    code, out, err = run()
    assert code == 0 and "already migrated" in out and "nothing was changed" in out, (out, err)
    assert not [c for c in w.calls[n_calls:] if c[1] in ("bootout", "bootstrap") or "edit" in c], w.calls[n_calls:]
    assert fingerprint(sd) == before
    # --check on a migrated state: says so, writes nothing (no -wal appears)
    code, out, _ = run(["--check"])
    assert code == 0 and "state: migrated" in out and fingerprint(sd) == before

# ---- --check on a pending state lists what it would stop and writes nothing
with world(reasons=["LaunchAgent t.orch-dash is loaded (...)", "session s1 (/w, home /h) started before the update: end"]) as (w, sd):
    before = fingerprint(sd)
    code, out, _ = run(["--check"])
    assert code == 1 and "would stop: LaunchAgent t.orch-dash" in out and "would stop: automation janitor" in out, out
    assert "blocked by" in out and "session" in out and "this command stops it itself" not in out, out
    assert fingerprint(sd) == before and not [c for c in w.calls if c[1] in ("bootout", "bootstrap") or "edit" in c]

# ---- --check refuses what the import would refuse on the data, and still writes nothing
with world() as (w, sd):
    (sd / "decisions.json").write_text('{"decisions": {"d1": {"id": "x1"}}}')
    before = fingerprint(sd)
    code, out, _ = run(["--check"])
    assert code == 1 and "blocked by the data" in out and "d1: id does not match its key" in out, out
    assert "nothing stands in the way" not in out and fingerprint(sd) == before and not store.path().exists()
    code, out, err = run()  # the real run refuses on the same data, so --check told the truth
    assert code == 1 and "the files hold entries" in err, (out, err)

# ---- the import fails half way (4a committed, 4b raises): services come back, the failure is named, a rerun finishes
with world() as (w, sd):
    want, real = w.plan(), store._import_board
    store._import_board = lambda sd_: (_ for _ in ()).throw(store.Refused("board import broke"))
    try:
        code, out, err = run()
    finally:
        store._import_board = real
    assert code == 1 and "board import broke" in err and w.plan() == want and "run it again" in out, (out, err)
    steps = store.migrated_steps()
    assert steps.get("4a-decisions") and "4b-board" not in steps, steps
    code, out, err = run()
    assert code == 0 and store.migrated_steps() == {"4a-decisions": "tombstoned", "4b-board": "tombstoned"}, (out, err)
    assert w.plan() == want

# ---- a good import is not called failed because the immutable read cannot see a -wal a lingering reader holds open
with world() as (w, sd):
    real_peek = store.peek_steps
    store.peek_steps = lambda: (0, {})  # what immutable=1 shows while the committed steps still sit in the -wal
    try:
        code, out, err = run()
    finally:
        store.peek_steps = real_peek
    assert code == 0 and "orch board show: ok" in out and "the import ended but" not in err, (out, err)
    assert store.migrated_steps() == {"4a-decisions": "tombstoned", "4b-board": "tombstoned"}

# ---- a service that will not come back stays in the plan file, and the next run puts it back first
with world() as (w, sd):
    want = w.plan()
    w.fail.add(f"bootstrap {store.SYNC_LABEL}")
    code, out, err = run()
    assert code == 1 and "NOT put back" in out and "not put back" in err, (out, err)
    assert (sd / cutover.RECOVER).is_file() and store.SYNC_LABEL not in w.loaded
    w.fail.clear()
    code, out, err = run()  # already migrated: nothing to import, but the leftovers are put back
    assert code == 0 and "already migrated" in out and "put back" in out and w.plan() == want, (out, err)
    assert not (sd / cutover.RECOVER).exists()

# ---- a stop that does not take refuses the import before anything is imported, and recovers
with world() as (w, sd):
    want = w.plan()
    w.fail.add("bootout t.orch-dash")
    code, out, err = run()
    assert code == 1 and "still loaded" in err and not store.path().exists() and w.plan() == want, (out, err)

# ---- an automation that was off stays off: only what was on is put back
with world() as (w, sd):
    code, out, err = run()
    assert code == 0 and w.jobs["agent-skills janitor-load"] is False and w.jobs["agent-skills janitor"] is True, w.jobs

# ---- the lock: a second migrate refuses, and the dashboard server does not start while it is held
with world() as (w, sd):
    assert store.cutover_running() is False
    with store.cutover_lock():
        assert store.cutover_running() is True
        code, out, err = run()
        assert code == 1 and "another `orch db migrate` is running" in err and not w.calls, (out, err)
        import dash  # noqa: E402
        ok, msg = dash.ensure(1)
        assert ok is False and msg == dash.MIGRATING, msg
        assert dash.install_agent(1) == (False, dash.MIGRATING)
        try:
            dash.cmd_serve([])
            raise AssertionError("serve started during a migrate")
        except SystemExit as e:
            assert e.code == dash.MIGRATING
    assert store.cutover_running() is False

# ---- a second run while one runs (recovery included) refuses and leaves the first one's plan alone
with world() as (w, sd):
    with store.cutover_lock(store.RUN_LOCK):
        cutover.save_plan([{"do": "bootstrap", "label": "t.orch-dash", "plist": str(agents / "t.orch-dash.plist")}])
        code, out, err = run()
        assert code == 1 and "another `orch db migrate` is running" in err and not w.calls, (out, err)
        assert (sd / cutover.RECOVER).is_file() and not store.path().exists()

# ---- a server that slipped past the start checks ends itself once the lock is held
import dash  # noqa: E402
with world() as (w, sd):
    killed = []
    with store.cutover_lock():
        dash.watch_migrate(poll=0.01, kill=lambda pid, sig: killed.append((pid, sig)))
    assert killed == [(os.getpid(), __import__("signal").SIGTERM)], killed

# ---- a LaunchAgent that is loaded but has no plist to load it from again is not taken down
with world() as (w, sd):
    (agents / f"{store.SYNC_LABEL}.plist").rename(agents / "sync.plist.off")
    try:
        code, out, err = run()
    finally:
        (agents / "sync.plist.off").rename(agents / f"{store.SYNC_LABEL}.plist")
    assert code == 1 and "no plist" in err and "nothing was changed" in err, (out, err)
    assert not [c for c in w.calls if c[1] in ("bootout", "bootstrap") or "edit" in c], w.calls
    assert w.plan() == (sorted({"t.orch-dash", store.SYNC_LABEL}), dict(w.jobs))

# ---- an automation replaced under the recorded name is never touched
with world() as (w, sd):
    cutover.save_plan([{"do": "enable", "name": "janitor", "id": "id-old"}])
    w.jobs["agent-skills janitor"] = False
    ok, lines = cutover.recover()
    assert ok and "gone" in lines[0] and not [c for c in w.calls if "edit" in c], (lines, w.calls)
    assert w.jobs["agent-skills janitor"] is False and not (sd / cutover.RECOVER).exists()

# ---- an automation renamed since it was recorded is found by its id and turned on again
with world() as (w, sd):
    w.jobs["agent-skills janitor"] = False
    cutover.save_plan([{"do": "enable", "name": "janitor", "id": "id-agent-skills janitor"}])
    w.jobs["renamed"] = w.jobs.pop("agent-skills janitor")  # the old name is gone, the id id-renamed is not the recorded one
    cutover.save_plan([{"do": "enable", "name": "janitor", "id": "id-renamed"}])
    ok, lines = cutover.recover()
    assert ok and "on again" in lines[0] and w.jobs["renamed"] is True, (lines, w.jobs)
    # Orca unreadable: the step stays in the plan
    cutover.save_plan([{"do": "enable", "name": "janitor", "id": "id-x"}])
    automations.orca_automations = lambda: (None, "orca down")
    ok, lines = cutover.recover()
    assert not ok and cutover.load_plan(), lines
    automations.orca_automations = w.listing

# ---- a server that ends itself once the lock is held is still put back: it is recorded before the lock
with world(reasons=("old code still runs",)) as (w, sd):
    def seen_up(plan):
        if store.cutover_running():  # the real server ends itself once the lock is held: nothing to see then
            return None
        if not any(s["do"] == "server" for s in plan):
            plan.append({"do": "server", "port": 4999})
        return {"pid": 1}
    real_note, cutover.note_server = cutover.note_server, seen_up
    try:
        code, out, err = run()
    finally:
        cutover.note_server = real_note
    assert code == 1 and any(c[1:] == ["ensure", "--port", "4999"] or c[2:] == ["ensure", "--port", "4999"]
                             for c in w.calls if "dash.py" in " ".join(map(str, c))), w.calls
    assert not (sd / cutover.RECOVER).exists()

# ---- a server that starts after the pre-lock probe is recorded as soon as the lock is held, before the slow steps
with world(reasons=("old code still runs",)) as (w, sd):
    slow = {"done": False}

    def late_up(plan):  # visible only in the moments after the lock is taken; the watcher ends it once the owner check ran
        if slow["done"] or not store.cutover_running():
            return None
        if not any(s["do"] == "server" for s in plan):
            plan.append({"do": "server", "port": 4998})
        return {"pid": 1}
    real_note, real_guard = cutover.note_server, cutover.guard_owner
    cutover.note_server = late_up
    cutover.guard_owner = lambda: slow.update(done=True)
    try:
        code, out, err = run()
    finally:
        cutover.note_server, cutover.guard_owner = real_note, real_guard
    assert code == 1 and any(c[-2:] == ["--port", "4998"] for c in w.calls), w.calls
    assert not (sd / cutover.RECOVER).exists()

# ---- a server that is up when the run starts is recorded before any stop
with world(server=True) as (w, sd):
    real = (dash.health, dash.home, dash.default_port)
    dash.health = lambda port: {"store": str(dash.home()), "pid": 1}
    dash.home = lambda: tmp / "programs"
    dash.default_port = lambda: 4999
    try:
        plan = []
        cutover.note_server(plan)
        assert plan == [{"do": "server", "port": 4999}] and not (sd / cutover.RECOVER).exists()
    finally:
        dash.health, dash.home, dash.default_port = real

# ---- init uses the state directory a LaunchAgent uses, and refuses when the shell names another
with world(legacy=False, state_name="init-env") as (w, sd):
    (agents / "t.orch-dash.plist").write_bytes(plistlib.dumps({"Label": "t.orch-dash", "EnvironmentVariables": {
        "ORCH_FLEET_STATE": str(tmp / "elsewhere")}}))
    try:
        store.main(["init"])
        raise AssertionError("init ran against a state directory the LaunchAgent does not use")
    except SystemExit as e:
        assert "ORCH_FLEET_STATE" in str(e.code), e.code
    finally:
        (agents / "t.orch-dash.plist").write_bytes(plistlib.dumps({"Label": "t.orch-dash", "ProgramArguments": ["/bin/true"]}))
    assert not store.path().exists()

# ---- a server that `ensure` started is stopped, checked, and started again
with world(server=True) as (w, sd):
    import dash  # noqa: E402
    up = {"on": True}
    real = (dash.health, dash.stop, dash.home, dash.default_port)
    stops = []
    dash.health = lambda port: {"store": str(dash.home()), "pid": 1} if up["on"] else None
    dash.stop = lambda h, port: (stops.append(port), up.update(on=False))
    dash.home = lambda: tmp / "programs"
    dash.default_port = lambda: 4999
    try:
        plan = []
        cutover.stop_server(plan)
        assert stops == [4999] and plan == [{"do": "server", "port": 4999}] and (sd / cutover.RECOVER).is_file()
        ok, line = cutover._undo(plan[0])
        assert ok and "4999" in line and w.calls[-1][1].endswith("dash.py") and w.calls[-1][2:] == ["ensure", "--port", "4999"]
        up["on"] = True  # one that will not stop is refused
        dash.stop = lambda h, port: None
        try:
            cutover.stop_server([])
            raise AssertionError("a server that stayed up was accepted")
        except store.Refused as e:
            assert "still up" in str(e)
    finally:
        dash.health, dash.stop, dash.home, dash.default_port = real

# ---- the guard: the owner's command refuses inside a Claude session, before it stops anything
with world(guard=True) as (w, sd):
    os.environ["CLAUDECODE"] = "1"
    try:
        code, out, err = run()
    finally:
        del os.environ["CLAUDECODE"]
    assert code == 1 and "plain terminal" in err and not [c for c in w.calls if c[1] in ("bootout", "bootstrap")], (out, err)

# ---- the dashboard's env comes from its plist when this shell lacks it, and a mismatch refuses
with world() as (w, sd):
    plist = agents / "t.orch-dash.plist"
    plist.write_bytes(plistlib.dumps({"Label": "t.orch-dash", "EnvironmentVariables": {
        "ORCH_FLEET_STATE": os.environ["ORCH_FLEET_STATE"], "PATH": "/usr/bin"}}))
    add, bad = cutover.settle_env()
    assert add == {} and bad == [], (add, bad)  # the shell has the same state directory
    plist.write_bytes(plistlib.dumps({"Label": "t.orch-dash", "EnvironmentVariables": {"ORCH_FLEET_STATE": str(tmp / "other")}}))
    add, bad = cutover.settle_env()
    assert add == {} and len(bad) == 1 and "ORCH_FLEET_STATE" in bad[0] and str(tmp) not in bad[0], (add, bad)
    code, out, err = run()
    assert code == 1 and "ORCH_FLEET_STATE" in err + out, (out, err)
    saved = os.environ.pop("ORCH_FLEET_STATE")
    try:
        add, bad = cutover.settle_env()
        assert add == {"ORCH_FLEET_STATE": str(tmp / "other")} and bad == [], (add, bad)
    finally:
        os.environ["ORCH_FLEET_STATE"] = saved
    plist.write_bytes(plistlib.dumps({"Label": "t.orch-dash", "ProgramArguments": ["/bin/true"]}))

# ---- a migrated DB with a legacy file back where the tombstone was: a diagnosis, not "already migrated"
with world() as (w, sd):
    assert run()[0] == 0
    n = len(w.calls)
    (sd / "board.json").rmdir()
    (sd / "board.json").write_text("{}")
    code, out, err = run()
    assert code == 1 and "a file again or gone" in err and len(w.calls) == n, (out, err)

# ---- a DB from a newer schema is refused, never touched
with world(legacy=False) as (w, sd):
    assert run()[0] == 0
    import sqlite3  # noqa: E402
    db = sqlite3.connect(str(store.path()))
    db.execute(f"PRAGMA user_version={store.SCHEMA_VERSION + 1}")
    db.close()
    n = len(w.calls)
    code, out, err = run()
    assert code == 1 and "newer than this code" in err and len(w.calls) == n, (out, err)

# ---- orch db init: a PC with no state gets an empty DB, both steps done; anything there is left alone
with world(legacy=False, state_name="fresh") as (w, sd):
    assert list(sd.iterdir()) == []
    assert "created an empty" in store.init_empty()
    assert store.migrated_steps() == {"4a-decisions": "tombstoned", "4b-board": "tombstoned"}
    assert cutover.status()[0] == "migrated"
    before = fingerprint(sd)
    assert "skipped" in store.init_empty() and fingerprint(sd) == before  # a second run changes nothing
with world(legacy=True, state_name="used") as (w, sd):
    before = fingerprint(sd)
    assert "skipped" in store.init_empty() and "bin/orch db migrate" in store.init_empty() and fingerprint(sd) == before
    assert not store.path().exists()
with world(legacy=False, state_name="busy") as (w, sd):
    with store.cutover_lock():
        assert "migrate is running" in store.init_empty() and not store.path().exists()

# ---- the owner's line names the main checkout, whichever checkout this code runs from
assert store.migrate_command() == f"{store.main_checkout()}/bin/orch db migrate" and "<" not in store.migrate_command()
assert (store.main_checkout() / "bin" / "orch").is_file()
print("test_cutover: ok")
