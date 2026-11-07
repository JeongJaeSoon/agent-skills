"""patrol.py rules, its log and delivery, and the watcher-down signals in card_watch and fleet.
Run: python3 test_patrol.py"""
import contextlib, datetime as dt, io, json, os, pathlib, signal, sys, tempfile, time

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
# Whether the I may wait its hour (pending_lines): only a card whose session was read and has no PR is held.
assert found[0]["pr_open"] is True, "no session read for the card: unknown"
read = {**f, "sessions": [{"path": "/w/a"}]}
assert patrol.judge(read, {})[0][0]["pr_open"] is False
pr = {**f, "sessions": [{"path": "/w/a", "linked_pr": {"key": "acme/app#1"}}], "prs": {"acme/app#1": {"state": "OPEN"}}}
assert patrol.judge(pr, {})[0][0]["pr_open"] is True
bar = ["⏺ Waiting on the inbox.", "❯ ", "  ⏵⏵ auto mode on · 1 shell · ← 1 agent"]
f2 = {**f, "screens": {}, "read": lambda h: bar}
assert patrol.judge(f2, {})[0] == [], "a background shell still running is not idle, though the transcript lost it"
assert patrol.judge({**f, "screens": {}, "read": lambda h: ["❯ "]}, {})[0][0]["code"] == "I"
assert patrol.background(["✻ Crunched for 22s · done 7:47 PM · 1 shell still running"])
# Captured status lines: "← N agent" counts sessions elsewhere waiting on a human, not work of this one.
fleet_bar = "  ⏵⏵ auto mode on (shift+tab to cycle) · PR #1 · ← 1 agent"
monitor_bar = "  ⏵⏵ auto mode on · 1 monitor · ← 1 agent"
assert not patrol.background(["⏺ Done.", "❯ ", fleet_bar])
assert patrol.background(["❯ ", monitor_bar]) and patrol.background(["❯ ", "  ⏵⏵ auto mode on · 1 shell, 1 monitor · ← 1 agent"])
assert [x["code"] for x in patrol.judge({**f, "screens": {}, "read": lambda h: ["⏺ Done.", "❯ ", fleet_bar]}, {})[0]] == ["I"], \
    "an idle session showing only that count is still idle"
assert patrol.judge({**f, "screens": {}, "read": lambda h: ["⏺ Done.", "❯ ", monitor_bar]}, {})[0] == [], "a Monitor running is not idle"
assert patrol.background(["❯ ", "  ⏵⏵ auto mode on · ← 1 agent", "  ⏺ main", "  ◯ general-purpose  reading notes"]), \
    "the panel listing the session's agents is background work"
assert not patrol.background(["⏺ main", "◯ a note in the reply", "─" * 20, "❯", "─" * 20, "  ⏵⏵ auto mode on · ← 1 agent"]), \
    "a reply reading like a panel row, above the prompt box, is not one"

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

# Q: an in-progress Run worker Orca cannot verify and has not observed for 2h+, its transcript not working.
def quiet_worker(hours, verdict="unverifiable", observed=True, **extra):
    live = {"verdict": verdict}
    if observed is True:
        live["observedAt"] = int((NOW - hours * 3600) * 1000)
    elif observed is not None:
        live["observedAt"] = observed
    return {"dispatchId": "dq", "agentTerminalHandle": "hq", "resource": {"worktreeId": "x::/w/q"},
            "projection": {"outcome": "in_progress", "liveness": live, **extra}}
LEAD = [{"id": "sq", "path": "/w/q", "parent": "sp"}, {"id": "sp", "path": "/w/p", "terminals": [{"handle": "hp", "agent": True}]}]
QT = {"/w/q": (idle(90), {"type": "assistant", "timestamp": ago(90)})}
def quiet_found(w, sessions=LEAD, tails=QT):
    return patrol.judge(facts(workers=[w], sessions=sessions, tails=tails), {})
found, v, _ = quiet_found(quiet_worker(3))
assert codes(found) == [("Q", "q")] and v["cards"]["/w/q"] == "run", found  # the card stays a Run worker's
assert found[0]["owner"] == "hp", found[0]  # its lead's agent terminal, not the orchestrator
assert found[0]["title"].startswith("❔") and "3h" in found[0]["title"], found[0]["title"]
assert patrol.DEPENDS["Q"] == {"terminals", "workers", "state"} and "Q" in patrol.SHOW_ONCE
for why, w in (("observed 1h ago", quiet_worker(1)), ("liveness verified", quiet_worker(3, verdict="live")),
               ("no observedAt", quiet_worker(3, observed=None)), ("bool observedAt", quiet_worker(3, observed=False)),
               ("string observedAt", quiet_worker(3, observed="soon")), ("negative observedAt", quiet_worker(3, observed=-5)),
               ("exited is X's", quiet_worker(3, verdict="exited"))):
    assert ("Q", "q") not in codes(quiet_found(w)[0]), why
assert [c for c, _ in codes(quiet_found(quiet_worker(3, verdict="exited"))[0])] == ["X"]
for why, kw in (("no sessions: the lead is unknown, nothing goes to the orchestrator", {"sessions": []}),
                ("session without a parent", {"sessions": [{"id": "sq", "path": "/w/q"}]}),
                ("parent without an agent terminal", {"sessions": [LEAD[0], {"id": "sp", "path": "/w/p", "terminals": []}]}),
                ("transcript unread", {"tails": {}}),
                ("transcript working", {"tails": {"/w/q": ({"state": "working", "since": ago(1)}, None)}})):
    assert ("Q", "q") not in codes(quiet_found(quiet_worker(3), **kw)[0]), why
# Delivery: to the lead only, once; a quiet that ends and comes back is a new one.
saved_paths = patrol.LOG, patrol.SHOWN
assert patrol.PER_OCCURRENCE == {"Q"} and patrol.key({"id": "Xa", "code": "R", "detected_at": "t"}) == "Xa"  # R, M keep the id
assert patrol.key({"id": "Qa", "code": "Q", "detected_at": "t"}) == "Qa@t" and patrol.key({"id": "Qa", "code": "Q"}) == "Qa"
with tempfile.TemporaryDirectory() as d:
    patrol.LOG, patrol.SHOWN = pathlib.Path(d) / "patrol.jsonl", pathlib.Path(d) / "shown.json"
    patrol.write_round(found, {}, False, NOW)
    assert patrol.pending_lines("hp", False, T)[0].startswith("PATROL Q "), "routed to the lead"
    assert patrol.pending_lines(None, True, T) == [], "the orchestrator is not asked"
    assert patrol.pending_lines("hp", False, T + dt.timedelta(hours=9)) == [], "shown once, not every RENOTIFY"
    patrol.write_round([], {}, False, NOW + 60)
    patrol.write_round(found, {}, False, NOW + 120)  # closed and back with no poll in between
    assert patrol.pending_lines("hp", False, T + dt.timedelta(hours=10))[0].startswith("PATROL Q "), "new after it closed"
    assert patrol.pending_lines("hp", False, T + dt.timedelta(hours=20)) == [], "and shown once again, not every RENOTIFY"
patrol.LOG, patrol.SHOWN = saved_paths
assert patrol.quiet_hours({"liveness": {"verdict": "unverifiable", "observedAt": 0}}, 1e9) == 0.0
assert patrol.quiet_hours({"liveness": {"verdict": "unverifiable", "observedAt": 10**13}}, 1000) == 0.0  # in the future
assert abs(patrol.quiet_hours({"liveness": {"verdict": "unverifiable", "observedAt": 1_000_000}}, 1000 + 7200) - 2.0) < 1e-9

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

# O, class 2: running CI is not someone on it for good. The clock stays through a round that skips the PR for CI, so a
# push that starts CI does not hand a PR with no session another day; a requested review still starts it over.
pending = {**prs, "acme/app#1": {**prs["acme/app#1"], "ci": "pending"}}
f1 = facts(sessions=sess[:1], prs=prs)
_, _, m1 = patrol.judge(f1, {})
_, _, m2 = patrol.judge({**f1, "now": NOW + 3600, "prs": pending}, m1)
assert m2["first_seen"] == {"O:/w/p:acme/app#1": NOW}, "CI running keeps the clock"
found, _, m3 = patrol.judge({**f1, "now": NOW + 25 * 3600}, m2)
assert codes(found) == [("O", "p")], "the day counts from the first sighting, CI in between or not"
asked_pr = {**prs, "acme/app#1": {**prs["acme/app#1"], "reviewers": [{"login": "x", "requested": True}]}}
_, _, m4 = patrol.judge({**f1, "now": NOW + 3600, "prs": asked_pr}, m1)
assert m4["first_seen"] == {}, "a requested review is someone waiting: the clock starts over"

# O, proposal 2: a conflict on a PR with no session is found after 2 hours; a clean one still takes a day. A PR a
# board item holds (a held item, or a to-do not yet closed) is never found, the owner's to-do included after its date.
dirty = {**prs, "acme/app#1": {**prs["acme/app#1"], "merge_state": "DIRTY"}}
fd = facts(sessions=sess[:1], prs=dirty)
_, _, md = patrol.judge(fd, {})
assert patrol.judge({**fd, "now": NOW + 3600}, md)[0] == [], "under 2 hours"
found, _, _ = patrol.judge({**fd, "now": NOW + 2 * 3600}, md)
assert codes(found) == [("O", "p")] and "conflict" in found[0]["title"], found
assert patrol.judge({**f1, "now": NOW + 2 * 3600}, m1)[0] == [], "a clean PR waits the day"
later = {"later": [{"id": "b1", "title": "acme/app#1 보류", "trigger": "next quarter"}]}
todo_w = {"todo": [{"id": "b2", "title": "acme/app#1 정리", "who": "writer", "after": "acme/app#9 머지 후"}]}
todo_o = {"todo": [{"id": "b3", "title": "acme/app#1 답변", "who": "owner", "due": "2020-01-01"}]}
for name, board in (("later", later), ("to-do", todo_w), ("owner to-do past its date", todo_o)):
    found, _, kept = patrol.judge({**fd, "now": NOW + 3 * 3600, "board": board}, md)
    assert found == [] and kept["first_seen"] == {}, (name, found)
todo_past = {"todo": [{"id": "b4", "title": "acme/app#1 정리", "who": "writer", "due": "2020-01-01"}]}
assert codes(patrol.judge({**fd, "now": NOW + 3 * 3600, "board": todo_past}, md)[0]) == [("O", "p")], "a writer's to-do past its date holds nothing"
# A board row that names the PR and records a hold, a wait or an owner is the PR's waiting; a done row, or one that
# records nothing, is not.
for name, row_ in (("held", {"status": "held"}), ("waiting", {"status": "waiting"}), ("owner", {"owner": "someone"}),
                   ("after an open PR", {"after": "acme/app#9"})):
    brd = {"rows": {"r1": {"kind": "task", "pr": "acme/app#1", "status": "moving", **row_}}}
    found, _, _ = patrol.judge({**fd, "now": NOW + 3 * 3600, "board": brd}, md)
    assert found == [], (name, found)
for name, row_ in (("done", {"status": "done", "owner": "someone"}), ("nothing", {}), ("a merged PR", {"after": "acme/app#9"})):
    brd = {"rows": {"r1": {"kind": "task", "pr": "acme/app#1", "status": "moving", **row_}}}
    ps = {**dirty, "acme/app#9": {"state": "MERGED"}} if name == "a merged PR" else dirty
    assert codes(patrol.judge({**fd, "now": NOW + 3 * 3600, "board": brd, "prs": ps}, md)[0]) == [("O", "p")], name
# A board that could not be read: no new finding, and the clock is kept.
found, _, kept = patrol.judge({**fd, "now": NOW + 3 * 3600, "board": {}, "failed": {"board"}}, md)
assert found == [] and kept["first_seen"] == md["first_seen"], (found, kept)

# P, class 5: a lead that parked itself with no worker running and nothing on record it waits for. Parked alone is a
# wake-up it armed, so the ledger has to be still too, and a recorded hold, a board row waiting on someone, or a Run
# worker still going each name what it waits for.
# The board is shaped as `board lead add` writes it: a lead row (kind lead, program) and a `leads` entry (worktree).
lsess = [{"id": "lead-s", "path": "/w/lead", "kind": "orchestration", "run": "run_1", "agents": [{}]}]
ev = lambda ev_, minutes, **kw: {"ev": ev_, "ts": ago(minutes), **kw}  # noqa: E731
led = lambda events, run="run_1": {"prog-a": {"run": run, "events": events}}  # noqa: E731
lboard = lambda rows=None, lead=None, entry=None: {  # noqa: E731
    "rows": {"L": {"kind": "lead", "program": "prog-a", "status": "moving", **(lead or {})}, **(rows or {})},
    "leads": {"L": {"name": "L", "worktree": "lead-s", **(entry or {})}}}
lead_facts = lambda **over: facts(**{  # noqa: E731
    "sessions": lsess, "terminals": [term("hl", "/w/lead")], "tails": {"/w/lead": (idle(300, parked=True), None)},
    "board": lboard(), "workers": [], "ledgers": led([ev("admitted", 200, ticket="T-1"), ev("landed", 190, pr=5)]), **over})
found, v, _ = patrol.judge(lead_facts(), {})
assert codes(found) == [("P", "lead")] and v["check"]["/w/lead"] == "워커 없이 대기 · 대상 기록 없음", (found, v)
quiet_lead = lambda **over: [x for x in patrol.judge(lead_facts(**over), {})[0] if x["code"] == "P"]  # noqa: E731
assert quiet_lead(ledgers=led([ev("landed", 90)])) == [], "the ledger moved an hour and a half ago"
assert quiet_lead(ledgers=led([ev("landed", 190)], run="run_2")) == [], "the program moved to another Run"
assert quiet_lead(board=lboard(entry={"worktree": None}, lead={"worktree": "lead-s"})) != [], "the worktree on the row counts"
assert quiet_lead(tails={"/w/lead": (idle(300), None)}) == [], "not parked: the idle rule's"
run_w = lambda run, outcome="in_progress": {"dispatchId": "d9", "runId": run, "resource": {"worktreeId": "x::/w/q"},  # noqa: E731
                                              "projection": {"outcome": outcome, "liveness": {"verdict": "live"}}}
assert quiet_lead(workers=[run_w("run_1")]) == [], "a worker of its Run is going"
assert quiet_lead(workers=[run_w("run_2")]) != [], "another Run's worker is not its"
assert quiet_lead(workers=[run_w("run_1", "succeeded")]) != [], "a finished worker waits for nothing"
assert quiet_lead(workers=[{**run_w("run_1"), "runId": None}]) == [], "a worker whose Run is unknown: cannot be ruled out"
assert quiet_lead(failed={"workers"}) == [] and quiet_lead(failed={"board"}) == []
for held_ledger in ([ev("parked", 200, pr=5)], [ev("parked", 200, pr=5), ev("landed", 190, pr=6)],
                    [ev("parked", 200, ticket="T-1")], [ev("admitted", 220, ticket="T-1"), ev("parked", 200)]):
    assert quiet_lead(ledgers=led(held_ledger)) == [], held_ledger  # a hold or a parked mark on record
assert quiet_lead(ledgers=led([ev("parked", 300, pr=5), ev("admitted", 200, pr=5)])) != [], "admitted lifts the hold"
assert quiet_lead(ledgers={"prog-a": None}) == [] and quiet_lead(ledgers={}) == [], "no ledger read: no evidence"
assert quiet_lead(ledgers=led([])) == [], "an empty ledger says nothing about how long it was still"
waits_row = {"kind": "task", "parent": "L", "status": "waiting", "title": "x"}
assert quiet_lead(board=lboard({"L/a": waits_row})) == [], "a row waiting on someone is the target"
assert quiet_lead(board=lboard({"L/a": {**waits_row, "status": "moving", "owner": "someone"}})) == []
assert quiet_lead(board=lboard({"M/a": {**waits_row, "parent": "M"}})) != [], "another lead's row"
assert quiet_lead(board=lboard(lead={"status": "waiting", "owner": "someone"})) == [], "the lead's own row records the wait"
assert quiet_lead(board=lboard(lead={"status": "held"})) == [], "a lead held on purpose"
assert quiet_lead(tails={"/w/lead": ({**idle(300, parked=True), "state": "tool"}, None)}) == [], "a tool is running: the turn has not ended"
assert quiet_lead(board=lboard({"L/a": {**waits_row, "status": "moving", "after": "acme/app#9"}})) == [], "a row after an open PR"
gate_open = [ev("gate_opened", 220, pr=5, gate="g1")]
assert quiet_lead(ledgers=led(gate_open)) == [], "a landing gate opened and not answered"
assert quiet_lead(ledgers=led(gate_open + [ev("approved", 200, pr=5)])) != [], "approved: the lead resumes, nothing is left to wait on"
assert quiet_lead(ledgers=led(gate_open + [ev("landed", 200, pr=5)])) != []
assert quiet_lead(ledgers=led([ev("gate_opened", 220, pr=5), ev("approved", 210, pr=5), ev("gate_opened", 200, pr=5)])) == [], "the newest gate counts"
for state in ("MERGED", "CLOSED"):
    assert quiet_lead(board=lboard({"L/a": {**waits_row, "status": "moving", "after": "acme/app#9"}}),
                      prs={"acme/app#9": {"state": state}}) != [], f"a {state} PR is no wait"
second = [term("hl", "/w/lead"), term("h2", "/w/lead")]
assert quiet_lead(terminals=second, waits={"h2": {"kind": "permission"}}) == [], "any agent terminal of the card at a prompt"
assert quiet_lead(board=lboard({"L/a": {**waits_row, "status": "done", "owner": "someone"}})) != [], "a done row waits for nothing"
assert quiet_lead(board=lboard({"L/a": {**waits_row, "done_at": ago(5)}})) != [], "a closed row waits for nothing"
deep = {"L/p": {"kind": "phase", "parent": "L", "status": "moving"}, "L/p/t": {**waits_row, "parent": "L/p"}}
assert quiet_lead(board=lboard(deep)) == [], "a waiting task below a phase is the lead's wait too"
assert quiet_lead(board=lboard({"L/p": deep["L/p"]})) != [], "a phase with nothing waiting under it"
assert quiet_lead(waits={"hl": {"kind": "permission"}}) == [], "at a prompt for a human"
assert quiet_lead(failed={"waits"}) == [], "without Orca's wait list a prompt cannot be ruled out"
assert quiet_lead(board=lboard(entry={"closed_at": ago(5)})) == [], "a closed lead"
assert quiet_lead(board=lboard(lead={"done_at": ago(5)})) == [], "a finished lead row"
# read_ledgers reads program.json's Run and the ledger as files; a missing or broken program is None, not a guess.
saved_home, patrol.prog.HOME = patrol.prog.HOME, tmp / "programs"
(patrol.prog.HOME / "prog-a").mkdir(parents=True)
(patrol.prog.HOME / "prog-a" / "program.json").write_text(json.dumps({"run": "run_1"}))
(patrol.prog.HOME / "prog-a" / "ledger.jsonl").write_text(json.dumps(ev("landed", 190, pr=5)) + "\n\n")
got = patrol.read_ledgers(lboard())
assert got == {"prog-a": {"run": "run_1", "events": [ev("landed", 190, pr=5)]}}, got
(patrol.prog.HOME / "prog-a" / "ledger.jsonl").write_text("{broken\n")
assert patrol.read_ledgers(lboard()) == {"prog-a": None}
assert patrol.read_ledgers(lboard(entry={"closed_at": ago(5)})) == {}, "a closed lead's program is not read"
patrol.prog.HOME = saved_home

# D: a doing item that names no PR or card, a day on; one naming a PR is tidy's to judge.
board = {"doing": [{"id": "b1", "title": "follow up with infra", "owner": "someone", "next": "ask"},
                   {"id": "b2", "title": "land acme/app#3", "owner": "someone", "next": "merge"}]}
f = facts(board=board, state={"sessions": []})
_, v, memo = patrol.judge(f, {})
assert v["doing_noref"] == []
found, v, _ = patrol.judge({**f, "now": NOW + 25 * 3600}, memo)
assert codes(found) == [("D", "b1")] and v["doing_noref"] == [found[0]["card"]]
# An owner naming a live card links the item as the brief does, so it is no unreferenced item.
owned = {"doing": [{"id": "b1", "title": "follow up with infra", "owner": "feature-card", "next": "ask"}]}
f = facts(board=owned, state={"sessions": [{"id": "s-owned", "name": "feature-card", "kind": "standalone", "phase": "working"}]})
_, _, memo = patrol.judge(f, {})
found, v, _ = patrol.judge({**f, "now": NOW + 25 * 3600}, memo)
assert codes(found) == [] and v["doing_noref"] == [], found

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
i = {"id": "Iabc", "code": "I", "card": "b", "path": "/w/b", "handle": "h2", "session": "s2", "owner": None, "title": "t", "pr_open": False}
verd = {"cards": {"/w/a": "exited"}, "doing_noref": [], "load": []}
new, gone = patrol.write_round([x, i], verd, False, NOW)
assert [f["id"] for f in new] == ["Xabc", "Iabc"] and gone == []
assert [k["key"] for k in fleet.external_items()] == [f"ext:patrol:Xabc:{fleet.iso(T)}"], fleet.external_items()
new, gone = patrol.write_round([x, i], verd, False, NOW + 60)
assert new == [] and gone == [], "a finding still there is not new again"
assert patrol.pending_lines("hl", False, T)[0].startswith("PATROL X Xabc a → hl"), "routed to its owner"
assert patrol.pending_lines("hl", False, T) == [], "not again soon after"
assert patrol.pending_lines("hl", False, T + dt.timedelta(hours=3, minutes=59)) == [], "each line ends a wait: hours apart"
assert patrol.pending_lines("hl", False, T + dt.timedelta(hours=4))[0].startswith("PATROL X Xabc")
# An I alone wakes nobody until it has stayed open an hour; the unowned go to the orchestrator.
assert patrol.pending_lines("ho", True, T + dt.timedelta(minutes=59)) == [], "a fresh I alone is held"
assert [l.split()[2] for l in patrol.pending_lines("ho", True, T + dt.timedelta(minutes=60))[:-1]] == ["Iabc"]
assert patrol.pending_lines("hz", False, T) == []
# An I on a card with an open PR is shown at once: the janitor closes that card's idle terminals (cards rule 8) before
# the hour is up, and the finding would clear unseen.
ip = {**i, "id": "Ipr", "card": "p", "path": "/w/p", "owner": "hp", "pr_open": True}
patrol.write_round([x, i, ip], verd, False, NOW + 70)
assert [l.split()[2] for l in patrol.pending_lines("hp", False, T + dt.timedelta(minutes=2))[:-1]] == ["Ipr"]
sess = [{"path": "/w/p", "linked_pr": {"key": "acme/app#1"}}, {"path": "/w/q", "linked_pr": {"key": "acme/app#2"}}]
prs = {"acme/app#1": {"state": "OPEN"}, "acme/app#2": {"state": "MERGED"}}
assert patrol.pr_open({"sessions": sess, "prs": prs}, "/w/p") and not patrol.pr_open({"sessions": sess, "prs": prs}, "/w/q")
assert patrol.pr_open({"sessions": sess, "prs": {}}, "/w/p"), "a PR whose state was not read counts as open"
assert not patrol.pr_open({"sessions": sess + [{"path": "/w/none"}], "prs": prs}, "/w/none"), "a read session, no PR"
assert patrol.pr_open({"sessions": sess, "prs": prs}, "/w/none"), "no session read for the card: unknown, not held"
assert patrol.pr_open({"prs": prs}, "/w/p"), "the collector gave no sessions at all"
by_branch = [{"id": "wt-a", "path": "/w/a"}, {"id": "wt-b", "path": "/w/b"}]
assert patrol.pr_open({"sessions": by_branch, "prs": {"k1": {"session": "wt-a", "state": "OPEN"}}}, "/w/a"), "PR found by branch"
assert not patrol.pr_open({"sessions": by_branch, "prs": {"k1": {"session": "wt-a", "state": "OPEN"}}}, "/w/b"), "another card's PR"
assert not patrol.pr_open({"sessions": by_branch, "prs": {"k1": {"session": "wt-a", "state": "MERGED"}}}, "/w/a"), "merged by branch"
assert patrol.pr_open({"sessions": by_branch, "prs": {"k1": {"session": "wt-a"}}}, "/w/a"), "state not read: unknown, not held"
# A PR linked after the I was held: the logged finding follows (op "pr"), and the line goes out without the hour.
late = {**i, "id": "Ilate", "card": "l", "path": "/w/l", "owner": "hq", "pr_open": False}
patrol.write_round([x, i, ip, late], verd, False, NOW + 72)
assert patrol.pending_lines("hq", False, T + dt.timedelta(minutes=3)) == [], "held while the card has no PR"
patrol.write_round([x, i, ip, {**late, "pr_open": True}], verd, False, NOW + 74)
assert patrol.standing()["Ilate"]["pr_open"] is True
assert [l.split()[2] for l in patrol.pending_lines("hq", False, T + dt.timedelta(minutes=3))[:-1]] == ["Ilate"]
new, gone = patrol.write_round([x, i], verd, False, NOW + 80)
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
# Needs you reads patrol.jsonl itself: a found row the round was cut short after is shown at once, one acked beside a
# round is gone, and nothing is copied into inbox-external.jsonl.
y = {**x, "id": "Eyyy", "code": "E"}
fleet.append_line(patrol.LOG, {"op": "found", **y, "detected_at": ago(0)})
assert sorted(k["key"].split(":")[2] for k in fleet.external_items()) == ["Eyyy", "Xabc"]
fleet.append_line(patrol.LOG, {"op": "ack", "id": "Eyyy", "at": ago(0)})
patrol.write_round([x, y], verd, False, NOW + 360)
assert [k["key"].split(":")[2] for k in fleet.external_items()] == ["Xabc"], "an ack written beside a round is gone"
shown = fleet.external_items()[0]
assert shown["type"] == "patrol" and shown["command"] == "orch patrol ack Xabc" and shown["key"].startswith("ext:patrol:Xabc:"), shown
assert not (fleet.state_dir() / "inbox-external.jsonl").exists(), "no copy of a finding goes to the inbox file"
# An old copy of the same key in inbox-external.jsonl (before the move) is not shown twice, and its resolve does not hide the finding.
fleet.inbox_add("patrol", "old copy", key=shown["key"][4:], source="patrol")
assert [k["key"] for k in fleet.external_items()] == [shown["key"]] and fleet.external_items()[0]["title"] != "old copy"
fleet.inbox_resolve(shown["key"][4:])
assert [k["key"] for k in fleet.external_items()] == [shown["key"]]
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

# A board that cannot be read, the DB not imported yet among them, fails the board source: D is kept and its clocks
# keep running.
try:
    patrol.read_board()
    raise AssertionError("bad board read")
except patrol.store.NotMigrated:
    pass
memo0 = {"first_seen": {"D:item": NOW - 3600}}
_, _, m = patrol.judge(facts(failed={"board"}), memo0)
assert m["first_seen"] == memo0["first_seen"]
assert "board" in patrol.DEPENDS["D"]
_, _, m = patrol.judge(facts(failed={"board"}), {"first_seen": {"D:item": NOW - 3600, "O:/w/a:acme/app#1": NOW - 90000}})
assert m["first_seen"] == {"D:item": NOW - 3600, "O:/w/a:acme/app#1": NOW - 90000}, "O reads the board for holds: a board failure keeps its clock"
assert "board" in patrol.DEPENDS["O"]
_, _, m = patrol.judge(facts(failed={"state"}), {"first_seen": {"D:item": 1, "O:/w/a:acme/app#1": 2}})
assert m["first_seen"] == {"D:item": 1, "O:/w/a:acme/app#1": 2}

# A round cut short by the budget after its log rows: the rows stay, and Needs you shows them from the log.
sync0 = patrol.fleet.write_atomic
def cut(*a, **k):
    raise patrol.Budget()
patrol.fleet.write_atomic = cut
xf = facts(workers=[{"dispatchId": "dq", "agentTerminalHandle": "hq", "resource": {"worktreeId": "x::/w/q"},
                     "projection": {"outcome": "in_progress", "liveness": {"verdict": "exited"}}}])
try:
    patrol.round_once(now=NOW + 500, gather_fn=lambda now, dl, memo: ({**xf, "now": now}, []))
    raise AssertionError("no budget")
except patrol.Budget:
    pass
patrol.fleet.write_atomic = sync0
assert [r["code"] for r in patrol.open_findings().values()] == ["X"]
assert [k["type"] for k in fleet.external_items()] == ["patrol"], "shown without any later round"
patrol.ack(["--all"])
assert fleet.external_items() == []

# Output marks move only past screens read; a failed terminal list keeps the error runs and marks.
f = facts(terminals=[term("ha", "/w/a", last=5), term("hb", "/w/b", last=7)], screens={"ha": ["x"]}, fresh={"ha"})
_, _, m = patrol.judge(f, {"last_output": {"hb": 3}})
assert m["last_output"] == {"ha": 5, "hb": 3}, m["last_output"]
hist = {"ha": [["Error: x"], ["Error: x"]]}
_, _, m = patrol.judge(facts(failed={"terminals"}), {"errors": hist, "last_output": {"ha": 5}})
assert m["errors"] == hist and m["last_output"] == {"ha": 5}

# --if-due: 25 minutes, or the trigger level rising over its floor since the last round, and a rise only 15 minutes after
# the last round. A fall never starts one; a climb back to the old level after a fall is a rise (the floor remembers it).
assert patrol.due({}, "ok", NOW)
assert not patrol.due({"t": NOW - 600, "level": "ok"}, "ok", NOW)
assert not patrol.due({"t": NOW - 600, "level": "ok"}, "warn", NOW), "a rise inside the 15-minute gap waits"
assert patrol.due({"t": NOW - 16 * 60, "level": "ok"}, "warn", NOW)
assert patrol.due({"t": NOW - 16 * 60, "level": "warn"}, "danger", NOW)
assert not patrol.due({"t": NOW - 16 * 60, "level": "danger"}, "warn", NOW), "danger to warn is a fall"
assert not patrol.due({"t": NOW - 16 * 60, "level": "warn"}, "warn", NOW)
assert not patrol.due({"t": NOW - 600, "level": "danger"}, "danger", NOW)
assert patrol.due({"t": NOW - 26 * 60, "level": "danger"}, "danger", NOW)
assert patrol.due({"t": NOW - 16 * 60, "level": "danger"}, "danger", NOW, floor="warn"), "danger again after a fall to warn"
assert not patrol.due({"t": NOW - 16 * 60, "level": "danger"}, "danger", NOW, floor="danger")
# trigger_level reads pressure's floor only for the round it tracked.
patrol.PRESSURE.write_text(json.dumps({"level": "warn", "trigger_level": "danger", "trigger_floor": "ok", "floor_for": 5}))
assert patrol.trigger_level({"t": 5}) == ("danger", "ok") and patrol.trigger_level({"t": 6}) == ("danger", None)
patrol.PRESSURE.unlink()

# The workers read: the live terminal states every round, the released ones (the bulk, settled) only every
# SETTLED_EVERY, plus the newest released page every round for a worker created since. A worker that left the live
# states joins the cached ones when settled; an unsettled one, or a page of new ones, forces a full read.
def wk(i, status="completed", ts="released"):
    return {"dispatchId": i, "dispatchStatus": status, "terminalState": ts}
calls, PAGE = [], 2
LIVE = {"retained": [wk("a", "completed", "retained"), wk("b", "active", "retained")], "released": [wk("r2"), wk("r1")]}  # newest first
def fake_pages(state=None, deadline=None, first_only=False):
    calls.append((state, first_only))
    return list(LIVE.get(state, []))[:PAGE if first_only else None]
def ids(rows):
    return sorted(w["dispatchId"] for w in rows)
full = lambda: [c for c in calls if c == ("released", False)]
patrol.WORKERS_CURSOR.unlink(missing_ok=True)
assert ids(patrol.all_workers(0, NOW, fake_pages)) == ["a", "b", "r1", "r2"] and len(full()) == 1, calls
calls.clear()
patrol.all_workers(0, NOW + 60, fake_pages)
assert not full() and ("released", True) in calls, "inside the cursor: the newest page only"
LIVE["retained"] = [wk("b", "active", "retained")]  # a settled worker left the live states: it is released now
LIVE["released"] = [wk("a")] + LIVE["released"]
calls.clear()
assert ids(patrol.all_workers(0, NOW + 120, fake_pages)) == ["a", "b", "r1", "r2"] and not full()
LIVE["released"] = [wk("r3")] + LIVE["released"]  # created and released since the cursor
calls.clear()
assert ids(patrol.all_workers(0, NOW + 130, fake_pages)) == ["a", "b", "r1", "r2", "r3"] and not full(), "a new settled worker is seen"
LIVE["released"] = [wk("r4", "active")] + LIVE["released"]  # released while its Dispatch still moves
calls.clear()
assert "r4" in ids(patrol.all_workers(0, NOW + 140, fake_pages)) and len(full()) == 1, "a moving worker forces a full read"
LIVE["released"][0] = wk("r4")
calls.clear()
patrol.all_workers(0, NOW + 150, fake_pages)  # the moving one made the cursor unusable: full again, and now it is settled
assert len(full()) == 1
LIVE["released"] = [wk("r6"), wk("r5")] + LIVE["released"]  # a whole page of new workers
calls.clear()
assert ids(patrol.all_workers(0, NOW + 160, fake_pages)) == ["a", "b", "r1", "r2", "r3", "r4", "r5", "r6"] and len(full()) == 1
LIVE["retained"] = []  # an unsettled worker left the live states
calls.clear()
patrol.all_workers(0, NOW + 170, fake_pages)
assert len(full()) == 1
calls.clear()
patrol.all_workers(0, NOW + 170 + fleet.SETTLED_EVERY + 1, fake_pages)
assert len(full()) == 1, "past SETTLED_EVERY"
# An empty cursor with more than a page of new released workers, the moving one past the first page: read in full.
LIVE["retained"], LIVE["released"] = [], []
patrol.all_workers(0, NOW + 3000, fake_pages)
LIVE["released"] = [wk("s1"), wk("s2"), wk("s3", "active")]
calls.clear()
assert ids(patrol.all_workers(0, NOW + 3010, fake_pages)) == ["s1", "s2", "s3"] and len(full()) == 1
patrol.WORKERS_CURSOR.unlink(missing_ok=True)

# A dry round writes nothing.
before = patrol.LOG.read_text()
res = patrol.round_once(dry=True, now=NOW + 999, gather_fn=lambda now, dl, memo: (facts(now=now, terminals=[term("h7", "/w/z")],
                        tails={"/w/z": (idle(99), None)}), []))
assert [f["code"] for f in res["findings"]] == ["I"] and patrol.LOG.read_text() == before
assert "NEW PATROL I" in "\n".join(patrol.report(res))

# A finished card keeps nothing: its transcript still ends on an armed wake-up (parked) but no agent terminal is left to
# take it, and its Run dispatch settled, so it is neither parked nor a Run worker. One with an agent terminal still is.
fin = facts(terminals=[term("h1", "/w/live")],
            tails={"/w/done": (idle(99, parked=True), None), "/w/live": (idle(99, parked=True), None),
                   "/w/ran": (idle(99, parked=True), None)},
            workers=[{"dispatchId": "d9", "terminalState": "released", "resource": {"worktreeId": "x::/w/ran"},
                      "projection": {"outcome": "succeeded", "liveness": {"verdict": "exited"}}}])
_, v, _ = patrol.judge(fin, {})
assert set(v["activity"]) == {"/w/live"} and v["cards"] == {"/w/live": "working"}, v
# Without the terminal list (failed or cut short) no card is proven terminal-less: parked stays.
_, v, _ = patrol.judge({**fin, "failed": {"terminals"}}, {})
assert {"/w/done", "/w/live"} <= set(v["activity"]) and v["activity"]["/w/done"]["state"] == "parked", v
_, v, _ = patrol.judge({**fin, "terminals_cut": True}, {})
assert v["activity"]["/w/done"]["state"] == "parked", "a cut list may have dropped the card's terminal"

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
# The fallback patrol runs under the shared runner, in a session of its own, so what a cut round leaves ends with it.
spawned, real_popen, os.environ["AGENT_SKILLS_PATROL"] = [], reap.subprocess.Popen, "on"
reap.subprocess.Popen = lambda argv, **kw: spawned.append((argv, kw))
try:
    assert reap.spawn_patrol("test", fallback=True)
finally:
    reap.subprocess.Popen, os.environ["AGENT_SKILLS_PATROL"] = real_popen, "off"
argv, kw = spawned[0]
assert argv[2:6] == [str(reap.JOB_RUNNER), "run", "patrol-spawn", "--timeout"] and kw["start_new_session"], spawned
assert argv[-3:] == ["patrol", "--if-due", "--fallback"], argv

# tidy shows patrol's D as ❔ and never closes on it.
d = {"id": "b1", "title": "follow up with infra", "owner": "someone", "next": "ask"}
(tmp / "patrol-verdicts.json").write_text(json.dumps({"doing_noref": ["b1"]}))
got = tidy.assess({"doing": [d]}, {"sessions": []}, {}, now=T)
assert got == {"b1": {"verdict": "check", "rule": "no_ref",
                      "evidence": "PR·카드 참조 없이 24h+ · 참조를 달거나 닫기", "label": d["title"]}}, got
# The verdict file is a round old: an owner set since then links the item, whatever it still says.
d2 = {**d, "owner": "feature-card"}
got = tidy.assess({"doing": [d2]}, {"sessions": [{"id": "s-owned", "name": "feature-card", "kind": "standalone", "phase": "working"}]}, {}, now=T)
assert got == {}, got

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

# M (a model drift) is shown once per drift; a different model is a new finding, and so is the first drift again once the
# finding in between closed.
def drift(have):
    return {"id": patrol.fid("M", "s9", "sonnet", have), "code": "M", "card": "c", "title": "x", "owner": "hm"}
patrol.write_round([drift("opus")], {"cards": {}}, False, NOW + 300)
assert patrol.pending_lines("hm", False, T)[0].startswith("PATROL M"), "the drift is shown to its owner"
assert patrol.pending_lines("hm", False, T + dt.timedelta(hours=3)) == [], "and not again every 30 minutes"
patrol.write_round([drift("haiku")], {"cards": {}}, False, NOW + 400)
assert patrol.pending_lines("hm", False, T + dt.timedelta(hours=4))[0].startswith("PATROL M"), "another model is news"
patrol.write_round([drift("opus")], {"cards": {}}, False, NOW + 500)
assert patrol.pending_lines("hm", False, T + dt.timedelta(hours=5))[0].startswith("PATROL M"), "so is the first one again"
# Polling one terminal with and without the unowned scope does not make an open finding new.
patrol.write_round([{**drift("opus"), "owner": None}], {"cards": {}}, False, NOW + 550)
assert patrol.pending_lines("hu", True, T + dt.timedelta(hours=5, minutes=1))[0].startswith("PATROL M")
assert patrol.pending_lines("hu", False, T + dt.timedelta(hours=5, minutes=2)) == []
assert patrol.pending_lines("hu", True, T + dt.timedelta(hours=5, minutes=3)) == [], "still the same open drift"
# A closed drift is forgotten for every terminal, including one nobody polled while it was closed.
patrol.write_round([{**drift("opus"), "owner": None}], {"cards": {}}, False, NOW + 560)
assert patrol.pending_lines("ha", True, T + dt.timedelta(hours=5, minutes=4))[0].startswith("PATROL M")
patrol.write_round([], {"cards": {}}, False, NOW + 570)
assert patrol.pending_lines("hb", False, T + dt.timedelta(hours=5, minutes=5)) == []
patrol.write_round([{**drift("opus"), "owner": None}], {"cards": {}}, False, NOW + 580)
assert patrol.pending_lines("ha", True, T + dt.timedelta(hours=5, minutes=6))[0].startswith("PATROL M"), "new for ha"
# The same drift back after it closed (opus, back on sonnet, opus), with nothing due in between.
patrol.write_round([], {"cards": {}}, False, NOW + 600)
assert patrol.pending_lines("hm", False, T + dt.timedelta(hours=6)) == [], "a closed drift shows nothing"
patrol.write_round([drift("opus")], {"cards": {}}, False, NOW + 700)
assert patrol.pending_lines("hm", False, T + dt.timedelta(hours=7))[0].startswith("PATROL M"), "and is new when it returns"

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
assert ("I", "b") in codes(found) and v["cards"]["/w/b"] == "stalled" and v["hints"]["/w/b"] == "stalled", \
    "15+ min idle is told as I too, and the card stays stalled"
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

# B: an ended turn, 15+ min, whose last message says it waits on a background task, with nothing that will wake it:
# the transcript armed no background command or Monitor still owed a result (not parked) and Claude Code's status line
# shows none running. Run workers too. Still running, parked, a human's prompt, too soon, an unread screen, or a
# screen without that wording: not judged.
waiting = ["❯ review it", "⏺ Bash(codex exec --sandbox read-only - < /tmp/p.md)", "  ⎿  Running in the background",
           "⏺ Waiting on the background Codex run to finish; its completion", "  notice will wake me.", RULE, "❯", RULE,
           "  ~/w ⎇ main"]
korean = ["⏺ 백그라운드 Codex 리뷰가 끝나면 완료 알림을 받아 이어서 진행합니다.", RULE, "❯", RULE, "  ~/w ⎇ main"]
running = waiting[:-1] + ["  ~/w ⎇ main · 1 shell"]
shown = {"b1": waiting, "b2": korean, "b3": running, "b4": waiting, "b5": plain, "b6": waiting, "b7": waiting,
         "b8": korean}
f = facts(terminals=[term(f"b{n}", f"/w/b{n}") for n in range(1, 9)],
          tails={"/w/b1": (idle(20), None), "/w/b2": (idle(40), None), "/w/b3": (idle(20), None),
                 "/w/b4": (idle(20, parked=True), None), "/w/b5": (idle(20), None), "/w/b6": (idle(5), None),
                 "/w/b7": (idle(20), None), "/w/b8": (idle(20), None)},
          waits={"b7": {"kind": "permission"}},
          workers=[{"dispatchId": "d8", "resource": {"worktreeId": "x::/w/b8"},
                    "projection": {"outcome": "in_progress", "liveness": {"verdict": "live"}}}],
          read=lambda h: shown[h])
found, v, _ = patrol.judge(f, {})
bg = sorted((x for x in found if x["code"] == "B"), key=lambda x: x["card"])
assert codes(bg) == [("B", "b1"), ("B", "b2"), ("B", "b8")], found
fleet_shown = {**shown, "b1": waiting[:-1] + [fleet_bar], "b2": korean[:-1] + [monitor_bar]}
bg2 = [x for x in patrol.judge({**f, "screens": {}, "read": lambda h: fleet_shown[h]}, {})[0] if x["code"] == "B"]
assert sorted(codes(bg2)) == [("B", "b1"), ("B", "b8")], "the waiting-sessions count hides no B finding; a Monitor does"
why = "백그라운드 작업을 기다리지만 깨울 작업이 없음"
assert v["check"] == {"/w/b1": why, "/w/b2": why, "/w/b8": why}, v["check"]
assert "Waiting on the background Codex run" in bg[0]["title"] and bg[0]["handle"] == "b1", bg[0]["title"]
assert v["cards"]["/w/b8"] == "run" and v["cards"]["/w/b3"] == "working", v["cards"]
assert ("I", "b5") in codes(found) and "/w/b5" not in v["check"], "an idle card without that wording stays I only"
assert [x["id"] for x in patrol.judge(f, {})[0] if x["code"] == "B"] == [x["id"] for x in bg], "the same turn, the same finding"
assert not [x for x in patrol.judge({**f, "screens": {}, "read": unreadable}, {})[0] if x["code"] == "B"], \
    "an unread screen is not judged"
assert not [x for x in patrol.judge({**f, "failed": {"waits"}}, {})[0] if x["code"] == "B"], \
    "without Orca's wait list a prompt for a human cannot be ruled out"
for lines in (plain, hooked, notice, ["⏺ Started the review in the background."] + plain[1:],
              ["⏺ The background run completed; results applied."] + plain[1:],
              ["⏺ 백그라운드 작업이 완료되어 결과를 반영했습니다."] + plain[1:],
              ["⏺ Received the background task completion notification; all results are applied."] + plain[1:],
              ["⏺ 백그라운드 작업 완료 알림을 받았고 결과를 반영했습니다."] + plain[1:],
              ["⏺ No need to wait on the background run any more: it is done."] + plain[1:],
              ["⏺ 백그라운드 작업은 기다릴 필요 없이 끝났습니다."] + plain[1:],
              ["⏺ The background task took 30 seconds to complete; results are applied."] + plain[1:],
              ["⏺ I waited for the background task to finish, then applied the results."] + plain[1:],
              ["⏺ 백그라운드 작업을 기다리던 중 완료 알림을 받아 결과를 반영했습니다."] + plain[1:],
              ["⏺ I was waiting for the background review, but it finished successfully and I applied all findings.",
               "  The task is complete."] + plain[1:],
              ["⏺ After waiting for the background review to finish, I applied its findings. All checks pass."]
              + plain[1:],
              ["⏺ While waiting on the background build I fixed the lint errors; the build has passed."] + plain[1:],
              ["⏺ I don’t wait on the background run any more: it is done."] + plain[1:]):
    assert patrol.bgwait_signal(lines) is None, lines
for text in ("I'll continue once the background review completes.", "백그라운드 리뷰가 끝나기를 기다리는 중입니다.",
             "백그라운드 CI 를 기다립니다.", "I'm waiting on the background Codex review; its notice will wake me.",
             "PR is up. Still waiting for the background CI run.",
             "I’m waiting on the background Codex run to finish; its completion notice will wake me.",
             "No need to wait for CI; it has passed. Waiting on the background review to finish; its completion "
             "notice will wake me."):
    assert patrol.bgwait_signal([f"⏺ {text}"] + plain[1:]), text
assert patrol.bgwait_signal(waiting).startswith("Waiting on the background Codex run to finish; its completion notice")
assert patrol.DEPENDS["B"] == {"terminals", "waits"}

# T: a config address naming a terminal Orca no longer lists. Placeholders only.
DEAD, ALIVE = "term_0FAKE-dead-handle", "term_0FAKE-live-handle"
config = {"reap": {"inbox_to": "run:run_0FAKE", "pressure": {"notify_to": DEAD, "always_on": ["term_is_a_list_item"]}},
          "prs": {"inbox_to": ALIVE}, "mentions": {"inbox_to": "dispatch:ctx_0FAKE"}, "cron": 5}
listed = [term(ALIVE, "/w/live"), term("term_is_a_list_item", "/w/other")]
dead, warning = patrol.dead_handles(config, listed)
assert dead == [("reap.pressure.notify_to", DEAD)] and warning is None, (dead, warning)
fs, _, _ = patrol.judge(facts(dead_handles=dead), {})
f = next(x for x in fs if x["code"] == "T")
assert codes(fs) == [("T", "reap.pressure.notify_to")] and f["owner"] is None, fs
assert DEAD[:12] in f["title"] and DEAD not in f["title"] and "run:<id>" in f["title"], f
assert "T" not in patrol.NEEDS_YOU and patrol.LABEL["T"]  # warn: shown to the orchestrator, no dashboard item

# A live handle, a run: address and an empty config find nothing.
assert patrol.dead_handles({"prs": {"inbox_to": ALIVE}}, listed) == ([], None)
assert patrol.dead_handles({"prs": {"inbox_to": "run:run_0FAKE"}}, []) == ([], None)
assert patrol.dead_handles(None, listed) == ([], None)

# Orca unread or cut short: one warning line, no finding, and a standing finding is kept. No handle in the config
# has nothing to warn about.
dead, warning = patrol.dead_handles(config, [], readable=False)
assert dead == [] and warning and "\n" not in warning and "run:<id>" not in warning, (dead, warning)
assert patrol.dead_handles({"prs": {"inbox_to": "run:run_0FAKE"}}, [], readable=False) == ([], None)
assert patrol.DEPENDS["T"] == {"terminals", "handles"}

conf = tmp / "agent-skills-config.json"
conf.write_text(json.dumps(config))
conf0, orca0, patrol.CONFIG = patrol.CONFIG, patrol.prog.orca_json, conf


def orca_terminals(rows, truncated=False):
    def fake(*a):
        if a[:2] == ("terminal", "list"):
            return {"terminals": rows, "truncated": truncated}
        raise SystemExit("not this source")
    return fake


try:
    patrol.prog.orca_json = orca_terminals(listed)
    got, notes = patrol.gather(NOW, time.time() + 100, {})
    assert got["dead_handles"] == [("reap.pressure.notify_to", DEAD)] and "handles" not in got["failed"], got["failed"]
    assert not [n for n in notes if n.startswith("config handles")], notes

    patrol.prog.orca_json = orca_terminals(listed, truncated=True)  # a page without the handle says nothing about it
    got, notes = patrol.gather(NOW, time.time() + 100, {})
    assert got["dead_handles"] == [] and "handles" in got["failed"], got["failed"]
    assert len([n for n in notes if n.startswith("config handles")]) == 1, notes

    def no_orca(*a):
        raise SystemExit("orca: unreachable")
    patrol.prog.orca_json = no_orca
    got, notes = patrol.gather(NOW, time.time() + 100, {})
    assert got["dead_handles"] == [] and {"terminals", "handles"} <= got["failed"], got["failed"]
    assert len([n for n in notes if n.startswith("config handles")]) == 1, notes

    # An unreadable config is not one with no handles: the source fails, so a standing T stays. No file is fine.
    patrol.prog.orca_json = orca_terminals(listed)
    conf.write_text("{not json")
    got, notes = patrol.gather(NOW, time.time() + 100, {})
    assert got["dead_handles"] == [] and "handles" in got["failed"], got["failed"]
    assert patrol.own_sources(got)["handles"] == "failed" and any("agent-skills.json" in n for n in notes), notes
    conf.unlink()
    got, notes = patrol.gather(NOW, time.time() + 100, {})
    assert got["dead_handles"] == [] and "handles" not in got["failed"], got["failed"]
    assert patrol.own_sources(got)["handles"] == "ok"
finally:
    patrol.prog.orca_json, patrol.CONFIG = orca0, conf0

# The real file lifecycle: found, kept while Orca is unread, cleared once the setting changes.
fresh = lambda **over: facts(now=NOW + 7000, **over)
found = [("reap.pressure.notify_to", DEAD)]
one = patrol.round_once(now=NOW + 7000, gather_fn=lambda now, dl, memo: (fresh(dead_handles=found), []))
fid_t = next(x["id"] for x in one["findings"] if x["code"] == "T")
assert fid_t in one["new"]
two = patrol.round_once(now=NOW + 7100, gather_fn=lambda now, dl, memo: (fresh(failed={"terminals", "handles"}), []))
assert fid_t in patrol.standing() and fid_t not in two["cleared"]
three = patrol.round_once(now=NOW + 7200, gather_fn=lambda now, dl, memo: (fresh(), []))
assert fid_t in three["cleared"] and fid_t not in patrol.standing()

# --fallback (reap-hard's beat) is due on the 25-minute mark, below reap-hard's 30-minute period: with the patrol job
# off, a round runs at every reap-hard pass, not every second one. Two rounds never come closer than the mark, because
# each starts only when the last is due. A rise in pressure still starts one at once.
assert patrol.due({"t": NOW - 26 * 60}, "ok", NOW) and not patrol.due({"t": NOW - 24 * 60}, "ok", NOW, patrol.FALLBACK_DUE_S)
assert patrol.due({"t": NOW - 26 * 60}, "ok", NOW, patrol.FALLBACK_DUE_S)
assert patrol.due({"t": NOW - 16 * 60, "level": "ok"}, "warn", NOW, patrol.FALLBACK_DUE_S)
assert patrol.FALLBACK_DUE_S < 1800, "reap-hard's default period"
last, rounds_at = NOW - 3600, []
for k in range(1, 7):  # six passes, 30 minutes apart, no patrol job and no pressure change
    t = NOW + k * 1800
    if patrol.due({"t": last, "level": "ok"}, "ok", t, patrol.FALLBACK_DUE_S):
        last = t
        rounds_at.append(k)
assert rounds_at == [1, 2, 3, 4, 5, 6], rounds_at
# With the job on at :00 and :30 and reap-hard's pass at :27, whoever comes first runs the round: two rounds within
# one job beat never happen.
t0, runs = NOW, []
last = t0
for minute in range(1, 181):
    now = t0 + minute * 60
    who = "job" if minute % 30 == 0 else "hard" if minute % 30 == 27 else None
    if who and patrol.due({"t": last, "level": "ok"}, "ok", now, patrol.DUE_S if who == "job" else patrol.FALLBACK_DUE_S):
        last = now
        runs.append(minute)
assert all(b - a >= 25 for a, b in zip(runs, runs[1:])), runs
# One round at a time across the round and its cleanup scans: a second one meanwhile does nothing.
with patrol.run_lock() as first:
    assert first
    with patrol.run_lock() as second:
        assert not second
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        patrol.patrol_round(["--once", "--dry-run"])
    assert "another round is running" in out.getvalue(), out.getvalue()

# A round cut by its budget, or whose candidate scans were cut, exits 1: the Orca job record must not read it as clean.
saved_round_once, saved_phase = patrol.round_once, patrol.candidates_phase
try:
    def cut_round(*a, **k):
        raise patrol.Budget()
    patrol.round_once = cut_round
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            patrol.patrol_round(["--once", "--dry-run"])
        raise AssertionError("a cut round exits non-zero")
    except SystemExit as e:
        assert e.code == 1, e.code
    patrol.round_once = lambda *a, **k: {"verdicts": {}, "notes": []}
    patrol.candidates_phase = lambda res, dry=False, collect=None: {"sources": {"candidates": "timeout"}}
    patrol.report = lambda res: ["report"]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            patrol.patrol_round(["--once", "--dry-run"])
        raise AssertionError("cut candidate scans exit non-zero")
    except SystemExit as e:
        assert e.code == 1, e.code
    patrol.candidates_phase = lambda res, dry=False, collect=None: {"sources": {"candidates": "ok"}}
    with contextlib.redirect_stdout(io.StringIO()):
        patrol.patrol_round(["--once", "--dry-run"])  # a clean round returns
finally:
    patrol.round_once, patrol.candidates_phase = saved_round_once, saved_phase

print("test_patrol: ok")
