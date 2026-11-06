"""Offline tests for the owner-order table (store.py order_, order_link), `orch order`, and the brief's
`연결 없는 오너 지시` headline line. Temporary state directories, invented data.

Run: python3 test_order.py
"""
import datetime as dt, json, os, pathlib, sqlite3, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-order-"))
env = {**os.environ, "ORCH_FLEET_STATE": str(tmp / "dashboard"), "ORCH_BRIEF_LAST": str(tmp / "brief-last"),
       "ORCH_FLEET_CONFIG": str(tmp / "none.json"), "AGENT_SKILLS_STATE": str(tmp / "state"), "TZ": "UTC",
       "AGENT_SKILLS_CONFIG": str(tmp / "none.json"), "PROGRAMS_HOME": str(tmp / "programs"),
       "HOME": str(tmp / "home"), "ORCA_TERMINAL_HANDLE": "term_root"}
os.environ.update(env)
sys.path.insert(0, str(HERE))
import fleet, store  # noqa: E402

fleet.state_dir().mkdir(parents=True, exist_ok=True)


def orch(*args):
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), *args], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def ok(*args):
    code, out, err = orch(*args)
    assert code == 0, (args, code, out, err)
    return out


def fails(*args):
    code, out, err = orch(*args)
    assert code != 0, (args, out)
    return err


def facts():
    return json.loads(ok("brief", "render", "--json", "--no-refresh"))["headline_facts"]


def backdate(oid, minutes):
    db = sqlite3.connect(str(store.path()))
    db.execute("UPDATE order_ SET created_at=? WHERE id=?",
               (fleet.iso(fleet.utcnow() - dt.timedelta(minutes=minutes)), oid))
    db.commit()
    db.close()


# ---- no DB, then a DB without the step: nothing is created, and the brief says nothing about orders
assert "orders are not available" in fails("order", "add", "x")
store.import_apply(checks=False)
assert "run" in fails("order", "add", "x") and "order setup" in fails("order", "list")
assert not any("오너 지시" in f for f in facts()), facts()

# ---- setup is recorded once
assert ok("order", "setup") == "created order_, order_link"
assert ok("order", "setup") == "already set up"
assert store.ORDER_STEP in store.migrated_steps() and store.ORDER_STEP not in store.NEEDS
assert not any("오너 지시" in f for f in facts()), "an empty table adds no line"

# ---- add: ids count up, kind and flags are kept, text is masked, a bad kind or empty text is refused
assert ok("order", "add", "첫 지시") == "o1"
assert ok("order", "add", "피드백", "--kind", "feedback", "--source", "terminal:abc", "--owner-action") == "o2"
assert "kind is one of" in fails("order", "add", "x", "--kind", "idea")
assert "needs its text" in fails("order", "add", "   ")
o3 = ok("order", "add", "token=ghp_" + "a" * 36 + " 를 써라")
rows = json.loads(ok("order", "list", "--json"))
assert [r["id"] for r in rows] == ["o1", "o2", o3], rows
assert rows[1]["kind"] == "feedback" and rows[1]["owner_action"] is True and rows[1]["source"] == "terminal:abc"
assert rows[0]["by"] == "term_root" and rows[0]["links"] == []
assert "ghp_" not in rows[2]["text"], rows[2]

# ---- the headline waits out the grace, then names the count and the oldest
assert not any("오너 지시" in f for f in facts()), "a fresh order is inside the 10 minute grace"
backdate("o2", 95)
backdate("o1", 12)
line = [f for f in facts() if f.startswith("연결 없는 오너 지시")]
assert len(line) == 1 and line[0].startswith("연결 없는 오너 지시 2 (가장 오래된 o2 1h") and line[0].endswith("`orch order link`)"), line

# ---- link: unit forms are checked, board and decision units must exist, a repeat changes nothing
assert "unit is" in fails("order", "link", "o2", "--unit", "nowhere")
assert "no order o9" in fails("order", "link", "o9", "--unit", "card:wt-1")
assert "no board item b1" in fails("order", "link", "o2", "--unit", "board:b1")
assert "no decision d1" in fails("order", "link", "o2", "--unit", "decision:d1")
assert ok("order", "link", "o2", "--unit", "card:wt-1") == "o2 linked card:wt-1"
assert ok("order", "link", "o2", "--unit", "card:wt-1") == "o2 already linked card:wt-1"
ok("order", "link", "o2", "--unit", "pr:acme/app#7")
line = [f for f in facts() if f.startswith("연결 없는 오너 지시")]
assert line and line[0].startswith("연결 없는 오너 지시 1 (가장 오래된 o1 "), line
ok("board", "todo", "add", "손으로 올린 할 일", "--who", "owner", "--due", "2099-01-01")
bid = json.loads(ok("board", "show"))["todo"][0]["id"]
assert ok("order", "link", "o1", "--unit", f"board:{bid}") == f"o1 linked board:{bid}"
assert not any("오너 지시" in f for f in facts()), facts()

# ---- the link stays when its unit is gone: nothing in order_link points at item
db = sqlite3.connect(str(store.path()))
db.execute("PRAGMA foreign_keys=ON")
db.execute("DELETE FROM item WHERE id=?", (bid,))
db.commit()
assert db.execute("SELECT count(*) FROM order_link WHERE unit=?", (f"board:{bid}",)).fetchone()[0] == 1
db.close()

# ---- done and drop close by hand and are final; a closed order is not counted as loose
backdate(o3, 30)
assert any(f.startswith("연결 없는 오너 지시 1 (가장 오래된 " + o3) for f in facts()), facts()
assert ok("order", "done", o3) == f"{o3} done"
assert ok("order", "drop", o3) == f"{o3} done", "the first close stands"
assert not any("오너 지시" in f for f in facts())
assert ok("order", "drop", "o1") == "o1 dropped"
assert [r["id"] for r in json.loads(ok("order", "list", "--status", "open", "--json"))] == ["o2"]
assert "no order o9" in fails("order", "done", "o9")

# ---- every change left an event in its own transaction
db = sqlite3.connect(str(store.path()))
evs = [(r[0], r[1]) for r in db.execute("SELECT id, ev FROM event WHERE entity='order' ORDER BY seq")]
db.close()
assert ("o1", "add") in evs and ("o2", "link") in evs and ("o1", "dropped") in evs and (o3, "done") in evs, evs
assert evs.count((o3, "done")) == 1 and (o3, "dropped") not in evs, evs

# ---- import: numbered lines under their heading become feedback once; --skip and --dry-run
src = tmp / "list.md"
src.write_text("# 목록\n\n## 가\n1. 첫째 항목: 진행\n2. 머지된 항목: #5 머지\n\n## 나\n1. 다른 곳의 첫째\n")
out = ok("order", "import", str(src), "--dry-run")
assert out.count("would add") == 3, out
assert not any(r["source"] and r["source"].startswith("import:") for r in json.loads(ok("order", "list", "--json")))
out = ok("order", "import", str(src), "--skip", "머지$")
assert out.endswith("2 added, 0 already there"), out
out = ok("order", "import", str(src), "--skip", "머지$")
assert out.endswith("0 added, 2 already there"), out
out = ok("order", "import", str(src))
assert out.endswith("1 added, 2 already there"), out
imported = [r for r in json.loads(ok("order", "list", "--json")) if (r["source"] or "").startswith("import:")]
assert {r["source"].split("@")[0] for r in imported} == {"import:list.md"} and len({r["source"] for r in imported}) == 3
assert all(r["kind"] == "feedback" for r in imported)
# Another directory's list.md with the same section and number is a different list, not a repeat.
(tmp / "b").mkdir()
other = tmp / "b" / "list.md"
other.write_text("## 가\n1. 다른 파일의 첫째\n")
assert ok("order", "import", str(other)).endswith("1 added, 0 already there")
# The same list with a line reworded under its old key is refused, not skipped or overwritten.
other.write_text("## 가\n1. 고쳐 쓴 첫째\n")
assert "other text" in fails("order", "import", str(other))
# A secret in a heading or a line is masked on screen, in the source and in the event.
leak = tmp / "leak.md"
leak.write_text("## token=ghp_" + "b" * 36 + "\n1. 평범한 줄\n")
assert "ghp_" not in ok("order", "import", str(leak), "--dry-run")
ok("order", "import", str(leak))
db = sqlite3.connect(str(store.path()))
assert not db.execute("SELECT 1 FROM order_ WHERE source LIKE '%ghp_%' OR text LIKE '%ghp_%'").fetchone()
assert not db.execute("SELECT 1 FROM event WHERE entity='order' AND after LIKE '%ghp_%'").fetchone()
db.close()
assert any(r["text"] == "나: 다른 곳의 첫째" for r in imported), imported
empty = tmp / "empty.md"
empty.write_text("nothing numbered\n")
assert "no numbered lines" in fails("order", "import", str(empty))

print("test_order: ok")
