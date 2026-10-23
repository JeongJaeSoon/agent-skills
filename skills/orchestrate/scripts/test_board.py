"""Offline tests for `orch board` and `orch brief render --periodic` (board.py through prog.py), on invented data.

Run: python3 test_board.py [--print]   (--print shows the rendered reports instead of checking them)
"""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE.parents[1] / "brief" / "scripts"))
import table_width  # noqa: E402

sys.path.insert(0, str(HERE))
import board  # noqa: E402

tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-board-"))
state = tmp / "dashboard"
env = {**os.environ, "ORCH_FLEET_STATE": str(state), "ORCH_BRIEF_LAST": str(tmp / "brief-last"),
       "ORCH_FLEET_CONFIG": str(tmp / "none.json"), "TZ": "UTC"}
T0 = "2026-10-13T14:00:00Z"
NOW = "2026-10-13T14:30:00Z"


def orch(*args):
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), *args], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout.rstrip("\n"), r.stderr.strip()


def ok(*args):
    code, out, err = orch(*args)
    assert code == 0, (args, out, err)
    return out


def stamp(at=T0, **older):
    """Every row confirmed at `at`, except the ids given, confirmed at their own time."""
    p = state / "board.json"
    b = json.loads(p.read_text())
    for k, r in b["rows"].items():
        r["reported_at"] = older.get(k, at)
    p.write_text(json.dumps(b, ensure_ascii=False))


assert orch("brief", "render", "--periodic")[1].startswith("board 에 행 없음")

# Refusals say what is missing.
assert orch("board", "set", "x/y", "--title", "고아")[2] == "no row x to be the parent; add it first"
assert orch("board", "set", "lone", "--title", "저장소 없음")[2] == "a lead row (lone) needs --repo"
assert orch("board", "lead", "add", "search", "--handle", "term_0FAKE1", "--repo", "acme/web")[0] == 0
assert orch("board", "set", "search", "--status", "maybe")[2].startswith("--status is one of")
assert board.status_of("⏸️") == board.status_of("⏸") == "held"
assert orch("board", "set", "search", "--progress", "9/8")[2] == "--progress N/M needs 0 <= N <= M and M > 0"
assert orch("board", "set", "search", "--progress", "half")[2].startswith("--progress is N/M")
assert orch("board", "later", "add", "캐시 TTL")[2].startswith("board later add needs")
assert orch("board", "doing", "add", "다른 팀 답변", "--owner", "사람")[2].startswith("board doing add needs")

# Lead 1: works in phases; the phases are its rows, the sub-tasks hang under them, one sub-task in another repo.
ok("board", "set", "search", "--title", "검색 개선", "--program", "검색 개선")
ok("board", "set", "search/ph1", "--kind", "phase", "--pr", "acme/web#40", "--status", "done", "--progress", "8/8")
ok("board", "set", "search/ph2", "--kind", "phase", "--pr", "acme/web#42", "--progress", "6/8", "--next", "CI · 워커")
ok("board", "set", "search/ph3", "--kind", "phase", "--progress", "3/8", "--next", "하위 3건 · 워커")
ok("board", "set", "search/ph3/w2", "--title", "색인", "--progress", "2/8", "--next", "PR 작성 · 워커")
ok("board", "set", "search/ph3/sort", "--title", "정렬", "--pr", "acme/web#45", "--progress", "4/8", "--next", "CI 재실행 · 워커")
ok("board", "set", "search/ph3/reidx", "--title", "재색인", "--repo", "acme/ops", "--pr", "acme/ops#88", "--progress", "2/8",
   "--next", "권한 · 결정 1")
ok("board", "set", "search/ph4", "--kind", "phase", "--title", "ph4 · ph5", "--status", "held", "--progress", "0/8",
   "--next", "ph3 머지 뒤")
# Lead 2: no phases, so the lead is the row. Three PRs wait only on rev-a: one folded row, and the lead row stays.
ok("board", "lead", "add", "notify", "--handle", "term_0FAKE2", "--repo", "acme/web")
# Its PRs go to feature/notify, and the lead's own PR lands that branch on main: the one row naming its own base.
ok("board", "set", "notify", "--title", "알림 정리", "--program", "알림 정리", "--status", "waiting", "--progress", "3/4",
   "--next", "리뷰 · 사람", "--pr", "acme/web#50")
for n, t in (("51", "설정"), ("52", "발송"), ("53", "로그")):
    ok("board", "set", f"notify/p{n}", "--title", t, "--pr", f"acme/web#{n}", "--status", "waiting", "--progress", "3/4",
       "--next", "10/13 09:30 의뢰")
ok("board", "set", "notify/p54", "--title", "문구가 아주 길어서 칸을 넘는 하위 작업", "--pr", "acme/web#54", "--progress", "2/4",
   "--next", "CI 재실행 뒤 리뷰 의뢰 다시 보내기 · 워커")
# Lead 3: two PRs on one reviewer fit the name cell. Lead 4: its terminal is gone and it last reported 3 hours ago.
ok("board", "lead", "add", "triage", "--handle", "term_0FAKE3", "--repo", "acme/ops")
ok("board", "set", "triage", "--title", "분류", "--status", "waiting", "--next", "리뷰 · 사람")
ok("board", "set", "triage/p61", "--pr", "acme/ops#61", "--status", "waiting")
ok("board", "set", "triage/p62", "--pr", "acme/ops#62", "--status", "waiting")
ok("board", "lead", "add", "ghost", "--handle", "term_0FAKE4", "--repo", "acme/ops")
ok("board", "set", "ghost", "--title", "정리 작업", "--progress", "1/3", "--next", "구현 · 워커")
# Two PRs with no lead, waiting on the same person: a bullet under the section's table.
ok("board", "set", "loose-70", "--kind", "task", "--repo", "acme/ops", "--title", "로그", "--pr", "acme/ops#70")
ok("board", "set", "loose-71", "--kind", "task", "--repo", "acme/ops", "--title", "대시보드", "--pr", "acme/ops#71")
# Moving work with no row of its own goes on the 진행 중 line; only work set aside on purpose goes on 보류.
ok("board", "doing", "add", "다른 팀 답변 대기", "--owner", "사람", "--next", "10/14 회신")
ok("board", "doing", "add", "로그 누락 조사", "--owner", "워커", "--next", "원인 보고")
ok("board", "later", "add", "prod 배포 창", "--trigger", "10/16")
ok("board", "later", "add", "캐시 TTL 다시 보기", "--trigger", "10/20")
ok("decide", "add", "--title", "재색인 권한", "--option", "서비스 계정::만료 없음", "--option", "개인 토큰", "--recommend", "1")
stamp(ghost="2026-10-13T11:20:00Z")


def pr(key, state_="OPEN", reviewers=(), ci="success", merged=None, probed="2026-10-13T14:25:00Z", base="main",
       merge_state="BLOCKED", decision="REVIEW_REQUIRED"):
    n = key.rsplit("#", 1)[1]
    return key, {"probed_at": probed, "detail": {
        "key": key, "url": f"https://example.test/{n}", "state": state_, "merged_at": merged, "draft": False, "ci": ci,
        "decision": decision if state_ == "OPEN" else None, "base": base,
        "merge_state": merge_state if state_ == "OPEN" else "UNKNOWN", "merge_sha": merged and f"{int(n):02d}c0ffee0000",
        "reviewers": [{"login": r, "requested": True, "state": None, "team": False} for r in reviewers]}}


(state / "prs.json").write_text(json.dumps({"prs": dict([
    # A merged PR is probed once a day (fleet TIER_EVERY "done"): its old probe neither ages the headline nor gets ⚠️.
    pr("acme/web#40", "MERGED", merged="2026-10-01T03:00:00Z", probed="2026-10-12T17:00:00Z"), pr("acme/web#42", "MERGED", merged="2026-10-13T13:00:00Z"),
    pr("acme/web#45", ci="failure", reviewers=["rev-a"], merge_state="CLEAN", decision=None),
    pr("acme/web#50", merge_state="DIRTY", decision=None),
    *(pr(f"acme/web#{n}", reviewers=["rev-a"], base="feature/notify") for n in (51, 52, 53)),
    pr("acme/web#54", ci="failure", base="feature/notify"), pr("acme/ops#88", probed="2026-10-13T11:00:00Z", ci="pending"),
    pr("acme/ops#61", reviewers=["rev-b"]), pr("acme/ops#62", reviewers=["rev-b"]),
    pr("acme/ops#70", reviewers=["rev-c"]), pr("acme/ops#71", reviewers=["rev-c"]),
])}))
(state / "state.json").write_text(json.dumps({"sessions": [
    {"id": s, "terminals": [{"handle": h}]} for s, h in (("s1", "term_0FAKE1"), ("s2", "term_0FAKE2"), ("s3", "term_0FAKE3"))]}))

FIRST = orch("brief", "render", "--periodic", "--now", NOW, "--commit", "--skipped", "21:00 이후라 Slack 미전송")
ok("board", "set", "search/ph2", "--status", "done", "--progress", "8/8", "--next", "-")
stamp(ghost="2026-10-13T11:20:00Z")
SECOND = orch("brief", "render", "--periodic", "--now", NOW, "--skipped", "21:00 이후라 Slack 미전송")
TG = orch("brief", "render", "--periodic", "--now", NOW, "--telegram")

if "--print" in sys.argv:
    for r in (FIRST, SECOND, TG):
        print(r[0], r[2], r[1], sep="\n", end="\n\n")
    sys.exit()

GOLDEN = """\
**14:30 · 바뀐 행 1 · PR 상태 3h 전 기준**

🔄 진행 · ⏳ 사람 대기 · ⏸️ 보류 · ✅ 완료 · 🔴 막힘 · 막대=단계(추정)

PR: ❗ 사람이 풀 것 · CI✗ CI 실패 · CI… CI 진행 · 7자리=머지 sha

**acme/web — 검색 개선 → main**

| | 리드/Phase | PR | 진행 | 다음 |
|---|---|---|---|---|
| ✅ | ph1 | [#40](https://example.test/40) 40c0ffe | ████████ 8/8 | — |
| ✅ | **ph2** | [#42](https://example.test/42) 42c0ffe | ████████ 8/8 | — |
| 🔄 | ph3 | — | ███░░░░░ 3/8 | 하위 3건 · 워커 |
| 🔄 | └ w2 색인 | — | ██░░░░░░ 2/8 | PR 작성 · 워커 |
| 🔄 | └ 정렬 | [#45](https://example.test/45) CI✗ | ████░░░░ 4/8 | CI 재실행 · 워커 |
| ⏸️ | ph4 · ph5 | — | ░░░░░░░░ 0/8 | ph3 머지 뒤 |

**acme/ops — (프로젝트 미지정) → main**

| | 리드/Phase | PR | 진행 | 다음 |
|---|---|---|---|---|
| — | 검색 개선 | — | — | — |
| 🔄 | └ 재색인 | [#88](https://example.test/88)⚠️ ❗리뷰 CI… | ██░░░░░░ 2/8 | 권한 · 결정 1 |
| ⏳ | 분류 | — | — | 리뷰 · 사람 |
| ⏳ | └ rev-b 대기 | [#61](https://example.test/61)·#62 | — | 리뷰 · rev-b |
| 🔴 | 정리 작업 | — | ███░░░░░ 1/3 | 세션 없음 · 3h 전 |

- ⏳ 2건 rev-c 대기 — 분야 로그·대시보드

**acme/web — 알림 정리 → feature/notify**

| | 리드/Phase | PR | 진행 | 다음 |
|---|---|---|---|---|
| ⏳ | 알림 정리 | [#50](https://example.test/50) ❗DIRTY→main | ██████░░ 3/4 | 리뷰 · 사람 |
| 🔄 | └ 문구가 아주…¹ | [#54](https://example.test/54) ❗리뷰 CI✗ | ████░░░░ 2/4 | CI 재실행 뒤 리…² |
| ⏳ | └ rev-a 대기 | [#51](https://example.test/51)·#52·#53 | ██████░░ 3/4 | 10/13 09:30 의뢰 |

¹ 문구가 아주 길어서 칸을 넘는 하위 작업
² CI 재실행 뒤 리뷰 의뢰 다시 보내기 · 워커

21:00 이후라 Slack 미전송

🔄 **진행 중** — 다른 팀 답변 대기 · 사람 · 다음: 10/14 회신

로그 누락 조사 · 워커 · 다음: 원인 보고

❓ **결정 필요** — 1. (d1) 재색인 권한 → 추천: 서비스 계정 — 만료 없음 (지난 보고와 동일)

🙋 **지금 오너가 할 일** — 없음

⏸️ **보류** — 10/16 → prod 배포 창 · 10/20 → 캐시 TTL 다시 보기"""

code, first, err = FIRST
assert code == 0, err
assert first.splitlines()[0] == "**14:30 · 지난 보고 없음, 굵게 없음 · PR 상태 3h 전 기준**", first.splitlines()[0]
assert "**" not in "\n".join(l for l in first.splitlines() if l.startswith("| ")), "no saved table, so no row is bold"
assert "(지난 보고와 동일)" not in first
code, second, err = SECOND
assert code == 0, err
if second != GOLDEN:
    import difflib
    sys.exit("\n".join(difflib.unified_diff(GOLDEN.splitlines(), second.splitlines(), "golden", "rendered", lineterm="")))
for start, rows in table_width.tables(second):
    assert table_width.table_width(rows) <= 78, (start, table_width.table_width(rows))

# Telegram: no markdown, one line per row, sub-tasks start with └, a changed row says (바뀜).
code, tg, err = TG
assert code == 0, err
assert not any(m in tg for m in ("**", "|", "](")), tg
assert "✅ ph2 #42 42c0ffe (바뀜) — ████████ 8/8 · —" in tg.splitlines(), tg
assert "└ ⏳ #51·#52·#53 rev-a 대기 — ██████░░ 3/4 · 10/13 09:30 의뢰" in tg.splitlines(), tg
assert "└ 🔄 문구가 아주 길어서 칸을 넘는 하위 작업 #54 ❗리뷰 CI✗ — ████░░░░ 2/4 · CI 재실행 뒤 리뷰 의뢰 다시 보내기 · 워커" in tg.splitlines(), tg
assert "📋 acme/web — 알림 정리 → feature/notify" in tg.splitlines() and "⏳ 알림 정리 #50 ❗DIRTY→main — ██████░░ 3/4 · 리뷰 · 사람" in tg.splitlines(), tg
assert tg.splitlines()[0] == "14:30 · 바뀐 행 1 · PR 상태 3h 전 기준"
assert "🔄 진행 중 — 다른 팀 답변 대기 · 사람 · 다음: 10/14 회신" in tg.splitlines(), tg
assert "⏸️ 보류 — 10/16 → prod 배포 창 · 10/20 → 캐시 TTL 다시 보기" in tg.splitlines(), tg

# --json carries what a dashboard or the model needs beside the text.
out = json.loads(ok("brief", "render", "--periodic", "--now", NOW, "--json", "--headline", "ph2 머지"))
# The model's headline replaces the change count only: the staleness facts stay.
assert out["text"].startswith("**14:30 · ph2 머지 · PR 상태 3h 전 기준**"), out["text"].splitlines()[0]
assert out["changed"] == ["search/ph2"], out["changed"]
assert out["stale"] == ["ghost"] and out["width_ok"] is True

# A PR whose state moves bolds its row; a saved table from before PR states were kept bolds nothing for them.
prs_json = json.loads((state / "prs.json").read_text())
prs_json["prs"]["acme/web#45"]["detail"]["merge_state"] = "DIRTY"
(state / "prs.json").write_text(json.dumps(prs_json))
out = json.loads(ok("brief", "render", "--periodic", "--now", NOW, "--json"))
assert out["changed"] == ["search/ph2", "search/ph3/sort"], out["changed"]
assert "| 🔄 | **└ 정렬** | [#45](https://example.test/45) ❗DIRTY CI✗ |" in out["text"], out["text"]
# A folded PR keeps a base other than its section's in the cell, and moving it bolds the row.
prs_json["prs"]["acme/web#52"]["detail"]["base"] = "main"
(state / "prs.json").write_text(json.dumps(prs_json))
out = json.loads(ok("brief", "render", "--periodic", "--now", NOW, "--json"))
assert "notify/~rev-a" in out["changed"] and "[#51](https://example.test/51)·#52→main·#53 |" in out["text"], out["text"]
last = json.loads((tmp / "brief-last" / "top-level.json").read_text())
for r in last["rows"].values():
    r.pop("pr")
(tmp / "brief-last" / "top-level.json").write_text(json.dumps(last))
assert json.loads(ok("brief", "render", "--periodic", "--now", NOW, "--json"))["changed"] == ["search/ph2"]

assert board.pr_state({"state": "OPEN", "merge_state": "BLOCKED", "ci": "success"}) == "BLOCKED"
assert board.pr_state({"state": "MERGED", "merged_at": T0}) == "머지됨"  # read before the merge sha was collected
assert board.pr_state({"state": "OPEN", "draft": True, "merge_state": "BEHIND", "ci": "pending"}) == "초안 CI… BEHIND"
assert board.pr_state({"state": "OPEN", "merge_state": "CLEAN", "ci": "success"}) == "✓"

# A row the board drops leaves the report; the snapshot keys rows by id, a hand-made one is no saved table.
(tmp / "brief-last" / "top-level.json").write_text(json.dumps({"ts": T0, "rows": [{"repo": "acme/web", "name": "ph1"}]}))
assert "지난 보고 없음" in ok("brief", "render", "--periodic", "--now", NOW).splitlines()[0]
ok("board", "drop", "notify")
assert "알림 정리" not in ok("brief", "render", "--periodic", "--now", NOW)
assert "notify/p51" not in json.loads((state / "board.json").read_text())["rows"]
assert (state / "board.prev.json").exists()

# A scope renders one program's rows only.
scoped = ok("brief", "render", "--periodic", "--now", NOW, "--scope", "검색 개선")
assert "분류" not in scoped and "ph1" in scoped

# Two leads in one repository share a table unless each names its project: set warns, the report says which is unnamed.
assert "pay" not in orch("board", "lead", "add", "pay", "--handle", "term_0FAKE5", "--repo", "acme/api", "--program", "결제")[2]
code, _, err = orch("board", "lead", "add", "refund", "--handle", "term_0FAKE6", "--repo", "acme/api")
assert code == 0 and err.startswith("warning: ") and "refund" in err and "triage" in err, err
ok("board", "set", "refund", "--title", "환불")
mixed = ok("brief", "render", "--periodic", "--now", NOW)
assert "**acme/api — 결제**" in mixed and "**acme/api — (프로젝트 미지정)**" in mixed, mixed
code, _, err = orch("board", "set", "refund", "--program", "환불")
assert code == 0 and "refund" not in err, err
split = ok("brief", "render", "--periodic", "--now", NOW)
assert "**acme/api — 결제**" in split and "**acme/api — 환불**" in split and "acme/api — (프로젝트 미지정)" not in split, split
# A lead that works in phases shows only its phase rows; its section is still headed unnamed.
ok("board", "lead", "add", "cart", "--handle", "term_0FAKE7", "--repo", "acme/shop")
ok("board", "lead", "add", "promo", "--handle", "term_0FAKE8", "--repo", "acme/shop")
ok("board", "set", "cart/ph1", "--kind", "phase")
ok("board", "set", "promo/ph1", "--kind", "phase")
assert "**acme/shop — (프로젝트 미지정)**" in ok("brief", "render", "--periodic", "--now", NOW)

# A long progress and a full PR column take from the name: the table stays within 78 columns. A folded row too long
# for its cell keeps each PR's own base in the footnote.
ok("board", "lead", "add", "wide", "--handle", "term_0FAKE9", "--repo", "acme/wide", "--program", "넓은 표")
ok("board", "set", "wide", "--title", "아주 긴 이름의 리드 작업", "--progress", "100/100", "--pr", "acme/wide#1201",
   "--next", "다음 단계 설명이 아주 길게 이어짐")
for n in (1123, 1124, 1125):
    ok("board", "set", f"wide/p{n}", "--pr", f"acme/wide#{n}", "--status", "waiting")
prs_json["prs"].update(dict([pr("acme/wide#1201", merge_state="DIRTY", decision=None, base="release/2026-10"),
                             *(pr(f"acme/wide#{n}", reviewers=["rev-d"], base="feature/long-target" if n == 1124 else "main")
                               for n in (1123, 1124, 1125))]))
(state / "prs.json").write_text(json.dumps(prs_json))
out = json.loads(ok("brief", "render", "--periodic", "--now", NOW, "--json", "--scope", "넓은 표"))
assert out["width_ok"], out["text"]
assert "² #1201 ❗DIRTY→release/2026-10" in out["text"].splitlines(), out["text"]
assert "³ #1123·#1124→feature/long-target·#1125" in out["text"].splitlines(), out["text"]

# A repository whose only rows are lead-less PRs waiting on one reviewer is a bullet with no table.
for n in (81, 82):
    ok("board", "set", f"solo-{n}", "--kind", "task", "--repo", "acme/solo", "--title", f"작업{n}", "--pr", f"acme/solo#{n}")
prs_json["prs"].update(dict(pr(f"acme/solo#{n}", reviewers=["rev-e"]) for n in (81, 82)))
(state / "prs.json").write_text(json.dumps(prs_json))
solo = ok("brief", "render", "--periodic", "--now", NOW)
assert "**acme/solo → main**\n\n- ⏳ 2건 rev-e 대기 — 분야 작업81·작업82" in solo, solo

# --fill: a lead session nobody wrote to the board shows with its cards and their PRs, in the lead's section even when a
# card works in another repo; a lead the board holds (by its terminal) keeps only its board rows; the coordinator's own
# card is a row of its own unless a board row already has its PR; an offline card with no PR is left out.
sess = lambda sid, kind, parent, phase, h=None, **kw: {"id": sid, "kind": kind, "parent": parent, "phase": phase, "name": sid,
                                                      "gh_repo": "acme/fill", "last_activity": "2026-10-13T14:20:00Z",
                                                      "terminals": [{"handle": h or f"term_{sid}"}], **kw}
(state / "state.json").write_text(json.dumps({"sessions": [
    sess("root", "orchestrator", None, "working"),
    sess("lead9", "orchestration", "root", "idle", name="정산 리드"),
    sess("c1", "task", "lead9", "working", task_title="FILL-1 영수증", gh_repo="acme/other"), sess("c2", "standalone", "lead9", "waiting"),
    sess("c3", "task", "lead9", "offline"), sess("c4", "task", "root", "working", last_activity="2026-10-13T11:00:00Z"),
    sess("held", "orchestration", "root", "working", h="term_0FAKE2"), sess("c5", "task", "root", "working")],
    "prs": [{"key": "acme/fill#5", "session": "c1"}, {"key": "acme/web#45", "session": "c5"}]}))
prs_json["prs"].update(dict([pr("acme/fill#5", ci="pending")]))
(state / "prs.json").write_text(json.dumps(prs_json))
assert "정산 리드" not in ok("brief", "render", "--periodic", "--now", NOW)
filled = json.loads(ok("brief", "render", "--periodic", "--now", NOW, "--json", "--fill"))["text"]
fill_rows = [l for l in filled.splitlines() if "acme/fill" in l or "acme/other" in l or "정산" in l or "FILL" in l
             or "c2" in l or "c4" in l]
assert fill_rows == [
    "**acme/fill — 정산 리드 → main**",
    "| ⏳ | 정산 리드 | — | — | 턴 종료 |",
    "| 🔄 | └ FILL-1 영수증 | [#5](https://example.test/5) ❗리뷰 CI… | — | 작업 중 |",
    "| ⏳ | └ c2 | — | — | 확인 대기 · 사람 |",
    "**acme/fill**",
    "| 🔄 | c4 | — | — | 작업 중 · 3h 전 |"], filled
assert "held" not in filled and "c3" not in filled and "c5" not in filled, filled
# The last committed report never had fleet rows (it ran without --fill): they are not bold as new on every poll.
(tmp / "brief-last" / "top-level.json").write_text(json.dumps({"ts": T0, "rows": {}}))
changed = json.loads(ok("brief", "render", "--periodic", "--now", NOW, "--json", "--fill"))["changed"]
assert not [k for k in changed if k.startswith("fleet:")], changed

print("ok")
