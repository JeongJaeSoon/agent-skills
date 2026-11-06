#!/usr/bin/env python3
"""jobs.py: a run's record reaches the Automations snapshot as written, a failing job puts one warning on the
timeline, and a job that never ran shows no record."""
import datetime as dt, json, os, pathlib, sys, tempfile, time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dash_demo  # noqa: E402

root = pathlib.Path(tempfile.mkdtemp(prefix="test-jobs-"))
os.environ.update(dash_demo.build(root))
import fleet, jobs  # noqa: E402

now = dt.datetime(2026, 10, 27, 9, 0, tzinfo=dt.timezone.utc)
events = []

# A run that closed something: its count, its words, and when the loop runs it next.
rec = jobs.record("tidy", n=2, result="closed 2", next_in=60, event=events.append, now=now)
assert rec == {"at": "2026-10-27T09:00:00Z", "ok": True, "n": 2, "result": "closed 2", "error": None,
               "next_at": "2026-10-27T09:01:00Z", "acted_at": "2026-10-27T09:00:00Z", "acted_n": 2,
}, rec
# A later run that closed nothing keeps when the last one that did.
rec = jobs.record("tidy", n=0, result="closed 0", next_in=60, event=events.append, now=now + dt.timedelta(minutes=1))
assert rec["at"] == "2026-10-27T09:01:00Z" and rec["acted_at"] == "2026-10-27T09:00:00Z" and rec["acted_n"] == 2
assert not events

# Failing three runs in a row with the same error is one timeline line; a new error is another.
for i in range(3):
    rec = jobs.record("tidy", error="OSError('disk full')", next_in=60, event=events.append, now=now + dt.timedelta(minutes=2 + i))
assert [e["kind"] for e in events] == ["job_failed"], events
assert events[0] == {"at": "2026-10-27T09:02:00Z", "kind": "job_failed", "session": None, "job": "tidy",
                     "text": "tidy: OSError('disk full')"}
assert not rec["ok"] and rec["failed_at"] == rec["warned_at"] == "2026-10-27T09:02:00Z"
rec = jobs.record("tidy", error="ValueError('bad row')", next_in=60, event=events.append, now=now + dt.timedelta(minutes=6))
assert len(events) == 2 and rec["warned_at"] == "2026-10-27T09:06:00Z"
# Back to fine: the error clears, the last warning stays findable, and a failure after that warns again.
rec = jobs.record("tidy", n=0, result="closed 0", next_in=60, event=events.append, now=now + dt.timedelta(minutes=7))
assert rec["ok"] and rec["error"] is None and rec["warned_at"] == "2026-10-27T09:06:00Z"
jobs.record("tidy", error="ValueError('bad row')", event=events.append, now=now + dt.timedelta(minutes=8))
assert len(events) == 3
# Secrets masked in record and line alike.
jobs.record("relay", error="token=ghp_abcdefghijklmnopqrstuvwxyz0123456789", event=events.append, now=now)
assert events[-1]["kind"] == "job_failed" and "ghp_" not in events[-1]["text"] and "ghp_" not in jobs.records()["relay"]["error"]
# An error too long for one timeline line is cut there only; the record keeps it whole.
long = "x" * 5000
jobs.record("pr-stats", error=long, event=events.append, now=now)
assert events[-1]["text"].endswith("…") and len(events[-1]["text"].encode()) <= jobs.EVENT_TEXT_BYTES + 3 and jobs.records()["pr-stats"]["error"] == long
fleet.append_line(fleet.state_dir() / "events.jsonl", events[-1])  # and it fits under append_line's 4 KiB

# The snapshot: every registered job, a record exactly as written, None for one that never ran.
reg = (("tidy", "Self-clean (tidy)", None, "after each fleet collect", "fleet-tick"),
       ("skill-usage", "Skill usage collect", "15m", "every 15m", None))
snap = jobs.snapshot(reg)
rows = {r["key"]: r for r in snap["automations"]}
by = {j["key"]: j for j in rows["orch-dash"]["jobs"]}
assert by["tidy"]["record"] == jobs.records()["tidy"] and by["tidy"]["runs_in"] == "orch-dash" and by["tidy"]["group"] == "fleet-tick"
assert by["skill-usage"]["record"] is None and by["skill-usage"]["cadence"] == "every 15m" and by["skill-usage"]["every"] == "15m"
assert [jobs.short(s) for s in (10, 90, 300, 900, 6 * 3600)] == ["10s", "90s", "5m", "15m", "6h"]

# One list. The names are the registry's (scripts/automations.py AUTOMATIONS), each once and in its order, and no other:
# not the ones Orca lists beyond them, and not a second row for a job that has its own file (sync, reap-hard).
import automations  # noqa: E402
want = [a.name for a in automations.AUTOMATIONS]
assert [r["key"] for r in snap["automations"]] == want and len(set(want)) == len(want), [r["key"] for r in snap["automations"]]
assert {"janitor", "sync", "reap-hard", "orch-dash", "watch-prs", "preview-watch", "share-server"} <= set(want)
orca = [{"id": "o1", "name": "agent-skills sync", "enabled": True, "rrule": "*/15 * * * *"},
        {"id": "o2", "name": "someone's own automation", "enabled": True},
        {"id": "o3", "name": "sync", "enabled": True}]  # not the registered name: agent-skills sync
got = jobs.snapshot(reg, orca)
rows = {r["key"]: r for r in got["automations"]}
assert [r["key"] for r in got["automations"]] == want and "someone's own" not in json.dumps(got["automations"])
assert rows["sync"]["registered"] is True and rows["sync"]["orca"]["id"] == "o1", rows["sync"]
assert rows["janitor"]["registered"] is False and rows["janitor"]["orca"] is None, "the registry lists it, Orca does not"
assert rows["share-server"]["registered"] is None and rows["share-server"]["job_name"] is None, "a LaunchAgent has no Orca row"
assert all(r["registered"] is None for r in jobs.snapshot(reg, None)["automations"]), "an Orca list not read says nothing is unregistered"
# orch-dash's own jobs open under the automation that runs them, the patrol round under reap-hard; nowhere else.
assert {k for k, r in rows.items() if r["jobs"]} == {"reap-hard", "orch-dash"}
assert [j["key"] for j in rows["reap-hard"]["jobs"]] == ["patrol"] and [j["key"] for j in rows["orch-dash"]["jobs"]] == [k for k, *_ in reg]
# Without scripts/automations.py beside this install there is no list to show, and the page is told why.
real_registry = jobs.registry
jobs.registry = lambda: (_ for _ in ()).throw(ImportError("No module named 'automations'"))
gone = jobs.snapshot(reg, orca)
jobs.registry = real_registry
assert gone["automations"] is None and "automations.py" in gone["error"] and gone["rules"], gone

# patrol: a reap-hard pass starts it, and its verdicts file keeps when the last round ran and whether it was cut short.
def patrol_row():
    return {j["key"]: j for j in jobs.snapshot(reg)["automations"] if j["key"] == "reap-hard"}["reap-hard"]["jobs"][0]
assert patrol_row()["record"] is None and "No round" in patrol_row()["why"]
(root / "patrol-verdicts.json").write_text(json.dumps({"t": 1793948481, "alerts": [1], "reports": [], "sources": {"round": "ok"}}))
rec = patrol_row()["record"]
assert rec["ok"] and rec["result"] == "1 alerts · 0 reports" and rec["at"] == "2026-11-06T07:01:21Z", rec
(root / "patrol-verdicts.json").write_text(json.dumps({"t": 1793948481, "sources": {"round": "timeout"}}))
assert not patrol_row()["record"]["ok"] and "cut short" in patrol_row()["record"]["error"]
(root / "patrol-verdicts.json").unlink()

# sync: Orca's runs are the record; its own file (the demo's sync.json: ok, 900 s) stands in only when Orca has none.
sync = rows["sync"]["record"]
assert sync["ok"] and sync["result"] == "up to date" and jobs.sync_job()["cadence"] == "every 15m"
at = fleet.parse(sync["at"])
assert fleet.parse(sync["next_at"]) - at == dt.timedelta(seconds=900)
(root / "sync.json").write_text(json.dumps({"at": "2026-10-27T18:00:00+0900", "ok": False, "interval": 600, "message": "rebase conflict"}))
sync = {j["key"]: j for j in jobs.snapshot(reg)["automations"]}["sync"]["record"]
assert sync["at"] == "2026-10-27T09:00:00Z" and sync["error"] == "rebase conflict" and not sync["ok"]
assert jobs.sync_job()["cadence"] == "every 10m" and sync["next_at"] == "2026-10-27T09:10:00Z"
(root / "sync.json").unlink()
assert {j["key"]: j for j in jobs.snapshot(reg)["automations"]}["sync"]["record"] is None

# reap-hard: its last pass as reap_hard wrote it; none written, no record.
sys.path.insert(0, str(HERE.parents[1] / "scripts"))
import reap_hard  # noqa: E402
assert reap_hard.LAST.parent == root
reap_hard.LAST.unlink()  # the demo's pass
reap = jobs.reap_hard_job()
assert reap["record"] is None and reap["cadence"] == f"every {reap_hard.config()['interval'] // 60}m" and "not set up" in reap["why"]
assert {j["key"]: j for j in jobs.snapshot(reg)["automations"]}["reap-hard"]["record"] is None
t = time.time()
reap_hard.write_last({"t": t, "at": "x", "acted": 3, "skipped": 0, "failed": 1, "kept": 4, "notes": ["/private/path"], "dark": []})
reap = {j["key"]: j for j in jobs.snapshot(reg)["automations"]}["reap-hard"]["record"]
assert reap["n"] == 3 and reap["result"] == "closed 3 · kept 4" and reap["error"] == "1 failed" and not reap["ok"]
assert "/private/path" not in json.dumps(reap)  # its notes name local paths; the page never gets them

# Rules come from the constants that apply them.
rules = {r["name"]: r["rule"] for r in snap["rules"]}
assert f"{fleet.EXTERNAL_KEEP_DAYS} days" in rules["Inbox item expiry"] and "24 h" in rules["Brief ✅ fold"]

# The server's registry: every job it records has a row, and the page reads it from /api/fleet/jobs.
import dash  # noqa: E402
keys = [k for k, *_ in dash.JOBS]
assert keys == ["fleet-tick", "tidy", "relay", "selfcheck", "skill-usage", "token-usage", "pr-stats"]
src = (HERE / "dash.py").read_text() + (HERE / "fleet.py").read_text()
assert all(f'jobs.record("{k}"' in src for k in keys), [k for k in keys if f'jobs.record("{k}"' not in src]
assert "/api/fleet/jobs" in (HERE.parent / "assets" / "dashboard" / "fleet.js").read_text()

# A reap-hard whose config cannot be read is an error on its own row; the rest of the page still loads.
real_config = reap_hard.config
reap_hard.config = lambda: (_ for _ in ()).throw(ValueError("bad interval"))
rows = {j["key"]: j for j in jobs.snapshot(reg)["automations"]}
reap_hard.config = real_config
assert not rows["reap-hard"]["record"]["ok"] and "bad interval" in rows["reap-hard"]["record"]["error"], rows["reap-hard"]
assert rows["sync"]["record"] is None and rows["orch-dash"]["jobs"][0]["record"] == jobs.records()["tidy"]

# A long error in any script stays under append_line's 4 KiB (it counts bytes, not characters); the record keeps it whole.
wide = "가" * 3000
jobs.record("relay", error=wide, event=events.append, now=now)
fleet.append_line(fleet.state_dir() / "events.jsonl", events[-1])
assert events[-1]["job"] == "relay" and jobs.records()["relay"]["error"] == wide

# A record that cannot be written, or a warning line that cannot be, is reported and swallowed: the job that ran must
# not stop because its bookkeeping did.
import contextlib, io, threading  # noqa: E402
saved = jobs.path().read_text()
jobs.path().unlink()
jobs.path().mkdir()  # the gate's repro: IsADirectoryError on every write
err = io.StringIO()
with contextlib.redirect_stderr(err):
    rec = jobs.record("tidy", error="OSError('x')", event=events.append, now=now)
    jobs.record("tidy", error="OSError('y')", event=lambda row: (_ for _ in ()).throw(ValueError("event line over 4 KiB")), now=now)
assert not rec["ok"] and "jobs.json" in err.getvalue() and "event line over 4 KiB" in err.getvalue(), err.getvalue()

# The fleet loop keeps ticking with jobs.json unwritable; before, the second record() inside an except killed the thread.
class Ticker:
    n = 0
    def tick(self, force=False, viewed=False):
        Ticker.n += 1
        raise RuntimeError("tick fails, so the except block records too")
    def event(self, row):
        pass
dash.FLEET, dash.tidy.run, dash.fleet.relay_decisions, dash.fleet.retry_reloads = Ticker(), list, list, list
dash.CHECK.due = lambda: False
with contextlib.redirect_stderr(io.StringIO()):
    loop = threading.Thread(target=dash.fleet_loop, daemon=True)
    loop.start()
    for _ in range(50):
        if Ticker.n >= 3:
            break
        dash.FLEET_WAKE.set()
        time.sleep(0.05)
    assert loop.is_alive() and Ticker.n >= 3, Ticker.n
jobs.path().rmdir()
jobs.path().write_text(saved)

print("test_jobs: ok")
