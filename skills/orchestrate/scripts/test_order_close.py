"""Offline tests for the automatic close of owner orders (SSOT 5c): the shadow records `would_close` and changes nothing,
the six boundary cases stay open, the key (default off) turns the same judgement into a close, a Reopen is not undone on
the same evidence, and `orch order shadow` sets the candidates against what the sessions did. Temporary state directories,
invented data, no GitHub: the PR lookup is a stub.

Run: python3 test_order_close.py
"""
import datetime as dt, json, os, pathlib, sqlite3, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-order-close-"))
cfg = tmp / "agent-skills.json"
env = {**os.environ, "ORCH_FLEET_STATE": str(tmp / "dashboard"), "ORCH_BRIEF_LAST": str(tmp / "brief-last"),
       "ORCH_FLEET_CONFIG": str(tmp / "none.json"), "AGENT_SKILLS_STATE": str(tmp / "state"), "TZ": "UTC",
       "AGENT_SKILLS_CONFIG": str(cfg), "PROGRAMS_HOME": str(tmp / "programs"), "HOME": str(tmp / "home"),
       "ORCA_TERMINAL_HANDLE": "term_root"}
os.environ.update(env)
sys.path.insert(0, str(HERE))
import board, fleet, order_close, store, tidy  # noqa: E402


def _legacy_set_row(rid, **fields):
    """Rows and doing lines as written before A-ANNOT: set_row refuses a new row for a card and doing add has no --force."""
    return board.change(lambda s: board.put(s, rid, fields))


def _legacy_doing(title, owner, next_):
    return board.change(lambda s: s["doing"].append({"title": title, "owner": owner, "next": next_}))


board.set_row = _legacy_set_row

store.import_apply(checks=False)
store.order_setup()
now = fleet.utcnow()
sd = fleet.state_dir()
ago = lambda h: fleet.iso(now - dt.timedelta(hours=h))  # noqa: E731


def orch(*args):
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), *args], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def pr(key, state, sha="abc1234def"):
    repo, n = key.split("#")
    return {"probed_at": fleet.iso(now), "detail": {"key": key, "repo": repo, "number": int(n), "state": state,
            "merged_at": ago(3) if state == "MERGED" else None, "merge_sha": sha if state == "MERGED" else None}}


def card(sid, phase):
    return {"id": sid, "name": sid, "kind": "standalone", "phase": phase, "parent": "s-coord", "task_title": sid,
            "gh_repo": "acme/app", "terminals": [{"handle": f"term_{sid}"}], "report": None}


PRS = {"acme/app#1": pr("acme/app#1", "MERGED"), "acme/app#2": pr("acme/app#2", "MERGED", "bbb2222"),
       "acme/app#3": pr("acme/app#3", "OPEN"), "acme/app#4": pr("acme/app#4", "CLOSED"),
       "acme/app#5": pr("acme/app#5", "MERGED", "ccc5555")}
SESSIONS = [card("s-done", "offline"), card("s-busy", "working"), card("s-post", "offline"), card("s-nopr", "offline")]
OWNER_OF = {"acme/app#1": "s-done", "acme/app#2": "s-post", "acme/app#3": "s-busy"}


def write_state(generated=None, sessions=SESSIONS, me="owner-me"):
    fleet.write_atomic(sd / "prs.json", json.dumps({"prs": PRS, "me": me}))
    fleet.write_atomic(sd / "state.json", json.dumps({"generated_at": generated or fleet.iso(now), "sessions": sessions, "prs": [
        {"key": k, "session": OWNER_OF.get(k), "state": v["detail"]["state"]} for k, v in PRS.items()]}))


sd.mkdir(parents=True, exist_ok=True)
write_state()
_legacy_doing("릴리스 확인", "card", "머지 → 릴리스")
board.set_row("post", title="post", repo="acme/app", status="moving", next="머지 → 릴리스", worktree="s-post")

# Units: a done board item, an open one, a dropped one; an answered decision, an open one, a withdrawn one; agents.
board.later_add("끝난 일", "조건 가")
board.later_add("열린 일", "조건 나")
board.later_add("버린 일", "조건 다")
ids = [i["id"] for i in store.board()["later"]]
done_b, open_b, drop_b = ids[-3:]
tidy.write([done_b], can={done_b: "끝난 일"})
db = sqlite3.connect(str(store.path()))
for did, st in (("d1", "done"), ("d2", "open"), ("d3", "dropped")):
    db.execute("INSERT INTO decision (id, status, title, created_at, closed_at, answer) VALUES (?,?,?,?,?,?)",
               (did, st, "고르기", fleet.iso(now), None if st == "open" else fleet.iso(now), "가" if st == "done" else None))
db.execute("UPDATE item SET status='dropped', closed_at=? WHERE id=?", (fleet.iso(now), drop_b))
db.commit()
db.close()
for ev, aid in (("start", "ag-ok"), ("stop", "ag-ok"), ("start", "ag-run")):
    fleet.append_line(sd / fleet.SUBAGENTS, {"v": 1, "event": ev, "at": fleet.iso(now), "agent": aid, "claude_session": None,
                                              "type": None, "handle": None, "cwd": None, "desc": None})

ASKED, GH = [], {}


def gh(key):
    ASKED.append(key)
    return GH.get(key)


def make(text, links, *, confirm=True, kind="order", owner=False, stand_in=()):
    row, _ = store.order_add(text, kind, None, owner, "term_root")
    for u in links:
        store.order_link(row["id"], u, "term_root")
    for u in stand_in:
        store.order_link(row["id"], u, "term_root", stand_in=True)
    if confirm:
        store.order_links_complete(row["id"], "term_root")
    return row["id"]


def tick():
    state, prs_file = order_close.load()
    return order_close.tick(state, prs_file, store.board(), fleet.utcnow(), merged_fn=gh)


def events(oid, ev=None):
    with store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM event WHERE entity='order' AND id=? ORDER BY seq", (oid,)) if ev in (None, r["ev"])]


def row(oid):
    return store.order_get(oid)


def why(oid):
    o = row(oid)
    state, prs_file = order_close.load()
    with store.read() as c:
        F = order_close.gather(c, state, prs_file, store.board(), fleet.utcnow(), merged_fn=gh)
        return order_close.judge(o, F)


# ---- the candidate: every unit ended, scope confirmed, nothing for the owner
good = make("로그인 개편", ["pr:acme/app#1", "card:s-done", f"board:{done_b}", "decision:d1", "agent:ag-ok"])
v = why(good)
assert v["close"] and v["units"]["pr:acme/app#1"] == {"pr": "acme/app#1", "merge": "abc1234def"}, v
assert v["units"]["card:s-done"] == {"card": "s-done", "pr": "acme/app#1", "merge": "abc1234def"}, v
assert v["units"]["decision:d1"] == {"decision": "d1", "status": "answered"} and v["units"]["agent:ag-ok"]["ended"] == "stopped", v
assert v["units"][f"board:{done_b}"]["closed_event"] > 0 and json.loads(v["snapshot"]) == v["units"], v

# ---- six boundary cases and the rest that hold an order open (each is judged, none is closed)
cases = {
    "partial": make("일부만", ["pr:acme/app#1", "pr:acme/app#3"]),                          # 1 one unit not ended
    "closed_pr": make("머지 없이 닫힌 PR", ["pr:acme/app#4"]),                              # 2 closed, never merged
    "unread": make("읽지 못함", ["pr:acme/app#1", "card:s-archived"]),                      # 3 the card is not in the list
    "scope": make("범위 미확인", ["pr:acme/app#1"], confirm=False),                          # 4 nobody said all units are linked
    "owner": make("오너 행동", ["pr:acme/app#1"], owner=True),                               # 5 --owner-action
    "stand_in": make("stand-in만", [], confirm=False, stand_in=["pr:acme/app#1"]),                       # 6 only a stand-in link
}
want = {"partial": "partial", "closed_pr": "partial", "unread": "unread", "scope": "scope_unconfirmed",
        "owner": "owner_action", "stand_in": "no_link"}
for name, oid in cases.items():
    got = why(oid)
    assert not got["close"] and got["why"] == want[name], (name, got)
assert "closed without a merge" in why(cases["closed_pr"])["detail"] and "s-archived" in why(cases["unread"])["detail"]
extra = {
    "busy_card": (make("카드가 일하는 중", ["card:s-busy"]), "partial"),
    "post_merge": (make("머지 뒤 단계", ["card:s-post"]), "partial"),
    "open_board": (make("열린 보드", [f"board:{open_b}"]), "partial"),
    "dropped_board": (make("버린 보드", [f"board:{drop_b}"]), "partial"),
    "open_decision": (make("열린 결정", ["decision:d2"]), "partial"),
    "withdrawn": (make("철회된 결정", ["decision:d3"]), "partial"),
    "running_agent": (make("도는 서브에이전트", ["agent:ag-run"]), "partial"),
    "no_ledger": (make("원장에 없는 서브에이전트", ["agent:ag-gone"]), "unread"),
    "no_pr_card": (make("PR 없는 카드", ["card:s-nopr"]), "partial"),
    "unseen_pr": (make("수집기가 모르는 PR", ["pr:acme/app#99"]), "unread"),
    "feedback": (make("피드백", [f"board:{done_b}"], kind="feedback"), "feedback_needs_pr"),
}
for name, (oid, code) in extra.items():
    got = why(oid)
    assert not got["close"] and got["why"] == code, (name, got)
assert ASKED and set(ASKED) == {"acme/app#99"}, "GitHub is asked only about a PR the collector has not seen"
# An owner-turn inbox item at a linked card, and an unreadable inbox, hold the order.
turn = make("오너 차례", ["pr:acme/app#1", "card:s-done"])
fleet.inbox_add("approval", "승인 필요", session="s-done", key="k-turn")
assert why(turn)["why"] == "owner_turn" and why(good)["why"] == "owner_turn", "every order that links the card"
assert why(cases["partial"])["why"] == "partial", "an order that links another card is not held by it"
fleet.inbox_resolve("k-turn")
assert why(turn)["close"] and why(good)["close"]
# A session list that is old says nothing about who is working.
write_state(generated=ago(1))
assert why(good)["why"] == "unread" and "stale" in why(good)["detail"]
write_state()

# ---- shadow (the default): a would_close event once per evidence, every order stays open
assert not order_close.enabled()
cands = {o["id"] for o in tick()}
assert cands == {good, turn}, cands
assert tick() == [] and len(events(good, "would_close")) == 1, "the same evidence is not recorded again"
ev = events(good, "would_close")[0]
assert json.loads(ev["after"]) == {"rule": "order_units_done", "snapshot": why(good)["snapshot"], "units": why(good)["units"]}, ev
assert all(row(o)["status"] == "open" for o in (good, turn, *cases.values())) and not events(good, "done")
assert tidy.run(now=now) is not None and len(events(good, "would_close")) == 1, "tidy's own pass runs it too and finds nothing new"
assert not any(events(oid, "would_close") for name, oid in cases.items()), "a boundary case leaves no candidate"
# A new real link takes the receiving session's word back; a stand-in link does not.
store.order_link(turn, "pr:acme/app#2", "term_root", stand_in=True)
assert row(turn)["links_complete_at"]
store.order_link(turn, "pr:acme/app#2", "term_root")
assert row(turn)["links_complete_at"] is None and events(turn, "links_open")
assert not why(turn)["close"] and why(turn)["why"] == "scope_unconfirmed"
store.order_links_complete(turn, "term_root")

# ---- a dry tidy writes nothing
n = len(events(turn))
assert subprocess.run([sys.executable, str(HERE / "prog.py"), "board", "tidy", "--dry-run"], env=env, capture_output=True).returncode == 0
assert len(events(turn)) == n

# ---- the comparison with what the sessions did
store.order_close(good, "done", "term_root")                                     # agrees with the candidate
store.order_close(cases["owner"], "done", "term_root")                           # hand-closed, never a candidate: missed
reopened = make("다시 열림", ["pr:acme/app#1"])
tick()
store.order_close(reopened, "done", "term_root")
store.order_reopen(reopened, "term_root")                                        # a close that did not hold
dropped = make("거둠", ["pr:acme/app#2"])
tick()
store.order_close(dropped, "dropped", "term_root", reason="not needed")          # dropped what shadow would call done
lines, wrong = order_close.compare(merged_fn=gh)
text = "\n".join(lines)
for needle in (f"{good:<5} agree", f"{turn:<5} pending", f"{reopened:<5} reopened", f"{dropped:<5} differs",
               f"{cases['owner']:<5} missed", "owner_action"):
    assert needle in text, (needle, text)
assert lines[0].startswith("shadow: ") and wrong == 2, (lines, wrong)
code, out, err = orch("order", "shadow")
assert code == 1 and out == text, (code, out, err)

# ---- the key on: the same judgement closes, once, with the snapshot; a Reopen holds it until the evidence changes
cfg.write_text(json.dumps({"orchestrate": {"order_auto_close": True}}))
assert order_close.enabled()
auto = make("자동으로 닫힘", ["pr:acme/app#5"])
shadowed = {o["id"] for o in tick()}
assert auto in shadowed and row(auto)["status"] == "done" and row(auto)["close_by"] == "auto"
(done_ev,) = events(auto, "done")
assert json.loads(done_ev["after"])["snapshot"] == json.dumps({"pr:acme/app#5": {"pr": "acme/app#5", "merge": "ccc5555"}}, sort_keys=True, separators=(",", ":"))
assert tick() == [] and len(events(auto, "done")) == 1
assert row(cases["partial"])["status"] == "open" and row(cases["scope"])["status"] == "open", "the boundary cases still stay"
store.order_reopen(auto, "term_root")
assert row(auto)["reopen_evidence"] == json.loads(done_ev["after"])["snapshot"], "the snapshot moves to reopen_evidence"
store.order_links_complete(auto, "term_root")
assert why(auto)["why"] == "same_evidence" and auto not in {o["id"] for o in tick()} and row(auto)["status"] == "open"
PRS["acme/app#6"] = pr("acme/app#6", "MERGED", "ddd6666")
write_state()
store.order_link(auto, "pr:acme/app#6", "term_root")
store.order_links_complete(auto, "term_root")
assert auto in {o["id"] for o in tick()} and row(auto)["status"] == "done", "new evidence closes it again"
# A close by hand has no snapshot: the CLI's reopen reads the units as they are, so tidy waits for a change too.
hand = make("손으로 닫음", ["pr:acme/app#1"])
cfg.write_text("{}")
store.order_close(hand, "done", "term_root")
code, out, err = orch("order", "reopen", hand)
assert code == 0 and out == f"{hand} open", (out, err)
assert json.loads(row(hand)["reopen_evidence"]) == {"pr:acme/app#1": {"pr": "acme/app#1", "merge": "abc1234def"}}
store.order_links_complete(hand, "term_root")
assert why(hand)["why"] == "same_evidence"
# ---- review round 1: what the first version let through
cfg.write_text("{}")
for n, sha in ((7, "eee7777"), (8, "fff8888"), (9, "ggg9999"), (10, "hhh1010")):
    PRS[f"acme/app#{n}"] = pr(f"acme/app#{n}", "MERGED", sha)
    OWNER_OF[f"acme/app#{n}"] = f"s-r{n}"
MORE = SESSIONS + [card(f"s-r{n}", "offline") for n in (7, 8, 9, 10)]
write_state(sessions=MORE)
# A board line about the card that names it but has no --worktree, and a doing item that does, still hold it.
r_row = make("이름으로만 이은 행", ["card:s-r7"])
assert why(r_row)["close"]
board.set_row("s-r7", title="행", repo="acme/app", status="moving", next="머지 → 배포")
assert why(r_row)["why"] == "partial" and "step after the merge" in why(r_row)["detail"]
r_doing = make("doing 항목", ["card:s-r8"])
assert why(r_doing)["close"]
_legacy_doing("s-r8 마무리", "card", "머지 → 릴리스")
assert why(r_doing)["why"] == "partial"
# Something waits on the owner at a linked card that the order does not name as a unit.
r_dec = make("오너 결정", ["card:s-r9"])
assert why(r_dec)["close"]
db = sqlite3.connect(str(store.path()))
db.execute("INSERT INTO decision (id, status, title, handle, created_at) VALUES ('d9', 'open', '고르기', 'term_s-r9', ?)", (fleet.iso(now),))
db.commit()
db.close()
assert why(r_dec)["why"] == "owner_turn" and "decision" in why(r_dec)["detail"]
r_own = make("오너의 열린 항목", ["card:s-r10"])
assert why(r_own)["close"]
_legacy_doing("s-r10 확인", "오너", "확인")
assert why(r_own)["why"] == "owner_turn" and "open doing item" in why(r_own)["detail"], why(r_own)
write_state(sessions=MORE, me=None)
assert why(r_own)["why"] == "owner_unread", "without the owner's login nothing says the owner has no turn"
write_state(sessions=MORE)
# An inbox or a subagent ledger that cannot be read is not an empty one.
if os.geteuid() != 0:
    inbox, ledger = sd / "inbox-external.jsonl", sd / fleet.SUBAGENTS
    fleet.inbox_add("approval", "읽을 수 없는 칸", session="s-nowhere", key="k-unread")
    inbox.chmod(0)
    assert why(r_doing)["why"] == "owner_unread", why(r_doing)
    inbox.chmod(0o600)
    fleet.inbox_resolve("k-unread")
    agent_order = make("서브에이전트", ["agent:ag-ok"])
    assert why(agent_order)["close"]
    ledger.chmod(0)
    assert why(agent_order)["why"] == "unread" and "could not be read" in why(agent_order)["detail"]
    ledger.chmod(0o600)
    assert why(agent_order)["close"]
# The write transaction reads the units that live in the DB again: a board item reopened after it was judged.
race = make("판정 뒤 다시 열린 보드", [f"board:{done_b}"])
v = why(race)
assert v["close"]
db = sqlite3.connect(str(store.path()))
db.execute("UPDATE item SET status='todo', closed_at=NULL WHERE id=?", (done_b,))
db.commit()
assert store.order_auto(race, v["snapshot"], v["units"], v["links"], close=True, recheck=order_close.recheck(v)) is False
assert row(race)["status"] == "open" and not events(race, "done") and not events(race, "would_close")
db.execute("UPDATE item SET status='done', closed_at=? WHERE id=?", (fleet.iso(now), done_b))
db.commit()
db.close()
assert store.order_auto(race, v["snapshot"], v["units"], v["links"], close=False, recheck=order_close.recheck(v))
# The owner's turn is read again too: a decision opened from a linked card after the order was judged.
late = make("판정 뒤 열린 결정", ["pr:acme/app#1", "card:s-done"])
v = why(late)
assert v["close"]
db = sqlite3.connect(str(store.path()))
db.execute("INSERT INTO decision (id, status, title, handle, created_at) VALUES ('d10', 'open', '늦은 질문', 'term_s-done', ?)", (fleet.iso(now),))
db.commit()
assert store.order_auto(late, v["snapshot"], v["units"], v["links"], close=True, recheck=order_close.recheck(v)) is False
assert row(late)["status"] == "open"
db.execute("DELETE FROM decision WHERE id='d10'")
# A decision that points at a linked PR by its link, from another terminal, is the owner's turn as well.
db.execute("INSERT INTO decision (id, status, title, handle, link, created_at) VALUES ('d11', 'open', '링크로 이은 결정', 'term_other', "
           "'https://github.com/acme/app/pull/1', ?)", (fleet.iso(now),))
db.commit()
assert why(late)["why"] == "owner_turn", why(late)
db.execute("DELETE FROM decision WHERE id='d11'")
# An open board row the owner holds at a linked card (here a card with no PR and a done close).
db.commit()
db.close()
board.set_row("s-nopr", title="확인 대기", repo="acme/app", status="owner", next="확인", worktree="s-nopr", owner="오너")
store.change_board(lambda b: b)
with store.tx() as c:
    store.close_unit(c, {"id": "card:s-nopr", "ts": fleet.iso(now), "by": "auto", "rule": "manual", "evidence": "x", "label": "x"})
nopr = make("PR 없는 카드", ["card:s-nopr"])
assert why(nopr)["why"] == "owner_turn" and "board row" in why(nopr)["detail"], why(nopr)
# A step added after the merge between the judgement and the write: the transaction reads the board lines again.
PRS["acme/app#12"] = pr("acme/app#12", "MERGED", "lll1212")
OWNER_OF["acme/app#12"] = "s-r12"
MORE = MORE + [card("s-r12", "offline")]
write_state(sessions=MORE)
step = make("머지 뒤 단계가 늦게 붙음", ["card:s-r12"])
v = why(step)
assert v["close"]
board.set_row("s-r12", title="행", repo="acme/app", status="moving", next="머지 → 배포", worktree="s-r12")
assert store.order_auto(step, v["snapshot"], v["units"], v["links"], close=True, recheck=order_close.recheck(v)) is False
assert row(step)["status"] == "open" and why(step)["why"] == "partial"
# A finished row keeps its owner and its next step as a record: neither holds the order.
PRS["acme/app#13"] = pr("acme/app#13", "MERGED", "mmm1313")
OWNER_OF["acme/app#13"] = "s-r13"
MORE = MORE + [card("s-r13", "offline")]
write_state(sessions=MORE)
board.set_row("s-r13", title="끝난 행", repo="acme/app", status="done", next="머지 → 배포", worktree="s-r13", owner="오너")
finished = make("끝난 행만 남음", ["card:s-r13"])
assert why(finished)["close"], why(finished)
# A ledger line the reader skips after an agent's last run may be its restart: the older stop is not evidence.
ledger_path = sd / fleet.SUBAGENTS
with open(ledger_path, "a") as f:
    f.write('{"v":1,"event":"start","at":"' + fleet.iso(now) + '","agent":"ag-cut"\n')   # cut off mid-line
for ev in ("start", "stop"):
    fleet.append_line(ledger_path, {"v": 1, "event": ev, "at": fleet.iso(now), "agent": "ag-after", "claude_session": None,
                                    "type": None, "handle": None, "cwd": None, "desc": None})
cut = make("잘린 줄", ["agent:ag-ok"])
assert why(cut)["why"] == "unread" and "unreadable ledger line" in why(cut)["detail"], why(cut)
assert why(make("잘린 줄 뒤의 실행", ["agent:ag-after"]))["close"], "a run recorded after the bad line is certain"
# The ledger and the session list are read again in the write transaction: the agent resumed after it was judged.
resumed = make("재개된 서브에이전트", ["agent:ag-after"])
v = why(resumed)
assert v["close"]
fleet.append_line(ledger_path, {"v": 1, "event": "start", "at": fleet.iso(fleet.utcnow()), "agent": "ag-after", "claude_session": None,
                                "type": None, "handle": None, "cwd": None, "desc": None})
assert store.order_auto(resumed, v["snapshot"], v["units"], v["links"], close=True, recheck=order_close.recheck(v)) is False
assert row(resumed)["status"] == "open" and not events(resumed, "would_close")
fleet.append_line(ledger_path, {"v": 1, "event": "stop", "at": fleet.iso(fleet.utcnow()), "agent": "ag-after", "claude_session": None,
                                "type": None, "handle": None, "cwd": None, "desc": None})

flip = make("A B A", ["pr:acme/app#1"])
for snap in ("A", "B", "A", "B"):
    store.order_auto(flip, snap, {}, ["pr:acme/app#1"])
assert [json.loads(e["after"])["snapshot"] for e in events(flip, "would_close")] == ["A", "B"]
# A close and a reopen that could not read the units: the first readable pass is the baseline, not a close.
GH.pop("acme/app#98", None)
blind = make("읽지 못한 채 닫고 연다", ["pr:acme/app#98"])
store.order_close(blind, "done", "term_root")
store.order_reopen(blind, "term_root")
assert row(blind)["reopen_evidence"] == store.EVIDENCE_UNKNOWN
store.order_links_complete(blind, "term_root")
assert blind not in {o["id"] for o in tick()} and row(blind)["reopen_evidence"] == store.EVIDENCE_UNKNOWN, "still unread: nothing to compare"
GH["acme/app#98"] = "iii9898"
assert why(blind)["why"] == "baseline"
assert blind not in {o["id"] for o in tick()} and row(blind)["status"] == "open" and events(blind, "baseline")
assert not events(blind, "would_close")
assert json.loads(row(blind)["reopen_evidence"]) == {"pr:acme/app#98": {"pr": "acme/app#98", "merge": "iii9898"}}
assert why(blind)["why"] == "same_evidence"
# A close by hand keeps the units it saw, so a Reopen after them needs no second reading.
PRS["acme/app#97"] = pr("acme/app#97", "MERGED", "jjj9797")
write_state(sessions=MORE)
seen = make("본 채 닫는다", ["pr:acme/app#97"])
code, out, err = orch("order", "done", seen)
assert code == 0 and out == f"{seen} done", (out, err)
(closed_ev,) = events(seen, "done")
want = json.dumps({"pr:acme/app#97": {"pr": "acme/app#97", "merge": "jjj9797"}}, sort_keys=True, separators=(",", ":"))
assert json.loads(closed_ev["after"])["snapshot"] == want, closed_ev
code, out, err = orch("order", "reopen", seen)
assert code == 0 and row(seen)["reopen_evidence"] == want, (out, err)
# So does a drop: the evidence it stood on, not what the units say when it is reopened.
gone = make("거두는 지시", ["pr:acme/app#97"])
code, out, err = orch("order", "drop", gone)
assert code == 0 and out == f"{gone} dropped", (out, err)
assert json.loads(events(gone, "dropped")[0]["after"])["snapshot"] == want
# A link added while the close reads the units: the snapshot is of other links, so the close is refused.
import order  # noqa: E402
racing_for = make("읽는 사이 늘어난 연결", ["pr:acme/app#97"])
real_snapshot = order_close.snapshot_now


def racing(o, now=None):
    snap = real_snapshot(o, now)
    store.order_link(o["id"], "pr:acme/app#2", "term_root")
    return snap


order_close.snapshot_now = racing
try:
    order.close(racing_for, "done", None)
    raise AssertionError("closed on a snapshot of other links")
except ValueError as e:
    assert "links changed while closing" in str(e), e
finally:
    order_close.snapshot_now = real_snapshot
assert row(racing_for)["status"] == "open"

# A malformed key is off, not on.
for bad in ('{"orchestrate": {"order_auto_close": "yes"}}', "not json"):
    cfg.write_text(bad)
    assert not order_close.enabled()
print("ok")
