"""Offline tests for store.py (agent-skills.db): import and its tombstone, re-runs and conflicts, crashes at each step,
legacy export, backup and restore, WAL, locks and transaction bounds, cutover-plan and the preflight. The board's step
is test_store_board.py's. Everything runs in temporary state directories with invented data.

Run: python3 test_store.py
"""
import contextlib, json, os, pathlib, shutil, sqlite3, stat, subprocess, sys, tempfile, threading, time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-store-"))
os.environ.update({"ORCH_FLEET_STATE": str(tmp / "a" / "dashboard"), "ORCH_FLEET_CONFIG": str(tmp / "none.json"),
                   "AGENT_SKILLS_CONFIG": str(tmp / "none.json"), "TZ": "UTC"})
import fleet, store  # noqa: E402

LEGACY = {"next": 4, "decisions": {
    "d1": {"id": "d1", "title": "Export format", "body": "CSV or JSON", "options": [{"label": "CSV", "description": "sheets"}],
           "recommend": 1, "link": "https://example.test/t/1", "handle": "term_0FAKE1", "created_at": "2026-10-01T00:00:00Z",
           "status": "open", "checked_at": "2026-10-02T00:00:00Z"},
    "d2": {"id": "d2", "title": "Ship today?", "body": "", "options": [], "recommend": None, "link": None, "handle": None,
           "created_at": "2026-10-01T01:00:00Z", "status": "done", "answer": "yes", "closed_at": "2026-10-01T02:00:00Z",
           "delivered": False},
    "d3": {"id": "d3", "title": "Old", "body": "", "options": [], "recommend": None, "link": None, "handle": "term_0FAKE2",
           "created_at": "2026-01-01T00:00:00Z", "status": "dropped", "answer": None, "closed_at": "2026-01-02T00:00:00Z",
           "delivered": True, "delivered_at": "2026-01-02T00:00:01Z"}}}


@contextlib.contextmanager
def state(name):
    """Point the state directory at a fresh one for the block."""
    was = os.environ["ORCH_FLEET_STATE"]
    os.environ["ORCH_FLEET_STATE"] = str(tmp / name / "dashboard")
    fleet.state_dir().mkdir(parents=True, exist_ok=True)
    try:
        yield fleet.state_dir()
    finally:
        os.environ["ORCH_FLEET_STATE"] = was


def legacy(sd, data=LEGACY):
    (sd / "decisions.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    return (sd / "decisions.json").read_bytes()


def orch(*args, env=None):
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), *args], capture_output=True, text=True,
                       env={**os.environ, **(env or {})})
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def raw(sd=None):
    db = sqlite3.connect(str((sd or fleet.state_dir()) / store.DB_NAME))
    db.row_factory = sqlite3.Row
    return db


# ---- import --apply: locks, backup with sha256, one transaction, then the tombstone
with state("import") as sd:
    original = legacy(sd)
    plan = store.import_plan()
    assert (plan["decisions"], plan["open"], plan["next"], plan["problems"], plan["state"]) == (3, 1, 4, [], "not imported"), plan
    assert not store.path().exists(), "the plan creates nothing"
    out = store.import_apply(checks=False)
    assert "imported 3 decisions (next d4)" in out and "tombstoned decisions.json" in out, out
    assert (sd / "decisions.json").is_dir() and not any((sd / "decisions.json").iterdir())
    assert stat.S_IMODE(store.path().stat().st_mode) == 0o600
    db = raw()
    row = dict(db.execute("SELECT * FROM migration WHERE step='4a-decisions'").fetchone())
    backup = pathlib.Path(row["backup"])
    assert row["step"] == "4a-decisions" and row["stage"] == "tombstoned", row
    assert stat.S_IMODE(backup.stat().st_mode) == 0o700 and stat.S_IMODE((backup / "decisions.json").stat().st_mode) == 0o600
    assert (backup / "decisions.json").read_bytes() == original
    assert json.loads(row["sources"]) == {"decisions.json": store.sha256(backup / "decisions.json")}
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal" and db.execute("PRAGMA user_version").fetchone()[0] == 2
    ev = [dict(r) for r in db.execute("SELECT entity, id, ev, after FROM event WHERE id != '4b-board' ORDER BY seq")]
    assert [(e["entity"], e["id"], e["ev"]) for e in ev] == [
        ("decision", "d1", "import"), ("decision", "d2", "import"), ("decision", "d3", "import"),
        ("migration", "4a-decisions", "imported"), ("migration", "4a-decisions", "tombstoned")], ev
    assert json.loads(ev[1]["after"]) == LEGACY["decisions"]["d2"]  # the legacy original, as it was
    db.close()
    # Values come back as the file held them: delivered None / False / True, the same keys.
    assert {d["id"]: d for d in store.decisions()} == LEGACY["decisions"]
    assert [d["id"] for d in fleet.pending_relays()] == ["d2"] and [d["id"] for d in fleet.open_decisions()] == ["d1"]
    # Re-run: nothing more to do, nothing written.
    files = sorted(p.name for p in sd.iterdir())
    assert store.import_apply(checks=False) == "already imported and tombstoned"
    assert sorted(p.name for p in sd.iterdir()) == files

    # decide add / close / keep / list through the CLI, all on the DB; a closed one stays (no 7-day delete).
    env = {"ORCA_TERMINAL_HANDLE": "term_0FAKE1"}
    assert orch("decide", "add", "--title", "Next step", "--option", "Go", env=env) == (0, "d4", "")
    assert orch("decide", "keep", "d4", env=env)[0] == 0
    assert orch("decide", "done", "d4", "--answer", "Go", env=env) == (0, "d4 done", "")
    code, out, _ = orch("decide", "list")
    assert code == 0 and out.splitlines()[0].startswith("d1  ") and "d2  answered on the dashboard" in out, out
    assert {d["id"] for d in store.decisions()} == {"d1", "d2", "d3", "d4"}
    assert [r["ev"] for r in raw().execute("SELECT ev FROM event WHERE id='d4' ORDER BY seq")] == ["add", "keep", "done"]

    # export --legacy --target pre-4a waits for 4b to be undone: code before 4a would have no board at all.
    code, _, err = orch("db", "export", "--legacy", "--target", "pre-4a", str(tmp / "early"))
    assert code == 1 and "rollback 4b" in err and not (tmp / "early").exists(), err

    # backup -> restore elsewhere: a lossless round trip of every table.
    before = store.dump()
    assert orch("db", "backup", str(tmp / "copy.db"))[0] == 0
    assert stat.S_IMODE((tmp / "copy.db").stat().st_mode) == 0o600
with state("restored") as sd2:
    assert store.restore(tmp / "copy.db", checks=False) == store.path()
    assert store.dump() == before and (sd2 / "decisions.json").is_dir()
    assert raw(sd2).execute("PRAGMA integrity_check").fetchone()[0] == "ok"
with state("pre-4a") as sd:  # with 4b not in, the decisions.json the code before 4a reads, what came after included
    legacy(sd)
    store.import_apply(checks=False, upto=store.DECISION_STEP)
    code, out, err = orch("db", "export", "--legacy", "--target", "pre-4a", str(tmp / "export"))
    assert code == 0 and "3 decisions" in out and "wal checkpoint: done" in out, (out, err)
    exported = json.loads((tmp / "export" / "decisions.json").read_text())
    assert exported["next"] == 4 and exported["decisions"] == LEGACY["decisions"]
    assert stat.S_IMODE((tmp / "export" / "decisions.json").stat().st_mode) == 0o600
with state("restore-onto-legacy") as sd3:
    legacy(sd3)
    try:
        store.restore(tmp / "copy.db", checks=False)
        raise AssertionError("restore over a legacy file")
    except store.Refused as e:
        assert "decisions.json is a file here" in str(e) and not store.path().exists(), e

# ---- the access gate: nothing opens, reads or writes a DB `orch db import` did not make
with state("gate") as sd:
    # A DB with the schema but without this code's data step is refused, and decisions.json is not written instead.
    db = sqlite3.connect(str(store.path()))
    db.executescript(store.SCHEMA)
    db.execute(f"PRAGMA user_version={store.SCHEMA_VERSION}")
    db.close()
    for call in (fleet.open_decisions, fleet.pending_relays, lambda: fleet.decision_add("x")):
        try:
            call()
            raise AssertionError(call)
        except store.NotMigrated:
            pass
    assert fleet.relay_decisions() == [] and fleet.decision_items({}, None) == []
    code, _, err = orch("decide", "add", "--title", "x")
    assert code == 1 and "bin/orch db migrate" in err and not (sd / "decisions.json").exists(), err
    try:
        store.open_db()
        raise AssertionError("opened without 4a-decisions")
    except store.NotMigrated as e:
        assert "4a-decisions" in str(e), e
    try:  # an export from it would be an empty decisions.json that old code takes as the whole record
        store.export_legacy(tmp / "gate-export", "pre-4a")
        raise AssertionError("exported without 4a-decisions")
    except store.NotMigrated:
        assert not (tmp / "gate-export").exists()
    try:
        store.import_apply(checks=False)
        raise AssertionError("imported into an unknown DB")
    except store.Refused as e:
        assert "without the 4a-decisions step" in str(e), e

# ---- malformed legacy entries stop the import before anything is written
with state("malformed") as sd:
    legacy(sd, {"next": 2, "decisions": {"d1": {"id": "d1", "status": "maybe", "title": "x", "created_at": "t"},
                                         "d2": {"id": "d2", "status": "open", "title": "y", "created_at": "t", "extra": 1}}})
    try:
        store.import_apply(checks=False)
        raise AssertionError("malformed imported")
    except store.Refused as e:
        assert "d1: status 'maybe'" in str(e) and "d2: fields this table has no column for: extra" in str(e), e
    assert (sd / "decisions.json").is_file() and not store.path().exists()
    assert not [p for p in sd.iterdir() if p.name.startswith("backup-db-")]

# ---- a fresh install: no decisions.json, an empty table, and still a tombstone so older code cannot start a file
with state("fresh") as sd:
    assert "imported 0 decisions (next d1)" in store.import_apply(checks=False)
    assert (sd / "decisions.json").is_dir() and fleet.decision_add("first")["id"] == "d1"

# ---- a crash at each step of import, and what a re-run does
with state("crash-before-commit") as sd:
    original = legacy(sd)
    real = os.replace
    os.replace = lambda *a: (_ for _ in ()).throw(KeyboardInterrupt("killed before the rename"))
    try:
        store.import_apply(checks=False)
    except KeyboardInterrupt:
        pass
    finally:
        os.replace = real
    assert (sd / "decisions.json").read_bytes() == original and not store.path().exists()
    assert not [p for p in sd.iterdir() if ".import-" in p.name], list(sd.iterdir())
    assert "tombstoned" in store.import_apply(checks=False)  # the next run starts over cleanly

for name, touch in (("crash-after-commit", False), ("crash-then-write", True)):
    with state(name) as sd:
        original = legacy(sd)
        real = store._tombstone
        store._tombstone = lambda *a: (_ for _ in ()).throw(KeyboardInterrupt("killed before the tombstone"))
        try:
            store.import_apply(checks=False)
        except KeyboardInterrupt:
            pass
        finally:
            store._tombstone = real
        assert (sd / "decisions.json").read_bytes() == original and store.migrated_steps() == {"4a-decisions": "imported"}
        if touch:  # old code wrote in between: the re-run stops on the conflict and tombstones nothing
            (sd / "decisions.json").write_text(original.decode().replace("Export format", "Export format v2"))
            try:
                store.import_apply(checks=False)
                raise AssertionError("tombstoned over a changed file")
            except store.Refused as e:
                assert "conflict: decisions.json changed after the import" in str(e), e
            assert (sd / "decisions.json").is_file() and store.migrated_steps() == {"4a-decisions": "imported"}
        else:  # same sha256: the re-run only lays the tombstone
            assert store.import_apply(checks=False, upto=store.DECISION_STEP) == "tombstoned decisions.json"
            assert (sd / "decisions.json").is_dir() and store.migrated_steps() == {"4a-decisions": "tombstoned"}
            assert raw().execute("SELECT count(*) FROM decision").fetchone()[0] == 3  # imported once, not twice
            # Old code writing after the cutover is shown, and a re-run refuses to paper over it.
            (sd / "decisions.json").rmdir()
            (sd / "decisions.json").write_text("{}")
            assert store.legacy_traces() == ["decisions.json (a file again)"]
            try:
                store.import_apply(checks=False)
                raise AssertionError("re-run over a trace")
            except store.Refused as e:
                assert "old code wrote after the cutover" in str(e), e

# ---- the tombstone fails older code's writes, and leaves no temporary file
with state("tombstone") as sd:
    store.import_apply(checks=False)
    for name in ("decisions.json", "board.json", "tidy.jsonl"):
        for write in (lambda: fleet.write_atomic(sd / name, "{}"), lambda: fleet.append_line(sd / name, {})):
            try:
                write()
                raise AssertionError("wrote through the tombstone")
            except IsADirectoryError:
                pass
    assert sorted(p.name for p in sd.iterdir() if p.name.startswith(".")) == []

# ---- WAL, the write lock and transaction bounds
with state("tx") as sd:
    store.import_apply(checks=False)
    fleet.decision_add("one")
    # A failure inside change() rolls back the row and its event together.
    try:
        fleet.change_decisions(lambda st: (st["decisions"]["d1"].update(status="done", closed_at="t"), 1 / 0))
    except ZeroDivisionError:
        pass
    assert store.decisions()[0]["status"] == "open" and raw().execute("SELECT count(*) FROM event WHERE id='d1'").fetchone()[0] == 1
    # Deleting a decision is refused, and leaves the table as it was.
    try:
        fleet.change_decisions(lambda st: st["decisions"].clear())
        raise AssertionError("deleted")
    except ValueError:
        pass
    # BEGIN IMMEDIATE: a second writer waits for the lock and fails after its timeout; a reader still reads (WAL).
    held, release = threading.Event(), threading.Event()

    def hold():
        with store.tx():
            held.set()
            release.wait(5)

    t = threading.Thread(target=hold)
    t.start()
    held.wait(5)
    try:
        with store.tx(timeout_ms=200):
            raise AssertionError("two writers at once")
    except sqlite3.OperationalError as e:
        assert "locked" in str(e), e
    assert [d["title"] for d in store.decisions()] == ["one"]  # read during the write
    release.set()
    t.join()
    # Many processes adding at once: every id once, every row with its event.
    procs = [subprocess.Popen([sys.executable, str(HERE / "prog.py"), "decide", "add", "--title", f"p{i}"],
                              stdout=subprocess.PIPE, text=True) for i in range(8)]
    ids = sorted(p.communicate()[0].strip() for p in procs)
    assert all(p.returncode == 0 for p in procs) and ids == sorted(f"d{i}" for i in range(2, 10)), ids
    assert raw().execute("SELECT count(*) FROM event WHERE ev='add'").fetchone()[0] == 9

# ---- cutover-plan prints the owner's commands and runs nothing
with state("plan") as sd:
    calls = []
    real_run, real_sh = subprocess.run, store._sh
    subprocess.run = store._sh = lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("ran"))
    try:
        text = store.cutover_plan("4a")
    finally:
        subprocess.run, store._sh = real_run, real_sh
    assert calls == [] and list(sd.iterdir()) == []
    for line in ("orch db import --dry-run", "orch db import --apply", "orch db backup <file>",
                 "orch db export --legacy --target pre-4a <dir>", "--ff-only"):
        assert line in text, (line, text)
    if sys.platform == "darwin":
        assert f"launchctl bootout gui/{os.getuid()}/{store.launch_labels()[0]}" in text and "orch-dash install-agent" in text
    for step in ("4a", "4b"):  # /restart keeps the pid and start time, so the plan must not offer it
        step3 = next(l for l in store.cutover_plan(step).splitlines() if l.startswith("# 3."))
        assert "end every live Claude session and start a new process" in step3 and "/restart do not count" in step3, step3
    assert "skills-sync setup disable janitor" in store.cutover_plan("4b")
    code, out, _ = orch("db", "cutover-plan", "4a")
    assert code == 0 and out == text and list(sd.iterdir()) == [], out

# ---- preflight: old processes, an old plugin copy, sessions from before the update, a loaded LaunchAgent
import datetime as dt  # noqa: E402
since = dt.datetime(2026, 11, 4, 10, 0, tzinfo=dt.timezone.utc)
repo = store.REPO
procs = {
    101: (1, "Wed Nov  4 09:00:00 2026", f"python3 {repo}/skills/orchestrate/scripts/dash.py serve"),       # before
    102: (1, "Wed Nov  4 11:00:00 2026", f"python3 {repo}/skills/orchestrate/scripts/dash.py serve"),       # after: ok
    103: (1, "Wed Nov  4 11:00:00 2026", "python3 /elsewhere/agent-skills/skills/orchestrate/scripts/prog.py wait"),
    104: (1, "Wed Nov  4 09:00:00 2026", "python3 /other/plugin/hooks/stop.py"),                         # not ours
    105: (1, "Wed Nov  4 09:00:00 2026", "vim notes.md"),
}
procs = {k: (v[0], " ".join(v[1].split()), v[2]) for k, v in procs.items()}
why = store.check_processes(procs, since)
assert len(why) == 2 and "pid 101" in why[0] and "pid 103" in why[1] and "outside this checkout" in why[1], why
# A relative path or -m module resolves against the process's own directory; an unreadable one refuses.
rel = {201: (1, "Wed Nov 4 09:00:00 2026", "python3 prog.py wait"),
       202: (1, "Wed Nov 4 09:00:00 2026", "python3 -m prog wait"),
       203: (1, "Wed Nov 4 09:00:00 2026", "python3 hooks/decision.py"),
       204: (1, "Wed Nov 4 09:00:00 2026", "python3 dash.py serve"),
       205: (1, "Wed Nov 4 11:00:00 2026", "python3 prog.py wait"),
       206: (1, "Wed Nov 4 11:00:00 2026", "python3 -m dash serve"),       # from PYTHONPATH: where is unknown
       207: (1, "Wed Nov 4 09:00:00 2026", "python3 notes/prog.py")}       # someone else's prog.py
cwds = {201: repo / store.SCRIPTS, 202: repo / store.SCRIPTS, 203: repo, 204: None, 205: repo / store.SCRIPTS,
        206: tmp, 207: tmp}
real_cwd, store._cwd = store._cwd, cwds.get
try:
    why = store.check_processes(rel, since)
finally:
    store._cwd = real_cwd
assert [w.split()[1] for w in why] == ["201", "202", "203", "204", "206"] and "cannot be read" in why[3], why
# A ps that fails with no output lists nothing, not even this process: that refuses instead of passing every check.
real_ps, fleet.ps_table = fleet.ps_table, lambda: {}
try:
    assert any("ps did not list this process" in w for w in store.preflight()), store.preflight()
finally:
    fleet.ps_table = real_ps
# crontab is read on every platform, and a line that runs a script directly counts.
# A failed crontab read refuses; "no crontab" is no line at all.
real_sh, real_which = store._sh, store.shutil.which
store.shutil.which = lambda name: "/usr/bin/" + name


def crontab(code, out, err=""):
    store._sh = lambda cmd: subprocess.CompletedProcess(cmd, code if cmd[0] == "crontab" else 1, out, err)
    return [w for w in store.check_launchers() if "crontab" in w]


try:
    assert crontab(0, "* * * * * python3 /x/agent-skills/skills/orchestrate/scripts/dash.py tick\n# orch-dash old\n"
                   "*/5 * * * * cd /x/agent-skills && python3 -m skills.orchestrate.scripts.dash fleet\n") == [
        "crontab line still active: * * * * * python3 /x/agent-skills/skills/orchestrate/scripts/dash.py tick",
        "crontab line still active: */5 * * * * cd /x/agent-skills && python3 -m skills.orchestrate.scripts.dash fleet"]
    assert crontab(1, "", "crontab: no crontab for someone") == []
    assert crontab(127, "", "timed out")[0].startswith("crontab -l failed (127)")
finally:
    store._sh, store.shutil.which = real_sh, real_which
# A session started after the update that loads another checkout with --plugin-dir runs that checkout's hooks.
old_co = tmp / "old-checkout"
(old_co / "skills" / "orchestrate").mkdir(parents=True)
spaced = tmp / "Old Checkout"
(spaced / "skills" / "orchestrate").mkdir(parents=True)
pd = {301: (1, "", f"claude --plugin-dir {old_co} --name x"), 302: (1, "", f"claude --plugin-dir={repo}"),
      303: (1, "", "claude --plugin-dir old-checkout"), 304: (1, "", f"claude --plugin-dir {tmp}"),
      305: (1, "", f"claude --plugin-dir {spaced} --name y")}   # ps shows argv joined by spaces
real_cwd, store._cwd = store._cwd, {303: tmp}.get
try:
    why = store.check_plugin_dirs(pd)
finally:
    store._cwd = real_cwd
assert [w.split()[1] for w in why] == ["301", "303", "305"], why
# A dashboard installed under another label (from another shell's ORCH_DASH_LABEL) is found by what it runs.
agents = tmp / "fakehome" / "Library" / "LaunchAgents"
agents.mkdir(parents=True)
import plistlib  # noqa: E402
(old_co / "bin").mkdir()
for label, spec in {
        "a.orch-dash": {"ProgramArguments": ["python3", "/x/agent-skills/skills/orchestrate/scripts/dash.py", "serve"]},
        "b.program": {"Program": str(old_co / "bin" / "orch-dash"), "ProgramArguments": ["dashboard", "serve"]},
        "c.module": {"ProgramArguments": ["python3", "-m", "dash", "serve"],
                     "WorkingDirectory": str(old_co / "skills" / "orchestrate" / "scripts")},
        "d.shell": {"ProgramArguments": ["/bin/sh", "-c", f"exec python3 {old_co}/hooks/decision.py"]},
        # Not ours: an entry point's file name somewhere else, or in another program's hooks/.
        "e.other": {"ProgramArguments": ["/bin/true"]},
        "f.same-name": {"ProgramArguments": ["python3", "/opt/example/dash.py"]},
        "g.other-hooks": {"ProgramArguments": ["python3", "/opt/example/hooks/refresh.py"]},
        "h.other-bin": {"ProgramArguments": ["/opt/example/bin/orch"]}}.items():
    (agents / f"{label}.plist").write_bytes(plistlib.dumps({"Label": label, **spec}))
(agents / "broken.plist").write_text("not a plist")
real_home = os.environ["HOME"]
os.environ["HOME"] = str(tmp / "fakehome")
try:
    assert store.launch_labels()[1:-1] == ["a.orch-dash", "b.program", "c.module", "d.shell"], store.launch_labels()
    assert store.launch_labels()[-1] == store.SYNC_LABEL  # the sync agent is always a stop target, plist or not
finally:
    os.environ["HOME"] = real_home
# backup never replaces the live DB or its sidecars.
with state("backup-live") as sd:
    store.import_apply(checks=False)
    for name in (store.DB_NAME, store.DB_NAME + "-wal", store.DB_NAME + "-shm"):
        try:
            store.backup(sd / name)
            raise AssertionError(f"backed up over {name}")
        except store.Refused:
            pass
    assert store.decisions() == [] and not list(sd.glob(f".{store.DB_NAME}*.tmp"))
real_live = fleet.live_sessions
fleet.live_sessions = lambda p: {"s-old": {"pids": [102, 101], "cwd": "/w/a", "home": "/h"},
                                 "s-new": {"pids": [102], "cwd": "/w/b", "home": "/h2"}}
try:
    why = store.check_sessions(procs, since)
finally:
    fleet.live_sessions = real_live
assert len(why) == 1 and "s-old" in why[0], why  # one pid from before the update is enough
assert why[0].endswith("end it and start a new process; /restart keeps the pid and start time, so it does not count"), why
home = tmp / "home"
(home / "plugins").mkdir(parents=True)
(home / "plugins" / "installed_plugins.json").write_text(json.dumps({"plugins": {
    "agent-skills@example": [{"installPath": str(tmp / "cache"), "gitCommitSha": "0" * 40}],
    "other@example": [{"installPath": str(tmp / "x"), "gitCommitSha": "1" * 40}]}}))
why = store.check_plugin_source([home])
assert len(why) == 1 and "agent-skills@example" in why[0], why
if sys.platform == "darwin":
    real_sh = store._sh
    store._sh = lambda cmd: subprocess.CompletedProcess(cmd, 0, "state = not running", "")  # loaded, no pid
    try:
        assert "is loaded" in store.check_launchers()[0]
    finally:
        store._sh = real_sh
# Any reason refuses --apply before it touches anything.
with state("refused") as sd:
    legacy(sd)
    real = store.preflight
    store.preflight = lambda: ["pid 101 started before this checkout's update"]
    try:
        store.import_apply()
        raise AssertionError("applied past the preflight")
    except store.Refused as e:
        assert "pid 101" in str(e), e
    finally:
        store.preflight = real
    assert (sd / "decisions.json").is_file() and not store.path().exists() and sorted(p.name for p in sd.iterdir()) == ["decisions.json"]
# --rehearsal skips the preflight only on a state directory other than the default one.
was = os.environ.pop("ORCH_FLEET_STATE")
try:
    store.gate(rehearsal=True)
    raise AssertionError("rehearsal on the default state")
except store.Refused:
    pass
finally:
    os.environ["ORCH_FLEET_STATE"] = was
store.gate(rehearsal=True)

# ---- the registry's DB check: a connection outside store.py, a table changed by a function not listed as its writer
import registry  # noqa: E402
assert registry.db_violations(registry.STORES) == []
got = registry.db_violations(registry.STORES, ([("board", 9)], [("store:dump", "decision", 1), ("store:x", "nope", 2)]))
assert [(k, key) for k, key, _ in got] == [("db", "board:9"), ("db", "store:dump"), ("db", "db:nope")], got
# The scan reads real source: an aliased or indirect import is still an import, SQL in any case is still SQL, and
# prose that only starts like SQL is not.
src = tmp / "scan"
src.mkdir()
(src / "board.py").write_text("import sqlite3 as sql\ndef f():\n    return sql.connect('x')\n")
(src / "tidy.py").write_text("from sqlite3 import connect\n")
(src / "card.py").write_text("import importlib\nm = importlib.import_module(name='sqlite3')\nprint('sqlite3')\n")
(src / "store.py").write_text('def surprise(db):\n    db.execute("update decision set title=?", ("x",))\n'
                              'def upsert(db):\n    db.execute("insert or replace into Counter (name) values (1)")\n'
                              'def schema(db):\n    db.execute("UPDATE main.decision SET title=1")\n'
                              'def bracket(db):\n    db.execute("DELETE FROM [migration] WHERE 1")\n'
                              'def prose():\n    """Update the checkout and restart. Delete the file."""\n')
connects, sql = registry.db_scan(sorted(src.iterdir()))
assert sorted(connects) == [("board", 1), ("card", 2), ("tidy", 1)], connects
assert sorted((w, t) for w, t, _ in sql) == [("store:bracket", "migration"), ("store:schema", "decision"),
                                            ("store:surprise", "decision"), ("store:upsert", "counter")], sql
got = registry.db_violations(registry.STORES, (connects, sql))
assert {key for _, key, _ in got} == {"board:1", "card:2", "tidy:1", "store:surprise", "store:upsert", "store:schema",
                                      "store:bracket"}, got

# ---- no DB yet: decisions.json is the store, and `orch db import` takes over from it
ME = {"ORCA_TERMINAL_HANDLE": "term_0FAKEME"}
with state("before-db") as sd:
    d = fleet.decision_add("Before the DB", handle="term_0FAKE1")
    assert [x["id"] for x in fleet.open_decisions()] == [d["id"]] and not store.path().exists()
    code, out, err = orch("decide", "answer", d["id"], "--quote", "placeholder answer", env=ME)
    assert code == 0 and out.startswith(f"{d['id']} ") and "by term_0FAKEME" in out and '"placeholder answer"' in out, (out, err)
    assert fleet.open_decisions() == [] and json.loads((sd / "decisions.json").read_text())["decisions"][d["id"]]["answered_by"] == "term_0FAKEME"
    code, out, _ = orch("decide", "answer", "--title", "Unasked order", "--quote", "placeholder order", env=ME)
    assert code == 0 and "placeholder order" in out, out
    # No session to name, and an open decision: both print from the file, with no DB to ask.
    bare = {k: v for k, v in os.environ.items() if k != "ORCA_TERMINAL_HANDLE"}
    open_d = fleet.decision_add("Still open")["id"]
    assert orch("decide", "show", open_d)[0] == 0, "show of an open decision before the DB"
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), "decide", "answer", open_d, "--quote", "no session"],
                       capture_output=True, text=True, env=bare)
    assert r.returncode == 0 and "by ?" in r.stdout, r
    fleet.decision_close(open_d, "dropped")
    legacy_ids = [x["id"] for x in store.decisions("done")]
    assert len(legacy_ids) == 2 and store.import_plan()["problems"] == [], legacy_ids
    store.import_apply(checks=False)
    assert (sd / "decisions.json").is_dir() and store.path().is_file()
    assert store.decision_record(d["id"])["answered_by"] == "term_0FAKEME" and store.decision_record(legacy_ids[1])["answered_by"] == "term_0FAKEME"
    # The tombstone is never read as an empty store, nor written.
    assert [x["id"] for x in store.decisions("done")] == legacy_ids and (sd / "decisions.json").is_dir()
    # Added and closed in one write the event is `add`, which show and export read as well.
    code, out, _ = orch("decide", "answer", "--title", "After the DB", "--quote", "placeholder two", env=ME)
    after = out.split()[0]
    assert store.decision_record(after)["answered_by"] == "term_0FAKEME"
    fleet.decision_close(after, "dropped")
    store.export_legacy(tmp / "with-by-4a", "4a")  # pre-4a export refuses a DB that holds 4b: undo 4b first
    store.rollback_board(tmp / "with-by-4a", checks=False)
    out_file, _, _ = store.export_legacy(tmp / "with-by", "pre-4a")
    exported = json.loads(out_file.read_text())["decisions"]
    assert exported[d["id"]]["answered_by"] == "term_0FAKEME" and exported[after]["answered_by"] == "term_0FAKEME", exported
with state("before-db-fresh-roundtrip") as sd:
    fleet.write_atomic(sd / "decisions.json", (tmp / "with-by" / "decisions.json").read_text())
    store.import_apply(checks=False)
    assert store.decision_record(d["id"])["answered_by"] == "term_0FAKEME"

# Corrupt, or missing its directory: never overwritten, and the callers see the same NotMigrated as before.
with state("before-db-corrupt") as sd:
    for bad in ("{not json", '{"decisions": [1]}', '{"decisions": []}', '{"decisions": false}'):
        (sd / "decisions.json").write_text(bad)
        for call in (fleet.open_decisions, lambda: fleet.decision_add("x")):
            try:
                call()
                raise AssertionError(call)
            except store.NotMigrated:
                pass
        assert (sd / "decisions.json").read_text() == bad
    os.chmod(sd / "decisions.json", 0)
    try:
        fleet.open_decisions()
        raise AssertionError("read an unreadable file")
    except store.NotMigrated:
        pass
    finally:
        os.chmod(sd / "decisions.json", 0o600)
os.environ["ORCH_FLEET_STATE"], was = str(tmp / "no-such" / "dashboard"), os.environ["ORCH_FLEET_STATE"]
try:
    fleet.open_decisions()
    raise AssertionError("read from a state directory that is not there")
except store.NotMigrated:
    assert not (tmp / "no-such").exists()
finally:
    os.environ["ORCH_FLEET_STATE"] = was


# A reader or writer that waited on the lock while the import ran goes on to the DB; it never sees the tombstone.
def waits_through_import(call):
    with state("lock-race") as sd:
        shutil.rmtree(sd)
        sd.mkdir(parents=True)
        fleet.decision_add("Raced")
        got, started = [], threading.Event()

        def run():
            started.set()
            got.append(call())
        with store._locks():
            worker = threading.Thread(target=run)
            worker.start()
            started.wait()
            time.sleep(0.2)
            assert not got, "the fallback did not wait for the import's lock"
            real = store._locks
            store._locks = contextlib.nullcontext
            try:
                store.import_apply(checks=False)
            finally:
                store._locks = real
        worker.join(10)
        assert not worker.is_alive() and (sd / "decisions.json").is_dir()
        return got[0]


assert [x["title"] for x in waits_through_import(fleet.open_decisions)] == ["Raced"]
assert waits_through_import(lambda: fleet.decision_add("During"))["id"] == "d2"


# A restore checks for decisions.json under the lock: `orch decide` may have written it just before.
with state("restore-race") as sd:
    store.import_apply(checks=False)
    store.backup(sd / "copy.db")
    store.path().unlink()
    (sd / "decisions.json").rmdir()

    @contextlib.contextmanager
    def writer_got_there_first():
        legacy(sd)
        yield
    real, store._locks = store._locks, writer_got_there_first
    try:
        store.restore(sd / "copy.db", checks=False)
        raise AssertionError("restored over a decisions.json written while it waited")
    except store.Refused as e:
        assert "is a file here" in str(e) and (sd / "decisions.json").is_file(), e
    finally:
        store._locks = real

# ---- `orch decide answer`: the words as given, when, and through which session
with state("answer") as sd:
    store.import_apply(checks=False)
    d = fleet.decision_add("Ship?", handle="term_0FAKE1")["id"]
    words = "first line\n\n  second line  "
    code, out, err = orch("decide", "answer", d, "--quote", words, "--by", "term_0FAKEBY", env=ME)
    rec = store.decision_record(d)
    assert code == 0 and rec["answer"] == words and rec["status"] == "done" and rec["closed_at"], rec
    assert rec["answered_by"] == "term_0FAKEBY" and out == f'{d} {rec["closed_at"]} by term_0FAKEBY: "first line" (+2줄)', out
    code, out, _ = orch("decide", "show", d)
    assert code == 0 and "first line" in out and "second line" in out and "Ship?" in out, out
    code, _, err = orch("decide", "answer", d, "--quote", "again")
    assert code == 1 and "already done" in err and store.decision_record(d)["answer"] == words, err
    assert orch("decide", "show", "d999")[0] == 1
    for bad in (("decide", "answer", "--quote", "no id"), ("decide", "answer", d), ("decide", "answer", "d1", "--title", "T", "--quote", "x")):
        assert orch(*bad)[0] == 1, bad
    # Too long is refused, never cut: a truncated quote is not the owner's words.
    long_d = fleet.decision_add("Long", handle=None)["id"]
    code, _, err = orch("decide", "answer", long_d, "--quote", "x" * (fleet.DECISION_ANSWER_MAX + 1))
    assert code == 1 and "characters" in err and store.decision_record(long_d)["status"] == "open", err
    code, _, _ = orch("decide", "answer", long_d, "--quote", " ")
    assert code == 1 and store.decision_record(long_d)["status"] == "open"
    # Masking lengthens a short secret: the stored text is what must fit, or it would end in a cut.
    grows = "password=a " * 160
    assert len(grows) < fleet.DECISION_ANSWER_MAX < len(fleet.mask(grows))
    assert orch("decide", "answer", long_d, "--quote", grows)[0] == 1 and store.decision_record(long_d)["status"] == "open"
    # --quote-file, and the answer survives a later delivered event.
    (sd / "q.txt").write_text("from a file\n")
    assert orch("decide", "answer", long_d, "--quote-file", str(sd / "q.txt"), env=ME)[0] == 0
    assert store.decision_record(long_d)["answer"] == "from a file\n"
    # Answered on the page and not relayed: the page's answer stays, the relay stops, and a different quote is said so.
    page = fleet.decision_add("On the page", handle="term_0FAKE1")["id"]
    assert fleet.decision_close(page, "done", "page answer", relay=True, by="dashboard")["delivered"] is False
    code, out, err = orch("decide", "answer", page, "--quote", "password=PLACEHOLDER_VALUE", env=ME)
    rec = store.decision_record(page)
    assert code == 0 and rec["answer"] == "page answer" and rec["answered_by"] == "dashboard" and rec["delivered"] is True, rec
    assert '"page answer"' in out and "not stored" in err and "PLACEHOLDER" not in err, (out, err)
    # `done` records who too.
    plain = fleet.decision_add("Plain")["id"]
    assert orch("decide", "done", plain, "--answer", "yes", env=ME)[0] == 0 and store.decision_record(plain)["answered_by"] == "term_0FAKEME"

# ---- brief render: an unregistered --decision is registered, with its id, and only once
ITEM = "캐시를 지울까: 지움 / 유지 → 추천: 유지 — 무료 한도 안 · 답이 없으면 유지"


def brief(*args, env=ME):
    return orch("brief", "render", "--no-refresh", "--no-fill", *args, env=env)


with state("register") as sd:
    store.import_apply(checks=False)
    code, out, err = brief("--decision", ITEM)
    opened = fleet.open_decisions()
    assert code == 0 and len(opened) == 1 and opened[0]["handle"] == "term_0FAKEME" and opened[0]["options"] == [], (out, err, opened)
    did = opened[0]["id"]
    assert f"({did}) {ITEM}" in out and out.count(ITEM) == 1, out
    # A second render, as after sending, registers nothing new.
    code, out, _ = brief("--decision", ITEM, "--commit")
    assert code == 0 and [x["id"] for x in fleet.open_decisions()] == [did] and out.count(ITEM) == 1, out
    # Another session's same words, or a changed recommendation, is another decision.
    brief("--decision", ITEM, env={"ORCA_TERMINAL_HANDLE": "term_0FAKE2"})
    brief("--decision", ITEM.replace("추천: 유지", "추천: 지움"))
    assert len(fleet.open_decisions()) == 3
    # Answered between the render and the next: not asked again, and said so.
    fleet.decision_close(did, "done", "placeholder")
    code, out, err = brief("--decision", ITEM)
    assert code == 0 and did in err and len(fleet.open_decisions()) == 2 and f"({did})" not in out, (out, err)
    # Dropped is no answer: raised again, it is a new decision.
    third = fleet.open_decisions()[-1]["id"]
    fleet.decision_close(third, "dropped")
    brief("--decision", ITEM.replace("추천: 유지", "추천: 지움"))
    assert len(fleet.open_decisions()) == 2 and third not in [x["id"] for x in fleet.open_decisions()]
    # --now replays the past: nothing is registered.
    before = len(store.decisions())
    code, out, _ = orch("brief", "render", "--now", "2026-11-01T00:00:00Z", "--no-fill", "--decision", ITEM + " 재현")
    assert code == 0 and ITEM + " 재현" in out and len(store.decisions()) == before, out
    # A bad form registers nothing, the others included.
    code, _, err = brief("--decision", ITEM + " 앞", "--decision", "추천이 없는 문구")
    assert code == 1 and err.startswith("--decision is one line") and len(store.decisions()) == before, err
    # A label is not made up, so an owner's sentence starting with the recommendation closes nothing by itself.
    assert all(not x["options"] for x in fleet.open_decisions())
    # A long item keeps its recommendation in the report, and a skipped one's answer stays out of stderr.
    long_item = "긴 질문 " * 200 + "→ 추천: 유지 — 끝까지 · 답이 없으면 유지"
    code, out, _ = brief("--decision", long_item)
    assert code == 0 and "답이 없으면 유지" in out and "끝까지" in out, out[-300:]
    long_id = fleet.open_decisions()[-1]["id"]
    fleet.decision_close(long_id, "done", "placeholder quiet")
    code, _, err = brief("--decision", long_item)
    assert code == 0 and long_id in err and "placeholder quiet" not in err, err
    before = len(store.decisions())
    # Too long to hold whole: two items differing past the limit must not become one decision.
    code, out, err = brief("--decision", "x" * (fleet.DECISION_BODY_MAX + 1) + " → 추천: 유지")
    assert code == 1 and "(미등록)" in out and len(store.decisions()) == before, (out[:300], err)

# Registration failed: the report still goes out, with a warning above it, the item marked, and exit 1.
with state("register-fails") as sd:
    db = sqlite3.connect(str(store.path()))
    db.executescript(store.SCHEMA)
    db.close()
    code, out, err = brief("--decision", ITEM)
    lines = out.splitlines()
    assert code == 1 and lines[0].startswith("⚠️ 결정 등록 실패 1건") and f"(미등록) {ITEM}" in out and "오너가 할 일" in out, (out, err)
    code, out, err = brief("--decision", ITEM, "--json")
    assert code == 1 and "결정 등록 실패" in err and f"(미등록) {ITEM}" in json.loads(out)["text"], (out, err)

print("test_store: ok")
