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

LAST = {}  # key -> the error its last run recorded, in this process
EVENT_TEXT_BYTES = 3500  # fleet.append_line refuses a line over 4 KiB; the record keeps the whole error


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


def line_text(text):
    """text cut to fit a timeline line, measured as append_line writes it: JSON-escaped UTF-8 bytes."""
    cut = text
    while len(json.dumps(cut, ensure_ascii=False).encode()) > EVENT_TEXT_BYTES:
        cut = cut[:len(cut) * 9 // 10]
    return cut if cut == text else cut + "…"


def record(key, n=None, result=None, error=None, next_in=None, event=None, kind="job_failed", now=None):
    """Write key's run: n counted, result in words, error (None: it ran fine), next_in seconds to its next run.
    event(row) is the fleet's Fleet.event; it gets the warning line. Returns the record.
    Never raises: a record or warning line it cannot write goes to stderr, and the job that called it carries on."""
    now = now or fleet.utcnow()
    stamp, error = fleet.iso(now), fleet.mask(error)
    rec = {}
    try:
        with locked():
            recs = records()
            prev = recs.get(key) or {}
            rec = {**prev, "at": stamp, "ok": error is None, "n": n, "result": fleet.mask(result), "error": error,
                   "next_at": fleet.iso(now + dt.timedelta(seconds=next_in)) if next_in is not None else None}
            if n:
                rec.update(acted_at=stamp, acted_n=n)
            # LAST covers a file it cannot read back: without it every failing run would warn again
            if error is not None and error not in (prev.get("error"), LAST.get(key)):
                rec["failed_at"] = stamp
                if event:
                    try:
                        event({"at": stamp, "kind": kind, "session": None, "job": key, "text": line_text(f"{key}: {error}")})
                        rec["warned_at"] = stamp
                    except Exception as e:
                        print(f"jobs: could not write {key}'s timeline warning: {e!r}", file=sys.stderr, flush=True)
            LAST[key] = error
            recs[key] = rec
            fleet.write_atomic(path(), json.dumps(recs, ensure_ascii=False))
    except Exception as e:
        print(f"jobs: could not record {key} in {path()}: {e!r}", file=sys.stderr, flush=True)
    return rec


def short(seconds):
    """10s, 5m, 6h: the page's cadence column."""
    for unit, n in (("h", 3600), ("m", 60)):
        if seconds >= n and seconds % n == 0:
            return f"{seconds // n}{unit}"
    return f"{seconds}s"


def sync_job():
    """skills-sync (scripts/sync.py under launchd) writes sync.json each pass."""
    s = fleet.read_json(fleet.state_dir().parent / "sync.json")
    every = (s or {}).get("interval") or 900
    job = {"key": "skills-sync", "name": "Skills sync", "every": short(every), "cadence": f"every {every // 60}m",
           "runs_in": "launchd", "record": None}
    if isinstance(s, dict) and (at := fleet.parse(s.get("at"))) and at.tzinfo:
        at = at.astimezone(dt.timezone.utc)  # sync.py writes local time with its offset
        ok, msg = s.get("ok") is not False, fleet.mask(s.get("message"))
        job["record"] = {"at": fleet.iso(at), "ok": ok, "n": None, "result": msg if ok else None, "error": None if ok else msg,
                         "next_at": fleet.iso(at + dt.timedelta(seconds=every))}
    return job


def reap_hard_job():
    """reap-hard (scripts/reap_hard.py under launchd) writes its last pass; read through it, as board.reap_line does."""
    job = {"key": "reap-hard", "name": "reap-hard", "every": None, "cadence": None, "runs_in": "launchd", "record": None,
           "why": "No reap_hard.py beside this install (it ships with the repository's scripts/)."}
    try:
        if (scripts := str(pathlib.Path(__file__).resolve().parents[3] / "scripts")) not in sys.path:  # each poll
            sys.path.insert(0, scripts)
        import reap_hard
    except ImportError:  # an install without the repo's scripts/
        return job
    every = reap_hard.config()["interval"]
    job.update(every=short(every), cadence=f"every {every // 60}m",
               why="No pass recorded in this store: its launchd agent is not installed here, or has not finished a pass yet.")
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
        {"name": "Stage row expiry", "rule": f"a finished row leaves the Stages page {stages.DONE_KEEP_S // 86400} days after done",
         "applied": "each read of stages.json"},
    ]


def snapshot(registry):
    """The Automations page's jobs and rules. registry: (key, name, every, cadence, group) for each of orch-dash's own
    jobs, group the key of the job whose run this one rides on (dash.JOBS)."""
    recs = records()
    jobs = [{"key": k, "name": name, "every": every, "cadence": cadence, "group": group, "runs_in": "orch-dash",
             "record": recs.get(k)} for k, name, every, cadence, group in registry]
    for key, read in (("skills-sync", sync_job), ("reap-hard", reap_hard_job)):
        try:
            jobs.append(read())
        except Exception as e:  # one unreadable record is that row's error, not the page's
            jobs.append({"key": key, "name": key, "every": None, "cadence": None, "runs_in": "launchd", "record": None,
                         "error": fleet.mask(repr(e))})
    return {"jobs": jobs, "rules": rules()}  # no timestamp: the page's ETag holds while nothing ran
