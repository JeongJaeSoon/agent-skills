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
       "ORCH_FLEET_CONFIG": str(tmp / "none.json"), "AGENT_SKILLS_STATE": str(tmp / "state"), "TZ": "UTC",
       "AGENT_SKILLS_CONFIG": str(tmp / "none.json")}
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
    """Every row confirmed at `at`, except the ids given, confirmed at their own time; a wait started then too."""
    p = state / "board.json"
    b = json.loads(p.read_text())
    for k, r in b["rows"].items():
        r["reported_at"] = older.get(k, at)
        if "since" in r:
            r["since"] = r["reported_at"]
    for d in b["doing"]:
        if "since" in d:
            d["since"] = at
    p.write_text(json.dumps(b, ensure_ascii=False))


def orch_env(extra, *args):
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), *args], capture_output=True, text=True, env={**env, **extra})
    return r.returncode, r.stdout.rstrip("\n"), r.stderr.strip()


# Nothing to show is still a report: the headline says so and 오너가 할 일 is there, so no report is made by hand.
assert ok("brief", "render", "--periodic", "--now", NOW) == "**14:30 · 리드·카드 행 없음**\n\n🙋 **지금 오너가 할 일** — 없음"
# The time is the system clock's, in the session's time zone; the model never writes it.
for tz in ("UTC", "Asia/Tokyo"):
    import datetime as dt, time
    before = dt.datetime.now().astimezone(__import__("zoneinfo").ZoneInfo(tz))
    code, out, _ = orch_env({"TZ": tz}, "brief", "render", "--no-refresh")
    after = dt.datetime.now().astimezone(__import__("zoneinfo").ZoneInfo(tz))
    assert code == 0 and out.splitlines()[0] in (f"**{before:%H:%M} · 리드·카드 행 없음**", f"**{after:%H:%M} · 리드·카드 행 없음**"), out
# With no state.json, or one older than state_min, the render collects the fleet itself; when it cannot (no orca on
# PATH), it renders from the old files and the headline says how old they are.
code, out, _ = orch_env({"PATH": str(tmp / "no-bin")}, "brief", "render")
assert code == 0 and out.splitlines()[0].endswith(" · 리드·카드 행 없음 · 세션 목록 없음 · 갱신 실패: orca not found**"), out
state.mkdir(parents=True, exist_ok=True)
(state / "state.json").write_text(json.dumps({"generated_at": "2000-01-01T00:00:00Z", "sessions": []}))
code, out, _ = orch_env({"PATH": str(tmp / "no-bin")}, "brief", "render")
assert code == 0 and "· 세션 목록 " in out and "일 전 기준 · 갱신 실패: orca not found**" in out.splitlines()[0], out
(state / "state.json").unlink()
# The model's decisions keep the form: a question with its recommendation, numbered after the registered ones.
code, _, err = orch("brief", "render", "--now", NOW, "--decision", "캐시를 지울까")
assert code == 1 and err.startswith("--decision is one line: <question>"), err

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
# The lead row waits on the owner (--owner 오너): it goes to 지금 오너가 할 일, the rev-a fold to 다른 사람 대기.
ok("board", "set", "notify", "--title", "알림 정리", "--program", "알림 정리", "--status", "waiting", "--progress", "3/4",
   "--next", "충돌 해소 승인", "--owner", "오너", "--pr", "acme/web#50")
for n, t in (("51", "설정"), ("52", "발송"), ("53", "로그")):
    ok("board", "set", f"notify/p{n}", "--title", t, "--pr", f"acme/web#{n}", "--status", "waiting", "--progress", "3/4",
       "--next", "10/13 09:30 의뢰")
ok("board", "set", "notify/p54", "--title", "문구 다듬기와 길이 정리", "--pr", "acme/web#54", "--progress", "2/4",
   "--next", "CI 재실행 뒤 리뷰 의뢰 다시 보내기 · 워커")
# Lead 3: two PRs on one reviewer fit the name cell; the lead row waits on a person nobody named, so it is 확인 필요.
# Lead 4: its terminal is gone and it last reported 3 hours ago.
ok("board", "lead", "add", "triage", "--handle", "term_0FAKE3", "--repo", "acme/ops")
ok("board", "set", "triage", "--title", "분류", "--status", "waiting", "--next", "리뷰 · 사람")
ok("board", "set", "triage/p61", "--pr", "acme/ops#61", "--status", "waiting")
ok("board", "set", "triage/p62", "--pr", "acme/ops#62", "--status", "waiting")
ok("board", "lead", "add", "ghost", "--handle", "term_0FAKE4", "--repo", "acme/ops")
ok("board", "set", "ghost", "--title", "정리 작업", "--progress", "1/3", "--next", "구현 · 워커")
# Two PRs with no lead, both asking the owner for review: a bullet under the section's table, and one owner item.
ok("board", "set", "loose-70", "--kind", "task", "--repo", "acme/ops", "--title", "로그", "--pr", "acme/ops#70")
ok("board", "set", "loose-71", "--kind", "task", "--repo", "acme/ops", "--title", "대시보드", "--pr", "acme/ops#71")
# Moving work with no row of its own goes on the 진행 중 list after the rows' next steps; a wait on another person's
# reply (--waiting) goes on 다른 사람 대기; only work set aside on purpose goes on 보류, after the held rows'
# conditions. The same held item added twice in other words stays one item.
ok("board", "doing", "add", "다른 팀 답변 대기", "--owner", "다른 팀", "--next", "10/14 리마인드", "--waiting")
ok("board", "doing", "add", "로그 누락 조사", "--owner", "워커", "--next", "원인 보고")
ok("board", "later", "add", "prod 배포 창", "--trigger", "10/16")
ok("board", "later", "add", "캐시 TTL 다시 보기", "--trigger", "10/20")
ok("board", "later", "add", "캐시 TTL을 다시 보기", "--trigger", "10/20")
assert [l["title"] for l in json.loads((state / "board.json").read_text())["later"]] == ["prod 배포 창", "캐시 TTL을 다시 보기"]
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


(state / "prs.json").write_text(json.dumps({"me": "octo-owner", "prs": dict([
    # A merged PR is probed once a day (fleet TIER_EVERY "done"): its old probe neither ages the headline nor gets ⚠️.
    pr("acme/web#40", "MERGED", merged="2026-10-01T03:00:00Z", probed="2026-10-12T17:00:00Z"), pr("acme/web#42", "MERGED", merged="2026-10-13T13:00:00Z"),
    pr("acme/web#45", ci="failure", reviewers=["rev-a"], merge_state="CLEAN", decision=None),
    pr("acme/web#50", merge_state="DIRTY", decision=None),
    *(pr(f"acme/web#{n}", reviewers=["rev-a"], base="feature/notify") for n in (51, 52, 53)),
    pr("acme/web#54", ci="failure", base="feature/notify"), pr("acme/ops#88", probed="2026-10-13T11:00:00Z", ci="pending"),
    pr("acme/ops#61", reviewers=["rev-b"]), pr("acme/ops#62", reviewers=["rev-b"]),
    pr("acme/ops#70", reviewers=["octo-owner"]), pr("acme/ops#71", reviewers=["octo-owner"]),
])}))
(state / "state.json").write_text(json.dumps({"sessions": [
    {"id": s, "terminals": [{"handle": h}]} for s, h in (("s1", "term_0FAKE1"), ("s2", "term_0FAKE2"), ("s3", "term_0FAKE3"))]}))

# ph1 merged 12 days before NOW: its ✅ row folds into the section's count; ph2, merged an hour ago, stays.
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
**14:30 · 바뀐 행 1 · PR 상태 3h 전 기준 · 오래된 board 행 1 (3h 전) · 접음 완료 1 (대시보드)**

🔄 진행 · 🙋 오너 차례 · ⏳ 다른 사람 대기 · ⏸️ 보류 · ✅ 완료 · 🔴 막힘 · 막대=단계(추정)

PR: ❗ 사람이 풀 것 · CI✗ CI 실패 · CI… CI 진행 · 7자리=머지 sha

**acme/web — 검색 개선 → main**

| | 리드/Phase | PR | 진행 |
|---|---|---|---|
| ✅ | **ph2** | [#42](https://example.test/42) 42c0ffe | ████████ 8/8 |
| 🔄 | ph3 | — | ███░░░░░ 3/8 |
| 🔄 | └ w2 색인 | — | ██░░░░░░ 2/8 |
| 🔄 | └ 정렬 | [#45](https://example.test/45) CI✗ | ████░░░░ 4/8 |
| ⏸️ | ph4 · ph5 | — | ░░░░░░░░ 0/8 |

✅ 그 전 완료 1건 (대시보드)

**acme/ops — (프로젝트 미지정) → main**

| | 리드/Phase | PR | 진행 |
|---|---|---|---|
| — | 검색 개선 | — | — |
| 🔄 | └ 재색인 | [#88](https://example.test/88)⚠️ ❗리뷰 CI… | ██░░░░░░ 2/8 |
| ⏳ | 분류 | — | — |
| ⏳ | └ rev-b 대기 | [#61](https://example.test/61)·#62 | — |
| 🔴 | 정리 작업 | — | ███░░░░░ 1/3 |

- 🙋 2건 오너 리뷰 — 분야 로그·대시보드

**acme/web — 알림 정리 → feature/notify**

| | 리드/Phase | PR | 진행 |
|---|---|---|---|
| 🙋 | 알림 정리 | [#50](https://example.test/50) ❗DIRTY→main | ██████░░ 3/4 |
| 🔄 | └ 문구 다듬기와 길이 정리 | [#54](https://example.test/54) ❗리뷰 CI✗ | ████░░░░ 2/4 |
| ⏳ | └ rev-a 대기 | [#51](https://example.test/51)·#52·#53 | ██████░░ 3/4 |

21:00 이후라 Slack 미전송

🔄 **진행 중**

- 🔄 검색 개선 ph3 — 하위 3건 · 워커
- └ 🔄 w2 색인 — PR 작성 · 워커
- └ 🔄 정렬 — CI 재실행 · 워커
- 검색 개선
- └ 🔄 재색인 — 권한 · 결정 1
- ❔ 분류 — 확인 필요: 누구 대기인지 기록 없음
- 🔴 정리 작업 — 세션 없음 · 3h 전
- 알림 정리
- └ 🔄 문구 다듬기와 길이 정리 — CI 재실행 뒤 리뷰 의뢰 다시 보내기 · 워커
- 🔄 로그 누락 조사 — 원인 보고 · 워커

⏳ **다른 사람 대기**

- rev-b · 리뷰 [#61](https://example.test/61)·#62 · 30분 전부터 → p61·p62
- rev-a · 리뷰 [#51](https://example.test/51)·#52·#53 · 30분 전부터 → 설정·발송·로그 · 다음: 10/13 09:30 의뢰
- 다른 팀 · 30분 전부터 → 다른 팀 답변 대기 · 다음: 10/14 리마인드

❓ **결정 필요** — 1. (d1) 재색인 권한 → 추천: 서비스 계정 — 만료 없음 (지난 보고와 동일)

🙋 **지금 오너가 할 일**

- 충돌 해소 승인 · [#50](https://example.test/50) → 알림 정리
- 리뷰 · #70·#71 → 로그·대시보드

⏸️ **보류**

- ph3 머지 뒤 → 검색 개선 ph4 · ph5
- 10/16 → prod 배포 창
- 10/20 → 캐시 TTL을 다시 보기"""

code, first, err = FIRST
assert code == 0, err
assert first.splitlines()[0] == "**14:30 · 지난 보고 없음, 굵게 없음 · PR 상태 3h 전 기준 · 오래된 board 행 1 (3h 전) · 접음 완료 1 (대시보드)**", first.splitlines()[0]
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
assert "✅ ph2 #42 42c0ffe (바뀜) — ████████ 8/8" in tg.splitlines(), tg
assert "└ ⏳ #51·#52·#53 rev-a 대기 — ██████░░ 3/4" in tg.splitlines(), tg
assert "└ 🔄 문구 다듬기와 길이 정리 — CI 재실행 뒤 리뷰 의뢰 다시 보내기 · 워커" in tg.splitlines(), tg
assert "📋 acme/web — 알림 정리 → feature/notify" in tg.splitlines() and "🙋 알림 정리 #50 ❗DIRTY→main — ██████░░ 3/4" in tg.splitlines(), tg
assert tg.splitlines()[0] == "14:30 · 바뀐 행 1 · PR 상태 3h 전 기준 · 오래된 board 행 1 (3h 전) · 접음 완료 1 (대시보드)"
assert "⏳ 다른 사람 대기\n\nrev-b · 리뷰 #61·#62 · 30분 전부터 → p61·p62\n" in tg, tg

# With the brief's send-log key, a ⏳ item says what last went out to its reviewer, when and where; rows that did not
# go out (refused, skipped) and other reviewers' rows do not count. Without the key or the log, nothing is added.
sendlog = tmp / "sendlog.jsonl"
t = board.fleet.parse(NOW).timestamp()
sendlog.write_text("\n".join(json.dumps(r) for r in (
    {"pr": "acme/ops#61", "reviewer": "rev-b", "action": "request", "source": "sent", "at": t - 3 * 3600, "channel": "C0FAKE"},
    {"pr": "acme/ops#62", "reviewer": "rev-b", "action": "remind", "source": "sent", "at": t - 3600, "channel": "C0FAKE",
     "thread": "C0FAKE:1.2"},
    {"pr": "acme/ops#62", "reviewer": "rev-b", "action": "remind", "source": "refused", "at": t - 60},
    {"pr": "acme/web#51", "reviewer": "rev-a", "date": "2026-10-13T12:30:00Z"},
    {"pr": "acme/web#52", "reviewer": "rev-z", "action": "request", "source": "sent", "at": t - 60},
    "not json")) + "\n")
skills_cfg = tmp / "agent-skills.json"
skills_cfg.write_text(json.dumps({"brief": {"sendlog": str(sendlog)}}))
code, asked, err = orch_env({"AGENT_SKILLS_CONFIG": str(skills_cfg)}, "brief", "render", "--periodic", "--now", NOW)
assert code == 0, err
assert "- rev-b · 리뷰 [#61](https://example.test/61)·#62 · 30분 전부터 · 마지막 리마인드 rev-b 1h 전 (C0FAKE 스레드) → p61·p62" \
    in asked.splitlines(), asked
assert ("- rev-a · 리뷰 [#51](https://example.test/51)·#52·#53 · 30분 전부터 · 마지막 의뢰 rev-a 2h 전 → 설정·발송·로그"
        " · 다음: 10/13 09:30 의뢰") in asked.splitlines(), asked
assert "- 다른 팀 · 30분 전부터 → 다른 팀 답변 대기 · 다음: 10/14 리마인드" in asked.splitlines(), asked
# A request scheduled for later shows its post time, not an age.
with sendlog.open("a") as f:
    f.write(json.dumps({"pr": "acme/ops#61", "reviewer": "rev-b", "action": "remind", "source": "scheduled",
                        "at": t - 60, "post_at": t + 18 * 3600, "channel": "C0FAKE"}) + "\n")
code, asked, err = orch_env({"AGENT_SKILLS_CONFIG": str(skills_cfg)}, "brief", "render", "--periodic", "--now", NOW)
assert code == 0 and "· 마지막 리마인드 rev-b 10-14 08:30 예약 (C0FAKE) → p61·p62" in asked, asked
sendlog.unlink()
assert orch_env({"AGENT_SKILLS_CONFIG": str(skills_cfg)}, "brief", "render", "--periodic", "--now", NOW)[1] == \
    orch("brief", "render", "--periodic", "--now", NOW)[1], "a missing log adds nothing"
assert "🙋 지금 오너가 할 일\n\n충돌 해소 승인 · #50 → 알림 정리\n리뷰 · #70·#71 → 로그·대시보드\n" in tg, tg
assert tg.endswith("⏸️ 보류\n\nph3 머지 뒤 → 검색 개선 ph4 · ph5\n10/16 → prod 배포 창\n10/20 → 캐시 TTL을 다시 보기"), tg

# --json carries what a dashboard or the model needs beside the text.
out = json.loads(ok("brief", "render", "--periodic", "--now", NOW, "--json", "--headline", "ph2 머지"))
# The model's headline replaces the change count only: the staleness facts stay.
assert out["text"].startswith("**14:30 · ph2 머지 · PR 상태 3h 전 기준 · 오래된 board 행 1 (3h 전) · 접음 완료 1 (대시보드)**"), out["text"].splitlines()[0]
assert out["changed"] == ["search/ph2"], out["changed"]
assert out["stale"] == ["ghost"] and out["width_ok"] is True

# A PR whose state moves bolds its row; a saved table from before PR states were kept bolds nothing for them.
prs_json = json.loads((state / "prs.json").read_text())
prs_json["prs"]["acme/web#45"]["detail"]["merge_state"] = "DIRTY"
(state / "prs.json").write_text(json.dumps(prs_json))
out = json.loads(ok("brief", "render", "--periodic", "--now", NOW, "--json"))
assert out["changed"] == ["search/ph2", "search/ph3/sort"], out["changed"]
assert "| 🔄 | └ **정렬** | [#45](https://example.test/45) ❗DIRTY CI✗ |" in out["text"], out["text"]
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
assert "분류" not in scoped and "ph2" in scoped

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

# No cell is cut and nothing goes to a footnote: a long PR state, a folded row's bases and a long progress show whole, and
# a table that then passes 78 columns overflows and the render reports it.
ok("board", "lead", "add", "wide", "--handle", "term_0FAKE9", "--repo", "acme/wide", "--program", "넓은 표")
ok("board", "set", "wide", "--title", "긴 이름의 리드 작업", "--progress", "100/100", "--pr", "acme/wide#1201",
   "--next", "다음 단계 설명이 아주 길게 이어짐")
for n in (1123, 1124, 1125):
    ok("board", "set", f"wide/p{n}", "--pr", f"acme/wide#{n}", "--status", "waiting")
prs_json["prs"].update(dict([pr("acme/wide#1201", merge_state="DIRTY", decision=None, base="release/2026-10"),
                             *(pr(f"acme/wide#{n}", reviewers=["rev-d"], base="feature/long-target" if n == 1124 else "main")
                               for n in (1123, 1124, 1125))]))
(state / "prs.json").write_text(json.dumps(prs_json))
code, wide, err = orch("brief", "render", "--periodic", "--now", NOW, "--json", "--scope", "넓은 표")
wide = json.loads(wide)
assert code == 1 and wide["width_ok"] is False and err == "a table is wider than 78 columns", (code, err, wide)
assert "| 🔴 | 긴 이름의 리드 작업 | [#1201](https://example.test/1201) ❗DIRTY→release/2026-10 | ████████ 100/100 |" \
    in wide["text"].splitlines(), wide["text"]
assert "| ⏳ | └ rev-d 대기 | [#1123](https://example.test/1123)·#1124→feature/long-target·#1125 | — |" \
    in wide["text"].splitlines(), wide["text"]
assert "- 🔴 긴 이름의 리드 작업 — 세션 없음" in wide["text"].splitlines(), wide["text"]
assert not any(m in wide["text"] for m in "¹²³…"), wide["text"]
ok("board", "drop", "wide")

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
assert "정산 리드" not in ok("brief", "render", "--periodic", "--now", NOW, "--no-fill")
filled = json.loads(ok("brief", "render", "--periodic", "--now", NOW, "--json"))["text"]
fill_rows = [l for l in filled.splitlines() if "acme/fill" in l or "acme/other" in l or "정산" in l or "FILL" in l
             or "c2" in l or "c4" in l]
assert fill_rows == [
    "**acme/fill — 정산 리드 → main**",
    "| 🙋 | 정산 리드 | — | — |",
    "| 🔄 | └ FILL-1 영수증 | [#5](https://example.test/5) ❗리뷰 CI… | — |",
    "| 🙋 | └ c2 | — | — |",
    "**acme/fill**",
    "| 🔄 | c4 | — | — |",
    "- 🔄 정산 리드 — 턴 종료",
    "- └ 🔄 FILL-1 영수증 — 작업 중",
    "- 🔄 c4 — 작업 중 · 3h 전",
    "- 확인 대기 → c2"], filled
assert "held" not in filled and "c3" not in filled and "c5" not in filled, filled
# The last committed report never had fleet rows (it ran without --fill): they are not bold as new on every poll.
(tmp / "brief-last" / "top-level.json").write_text(json.dumps({"ts": T0, "rows": {}}))
changed = json.loads(ok("brief", "render", "--periodic", "--now", NOW, "--json", "--fill"))["changed"]
assert not [k for k in changed if k.startswith("fleet:")], changed

# A card's report file: the brief (the session's Orca comment) names it on its REPORT line, or says "this note"; the
# newest milestone's 다음/Next/次 line, else its first line, is the row's next step, and its last PR link the row's PR.
import fleet  # noqa: E402
briefs = tmp / "briefs"
briefs.mkdir()
(briefs / "a.md").write_text("# a\n\nGOAL: x\nReport: 마일스톤마다 " + str(tmp / "cards" / "a.md") + " 에 추가.\n")
(briefs / "b.md").write_text("REPORT      orca orchestration send --to term_0FAKE3\n")
(briefs / "c.md").write_text("REPORT      Digest lines appended to this brief's note at each milestone.\n\n- 2026-10-13 착수\n")
(tmp / "cards").mkdir()
(tmp / "cards" / "a.md").write_text("# a\n\n## 10:00 설계\n- 다음: 옛 다음\n\n## 14:00 PR 열림\n"
                                    "- https://github.com/acme/web/pull/77\n- 다음: 리뷰 대기 · rev-a\n")
assert fleet.report_path(str(briefs / "a.md")) == tmp / "cards" / "a.md"
assert fleet.report_path(str(briefs / "b.md")) is None and fleet.report_path("Orca automations runner") is None
assert fleet.report_path(str(briefs / "c.md")) == briefs / "c.md"
rep = fleet.report_of(str(briefs / "a.md"))
assert (rep["next"], rep["pr"]) == ("리뷰 대기 · rev-a", "acme/web#77"), rep
assert fleet.report_of(str(briefs / "c.md"))["next"] == "2026-10-13 착수"

# A standalone card of the coordinator's (orca worktree create, no Run) is a row once it has a report; a resident
# standalone session with none (an automations runner) is not.
(state / "state.json").write_text(json.dumps({"sessions": [
    sess("root", "orchestrator", None, "working"),
    sess("card7", "standalone", "root", "working", name="관측 카드", report=rep),
    sess("runner", "standalone", "root", "idle", name="automations-runner")], "prs": []}))
(tmp / "brief-last" / "top-level.json").unlink()
carded = ok("brief", "render", "--periodic", "--now", NOW)
assert "| 🔄 | 관측 카드 | #77 ? | — |" in carded and "- 🔄 관측 카드 — 리뷰 대기 · rev-a" in carded and "runner" not in carded, carded

# Open owner requests get one line with their count, the oldest's age and how many are left unlinked; none, no line.
assert "📝" not in carded
(state / "requests.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [
    {"ev": "open", "id": "r1", "ts": "2026-10-13T13:30:00Z", "text": "merge acme/web#77"},
    {"ev": "open", "id": "r2", "ts": "2026-10-13T14:20:00Z", "text": "look at the flaky test"},
    {"ev": "open", "id": "r3", "ts": "2026-10-13T13:00:00Z", "text": "reply to the reviewer"},
    {"ev": "done", "id": "r3", "ts": "2026-10-13T13:10:00Z", "evidence": "replied"}]))
asked = ok("brief", "render", "--periodic", "--now", NOW)
assert "📝 **요청** — 요청 2건 열림 · 가장 오래된 것 1h00m · 미연결 30분+ 1건" in asked, asked

# A held item stored twice before later add merged them renders once, in its newest words.
b = json.loads((state / "board.json").read_text())
b["later"] += [{"title": "콘솔 시안 대비 main에서 누락된 절차 확인", "trigger": "#9 land 뒤"},
               {"title": "콘솔 시안 대비 main 누락 절차만 확인", "trigger": "#9 land 뒤"}]
(state / "board.json").write_text(json.dumps(b, ensure_ascii=False))
held = ok("brief", "render", "--periodic", "--now", NOW)
assert held.count("#9 land 뒤") == 1 and "- #9 land 뒤 → 콘솔 시안 대비 main 누락 절차만 확인" in held.splitlines(), held

# A report on request: no board at all, only the fleet's sessions and the stage table. The live card is a row, the stage
# table goes first, the model's insight comes after 진행 중 and its decision is numbered after the registered one.
req = tmp / "req"
renv = {"ORCH_FLEET_STATE": str(req / "dashboard"), "ORCH_BRIEF_LAST": str(req / "brief-last")}


def rok(*args):
    code, out, err = orch_env(renv, *args)
    assert code == 0, (args, out, err)
    return out


rok("stage", "add", "login", "--title", "로그인 개선", "--pr", "acme/web#40")
rok("stage", "set", "login", "--col", "dev", "--mark", "ok", "--evidence", "v1.7.2")
rok("stage", "set", "login", "--col", "prod", "--mark", "checking", "--by", "QA")
rok("stage", "env", "--prod", "v1.7.1", "--dev", "v1.7.2")
rok("stage", "next", "v1.7.2 prod 배포 (사람, 미정)")
rok("decide", "add", "--title", "재색인 권한", "--option", "서비스 계정::만료 없음", "--option", "개인 토큰", "--recommend", "1")
(req / "dashboard" / "state.json").write_text(json.dumps({"generated_at": NOW, "sessions": [
    sess("root", "orchestrator", None, "working"),
    sess("c7", "task", "root", "working", task_title="ABC-1 정렬", gh_repo="acme/web",
         report={"next": "CI 통과 → 리뷰 의뢰 · 워커", "pr": "acme/web#45"})],
    "prs": [{"key": "acme/web#40", "state": "MERGED", "merged_at": "2026-10-12T03:00:00Z"}]}))
(req / "dashboard" / "prs.json").write_text(json.dumps({"prs": dict([pr("acme/web#45", ci="pending")])}))
REQUEST = ["brief", "render", "--now", NOW, "--headline", "정렬 PR CI 중, 유료 플랜 하나만 정해 주세요",
           "--insight", "★ 정렬은 CI 가 끝나면 리뷰로 간다",
           "--decision", "검색 API 유료 플랜(월 $50): 올림 / 현행 유지 → 추천: 현행 유지 — 무료 한도의 60% · 답이 없으면 현행 유지"]
REQUEST_GOLDEN = """\
**14:30 · 정렬 PR CI 중, 유료 플랜 하나만 정해 주세요**

prod: v1.7.1 · dev: v1.7.2
✅ 완료·확인 · ❌ 미완료·실패 · ⚠️ 일부·추정 · 🔄 확인 중 · 해당 없음

| 작업 | feature·대표 PR | PR 머지 | dev 확인 | prod 확인 |
|---|---|---|---|---|
| 로그인 개선 | acme/web#40 | ✅ 10/12 | ✅ v1.7.2 | 🔄 (QA) |

다음 순서
1. v1.7.2 prod 배포 (사람, 미정)

🔄 진행 · 🙋 오너 차례 · ⏳ 다른 사람 대기 · ⏸️ 보류 · 막대=단계(추정)

PR: ❗ 사람이 풀 것 · CI… CI 진행

**acme/web → main**

| | 리드/Phase | PR | 진행 |
|---|---|---|---|
| 🔄 | ABC-1 정렬 | [#45](https://example.test/45) ❗리뷰 CI… | — |

🔄 **진행 중**

- 🔄 ABC-1 정렬 — CI 통과 → 리뷰 의뢰 · 워커

★ 정렬은 CI 가 끝나면 리뷰로 간다

❓ **결정 필요** — 1. (d1) 재색인 권한 → 추천: 서비스 계정 — 만료 없음

2. 검색 API 유료 플랜(월 $50): 올림 / 현행 유지 → 추천: 현행 유지 — 무료 한도의 60% · 답이 없으면 현행 유지

🙋 **지금 오너가 할 일** — 없음"""
request = rok(*REQUEST)
if "--print" in sys.argv:
    print(request)
if request != REQUEST_GOLDEN:
    import difflib
    sys.exit("\n".join(difflib.unified_diff(REQUEST_GOLDEN.splitlines(), request.splitlines(), "golden", "rendered", lineterm="")))
for start, rows in table_width.tables(request):
    assert table_width.table_width(rows) <= 78, (start, table_width.table_width(rows))
# The periodic form of the same fleet has no stage table; Telegram has no markdown and keeps the stage table's lines.
assert "| 작업 |" not in rok("brief", "render", "--periodic", "--now", NOW)
tg_req = rok(*REQUEST, "--telegram")
assert not any(m in tg_req for m in ("**", "|", "](")), tg_req
assert "📋 작업별 (prod: v1.7.1 · dev: v1.7.2)" in tg_req.splitlines() and "🔄 ABC-1 정렬 #45 ❗리뷰 CI… — —" in tg_req.splitlines(), tg_req
# A stage table wider than 78 columns is reported like any other.
rok("stage", "add", "wide", "--title", "아주 긴 이름의 작업으로 표의 폭을 넘기는 행", "--pr", "acme/web#1234")
rok("stage", "set", "wide", "--col", "dev", "--mark", "partial", "--evidence", "로그로만 확인하고 화면은 아직 보지 않음")
code, _, err = orch_env(renv, *REQUEST)
assert code == 1 and err == "a table is wider than 78 columns", (code, err)

# GitHub outranks the board: a phase whose own PR merged is ✅ and leaves 진행 중 whatever its board next says, a PR
# closed unmerged is ⏸️ PR 닫힘. A lead the board holds still shows its live cards whose PR no board row has, and a PR
# the PR list does not have says so instead of showing a bare number.
mg = tmp / "merged"
menv = {"ORCH_FLEET_STATE": str(mg / "dashboard"), "ORCH_BRIEF_LAST": str(mg / "brief-last")}


def mok(*args):
    code, out, err = orch_env(menv, *args)
    assert code == 0, (args, out, err)
    return out


mok("board", "lead", "add", "L", "--handle", "term_0FAKEL", "--repo", "acme/web")
mok("board", "set", "L/ph1", "--kind", "phase", "--status", "waiting", "--pr", "acme/web#45", "--next", "머지 대기")
mok("board", "set", "L/ph1/t1", "--title", "하위", "--pr", "acme/web#46")
mok("board", "set", "L/ph2", "--kind", "phase", "--status", "moving", "--pr", "acme/web#48", "--next", "리뷰 대응")
(mg / "dashboard" / "prs.json").write_text(json.dumps({"prs": dict([
    pr("acme/web#45", "MERGED", merged="2026-10-13T13:00:00Z"), pr("acme/web#46", "MERGED", merged="2026-10-13T13:00:00Z"),
    pr("acme/web#48", "CLOSED")])}))
(mg / "dashboard" / "state.json").write_text(json.dumps({"generated_at": NOW, "sessions": [
    sess("root", "orchestrator", None, "working"),
    sess("lead", "orchestration", "root", "working", h="term_0FAKEL"),
    sess("c9", "task", "lead", "working", task_title="ABC-9 라이브", gh_repo="acme/web", report={"pr": "acme/web#47"}),
    sess("c8", "task", "lead", "working", task_title="ABC-8 보드에 있음", gh_repo="acme/web", report={"pr": "acme/web#46"})],
    "prs": []}))
merged = json.loads(mok("brief", "render", "--periodic", "--now", NOW, "--json"))
text = merged["text"]
assert "| ✅ | ph1 | [#45](https://example.test/45) 45c0ffe | — |" in text, text
assert "| ✅ | └ 하위 | [#46](https://example.test/46) 46c0ffe | — |" in text, text
assert "| ⏸️ | ph2 | [#48](https://example.test/48) 닫힘 | — |" in text and "- PR 닫힘 → L ph2" in text, text
assert "| 🔄 | └ ABC-9 라이브 | #47 ? | — |" in text and "- └ 🔄 ABC-9 라이브 — 작업 중" in text, text
assert "머지 대기" not in text and "ABC-8" not in text and "fleet:lead" not in merged["snapshot"]["rows"], text


class GhDown:  # sessions collected, the PR list not: the headline says the PR states are old
    sources = {"orca": {"error": None}, "github": {"error": "gh: API rate limit exceeded"}}

    def tick(self):
        return {}


os.environ["ORCH_FLEET_STATE"] = str(tmp / "gh-down")
board.fleet.Fleet = GhDown
assert board.refresh(fleet.parse(NOW), 5) == "PR 목록 갱신 실패: gh: API rate limit exceeded"
assert "PR 상태 없음 1" in merged["headline_facts"] and "PR: ? PR 상태 없음" in text, (merged["headline_facts"], text)

# Who a wait is on: the owner (🙋, 지금 오너가 할 일) or another person (⏳, 다른 사람 대기), each item in one list
# only. A review the owner was asked for, a review someone else was asked for, another team's reply, a decision, and
# a row that says nothing of who it waits on (❔, 진행 중 with 확인 필요: no guess).
wd = tmp / "who"
wenv = {"ORCH_FLEET_STATE": str(wd / "dashboard"), "ORCH_BRIEF_LAST": str(wd / "brief-last")}


def wok(*args):
    code, out, err = orch_env(wenv, *args)
    assert code == 0, (args, out, err)
    return out


def section(text, head):
    """The item lines under a section heading, or [] when the report has no such section."""
    blocks = text.split("\n\n")
    return blocks[blocks.index(head) + 1].splitlines() if head in blocks else []


wok("board", "lead", "add", "W", "--handle", "term_0FAKEW", "--repo", "acme/web")
wok("board", "set", "W/mine", "--title", "내 리뷰", "--pr", "acme/web#91", "--status", "waiting")
wok("board", "set", "W/theirs", "--title", "남 리뷰", "--pr", "acme/web#92", "--status", "waiting")
wok("board", "set", "W/reply", "--title", "디자인 답변", "--status", "waiting", "--owner", "디자인팀", "--next", "10/15 리마인드")
wok("board", "set", "W/ask", "--title", "누구 대기", "--status", "waiting")
wok("decide", "add", "--title", "캐시 정책", "--option", "TTL 1h", "--option", "TTL 1d", "--recommend", "1")
(wd / "dashboard" / "state.json").write_text(json.dumps({"generated_at": NOW, "sessions": [
    sess("lw", "orchestration", "root", "working", h="term_0FAKEW")]}))
who_prs = {"me": "octo-owner", "prs": dict([pr("acme/web#91", reviewers=["octo-owner"]), pr("acme/web#92", reviewers=["rev-x"])])}
(wd / "dashboard" / "prs.json").write_text(json.dumps(who_prs))
stamp_dir, state = state, wd / "dashboard"
stamp()
state = stamp_dir
text = wok("brief", "render", "--periodic", "--now", NOW)
mine, theirs, moving = section(text, "🙋 **지금 오너가 할 일**"), section(text, "⏳ **다른 사람 대기**"), section(text, "🔄 **진행 중**")
assert "| 🙋 | └ 내 리뷰 | [#91](https://example.test/91) ❗리뷰 | — |" in text, text
assert "| ⏳ | └ 남 리뷰 | [#92](https://example.test/92) ❗리뷰 | — |" in text, text
assert "| ⏳ | └ reply 디자인 답변 | — | — |" in text and "| ❔ | └ ask 누구 대기 | — | — |" in text, text
assert mine == ["- 리뷰 · [#91](https://example.test/91) → 내 리뷰"], mine
assert theirs == ["- rev-x · 리뷰 [#92](https://example.test/92) · 30분 전부터 → 남 리뷰",
                  "- 디자인팀 · 30분 전부터 → reply 디자인 답변 · 다음: 10/15 리마인드"], theirs
assert "- └ ❔ ask 누구 대기 — 확인 필요: 누구 대기인지 기록 없음" in moving, moving
assert not any(w in l for l in mine + moving for w in ("남 리뷰", "디자인 답변")), (mine, moving)
assert not any(w in l for l in theirs + moving for w in ("내 리뷰", "캐시 정책")) and "누구 대기" not in "".join(mine + theirs)
assert "❓ **결정 필요** — 1. (d1) 캐시 정책 → 추천: TTL 1h" in text and "캐시 정책" not in "".join(mine), text
# Without the owner's gh login a requested review could be anyone's: 확인 필요, not someone else's.
del who_prs["me"]
(wd / "dashboard" / "prs.json").write_text(json.dumps(who_prs))
text = wok("brief", "render", "--periodic", "--now", NOW)
assert "- └ ❔ 내 리뷰 — 확인 필요: 오너 login 모름" in text and "→ 내 리뷰" not in text, text
# A waiting row on the owner by name, a check status nobody may set, and 🙋 as a status shorthand.
assert board.is_owner("오너", None) and board.is_owner("@Octo-Owner", "octo-owner") and not board.is_owner("rev-x", "octo-owner")
assert board.status_of("🙋") == "owner" and orch("board", "set", "search", "--status", "check")[2].startswith("--status is one of")

# Only finished work folds. A ✅ row merged over 24 h ago leaves its table for one count per section, and a section with
# nothing else left goes whole; a ✅ within 24 h, a done head over a recent ✅ child, and every 🔄 ⏳ ⏸️ row stay.
fd = tmp / "fold"
fenv = {"ORCH_FLEET_STATE": str(fd / "dashboard"), "ORCH_BRIEF_LAST": str(fd / "brief-last")}


def fok(*args):
    code, out, err = orch_env(fenv, *args)
    assert code == 0, (args, out, err)
    return out


fok("board", "lead", "add", "F", "--handle", "term_0FAKEF", "--repo", "acme/web")
fok("board", "set", "F/old", "--title", "옛 완료", "--pr", "acme/web#1")
fok("board", "set", "F/new", "--title", "새 완료", "--pr", "acme/web#2")
fok("board", "set", "F/run", "--title", "진행", "--next", "구현 · 워커")
fok("board", "set", "F/wait", "--title", "대기", "--status", "waiting", "--owner", "디자인팀", "--next", "리마인드")
fok("board", "set", "F/hold", "--title", "보류 행", "--status", "held", "--next", "다음 분기")
fok("board", "lead", "add", "G", "--handle", "term_0FAKEG", "--repo", "acme/old")
fok("board", "set", "G", "--status", "done")
fok("board", "set", "G/t", "--title", "끝난 일", "--pr", "acme/old#3")
fok("board", "lead", "add", "H", "--handle", "term_0FAKEH", "--repo", "acme/mid")
fok("board", "set", "H", "--status", "done")
fok("board", "set", "H/t", "--title", "막 끝난 일", "--pr", "acme/mid#4")
fok("board", "later", "add", "rollout 카드 재기동(브리프 x.md)", "--trigger", "B2 머지")
fok("board", "later", "add", "rollout 카드 재기동 — 확인 즉시", "--trigger", "B2 머지 + 호환 테스트")
stamp_dir, state = state, fd / "dashboard"
stamp()
b = json.loads((state / "board.json").read_text())
for k in ("G", "H"):
    b["rows"][k]["done_at"] = "2026-10-10T00:00:00Z"
(state / "board.json").write_text(json.dumps(b, ensure_ascii=False))
state = stamp_dir
(fd / "dashboard" / "prs.json").write_text(json.dumps({"prs": dict([
    pr("acme/web#1", "MERGED", merged="2026-10-11T00:00:00Z"), pr("acme/web#2", "MERGED", merged="2026-10-13T12:00:00Z"),
    pr("acme/old#3", "MERGED", merged="2026-10-09T00:00:00Z"), pr("acme/mid#4", "MERGED", merged="2026-10-13T13:00:00Z")])}))
folded = json.loads(fok("brief", "render", "--periodic", "--now", NOW, "--json"))
text = folded["text"]
assert "옛 완료" not in text and "acme/old" not in text and "끝난 일" not in text.replace("막 끝난 일", ""), text
assert "| ✅ | └ 새 완료 | [#2](https://example.test/2) 02c0ffe | — |" in text, text
assert all(f"| {i} | └ {t} |" in text for i, t in (("🔄", "run 진행"), ("⏳", "wait 대기"), ("⏸️", "hold 보류 행"))), text
assert "| ✅ | H | — | — |" in text and "| ✅ | └ 막 끝난 일 |" in text, text
# acme/web counts its one old row; acme/old (G and its sub-task) is gone whole and counts only in the headline.
assert text.count("✅ 그 전 완료 1건 (대시보드)") == 1 and "접음 완료 3 (대시보드)" in folded["headline_facts"], text
assert "F/old" not in folded["snapshot"]["rows"] and "G/t" not in folded["snapshot"]["rows"]
# Held items whose work reads the same once the asides are gone are one line, the newest wording.
assert section(text, "⏸️ **보류**") == ["- 다음 분기 → hold 보류 행", "- B2 머지 + 호환 테스트 → rollout 카드 재기동 — 확인 즉시"], text

# The stage table drops a finished row and one a week unchanged; with none left, 다음 순서 goes with it.
sp = fd / "dashboard" / "stages.json"
sp.write_text(json.dumps({"order": ["done", "all", "idle"], "next": ["다음 배포"], "rows": {
    "done": {"id": "done", "title": "끝남", "cells": {}, "created_at": "2026-10-13T10:00:00Z", "done_at": "2026-10-13T11:00:00Z"},
    "all": {"id": "all", "title": "모두 확인", "created_at": "2026-10-13T10:00:00Z", "cells": {
        c: {"mark": m, "evidence": "v1", "at": "2026-10-13T10:00:00Z"} for c, m in (("merge", "ok"), ("dev", "ok"), ("prod", "na"))}},
    "idle": {"id": "idle", "title": "멈춤", "created_at": "2026-10-01T00:00:00Z",
             "cells": {"dev": {"mark": "checking", "at": "2026-10-05T00:00:00Z"}}}}}, ensure_ascii=False))
rq = fok("brief", "render", "--now", NOW, "--json")
assert "| 작업 |" not in rq and "다음 배포" not in rq and "단계표 3" in json.loads(rq)["headline_facts"][-1], rq
s = json.loads(sp.read_text())
s["order"].append("live")
s["rows"]["live"] = {"id": "live", "title": "확인 중", "created_at": "2026-10-13T13:00:00Z", "cells": {}}
sp.write_text(json.dumps(s, ensure_ascii=False))
rq = json.loads(fok("brief", "render", "--now", NOW, "--json"))["text"]
assert "| 확인 중 | – | – | – | – |" in rq and "1. 다음 배포" in rq and "멈춤" not in rq, rq

print("ok")
