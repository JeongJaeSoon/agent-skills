"""Offline tests for rowstate.py: what each brief row waits on (🔄 🙋 ⏳ ⏸️ ❔), that every row shows once, and that
brief render, the dashboard and the stage table read that one judgment. Invented data.

The first part runs `orch brief render` end to end, so it also runs against a checkout from before rowstate.py.

Run: python3 test_rowstate.py
"""
import datetime as dt, json, os, pathlib, re, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-rowstate-"))
state = tmp / "dashboard"
env = {**os.environ, "ORCH_FLEET_STATE": str(state), "ORCH_BRIEF_LAST": str(tmp / "brief-last"),
       "ORCH_FLEET_CONFIG": str(tmp / "none.json"), "AGENT_SKILLS_STATE": str(tmp / "state"), "TZ": "UTC",
       "AGENT_SKILLS_CONFIG": str(tmp / "none.json"), "PROGRAMS_HOME": str(tmp / "programs")}
os.environ.update(env)
sys.path.insert(0, str(HERE))
NOW = "2026-10-13T14:30:00Z"


def orch(*args):
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), *args], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout.rstrip("\n"), r.stderr.strip()


def ok(*args):
    code, out, err = orch(*args)
    assert code == 0, (args, out, err)
    return out


def pr(key, **kw):
    n = key.rsplit("#", 1)[1]
    return key, {"probed_at": NOW, "detail": {
        "key": key, "url": f"https://example.test/{n}", "state": "OPEN", "merged_at": None, "draft": False, "ci": "success",
        "decision": None, "base": "main", "merge_state": "CLEAN", "reviewers": [], "created": "2026-10-13T10:00:00Z", **kw}}


# ---------------------------------------------------------------- end to end: the brief's rows
ok("db", "import", "--apply", "--rehearsal")
state.mkdir(parents=True, exist_ok=True)
(state / "prs.json").write_text(json.dumps({"me": "octo", "prs": dict([
    pr("octo/app#1"),                                                    # the owner's repository, ready: the agent merges
    pr("octo/app#2", ci="pending"),                                      # a decision names it
    pr("octo/app#3"),                                                    # the classifier refused the agent's merge
    pr("acme/lib#4", reviewers=[{"login": "rev-a", "requested": True}], decision="REVIEW_REQUIRED", merge_state="BLOCKED"),
    pr("octo/app#5"),                                                    # waits for #1 to merge first
    pr("octo/app#6"),                                                    # a held item names it
])}))
(state / "state.json").write_text(json.dumps({"generated_at": NOW, "sessions": [
    {"id": "wt-lead", "kind": "orchestration", "phase": "working", "name": "lead",
     "terminals": [{"handle": "term_0FAKEL", "agent": "claude", "connected": True, "writable": True}]}]}))
ok("board", "lead", "add", "L", "--handle", "term_0FAKEL", "--repo", "octo/app", "--worktree", "wt-lead")
ok("board", "set", "L/merge", "--title", "머지 대기", "--pr", "octo/app#1", "--status", "waiting", "--next", "머지 대기")
ok("board", "set", "L/dec", "--title", "결정 대기", "--pr", "octo/app#2", "--next", "구현")
ok("decide", "add", "--title", "캐시 정책", "--option", "TTL 1h", "--option", "TTL 1d", "--recommend", "1", "--link", "octo/app#2")
ok("board", "set", "L/cls", "--title", "거부된 머지", "--pr", "octo/app#3", "--owner", "오너", "--next", "classifier 거부: 오너가 머지")
ok("board", "set", "L/rev", "--title", "남 리뷰", "--repo", "acme/lib", "--pr", "acme/lib#4", "--next", "리뷰 대기")
code, _, err = orch("board", "set", "L/after", "--title", "선행 대기", "--pr", "octo/app#5", "--after", "#1")
assert code != 0 and "owner/repo#N" in err, err  # --after names the PR whole
ok("board", "set", "L/after", "--title", "선행 대기", "--pr", "octo/app#5", "--after", "octo/app#1")
ok("board", "set", "L/held", "--title", "보류 행", "--pr", "octo/app#6", "--next", "구현")
ok("board", "later", "add", "octo/app#6 재검토", "--trigger", "10/20 QA 뒤")
ok("board", "set", "L/gap", "--title", "빈 기록", "--status", "waiting")

out = json.loads(ok("brief", "render", "--periodic", "--now", NOW, "--json", "--no-refresh"))
text = out["text"]
status = {o["id"]: o["status"] for o in out["open"] if o["kind"] == "row"}
want = {"row:L/merge": "moving",   # (a) the owner's repository: green and unasked, so the agent merges it
        "row:L/dec": "owner",      # (b) a decision only the owner makes
        "row:L/cls": "owner",      # (b) a merge the classifier refused, recorded with --owner 오너
        "row:L/rev": "waiting",    # another person was asked for a review
        "row:L/after": "held",     # a preceding PR
        "row:L/held": "held",      # the held queue names its PR
        "row:L/gap": "check"}      # (c) the record does not say who it waits on: the agent's to fill
assert {k: status.get(k) for k in want} == want, {k: (status.get(k), v) for k, v in want.items() if status.get(k) != v}
row = {m[1]: m[0] for m in re.findall(r"^\| (\S*) \| └ (\S+ \S+)", text, re.M)}
assert row["머지 대기"] == "🔄" and row["결정 대기"] == "🙋" and row["남 리뷰"] == "⏳" and row["보류 행"] == "⏸️", row
# The agent's next step on a ready PR it merges, from the evidence: no program verdict here, so 리뷰·머지, never 머지.
assert "| 리뷰·머지 |" in text, text
# A ❔ says it is the agent's to fix, in the row's own 다음 cell.
assert "에이전트 조치 필요: 누구 대기인지 기록 없음" in text, text
assert "선행 octo/app#1 머지 대기 → 선행 대기" in text, text  # the 보류 list says what it waits on
# Every row once: in its table, where its 다음 cell points to the list that names it; the lists hold references only.
assert out["duplicates"] == [] and out["missing"] == [], (out["duplicates"], out["missing"])
for name in ("머지 대기", "결정 대기", "거부된 머지", "남 리뷰", "선행 대기", "보류 행", "gap 빈 기록"):
    assert sum(1 for l in text.splitlines() if l.startswith("| ") and f"└ {name} |" in l) == 1, (name, text)
assert "🔄 **진행 중**" not in text and "| ↓ 오너 |" in text and "| ↓ 대기 |" in text and "| ↓ 보류 |" in text, text
ok("board", "set", "L/after", "--after", "-")  # the record path takes the wait back
assert "after" not in json.loads((state / "board.json").read_text())["rows"]["L/after"]

# ---------------------------------------------------------------- the rules, on facts
import rowstate  # noqa: E402

T = dt.datetime(2026, 10, 13, 14, 30, tzinfo=dt.timezone.utc)


def facts(prs=None, me="octo", programs=None, pressure=None, sessions=(), holds=None, decisions=()):
    st = {"generated_at": NOW, "sessions": list(sessions), "items": [], "sources": {}}
    f = rowstate.gather(now=T, prs=prs or {}, me=me, shut={}, verdicts={}, holds=holds or {}, hold_why={}, leads={},
                        state=st, decisions=list(decisions), owner_items=[], programs=programs or {}, pressure=pressure or {},
                        merges=[])
    return f


READY = dict(pr("octo/app#1")[1]["detail"])
row = {"id": "r", "status": "moving", "next": "", "pr": "octo/app#1"}
# Unknown is not false: no owner login with a reviewer asked stops at ❔, whatever the later rules would say.
asked = {**READY, "reviewers": [{"login": "rev-a", "requested": True}]}
assert rowstate.judge(row, facts({"octo/app#1": asked}, me=None))["status"] == "check"
assert rowstate.judge(row, facts({}))["unknown"] == ["PR 상태 모름"]
# Not the owner's repository and no policy: who merges is unknown, so ❔, never a guessed 🔄 or 🙋.
other = {**READY, "key": "acme/app#1"}
assert rowstate.judge({**row, "pr": "acme/app#1"}, facts({"acme/app#1": other}))["next"].endswith("머지 주체 기록 없음")
# A team asked is another person's wait, never the owner's.
team = {**READY, "reviewers": [{"login": "team:dev", "requested": True, "team": True}], "merge_state": "BLOCKED"}
assert rowstate.judge(row, facts({"octo/app#1": team}))["status"] == "waiting"


def gate(evs, open_gates):
    progs = {"octo/app#1": [{"program": "p", "policy": "human-gate", "verdict": None, "gate": rowstate.gate_of(evs, open_gates)}]}
    return rowstate.judge(row, facts({"octo/app#1": READY}, programs=progs))


# A human-gate program: G1 held, its held item, the item closed, G2 opened (🙋), G2 approved (🔄). A gate missing from
# the open list is no answer (❔), and the held item, while it is open, holds the row (⏸️) whatever the gate says.
G1 = [{"ev": "gate_opened", "gate": "g1"}]
assert gate([], set())["status"] == "moving"                         # never opened: the agent opens it
assert gate(G1, {"g1"})["status"] == "owner"                         # waiting on the owner's answer
assert gate(G1, set())["status"] == "check"                          # answered, cancelled or unseen: read it
held = rowstate.judge(row, facts({"octo/app#1": READY}, holds={"octo/app#1": "다음 주"},
                                 programs={"octo/app#1": [{"program": "p", "policy": "human-gate", "verdict": None,
                                                           "gate": rowstate.gate_of(G1, set())}]}))
assert held["status"] == "held", held                                # G1 hold → held item: ⏸️ before ❔
G2 = G1 + [{"ev": "gate_opened", "gate": "g2"}]
assert gate(G2, {"g2"})["status"] == "owner"                         # held item closed, G2 open: 🙋
assert gate(G2 + [{"ev": "approved"}], set())["status"] == "moving"  # G2 approved: 🔄
# The pressure queue: a machine wait holds the row; one past its deadline, or failed, is the agent's to look at.
q = lambda st, late=False: {"wt": [rowstate.queue_state({"state": st, "what": "빌드", "deadline": T.timestamp() + (-60 if late else 600)}, T)]}  # noqa: E731
sess = [{"id": "wt", "phase": "working", "terminals": []}]
assert rowstate.judge(row, facts({"octo/app#1": READY}, pressure=q("waiting"), sessions=sess), sid="wt")["status"] == "held"
for st, late in (("waiting", True), ("retrying", True), ("failed", False)):
    got = rowstate.judge(row, facts({"octo/app#1": READY}, pressure=q(st, late), sessions=sess), sid="wt")
    assert got["status"] == "check" and got["next"].startswith(rowstate.AGENT_ACTION), (st, got)
# A row marked done whose PR closed unmerged is not done: GitHub outranks the board.
closed = {**READY, "state": "CLOSED"}
assert rowstate.judge({**row, "status": "done"}, facts({"octo/app#1": closed}))["status"] == "held"
# A fact a rule needs and cannot read stops at ❔, never a guessed 🔄 or ⏳.
lead = {"title": "L", "handle": "term_0FAKEL"}
stale = facts({"octo/app#1": READY})
stale["sessions_known"] = False
assert rowstate.judge(row, stale, lead=lead)["unknown"] == ["세션 목록 모름"]
assert rowstate.judge(row, facts({"octo/app#1": READY}, sessions=sess) | {"pressure_known": False}, sid="wt")["unknown"] == ["pressure 대기열 모름"]
assert rowstate.judge(row, facts({"octo/app#1": {"key": "octo/app#1", "state": "OPEN"}}))["unknown"] == ["PR 상세 모름"]
assert rowstate.judge({**row, "owner": "someone"}, facts({"octo/app#1": READY}, me=None))["unknown"] == ["오너 login 모름"]
# A draft nobody works on, by a session list that can be trusted, is the agent's to resume or hold.
draft = {**READY, "draft": True}
assert rowstate.judge(row, facts({"octo/app#1": draft}))["next"].startswith(f"{rowstate.AGENT_ACTION}: 초안")
# A program whose files cannot be read may own the PR: who merges it is unknown, not the owner-repository default.
unread = facts({"octo/app#1": READY}) | {"programs_unread": {"octo/app"}}
assert rowstate.judge(row, unread)["next"].endswith("프로그램 파일 못 읽음")
progs = tmp / "programs-broken" / "p"
progs.mkdir(parents=True)
(progs / "program.json").write_text(json.dumps({"repo": "octo/app", "merge_policy": "human-gate"}))
(progs / "ledger.jsonl").write_text("{not json\n")
seen = set()
assert rowstate.program_facts(home=progs.parent, unread=seen) == {} and seen == {"octo/app"}
# A program read that is not the only one on the repository does not decide who merges: one unread may hold its gate.
auto = {"octo/app#1": [{"program": "q", "policy": "autonomous", "verdict": None, "gate": "none"}]}
assert rowstate.judge(row, facts({"octo/app#1": READY}, programs=auto) | {"programs_unread": {"octo/app"}})["next"].endswith("프로그램 파일 못 읽음")
# A PR detail a rule does not read leaves the earlier rule's answer: no CI read, and --owner 오너 is still 🙋.
no_ci = {k: v for k, v in READY.items() if k != "ci"}
assert rowstate.judge({**row, "owner": "오너"}, facts({"octo/app#1": no_ci}))["status"] == "owner"
assert rowstate.judge(row, facts({"octo/app#1": no_ci}))["unknown"] == ["PR 상세 모름"]
# ... and a rule that cannot apply needs none: a draft asks no review, nor does a PR known to ask nobody.
held_row = {**row, "status": "held", "next": "대기"}
no_revs = {k: v for k, v in {**READY, "draft": True}.items() if k != "reviewers"}
assert rowstate.judge(held_row, facts({"octo/app#1": no_revs}))["status"] == "held"
no_draft = {k: v for k, v in READY.items() if k != "draft"}
assert rowstate.judge(held_row, facts({"octo/app#1": no_draft}))["status"] == "held"
assert rowstate.judge({**row, "owner": "rev-b"}, facts({"octo/app#1": no_ci}))["status"] == "waiting"
# A team asked needs no owner login to be another's wait; a person asked does.
assert rowstate.judge(row, facts({"octo/app#1": team}, me=None))["status"] == "waiting"
# A stale session list is no evidence an agent works on the draft.
card = {**row, "session": "wt"}
busy_stale = facts({"octo/app#1": draft}, sessions=[{"id": "wt", "phase": "working", "terminals": []}])
busy_stale["sessions_known"] = False
assert rowstate.judge(card, busy_stale)["unknown"] == ["세션 목록 모름"]
# A pressure queue file that does not parse is a queue not read, not an empty one.
sys.path.insert(0, str(HERE.parents[1] / "reap-resources" / "scripts"))
import pressure  # noqa: E402
real_q = pressure.QUEUE
pressure.QUEUE = tmp / "pressure-queue.json"
try:
    assert rowstate.pressure_facts({}, T) == {}
    pressure.QUEUE.write_text("{torn")
    assert rowstate.pressure_facts({}, T) is None
finally:
    pressure.QUEUE = real_q
# 지금 in the stage table only for a PR exactly one row links, even when two rows agree.
two = {"a": rowstate.RowState("held"), "b": rowstate.RowState("held"), "c": rowstate.RowState("owner")}
assert rowstate.by_pr(two, {"a": {"pr": "x#1"}, "b": {"pr": "x#1"}, "c": {"pr": "x#2"}}) == {"x#2": "owner"}
# Placement: an id in two places, or one expected nowhere.
assert rowstate.check_placement({"a": ["table:x", "list:moving"], "b": ["table:x", "ref:owner"]}, ["a", "b", "c"]) == (["a"], ["c"], [])

# ---------------------------------------------------------------- one judgment, every reader
# brief render, the dashboard's Brief and Stages and `orch stage show` all take their status from rowstate.judge():
# made to answer ⏸️ for every row, each of them shows ⏸️ and nothing else.
import board, stages  # noqa: E402

real = rowstate.judge
rowstate.judge = lambda r, f, lead=None, sid=None: rowstate.RowState("held", "고정")
try:
    snap = rowstate.snapshot(now=T)
    out = board.render(now=T, fill=True, periodic=False)
finally:
    rowstate.judge = real
assert {st["status"] for st in snap["rows"].values()} == {"held"} and set(snap["by_pr"].values()) == {"held"}, snap
icons = {x["status"] for t in out["table_rows"] for x in t["rows"] if x["status"] != "—"}
assert icons == {"⏸️"}, icons
stages.add("s1", "단계 행", pr="octo/app#5")
cur, _ = stages.current(stages.table(), T, 0, None, snap["by_pr"])
assert [r["now"] for r in cur["rows"] if r["id"] == "s1"] == ["held"], cur["rows"]
# A head and its sub-task judged apart (head 🔄, sub-task ⏳): the head shows its worst open sub-task, and the
# snapshot carries that same status, so the table and the stage table's 지금 agree; "own" keeps judge()'s.
rowstate.judge = lambda r, f, lead=None, sid=None: (
    rowstate.RowState("waiting", "x", who="rev", what="리뷰") if r["kind"] == "task" else rowstate.RowState("moving", "x"))
try:
    out = board.render(now=T, fill=True, periodic=False)
finally:
    rowstate.judge = real
shown_rows = [x for t in out["table_rows"] for x in t["rows"] if x["id"] in out["states"]]
assert all(x["status"] == rowstate.ICON[out["states"][x["id"]]["status"]] for x in shown_rows), shown_rows
rolled = [k for k, st in out["states"].items() if st.get("own") == "moving" and st["status"] == "waiting"]
assert rolled and any(x["id"] in rolled for x in shown_rows), out["states"]
assert all(out["now_by_pr"].get(r["pr"]) in (None, out["states"][k]["status"])
           for k, r in board.read()["rows"].items() if k in out["states"] and r.get("pr")), out["now_by_pr"]
# Placement counts the rows written, not the lines handed to the table: one the table lost is missing.
real_fit = board.table_width.fit
board.table_width.fit = lambda rows, *a, **kw: (lambda t, n: (t[:-1], n))(*real_fit(rows, *a, **kw))
try:
    lost = board.render(now=T, fill=True, periodic=False)
finally:
    board.table_width.fit = real_fit
assert lost["missing"], lost["missing"]
# ... and a row written in another's place (same count, same icon) is that other row twice, and this one missing.
board.table_width.fit = lambda rows, *a, **kw: (lambda t, n: (t[:-1] + [t[-2]], n))(*real_fit(rows, *a, **kw))
try:
    swapped = board.render(now=T, fill=True, periodic=False)
finally:
    board.table_width.fit = real_fit
assert swapped["duplicates"] and swapped["missing"], (swapped["duplicates"], swapped["missing"])
# A quiet stage row is kept while its 지금 has work left (⏸️ too), or while the brief's judgment could not be read.
old = {**stages.table(), "rows": [{**r, "created_at": "2026-01-01T00:00:00Z"} for r in stages.table()["rows"]]}
assert [r["id"] for r in stages.current(old, T, 60, None, {"octo/app#5": "held"})[0]["rows"]] == ["s1"]
assert stages.current(old, T, 60, None, {})[0]["rows"] == []
assert [r["id"] for r in stages.current(old, T, 60, None, {}, now_known=False)[0]["rows"]] == ["s1"]
# Who reads statuses decides none: the renderers name no rule of rowstate's.
for f in ("board.py", "stages.py", "dash.py", "../assets/dashboard/fleet.js"):
    src = (HERE / f).read_text()
    assert not re.search(r"\b(def blocker|merge_actor|reviewable|gate_of)\b", src), f
# 지금 in `orch stage show`: the brief's own status of the board row its PR links (L/after, its wait taken back: 🔄).
# It renders at the real time, so the session list must be fresh; a stale one would make every lead row ❔.
fresh = json.loads((state / "state.json").read_text()) | {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat()}
(state / "state.json").write_text(json.dumps(fresh))
shown = ok("stage", "show")
assert "| 지금 | 작업 |" in shown and "| 🔄 | 단계 행 | octo/app#5 |" in shown, shown
print("test_rowstate: ok")
