"""Offline tests for units.build(): one row per session, row, subagent and unowned PR, on invented data.

Run: python3 test_units.py
"""
import datetime as dt, json, os, pathlib, sys, tempfile

HERE = pathlib.Path(__file__).parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-units-"))
cfg = tmp / "agent-skills.json"
cfg.write_text(json.dumps({"dashboard": {"ticket_url": "https://tracker.example.test/issue"}}))
env = {"ORCH_FLEET_STATE": str(tmp / "dashboard"), "ORCH_BRIEF_LAST": str(tmp / "brief-last"),
       "ORCH_FLEET_CONFIG": str(tmp / "none.json"), "AGENT_SKILLS_STATE": str(tmp / "state"), "TZ": "UTC",
       "AGENT_SKILLS_CONFIG": str(cfg), "PROGRAMS_HOME": str(tmp / "programs")}
os.environ.update(env)
sys.path.insert(0, str(HERE))
import board, fleet, rowstate, store, units  # noqa: E402

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
    return {"id": sid, "name": sid, "kind": "task", "phase": phase, "parent": "s-root", "orca_parent": "s-root",
            "task_title": sid, "gh_repo": "acme/app", "project": "acme/app", "path": f"/work/{sid}",
            "terminals": [{"handle": f"term_{sid}"}], "last_activity": ago(5), "report": None, "dispatch": None,
            "dispatch_status": None, **kw}


PRS = {k: pr(k) for k in ("acme/app#2", "acme/app#3", "acme/app#4", "acme/app#5", "acme/app#6")}
TICKETS = "https://tracker.example.test/issue/"
LINKED5 = {"key": "acme/app#5", "number": 5, "url": "https://example.test/5", "state": "OPEN"}
SESSIONS = [{**card("s-root", "working"), "kind": "orchestrator", "parent": None, "orca_parent": None},
            {**card("s-lead", "working"), "kind": "orchestration", "name": "lead-x",
             "subagents": [{"agent": "a2", "desc": "lead's own helper", "started_at": ago(1), "type": "x"}]},
            card("s-amb", "working", linked_pr=LINKED5),               # claims #5 with s-amb2, and Orca links it; a board row names its worktree
            card("s-amb2", "working", linked_pr={"key": None, "number": 5, "url": None, "state": "OPEN"}),  # Orca's link with no key
            card("s-new", "working", linked_pr={"key": "acme/app#7", "number": 7, "url": "https://example.test/7", "state": "OPEN"}),
            card("s-prog", "working"),
            card("s-idle", "idle"),                                    # patrol found it stalled: its fleet row is ❔
            card("s-under", "working", parent="s-lead", orca_parent="s-lead", name="ABC-12 fix", branch="user/ABC-12-fix", subagents=[{"agent": "a1"}]),
            card("s-off", "offline"),
            card("s-onboard", "working"),
            card("s-plain", "working", needs_owner=2, refs=[{"type": "issue", "key": "ABC-1", "url": "https://tracker.example.test/ABC-1"},
                                             {"type": "pr", "key": "acme/app#4", "url": "https://example.test/4"}])]
STATE = {"generated_at": fleet.iso(now), "sources": {"orca": {}, "runs": {}}, "sessions": SESSIONS,
         "runs": [{"id": "run_a", "coordinator": "term_s-root"}],
         "prs": [{"key": "acme/app#2", "session": "s-onboard", "state": "OPEN", "candidates": ["s-onboard", "s-plain"]},
                 {"key": "acme/app#3", "session": None, "state": "OPEN", "candidates": []},
                 {"key": "acme/app#4", "session": "s-under", "state": "OPEN", "candidates": ["s-under"]},
                 {"key": "acme/app#5", "session": "s-amb", "state": "OPEN", "candidates": ["s-amb", "s-amb2"]},
                 {"key": "acme/app#6", "session": "ghost", "state": "OPEN", "candidates": ["ghost"]}]}  # its session left the list

sd.mkdir(parents=True, exist_ok=True)
store.import_apply(checks=False)
fleet.write_atomic(sd / "prs.json", json.dumps({"prs": PRS, "me": "owner-me"}))
fleet.write_atomic(sd / "state.json", json.dumps(STATE))
fleet.write_atomic(sd.parent / "patrol-verdicts.json", json.dumps({"t": now.timestamp(), "check": {"/work/s-idle": "stalled on a prompt"}}))
board.set_row("r-pr", title="XYZ-7 ship it", repo="acme/app", status="moving", pr="acme/app#2", next="작업")
board.set_row("r-prog", title="no session", repo="acme/app", status="moving", next="작업", program="prog-a")
board.set_row("r-wt", title="on a worktree", repo="acme/app", status="moving", worktree="s-off", next="작업")
board.set_row("r-prog2", title="prog card", repo="acme/app", status="moving", worktree="s-prog", next="작업", program="prog-b")
board.set_row("r-amb",title="amb", repo="acme/app", status="moving", worktree="s-amb", next="작업")  # the board refuses a --pr here: Orca knows it

inp = rowstate.inputs(now)
assert units.build(inp, TICKETS) == units.build(inp, TICKETS) and units.build(inp)["units"][0]["tickets"] is not None
out = units.build(inp, TICKETS)
by = {u["id"]: u for u in out["units"]}
json.dumps(out)  # the endpoint serializes it whole

# One unit per session, row without a session, and PR nobody owns; each in exactly one group.
assert {"card:s-root", "card:s-lead", "card:s-under", "card:s-off", "card:s-onboard", "card:s-plain", "row:r-pr", "row:r-prog",
        "pr:acme/app#3"} <= set(by), sorted(by)
placed = [i for g in out["groups"] for i in g["units"]]
assert sorted(placed) == sorted(by) and len(placed) == len(set(placed)), "a unit is in one group only"
assert all(u["group"] and u["group"]["id"] for u in out["units"])
g = {g["id"]: g for g in out["groups"]}
assert g["root"]["units"] == ["card:s-root"] and out["groups"][0]["id"] == "root"
assert set(g["lead:s-lead"]["units"]) >= {"card:s-lead", "card:s-under"} and g["lead:s-lead"]["lead"] == "card:s-lead"
assert "agent:a1" not in by or by["agent:a1"]["group"]["id"] == "lead:s-lead"
assert "row:r-prog" in g["project:prog-a"]["units"] and "card:s-plain" in g["solo"]["units"]
assert by["card:s-under"]["group"]["parent_source"] == "orca" and by["card:s-plain"]["group"]["parent_source"] == "orca"

# Two candidates: the PR is linked to neither session and is counted as link unknown.
assert out["link_unknown"] == ["acme/app#2", "acme/app#5"] and out["counts"]["link_unknown"] == 2, out["link_unknown"]
# Neither the PR a render fills into a row, nor Orca's own link (with its key, or without one) re-links it to a session.
assert not inp["board_rows"]["r-amb"].get("pr") and inp["rows"]["r-amb"]["pr"] == "acme/app#5"
assert by["card:s-amb"]["prs"] == [] and by["card:s-amb2"]["prs"] == [], by["card:s-amb"]["prs"]
assert by["card:s-amb"]["pr_unknown"] == ["acme/app#5"] and "pr:acme/app#5" in by, "no stored row names it: its own unit"
# A PR whose claimant left the session list is not lost; Orca's link read before the collector keeps its number and url;
# a linked program row puts the card under its program.
assert "pr:acme/app#6" in by and by["pr:acme/app#6"]["link"] == "none", sorted(by)
new = by["card:s-new"]["prs"]
assert [(p["key"], p["number"], p["url"]) for p in new] == [("acme/app#7", 7, "https://example.test/7")], new
assert by["card:s-prog"]["group"]["id"] == "project:prog-b" and by["card:s-prog"]["group"]["by"] == "project", by["card:s-prog"]["group"]
# A subagent follows its host: a lead's own helper sits in the lead's group, not Solo.
assert by["agent:a2"]["group"]["id"] == "lead:s-lead", by["agent:a2"]["group"]
for sid in ("s-onboard", "s-plain"):
    assert not any(p["key"] == "acme/app#2" for p in by[f"card:{sid}"]["prs"]), sid
assert [p["key"] for p in by["card:s-under"]["prs"]] == ["acme/app#4"] and by["card:s-under"]["prs"][0]["via"] == "session"
assert by["pr:acme/app#3"]["link"] == "none" and by["pr:acme/app#3"]["kind"] == "pr"
assert "pr:acme/app#2" not in by, "a stored board row names it"

# The same equality for a card patrol found stalled, and the root never takes a PR from the report it digests.
idle = next(s for s in SESSIONS if s["id"] == "s-idle")
ri = units.synthetic_row(idle, fleet.prs_by_session(STATE["prs"]), inp["prs"])
assert inp["states"]["fleet:s-idle"]["status"] == "check" and rowstate.judged(ri, inp["facts"], None, "s-idle") == inp["states"]["fleet:s-idle"]
root = units.synthetic_row({**SESSIONS[0], "report": {"pr": "acme/app#4"}}, fleet.prs_by_session(STATE["prs"]), inp["prs"])
assert root["kind"] == "lead" and root["pr"] is None, root

# A synthetic row is judged by rowstate.judged() on the dict fleet_rows would write: the same state as the brief's own.
real = inp["states"]["fleet:s-plain"]
assert by["card:s-plain"]["rows"][0]["via"] == "own" and by["card:s-plain"]["state"] == real
r = units.synthetic_row(next(s for s in SESSIONS if s["id"] == "s-plain"), fleet.prs_by_session(STATE["prs"]), inp["prs"])
assert r == inp["rows"]["fleet:s-plain"], (r, inp["rows"]["fleet:s-plain"])
assert rowstate.judged(r, inp["facts"], None, "s-plain") == real
syn = by["card:s-onboard"]["rows"][0]
assert syn["via"] == "synthetic" and syn["id"] == "fleet:s-onboard"
sr = units.synthetic_row(next(s for s in SESSIONS if s["id"] == "s-onboard"),fleet.prs_by_session(STATE["prs"]), inp["prs"])
assert syn["state"] == rowstate.judged(sr, inp["facts"], None, "s-onboard"), "a session with no row is judged, not guessed"
assert by["card:s-off"]["rows"][0]["via"] == "worktree" and by["card:s-off"]["state"] == inp["states"]["r-wt"]

# The brief's own states are untouched by asking.
assert rowstate.snapshot(now)["rows"] == inp["states"]

# Tickets are computed here once: the card's link, then ids in name, branch, row titles.
assert [(t["id"], t["from"]) for t in by["card:s-under"]["tickets"]] == [("ABC-12", "name")]
assert by["card:s-under"]["tickets"][0]["url"] == "https://tracker.example.test/issue/ABC-12"
assert by["card:s-plain"]["tickets"][0] == {"id": "ABC-1", "url": "https://tracker.example.test/ABC-1", "from": "refs"}
assert [t["id"] for t in by["row:r-pr"]["tickets"]] == ["XYZ-7"]
got = units.tickets_of({"name": "UTF-8 SHA-256 XYZ-3, ABC-12 ABC-12", "branch": "feat/ABC-12_fix xABC-9 ABC-9x feat_ABC-3", "task_title": "ABC-3 ABC-4"},
                       ["row ABC-5", "ABC-12"], "")
assert [(t["id"], t["from"]) for t in got] == [("XYZ-3", "name"), ("ABC-12", "name"), ("ABC-3", "branch"), ("ABC-5", "row"), ("ABC-4", "title")], got
assert all(t["url"] is None for t in got), "no ticket_url, no link"
# Owner's turn is the judged status, not the inbox count: a card whose items the agent answers is not the owner's.
assert by["card:s-plain"]["owner_turn"] is (by["card:s-plain"]["state"]["status"] == "owner") is False

# dirty is the janitor's answer or unknown, passed through unread.
assert by["card:s-plain"]["dirty"] is None and set(by["card:s-plain"]) >= {"dirty", "owner_turn", "state"}
plan = pathlib.Path(env["AGENT_SKILLS_STATE"]) / "janitor-plan.json"
plan.parent.mkdir(parents=True, exist_ok=True)
plan.write_text(json.dumps({"at": fleet.iso(now), "items": [{"kind": "card", "path": "/work/s-plain", "dirty": False},
                                                            {"kind": "card", "path": "/work/s-under", "dirty": True}]}))
d = {u["id"]: u for u in units.build(rowstate.inputs(now))["units"]}
assert d["card:s-plain"]["dirty"] is False and d["card:s-under"]["dirty"] is True and d["card:s-root"]["dirty"] is None

# An older collector wrote no candidates: its single owner is used, nothing is guessed beyond it.
fleet.write_atomic(sd / "state.json", json.dumps({**STATE, "prs": [{"key": "acme/app#4", "session": "s-under", "state": "OPEN"}]}))
old = units.build(rowstate.inputs(now))
assert [p["key"] for p in {u["id"]: u for u in old["units"]}["card:s-under"]["prs"]] == ["acme/app#4"]
assert "candidates" in old["unknown"] and old["link_unknown"] == []

# No state at all: no session units, the board's rows still stand.
(sd / "state.json").unlink()
none = units.build(rowstate.inputs(now))
assert {u["kind"] for u in none["units"]} == {"item"} and none["counts"]["units"] == 5 and "state" in none["unknown"], none["counts"]
print("test_units: ok")
