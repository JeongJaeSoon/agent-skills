"""Offline tests for how the brief classifies a card's row from its PR's live state and the board's held items, on
rows shaped like ones a real report got wrong (names invented).

Run: python3 test_brief_rows.py
"""
import datetime as dt, json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-brief-rows-"))
env = {**os.environ, "ORCH_FLEET_STATE": str(tmp / "dashboard"), "ORCH_BRIEF_LAST": str(tmp / "brief-last"),
       "ORCH_FLEET_CONFIG": str(tmp / "none.json"), "AGENT_SKILLS_STATE": str(tmp / "state"), "TZ": "UTC",
       "AGENT_SKILLS_CONFIG": str(tmp / "none.json")}
os.environ.update(env)
sys.path.insert(0, str(HERE))
import board, fleet, tidy  # noqa: E402

now = fleet.utcnow()
sd = fleet.state_dir()
ago = lambda h: fleet.iso(now - dt.timedelta(hours=h))  # noqa: E731
BOT_OK = {"login": "review-bot", "requested": False, "bot": True, "state": "APPROVED"}
BOT_CR = {"login": "review-bot", "requested": False, "bot": True, "state": "CHANGES_REQUESTED"}
TEAM = {"login": "team:app-dev", "requested": True, "team": True, "bot": False, "state": None}


def pr(key, **kw):
    repo, n = key.split("#")
    return {"probed_at": fleet.iso(now), "detail": {
        "key": key, "repo": repo, "number": int(n), "url": f"https://example.test/{n}", "state": "OPEN", "merged_at": None,
        "draft": False, "base": "main", "reviewers": [], "decision": "REVIEW_REQUIRED", "ci": "success",
        "merge_state": "BLOCKED", "created": ago(12), "updated": ago(1), **kw}}


def card(sid, name, phase, repo):
    return {"id": sid, "name": name, "kind": "standalone", "phase": phase, "parent": "s-root", "task_title": name,
            "gh_repo": repo, "project": repo, "terminals": [{"handle": f"term_{sid}"}], "last_activity": ago(5), "report": None}


PRS = dict([
    # No reviewer asked, CI green: GitHub wants a review nobody was asked for.
    ("acme/app#363", pr("acme/app#363")),
    # A draft with the team asked and a bot's approval, and no held item about it.
    ("acme/api#550", pr("acme/api#550", draft=True, decision=None, merge_state="CLEAN", reviewers=[BOT_OK, TEAM])),
    # A draft a bot asked to change, which a held item names by its bare number.
    ("acme/plugins#633", pr("acme/plugins#633", draft=True, decision="CHANGES_REQUESTED", reviewers=[BOT_CR, TEAM])),
    # Waiting on the team's review, the card gone or never started.
    ("acme/plugins#630", pr("acme/plugins#630", reviewers=[BOT_OK, TEAM])),
    ("acme/plugins#631", pr("acme/plugins#631", reviewers=[{"login": "rev-h", "requested": True}, TEAM])),
    # One number in two repositories: a bare #182 is neither.
    ("acme/flows#182", pr("acme/flows#182", reviewers=[TEAM])),
    ("acme/app#182", pr("acme/app#182", state="MERGED", merged_at=ago(48))),
    # Held by name like #633, but its card is stopped at a prompt.
    ("acme/plugins#640", pr("acme/plugins#640", draft=True, decision="CHANGES_REQUESTED", reviewers=[BOT_CR, TEAM])),
    # Held by name, on a board row whose wait nobody recorded.
    ("acme/api#641", pr("acme/api#641", decision="CHANGES_REQUESTED")),
])
SESSIONS = [{**card("s-root", "root", "working", "acme/app"), "kind": "orchestrator", "parent": None},
            card("s1", "plugins-reference", "offline", "acme/app"),
            card("s2", "headless-apitoken", "offline", "acme/api"),
            card("s3", "headless-skill", "open", "acme/plugins"),
            card("s4", "shared-files-fix", "open", "acme/plugins"),
            card("s5", "eval-migrate", "offline", "acme/plugins"),
            card("s6", "flows-revert", "offline", "acme/flows"),
            card("s7", "prompt-stopped", "waiting", "acme/plugins")]
OWNER_OF = {"acme/app#363": "s1", "acme/api#550": "s2", "acme/plugins#633": "s3", "acme/plugins#630": "s4",
            "acme/plugins#631": "s5", "acme/flows#182": "s6", "acme/plugins#640": "s7"}
sd.mkdir(parents=True, exist_ok=True)
fleet.write_atomic(sd / "prs.json", json.dumps({"prs": PRS, "me": "owner-me"}))
fleet.write_atomic(sd / "state.json", json.dumps({"generated_at": fleet.iso(now), "sessions": SESSIONS, "prs": [
    {"key": k, "session": OWNER_OF.get(k), "state": v["detail"]["state"]} for k, v in PRS.items()]}))
soon = f"{(now + dt.timedelta(days=3)):%-m/%-d}"
board.later_add("#633 ready 화 + 리뷰 재요청", f"{soon} prod 전환 확인 후")
board.later_add("#182 다시 보기", "flows 정리 뒤")
board.later_add("#640 다시 보기", f"{soon} 리뷰 뒤")
board.later_add("#641 다시 보기", f"{soon} 배포 뒤")
board.set_row("api-cr", title="api-cr", repo="acme/api", status="waiting", pr="acme/api#641", next="수정 반영")


def brief():
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), "brief", "render", "--periodic", "--json", "--no-refresh"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, (r.stdout, r.stderr)
    return json.loads(r.stdout)


def section(text, head):
    lines, on = [], False
    for line in text.splitlines():
        if line and line[0] in "🔄⏳⏸🙋❓📝":
            on = line.startswith(head)
            continue
        if on and line.startswith("- "):
            lines.append(line[2:])
    return lines


b = brief()
text, rows = b["text"], {o["label"]: o["status"] for o in b["open"]}
moving, theirs, turn, held = (section(text, h) for h in ("🔄", "⏳", "🙋", "⏸️"))

# acme/app#363: 🙋, a review to ask for, not ❔ 세션 꺼짐 · PR ❗리뷰.
assert rows["plugins-reference"] == "owner", rows
assert "리뷰 요청 · [#363](https://example.test/363) → plugins-reference" in turn, turn
# acme/api#550: no held item names it, so 🙋 with what to do and how to hold it instead, not ❔ 세션 꺼짐 · PR 초안.
assert rows["headless-apitoken"] == "owner", rows
assert ("초안 — ready 로 만들지 정하기 (카드 세션 꺼짐; 보류라면 `orch board later add \"<할 일> acme/api#550\" --trigger <날짜>` 로 ⏸️)"
        " · [#550](https://example.test/550) → headless-apitoken") in turn, turn
# acme/plugins#633: the held item naming #633 holds the row: ⏸️ in the table, one 보류 line with the row's name.
assert rows["headless-skill"] == "held" and "| ⏸️ | headless-skill | [#633](https://example.test/633) 초안 수정요청 | — |" in text, text
assert f"{soon} prod 전환 확인 후 → headless-skill" in held, held  # the held item itself is counted on the pointer line
assert not any("headless-skill" in m for m in moving + turn + theirs), (moving, turn, theirs)
# ...and Needs you demotes the PR on the same rule.
holds = fleet.held_prs(fleet.read_json(sd / "board.json"), tidy.closed(), {k: v["detail"] for k, v in PRS.items()}, now)
assert holds == {"acme/plugins#633": f"{soon} prod 전환 확인 후", "acme/plugins#640": f"{soon} 리뷰 뒤",
                 "acme/api#641": f"{soon} 배포 뒤"}, holds  # #182 is two PRs, so it holds neither
# acme/plugins#640: a held item names it, but its card is stopped at a prompt only the owner answers: 🙋, not ⏸️.
assert rows["prompt-stopped"] == "owner", rows
assert any(t.startswith("확인 대기 · [#640]") and t.endswith("→ prompt-stopped") for t in turn), turn
assert not any("#640" in h for h in held), held  # a queue item, not a ⏸️ row: listed by orch board todo list
# acme/api#641: the row would be ❔ (수정요청 받음), so its held item holds it, under one 보류 line.
assert rows["api-cr"] == "held" and f"{soon} 배포 뒤 · 수정 반영 → api-cr" in held, held
assert not any("api-cr" in m for m in moving + turn + theirs), (moving, turn, theirs)
# Waiting on a review: the next step is whose review, not the card's session.
assert "team:app-dev · 리뷰 [#630](https://example.test/630) · 12h 전부터 → shared-files-fix · 다음: team:app-dev 리뷰 대기" in theirs, theirs
assert ("rev-h, team:app-dev · 리뷰 [#631](https://example.test/631) · 12h 전부터 → eval-migrate · 다음: rev-h, team:app-dev 리뷰 대기"
        in theirs), theirs
assert any(t.startswith("team:app-dev · 리뷰 [#182]") and t.endswith("다음: team:app-dev 리뷰 대기") for t in theirs), theirs
assert not any(w in t for t in theirs for w in ("세션 꺼짐", "에이전트 없음")), theirs
assert any("#182 다시 보기" in k for k in rows) and rows["flows-revert"] == "waiting", rows  # still in the queue

# Nothing is cut where it is written: a long title, next step, held item or stage evidence is kept whole.
import stages  # noqa: E402
LONG = "acme/app#877 머지 + 배포 (오너 지시: 리뷰 두 팀 승인 확인 뒤, 제목의 보류 표시를 떼고 아침에 머지) " * 3
board.later_add(LONG, "목 아침 " * 20)
board.doing_add(LONG, "lead", "수: 머지·릴리스 / 목: 실측 / 금: 전환 " * 4)
board.set_row("long", title=LONG, repo="acme/app", status="moving", next=LONG)
stages.add("long", title=LONG)
stages.set_cell("long", "prod", "partial", evidence=LONG)
st = fleet.read_json(sd / "board.json")
assert st["later"][-1]["title"] == " ".join(LONG.split()) and st["later"][-1]["trigger"] == " ".join(("목 아침 " * 20).split())
assert st["doing"][-1]["next"].endswith("금: 전환") and st["rows"]["long"]["title"] == st["rows"]["long"]["next"] == " ".join(LONG.split())
row = next(r for r in stages.table()["rows"] if r["id"] == "long")
assert row["title"] == row["cells"]["prod"]["evidence"] == " ".join(LONG.split()), row
# The table cuts the long name to fit 78 columns, and the whole name is listed right under it; the API keeps it whole.
b = brief()
lines = b["text"].splitlines()
cut = next(l for l in lines if l.startswith("| 🔄 | acme/app#877"))
assert cut.endswith("…[1] | — | — |") and f"- [1] {' '.join(LONG.split())}" in lines, b["text"]
assert lines.index(f"- [1] {' '.join(LONG.split())}") - lines.index(cut) <= 3, b["text"]
assert b["width_ok"] and {o["label"] for o in b["open"]} >= {" ".join(LONG.split())}, b["open"]
# Orca cuts a task's name to a head and "...": the spec's first line is the whole of it, and nothing else replaces it.
assert fleet.task_name({"display_name": "role-terms: 용어 정리 — card (202...", "spec": "role-terms: 용어 정리 — card (2026-10-27)\n\n본문"}) \
    == "role-terms: 용어 정리 — card (2026-10-27)"
assert fleet.task_name({"display_name": "짧은 이름...", "spec": "다른 첫 줄\n본문"}) == "짧은 이름..."
assert fleet.task_name({"task_title": "온전한 이름", "spec": "온전한 이름 그리고 더"}) == "온전한 이름"
print("test_brief_rows: ok")
