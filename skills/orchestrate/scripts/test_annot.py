"""Offline tests for card annotations (`orch board set card:<worktree>`) and their migration (`orch board annot-migrate`):
the write rules, that an annotation changes nothing on the screen yet, the refusals of a new card-linked row and of
`doing add --force`, and a migration in a fake HOME (names invented).

Run: python3 test_annot.py
"""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-annot-"))
env = {**os.environ, "HOME": str(tmp / "home"), "ORCH_FLEET_STATE": str(tmp / "dashboard"), "ORCH_BRIEF_LAST": str(tmp / "brief-last"),
       "ORCH_FLEET_CONFIG": str(tmp / "none.json"), "AGENT_SKILLS_STATE": str(tmp / "state"), "TZ": "UTC",
       "AGENT_SKILLS_CONFIG": str(tmp / "none.json"), "PROGRAMS_HOME": str(tmp / "programs"), "ORCA_TERMINAL_HANDLE": "term_root"}
os.environ.update(env)
sys.path.insert(0, str(HERE))
import board, fleet, store  # noqa: E402

T = "2026-10-01T00:00:00Z"
sd = fleet.state_dir()
sd.mkdir(parents=True, exist_ok=True)


def sess(sid, name, kind="standalone"):
    return {"id": sid, "name": name, "kind": kind, "phase": "working", "parent": "root", "orca_parent": None, "task_title": name,
            "gh_repo": "acme/app", "project": "app", "terminals": [{"handle": f"term_{sid}"}], "path": f"/w/{name}",
            "last_activity": fleet.iso(fleet.utcnow()), "report": {"at": T, "next": "작업 중"}, "dispatch": None, "run": None}


def write_state(*cards):
    fleet.write_atomic(sd / "state.json", json.dumps({
        "generated_at": fleet.iso(fleet.utcnow()), "root": "root", "sources": {}, "prs": [],
        "sessions": [{**sess("root", "root", "orchestrator"), "parent": None, "terminals": [{"handle": "term_root"}]}, *cards]}))


CARDS = [sess("wt-card", "abc-1-login"), sess("wt-other", "other-card"), sess("wt-third", "third-card"), sess("wt-mine", "mine-card")]
write_state(*CARDS)
fleet.write_atomic(sd / "prs.json", json.dumps({"me": "me-login", "prs": {}}))  # the owner's login, as the collector writes it


def row(rid, **kw):
    return {"id": rid, "kind": "task", "title": rid, "parent": None, "status": "moving", "next": "", "reported_at": T, **kw}


LEGACY = {"v": 1, "order": [], "leads": {}, "doing": [], "later": [], "todo": [], "owner_todo": [], "rows": {
    "P": row("P", kind="phase", title="Stage 2", repo="acme/app"),
    # one card by --worktree, with an owner block, a progress, an after and a PR the import cut
    "P/one": row("P/one", parent="P", title="login work", worktree="wt-card", status="owner", owner="오너", next="직접 머지",
                 progress=[2, 5], after="acme/app#3", pr="acme/app#1"),
    # the worktree the import event kept is the only evidence (the row lost its own)
    "P/moved": row("P/moved", parent="P", title="small job", worktree="wt-other", next="CI 확인"),
    # no evidence: the worktree it claims is no card of Orca's
    "gone": row("gone", title="old thing", repo="acme/app", worktree="wt-gone"),
    # two cards: its worktree is one, its title names another
    "both": row("both", title="other-card sync", repo="acme/app", worktree="wt-card"),
    # its title names two cards, whatever its worktree says
    "twin": row("twin", title="abc-1-login and other-card sync", repo="acme/app", worktree="wt-card"),
    # two rows for one card
    "d1": row("d1", title="d1 job", repo="acme/app", worktree="wt-third"),
    "d2": row("d2", title="d2 job", repo="acme/app", worktree="wt-third"),
    # work with no session: not a card's row, stays as it is
    "free": row("free", title="free", repo="acme/app"),
    # blocked on the owner by login, not by the word 오너
    "mine": row("mine", title="mine job", repo="acme/app", worktree="wt-mine", owner="me-login", next="Approve the change"),
}}
CLOSE = [{"ev": "done", "id": "row:P/one", "ts": "2026-10-02T00:00:00Z", "by": "owner", "rule": "manual", "evidence": "merged",
          "label": "login work"}]


def orch(*args):
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), *args], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def refused(call, *words):
    try:
        call()
    except ValueError as e:
        assert all(w in str(e) for w in words), e
        return
    raise AssertionError(words)


def screen():
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), "brief", "render", "--json", "--no-refresh"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, (r.stdout, r.stderr)
    return json.loads(r.stdout)["snapshot"]["rows"]


store.import_apply(checks=False)
store.load_board(LEGACY, CLOSE, {"sessions": CARDS})
board.change(lambda s: s["rows"]["P/moved"].pop("worktree"))  # leaves the import event as the only record of it
before = screen()

# ---- the write path: only the five fields, `-` clears, owner and next as a pair
refused(lambda: board.set_row("card:wt-card", title="x"), "annotation", "--title")
refused(lambda: board.set_row("card:wt-card", pr="acme/app#9"), "--pr")
refused(lambda: board.set_row("card:wt-card"), "needs one of")
refused(lambda: board.set_row("card:wt-nope", phase="p"), "no card wt-nope")
refused(lambda: board.set_row("card:wt-card", after="soon"), "owner/repo#N")
refused(lambda: board.set_row("card:wt-card", progress="9/3"), "N <= M")
refused(lambda: board.set_row("card:wt-card", owner="오너"), "--owner needs --next")
refused(lambda: board.set_row("card:wt-card", next="CI 확인"), "step after merge")
assert board.read()["annots"] == {}
board.set_row("card:wt-card", phase="단계 2", progress="1/4", after="acme/app#3")
board.set_row("card:wt-card", owner="오너", next="직접 머지")
a = board.read()["annots"]["card:wt-card"]
assert (a["kind"], a["phase"], a["progress"], a["after"], a["owner"], a["next"]) == ("annot", "단계 2", [1, 4], "acme/app#3", "오너", "직접 머지"), a
assert not {"title", "pr", "worktree"} & set(a), a
board.set_row("card:wt-card", owner="-")  # the block is lifted, its task goes with it
a = board.read()["annots"]["card:wt-card"]
assert "owner" not in a and "next" not in a and a["phase"] == "단계 2", a
board.set_row("card:wt-card", next="릴리스 확인")  # a step after merge needs no owner
assert board.read()["annots"]["card:wt-card"]["next"] == "릴리스 확인"
board.set_row("card:wt-card", next="-", phase="-", progress="-", after="-")
assert board.read()["annots"] == {}, "an annotation with no field left is gone"
code, _, err = orch("board", "set", "card:wt-card", "--phase", "단계 3")
assert code == 0 and board.read()["annots"]["card:wt-card"]["phase"] == "단계 3", err
code, _, err = orch("board", "set", "card:wt-card", "--status", "done")
assert code == 1 and "--status" in err, err

# ---- until the renderer reads annotations the screen does not change, and an annotation is no row
assert "card:wt-card" not in board.read()["rows"] and screen() == before, "an annotation shows nowhere yet"

# ---- a new row for a card is refused; a row about no card is not
refused(lambda: board.set_row("fresh", repo="acme/app", worktree="wt-card"), "would be card wt-card's", "card:wt-card")
refused(lambda: board.set_row("abc-1-login", repo="acme/app"), "would be card wt-card's")
board.set_row("fresh", repo="acme/app", title="no card", status="moving")
assert "fresh" in board.read()["rows"]
board.drop("fresh")

# ---- doing add has no override
refused(lambda: board.doing_add("abc-1-login 후속", "a", "n"), "card wt-card", "reworded title")
try:
    board.doing_add("abc-1-login 후속", "a", "n", force=True)
    raise AssertionError("doing add has no override")
except TypeError:
    pass
code, _, err = orch("board", "doing", "add", "abc-1-login 후속", "--owner", "a", "--next", "n", "--force", "--why", "w")
assert code == 1 and "no --force" in err and board.read()["doing"] == [], err

# ---- migration: a plan with the evidence of each row; nothing is written without --apply
board.set_row("card:wt-other", phase="수동")  # a field already written wins over the row's
move, left = board.annot_migrate()
assert sorted(move) == ["wt-card", "wt-mine", "wt-other"], move
assert move["wt-mine"] == ("mine", {"owner": "me-login", "next": "Approve the change"}), "the owner's login is an owner block"
assert move["wt-card"] == ("P/one", {"phase": "Stage 2", "progress": [2, 5], "after": "acme/app#3", "owner": "오너", "next": "직접 머지"}), move
assert move["wt-other"] == ("P/moved", {"phase": "Stage 2"}), "a milestone copy in next is not carried"
assert sorted(r for r, _, _ in left) == ["both", "d1", "d2", "gone", "twin"], left
why = {r: w for r, w, _ in left}
assert "no card of Orca's" in why["gone"] and "2 cards" in why["both"] and "2 rows point at card wt-third" in why["d1"], why
assert "2 cards" in why["twin"], "two names in the title are evidence for two cards, not none"
rows_before = json.dumps(board.read()["rows"], sort_keys=True)
code, out, err = orch("board", "annot-migrate")
assert code == 0 and out.startswith("would move 3 row(s)") and "중복 5건" in out and "orch board drop gone" in out, (out, err)
assert json.dumps(board.read()["rows"], sort_keys=True) == rows_before, "a dry run writes nothing"
# the apply waits for the renderer: a moved row would leave the screen with nothing in its place
refused(lambda: board.annot_migrate(apply=True), "A-ANNOT-b")
code, _, err = orch("board", "annot-migrate", "--apply")
assert code == 1 and "A-ANNOT-b" in err and json.dumps(board.read()["rows"], sort_keys=True) == rows_before, err

# ---- the apply, with the gate open: annotation, alias, dropped original (kept in the event), the old close
board.ANNOT_RENDERED = True
assert board.annot_migrate(apply=True)[0] == move
annots = board.read()["annots"]
assert annots["card:wt-card"]["aliases"] == ["row:P/one"] and annots["card:wt-card"]["owner"] == "오너" \
    and annots["card:wt-card"]["next"] == "직접 머지", annots  # the owner block moves as a pair
assert annots["card:wt-other"]["phase"] == "수동", "the written field won"
rows = board.read()["rows"]
assert rows["P/one"]["status"] == "dropped" and rows["P/moved"]["status"] == "dropped" and rows["free"]["status"] == "moving", rows
db = store.open_db()
try:
    orig = json.loads(db.execute("SELECT before FROM event WHERE entity='board_row' AND id='P/one' AND ev='update' ORDER BY seq DESC"
                                 ).fetchone()[0])
    imported = json.loads(db.execute("SELECT before FROM event WHERE entity='board_row' AND id='P/one' AND ev='import'").fetchone()[0])
finally:
    db.close()
assert orig["status"] == "owner" and orig["next"] == "직접 머지" and imported["pr"] == "acme/app#1", (orig, imported)
latest = store.latest()
assert latest["row:P/one"]["ev"] == "done" and latest["card:wt-card"] == {**latest["row:P/one"], "id": "card:wt-card"}, "the old close reaches the card"
assert "card:wt-other" not in latest

# ---- clearing every field keeps the alias (the close the card inherits), and an export keeps the annotations
board.set_row("card:wt-card", owner="-", phase="-", progress="-", after="-")
kept = board.read()["annots"]["card:wt-card"]
assert kept["aliases"] == ["row:P/one"] and not {"phase", "progress", "after", "owner", "next"} & set(kept), kept
assert store.latest()["card:wt-card"]["ev"] == "done"
saved_annots = board.read()["annots"]
exported = store._export_board(tmp / "export")[0]
saved = json.loads((exported / "board.json").read_text())
assert saved["annots"] == saved_annots and not [k for k in saved["rows"] if k.startswith("card:")], saved
store.load_board(saved, CLOSE)  # the closes travel in tidy.jsonl
assert board.read()["annots"] == saved_annots, "annotations come back through the import"
(exported / "bad.json").write_text(json.dumps({**saved, "annots": {"card:": {"id": "card:", "kind": "annot"}, "x": {"id": "x"}}}))
assert len([p for p in store._legacy_board(exported / "bad.json")[1] if "annotation" in p]) == 2

# ---- running it again changes nothing
snap = json.dumps([board.read()["annots"], board.read()["rows"]], sort_keys=True)
again, left2 = board.annot_migrate(apply=True)
assert again == {} and left2 == left and json.dumps([board.read()["annots"], board.read()["rows"]], sort_keys=True) == snap, again
code, out, _ = orch("board", "annot-migrate")
assert code == 0 and out.startswith("would move 0 row(s)") and "중복 5건" in out, out
# a close the card inherits can be undone: the reopen lands on the row's close, which the alias reads through
import tidy  # noqa: E402

assert tidy.write(["card:wt-card"], undo=True)[0] == ["card:wt-card"]
assert store.latest()["card:wt-card"]["ev"] == "reopen" and store.latest()["row:P/one"]["ev"] == "reopen", store.latest()

# ---- a card and its alias reopened in one call undo the close once, with its evidence kept
assert tidy.write(["row:P/one"], can={"row:P/one": "x"})[0] == ["row:P/one"]
assert store.latest()["card:wt-card"]["ev"] == "done"
assert sorted(tidy.write(["card:wt-card", "row:P/one"], undo=True)[0]) == ["card:wt-card", "row:P/one"]
assert store.latest()["row:P/one"]["ev"] == "reopen" and store.latest()["row:P/one"]["evidence"], store.latest()["row:P/one"]

# ---- a row id used again after the import is another row: the import's worktree is no evidence for it
board.drop("gone")
board.set_row("gone", repo="acme/app", title="docs work")
assert "gone" not in [r for r, _, _ in board.annot_plan()[1]] and all(r != "gone" for r, _ in board.annot_plan()[0].values())
# a 🙋 row with no owner named is blocked on the owner
assert board.annot_fields({"status": "owner", "next": "승인"}, {}) == {"owner": "오너", "next": "승인"}
assert board.annot_fields({"status": "owner", "next": ""}, {}) == {"owner": "오너", "next": "오너 확인"}, "the block is not lost with its next"
assert board.annot_fields({"status": "moving", "owner": "오너", "next": ""}, {}) == {"owner": "오너", "next": "오너 확인"}
assert board.annot_fields({"status": "moving", "owner": "me-login", "next": ""}, {}, "me-login") == {"owner": "me-login", "next": "오너 확인"}
assert board.annot_fields({"status": "moving", "owner": "Alice", "next": "승인"}, {}) == {}

print("test_annot: ok")
