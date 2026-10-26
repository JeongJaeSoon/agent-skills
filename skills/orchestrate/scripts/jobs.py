"""What orch-dash does on its own, on a clock, for the dashboard's Automations page: its periodic jobs with their last
run, two jobs outside it that keep their own record, and the rules it applies whenever it reads (no run to record).

Each job records its run when it runs (record()); nothing else writes the file, and a job with no record shows as
none, never an estimated time. One file, <fleet state>/jobs.json (0600, replaced whole under jobs.lock):
  {"<job key>": {"at", "ok", "n", "result", "error", "next_at", "acted_at", "acted_n", "failed_at", "warned_at"}}
n is what the run closed, linked or added (None when the job does not count); acted_* is the last run with n > 0.
next_at is when the job's own loop will run it next at the latest (a wake-up can run it sooner). A failed run puts one
line on the timeline (the fleet's events.jsonl) when the job starts failing or its error changes, not each run it
keeps failing; warned_at says when that line was written.
"""
import contextlib, datetime as dt, fcntl, json, pathlib, sys

import fleet

EVENT_TEXT_MAX = 3000  # fleet.append_line refuses a line over 4 KiB; the record keeps the whole error


def path():
    return fleet.state_dir() / "jobs.json"


@contextlib.contextmanager
def locked():
    path().parent.mkdir(parents=True, exist_ok=True)
    with (path().parent / "jobs.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def records():
    got = fleet.read_json(path(), {})
    return got if isinstance(got, dict) else {}


def record(key, n=None, result=None, error=None, next_in=None, event=None, kind="job_failed", now=None):
    """Write key's run: n counted, result in words, error (None: it ran fine), next_in seconds to its next run.
    event(row) is the fleet's Fleet.event; it gets the warning line. Returns the record."""
    now = now or fleet.utcnow()
    stamp, error = fleet.iso(now), fleet.mask(error)
    with locked():
        recs = records()
        prev = recs.get(key) or {}
        rec = {**prev, "at": stamp, "ok": error is None, "n": n, "result": fleet.mask(result), "error": error,
               "next_at": fleet.iso(now + dt.timedelta(seconds=next_in)) if next_in is not None else None}
        if n:
            rec.update(acted_at=stamp, acted_n=n)
        if error is not None and prev.get("error") != error:
            rec["failed_at"] = stamp
            if event:
                event({"at": stamp, "kind": kind, "session": None, "text": fleet.mask(f"{key}: {error}", EVENT_TEXT_MAX)})
                rec["warned_at"] = stamp
        recs[key] = rec
        fleet.write_atomic(path(), json.dumps(recs, ensure_ascii=False))
    return rec


def sync_job():
    """skills-sync (scripts/sync.py under launchd) writes sync.json each pass."""
    s = fleet.read_json(fleet.state_dir().parent / "sync.json")
    every = (s or {}).get("interval") or 900
    job = {"key": "skills-sync", "name": "Skills sync", "cadence": f"every {every // 60}m", "runs_in": "launchd",
           "record": None}
    if isinstance(s, dict) and (at := fleet.parse(s.get("at"))) and at.tzinfo:
        at = at.astimezone(dt.timezone.utc)  # sync.py writes local time with its offset
        ok, msg = s.get("ok") is not False, fleet.mask(s.get("message"))
        job["record"] = {"at": fleet.iso(at), "ok": ok, "n": None, "result": msg if ok else None, "error": None if ok else msg,
                         "next_at": fleet.iso(at + dt.timedelta(seconds=every))}
    return job


def reap_hard_job():
    """reap-hard (scripts/reap_hard.py under launchd) writes its last pass; read through it, as board.reap_line does."""
    job = {"key": "reap-hard", "name": "reap-hard", "cadence": None, "runs_in": "launchd", "record": None}
    try:
        if (scripts := str(pathlib.Path(__file__).resolve().parents[3] / "scripts")) not in sys.path:  # each poll
            sys.path.insert(0, scripts)
        import reap_hard
    except ImportError:  # an install without the repo's scripts/
        return job
    every = reap_hard.config()["interval"]
    job["cadence"] = f"every {every // 60}m"
    s = reap_hard.read_last()
    if isinstance(s, dict) and isinstance(s.get("t"), (int, float)):
        at = dt.datetime.fromtimestamp(s["t"], dt.timezone.utc)
        error = s.get("error") or (f"{s['failed']} failed" if s.get("failed") else None)
        job["record"] = {"at": fleet.iso(at), "ok": error is None, "n": s.get("acted"), "error": fleet.mask(error),
                         "result": None if s.get("error") else f"closed {s.get('acted', 0)} · kept {s.get('kept', 0)}",
                         "next_at": fleet.iso(at + dt.timedelta(seconds=every))}
    return job


def rules():
    """Applied whenever the data is read or drawn, so they have no run of their own."""
    import board, owner_requests, stages
    lim = {**board.LIMITS, **(fleet.config().get("board") or {})}
    return [
        {"name": "Inbox item expiry", "rule": f"an item added with orch-dash inbox add and never resolved leaves Needs you "
                                              f"{fleet.EXTERNAL_KEEP_DAYS} days after it was added", "applied": "each fleet collect"},
        {"name": "Request expiry", "rule": f"an open request with no link for {owner_requests.TTL_MIN // 60} h expires: still open "
                                           "in the ledger, counted nowhere", "applied": "each read of the request ledger"},
        {"name": "Brief ✅ fold", "rule": f"a ✅ row merged over {lim['done_fold_h']} h ago folds into its repository's "
                                         "\"✅ 그 전 완료 N건\" line", "applied": "each brief render"},
        {"name": "Brief stage table", "rule": f"a stage row finished, or unchanged for {lim['stage_idle_d']} days, leaves the brief's "
                                              "stage table", "applied": "each brief render"},
        {"name": "Work row expiry", "rule": f"a finished Work row leaves the table {stages.DONE_KEEP_S // 86400} days after done",
         "applied": "each read of stages.json"},
    ]


def snapshot(registry):
    """The Automations page's jobs and rules. registry: (key, name, cadence) for each of orch-dash's own jobs."""
    recs = records()
    jobs = [{"key": k, "name": name, "cadence": cadence, "runs_in": "orch-dash", "record": recs.get(k)}
            for k, name, cadence in registry]
    return {"jobs": jobs + [sync_job(), reap_hard_job()], "rules": rules()}  # no timestamp: the page's ETag holds while nothing ran
