"""Offline tests for the board's step of agent-skills.db (schema 2, 4b-board): import, stable ids and aliases, closures,
the write bans, the inbox's effective state, export and rollback to the 4a files, crashes, and what readers do before
the import. Everything runs in temporary state directories with invented data.

Run: python3 test_store_board.py
"""
import contextlib, json, os, pathlib, sqlite3, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-store-board-"))
os.environ.update({"ORCH_FLEET_STATE": str(tmp / "a" / "dashboard"), "ORCH_FLEET_CONFIG": str(tmp / "none.json"),
                   "AGENT_SKILLS_CONFIG": str(tmp / "none.json"), "AGENT_SKILLS_STATE": str(tmp / "state"),
                   "ORCH_BRIEF_LAST": str(tmp / "brief-last"), "TZ": "UTC"})
import board, fleet, rowstate, store, tidy  # noqa: E402

T = "2026-10-01T00:00:00Z"
BOARD = {"v": 1, "order": ["L", "L/card", "L/free", "Old"],
         "rows": {"L": {"id": "L", "kind": "lead", "repo": "acme/app", "title": "L", "parent": None, "status": "moving", "next": ""},
                  "L/card": {"id": "L/card", "kind": "task", "title": "abc-1-login", "parent": "L", "status": "moving", "next": "x",
                             "pr": "acme/app#1", "reported_at": T},
                  "L/free": {"id": "L/free", "kind": "task", "title": "free", "parent": "L", "status": "moving", "next": "y",
                             "pr": "acme/app#2"},
                  "Old": {"id": "Old", "kind": "lead", "repo": "acme/app", "title": "Old", "parent": None, "status": "done", "next": "",
                          "done_at": "2099-01-01T00:00:00Z"}},
         "leads": {"L": {"name": "L", "handle": "term_0FAKE1", "repo": "acme/app", "run": None, "started_at": T},
                   "Old": {"name": "Old", "handle": "term_0FAKE2", "repo": "acme/app", "started_at": T, "closed_at": T}},
         "doing": [{"title": "closed one", "owner": "a", "next": "n"}, {"title": "open one", "owner": "a", "next": "n", "waiting": True, "since": T}],
         "later": [{"title": "reopened", "trigger": "t"}, {"title": "plain", "trigger": "t2", "who": "w", "due": "10/1"}],
         "todo": [{"title": "do", "who": "w", "due": "2026-12-01T00:00:00+00:00", "added": T}],
         "owner_todo": ["now one"]}
H = {"closed": store.legacy_id("doing", BOARD["doing"][0]), "reopened": store.legacy_id("later", BOARD["later"][0]),
     "now": store.legacy_id("owner_todo", "now one")}


def line(ev, iid, **kw):
    return {"ev": ev, "id": iid, "ts": "2026-10-02T00:00:00Z", "by": "owner", "rule": "manual", "evidence": "e", "label": "l", **kw}


TIDY = [line("done", H["closed"]), line("reopen", H["reopened"], evidence="undone"), line("done", "row:L/free"), line("done", "card:s1"),
        line("done", "req:r1"), line("done", "doing:deadbeef"), line("done", "ext:k1"), line("done", "ext:gone"), line("done", "ext:k1", carried=True)]
STATE = {"generated_at": T, "sessions": [
    {"id": "wt-lead", "kind": "orchestration", "phase": "working", "name": "lead", "terminals": [{"handle": "term_0FAKE1"}], "path": "/w/lead"},
    {"id": "wt-card", "kind": "standalone", "phase": "working", "name": "abc-1-login", "terminals": [{"handle": "term_card"}], "path": "/w/abc-1-login",
     "subagents": [{"agent": "ag1", "desc": "Read the login flow", "stale": False}]}], "prs": []}


@contextlib.contextmanager
def state(name, board_=BOARD, lines=TIDY, fleet_=STATE):
    """A fresh state directory holding the legacy files, for the block."""
    was = os.environ["ORCH_FLEET_STATE"]
    os.environ["ORCH_FLEET_STATE"] = str(tmp / name / "dashboard")
    sd = fleet.state_dir()
    sd.mkdir(parents=True, exist_ok=True)
    if board_ is not None:
        (sd / "board.json").write_text(json.dumps(board_, ensure_ascii=False))
    if lines:
        (sd / "tidy.jsonl").write_text("".join(json.dumps(l, ensure_ascii=False) + "\n" for l in lines))
    if fleet_:
        (sd / "state.json").write_text(json.dumps(fleet_))
    try:
        yield sd
    finally:
        os.environ["ORCH_FLEET_STATE"] = was


def orch(*args):
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), *args], capture_output=True, text=True, env=dict(os.environ))
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def raw(sd=None):
    db = sqlite3.connect(str((sd or fleet.state_dir()) / store.DB_NAME))
    db.row_factory = sqlite3.Row
    return db


def refused(call, *words, kind=ValueError):
    try:
        call()
    except kind as e:
        assert all(w in str(e) for w in words), e
        return
    raise AssertionError(words)


# ---- import: both steps in one run, each with its backup, tombstones, every id and close kept
with state("import") as sd:
    (sd / "board.prev.json").write_text("{}")
    (sd / "tidy.jsonl.1").write_text("{}\n")
    original = (sd / "board.json").read_bytes()
    plan = store.import_plan()["board"]
    assert plan["items"] == {"todo": 1, "later": 2, "doing": 2, "owner_todo": 1} and plan["problems"] == [], plan
    assert (plan["open"], plan["closed"], plan["ids"], plan["aliases"], plan["closures"]) == (5, 1, "b1..b6", 6, 6), plan
    assert plan["leads_orca_knows"] == ["L"] and plan["rows_pr_cut"] == 1 and plan["tidy_lines"] == len(TIDY), plan
    assert not store.path().exists(), "the plan creates nothing"
    out = store.import_apply(checks=False)
    assert "imported 6 board items, 6 closures, 4 rows, 1 leads (1 Orca knows" in out and "tombstoned board.json, tidy.jsonl" in out, out
    assert store.migrated_steps() == {"4a-decisions": "tombstoned", "4b-board": "tombstoned"}
    assert (sd / "board.json").is_dir() and (sd / "tidy.jsonl").is_dir()
    db = raw()
    row = db.execute("SELECT * FROM migration WHERE step='4b-board'").fetchone()
    bak = pathlib.Path(row["backup"])
    assert (bak / "board.json").read_bytes() == original and (bak / "board.prev.json").is_file() and (bak / "tidy.jsonl.1").is_file()
    assert json.loads(row["sources"])["board.json"] == store.sha256(bak / "board.json")
    assert not (sd / "board.prev.json").exists() and not (sd / "tidy.jsonl.1").exists()
    assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    # The originals are in the event log, tidy's every line but the carried copy.
    assert db.execute("SELECT count(*) FROM event WHERE entity='tidy'").fetchone()[0] == len(TIDY) - 1
    assert json.loads(db.execute("SELECT before FROM event WHERE entity='item' AND ev='import' AND id='b1'").fetchone()[0]) == BOARD["todo"][0]
    assert db.execute("SELECT count(*) FROM event WHERE ev='import_orca_knows' AND id='L'").fetchone()[0] == 1
    items = {r["id"]: dict(r) for r in db.execute("SELECT * FROM item")}
    b = store.board()
    closed_id = store.resolve(db, H["closed"])
    assert items[closed_id]["status"] == "done" and items[closed_id]["close_evidence"] == "e" and closed_id not in [d["id"] for d in b["doing"]]
    again_id = store.resolve(db, H["reopened"])
    assert items[again_id]["status"] == "held" and items[again_id]["reopen_evidence"] == "undone"
    assert [t["title"] for t in b["owner_todo"]] == ["now one"] and b["owner_todo"][0]["id"].startswith("b")
    assert b["doing"][0]["since"] == T and b["doing"][0]["waiting"] is True and b["later"][1]["due"] == "10/1"
    assert b["todo"][0]["added"] == T and b["order"] == BOARD["order"]
    # A row linked to a card keeps the link and not the card's PR; an unlinked row keeps its own.
    assert "pr" not in b["rows"]["L/card"] and b["rows"]["L/card"]["reported_at"] == T and b["rows"]["L/free"]["pr"] == "acme/app#2"
    # A lead Orca knows leaves the lead table and its row keeps the session; a closed one stays.
    assert set(b["leads"]) == {"Old"} and b["rows"]["L"]["worktree"] == "wt-lead" and b["leads"]["Old"]["closed_at"] == T
    assert b["rows"]["Old"]["done_at"] == "2099-01-01T00:00:00Z"
    # A close with no item left stays by its old id.
    units = {r["unit"]: dict(r) for r in db.execute("SELECT * FROM closure")}
    assert set(units) == {"row:L/free", "card:s1", "req:r1", "doing:deadbeef", "ext:k1", "ext:gone"}
    assert store.latest()["doing:deadbeef"]["ev"] == "done" and not tidy.can_reopen("doing:deadbeef")
    code, _, err = orch("board", "reopen", "doing:deadbeef", "req:r1")
    assert code == 1 and "left the board" in err and "owner-request ledger" in err, err
    db.close()
    # An old id still reaches its item, in both directions.
    assert orch("board", "reopen", H["closed"]) == (0, f"reopened 1: {closed_id}", "")
    assert closed_id in [d["id"] for d in store.board()["doing"]]
    assert orch("board", "done", closed_id, "--why", "again")[0] == 0
    # Re-run: nothing, and nothing written.
    before = store.dump()
    assert store.import_apply(checks=False) == "already imported and tombstoned"
    assert store.dump() == before


# ---- the same words twice in a lane: both take the tidy state, the alias the first
twin = {**BOARD, "doing": [BOARD["doing"][0], dict(BOARD["doing"][0])]}
with state("twin", twin) as sd:
    assert store.import_plan()["board"]["same_hash"] == [H["closed"]]
    store.import_apply(checks=False)
    assert raw().execute("SELECT count(*) FROM item WHERE lane='doing' AND status='done'").fetchone()[0] == 2
    assert raw().execute("SELECT count(*) FROM alias WHERE old=?", (H["closed"],)).fetchone()[0] == 1


# ---- a malformed legacy entry stops the import before either step touches anything
bad = {**BOARD, "doing": [{"owner": "a"}]}
with state("malformed", bad) as sd:
    (sd / "decisions.json").write_text('{"next": 1, "decisions": {}}')
    refused(lambda: store.import_apply(checks=False), "doing", kind=store.Refused)
    assert (sd / "board.json").is_file() and (sd / "decisions.json").is_file() and not store.path().exists()
with state("unknown-field", {**BOARD, "later": [{"title": "x", "trigger": "t", "note": "hand"}]}) as sd:
    assert store.import_plan()["board"]["unknown_fields"], "reported"
    store.import_apply(checks=False)  # and kept only in the event's original
    assert "note" in raw().execute("SELECT before FROM event WHERE entity='item' AND id=?", (store.board()["later"][0]["id"],)).fetchone()[0]


# ---- the write path: stable ids through rewording and moves, every change with its event, dropped rows stay
with state("writes", None, None) as sd:
    store.import_apply(checks=False)
    board.later_add("캐시 TTL 다시 보기", "10/20")
    first = board.read()["later"][0]["id"]
    board.later_add("캐시 TTL을 다시 보기", "10/20")
    assert [(l["id"], l["title"]) for l in board.read()["later"]] == [(first, "캐시 TTL을 다시 보기")]
    board.todo_from_later(1, due="2026-12-01", who="w")
    assert board.read()["later"] == [] and board.read()["todo"][0]["id"] == first
    assert raw().execute("SELECT lane, status FROM item WHERE id=?", (first,)).fetchone()[0] == "todo"
    board.todo_add("다시 쓴 일", due="2026-12-02", who="w")
    second = board.read()["todo"][1]["id"]
    board.todo_add("다시 쓴 일 하나", due="2026-12-02", who="w")
    assert [t["id"] for t in board.read()["todo"]] == [first, second]
    board.set_now(["a", "b"])
    a, b_ = [t["id"] for t in board.read()["owner_todo"]]
    board.set_now(["b", "c"])
    now_list = board.read()["owner_todo"]
    assert now_list[0]["id"] == b_ and now_list[1]["id"] not in (a, b_) and now_list[1]["title"] == "c"
    assert raw().execute("SELECT status FROM item WHERE id=?", (a,)).fetchone()[0] == "dropped"
    # A close by old-style position: ids are never reused and a drop keeps the row, so its number stays taken.
    ids = [r[0] for r in raw().execute("SELECT id FROM item")]
    assert len(ids) == len(set(ids))
    refused(lambda: store.change_board(lambda s: s["later"].extend([dict(s["todo"][0]), dict(s["todo"][0])])), "twice")
    refused(lambda: store.change_board(lambda s: s["later"].append({"id": "b999", "title": "x", "trigger": "t"})), "b999")
    assert [ev["ev"] for ev in raw().execute("SELECT ev FROM event WHERE entity='item' AND id=? ORDER BY seq", (first,))] == ["add", "update", "update"]


# ---- the write bans
with state("bans", None, None, STATE) as sd:
    store.import_apply(checks=False)
    refused(lambda: board.doing_add("abc-1-login 후속", "a", "n"), "card wt-card", "--force")
    refused(lambda: board.doing_add("Read the login flow 확인", "a", "n"), "subagent ag1")
    refused(lambda: board.doing_add("abc-1-login 후속", "a", "n", force=True), "--why")
    assert board.read()["doing"] == []
    board.doing_add("abc-1-login 후속", "a", "n", force=True, why="오너 지시로 따로 추적")
    iid = board.read()["doing"][0]["id"]
    after = json.loads(raw().execute("SELECT after FROM event WHERE entity='item' AND id=?", (iid,)).fetchone()[0])
    assert after["forced"] == "오너 지시로 따로 추적"
    board.doing_add("카드와 무관한 일", "a", "n")
    # A row names its card: no PR of its own, by name or by --worktree; an unlinked row links once.
    refused(lambda: board.set_row("abc-1-login", repo="acme/app", pr="acme/app#9"), "linked to card wt-card")
    refused(lambda: board.set_row("L", repo="acme/app", pr="acme/app#9", worktree="wt-card"), "linked to card wt-card")
    board.set_row("L", repo="acme/app", worktree="wt-card")
    refused(lambda: board.set_row("L", worktree="wt-other"), "drop the row")
    refused(lambda: board.set_row("L", pr="acme/app#9"), "linked to card wt-card")
    board.set_row("L", next="다음")  # everything else on the row still writes
    board.set_row("L", worktree="wt-card")  # and naming the same card again is no change
    board.set_row("plain", repo="acme/app", pr="acme/app#9")  # a row about no card keeps its PR
    assert board.read()["rows"]["plain"]["pr"] == "acme/app#9"
    # A lead Orca knows has its row by `board set`; a lead it cannot see is added as before.
    refused(lambda: board.lead_add("M", "term_0FAKE1", "acme/app"), "Orca already knows lead wt-lead", "orch board set M")
    assert "M" not in board.read()["leads"]
    board.lead_add("N", "term_else", "acme/app")
    assert "N" in board.read()["leads"]
    # Through orch: the refusal is a plain message and exit 1, and --force --why pass.
    code, _, err = orch("board", "doing", "add", "abc-1-login 또", "--owner", "a", "--next", "n")
    assert code == 1 and "already shows this work" in err, err
    assert orch("board", "doing", "add", "abc-1-login 또", "--owner", "a", "--next", "n", "--force", "--why", "w")[0] == 0
    # No fleet state: nothing to check against, so the write goes through with one warning line.
    (sd / "state.json").unlink()
    code, _, err = orch("board", "doing", "add", "abc-1-login 셋", "--owner", "a", "--next", "n")
    assert code == 0 and err == "warning: no fleet state; doing add not checked against Orca", err


# ---- the inbox's effective state: the file and the closure together
def keys():
    return {i["key"]: i for i in fleet.external_items()}


def close(*ids):
    return tidy.write(list(ids), can={i: "x" for i in ids})


with state("ext", None, None) as sd:
    store.import_apply(checks=False)
    fleet.inbox_add("review", "t1", key="a")
    fleet.inbox_add("review", "t2", key="b")
    assert set(keys()) == {"ext:a", "ext:b"}
    size = (sd / "inbox-external.jsonl").stat().st_size
    # Closed: hidden, and the file is not written.
    assert close("ext:a")[0] == ["ext:a"] and "ext:a" not in keys() and (sd / "inbox-external.jsonl").stat().st_size == size
    # Reopened: back, from the reopen's time, the file still unwritten.
    assert tidy.write(["ext:a"], undo=True)[0] == ["ext:a"]
    assert keys()["ext:a"]["at"] == store.latest()["ext:a"]["ts"] and (sd / "inbox-external.jsonl").stat().st_size == size
    # Closed, then a new add of the key: the line after the mark shows it.
    close("ext:a")
    fleet.inbox_add("review", "t1 again", key="a")
    assert keys()["ext:a"]["title"] == "t1 again"
    # An identical line in the same second is still a later line: the byte offset tells them apart.
    close("ext:a")
    lines = (sd / "inbox-external.jsonl").read_bytes()
    last = lines.splitlines(True)[-1]
    (sd / "inbox-external.jsonl").write_bytes(lines + last)
    assert "ext:a" in keys()
    # Reopened, then resolved by a writer of the file: the line decides.
    close("ext:a")
    tidy.write(["ext:a"], undo=True)
    fleet.inbox_resolve("a")
    assert "ext:a" not in keys()
    # A reopen an old close's age would have dropped comes back: the closure's time is its age.
    (sd / "inbox-external.jsonl").write_text(json.dumps({"v": 1, "op": "add", "key": "old", "type": "review", "title": "old",
                                                         "at": "2026-01-01T00:00:00Z"}) + "\n")
    close("ext:old")
    tidy.write(["ext:old"], undo=True)
    assert "ext:old" in {i["key"] for i in fleet.external_items(fleet.utcnow())}
    # A file rewritten by hand loses the mark: the last line decides and the headline says so.
    fleet.inbox_add("review", "k", key="c")
    close("ext:c")
    (sd / "inbox-external.jsonl").write_text(json.dumps({"v": 1, "op": "add", "key": "c", "type": "review", "title": "other", "at": T}) + "\n")
    assert "ext:c" in keys()
    assert any(f.startswith("인박스 기록 어긋남 1 (c") for f in board.render(fresh=False, periodic=False)["headline_facts"])


# ---- import's marks keep what the file alone showed, in every order the old tidy wrote
def legacy_inbox(sd, *rows):
    (sd / "inbox-external.jsonl").write_text("".join(json.dumps({"v": 1, "key": k, **r}) + "\n" for k, r in rows))


ADD = {"op": "add", "type": "review", "title": "t", "at": "2026-10-01T00:00:00Z"}
RES = {"op": "resolve", "at": "2026-10-01T01:00:00Z"}
with state("marks", {**BOARD, "doing": [], "later": [], "todo": [], "owner_todo": []},
           [line("done", "ext:shut"), line("reopen", "ext:back", evidence="e"), line("done", "ext:newer"), line("done", "ext:none")]) as sd:
    legacy_inbox(sd, ("shut", ADD), ("shut", RES), ("back", ADD), ("back", RES), ("back", ADD),
                 ("newer", ADD), ("newer", RES), ("newer", ADD), ("none", ADD))
    store.import_apply(checks=False)
    got = keys()
    assert "ext:shut" not in got and "ext:back" in got and "ext:newer" in got, got
    # Closed with no resolve line to mark: no mark, and the last line decides as the file alone did.
    assert "ext:none" in got and store.ext_closures()["none"]["ext_mark"] is None


# ---- tidy judges outside the transaction and checks inside it
with state("race", None, None) as sd:
    store.import_apply(checks=False)
    pr = {"state": "MERGED", "merged_at": T, "key": "acme/app#1", "number": 1, "repo": "acme/app"}
    (sd / "prs.json").write_text(json.dumps({"me": "o", "prs": {"acme/app#1": {"detail": pr}}}))
    (sd / "state.json").write_text(json.dumps({"generated_at": T, "sessions": [{"id": "s", "kind": "standalone", "phase": "open", "terminals": []}], "prs": []}))
    board.doing_add("acme/app#1 정리", "a", "n")
    fleet.inbox_add("review", "acme/app#1 리뷰", key="r")
    assess, write_seen = tidy.assess, []

    def moved(*a, **k):
        got = assess(*a, **k)
        board.change(lambda s: s["doing"][0].update(next="다시 씀"))
        fleet.inbox_add("review", "acme/app#1 리뷰 또", key="r")
        return got

    # updated_at has one-second grain: the move below would look unchanged inside the same second.
    store.change_board(lambda s: s["doing"][0].update(next="이전"), None)
    with store.tx() as db:
        db.execute("UPDATE item SET updated_at='2026-01-01T00:00:00Z' WHERE lane='doing'")
    tidy.assess = moved
    try:
        rows = tidy.run()
    finally:
        tidy.assess = assess
    assert [r["id"] for r in rows] == ["ext:r"], rows  # the item changed since it was judged: left for the next tick
    assert board.read()["doing"][0]["next"] == "다시 씀"
    assert keys()["ext:r"]["title"] == "acme/app#1 리뷰 또", "the add after the judgment stays"
    # The next tick closes both on what it sees then, and a reopen on the same evidence holds.
    iid = board.read()["doing"][0]["id"]
    assert {r["id"] for r in tidy.run()} == {iid, "ext:r"}
    tidy.write(["ext:r", iid], undo=True)
    assert tidy.run() == []


# ---- export to the 4a files, rollback, and a later import
def export(name):
    d = tmp / name
    code, out, err = orch("db", "export", "--legacy", "--target", "4a", str(d))
    assert code == 0 and "wal checkpoint" not in err, (out, err)
    return d


with state("roundtrip", fleet_=None) as sd:
    legacy_inbox(sd, ("k1", ADD), ("k1", RES), ("k3", ADD))
    store.import_apply(checks=False)
    store.change_board(lambda s: s["doing"].append({"title": "after import", "owner": "a", "next": "n"}))
    made = store.board()["doing"][-1]["id"]
    close("ext:k3")
    d = export("export-1")
    assert sorted(p.name for p in d.iterdir()) == ["board.json", "export.json", "inbox-external.jsonl", "tidy.jsonl"]
    old = json.loads((d / "board.json").read_text())
    assert old["owner_todo"] == ["now one"] and old["doing"][0] == BOARD["doing"][0] and old["doing"][1] == BOARD["doing"][1]
    assert old["later"] == BOARD["later"] and old["rows"]["L/free"] == BOARD["rows"]["L/free"] and old["rows"]["Old"]["done_at"]
    assert old["todo"][0]["added"] == T and "id" not in old["doing"][0] and "updated_at" not in old["later"][0]
    lines = [json.loads(l) for l in (d / "tidy.jsonl").read_text().splitlines()]
    last = {l["id"]: l for l in lines}
    assert last[H["closed"]]["ev"] == "done" and last[H["reopened"]]["ev"] == "reopen" and last[H["reopened"]]["evidence"] == "undone"
    assert {"row:L/free", "card:s1", "req:r1", "doing:deadbeef", "ext:k1", "ext:k3"} <= set(last)
    # The inbox copy says alone what the closures said: k3's resolve line, which tidy no longer writes.
    rows = [json.loads(l) for l in (d / "inbox-external.jsonl").read_text().splitlines()]
    assert rows[-1]["op"] == "resolve" and rows[-1]["key"] == "k3", rows
    # Same words in two states have no legacy form: refused, and nothing half-written.
    with state("twin-export", twin, []) as sd2:
        store.import_apply(checks=False)
        one = raw(sd2).execute("SELECT id FROM item WHERE lane='doing' ORDER BY id").fetchone()[0]
        close(one)
        refused(lambda: store.export_legacy(tmp / "twin-out", "4a"), "same words", kind=store.Refused)
        assert not (tmp / "twin-out").exists()
    # Rollback checks what it puts back.
    (tmp / "not-export").mkdir()
    refused(lambda: store.rollback_board(tmp / "not-export", checks=False), "export.json", kind=store.Refused)
    fleet.inbox_add("review", "news", key="late")
    refused(lambda: store.rollback_board(d, checks=False), "changed after that export", kind=store.Refused)
    d = export("export-2")
    store.change_board(lambda s: s["doing"].pop())
    refused(lambda: store.rollback_board(d, checks=False), "the DB changed", kind=store.Refused)
    d = export("export-3")
    # A crash after the files are placed, before the step leaves, runs again.
    real = store.tx
    store.tx = lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt("killed before the step left"))
    try:
        store.rollback_board(d, checks=False)
    except KeyboardInterrupt:
        pass
    finally:
        store.tx = real
    assert (sd / "board.json").is_file() and "4b-board" in store.migrated_steps()
    assert "undone" in store.rollback_board(d, checks=False)
    assert "4b-board" not in store.migrated_steps() and json.loads((sd / "board.json").read_text()) == json.loads((d / "board.json").read_text())
    assert store.import_plan()["board"]["state"] == "not imported"
    refused(store.board, "4b-board", kind=store.NotMigrated)
    refused(lambda: store.rollback_board(d, checks=False), "nothing to undo", kind=store.Refused)
    # Importing again takes the files back in, and no id comes back with other work.
    was = int(raw().execute("SELECT next FROM counter WHERE name='b'").fetchone()[0])
    store.import_apply(checks=False)
    ids = [int(r[0][1:]) for r in raw().execute("SELECT id FROM item")]
    assert min(ids) >= was, (was, ids)
    assert made not in {r[0] for r in raw().execute("SELECT id FROM item")}
    assert store.latest()["card:s1"]["ev"] == "done" and "ext:k3" not in keys()


# ---- backup and restore: every table, losslessly, with the tombstones
with state("backup") as sd:
    store.import_apply(checks=False)
    before = store.dump()
    assert orch("db", "backup", str(tmp / "b.db"))[0] == 0
with state("restored", None, None, None) as sd:
    assert store.restore(tmp / "b.db", checks=False) == store.path()
    assert store.dump() == before and (sd / "board.json").is_dir() and (sd / "tidy.jsonl").is_dir()
    assert raw().execute("PRAGMA integrity_check").fetchone()[0] == "ok"
with state("restore-onto-board") as sd:
    refused(lambda: store.restore(tmp / "b.db", checks=False), "board.json", "is a file here", kind=store.Refused)


# ---- a crash at each point of the 4b step
with state("crash-in-tx") as sd:
    original = (sd / "board.json").read_bytes()
    real = store._fill_board
    store._fill_board = lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt("killed in the transaction"))
    try:
        store.import_apply(checks=False)
    except KeyboardInterrupt:
        pass
    finally:
        store._fill_board = real
    assert (sd / "board.json").read_bytes() == original and store.migrated_steps() == {"4a-decisions": "tombstoned"}
    assert raw().execute("PRAGMA user_version").fetchone()[0] == 1
    assert "imported 6 board items" in store.import_apply(checks=False), "the next run starts over"

for name, touch in (("crash-after", False), ("crash-after-write", True)):
    with state(name) as sd:
        original = (sd / "board.json").read_bytes()
        real = store._finish
        store._finish = lambda step, row: real(step, row) if step == store.DECISION_STEP else (_ for _ in ()).throw(KeyboardInterrupt("killed"))
        try:
            store.import_apply(checks=False)
        except KeyboardInterrupt:
            pass
        finally:
            store._finish = real
        assert (sd / "board.json").read_bytes() == original and store.migrated_steps()["4b-board"] == "imported"
        if touch:
            (sd / "board.json").write_text("{}")
            refused(lambda: store.import_apply(checks=False), "conflict: board.json changed", kind=store.Refused)
            assert (sd / "board.json").is_file()
        else:
            assert store.import_apply(checks=False).endswith("tombstoned board.json, tidy.jsonl")
            assert (sd / "board.json").is_dir() and raw().execute("SELECT count(*) FROM item").fetchone()[0] == 6
            (sd / "board.json").rmdir()
            (sd / "board.json").write_text("{}")
            assert store.legacy_traces() == ["board.json (a file again)"]


# ---- before the import: no write, and the report says what it cannot know
with state("before", None, None, STATE) as sd:
    for call in (lambda: board.later_add("x", "t"), lambda: tidy.write(["row:x"], can={"row:x": "x"})):
        refused(call, "run orch db import", kind=store.NotMigrated)
    code, _, err = orch("board", "later", "add", "x", "--trigger", "t")
    assert code == 1 and "orch db cutover-plan 4b" in err, err
    assert tidy.run() == [] and tidy.closed() == {} and tidy.timeline() == [] and fleet.board_prs() == set()
    r = board.render(fill=True, fresh=False, periodic=False)
    assert "이행 대기: 보드·결정 DB (`orch db cutover-plan 4b`)" in r["headline_facts"], r["headline_facts"]
    assert r["states"] and all(s["status"] == "check" for s in r["states"].values()), r["states"]
    assert r["duplicates"] == [] and r["missing"] == [] and not store.path().exists()
    assert rowstate.judged({"id": "x", "kind": "task", "status": "moving"}, {"db_unknown": True})["unknown"] == ["결정·닫힘 DB 이행 대기"]
    assert all(v["status"] == "check" for v in rowstate.snapshot()["by_pr"].values())
# A 4a DB, the guard row not in: the board is still not read, and 4a's decisions wait with it.
with state("half") as sd:
    store.import_apply(checks=False, upto=store.DECISION_STEP)
    refused(store.board, "schema version 1", kind=store.NotMigrated)
    assert board.render(fresh=False, periodic=False)["headline_facts"][-1].startswith("이행 대기"), "and it says so"


# ---- only the migration code names the files the board left: everything else reads the DB
import ast  # noqa: E402
for p in HERE.glob("*.py"):
    if p.name.startswith("test_") or p.name == "store.py":
        continue
    for n in ast.walk(ast.parse(p.read_text())):
        if isinstance(n, ast.Constant) and n.value in ("board.json", "board.prev.json", "tidy.jsonl", "tidy.jsonl.1"):
            raise AssertionError((p.name, n.lineno, n.value))


# ---- cutover-plan 4b prints the owner's commands and runs nothing
with state("plan", None, None, None) as sd:
    calls = []
    real_run, real_sh = subprocess.run, store._sh
    subprocess.run = store._sh = lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("ran"))
    try:
        text = store.cutover_plan("4b")
    finally:
        subprocess.run, store._sh = real_run, real_sh
    assert calls == [] and list(sd.iterdir()) == []
    for words in ("orch db import --dry-run", "orch db import --apply", "orch db export --legacy --target 4a <dir>",
                  "orch db rollback 4b <dir>", "skills-sync setup disable janitor", "skills-sync setup enable janitor"):
        assert words in text, (words, text)
    code, out, _ = orch("db", "cutover-plan", "4b")
    assert code == 0 and out == text and list(sd.iterdir()) == [], out

print("test_store_board: ok")
