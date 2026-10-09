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
assert orch("add", "x", "--parent", "sort", "--title", "손자")[2] == "sort is a child row; a child hangs off a top-level row"

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
code, md, _ = orch("show", "--md")
assert code == 0, md
lines = md.splitlines()
assert lines[0] == "prod: v1.7.2 · dev: v1.8.0" and lines[1].startswith("✅ 완료·확인"), lines[:2]
rows = [l for l in lines if l.startswith("| ") and not l.startswith("| 작업")]
assert rows == [
    "| 검색 개선 | acme/web#42 | ✅ 10/2 | ✅ v1.8.0 | 🔄 확인 중 (QA 리드) |",
    "| ㄴ 인덱스 재구축 | acme/ops#88 | ✅ 10/2 | – | 해당 없음 |",
    "| ㄴ 정렬 버그 | acme/web#45 | ❌ CI 실패 | ⚠️ a\\|b | – |",
    "| 로그인 개선 | acme/web#40 | ✅ 9/28 | ✅ v1.7.2 | ✅ token=[masked] |",
], rows
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
assert orch("done", "login")[0] == 0 and "로그인 개선 (끝남)" in orch("show")[1]
assert orch("drop", "search")[0] == 0
assert [r["id"] for r in json.loads(orch("show", "--json")[1])["rows"]] == ["login"]
store = json.loads((state / "stages.json").read_text())
store["rows"]["login"]["done_at"] = "2020-01-01T00:00:00Z"
(state / "stages.json").write_text(json.dumps(store))
assert orch("next")[0] == 0  # any write prunes finished rows past the week
assert orch("show") == (0, "기록된 단계 없음", "")
assert orch("bogus")[0] == 1

print("test_stages: ok")
