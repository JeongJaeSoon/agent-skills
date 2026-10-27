"""Offline tests for `orch stage` (stages.py through prog.py), on invented data.

Run: python3 test_stages.py
"""
import json, os, pathlib, subprocess, sys, tempfile

state = pathlib.Path(tempfile.mkdtemp(prefix="test-stages-"))
env = {**os.environ, "ORCH_FLEET_STATE": str(state)}
PROG = pathlib.Path(__file__).parent / "prog.py"


def orch(*args):
    r = subprocess.run([sys.executable, str(PROG), "stage", *args], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


assert orch("show") == (0, "기록된 단계 없음", "")
assert orch("add", "search", "--title", "검색 개선", "--pr", "acme/web#42")[0] == 0
assert orch("add", "login", "--title", "로그인 개선", "--pr", "acme/web#40")[0] == 0
assert orch("add", "reindex", "--parent", "search", "--title", "인덱스 재구축", "--pr", "acme/ops#88")[0] == 0
assert orch("add", "sort", "--parent", "search", "--title", "정렬 버그", "--pr", "acme/web#45")[0] == 0
assert orch("add", "x", "--parent", "nope", "--title", "고아")[2] == "no row nope to be the parent"
assert orch("add", "x", "--parent", "sort", "--title", "손자")[2] == "sort is a child row; a child hangs off a top-level row or a group's row"

# A cell is written where it was verified; ok without evidence is refused, emoji marks are accepted.
assert orch("set", "search", "--col", "dev", "--mark", "ok")[2].startswith("✅ needs --evidence")
assert orch("set", "search", "--col", "dev", "--mark", "✅", "--evidence", "v1.8.0")[0] == 0
assert orch("set", "search", "--col", "prod", "--mark", "checking", "--evidence", "확인 중", "--by", "QA 리드")[0] == 0
assert orch("set", "reindex", "--col", "prod", "--mark", "na")[0] == 0
assert orch("set", "sort", "--col", "dev", "--mark", "partial", "--evidence", "a|b")[0] == 0
assert orch("set", "login", "--col", "dev", "--mark", "ok", "--evidence", "v1.7.2")[0] == 0
assert orch("set", "login", "--col", "qa", "--mark", "ok", "--evidence", "x")[2].startswith("--col is one of")
assert orch("set", "login", "--col", "dev", "--mark", "maybe")[2].startswith("--mark is one of")
assert orch("set", "ghost", "--col", "dev", "--mark", "na")[2].startswith("no row ghost")
assert orch("set", "login", "--col", "prod", "--mark", "ok", "--evidence", "token=ghp_" + "a1" * 18)[0] == 0
assert "ghp_" not in (state / "stages.json").read_text()
assert orch("env", "--prod", "v1.7.2", "--dev", "v1.8.0")[0] == 0
assert orch("next", "acme/web#45 리뷰 → 머지 (에이전트, 오늘)", "v1.8.0 prod 배포 (사람, 미정)")[0] == 0

# The merge cell comes from the collector's PR list, never from a written cell.
(state / "state.json").write_text(json.dumps({"prs": [
    {"key": "acme/web#42", "state": "MERGED", "merged_at": "2026-10-02T03:00:00Z"},
    {"key": "acme/web#40", "state": "MERGED", "merged_at": "2026-09-28T03:00:00Z"},
    {"key": "acme/ops#88", "state": "MERGED", "merged_at": "2026-10-02T05:00:00Z"},
    {"key": "acme/web#45", "state": "OPEN", "ci": "failure"},
]}))
# --all is the whole table; its stage cells alone pass 78 columns, so show prints it and exits 1.
code, md, err = orch("show", "--md", "--all")
assert code == 1 and err == "a table is wider than 78 columns", (code, err, md)
lines = md.splitlines()
assert lines[0] == "prod: v1.7.2 · dev: v1.8.0" and lines[1].startswith("✅ 완료·확인"), lines[:2]
rows = [l for l in lines if l.startswith("| ") and not l.startswith("| 작업")]
# Only a name or PR is cut, never a stage cell: these names are short, so the table overflows 78 whole, no list under it.
assert rows[:3] == [
    "| 검색 개선 | acme/web#42 | ✅ 10/2 | ✅ v1.8.0 | 🔄 확인 중 (QA 리드) |",
    "| ㄴ 인덱스 재구축 | acme/ops#88 | ✅ 10/2 | – | 해당 없음 |",
    "| ㄴ 정렬 버그 | acme/web#45 | ❌ CI 실패 | ⚠️ a\\|b | – |",
], rows
assert rows[3].startswith("| 로그인 개선 | acme/web#40 | ✅ 9/28 | ✅ v1.7.2 | ✅ token=") and "…[" not in md, md
assert not any(l.startswith("- [") for l in lines), lines
assert lines[-2:] == ["1. acme/web#45 리뷰 → 머지 (에이전트, 오늘)", "2. v1.8.0 prod 배포 (사람, 미정)"], lines[-2:]

code, tg, _ = orch("show", "--telegram")
assert tg.splitlines()[0] == "📋 작업별 (prod: v1.7.2 · dev: v1.8.0)", tg
assert "ㄴ 정렬 버그 (acme/web#45) — 머지 ❌ CI 실패 · dev ⚠️ a|b · prod –" in tg.splitlines(), tg
assert "| " not in tg and "**" not in tg

code, js, _ = orch("show", "--json")
t = json.loads(js)
assert [r["id"] for r in t["rows"]] == ["search", "reindex", "sort", "login"]
assert t["rows"][0]["cells"]["merge"] == {"mark": "ok", "evidence": "10/2", "auto": True}

# done marks a row finished and keeps it a week; drop removes it with its children.
# Without --all, show prints what the brief's line counts: the finished row folds into one line.
code, cur, _ = orch("show")
assert "로그인 개선" not in cur and cur.endswith("접음 1행 (끝남 또는 7일 그대로) · --all 로 전부"), cur
assert orch("done", "login")[0] == 0 and "로그인 개선 (끝남)" in orch("show", "--all")[1]
assert orch("drop", "search")[0] == 0
assert [r["id"] for r in json.loads(orch("show", "--json")[1])["rows"]] == ["login"]
store = json.loads((state / "stages.json").read_text())
store["rows"]["login"]["done_at"] = "2020-01-01T00:00:00Z"
(state / "stages.json").write_text(json.dumps(store))
assert orch("next")[0] == 0  # any write prunes finished rows past the week
assert orch("show") == (0, "기록된 단계 없음", "")
assert orch("bogus")[0] == 1

# A group is a heading with no PR and no cells; under it a work row may still hold children, as at the top.
assert orch("add", "chat", "--group", "--title", "채팅")[0] == 0
assert orch("add", "body", "--parent", "chat", "--title", "본체", "--pr", "acme/web#60")[0] == 0
assert orch("add", "icon", "--parent", "body", "--title", "사이드바 접기 아이콘")[0] == 0
assert orch("add", "ticket", "--parent", "chat", "--title", "티켓 하나")[0] == 0
assert orch("add", "solo", "--title", "단독 작업")[0] == 0
assert orch("add", "x", "--parent", "icon", "--title", "손자")[2].startswith("icon is a child row")
assert orch("add", "g2", "--group", "--title", "그룹", "--pr", "acme/web#1")[2].startswith("a group row (g2)")
assert orch("add", "chat", "--title", "채팅", "--parent", "solo")[2].startswith("a group row (chat)")
assert orch("set", "chat", "--col", "dev", "--mark", "na")[2].startswith("chat is a group row")
assert orch("set", "icon", "--col", "dev", "--mark", "partial", "--evidence", "스텁으로만 확인")[0] == 0
rows = [l for l in orch("show", "--md")[1].splitlines() if l.startswith("| ") and not l.startswith("| 작업")]
assert rows == [
    "| **채팅** |  |  |  |  |",
    "| 본체 | acme/web#60 | – | – | – |",
    "| ㄴ 사이드…[1] | – | – | ⚠️ 스텁으로만 확인 | – |",
    "| 티켓 하나 | – | – | – | – |",
    "| 단독 작업 | – | – | – | – |",
], rows
tg = orch("show", "--telegram")[1].splitlines()
assert tg[1:4] == ["[채팅]", "- 본체 (acme/web#60) — 머지 – · dev – · prod –",
                   "ㄴ 사이드바 접기 아이콘 — 머지 – · dev ⚠️ 스텁으로만 확인 · prod –"], tg
assert [r["id"] for r in json.loads(orch("show", "--program", "chat", "--json")[1])["rows"]] == ["chat", "body", "icon", "ticket"]
assert "단독 작업" not in orch("show", "--program", "chat")[1]
# Dropping or pruning a group takes its rows' children too, not only its direct rows.
assert orch("drop", "chat")[0] == 0
assert [r["id"] for r in json.loads(orch("show", "--json")[1])["rows"]] == ["solo"]

print("test_stages: ok")
