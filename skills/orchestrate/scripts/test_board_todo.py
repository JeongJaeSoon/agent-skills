"""Offline tests for the to-do list (`orch board todo`), the owner's now-list (`orch board now`) and their sections in
`orch brief render`, on invented data.

Run: python3 test_board_todo.py
"""
import datetime as dt, json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-board-todo-"))
state = tmp / "dashboard"
env = {**os.environ, "ORCH_FLEET_STATE": str(state), "ORCH_BRIEF_LAST": str(tmp / "brief-last"),
       "ORCH_FLEET_CONFIG": str(tmp / "none.json"), "AGENT_SKILLS_STATE": str(tmp / "state"), "TZ": "UTC",
       "AGENT_SKILLS_CONFIG": str(tmp / "none.json")}
os.environ.update(env)
sys.path.insert(0, str(HERE))
import board, fleet, tidy  # noqa: E402

BEFORE = "2026-10-27T01:00:00Z"
AFTER = "2026-10-30T09:05:00Z"


def orch(*args):
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), *args], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout.rstrip("\n"), r.stderr.strip()


def ok(*args):
    code, out, err = orch(*args)
    assert code == 0, (args, out, err)
    return out


def store():
    return json.loads((state / "board.json").read_text())


def render(now, *extra):
    return ok("brief", "render", "--now", now, "--no-refresh", *extra)


def section(text, head):
    """The item lines under a bold section head, up to the next blank line after them."""
    lines = text.splitlines()
    i = lines.index(head) + 2
    return lines[i:lines.index("", i) if "" in lines[i:] else len(lines)]


def write_prs(merged5):
    def pr(key, merged=False):
        return key, {"probed_at": BEFORE, "detail": {"key": key, "url": f"https://example.test/{key[-1]}",
                     "state": "MERGED" if merged else "OPEN", "merged_at": "2026-10-29T00:00:00Z" if merged else None,
                     "draft": False, "ci": "success", "decision": None, "base": "main", "merge_state": "CLEAN",
                     "reviewers": []}}
    (state / "prs.json").write_text(json.dumps({"me": "octo-owner", "prs": dict(
        [pr("acme/app#1"), pr("acme/app#2"), pr("acme/app#5", merged5)])}))


# Refusals: a to-do needs a when and a who, and a --due the renderer can read.
assert orch("board", "todo", "add", "배포")[2].startswith("board todo add needs a title, --due D or --after C"), orch("board", "todo", "add", "배포")
assert orch("board", "todo", "add", "배포", "--due", "10/30")[2].startswith("board todo add needs")
assert orch("board", "todo", "add", "배포", "--due", "금요일", "--who", "오너")[2].startswith("--due is")
# `board todo <line>` once replaced the owner's list: now it stops and names the command that does.
ok("board", "now", "acme/app#3 리뷰")
for args in (("board", "todo", "회의 준비"), ("board", "todo")):
    code, _, err = orch(*args)
    assert code == 1 and "orch board now" in err, err
assert store()["owner_todo"] == ["acme/app#3 리뷰"] and store()["todo"] == []

ok("board", "todo", "add", "acme/app#1 머지·배포", "--due", "2026-10-30 09:00", "--who", "오너")
ok("board", "todo", "add", "acme/app#2 ready 로 전환", "--after", "acme/app#5 머지 후", "--who", "lead-billing")
ok("board", "todo", "add", "acme/app#2 ready로 전환", "--after", "acme/app#5 머지 후", "--who", "lead-billing")  # reworded: one item
ok("board", "later", "add", "캐시 TTL 다시 보기", "--trigger", "11월 QA 다음 회차")
ok("board", "later", "add", "릴리스 노트 정리", "--trigger", "v2 출시 뒤", "--due", "2026-11-03")
assert [t["title"] for t in store()["todo"]] == ["acme/app#1 머지·배포", "acme/app#2 ready로 전환"]
# from-later moves one held item, its trigger as --after and its --due kept; nothing is moved without --who.
assert orch("board", "todo", "from-later", "2")[2] == "board todo from-later needs --who W (who does it)"
assert orch("board", "todo", "from-later", "9", "--who", "오너")[2] == "no later item 9; there are 2"
ok("board", "todo", "from-later", "2", "--who", "writer")
s = store()
assert [l["title"] for l in s["later"]] == ["캐시 TTL 다시 보기"]
moved = {k: v for k, v in s["todo"][-1].items() if k != "added"}
assert moved == {"title": "릴리스 노트 정리", "who": "writer", "due": "2026-11-03T00:00:00+00:00", "after": "v2 출시 뒤"}, s["todo"]

state.mkdir(parents=True, exist_ok=True)
(state / "state.json").write_text(json.dumps({"generated_at": BEFORE, "sessions": []}))
write_prs(merged5=False)

# Before anything comes: 📅 할 일 after the owner's list and before 보류, dated items first by date, each with who does it.
out = render(BEFORE)
heads = [l for l in out.splitlines() if l.startswith(("🙋", "📅", "⏸️"))]
assert heads == ["🙋 **지금 오너가 할 일**", "📅 **할 일**", "⏸️ **보류**"], out
assert section(out, "📅 **할 일**") == ["- 10/30(금) 09:00 → acme/app#1 머지·배포 · 오너",
                                         "- 11/3(화) · v2 출시 뒤 → 릴리스 노트 정리 · writer",
                                         "- acme/app#5 머지 후 → acme/app#2 ready로 전환 · lead-billing"], out
assert section(out, "🙋 **지금 오너가 할 일**") == ["- acme/app#3 리뷰"], out
assert "⏰" not in out
tg = render(BEFORE, "--telegram")
assert "10/30(금) 09:00 → acme/app#1 머지·배포 · 오너" in tg.splitlines(), tg
# A to-do naming an open PR holds it in Needs you until it comes, as a held item does.
now = fleet.parse(BEFORE)
assert set(fleet.held_prs(board.read(), {}, {}, now)) == {"acme/app#1", "acme/app#2"}
assert "acme/app#1" in fleet.board_prs()

# 10/30 09:05 with #5 merged: the owner's item goes to their list, lead-billing's to 진행 중; 할 일 keeps the rest.
write_prs(merged5=True)
out = render(AFTER)
assert "- ⏰ acme/app#1 머지·배포 · 10/30(금) 09:00 도래" in section(out, "🙋 **지금 오너가 할 일**"), out
assert "- ⏰ acme/app#2 ready로 전환 — acme/app#5 머지 후 도래 · lead-billing" in section(out, "🔄 **진행 중**"), out
assert section(out, "📅 **할 일**") == ["- 11/3(화) · v2 출시 뒤 → 릴리스 노트 정리 · writer"], out
merged = {"acme/app#5": {"state": "MERGED", "merged_at": "2026-10-29T00:00:00Z"}}
assert set(fleet.held_prs(board.read(), {}, merged, fleet.parse(AFTER))) == set()

# Open items list every to-do with its do: id; Done closes one and nothing closes it on its own.
items = {o["id"]: o for o in json.loads(ok("board", "open", "--json"))}
do = {o["label"]: i for i, o in items.items() if o["kind"] == "do"}
assert len(do) == 3 and all(i.startswith("do:") for i in do.values()), items
assert tidy.item_id("todo", "acme/app#3 리뷰") in items and items[tidy.item_id("todo", "acme/app#3 리뷰")]["kind"] == "todo"
assert all(not r["id"].startswith("do:") for r in tidy.run(dry=True))
first = do["10/30(금) 09:00 → acme/app#1 머지·배포 · 오너"]
ok("board", "done", first)
out = render(AFTER)
assert "acme/app#1 머지·배포" not in out, out
ok("board", "reopen", first)
assert "⏰ acme/app#1 머지·배포" in render(AFTER)
ok("board", "todo", "drop", "1")
assert [t["title"] for t in store()["todo"]] == ["acme/app#2 ready로 전환", "릴리스 노트 정리"]

# A M/D is kept as the moment it named when added: read again in January, last October's dates stay come. The date in
# --after reads against when the to-do was added, and one written before the stamp gets it on the next write.
fleet.utcnow = lambda: dt.datetime(2026, 10, 28, 1, tzinfo=dt.timezone.utc)
board.todo_add("분기 정산", due="10/1", who="writer")
board.todo_add("회고 정리", due="10/29", who="writer")
board.todo_add("감사 대응", after="10/27 이후", who="writer")
assert [t["due"] for t in store()["todo"] if t["title"] in ("분기 정산", "회고 정리")] == ["2026-10-01T00:00:00+00:00",
                                                                                   "2026-10-29T00:00:00+00:00"]
legacy = store()
legacy["todo"].append({"title": "옛 할 일", "who": "writer", "due": "10/1"})
(state / "board.json").write_text(json.dumps(legacy, ensure_ascii=False))
board.change(lambda s: None)
assert store()["todo"][-1]["added"] == "2026-10-28T01:00:00Z", store()["todo"]
JAN = "2027-01-10T00:00:00Z"
out = render(JAN)
doing = "\n".join(section(out, "🔄 **진행 중**"))
assert all(f"⏰ {w}" in doing for w in ("분기 정산", "회고 정리", "감사 대응", "옛 할 일")), out
assert "📅 **할 일**" not in out, out

# --due and --after: whichever comes first. A later --due does not wait out a merged --after, and a passed --due does
# not wait for an --after still open; Needs you lifts the PR hold on the same rule.
board.todo_add("acme/app#1 핫픽스 배포", due="2027-03-01", after="acme/app#5 머지 후", who="writer")
board.todo_add("acme/app#2 롤백 확인", due="10/27", after="acme/app#9 머지 후", who="writer")
out = render(AFTER)
doing = "\n".join(section(out, "🔄 **진행 중**"))
assert "⏰ acme/app#1 핫픽스 배포" in doing and "⏰ acme/app#2 롤백 확인" in doing, out
assert set(fleet.held_prs(board.read(), {}, merged, fleet.parse(AFTER))) == set()
write_prs(merged5=False)
assert "acme/app#1 핫픽스 배포" in "\n".join(section(render(BEFORE), "📅 **할 일**"))

assert orch("board", "todo", "drop", "abc")[2].startswith("board todo is the to-do list now: todo add")

print("ok")
