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
       "AGENT_SKILLS_CONFIG": str(tmp / "none.json"), "PROGRAMS_HOME": str(tmp / "programs")}
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

# Before anything comes the report lists no to-do: one line counts them and says where the list is (the dashboard's
# To-do page; with no server here, the command), and Telegram always gets the commands.
out = render(BEFORE)
assert "📅" not in out and "할 일 3건 · 보류 큐 1건 · `orch board todo list` · `orch req list`" in out.splitlines(), out
assert section(out, "🙋 **지금 오너가 할 일**") == ["- acme/app#3 리뷰"], out
assert "⏰" not in out and "머지·배포" not in out, out
tg = render(BEFORE, "--telegram")
assert "할 일 3건 · 보류 큐 1건 · orch board todo list --telegram · orch req list" in tg.splitlines(), tg
# A held row whose PR a to-do names stays in 보류 under the to-do's date: the list it once folded into is gone.
ok("board", "set", "deploy", "--repo", "acme/app", "--title", "배포 리드", "--status", "held", "--pr", "acme/app#1")
assert "- 10/30(금) 09:00 → 배포 리드" in section(render(BEFORE), "⏸️ **보류**"), render(BEFORE)
ok("board", "drop", "deploy")
# `orch board todo list` has the list the report only counts: a table, ⏰ for one whose time came, 📅 for one waiting,
# in the page's order, and the held queue under it.
listed = ok("board", "todo", "list", "--now", BEFORE, "--no-refresh")
assert listed.splitlines()[:7] == ["**📅 할 일 3건 · ⏰ 도래 0**", "", "| | 언제 | 할 일 | 담당 |", "|---|---|---|---|",
                                   "| 📅 | 10/30(금) 09:00 | acme/app#1 머지·배포 | 오너 |",
                                   "| 📅 | 11/3(화) · v2 출시 뒤 | 릴리스 노트 정리 | writer |",
                                   "| 📅 | acme/app#5 머지 후 | acme/app#2 ready로 전환 | lead-billing |"], listed
assert "| ▶ 1 | 캐시 TTL 다시 보기 | 11월 QA 다음 회차 | — |" in listed, listed
# With the dashboard up, the line links its To-do page; never on Telegram, where 127.0.0.1 is another device.
(tmp / "programs").mkdir()
(tmp / "programs" / ".dash.json").write_text(json.dumps({"pid": os.getpid(), "port": 4999}))
assert "할 일 3건 · 보류 큐 1건 · http://127.0.0.1:4999/#/fleet/todo" in render(BEFORE).splitlines()
assert "할 일 3건 · 보류 큐 1건 · orch board todo list --telegram · orch req list" in render(BEFORE, "--telegram").splitlines()
(tmp / "programs" / ".dash.json").unlink()
# The page reads each to-do's parts from the render's open items: dated ones carry their first date as `at`.
rendered = json.loads(render(BEFORE, "--json"))
page = sorted(({k: o[k] for k in ("title", "who", "when", "after", "at", "due")} for o in rendered["open"] if o["kind"] == "do"),
              key=lambda o: o["title"])
assert page == [
    {"title": "acme/app#1 머지·배포", "who": "오너", "when": "10/30(금) 09:00", "after": None, "at": "2026-10-30T09:00:00Z", "due": False},
    {"title": "acme/app#2 ready로 전환", "who": "lead-billing", "when": "acme/app#5 머지 후", "after": "acme/app#5 머지 후", "at": None, "due": False},
    {"title": "릴리스 노트 정리", "who": "writer", "when": "11/3(화) · v2 출시 뒤", "after": "v2 출시 뒤", "at": "2026-11-03T00:00:00Z", "due": False}], page
assert "할 일" not in rendered["headline_facts"][0], rendered["headline_facts"]
# A to-do naming an open PR holds it in Needs you until it comes, as a held item does, its date as the report says it.
now = fleet.parse(BEFORE)
assert fleet.held_prs(board.read(), {}, {}, now) == {"acme/app#1": "10/30(금) 09:00", "acme/app#2": "acme/app#5 머지 후"}
assert "acme/app#1" in fleet.board_prs()

# 10/30 09:05 with #5 merged: the owner's item goes to their list, lead-billing's to 진행 중; 할 일 keeps the rest.
write_prs(merged5=True)
out = render(AFTER)
assert "- ⏰ acme/app#1 머지·배포 · 10/30(금) 09:00 도래" in section(out, "🙋 **지금 오너가 할 일**"), out
assert "- ⏰ acme/app#2 ready로 전환 — acme/app#5 머지 후 도래 · lead-billing" in section(out, "🔄 **진행 중**"), out
assert "할 일 1건 · 보류 큐 1건 · `orch board todo list` · `orch req list`" in out.splitlines(), out
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
assert not any(l.startswith("할 일 ") for l in out.splitlines()), out

# --due and --after: whichever comes first. A later --due does not wait out a merged --after, and a passed --due does
# not wait for an --after still open; Needs you lifts the PR hold on the same rule.
board.todo_add("acme/app#1 핫픽스 배포", due="2027-03-01", after="acme/app#5 머지 후", who="writer")
board.todo_add("acme/app#2 롤백 확인", due="10/27", after="acme/app#9 머지 후", who="writer")
out = render(AFTER)
doing = "\n".join(section(out, "🔄 **진행 중**"))
assert "⏰ acme/app#1 핫픽스 배포" in doing and "⏰ acme/app#2 롤백 확인" in doing, out
assert set(fleet.held_prs(board.read(), {}, merged, fleet.parse(AFTER))) == set()
write_prs(merged5=False)
out = render(BEFORE)
assert "acme/app#1 핫픽스 배포" not in out and any(l.startswith("할 일 ") for l in out.splitlines()), out

# --after naming only a PR, no merge word: the report's ⏰ and the Needs you hold read one rule (fleet.todo_come), so
# the to-do comes the moment the hold lifts, and not before.
board.todo_add("acme/app#2 배포 노트", after="acme/app#5", who="writer")
t = store()["todo"][-1]
assert not fleet.todo_come(t, fleet.parse(BEFORE), {}) and "acme/app#2" in fleet.held_prs(board.read(), {}, {}, fleet.parse(BEFORE))
assert fleet.todo_come(t, fleet.parse(BEFORE), merged)
write_prs(merged5=True)
assert "- ⏰ acme/app#2 배포 노트 — acme/app#5 도래 · writer" in section(render(BEFORE), "🔄 **진행 중**"), render(BEFORE)
assert "acme/app#2" not in fleet.held_prs(board.read(), {}, merged, fleet.parse(BEFORE))
# A card it names stands for the card's PR, in both.
cards = {"c1": {"id": "c1", "name": "deploy-card", "pr": "acme/app#5"}}
assert fleet.todo_come({"title": "x", "who": "w", "after": "deploy-card 끝나면"}, fleet.parse(BEFORE), merged, cards)
assert not fleet.todo_come({"title": "x", "who": "w", "after": "deploy-card 끝나면"}, fleet.parse(BEFORE), {}, cards)

# A long name is cut to fit 78 columns as the report's tables are, its whole text under the table.
board.todo_add("아주 긴 할 일 이름이 표 폭을 넘겨서 잘려야 하는 경우를 확인하기 위한 문장 — 끝까지 읽혀야 한다", due="2027-05-01", who="writer")
listed = ok("board", "todo", "list", "--now", BEFORE, "--no-refresh")
assert "아주 긴 할 일 이…[2]" in listed and "- [2] 아주 긴 할 일 이름이 표 폭을 넘겨서 잘려야 하는 경우를 확인하기 위한 문장 — 끝까지 읽혀야 한다" in listed, listed
sys.path.insert(0, str(HERE.parents[1] / "brief" / "scripts"))
import table_width  # noqa: E402
assert all(table_width.text_width(l) <= 78 for l in listed.splitlines() if l.startswith("|")), listed

assert orch("board", "todo", "drop", "abc")[2].startswith("board todo is the to-do list now: todo add")

# An --after that only repeats the --due's date (with or without its weekday) says it once; other words stay.
base = {"due": "2026-10-30T09:00:00+00:00", "added": "2026-10-27T00:00:00Z"}
assert fleet.todo_parts({**base, "after": "금 10/30 09:00"}, now) == ("10/30(금) 09:00", None)
assert fleet.todo_when({**base, "after": "목요일 10/30 09:00"}, now) == "10/30(금) 09:00"
assert fleet.todo_parts({**base, "after": "10/30 09:00 이후 검증"}, now) == ("10/30(금) 09:00", "이후 검증")
assert fleet.todo_parts({**base, "after": "10/31 09:00"}, now) == ("10/30(금) 09:00", "10/31 09:00")
board.todo_add("노트 리뷰", due="2027-06-01 09:00", after="화 6/1 09:00", who="writer")
page = {o["title"]: o for o in json.loads(ok("board", "todo", "list", "--now", BEFORE, "--no-refresh", "--json")) if o["kind"] == "do"}
assert {k: page["노트 리뷰"][k] for k in ("when", "date", "cond")} == {"when": "6/1(화) 09:00", "date": "6/1(화) 09:00", "cond": None}, page["노트 리뷰"]
assert page["acme/app#2 배포 노트"]["date"] is None and page["acme/app#2 배포 노트"]["cond"] == "acme/app#5", page

# The list counts as the report's pointer line does: its captions leave out the come to-dos and the held items whose
# trigger tidy found met, which stay in the table, ❔ on their number. The numbers are the queue's from 1 with closed
# items left out, and they are what later move, drop and todo from-later take.
ok("board", "later", "add", "acme/app#2 후속 정리", "--trigger", "acme/app#5 머지 뒤")
ok("board", "later", "add", "문서 이미지 교체", "--trigger", "디자인 확정 뒤")
listed = ok("board", "todo", "list", "--now", BEFORE, "--no-refresh")
due_n = sum(o["due"] for o in page.values())
assert f"**📅 할 일 {len(page) - due_n}건 · ⏰ 도래 {due_n}**" in listed.splitlines(), listed
assert "**⏸️ 보류 큐 2건 · ▶ 다음 차례 · ❔ 확인 필요 1**" in listed.splitlines() and "| ❔2 | acme/app#2 후속 정리 |" in listed, listed
assert f"할 일 {len(page) - due_n}건 · 보류 큐 2건 · `orch board todo list` · `orch req list`" in render(BEFORE).splitlines()
held = {o["title"]: o["id"] for o in json.loads(ok("board", "open", "--json")) if o["kind"] == "later"}
ok("board", "done", held["캐시 TTL 다시 보기"])
listed = ok("board", "todo", "list", "--now", BEFORE, "--no-refresh")
assert "| ▶ ❔1 | acme/app#2 후속 정리 |" in listed and "| 2 | 문서 이미지 교체 |" in listed, listed
ok("board", "later", "move", "2", "--to", "1")
listed = ok("board", "todo", "list", "--now", BEFORE, "--no-refresh")
assert "| ▶ 1 | 문서 이미지 교체 |" in listed and "| ❔2 | acme/app#2 후속 정리 |" in listed, listed
assert orch("board", "later", "move", "3", "--to", "1")[2] == "no later item 3; there are 2"
ok("board", "later", "add", "릴리스 공지", "--trigger", "v3 뒤", "--at", "2")
assert [l["title"] for l in store()["later"]] == ["캐시 TTL 다시 보기", "문서 이미지 교체", "릴리스 공지", "acme/app#2 후속 정리"], store()
ok("board", "later", "drop", "3")
assert [l["title"] for l in store()["later"]] == ["캐시 TTL 다시 보기", "문서 이미지 교체", "릴리스 공지"], store()

print("ok")
