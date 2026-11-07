#!/usr/bin/env python3
"""agent-skills.db: the one SQLite file (WAL) that holds the facts agent-skills decides and changes itself.

PR 4a moved decisions (`orch decide`, the page's answers) here from decisions.json. PR 4b moved the board and tidy:
item (to-dos, held items, doing lines, the owner's now-list, open and closed in one row), board_row, lead (only leads
Orca cannot see), closure (what tidy closed outside item: card:, row:, ext:). Every connection to this file goes
through this module; the registry checks that no other script opens it and that each writer here touches only the
tables the registry lists for it.

Access gate: only `orch db import` (import_apply) creates the file, sets the schema version and moves data in. Any
other open needs SCHEMA_VERSION and every step in NEEDS recorded in `migration`, else NotMigrated: the CLI stops with
"orch db import", a hook passes silently, the brief shows 이행 대기. Nothing reads or writes an empty DB as canonical.
With no DB file at all, decisions are read and written in decisions.json under its lock, so `orch decide` works before
the cutover and the import picks them up unchanged.

Commands (prog.py `orch db`):
  import [--dry-run | --apply] [--rehearsal]   decisions.json, board.json, tidy.jsonl -> tables, tombstones in their place
  export --legacy --target 4a|pre-4a DIR       the files older code reads (4a: board, tidy, an inbox copy; pre-4a: decisions)
  rollback 4b DIR [--rehearsal]                put an export --target 4a back and take the 4b step out of the DB
  dump [TABLE]                                 read-only JSON of the tables
  backup FILE | restore FILE [--rehearsal]     a lossless copy (Connection.backup), and putting one back
  cutover-plan 4a|4b                           the commands the owner runs around an import; runs none of them
  state                                        acks.jsonl and stages.json -> tables, then prs/sendlog.jsonl -> send_log (the
                                               review-ask queue's tables come with it); backup first, tombstones in place
  export --legacy --target state DIR           those two files as older code reads them (the way back)
  rollback state DIR                           put that export back and take the step out of the DB
  export --legacy --target sendlog DIR         sendlog.jsonl as older code reads it
  rollback sendlog DIR                         put that export back; refused while review asks are open
  migrate [--check]                            the whole cutover in one rerunnable command (cutover.py); owner only
  init                                         a new PC: an empty DB with both steps done, only when no state exists
"""
import contextlib, copy, datetime as dt, fcntl, functools, hashlib, json, os, pathlib, plistlib, re, shutil, sqlite3, subprocess, sys, tempfile, time

import fleet

DB_NAME = "agent-skills.db"
SCHEMA_VERSION = 2
DECISION_STEP, BOARD_STEP = "4a-decisions", "4b-board"
NEEDS = (DECISION_STEP, BOARD_STEP)   # the data steps this code reads
BUSY_MS = 5000
LEGACY = {DECISION_STEP: ("decisions.json",), BOARD_STEP: ("board.json", "tidy.jsonl")}
RETIRED = ("board.prev.json", "tidy.jsonl.1")  # no reader left: they move into the backup with the step's files
LOCKS = ("board.lock", "tidy.lock", "decisions.lock")
CUTOVER_LOCK, RUN_LOCK = "cutover.lock", "cutover-run.lock"
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
SCHEMA_V2 = (
    """CREATE TABLE item (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('todo','doing','held','done','dropped')),
  lane TEXT NOT NULL CHECK (lane IN ('todo','later','doing','owner_todo')),
  pos INTEGER NOT NULL,
  who TEXT,
  title TEXT NOT NULL,
  next TEXT,
  waiting INTEGER NOT NULL DEFAULT 0 CHECK (waiting IN (0,1)), waiting_since TEXT,
  trig TEXT,
  due TEXT, after_ref TEXT,
  program TEXT,
  note TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  closed_at TEXT, close_by TEXT, close_rule TEXT, close_evidence TEXT,
  reopen_evidence TEXT,
  CHECK ((status IN ('done','dropped')) = (closed_at IS NOT NULL))
)""",
    "CREATE INDEX item_lane ON item(lane, pos) WHERE status IN ('todo','doing','held')",
    "CREATE TABLE alias (old TEXT PRIMARY KEY, id TEXT NOT NULL REFERENCES item(id))",
    "CREATE TABLE board_row (id TEXT PRIMARY KEY, parent TEXT, pos INTEGER NOT NULL, data TEXT NOT NULL, done_at TEXT)",
    "CREATE TABLE lead (name TEXT PRIMARY KEY, data TEXT NOT NULL, closed_at TEXT)",
    """CREATE TABLE closure (
  unit TEXT PRIMARY KEY,
  state TEXT NOT NULL CHECK (state IN ('done','reopen')),
  ts TEXT NOT NULL, by TEXT, rule TEXT, evidence TEXT, label TEXT,
  undone_evidence TEXT,
  ext_mark TEXT
)""")
TABLES_V2 = ("alias", "closure", "lead", "board_row", "item")  # dropped in this order: alias points at item
# lane -> (its open status, the prefix tidy's content hash used before stable ids)
LANES = {"todo": ("todo", "do"), "later": ("held", "later"), "doing": ("doing", "doing"), "owner_todo": ("todo", "todo")}
OPEN = ("todo", "doing", "held")
OPEN_SQL = "('todo','doing','held')"
GROUP_KIND, GROUP_POS = "group", 1_000_000  # a priority rule is a board_row of this kind, listed after every row
# A card annotation (board.annot_set) is a board_row of this kind with the id card:<worktree>. Like a rule it is kept out of
# board["rows"], so no report, tidy or count sees it until the renderer reads board["annots"].
ANNOT_KIND, ANNOT_POS = "annot", 2_000_000
GROUP_MATCH = re.compile(r"(?:worktree|program):\S.*|repo:[\w.-]+/[\w.-]+")  # what board.match_of() lets in
GROUP_PRIORITY = re.compile(r"\d{1,3}:(?!.* — )\S.*")  # RANK:NAME, as board.priority_of() writes it
ITEM_COLS = ("who", "title", "next", "waiting", "waiting_since", "trig", "due", "after_ref", "note")
ORPHAN = tuple(f"{prefix}:" for _, prefix in LANES.values())  # a tidy id from before stable ids whose item left the board
DECISION_COLS = ("id", "status", "title", "body", "options", "recommend", "link", "handle", "created_at", "checked_at",
                 "answer", "closed_at", "delivered", "delivered_at")
DECISION_ALWAYS = ("id", "title", "body", "options", "recommend", "link", "handle", "created_at", "status")


class NotMigrated(Exception):
    pass


class Refused(Exception):
    """import, restore or export would be unsafe now; the message lists why."""


def path():
    return fleet.state_dir() / DB_NAME


@functools.lru_cache(maxsize=1)
def main_checkout():
    """The checkout sessions load: the repository's main worktree even when this code runs from a linked one."""
    p = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                       capture_output=True, text=True)
    common = pathlib.Path(p.stdout.strip()) if p.returncode == 0 and p.stdout.strip() else None
    return common.parent if common and common.name == ".git" else REPO


def migrate_command():
    """The one line that finishes a cutover on this PC, with the absolute path of the main checkout."""
    return f"{main_checkout()}/bin/orch db migrate"


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
        raise NotMigrated(f"no {DB_NAME} in {p.parent}: run {migrate_command()}")
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
        raise NotMigrated(f"{p}: {why}: run {migrate_command()}")
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


def _add_note_column(db):
    """item.note came after the schema was versioned. Adding it is no step and no version bump: readers and older code
    ignore a column they do not name, so the first writer that opens the DB adds it, inside its own transaction."""
    if not any(r["name"] == "note" for r in db.execute("PRAGMA table_info(item)")):
        db.execute("ALTER TABLE item ADD COLUMN note TEXT")


@contextlib.contextmanager
def tx(timeout_ms=BUSY_MS):
    """The only way to write: BEGIN IMMEDIATE takes the write lock up front, so a read never upgrades into BUSY."""
    db = open_db(timeout_ms)
    try:
        db.execute("BEGIN IMMEDIATE")
        try:
            if why := _missing(db):  # a rollback may have taken the step out while this waited for the lock
                raise NotMigrated(f"{path()}: {why}: run {migrate_command()}")
            _add_note_column(db)
            yield db
        except BaseException:
            db.execute("ROLLBACK")
            raise
        db.execute("COMMIT")
    finally:
        db.close()


def _event(db, entity, iid, ev, by=None, before=None, after=None, ts=None):
    js = lambda v: None if v is None else json.dumps(v, ensure_ascii=False, sort_keys=True)  # noqa: E731
    db.execute("INSERT INTO event (ts, entity, id, ev, by, before, after) VALUES (?,?,?,?,?,?,?)",
               (ts or now(), entity, iid, ev, by, js(before), js(after)))


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


def _on_file():
    """True until `orch db import`: no DB file and decisions.json is not its tombstone. A DB file that is there and
    not imported is a cutover in progress, never written around."""
    sd = fleet.state_dir()
    return sd.is_dir() and not path().exists() and not (sd / "decisions.json").is_dir()


@contextlib.contextmanager
def _file_lock():
    """decisions.lock, which import_apply holds too. Callers check _on_file() again inside: the import may have
    finished while they waited, and its tombstone reads as an empty store."""
    try:
        f = (fleet.state_dir() / "decisions.lock").open("a")
    except OSError as e:
        raise NotMigrated(f"decisions.lock: {e}") from e
    with f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def _file_decisions():
    try:
        got, nxt, bad = _legacy_decisions(fleet.state_dir() / "decisions.json")
    except OSError as e:
        raise NotMigrated(f"decisions.json: {e}") from e
    if bad:
        raise NotMigrated(f"decisions.json: {bad[0]}")
    return got, nxt


def _change_file(change):
    """change_decisions on decisions.json; `orch db import` reads it as it stands, so nothing here needs migrating."""
    got, nxt = _file_decisions()
    store = {"next": nxt, "decisions": {d["id"]: d for d in got}}
    out = change(store)
    if {d["id"] for d in got} - set(store.get("decisions") or {}):
        raise ValueError("decisions are never deleted")
    target = fleet.state_dir() / "decisions.json"
    fleet.write_atomic(target, json.dumps(store, ensure_ascii=False, indent=1) + "\n")
    os.chmod(target, 0o600)
    return out


def change_decisions(change, by=None):
    """Run change(store) on {"next", "decisions"} as decisions.json held them, in one write transaction; each changed
    decision is written with its event. A decision change() removes is refused: closed ones stay, filtered by status.
    Before `orch db import` the same change runs on decisions.json. answered_by has no column: it stays in the event's
    `after`, or in the file, and _answered_by reads it back."""
    if _on_file():
        with _file_lock():
            if _on_file():
                return _change_file(change)
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
    if _on_file():
        with _file_lock():
            if _on_file():
                return sorted((d for d in _file_decisions()[0] if not status or d["status"] == status),
                              key=lambda d: int(d["id"][1:]))
    with read() as c:
        return run(c)


def _answered_by(db):
    """{id: the session that recorded the answer}, from each decision's events; later events may lack the key."""
    out = {}
    for r in db.execute("SELECT id, after FROM event WHERE entity='decision' ORDER BY seq"):
        by = json.loads(r["after"] or "{}").get("answered_by")
        if by:
            out[r["id"]] = by
    return out


def decision_record(did):
    """One decision of any status with its answered_by, or None."""
    d = next((x for x in decisions() if x["id"] == did), None)
    if d is None or "answered_by" in d or _on_file():
        return d
    with read() as db:
        by = _answered_by(db).get(did)
    return {**d, "answered_by": by} if by else d


def session_event(key, ev, data, by=None):
    """`orch handoff`: what a role session (key `orchestrator` or `lead:<slug>`) leaves for the next one ('handoff')
    and its restarts ('restart'). Event rows with entity 'session', so the facts need no table and no schema step;
    the newest row of a kind is the one in force."""
    with tx() as db:
        _event(db, "session", key, ev, by=by, after=data)


def save_auto_handoff(key, meta, handle, session, within_s, fields, default_next, at=None):
    """The clear band's save, decided and written in one transaction so two terminals cannot both pass the check.
    The newest handoff row of `key` that is auto (a manual save between does not hide it): from another handle within
    within_s -> ("conflict", that handle), nothing written; from this session within within_s -> ("skipped", None);
    else the judgment fields of the newest handoff row, manual or auto, are copied into a new auto row with `meta`
    -> ("saved", None). `at` is the clock, epoch seconds."""
    at = time.time() if at is None else at
    with tx() as db:
        last = db.execute("SELECT ts, after FROM event WHERE entity='session' AND id=? AND ev='handoff' "
                          "AND json_extract(after, '$.auto') = 1 ORDER BY seq DESC LIMIT 1", (key,)).fetchone()
        if last:
            prev = json.loads(last["after"] or "null")
            prev = prev if isinstance(prev, dict) else {}
            try:
                age = at - dt.datetime.fromisoformat(last["ts"].replace("Z", "+00:00")).timestamp()
            except (AttributeError, ValueError):
                age = None
            if age is not None and age < within_s:
                if prev.get("handle") != handle:  # a row or a caller with no handle is another terminal too
                    return "conflict", prev.get("handle") or "an unknown terminal"
                if prev.get("session") == session:
                    return "skipped", None
        newest = last_session_event(key, "handoff", db)
        base = newest[1] if newest and isinstance(newest[1], dict) else {}
        carried = {f: base.get(f) for f in fields if base.get(f)}
        carried.setdefault("next", default_next)
        _event(db, "session", key, "handoff", by="orch handoff ready", after={**carried, **meta})
    return "saved", None


def last_session_event(key, ev, db=None):
    """(ts, data) of the newest `ev` row for key, or None."""
    def newest(db):
        r = db.execute("SELECT ts, after FROM event WHERE entity='session' AND id=? AND ev=? ORDER BY seq DESC LIMIT 1",
                       (key, ev)).fetchone()
        return (r["ts"], json.loads(r["after"] or "null")) if r else None
    if db is not None:
        return newest(db)
    with read() as db:
        return newest(db)


# ---------------------------------------------------------------- the board: items, rows, leads

def legacy_id(lane, item):
    """The content hash tidy named an item by before stable ids. item as board.json held it (owner_todo: its text)."""
    kind = LANES[lane][1]
    parts = ((item["title"], item["trigger"]) if lane == "later" else (item["title"], item.get("due") or "", item.get("after") or "")
             if lane == "todo" else (item["title"],) if lane == "doing" else (item,))
    return f"{kind}:{fleet.digest(*parts)[:8]}"


def _item(r):
    """An item row as the dict its lane held in board.json, with its id; owner_todo items are {id, title}."""
    d, lane = {"id": r["id"], "title": r["title"], "updated_at": r["updated_at"]}, r["lane"]
    opt = lambda **kw: d.update({k: v for k, v in kw.items() if v})  # noqa: E731
    opt(note=r["note"] if "note" in r.keys() else None)  # a DB no writer has opened since the note column came has none yet
    if lane == "doing":
        d.update(owner=r["who"], next=r["next"] or "")
        opt(waiting=bool(r["waiting"]), since=r["waiting_since"])
    elif lane == "later":
        d["trigger"] = r["trig"]
        opt(due=r["due"], who=r["who"])
    elif lane == "todo":
        d["who"] = r["who"]
        opt(due=r["due"], after=r["after_ref"])
        d["added"] = r["created_at"]
    return d


def _item_cols(lane, d):
    """ITEM_COLS' values for a dict as _item gives it."""
    return (d.get("owner") if lane == "doing" else "owner" if lane == "owner_todo" else d.get("who"), d["title"], d.get("next"),
            int(bool(d.get("waiting"))), d.get("since"), d.get("trigger"), d.get("due"), d.get("after"), d.get("note"))


def _insert_item(db, iid, lane, status, pos, cols, created, updated, close=(None, None, None, None), reopen=None):
    db.execute(f"INSERT INTO item (id, status, lane, pos, {', '.join(ITEM_COLS)}, created_at, updated_at, closed_at, "
               "close_by, close_rule, close_evidence, reopen_evidence) VALUES "
               f"({', '.join('?' * (4 + len(ITEM_COLS) + 7))})",
               (iid, status, lane, pos, *cols, created, updated, *close, reopen))


def _next_b(db):
    n = _counter(db, "b")
    db.execute("INSERT INTO counter (name, next) VALUES ('b', ?) ON CONFLICT(name) DO UPDATE SET next=excluded.next", (n + 1,))
    return f"b{n}"


def _row_out(r):
    row = {**json.loads(r["data"]), "id": r["id"], "parent": r["parent"]}
    if r["done_at"]:
        row["done_at"] = r["done_at"]
    return row


def _row_data(row):
    return json.dumps({k: v for k, v in row.items() if k not in ("id", "parent", "done_at")}, ensure_ascii=False, sort_keys=True)


def _insert_row(db, row, pos):
    db.execute("INSERT INTO board_row (id, parent, pos, data, done_at) VALUES (?,?,?,?,?)",
               (row["id"], row.get("parent"), pos, _row_data(row), row.get("done_at")))


def _lead_data(lead):
    return json.dumps({k: v for k, v in lead.items() if k not in ("name", "closed_at")}, ensure_ascii=False, sort_keys=True)


def _insert_lead(db, name, lead):
    db.execute("INSERT INTO lead (name, data, closed_at) VALUES (?,?,?)", (name, _lead_data(lead), lead.get("closed_at")))


def _board(db):
    """The open board in the shape board.json had: rows, order, leads and the four lanes, each item with its id."""
    out = {"v": 1, "rows": {}, "order": [], "leads": {}, "groups": {}, "annots": {}, **{lane: [] for lane in LANES}}
    for r in db.execute(f"SELECT * FROM item WHERE status IN {OPEN_SQL} ORDER BY lane, pos, CAST(substr(id, 2) AS INTEGER)"):
        out[r["lane"]].append(_item(r))
    for r in db.execute("SELECT * FROM board_row ORDER BY pos, id"):
        row = _row_out(r)
        if row.get("kind") == GROUP_KIND:  # a priority rule (board.priority_set), not a row: no report, tidy or count sees it
            out["groups"][r["id"]] = row
            continue
        if row.get("kind") == ANNOT_KIND:  # a card annotation (board.annot_set): the same
            out["annots"][r["id"]] = row
            continue
        out["rows"][r["id"]] = row
        out["order"].append(r["id"])
    for r in db.execute("SELECT * FROM lead ORDER BY name"):
        out["leads"][r["name"]] = {**json.loads(r["data"]), "name": r["name"],
                                   **({"closed_at": r["closed_at"]} if r["closed_at"] else {})}
    return out


def board(db=None):
    if db is not None:
        return _board(db)
    with read() as c:
        return _board(c)


def _put_items(db, old, new, by, ts):
    known = {d["id"]: (lane, d) for lane in LANES for d in old[lane]}
    seen = set()
    for lane in LANES:
        kept = []
        for pos, d in enumerate(new[lane]):
            iid = d.get("id")
            if iid in seen or iid and iid not in known:
                raise ValueError(f"{iid} is listed twice or is not an open item")
            if not iid:
                iid = d["id"] = _next_b(db)
                cols = _item_cols(lane, d)
                _insert_item(db, iid, lane, LANES[lane][0], pos, cols, d.get("added") or ts, ts)
                _event(db, "item", iid, "add", by, None, {**d, "lane": lane})
                continue
            seen.add(iid)
            kept.append(iid)
            was_lane, was = known[iid]
            cols = _item_cols(lane, d)
            if was_lane != lane or cols != _item_cols(was_lane, was):
                added = d.get("added") if lane == "todo" and was_lane != lane else None  # a to-do's date reads from it
                db.execute(f"UPDATE item SET status=?, lane=?, pos=?, updated_at=?, created_at=COALESCE(?, created_at), "
                           f"{', '.join(f'{c}=?' for c in ITEM_COLS)} WHERE id=?", (LANES[lane][0], lane, pos, ts, added, *cols, iid))
                _event(db, "item", iid, "update", by, {**was, "lane": was_lane}, {**d, "lane": lane})
            else:
                db.execute("UPDATE item SET pos=? WHERE id=?", (pos, iid))
        before = [d["id"] for d in old[lane] if d["id"] in set(kept)]
        if before != kept:
            _event(db, "lane", lane, "move", by, before, kept)
    for iid in set(known) - seen:
        lane, was = known[iid]
        db.execute("UPDATE item SET status='dropped', closed_at=?, close_by=?, close_rule='dropped', updated_at=? WHERE id=?",
                   (ts, by or "board", ts, iid))
        _event(db, "item", iid, "drop", by, {**was, "lane": lane})


def _put_rows(db, old, new, by):
    order = [k for k in new["order"] if k in new["rows"]] + [k for k in new["rows"] if k not in new["order"]]
    for pos, rid in enumerate(order):
        row = new["rows"][rid]
        if rid not in old["rows"]:
            _insert_row(db, row, pos)
            _event(db, "board_row", rid, "add", by, None, row)
            continue
        if row != old["rows"][rid]:
            db.execute("UPDATE board_row SET parent=?, data=?, done_at=? WHERE id=?",
                       (row.get("parent"), _row_data(row), row.get("done_at"), rid))
            _event(db, "board_row", rid, "update", by, old["rows"][rid], row)
        db.execute("UPDATE board_row SET pos=? WHERE id=?", (pos, rid))
    for rid in set(old["rows"]) - set(new["rows"]):
        db.execute("DELETE FROM board_row WHERE id=?", (rid,))
        _event(db, "board_row", rid, "drop", by, old["rows"][rid])


def _put_groups(db, old, new, by):
    """The priority rules: board_row rows of kind group, positioned after every row so row order is what it was."""
    for n, (rid, row) in enumerate(sorted(new["groups"].items())):
        if rid not in old["groups"]:
            _insert_row(db, row, GROUP_POS + n)
            _event(db, "board_row", rid, "add", by, None, row)
        elif row != old["groups"][rid]:
            db.execute("UPDATE board_row SET data=? WHERE id=?", (_row_data(row), rid))
            _event(db, "board_row", rid, "update", by, old["groups"][rid], row)
    for rid in set(old["groups"]) - set(new["groups"]):
        db.execute("DELETE FROM board_row WHERE id=?", (rid,))
        _event(db, "board_row", rid, "drop", by, old["groups"][rid])


def _put_annots(db, old, new, by):
    """The card annotations: board_row rows of kind annot, positioned after the rules. An annotation with no field left
    is deleted with its last value in the event."""
    for n, (rid, row) in enumerate(sorted(new["annots"].items())):
        if rid not in old["annots"]:
            _insert_row(db, row, ANNOT_POS + n)
            _event(db, "board_row", rid, "add", by, None, row)
        elif row != old["annots"][rid]:
            db.execute("UPDATE board_row SET data=? WHERE id=?", (_row_data(row), rid))
            _event(db, "board_row", rid, "update", by, old["annots"][rid], row)
    for rid in set(old["annots"]) - set(new["annots"]):
        db.execute("DELETE FROM board_row WHERE id=?", (rid,))
        _event(db, "board_row", rid, "drop", by, old["annots"][rid])


def row_origins():
    """{row id: the row as the import event kept it}: the original worktree and PR of a row the import cut. A row dropped
    or added again since is another row under the same id, and has no origin."""
    out = {}
    with read() as c:
        for r in c.execute("SELECT id, ev, before FROM event WHERE entity='board_row' AND ev IN ('import','add','drop') ORDER BY seq"):
            if r["ev"] == "import" and r["before"]:
                out[r["id"]] = json.loads(r["before"])
            else:
                out.pop(r["id"], None)
    return out


def _put_leads(db, old, new, by):
    for name, lead in new["leads"].items():
        if name not in old["leads"]:
            _insert_lead(db, name, lead)
            _event(db, "lead", name, "add", by, None, lead)
        elif lead != old["leads"][name]:
            db.execute("UPDATE lead SET data=?, closed_at=? WHERE name=?", (_lead_data(lead), lead.get("closed_at"), name))
            _event(db, "lead", name, "update", by, old["leads"][name], lead)


def change_board(fn, by=None):
    """Run fn(store) on the open board in one write transaction and write what changed, each change with its event: an
    item with no id gets the next b<N>, an open item no longer in any lane becomes dropped (its row stays), a row gone
    from store["rows"] is deleted with its last value in the event. Rewording or moving an item keeps its dict's id."""
    with tx() as db:
        old = _board(db)
        new = copy.deepcopy(old)
        out = fn(new)
        ts = now()
        _put_items(db, old, new, by, ts)
        _put_rows(db, old, new, by)
        _put_groups(db, old, new, by)
        _put_annots(db, old, new, by)
        _put_leads(db, old, new, by)
        return out


# ---------------------------------------------------------------- closing: an item's row, or a closure row

def resolve(db, iid):
    """The stable id of an id from before stable ids (alias); any other id as it is."""
    r = db.execute("SELECT id FROM alias WHERE old=?", (iid,)).fetchone()
    return r[0] if r else iid


def latest(db=None):
    """id -> what tidy.jsonl's last line held: {ev: done | reopen, id, ts, by, rule, evidence, label}. A reopen's evidence
    is the one it undid. Dropped items are not here: a drop is not a close."""
    def run(c):
        out = {}
        for r in c.execute("SELECT * FROM item WHERE status='done' OR reopen_evidence IS NOT NULL"):
            done = r["status"] == "done"
            out[r["id"]] = {"ev": "done" if done else "reopen", "id": r["id"], "ts": r["closed_at"] if done else r["updated_at"],
                            "by": r["close_by"], "rule": r["close_rule"], "label": r["title"],
                            "evidence": r["close_evidence"] if done else r["reopen_evidence"]}
        for r in c.execute("SELECT * FROM closure"):
            done = r["state"] == "done"
            out[r["unit"]] = {"ev": r["state"], "id": r["unit"], "ts": r["ts"], "by": r["by"], "rule": r["rule"],
                              "label": r["label"], "evidence": r["evidence"] if done else r["undone_evidence"]}
        # row:<id> is an alias of the card an annotation took the row's place in: a close of the old row is the card's.
        for r in c.execute("SELECT id, data FROM board_row WHERE id LIKE 'card:%'"):
            for old in json.loads(r["data"]).get("aliases") or ():
                if old in out and r["id"] not in out:
                    out[r["id"]] = {**out[old], "id": r["id"]}
        return out
    if db is not None:
        return run(db)
    with read() as c:
        return run(c)


def ext_closures(db=None):
    """{inbox key: closure row} for fleet.external_items; {} before the board is in the DB (tidy wrote resolve lines)."""
    def run(c):
        return {r["unit"][4:]: dict(r) for r in c.execute("SELECT * FROM closure WHERE unit LIKE 'ext:%'")}
    if db is not None:
        return run(db)
    try:
        with read(1000) as c:
            return run(c)
    except NotMigrated:
        return {}


def item_same(db, seen):
    """Whether an item is still open with every field tidy judged: updated_at alone is one second wide."""
    r = db.execute(f"SELECT * FROM item WHERE id=? AND status IN {OPEN_SQL}", (seen["id"],)).fetchone()
    return bool(r) and _item(r) == seen


def _item_now(db, iid):
    return db.execute("SELECT * FROM item WHERE id=?", (iid,)).fetchone()


def close_unit(db, line, mark=None):
    """Close line["id"] ({id, ts, by, rule, evidence, label}): its item, else a closure row. mark: an ext: unit's ext_mark."""
    iid = line["id"]
    before = _item_now(db, iid)
    if before:
        db.execute("UPDATE item SET status='done', closed_at=?, close_by=?, close_rule=?, close_evidence=?, reopen_evidence=NULL, "
                   "updated_at=? WHERE id=?", (line["ts"], line.get("by"), line.get("rule"), line.get("evidence"), line["ts"], iid))
        _event(db, "item", iid, "done", line.get("by"), dict(before), dict(_item_now(db, iid)), line["ts"])
        return
    was = db.execute("SELECT * FROM closure WHERE unit=?", (iid,)).fetchone()
    db.execute("INSERT INTO closure (unit, state, ts, by, rule, evidence, label, ext_mark) VALUES (?, 'done', ?,?,?,?,?,?) "
               "ON CONFLICT(unit) DO UPDATE SET state='done', ts=excluded.ts, by=excluded.by, rule=excluded.rule, "
               "evidence=excluded.evidence, label=excluded.label, undone_evidence=NULL, ext_mark=excluded.ext_mark",
               (iid, line["ts"], line.get("by"), line.get("rule"), line.get("evidence"), line.get("label"), mark))
    _event(db, "closure", iid, "done", line.get("by"), dict(was) if was else None,
           dict(db.execute("SELECT * FROM closure WHERE unit=?", (iid,)).fetchone()), line["ts"])


def _alias_closure(db, iid):
    """The unit that holds the close a card inherits (latest()): the row:<id> an annotation took the place of, when
    card:<worktree> has no close of its own. Any other id as it is."""
    if not iid.startswith("card:") or db.execute("SELECT 1 FROM closure WHERE unit=?", (iid,)).fetchone():
        return iid
    r = db.execute("SELECT data FROM board_row WHERE id=?", (iid,)).fetchone()
    return next((a for a in json.loads(r["data"]).get("aliases") or ()
                 if db.execute("SELECT 1 FROM closure WHERE unit=?", (a,)).fetchone()), iid) if r else iid


def reopen_unit(db, iid, ts, by, rule="manual", mark=None):
    """Undo a close in one transaction: status back to what the lane has open, or the closure row marked reopen, with the
    evidence it undid (tidy does not close again on it) and, for ext:, a new ext_mark."""
    iid = _alias_closure(db, iid)
    before = _item_now(db, iid)
    if before:
        db.execute("UPDATE item SET status=?, closed_at=NULL, close_by=?, close_rule=?, close_evidence=NULL, reopen_evidence=?, "
                   "updated_at=? WHERE id=?", (LANES[before["lane"]][0], by, rule, before["close_evidence"] or "", ts, iid))
        _event(db, "item", iid, "reopen", by, dict(before), dict(_item_now(db, iid)), ts)
        return
    was = db.execute("SELECT * FROM closure WHERE unit=?", (iid,)).fetchone()
    if was["state"] == "reopen":  # a card and its alias in one call: the close is already undone, with its evidence kept
        return
    db.execute("UPDATE closure SET state='reopen', ts=?, by=?, rule=?, evidence=NULL, undone_evidence=?, ext_mark=? WHERE unit=?",
               (ts, by, rule, was["evidence"], mark, iid))
    _event(db, "closure", iid, "reopen", by, dict(was), dict(db.execute("SELECT * FROM closure WHERE unit=?", (iid,)).fetchone()), ts)


def closing_events(keep=200):
    """The last closes and reopens as {at, ev, id, by, rule, evidence, label}, oldest first, for the Timeline."""
    out = []
    with read() as c:
        rows = c.execute("SELECT * FROM event WHERE entity IN ('item','closure','tidy') AND ev IN ('done','reopen') "
                         "ORDER BY seq DESC LIMIT ?", (keep,)).fetchall()
    for r in reversed(rows):
        a, done = json.loads(r["after"] or "{}"), r["ev"] == "done"
        if r["entity"] == "item":
            a = {"label": a.get("title"), "rule": a.get("close_rule"),
                 "evidence": a.get("close_evidence") if done else a.get("reopen_evidence")}
        elif r["entity"] == "closure":
            a = {**a, "evidence": a.get("evidence") if done else a.get("undone_evidence")}
        out.append({"at": r["ts"], "ev": r["ev"], "id": r["id"], "by": r["by"], "rule": a.get("rule"), "label": a.get("label"),
                    "evidence": a.get("evidence")})
    return out


def view():
    """The board, every close and the open decisions from one read snapshot: what one render reads."""
    with read() as db:
        return {"board": _board(db), "latest": latest(db), "decisions": decisions("open", db),
                "orders_unlinked": orders_unlinked(db)}


# ---------------------------------------------------------------- land requests (land_exec.py)

# An optional step: NEEDS leaves it out, so nothing else waits on it, and only land_exec reads the table.
LAND_STEP = "5-land-queue"
LAND_OPEN = ("queued", "running")
SCHEMA_LAND = (
    """CREATE TABLE land_request (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  program TEXT NOT NULL, home TEXT NOT NULL, repo TEXT NOT NULL, pr INTEGER NOT NULL,
  sha TEXT NOT NULL, base_sha TEXT, klass TEXT NOT NULL, requester TEXT,
  status TEXT NOT NULL CHECK (status IN ('queued','running','merged','refused','superseded')),
  code INTEGER, reason TEXT, merge_sha TEXT, audit TEXT, attempts INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, closed_at TEXT,
  told_at TEXT,
  CHECK ((status IN ('merged','refused','superseded')) = (closed_at IS NOT NULL))
)""",
    # Two programs homes may share a slug and a PR number; the home and repo keep their requests apart.
    "CREATE UNIQUE INDEX land_request_open ON land_request(home, repo, program, pr) WHERE status IN ('queued','running')",
)


def land_setup_command():
    return f"{main_checkout()}/bin/orch land-exec setup"


def land_ready(db):
    return db.execute("SELECT 1 FROM migration WHERE step=?", (LAND_STEP,)).fetchone() is not None


def land_setup(by="land-exec setup"):
    """Add the land_request table as a recorded step; a second run changes nothing. Returns what it did."""
    with tx() as db:
        if land_ready(db):
            return "already set up"
        for s in SCHEMA_LAND:
            db.execute(s)
        db.execute("INSERT INTO migration (step, ts, backup, sources, stage) VALUES (?,?,?,?, 'imported')",
                   (LAND_STEP, now(), "", "[]"))
        _event(db, "migration", LAND_STEP, "imported", by, None, {"tables": ["land_request"]})
        return "created land_request"


@contextlib.contextmanager
def _land_tx():
    with tx() as db:
        if not land_ready(db):
            raise NotMigrated(f"{path()}: the land queue is not set up: run {land_setup_command()}")
        yield db


def _land_row(r):
    return None if r is None else {**dict(r), "audit": json.loads(r["audit"]) if r["audit"] else None}


def land_put(program, home, repo, pr, sha, base_sha, klass, requester, by=None, only_new=False):
    """(row, outcome): outcome is "same" when an open request names this sha, "busy" when the executor is running an
    open request for another sha (nothing changes), else "new", after superseding a queued one for another sha.
    only_new: the auto-queue's rule, checked in the same transaction as the insert: when a request of any status
    already names this sha, nothing changes and the outcome is "seen" (a concurrent `orch land` may have moved the
    queue from this sha to another and back since the caller looked)."""
    with _land_tx() as db:
        if only_new:
            seen = db.execute("SELECT * FROM land_request WHERE home=? AND repo=? AND program=? AND pr=? AND sha=? "
                              "ORDER BY id DESC LIMIT 1", (home, repo, program, pr, sha)).fetchone()
            if seen:
                return _land_row(seen), "seen"
        old = db.execute("SELECT * FROM land_request WHERE home=? AND repo=? AND program=? AND pr=? "
                         "AND status IN ('queued','running')", (home, repo, program, pr)).fetchone()
        if old and old["sha"] == sha:
            if requester and old["requester"] != requester:
                db.execute("UPDATE land_request SET requester=?, updated_at=? WHERE id=?", (requester, now(), old["id"]))
                _event(db, "land_request", str(old["id"]), "requester", by, {"requester": old["requester"]},
                       {"requester": requester})
            return _land_row(db.execute("SELECT * FROM land_request WHERE id=?", (old["id"],)).fetchone()), "same"
        if old and old["status"] == "running":
            return _land_row(old), "busy"
        ts = now()
        if old:
            db.execute("UPDATE land_request SET status='superseded', reason=?, updated_at=?, closed_at=? WHERE id=?",
                       (f"a request for {sha[:8]} replaced it", ts, ts, old["id"]))
            _event(db, "land_request", str(old["id"]), "superseded", by, {"sha": old["sha"]}, {"sha": sha})
        cur = db.execute("INSERT INTO land_request (program, home, repo, pr, sha, base_sha, klass, requester, status, "
                         "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?, 'queued', ?, ?)",
                         (program, home, repo, pr, sha, base_sha, klass, requester, ts, ts))
        row = db.execute("SELECT * FROM land_request WHERE id=?", (cur.lastrowid,)).fetchone()
        _event(db, "land_request", str(row["id"]), "queued", by, None, dict(row))
        return _land_row(row), "new"


def land_seen(program, home, repo, pr, sha):
    """Has a request of any status ever named this program's PR at this head? A refused or merged one counts: the
    auto-queue asks once per head."""
    with _land_tx() as db:
        return db.execute("SELECT 1 FROM land_request WHERE home=? AND repo=? AND program=? AND pr=? AND sha=? LIMIT 1",
                          (home, repo, program, pr, sha)).fetchone() is not None


def land_get(rid):
    with _land_tx() as db:
        return _land_row(db.execute("SELECT * FROM land_request WHERE id=?", (rid,)).fetchone())


def land_rows(statuses=None, limit=200):
    """Requests, oldest first when filtered by status (the executor's order), else the newest `limit`."""
    with _land_tx() as db:
        if statuses:
            q = ",".join("?" * len(statuses))
            rows = db.execute(f"SELECT * FROM land_request WHERE status IN ({q}) ORDER BY id", tuple(statuses))
        else:
            rows = db.execute("SELECT * FROM (SELECT * FROM land_request ORDER BY id DESC LIMIT ?) ORDER BY id", (limit,))
        return [_land_row(r) for r in rows.fetchall()]


def land_claim(rid, by=None):
    """queued -> running, only if still queued: a supersede between the read and here wins."""
    with _land_tx() as db:
        cur = db.execute("UPDATE land_request SET status='running', attempts=attempts+1, updated_at=? "
                         "WHERE id=? AND status='queued'", (now(), rid))
        if cur.rowcount == 1:
            _event(db, "land_request", str(rid), "running", by)
        return cur.rowcount == 1


def land_finish(rid, status, code=None, reason=None, merge_sha=None, audit=None, by=None):
    """End a running request (merged, refused) or put it back in the queue (queued, with why it waits)."""
    if status not in ("queued", "merged", "refused"):
        raise ValueError(status)
    with _land_tx() as db:
        ts = now()
        cur = db.execute("UPDATE land_request SET status=?, code=?, reason=?, merge_sha=COALESCE(?, merge_sha), "
                         "audit=COALESCE(?, audit), updated_at=?, closed_at=? WHERE id=? AND status='running'",
                         (status, code, (reason or "")[:1000] or None, merge_sha,
                          json.dumps(audit, ensure_ascii=False, sort_keys=True) if audit is not None else None,
                          ts, None if status == "queued" else ts, rid))
        if cur.rowcount == 1 and status != "queued":
            _event(db, "land_request", str(rid), status, by, None,
                   {"code": code, "reason": reason, "merge_sha": merge_sha})
        return cur.rowcount == 1


def land_requeue_running(by=None):
    """Put back rows a dead round left running. Only the holder of the round lock calls this."""
    with _land_tx() as db:
        ids = [r[0] for r in db.execute("SELECT id FROM land_request WHERE status='running'")]
        db.execute("UPDATE land_request SET status='queued', updated_at=? WHERE status='running'", (now(),))
        for rid in ids:
            _event(db, "land_request", str(rid), "requeued", by)
        return ids


def land_told(rid):
    """Mark a refusal as put in the dashboard inbox."""
    with _land_tx() as db:
        db.execute("UPDATE land_request SET told_at=? WHERE id=? AND told_at IS NULL", (now(), rid))


# ---------------------------------------------------------------- owner orders (order.py)

# An optional step like the land queue: NEEDS leaves it out, and the brief skips its line until `orch order setup` ran.
# The table is order_ because ORDER is an SQL keyword.
ORDER_STEP = "5a-order"
ORDER_KINDS = ("order", "feedback")
SCHEMA_ORDER = (
    """CREATE TABLE order_ (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('order','feedback')),
  status TEXT NOT NULL CHECK (status IN ('open','done','dropped')),
  text TEXT NOT NULL,
  source TEXT,
  owner_action INTEGER NOT NULL DEFAULT 0 CHECK (owner_action IN (0,1)),
  by TEXT,
  created_at TEXT NOT NULL,
  closed_at TEXT, close_by TEXT,
  links_complete_at TEXT,
  reopen_evidence TEXT,
  CHECK ((status = 'open') = (closed_at IS NULL))
)""",
    # links_complete_at (the receiving session said every unit is linked) and reopen_evidence (the snapshot a Reopen
    # keeps) are the places PR 5c's automatic close reads; nothing writes them yet.
    # The link outlives its unit: nothing here points at a row of item, decision or the fleet.
    # resolves 0 marks a stand-in (the temporary to-do an import replaced): it shows where the order came from and is
    # never evidence that the order was carried out.
    """CREATE TABLE order_link (
  order_id TEXT NOT NULL REFERENCES order_(id),
  unit TEXT NOT NULL,
  resolves INTEGER NOT NULL DEFAULT 1 CHECK (resolves IN (0,1)),
  ts TEXT NOT NULL, by TEXT,
  PRIMARY KEY (order_id, unit)
)""",
)
# Columns the first 5a-order DBs lacked, as setup adds them to a DB that already has the step.
ORDER_COLUMNS = (("order_", "links_complete_at", "TEXT"), ("order_", "reopen_evidence", "TEXT"),
                 ("order_link", "resolves", "INTEGER NOT NULL DEFAULT 1 CHECK (resolves IN (0,1))"))
UNIT = re.compile(r"(?:card|agent):\S+|board:b\d+|decision:d\d+|pr:[\w.-]+/[\w.-]+#\d+")


def order_setup_command():
    return f"{main_checkout()}/bin/orch order setup"


def order_ready(db):
    return db.execute("SELECT 1 FROM migration WHERE step=?", (ORDER_STEP,)).fetchone() is not None


def order_setup(by="order setup"):
    """Add the order tables as a recorded step; a second run changes nothing. Returns what it did."""
    with tx() as db:
        if order_ready(db):
            # A DB set up before these columns existed gets them; the links it already has stay real.
            added = []
            for t, c, ddl in ORDER_COLUMNS:
                if c not in {r[1] for r in db.execute(f"PRAGMA table_info({t})")}:
                    db.execute(f"ALTER TABLE {t} ADD COLUMN {c} {ddl}")
                    added.append(f"{t}.{c}")
            if added:
                _event(db, "migration", ORDER_STEP, "upgraded", by, None, {"columns": added})
            return f"added {', '.join(added)}" if added else "already set up"
        for s in SCHEMA_ORDER:
            db.execute(s)
        db.execute("INSERT INTO migration (step, ts, backup, sources, stage) VALUES (?,?,?,?, 'imported')",
                   (ORDER_STEP, now(), "", "[]"))
        _event(db, "migration", ORDER_STEP, "imported", by, None, {"tables": ["order_", "order_link"]})
        return "created order_, order_link"


@contextlib.contextmanager
def _order_tx():
    with tx() as db:
        if not order_ready(db):
            raise NotMigrated(f"{path()}: orders are not set up: run {order_setup_command()}")
        yield db


def _order_out(db, r):
    links = db.execute("SELECT unit, resolves FROM order_link WHERE order_id=? ORDER BY ts, unit", (r["id"],)).fetchall()
    return {**dict(r), "owner_action": bool(r["owner_action"]),
            "links": [x[0] for x in links], "stand_ins": [x[0] for x in links if not x[1]]}


def order_add(text, kind="order", source=None, owner_action=False, by=None, dedupe=False):
    """(order, True) for a new one. With dedupe, a row that already has this source comes back as (row, False)."""
    if kind not in ORDER_KINDS:
        raise ValueError(f"kind is one of {', '.join(ORDER_KINDS)}")
    text = fleet.mask(text.strip())
    source = fleet.mask(source) if source else source
    if not text:
        raise ValueError("an order needs its text")
    with _order_tx() as db:
        if dedupe and source:
            old = db.execute("SELECT * FROM order_ WHERE source=?", (source,)).fetchone()
            if old and old["text"] != text:  # the same key for other words is a clash, not a repeat
                raise ValueError(f"{old['id']} already holds source {source} with other text; it was not changed")
            if old:
                return _order_out(db, old), False
        n = _counter(db, "o")
        db.execute("INSERT INTO counter (name, next) VALUES ('o', ?) ON CONFLICT(name) DO UPDATE SET next=excluded.next", (n + 1,))
        oid = f"o{n}"
        db.execute("INSERT INTO order_ (id, kind, status, text, source, owner_action, by, created_at) "
                   "VALUES (?,?, 'open', ?,?,?,?,?)", (oid, kind, text, source, int(bool(owner_action)), by, now()))
        row = _order_out(db, db.execute("SELECT * FROM order_ WHERE id=?", (oid,)).fetchone())
        _event(db, "order", oid, "add", by, None, {k: row[k] for k in ("kind", "source", "owner_action", "text")})
        return row, True


def order_link(oid, unit, by=None, stand_in=False):
    """True when the link is new. The unit is checked for form, and a board item or decision for existing. A stand-in
    link does not count as carrying the order; linking the same unit again as real turns it real."""
    if not UNIT.fullmatch(unit):
        raise ValueError("unit is card:<worktree> | agent:<id> | board:<bN> | decision:<dN> | pr:<owner/repo#number>")
    with _order_tx() as db:
        if not db.execute("SELECT 1 FROM order_ WHERE id=?", (oid,)).fetchone():
            raise ValueError(f"no order {oid}")
        kind, _, ident = unit.partition(":")
        if kind == "board" and not db.execute("SELECT 1 FROM item WHERE id=?", (ident,)).fetchone():
            raise ValueError(f"no board item {ident}")
        if kind == "decision" and not db.execute("SELECT 1 FROM decision WHERE id=?", (ident,)).fetchone():
            raise ValueError(f"no decision {ident}")
        cur = db.execute("INSERT OR IGNORE INTO order_link (order_id, unit, resolves, ts, by) VALUES (?,?,?,?,?)",
                         (oid, unit, 0 if stand_in else 1, now(), by))
        if cur.rowcount:
            _event(db, "order", oid, "link", by, None, {"unit": unit, "stand_in": bool(stand_in)})
            if not stand_in:
                _unmark(db, oid, by)
            return True
        if not stand_in and db.execute("UPDATE order_link SET resolves=1 WHERE order_id=? AND unit=? AND resolves=0",
                                       (oid, unit)).rowcount:
            _event(db, "order", oid, "link", by, {"stand_in": True}, {"unit": unit, "stand_in": False})
            _unmark(db, oid, by)
            return True
        return False


def _unmark(db, oid, by):
    """A real link added after the receiving session said every unit was linked takes that word back: it has to say it again."""
    r = db.execute("SELECT links_complete_at FROM order_ WHERE id=?", (oid,)).fetchone()
    if r and r[0]:
        db.execute("UPDATE order_ SET links_complete_at=NULL WHERE id=?", (oid,))
        _event(db, "order", oid, "links_open", by, {"links_complete_at": r[0]}, {"links_complete_at": None, "why": "new link"})


def order_close(oid, status, by=None, evidence=None, reason=None, check=None, snapshot=None):
    """Open -> done or dropped. Returns the row; a closed order keeps its first close. evidence and reason go into the
    close event, and so does snapshot, the normalized evidence of the units a Reopen keeps (order_close.py). check(row)
    runs inside the write transaction on an open order and raises ValueError to refuse the close, and may return the
    evidence to record: the close is judged on the links as they are when it is written."""
    if status not in ("done", "dropped"):
        raise ValueError(status)
    with _order_tx() as db:
        r = db.execute("SELECT * FROM order_ WHERE id=?", (oid,)).fetchone()
        if not r:
            raise ValueError(f"no order {oid}")
        if r["status"] == "open":
            if check:
                evidence = check(_order_out(db, r)) or evidence
            db.execute("UPDATE order_ SET status=?, closed_at=?, close_by=? WHERE id=?", (status, now(), by, oid))
            _event(db, "order", oid, status, by, {"status": "open"},
                   {"status": status, **({"evidence": evidence} if evidence else {}),
                    **({"snapshot": snapshot} if snapshot else {}), **({"reason": fleet.mask(reason)} if reason else {})})
        return _order_out(db, db.execute("SELECT * FROM order_ WHERE id=?", (oid,)).fetchone())


def order_get(oid, require_open=False):
    """The order with its links. require_open: a closed one is a ValueError, so a unit is never started for an order
    that no longer asks for work."""
    with _order_tx() as db:
        r = db.execute("SELECT * FROM order_ WHERE id=?", (oid,)).fetchone()
        if not r:
            raise ValueError(f"no order {oid}")
        if require_open and r["status"] != "open":
            raise ValueError(f"{oid} is {r['status']}: reopen it (orch order reopen {oid}) before linking new work to it")
        return _order_out(db, r)


def order_reopen(oid, by=None, snapshot=None):
    """Done or dropped -> open. The receiving session must say again that every unit is linked, so the mark goes with
    the close. reopen_evidence keeps the evidence the close stood on, so the automatic close does not shut the order
    again on the same evidence: the snapshot the close event holds (every close by `orch order` writes one it could
    read), else `snapshot` (the caller's reading of the units now). Neither: EVIDENCE_UNKNOWN, and the next pass that
    can read the units makes what it reads the baseline instead of closing (order_baseline)."""
    with _order_tx() as db:
        r = db.execute("SELECT * FROM order_ WHERE id=?", (oid,)).fetchone()
        if not r:
            raise ValueError(f"no order {oid}")
        if r["status"] != "open":
            closed = db.execute("SELECT after FROM event WHERE entity='order' AND id=? AND ev IN ('done','dropped') "
                                "ORDER BY seq DESC LIMIT 1", (oid,)).fetchone()
            kept = (json.loads(closed[0] or "{}").get("snapshot") if closed else None) or snapshot or EVIDENCE_UNKNOWN
            db.execute("UPDATE order_ SET status='open', closed_at=NULL, close_by=NULL, links_complete_at=NULL, "
                       "reopen_evidence=? WHERE id=?", (kept, oid))
            _event(db, "order", oid, "reopen", by, {"status": r["status"], "closed_at": r["closed_at"],
                                                    "links_complete_at": r["links_complete_at"]},
                   {"status": "open", "reopen_evidence": kept})
        return _order_out(db, db.execute("SELECT * FROM order_ WHERE id=?", (oid,)).fetchone())


def auto_still(row, links_seen, snapshot):
    """Raise ValueError unless an open order is still what the judgement read: the same real links, the receiving
    session's word that they are all there, not the owner's to act on, and evidence other than a reopen kept."""
    real = sorted(u for u in row["links"] if u not in row["stand_ins"])
    if (row["status"] != "open" or real != sorted(links_seen) or not row["links_complete_at"] or row["owner_action"]
            or row["reopen_evidence"] in (snapshot, EVIDENCE_UNKNOWN)):
        raise ValueError(f"{row['id']} changed while it was judged")


EVIDENCE_UNKNOWN = "unknown"  # reopen_evidence of an order reopened while its units could not be read


def order_auto(oid, snapshot, units, links_seen, close=False, recheck=None, by="auto"):
    """The automatic close of an order, or with close=False its shadow: one `would_close` event saying what tidy would
    have closed on, nothing else changes. Everything is read again inside the write transaction: the order (links,
    the receiving session's word, owner action, kept evidence) by auto_still, and recheck(db) the units that live in
    this DB. True when written; False when something moved since the order was judged, or (shadow) this evidence was
    recorded before, since a tick finds the same candidate every minute."""
    with _order_tx() as db:
        r = db.execute("SELECT * FROM order_ WHERE id=?", (oid,)).fetchone()
        if not r:
            return False
        try:
            auto_still(_order_out(db, r), links_seen, snapshot)
            if recheck:
                recheck(db)
        except ValueError:
            return False
        if close:
            db.execute("UPDATE order_ SET status='done', closed_at=?, close_by=? WHERE id=?", (now(), by, oid))
            _event(db, "order", oid, "done", by, {"status": "open"},
                   {"status": "done", "evidence": {"rule": "order_units_done", "units": units}, "snapshot": snapshot})
            return True
        if any(json.loads(a[0] or "{}").get("snapshot") == snapshot for a in db.execute(
                "SELECT after FROM event WHERE entity='order' AND id=? AND ev='would_close'", (oid,))):
            return False
        _event(db, "order", oid, "would_close", by, None, {"rule": "order_units_done", "snapshot": snapshot, "units": units})
        return True


def order_baseline(oid, snapshot, links_seen):
    """An order reopened with its units unread has no evidence to compare: the first readable pass records what it
    reads as reopen_evidence, and the order closes only when that changes. True when recorded."""
    with _order_tx() as db:
        r = db.execute("SELECT * FROM order_ WHERE id=?", (oid,)).fetchone()
        if not r or r["status"] != "open" or r["reopen_evidence"] != EVIDENCE_UNKNOWN:
            return False
        db.execute("UPDATE order_ SET reopen_evidence=? WHERE id=?", (snapshot, oid))
        _event(db, "order", oid, "baseline", "auto", {"reopen_evidence": EVIDENCE_UNKNOWN}, {"reopen_evidence": snapshot})
        return True


def order_links_complete(oid, by=None, undo=False):
    """The receiving session says every unit the order needs is linked; PR 5c reads the mark before it may close the
    order. Needs an open order with a real link: a stand-in carries nothing. undo clears the mark."""
    with _order_tx() as db:
        r = db.execute("SELECT * FROM order_ WHERE id=?", (oid,)).fetchone()
        if not r:
            raise ValueError(f"no order {oid}")
        if r["status"] != "open":
            raise ValueError(f"{oid} is {r['status']}")
        if not undo and not db.execute("SELECT 1 FROM order_link WHERE order_id=? AND resolves=1", (oid,)).fetchone():
            raise ValueError(f"{oid} has no unit linked yet: link the card, board item, decision or PR first")
        if bool(r["links_complete_at"]) != (not undo):
            at = None if undo else now()
            db.execute("UPDATE order_ SET links_complete_at=? WHERE id=?", (at, oid))
            _event(db, "order", oid, "links_open" if undo else "links_complete", by, {"links_complete_at": r["links_complete_at"]},
                   {"links_complete_at": at})
        return _order_out(db, db.execute("SELECT * FROM order_ WHERE id=?", (oid,)).fetchone())


def orders(status=None, db=None):
    """Orders oldest first with their links; None while the tables are not set up."""
    if db is None:
        with read() as db:
            return orders(status, db)
    if not order_ready(db):
        return None
    q, args = ("WHERE status=?", (status,)) if status else ("", ())
    return [_order_out(db, r) for r in db.execute(f"SELECT * FROM order_ {q} ORDER BY created_at, CAST(substr(id,2) AS INTEGER)", args)]


def order_sources(sources):
    """{source: stored text} for those of `sources` an order already carries: what an import took, so what is left is
    not migrated."""
    with read() as db:
        if not order_ready(db):
            raise NotMigrated(f"{path()}: orders are not set up: run {order_setup_command()}")
        want = {fleet.mask(s) for s in sources}  # stored masked, as order_add stores them
        return {s: t for s, t in db.execute("SELECT source, text FROM order_ WHERE source IS NOT NULL") if s in want}


def orders_unlinked(db):
    """Open orders no real unit carries (no link, or only stand-ins), oldest first; None while the tables are not set
    up. The brief ages them."""
    if not order_ready(db):
        return None
    return [dict(r) for r in db.execute(
        "SELECT id, kind, created_at, text FROM order_ WHERE status='open' "
        "AND NOT EXISTS (SELECT 1 FROM order_link WHERE order_id=order_.id AND resolves=1) "
        "ORDER BY created_at, CAST(substr(id,2) AS INTEGER)")]


# ---------------------------------------------------------------- acks and the stage table (acks.py, stages.py)

# An optional step like the land queue: NEEDS leaves it out, so every reader keeps working on acks.jsonl and stages.json
# until `orch db state` moved them in. After it both files are tombstones (a directory in their place, so a legacy
# writer fails loudly) and these tables are the only copy; the backup directory keeps the files themselves.
STATE_STEP = "6-state"
STATE_FILES = ("acks.jsonl", "stages.json")
STATE_LOCKS = ("acks.lock", "stages.lock")
ACK_KINDS = ("stage", "board", "req")
STAGE_COLS = ("merge", "dev", "prod")
STAGE_MARKS = ("ok", "fail", "partial", "checking", "na")
PR_KEY = re.compile(r"([\w.-]+/[\w.-]+)#(\d+)")
SCHEMA_STATE = (
    # A reference to a GitHub PR, never canonical: the key plus what a screen or a rule decides on. repo is lower-cased
    # (one key for a repository whose case differs between sources); name is the nameWithOwner as the source wrote it.
    # Rows made before a collector saw the PR are 'unknown'.
    """CREATE TABLE pr_ref (
  repo TEXT NOT NULL, number INTEGER NOT NULL, name TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('unknown','open','merged','closed')),
  draft INTEGER NOT NULL DEFAULT 0 CHECK (draft IN (0,1)),
  head_sha TEXT, base_ref TEXT, author TEXT, assignee TEXT, ci TEXT, review_decision TEXT, merge_state TEXT,
  merged_at TEXT, merge_sha TEXT, closed_at TEXT,
  last_seen_at TEXT NOT NULL, refreshed_by TEXT NOT NULL,
  PRIMARY KEY (repo, number),
  CHECK (state = 'merged' OR merged_at IS NULL)
) WITHOUT ROWID""",
    """CREATE TABLE stage_row (
  id TEXT PRIMARY KEY, parent TEXT REFERENCES stage_row(id),
  grp INTEGER NOT NULL DEFAULT 0 CHECK (grp IN (0,1)), overlay INTEGER NOT NULL DEFAULT 0 CHECK (overlay IN (0,1)),
  title TEXT, pr_repo TEXT, pr_number INTEGER, created_at TEXT NOT NULL,
  done_at TEXT,
  pos INTEGER NOT NULL,
  program TEXT GENERATED ALWAYS AS (CASE WHEN instr(id, '/') > 0 THEN substr(id, 1, instr(id, '/') - 1) END) VIRTUAL,
  FOREIGN KEY (pr_repo, pr_number) REFERENCES pr_ref(repo, number)
)""",
    """CREATE TABLE stage_cell (
  row_id TEXT NOT NULL REFERENCES stage_row(id),
  col TEXT NOT NULL CHECK (col IN ('merge','dev','prod')),
  mark TEXT NOT NULL CHECK (mark IN ('ok','fail','partial','checking','na')),
  evidence TEXT, by TEXT, ts TEXT NOT NULL,
  PRIMARY KEY (row_id, col)
) WITHOUT ROWID""",
    "CREATE TABLE stage_env (env TEXT PRIMARY KEY CHECK (env IN ('dev','prod')), version TEXT NOT NULL, ts TEXT NOT NULL)",
    "CREATE TABLE stage_next (pos INTEGER PRIMARY KEY, text TEXT NOT NULL)",
    # The owner saw it. History is the data: the fold of the events is the acked set. key is a stage row id or a brief row
    # key (a board row id, an alias, fleet:<id>::<path>, agent:<id>); derived rows are not tables, so it is no foreign key.
    """CREATE TABLE ack_event (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
  ev TEXT NOT NULL CHECK (ev IN ('ack','unack')),
  kind TEXT NOT NULL CHECK (kind IN ('stage','board','req')),
  key TEXT NOT NULL
)""",
    "CREATE INDEX ack_event_key ON ack_event(kind, key, seq)",
)
STATE_TABLES = ("ack_event", "stage_next", "stage_env", "stage_cell", "stage_row", "pr_ref")  # dropped in this order


def _legacy(step):
    if step == SENDLOG_STEP:  # once recorded, the path the step moved, not the one this environment would pick
        row = _step_row(step)
        return tuple(json.loads(row["sources"])) if row else (str(sendlog_file()),)
    return STATE_FILES if step == STATE_STEP else LEGACY.get(step, ())


def state_ready():
    """Whether acks and the stage table live in the DB; False without a DB or before `orch db state`."""
    return STATE_STEP in migrated_steps()


def state_command():
    return f"{main_checkout()}/bin/orch db state"


def pr_split(text):
    """('owner/repo', number) of an `owner/repo#N` text, else None."""
    m = PR_KEY.fullmatch((text or "").strip())
    return (m.group(1), int(m.group(2))) if m else None


def _pr_stub(db, repo, number, by):
    """The reference row a stage row points at, made 'unknown' when no collector has seen the PR yet."""
    db.execute("INSERT OR IGNORE INTO pr_ref (repo, number, name, state, last_seen_at, refreshed_by) "
               "VALUES (?,?,?, 'unknown', ?, ?)", (repo.lower(), number, repo, now(), by))


def _ack_fold(db):
    """The fold in SQL: an id is acked when its newest event is an ack."""
    return {f"{r['kind']}:{r['key']}": r["ts"] for r in db.execute(
        "SELECT kind, key, ts FROM ack_event e WHERE ev='ack' AND seq="
        "(SELECT max(seq) FROM ack_event WHERE kind=e.kind AND key=e.key)")}


def ack_fold():
    """id (`stage:<row>`, `board:<key>`) -> when it was acked. Callers check state_ready() first: before it the acks are
    in acks.jsonl, which acks.py reads."""
    with read() as db:
        return _ack_fold(db)


def ack_change(fn, ts):
    """fn(have) -> (events [(ev, id)], result), run in one transaction with the acked set; the events are appended."""
    with tx() as db:
        events, out = fn(_ack_fold(db))
        for ev, aid in events:
            kind, key = aid.split(":", 1)
            db.execute("INSERT INTO ack_event (ts, ev, kind, key) VALUES (?,?,?,?)", (ts, ev, kind, key))
        return out


def _stage_hidden(rows, cutoff):
    """Ids of finished rows older than the cutoff and of the rows under them: stored, not shown."""
    # An overlay over a generated row is not aged out: hidden, the row it closes would open again (it sits on nothing
    # once that row is gone, and merged_counted ignores it).
    gone = {k for k, r in rows.items() if r.get("done_at") and r["done_at"] < cutoff and not r.get("overlay")}
    while more := {k for k, r in rows.items() if r.get("parent") in gone} - gone:
        gone |= more
    return gone


def _stage_load(db):
    rows = {}
    for r in db.execute("SELECT s.*, p.name FROM stage_row s LEFT JOIN pr_ref p ON p.repo=s.pr_repo AND p.number=s.pr_number "
                        "ORDER BY s.pos, s.id"):
        row = {"id": r["id"], "cells": {}, "created_at": r["created_at"]}
        for k in ("title", "parent", "done_at"):
            if r[k] is not None:
                row[k] = r[k]
        if r["name"] is not None:
            row["pr"] = f"{r['name']}#{r['pr_number']}"
        if r["grp"]:
            row["group"] = True
        if r["overlay"]:
            row["overlay"] = True
        rows[r["id"]] = row
    for c in db.execute("SELECT row_id, col, mark, evidence, by, ts FROM stage_cell"):
        rows[c["row_id"]]["cells"][c["col"]] = {"mark": c["mark"], "evidence": c["evidence"], "by": c["by"], "at": c["ts"]}
    env = {r["env"]: r["version"] for r in db.execute("SELECT env, version FROM stage_env")}
    if env:  # the file kept one time for both: the latest
        env["at"] = db.execute("SELECT max(ts) FROM stage_env").fetchone()[0]
    return {"rows": rows, "order": list(rows), "env": env,
            "next": [r["text"] for r in db.execute("SELECT text FROM stage_next ORDER BY pos")]}


def stage_read_in(db, keep_s):
    """The stage table in the shape stages.json had, {rows, order, env, next}, without finished rows older than keep_s
    (and the rows under them): they stay stored, only hidden."""
    st = _stage_load(db)
    hide = _stage_hidden(st["rows"], fleet.iso(fleet.utcnow() - dt.timedelta(seconds=keep_s)))
    st["rows"] = {k: r for k, r in st["rows"].items() if k not in hide}
    st["order"] = [k for k in st["order"] if k in st["rows"]]
    return st


def stage_read(keep_s):
    """stage_read_in on its own snapshot. Callers check state_ready() first: before it stages.py reads stages.json."""
    with read() as db:
        return stage_read_in(db, keep_s)


def _stage_save(db, before, after, by):
    """Write what fn changed in `after` (the shape of stage_read). A row in `before` and not in `after` is dropped."""
    for rid, r in after["rows"].items():
        if r.get("pr") and not pr_split(r["pr"]):  # nothing else holds the text, so it is refused, not dropped
            raise ValueError(f"{rid}: pr must look like owner/repo#N, got {r['pr']!r}")
    db.execute("PRAGMA defer_foreign_keys=ON")
    gone = set(before["rows"].keys() - after["rows"].keys())
    todo = set(gone)
    while todo:  # finished rows past retention are stored but hidden, so `before` does not list them: they go with the parent
        todo = {r[0] for r in db.execute(f"SELECT id FROM stage_row WHERE parent IN ({','.join('?' * len(todo))})",
                                         tuple(todo))} - gone
        gone |= todo
    for rid in gone:
        db.execute("DELETE FROM stage_cell WHERE row_id=?", (rid,))
        db.execute("DELETE FROM stage_row WHERE id=?", (rid,))
    order = [k for k in after["order"] if k in after["rows"]] + [k for k in after["rows"] if k not in after["order"]]
    for pos, rid in enumerate(order):
        r = after["rows"][rid]
        pr = pr_split(r.get("pr"))
        if pr:
            _pr_stub(db, *pr, by)
        db.execute("INSERT INTO stage_row (id, parent, grp, overlay, title, pr_repo, pr_number, created_at, done_at, pos) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET parent=excluded.parent, grp=excluded.grp, "
                   "overlay=excluded.overlay, title=excluded.title, pr_repo=excluded.pr_repo, pr_number=excluded.pr_number, "
                   "done_at=excluded.done_at, pos=excluded.pos",
                   (rid, r.get("parent"), int(bool(r.get("group"))), int(bool(r.get("overlay"))), r.get("title"),
                    pr[0].lower() if pr else None, pr[1] if pr else None, r.get("created_at") or now(), r.get("done_at"), pos))
        db.execute("DELETE FROM stage_cell WHERE row_id=?", (rid,))
        for col, c in r["cells"].items():
            db.execute("INSERT INTO stage_cell (row_id, col, mark, evidence, by, ts) VALUES (?,?,?,?,?,?)",
                       (rid, col, c["mark"], c.get("evidence"), c.get("by"), c.get("at") or now()))
    db.execute("DELETE FROM stage_env")
    for env in ("dev", "prod"):
        if after["env"].get(env):
            db.execute("INSERT INTO stage_env (env, version, ts) VALUES (?,?,?)",
                       (env, after["env"][env], after["env"].get("at") or now()))
    db.execute("DELETE FROM stage_next")
    for pos, text in enumerate(after["next"]):
        db.execute("INSERT INTO stage_next (pos, text) VALUES (?,?)", (pos, text))


def stage_change(fn, keep_s, by="orch stage"):
    """Run fn(table) on the visible stage table in one transaction and store what it changed; returns fn's result.
    Callers check state_ready() first: before it stages.py writes stages.json itself."""
    with tx() as db:
        visible = stage_read_in(db, keep_s)
        after = copy.deepcopy(visible)
        out = fn(after)
        _stage_save(db, visible, after, by)
        return out


def _state_import(db, acks_lines, table, by="import"):
    """Parse the two legacy files into the tables; returns the counts of what was skipped and why, never row text."""
    skipped = {"ack_bad": 0, "stage_mark": 0, "stage_pr": 0, "stage_col": 0}
    n = {"acks": 0, "rows": 0, "cells": 0}
    for r in acks_lines:
        aid = r.get("id") if isinstance(r, dict) else None
        kind, _, key = aid.partition(":") if isinstance(aid, str) else ("", "", "")
        if not (isinstance(r, dict) and r.get("ev") in ("ack", "unack") and kind in ACK_KINDS and key
                and isinstance(r.get("ts"), str)):
            skipped["ack_bad"] += 1  # counted, never aborts the import
            continue
        db.execute("INSERT INTO ack_event (ts, ev, kind, key) VALUES (?,?,?,?)", (r["ts"], r["ev"], kind, key))
        n["acks"] += 1
    db.execute("PRAGMA defer_foreign_keys=ON")
    rows = table["rows"]
    order = [k for k in table["order"] if k in rows] + [k for k in rows if k not in table["order"]]
    for pos, rid in enumerate(order):
        r = rows[rid]
        pr = pr_split(r.get("pr"))
        if r.get("pr") and not pr:
            skipped["stage_pr"] += 1
        if pr:
            _pr_stub(db, *pr, by)
        db.execute("INSERT INTO stage_row (id, parent, grp, overlay, title, pr_repo, pr_number, created_at, done_at, pos) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (rid, r.get("parent"), int(bool(r.get("group"))), int(bool(r.get("overlay"))), r.get("title"),
                    pr[0].lower() if pr else None, pr[1] if pr else None, r.get("created_at") or now(), r.get("done_at"), pos))
        n["rows"] += 1
        for col, c in (r.get("cells") or {}).items():
            if col not in STAGE_COLS:
                skipped["stage_col"] += 1
            elif not isinstance(c, dict) or c.get("mark") not in STAGE_MARKS:
                skipped["stage_mark"] += 1
            else:
                db.execute("INSERT INTO stage_cell (row_id, col, mark, evidence, by, ts) VALUES (?,?,?,?,?,?)",
                           (rid, col, c["mark"], c.get("evidence"), c.get("by"), c.get("at") or now()))
                n["cells"] += 1
    env = table["env"]
    for e in ("dev", "prod"):
        if env.get(e):
            db.execute("INSERT INTO stage_env (env, version, ts) VALUES (?,?,?)", (e, str(env[e]), env.get("at") or now()))
    for pos, text in enumerate(table["next"]):
        db.execute("INSERT INTO stage_next (pos, text) VALUES (?,?)", (pos, str(text)))
    return n, skipped


def _state_sources(sd):
    """(ack events, stage table) read from the two files, strictly: a line or file that does not parse stops the step
    before anything is written, so the files stay as they are. A file that is not there is empty."""
    for name in STATE_FILES:  # a tombstone, or a missing file with its .restore beside it, is what a rollback cut short leaves
        if (sd / name).is_dir() or (not (sd / name).exists() and (sd / (name + ".restore")).exists()):
            raise Refused(f"{sd / name} is a tombstone or missing but {STATE_STEP} is not in {DB_NAME}: a rollback was cut short. "
                          f"Put the file back by hand ({name}.restore beside it, or the export), then run {state_command()}")
    bad, events = [], []
    if (sd / "acks.jsonl").is_file():
        for i, line in enumerate((sd / "acks.jsonl").read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                bad.append(f"acks.jsonl line {i}")
    table = {"rows": {}, "order": [], "env": {}, "next": []}
    if (sd / "stages.json").is_file():
        try:
            st = json.loads((sd / "stages.json").read_text())
            if not isinstance(st, dict):
                raise ValueError
            table = {"rows": st.get("rows") or {}, "order": st.get("order") or [], "env": st.get("env") or {},
                     "next": st.get("next") or []}
        except ValueError:
            bad.append("stages.json")
    if bad:
        raise Refused(f"{', '.join(bad)} does not parse; nothing was moved or changed. Fix or move it aside, then run "
                      f"{state_command()} again")
    return events, table


def state_setup(by="orch db state", auto=False):
    """Move acks.jsonl and stages.json into the tables: back the files up, import in one transaction, record the step with
    each file's sha256, tombstone the files. A second run finishes a tombstone a crash left and otherwise changes nothing.
    auto: the call setup makes after an update; a DB that is not migrated yet is skipped, not an error."""
    sd = fleet.state_dir()
    steps = migrated_steps()
    if STATE_STEP in steps:
        return _finish(STATE_STEP, _step_row(STATE_STEP))
    try:
        open_db().close()  # NotMigrated, with the command, when the DB or a data step is missing
    except NotMigrated as e:
        if auto:
            return f"skipped: {e}"
        raise
    _state_sources(sd)  # refuses before a lock or a backup exists
    with contextlib.ExitStack() as stack:
        for name in STATE_LOCKS:  # the writers of both files hold these while they write
            fcntl.flock(stack.enter_context((sd / name).open("a")), fcntl.LOCK_EX)
        if STATE_STEP in migrated_steps():  # a run that held the locks first finished it
            return _finish(STATE_STEP, _step_row(STATE_STEP))
        acks_lines, table = _state_sources(sd)  # read again under the locks, so the backup, the hash and the import agree
        ts = dt.datetime.now(dt.timezone.utc)
        backup, sources = _backup_files(sd, STATE_STEP, ts)
        with tx() as db:
            for ddl in SCHEMA_STATE:
                db.execute(ddl)
            n, skipped = _state_import(db, acks_lines, table)
            db.execute("INSERT INTO migration (step, ts, backup, sources, stage) VALUES (?,?,?,?, 'imported')",
                       (STATE_STEP, now(), str(backup), json.dumps(sources)))
            _event(db, "migration", STATE_STEP, "imported", by, None, {"backup": str(backup), "imported": n, "skipped": skipped})
    done = _finish(STATE_STEP, _step_row(STATE_STEP))
    left = ", ".join(f"{k} {v}" for k, v in skipped.items() if v)
    return f"{done}: {n['acks']} ack events, {n['rows']} stage rows, {n['cells']} cells" + (f"; skipped {left}" if left else "")


def state_export(dest):
    """acks.jsonl and stages.json in the shapes older code reads, into dest. env.at is the latest stage_env time; the stage
    table is written whole, hidden rows included (retention only hides)."""
    with read() as db:
        events = [{"ev": r["ev"], "id": f"{r['kind']}:{r['key']}", "ts": r["ts"]}
                  for r in db.execute("SELECT ev, kind, key, ts FROM ack_event ORDER BY seq")]
        table = _stage_load(db)
    dest = pathlib.Path(dest).expanduser()
    dest.mkdir(mode=0o700, parents=True, exist_ok=True)
    fleet.write_atomic(dest / "acks.jsonl", "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events))
    fleet.write_atomic(dest / "stages.json", json.dumps(table, ensure_ascii=False, indent=1) + "\n")
    for name in STATE_FILES:
        os.chmod(dest / name, 0o600)
    return dest, len(events), len(table["rows"])


def state_rollback(src, rehearsal=False):
    """Put an export's files back in place of the tombstones and take the step and its tables out of the DB. What was
    written to the tables since the export is lost with them; export again first when that matters."""
    sd, src = fleet.state_dir(), pathlib.Path(src).expanduser()
    if migrated_steps().get(STATE_STEP) is None:
        raise Refused(f"{STATE_STEP} is not in {DB_NAME}: nothing to roll back")
    if REFS_STEP in migrated_steps():  # pr_reviewer and card_ref point at pr_ref too
        raise Refused(f"{REFS_STEP} is in {DB_NAME} and points at the PR references: roll it back first (orch db rollback refs)")
    if SENDLOG_STEP in migrated_steps():  # send_log points at pr_ref, which this drops
        raise Refused(f"{SENDLOG_STEP} is in {DB_NAME} and points at the PR references: roll it back first "
                      f"(orch db rollback sendlog DIR)")
    for name in STATE_FILES:
        if not (src / name).is_file():
            raise Refused(f"{src / name} is missing: run `orch db export --legacy --target state {src}` first")
        if not (sd / name).is_dir() or any((sd / name).iterdir()):
            raise Refused(f"{sd / name} is not an empty tombstone: move what is there aside first")
    if rehearsal:
        return f"dry-run: would put {', '.join(STATE_FILES)} back from {src} and drop {STATE_STEP}; nothing was changed"
    staged = {}
    for name in STATE_FILES:  # copied beside the tombstone first, so only a rename is left after the DB changes
        staged[name] = sd / (name + ".restore")
        shutil.copyfile(src / name, staged[name])
        os.chmod(staged[name], 0o600)
    with tx() as db:
        for t in STATE_TABLES:
            db.execute(f"DROP TABLE {t}")
        db.execute("DELETE FROM migration WHERE step=?", (STATE_STEP,))
        _event(db, "migration", STATE_STEP, "rolled-back", "orch db rollback")
    for name in STATE_FILES:
        (sd / name).rmdir()
        os.replace(staged[name], sd / name)
    return f"put {', '.join(STATE_FILES)} back from {src}; {STATE_STEP} is out of the DB"


# ---------------------------------------------------------------- watch-prs send-log and the review-ask queue

# Needs the 6-state step (the PR reference table). prs/sendlog.jsonl lives under the shared state, not the fleet state, so
# the step names it by absolute path: the backup, the sha256 and the tombstone all go by that path.
SENDLOG_STEP = "7-sendlog"
SEND_ACTIONS = ("request", "rerequest", "remind", "retrigger", "remind_author")
# found: someone already asked in the channel; no_thread: nothing was posted. Both are in the legacy log and decide()
# counts them as asked, so they stay: dropping them would ask those reviewers again.
SEND_SOURCES = ("sent", "scheduled", "found", "refused", "no_thread", "skipped")
SENT_SOURCES = ("sent", "scheduled", "found")  # the message went out, or was already there
ASK_CLOSED = ("reviewed", "merged", "withdrawn")
SCHEMA_SENDLOG = (
    # One row per outcome of a watch-prs action. head '' is a row written by hand with no head: NULL would make the
    # UNIQUE key useless for it. at, post_at are epoch seconds, as the file held them.
    """CREATE TABLE send_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pr_repo TEXT NOT NULL, pr_number INTEGER NOT NULL, reviewer TEXT NOT NULL, head TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL CHECK (action IN ('request','rerequest','remind','retrigger','remind_author')),
  source TEXT NOT NULL CHECK (source IN ('sent','scheduled','found','refused','no_thread','skipped')),
  at REAL NOT NULL, post_at REAL, thread TEXT, scheduled_id TEXT, channel TEXT,
  FOREIGN KEY (pr_repo, pr_number) REFERENCES pr_ref(repo, number),
  UNIQUE (pr_repo, pr_number, reviewer, head, action, source, at)
)""",
    # The owner's queue of "ask this person to review this PR". Its state is derived, never stored: queued while it points
    # at no send_log row, scheduled while that row is a scheduled post still ahead, sent after; closed_why only when it
    # ended without a send or after it.
    """CREATE TABLE review_ask (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pr_repo TEXT NOT NULL, pr_number INTEGER NOT NULL, reviewer TEXT NOT NULL,
  created_at TEXT NOT NULL, by TEXT NOT NULL,
  send_log_id INTEGER REFERENCES send_log(id),
  closed_at TEXT, closed_why TEXT CHECK (closed_why IN ('reviewed','merged','withdrawn')),
  FOREIGN KEY (pr_repo, pr_number) REFERENCES pr_ref(repo, number),
  CHECK ((closed_at IS NULL) = (closed_why IS NULL))
)""",
    "CREATE INDEX review_ask_open ON review_ask(pr_repo, pr_number) WHERE closed_at IS NULL",
)
SENDLOG_TABLES = ("review_ask", "send_log")  # dropped in this order


def sendlog_file(state=None):
    """prs/sendlog.jsonl under the shared state: `state` (what prs.py gets from --state), else $AGENT_SKILLS_STATE."""
    base = state or os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills"
    return (pathlib.Path(base).expanduser() / "prs" / "sendlog.jsonl").resolve()


def _sendlog_config():
    """The send-log agent-skills.json → brief → sendlog names, resolved; None when it names none."""
    p = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG") or "~/.claude/agent-skills.json").expanduser()
    try:
        v = json.loads(p.read_text())["brief"]["sendlog"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return pathlib.Path(v).expanduser().resolve() if isinstance(v, str) and v else None


def sendlog_split():
    """Why the two places a send-log is configured disagree, or None: prs.py writes one file and the brief reads the other,
    so after the move one of them would be reading a tombstone."""
    cfg = _sendlog_config()
    if cfg and cfg != sendlog_file():
        return f"the brief's sendlog ({cfg}) is not the file watch-prs writes ({sendlog_file()}): make them one path"
    return None


@contextlib.contextmanager
def sendlog_lock(state=None):
    """The lock a writer of the send-log file holds while it checks sendlog_ready() and appends, and the step holds from
    its backup to the tombstone: a row can then not land in the file between the copy and the move."""
    lock = sendlog_file(state).parent / ".sendlog.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def sendlog_ready(state=None):
    """Whether the send-log lives in the DB for this state directory; False without a DB or before `orch db state`."""
    if SENDLOG_STEP not in migrated_steps():
        return False
    return str(sendlog_file(state)) in json.loads(_step_row(SENDLOG_STEP)["sources"])


def _send_row(r):
    """A row as the file held it: the keys decide() and the brief read."""
    return {"pr": f"{r['name']}#{r['pr_number']}", "reviewer": r["reviewer"], "head": r["head"] or None,
            "action": r["action"], "source": r["source"], "at": r["at"], "post_at": r["post_at"], "thread": r["thread"],
            "scheduled": r["scheduled_id"], "channel": r["channel"]}


def send_rows(db=None):
    """Every send-log row, oldest first. Callers check sendlog_ready() first: before it the rows are in the file."""
    sql = ("SELECT s.*, p.name FROM send_log s JOIN pr_ref p ON p.repo=s.pr_repo AND p.number=s.pr_number ORDER BY s.id")
    if db is not None:
        return [_send_row(r) for r in db.execute(sql)]
    with read() as d:
        return [_send_row(r) for r in d.execute(sql)]


def _send_insert(db, row, by):
    """Insert one row (the file's shape); (id of the row now there, whether it was added). A row the UNIQUE key already
    holds is not added twice. A request that went out settles the queued asks for that PR and person."""
    repo, number = pr_split(row["pr"]) or (None, None)
    if repo is None:
        raise ValueError(f"send-log: pr must look like owner/repo#N, got {row['pr']!r}")
    _pr_stub(db, repo, number, by)
    vals = (repo.lower(), number, row["reviewer"], row.get("head") or "", row["action"], row["source"], float(row["at"]),
            row.get("post_at"), row.get("thread"), row.get("scheduled"), row.get("channel"))
    cur = db.execute("INSERT INTO send_log (pr_repo, pr_number, reviewer, head, action, source, at, post_at, thread, "
                     "scheduled_id, channel) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING", vals)
    if not cur.rowcount:
        return db.execute("SELECT id FROM send_log WHERE pr_repo=? AND pr_number=? AND reviewer=? AND head=? AND action=? "
                          "AND source=? AND at=?", vals[:7]).fetchone()[0], False
    if row["action"] == "request" and row["source"] in SENT_SOURCES:
        db.execute("UPDATE review_ask SET send_log_id=? WHERE pr_repo=? AND pr_number=? AND lower(reviewer)=? "
                   "AND send_log_id IS NULL AND closed_at IS NULL", (cur.lastrowid, repo.lower(), number, row["reviewer"].lower()))
    return cur.lastrowid, True


def send_log_add(row, by="orch watch-prs"):
    """Record one outcome (pr, reviewer, head, action, source, at, post_at, thread, scheduled, channel); the row's id."""
    if row["action"] not in SEND_ACTIONS or row["source"] not in SEND_SOURCES:
        raise ValueError(f"send-log: action is one of {', '.join(SEND_ACTIONS)}, source one of {', '.join(SEND_SOURCES)}")
    with tx() as db:
        return _send_insert(db, row, by)[0]


def ask_add(pr, reviewer, by="orch ask add"):
    """Queue an ask: (id, True), or (id, False) when the same PR and person are already queued or asked and not closed."""
    split, who = pr_split(pr), (reviewer or "").strip()
    if not split or not who:
        raise ValueError("an ask needs a PR (owner/repo#N) and a reviewer")
    with tx() as db:
        old = db.execute("SELECT id FROM review_ask WHERE pr_repo=? AND pr_number=? AND lower(reviewer)=? AND closed_at IS NULL",
                         (split[0].lower(), split[1], who.lower())).fetchone()
        if old:
            return old["id"], False
        _pr_stub(db, *split, by)
        last = db.execute("SELECT id FROM send_log WHERE pr_repo=? AND pr_number=? AND lower(reviewer)=? AND action='request' "
                          f"AND source IN ({','.join('?' * len(SENT_SOURCES))}) ORDER BY id DESC LIMIT 1",
                          (split[0].lower(), split[1], who.lower(), *SENT_SOURCES)).fetchone()  # already asked: nothing to queue
        aid = db.execute("INSERT INTO review_ask (pr_repo, pr_number, reviewer, created_at, by, send_log_id) VALUES (?,?,?,?,?,?)",
                         (split[0].lower(), split[1], who, now(), by, last and last["id"])).lastrowid
        _event(db, "review_ask", str(aid), "added", by, None, {"pr": pr, "reviewer": who})
        return aid, True


def ask_close(aid, why="withdrawn", by="orch ask cancel"):
    """Close an open ask; False when there is none by that id."""
    if why not in ASK_CLOSED:
        raise ValueError(f"an ask closes as one of {', '.join(ASK_CLOSED)}")
    with tx() as db:
        n = db.execute("UPDATE review_ask SET closed_at=?, closed_why=? WHERE id=? AND closed_at IS NULL",
                       (now(), why, aid)).rowcount
        if n:
            _event(db, "review_ask", str(aid), why, by)
        return bool(n)


def asks(now_ts=None, status=None):
    """The asks, oldest first, each with its derived `status`: queued (no send yet), scheduled (a post still ahead),
    sent, or the way it closed. status filters. Callers check sendlog_ready() first."""
    t = time.time() if now_ts is None else now_ts
    out = []
    with read() as db:
        for r in db.execute("SELECT a.*, p.name, s.source, s.post_at FROM review_ask a JOIN pr_ref p ON p.repo=a.pr_repo "
                            "AND p.number=a.pr_number LEFT JOIN send_log s ON s.id=a.send_log_id ORDER BY a.id"):
            st = (r["closed_why"] or ("queued" if r["send_log_id"] is None else
                                      "scheduled" if r["source"] == "scheduled" and (r["post_at"] or 0) > t else "sent"))
            if status is None or st in ((status,) if isinstance(status, str) else status):
                out.append({"id": r["id"], "pr": f"{r['name']}#{r['pr_number']}", "reviewer": r["reviewer"],
                            "created_at": r["created_at"], "by": r["by"], "send_log_id": r["send_log_id"], "status": st})
    return out


def _send_legacy(r, skipped):
    """One legacy line as a send_log row, or None with the reason counted. A row without `action` was appended by hand
    for a request someone sent outside the round: a sent request, timed by `at`, `date` or `slack_ts`."""
    if not isinstance(r, dict) or not (r.get("pr") and r.get("reviewer")):
        skipped["no_pr_or_reviewer"] += 1
        return None
    if isinstance(r["pr"], int):
        skipped["int_pr"] += 1
        return None
    if not isinstance(r["pr"], str) or not pr_split(r["pr"]) or not isinstance(r["reviewer"], str):
        skipped["pr_text"] += 1
        return None
    if "action" not in r:
        try:
            at = r.get("at") or (dt.datetime.fromisoformat(r["date"].replace("Z", "+00:00")).timestamp() if r.get("date")
                                 else float(r["slack_ts"]))
        except (KeyError, TypeError, ValueError, AttributeError):
            skipped["no_time"] += 1
            return None
        return {"pr": r["pr"], "reviewer": r["reviewer"], "head": r.get("head"), "action": "request", "source": "sent",
                "at": at}
    src = r.get("source", "sent")
    if src == "coordinator":  # the name an older round gave a send it made itself
        skipped["coordinator_to_sent"] += 1
        r = {**r, "source": "sent"}
    elif src not in SEND_SOURCES:  # decide() treated any source but refused and skipped as an ask that went out
        skipped["other_source_to_sent"] += 1
        r = {**r, "source": "sent"}
    return r


def _sendlog_import(db, lines, by="import"):
    """The legacy lines into send_log; returns (rows added, counts of what was mapped or skipped, never row text).
    A row the table's CHECK or UNIQUE refuses is counted and skipped: it never stops the import."""
    skipped = dict.fromkeys(("bad_line", "no_pr_or_reviewer", "int_pr", "pr_text", "no_time", "coordinator_to_sent",
                             "other_source_to_sent", "bad_value", "duplicate"), 0)
    n = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except ValueError:
            skipped["bad_line"] += 1
            continue
        row = _send_legacy(raw, skipped)
        if row is None:
            continue
        try:
            if _send_insert(db, row, by)[1]:
                n += 1
            else:
                skipped["duplicate"] += 1
        except Exception:  # noqa: BLE001  a CHECK, a number SQLite cannot bind, a missing key: this row only
            skipped["bad_value"] += 1
    return n, skipped


def sendlog_setup(by="orch db state", auto=False):
    """Move prs/sendlog.jsonl into send_log: back the file up, import from the copy (so the hash and the rows agree),
    record the step with the file's absolute path and sha256, tombstone it. A second run finishes a tombstone a crash left
    and otherwise changes nothing. auto: the call setup and the sync tick make; a DB not ready for it is skipped."""
    steps = migrated_steps()
    if SENDLOG_STEP in steps:
        return _finish(SENDLOG_STEP, _step_row(SENDLOG_STEP))
    f = sendlog_file()
    try:
        open_db().close()
        if STATE_STEP not in steps:
            raise NotMigrated(f"{STATE_STEP} is not in {DB_NAME}: run {state_command()} first")
    except NotMigrated as e:
        if auto:
            return f"skipped: {e}"
        raise
    if why := sendlog_split():
        if auto:
            return f"skipped: {why}"
        raise Refused(why)
    if f.is_dir() or (not f.exists() and f.with_name(f.name + ".restore").exists()):
        raise Refused(f"{f} is a tombstone or missing but {SENDLOG_STEP} is not in {DB_NAME}: a rollback was cut short. "
                      f"Put the file back by hand ({f.name}.restore beside it, or the export), then run {state_command()}")
    ts = dt.datetime.now(dt.timezone.utc)
    with sendlog_lock():  # prs.log appends under it, so the copy, its sha256 and the tombstone see the same file
        backup, sources = _backup_files(fleet.state_dir(), SENDLOG_STEP, ts)
        copy_ = backup / f.name
        lines = copy_.read_text(errors="replace").splitlines() if copy_.is_file() else []  # a bad byte spoils its line only
        with tx() as db:
            for ddl in SCHEMA_SENDLOG:
                db.execute(ddl)
            n, skipped = _sendlog_import(db, lines)
            db.execute("INSERT INTO migration (step, ts, backup, sources, stage) VALUES (?,?,?,?, 'imported')",
                       (SENDLOG_STEP, now(), str(backup), json.dumps(sources)))
            _event(db, "migration", SENDLOG_STEP, "imported", by, None, {"backup": str(backup), "imported": n, "skipped": skipped})
        done = _finish(SENDLOG_STEP, _step_row(SENDLOG_STEP))
    left = ", ".join(f"{k} {v}" for k, v in skipped.items() if v)
    return f"{done}: {n} send-log rows from {f}" + (f"; {left}" if left else "") + f"; backup {backup}"


def sendlog_export(dest):
    """sendlog.jsonl in the shape older code reads, into dest. The review-ask queue has no legacy file."""
    rows = send_rows()
    dest = pathlib.Path(dest).expanduser()
    dest.mkdir(mode=0o700, parents=True, exist_ok=True)
    fleet.write_atomic(dest / "sendlog.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    os.chmod(dest / "sendlog.jsonl", 0o600)
    return dest, len(rows)


def sendlog_rollback(src, rehearsal=False):
    """Put an export's file back where the step found it and take the step and its tables out of the DB. What was written
    since the export is lost with the tables; export again first when that matters. Open asks have no file to go back
    to, so they stop it: cancel them first."""
    src = pathlib.Path(src).expanduser()
    row = _step_row(SENDLOG_STEP) if SENDLOG_STEP in migrated_steps() else None
    if row is None:
        raise Refused(f"{SENDLOG_STEP} is not in {DB_NAME}: nothing to roll back")
    target = pathlib.Path(next(iter(json.loads(row["sources"]))))
    if not (src / "sendlog.jsonl").is_file():
        raise Refused(f"{src / 'sendlog.jsonl'} is missing: run `orch db export --legacy --target sendlog {src}` first")
    if not target.is_dir() or any(target.iterdir()):
        raise Refused(f"{target} is not an empty tombstone: move what is there aside first")
    if live := asks(status=("queued", "scheduled")):
        raise Refused(f"{len(live)} open review asks have no file to go back to: `orch ask cancel <id>` them first")
    exported = len((src / "sendlog.jsonl").read_text().splitlines())
    if exported != len(send_rows()):
        raise Refused(f"the export holds {exported} rows and the DB {len(send_rows())}: rows were written after it, and the "
                      f"rollback would drop them. Run `orch db export --legacy --target sendlog {src}` again")
    if rehearsal:
        return f"dry-run: would put {target} back from {src} and drop {SENDLOG_STEP}; nothing was changed"
    staged = target.with_name(target.name + ".restore")
    shutil.copyfile(src / "sendlog.jsonl", staged)
    os.chmod(staged, 0o600)
    with tx() as db:
        for t in SENDLOG_TABLES:
            db.execute(f"DROP TABLE {t}")
        db.execute("DELETE FROM migration WHERE step=?", (SENDLOG_STEP,))
        _event(db, "migration", SENDLOG_STEP, "rolled-back", "orch db rollback")
    target.rmdir()
    os.replace(staged, target)
    return f"put {target} back from {src}; {SENDLOG_STEP} is out of the DB"


# ---------------------------------------------------------------- step 8-refs: what the collector knows, kept in the DB

REFS_STEP = "8-refs"
PR_STATES = {"OPEN": "open", "MERGED": "merged", "CLOSED": "closed"}  # prs.json's words -> pr_ref.state
PR_WORDS = {v: k for k, v in PR_STATES.items()}  # and back, for readers that compare "MERGED"
RETAIN_CARD_DAYS, RETAIN_PR_DAYS = 30, 90  # a gone card, and a merged or closed PR nothing points at, are dropped after
SCHEMA_REFS = (
    # Who is on a PR's review, as the collector last read it; a cache like pr_ref, replaced whole on each read.
    """CREATE TABLE pr_reviewer (
  pr_repo TEXT NOT NULL, pr_number INTEGER NOT NULL, login TEXT NOT NULL,
  requested INTEGER NOT NULL CHECK (requested IN (0,1)), team INTEGER NOT NULL DEFAULT 0, bot INTEGER NOT NULL DEFAULT 0,
  state TEXT, at TEXT, stale INTEGER NOT NULL DEFAULT 0, last_seen_at TEXT NOT NULL,
  PRIMARY KEY (pr_repo, pr_number, login),
  FOREIGN KEY (pr_repo, pr_number) REFERENCES pr_ref(repo, number) ON DELETE CASCADE
) WITHOUT ROWID""",
    # An Orca worktree the collector has seen. A worktree that is no longer there keeps its row with gone_at (a tombstone)
    # until the retention window passes, so a card's PR can still be looked up after the worktree was removed.
    """CREATE TABLE card_ref (
  orca_worktree_id TEXT PRIMARY KEY, repo TEXT, branch TEXT, pr_repo TEXT, pr_number INTEGER,
  first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, gone_at TEXT
)""",
    # What is left to reap, derived on read: nothing is written for it.
    """CREATE VIEW reap_card AS
SELECT c.orca_worktree_id, c.repo, c.branch, c.pr_repo, c.pr_number, c.gone_at, p.state AS pr_state, p.merged_at,
       CASE WHEN c.gone_at IS NOT NULL THEN 'worktree_gone' WHEN p.state = 'merged' THEN 'pr_merged'
            WHEN p.state = 'closed' THEN 'pr_closed' END AS reason
FROM card_ref c LEFT JOIN pr_ref p ON p.repo = c.pr_repo AND p.number = c.pr_number
WHERE c.gone_at IS NOT NULL OR p.state IN ('merged', 'closed')""",
)
# VIRTUAL generated columns over the JSON the old readers still parse: pr, program and kind can be filtered and joined on,
# and nothing that reads the table as it was changes.
# pr is the lower-cased 'owner/repo#N', the key pr_ref's repo and number join on.
GENERATED = {
    "board_row": (("pr", "lower(json_extract(data, '$.pr'))"), ("program", "json_extract(data, '$.program')"),
                  ("kind", "json_extract(data, '$.kind')")),
    "lead": (("pr", "lower(json_extract(data, '$.pr'))"), ("program", "name"), ("kind", "'lead'")),
    "decision": (("pr", "CASE WHEN link LIKE 'https://github.com/%/pull/%' THEN lower(substr(link, 20, instr(substr(link, 20), "
                        "'/pull/') - 1)) || '#' || CAST(substr(link, instr(link, '/pull/') + 6) AS INTEGER) END"),
                 ("program", "NULL"), ("kind", "'decision'")),
}


def refs_ready():
    """Whether the collector's tables exist; False without a DB or before `orch db state`."""
    return REFS_STEP in migrated_steps()


def _add_generated(db, tables=None):
    for table, cols in GENERATED.items():
        if tables and table not in tables:
            continue
        have = {r["name"] for r in db.execute(f"PRAGMA table_xinfo({table})")}
        for name, expr in cols:
            if name not in have:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} TEXT GENERATED ALWAYS AS ({expr}) VIRTUAL")


def refs_setup(by="orch db state", auto=False):
    """Create pr_reviewer, card_ref, the reap_card view and the generated columns; nothing is imported and no file moves, so
    there is no backup. A second run changes nothing. auto: a DB not ready for it is skipped."""
    steps = migrated_steps()
    if REFS_STEP in steps:
        return "already created"
    try:
        open_db().close()
        if STATE_STEP not in steps:
            raise NotMigrated(f"{STATE_STEP} is not in {DB_NAME}: run {state_command()} first")
    except NotMigrated as e:
        if auto:
            return f"skipped: {e}"
        raise
    with tx() as db:
        for ddl in SCHEMA_REFS:
            db.execute(ddl)
        _add_generated(db)
        db.execute("INSERT INTO migration (step, ts, backup, sources, stage) VALUES (?,?,?,?, 'tombstoned')",
                   (REFS_STEP, now(), "", "{}"))
        _event(db, "migration", REFS_STEP, "created", by)
    return "created pr_reviewer, card_ref, reap_card and the generated columns pr, program, kind"


def refs_rollback():
    """Take the step out: the tables, the view and the generated columns go. The data is a cache the collector fills again."""
    if REFS_STEP not in migrated_steps():
        raise Refused(f"{REFS_STEP} is not in {DB_NAME}: nothing to roll back")
    with tx() as db:
        db.execute("DROP VIEW reap_card")
        db.execute("DROP TABLE pr_reviewer")
        db.execute("DROP TABLE card_ref")
        for table, cols in GENERATED.items():
            have = {r["name"] for r in db.execute(f"PRAGMA table_xinfo({table})")}  # a reload of the board may have dropped them
            for name, _ in cols:
                if name in have:
                    db.execute(f"ALTER TABLE {table} DROP COLUMN {name}")
        db.execute("DELETE FROM migration WHERE step=?", (REFS_STEP,))
        _event(db, "migration", REFS_STEP, "rolled-back", "orch db rollback")
    return f"{REFS_STEP} is out of the DB"


def pr_word(state):
    """pr_ref.state as the collector's prs.json spells it (OPEN, MERGED, CLOSED); None for 'unknown'."""
    return PR_WORDS.get(state)


def pr_words():
    """{'owner/repo#N': 'MERGED' | 'OPEN' | 'CLOSED'} for every PR the DB has seen, so a reader that compares prs.json's words
    works on the table too. The key is spelled as the source wrote the repository."""
    with read() as db:
        return {f"{r['name']}#{r['number']}": PR_WORDS[r["state"]]
                for r in db.execute("SELECT name, number, state FROM pr_ref WHERE state != 'unknown'")}


def pr_refresh(details, by="collector"):
    """The collector's PR details (fleet.shape_pr dicts) into pr_ref and pr_reviewer, then the asks they settle: an ask
    whose PR merged closes as merged, one whose PR closed unmerged as withdrawn, a sent one whose reviewer has since
    reviewed as reviewed. Returns {'prs': n, 'closed': {why: n}}."""
    at, n = now(), 0
    with tx() as db:
        for d in details:
            state, repo, num = PR_STATES.get(d.get("state")), d.get("repo"), d.get("number")
            if not state or not repo or not isinstance(num, int):
                continue
            db.execute(
                "INSERT INTO pr_ref (repo, number, name, state, draft, head_sha, base_ref, author, assignee, ci, review_decision, "
                "merge_state, merged_at, merge_sha, closed_at, last_seen_at, refreshed_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(repo, number) DO UPDATE SET name=excluded.name, state=excluded.state, draft=excluded.draft, "
                "head_sha=excluded.head_sha, base_ref=excluded.base_ref, author=excluded.author, assignee=excluded.assignee, "
                "ci=excluded.ci, review_decision=excluded.review_decision, merge_state=excluded.merge_state, "
                "merged_at=excluded.merged_at, merge_sha=excluded.merge_sha, last_seen_at=excluded.last_seen_at, "
                "refreshed_by=excluded.refreshed_by, closed_at=CASE WHEN excluded.state='closed' "
                "THEN COALESCE(pr_ref.closed_at, excluded.closed_at) END",
                (repo.lower(), num, repo, state, int(bool(d.get("draft"))), d.get("head"), d.get("base"), d.get("author"),
                 d.get("assignee"), d.get("ci"), d.get("decision"), d.get("merge_state"),
                 d.get("merged_at") if state == "merged" else None, d.get("merge_sha"), at if state == "closed" else None,
                 at, by))
            db.execute("DELETE FROM pr_reviewer WHERE pr_repo=? AND pr_number=?", (repo.lower(), num))
            for r in d.get("reviewers") or []:
                if r.get("login"):
                    db.execute("INSERT OR REPLACE INTO pr_reviewer (pr_repo, pr_number, login, requested, team, bot, state, at, "
                               "stale, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (repo.lower(), num, r["login"], int(bool(r.get("requested"))), int(bool(r.get("team"))),
                                int(bool(r.get("bot"))), r.get("state"), r.get("at"), int(bool(r.get("stale"))), at))
            n += 1
        closed = {}
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='review_ask'").fetchone():
            closed = _ask_settle(db, at, by)
    return {"prs": n, "closed": closed}


def _ask_settle(db, at, by):
    """Close the open asks the PR table now answers; each close is an event. Merged outranks closed outranks reviewed."""
    out = {}
    for why, cond in (
            ("merged", "p.state = 'merged'"),
            ("withdrawn", "p.state = 'closed'"),
            ("reviewed", "a.send_log_id IS NOT NULL AND EXISTS (SELECT 1 FROM pr_reviewer r WHERE r.pr_repo = a.pr_repo "
                         "AND r.pr_number = a.pr_number AND lower(r.login) = lower(a.reviewer) AND r.state IN ('APPROVED', 'CHANGES_REQUESTED', 'COMMENTED') "
                         "AND CAST(strftime('%s', r.at) AS INTEGER) >= CAST((SELECT at FROM send_log WHERE id = a.send_log_id) "
                         "AS INTEGER))")):
        ids = [r[0] for r in db.execute(
            "SELECT a.id FROM review_ask a JOIN pr_ref p ON p.repo = a.pr_repo AND p.number = a.pr_number "
            f"WHERE a.closed_at IS NULL AND {cond}")]
        for aid in ids:
            db.execute("UPDATE review_ask SET closed_at=?, closed_why=? WHERE id=?", (at, why, aid))
            _event(db, "review_ask", str(aid), why, by)
        if ids:
            out[why] = len(ids)
    return out


def open_ask_prs():
    """'owner/repo#N' of every PR with an open ask, spelled as the source wrote it: the collector reads them even when no
    session or search names them, so the ask can close when the PR does. [] before 7-sendlog."""
    try:
        with read() as db:
            return [f"{r['name']}#{r['pr_number']}" for r in db.execute(
                "SELECT DISTINCT p.name, a.pr_number FROM review_ask a JOIN pr_ref p ON p.repo=a.pr_repo AND p.number=a.pr_number "
                "WHERE a.closed_at IS NULL")]
    except sqlite3.Error:
        return []


def card_refresh(cards, by="collector"):
    """Every Orca worktree the collector saw this tick: [{'id', 'repo', 'branch', 'pr': 'owner/repo#N' | None}]. A worktree
    in the list is (re)marked present; one that was present and is not is tombstoned with gone_at, never deleted here."""
    at = now()
    with tx() as db:
        for c in cards:
            pr = pr_split(c["pr"]) if c.get("pr") else None
            db.execute(
                "INSERT INTO card_ref (orca_worktree_id, repo, branch, pr_repo, pr_number, first_seen_at, last_seen_at) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(orca_worktree_id) DO UPDATE SET repo=excluded.repo, branch=excluded.branch, "
                "pr_repo=CASE WHEN excluded.branch IS card_ref.branch THEN COALESCE(excluded.pr_repo, card_ref.pr_repo) ELSE excluded.pr_repo END, "
                "pr_number=CASE WHEN excluded.branch IS card_ref.branch THEN COALESCE(excluded.pr_number, card_ref.pr_number) ELSE excluded.pr_number END, "
                "last_seen_at=excluded.last_seen_at, gone_at=NULL",
                (c["id"], c.get("repo"), c.get("branch"), pr and pr[0].lower(), pr and pr[1], at, at))
        return db.execute("UPDATE card_ref SET gone_at=? WHERE gone_at IS NULL AND orca_worktree_id NOT IN "
                          "(SELECT value FROM json_each(?))", (at, json.dumps([c["id"] for c in cards]))).rowcount


def retention(now_ts=None, dry_run=False):
    """The policy for what the collector's tables keep: a card gone for RETAIN_CARD_DAYS goes; a merged or closed PR that
    no card, stage row, send-log row or ask points at, and that no collector has seen for RETAIN_PR_DAYS, goes with its
    reviewers. {'card_ref': n, 'pr_ref': n}; dry_run counts and deletes nothing."""
    if not refs_ready():
        raise Refused(f"{REFS_STEP} is not in {DB_NAME}: run `orch db state` first")
    t = time.time() if now_ts is None else now_ts
    cut = lambda days: dt.datetime.fromtimestamp(t - days * 86400, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cards = "FROM card_ref WHERE gone_at IS NOT NULL AND gone_at < :card_cut"
    with tx() as db:
        # A card this same run deletes no longer points, so a dry run counts what a real one removes.
        pointers = {"card_ref": "AND (x.gone_at IS NULL OR x.gone_at >= :card_cut)", "stage_row": ""}
        pointers |= {t: "" for t in ("send_log", "review_ask") if db.execute(  # 7-sendlog may not be in
            "SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone()}
        unused = ("FROM pr_ref WHERE state IN ('merged','closed') AND last_seen_at < :pr_cut " + " ".join(
            f"AND NOT EXISTS (SELECT 1 FROM {t} x WHERE x.pr_repo = pr_ref.repo AND x.pr_number = pr_ref.number {extra})"
            for t, extra in pointers.items()))
        args = {"card_cut": cut(RETAIN_CARD_DAYS), "pr_cut": cut(RETAIN_PR_DAYS)}
        out = {"card_ref": db.execute(f"SELECT count(*) {cards}", args).fetchone()[0],
               "pr_ref": db.execute(f"SELECT count(*) {unused}", args).fetchone()[0]}
        if not dry_run:
            db.execute(f"DELETE {unused}", args)
            db.execute(f"DELETE {cards}", args)
    return out


def reap_view():
    """The worktrees to reap, derived by the reap_card view: [{'id', 'repo', 'branch', 'pr', 'reason'}]."""
    with read() as db:
        return [{"id": r["orca_worktree_id"], "repo": r["repo"], "branch": r["branch"],
                 "pr": r["pr_repo"] and f"{r['pr_repo']}#{r['pr_number']}", "reason": r["reason"]}
                for r in db.execute("SELECT * FROM reap_card ORDER BY orca_worktree_id")]


# ---------------------------------------------------------------- what older code left

def migrated_steps():
    """Data steps recorded in the DB, read without creating it; empty when there is no DB yet or it cannot be read."""
    p = path()
    if not p.is_file():
        return {}
    try:
        db = _connect(p, 1000)
        try:
            return {r["step"]: r["stage"] for r in db.execute("SELECT step, stage FROM migration")}
        finally:
            db.close()
    except sqlite3.DatabaseError:
        return {}


def _steps_of(db):
    return db.execute("PRAGMA user_version").fetchone()[0], dict(db.execute("SELECT step, stage FROM migration"))


def peek_steps():
    """(user_version, {step: stage}) without touching anything in the state directory: the DB file is opened immutable,
    which never creates or changes a file. An immutable open ignores a -wal, and while a process holds the DB open its
    commits sit there; so a -wal with content is read from a copy of the DB and the -wal made in a private temporary
    directory (the DB first, then the -wal, so a checkpoint in between only shows fewer steps). Nothing opens the live
    -shm. (0, {}) with no DB; Refused when it cannot be read."""
    p = path()
    if not p.is_file():
        return 0, {}
    wal = pathlib.Path(f"{p}-wal")
    try:
        if wal.is_file() and wal.stat().st_size > 0:
            with tempfile.TemporaryDirectory(prefix="orch-peek-") as d:
                q = pathlib.Path(d) / p.name
                try:
                    shutil.copyfile(p, q)
                    shutil.copyfile(wal, f"{q}-wal")
                except FileNotFoundError:  # the last holder closed and checkpointed between the check and the copy
                    q = None
                if q:
                    db = sqlite3.connect(str(q))
                    try:
                        return _steps_of(db)
                    finally:
                        db.close()
        db = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
        try:
            return _steps_of(db)
        finally:
            db.close()
    except (sqlite3.DatabaseError, OSError) as e:
        raise Refused(f"{p} cannot be read: {e}") from e


def legacy_traces():
    """Legacy files a tombstoned step replaced that are files again or gone: old code wrote after the cutover."""
    out = []
    for step, stage in migrated_steps().items():
        if stage != "tombstoned":
            continue
        for name in _legacy(step):
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


SYNC_LABEL = "io.github.jeongjaesoon.agent-skills-sync"  # scripts/bootstrap.py LABEL (test_cutover keeps them equal)


def launch_plists():
    """{label: (plist path or None, its spec)} of the LaunchAgents whose jobs write the fleet state: the dashboard
    server (fleet tick, relay, tidy) under this shell's label, every installed agent whose program runs an entry point
    (one installed under another label), and agent-skills-sync, which moves the checkout under every session."""
    own = os.environ.get("ORCH_DASH_LABEL") or "io.github.jeongjaesoon.orch-dash"
    out, names = {own: (None, {})}, _entry_names()
    for p in sorted(pathlib.Path("~/Library/LaunchAgents").expanduser().glob("*.plist")):
        try:
            spec = plistlib.loads(p.read_bytes())
        except (OSError, plistlib.InvalidFileException, ValueError):
            continue
        label = spec.get("Label")
        if label == own or label == SYNC_LABEL:
            out[label] = (p, spec)
            continue
        args = [str(a) for a in [spec.get("Program") or "", *(spec.get("ProgramArguments") or [])] if a]
        cwd = pathlib.Path(str(spec.get("WorkingDirectory") or "/"))
        hits = []
        for i, a in enumerate(args):
            if " " in a:  # a `sh -c` string
                hits += [w for w, _ in _words(a, names)]
            else:
                hits.append(a.replace(".", "/") + ".py" if i and args[i - 1] == "-m" else a)
        if label and label not in out and any(
                pathlib.PurePath(w).name in names and _ours((cwd / pathlib.Path(w).expanduser()).resolve()) for w in hits):
            out[label] = (p, spec)
    out.setdefault(SYNC_LABEL, (None, {}))
    return out


def launch_labels():
    return list(launch_plists())


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
                       "end it and start a new process; /restart keeps the pid and start time, so it does not count")
    return out


def check_launchers():
    out = []
    if sys.platform == "darwin":
        for label in launch_labels():
            p = _sh(["launchctl", "print", f"gui/{os.getuid()}/{label}"])
            if p.returncode == 0:
                out.append(f"LaunchAgent {label} is loaded (with or without a pid it restarts old code): "
                           f"launchctl bootout gui/{os.getuid()}/{label}")
            elif p.returncode != 113:  # 113: no such service. Anything else (127 not run, -9 killed) is not "not loaded"
                how = f"killed by signal {-p.returncode}" if p.returncode < 0 else f"exit {p.returncode}"
                out.append(f"launchctl print {label} did not answer ({how}): {p.stderr.strip()[:120]}")
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
            + check_sessions(procs, since) + check_launchers() + ([why] if (why := sendlog_split()) else []))


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


@contextlib.contextmanager
def cutover_lock(name=CUTOVER_LOCK):
    """`orch db migrate` holds two: RUN_LOCK for the whole run, recovery included, so a second run refuses instead of
    racing it; CUTOVER_LOCK from before the first stop to the end of the import, which keeps the dashboard server down.
    A second holder is refused, not waited for. The kernel drops them with the process: a killed run leaves nothing stale."""
    sd = fleet.state_dir()
    sd.mkdir(parents=True, exist_ok=True)
    with (sd / name).open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused("another `orch db migrate` is running here; wait for it to end") from None
        yield


def cutover_running():
    """Whether a migrate holds its lock now: the dashboard server must not start meanwhile. Reads only."""
    try:
        fd = os.open(fleet.state_dir() / CUTOVER_LOCK, os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
    return False


YIELD_MARK = "dash-yielded.json"


def mark_dash_yielded(port):
    """A dashboard server ends itself because a migrate holds its lock: say so before it goes, so the migrate can start
    it again even when its own probes ran too early or too late to see it."""
    sd = fleet.state_dir()
    sd.mkdir(parents=True, exist_ok=True)
    tmp = sd / f"{YIELD_MARK}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps({"port": port}))
    os.replace(tmp, sd / YIELD_MARK)


def dash_yielded():
    """The port a server left with mark_dash_yielded; None when there is none or it holds no port. Reads only: the
    caller clears the mark once the port is safely in the recovery plan."""
    got = fleet.read_json(fleet.state_dir() / YIELD_MARK, None)
    port = got.get("port") if isinstance(got, dict) else None
    return port if isinstance(port, int) else None


def clear_dash_yielded():
    (fleet.state_dir() / YIELD_MARK).unlink(missing_ok=True)


def init_empty():
    """`orch db init`, for a PC with no state: an empty DB with both steps done, so the first board or decision write
    works and no cutover is needed. Does nothing when any state, a DB or a tombstone exists, or a migrate runs; the
    condition is checked again under the same locks the import takes."""
    sd = fleet.state_dir()
    sd.mkdir(parents=True, exist_ok=True)
    if cutover_running():
        return "skipped: orch db migrate is running"
    with _locks():
        if path().exists() or any((sd / n).exists() for names in LEGACY.values() for n in names):
            return f"skipped: state exists here; `{migrate_command()}` moves it"
        _import_decisions(sd)
        _import_board(sd)
    return f"created an empty {DB_NAME} (both steps done)"


def _legacy_decisions(p):
    """(decisions, next, problems) from a decisions.json."""
    if not p.is_file():
        return [], 1, []
    try:
        data = json.loads(p.read_text())
    except ValueError as e:
        return [], 1, [f"{p.name}: not JSON ({e})"]
    if not isinstance(data, dict) or not isinstance(data.get("decisions", {}), dict):
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
        elif set(d) - set(DECISION_COLS) - {"answered_by"}:  # answered_by goes on in the import event's `after`
            extra = set(d) - set(DECISION_COLS) - {"answered_by"}
            bad.append(f"{key}: fields this table has no column for: {', '.join(sorted(extra))}")
        else:
            got.append(d)
    top = max([int(d["id"][1:]) for d in got], default=0) + 1
    nxt = data.get("next", 1)
    return got, max(nxt if isinstance(nxt, int) else 1, top), bad


BOARD_FIELDS = {"doing": ("title", "owner", "next", "waiting", "since"), "later": ("title", "trigger", "due", "who"),
                "todo": ("title", "who", "due", "after", "added")}


def _legacy_board(p):
    """(board, problems, extra) from a board.json. problems stop the import; extra names fields no column holds, which
    stay in the backup and in each item's import event."""
    out, bad, extra = {"rows": {}, "order": [], "leads": {}, "groups": {}, "annots": {}, **{lane: [] for lane in LANES}}, [], set()
    if p.is_dir():
        return out, [f"{p.name} is a directory but the step that left it is not recorded: put the file back first"], extra
    if not p.is_file():
        return out, [], extra
    try:
        data = json.loads(p.read_text())
    except ValueError as e:
        return out, [f"{p.name}: not JSON ({e})"], extra
    if not isinstance(data, dict):
        return out, [f"{p.name}: not an object"], extra
    for key, kind in (("rows", dict), ("order", list), ("leads", dict), ("groups", dict), ("annots", dict),
                      *((lane, list) for lane in LANES)):
        if isinstance(data.get(key) or kind(), kind):
            out[key] = data.get(key) or kind()
        else:
            bad.append(f"{key}: not a {kind.__name__}")
    for lane in LANES:
        for i, it in enumerate(out[lane]):
            if lane == "owner_todo":
                if not isinstance(it, str) or not it.strip():
                    bad.append(f"owner_todo {i + 1}: not a line of text")
            elif not isinstance(it, dict) or not isinstance(it.get("title"), str) or not it["title"]:
                bad.append(f"{lane} {i + 1}: no title")
            elif lane == "later" and not isinstance(it.get("trigger"), str):
                bad.append(f"later {i + 1}: no trigger")
            elif any(not isinstance(it.get(k) or "", str) for k in BOARD_FIELDS[lane] if k != "waiting"):
                bad.append(f"{lane} {i + 1}: a field of the wrong type")
            else:
                extra |= {f"{lane}.{k}" for k in set(it) - set(BOARD_FIELDS[lane])}
    for rid, r in out["rows"].items():
        if not isinstance(r, dict) or r.get("id") != rid or not r.get("kind"):
            bad.append(f"row {rid}: id does not match its key, or no kind")
        elif r["kind"] == GROUP_KIND or rid.startswith("prio:"):
            bad.append(f"row {rid}: kind {GROUP_KIND} and ids starting prio: are for priority rules, which belong under groups")
    for name, l in out["leads"].items():
        if not isinstance(l, dict):
            bad.append(f"lead {name}: not an object")
    for gid, g in out["groups"].items():
        if (not isinstance(g, dict) or g.get("id") != gid or g.get("kind") != GROUP_KIND
                or not isinstance(g.get("match"), str) or gid != f"prio:{g['match']}"
                or not GROUP_MATCH.fullmatch(g["match"]) or not isinstance(g.get("priority"), str)
                or not GROUP_PRIORITY.fullmatch(g["priority"])
                or any(" ".join(g[k].split()) != g[k] for k in ("match", "priority"))  # board writes whitespace folded
                or g["priority"].split(":", 1)[0] != str(int(g["priority"].split(":", 1)[0]))):  # and no leading zero
            bad.append(f"priority rule {gid}: not a group with id prio:MATCH, a match of worktree:ID, program:NAME or "
                       "repo:OWNER/NAME, and a priority of RANK:NAME")
    for aid, a in out["annots"].items():
        if not isinstance(a, dict) or a.get("id") != aid or a.get("kind") != ANNOT_KIND or not aid.startswith("card:") or len(aid) == 5:
            bad.append(f"annotation {aid}: not a row of kind {ANNOT_KIND} with the id card:<worktree>")
    return out, bad, extra


def _legacy_tidy(p):
    """(the done and reopen lines of a tidy.jsonl, how many lines were neither, problems)."""
    if p.is_dir():
        return [], 0, [f"{p.name} is a directory but the step that left it is not recorded: put the file back first"]
    lines = fleet.read_lines(p) if p.is_file() else []
    good = [l for l in lines if isinstance(l, dict) and isinstance(l.get("id"), str) and l.get("ev") in ("done", "reopen")]
    return good, (len(p.read_text().splitlines()) if p.is_file() else 0) - len(good), []


def _plan_board(board_, lines, state, ts, start=1):
    """What the 4b step writes: every item with its stable id, the alias from its content hash and the state its tidy
    line left, a closure per other tidy id, rows and leads less what is Orca's. state None: Orca's view is not known, so
    rows and leads stay as they are. Nothing is written here; --dry-run shows it and import fills from it."""
    import tidy  # imports this module
    last, n = {l["id"]: l for l in lines}, start
    out = {"items": [], "alias": {}, "same_hash": [], "closures": [], "rows": [], "leads": [], "leads_known": [], "rows_cut": 0}
    anchors = fleet.inbox_anchors()
    for lane in LANES:
        for pos, raw in enumerate(board_[lane]):
            h, iid = legacy_id(lane, raw), f"b{n}"
            n += 1
            d = {"title": raw} if lane == "owner_todo" else raw
            cur, status, close, reopen = last.get(h) or {}, LANES[lane][0], (None, None, None, None), None
            if cur.get("ev") == "done":
                status, close = "done", (cur.get("ts") or ts, cur.get("by"), cur.get("rule"), cur.get("evidence"))
            elif cur.get("ev") == "reopen":
                close, reopen = (None, cur.get("by"), cur.get("rule"), None), cur.get("evidence") or ""
            if h in out["alias"]:
                out["same_hash"].append(h)
            else:
                out["alias"][h] = iid
            out["items"].append({"id": iid, "lane": lane, "pos": pos, "status": status, "cols": _item_cols(lane, d), "legacy": raw,
                                 "created": d.get("added") or ts, "close": close, "reopen": reopen})
    hashes = {legacy_id(lane, raw) for lane in LANES for raw in board_[lane]}
    for uid, l in last.items():
        if uid not in hashes:
            done = l["ev"] == "done"
            out["closures"].append({"unit": uid, "state": l["ev"], "ts": l.get("ts") or ts, "by": l.get("by"), "rule": l.get("rule"),
                                    "evidence": l.get("evidence") if done else None, "label": l.get("label"),
                                    "undone": None if done else l.get("evidence"), "legacy": l,
                                    "mark": anchors.get(uid[4:], {}).get("resolve") if done and uid.startswith("ext:") else None})
    cards = tidy.cards_of(state) if state else {}
    sessions = {s.get("id"): s for s in (state or {}).get("sessions") or []}
    by_handle = {t.get("handle"): sid for sid, s in sessions.items() for t in s.get("terminals") or []}
    rows = {rid: dict(r) for rid, r in board_["rows"].items()}
    for name, l in board_["leads"].items():
        sid = l.get("worktree") if l.get("worktree") in sessions else by_handle.get(l.get("handle"))
        if not l.get("closed_at") and (sessions.get(sid) or {}).get("kind") == "orchestration":
            out["leads_known"].append((name, l))
            if name in rows and rows[name].get("kind") == "lead":  # the row keeps its link to the session the lead was
                rows[name].setdefault("worktree", sid)
        else:
            out["leads"].append((name, l))
    order = [k for k in board_["order"] if k in rows] + [k for k in rows if k not in board_["order"]]
    for pos, rid in enumerate(order):
        r = rows[rid]
        if r.get("pr") and (cards.get(r.get("worktree")) or tidy.named(f"{rid} {r.get('title') or ''}", cards)):
            del r["pr"]  # the card's PR is Orca's; `worktree` stays as the row's link to the card
            out["rows_cut"] += 1
        out["rows"].append((pos, r, board_["rows"][rid]))
    out["groups"] = sorted((board_.get("groups") or {}).items())
    out["annots"] = sorted((board_.get("annots") or {}).items())
    out["next"] = n
    return out


def _fill_board(db, plan, lines, by="import"):
    for it in plan["items"]:
        _insert_item(db, it["id"], it["lane"], it["status"], it["pos"], it["cols"], it["created"],
                     it["close"][0] or it["created"], it["close"], it["reopen"])
        _event(db, "item", it["id"], "import", by, it["legacy"], {"lane": it["lane"], "status": it["status"]})
    for old, iid in plan["alias"].items():
        db.execute("INSERT INTO alias (old, id) VALUES (?,?)", (old, iid))
    for c in plan["closures"]:
        db.execute("INSERT INTO closure (unit, state, ts, by, rule, evidence, label, undone_evidence, ext_mark) "
                   "VALUES (?,?,?,?,?,?,?,?,?)", (c["unit"], c["state"], c["ts"], c["by"], c["rule"], c["evidence"], c["label"],
                                                 c["undone"], c["mark"]))
        _event(db, "closure", c["unit"], "import", by, c["legacy"])
    for pos, r, legacy in plan["rows"]:
        _insert_row(db, r, pos)
        _event(db, "board_row", r["id"], "import", by, legacy)
    for n, (gid, g) in enumerate(plan["groups"]):
        _insert_row(db, g, GROUP_POS + n)
        _event(db, "board_row", gid, "import", by, g)
    for n, (aid, a) in enumerate(plan["annots"]):
        _insert_row(db, a, ANNOT_POS + n)
        _event(db, "board_row", aid, "import", by, a)
    for name, l in plan["leads"]:
        _insert_lead(db, name, l)
        _event(db, "lead", name, "import", by, l)
    for name, l in plan["leads_known"]:
        _event(db, "lead", name, "import_orca_knows", by, l)
    for l in lines:
        if not l.get("carried"):
            _event(db, "tidy", l["id"], l["ev"], l.get("by"), None, l, l.get("ts"))
    db.execute("INSERT INTO counter (name, next) VALUES ('b', ?) ON CONFLICT(name) DO UPDATE SET next=excluded.next", (plan["next"],))


def _reset_v2(db):
    for t in TABLES_V2:
        db.execute(f"DROP TABLE IF EXISTS {t}")
    for s in SCHEMA_V2:
        db.execute(s)
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='migration'").fetchone() and db.execute(
            "SELECT 1 FROM migration WHERE step=?", (REFS_STEP,)).fetchone():  # the recreated tables keep the step's columns
        _add_generated(db, TABLES_V2)


def load_board(board_=None, lines=(), state=None):
    """Replace the board tables with board_ (board.json's shape) and lines (tidy.jsonl's), as the import fills them. For
    tests and the demo, whose worlds are written in the old shape."""
    source = {"rows": {}, "order": [], "leads": {}, **{lane: [] for lane in LANES}, **(board_ or {})}
    with tx() as db:
        plan = _plan_board(source, list(lines), state, now(), _counter(db, "b"))
        _reset_v2(db)
        _fill_board(db, plan, list(lines), "load")
        return {o: i for o, i in plan["alias"].items()}


def _board_sources(sd):
    """(board, tidy lines, problems, unread lines, extra fields) from the files the 4b step moves."""
    b, bad, extra = _legacy_board(sd / "board.json")
    lines, unread, bad_t = _legacy_tidy(sd / "tidy.jsonl")
    return b, lines, bad + bad_t, unread, extra


def import_plan():
    """What `orch db import` would do now: the 4a step's {step, state, decisions, open, next, problems} and, in "board",
    the 4b step's counts, the ids it would give, and what it finds wrong."""
    sd, steps = fleet.state_dir(), migrated_steps()
    src = sd / "decisions.json"
    got, nxt, bad = _legacy_decisions(src)
    stage = steps.get(DECISION_STEP)
    state = ("tombstoned" if stage == "tombstoned" else "imported, tombstone pending" if stage == "imported"
             else "unexpected DB without the 4a step" if path().exists() else "not imported")
    out = {"step": DECISION_STEP, "state": state, "decisions": len(got), "open": sum(d["status"] == "open" for d in got),
           "next": nxt, "problems": bad, "source": str(src), "source_kind": "dir" if src.is_dir() else
           "file" if src.is_file() else "absent"}
    stage = steps.get(BOARD_STEP)
    if stage:
        out["board"] = {"step": BOARD_STEP, "state": "tombstoned" if stage == "tombstoned" else "imported, tombstone pending"}
        return out
    b, lines, bad, unread, extra = _board_sources(sd)
    state_ = fleet.read_json(sd / "state.json", None)
    p = _plan_board(b, lines, state_ if isinstance(state_, dict) else None, now())
    by_lane = {lane: sum(i["lane"] == lane for i in p["items"]) for lane in LANES}
    out["board"] = {"step": BOARD_STEP, "state": "not imported", "items": by_lane, "open": sum(i["status"] in OPEN for i in p["items"]),
                    "closed": sum(i["status"] == "done" for i in p["items"]), "ids": f"b1..b{p['next'] - 1}" if p["items"] else "none",
                    "aliases": len(p["alias"]), "same_hash": sorted(set(p["same_hash"])), "closures": len(p["closures"]),
                    "rows": len(p["rows"]), "rows_pr_cut": p["rows_cut"], "leads": len(p["leads"]),
                    "leads_orca_knows": [n for n, _ in p["leads_known"]], "tidy_lines": len(lines), "tidy_unread_lines": unread,
                    "unknown_fields": sorted(extra), "orca_view": "state.json" if isinstance(state_, dict) else "none: rows and leads kept",
                    "problems": bad}
    return out


def _tombstone(sd, names):
    for name in names:
        p = sd / name
        if p.is_file():
            p.unlink()
        p.mkdir(mode=0o700, parents=True, exist_ok=True)


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
    if step == BOARD_STEP:  # before the stage, so a crash here runs again; a file already moved is simply not there
        for name in RETIRED:
            if (sd / name).is_file():
                os.replace(sd / name, pathlib.Path(row["backup"]) / name)
    _set_stage(step, "tombstoned")
    return "tombstoned " + ", ".join(sources)


def _backup_files(sd, step, ts):
    """Copy the step's files (0700 directory, 0600 files). Returns (directory, {name: sha256 or None})."""
    backup = sd / f"backup-db-{ts.strftime('%Y%m%dT%H%M%S%fZ')}"
    backup.mkdir(mode=0o700)
    os.chmod(backup, 0o700)
    sources = {}
    for name in _legacy(step):  # a name is relative to sd, or an absolute path (`sd / name` is then that path)
        if (sd / name).is_file():
            keep = backup / pathlib.Path(name).name  # never `backup / name`: an absolute name would be the original
            shutil.copyfile(sd / name, keep)
            os.chmod(keep, 0o600)
            sources[name] = sha256(sd / name)
        else:
            sources[name] = None
    return backup, sources


def _step_row(step):
    """The migration row of step, or None; the table missing is None too."""
    db = _connect(path())
    try:
        return db.execute("SELECT * FROM migration WHERE step=?", (step,)).fetchone() if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='migration'").fetchone() else None
    finally:
        db.close()


def _fill_decisions(db, got, nxt, ts, backup, sources):
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    db.execute("BEGIN IMMEDIATE")
    for d in got:
        _insert_decision(db, d)
        _event(db, "decision", d["id"], "import", "import", None, d)
    db.execute("INSERT INTO counter (name, next) VALUES ('d', ?)", (nxt,))
    db.execute("INSERT INTO migration (step, ts, backup, sources, stage) VALUES (?,?,?,?, 'imported')",
               (DECISION_STEP, ts, str(backup), json.dumps(sources)))
    _event(db, "migration", DECISION_STEP, "imported", "import", None, {"backup": str(backup), "sources": sources})
    db.execute("PRAGMA user_version=1")
    db.execute("COMMIT")
    ok = db.execute("PRAGMA integrity_check").fetchone()[0]
    if ok != "ok":
        raise Refused(f"integrity_check: {ok}")


def _import_decisions(sd):
    """The 4a step: a new DB holding decisions.json, made under a temporary name and renamed in."""
    step = DECISION_STEP
    if path().exists():
        row = _step_row(step)
        if not row:
            raise Refused(f"{path()} exists without the {step} step: move it aside by hand, or restore a backup")
        return _finish(step, row)
    got, nxt, _ = _legacy_decisions(sd / "decisions.json")
    ts = fleet.utcnow()
    backup, sources = _backup_files(sd, step, ts)
    tmp = sd / f".{DB_NAME}.import-{os.getpid()}.tmp"
    tmp.unlink(missing_ok=True)
    old_mask = os.umask(0o077)
    try:
        db = _connect(tmp, mode="rwc")
        try:
            _fill_decisions(db, got, nxt, fleet.iso(ts), backup, sources)
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
    done = _finish(step, _step_row(step))
    return f"imported {len(got)} decisions (next d{nxt}); backup {backup}; {done}"


def _import_board(sd):
    """The 4b step, in the live DB: one transaction raises the schema, fills the tables and records the step. A table left
    by an undone 4b is dropped and filled again; counter b stays, so a b<N> is never given twice."""
    step = BOARD_STEP
    row = _step_row(step)
    if row:
        return _finish(step, row)
    b, lines, _, _, _ = _board_sources(sd)
    state = fleet.read_json(sd / "state.json", None)
    ts = fleet.utcnow()
    backup, sources = _backup_files(sd, step, ts)
    db = _connect(path())
    try:
        db.execute("BEGIN IMMEDIATE")
        try:
            plan = _plan_board(b, lines, state if isinstance(state, dict) else None, fleet.iso(ts), _counter(db, "b"))
            _reset_v2(db)
            _fill_board(db, plan, lines)
            db.execute("INSERT INTO migration (step, ts, backup, sources, stage) VALUES (?,?,?,?, 'imported')",
                       (step, fleet.iso(ts), str(backup), json.dumps(sources)))
            _event(db, "migration", step, "imported", "import", None, {"backup": str(backup), "sources": sources})
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            ok = db.execute("PRAGMA integrity_check").fetchone()[0]
            if ok != "ok":
                raise Refused(f"integrity_check: {ok}")
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    finally:
        db.close()
    done = _finish(step, _step_row(step))
    return f"imported {len(plan['items'])} board items, {len(plan['closures'])} closures, {len(plan['rows'])} rows, " \
           f"{len(plan['leads'])} leads ({len(plan['leads_known'])} Orca knows, kept in events; {plan['rows_cut']} rows' " \
           f"--pr dropped); backup {backup}; {done}"


def _check_sources(sd, upto, steps=None):
    """Every reason the pending steps would stop, before either touches anything. `steps` lets a read-only caller pass
    what peek_steps read, since migrated_steps opens the DB and can create its -wal."""
    bad = []
    steps = migrated_steps() if steps is None else steps
    if not path().exists() and DECISION_STEP not in steps:
        bad += _legacy_decisions(sd / "decisions.json")[2]
    if upto == BOARD_STEP and BOARD_STEP not in steps:
        bad += _board_sources(sd)[2]
    if bad:
        raise Refused("the files hold entries the tables cannot; fix them by hand first:\n" + "\n".join(f"  - {b}" for b in bad))


def import_apply(rehearsal=False, checks=True, upto=BOARD_STEP):
    """`orch db import --apply`: each step not yet in, 4a then 4b. Returns a line of what it did. checks=False is for
    tests only; upto=DECISION_STEP stops after 4a, for a rehearsal of 4b on a live 4a DB."""
    if checks:
        gate(rehearsal)
    sd = fleet.state_dir()
    sd.mkdir(parents=True, exist_ok=True)
    with _locks():
        _check_sources(sd, upto)
        done = [_import_decisions(sd)]
        if upto == BOARD_STEP:
            done.append(_import_board(sd))
    return "; ".join(dict.fromkeys(done))


# ---------------------------------------------------------------- backup, restore, export, dump

def _raw():
    """The DB without the access gate (dump, backup and export work on any version), never created here."""
    if not path().is_file():
        raise NotMigrated(f"no {DB_NAME} in {path().parent}: run {migrate_command()}")
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
    with _locks():  # checked inside: `orch decide` writes decisions.json under the same lock until the DB is there
        files = [n for n in names if (sd / n).is_file()]
        if files:
            raise Refused(f"{', '.join(files)} is a file here: the restored DB already holds it. Move it aside first")
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
    """The files the code of `target` reads. 4a: board.json, tidy.jsonl and a copy of inbox-external.jsonl, with
    export.json for `orch db rollback 4b`. pre-4a: decisions.json, once 4b is undone. Keeping 4b and undoing 4a alone is
    not supported. Returns (what was written, how many, whether the WAL checkpoint finished; None when none is needed)."""
    if target not in ("4a", "pre-4a"):
        raise Refused("--target is 4a (board and tidy, decisions stay in the DB) or pre-4a (decisions, after 4b is undone)")
    steps = migrated_steps()
    if target == "4a":
        return _export_board(pathlib.Path(dest).expanduser())
    if BOARD_STEP in steps:
        raise Refused(f"{BOARD_STEP} is imported: undo 4b first (export --legacy --target 4a, then rollback 4b)")
    if DECISION_STEP not in steps:  # a DB without the decisions step would export an empty file old code takes as canonical
        raise NotMigrated(f"{DECISION_STEP} is not in {DB_NAME}: there is nothing to export")
    db = _raw()
    try:
        db.execute("BEGIN")
        by = _answered_by(db)
        rows = [{**d, "answered_by": by[d["id"]]} if d["id"] in by else d for d in decisions(db=db)]
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


def _legacy_item(r):
    """An item row as board.json held it: (the entry, the id tidy knew it by)."""
    d = _item(r)
    if r["lane"] == "owner_todo":
        return d["title"], legacy_id("owner_todo", d["title"])
    legacy = {k: v for k, v in d.items() if k not in ("id", "updated_at")}
    return legacy, legacy_id(r["lane"], legacy)


def legacy_ids(ids):
    """The ids older code knows items by, for a line it will run."""
    with read() as db:
        return [_legacy_item(_item_now(db, i))[1] for i in ids]


def _export_board(dest):
    db = open_db()
    try:
        db.execute("BEGIN")
        items = db.execute("SELECT * FROM item WHERE status<>'dropped' ORDER BY lane, pos, CAST(substr(id, 2) AS INTEGER)").fetchall()
        b = _board(db)
        closures = db.execute("SELECT * FROM closure ORDER BY ts, unit").fetchall()
        shut = ext_closures(db)
        seq = db.execute("SELECT max(seq) FROM event").fetchone()[0]
    finally:
        db.close()
    lanes, lines, state_of = {lane: [] for lane in LANES}, [], {}
    for r in items:
        legacy, h = _legacy_item(r)
        lanes[r["lane"]].append(legacy)
        state = (r["status"] == "done", r["reopen_evidence"])
        if state_of.setdefault(h, (state, r["id"]))[0] != state:
            raise Refused(f"{state_of[h][1]} and {r['id']} have the same words ({h}) in different states; the old format "
                          "keeps one state per hash. Close or reword one, then export again")
        if h in {l["id"] for l in lines}:
            continue
        if r["status"] == "done":
            lines.append({"ev": "done", "id": h, "ts": r["closed_at"], "by": r["close_by"], "rule": r["close_rule"],
                          "evidence": r["close_evidence"], "label": r["title"]})
        elif r["reopen_evidence"] is not None:
            lines.append({"ev": "reopen", "id": h, "ts": r["updated_at"], "by": r["close_by"], "rule": r["close_rule"],
                          "evidence": r["reopen_evidence"], "label": r["title"]})
    live = fleet.state_dir() / "inbox-external.jsonl"
    data = live.read_bytes() if live.is_file() else None
    inbox = fleet.inbox_lines(data or b"")
    for c in closures:
        done = c["state"] == "done"
        if c["unit"] in state_of and state_of[c["unit"]][0] != (done, None if done else c["undone_evidence"]):
            raise Refused(f"{state_of[c['unit']][1]} has the words of an item that left the board in another state "
                          f"({c['unit']}); the old format would give it that state. Reword or close it, then export again")
        key = c["unit"][4:] if c["unit"].startswith("ext:") else None
        if done and inbox.get(key) and fleet.ext_state(inbox[key], shut.get(key))[0]:
            continue  # a newer line of the key is open, and an old done line would hide it from older code
        lines.append({"ev": c["state"], "id": c["unit"], "ts": c["ts"], "by": c["by"], "rule": c["rule"],
                      "evidence": c["evidence"] if done else c["undone_evidence"], "label": c["label"]})
    dest.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    drop = lambda row: {k: v for k, v in row.items() if k != "done_at" or v}  # noqa: E731
    board_json = {"v": 1, "rows": {k: drop(r) for k, r in b["rows"].items()}, "order": b["order"], "leads": b["leads"], **lanes,
                  # the 4a code ignores a key it does not know, and the next import puts the priority rules back
                  **({"groups": b["groups"]} if b["groups"] else {}),
                  # card annotations: 4a ignores the key too, and the next import puts them back
                  **({"annots": b["annots"]} if b["annots"] else {})}
    fleet.write_atomic(dest / "board.json", json.dumps(board_json, ensure_ascii=False, indent=1) + "\n")
    fleet.write_atomic(dest / "tidy.jsonl", "".join(json.dumps(l, ensure_ascii=False, separators=(",", ":")) + "\n" for l in lines))
    text = (data or b"").decode()
    extra = "".join(json.dumps(l, ensure_ascii=False, separators=(",", ":")) + "\n" for l in fleet.inbox_for_export(shut, data or b""))
    cut = "\n" if extra and text and not text.endswith("\n") else ""  # a crashed writer's partial line must not swallow them
    fleet.write_atomic(dest / "inbox-external.jsonl", text + cut + extra)
    names = ("board.json", "tidy.jsonl", "inbox-external.jsonl")
    for name in names:
        os.chmod(dest / name, 0o600)
    fleet.write_atomic(dest / "export.json", json.dumps(
        {"target": "4a", "seq": seq, "inbox_source": None if data is None else hashlib.sha256(data).hexdigest(),
         "files": {name: sha256(dest / name) for name in names}}, indent=1) + "\n")
    os.chmod(dest / "export.json", 0o600)
    return dest, len(items), None


def rollback_board(src, rehearsal=False, checks=True):
    """`orch db rollback 4b DIR`: put an `export --legacy --target 4a` back and take the 4b step out, so the 4a code runs on
    the files again. The same preflight as import. The export must be of this DB as it stands (no event since) and of this
    inbox (or the copy a crashed run already placed). The step goes last, so a crash leaves it to run again; its tables
    stay for a later import to replace."""
    src = pathlib.Path(src).expanduser()
    manifest = fleet.read_json(src / "export.json", None)
    if not isinstance(manifest, dict) or manifest.get("target") != "4a":
        raise Refused(f"{src}: no export.json from `orch db export --legacy --target 4a`")
    if checks:
        gate(rehearsal)
    if BOARD_STEP not in migrated_steps():
        raise Refused(f"{BOARD_STEP} is not in {DB_NAME}: nothing to undo")
    sd = fleet.state_dir()
    names = ("board.json", "tidy.jsonl", "inbox-external.jsonl")
    with _locks():
        with tx() as db:  # the write lock from the check to the step's removal: no write slips between them
            if db.execute("SELECT max(seq) FROM event").fetchone()[0] != manifest.get("seq"):
                raise Refused("the DB changed after that export: export again")
            live = sd / "inbox-external.jsonl"
            have = sha256(live) if live.is_file() else None
            if have not in (manifest.get("inbox_source"), manifest["files"]["inbox-external.jsonl"]):
                raise Refused("inbox-external.jsonl changed after that export: export again")
            for name in names:
                if sha256(src / name) != manifest["files"][name]:
                    raise Refused(f"{src / name} is not what the export wrote")
            for name in names:
                p, tmp = sd / name, sd / f".{name}.rollback-{os.getpid()}.tmp"
                shutil.copyfile(src / name, tmp)
                os.chmod(tmp, 0o600)
                if p.is_dir():
                    p.rmdir()  # empty, or this stops: a tombstone holds nothing
                os.replace(tmp, p)
            db.execute("DELETE FROM migration WHERE step=?", (BOARD_STEP,))
            _event(db, "migration", BOARD_STEP, "rollback", "rollback", None, {"from": str(src)})
    return f"{BOARD_STEP} undone: board.json, tidy.jsonl and inbox-external.jsonl are back from {src}"


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
    undo = (["orch db backup <file>", "orch db export --legacy --target 4a <dir>", "orch db rollback 4b <dir>",
             "# revert the PR, fast-forward, start the writers. To go back before 4a too, then:",
             "# orch db export --legacy --target pre-4a <dir2>",
             f"# move {DB_NAME} with its -wal and -shm out of the state directory, remove the empty decisions.json",
             "# directory there, put <dir2>/decisions.json in its place, revert the 4a PR, fast-forward, start the writers"]
            if step == "4b" else
            ["orch db backup <file>", "orch db export --legacy --target pre-4a <dir>",
             f"# move {DB_NAME} with its -wal and -shm out of the state directory, remove the empty decisions.json",
             "# directory there, put <dir>/decisions.json in its place, revert the PR, fast-forward, start the writers"])
    lines = [f"# cutover {step}: run these yourself, in order. This command ran none of them.",
             f"# The same steps in one rerunnable command, from a plain terminal (never from a Claude session): "
             f"{migrate_command()}   ({migrate_command()} --check changes nothing and lists what stands in the way)",
             "# 1. stop every long-running writer of the state (the dashboard server runs the fleet tick, relay, tidy)",
             *stop,
             "# 2. bring the main checkout to the merged code",
             f"git -C {main_checkout()} pull --ff-only",
             "# 3. end every live Claude session and start a new process (each runs the decision hook; /reload-plugins and "
             "/restart do not count: only a process that started after the update passes), then",
             "orch db import --dry-run",
             "# 4. it refuses while any old writer, session or loaded LaunchAgent remains; then",
             "orch db import --apply",
             "# 5. start the writers again",
             *start,
             "",
             f"# undo {step}: stop the writers as in 1, then",
             *undo]
    return "\n".join(lines)


def main(argv):
    sub, rest = (argv[0] if argv else ""), argv[1:]
    rehearsal = "--rehearsal" in rest
    try:
        if sub == "import" and "--apply" in rest:
            print(import_apply(rehearsal))
            print("start the writers again (run these yourself):\n" + "\n".join(_stop_start("4b")[1]))
        elif sub == "import":
            p = import_plan()
            print(json.dumps(p, ensure_ascii=False, indent=1))
            if not rehearsal:
                why = preflight()
                print("preflight: ok" if not why else "preflight: --apply would refuse\n" + "\n".join(f"  - {w}" for w in why))
            print("dry-run: nothing written. orch db import --apply")
        elif sub == "state":
            print(state_setup(auto="--auto" in rest))
            print(sendlog_setup(auto="--auto" in rest))  # the send-log needs the PR references the line above made
            print(refs_setup(auto="--auto" in rest))  # and so do the reviewers, cards and the reap view
        elif sub == "rollback" and rest[:1] == ["refs"]:
            print(refs_rollback())
        elif sub == "retention":
            print(json.dumps(retention(dry_run="--dry-run" in rest)))
        elif sub == "export" and "--legacy" in rest and "--target" in rest and rest[rest.index("--target") + 1:][:1] == ["sendlog"]:
            dirs = [a for a in rest[:rest.index("--target")] + rest[rest.index("--target") + 2:] if not a.startswith("--")]
            if not dirs:
                raise Refused("export needs the directory to write to: orch db export --legacy --target sendlog DIR")
            out, n = sendlog_export(dirs[-1])
            print(f"wrote {out}: {n} send-log rows")
        elif sub == "rollback" and rest[:1] == ["sendlog"] and [a for a in rest[1:] if not a.startswith("--")]:
            print(sendlog_rollback([a for a in rest[1:] if not a.startswith("--")][0], rehearsal))
        elif sub == "export" and "--legacy" in rest and "--target" in rest and rest[rest.index("--target") + 1:][:1] == ["state"]:
            i = rest.index("--target")
            dirs = [a for a in rest[:i] + rest[i + 2:] if not a.startswith("--")]
            if not dirs:
                raise Refused("export needs the directory to write to: orch db export --legacy --target state DIR")
            out, n, rows = state_export(dirs[-1])
            print(f"wrote {out}: {n} ack events, {rows} stage rows")
        elif sub == "rollback" and rest[:1] == ["state"] and [a for a in rest[1:] if not a.startswith("--")]:
            print(state_rollback([a for a in rest[1:] if not a.startswith("--")][0], rehearsal))
        elif sub == "export" and "--legacy" in rest and len([a for a in rest if not a.startswith("--")]) >= 2:
            target = rest[rest.index("--target") + 1] if "--target" in rest else ""
            dest = [a for a in rest if not a.startswith("--") and a != target][-1]
            out, n, truncated = export_legacy(dest, target)
            print(f"wrote {out} ({n} {'decisions' if target == 'pre-4a' else 'board items'})")
            if truncated is not None:
                print("wal checkpoint: done, the DB can be moved" if truncated else
                      "wal checkpoint: busy, another connection is open; stop it before moving the DB")
        elif sub == "rollback" and rest[:1] == ["4b"] and [a for a in rest[1:] if not a.startswith("--")]:
            print(rollback_board([a for a in rest[1:] if not a.startswith("--")][0], rehearsal))
        elif sub == "dump":
            print(json.dumps(dump(rest[0] if rest else None), ensure_ascii=False, indent=1))
        elif sub == "backup" and rest:
            print(f"backup: {backup(rest[0])}")
        elif sub == "restore" and [a for a in rest if not a.startswith("--")]:
            print(f"restored {restore([a for a in rest if not a.startswith('--')][0], rehearsal)}")
        elif sub == "cutover-plan" and rest:
            print(cutover_plan(rest[0]))
        elif sub == "migrate":
            import cutover
            sys.exit(cutover.main(rest))
        elif sub == "init":
            import cutover
            if why := cutover.ensure_env("init", rest):  # the state a LaunchAgent uses, not this shell's default
                sys.exit(why)
            print(init_empty())
        else:
            sys.exit(__doc__)
    except (Refused, NotMigrated, ValueError, OSError, sqlite3.Error) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main(sys.argv[1:])
