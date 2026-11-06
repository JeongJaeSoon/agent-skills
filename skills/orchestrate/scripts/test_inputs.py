"""Offline tests for rowstate.inputs(): what the planned units.py reads, exported from the brief's own render, on invented
data. The brief itself must not change by being asked (that is test_brief_rows / test_status_lines / the render compare).

Run: python3 test_inputs.py
"""
import datetime as dt, json, os, pathlib, sys, tempfile

HERE = pathlib.Path(__file__).parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-inputs-"))
env = {"ORCH_FLEET_STATE": str(tmp / "dashboard"), "ORCH_BRIEF_LAST": str(tmp / "brief-last"),
       "ORCH_FLEET_CONFIG": str(tmp / "none.json"), "AGENT_SKILLS_STATE": str(tmp / "state"), "TZ": "UTC",
       "AGENT_SKILLS_CONFIG": str(tmp / "none.json"), "PROGRAMS_HOME": str(tmp / "programs")}
os.environ.update(env)
sys.path.insert(0, str(HERE))
import board, fleet, rowstate, store  # noqa: E402

now = fleet.utcnow()
sd = fleet.state_dir()
ago = lambda m: fleet.iso(now - dt.timedelta(minutes=m))  # noqa: E731


def pr(key):
    repo, n = key.split("#")
    return {"probed_at": fleet.iso(now), "detail": {
        "key": key, "repo": repo, "number": int(n), "url": f"https://example.test/{n}", "state": "OPEN", "merged_at": None,
        "draft": False, "base": "main", "reviewers": [], "decision": "REVIEW_REQUIRED", "ci": "success",
        "merge_state": "BLOCKED", "created": ago(600), "updated": ago(60)}}


def card(sid, phase, **kw):
    return {"id": sid, "name": sid, "kind": "task", "phase": phase, "parent": "s-root", "task_title": sid, "gh_repo": "acme/app",
            "project": "acme/app", "path": f"/work/{sid}", "terminals": [{"handle": f"term_{sid}"}], "last_activity": ago(5),
            "report": None, "dispatch": None, "dispatch_status": None, **kw}


PRS = {k: pr(k) for k in ("acme/app#2", "acme/app#3")}
SESSIONS = [{**card("s-root", "working"), "kind": "orchestrator", "parent": None},
            card("s-off", "offline"),                                  # offline, no PR: gets no row
            card("s-onboard", "working"),                              # its PR is a board row's: no row of its own
            card("s-done", "working", dispatch_status="completed"),    # reported and finished, no PR ever
            card("s-linked", "working"),                               # a board row names its worktree
            card("s-plain", "working")]                                # an ordinary card: a row of its own
STATE = {"generated_at": fleet.iso(now), "sources": {"orca": {}, "runs": {}}, "sessions": SESSIONS,
         "runs": [{"id": "run_a", "coordinator": "term_s-root"}, {"id": "run_b", "coordinator": "term_gone"},
                  {"id": "run_c", "coordinator": None}],
         "prs": [{"key": "acme/app#2", "session": "s-onboard", "state": "OPEN", "candidates": ["s-onboard", "s-plain"]},
                 {"key": "acme/app#3", "session": None, "state": "OPEN", "candidates": []}]}


def write_state(state):
    fleet.write_atomic(sd / "state.json", json.dumps(state))


sd.mkdir(parents=True, exist_ok=True)
store.import_apply(checks=False)
fleet.write_atomic(sd / "prs.json", json.dumps({"prs": PRS, "me": "owner-me"}))
write_state(STATE)
board.set_row("r-pr", title="r-pr", repo="acme/app", status="moving", pr="acme/app#2", next="작업")
board.change(lambda s: board.put(s, "r-wt", dict(title="r-wt", repo="acme/app", status="moving", worktree="s-linked", next="작업")))  # as before A-ANNOT

got = rowstate.inputs(now)

# The export holds every key a reader joins on.
assert set(got) == {"facts", "state", "prs", "board_rows", "rows", "session_of", "lead", "states", "reasons", "omitted", "card",
                    "candidates", "runs", "dirty", "unknown"}, sorted(got)

# The states are the brief's own: no second judge.
assert got["states"] == rowstate.snapshot(now)["rows"] and "fleet:s-plain" in got["states"], sorted(got["states"])

# shown()'s reasons: a session gets no row of its own for exactly the reasons fleet_rows had.
assert got["omitted"] == {"s-off": ["offline"], "s-onboard": ["pr_on_board"], "s-done": ["dispatch_done"], "s-linked": ["linked"]}, got["omitted"]
assert {"s-off", "s-onboard", "s-done", "s-linked", "s-plain"} <= set(got["card"]) and "s-root" not in got["card"], got["card"]
assert "fleet:s-plain" in got["rows"] and not any(k in got["rows"] for k in ("fleet:s-off", "fleet:s-onboard", "fleet:s-done", "fleet:s-linked"))

# The stored rows come before the card links the render adds; session_of is the render's own resolution.
assert "linked" not in got["board_rows"]["r-wt"] and got["rows"]["r-wt"]["linked"] == "s-linked", (got["board_rows"]["r-wt"], got["rows"]["r-wt"])
assert got["board_rows"]["r-pr"]["status"] == "moving" and got["reasons"]["r-pr"]["raw_status"] == "moving", got["reasons"]["r-pr"]
assert got["session_of"]["r-wt"] == "s-linked" and got["session_of"]["fleet:s-plain"] == "s-plain", got["session_of"]

# Every session that claimed a PR, not only the first.
assert got["candidates"] == {"acme/app#2": ["s-onboard", "s-plain"], "acme/app#3": []}, got["candidates"]

# A Run's coordinator, and whether its session is on; a Run with no handle is unknown, not dead.
assert got["runs"] == {"run_a": {"handle": "term_s-root", "session": "s-root", "live": True},
                       "run_b": {"handle": "term_gone", "session": None, "live": False},
                       "run_c": {"handle": None, "session": None, "live": None}}, got["runs"]
assert "runs.coordinator" in got["unknown"] and "candidates" not in got["unknown"], got["unknown"]
write_state({**STATE, "sources": {"orca": {}, "runs": {"error": "down"}}})
failed = rowstate.inputs(now)
assert {r["live"] for r in failed["runs"].values()} == {None} and "sources.runs" in failed["unknown"], "a Runs source that failed says nothing of liveness"
write_state({**STATE, "generated_at": ago(120)})
stale_state = rowstate.inputs(now)
assert {r["live"] for r in stale_state["runs"].values()} == {None} and "sessions" in stale_state["unknown"], "an old state.json is not evidence of a dead coordinator"
write_state({**STATE, "sources": {"orca": {"error": "down"}, "runs": {}}})
assert "sessions" in rowstate.inputs(now)["unknown"] and rowstate.inputs(now)["runs"]["run_b"]["live"] is None
assert "sessions" not in got["unknown"] and "sources.runs" not in got["unknown"], got["unknown"]
off = {**STATE, "sessions": [{**s, "phase": "offline"} if s["id"] == "s-root" else s for s in SESSIONS]}
write_state(off)
assert rowstate.inputs(now)["runs"]["run_a"]["live"] is False, "its session is offline"

# An older collector wrote no candidates or coordinators: named, not guessed.
write_state({**STATE, "runs": [{"id": "run_a"}], "prs": [{"key": "acme/app#2", "session": "s-onboard", "state": "OPEN"}]})
old = rowstate.inputs(now)
assert old["candidates"] == {} and {"candidates", "runs.coordinator"} <= set(old["unknown"]), old["unknown"]
write_state(STATE)

# dirty comes from the janitor's plan, and only a fresh one with a row that reached git status says anything.
plan = pathlib.Path(env["AGENT_SKILLS_STATE"]) / "janitor-plan.json"
plan.parent.mkdir(parents=True, exist_ok=True)


def with_plan(at, items):
    plan.write_text(json.dumps({"at": fleet.iso(at), "items": items}))
    return rowstate.inputs(now)


rows_ = [{"kind": "card", "path": "/work/s-plain", "dirty": True}, {"kind": "card", "path": "/work/s-linked", "dirty": False},
         {"kind": "card", "path": "/work/s-done", "dirty": None}]
fresh = with_plan(now - dt.timedelta(minutes=10), rows_)
assert fresh["dirty"]["s-plain"] is True and fresh["dirty"]["s-linked"] is False, fresh["dirty"]
assert fresh["dirty"]["s-done"] is None and fresh["dirty"]["s-off"] is None, "a row that never read git status, and no row at all"
assert "dirty" not in fresh["unknown"], fresh["unknown"]
stale = with_plan(now - dt.timedelta(minutes=rowstate.DIRTY_PLAN_MAX_MIN + 1), rows_)
assert set(stale["dirty"].values()) == {None} and "dirty" in stale["unknown"], stale["dirty"]
plan.write_text(json.dumps({"at": "2026-11-05T10:00:00", "items": rows_}))  # a time with no zone
assert set(rowstate.inputs(now)["dirty"].values()) == {None}, "a plan time that cannot be compared is unknown, not an error"
plan.write_text("{not json")
broken = rowstate.inputs(now)
assert set(broken["dirty"].values()) == {None} and "dirty" in broken["unknown"], broken["dirty"]
plan.unlink()
assert "dirty" in rowstate.inputs(now)["unknown"], "no plan at all"

# session_gone is the lead rule's own test (rule 5 of judge), now shared.
sess = {"s1": {"phase": "working"}, "s2": {"phase": "offline"}}
f = {"sessions_known": True, "sessions": sess, "live": {"term_a"}}
assert rowstate.session_gone(f, {"handle": "term_a"}, "s1") is False
assert rowstate.session_gone(f, {"handle": "term_a"}, "s2") is True and rowstate.session_gone(f, {"handle": "term_a"}, "s9") is True
assert rowstate.session_gone(f, {"handle": "term_a"}, None) is False and rowstate.session_gone(f, {"handle": "term_b"}, None) is True
assert rowstate.session_gone({**f, "live": None}, {"handle": "term_b"}, None) is False, "no terminal list, no verdict of gone"
assert rowstate.session_gone({**f, "sessions_known": False}, {"handle": "term_a"}, "s1") is None

print("test_inputs: ok")
