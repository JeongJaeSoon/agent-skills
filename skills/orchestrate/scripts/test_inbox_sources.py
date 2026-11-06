"""SSOT PR 6: the Inbox reads pressure-queue.json and patrol.jsonl itself, `migrate-inputs` retires the old copies, the
dashboard's typed lines are sent-log rows, and a transcript send with no record is a warning. Everything runs under
a throwaway state directory."""
import datetime as dt, json, os, pathlib, stat, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-inbox-sources-"))
os.environ.update(AGENT_SKILLS_STATE=str(tmp), ORCH_FLEET_STATE=str(tmp / "dashboard"), HOME=str(tmp / "home"))
import fleet, sent  # noqa: E402

D = tmp / "dashboard"
NOW = dt.datetime(2026, 11, 6, 12, 0, tzinfo=dt.timezone.utc)
T = NOW.timestamp()


def queue(*entries):
    fleet.write_atomic(tmp / "pressure-queue.json", json.dumps({e["id"]: e for e in entries}))


def q(i, state, **kw):
    return {"id": f"q-{i}", "session": f"s{i}", "command": "docker compose up", "what": "docker compose up", "why": "load 2.3",
            "added": T - 60 * i, "deadline": T + 600, "attempts": 1, "state": state, **kw}


# --- pressure: every waiting entry is one item, whatever the number; gave_up and failed ask the human one by one.
queue(q(1, "waiting"), q(2, "retrying"), q(3, "done"), q(4, "gave_up", ended=T), q(5, "failed", error="Exit code 1", ended=T))
mem = {}
items = fleet.pressure_items({}, mem)
by_key = {i["key"]: i for i in items}
wait_key = next(k for k in by_key if k.startswith(fleet.PRESSURE_WAIT_PREFIX))
assert sorted(by_key) == sorted([wait_key, "ext:pressure:queue:q-4", "ext:pressure:queue:q-5"]), sorted(by_key)
assert by_key[wait_key]["title"].startswith("기계 대기 2건"), by_key[wait_key]["title"]
assert "포기" in by_key["ext:pressure:queue:q-4"]["title"] and "재시도 실패: Exit code 1" in by_key["ext:pressure:queue:q-5"]["title"]
assert all(i["type"] == "pc_pressure" and i["command"].endswith("pressure.py queue") for i in items)
assert not (D / "inbox-external.jsonl").exists(), "reading the queue writes nothing"
assert [s["ids"] for s in mem["pressure_sets"].values()] == [["q-1", "q-2"]]
# A page left open while the set changed many times can still dismiss what it drew: sets are kept by age, not by count.
old = wait_key
for n in range(60):
    queue(q(1, "waiting"), q(2, "retrying"), *[q(100 + i, "waiting") for i in range(n)])
    fleet.pressure_items({}, mem)
queue(q(1, "waiting"), q(2, "retrying"), q(3, "done"), q(4, "gave_up", ended=T), q(5, "failed", error="Exit code 1", ended=T))
mem["dismissed"] = {old: "2026-11-06T11:00:00Z"}
fleet.pressure_items({}, mem)
assert set(mem["dismissed"]) == {"ext:pressure:queue:q-1", "ext:pressure:queue:q-2"}, mem["dismissed"]
mem = {}
fleet.pressure_items({}, mem)

# --- suppression is by queue id. A closure of one entry lowers the count; dismissing the one item dismisses exactly the
# entries it counted, so an entry that arrives later shows ("기계 대기 2건": one closed, one open, a new one).
items = fleet.pressure_items({"pressure:queue:q-1": {"state": "done", "ts": fleet.iso(NOW)}}, {})
assert next(i for i in items if i["key"].startswith(fleet.PRESSURE_WAIT_PREFIX))["title"].startswith("기계 대기 1건")
at = "2026-11-06T11:00:00Z"
mem["dismissed"] = {wait_key: at}
items = fleet.pressure_items({}, mem)
assert [i["key"] for i in items] == ["ext:pressure:queue:q-4", "ext:pressure:queue:q-5"], items
assert mem["dismissed"] == {"ext:pressure:queue:q-1": at, "ext:pressure:queue:q-2": at} and mem["pressure_sets"] == {}, mem
# B joins after the page drew {A}; the collector draws {A, B} (another key) before the click on {A} is handled: B shows.
queue(q(1, "waiting"))
mem = {}
key_a = fleet.pressure_items({}, mem)[0]["key"]
queue(q(1, "waiting"), q(6, "waiting"))
fleet.pressure_items({}, mem)
mem["dismissed"] = {key_a: at}
items = fleet.pressure_items({}, mem)
assert list(mem["dismissed"]) == ["ext:pressure:queue:q-1"] and len(items) == 1 and items[0]["title"].startswith("기계 대기 1건"), (mem, items)
# A, drawn then dismissed, moved on to gave_up meanwhile: its dismissal still follows the set, so it does not come back alone.
queue(q(1, "waiting"))
mem = {}
key_a = fleet.pressure_items({}, mem)[0]["key"]
queue(q(1, "gave_up", ended=T))
mem["dismissed"] = {key_a: at}
assert fleet.pressure_items({}, mem) == [] and mem["dismissed"] == {"ext:pressure:queue:q-1": at}
queue(q(1, "waiting"), q(2, "retrying"), q(6, "waiting"))
items = fleet.pressure_items({"pressure:queue:q-1": {"state": "done", "ts": fleet.iso(NOW)}}, {"dismissed": {"ext:pressure:queue:q-2": "x"}})
assert len(items) == 1 and items[0]["title"].startswith("기계 대기 1건") and "docker compose up" in items[0]["title"], items
fleet.write_atomic(tmp / "pressure-queue.json", json.dumps({"a": q(1, "waiting"), "b": {"id": 7, "what": "x", "state": "waiting"}, "c": "junk"}))
assert fleet.pressure_items({}, {})[0]["title"].startswith("기계 대기 1건"), "a row that is no whole entry costs only itself"
(tmp / "pressure-queue.json").unlink()
assert fleet.pressure_items({}, {}) == []

# --- patrol: the open, unacked X/E/L/O findings, one key per occurrence; a closure decides as for any ext: item.
log = tmp / "patrol.jsonl"
for r in ({"op": "found", "id": "Xa", "code": "X", "card": "/w/a", "title": "exited", "session": "wt-a", "detected_at": "2026-11-06T10:00:00Z"},
          {"op": "found", "id": "Ib", "code": "I", "card": "/w/b", "title": "idle", "detected_at": "2026-11-06T10:00:00Z"},
          {"op": "found", "id": "Lc", "code": "L", "card": "/w/c", "title": "load", "detected_at": "2026-11-06T10:05:00Z"},
          {"op": "found", "id": "Od", "code": "O", "card": "/w/d", "title": "orphan", "detected_at": "2026-11-06T10:06:00Z"},
          {"op": "ack", "id": "Od", "at": "2026-11-06T10:07:00Z"}):
    fleet.append_line(log, r)
pi = fleet.patrol_items({})
assert [i["key"] for i in pi] == ["ext:patrol:Xa:2026-11-06T10:00:00Z", "ext:patrol:Lc:2026-11-06T10:05:00Z"], pi
assert pi[0]["session"] == "wt-a" and pi[0]["command"] == "orch patrol ack Xa" and pi[0]["at"] == "2026-11-06T10:00:00Z"
assert [i["key"] for i in fleet.patrol_items({"patrol:Xa:2026-11-06T10:00:00Z": {"state": "done", "ts": "x"}})] == [pi[1]["key"]]
re = fleet.patrol_items({"patrol:Xa:2026-11-06T10:00:00Z": {"state": "reopen", "ts": "2026-11-06T11:30:00Z"}})
assert re[0]["at"] == "2026-11-06T11:30:00Z", "a reopen shows it from the reopen's time"
fleet.append_line(log, {"op": "clear", "id": "Xa", "at": "2026-11-06T10:30:00Z"})
fleet.append_line(log, {"op": "found", "id": "Xa", "code": "X", "card": "/w/a", "title": "again", "detected_at": "2026-11-06T11:00:00Z"})
assert [i["key"] for i in fleet.patrol_items({})][0] == "ext:patrol:Lc:2026-11-06T10:05:00Z" and \
    "ext:patrol:Xa:2026-11-06T11:00:00Z" in [i["key"] for i in fleet.patrol_items({})], "after a clear, the same id is a new occurrence"

# --- a closed item can be reopened while its source still holds it (no inbox line is needed or written).
import tidy  # noqa: E402
assert tidy.no_reopen("ext:patrol:Lc:2026-11-06T10:05:00Z") is None and tidy.no_reopen("ext:patrol:Gone:2026-11-01T00:00:00Z")
queue(q(1, "waiting"))
assert tidy.no_reopen("ext:pressure:queue:q-1") is None and tidy.no_reopen("ext:pressure:queue:q-404")
(tmp / "pressure-queue.json").unlink()

# --- the old copies in inbox-external.jsonl: not shown, and `migrate-inputs` resolves the open ones (reason migrated).
inbox = D / "inbox-external.jsonl"
for key in ("pressure:queue:q-9", "patrol:Gone:2026-11-01T00:00:00Z", "pressure:danger"):
    fleet.inbox_add("pc_pressure" if key.startswith("pressure") else "patrol", f"old {key}", key=key, source="x")
fleet.inbox_resolve("pressure:queue:q-9")
fleet.inbox_add("pc_pressure", "old again", key="pressure:queue:q-9", source="x")
before = inbox.read_text()
shown = [i["key"] for i in fleet.external_items()]
assert "ext:pressure:danger" in shown and not any(k.startswith(("ext:pressure:queue", "ext:patrol:Gone")) for k in shown), shown
dry = fleet.migrate_inputs()
assert dry["inbox_resolved"] == 2 and inbox.read_text() == before, "a dry run writes nothing"
done = fleet.migrate_inputs(apply=True)
rows = [json.loads(l) for l in inbox.read_text().splitlines()[len(before.splitlines()):]]
assert done["inbox_resolved"] == 2 and sorted(r["key"] for r in rows) == ["patrol:Gone:2026-11-01T00:00:00Z", "pressure:queue:q-9"], rows
assert all(r["op"] == "resolve" and r["reason"] == "migrated" for r in rows), rows
assert fleet.migrate_inputs(apply=True)["inbox_resolved"] == 0, "run again: nothing left"
assert "ext:pressure:danger" in [i["key"] for i in fleet.external_items()], "other external items are untouched"

# --- sends.jsonl: typed lines become sent-log rows, the rest stays an action record, the file is archived read-only.
sends = D / "sends.jsonl"
at = fleet.iso(NOW - dt.timedelta(minutes=5))
fleet.append_line(sends, {"at": at, "session": "wt-x", "handle": "term_x", "text": "use CSV", "ok": True, "typed": True, "code": 200})
fleet.append_line(sends, {"at": at, "session": "wt-x", "handle": "term_x", "text": "no", "ok": False, "typed": False, "code": 200})
fleet.append_line(sends, {"at": at, "session": "wt-x", "handle": None, "text": "open", "ok": True, "code": 200})
fleet.append_line(sends, {"typed": True, "at": 5, "text": "x"})
fleet.append_line(sends, {"typed": True, "at": at, "session": "wt-x", "text": None})
assert sent.import_sends() == {"typed": 1, "added": 1, "skipped": 0} and not (D / "sent").exists(), "dry run"
assert sent.import_sends(apply=True) == {"typed": 1, "added": 1, "skipped": 0}
archive = D / "sends.archive.jsonl"
assert archive.exists() and not sends.exists() and stat.S_IMODE(archive.stat().st_mode) == 0o400
fleet.append_line(sends, {"at": at, "session": "wt-x", "handle": "term_x", "text": "use CSV", "ok": True, "typed": True, "code": 200})
assert sent.import_sends(apply=True) == {"typed": 1, "added": 0, "skipped": 1}, "a line already in the log is not added twice"
assert archive.exists() and sends.exists(), "an archive already there is not replaced"
# A typed line the page appended after the rename (its log write failed) is in the new sends.jsonl: the next run takes it.
at2 = fleet.iso(NOW - dt.timedelta(minutes=3))
fleet.append_line(sends, {"at": at2, "session": "wt-x", "handle": "term_x", "text": "late fallback", "ok": True, "typed": True, "code": 200})
assert sent.import_sends(apply=True) == {"typed": 2, "added": 1, "skipped": 1}
assert [r["text"] for r in sent.read_log(NOW - dt.timedelta(days=1))] == ["use CSV", "late fallback"]
assert archive.read_text().count("use CSV") == 1, "the archive was not touched"
rows = sent.read_log(NOW - dt.timedelta(days=1))
assert [(r["from"], r["to"], r["kind"], r["channel"], r["text"], r["ts"]) for r in rows] == [
    ("dashboard", "wt-x", "dashboard", "term_x", "use CSV", at),
    ("dashboard", "wt-x", "dashboard", "term_x", "late fallback", at2)], rows

# --- Sent: the log and Orca make rows; a transcript copy folds in or is only counted.
tr = {"ts": at, "to": "wt-x", "text": "use CSV", "channel": "Agent", "source": "transcript"}
lost = {"ts": at, "to": "card-q", "text": "nobody logged this", "channel": "Agent", "source": "transcript"}
page = sent.page([tr, lost], days=1, now=NOW)
assert [(r["text"], r["source"], r.get("also")) for r in page["rows"]] == [
    ("late fallback", "log", None), ("use CSV", "log", ["transcript"])], page["rows"]
assert page["unrecorded"] == 1 and page["total"] == 2, page
assert sent.page([], days=1, now=NOW)["unrecorded"] == 0
# A Run Task's spec is Orca's record too: the transcript copy of the same brief folds into it.
task = {"ts": at, "from": "term_c", "to": "task:t1", "kind": "worker", "text": "brief for the worker", "source": "orca"}
page = sent.page([task, {**tr, "to": "task:t1", "text": "brief for the worker"}], days=1, now=NOW)
assert page["unrecorded"] == 0 and [r["also"] for r in page["rows"] if r["to"] == "task:t1"] == [["transcript"]], page

# --- pr-events.jsonl: nothing writes it; `migrate-inputs` makes the old file read-only.
old = D / "pr-events.jsonl"
old.write_text('{"v": 1}\n')
assert fleet.migrate_inputs()["archived"] == ["pr-events.jsonl"] and stat.S_IMODE(old.stat().st_mode) == 0o644
fleet.migrate_inputs(apply=True)
assert stat.S_IMODE(old.stat().st_mode) == 0o400 and fleet.migrate_inputs(apply=True)["archived"] == []
print("test_inbox_sources: ok")
