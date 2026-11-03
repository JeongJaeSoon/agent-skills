#!/usr/bin/env python3
"""agent-skills.db: the one SQLite file (WAL) that holds the facts agent-skills decides and changes itself.

PR 4a moves decisions (`orch decide`, the page's answers) here from decisions.json. Every connection to this file
goes through this module; the registry checks that no other script opens it and that each writer here touches only
the tables the registry lists for it.

Access gate: only `orch db import` (import_apply) creates the file, sets the schema version and moves data in. Any
other open needs SCHEMA_VERSION and every step in NEEDS recorded in `migration`, else NotMigrated: the CLI stops with
"orch db import", a hook passes silently, the brief shows 이행 대기. Nothing reads or writes an empty DB as canonical.

Commands (prog.py `orch db`):
  import [--dry-run | --apply] [--rehearsal]   decisions.json -> decision table, then a tombstone in its place
  export --legacy --target pre-4a DIR          the decisions.json the code before 4a reads
  dump [TABLE]                                 read-only JSON of the tables
  backup FILE | restore FILE [--rehearsal]     a lossless copy (Connection.backup), and putting one back
  cutover-plan 4a|4b                           the commands the owner runs around an import; runs none of them
"""
import contextlib, copy, datetime as dt, fcntl, hashlib, json, os, pathlib, plistlib, re, shutil, sqlite3, subprocess, sys

import fleet

DB_NAME = "agent-skills.db"
SCHEMA_VERSION = 1
NEEDS = ("4a-decisions",)        # the data steps this code reads; 4b code adds "4b-board"
LEGACY_STOP = "4b-board"         # once this step is in, the board and tidy writers below it must not write files
BUSY_MS = 5000
LEGACY = {"4a-decisions": ("decisions.json",)}
LOCKS = ("board.lock", "tidy.lock", "decisions.lock")
SCRIPTS = "skills/orchestrate/scripts"
BINS = ("orch", "orch-dash", "skills-sync")
CRON = re.compile(r"\b(?:orch-dash|orch|skills-sync)\b|skills/orchestrate/|hooks/[a-z_]+\.py")
REPO = pathlib.Path(__file__).resolve().parents[3]

SCHEMA = """
CREATE TABLE counter (name TEXT PRIMARY KEY, next INTEGER NOT NULL);
CREATE TABLE migration (step TEXT PRIMARY KEY, ts TEXT NOT NULL, backup TEXT NOT NULL, sources TEXT NOT NULL,
                        stage TEXT NOT NULL CHECK (stage IN ('imported','tombstoned')));
CREATE TABLE event (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, entity TEXT NOT NULL, id TEXT NOT NULL,
                    ev TEXT NOT NULL, by TEXT, before TEXT, after TEXT);
CREATE TABLE decision (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('open','done','dropped')),
  title TEXT NOT NULL, body TEXT, options TEXT NOT NULL DEFAULT '[]', recommend INTEGER, link TEXT, handle TEXT,
  created_at TEXT NOT NULL, checked_at TEXT, answer TEXT, closed_at TEXT,
  delivered INTEGER CHECK (delivered IN (0,1)),
  delivered_at TEXT
);
"""
DECISION_COLS = ("id", "status", "title", "body", "options", "recommend", "link", "handle", "created_at", "checked_at",
                 "answer", "closed_at", "delivered", "delivered_at")
DECISION_ALWAYS = ("id", "title", "body", "options", "recommend", "link", "handle", "created_at", "status")


class NotMigrated(Exception):
    pass


class LegacyStopped(Exception):
    """A board or tidy write from code older than the step that moved those files into the DB."""


class Refused(Exception):
    """import, restore or export would be unsafe now; the message lists why."""


def path():
    return fleet.state_dir() / DB_NAME


def now():
    return fleet.iso(fleet.utcnow())


def _connect(p, timeout_ms=BUSY_MS, mode="rw"):
    """mode rw never creates the file (only import and restore pass rwc). Reads use rw too: a read-only connection
    cannot remove the -wal and -shm it opened, so they would stay behind after every read."""
    db = sqlite3.connect(f"{pathlib.Path(p).resolve().as_uri()}?mode={mode}", uri=True, timeout=timeout_ms / 1000,
                         isolation_level=None, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute(f"PRAGMA busy_timeout={int(timeout_ms)}")
    return db


def _missing(db):
    """What this code needs and the DB lacks, or None."""
    v = db.execute("PRAGMA user_version").fetchone()[0]
    if v < SCHEMA_VERSION:
        return f"schema version {v}, this code needs {SCHEMA_VERSION}"
    have = {r[0] for r in db.execute("SELECT step FROM migration")}
    lack = [s for s in NEEDS if s not in have]
    return f"data step {', '.join(lack)} not imported" if lack else None


def open_db(timeout_ms=BUSY_MS):
    """A connection to the migrated DB; NotMigrated when the file, its schema or a needed data step is missing."""
    p = path()
    if not p.is_file():
        raise NotMigrated(f"no {DB_NAME} in {p.parent}: run orch db import")
    try:
        db = _connect(p, timeout_ms)
    except sqlite3.OperationalError as e:  # gone between the check and the open: still never created here
        raise NotMigrated(f"{p}: {e}") from e
    try:
        why = _missing(db)
    except sqlite3.DatabaseError as e:
        db.close()
        raise NotMigrated(f"{p}: {e}") from e
    if why:
        db.close()
        raise NotMigrated(f"{p}: {why}: run orch db import")
    return db


@contextlib.contextmanager
def read(timeout_ms=BUSY_MS):
    """One read snapshot: a render opens this once and passes it on, and closes it so WAL checkpoints go on."""
    db = open_db(timeout_ms)
    try:
        db.execute("BEGIN")
        yield db
    finally:
        db.close()


@contextlib.contextmanager
def tx(timeout_ms=BUSY_MS):
    """The only way to write: BEGIN IMMEDIATE takes the write lock up front, so a read never upgrades into BUSY."""
    db = open_db(timeout_ms)
    try:
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
        except BaseException:
            db.execute("ROLLBACK")
            raise
        db.execute("COMMIT")
    finally:
        db.close()


def _event(db, entity, iid, ev, by=None, before=None, after=None):
    js = lambda v: None if v is None else json.dumps(v, ensure_ascii=False, sort_keys=True)  # noqa: E731
    db.execute("INSERT INTO event (ts, entity, id, ev, by, before, after) VALUES (?,?,?,?,?,?,?)",
               (now(), entity, iid, ev, by, js(before), js(after)))


# ---------------------------------------------------------------- decisions

def _decision(r):
    """A row as the dict decisions.json held: the same keys, and delivered as None / False / True."""
    d = {k: r[k] for k in DECISION_ALWAYS}
    d["options"] = json.loads(d["options"] or "[]")
    for k in ("checked_at", "closed_at", "delivered_at"):
        if r[k] is not None:
            d[k] = r[k]
    if r["status"] != "open":
        d["answer"] = r["answer"]
    if r["delivered"] is not None:
        d["delivered"] = bool(r["delivered"])
    return d


def _decision_row(d):
    delivered = d.get("delivered")
    return (d["id"], d["status"], d["title"], d.get("body"), json.dumps(d.get("options") or [], ensure_ascii=False),
            d.get("recommend"), d.get("link"), d.get("handle"), d["created_at"], d.get("checked_at"), d.get("answer"),
            d.get("closed_at"), None if delivered is None else int(bool(delivered)), d.get("delivered_at"))


def _insert_decision(db, d):
    db.execute(f"INSERT INTO decision ({', '.join(DECISION_COLS)}) VALUES ({', '.join('?' * len(DECISION_COLS))})",
               _decision_row(d))


def _counter(db, name, default=1):
    r = db.execute("SELECT next FROM counter WHERE name=?", (name,)).fetchone()
    return r[0] if r else default


def _ev_name(before, after):
    if before["status"] != after["status"]:
        return after["status"]
    if before.get("delivered") != after.get("delivered"):
        return "delivered"
    if before.get("checked_at") != after.get("checked_at"):
        return "keep"
    return "update"


def change_decisions(change, by=None):
    """Run change(store) on {"next", "decisions"} as decisions.json held them, in one write transaction; each changed
    decision is written with its event. A decision change() removes is refused: closed ones stay, filtered by status."""
    with tx() as db:
        old = {r["id"]: _decision(r) for r in db.execute("SELECT * FROM decision")}
        n = _counter(db, "d")
        store = {"next": n, "decisions": copy.deepcopy(old)}
        out = change(store)
        new = store.get("decisions") or {}
        if set(old) - set(new):
            raise ValueError(f"decisions are never deleted: {', '.join(sorted(set(old) - set(new)))}")
        for did, d in new.items():
            if did not in old:
                _insert_decision(db, d)
                _event(db, "decision", did, "add", by, None, d)
            elif d != old[did]:
                row = _decision_row(d)
                db.execute(f"UPDATE decision SET {', '.join(f'{c}=?' for c in DECISION_COLS[1:])} WHERE id=?",
                           row[1:] + row[:1])
                _event(db, "decision", did, _ev_name(old[did], d), by, old[did], d)
        if store.get("next", n) != n:
            db.execute("INSERT INTO counter (name, next) VALUES ('d', ?) ON CONFLICT(name) DO UPDATE SET next=excluded.next",
                       (store["next"],))
        return out


def decisions(status=None, db=None):
    """Decisions as dicts, in id order; status narrows to one."""
    def run(c):
        q, a = ("SELECT * FROM decision WHERE status=?", (status,)) if status else ("SELECT * FROM decision", ())
        return sorted((_decision(r) for r in c.execute(q, a)), key=lambda d: int(d["id"][1:]))
    if db is not None:
        return run(db)
    with read() as c:
        return run(c)


# ---------------------------------------------------------------- the guard older board and tidy writes pass

def migrated_steps(strict=False):
    """Data steps recorded in the DB, read without creating it; empty when there is no DB yet. A DB that is there but
    cannot be read is empty too, or with strict the error."""
    p = path()
    if not p.is_file():
        return {}
    try:
        db = _connect(p, BUSY_MS if strict else 1000)
        try:
            return {r["step"]: r["stage"] for r in db.execute("SELECT step, stage FROM migration")}
        finally:
            db.close()
    except sqlite3.DatabaseError:
        if strict:
            raise
        return {}


def guard_legacy_write(what):
    """Board and tidy still write files in 4a. Once 4b moved them into the DB, this (4a) code is the old code: stop
    before any side effect (a file write, an inbox resolve or revive). A DB that cannot be read stops it too."""
    try:
        steps = migrated_steps(strict=True)
    except sqlite3.DatabaseError as e:
        raise LegacyStopped(f"{what}: cannot tell from {DB_NAME} whether {LEGACY_STOP} is in ({e}), so it writes "
                            "nothing") from e
    if LEGACY_STOP in steps:
        raise LegacyStopped(f"{what}: the board and tidy moved into {DB_NAME} ({LEGACY_STOP}); this code is older "
                            f"than that, so it writes nothing. Update the checkout and restart.")


def legacy_traces():
    """Legacy files a tombstoned step replaced that are files again or gone: old code wrote after the cutover."""
    out = []
    for step, stage in migrated_steps().items():
        if stage != "tombstoned":
            continue
        for name in LEGACY.get(step, ()):
            p = fleet.state_dir() / name
            if not p.is_dir():
                out.append(f"{name} ({'a file again' if p.exists() else 'tombstone gone'})")
    return out


# ---------------------------------------------------------------- preflight: no old code may write during a cutover

def _sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 127, "", str(e))


def applied_at():
    """When this checkout's HEAD arrived (its last reflog entry), the time every writer must have started after."""
    p = _sh(["git", "-C", str(REPO), "log", "-g", "-1", "--format=%gd", "--date=unix", "HEAD"])
    m = re.search(r"\{(\d+)\}", p.stdout)
    if not m:
        return None
    return dt.datetime.fromtimestamp(int(m.group(1)), dt.timezone.utc)


def head():
    return _sh(["git", "-C", str(REPO), "rev-parse", "HEAD"]).stdout.strip()


def launch_labels():
    """The LaunchAgents whose jobs write the fleet state: the dashboard server (fleet tick, relay, tidy) under this
    shell's label, and every installed agent whose program runs an entry point (one installed under another label)."""
    out, names = [os.environ.get("ORCH_DASH_LABEL") or "io.github.jeongjaesoon.orch-dash"], _entry_names()
    for p in sorted(pathlib.Path("~/Library/LaunchAgents").expanduser().glob("*.plist")):
        try:
            spec = plistlib.loads(p.read_bytes())
        except (OSError, plistlib.InvalidFileException, ValueError):
            continue
        args = [str(a) for a in [spec.get("Program") or "", *(spec.get("ProgramArguments") or [])] if a]
        cwd = pathlib.Path(str(spec.get("WorkingDirectory") or "/"))
        hits = []
        for i, a in enumerate(args):
            if " " in a:  # a `sh -c` string
                hits += [w for w, _ in _words(a, names)]
            else:
                hits.append(a.replace(".", "/") + ".py" if i and args[i - 1] == "-m" else a)
        if spec.get("Label") and spec["Label"] not in out and any(
                pathlib.PurePath(w).name in names and _ours((cwd / pathlib.Path(w).expanduser()).resolve()) for w in hits):
            out.append(spec["Label"])
    return out


def _ancestors(procs):
    out, pid = set(), os.getpid()
    while pid and pid not in out:
        out.add(pid)
        pid = (procs.get(pid) or (0,))[0]
    return out


def _entry_names():
    """Every entry point's file name in this checkout: the cheap filter before a process's directory is read."""
    return {p.name for d in (SCRIPTS, "hooks") for p in (REPO / d).glob("*.py")} | set(BINS)


def _cwd(pid):
    try:
        return pathlib.Path(os.readlink(f"/proc/{pid}/cwd"))
    except OSError:
        pass
    p = _sh(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])
    return next((pathlib.Path(l[1:]) for l in p.stdout.splitlines() if l.startswith("n/")), None)


def _words(cmd, names):
    """(word, module) for each word of a command line that names one of `names`: a path, or the module after -m."""
    words = cmd.split()
    for i, w in enumerate(words):
        module = bool(i) and words[i - 1] == "-m"
        if module:
            w = w.replace(".", "/") + ".py"
        if pathlib.PurePath(w).name in names:
            yield w, module


def _ours(p):
    """Whether a path is an entry point of some agent-skills checkout, this one or another."""
    if p.parent.name in ("bin", "hooks"):  # another program's bin/ or hooks/ is not ours
        return (p.name in BINS or p.parent.name == "hooks") and (p.parents[1] / "skills" / "orchestrate").is_dir()
    return p.parent.as_posix().endswith("/" + SCRIPTS)


def check_processes(procs, since):
    """Processes running an entry point from another checkout or from before `since`. A relative path or -m module
    resolves against the process's own directory; one whose directory cannot be read is a reason too."""
    out, mine, names = [], _ancestors(procs), _entry_names()
    for pid, (_, start, cmd) in sorted(procs.items()):
        if pid in mine:
            continue
        for w, module in _words(cmd, names):
            where = pathlib.Path(w)
            if not where.is_absolute():
                cwd = _cwd(pid)
                if cwd is None:
                    out.append(f"pid {pid} runs {w} from a directory that cannot be read: {cmd[:120]}")
                    break
                where = cwd / where
            where = where.resolve()
            if not _ours(where):
                if module:  # -m may load it from PYTHONPATH, which ps does not show
                    out.append(f"pid {pid} runs -m with an entry point's name from where this cannot see: {cmd[:120]}")
                    break
                continue
            at = fleet.ps_time(start)
            if REPO not in where.parents:
                out.append(f"pid {pid} runs {where} outside this checkout ({REPO})")
            elif not at or not since or at < since:
                out.append(f"pid {pid} started {start} before this checkout's update: {cmd[:120]}")
            break
    return out


def check_plugin_dirs(procs):
    """Claude processes that load an agent-skills checkout other than this one with --plugin-dir: its hooks are old
    code however late the session started."""
    out = []
    for pid, (_, _, cmd) in sorted(procs.items()):
        words = cmd.split()
        for i, w in enumerate(words):
            if w.startswith("--plugin-dir="):
                rest = [w.split("=", 1)[1], *words[i + 1:]]
            elif w == "--plugin-dir" and i + 1 < len(words):
                rest = words[i + 1:]
            else:
                continue
            cwd = None if pathlib.Path(rest[0]).expanduser().is_absolute() else _cwd(pid)
            if cwd is None and not pathlib.Path(rest[0]).expanduser().is_absolute():
                out.append(f"pid {pid} loads --plugin-dir {rest[0]} from a directory that cannot be read")
                continue
            # ps joins argv with spaces, so a directory with a space in it spans words: take the longest that exists.
            tries = [(cwd or pathlib.Path("/")) / pathlib.Path(" ".join(rest[:n])).expanduser()
                     for n in range(len(rest), 0, -1)]
            p = next((t for t in tries if t.is_dir()), tries[-1]).resolve()
            if p != REPO and (p / "skills" / "orchestrate").is_dir():
                out.append(f"pid {pid} loads agent-skills from {p} with --plugin-dir, not {REPO}: end it")
    return out


def check_plugin_source(homes):
    out, sha = [], head()
    for home in homes:
        data = fleet.read_json(home / "plugins" / "installed_plugins.json", {}) or {}
        for key, entries in (data.get("plugins") or {}).items():
            if not key.startswith("agent-skills@"):
                continue
            for e in entries if isinstance(entries, list) else [entries]:
                ip = pathlib.Path(str(e.get("installPath") or "")).expanduser()
                if ip.resolve() != REPO and e.get("gitCommitSha") != sha:
                    out.append(f"{home}: {key} loads {ip} at {str(e.get('gitCommitSha'))[:12]}, not HEAD {sha[:12]}: "
                               "skills-sync sync, then update the plugin")
    return out


def check_sessions(procs, since):
    """Every live Claude session in every home: all of its pids must have started after `since`."""
    out = []
    for sid, s in fleet.live_sessions(procs).items():
        starts = [fleet.ps_time((procs.get(p) or (0, ""))[1]) for p in s.get("pids") or []]
        if not starts or not since or any(t is None or t < since for t in starts):
            out.append(f"session {sid} ({s.get('cwd')}, home {s.get('home')}) started before the update: "
                       "end or restart it")
    return out


def check_launchers():
    out = []
    if sys.platform == "darwin":
        for label in launch_labels():
            p = _sh(["launchctl", "print", f"gui/{os.getuid()}/{label}"])
            if p.returncode == 0:
                out.append(f"LaunchAgent {label} is loaded (with or without a pid it restarts old code): "
                           f"launchctl bootout gui/{os.getuid()}/{label}")
            elif p.returncode == 127:
                out.append(f"launchctl print {label}: {p.stderr.strip()[:120]}")
    if not shutil.which("crontab"):
        return out
    p, names = _sh(["crontab", "-l"]), _entry_names()  # cron runs on macOS too
    if p.returncode and "no crontab" not in p.stderr.lower():
        return out + [f"crontab -l failed ({p.returncode}): {p.stderr.strip()[:120]}"]
    for line in p.stdout.splitlines():
        if line.strip() and not line.lstrip().startswith("#") and (CRON.search(line) or next(_words(line, names), None)):
            out.append(f"crontab line still active: {line.strip()[:120]}")
    return out


def preflight():
    """Every reason a cutover must not run now; [] when none. A check that cannot run is a reason too."""
    since = applied_at()
    out = [] if since else [f"cannot read when {REPO} was updated (git reflog)"]
    try:
        procs = fleet.ps_table()
    except fleet.SourceError as e:
        return out + [str(e)]
    if os.getpid() not in procs:  # ps failed with no output: an empty table would pass every check below
        return out + ["ps did not list this process: cannot tell which writers run"]
    return (out + check_processes(procs, since) + check_plugin_dirs(procs) + check_plugin_source(fleet.claude_homes())
            + check_sessions(procs, since) + check_launchers())


def _rehearsal_ok():
    """A rehearsal skips preflight, so it runs only on a state directory named in ORCH_FLEET_STATE that is not the
    default one."""
    env = os.environ.get("ORCH_FLEET_STATE")
    default = pathlib.Path("~/.local/state/agent-skills/dashboard").expanduser().resolve()
    return bool(env) and pathlib.Path(env).expanduser().resolve() != default


def gate(rehearsal):
    if rehearsal:
        if not _rehearsal_ok():
            raise Refused("--rehearsal runs only with ORCH_FLEET_STATE set to a directory other than the default state")
        return
    why = preflight()
    if why:
        raise Refused("refused: old code could still write the state\n" + "\n".join(f"  - {w}" for w in why))


# ---------------------------------------------------------------- import (4a: decisions.json)

def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


@contextlib.contextmanager
def _locks():
    sd = fleet.state_dir()
    with contextlib.ExitStack() as stack:
        for name in LOCKS:
            f = stack.enter_context((sd / name).open("a"))
            fcntl.flock(f, fcntl.LOCK_EX)
        yield


def _legacy_decisions(p):
    """(decisions, next, problems) from a decisions.json."""
    if not p.is_file():
        return [], 1, []
    try:
        data = json.loads(p.read_text())
    except ValueError as e:
        return [], 1, [f"{p.name}: not JSON ({e})"]
    if not isinstance(data, dict):
        return [], 1, [f"{p.name}: not an object"]
    got, bad = [], []
    for key, d in (data.get("decisions") or {}).items():
        if not isinstance(d, dict) or d.get("id") != key or not re.fullmatch(r"d\d+", key):
            bad.append(f"{key}: id does not match its key")
        elif d.get("status") not in ("open", "done", "dropped"):
            bad.append(f"{key}: status {d.get('status')!r}")
        elif not isinstance(d.get("title"), str) or not d["title"] or not d.get("created_at"):
            bad.append(f"{key}: no title or created_at")
        elif not isinstance(d.get("options") or [], list) or d.get("delivered") not in (None, True, False):
            bad.append(f"{key}: options or delivered of the wrong type")
        elif set(d) - set(DECISION_COLS):
            bad.append(f"{key}: fields this table has no column for: {', '.join(sorted(set(d) - set(DECISION_COLS)))}")
        else:
            got.append(d)
    top = max([int(d["id"][1:]) for d in got], default=0) + 1
    nxt = data.get("next", 1)
    return got, max(nxt if isinstance(nxt, int) else 1, top), bad


def import_plan():
    """What `orch db import` would do now: {step, state, decisions, open, next, problems}."""
    sd, steps = fleet.state_dir(), migrated_steps()
    src = sd / "decisions.json"
    got, nxt, bad = _legacy_decisions(src)
    stage = steps.get("4a-decisions")
    state = ("tombstoned" if stage == "tombstoned" else "imported, tombstone pending" if stage == "imported"
             else "unexpected DB without the 4a step" if path().exists() else "not imported")
    return {"step": "4a-decisions", "state": state, "decisions": len(got), "open": sum(d["status"] == "open" for d in got),
            "next": nxt, "problems": bad, "source": str(src), "source_kind": "dir" if src.is_dir() else
            "file" if src.is_file() else "absent"}


def _tombstone(sd, names):
    for name in names:
        p = sd / name
        if p.is_file():
            p.unlink()
        p.mkdir(mode=0o700, exist_ok=True)


def _set_stage(step, stage):
    db = _connect(path())
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE migration SET stage=? WHERE step=?", (stage, step))
        _event(db, "migration", step, stage)
        db.execute("COMMIT")
    finally:
        db.close()


def _finish(step, row):
    """After the import committed: tombstone each source still as imported (sha equal), else stop on a conflict."""
    sd, sources = fleet.state_dir(), json.loads(row["sources"])
    if row["stage"] == "tombstoned":
        trace = [n for n in sources if not (sd / n).is_dir()]
        if trace:
            raise Refused(f"{step} is tombstoned, but {', '.join(trace)} is a file again or gone: old code wrote after "
                          f"the cutover. Compare it with {row['backup']} and move it aside by hand")
        return "already imported and tombstoned"
    changed = [n for n, h in sources.items() if (sd / n).is_file() and sha256(sd / n) != h]
    if changed:
        raise Refused(f"conflict: {', '.join(changed)} changed after the import into the DB (sha256 differs from "
                      f"{row['backup']}). Nothing was tombstoned; compare the file with the backup and the DB by hand")
    _tombstone(sd, sources)
    _set_stage(step, "tombstoned")
    return "tombstoned " + ", ".join(sources)


def import_apply(rehearsal=False, checks=True):
    """`orch db import --apply` for 4a. Returns a line of what it did. checks=False is for tests only."""
    if checks:
        gate(rehearsal)
    sd = fleet.state_dir()
    sd.mkdir(parents=True, exist_ok=True)
    step = "4a-decisions"
    with _locks():
        if path().exists():
            db = _connect(path())
            try:
                row = db.execute("SELECT * FROM migration WHERE step=?", (step,)).fetchone() if db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='migration'").fetchone() else None
            finally:
                db.close()
            if not row:
                raise Refused(f"{path()} exists without the {step} step: move it aside by hand, or restore a backup")
            return _finish(step, row)
        got, nxt, bad = _legacy_decisions(sd / "decisions.json")
        if bad:
            raise Refused("decisions.json has entries the table cannot hold; fix them by hand first:\n"
                          + "\n".join(f"  - {b}" for b in bad))
        ts = fleet.utcnow()
        backup = sd / f"backup-db-{ts.strftime('%Y%m%dT%H%M%S%fZ')}"
        backup.mkdir(mode=0o700)
        os.chmod(backup, 0o700)
        sources = {}
        for name in LEGACY[step]:
            if (sd / name).is_file():
                shutil.copyfile(sd / name, backup / name)
                os.chmod(backup / name, 0o600)
                sources[name] = sha256(sd / name)
            else:
                sources[name] = None
        tmp = sd / f".{DB_NAME}.import-{os.getpid()}.tmp"
        tmp.unlink(missing_ok=True)
        old_mask = os.umask(0o077)
        try:
            db = _connect(tmp, mode="rwc")
            try:
                db.execute("PRAGMA journal_mode=WAL")
                db.executescript(SCHEMA)
                db.execute("BEGIN IMMEDIATE")
                for d in got:
                    _insert_decision(db, d)
                    _event(db, "decision", d["id"], "import", "import", None, d)
                db.execute("INSERT INTO counter (name, next) VALUES ('d', ?)", (nxt,))
                db.execute("INSERT INTO migration (step, ts, backup, sources, stage) VALUES (?,?,?,?, 'imported')",
                           (step, fleet.iso(ts), str(backup), json.dumps(sources)))
                _event(db, "migration", step, "imported", "import", None, {"backup": str(backup), "sources": sources})
                db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                db.execute("COMMIT")
                ok = db.execute("PRAGMA integrity_check").fetchone()[0]
                if ok != "ok":
                    raise Refused(f"integrity_check: {ok}")
            finally:
                db.close()
            os.chmod(tmp, 0o600)
            os.replace(tmp, path())
        except BaseException:
            for p in (tmp, tmp.with_name(tmp.name + "-wal"), tmp.with_name(tmp.name + "-shm")):
                p.unlink(missing_ok=True)
            raise
        finally:
            os.umask(old_mask)
        db = _connect(path())
        try:
            row = db.execute("SELECT * FROM migration WHERE step=?", (step,)).fetchone()
        finally:
            db.close()
        done = _finish(step, row)
    return f"imported {len(got)} decisions (next d{nxt}); backup {backup}; {done}"


# ---------------------------------------------------------------- backup, restore, export, dump

def _raw():
    """The DB without the access gate (dump, backup and export work on any version), never created here."""
    if not path().is_file():
        raise NotMigrated(f"no {DB_NAME} in {path().parent}: run orch db import")
    return _connect(path())


def backup(dest):
    """A lossless, consistent copy of the DB through Connection.backup, even while others write. 0600."""
    dest = pathlib.Path(dest).expanduser()
    live = path().resolve()
    if dest.resolve() in (live, live.with_name(live.name + "-wal"), live.with_name(live.name + "-shm")):
        raise Refused(f"{dest} is the live DB or its sidecar: back it up to another path")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    src = _raw()
    old_mask = os.umask(0o077)
    try:
        out = sqlite3.connect(str(tmp))
        try:
            src.backup(out)
            out.execute("PRAGMA journal_mode=DELETE")
        finally:
            out.close()
        os.chmod(tmp, 0o600)
        os.replace(tmp, dest)
    finally:
        os.umask(old_mask)
        src.close()
        tmp.unlink(missing_ok=True)
    return dest


def restore(src, rehearsal=False, checks=True):
    """Put a backup file back as the DB. The same preflight as import; a legacy file in a tombstone's place stops it."""
    src = pathlib.Path(src).expanduser()
    if not src.is_file():
        raise Refused(f"{src}: no such file")
    s = _connect(src, mode="ro")
    try:
        if s.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise Refused(f"{src}: integrity_check failed")
        why = _missing(s)
        if why:
            raise Refused(f"{src}: {why}")
        steps = {r["step"]: r["stage"] for r in s.execute("SELECT step, stage FROM migration")}
    except sqlite3.DatabaseError as e:
        raise Refused(f"{src}: not an {DB_NAME} backup ({e})") from e
    finally:
        s.close()
    if checks:
        gate(rehearsal)
    sd = fleet.state_dir()
    sd.mkdir(parents=True, exist_ok=True)
    names = [n for st in steps for n in LEGACY.get(st, ())]
    files = [n for n in names if (sd / n).is_file()]
    if files:
        raise Refused(f"{', '.join(files)} is a file here: the restored DB already holds it. Move it aside first")
    with _locks():
        old_mask = os.umask(0o077)
        try:
            s, d = _connect(src, mode="ro"), _connect(path(), mode="rwc")
            try:
                s.backup(d)
                d.execute("PRAGMA journal_mode=WAL")
            finally:
                s.close()
                d.close()
        finally:
            os.umask(old_mask)
        os.chmod(path(), 0o600)
        _tombstone(sd, names)
    return path()


def export_legacy(dest, target):
    """The files the code before `target` reads. 4a code holds only decisions, so only pre-4a is possible here."""
    if target != "pre-4a":
        raise Refused("this code has only the 4a step: --target pre-4a. Undoing 4b alone is 4b code's export")
    if LEGACY_STOP in migrated_steps():
        raise Refused(f"{LEGACY_STOP} is imported: undo 4b first with the 4b code's export --target 4a")
    db = open_db()  # a DB without the decisions step would export an empty file that old code takes as canonical
    try:
        db.execute("BEGIN")
        rows = decisions(db=db)
        nxt = _counter(db, "d")
        db.execute("COMMIT")
        busy = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
    finally:
        db.close()
    dest = pathlib.Path(dest).expanduser()
    dest.mkdir(mode=0o700, parents=True, exist_ok=True)
    out = dest / "decisions.json"
    fleet.write_atomic(out, json.dumps({"next": nxt, "decisions": {d["id"]: d for d in rows}}, ensure_ascii=False,
                                       indent=1) + "\n")
    os.chmod(out, 0o600)
    return out, len(rows), not busy


def dump(table=None):
    db = _raw()
    try:
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                                          "ORDER BY name")]
        if table and table not in names:
            raise ValueError(f"no table {table!r}; tables: {', '.join(names)}")
        out = {"user_version": db.execute("PRAGMA user_version").fetchone()[0]}
        db.execute("BEGIN")
        for t in [table] if table else names:
            out[t] = [dict(r) for r in db.execute(f'SELECT * FROM "{t}"')]
        return out
    finally:
        db.close()


def _stop_start(step):
    uid = os.getuid()
    stop = [f"launchctl bootout gui/{uid}/{l}" for l in launch_labels()] if sys.platform == "darwin" else []
    stop.append("crontab -e    # if a line runs orch, orch-dash, skills-sync or an orchestrate script, comment it out")
    start = (["orch-dash install-agent"] if sys.platform == "darwin" else []) + \
        ["crontab -e    # uncomment the lines commented out above, if any"]
    if step == "4b":
        stop.append("skills-sync setup disable janitor")
        start.append("skills-sync setup enable janitor")
    return stop, start


def cutover_plan(step):
    """The commands the owner runs around `orch db import`, in order. Nothing here runs them."""
    if step not in ("4a", "4b"):
        raise ValueError("cutover-plan 4a|4b")
    stop, start = _stop_start(step)
    lines = [f"# cutover {step}: run these yourself, in order. This command ran none of them.",
             "# 1. stop every long-running writer of the state (the dashboard server runs the fleet tick, relay, tidy)",
             *stop,
             "# 2. bring the main checkout to the merged code",
             f"git -C {REPO} pull --ff-only",
             "# 3. restart or /reload-plugins every live Claude session (each runs the decision hook), then",
             "orch db import --dry-run",
             "# 4. it refuses while any old writer, session or loaded LaunchAgent remains; then",
             "orch db import --apply",
             "# 5. start the writers again",
             *start,
             "",
             f"# undo {step} (pre-4a): stop the writers as in 1, then",
             "orch db backup <file>",
             "orch db export --legacy --target pre-4a <dir>",
             f"# move {DB_NAME} with its -wal and -shm out of the state directory, remove the empty decisions.json",
             "# directory there, put <dir>/decisions.json in its place, revert the PR, fast-forward, start the writers"]
    return "\n".join(lines)


def main(argv):
    sub, rest = (argv[0] if argv else ""), argv[1:]
    rehearsal = "--rehearsal" in rest
    try:
        if sub == "import" and "--apply" in rest:
            print(import_apply(rehearsal))
            print("start the writers again (run these yourself):\n" + "\n".join(_stop_start("4a")[1]))
        elif sub == "import":
            p = import_plan()
            print(json.dumps(p, ensure_ascii=False, indent=1))
            if not rehearsal:
                why = preflight()
                print("preflight: ok" if not why else "preflight: --apply would refuse\n" + "\n".join(f"  - {w}" for w in why))
            print("dry-run: nothing written. orch db import --apply")
        elif sub == "export" and "--legacy" in rest and len([a for a in rest if not a.startswith("--")]) >= 2:
            target = rest[rest.index("--target") + 1] if "--target" in rest else ""
            dest = [a for a in rest if not a.startswith("--") and a != target][-1]
            out, n, truncated = export_legacy(dest, target)
            print(f"wrote {out} ({n} decisions)")
            print("wal checkpoint: done, the DB can be moved" if truncated else
                  "wal checkpoint: busy, another connection is open; stop it before moving the DB")
        elif sub == "dump":
            print(json.dumps(dump(rest[0] if rest else None), ensure_ascii=False, indent=1))
        elif sub == "backup" and rest:
            print(f"backup: {backup(rest[0])}")
        elif sub == "restore" and [a for a in rest if not a.startswith("--")]:
            print(f"restored {restore([a for a in rest if not a.startswith('--')][0], rehearsal)}")
        elif sub == "cutover-plan" and rest:
            print(cutover_plan(rest[0]))
        else:
            sys.exit(__doc__)
    except (Refused, NotMigrated, ValueError, OSError, sqlite3.Error) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main(sys.argv[1:])
