"""Offline test: the owner's now-list (`orch board now`) shows in Needs you as the same set the Brief's 📅 list shows.

Run: python3 test_owner_todo_inbox.py
"""
import os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-owner-todo-inbox-"))
env = {**os.environ, "ORCH_FLEET_STATE": str(tmp / "dashboard"), "ORCH_BRIEF_LAST": str(tmp / "brief-last"),
       "ORCH_FLEET_CONFIG": str(tmp / "none.json"), "AGENT_SKILLS_STATE": str(tmp / "state"), "TZ": "UTC",
       "AGENT_SKILLS_CONFIG": str(tmp / "none.json"), "PROGRAMS_HOME": str(tmp / "programs"), "HOME": str(tmp)}
os.environ.update(env)
sys.path.insert(0, str(HERE))
import board, fleet  # noqa: E402
import store as db_store  # noqa: E402

db_store.import_apply(checks=False)


def orch(*args):
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), *args], capture_output=True, text=True, env=env)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout


orch("board", "now", "acme/app#3 리뷰", "토큰 발급 확인")
now = fleet.utcnow()

# Needs you lists every open owner_todo item, under the id the Brief and `orch board done` use.
items = fleet.owner_todo_items(fleet.board_open(), {})
opened = board.read()["owner_todo"]
assert [i["key"] for i in items] == [t["id"] for t in opened] and len(items) == 2, items
assert {i["title"] for i in items} == {"acme/app#3 리뷰", "토큰 발급 확인"}
assert all(i["type"] == "owner_todo" and i["session"] is None and i["source"] == "board" and i["at"] for i in items)
assert fleet.needs_rank(items[0]) == 2

# The Brief's list and Needs you name the same titles.
brief = orch("brief", "render", "--now", fleet.iso(now), "--no-refresh")
assert all(i["title"] in brief for i in items), brief

# A closed item leaves Needs you, as it leaves the Brief.
first = items[0]
orch("board", "done", first["key"])
after = fleet.owner_todo_items(fleet.board_open(), {})
assert [i["key"] for i in after] == [items[1]["key"]], after

# A close the board did not apply yet (tidy's closed map) hides it as well.
assert fleet.owner_todo_items(fleet.board_open(), {items[1]["key"]: {"ev": "done"}}) == []
assert fleet.owner_todo_items({}, {}) == []
print("ok")
