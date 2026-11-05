"""Offline tests for how the brief classifies a card's row from its PR's live state and the board's held items, on
rows shaped like ones a real report got wrong (names invented).

Run: python3 test_brief_rows.py
"""
import datetime as dt, json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-brief-rows-"))
env = {**os.environ, "ORCH_FLEET_STATE": str(tmp / "dashboard"), "ORCH_BRIEF_LAST": str(tmp / "brief-last"),
       "ORCH_FLEET_CONFIG": str(tmp / "none.json"), "AGENT_SKILLS_STATE": str(tmp / "state"), "TZ": "UTC",
       "AGENT_SKILLS_CONFIG": str(tmp / "none.json"), "PROGRAMS_HOME": str(tmp / "programs")}
os.environ.update(env)
sys.path.insert(0, str(HERE))
import board, fleet, store, table_width, tidy  # noqa: E402

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
store.import_apply(checks=False)
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
        if line.startswith("## ") and line[3] in "📌⏳⏸🙋❓📝":
            on = line.startswith("## " + head)
            continue
        if line.startswith("## ") and " → " in line:  # a repository's heading ends the section; its rows may be lines
            on = False
        if on and line.startswith("- "):
            lines.append(line[2:])
    return lines


b = brief()
text, rows = b["text"], {o["label"]: o["status"] for o in b["open"]}
moving, theirs, turn, held = (section(text, h) for h in ("📌", "⏳", "🙋", "⏸️"))

full = b["full"]  # the dashboard's copy: no cell cut, so a long next step is read whole
assert "- [" not in text and "…[" not in text, text
# acme/app#363: nothing on the PR is the agent's to fix and nobody is asked, but who merges it is not recorded (not the
# owner's repository, no program, no agent_merges): ❔ for the agent to record, never 🙋 on a guess.
assert rows["plugins-reference"] == "check", rows
assert "에이전트 조치 필요: 머지 주체 기록 없음 · 5h 전" in full and not any("plugins-reference" in t for t in turn), (full, turn)
# acme/api#550: a draft no session works on and no held item names: ❔ for the agent to resume or hold, not 🙋.
assert rows["headless-apitoken"] == "check", rows
assert "에이전트 조치 필요: 초안 · 세션 없음 — 재개하거나 보류로 기록 · 5h 전" in full, full
# acme/plugins#633: the held item naming #633 holds the row: ⏸️ in the table, one 보류 line with the row's name.
assert rows["headless-skill"] == "held" and "| ⏸️ | headless-skill | [#633](https://example.test/633) 초안 수정요청 | ↓ 보류 |" in text, text
assert f"{soon} prod 전환 확인 후 → headless-skill" in held, held  # the held item itself is counted on the pointer line
assert not any("headless-skill" in m for m in moving + turn + theirs), (moving, turn, theirs)
# ...and Needs you demotes the PR on the same rule.
holds = fleet.held_prs(board.read(), tidy.closed(), {k: v["detail"] for k, v in PRS.items()}, now)
assert holds == {"acme/plugins#633": f"{soon} prod 전환 확인 후", "acme/plugins#640": f"{soon} 리뷰 뒤",
                 "acme/api#641": f"{soon} 배포 뒤"}, holds  # #182 is two PRs, so it holds neither
# acme/plugins#640: a held item names it, but its card is stopped at a prompt only the owner answers: 🙋, not ⏸️.
assert rows["prompt-stopped"] == "owner", rows
assert any(t.startswith("확인 대기 · [#640]") and t.endswith("→ prompt-stopped") for t in turn), turn
assert not any("#640" in h for h in held), held  # a queue item, not a ⏸️ row: listed by orch board todo list
# acme/api#641: the row would be ❔ (수정요청 받음), so its held item holds it, under one 보류 line.
assert rows["api-cr"] == "held" and f"{soon} 배포 뒤 · 수정 반영 → api-cr" in held, held
assert not any("api-cr" in m for m in moving + turn + theirs), (moving, turn, theirs)
# Waiting on a review (a team's counts): the item says whose review, and no next step repeats it.
assert "team:app-dev · 리뷰 [#630](https://example.test/630) · 12h 전부터 → shared-files-fix" in theirs, theirs
assert "rev-h, team:app-dev · 리뷰 [#631](https://example.test/631) · 12h 전부터 → eval-migrate" in theirs, theirs
assert any(t.startswith("team:app-dev · 리뷰 [#182]") and t.endswith("→ flows-revert") for t in theirs), theirs
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
st = board.read()
assert st["later"][-1]["title"] == " ".join(LONG.split()) and st["later"][-1]["trigger"] == " ".join(("목 아침 " * 20).split())
assert st["doing"][-1]["next"].endswith("금: 전환") and st["rows"]["long"]["title"] == st["rows"]["long"]["next"] == " ".join(LONG.split())
row = next(r for r in stages.table()["rows"] if r["id"] == "long")
assert row["title"] == row["cells"]["prod"]["evidence"] == " ".join(LONG.split()), row
# A long name and next step are never cut: that repository goes out as whole lines, and the API's `full` keeps the table.
b = brief()
lines = b["text"].splitlines()
whole_line = f"- 🔄 {' '.join(LONG.split())} — {' '.join(LONG.split())} · 세션 없음"
assert whole_line in lines and not any("…" in l for l in lines if "acme/app#877" in l), b["text"]
assert f"| 🔄 | {' '.join(LONG.split())} | — | {' '.join(LONG.split())} | 세션 없음 |" in b["full"].splitlines(), b["full"]
assert b["width_ok"] and {o["label"] for o in b["open"]} >= {" ".join(LONG.split())}, b["open"]
# Orca cuts a task's name to a head and "...": the spec's first line is the whole of it, and nothing else replaces it.
assert fleet.task_name({"display_name": "role-terms: 용어 정리 — card (202...", "spec": "role-terms: 용어 정리 — card (2026-10-27)\n\n본문"}) \
    == "role-terms: 용어 정리 — card (2026-10-27)"
assert fleet.task_name({"display_name": "짧은 이름...", "spec": "다른 첫 줄\n본문"}) == "짧은 이름..."
assert fleet.task_name({"task_title": "온전한 이름", "spec": "온전한 이름 그리고 더"}) == "온전한 이름"

# A probe dispatched with an instruction for its spec ("send worker_done … 브리프: <path>") was listed under its lead as
# a moving row named by that instruction: Orca names a task after the spec's first line. A card whose task name is a
# goal sentence pointing at its brief goes by its worktree's name, and a dispatch that completed with no PR leaves.
fake = lambda sid, **kw: {"id": sid, "kind": "task", "parent": "lead1", "phase": "idle", "name": sid, "gh_repo": "acme/app",
                          "last_activity": ago(0.1), "terminals": [{"handle": f"term_{sid}"}], "report": None, **kw}
probe_state = {"sessions": [{**fake("lead1", name="계보 리드"), "kind": "orchestration", "parent": "s-root"},
                            {**card("s-root", "root", "working", "acme/app"), "kind": "orchestrator", "parent": None},
                            fake("probe-bound", task_title='send worker_done with the body "ok" and stop 브리프: /var/x/bound-brief.md',
                                 dispatch_status="completed"),
                            fake("probe-live", task_title="/goal 측정 probe 를 끝까지 진행한다 브리프: ~/share/x/brief.md",
                                 dispatch_status="dispatched"),
                            fake("w-titled", task_title="ABC-7 정렬 수정", dispatch_status="dispatched", phase="working")]}
rows_, _ = board.fleet_rows(probe_state, {}, {})
titles = {r["session"]: r["title"] for r in rows_.values()}
assert "probe-bound" not in titles, titles  # completed, no PR: nothing left to report
assert titles["probe-live"] == "probe-live" and titles["w-titled"] == "ABC-7 정렬 수정", titles
assert not any("worker_done" in t or "브리프" in t for t in titles.values()), titles
assert board.card_title({"name": "wt", "task_title": "Brief: ~/b.md"}) == "wt"
assert board.card_title({"name": "wt", "task_title": "ABC-1 브리프 정리"}) == "ABC-1 브리프 정리"  # no path: a name

# Each open row's 세션 cell: working, stopped (a prompt waits, or the turn ended STALL_MIN ago), or no session.
now_ = fleet.utcnow()
assert board.session_state({"phase": "working"}, now_) == "working"
assert board.session_state({"phase": "waiting", "last_activity": ago(0)}, now_) == "stalled"
assert board.session_state({"phase": "idle", "last_activity": ago(0.1)}, now_) == "working"
assert board.session_state({"phase": "idle", "last_activity": ago(1)}, now_) == "stalled"
assert board.session_state({"phase": "open"}, now_) == board.session_state(None, now_) == "none"

# A lead whose session is gone, with a task row `board set --worktree` linked to a card still working: the task row is
# judged by its own session (moving, 작업 중), the lead's own row by the lead's (🔴 세션 없음).
st = json.loads((sd / "state.json").read_text())
st["sessions"].append(card("s-task", "task-card", "working", "acme/app"))
fleet.write_atomic(sd / "state.json", json.dumps(st))
board.lead_add("gone-lead", "term_gone", "acme/app", worktree="s-gone")
board.set_row("gone-lead/t", kind="task", parent="gone-lead", title="linked-task", status="moving", worktree="s-task")
by_label = {x["label"]: x for t in brief()["table_rows"] for x in t["rows"]}
assert by_label["task-card"]["session"]["word"] == "작업 중" and by_label["task-card"]["status"] != "🔴", by_label["task-card"]
assert by_label["gone-lead"]["status"] == "🔴" and by_label["gone-lead"]["session"]["word"] == "세션 없음", by_label["gone-lead"]

# A card a board row links with --worktree is that row's work: no fleet row of its own beside it.
rows_, _ = board.fleet_rows(probe_state, {}, {"fix": {"id": "fix", "worktree": "w-titled"}})
assert "w-titled" not in {r["session"] for r in rows_.values()}, rows_
# ...and a board lead found by its worktree after a terminal restart (a new handle) holds its lead session.
rows_, _ = board.fleet_rows(probe_state, {"계보": {"handle": "term_old", "worktree": "lead1"}}, {"계보": {"id": "계보"}})
assert not any(r["kind"] == "lead" for r in rows_.values()) and all(r.get("parent") == "계보" for r in rows_.values()), rows_

# A row whose only change is its session (작업 중 → 멈춤) is a changed row, bold in the next report.
r = subprocess.run([sys.executable, str(HERE / "prog.py"), "brief", "render", "--periodic", "--commit", "--no-refresh"],
                   capture_output=True, text=True, env=env)
assert r.returncode == 0, r.stderr
st = json.loads((sd / "state.json").read_text())
next(x for x in st["sessions"] if x["id"] == "s-task").update(phase="idle", last_activity=ago(1))
fleet.write_atomic(sd / "state.json", json.dumps(st))
assert "gone-lead/t" in brief()["changed"]

# A row linked by --worktree to a card whose PR its title never names takes that PR, since the card's own row is gone.
board.set_row("wt-pr", title="untitled-fix", repo="acme/plugins", status="moving", worktree="s4")
linked_row = next(x for t in brief()["table_rows"] for x in t["rows"] if (x["session"] or {}).get("id") == "s4")
assert linked_row["session"]["pr"] == "acme/plugins#630", linked_row

# A quiet row with no PR whose --worktree card is working is not stale: the card is its evidence.
quiet_store = {"rows": {"q": {"id": "q", "title": "untitled", "status": "moving", "reported_at": ago(9), "worktree": "w-titled"}}}
assert "row:q" not in tidy.assess(quiet_store, probe_state, {}, now=now), tidy.assess(quiet_store, probe_state, {}, now=now)
# A board lead row linked to a lead session with --worktree holds that session: no second fleet lead row.
rows_, _ = board.fleet_rows(probe_state, {}, {"L": {"id": "L", "kind": "lead", "worktree": "lead1"}})
assert not any(r["kind"] == "lead" for r in rows_.values()) and all(r.get("parent") == "L" for r in rows_.values()), rows_

# A card whose every tool call a stale hook blocks reads as an ended turn: patrol's H (or R) makes its row ❔ with
# patrol's reason, while that round is fresh; another card, and a stale round, leave it as it was.
st = json.loads((sd / "state.json").read_text())
st["sessions"] += [{**card("s-stuck", "stuck-card", "idle", "acme/app"), "path": "/w/stuck", "last_activity": ago(5),
                    "orca_parent": "s-root"},
                   {**card("s-fine", "fine-card", "idle", "acme/app"), "path": "/w/fine", "orca_parent": "s-root"}]
fleet.write_atomic(sd / "state.json", json.dumps(st))
verdicts = sd.parent / "patrol-verdicts.json"
fleet.write_atomic(verdicts, json.dumps({"t": now.timestamp() - 600, "check": {"/w/stuck": "hook 오류로 도구가 막힘"}}))
b = brief()
rows = {o["label"]: o["status"] for o in b["open"]}
assert "에이전트 조치 필요: hook 오류로 도구가 막힘" in b["full"], b["full"]
assert rows["stuck-card"] == "check" and rows["fine-card"] == "moving", rows
judged = {"/w/stuck": ("hook 오류로 도구가 막힘", now - dt.timedelta(minutes=10))}
stuck = st["sessions"][-2]
for s, why in (({**stuck, "phase": "working"}, "a session at work again has got past what patrol saw"),
               ({**stuck, "last_activity": ago(0.1)}, "and so has one that ended another turn since")):
    rows_, _ = board.fleet_rows({"sessions": [SESSIONS[0], s]}, {}, {}, checks=judged)
    assert [r["status"] for r in rows_.values()] == ["moving"], why
rows_, _ = board.fleet_rows({"sessions": [SESSIONS[0], stuck]}, {}, {}, checks=judged)
assert [r["status"] for r in rows_.values()] == ["check"], rows_
fleet.write_atomic(verdicts, json.dumps({"t": now.timestamp() - 2 * board.PATROL_CHECK_S,
                                         "check": {"/w/stuck": "hook 오류로 도구가 막힘"}}))
assert {o["label"]: o["status"] for o in brief()["open"]}["stuck-card"] == "moving", "a stale round says nothing of now"
# patrol's B (a card idle on a background task nothing will finish) reaches the same ❔ row with its own reason.
fleet.write_atomic(verdicts, json.dumps({"t": now.timestamp() - 600,
                                         "check": {"/w/stuck": "백그라운드 작업을 기다리지만 깨울 작업이 없음"}}))
b = brief()
rows = {o["label"]: o["status"] for o in b["open"]}
assert rows["stuck-card"] == "check", rows
assert "에이전트 조치 필요: 백그라운드 작업을 기다리지만 깨울 작업이 없음" in b["full"], b["full"]
# The same verdict on a card a board row links: no fleet row exists to carry it, so the board row does.
fleet.write_atomic(verdicts, json.dumps({"t": now.timestamp() - 600, "check": {"/w/stuck": "hook 오류로 도구가 막힘"}}))
board.set_row("linked-stuck", title="linked-stuck", repo="acme/app", status="moving", worktree="s-stuck")
b = brief()
rows = {o["label"]: o["status"] for o in b["open"]}
assert rows["stuck-card"] == "check" and "linked-stuck" not in rows and [o["label"] for o in b["open"]].count("stuck-card") == 1, rows
assert "에이전트 조치 필요: hook 오류로 도구가 막힘" in b["full"], b["full"]
board.set_row("linked-stuck", status="done")
# Nor is there one when only the PR ties a board row to the card: the card's row is left to the board row.
PRS["acme/app#9"] = {"detail": {"key": "acme/app#9", "state": "OPEN", "merged_at": None, "draft": False, "ci": "success",
                                "decision": None, "merge_state": "CLEAN", "reviewers": []}}
fleet.write_atomic(sd / "prs.json", json.dumps({"prs": PRS, "me": "owner-me"}))
st["sessions"] = [{**s, "linked_pr": {"key": "acme/app#9"}} if s.get("id") == "s-stuck" else s for s in st["sessions"]]
fleet.write_atomic(sd / "state.json", json.dumps(st))
board.set_row("pr-only", title="pr-only", repo="acme/app", status="moving", pr="acme/app#9")
b = brief()
rows = {o["label"]: o["status"] for o in b["open"]}
assert rows["pr-only"] == "check" and "stuck-card" not in rows, rows
assert "에이전트 조치 필요: hook 오류로 도구가 막힘" in b["full"], b["full"]
# A lead's report names its cards' PRs: a stalled lead says nothing of them.
st["sessions"] = [{**s, "kind": "orchestrator"} if s.get("id") == "s-stuck" else s for s in st["sessions"]]
fleet.write_atomic(sd / "state.json", json.dumps(st))
assert "hook 오류" not in brief()["text"]
board.set_row("pr-only", status="done")
verdicts.unlink()

# The 세션 cell says which session: its worktree's name (cut in a table, unless the row's name already says it) and,
# while the dashboard runs, a link to that session's page. Telegram has the whole name and no link: 127.0.0.1 is dead there.
st = fleet.read_json(sd / "state.json")
st["sessions"] += [card("s-link", "payments-migration-worktree", "working", "acme/links")]
fleet.write_atomic(sd / "state.json", json.dumps(st))
board.set_row("link-row", title="결제 이행", repo="acme/links", status="moving", worktree="s-link", next="리뷰")
board.set_row("named-row", title="payments-migration-worktree 정리", repo="acme/links", status="moving", worktree="s-link", next="리뷰")
board.set_row("no-link", title="연결 없음", repo="acme/links", status="moving", next="리뷰")
programs = tmp / "programs"
programs.mkdir(exist_ok=True)
text = brief()["text"]
assert "작업 중 · payments-migration-worktree" in text and "(http://" not in text, text  # no server: the name alone
assert "- [" not in text and "…" not in text, text
assert "payments-migration-worktree 정리** | — | 리뷰 | 작업 중 |" in brief()["full"]
assert "연결 없음" in text and "연결 없음** | — | 리뷰 | 세션 없음 |" in brief()["full"], text
(programs / ".dash.json").write_text(json.dumps({"pid": os.getpid(), "port": 4780}))
text = brief()["text"]
assert "[작업 중](http://127.0.0.1:4780/#/fleet/session/s-link) · payments-migration-worktree" in text, text
tg = subprocess.run([sys.executable, str(HERE / "prog.py"), "brief", "render", "--telegram", "--no-refresh"],
                    capture_output=True, text=True, env=env).stdout
assert any(l.endswith("— 리뷰 · 작업 중 · payments-migration-worktree") and "결제 이행" in l for l in tg.splitlines()) and "127.0.0.1:4780/#/fleet/session" not in tg, tg
# The model the collector read for the session (fleet.model_tags) sits in the same cell after the state word: O, S or H,
# `!` when it is off orchestrate.models. A row with no session shows none.
st = fleet.read_json(sd / "state.json")
for s in st["sessions"]:
    s["model_tag"] = "S!" if s["id"] == "s-link" else ""
fleet.write_atomic(sd / "state.json", json.dumps(st))
out = brief()
text = out["text"]
assert "[작업 중](http://127.0.0.1:4780/#/fleet/session/s-link) S! · payments-migration-worktree" in text, text
assert "연결 없음** | — | 리뷰 | 세션 없음 |" in out["full"], out["full"]
assert out["width_ok"] and "…" not in text, "the model mark must not push the table past 78 columns, nor cut a name"
# The same with a progress bar, a PR and a short session name beside the mark.
def link_pr(key):
    """A linked card's PR is Orca's (its linked_pr): board set --pr is refused on such a row, so the fixture says it here."""
    for s in st["sessions"]:
        if s["id"] == "s-link":
            s["linked_pr"] = {"key": key, "state": "OPEN"} if key else None
    fleet.write_atomic(sd / "state.json", json.dumps(st))


for s in st["sessions"]:
    if s["id"] == "s-link":
        s["name"], s["model_tag"] = "pay-mig", "O!"
board.set_row("link-row", progress="3/8")
link_pr("acme/links#1")
out = brief()
assert "O! · pay-mig" in out["text"] and out["width_ok"], out["text"]
link_pr("#123→rc-branch")
out = brief()
assert "O! · pay-mig" in out["text"] and out["width_ok"] and "…" not in out["text"], out["text"]  # nothing is cut
board.set_row("link-row", progress="")
link_pr(None)
for s in st["sessions"]:
    if s["id"] == "s-link":
        s["name"] = "payments-migration-worktree"
for s in st["sessions"]:
    s.pop("model_tag", None)
fleet.write_atomic(sd / "state.json", json.dumps(st))
# No section lists terminals: the report's text carries no handle on either surface.
st = fleet.read_json(sd / "state.json")
for s in st["sessions"]:
    if s["id"] == "s-link":
        s["terminals"] = [{"handle": "term_other", "connected": True}, {"handle": "term_agent", "connected": True, "agent": True}]
fleet.write_atomic(sd / "state.json", json.dumps(st))
text = brief()["text"]
assert "세션 찾기" not in text and "term_agent" not in text, text
tg = subprocess.run([sys.executable, str(HERE / "prog.py"), "brief", "render", "--telegram", "--no-refresh"],
                    capture_output=True, text=True, env=env).stdout
assert "세션 찾기" not in tg and "term_agent" not in tg, tg
# A row alone on its session reads as Orca names the worktree, whatever the board calls it, so that name finds it in
# Orca's Cmd+J palette: a phase too, without its lead's name in front in a shared table. Two rows on one session keep
# their titles (asserted above) and a title that holds the name stays. A cut name stays a prefix of it, which the
# palette also finds, and the list under the table has the whole of it.
LONG = "orca-display-name-that-runs-well-past-the-width-of-its-column-in-the-table-to-force-a-cut"
st = fleet.read_json(sd / "state.json")
st["sessions"] += [card("s-solo", "SOLO-CARD", "working", "acme/solo"), card("s-long", LONG, "working", "acme/solo"),
                   card("s-holds", "HOLDS-NAME", "working", "acme/solo")]
fleet.write_atomic(sd / "state.json", json.dumps(st))
board.set_row("solo-row", title="보드가 지은 제목", repo="acme/solo", status="moving", worktree="s-solo", next="리뷰")
board.set_row("long-row", title="긴 카드", repo="acme/solo", status="moving", worktree="s-long", next="리뷰")
board.set_row("holds-row", title="HOLDS-NAME 정리", repo="acme/solo", status="moving", worktree="s-holds", next="리뷰")
b = brief()
assert "보드가 지은 제목" not in b["text"] and "| 🔄 | **SOLO-CARD** | — | 리뷰 | [작업 중](" in b["full"], b["full"]
assert "HOLDS-NAME 정리" in b["text"], b["text"]
labels = [r["label"] for t in b["table_rows"] for r in t["rows"]]
assert "SOLO-CARD" in labels, labels
# The long name does not fit a 78-column table, so nothing is cut: the repository goes out as one whole line per row.
assert f"- 🔄 **{LONG}** — 리뷰" in b["text"] and "…" not in b["text"].split("acme/solo")[1] and "- [" not in b["text"], b["text"]
tg = subprocess.run([sys.executable, str(HERE / "prog.py"), "brief", "render", "--telegram", "--no-refresh"],
                    capture_output=True, text=True, env=env).stdout
assert LONG in tg and "SOLO-CARD" in tg and "보드가 지은 제목" not in tg, tg
for rid in ("solo-row", "long-row", "holds-row"):
    board.set_row(rid, status="done")
# Two long cards with one head are never cut alike, nor cut at all: their cells are whole, in lines when the table is
# too wide.
ACTIONS, WIZARD = "xx-agent-actions-lead", "xx-agent-wizard-lead-with-a-longer-name"
for rid, name in (("pair-a", ACTIONS), ("pair-w", WIZARD)):
    board.set_row(rid, title=name, repo="acme/twins", status="moving", next="에이전트 조치 필요: " + "설명이 아주 긴 다음 단계 " * 4)
b = brief()
twins = [l for l in b["text"].splitlines() if "xx-agent-" in l]
assert len(twins) == 2 and not any("…" in l for l in twins) and ACTIONS in twins[0] and WIZARD in twins[1], (twins, b["text"])
assert all(table_width.table_width(t) <= 78 for _, t in table_width.tables(b["text"])) and "- [" not in b["text"], b["text"]
assert ACTIONS in b["full"] and WIZARD in b["full"], b["full"]
for rid in ("pair-a", "pair-w"):
    board.set_row(rid, status="done")
# A finished row still shares its session: the row left open keeps its title.
st["sessions"] = st["sessions"][:-3] + [card("s-pair", "PAIR-CARD", "working", "acme/pair")]
fleet.write_atomic(sd / "state.json", json.dumps(st))
board.set_row("pair-done", title="끝난 단계", repo="acme/pair", status="done", worktree="s-pair", next="-")
board.set_row("pair-open", title="남은 단계", repo="acme/pair", status="moving", worktree="s-pair", next="리뷰")
text = brief()["text"]
assert "남은 단계" in text, text
board.set_row("pair-open", status="done")
st["sessions"].pop()
# A display name is Orca's text: a line break in it must not split the table row or part it from table_rows.
st["sessions"].append(card("s-break", "first\nsecond", "working", "acme/break"))
fleet.write_atomic(sd / "state.json", json.dumps(st))
board.set_row("break-row", title="제목", repo="acme/break", status="moving", worktree="s-break", next="리뷰")
b = brief()
assert "| 🔄 | **first second** |" in b["text"] and "second |" not in b["text"].replace("first second** |", ""), b["text"]
assert "first second" in [r["label"] for t in b["table_rows"] for r in t["rows"]], b["table_rows"]
board.set_row("break-row", status="done")
st["sessions"].pop()
fleet.write_atomic(sd / "state.json", json.dumps(st))
# The 진행 column shows only where a row has a bar; `--progress` on one row brings it back for that table.
full = brief()["full"]  # the tables with every cell whole, whether or not they fit 78 columns
assert "| | 리드/Phase | PR | 다음 | 세션 |" in full and "막대=단계" not in full, full
board.set_row("no-link", progress="1/4")
full = brief()["full"]
assert "| | 리드/Phase | PR | 진행 | 다음 | 세션 |" in full and "| ██░░░░░░ 1/4 | 리뷰 | 세션 없음 |" in full and "막대=단계" in full, full
# A doing item no session runs is ❔ with who holds it, never 🔄; one naming a live card by its owner is that card's.
board.doing_add("장애 조사", "lead-x", "원인 보고")
board.doing_add("이행 점검", "payments-migration-worktree", "리뷰 뒤 점검")
b = brief()
no_card = section(b["text"], "📌")
assert "❔ 장애 조사 — 세션 없음 · 원인 보고 · lead-x" in no_card and not any("이행 점검" in m for m in no_card), no_card
# A column that would cut to one phrase in every row is not cut: three reasons that share a long head stay whole, the
# repository goes out as lines, and the report has no `…` for a name or a reason and no footnote.
REASONS = ["에이전트 조치 필요: 리뷰 두 팀 승인을 확인한 뒤 머지 그리고 배포까지 " + w for w in ("진행", "보고", "정리")]
for n, reason in enumerate(REASONS):
    board.set_row(f"same-{n}", title=f"same-head-name-{n}", repo="acme/same", status="moving", next=reason)
b = brief()
same = [l for l in b["text"].splitlines() if "same-head-name-" in l]
assert len(same) == 3 and all(any(r in l for r in REASONS) for l in same) and not any("…" in l for l in same), (same, b["text"])
assert "- [" not in b["text"] and all(table_width.table_width(t) <= 78 for _, t in table_width.tables(b["text"])), b["text"]
for n in range(3):
    board.set_row(f"same-{n}", status="done")
print("test_brief_rows: ok")
