"""Offline tests for the 6-state step: acks.jsonl and stages.json moving into agent-skills.db, on invented data.

Each scenario is a machine of its own: a fresh state directory holding old-format files.

Run: python3 test_state.py
"""
import datetime as dt, json, os, pathlib, sqlite3, sys, tempfile

HERE = pathlib.Path(__file__).parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-state-"))
os.environ.update({"ORCH_BRIEF_LAST": str(tmp / "brief-last"), "ORCH_FLEET_CONFIG": str(tmp / "none.json"),
                   "AGENT_SKILLS_STATE": str(tmp / "state"), "AGENT_SKILLS_CONFIG": str(tmp / "none.json"), "TZ": "UTC"})
sys.path.insert(0, str(HERE))
import acks, fleet, stages, store  # noqa: E402

n_machines = 0


def machine(migrated=True):
    """A new machine: its state directory, with the 4a/4b DB when migrated."""
    global n_machines
    n_machines += 1
    sd = tmp / f"pc{n_machines}"
    sd.mkdir()
    os.environ["ORCH_FLEET_STATE"] = str(sd)
    if migrated:
        store.import_apply(checks=False)
    return sd


def jl(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def norm(table):
    """Rows without keys that hold nothing, so a file's `"pr": null` and a missing key compare equal."""
    rows = {k: {f: v for f, v in r.items() if v not in (None, False, {}, [])} for k, r in table["rows"].items()}
    return {"rows": rows, "order": table["order"], "env": table["env"], "next": table["next"]}


OLD = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


def old_machine():
    """A PC on the previous version: acks and a stage table with every shape the file holds."""
    sd = machine()
    stages.add("g", "Release", group=True)
    stages.add("w", "Login rework", pr="Acme/Web#42")
    stages.add("w1", "Session expiry", parent="w", pr="acme/web#43")
    stages.add("old", "Long finished")
    stages.set_cell("w", "dev", "ok", "v1.8.0", by="qa")
    stages.set_cell("w", "prod", "fail", "rolled back")
    stages.set_cell("w1", "merge", "checking")
    stages.set_cell("old", "dev", "na")
    stages.set_env(prod="v1.7.2", dev="v1.8.0")
    stages.set_next(["review acme/web#45", "ship v1.8.0"])
    stages.finish("old")
    # a generated row's id with cells only: an overlay
    store_file = json.loads((sd / "stages.json").read_text())
    store_file["rows"]["acme/auto-row"] = {"id": "acme/auto-row", "cells": {"dev": {"mark": "ok", "evidence": "e", "by": None,
                                           "at": "2026-10-27T15:28:33Z"}}, "overlay": True, "created_at": "2026-10-27T15:28:33Z"}
    store_file["order"].append("acme/auto-row")
    (sd / "stages.json").write_text(json.dumps(store_file))
    jl(sd / "acks.jsonl", [
        {"ev": "ack", "id": "stage:g", "ts": "2026-10-01T00:00:00Z"},
        {"ev": "ack", "id": "board:fleet:abc::/w/t", "ts": "2026-10-02T00:00:00Z"},
        {"ev": "ack", "id": "board:b7", "ts": "2026-10-03T00:00:00Z"},
        {"ev": "unack", "id": "board:b7", "ts": "2026-10-04T00:00:00Z"},
        {"ev": "ack", "id": "req:r7", "ts": "2026-10-05T00:00:00Z"},
        {"ev": "ack", "id": "weird:x", "ts": "2026-10-06T00:00:00Z"},   # a kind nothing writes: counted, not stored
    ])
    return sd


def state_backups(sd):
    """The backup directories of the 6-state step: the ones that hold the two files (the 4a/4b import made others)."""
    return [p for p in sd.glob("backup-db-*") if (p / "acks.jsonl").exists() or (p / "stages.json").exists()]


def db_count(table):
    c = sqlite3.connect(store.path())
    try:
        return c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    finally:
        c.close()


# ---- upgrade: the readers answer the same before and after, the files are tombstoned, a backup holds them
sd = old_machine()
assert not store.state_ready()
acked_before, table_before = acks.acked(), norm(stages.read())
assert "stage:g" in acked_before and "board:b7" not in acked_before and "weird:x" in acked_before
files_before = {n: (sd / n).read_bytes() for n in store.STATE_FILES}

report = store.state_setup()
assert report.startswith("tombstoned acks.jsonl, stages.json: 5 ack events, ") and "skipped ack_bad 1" in report, report
assert store.state_ready()
assert all((sd / n).is_dir() and not any((sd / n).iterdir()) for n in store.STATE_FILES)
(backup,) = state_backups(sd)
assert {n: (backup / n).read_bytes() for n in store.STATE_FILES} == files_before
assert store.legacy_traces() == []
assert acks.acked() == {k: v for k, v in acked_before.items() if k != "weird:x"}
after = norm(stages.read())
assert after == table_before, (after, table_before)
assert after["rows"]["w"]["pr"] == "Acme/Web#42"          # the case the writer used survives
assert after["rows"]["w"]["cells"]["prod"]["mark"] == "fail"  # the whole mark vocabulary, not three
assert after["env"] == {"prod": "v1.7.2", "dev": "v1.8.0", "at": table_before["env"]["at"]}
assert after["next"] == ["review acme/web#45", "ship v1.8.0"]
assert sqlite3.connect(store.path()).execute("SELECT repo FROM pr_ref WHERE number=42").fetchone() == ("acme/web",)

# ---- re-run: nothing changes
assert store.state_setup() == "already imported and tombstoned"
assert db_count("ack_event") == 5 and db_count("stage_row") == len(table_before["rows"])

# ---- writes now go to the tables, through the same functions
written, refused = acks.write(["stage:w"], can={"stage:w": {}})
assert written == ["stage:w"] and refused == [] and "stage:w" in acks.acked()
assert acks.write(["stage:w"], undo=True)[0] == ["stage:w"] and "stage:w" not in acks.acked()
stages.set_cell("w1", "dev", "ok", "v1.8.0")
stages.add("late", "Added after the move")
assert stages.read()["rows"]["w1"]["cells"]["dev"]["mark"] == "ok" and "late" in stages.read()["order"]
stages.finish("late", drop=True)
assert "late" not in stages.read()["rows"] and db_count("stage_row") == len(table_before["rows"])
assert not any((sd / n).is_file() for n in store.STATE_FILES)

# ---- a finished row older than the retention is hidden, still stored, and survives the next change
c = sqlite3.connect(store.path())
c.execute("UPDATE stage_row SET done_at='2020-01-01T00:00:00Z' WHERE id='old'")
c.commit()
c.close()
assert "old" not in stages.read()["rows"]
stages.set_env(dev="v1.9.0")
assert db_count("stage_row") == len(table_before["rows"])

# ---- a writer on the previous version recreates a legacy file: it is detected, not silently accepted
(sd / "acks.jsonl").rmdir()
(sd / "acks.jsonl").write_text("")
assert store.legacy_traces() == ["acks.jsonl (a file again)"], store.legacy_traces()
(sd / "acks.jsonl").unlink()
(sd / "acks.jsonl").mkdir()

# ---- the way back: export, rollback, and the old readers work again; then upgrade once more
exported = tmp / "export"
_, n_events, n_rows = store.state_export(exported)
assert (exported / "acks.jsonl").is_file() and n_events == 7 and n_rows == len(table_before["rows"])
assert "env" in json.loads((exported / "stages.json").read_text())
store.state_rollback(exported)
assert not store.state_ready() and all((sd / n).is_file() for n in store.STATE_FILES)
assert "stage:g" in acks.acked() and acks.acked().get("stage:w") is None
assert stages.read()["rows"]["old"]["done_at"] == "2020-01-01T00:00:00Z" and stages.read()["env"]["dev"] == "v1.9.0"
assert len(state_backups(sd)) == 1
again = store.state_setup()
assert again.startswith("tombstoned") and store.state_ready() and len(state_backups(sd)) == 2
assert "stage:g" in acks.acked() and stages.read()["env"]["dev"] == "v1.9.0"

# ---- a source that does not parse: loud, nothing written, nothing moved
sd = machine()
stages.add("w", "Login rework")
(sd / "stages.json").write_text('{"rows": ')
jl(sd / "acks.jsonl", [{"ev": "ack", "id": "stage:w", "ts": "2026-10-01T00:00:00Z"}])
with open(sd / "acks.jsonl", "a") as f:
    f.write("{not json\n")
before = {n: (sd / n).read_bytes() for n in store.STATE_FILES}
try:
    store.state_setup()
    raise AssertionError("a corrupt source must stop the step")
except store.Refused as e:
    assert "stages.json" in str(e) and "acks.jsonl line 2" in str(e), e
assert {n: (sd / n).read_bytes() for n in store.STATE_FILES} == before and not state_backups(sd)
assert not store.state_ready()

# ---- a fresh machine: no file ever existed. The step is a no-op that leaves working, empty readers
sd = machine()
assert store.state_setup().startswith("tombstoned") and store.state_ready()
assert acks.acked() == {} and stages.read() == {"rows": {}, "order": [], "env": {}, "next": []}
stages.add("first", "Written after the move")
assert list(stages.read()["rows"]) == ["first"]

# ---- a machine whose DB is not migrated yet: setup's automatic call skips it, a manual call says what to run
sd = machine(migrated=False)
assert store.state_setup(auto=True).startswith("skipped: no agent-skills.db")
try:
    store.state_setup()
    raise AssertionError("not migrated must be refused")
except store.NotMigrated as e:
    assert "orch db migrate" in str(e)
assert list(sd.iterdir()) == []

print("ok")
