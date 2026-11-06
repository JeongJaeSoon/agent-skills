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


def record(key, n=None, result=None, error=None, next_in=None, event=None, now=None):
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
                        event({"at": stamp, "kind": "job_failed", "session": None, "job": key, "text": line_text(f"{key}: {error}")})
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
    """skills-sync (scripts/sync.py) writes sync.json each pass. The page uses it only when Orca has no run to show."""
    s = fleet.read_json(fleet.state_dir().parent / "sync.json")
    every = (s or {}).get("interval") or 900
    job = {"key": "sync", "name": "Skills sync", "every": short(every), "cadence": f"every {every // 60}m",
           "runs_in": "orca", "record": None}
    if isinstance(s, dict) and (at := fleet.parse(s.get("at"))) and at.tzinfo:
        at = at.astimezone(dt.timezone.utc)  # sync.py writes local time with its offset
        ok, msg = s.get("ok") is not False, fleet.mask(s.get("message"))
        job["record"] = {"at": fleet.iso(at), "ok": ok, "n": None, "result": msg if ok else None, "error": None if ok else msg,
                         "next_at": fleet.iso(at + dt.timedelta(seconds=every))}
    return job


def reap_hard_job():
    """reap-hard (scripts/reap_hard.py) writes its last pass; read through it, as board.reap_line does. The page uses it
    only when Orca has no run to show."""
    job = {"key": "reap-hard", "name": "reap-hard", "every": None, "cadence": None, "runs_in": "orca", "record": None,
           "why": "No reap_hard.py beside this install (it ships with the repository's scripts/)."}
    try:
        if (scripts := str(pathlib.Path(__file__).resolve().parents[3] / "scripts")) not in sys.path:  # each poll
            sys.path.insert(0, scripts)
        import reap_hard
    except ImportError:  # an install without the repo's scripts/
        return job
    every = reap_hard.config()["interval"]
    job.update(every=short(every), cadence=f"every {every // 60}m",
               why="No pass recorded in this store: the job is not set up here, or has not finished a pass yet.")
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
    import board, stages
    lim = {**board.LIMITS, **(fleet.config().get("board") or {})}
    return [
        {"name": "Inbox item expiry", "rule": f"an item added with orch-dash inbox add and never resolved leaves Needs you "
                                              f"{fleet.EXTERNAL_KEEP_DAYS} days after it was added", "applied": "each fleet collect"},
        {"name": "Brief ✅ fold", "rule": f"a ✅ row merged over {lim['done_fold_h']} h ago folds into its repository's "
                                         "\"✅ 그 전 완료 N건\" line", "applied": "each brief render"},
        {"name": "Stage table fold", "rule": f"a stage row finished, or unchanged for {lim['stage_idle_d']} days, leaves "
                                             "`orch stage show` and the brief's stage line count", "applied": "each stage show and brief render"},
        {"name": "Stage row expiry", "rule": f"a finished row leaves the Stages page {stages.DONE_KEEP_S // 86400} days after done",
         "applied": "each read of stages.json"},
    ]


def registry():
    """The automations the page lists: scripts/automations.py AUTOMATIONS, the one list `skills-sync setup` installs
    from. Only its attributes are read; status() would call Orca. job_name is the Orca automation's name, None for a
    LaunchAgent. Raises ImportError where the repository's scripts/ does not sit beside this install."""
    if (scripts := str(pathlib.Path(__file__).resolve().parents[3] / "scripts")) not in sys.path:
        sys.path.insert(0, scripts)
    import automations
    return [{"key": a.name, "job_name": getattr(a, "job_name", None) or None, "default": a.default, "what": a.what}
            for a in automations.AUTOMATIONS]


def patrol_job():
    """`orch patrol` has no schedule of its own: a reap-hard pass starts it, and it leaves patrol-verdicts.json."""
    job = {"key": "patrol", "name": "Session patrol", "every": None, "cadence": "started by reap-hard, every other pass",
           "group": None, "runs_in": "reap-hard", "record": None, "why": "No round recorded in this store yet."}
    v = fleet.read_json(fleet.state_dir().parent / "patrol-verdicts.json")
    if isinstance(v, dict) and isinstance(v.get("t"), (int, float)):
        at = dt.datetime.fromtimestamp(v["t"], dt.timezone.utc)
        src = v.get("sources") if isinstance(v.get("sources"), dict) else {}
        bad = sorted(f"{k}: {s}" for k, s in src.items() if s in ("timeout", "failed"))  # a source or phase that gave up
        count = lambda k: len(v[k]) if isinstance(v.get(k), list) else 0
        job["record"] = {"at": fleet.iso(at), "ok": not bad, "n": None, "next_at": None,
                         "result": None if bad else f"{count('alerts')} alerts · {count('reports')} reports",
                         "error": fleet.mask(f"the round was cut short ({', '.join(bad)})") if bad else None}
    return job


def own_jobs(reg):
    recs = records()
    return [{"key": k, "name": name, "every": every, "cadence": cadence, "group": group, "runs_in": "orch-dash",
             "record": recs.get(k)} for k, name, every, cadence, group in reg]


def automation_list(reg, orca, own, stand_in=None):
    """One row per registry name, in registry order, and no other: what Orca says about the automation (None when
    it is not registered there, or when Orca could not be read: `registered` is then None), the jobs that run inside
    it, and `record`, a stand-in from the job's own file for when Orca has no run to show. A name Orca lists that the
    registry lacks is not here."""
    by_name = {a.get("name"): a for a in orca or []}
    stand_in = stand_in or {}
    rows = []
    for r in reg:
        a = by_name.get(r["job_name"]) if r["job_name"] else None
        kids = {"orch-dash": own, "reap-hard": [patrol_job()]}.get(r["key"], [])
        registered = None if not r["job_name"] or orca is None else a is not None
        rows.append({**r, "orca": a, "registered": registered, "record": stand_in.get(r["key"]), "jobs": kids})
    return rows


def snapshot(reg, orca=None):
    """The Automations page. reg: (key, name, every, cadence, group) for each of orch-dash's own jobs, group the key
    of the job whose run this one rides on (dash.JOBS). orca: the Orca automations of the last good collect, or None
    when it could not be read."""
    own = own_jobs(reg)
    out = {"rules": rules()}  # no timestamp: the page's ETag holds while nothing ran
    try:
        stand_in = {}
        for key, read in (("sync", sync_job), ("reap-hard", reap_hard_job)):
            try:
                j = read()
                stand_in[key] = j["record"]
            except Exception as e:  # one unreadable file is that row's error, not the page's
                stand_in[key] = {"at": None, "ok": False, "n": None, "result": None, "next_at": None, "error": fleet.mask(repr(e))}
        out["automations"] = automation_list(registry(), orca, own, stand_in)
    except ImportError as e:
        out["automations"], out["error"] = None, fleet.mask(f"scripts/automations.py not readable: {e}")
    return out
