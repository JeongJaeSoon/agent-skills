"""Offline tests for which sessions the brief shows as card rows, the one current-PR rule, a `run:` card's milestone
read from Orca's inbox, and the headline's missing-session and decision counts (names invented).

Run: python3 test_card_rows.py
"""
import datetime as dt, json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-card-rows-"))
env = {**os.environ, "ORCH_FLEET_STATE": str(tmp / "dashboard"), "ORCH_BRIEF_LAST": str(tmp / "brief-last"),
       "ORCH_FLEET_CONFIG": str(tmp / "none.json"), "AGENT_SKILLS_STATE": str(tmp / "state"), "TZ": "UTC",
       "AGENT_SKILLS_CONFIG": str(tmp / "none.json"), "PROGRAMS_HOME": str(tmp / "programs"),
       "ORCA_TERMINAL_HANDLE": "term_root"}
os.environ.update(env)
sys.path.insert(0, str(HERE))
import board, fleet, tidy  # noqa: E402

now = fleet.utcnow()
sd = fleet.state_dir()
sd.mkdir(parents=True, exist_ok=True)
ago = lambda h: fleet.iso(now - dt.timedelta(hours=h))  # noqa: E731


def sess(sid, kind, phase, **kw):
    return {"id": sid, "name": sid, "kind": kind, "phase": phase, "parent": "root", "orca_parent": None,
            "task_title": sid, "gh_repo": "acme/app", "project": "app", "terminals": [{"handle": f"term_{sid}"}],
            "last_activity": ago(0.1), "report": None, "dispatch": None, "run": None, **kw}


def detail(key, state):
    n = int(key.rsplit("#", 1)[1])
    return {"probed_at": fleet.iso(now), "detail": {
        "key": key, "url": f"https://example.test/{n}", "state": state, "merged_at": ago(3) if state == "MERGED" else None,
        "merge_sha": f"{n:02d}c0ffee00" if state == "MERGED" else None, "draft": False, "base": "main", "reviewers": [],
        "decision": None, "ci": "success", "merge_state": "CLEAN", "created": ago(30), "updated": ago(1)}}


def render(*extra):
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), "brief", "render", "--json", "--no-refresh", *extra],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, (r.stdout, r.stderr)
    return json.loads(r.stdout)


def write_state(sessions, prs=(), sources=None, at=None, root_term=None):
    fleet.write_atomic(sd / "state.json", json.dumps({
        "generated_at": at or fleet.iso(now), "root": "root", "sources": sources or {}, "prs": list(prs),
        "sessions": [{**sess("root", "orchestrator", "working"), "parent": None,
                      "terminals": [root_term or {"handle": "term_root"}]}] + sessions}))


# ---- the current-PR rule, on its own
merged1 = ("acme/app#1", "MERGED")
by = {"busy": [("acme/app#2", "OPEN"), merged1]}
assert fleet.current_pr(sess("busy", "standalone", "working"), by) == "acme/app#2"
assert fleet.current_pr(sess("busy", "standalone", "working", linked_pr={"key": "acme/app#1", "state": "merged"}),
                        {}) is None  # working on what comes after PR 1: no PR yet, not PR 1
assert fleet.current_pr(sess("busy", "standalone", "idle", linked_pr={"key": "acme/app#1", "state": "merged"}),
                        {}) == "acme/app#1"  # an idle card follows its newest PR
assert fleet.current_pr(sess("busy", "standalone", "offline"), by) == "acme/app#2"
# prs.json's detail outranks the state the collector saw; a board row's --pr is the last candidate.
assert fleet.current_pr(sess("busy", "standalone", "waiting"), by, {"acme/app#2": {"state": "MERGED"}}) is None
assert fleet.current_pr(sess("busy", "standalone", "working"), {}, extra=["acme/app#9"]) == "acme/app#9"

# ---- which sessions are card rows, and the "PR 1 merged" card
fleet.write_atomic(sd / "prs.json", json.dumps({"prs": dict([("acme/app#1", detail("acme/app#1", "MERGED")),
                                                              ("acme/app#3", detail("acme/app#3", "MERGED"))])}))
write_state([
    # A /goal card `skills-sync card` just started: Orca records the orchestrator as its parent, and it has no report,
    # PR or dispatch yet.
    sess("goal-card", "standalone", "working", orca_parent="root"),
    # A folder the collector only hung under the root (no parent in Orca): not a card.
    sess("resident", "standalone", "working"),
    # Its PR 1 merged while it works on PR 2: a working row, not ✅.
    sess("two-prs", "standalone", "working", orca_parent="root", linked_pr={"key": "acme/app#1", "state": "merged"}),
    # Done and idle: ✅ on its merged PR.
    sess("finished", "standalone", "idle", orca_parent="root", linked_pr={"key": "acme/app#3", "state": "merged"}),
    # No parent in Orca, but it had a PR: still a card once that PR merged and it works on.
    sess("pr-only", "standalone", "working", linked_pr={"key": "acme/app#1", "state": "merged"}),
])
out = render()
text, snap = out["text"], out["snapshot"]["rows"]
assert "fleet:goal-card" in snap and snap["fleet:goal-card"]["status"] == "moving", snap
assert "fleet:resident" not in snap, snap
assert snap["fleet:two-prs"]["status"] == "moving" and snap["fleet:two-prs"]["pr"] == "", snap
assert snap["fleet:finished"]["status"] == "done", snap
assert snap["fleet:pr-only"]["status"] == "moving" and snap["fleet:pr-only"]["pr"] == "", snap
# tidy reads the same rule: no card PR to close the working card's doing item on.
cards = tidy.cards_of(fleet.read_json(sd / "state.json"), {"acme/app#1": {"state": "MERGED"}})
assert cards["two-prs"]["pr"] is None and cards["finished"]["pr"] == "acme/app#3", cards
# Nothing working is missing from the report, so the headline has no missing count.
assert not any(f.startswith("보고서에 없는 세션") for f in out["headline_facts"]), out["headline_facts"]
# A board row that names the card is the card's row, with or without a current PR: no second row for the card.
board.set_row("ABC-1", title="two-prs 다음 단계", status="moving", next="작업 중", repo="acme/app")
snap = render()["snapshot"]["rows"]
assert "fleet:two-prs" not in snap and snap["ABC-1"]["pr"] == "", snap
board.drop("ABC-1")

# ---- the missing-session headline
write_state([
    sess("lead-x", "orchestration", "working", orca_parent="root", run="run_x"),
    sess("worker-x", "task", "working", parent="lead-x", dispatch="ctx_x", dispatch_status="completed"),
    sess("resident", "standalone", "working"),
])
facts = render()["headline_facts"]
# The lead has its own row; its worker's completed dispatch with no PR leaves the table, but it still works.
assert "보고서에 없는 세션 1 (worker-x)" in facts, facts
# A doing item naming it would stand for it, but not once that item is closed: the report no longer lists it.
board.doing_add("worker-x 후속 정리", "card", "확인")
assert not any(f.startswith("보고서에 없는 세션") for f in render()["headline_facts"])
iid = tidy.item_id("doing", fleet.read_json(sd / "board.json")["doing"][0])
assert tidy.write([iid], can={iid: "worker-x 후속 정리"})[0] == [iid]
assert "보고서에 없는 세션 1 (worker-x)" in render()["headline_facts"]
board.doing_drop(1)
# A source that failed, or an old state.json, says the check did not run instead of counting.
write_state([sess("worker-x", "task", "working", dispatch="ctx_x", dispatch_status="completed")],
            sources={"runs": {"error": "orca: timed out"}})
facts = render()["headline_facts"]
assert "누락 검사 안 함: runs 읽기 실패" in facts and not any("보고서에 없는" in f for f in facts), facts
write_state([sess("worker-x", "task", "working", dispatch="ctx_x", dispatch_status="completed")], at=ago(2))
facts = render()["headline_facts"]
assert "누락 검사 안 함: 세션 목록 2h 전 기준" in facts, facts

# ---- decisions: stale after decision_stale_h from max(created_at, checked_at), and with no route back
usable_root = {"handle": "term_root", "agent": "claude", "connected": True, "writable": True}
write_state([], root_term=usable_root)
# With no DB the decisions work from decisions.json, so nothing waits on `orch db import`. A DB that is there and not
# imported does wait: the brief says so, and still renders.
import store  # noqa: E402
assert not any(f.startswith("이행 대기") for f in render()["headline_facts"])
store.path().touch()
assert any(f.startswith("이행 대기: 결정 DB") for f in render()["headline_facts"])
store.path().unlink()
store.import_apply(checks=False)
assert not any(f.startswith("이행 대기") for f in render()["headline_facts"])
d_old = fleet.decision_add("오래된 질문", handle="term_root")
d_gone = fleet.decision_add("떠난 터미널의 질문", handle="term_gone")


def backdate(did, **fields):
    fleet.change_decisions(lambda st: st["decisions"][did].update(fields))


backdate(d_old["id"], created_at=ago(13))
facts = render()["headline_facts"]
assert f"오래된 결정 1 ({d_old['id']} · `orch decide keep|drop`)" in facts, facts
# The registering terminal is gone, so the relay sends to the root's writable agent terminal: rerouted, not lost.
assert f"등록 터미널 없음 1 ({d_gone['id']} · 답은 오케스트레이터로)" in facts, facts
assert not any(f.startswith("응답 경로 없음") for f in facts), facts
# The root's agent exited: the line typed there is refused, so neither has a route, even the one whose terminal is
# still listed.
write_state([])
facts = render()["headline_facts"]
assert f"응답 경로 없음 2 ({d_old['id']}, {d_gone['id']})" in facts, facts
assert not any(f.startswith("등록 터미널 없음") for f in facts), facts
# One added outside Orca has no handle: it goes to the root's agent terminal, so with none it has no route either.
d_bare = fleet.decision_add("Orca 밖에서 연 질문")
assert f"응답 경로 없음 3 ({d_old['id']}, {d_gone['id']}, {d_bare['id']})" in render()["headline_facts"]
fleet.decision_close(d_bare["id"], "dropped")
fleet.change_decisions(lambda st: st["decisions"][d_bare["id"]].update(closed_at=ago(8 * 24)))
r = subprocess.run([sys.executable, str(HERE / "prog.py"), "decide", "keep", d_old["id"]], capture_output=True, text=True,
                   env=env)
assert r.returncode == 0 and r.stdout.startswith(f"{d_old['id']} checked "), (r.stdout, r.stderr)
kept = {d["id"]: d for d in store.decisions()}[d_old["id"]]
assert kept["status"] == "open" and kept["checked_at"] >= fleet.iso(now), kept  # keep closes nothing
assert not any(f.startswith("오래된 결정") for f in render()["headline_facts"])
r = subprocess.run([sys.executable, str(HERE / "prog.py"), "decide", "keep", "d999"], capture_output=True, text=True,
                   env=env)
assert r.returncode != 0 and "no decision d999" in r.stderr, r.stderr
# Closed decisions stay in the table, however old: readers filter on status (no 7-day delete since PR 4a).
fleet.decision_close(d_gone["id"], "dropped")
backdate(d_gone["id"], closed_at=ago(8 * 24))
fleet.decision_close(d_old["id"], "done", "yes")
assert {d["id"] for d in store.decisions()} == {d_old["id"], d_gone["id"], d_bare["id"]}
assert fleet.open_decisions() == []
# A legacy decisions.json back where the tombstone was: old code wrote after the cutover.
(sd / "decisions.json").rmdir()
(sd / "decisions.json").write_text("{}")
assert "옛 코드가 쓴 흔적 decisions.json (a file again)" in render()["headline_facts"]
(sd / "decisions.json").unlink()
(sd / "decisions.json").mkdir()

# ---- a `run:` card's milestone comes from Orca's inbox
brief = tmp / "brief.md"
brief.write_text("GOAL  something\nREPORT  `orca orchestration send --to run:run_lead1 --type status`\n")
card = sess("goal-card", "standalone", "working", terminals=[{"handle": "term_goal", "pane": "tab1:leaf1"}])
msgs = [
    {"id": "m1", "type": "status", "from_handle": "term_goal", "to_handle": "run:run_lead1", "subject": "착수",
     "body": "", "created_at": "2026-10-13T10:00:00Z"},
    {"id": "m2", "type": "status", "from_handle": "term_old", "sender_pane_key": "tab1:leaf1", "to_handle": "term_coord",
     "subject": "PR 열림", "body": "acme/app#5 리뷰 대기\n둘째 줄", "created_at": "2026-10-13T11:00:00Z"},
    {"id": "m3", "type": "heartbeat", "from_handle": "term_goal", "to_handle": "run:run_lead1", "subject": "alive",
     "body": "", "created_at": "2026-10-13T12:00:00Z"},
    {"id": "m4", "type": "question", "from_handle": "term_goal", "to_handle": "run:run_lead1", "subject": "질문",
     "body": "", "created_at": "2026-10-13T12:30:00Z"},
    {"id": "m5", "type": "status", "from_handle": "term_goal", "to_handle": "run:run_other", "subject": "딴 곳",
     "body": "", "created_at": "2026-10-13T13:00:00Z"},
]
assert fleet.report_target(str(brief)) == ("run", "run_lead1") and fleet.report_path(str(brief)) is None
rep = fleet.report_of(str(brief), card, msgs, {"run_lead1": "term_coord"})
assert rep == {"source": "inbox", "id": "m2", "at": "2026-10-13T11:00:00Z", "type": "status",
               "next": "PR 열림 · acme/app#5 리뷰 대기", "pr": "acme/app#5"}, rep
assert fleet.report_of(str(brief), card, msgs[:1] + msgs[2:], {})["id"] == "m1"
assert fleet.report_of(str(brief), card, [], {}) is None
# A card whose comment names no brief reads the Runs its parent coordinates, never another Run (an order sent down).
assert fleet.report_of("", card, msgs, {"run_lead1": "term_coord"}, ["run_lead1"])["id"] == "m2"
assert fleet.report_of("", card, msgs, {}, ["run_other"])["id"] == "m5"
assert fleet.report_of("", card, msgs, {}, []) is None
# A report file keeps reading the file, and says so.
(tmp / "rep.md").write_text("- 2026-10-13 착수\n")
brief.write_text(f"REPORT  {tmp / 'rep.md'}\n")
assert fleet.report_of(str(brief), card, msgs, {})["source"] == "file"

print("test_card_rows: ok")
