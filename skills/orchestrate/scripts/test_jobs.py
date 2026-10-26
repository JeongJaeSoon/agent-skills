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
# The kind the caller names (the request ledger keeps its own), and secrets masked in record and line alike.
jobs.record("requests-reconcile", error="token=ghp_abcdefghijklmnopqrstuvwxyz0123456789", event=events.append, kind="requests_error", now=now)
assert events[-1]["kind"] == "requests_error" and "ghp_" not in events[-1]["text"] and "ghp_" not in jobs.records()["requests-reconcile"]["error"]
# An error too long for one timeline line is cut there only; the record keeps it whole.
long = "x" * 5000
jobs.record("pr-stats", error=long, event=events.append, now=now)
assert events[-1]["text"].endswith("…") and len(events[-1]["text"].encode()) <= jobs.EVENT_TEXT_BYTES + 3 and jobs.records()["pr-stats"]["error"] == long
fleet.append_line(fleet.state_dir() / "events.jsonl", events[-1])  # and it fits under append_line's 4 KiB

# The snapshot: every registered job, a record exactly as written, None for one that never ran.
reg = (("tidy", "Self-clean (tidy)", "after each fleet collect"), ("skill-usage", "Skill usage collect", "every 15m"))
snap = jobs.snapshot(reg)
by = {j["key"]: j for j in snap["jobs"]}
assert by["tidy"]["record"] == jobs.records()["tidy"] and by["tidy"]["runs_in"] == "orch-dash"
assert by["skill-usage"]["record"] is None and by["skill-usage"]["cadence"] == "every 15m"

# skills-sync: the demo's sync.json (ok, 900 s) maps to its record; a stopped sync is an error.
sync = by["skills-sync"]
assert sync["runs_in"] == "launchd" and sync["cadence"] == "every 15m" and sync["record"]["ok"] and sync["record"]["result"] == "up to date"
at = fleet.parse(sync["record"]["at"])
assert fleet.parse(sync["record"]["next_at"]) - at == dt.timedelta(seconds=900)
(root / "sync.json").write_text(json.dumps({"at": "2026-10-27T18:00:00+0900", "ok": False, "interval": 600, "message": "rebase conflict"}))
sync = {j["key"]: j for j in jobs.snapshot(reg)["jobs"]}["skills-sync"]
assert sync["record"]["at"] == "2026-10-27T09:00:00Z" and sync["record"]["error"] == "rebase conflict" and not sync["record"]["ok"]
assert sync["cadence"] == "every 10m" and sync["record"]["next_at"] == "2026-10-27T09:10:00Z"
(root / "sync.json").unlink()
assert {j["key"]: j for j in jobs.snapshot(reg)["jobs"]}["skills-sync"]["record"] is None

# reap-hard: its last pass as reap_hard wrote it; none written, no record.
sys.path.insert(0, str(HERE.parents[1] / "scripts"))
import reap_hard  # noqa: E402
assert reap_hard.LAST.parent == root
reap_hard.LAST.unlink()  # the demo's pass
reap = {j["key"]: j for j in jobs.snapshot(reg)["jobs"]}["reap-hard"]
assert reap["record"] is None and reap["cadence"] == f"every {reap_hard.config()['interval'] // 60}m"
t = time.time()
reap_hard.write_last({"t": t, "at": "x", "acted": 3, "skipped": 0, "failed": 1, "kept": 4, "notes": ["/private/path"], "dark": []})
reap = {j["key"]: j for j in jobs.snapshot(reg)["jobs"]}["reap-hard"]["record"]
assert reap["n"] == 3 and reap["result"] == "closed 3 · kept 4" and reap["error"] == "1 failed" and not reap["ok"]
assert "/private/path" not in json.dumps(reap)  # its notes name local paths; the page never gets them

# Rules come from the constants that apply them.
rules = {r["name"]: r["rule"] for r in snap["rules"]}
assert f"{fleet.EXTERNAL_KEEP_DAYS} days" in rules["Inbox item expiry"] and "24 h" in rules["Brief ✅ fold"]

# The server's registry: every job it records has a row, and the page reads it from /api/fleet/jobs.
import dash  # noqa: E402
keys = [k for k, _, _ in dash.JOBS]
assert keys == ["fleet-tick", "requests-reconcile", "tidy", "relay", "selfcheck", "skill-usage", "pr-stats"]
src = (HERE / "dash.py").read_text() + (HERE / "fleet.py").read_text()
assert all(f'jobs.record("{k}"' in src for k in keys), [k for k in keys if f'jobs.record("{k}"' not in src]
assert "/api/fleet/jobs" in (HERE.parent / "assets" / "dashboard" / "fleet.js").read_text()

# A reap-hard whose config cannot be read is an error on its own row; the rest of the page still loads.
real_config = reap_hard.config
reap_hard.config = lambda: (_ for _ in ()).throw(ValueError("bad interval"))
by = {j["key"]: j for j in jobs.snapshot(reg)["jobs"]}
reap_hard.config = real_config
assert by["reap-hard"]["record"] is None and "bad interval" in by["reap-hard"]["error"], by["reap-hard"]
assert by["tidy"]["record"] == jobs.records()["tidy"] and "error" not in by["tidy"] and "error" not in by["skills-sync"]

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
