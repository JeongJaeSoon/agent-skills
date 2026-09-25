"""Offline tests for dash.py: every source from fixtures, a failing source, series dedupe, atomic writes.

Run: python3 test_dash.py
"""
import http.server, json, os, pathlib, sys, tempfile, threading, urllib.request, urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import dash, dash_demo

root = pathlib.Path(tempfile.mkdtemp(prefix="test-dash-"))
os.environ.update(dash_demo.build(root), ORCH_FLEET="off")  # the shims do not answer the fleet's calls
store, fx = root / "programs", root / "fixtures"
A, B = "launchpad-ga", "billing-q4"


def state(slug):
    return json.loads((store / slug / "dashboard" / "state.json").read_text())


def history(slug):
    return (store / slug / "dashboard" / "history.jsonl").read_text().splitlines()


def append(slug, **row):
    row = {"ts": dash.iso(dash.utcnow()), **row}
    with (store / slug / "ledger.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")


# All four sources from fixtures.
st = dash.collect(A)
s = st["summary"]
assert st["errors"] == [], st["errors"]
assert all(st["sources"][k]["ok"] for k in ("tracker", "github", "orca", "ledger")), st["sources"]
assert (s["predicate_done"], s["predicate_total"]) == (5, 7), s
assert s["main"] == "green" and s["cap"] == 4 and s["in_flight"] == 3, s
assert s["ready_prs"] == [207, 208] and s["landed_total"] == 6 and s["landed_24h"] == 3, s
assert s["idle_waiting"] == ["ctx_a1000009"] and s["untriaged"] == ["ACME-127"], s
assert s["stale_workers"] == ["ctx_a1000010"], s["stale_workers"]
w10 = next(w for w in st["workers"] if w["dispatch"] == "ctx_a1000010")
assert w10["liveness_reason"] == "stale_status" and w10["seen_at"] and w10["since"] != w10["seen_at"], w10
assert [c["ticket"] for c in st["landed_open"]] == ["ACME-105"], st["landed_open"]
sup = {t["id"]: t["superseded_by"] for t in st["tasks"]}
assert sup["task_107x"] == "task_107" and sup["task_103"] is None, sup
green = next(a for a in st["activity"] if a["kind"] == "main_green")
assert green["sha"] == "m206", green
# Stages: each collect visits one more level; ACME-121 sits under ACME-103, so the third one settles.
assert st["stages"]["stages"][0]["unvisited"], st["stages"]
dash.collect(A, sources=("stages",))
st2 = dash.collect(A, sources=("stages",))
assert [(x["id"], x["done"], x["started"], x["total"], x["unvisited"]) for x in st2["stages"]["stages"]] == [
    ("ACME-100", 3, 0, 3, 0), ("ACME-110", 2, 2, 4, 0), ("ACME-111", 0, 1, 1, 0)], st2["stages"]
# A cycle left in the cached tree (a reparent seen half-way) must not hang the crawl.
tree_p = dash.program_dir(A) / "dashboard" / "tree.json"
tree = json.loads(tree_p.read_text())
tree["kids"]["ACME-101"] = ["ACME-100"]
tree_p.write_text(json.dumps(tree))
assert dash.collect(A, sources=("stages",))["sources"]["stages"]["ok"]
cfg_a, ev_a = json.loads((store / A / "program.json").read_text()), dash.read_jsonl(store / A / "ledger.jsonl")
closed = [{**i, "state_type": "completed"} if i["id"] == "ACME-127" else i for i in st["issues"]]
assert dash.summarize(cfg_a, ev_a, closed, st["workers"], dash.utcnow(), [])["untriaged"] == [], "a closed follow-up needs no triage"
assert s["derived_total"] == 8 and s["admitted"] == 2 and s["parked"] == 5, s
assert s["next"].startswith("unstick first: 1 PR"), s["next"]  # same rule as `prog.py status`: a PR has waited 3h+
prs = {p["number"]: p for p in st["prs"]}
assert (prs[207]["ci"], prs[207]["verdict"], prs[207]["review_rounds"], prs[207]["ticket"]) == ("pass", "pass", 3, "ACME-106")
assert (prs[208]["ci"], prs[208]["verdict"]) == ("pending", "none")
assert "ACME-090" not in {i["id"] for i in st["issues"]}, "issues the program never touched stay out"
assert st["landing"]["main"]["holder"]["pr"] == 207 and [q["pr"] for q in st["landing"]["main"]["queue"]] == [208]
assert len(st["series"]) > 10 and st["series"][-1]["done"] == 6, "backfilled from ledger and tracker timestamps"
assert [n["kind"] for n in st["notes"]] == ["risk", "decision", "digest"]
lo = st["land_order"]
assert [(e["pr"], e["state"]) for e in lo][:1] == [(207, "ready")] and lo[1]["pr"] == 208, lo
assert lo[1]["ticket"] == "ACME-125" and "behind base" in lo[1]["reasons"] and lo[1]["state"] != "ready"
assert st["summary"]["oldest_open_pr"]["pr"] == 207 and 11.9 < st["summary"]["oldest_open_pr"]["age_h"] < 12.1
dep = next(a for a in st["activity"] if a["kind"] == "dep")
assert dep["text"] == "dependency recorded (ACME-107) · after=ACME-106", dep["text"]
assert (store / A / "dashboard" / "pr_rows.json").exists()
assert st["run_objective"].startswith("Launchpad GA")
g = st["graph"]
status = {n["id"]: n["status"] for n in g["nodes"]}
assert (status["ACME-106"], status["ACME-101"], status["task_ver"]) == ("landing", "done", "waiting"), status
assert status["ACME-125"] in ("blocked", "in_progress"), status  # follows the land order's verdict on #208
assert ["ACME-106", "ACME-107"] in g["edges"] and ["ACME-125", "task_ver"] in g["edges"]
assert g["critical"] == ["ACME-106", "ACME-107", "task_ver"], g["critical"]

# Token usage: repeated rows of one message count once, and a rescan reads only new bytes.
tok = st["usage"]["total"]["total"]
w107 = next(w for w in st["workers"] if w["dispatch"] == "ctx_a1000009")
assert tok > 0 and w107["tokens"]["total"] > 0 and st["series"][-1]["tokens"] == tok
assert next(w for w in st["workers"] if w["model"] == "gpt-6-sol")["tokens"] is None, "codex workers are n/a"
assert dash.collect(A, sources=("orca",))["usage"]["total"]["total"] == tok, "rescan must not double count"
tr = pathlib.Path(os.environ["CLAUDE_PROJECTS_DIR"]) / "-workspaces-acme-107" / "session.jsonl"
extra = {"input_tokens": 10, "output_tokens": 20, "cache_creation_input_tokens": 30, "cache_read_input_tokens": 40}
with tr.open("a") as f:
    for _ in range(3):
        f.write(json.dumps({"type": "assistant", "timestamp": dash.iso(dash.utcnow()),
                            "message": {"id": "msg_new", "usage": extra}}) + "\n")
assert dash.collect(A, sources=("orca",))["usage"]["total"]["total"] == tok + 100

# A failing source records an error and keeps what was there; its timestamp stays at the last success.
st = dash.collect(B)
assert st["errors"] == ["github: GraphQL: API rate limit exceeded for user ID 1234."], st["errors"]
assert st["sources"]["github"]["ok"] is False and st["sources"]["github"]["updated_at"] is None
assert st["summary"]["main"] == "red" and st["summary"]["next"].startswith("SAFETY STOP")
assert st["summary"]["human_wait_prs"] == [303]
gh_fixture = fx / "gh" / "acme__billing.json"
gh_fixture.write_text(json.dumps([dash_demo._pr(dash.utcnow(), "acme/billing", 303, "Tax ID validation", "bill-13-tax", "OPEN", 5)]))
good = dash.collect(B)
assert good["errors"] == [] and [p["number"] for p in good["prs"]] == [303]
# Closed PRs come without checks (the window query that timed out); one seen open keeps its CI and rounds.
assert (good["prs"][0]["ci"], good["prs"][0]["state"]) == ("pass", "open")
gh_fixture.write_text(json.dumps([dash_demo._pr(dash.utcnow(), "acme/billing", 303, "Tax ID validation", "bill-13-tax",
                                                "MERGED", 5, 1, ci="fail")]))
st = dash.collect(B)
assert (st["prs"][0]["ci"], st["prs"][0]["state"]) == ("pass", "merged"), st["prs"]
moved = dash_demo._pr(dash.utcnow(), "acme/billing", 303, "Tax ID validation", "bill-13-tax", "MERGED", 5, 1, head="f" * 40)
gh_fixture.write_text(json.dumps([moved]))
assert dash.collect(B)["prs"][0]["ci"] == "unknown", "a head pushed after the last look was never checked"
running = dash_demo._pr(dash.utcnow(), "acme/billing", 304, "Tax rounding", "bill-14-round", "OPEN", 2, ci="pending")
gh_fixture.write_text(json.dumps([running]))
assert dash.collect(B)["prs"][0]["ci"] == "pending"
gh_fixture.write_text(json.dumps([dict(running, state="MERGED")]))
assert dash.collect(B)["prs"][0]["ci"] == "unknown", "a run seen going is not shown running forever"
assert {p["ci"] for p in state(A)["prs"] if p["state"] != "open"} == {"unknown"}, "never seen open: not fetched"
gh_fixture.write_text(json.dumps([dash_demo._pr(dash.utcnow(), "acme/billing", 303, "Tax ID validation", "bill-13-tax", "OPEN", 5)]))
good = dash.collect(B)
gh_fixture.write_text(json.dumps({"fail": "HTTP 502"}))
st = dash.collect(B)
assert [p["number"] for p in st["prs"]] == [303], "last good PR section survives a failure"
assert st["sources"]["github"]["updated_at"] == good["sources"]["github"]["updated_at"]
assert st["errors"] == ["github: HTTP 502"]
# A ledger-only refresh does not re-run GitHub, so its error stays visible until the next full collect.
st = dash.collect(B, sources=())
assert st["errors"] == ["github: HTTP 502"] and [p["number"] for p in st["prs"]] == [303]
# An Orca error envelope is a source error that keeps the last workers; a missing CLI never crashes.
orca_fixture = fx / "orca" / "run_demo_alpha.json"
saved = orca_fixture.read_text()
orca_fixture.write_text(json.dumps({"ok": False, "error": {"code": "run_not_found"}}))
st = dash.collect(A, sources=("orca",))
assert st["errors"] == ['orca: {"code": "run_not_found"}'] and st["summary"]["in_flight"] == 3, st["errors"]
orca_fixture.write_text(saved)
assert dash.collect(A, sources=("orca",))["errors"] == []
try:
    dash.sh(["dash-test-no-such-cli"])
    raise AssertionError("expected SourceError")
except dash.SourceError as e:
    assert "not found" in str(e)

# Series: unchanged values add no row; a new landing adds exactly one.
n = len(history(A))
dash.collect(A, sources=())
dash.collect(A, sources=())
assert len(history(A)) == n, "unchanged collect must not grow history.jsonl"
append(A, ev="lock_released", pr=207, base="main")
append(A, ev="landed", pr=207, sha="m207")
st = dash.collect(A, sources=())
assert len(history(A)) == n + 1 and st["series"][-1]["landed"] == 7
assert st["landing"]["main"]["holder"] is None and [q["pr"] for q in st["landing"]["main"]["queue"]] == [208]
assert [e["pr"] for e in st["land_order"]][-1:] == [208], "a ledger-only refresh re-ranks from the saved open rows"
assert st["activity"][0]["text"] == "landed #207 as m207", st["activity"][0]
assert st["summary"]["main"] == "pending"

# No lock events at all: no holder, queue from ready PRs only.
assert dash.landing_of({}, [], []) == {}

# Atomic write: a crash while replacing leaves the old state.json intact and no temp file behind.
before = (store / A / "dashboard" / "state.json").read_text()
real_replace = os.replace
os.replace = lambda *a: (_ for _ in ()).throw(OSError("disk full"))
try:
    dash.collect(A, sources=())
    raise AssertionError("collect should have raised")
except OSError:
    pass
finally:
    os.replace = real_replace
assert (store / A / "dashboard" / "state.json").read_text() == before
assert not [p for p in (store / A / "dashboard").iterdir() if p.name.startswith(".state.json.")]

# note appends a line the next collect shows first.
dash.cmd_note([A, "--kind", "decision", "--text", "hold ACME-107 until the quickstart copy is final", "--author", "test"])
st = dash.collect(A, sources=())
assert st["notes"][0]["text"].startswith("hold ACME-107") and st["activity"][0]["kind"] == "note"

# Serving: ETag round trip gives 304; a traversal attempt is a 404.
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), dash.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
r = urllib.request.urlopen(f"{base}/api/{A}/state")
etag = r.headers["ETag"]
assert r.status == 200 and json.loads(r.read())["slug"] == A
try:
    urllib.request.urlopen(urllib.request.Request(f"{base}/api/{A}/state", headers={"If-None-Match": etag}))
    raise AssertionError("expected 304")
except urllib.error.HTTPError as e:
    assert e.code == 304
try:
    urllib.request.urlopen(f"{base}/api/..%2F..%2Fetc/state")
    raise AssertionError("expected 404")
except urllib.error.HTTPError as e:
    assert e.code == 404
progs = json.loads(urllib.request.urlopen(f"{base}/api/programs").read())
assert [p["slug"] for p in progs] == [B, A]
assert b"<title>" in urllib.request.urlopen(f"{base}/").read()

# Answering a permission prompt is a POST like the others: this server's Host and Origin, and the page's token.
dash.HEALTH["port"] = srv.server_address[1]
answered, dash.fleet.answer_prompt = [], lambda *a: answered.append(a) or (200, {"ok": True})
body = json.dumps({"session": "wt-docs", "prompt": "p1", "action": "approve"}).encode()


def post(headers, path="/api/fleet/prompt"):
    req = urllib.request.Request(f"{base}{path}", data=body, method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        return urllib.request.urlopen(req).status
    except urllib.error.HTTPError as e:
        return e.code


host = f"127.0.0.1:{srv.server_address[1]}"
assert post({"Host": host}) == 403, "no token"
assert post({"Host": host, "X-Dash-Token": "wrong"}) == 403
assert post({"Host": f"evil.example:{srv.server_address[1]}", "X-Dash-Token": dash.TOKEN}) == 403, "rebinding name"
assert post({"Host": host, "Origin": "http://evil.example", "X-Dash-Token": dash.TOKEN}) == 403, "cross-site page"
assert answered == []
assert post({"Host": host, "Origin": f"http://{host}", "X-Dash-Token": dash.TOKEN}) == 200
assert answered == [("wt-docs", "p1", "approve")], answered
# A decision answered on the page is a line for the coordinator's terminal, through the send box's endpoint and guard.
sent, dash.fleet.send = [], lambda *a: sent.append(a) or (200, {"ok": True})
body = json.dumps({"session": "wt-coord", "handle": "term_coord", "text": "decision d1: CSV"}).encode()
assert post({"Host": host}, "/api/fleet/send") == 403 and sent == []
assert post({"Host": host, "X-Dash-Token": dash.TOKEN}, "/api/fleet/send") == 200
assert sent == [("wt-coord", "decision d1: CSV", "term_coord")], sent
srv.shutdown()

# A ledger backfilled with landings from before the series began rebuilds the series once.
cfg_path = store / A / "program.json"
cfg = json.loads(cfg_path.read_text())
early = dash.iso(dash.utcnow() - dash.dt.timedelta(days=3))
cfg["created_at"] = early
cfg_path.write_text(json.dumps(cfg))
ledger = (store / A / "ledger.jsonl").read_text()
(store / A / "ledger.jsonl").write_text(json.dumps({"ts": early, "ev": "landed", "pr": 150, "sha": "m150", "note": "backfill"})
                                        + "\n" + ledger)
assert json.loads(history(A)[0])["t"] > early
sampled = [json.loads(r) for r in history(A) if "tokens" in json.loads(r)]
st = dash.collect(A, sources=())
assert json.loads(history(A)[0])["t"] == early and st["series"][0]["landed"] == 1, history(A)[:2]
assert st["series_from"] == early and st["series"][0]["t"] == early, "the charts start where the program did"
dash.SERIES_DAYS = 1  # a program older than the window: the lines start at its edge with the values then in force
st = dash.collect(A, sources=())
dash.SERIES_DAYS = 30
rows_ = [json.loads(r) for r in history(A)]
edge = dash.prog.parse_ts(st["series_from"])
then = [r for r in rows_ if dash.prog.parse_ts(r["t"]) < edge][-1]
assert st["series"][0] == {**then, "t": st["series_from"]} and len(st["series"]) < len(rows_), st["series"][0]
kept = [r for r in map(json.loads, history(A)) if "tokens" in r]
assert [r["tokens"] for r in kept[:len(sampled)]] == [r["tokens"] for r in sampled] and sampled, "live samples keep their tokens"
assert all(k["landed"] > r["landed"] for k, r in zip(kept, sampled)), "the ledger's counts under them include the backfill"
n = len(history(A))
dash.collect(A, sources=())
assert len(history(A)) == n, "rebuilt once, then appended as usual"

assert [m.upper() for m in dash.TICKET_RE.findall("JeongJaeSoon/94s-135-prep on 2026-09-24, ACME-7")] == ["94S-135", "ACME-7"]

# A landing does not finish a ticket the tracker still has open: a ticket can take several PRs.
tasks = [{"id": "t1", "ticket": "X-1", "title": "X-1", "status": "dispatched", "deps": []}]
landed = [{"ts": early, "ev": "landed", "pr": 1, "ticket": "X-1"}]
node = lambda issues: dash.build_graph(tasks, landed, issues, [], {})["nodes"][0]["status"]
assert node([{"id": "X-1", "state_type": "started"}]) == "in_progress"
assert node([{"id": "X-1", "state_type": "completed"}]) == node([]) == node(None) == "done"
ticketless = [{"id": "t9", "ticket": None, "title": "QA lead", "status": "dispatched", "deps": []}]
assert dash.build_graph(ticketless, [{"ts": early, "ev": "landed", "pr": 3}], [], [], {})["nodes"][0]["status"] == "in_progress", \
    "a landing with no ticket does not finish every ticketless task"

# Stalls: only in-flight Claude workers, each by its transcript's last state.
now = dash.utcnow()
ago = lambda m: dash.iso(now - dash.dt.timedelta(minutes=m))
ws = [{"dispatch": "a", "outcome": "in_progress", "since": ago(60), "motion": {"state": "idle", "since": ago(20)}},
      {"dispatch": "b", "outcome": "in_progress", "since": ago(60), "motion": {"state": "idle", "since": ago(20), "parked": True}},
      {"dispatch": "c", "outcome": "in_progress", "since": ago(30), "motion": None},
      {"dispatch": "d", "outcome": "in_progress", "since": ago(30), "motion": None, "model": "gpt-6"},
      {"dispatch": "e", "outcome": "in_progress", "since": ago(90), "motion": {"state": "tool", "since": ago(50), "tool": "Bash"}},
      {"dispatch": "f", "outcome": "in_progress", "since": ago(90), "motion": {"state": "working", "since": ago(1)}},
      {"dispatch": "g", "outcome": "succeeded", "since": ago(90), "motion": {"state": "idle", "since": ago(80)}}]
assert [(x["dispatch"], x["kind"]) for x in dash.stalls(ws, now)] == [("a", "idle"), ("c", "start_unconfirmed"), ("e", "long_tool")]
held = dash.spare({"next": "stop spawning: land what is verified", "cap": 4, "in_flight": 1}, [])
assert held["slots"] == 0 and held["held"].startswith("stop spawning"), held
free = dash.spare({"next": "may spawn 3 more", "cap": 4, "in_flight": 1},
                  [{"id": "t1", "status": "completed", "deps": [], "ticket": "X-1", "title": "X-1"},
                   {"id": "t2", "status": "pending", "deps": ["t1"], "ticket": "X-2", "title": "X-2"},
                   {"id": "t3", "status": "pending", "deps": ["t2"], "ticket": "X-3", "title": "X-3"},
                   {"id": "t4", "status": "ready", "deps": [], "ticket": None, "title": "Land #12"}])
assert free == {"slots": 3, "ready": ["X-2"]}, free  # a human-gate Land task is the coordinator's, not work to start

# Transcript motion: a tool call, its result, the turn's end; a turn whose wake-up call succeeded is parked.
def motion(rows, m=None):
    m = m or {}
    for row in rows:
        m = dash.step_motion(m, row)
    return m
call = lambda t, i, name, inp: {"type": "assistant", "timestamp": t, "message": {"content": [{"type": "tool_use", "id": i, "name": name, "input": inp}]}}
result = lambda t, i, err=False: {"type": "user", "timestamp": t, "message": {"content": [{"type": "tool_result", "tool_use_id": i, "is_error": err}]}}
end = lambda t: {"type": "assistant", "timestamp": t, "message": {"content": [{"type": "text"}], "stop_reason": "end_turn"}}
m = motion([call("t1", "u1", "Bash", {"command": "gh run watch 1"})])
assert m["state"] == "tool" and m["detail"] == "gh run watch 1" and not m["parked"], m
m = motion([result("t2", "u1"), call("t3", "u2", "ScheduleWakeup", {}), result("t4", "u2"), end("t5")], m)
assert (m["state"], m["since"], m["parked"]) == ("idle", "t5", True), m
m = dash.step_motion(m, {"type": "user", "timestamp": "t6", "message": {"content": "next task"}})
assert (m["state"], m["since"], m["parked"]) == ("working", "t6", False), m
m = motion([call("t1", "u3", "ScheduleWakeup", {}), result("t2", "u3", err=True), end("t3")])
assert (m["state"], m["parked"]) == ("idle", False), m  # a refused wake-up arms nothing
m = motion([call("t1", "u4", "Bash", {"command": "sleep 99", "run_in_background": True}), result("t2", "u4"), end("t3")])
assert m["parked"], m

# A retry of a ticket is dated by its own spawn row, not the ticket's first.
ev_r = [{"ts": "2026-01-01T00:00:00Z", "ev": "spawned", "ticket": "X-1", "note": "ctx_01"},
        {"ts": "2026-01-01T05:00:00Z", "ev": "spawned", "ticket": "X-1", "note": "retry ctx_02"}]
ws_r = dash.shape_workers([{"dispatchId": "ctx_02", "taskId": "t"}, {"dispatchId": "ctx_01", "taskId": "t"}], ev_r)
assert {w["dispatch"]: w["since"] for w in ws_r} == {"ctx_01": "2026-01-01T00:00:00Z", "ctx_02": "2026-01-01T05:00:00Z"}, ws_r

# Ledger gaps: an unrecorded dispatch, a merge the ledger never saw, a landing without a main CI result.
cfg_g = {"created_at": ago(600)}
ev_g = [{"ts": ago(500), "ev": "spawned", "ticket": "X-1", "note": "ctx_aa"},
        {"ts": ago(400), "ev": "landed", "pr": 1, "sha": "s1"}, {"ts": ago(390), "ev": "main_green", "sha": "s1"},
        {"ts": ago(300), "ev": "landed", "pr": 2, "sha": "s2"}]
prs_g = [{"number": n, "state": "merged", "merged_at": ago(t)} for n, t in ((1, 400), (2, 300), (3, 200), (4, 900))]
gaps = dash.ledger_gaps(cfg_g, ev_g, [{"dispatch": "ctx_aa", "outcome": "in_progress"},
                                     {"dispatch": "ctx_bb", "outcome": "in_progress"}], prs_g, now)
assert gaps == {"spawns": ["ctx_bb"], "landings": [3], "ci": [2]}, gaps
stacked = [{"ts": ago(300), "ev": "landed", "pr": 5, "sha": "s5", "stack_top": 6},
           {"ts": ago(300), "ev": "landed", "pr": 6, "sha": "s6"}, {"ts": ago(290), "ev": "main_green", "sha": "s6"}]
assert dash.ledger_gaps(cfg_g, stacked, [], [], now)["ci"] == [], "a lower stack layer has no main CI run to record"

# ensure: starts this store's server once, then finds it; a second serve on the store refuses.
import socket, subprocess, signal
with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
ok, msg = dash.ensure(port)
assert ok and "started" in msg, msg
h = dash.health(port)
assert h["store"] == str(dash.home()) and h["version"] == dash.code_version(), h
assert dash.ensure(port) == (True, f"dashboard: http://127.0.0.1:{port}/")
again = subprocess.run([sys.executable, str(pathlib.Path(dash.__file__)), "serve", "--port", "0"], capture_output=True,
                       text=True, timeout=20)
assert again.returncode and "already serves" in again.stderr, again.stderr
os.kill(h["pid"], signal.SIGTERM)

# The real tracker adapter, when present, satisfies the same interface through TRACKER_FIXTURES.
real = pathlib.Path(__file__).resolve().parents[2] / "use-tracker/scripts/tracker.py"
if real.exists():
    os.environ["TRACKER_PY"] = str(real)
    os.environ["TRACKER_FIXTURES"] = str(fx / "tracker" / "Launchpad GA")
    st = dash.collect(A, sources=("tracker", "stages"))
    assert st["sources"]["tracker"]["ok"] and st["summary"]["predicate_done"] == 5, st["errors"]
    assert st["sources"]["stages"]["ok"] and [x["id"] for x in st["stages"]["stages"]] == ["ACME-100", "ACME-110", "ACME-111"], st["errors"]
    print("  (checked against the real use-tracker/scripts/tracker.py)")

# A current final check stops polling the program's sources; a landing after it reopens the program.
assert not dash.closed(B)
append(B, ev="predicate_verified", note="final check")
assert dash.closed(B)
append(B, ev="landed", pr=999, sha="m999")
assert not dash.closed(B)

print("dash.py collect/serve: all pass")
