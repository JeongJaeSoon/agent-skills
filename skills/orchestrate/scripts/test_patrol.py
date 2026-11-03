"""patrol.py rules, its log and delivery, and the watcher-down signals in card_watch and fleet.
Run: python3 test_patrol.py"""
import datetime as dt, json, os, pathlib, sys, tempfile, time

tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["AGENT_SKILLS_STATE"] = str(tmp)
os.environ["ORCH_FLEET_STATE"] = str(tmp / "dashboard")
os.environ["AGENT_SKILLS_PATROL"] = "off"
(tmp / "dashboard").mkdir()
sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "reap-resources" / "scripts"))
import card_watch, fleet, patrol, reap, sent, tidy  # noqa: E402

NOW = 1_800_000_000.0
T = dt.datetime.fromtimestamp(NOW, dt.timezone.utc)


def ago(minutes):
    return fleet.iso(T - dt.timedelta(minutes=minutes))


def term(h, path, agent="claude", last=NOW * 1000):
    return {"handle": h, "worktreePath": path, "agentIdentity": agent, "connected": True, "lastOutputAt": last}


def idle(minutes, parked=False):
    return {"state": "idle", "since": ago(minutes), "parked": parked}


def facts(**over):
    base = {"now": NOW, "terminals": [], "waits": {}, "workers": [], "sessions": [], "prs": {}, "board": {}, "state": {},
            "supersedes": [], "level": "ok", "consumers": [], "tails": {}, "screens": {}, "watchers": {}}
    return {**base, **over}


def codes(fs):
    return sorted((f["code"], f["card"]) for f in fs)


# I: an ended turn with nothing armed, outside any Run; parked, recent, waiting on a human or a Run worker is not.
f = facts(terminals=[term("h1", "/w/a"), term("h2", "/w/b"), term("h3", "/w/c"), term("h4", "/w/d"), term("h5", "/w/e")],
          tails={"/w/a": (idle(40), None), "/w/b": (idle(40, parked=True), None), "/w/c": (idle(5), None),
                 "/w/d": (idle(40), None), "/w/e": (idle(40), None)},
          waits={"h4": {"kind": "permission"}},
          workers=[{"dispatchId": "d1", "resource": {"worktreeId": "x::/w/e"},
                    "projection": {"outcome": "in_progress", "liveness": {"verdict": "live"}}}])
found, v, memo = patrol.judge(f, {})
assert codes(found) == [("I", "a")], found
assert v["cards"] == {"/w/a": "idle", "/w/b": "working", "/w/c": "working", "/w/d": "waiting", "/w/e": "run"}, v["cards"]
assert v["hints"] == {"/w/a": "idle"} and v["failed"] == [], v
assert patrol.judge(f, {})[0][0]["id"] == found[0]["id"], "the same idle spell is the same finding"
bar = ["⏺ Waiting on the inbox.", "❯ ", "  ⏵⏵ auto mode on · 1 shell · ← 1 agent"]
f2 = {**f, "screens": {}, "read": lambda h: bar}
assert patrol.judge(f2, {})[0] == [], "a background shell still running is not idle, though the transcript lost it"
assert patrol.judge({**f, "screens": {}, "read": lambda h: ["❯ "]}, {})[0][0]["code"] == "I"
assert patrol.background(["✻ Crunched for 22s · done 7:47 PM · 1 shell still running"])

# X: Orca's exited verdict for a worker; for a card outside a Run, a transcript ending on an error with no agent left.
err = {"type": "assistant", "isApiErrorMessage": True, "timestamp": ago(30),
       "message": {"content": [{"type": "text", "text": "API Error: 500 overloaded"}]}}
f = facts(workers=[{"dispatchId": "d9", "agentTerminalHandle": "h9", "resource": {"worktreeId": "x::/w/w"},
                    "projection": {"outcome": "in_progress", "liveness": {"verdict": "exited"}}}],
          tails={"/w/x": ({"state": "working", "since": ago(30)}, err), "/w/y": (idle(90), {"type": "assistant", "timestamp": ago(90)}),
                 "/w/old": (None, {**err, "timestamp": ago(3000)})})
found, v, _ = patrol.judge(f, {})
assert codes(found) == [("X", "w"), ("X", "x")], found
assert v["cards"]["/w/w"] == "run" and v["cards"]["/w/x"] == "exited" and "/w/y" not in v["cards"]
assert v["hints"] == {"/w/w": "exited", "/w/x": "exited"}, v["hints"]
assert "API Error: 500 overloaded" in next(x for x in found if x["card"] == "x")["title"]

# S: a logged supersede 30+ min old whose card still has an agent; by handle or by card name.
f = facts(terminals=[term("h1", "/w/a"), term("h2", "/w/b")],
          supersedes=[{"ts": ago(45), "to": "h1", "kind": "supersede"}, {"ts": ago(40), "to": "b", "kind": "supersede"},
                      {"ts": ago(10), "to": "h2", "kind": "supersede"}])
found, v, _ = patrol.judge(f, {})
assert codes(found) == [("S", "a"), ("S", "b")], found
assert v["hints"] == {"/w/a": "superseded", "/w/b": "superseded"} and "/w/a" not in v["cards"], v

# A finding never takes a card out of run, waiting or working: those stay in cards, the finding goes to hints.
f = facts(terminals=[term("h1", "/w/a"), term("h2", "/w/b")], waits={"h1": {"kind": "permission"}},
          tails={"/w/a": (idle(40), None), "/w/b": ({"state": "working", "since": ago(1)}, None)},
          supersedes=[{"ts": ago(45), "to": "h1", "kind": "supersede"}, {"ts": ago(45), "to": "h2", "kind": "supersede"}],
          workers=[{"dispatchId": "d9", "agentTerminalHandle": "h9", "resource": {"worktreeId": "x::/w/w"},
                    "projection": {"outcome": "in_progress", "liveness": {"verdict": "exited"}}}])
found, v, _ = patrol.judge(f, {})
assert codes(found) == [("S", "a"), ("S", "b"), ("X", "w")], found
assert v["cards"] == {"/w/a": "waiting", "/w/b": "working", "/w/w": "run"}, v["cards"]
assert v["hints"] == {"/w/a": "superseded", "/w/b": "superseded", "/w/w": "exited"}, v["hints"]
# The same round before the split: every card above would have left reap's keep set.
assert {p for p, x in v["cards"].items() if x in reap.PATROL_KEEP} == {"/w/a", "/w/b", "/w/w"}
assert set(patrol.ACTIVE) == set(reap.PATROL_KEEP)
# Without Orca's wait list a human's prompt cannot be ruled out: no I, the card stays working, the source is failed.
f = facts(terminals=[term("h1", "/w/a")], tails={"/w/a": (idle(40), None)}, failed={"waits"})
found, v, _ = patrol.judge(f, {})
assert found == [] and v["cards"] == {"/w/a": "working"} and v["failed"] == ["waits"], (found, v)
assert "waits" in patrol.DEPENDS["I"], "a standing I is not cleared by a round that could not read the waits"

# O: an open PR with no session for a day and nobody on it; a requested review or running CI is someone on it.
sess = [{"id": "s1", "path": "/w/p", "agents": [], "linked_pr": {"key": "acme/app#1"}},
        {"id": "s2", "path": "/w/q", "agents": [], "linked_pr": {"key": "acme/app#2"}}]
prs = {"acme/app#1": {"state": "OPEN", "reviewers": [], "ci": "success"},
       "acme/app#2": {"state": "OPEN", "reviewers": [{"login": "someone", "requested": True}], "ci": "success"},
       "acme/app#3": {"state": "OPEN", "reviewers": [{"login": "bot", "requested": False, "status": "commented"}], "ci": "success"}}
sess.append({"id": "s3", "path": "/w/r", "agents": [], "linked_pr": {"key": "acme/app#3"}})
f = facts(sessions=sess, prs=prs)
found, _, memo = patrol.judge(f, {})
assert found == [] and memo["first_seen"] == {"O:/w/p:acme/app#1": NOW, "O:/w/r:acme/app#3": NOW}, memo
found, _, memo = patrol.judge({**f, "now": NOW + 25 * 3600}, memo)
assert codes(found) == [("O", "p"), ("O", "r")] and found[0]["session"] == "s1", "a past review is nobody waiting"
_, _, kept = patrol.judge(facts(failed={"state"}), memo)
assert kept["first_seen"] == memo["first_seen"], "a stale collector does not restart the day-long clock"
memo = {"first_seen": {"O:/w/p:acme/app#1": NOW, "O:/w/r:acme/app#3": NOW}}
found, _, memo2 = patrol.judge({**f, "prs": {**prs, "acme/app#1": {**prs["acme/app#1"], "state": "MERGED"}}}, memo)
assert found == [] and list(memo2["first_seen"]) == ["O:/w/r:acme/app#3"], "a PR that moved on forgets its first sighting"

# D: a doing item that names no PR or card, a day on; one naming a PR is tidy's to judge.
board = {"doing": [{"title": "follow up with infra", "owner": "someone", "next": "ask"},
                   {"title": "land acme/app#3", "owner": "someone", "next": "merge"}]}
f = facts(board=board, state={"sessions": []})
_, v, memo = patrol.judge(f, {})
assert v["doing_noref"] == []
found, v, _ = patrol.judge({**f, "now": NOW + 25 * 3600}, memo)
assert codes(found) == [("D", tidy.item_id("doing", board["doing"][0]))] and v["doing_noref"] == [found[0]["card"]]

# E: one error line in three rounds in a row with new output; timings in it do not make it new, other lines do.
scr = lambda t: ["⏺ Running the suite", f"  ⎿  Error: hook pre-tool failed after {t}s at 12:0{t}", "❯ "]
memo = {}
for i, t in enumerate((3, 4, 5)):
    found, _, memo = patrol.judge(facts(terminals=[term("h1", "/w/a")], screens={"h1": scr(t)}), memo)
    assert [x["code"] for x in found] == ([] if i < 2 else ["E"]), (i, found)
assert found[0]["title"].startswith("❔ the same error in 3 rounds")
_, _, m2 = patrol.judge(facts(terminals=[term("h1", "/w/a")], screens={"h1": ["⏺ ok", "❯ "]}), memo)
found, _, _ = patrol.judge(facts(terminals=[term("h1", "/w/a")], screens={"h1": scr(6)}), m2)
assert found == [], "a round without the error breaks the run"
assert patrol.error_keys(["⏺ quoting: Error: x", "✻ Running… (esc to interrupt)"]) == set(), "nothing while a tool runs"
assert patrol.error_keys(["  ⎿  Error: old", "⏺ Fixed it.", "❯ "]) == set(), "only lines after the last turn mark"
_, _, m3 = patrol.judge(facts(terminals=[term("h1", "/w/a")]), memo)
assert m3["errors"]["h1"] == memo["errors"]["h1"], "a round that read no screen keeps the history"
f3 = facts(terminals=[term("h1", "/w/a")])
assert [x["code"] for x in patrol.judge(f3, memo)[0]] == ["E"], "a standing E stays while no new output comes"
memo = {}
for t in (3, 4, 5):  # read only as an idle candidate: not new output, so the run does not grow
    _, _, memo = patrol.judge(facts(terminals=[term("h1", "/w/a")], screens={"h1": scr(t)}, fresh=set()), memo)
assert memo["errors"] == {}, memo

# Without the Run list, no card is judged idle: a worker would look like one.
f4 = facts(terminals=[term("h1", "/w/a")], tails={"/w/a": (idle(40), None)}, failed={"workers"})
assert patrol.judge(f4, {})[0] == []
assert issubclass(patrol.Budget, BaseException) and not issubclass(patrol.Budget, Exception), "no handler swallows it"

# L: at warn or danger, a top consumer held by a card judged idle; a working card's is shown, not raised.
f = facts(terminals=[term("h1", "/w/a"), term("h2", "/w/b")], tails={"/w/a": (idle(40), None), "/w/b": (idle(1), None)},
          level="danger", consumers=[{"what": "compose stack-a", "cpu": 80, "card": "/w/a"},
                                     {"what": "pid 1 node", "cpu": 90, "card": "/w/b"}, {"what": "pid 2 x", "cpu": 50, "card": None}])
found, v, _ = patrol.judge(f, {})
assert codes(found) == [("I", "a"), ("L", "a")], found
assert [c["verdict"] for c in v["load"]] == ["idle", "working", "no card"]

# W: the orchestrator's watcher, stale an hour or never run.
f = facts(watchers={"ho": (NOW - 61 * 60, 180), "hp": (NOW - 5 * 60, 180), "hq": None})
assert sorted(x["handle"] for x in patrol.judge(f, {})[0]) == ["ho", "hq"]

# Owners: a finding goes to the session that started the card.
own = patrol.owners([{"id": "lead", "path": "/w/l", "terminals": [{"handle": "hl", "agent": "claude"}]},
                     {"id": "c", "path": "/w/a", "parent": "lead"}, {"id": "d", "path": "/w/b", "parent": "gone"}])
assert own["/w/a"] == ("c", "hl") and own["/w/b"] == ("d", None)

# Log: new findings once, cleared when gone, acked by id; Needs you follows X/E/L/O only.
x = {"id": "Xabc", "code": "X", "card": "a", "path": "/w/a", "handle": "h1", "session": "s1", "owner": "hl", "title": "t"}
i = {"id": "Iabc", "code": "I", "card": "b", "path": "/w/b", "handle": "h2", "session": "s2", "owner": None, "title": "t"}
verd = {"cards": {"/w/a": "exited"}, "doing_noref": [], "load": []}
new, gone = patrol.write_round([x, i], verd, False, NOW)
assert [f["id"] for f in new] == ["Xabc", "Iabc"] and gone == []
assert [k["key"] for k in fleet.external_items()] == [f"ext:patrol:Xabc:{fleet.iso(T)}"], fleet.external_items()
new, gone = patrol.write_round([x, i], verd, False, NOW + 60)
assert new == [] and gone == [], "a finding still there is not new again"
assert patrol.pending_lines("hl", False, T)[0].startswith("PATROL X Xabc a → hl"), "routed to its owner"
assert patrol.pending_lines("hl", False, T) == [], "not again within 30 minutes"
assert patrol.pending_lines("hl", False, T + dt.timedelta(minutes=31))[0].startswith("PATROL X Xabc")
assert [l.split()[2] for l in patrol.pending_lines("ho", True, T)[:-1]] == ["Iabc"], "the unowned go to the orchestrator"
assert patrol.pending_lines("hz", False, T) == []
new, gone = patrol.write_round([x], verd, False, NOW + 90, failed={"workers"})
assert gone == [], "a finding whose source failed this round is kept, not cleared"
new, gone = patrol.write_round([{**x, "owner": None}, i], verd, False, NOW + 100)
assert patrol.open_findings()["Xabc"]["owner"] is None, "its starter gone, the finding goes to the orchestrator"
assert "Xabc" in " ".join(patrol.pending_lines("ho", True, T + dt.timedelta(hours=1)))
new, gone = patrol.write_round([x], verd, False, NOW + 120)
assert [r["id"] for r in gone] == ["Iabc"] and "Iabc" not in patrol.open_findings()
assert patrol.ack(["Xabc", "nope"]) == ["Xabc"] and patrol.open_findings() == {}
assert fleet.external_items() == [], "ack resolves its Needs you item"
new, _ = patrol.write_round([x], verd, False, NOW + 180)
assert new == [], "an acked finding is not raised again while it lasts"
assert json.loads(patrol.VERDICTS.read_text())["cards"] == {"/w/a": "exited"}
new, gone = patrol.write_round([], verd, False, NOW + 240)
assert [r["id"] for r in gone] == ["Xabc"], "an acked finding still clears when its condition goes"
new, _ = patrol.write_round([x], verd, False, NOW + 300)
assert [f["id"] for f in new] == ["Xabc"], "after it cleared, the same condition is a new occurrence"
assert [k["key"].split(":")[2] for k in fleet.external_items()] == ["Xabc"]
# A found row whose Needs you item never went in (the round cut short) is added by the next sync; one acked in
# between is resolved.
y = {**x, "id": "Eyyy", "code": "E"}
fleet.append_line(patrol.LOG, {"op": "found", **y, "detected_at": ago(0)})
patrol.sync_needs_you()
assert sorted(k["key"].split(":")[2] for k in fleet.external_items()) == ["Eyyy", "Xabc"]
fleet.append_line(patrol.LOG, {"op": "ack", "id": "Eyyy", "at": ago(0)})
patrol.write_round([x, y], verd, False, NOW + 360)
assert [k["key"].split(":")[2] for k in fleet.external_items()] == ["Xabc"], "an ack written beside a round is resolved"
fleet.inbox_resolve(fleet.external_items()[0]["key"][4:])
patrol.sync_needs_you()
assert fleet.external_items() == [], "one dismissed on the dashboard is not added again"
patrol.ack(["--all"])
patrol.write_round([], verd, False, NOW + 420)

# A failed Orca listing raises, so the round marks its source failed instead of seeing an empty fleet.
class P:
    def __init__(self, out, rc=0):
        self.stdout, self.returncode = out, rc
run0 = patrol.subprocess.run
for out, rc in (('{"ok": false, "error": "no runtime"}', 0), ('{"ok": true, "result": {}}', 0), ("", 1)):
    patrol.subprocess.run = lambda *a, **k: P(out, rc)
    try:
        patrol.all_workers(time.time() + 120)
        raise AssertionError(f"no raise for {out!r}")
    except RuntimeError:
        pass
patrol.subprocess.run = lambda *a, **k: P('{"ok": true, "result": {"workers": [], "scope": {"source": "bound", "run": "r"}}}')
try:
    patrol.all_workers(time.time() + 120)
    raise AssertionError("a list bound to one Run is not the fleet")
except RuntimeError:
    pass
patrol.subprocess.run = lambda *a, **k: P('{"ok": true, "result": {"workers": [{"dispatchId": "d"}], "page": {}}}')
assert patrol.all_workers(time.time() + 120) == [{"dispatchId": "d"}]
patrol.subprocess.run = run0

# An unreadable board.json fails the board source: D is kept and its clocks keep running.
(tmp / "dashboard" / "board.json").write_text("{not json")
try:
    patrol.read_board()
    raise AssertionError("bad board read")
except ValueError:
    pass
(tmp / "dashboard" / "board.json").unlink()
assert patrol.read_board() == {}
memo0 = {"first_seen": {"D:item": NOW - 3600}}
_, _, m = patrol.judge(facts(failed={"board"}), memo0)
assert m["first_seen"] == memo0["first_seen"]
assert "board" in patrol.DEPENDS["D"]
_, _, m = patrol.judge(facts(failed={"board"}), {"first_seen": {"D:item": NOW - 3600, "O:/w/a:acme/app#1": NOW - 90000}})
assert m["first_seen"] == {"D:item": NOW - 3600}, "an O clock the round could see go is not kept by a board failure"
_, _, m = patrol.judge(facts(failed={"state"}), {"first_seen": {"D:item": 1, "O:/w/a:acme/app#1": 2}})
assert m["first_seen"] == {"D:item": 1, "O:/w/a:acme/app#1": 2}

# A round cut short by the budget after its log rows: the rows stay and the next sync adds the Needs you item.
sync0 = patrol.sync_needs_you
def cut(*a, **k):
    raise patrol.Budget()
patrol.sync_needs_you = cut
xf = facts(workers=[{"dispatchId": "dq", "agentTerminalHandle": "hq", "resource": {"worktreeId": "x::/w/q"},
                     "projection": {"outcome": "in_progress", "liveness": {"verdict": "exited"}}}])
try:
    patrol.round_once(now=NOW + 500, gather_fn=lambda now, dl, memo: ({**xf, "now": now}, []))
    raise AssertionError("no budget")
except patrol.Budget:
    pass
patrol.sync_needs_you = sync0
assert [r["code"] for r in patrol.open_findings().values()] == ["X"] and fleet.external_items() == []
patrol.sync_needs_you()
assert [k["type"] for k in fleet.external_items()] == ["patrol"]
patrol.ack(["--all"])

# Output marks move only past screens read; a failed terminal list keeps the error runs and marks.
f = facts(terminals=[term("ha", "/w/a", last=5), term("hb", "/w/b", last=7)], screens={"ha": ["x"]}, fresh={"ha"})
_, _, m = patrol.judge(f, {"last_output": {"hb": 3}})
assert m["last_output"] == {"ha": 5, "hb": 3}, m["last_output"]
hist = {"ha": [["Error: x"], ["Error: x"]]}
_, _, m = patrol.judge(facts(failed={"terminals"}), {"errors": hist, "last_output": {"ha": 5}})
assert m["errors"] == hist and m["last_output"] == {"ha": 5}

# --if-due: 25 minutes, or pressure rising to warn or danger.
assert patrol.due({}, "ok", NOW)
assert not patrol.due({"t": NOW - 600, "level": "ok"}, "ok", NOW)
assert patrol.due({"t": NOW - 600, "level": "ok"}, "warn", NOW)
assert not patrol.due({"t": NOW - 600, "level": "danger"}, "danger", NOW)
assert patrol.due({"t": NOW - 26 * 60, "level": "danger"}, "danger", NOW)

# A dry round writes nothing.
before = patrol.LOG.read_text()
res = patrol.round_once(dry=True, now=NOW + 999, gather_fn=lambda now, dl, memo: (facts(now=now, terminals=[term("h7", "/w/z")],
                        tails={"/w/z": (idle(99), None)}), []))
assert [f["code"] for f in res["findings"]] == ["I"] and patrol.LOG.read_text() == before
assert "NEW PATROL I" in "\n".join(patrol.report(res))

# reap reads the verdicts only to keep, and only while fresh.
patrol.VERDICTS.write_text(json.dumps({"t": time.time(), "cards": {"/w/a": "working", "/w/b": "idle", "/w/c": "run"}}))
assert reap.patrol_working() == {reap.real("/w/a"), reap.real("/w/c")}
patrol.VERDICTS.write_text(json.dumps({"t": time.time() - 7200, "cards": {"/w/a": "working"}}))
assert reap.patrol_working() == set()
assert reap.spawn_patrol("test") is False, "AGENT_SKILLS_PATROL=off starts nothing"

# tidy shows patrol's D as ❔ and never closes on it.
d = {"title": "follow up with infra", "owner": "someone", "next": "ask"}
(tmp / "patrol-verdicts.json").write_text(json.dumps({"doing_noref": [tidy.item_id("doing", d)]}))
got = tidy.assess({"doing": [d]}, {"sessions": []}, {}, now=T)
assert got == {tidy.item_id("doing", d): {"verdict": "check", "rule": "no_ref",
                                           "evidence": "PR·카드 참조 없이 24h+ · 참조를 달거나 닫기", "label": d["title"]}}, got

# sent: supersede is a kind.
assert sent.add("h1", "replaced by the new card", kind="supersede", now=T)["kind"] == "supersede"
assert [r["to"] for r in patrol.sent_supersedes(T)] == ["h1"]

# card_watch: last_scan and the DOWN line, from the stamp or an older file's mtime, every 30 minutes.
cw = tmp / "cw"
cw.mkdir()
assert card_watch.last_scan("hx", cw) is None and card_watch.down_line("hx", NOW, cw) is None, "never ran: no line"
card_watch.save(cw / "hx.json", {"last_scan_at": NOW - 600, "interval": 180})
assert card_watch.last_scan("hx", cw) == (NOW - 600, 180)
assert card_watch.down_line("hx", NOW - 600 + 600, cw) is None, "inside two intervals and the grace"
line = card_watch.down_line("hx", NOW + 400, cw)
assert line and line.startswith("WATCH-CARDS DOWN: its last round ended 16 min ago") and "no pipe" in line, line
assert card_watch.down_line("hx", NOW + 500, cw) is None, "not again within 30 minutes"
assert card_watch.down_line("hx", NOW + 400 + 1801, cw)
card_watch.save(cw / "hx.json", {"last_scan_at": NOW + 5000, "interval": 180})
assert card_watch.down_line("hx", NOW + 5001, cw) is None, "a running watcher clears it"
card_watch.save(cw / "old.json", {})
os.utime(cw / "old.json", (NOW - 100, NOW - 100))
assert card_watch.last_scan("old", cw) == (NOW - 100, 180), "an old state file is stamped by its mtime"

# fleet: the dashboard raises an orchestrator's stale or missing watcher, one item per stop.
sessions = {"o": {"id": "o", "kind": "orchestrator", "terminals": [{"handle": "ho", "agent": "claude"}, {"handle": "sh", "agent": None}]},
            "l": {"id": "l", "kind": "orchestration", "terminals": [{"handle": "hl", "agent": "claude"}]}}
scans = {"ho": (NOW - 2 * 3600, 180)}
items = fleet.watcher_items(sessions, T, scans.get)
assert [(i["key"], i["type"], i["session"]) for i in items] == [(f"watch:ho:{int(NOW - 7200)}", "watcher_down", "o")], items
assert "120분 전" in items[0]["title"]
scans["ho"] = (NOW - 60, 180)
assert fleet.watcher_items(sessions, T, scans.get) == []
assert fleet.watcher_items(sessions, T, lambda h: None)[0]["key"] == "watch:ho:0"

print("test_patrol: ok")
