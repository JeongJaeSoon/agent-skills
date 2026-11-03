"""patrol.py rules, its log and delivery, and the watcher-down signals in card_watch and fleet.
Run: python3 test_patrol.py"""
import datetime as dt, json, os, pathlib, signal, sys, tempfile, time

tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["AGENT_SKILLS_STATE"] = str(tmp)
os.environ["ORCH_FLEET_STATE"] = str(tmp / "dashboard")
os.environ["AGENT_SKILLS_PATROL"] = "off"
(tmp / "dashboard").mkdir()
sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "reap-resources" / "scripts"))
import card_watch, fleet, patrol, prog, reap, sent, tidy  # noqa: E402

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
f = facts(terminals=[term("h1", "/w/a")], tails={"/w/a": (idle(40), None)}, level="danger",
          consumers=[{"what": "pid 1 demo", "cpu": 80, "card": "/w/a"}])
prior = patrol.judge(f, {})[0]
assert sorted(x["code"] for x in prior) == ["I", "L"], prior
saved_rows, patrol.log_rows = patrol.log_rows, lambda: [{"op": "found", **r} for r in prior]
current, v, _ = patrol.judge({**f, "failed": {"waits"}}, {})
assert patrol.write_round(current, v, True, NOW + 60, failed={"waits"}) == ([], []), "neither I nor L clears"
patrol.log_rows = saved_rows

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
        self.stdout, self.stderr, self.returncode = out, "", rc
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

# v2 fields: the old ones keep their names and types, so reap.patrol_working reads the same set from a v2 file.
v2f = facts(now=NOW, terminals=[term("h1", "/w/a"), term("h2", "/w/b"), term("h3", "/w/p")],
            tails={"/w/a": ({"state": "busy", "since": ago(1)}, None), "/w/b": (idle(40), None),
                   "/w/p": (idle(40, parked=True), None)}, timings={"terminals": 0.4, "waits": 0.2})
patrol.VERDICTS.unlink(missing_ok=True)
res = patrol.round_once(now=v2f["now"], gather_fn=lambda now, dl, memo: (v2f, []))
on_file = json.loads(patrol.VERDICTS.read_text())
assert on_file["cards"] == {"/w/a": "working", "/w/b": "idle", "/w/p": "working"} and on_file["hints"] == {"/w/b": "idle"}, on_file
assert isinstance(on_file["failed"], list) and isinstance(on_file["doing_noref"], list) and isinstance(on_file["load"], list)
assert on_file["schema"] == 2 and on_file["round"].startswith("r-") and on_file["complete"] is False
assert on_file["activity"] == {"/w/p": {"state": "parked", "why": on_file["activity"]["/w/p"]["why"]}}, on_file["activity"]
assert on_file["sources"]["terminals"] == "ok" and on_file["sources"]["consumers"] == "skipped", on_file["sources"]
assert on_file["candidates"] == [] and on_file["timings"] == {"terminals": 0.4, "waits": 0.2}
v1 = {k: on_file[k] for k in ("t", "cards")}
assert reap.patrol_working() == {reap.real("/w/a"), reap.real("/w/p")}
patrol.VERDICTS.write_text(json.dumps(v1))
assert reap.patrol_working() == {reap.real("/w/a"), reap.real("/w/p")}, "the same set from the old shape"
patrol.VERDICTS.write_text(json.dumps(on_file))
# Unread sources make the card unknown in activity, never a different `cards` value.
_, v, _ = patrol.judge(facts(terminals=[term("h1", "/w/a")], tails={"/w/a": (idle(40), None)}, failed={"waits"}), {})
assert v["cards"] == {"/w/a": "working"} and v["activity"]["/w/a"]["state"] == "unknown"
_, v, _ = patrol.judge(facts(terminals=[term("h1", "/w/a")], tails={"/w/a": (idle(40), None)}, failed={"workers"}), {})
assert v["cards"] == {} and v["activity"]["/w/a"]["state"] == "unknown"

# The candidate phase fills the round it belongs to, and leaves a file a newer round wrote alone.
fake = {"sources": {**on_file["sources"], "reap_scan": "ok"}, "timings": {"reap_scan": 1.5},
        "candidates": [{"rule": "card:landed", "kind": "card", "key": "card:/w/b", "needs": ["reap_scan"], "item": {}}],
        "reports": [], "alerts": [{"kind": "card", "key": "card:/w/b", "path": "/w/b", "decide": "질문"}], "notes": []}
res["owners"] = {"/w/b": "hb"}
before = time.time()
patrol.candidates_phase(res, collect=lambda sources, **k: json.loads(json.dumps(fake)))
on_file = json.loads(patrol.VERDICTS.read_text())
assert before <= on_file["candidates_t"] <= time.time()
assert on_file["complete"] is True and [c["key"] for c in on_file["candidates"]] == ["card:/w/b"]
assert on_file["alerts"][0]["to"] == "hb" and on_file["timings"]["reap_scan"] == 1.5 and "candidates" in on_file["timings"]
assert on_file["cards"]["/w/a"] == "working" and reap.patrol_working() == {reap.real("/w/a"), reap.real("/w/p")}
row = json.loads(patrol.ROUNDS.read_text().splitlines()[-1])
assert row["round"] == on_file["round"] and row["complete"] is True and row["failed"] == []
assert patrol.timing_lines()[0].startswith("patrol timing over 1 rounds"), patrol.timing_lines()
patrol.VERDICTS.write_text(json.dumps({**on_file, "round": "r-newer", "candidates": []}))
patrol.candidates_phase(res, collect=lambda sources, **k: json.loads(json.dumps(fake)))
assert json.loads(patrol.VERDICTS.read_text())["candidates"] == [], "a newer round's file is not overwritten"
# Danger skips the scans; a budget cut leaves the round incomplete, never with half the candidates.
patrol.VERDICTS.write_text(json.dumps({**on_file, "complete": False, "candidates": []}))
out = patrol.candidates_phase({**res, "level": "danger", "notes": []}, collect=lambda s, **k: 1 / 0)
assert out["complete"] is False and out["sources"]["candidates"] == "skipped" and out["candidates"] == []


def slow_scan(sources, progress):
    """Reads one source, then hangs on the next until the real alarm fires."""
    progress.update(sources={**sources, "reap_scan": "ok"}, timings={"reap_scan": 0.5})
    time.sleep(5)
    raise AssertionError("the alarm did not fire")


signal.signal(signal.SIGALRM, patrol.give_up)
signal.alarm(1)
out = patrol.candidates_phase({**res, "notes": []}, collect=slow_scan)
assert out["complete"] is False and out["sources"]["candidates"] == "timeout" and out["candidates"] == []
assert out["sources"]["reap_scan"] == "ok" and out["timings"]["reap_scan"] == 0.5, "what was read before the cut stays"
assert json.loads(patrol.VERDICTS.read_text())["complete"] is False
assert len(patrol.ROUNDS.read_text().splitlines()) == 4
assert json.loads(patrol.ROUNDS.read_text().splitlines()[-1])["failed"] == ["candidates"]
# A main round the budget cut: the earlier round's fields stay, under the new id and incomplete.
patrol.VERDICTS.write_text(json.dumps({**on_file, "complete": True, "candidates": [{"key": "old"}]}))
orca0 = patrol.prog.orca_json
patrol.prog.orca_json = lambda *a: time.sleep(5)  # the first source hangs until the real alarm
signal.alarm(1)
try:
    patrol.gather(NOW, time.time() + 100, {})
    raise AssertionError("the alarm did not fire")
except patrol.Budget:
    pass
finally:
    signal.alarm(0)
    patrol.prog.orca_json = orca0
assert patrol.CURRENT["failed"] == {"terminals"} and patrol.CURRENT["timings"]["terminals"] >= 0.9, patrol.CURRENT
patrol.mark_cut("r-cut0", 1.0)
assert json.loads(patrol.VERDICTS.read_text())["sources"]["terminals"] == "failed", "a source the budget cut is not ok"
patrol.CURRENT.update(timings={"terminals": 0.3, "workers": 119.0}, failed={"workers"})
patrol.mark_cut("r-cut", 120.2)
cut = json.loads(patrol.VERDICTS.read_text())
assert cut["round"] == "r-cut" and cut["complete"] is False and cut["candidates"] == [] and cut["cards"] == on_file["cards"]
assert cut["sources"]["round"] == "timeout" and cut["sources"]["workers"] == "failed" and cut["sources"]["terminals"] == "ok"
assert cut["timings"] == {"terminals": 0.3, "workers": 119.0}, "this round's timings, not the earlier round's"
row = json.loads(patrol.ROUNDS.read_text().splitlines()[-1])
assert row["round"] == "r-cut" and row["seconds"] == 120.2 and row["complete"] is False and "round" in row["failed"]
assert "workers: p50" in "\n".join(patrol.timing_lines()), "a slow cut round counts in the percentiles"
patrol.VERDICTS.write_text(json.dumps(on_file))
# prog.run waits forever unless the patrol round set a timeout; then a hung command is a failed read.
assert prog.RUN_TIMEOUT is None
prog.RUN_TIMEOUT = 0.2
assert prog.run(["sleep", "2"], check=False).returncode == 124
prog.RUN_TIMEOUT = None

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

# R: the same thing told over and over with nothing changing in between (dash.repeat_turns).
dash = patrol.dash
def prompt(t, text):
    return {"type": "user", "timestamp": t, "message": {"content": text}}
def reply(t, text, **kw):
    return {"type": "assistant", "timestamp": t, "message": {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}, **kw}
def use(t, i, cmd):
    return {"type": "assistant", "timestamp": t, "message": {"content": [{"type": "tool_use", "id": i, "name": "Bash", "input": {"command": cmd}}]}}
def res(t, i, out):
    return {"type": "user", "timestamp": t, "message": {"content": [{"type": "tool_result", "tool_use_id": i, "content": out}]}}
def nudges(n, gap, said=lambda k: f"Waiting on owner approval for acme/app#1 (asked {10 + k}:05, msg_{k}abcdefgh)."):
    rows = []
    for k in range(n):
        rows += [prompt(ago(gap * (n - k)), "status?"), reply(ago(gap * (n - k) - 1), said(k))]
    return rows
rep = dash.repeat_turns(nudges(3, 20))
assert (rep["kind"], rep["count"], rep["since"]) == ("assistant", 3, ago(60)), rep
assert dash.repeat_turns(nudges(2, 30)) is None, "two turns are not a streak"
assert dash.repeat_turns(nudges(4, 5)) is None, "four turns in 20 minutes are too short to say"
assert dash.repeat_turns(nudges(3, 20, lambda k: f"Waiting on approval: {k} of 3 checks pass.")) is None, \
    "a number other than a time or an id is progress"
# A different nudge with the same answer still counts; the same nudge with work that changed does not.
assert dash.repeat_turns(nudges(3, 20)[:-2] + [prompt(ago(19), "any news?"), reply(ago(18), "Waiting on owner approval for acme/app#1 (asked 12:05, msg_9abcdefgh).")])["kind"] == "assistant"
def polls(outs, gap=20):
    rows = []
    for k, out in enumerate(outs):
        at = gap * (len(outs) - k)
        rows += [prompt(ago(at), "check CI"), use(ago(at - 1), f"u{k}", "gh pr checks 1"), res(ago(at - 2), f"u{k}", out),
                 reply(ago(at - 3), "CI still running.")]
    return rows
assert dash.repeat_turns(polls(["pending 3/7", "pending 4/7", "pending 6/7"])) is None, "a result that changes is progress"
assert dash.repeat_turns(polls(["pending 3/7"] * 3))["count"] == 3, "the same call and the same result are not"
assert dash.repeat_turns(polls(["pending 3/7"] * 3) + [prompt(ago(0), "check CI"), use(ago(0), "u9", "gh pr checks 1")]) is None, \
    "a turn still running ends the streak: long work never reads as one"
swapped = polls(["pending 3/7"] * 3)
swapped[5] = use(ago(39), "u1", "gh pr checks 2")
assert dash.repeat_turns(swapped) is None, "a tool call that changed is progress"
assert dash.repeat_turns(nudges(3, 20, lambda k: f"Processed {k + 1}000000 records.")) is None, "a long count is no id"
stamped = polls(["pending 3/7"] * 3)
for k, i in ((1, 1), (5, 5), (9, 9)):
    stamped[k] = use(stamped[k]["timestamp"], f"u{i}", f"gh pr checks 1 --since {stamped[k]['timestamp']}")
assert dash.repeat_turns(stamped)["count"] == 3, "a time in a tool's input is no change"
pins = polls(["ok"] * 3)
for k, sha in ((1, "1a2b3c4d5e6f7"), (5, "2b3c4d5e6f7a8"), (9, "3c4d5e6f7a8b9")):
    pins[k] = use(pins[k]["timestamp"], f"u{k}", f"git checkout {sha}")
assert dash.repeat_turns(pins) is None, "another commit in a tool's input is other work"
shot = lambda data: [{"type": "image", "source": {"type": "base64", "data": data}}]
assert dash.repeat_turns(polls([shot("AAAA"), shot("BBBB"), shot("CCCC")])) is None, "a screenshot that changed is a change"
pic = {"type": "user", "timestamp": ago(0), "message": {"content": shot("DDDD")}}
assert dash.repeat_turns(polls(["pending 3/7"] * 3) + [pic]) is None, "an image sent with no text is a new prompt"
long = "x" * 5000
assert dash.repeat_turns(polls([long + "3/7", long + "4/7", long + "6/7"])) is None, "a result is compared whole"
errs = [r for k in range(3) for r in (prompt(ago(90 - 30 * k), "continue"),
        reply(ago(89 - 30 * k), "API Error: 529 overloaded (req_01ABCDEFGHJK)", isApiErrorMessage=True))]
assert dash.repeat_turns(errs)["kind"] == "error", dash.repeat_turns(errs)
tail = [json.loads(l) for l in (pathlib.Path(__file__).parent / "testdata" / "reload-tail.jsonl").read_text().splitlines()]
rep = dash.repeat_turns([r for r in tail if r.get("timestamp")])
assert (rep["kind"], rep["count"], rep["text"]) == ("reload", 3, "/reload-plugins /reload-skills"), "one turn per reload, not per row"
assert dash.repeat_turns([prompt("2026-11-03T10:30:00Z", "fix the bug")] + [r for r in tail if r.get("timestamp")][3:]) is None, \
    "reloads after a prompt still unanswered: the turn may be working"
mixed = nudges(3, 20)
mixed.insert(2, [r for r in tail if r.get("timestamp")][5])
assert dash.repeat_turns(mixed)["kind"] == "assistant", "a reload in between is no turn of the session's"

# R in a round: a stalled card is not working, a parked one stays protected; raised once per session and text.
rep = {**dash.repeat_turns(nudges(3, 20)), "session": "s1"}
f = facts(terminals=[term("h1", "/w/a"), term("h2", "/w/b")],
          tails={"/w/a": ({"state": "working", "since": ago(1), "repeat": rep}, None),
                 "/w/b": ({"state": "working", "since": ago(1), "parked": True, "repeat": {**rep, "session": "s2"}}, None)})
found, v, memo = patrol.judge(f, {})
assert codes(found) == [("R", "a"), ("R", "b")], found
assert v["cards"] == {"/w/a": "stalled", "/w/b": "working"}, v["cards"]
assert v["check"] == {"/w/a": "같은 답 3턴 반복"}, "the brief's ❔ row: the stalled card, not the parked one"
assert "assistant in 3 turns" in found[0]["title"] and "acme/app#1" in found[0]["title"], found[0]["title"]
again, _, memo2 = patrol.judge(f, memo)
assert [x["id"] for x in again] == [x["id"] for x in found], "a streak that lasts stays the same finding"
_, v3, memo3 = patrol.judge(facts(terminals=f["terminals"], tails={"/w/a": (idle(1), None), "/w/b": (idle(1), None)}), memo2)
back, v4, _ = patrol.judge(f, memo3)
assert back == [] and v4["cards"]["/w/a"] == "stalled", "the same text again is not raised again, but is still no work"
assert patrol.judge(f, patrol.judge({**f, "tails": {}}, memo, )[2])[0] == [], "nor after a round that did not see it"
_, _, kept = patrol.judge({**f, "failed": {"waits"}}, memo)
assert kept["repeats"] == memo["repeats"], "a round that could not look ends no streak"
assert patrol.judge({**f, "waits": {"h1": {"kind": "permission"}}}, {})[1]["cards"]["/w/a"] == "waiting"
# From the real motion: a session idle after the streak is told of it too, and stays idle.
rows = nudges(3, 20)
m = dash.step_motion({}, rows[0])
for row in rows[1:]:
    m = dash.step_motion(m, row)
m = {**m, "repeat": {**dash.repeat_turns(rows), "session": "s3"}}
fi = facts(terminals=[term("h1", "/w/a")], tails={"/w/a": ({**m, "since": ago(19)}, None)}, read=lambda h: ["❯ "])
found_i, vi, _ = patrol.judge(fi, {})
assert codes(found_i) == [("I", "a"), ("R", "a")] and vi["cards"] == {"/w/a": "idle"}, (found_i, vi)
# Never again for the same key, however many other sessions were told since.
key = f"s3:{m['repeat']['kind']}:{m['repeat']['digest']}"
crowd = {f"s{n}:assistant:x": {"at": NOW - 86400, "live": False} for n in range(5000)}
_, _, kept = patrol.judge(fi, {"repeats": {**crowd, key: {"at": NOW - 10 * 86400, "live": False}}})
assert key in kept["repeats"] and codes(patrol.judge(fi, kept)[0]) == [("I", "a")], "only I: R was raised before"
# Kept 90 days from when it was last seen, not from when it was first raised.
_, _, gone_round = patrol.judge({**fi, "tails": {}}, {"repeats": {key: {"at": NOW - 91 * 86400, "seen": NOW - 600, "live": True}}})
assert key in gone_round["repeats"] and codes(patrol.judge(fi, gone_round)[0]) == [("I", "a")]
_, _, old_round = patrol.judge({**fi, "tails": {}}, {"repeats": {key: {"at": NOW - 200 * 86400, "seen": NOW - 91 * 86400, "live": False}}})
assert old_round["repeats"] == {}
r = {**found[0], "owner": "hr"}
patrol.write_round([r], {"cards": {}}, False, NOW + 200)
assert patrol.pending_lines("hr", False, T)[0].startswith("PATROL R"), "shown to its owner"
assert patrol.pending_lines("hr", False, T + dt.timedelta(hours=3)) == [], "R is shown once, not every 30 minutes"

# H: an ended turn whose screen shows a hook error in its last turn or Claude Code's restart notice. Run workers too;
# a screen without that wording, a turn still running, a prompt waiting on a human or an unread screen is not judged.
RULE = "─" * 40
hooked = ["❯ run the tests", "⏺ Bash(make test)",
          "  ⎿  PreToolUse:Bash hook error: python3: can't open file '/w/plugin/hooks/gone.py': [Errno 2] No such file",
          "⏺ Every tool call is blocked by a hook error.", RULE, "❯", RULE, "  ~/w ⎇ main"]
notice = ["⏺ Goal achieved.", RULE, "❯", RULE, "  ~/w ⎇ main", "  ✓ Update installed · /restart to apply"]
plain = ["⏺ Done.", RULE, "❯", RULE, "  ~/w ⎇ main"]
shown = {"h1": hooked, "h2": notice, "h3": plain, "h4": hooked, "h5": notice, "h6": hooked}
f = facts(terminals=[term(f"h{n}", f"/w/{c}") for n, c in enumerate("abcdef", 1)],
          tails={"/w/a": (idle(5), None), "/w/b": (idle(300), None), "/w/c": (idle(5), None),
                 "/w/d": ({"state": "working", "since": ago(1)}, None), "/w/e": (idle(5), None), "/w/f": (idle(5), None)},
          waits={"h5": {"kind": "permission"}},
          workers=[{"dispatchId": "d1", "resource": {"worktreeId": "x::/w/f"},
                    "projection": {"outcome": "in_progress", "liveness": {"verdict": "live"}}}],
          read=lambda h: shown[h])
found, v, _ = patrol.judge(f, {})
stale = sorted((x for x in found if x["code"] == "H"), key=lambda x: x["card"])
assert codes(stale) == [("H", "a"), ("H", "b"), ("H", "f")], found
assert v["check"] == {"/w/a": "hook 오류로 도구가 막힘", "/w/b": "업데이트가 /restart 를 기다림",
                      "/w/f": "hook 오류로 도구가 막힘"}, v["check"]
assert v["cards"]["/w/a"] == "stalled" and v["cards"]["/w/c"] == "working" and v["cards"]["/w/f"] == "run", v["cards"]
assert v["cards"]["/w/b"] == "idle", "15+ min idle is told as I too, and neither keeps the card"
assert "--terminal h1" in stale[0]["title"] and "/reload-plugins" in stale[0]["title"], stale[0]["title"]
assert "/restart" in stale[1]["title"], stale[1]["title"]
assert [x["id"] for x in patrol.judge(f, {})[0] if x["code"] == "H"] == [x["id"] for x in stale], "the same screen, the same finding"


def unreadable(h):
    raise OSError("no screen")


assert not [x for x in patrol.judge({**f, "screens": {}, "read": unreadable}, {})[0] if x["code"] == "H"], \
    "an unread screen is not judged"
assert not [x for x in patrol.judge({**f, "failed": {"waits"}}, {})[0] if x["code"] == "H"], \
    "without Orca's wait list a prompt for a human cannot be ruled out"
assert patrol.DEPENDS["H"] == {"terminals", "waits"}

print("test_patrol: ok")
