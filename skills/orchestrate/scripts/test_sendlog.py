"""Offline tests for the 7-sendlog step: prs/sendlog.jsonl and the review-ask queue moving into agent-skills.db, on invented
data. Each scenario is a machine of its own: a fresh fleet state, a fresh shared state and a config naming the send-log.

Run: python3 test_sendlog.py
"""
import fcntl, json, os, pathlib, sqlite3, sys, tempfile, threading, time

HERE = pathlib.Path(__file__).parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-sendlog-"))
os.environ.update({"ORCH_BRIEF_LAST": str(tmp / "brief-last"), "ORCH_FLEET_CONFIG": str(tmp / "none.json"), "TZ": "UTC"})
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "watch-prs" / "scripts"))
import board, fleet, store  # noqa: E402

n_machines = 0
ASKER = "acme/widgets#1"


def machine(config=True, with_state=True):
    """A new PC: its fleet state with the 4a/4b DB (and the 6-state step), a shared state, and the config."""
    global n_machines
    n_machines += 1
    root = tmp / f"pc{n_machines}"
    (root / "fleet").mkdir(parents=True)
    (root / "shared" / "prs").mkdir(parents=True)
    os.environ["ORCH_FLEET_STATE"] = str(root / "fleet")
    os.environ["AGENT_SKILLS_STATE"] = str(root / "shared")
    cfg = root / "agent-skills.json"
    cfg.write_text(json.dumps({"brief": {"sendlog": str(root / "shared" / "prs" / "sendlog.jsonl")}}) if config else "{}")
    os.environ["AGENT_SKILLS_CONFIG"] = str(cfg)
    board.SKILLS_CONFIG = cfg  # read once at import
    store.import_apply(checks=False)
    if with_state:
        store.state_setup()
    return root


def row(pr, who, action="request", source="sent", at=1000.0, head="h1", **kw):
    return {"pr": pr, "reviewer": who, "head": head, "action": action, "source": source, "at": at, "post_at": None,
            "thread": None, "scheduled": None, **kw}


def write_log(root, lines):
    (root / "shared" / "prs" / "sendlog.jsonl").write_text(
        "".join((json.dumps(l) if isinstance(l, dict) else l) + "\n" for l in lines))


def count(table):
    c = sqlite3.connect(store.path())
    try:
        return c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    finally:
        c.close()


LEGACY = [
    row("Acme/Widgets#1", "bob", thread="CFAKE:1.0", channel="CFAKE"),                       # a plain request
    row("acme/widgets#1", "bob", "remind", at=2000.0, thread="CFAKE:1.0"),
    row("acme/widgets#2", "carol", source="coordinator", at=1001.0),                         # the old name for sent
    row("acme/widgets#3", "carol", source="found", at=1002.0, thread="CFAKE:3.0"),           # someone had already asked
    row("acme/widgets#3", "dave", source="no_thread", action="rerequest", at=1003.0),
    row("acme/widgets#4", "erin", source="scheduled", at=1004.0, post_at=9000.0, scheduled="Q1"),
    row("acme/widgets#4", "frank", source="refused", at=1005.0),
    row("acme/widgets#4", "gina", source="skipped", action="remind", at=1006.0),
    row("acme/widgets#5", "heidi", action="retrigger", at=1007.0),
    row("acme/widgets#5", "ivan", action="remind_author", at=1008.0),
    {"pr": "acme/widgets#6", "reviewer": "judy", "date": "2026-10-01T00:00:00Z"},            # written by hand: a request
    {"pr": "acme/widgets#6", "reviewer": "kim", "slack_ts": "1700000000.5"},
    {"pr": "acme/widgets#6", "reviewer": "lee"},                                              # no time at all
    row(7, "mia"),                                                                            # a number, no repository
    row("acme/widgets#8", "ned", source="bogus"),                                             # outside the vocabulary
    row("acme/widgets#8", "ned", action="shout"),
    {"reviewer": "oz"},
    "{not json",
    row("Acme/Widgets#1", "bob", thread="CFAKE:1.0", channel="CFAKE"),                       # the same row twice
]

# ---- upgrade: every line is either a row or counted, the file is a tombstone, the backup holds it, the path is absolute
root = machine()
write_log(root, LEGACY)
legacy_file = root / "shared" / "prs" / "sendlog.jsonl"
before = legacy_file.read_bytes()
assert not store.sendlog_ready()
report = store.sendlog_setup()
assert report.startswith(f"tombstoned {legacy_file.resolve()}: 13 send-log rows"), report
for want in ("coordinator_to_sent 1", "other_source_to_sent 1", "int_pr 1", "no_pr_or_reviewer 1", "no_time 1", "bad_line 1",
             "bad_value 1", "duplicate 1"):
    assert want in report, (want, report)
assert store.sendlog_ready() and legacy_file.is_dir() and not any(legacy_file.iterdir())
row_ = dict(sqlite3.connect(store.path()).execute("SELECT step, backup FROM migration").fetchall())
backup = pathlib.Path(row_[store.SENDLOG_STEP])
assert (backup / "sendlog.jsonl").read_bytes() == before, "the backup is the original, byte for byte"
assert json.loads(sqlite3.connect(store.path()).execute("SELECT sources FROM migration WHERE step=?",
                                                         (store.SENDLOG_STEP,)).fetchone()[0]) == \
    {str(legacy_file.resolve()): store.sha256(backup / "sendlog.jsonl")}
assert store.legacy_traces() == []

rows = store.send_rows()
kinds = sorted((r["pr"].lower(), r["reviewer"], r["action"], r["source"]) for r in rows)
assert ("acme/widgets#2", "carol", "request", "sent") in kinds, "coordinator became sent"
assert ("acme/widgets#3", "carol", "request", "found") in kinds and ("acme/widgets#3", "dave", "rerequest", "no_thread") in kinds
assert ("acme/widgets#6", "judy", "request", "sent") in kinds and ("acme/widgets#6", "kim", "request", "sent") in kinds, \
    "a hand-written row counts as a sent request"
assert not [k for k in kinds if k[1] in ("mia", "lee", "oz")]
assert [k for k in kinds if k[0].endswith("#8")] == [("acme/widgets#8", "ned", "request", "sent")], \
    "a source outside the vocabulary was still a send decide() counted: it is kept as sent, and the unknown action is not"
sched = next(r for r in rows if r["reviewer"] == "erin")
assert (sched["post_at"], sched["scheduled"]) == (9000.0, "Q1") and next(r for r in rows if r["reviewer"] == "bob")["channel"] == "CFAKE"
assert next(r for r in rows if r["reviewer"] == "heidi")["head"] == "h1"
assert next(r for r in rows if r["reviewer"] == "judy")["head"] is None, "no head stays no head"

# ---- re-run: nothing changes, and a second import of the same lines adds nothing
assert store.sendlog_setup() == "already imported and tombstoned" and count("send_log") == 13
with store.tx() as db:
    n, skipped = store._sendlog_import(db, [json.dumps(r) for r in LEGACY[:2]])
assert n == 0 and skipped["duplicate"] == 2 and count("send_log") == 13

# ---- readers: decide() and the brief see the same rows through the DB, whatever case the PR was written in
import prs  # noqa: E402

assert prs.load_sendlog() == rows or sorted(map(json.dumps, prs.load_sendlog())) == sorted(map(json.dumps, rows))
asks_ = board.last_asks()
assert asks_[("acme/widgets#1", "bob")][:2] == ("remind", 2000.0), "the newest word to bob, from the DB"
assert ("acme/widgets#4", "frank") not in asks_, "a refusal is not a send"

# ---- writes after the move go to the table, through prs.log's path
store.send_log_add(row("acme/widgets#9", "oscar", at=3000.0))
assert count("send_log") == 14 and not legacy_file.is_file()
assert store.send_log_add(row("ACME/WIDGETS#9", "oscar", at=3000.0)) == store.send_log_add(row("acme/widgets#9", "oscar", at=3000.0)) \
    and count("send_log") == 14, "the same outcome twice is one row, whatever the case"
try:
    store.send_log_add(row("acme/widgets#9", "oscar", source="bogus"))
    raise AssertionError("an unknown source must be refused at the writer")
except ValueError:
    pass

# ---- the review-ask queue: queued, scheduled (derived), sent, withdrawn
aid, new = store.ask_add("Acme/Widgets#20", "Pat")
assert (aid, new) == (1, True) and store.ask_add("acme/widgets#20", "pat") == (1, False), "one open ask per PR and person"
assert [a["status"] for a in store.asks()] == ["queued"]
assert store.asks(status="queued")[0]["pr"] == "Acme/Widgets#20", "the case the writer used survives"
store.send_log_add(row("acme/widgets#20", "pat", source="scheduled", at=4000.0, post_at=5000.0, scheduled="Q2"))
assert store.asks(now_ts=4500.0)[0]["status"] == "scheduled", "a post still ahead"
assert store.asks(now_ts=5000.0)[0]["status"] == "sent", "done once post_at <= now"
assert store.asks(now_ts=4500.0)[0]["send_log_id"] is not None
assert store.ask_add("acme/widgets#1", "bob") == (2, True) and store.asks(status="queued") == [], \
    "already asked: the ask points at that send-log row at once"
aid3, _ = store.ask_add("acme/widgets#21", "quinn")
assert store.ask_close(aid3) and not store.ask_close(aid3) and store.asks(status="withdrawn")[0]["id"] == aid3
for bad in (("not-a-pr", "x"), ("acme/widgets#1", " ")):
    try:
        store.ask_add(*bad)
        raise AssertionError(bad)
    except ValueError:
        pass

# ---- rollback: open asks stop it; then the file comes back and the tables are gone
store.send_log_add(row("acme/widgets#22", "rae", at=4100.0))
open_ask, _ = store.ask_add("acme/widgets#23", "sam")
out, n = store.sendlog_export(tmp / "export")
assert n == count("send_log") and (out / "sendlog.jsonl").is_file()
for refused_call, why in ((lambda: store.sendlog_rollback(tmp / "export"), "open review asks"),
                          (lambda: store.state_rollback(tmp / "none"), "roll it back first")):
    try:
        refused_call()
        raise AssertionError(why)
    except store.Refused as e:
        assert why in str(e), e
assert store.ask_close(open_ask)
assert store.sendlog_rollback(tmp / "export", rehearsal=True).startswith("dry-run") and store.sendlog_ready()
store.sendlog_rollback(tmp / "export")
assert legacy_file.is_file() and not store.sendlog_ready() and store.SENDLOG_STEP not in store.migrated_steps()
back = [json.loads(l) for l in legacy_file.read_text().splitlines()]
assert len(back) == n and all({"pr", "reviewer", "head", "action", "source", "at"} <= set(r) for r in back)
assert prs.load_sendlog() and prs.sendlog_rows(legacy_file), "before the step the file is read again"
assert store.sendlog_setup().startswith("tombstoned"), "and the upgrade runs again"

# ---- two configured paths: refused, by the step and by preflight; with none configured the step still runs
root = machine()
write_log(root, [LEGACY[0]])
(root / "elsewhere").mkdir()
pathlib.Path(os.environ["AGENT_SKILLS_CONFIG"]).write_text(json.dumps({"brief": {"sendlog": str(root / "elsewhere" / "s.jsonl")}}))
try:
    store.sendlog_setup()
    raise AssertionError("the two paths differ")
except store.Refused as e:
    assert "make them one path" in str(e)
store.applied_at = lambda: object()  # preflight's other checks look at this machine's processes and LaunchAgents
fleet.ps_table = lambda: {os.getpid(): {}}
fleet.claude_homes = lambda: []
for check in ("check_processes", "check_plugin_dirs", "check_plugin_source", "check_sessions", "check_launchers"):
    setattr(store, check, lambda *a, **k: [])
assert "make them one path" in " ".join(store.preflight()), "preflight names it too"
assert (root / "shared" / "prs" / "sendlog.jsonl").is_file() and not store.sendlog_ready(), "nothing was moved"
pathlib.Path(os.environ["AGENT_SKILLS_CONFIG"]).write_text("{}")
assert store.sendlog_setup().startswith("tombstoned") and count("send_log") == 1

# ---- a PC that has no send-log file, one before the 6-state step, and --auto
root = machine()
assert store.sendlog_setup().startswith("tombstoned") and count("send_log") == 0 and (root / "shared/prs/sendlog.jsonl").is_dir()
root = machine(with_state=False)
assert store.sendlog_setup(auto=True).startswith("skipped: ") and not store.sendlog_ready()
try:
    store.sendlog_setup()
    raise AssertionError("6-state first")
except store.NotMigrated:
    pass

# ---- a cut-short rollback is refused rather than guessed at
root = machine()
(root / "shared" / "prs" / "sendlog.jsonl").mkdir()
try:
    store.sendlog_setup()
    raise AssertionError("a directory where the file should be")
except store.Refused as e:
    assert "tombstone or missing" in str(e)

# ---- an old writer recreating the file after the move is detected
root = machine()
write_log(root, [LEGACY[0]])
store.sendlog_setup()
f = root / "shared" / "prs" / "sendlog.jsonl"
f.rmdir()
f.write_text("")
assert any("sendlog.jsonl (a file again)" in t for t in store.legacy_traces()), store.legacy_traces()

# ---- what an independent review found; each block is its own machine and fails on the code before the fix
failures = []


def check(fn):
    try:
        fn()
    except BaseException as e:  # noqa: BLE001  a failing block is reported with the rest, not first
        failures.append(f"{fn.__name__}: {type(e).__name__}: {e}")


def t_bad_rows_never_stop_the_import():
    root = machine()
    (root / "shared/prs/sendlog.jsonl").write_bytes(
        json.dumps(row("acme/widgets#99999999999999999999", "zed")).encode() + b"\n" + json.dumps(LEGACY[0]).encode()
        + b"\n\xff\xfe not utf-8\n")
    rep = store.sendlog_setup()
    assert rep.startswith("tombstoned") and count("send_log") == 1 and "bad_value 1" in rep and "bad_line 1" in rep, rep


def t_writers_wait_for_the_step():
    root = machine()
    write_log(root, [LEGACY[0]])
    held = []
    real = store._backup_files

    def probe(*a, **k):
        with (root / "shared/prs/.sendlog.lock").open("a") as fd:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held.append(False)
            except BlockingIOError:
                held.append(True)
        return real(*a, **k)

    store._backup_files = probe
    try:
        store.sendlog_setup()
    finally:
        store._backup_files = real
    assert held == [True], "the step must hold the send-log lock from the backup to the tombstone"
    root = machine()
    st = root / "shared" / "prs"
    (st / "plan.json").write_text(json.dumps({"new": [{"key": "k1", "action": "request", "pr": "acme/widgets#1",
                                                       "reviewer": "bob", "head": "h1"}]}))
    done = threading.Event()
    with store.sendlog_lock(root / "shared"):
        t = threading.Thread(target=lambda: (prs.log(st, "k1", "sent", 1000.0), done.set()))
        t.start()
        time.sleep(0.4)
        assert not done.is_set(), "prs.log wrote while the step held the lock"
    t.join(5)
    assert done.is_set() and (st / "sendlog.jsonl").is_file()


def t_unreadable_db_never_reads_as_an_empty_log():
    root = machine()
    write_log(root, [LEGACY[0]])
    store.sendlog_setup()
    prs.STATE = root / "shared" / "prs"
    real = os.environ["ORCH_FLEET_STATE"]
    os.environ["ORCH_FLEET_STATE"] = str(tmp / "no-such-fleet")
    try:
        try:
            prs.load_sendlog()
            raise AssertionError("an empty log asks everybody again")
        except SystemExit:
            pass
    finally:
        os.environ["ORCH_FLEET_STATE"] = real


def t_traces_follow_the_recorded_path():
    root = machine()
    write_log(root, [LEGACY[0]])
    store.sendlog_setup()
    real = os.environ["AGENT_SKILLS_STATE"]
    os.environ["AGENT_SKILLS_STATE"] = str(tmp / "other-shared")
    try:
        assert store.legacy_traces() == [], store.legacy_traces()
    finally:
        os.environ["AGENT_SKILLS_STATE"] = real


def t_rollback_refuses_a_stale_export():
    root = machine()
    write_log(root, [LEGACY[0]])
    store.sendlog_setup()
    store.sendlog_export(tmp / "ex-stale")
    store.send_log_add(row("acme/widgets#30", "tom", at=5000.0))
    try:
        store.sendlog_rollback(tmp / "ex-stale")
        raise AssertionError("a row written after the export would be lost")
    except store.Refused as e:
        assert "export" in str(e), e
    store.sendlog_export(tmp / "ex-stale")
    store.sendlog_rollback(tmp / "ex-stale")


def t_auto_skips_a_split_config():
    root = machine()
    write_log(root, [LEGACY[0]])
    (root / "elsewhere").mkdir()
    pathlib.Path(os.environ["AGENT_SKILLS_CONFIG"]).write_text(json.dumps({"brief": {"sendlog": str(root / "elsewhere/s.jsonl")}}))
    assert store.sendlog_setup(auto=True).startswith("skipped: ") and not store.sendlog_ready()


for t_ in (t_bad_rows_never_stop_the_import, t_writers_wait_for_the_step, t_unreadable_db_never_reads_as_an_empty_log,
           t_traces_follow_the_recorded_path, t_rollback_refuses_a_stale_export, t_auto_skips_a_split_config):
    check(t_)
if failures:
    sys.exit("test_sendlog FAILED:\n  " + "\n  ".join(failures))

print("test_sendlog: ok")
