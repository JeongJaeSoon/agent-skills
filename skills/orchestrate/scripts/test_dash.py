"""Offline tests for dash.py: every source from fixtures, a failing source, series dedupe, atomic writes.

Run: python3 test_dash.py
"""
import datetime as dt_mod, http.server, json, os, pathlib, re, sys, tempfile, threading, urllib.request, urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import time as dash_time
import dash, dash_demo

root = pathlib.Path(tempfile.mkdtemp(prefix="test-dash-"))
os.environ.update(dash_demo.build(root), ORCH_FLEET="off")  # the shims do not answer the fleet's calls
store, fx = root / "programs", root / "fixtures"
A, B = "launchpad-ga", "billing-q4"
assert os.environ["ORCH_SKILLS"] == "off", "the demo must not scan real transcripts"
assert os.environ["ORCH_PR_STATS"] == "off", "the demo must not search the real GitHub"
assert os.environ["ORCH_TOKENS"] == "off", "the demo must not read real transcripts for token use"


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
foreign = dash.shape_workers([{"dispatchId": "foreign", "resource": {"worktreeId": "r::" + w107["worktree_path"]},
    "projection": {"provider": {"id": "unavailable-adapter", "model": "same-model"}, "outcome": "in_progress"}}], [])
assert foreign[0]["transcript_expected"] is False
(root / "foreign-usage").mkdir()
assert dash.collect_usage(root / "foreign-usage", foreign, {})["workers"] == {}
# Older Orca rows omit provider metadata. Preserve transcript discovery without guessing a model.
for provider in (None, {}, {"model": "arbitrary-model"}, {"id": "claude"}):
    workers = dash.shape_workers([{"dispatchId": "legacy", "taskId": "legacy-task",
        "resource": {"worktreeId": "r::" + w107["worktree_path"]},
        "projection": {"provider": provider, "outcome": "in_progress"}}], [],
        [{"id": "legacy-task", "created_at": w107["since"]}])
    (root / "legacy-usage").mkdir(exist_ok=True)
    usage = dash.collect_usage(root / "legacy-usage", workers, cfg_a)
    assert usage["workers"]["legacy"]["total"] == w107["tokens"]["total"], provider
    assert usage["motion"]["legacy"], provider
    assert dash.stalls(workers, dash.utcnow())[0]["kind"] == "start_unconfirmed", provider
    workers[0]["motion"] = {"state": "idle", "since": workers[0]["since"]}
    assert dash.stalls(workers, dash.utcnow())[0]["kind"] == "idle", provider
assert tok > 0 and w107["tokens"]["total"] > 0 and st["series"][-1]["tokens"] == tok
assert next(w for w in st["workers"] if w["model"] == "local-reviewer")["tokens"] is None, "workers without transcripts are unmeasured"
assert dash.collect(A, sources=("orca",))["usage"]["total"]["total"] == tok, "rescan must not double count"
assert dash.transcript_dir("/w/my_repo.x").name == "-w-my-repo-x"
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
    dash.sh([str(root / "dash-test-no-such-cli")])
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
dash.STATE_WROTE.clear()  # an unchanged state is not rewritten at all (below); forget the last write to reach the replace
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
dash.HEALTH["port"] = srv.server_address[1]
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
assert [p["slug"] for p in progs if p["slug"] in (A, B)] == [B, A]
done = {p["slug"]: (p["closed"], bool(p["last_event"])) for p in progs}
assert done["docs-refresh"] == (True, True) and done["metrics-spike"] == (False, True) and not done[A][0], done
assert b"<title>" in urllib.request.urlopen(f"{base}/").read()

# Answering a permission prompt is a POST like the others: this server's Host and Origin, and the page's token.
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
# A decision answered on the page is recorded through its own endpoint, behind the same Host and token guard; the send
# box's endpoint (and its idle check) is not in its path.
answers, dash.fleet.decision_answer = [], lambda *a: answers.append(a) or (200, {"ok": True, "delivered": False})
body = json.dumps({"decision": "d1", "answer": "CSV"}).encode()
assert post({"Host": host}, "/api/fleet/decide") == 403 and answers == []
assert post({"Host": host, "X-Dash-Token": dash.TOKEN}, "/api/fleet/decide") == 200
assert answers == [("d1", "CSV")], answers
# PC load: pressure.py's history of the demo's seeded rounds, through the route; a missing script is an error row.
pr = json.loads(urllib.request.urlopen(f"{base}/api/fleet/pressure?hours=24").read())
assert pr["hours"] == 24 and len(pr["series"]) > 400 and pr["last"]["level"] == "warn", {k: pr.get(k) for k in ("hours", "last")}
assert pr["thresholds"]["load_per_cpu"] == [1.3, 2.0] and [q["id"] for q in pr["queue"]] == ["q-0fake001"]
assert len(dash.pressure_report(6)["series"]) < len(pr["series"])
real_pressure, dash.PRESSURE = dash.PRESSURE, root / "missing.py"
assert "error" in dash.pressure_report(), dash.pressure_report()
dash.PRESSURE = real_pressure
sk = json.loads(urllib.request.urlopen(f"{base}/api/fleet/skills").read())
assert {"agent-skills", "user", "other"} <= {x["source"] for x in sk["sources"]}, sk["sources"]
assert next(r for r in sk["skills"] if r["name"] == "agent-skills:explain-code")["flags"] == ["unused_30d"]
try:
    urllib.request.urlopen(urllib.request.Request(f"{base}/api/fleet/skills", headers={"Host": f"evil.example:{srv.server_address[1]}"}))
    raise AssertionError("a rebinding name read skill usage")
except urllib.error.HTTPError as e:
    assert e.code == 403, e.code
# Every API read refuses a rebinding name or a foreign page, not only the fleet's; the page itself stays open.
def status(path, headers):
    try:
        return urllib.request.urlopen(urllib.request.Request(f"{base}{path}", headers=headers)).status
    except urllib.error.HTTPError as e:
        return e.code
port = srv.server_address[1]
for path in ("/api/health", "/api/programs", "/api/fleet/skills", "/api/no-such-program/state"):
    assert status(path, {"Host": f"evil.example:{port}"}) == 403, path
    assert status(path, {"Origin": "http://evil.example"}) == 403, path
assert status("/api/health", {}) == status("/api/programs", {}) == 200
assert status("/api/health", {"Host": f"localhost:{port}"}) == 200
assert status("/api/health", {"Origin": f"http://127.0.0.1:{port}"}) == 200
assert status("/", {"Host": f"evil.example:{port}"}) == 200
assert status("/no-such-page", {"Host": f"evil.example:{port}"}) == 404
# Only a page saying ?viewed=1 counts as a view of the fleet; a bare read does not keep the fast poll alive.
dash.FLEET_VIEWED[0] = 0.0
status("/api/fleet/state", {})
assert dash.FLEET_VIEWED[0] == 0.0, "a read without ?viewed=1 was counted as a view"
for q in ("?viewed=1.0", "?viewed=10", "?viewed=1-no", "?x=?viewed=1", "?viewed=0", "?viewed=1&viewed=2", "?viewed=1&viewed=", "?viewed=&viewed=1"):
    status("/api/fleet/state" + q, {})
    assert dash.FLEET_VIEWED[0] == 0.0, f"{q} was counted as a view"
dash.FLEET_FORCE[0] = False
dash.FLEET_WAKE.clear()
# monotonic() starts near zero on a freshly booted runner, so 0.0 would not read as idle there.
dash.FLEET_VIEWED[0] = dash_time.monotonic() - 10 * dash.FLEET_VIEW_S
status("/api/fleet/state?viewed=1", {})
assert dash.FLEET_VIEWED[0] > 0.0, "?viewed=1 was not counted as a view"
assert dash.FLEET_FORCE[0] and dash.FLEET_WAKE.is_set(), "a page opening after idle must collect at once"
status("/api/fleet/state?a=1&viewed=1", {})
dash.FLEET_VIEWED[0], dash.FLEET_FORCE[0] = 0.0, False
dash.FLEET_WAKE.clear()
# pressure's own config is an input of its answer.
cfg_file = root / "agent-skills.json"
real_cfg = os.environ.get("AGENT_SKILLS_CONFIG")
os.environ["AGENT_SKILLS_CONFIG"] = str(cfg_file)
cfg_a = dash.pressure_signature()
cfg_file.write_text('{"reap": {"pressure": {"interval": 60}}}')
assert dash.pressure_signature() != cfg_a, "a changed agent-skills.json did not move the pressure signature"
# pressure.py reads ~/.claude/agent-skills.json whatever AGENT_SKILLS_CONFIG says: the default file counts too.
fake_home = root / "home-for-pressure"
(fake_home / ".claude").mkdir(parents=True, exist_ok=True)
real_home = os.environ.get("HOME")
os.environ["HOME"] = str(fake_home)
try:
    before_home = dash.pressure_signature()
    (fake_home / ".claude" / "agent-skills.json").write_text('{"reap": {"pressure": {"interval": 30}}}')
    assert dash.pressure_signature() != before_home, "the default agent-skills.json is not in the pressure signature"
finally:
    os.environ["HOME"] = real_home
if real_cfg is None:
    del os.environ["AGENT_SKILLS_CONFIG"]
else:
    os.environ["AGENT_SKILLS_CONFIG"] = real_cfg
# The pressure and sent answers are built once per change of their inputs, not once per poll.
builds = {"pressure": 0, "sent": 0}
real_pressure_render, real_sent_render = dash.pressure_render, dash.sent_render
dash.pressure_render = lambda h: (builds.__setitem__("pressure", builds["pressure"] + 1), real_pressure_render(h))[1]
dash.sent_render = lambda d: (builds.__setitem__("sent", builds["sent"] + 1), real_sent_render(d))[1]
try:
    for _ in range(3):
        urllib.request.urlopen(f"{base}/api/fleet/pressure?hours=12").read()
        urllib.request.urlopen(f"{base}/api/fleet/sent?days=2").read()
    assert builds == {"pressure": 1, "sent": 1}, builds
    hist = dash.state_root() / "pressure-history.jsonl"
    hist.parent.mkdir(parents=True, exist_ok=True)
    with hist.open("a") as f:
        f.write("\n")
    urllib.request.urlopen(f"{base}/api/fleet/pressure?hours=12").read()
    assert builds["pressure"] == 2, "a changed pressure file was not read again"
    dash.sent.sent_dir().mkdir(parents=True, exist_ok=True)
    (dash.sent.sent_dir() / "2099-01-01.jsonl").write_text("")
    urllib.request.urlopen(f"{base}/api/fleet/sent?days=2").read()
    assert builds["sent"] == 2, "a changed sent log was not read again"
    (dash.sent.sent_dir() / "2099-01-01.jsonl").unlink()
finally:
    dash.pressure_render, dash.sent_render = real_pressure_render, real_sent_render
# /api/programs revalidates: the same body answers If-None-Match with a 304 and no body.
etag = urllib.request.urlopen(f"{base}/api/programs").headers["ETag"]
assert etag, "/api/programs sends an ETag"
assert status("/api/programs", {"If-None-Match": etag}) == 304, "an unchanged /api/programs was sent again"
# Data flow and skill changes: served from the demo's own state, cached in the process between polls.
fl = json.loads(urllib.request.urlopen(f"{base}/api/fleet/flow").read())
assert next(s for s in fl["stores"] if s["id"] == "skillsj")["exists"] and "error" not in fl, fl.get("error")
assert urllib.request.urlopen(f"{base}/api/fleet/flow").read() == dash.FLOW_CACHE["flow"][1]
chg = json.loads(urllib.request.urlopen(f"{base}/api/fleet/changes").read())
assert any(r["status"] == "proposed" and r["skills"] == ["end-session"] for r in chg["rows"]), chg.get("error")
# The brief page passes the renderer's output through: a headline saying there are no rows, the report once rows exist.
br = json.loads(urllib.request.urlopen(f"{base}/api/fleet/brief").read())
assert "리드·카드 행 없음" in br["text"].splitlines()[0], br
dash_demo.seed_board()
br = json.loads(urllib.request.urlopen(f"{base}/api/fleet/brief").read())
# The demo's PRs are not collected: a row whose PR state is unknown stops at ❔ (rowstate rule 1), before 🔴.
assert "| ❔ | 로그인 개편 | #41 ? |" in br["full"] and br["width_ok"] is True and "snapshot" not in br, br
# Every row and list item is placed once: the page's buttons and the report's text agree.
assert br["duplicates"] == [] and br["missing"] == [] and br["placement"], br
# The units endpoint joins the same render: every unit sits in one group, and its rows carry the brief's own states.
un = json.loads(urllib.request.urlopen(f"{base}/api/fleet/units").read())
assert "error" not in un and un["counts"]["units"] == len(un["units"]) and un["units"], un
assert sorted(i for g in un["groups"] for i in g["units"]) == sorted(u["id"] for u in un["units"]), un["groups"]
assert any(rf["state"] and rf["state"]["status"] for u in un["units"] for rf in u["rows"]), un["units"]
# The Brief carries the stage table, read on each poll: rows nest under their group with a depth, and a write shows
# at once. The Work page and its endpoint are gone.
wk = br["stages"]
recorded = [(x["id"], x["depth"], bool(x.get("group"))) for x in wk["rows"] if x.get("source") != "auto"]
assert recorded[:4] == [("login-revamp", 0, True), ("login", 1, False), ("redirect", 2, False), ("export", 1, False)], wk["rows"]
assert wk["env"]["prod"] and wk["next"], wk
assert wk["refs"]["acme/launchpad#41"] == "https://github.com/acme/launchpad/pull/41", wk["refs"]
try:
    urllib.request.urlopen(f"{base}/api/fleet/work")
    raise AssertionError("the Work endpoint still answers")
except urllib.error.HTTPError as e:
    assert e.code == 404, e.code
import stages
stages.set_cell("docs", "dev", "ok", "v2.4.0-rc1")
wk = json.loads(urllib.request.urlopen(f"{base}/api/fleet/brief").read())["stages"]
assert next(x for x in wk["rows"] if x["id"] == "docs")["cells"]["dev"]["mark"] == "ok", wk["rows"]
# The sent page clamps its window to 1..30 days, and a failure comes back as the page's error, not a dead request.
sent_api = lambda q: json.loads(urllib.request.urlopen(f"{base}/api/fleet/sent{q}").read())
assert [sent_api(q)["days"] for q in ("", "?days=99", "?days=0", "?days=7")] == [1, 30, 1, 7]
real_page, dash.sent.page = dash.sent.page, lambda *a, **k: {}["boom"]
dash.SENT_CACHES.clear()  # a good answer is kept while the inputs stand; this one has to be built again
try:
    assert sent_api("?days=1") == {"error": "sent: KeyError: 'boom'"}
    assert sent_api("?days=1") == {"error": "sent: KeyError: 'boom'"}  # a failure is not kept
finally:
    dash.sent.page = real_page
assert "error" not in sent_api("?days=1"), "the failure was served after the page recovered"
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

assert [m.upper() for m in dash.TICKET_RE.findall("example/abc-135-prep on 2026-09-24, ACME-7")] == ["ABC-135", "ACME-7"]

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
      {"dispatch": "c", "transcript_expected": True, "outcome": "in_progress", "since": ago(30), "motion": None},
      {"dispatch": "d", "outcome": "in_progress", "since": ago(30), "motion": None, "model": "local-reviewer"},
      {"dispatch": "e", "outcome": "in_progress", "since": ago(90), "motion": {"state": "tool", "since": ago(50), "tool": "Bash"}},
      {"dispatch": "f", "outcome": "in_progress", "since": ago(90), "motion": {"state": "working", "since": ago(1)}},
      {"dispatch": "g", "outcome": "succeeded", "since": ago(90), "motion": {"state": "idle", "since": ago(80)}},
      # A task-notification row after the turn ended: "working" with nothing written since.
      {"dispatch": "h", "outcome": "in_progress", "since": ago(200), "motion": {"state": "working", "since": ago(150)}}]
assert [(x["dispatch"], x["kind"]) for x in dash.stalls(ws, now)] == [("a", "idle"), ("c", "start_unconfirmed"), ("e", "long_tool"),
                                                                      ("h", "silent")]
assert "{" not in dash.prog.STALL_TEXT["silent"].format(dispatch="h", minutes=150)
# A parked ticket's worker waits on purpose; admitting the ticket again brings its stall back.
ws_p = [{"dispatch": "p1", "ticket": "T-5", "outcome": "in_progress", "since": ago(90), "motion": {"state": "idle", "since": ago(60)}}]
parked = [{"ts": ago(70), "ev": "parked", "ticket": "T-5", "note": "waits on human"}]
assert dash.stalls(ws_p, now, dash.parked_tickets(parked)) == []
assert [x["dispatch"] for x in dash.stalls(ws_p, now, dash.parked_tickets(parked + [{"ts": ago(5), "ev": "admitted", "ticket": "T-5"}]))] == ["p1"]
held = dash.spare({"next": "stop spawning: land what is verified", "cap": 4, "in_flight": 1}, [])
assert held["slots"] == 0 and held["held"].startswith("stop spawning"), held
free = dash.spare({"next": "may spawn 3 more", "cap": 4, "in_flight": 1},
                  [{"id": "t1", "status": "completed", "deps": [], "ticket": "X-1", "title": "X-1"},
                   {"id": "t2", "status": "pending", "deps": ["t1"], "ticket": "X-2", "title": "X-2"},
                   {"id": "t3", "status": "pending", "deps": ["t2"], "ticket": "X-3", "title": "X-3"},
                   {"id": "t4", "status": "ready", "deps": [], "ticket": None, "title": "Land #12"}])
assert free == {"slots": 3, "ready": ["X-2"]}, free  # a human-gate Land task is the lead's, not work to start

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
# The skills-sync broadcast types /reload-* into finished sessions and nothing answers: the turn stays ended.
assert dash.reload_row.__module__ == "sync", "the repo's scripts/sync.py must load, not the fallback"
RELOAD_TAIL = [json.loads(l) for l in (pathlib.Path(__file__).parent / "testdata" / "reload-tail.jsonl").read_text().splitlines()]
RELOAD_TAIL = [r for r in RELOAD_TAIL if r.get("timestamp")]
m = motion(RELOAD_TAIL)
assert (m["state"], m["since"]) == ("idle", "2026-11-03T10:05:00.000Z"), m
assert motion(RELOAD_TAIL[3:]) == {}, "a tail of reloads only says nothing about the session"
# A cache scanned under the old rules held such a session at working: it is read again once.
fixture = pathlib.Path(__file__).parent / "testdata" / "reload-tail.jsonl"
size = fixture.stat().st_size
old = lambda **kw: {"offset": size, "committed": {}, "motion": {"state": "working", "since": "2026-11-03T13:00:00.000Z"}, **kw}
assert dash.scan_transcript(fixture, old(), "")["motion"]["state"] == "idle"
assert dash.scan_transcript(fixture, old(motion_v=dash.MOTION_V), "")["motion"]["state"] == "working", \
    "a cache under the current rules is not read again"
other = [{"type": "user", "timestamp": "t9", "isMeta": True, "message": {"content": "<local-command-caveat>x</local-command-caveat>"}},
         {"type": "user", "timestamp": "t9", "message": {"content": "<command-name>/compact</command-name>"}}]
m = motion(other, motion(RELOAD_TAIL))
assert (m["state"], m["since"]) == ("working", "t9"), "any other slash command is still the session at work"

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
folded = [{"ts": ago(300), "ev": "landed", "pr": 7, "sha": "s7"}, {"ts": ago(290), "ev": "landed", "pr": 8, "sha": "s8"},
          {"ts": ago(280), "ev": "main_green", "sha": "s8"}]
prs_f = [{"number": 7, "state": "merged", "merged_at": ago(300), "branch": "t-upper", "base": "t-lower"},
         {"number": 8, "state": "merged", "merged_at": ago(290), "branch": "t-lower", "base": "feat/x"}]
assert dash.ledger_gaps(cfg_g, folded, [], prs_f, now)["ci"] == [], "a PR folded into a lower branch has no CI run of its own"
tip = [{"ts": ago(300), "ev": "landed", "pr": 9, "sha": "s9"}, {"ts": ago(290), "ev": "landed", "pr": 10, "sha": "s10"},
       {"ts": ago(280), "ev": "main_green", "sha": "s10"}]
assert dash.ledger_gaps(cfg_g, tip, [], [], now, contains=lambda a, b: (a, b) == ("s9", "s10"))["ci"] == [], \
    "a later green at the branch tip covers the landing under it"
assert dash.ledger_gaps(cfg_g, tip, [], [], now, contains=lambda a, b: False)["ci"] == [9], "not in its history: still a gap"
# gh compare is guarded: not asked while core quota is low, and not again until the reset after a rate-limit error.
cfg_q = {**cfg_g, "repo": "acme/app"}
two = [{"ts": ago(300), "ev": "landed", "pr": 20, "sha": "s20"}, {"ts": ago(290), "ev": "landed", "pr": 21, "sha": "s21"},
       {"ts": ago(280), "ev": "main_green", "sha": "s22"}, {"ts": ago(270), "ev": "main_green", "sha": "s23"}]
calls, quota, clock = [], {"n": 0, "t": 1000 + 3600}, [1000.0]
def fake_sh(cmd, timeout=45):
    calls.append(cmd[2])
    if cmd[2] == "rate_limit":
        return {"n": quota["n"], "t": quota["t"]}
    if quota["n"] < 1:
        raise dash.SourceError("API rate limit exceeded")
    return {"s": "ahead"}
real_sh, real_mono, real_time = dash.sh, dash.time.monotonic, dash.time.time
dash.sh, dash.time.monotonic, dash.time.time = fake_sh, lambda: clock[0], lambda: clock[0]
dash._CONTAINS.clear(); dash._gh_down_until = dash._gh_check_at = 0.0
compares = lambda: [c for c in calls if c != "rate_limit"]
assert dash.ledger_gaps(cfg_q, two, [], [], now)["ci"] == [], "unknown is no gap"
assert not compares(), f"quota low: no compare asked: {calls}"
n_quota = len(calls)
for _ in range(20):
    clock[0] += 60
    dash.ledger_gaps(cfg_q, two, [], [], now)
assert not compares() and len(calls) == n_quota, f"nothing is asked until the reset: {calls}"
clock[0], quota["n"] = 1000.0 + 3601, 5000
dash.ledger_gaps(cfg_q, two, [], [], now)
assert compares(), "after the reset the compares run"
# A rate-limit error with plenty of quota reported: one failed call, then backoff.
calls.clear(); dash._CONTAINS.clear(); dash._gh_down_until = dash._gh_check_at = 0.0
quota["n"] = 5000
clock[0] = 2000.0
reset_at, left = 3800.0, {"n": 5000}
def limited(cmd, timeout=45):
    calls.append(cmd[2])
    if cmd[2] == "rate_limit":
        return {"n": left["n"], "t": reset_at}
    left["n"] = 0  # the failed compare is what used the last of it
    clock[0] += 45  # it timed out: the backoff counts from here, not from when the call began
    raise dash.SourceError("API rate limit exceeded")
dash.sh = limited
assert dash.ledger_gaps(cfg_q, two, [], [], now)["ci"] == [], "a failed compare is no gap"
assert len(compares()) == 1, f"one failed compare, the rest of the pairs wait: {calls}"
assert dash.gh_contains("acme/app", "s20", "s22") is None, "unknown is None, never False"
asked = len(calls)
clock[0] = 2344.0  # 299 s after the failure finished at 2045
dash.ledger_gaps(cfg_q, two, [], [], now)
assert len(calls) == asked, "inside the 300 s backoff nothing is asked"
clock[0] = 2346.0
dash.ledger_gaps(cfg_q, two, [], [], now)
assert calls[asked:] == ["rate_limit"], f"after the backoff the quota is read first: {calls[asked:]}"
asked = len(calls)
for t in range(2400, 3800, 100):
    clock[0] = float(t)
    dash.ledger_gaps(cfg_q, two, [], [], now)
assert len(compares()) == 1, f"no compare until the reset: {calls}"
left["n"], clock[0] = 5000, 3801.0
dash.ledger_gaps(cfg_q, two, [], [], now)
assert len(compares()) == 2, f"asked again after the reset: {calls}"
pathlib.Path(os.environ["AGENT_SKILLS_CONFIG"]).write_text(json.dumps({"dashboard": {"github_every_s": 10 ** 20}}))
assert dash.github_every() == 300, "a value Event.wait cannot take is not used"
dash.sh, dash.time.monotonic, dash.time.time = real_sh, real_mono, real_time
dash._CONTAINS.clear(); dash._gh_down_until = dash._gh_check_at = 0.0

# The GitHub stage runs once per period; a landing row or a manual refresh runs it at once.
cfg_file = pathlib.Path(os.environ["AGENT_SKILLS_CONFIG"])
cfg_file.write_text("{}")
assert dash.github_every() == 300, "no key: the default"
cfg_file.write_text(json.dumps({"dashboard": {"github_every_s": 600}}))
assert dash.github_every() == 600
cfg_file.write_text(json.dumps({"dashboard": {"github_every_s": 5}}))
assert dash.github_every() == 300, "below the 60 s floor is not a guess"
cfg_file.write_text(json.dumps({"dashboard": {"github_every_s": 900}}))
rounds, seen_intervals, seen_waits = [], [], []
real_wake_event = dash.SLOW_WAKE
real_round = dash.slow_round
dash.slow_round = lambda interval, first: (rounds.append(first), seen_intervals.append(interval))
class RecordingEvent(type(dash.SLOW_WAKE)):
    def wait(self, timeout=None):
        seen_waits.append(timeout)
        return super().wait(timeout)
dash.SLOW_WAKE = RecordingEvent()
loop = threading.Thread(target=dash.slow_loop, args=(60,), daemon=True)
loop.start()
def wait_for(n, secs=5):
    end = dash.time.monotonic() + secs
    while len(rounds) < n and dash.time.monotonic() < end:
        dash.time.sleep(0.02)
    return len(rounds)
assert wait_for(1) == 1
dash.time.sleep(0.5)
assert len(rounds) == 1, f"inside the period the stage does not run again: {rounds}"
dash.SLOW_WAKE.set()  # a manual refresh
assert wait_for(2) == 2, "a refresh runs it now"
dash.time.sleep(0.3)
assert len(rounds) == 2
LP = "landing-test"
(store / LP).mkdir()
(store / LP / "program.json").write_text(json.dumps({"repo": "acme/app", "created_at": ago(900)}))
(store / LP / "ledger.jsonl").write_text(json.dumps({"ts": ago(10), "ev": "spawned", "ticket": "X-1"}) + "\n")
dash.note_landings(LP)
dash.time.sleep(0.3)
assert len(rounds) == 2, "the first look at a ledger wakes nothing"
with (store / LP / "ledger.jsonl").open("a") as f:
    f.write(json.dumps({"ts": ago(1), "ev": "note", "text": "x"}) + "\n")
dash.note_landings(LP)
dash.time.sleep(0.3)
assert len(rounds) == 2, "a row that is not a landing wakes nothing"
with (store / LP / "ledger.jsonl").open("a") as f:
    f.write(json.dumps({"ts": ago(0), "ev": "landed", "pr": 1, "sha": "s1"}) + "\n")
dash.note_landings(LP)
assert wait_for(3) == 3, "a new landed row runs it now"
assert set(seen_intervals) == {900} and set(seen_waits) == {900}, (seen_intervals, seen_waits)
LQ = "landing-new"  # a program that appears after start with a landing already in its ledger
(store / LQ).mkdir()
(store / LQ / "program.json").write_text(json.dumps({"repo": "acme/app", "created_at": ago(900)}))
(store / LQ / "ledger.jsonl").write_text(json.dumps({"ts": ago(1), "ev": "landed", "pr": 2, "sha": "s2"}) + "\n")
dash.note_landings(LQ)
assert wait_for(4) == 4, "its landed row is new to the loop"
dash.slow_round, dash.SLOW_WAKE = real_round, real_wake_event

# A gh call that never returns must not hold the program lock: a ledger-only refresh (the fast loop) still finishes.
H = "hang-test"
(store / H).mkdir()
(store / H / "program.json").write_text(json.dumps({"repo": "acme/app", "created_at": ago(900)}))
(store / H / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in two))
release, entered = threading.Event(), threading.Event()
def hanging_sh(cmd, timeout=45):
    entered.set()
    release.wait(30)
    raise dash.SourceError("released")
real_gh_fetch, dash.FETCHERS["github"] = dash.FETCHERS["github"], lambda cfg: []
dash.sh = hanging_sh
slow = threading.Thread(target=dash.collect, args=(H, ("github",)), daemon=True)
slow.start()
assert entered.wait(10), "the slow collect never reached gh"
fast = threading.Thread(target=dash.collect, args=(H, ()), daemon=True)
fast.start()
fast.join(5)
stuck = fast.is_alive()
release.set()
slow.join(10)
fast.join(10)
dash.sh, dash.FETCHERS["github"] = real_sh, real_gh_fetch
dash._CONTAINS.clear()
dash._gh_down_until = 0.0
assert not stuck, "a ledger-only collect waited on another collect's gh call"

# A silent fast loop: health says stale though the heartbeat is fresh; under launchd the server exits itself.
old_health, old_agent, old_restart = dict(dash.HEALTH), dash.under_agent, dash.restart
exits = []
dash.restart, dash.under_agent = lambda: exits.append(1), lambda: True
age = lambda m: dash.iso(dash.utcnow() - dash.dt.timedelta(minutes=m))
dash.HEALTH.clear(); dash.HEALTH.update(heartbeat=age(0), stale=False)
assert not dash.health_view()["stale"], "no tick yet (just started) is not a stop"
dash.check_loops(); assert not exits
dash.HEALTH["ticks"] = {"fast": age(4)}
assert not dash.health_view()["stale"]; dash.check_loops(); assert not exits, "inside the 5 minutes"
dash.HEALTH["ticks"] = {"fast": age(6)}
assert dash.health_view()["stale"] is True; dash.check_loops(); assert not exits, "stale in health, but a slow round is not restarted yet"
dash.HEALTH["ticks"] = {"fast": age(16)}
dash.check_loops(); assert exits == [1], "a loop silent for 15 minutes restarts the server"
dash.HEALTH.update(stale=True)
dash.check_loops(); assert exits == [1], "other code on disk: check_code restarts it, after importing it"
dash.HEALTH.update(stale=False)
dash.under_agent = lambda: False
dash.check_loops(); assert exits == [1], "outside launchd it only reports"
dash.HEALTH.clear(); dash.HEALTH.update(old_health); dash.under_agent, dash.restart = old_agent, old_restart

# A bad-ts event in the ledger does not stop the compare warm-up from asking about the good ones.
calls.clear()
dash.sh = lambda cmd, timeout=45: (calls.append(cmd), {"s": "behind"})[1]
(store / H / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in
                                               [{"ts": "not a time", "ev": "main_green", "sha": "s99"}] + two))
dash._CONTAINS.clear(); dash._cache_state.update(loaded=False, dirty=False); dash.cache_path().unlink(missing_ok=True)
dash.warm_contains(store / H, {"repo": "acme/app", "created_at": ago(900)})
dash.sh = real_sh
pairs_asked = sorted(c[2].rsplit("/", 1)[1] for c in calls if c[2].startswith("repos/"))
dash._CONTAINS.clear()
assert pairs_asked == ["s20...s22", "s20...s23", "s21...s22", "s21...s23"], f"every good pair is asked: {pairs_asked}"

# Calls per collect are bounded, answers outlive a restart, and a local clone answers without GitHub.
CP = "cap-test"
(store / CP).mkdir()
cfg_cp = {"repo": "acme/app", "created_at": ago(60 * 24 * 30)}
(store / CP / "program.json").write_text(json.dumps(cfg_cp))
rows_cp = ([{"ts": ago(600 + i), "ev": "landed", "pr": 100 + i, "sha": f"a{i}"} for i in range(30)]
           + [{"ts": ago(100 + i), "ev": "main_green", "sha": f"g{i}"} for i in range(30)])
(store / CP / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows_cp))
asked = []
def counting_sh(cmd, timeout=45):
    asked.append(cmd[2])
    return {"n": 5000, "t": 0} if cmd[2] == "rate_limit" else {"s": "behind"}
for f in (dash.cache_path(),):
    f.unlink(missing_ok=True)
dash._CONTAINS.clear(); dash._cache_state.update(loaded=False, dirty=False); dash._gh_down_until = dash._gh_check_at = 0.0
dash.sh = counting_sh
dash.warm_contains(store / CP, cfg_cp)
first = [c for c in asked if c != "rate_limit"]
assert 0 < len(first) <= dash.GH_COMPARES_PER_ROUND, f"900 pairs, {len(first)} asked in one collect"
assert dash.cache_path().exists(), "the answers are on disk"
dash._CONTAINS.clear(); dash._cache_state.update(loaded=False, dirty=False)  # a restart
asked.clear()
dash.warm_contains(store / CP, cfg_cp)
again = [c for c in asked if c != "rate_limit"]
assert set(again).isdisjoint(first), "an answer already on disk is not asked again"
# A program at rest (no ledger event for 3 days) asks GitHub nothing, even with unknown pairs left.
RP = "rest-test"
(store / RP).mkdir()
(store / RP / "program.json").write_text(json.dumps(cfg_cp))
(store / RP / "ledger.jsonl").write_text("".join(
    json.dumps({**r, "ts": dash.iso(dash.utcnow() - dash.dt.timedelta(days=5, minutes=i))}) + "\n"
    for i, r in enumerate(rows_cp)))
dash._CONTAINS.clear(); dash._cache_state.update(loaded=False, dirty=False); dash.cache_path().unlink(missing_ok=True)
asked.clear(); dash.sh = counting_sh
assert dash.closed(RP)
real_fetch, dash.FETCHERS["github"] = dash.FETCHERS["github"], lambda cfg: []
dash.collect(RP, ("github",))
dash.FETCHERS["github"] = real_fetch
assert not [c for c in asked if c != "rate_limit"], f"a program at rest was asked about: {asked[:3]}"
# A landing past the age limit is not a gap to chase.
old =[{"ts": ago(60 * 24 * 15), "ev": "landed", "pr": 1, "sha": "o1"}]
assert dash.ledger_gaps(cfg_cp, old, [], [], now, contains=lambda a, b: False)["ci"] == [], "15 days old"
assert dash.ledger_gaps(cfg_cp, [{**old[0], "ts": ago(60 * 24 * 10)}], [], [], now, contains=lambda a, b: False)["ci"] == [1]
dash.sh = real_sh
# A local clone answers first: no gh call at all.
import subprocess
clone = pathlib.Path(tempfile.mkdtemp())
git = lambda *a: subprocess.run(["git", "-C", str(clone), "-c", "user.name=t", "-c", "user.email=t@example.test",
                                 "-c", "commit.gpgsign=false", *a], capture_output=True, text=True, check=True).stdout.strip()
git("init", "-q"); git("commit", "-q", "--allow-empty", "-m", "one"); c1 = git("rev-parse", "HEAD")
git("commit", "-q", "--allow-empty", "-m", "two"); c2 = git("rev-parse", "HEAD")
asked.clear(); dash.sh = counting_sh
assert dash.gh_contains("acme/local", c1, c2, clone) is True and dash.gh_contains("acme/local", c2, c1, clone) is False
assert not asked, f"a local clone needs no gh: {asked}"
# A reply that is not one of GitHub's four statuses is no answer, and is not remembered.
dash.sh = lambda cmd, timeout=45: {"n": 5000, "t": 0} if cmd[2] == "rate_limit" else {"s": None}
assert dash.gh_contains("acme/odd", "p", "q") is None and ("acme/odd", "p", "q") not in dash._CONTAINS
# A shallow clone's "no" may only mean cut history: it is not an answer.
git("commit", "-q", "--allow-empty", "-m", "three"); c3 = git("rev-parse", "HEAD")
shallow = pathlib.Path(tempfile.mkdtemp()) / "s"
git("config", "uploadpack.allowAnySHA1InWant", "true")
subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{clone}", str(shallow)], check=True, capture_output=True)
subprocess.run(["git", "-C", str(shallow), "fetch", "-q", "--depth", "1", "origin", c1], check=True, capture_output=True)
rc = subprocess.run(["git", "-C", str(shallow), "merge-base", "--is-ancestor", c1, c3], capture_output=True).returncode
assert rc == 1, f"the fixture must make git itself answer no: {rc}"
assert dash.local_contains(shallow, c1, c3) is None, "shallow: unknown, not False"
# A damaged cache file is a cold cache, and the cache stays bounded.
dash._CONTAINS.clear(); dash._cache_state.update(loaded=False, dirty=False)
dash.cache_path().write_text('["not", "a", "dict"]')
dash.load_compare_cache()
assert dash._cache_state["loaded"] and not dash._CONTAINS
dash.cache_path().write_text('{"acme/app": ["x"]}')
dash._cache_state["loaded"] = False
dash.load_compare_cache()
assert not dash._CONTAINS
real_max, dash.CACHE_MAX = dash.CACHE_MAX, 3
for i in range(5):
    dash._CONTAINS[("acme/app", f"o{i}", "n")] = True
dash._cache_state["dirty"] = True
dash.save_compare_cache()
dash.CACHE_MAX = real_max
assert sorted(json.loads(dash.cache_path().read_text())["acme/app"]) == ["o2...n", "o3...n", "o4...n"], "the file is bounded"
assert len(dash._CONTAINS) == 5, "memory holds twice the file bound, so the active pairs stay"
dash.CACHE_MAX = 2
for i in range(10):
    dash._CONTAINS[("acme/gone", f"g{i}", "n")] = True
dash._cache_state["dirty"] = True
dash.save_compare_cache()
dash.CACHE_MAX = real_max
assert len(dash._CONTAINS) == 4, f"answers of long-gone pairs leave memory too: {len(dash._CONTAINS)}"
for i in range(5):  # put the five back for the checks below
    dash._CONTAINS[("acme/app", f"o{i}", "n")] = True
# An answer a ledger still asks for is renewed, so the file keeps it; a long active set is never judged unknown.
dash.CACHE_MAX = 3
dash.cached(("acme/app", "o0", "n"))
dash._CONTAINS[("acme/app", "o5", "n")] = False
dash._cache_state["dirty"] = True
dash.save_compare_cache()
dash.CACHE_MAX = real_max
assert dash.cached(("acme/app", "o1", "n")) is True, "memory is not evicted by the file bound"
assert "o0...n" in json.loads(dash.cache_path().read_text())["acme/app"], "a renewed answer stays in the file"
# Another process saved meanwhile: its answers are merged in, not overwritten.
other = json.loads(dash.cache_path().read_text())
other.setdefault("acme/other", {})["x...y"] = True
dash.cache_path().write_text(json.dumps(other))
dash._CONTAINS[("acme/app", "o6", "n")] = True
dash._cache_state["dirty"] = True
dash.save_compare_cache()
saved = json.loads(dash.cache_path().read_text())
assert saved["acme/other"] == {"x...y": True} and "o6...n" in saved["acme/app"], saved
# The loops renew answers while a save rebuilds the dict: no "changed during iteration".
for i in range(3000):
    dash._CONTAINS[("acme/app", f"h{i}", "n")] = True
stop, errors = threading.Event(), []
def hammer():
    i = 0
    while not stop.is_set():
        try:
            dash.cached(("acme/app", f"h{i % 3000}", "n"))
        except Exception as e:
            errors.append(e)
        i += 1
th = threading.Thread(target=hammer, daemon=True)
th.start()
for _ in range(30):
    dash._cache_state["dirty"] = True
    dash.save_compare_cache()
stop.set(); th.join(5)
assert not errors, errors[:1]
# A graft file rewrites ancestry: the local answer is not trusted.
(clone / ".git" / "info").mkdir(exist_ok=True)
(clone / ".git" / "info" / "grafts").write_text(c3 + "\n")
assert dash.local_contains(clone, c1, c3) is None, "grafts: unknown"
(clone / ".git" / "info" / "grafts").unlink()
dash.sh = real_sh
dash._CONTAINS.clear(); dash._cache_state.update(loaded=False, dirty=False); dash._gh_down_until = dash._gh_check_at = 0.0
dash.cache_path().unlink(missing_ok=True)

mixed = [{"number": 11, "state": "merged", "merged_at": ago(100), "base": "feat/x", "branch": "a"},
         {"number": 12, "state": "merged", "merged_at": ago(90), "base": "feat/x", "branch": "b"},
         {"number": 13, "state": "merged", "merged_at": ago(80), "base": "main", "branch": "c"}]
assert dash.ledger_gaps(cfg_g, [{"ts": ago(100), "ev": "landed", "pr": 11, "sha": "s11"}], [], mixed, now)["landings"] == [12], \
    "a merge into another base belongs to another session"

# ensure: starts this store's server once, then finds it; a second serve on the store refuses.
import socket, subprocess, signal
with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
# Started from a directory that is then removed (an archived worktree), the server runs in its store instead:
# the orca children of its collectors inherit its cwd and die at startup without one.
gone, live = pathlib.Path(tempfile.mkdtemp(prefix="test-dash-gone-")), os.getcwd()
os.chdir(gone)
gone.rmdir()
try:
    ok, msg = dash.ensure(port)
finally:
    os.chdir(live)
assert ok and "started" in msg, msg
h = dash.health(port)
assert h["store"] == str(dash.home()) and h["version"] == dash.code_version(), h
cwd = subprocess.run(["lsof", "-a", "-p", str(h["pid"]), "-d", "cwd", "-Fn"], capture_output=True, text=True).stdout
assert f"n{dash.home().resolve()}" in cwd.splitlines(), cwd
assert dash.ensure(port) == (True, f"dashboard: http://127.0.0.1:{port}/")
import urllib.request
for p in ("/fleet.js", "/api/programs"):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{p}") as r:
        assert r.headers["X-Dash-Version"] == h["version"], (p, dict(r.headers))
again = subprocess.run([sys.executable, str(pathlib.Path(dash.__file__)), "serve", "--port", "0"], capture_output=True,
                       text=True, timeout=20)
assert again.returncode and "already serves" in again.stderr, again.stderr
# Another port moves this store's server rather than failing on its lock, and a later restart keeps the new port.
assert dash.default_port() == port
with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port2 = sk.getsockname()[1]
ok, msg = dash.ensure(port2)
assert ok and "started" in msg and dash.health(port) is None, msg
h2 = dash.health(port2)
assert h2["store"] == str(dash.home()) and h2["pid"] != h["pid"], h2
assert dash.default_port() == port2
os.environ["ORCH_DASH_PORT"] = str(port)
assert dash.default_port() == port, "the env still wins"
del os.environ["ORCH_DASH_PORT"]
os.kill(h2["pid"], signal.SIGTERM)
# The running code is named in the health answer, and once the code on disk changes under a running server the
# server says it is stale (a merge landed after it started) rather than serving the old routes silently.
assert h["stale"] is False and h["disk_version"] == h["version"] and h["script"] == str(pathlib.Path(dash.__file__).resolve()), h
assert "git" in h, h
dash.HEALTH.update(version="started-as")
dash.check_code()
assert dash.HEALTH["stale"] is True and dash.HEALTH["disk_version"] == dash.code_version(), dash.HEALTH
dash.HEALTH.update(version=dash.code_version())
dash.check_code()
assert dash.HEALTH["stale"] is False

# With this store's LaunchAgent loaded, launchd is the only manager. The tests never run launchctl against a
# real label: the label is a throwaway one, and install_agent is not called here (see the PR's launchd runs).
import time
os.environ["ORCH_DASH_LABEL"] = f"io.github.jeongjaesoon.orch-dash-test-{os.getpid()}"
assert dash.agent_status() is None
spec = dash.agent_spec(port)
assert spec["KeepAlive"] and spec["RunAtLoad"] and spec["EnvironmentVariables"]["PROGRAMS_HOME"] == str(store), spec
assert spec["WorkingDirectory"] == str(dash.home()), spec
assert spec["ProgramArguments"][2:] == [str(pathlib.Path(dash.__file__).resolve()), "serve", "--port", str(port)], spec
# A broken plist is no agent, so ensure keeps its own path instead of failing every `orch status`.
real_plist = dash.agent_plist
for body in (b"<plist><dict><key>Label", b"<?xml version='1.0'?><plist><array/></plist>"):
    (root / "bad.plist").write_bytes(body)
    dash.agent_plist = lambda: root / "bad.plist"
    assert dash.agent_status() is None, body
dash.agent_plist = real_plist
# A server launchd starts waits on a held store lock instead of exiting into launchd's retry loop; ensure stops
# the server launchd did not start, and the waiting one takes over.
ok, msg = dash.ensure(port)
foreign = dash.health(port)
assert ok and foreign, msg
agent = subprocess.Popen([sys.executable, str(pathlib.Path(dash.__file__)), "serve", "--port", str(port)],
                         env=dict(os.environ, XPC_SERVICE_NAME=dash.agent_label()),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2)
assert agent.poll() is None, "launchd's server exited on the held lock"
kicks, stale, real_health = [], [False], dash.health
real_status, real_kickstart, real_under, real_restart = dash.agent_status, dash.kickstart, dash.under_agent, dash.restart
dash.agent_status = lambda: {"pid": agent.pid, "port": port}
dash.kickstart = lambda kill: (kicks.append(kill), stale.__setitem__(0, False))
ok, msg = dash.ensure(port)
assert ok and dash.health(port)["pid"] == agent.pid and dash.health(port)["pid"] != foreign["pid"], msg
assert dash.ensure(port) == (True, f"dashboard: http://127.0.0.1:{port}/ (launchd)")
# Its own server on older code is restarted through launchd (kickstart -k), never started here.
kicks.clear()
stale[0] = True
dash.health = lambda p: (h := real_health(p)) and dict(h, stale=stale[0])
ok, msg = dash.ensure(port)
assert ok and kicks == [True], (msg, kicks)
# A live process whose fast loop stopped ticking is restarted too, even though the slow loop keeps its heartbeat
# fresh; one that never reports ticks (older code) is judged by its version alone.
kicks.clear()
stale[0] = False
old_tick = dash.iso(dash.utcnow() - dash.LOOP_STALE - dash.dt.timedelta(minutes=1))
dash.health = lambda p: (h := real_health(p)) and dict(h, ticks={"slow": dash.iso(dash.utcnow()), "fast": old_tick})
assert dash.fresh(dash.health(port))
ok, msg = dash.ensure(port)
assert kicks == [True], (msg, kicks)
assert not dash.loops_alive({"ticks": {"slow": dash.iso(dash.utcnow())}}) and dash.loops_alive({})
assert dash.loops_alive({"ticks": {"fast": dash.iso(dash.utcnow())}})
kicks.clear()
dash.health = lambda p: (h := real_health(p)) and dict(h, ticks={"fast": dash.iso(dash.utcnow())})
ok, msg = dash.ensure(port)
assert ok and kicks == [], (msg, kicks)
# ...unless the code on disk does not import: then the old server is the one to keep.
kicks.clear()
stale[0] = True
dash.health = lambda p: (h := real_health(p)) and dict(h, stale=stale[0], broken=h["disk_version"])
ok, msg = dash.ensure(port)
assert ok and kicks == [] and "does not import" in msg, (msg, kicks)
dash.health = real_health
agent.terminate()
agent.wait(10)
# Under launchd a server whose code on disk changed exits for launchd to start the new code, once the change has
# held for two checks (a checkout still writing files is not restarted mid-way).
restarts = []
dash.under_agent, dash.restart = lambda: True, lambda: restarts.append(1)
real_loads = dash.loads
dash.HEALTH.update(version="started-as", disk_version="something-else")
dash.check_code()
assert dash.HEALTH["stale"] and not restarts
dash.loads = lambda: False  # a commit that does not import keeps the old server up
dash.check_code()
assert not restarts and dash.HEALTH["broken"] == dash.code_version()
dash.HEALTH.pop("broken")
dash.loads = real_loads
dash.check_code()
assert restarts == [1]
dash.HEALTH.update(version=dash.code_version())
dash.agent_status, dash.kickstart, dash.under_agent, dash.restart = real_status, real_kickstart, real_under, real_restart
del os.environ["ORCH_DASH_LABEL"]

# The wordmark in the sidebar is a link home; the page shows the served version and a banner when it is stale.
html = (dash.ASSETS / "index.html").read_text()
assert re.search(r'<a class="brand" href="#/fleet/overview"[^>]*>\s*<span class="logo"', html), "logo is not a home link"
assert 'id="code-version"' in html and 'id="stale-code"' in html

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
# Three quiet days rest it the same way, and the page stops counting its unpolled sources as stale.
old = dash.utcnow() - dash.QUIET - dash.dt.timedelta(minutes=1)
assert dash.resting(False, dash.iso(old)) and not dash.resting(False, dash.iso(dash.utcnow())) and not dash.resting(False, None)
append(B, ts=dash.iso(old), ev="note", note="quiet")
assert dash.closed(B) and dash.collect(B, sources=())["summary"]["resting"]
append(B, ev="note", note="back")
assert not dash.closed(B)
# A bad timestamp in one ledger leaves that program polled and shows on its row; it does not stop the loop.
append(B, ts="not-a-time", ev="note", note="hand-edited")
assert not dash.closed(B) and not dash.resting(False, 12345)
st = dash.collect(B, sources=())
assert not st["summary"]["resting"] and "ledger: 1 event(s) with a bad ts left out, first 'not-a-time'" in st["errors"], st["errors"]
lines = (store / B / "ledger.jsonl").read_text().splitlines()
(store / B / "ledger.jsonl").write_text("\n".join(l for l in lines if "not-a-time" not in l) + "\n")
assert not any(e.startswith("ledger:") for e in dash.collect(B, sources=())["errors"])
# A ts without a timezone parses, so only the tzinfo check keeps it from a TypeError against now.
with (store / B / "ledger.jsonl").open("a") as f:
    f.write(json.dumps({"ts": "2026-10-23T10:00:00", "ev": "note", "note": "hand-edited"}) + "\n")
errs = dash.collect(B, sources=())["errors"]
assert "ledger: 1 event(s) with a bad ts left out, first '2026-10-23T10:00:00'" in errs, errs
lines = (store / B / "ledger.jsonl").read_text().splitlines()
(store / B / "ledger.jsonl").write_text("\n".join(lines[:-1]) + "\n")
# A row that is not an object, or has no ev, is set aside before anything indexes e["ev"]: closed() runs outside
# both loops' try, so it once raised there and stopped every program on the loop's second round.
clean = (store / B / "ledger.jsonl").read_text()
last_ts = dash.ledger_tail(B)[1]
with (store / B / "ledger.jsonl").open("a") as f:
    f.write("[1, 2]\n" + json.dumps({"ts": dash.iso(dash.utcnow()), "note": "no ev"}) + "\n")
assert not dash.closed(B) and dash.ledger_tail(B)[1] == last_ts  # the last event, not the rejected row after it
st = dash.collect(B, sources=())
assert "ledger: 2 row(s) without an ev left out, first [1, 2]" in st["errors"] and st["summary"], st["errors"]
assert not any(e.startswith("ledger:") for e in dash.collect(A, sources=())["errors"])
real_wake, real_collect, waits, collected = dash.SLOW_WAKE, dash.collect, [], []
dash.collect = lambda slug, *a, **k: collected.append(slug) or {"errors": []}
class StopAfterTwo:
    def clear(self):
        pass
    def wait(self, _):
        waits.append(1)
        if len(waits) == 2:
            raise StopIteration
dash.SLOW_WAKE = StopAfterTwo()
try:
    dash.slow_loop(1)
except StopIteration:
    pass
dash.SLOW_WAKE, dash.collect = real_wake, real_collect
assert collected.count(B) == 2 and A in collected, collected  # the second round reached B through closed()
(store / B / "ledger.jsonl").write_text(clean)

# A program listed by programs() whose directory is gone before the loop reads it (closed(), signature()) is resting,
# and neither loop ends: both once died on the first FileNotFoundError and nothing noticed.
class Stop(BaseException):
    pass
def run_loop(loop, rounds_wanted):
    n = []
    def sleep(_):
        n.append(1)
        if len(n) == rounds_wanted:
            raise Stop
    class Ev:  # the slow loop waits on its wake event, the fast loop sleeps
        def clear(self):
            pass
        def set(self):
            pass
        def wait(self, t):
            sleep(t)
    real_sleep, dash.time.sleep, dash.SLOW_WAKE = dash.time.sleep, sleep, Ev()
    try:
        loop(1)
    except Stop:
        pass
    finally:
        dash.time.sleep, dash.SLOW_WAKE = real_sleep, real_wake
    return len(n)
GONE = "vanished-slug"
real_programs, real_orca_every = dash.programs, dash.ORCA_EVERY
dash.programs = lambda: [GONE, A]
dash.ORCA_EVERY = 0
assert dash.closed(GONE) and dash.signature(GONE) == (None, None, None)
collected.clear()
dash.collect = lambda slug, *a, **k: collected.append(slug) or {"errors": []}
dash.HEALTH.pop("ticks", None)
assert run_loop(dash.fast_loop, 3) == 3 and collected.count(A) >= 2, collected  # survived 3 rounds, A still served
assert run_loop(dash.slow_loop, 3) == 3 and "fast" in dash.HEALTH["ticks"] and "slow" in dash.HEALTH["ticks"]
assert dash.loops_alive(dash.HEALTH)
# The tick moves with each program, so a round of many slow collects is not read as a stopped loop.
seen_ticks = []
dash.collect = lambda slug, *a, **k: (seen_ticks.append(dash.HEALTH["ticks"]["fast"]), {"errors": []})[1]
dash.HEALTH["ticks"]["fast"] = "2000-01-01T00:00:00Z"
dash.fast_round(({}, {}, {}), 1)
assert len(seen_ticks) == 1 and seen_ticks[0] > "2020", seen_ticks  # ticked after the vanished program, before A's collect
dash.collect = lambda slug, *a, **k: collected.append(slug) or {"errors": []}
# Any other error in one program's step stays on that program; an error from programs() itself costs one round.
def boom(slug):
    if slug == GONE:
        raise RuntimeError("bad ledger")
    return real_signature(slug)
real_signature = dash.signature
dash.signature = boom
collected.clear()
assert run_loop(dash.fast_loop, 3) == 3 and A in collected, collected
dash.signature = real_signature
def no_programs():
    raise OSError("store unreadable")
dash.programs = no_programs
dash.HEALTH["ticks"] = {}
assert run_loop(dash.fast_loop, 2) == 2 and "fast" in dash.HEALTH["ticks"]  # logged, ticked, still looping
assert run_loop(dash.slow_loop, 2) == 2
dash.programs, dash.ORCA_EVERY, dash.collect = real_programs, real_orca_every, real_collect
# The stale check runs on its own thread, so a collector that died or hangs does not freeze it.
checks = []
real_check = dash.check_code
def failing_check():
    checks.append(1)
    1 / 0
dash.check_code = failing_check  # raises every time; the loop lives
assert run_loop(lambda _i: dash.code_loop(), 4) == 4 and len(checks) == 3, checks
dash.check_code = real_check

# The screen quotes prog.py's cleanup line, so the two cannot drift, and a person is not told to remove cards by hand.
app_js = pathlib.Path(__file__).parents[1].joinpath("assets/dashboard/app.js").read_text()
assert f"const CLEANUP = `{dash.prog.CLEANUP}`;" in app_js
assert "for the cleaners (${CLEANUP})" in app_js and "${esc(CLEANUP)}" in app_js
assert "worktree rm" not in app_js

# The programs poll stops on a hidden tab, resumes once when the tab returns, and revalidates with If-None-Match.
poll_src = app_js.split("async function pollPrograms()", 1)[1].split("\n}\n", 1)[0]
assert poll_src.lstrip().startswith("{\n  if (document.hidden || programsBusy) return;"), poll_src[:80]
assert "if (!document.hidden) { pollState(); pollPrograms(); }" in app_js
assert 'conditionalFetch("/api/fleet/state", S.fleet, "?viewed=1")' in app_js and 'slug === "fleet" ? "?viewed=1"' in app_js, \
    "both visible-tab polls of the fleet state must say ?viewed=1"
assert '"If-None-Match": etag' in app_js and 'r.status === 304' in app_js
assert "if (changed) renderSidebar();" in poll_src
assert "POLL_BODY[url] === have" in app_js  # an ETag is sent only for the object it came with
assert "programsBusy" in poll_src and "finally { programsBusy = false; }" in poll_src
read_src = app_js.split("async function readConditional", 1)[1].split("\n}\n", 1)[0]
assert read_src.index("await r.json()") < read_src.index("POLL_ETAG[url] = etag")  # parse before the ETag is kept

# state.json: an unchanged collect leaves the file alone, a change or the beat rewrites it.
sp = store / B / "dashboard" / "state.json"
dash.collect(B, sources=())
ino, m0 = sp.stat().st_ino, sp.stat().st_mtime_ns
dash.collect(B, sources=())
assert (sp.stat().st_ino, sp.stat().st_mtime_ns) == (ino, m0), "an unchanged state.json was written again"
append(B, ev="note", note="a new ledger row")
dash.collect(B, sources=())
assert sp.stat().st_mtime_ns != m0, "a changed state was not written"
m1 = sp.stat().st_mtime_ns
dash.STATE_WROTE[sp] = (dash.STATE_WROTE[sp][0], dash.STATE_WROTE[sp][1] - dash.STATE_BEAT_S - 1)
dash.collect(B, sources=())
assert sp.stat().st_mtime_ns != m1, "the heartbeat did not rewrite an unchanged state.json"
sp.unlink()
dash.collect(B, sources=())
assert sp.exists(), "a deleted state.json must come back even when nothing changed"

# gh pr list: programs on one repo share a round's lists, a renamed repo counts once, and a failure is asked once.
calls = []
real_sh = dash.sh


def fake_sh(cmd, timeout=45):
    calls.append(cmd)
    if cmd[:2] == ["gh", "api"]:
        return {"n": "Acme/New-Name"}
    if "--state" in cmd and cmd[cmd.index("--state") + 1] == "open":
        return [{"number": 1, "state": "OPEN", "createdAt": "2026-10-01T00:00:00Z"}]
    return [{"number": n, "state": "MERGED", "createdAt": d} for n, d in ((2, "2026-10-20T00:00:00Z"), (3, "2026-10-02T00:00:00Z"))]


dash.sh = fake_sh
try:
    old = {"repo": "Acme/Old-Name", "created_at": "2026-10-10T00:00:00Z"}
    new = {"repo": "acme/new-name", "created_at": "2026-10-10T00:00:00Z"}
    wide = {"repo": "acme/new-name", "created_at": "2026-10-01T00:00:00Z"}
    dash.ROUND_PRS[0] = {}
    got = [[p["number"] for p in dash.fetch_prs(c)] for c in (wide, old, new)]
    lists = [c for c in calls if c[:3] == ["gh", "pr", "list"]]
    assert len(lists) == 2 and all(c[c.index("--repo") + 1] == "acme/new-name" for c in lists), lists
    assert got == [[1, 2, 3], [1, 2], [1, 2]], got  # the narrower windows are cut from the wide one by createdAt
    got_open = dash.fetch_prs(old)
    got_open[0]["state"] = "mutated"
    assert dash.fetch_prs(new)[0]["state"] == "OPEN", "a caller changed the round's shared rows"
    dash.ROUND_PRS[0] = None
    calls.clear()
    dash.fetch_prs(new), dash.fetch_prs(new)
    assert len([c for c in calls if c[:3] == ["gh", "pr", "list"]]) == 4, "outside a round every collect asks"
    calls.clear()

    def down(cmd, timeout=45):
        calls.append(cmd)
        if cmd[:2] == ["gh", "api"]:
            return {"n": "acme/new-name"}
        raise dash.SourceError("HTTP 502")
    dash.sh, dash.ROUND_PRS[0] = down, {}
    for _ in range(3):
        try:
            dash.fetch_prs(new)
            raise AssertionError("a failing gh list returned rows")
        except dash.SourceError:
            pass
    assert len([c for c in calls if c[:3] == ["gh", "pr", "list"]]) == 1, calls
finally:
    dash.sh, dash.ROUND_PRS[0] = real_sh, None

# The brief render cache: rendered again only when its inputs' signature or the age limit says so.
n, sig = [0], [("a",)]
rc = dash.RenderCache(lambda v: "text" in v)
real_sig = dash.render_signature
dash.render_signature = lambda: sig[0]
try:
    def build():
        n[0] += 1
        return {"text": str(n[0])} if n[0] != 2 else {"message": "timed out"}
    assert rc.get(build)["text"] == "1" and rc.get(build)["text"] == "1" and n[0] == 1
    sig[0] = ("b",)
    assert rc.get(build) == {"message": "timed out"} and n[0] == 2
    assert rc.get(build)["text"] == "3", "a failed render must not be kept"
    assert rc.get(build)["text"] == "3" and n[0] == 3
    rc.at -= dash.RENDER_MAX_AGE_S + 1
    assert rc.get(build)["text"] == "4"
finally:
    dash.render_signature = real_sig

# The fleet's own files: clocks alone do not rewrite state.json or memory.json; data or the beat does.
fl_dir = pathlib.Path(tempfile.mkdtemp(prefix="fleet-quiet-"))
os.environ["ORCH_FLEET_STATE"] = str(fl_dir)


class Stub:
    saved = {}


def state_text(gen, upd, items):
    return json.dumps({"generated_at": gen, "sources": {"orca": {"ok": True, "updated_at": upd}}, "items": items})


stub = Stub()
dash.quiet_save(stub, **{"state.json": state_text("t1", "u1", [1]), "memory.json": json.dumps({"stage_auto_at": "a1", "k": 1})})
m_state = (fl_dir / "state.json").stat().st_mtime_ns
dash.quiet_save(stub, **{"state.json": state_text("t2", "u2", [1]), "memory.json": json.dumps({"stage_auto_at": "a2", "k": 1})})
assert json.loads((fl_dir / "state.json").read_text())["generated_at"] == "t1", "only clocks moved: no write"
assert json.loads((fl_dir / "memory.json").read_text())["stage_auto_at"] == "a1"
dash.quiet_save(stub, **{"state.json": state_text("t3", "u3", [1, 2])})
assert json.loads((fl_dir / "state.json").read_text())["items"] == [1, 2], "a data change is written"
stub.wrote["state.json"] = (stub.wrote["state.json"][0], stub.wrote["state.json"][1] - dash.FLEET_BEAT_S["state.json"] - 1)
dash.quiet_save(stub, **{"state.json": state_text("t4", "u4", [1, 2])})
assert json.loads((fl_dir / "state.json").read_text())["generated_at"] == "t4", "the beat writes an unchanged state"
dash.quiet_save(stub, **{"prs.json": "{}"})
dash.quiet_save(stub, **{"prs.json": "{}"})
assert (fl_dir / "prs.json").read_text() == "{}"

# fetch_orca reads the fleet tick's worktrees while that tick is fresh, in `worktree list`'s shape; else it asks Orca.
class FakeFleet:
    def __init__(self, at):
        self.fast = {"worktrees": [{"worktreeId": "wt1", "path": "/x/wt1", "isMainWorktree": False}]}
        self.sources = {"orca": {"updated_at": at}}


real_fleet, real_orca_result = dash.FLEET, dash.orca_result
try:
    dash.FLEET = None
    assert dash.fleet_worktrees() is None
    dash.FLEET = FakeFleet(dash.iso(dash.utcnow()))
    assert dash.fleet_worktrees() == [{"worktreeId": "wt1", "path": "/x/wt1", "isMainWorktree": False, "id": "wt1"}]
    dash.FLEET = FakeFleet(dash.iso(dash.utcnow() - dt_mod.timedelta(seconds=dash.FLEET_SNAPSHOT_TTL_S + 5)))
    assert dash.fleet_worktrees() is None, "a stale fleet snapshot must not be reused"
    dash.FLEET = FakeFleet(None)
    assert dash.fleet_worktrees() is None

    # fetch_orca asks Orca for no worktree list while the snapshot is fresh, and does when it is not.
    asked = []
    real_sh2 = dash.sh
    dash.sh = lambda cmd, timeout=45: (asked.append(cmd[:3]), real_sh2(cmd, timeout))[1]
    cfg_a = json.loads((dash.program_dir(A) / "program.json").read_text())
    dash.FLEET = FakeFleet(dash.iso(dash.utcnow()))
    dash.fetch_orca(cfg_a)
    assert ["orca", "worktree", "list"] not in asked, asked
    dash.FLEET = None
    dash.fetch_orca(cfg_a)
    assert ["orca", "worktree", "list"] in asked, asked
    dash.sh = real_sh2

    # The Run, its workers and its tasks come from the fleet tick's runs snapshot while it is fresh.
    class RunsFleet(FakeFleet):
        def __init__(self, at, err=None, objective=True):
            super().__init__(dash.iso(dash.utcnow()))
            run = {"id": "run_a", **({"objective": "ship A"} if objective else {})}
            self.sources["runs"] = {"updated_at": at, "error": err}
            self.runs = {"runs": [run], "workers": [{"runId": "run_a", "dispatchId": "d1"}, {"runId": "run_b", "dispatchId": "d2"}],
                         "tasks": [{"id": "t1", "run_id": "run_a"}, {"id": "t2", "run_id": "run_b"}]}

    now_iso = dash.iso(dash.utcnow())
    dash.FLEET = RunsFleet(now_iso)
    assert dash.fleet_run("run_a") == ({"id": "run_a", "objective": "ship A"}, [{"runId": "run_a", "dispatchId": "d1"}],
                                       [{"id": "t1", "run_id": "run_a"}])
    assert dash.fleet_run("run_zzz") is None, "a Run the snapshot does not list is asked of Orca"
    settled = RunsFleet(now_iso)
    settled.runs["workers"][0]["dispatchStatus"] = "completed"
    settled.runs["tasks"][0]["status"] = "completed"
    dash.FLEET = settled
    assert dash.fleet_run("run_a") is None, "a settled Run may sit in the fleet's 900 s cache: asked of Orca"
    settled.runs["tasks"][0]["status"] = "running"
    assert dash.fleet_run("run_a") is not None, "one moving task keeps the Run on the snapshot"
    settled.runs["tasks"][0]["status"] = "completed"
    settled.runs["workers"][0]["dispatchStatus"] = "running"
    assert dash.fleet_run("run_a") is not None, "one running worker keeps the Run on the snapshot"
    settled.runs["workers"], settled.runs["tasks"] = [], []
    assert dash.fleet_run("run_a") is None, "a Run with no worker and no task (gates only, or empty) is asked of Orca"
    dash.FLEET = RunsFleet(now_iso)
    dash.FLEET = RunsFleet(dash.iso(dash.utcnow() - dt_mod.timedelta(seconds=dash.FLEET_RUNS_TTL_S + 5)))
    assert dash.fleet_run("run_a") is None, "a stale runs snapshot must not be reused"
    dash.FLEET = RunsFleet(now_iso, err="boom")
    assert dash.fleet_run("run_a") is None, "a failed runs read must not be reused"
    dash.FLEET = RunsFleet(now_iso, objective=False)
    assert dash.fleet_run("run_a") is None, "a Run row without an objective (Orca before --full) is asked of Orca"
    dash.FLEET = None
    assert dash.fleet_run("run_a") is None

    # fetch_orca makes no worker-list, task-list or run-show call while the snapshot is fresh, and does when it is not.
    asked = []
    dash.sh = lambda cmd, timeout=45: (asked.append(cmd[:3]), real_sh2(cmd, timeout))[1]
    real_result = dash.orca_result
    dash.orca_result = lambda *a: (asked.append(["orca_result", *a[:1]]), real_result(*a))[1]
    cfg_run = {**cfg_a, "run": "run_a", "lead_dispatch": None}
    dash.FLEET = RunsFleet(now_iso)
    got = dash.fetch_orca(cfg_run)
    assert not [a for a in asked if a[0] == "orca_result"], asked
    assert got["run"]["objective"] == "ship A" and [w["dispatchId"] for w in got["workers"]] == ["d1"] and [t["id"] for t in got["tasks"]] == ["t1"]
    dash.FLEET = None
    try:
        dash.fetch_orca(cfg_run)
    except Exception:
        pass  # no Orca in the test: what matters is that it asked
    assert [a for a in asked if a[0] == "orca_result"], "without a snapshot fetch_orca must ask Orca"
    dash.sh, dash.orca_result = real_sh2, real_result
finally:
    dash.FLEET = real_fleet
    dash.sh = real_sh
    dash.orca_result = real_orca_result

# A write skipped for an unchanged state still keeps the sources' new success times for the next collect.
import time as time_mod
sp = store / A / "dashboard" / "state.json"
dash.collect(A, sources=("github",))
time_mod.sleep(1.1)
dash.collect(A, sources=("github",))  # the same data again: its updated_at moves, the write is skipped
t_ok = dash.collect(A, sources=())["sources"]["github"]["updated_at"]
assert json.loads(sp.read_text())["sources"]["github"]["updated_at"] < t_ok, "the skipped write reached the disk"
dash.STATE_WROTE[sp] = (dash.STATE_WROTE[sp][0], dash.STATE_WROTE[sp][1] - dash.STATE_BEAT_S - 1)
dash.collect(A, sources=())
assert json.loads(sp.read_text())["sources"]["github"]["updated_at"] == t_ok, "the heartbeat write lost a source's success time"

# The render signature sees the files the renders read outside the store.
state_root = pathlib.Path(tempfile.mkdtemp(prefix="agent-skills-state-"))
os.environ["AGENT_SKILLS_STATE"] = str(state_root)
sig0 = dash.render_signature()
(state_root / "janitor-plan.json").write_text('{"at": "x", "items": []}')
sig1 = dash.render_signature()
assert sig0 != sig1, "a janitor-plan.json change did not move the render signature"
append(B, ev="note", note="signature probe")
assert dash.render_signature() != sig1, "a program ledger change did not move the render signature"
del os.environ["AGENT_SKILLS_STATE"]

# A file another process wrote after the skipped write wins over the remembered sources.
dash.collect(A, sources=("github",))
dash.collect(A, sources=("github",))  # skipped, remembered
other = json.loads(sp.read_text())
other["sources"]["github"] = {"updated_at": "2099-01-01T00:00:00Z", "ok": False, "error": "from an external collect"}
sp.write_text(json.dumps(other))
assert dash.collect(A, sources=())["sources"]["github"]["error"] == "from an external collect", "an external collect was overwritten"

# An external write between this collect's read and its skipped write is not overwritten by the next one either.
dash.collect(A, sources=())
sig_state = dash.STATE_WROTE[sp]
race_key = dash.file_key(sp)
time_mod.sleep(0.01)
ext = json.loads(sp.read_text())
ext["sources"]["github"] = {"updated_at": "2099-01-01T00:00:00Z", "ok": False, "error": "raced"}
sp.write_text(json.dumps(ext))  # lands after the (stale) read_key was taken
st_race = json.loads(sp.read_text())
st_race["sources"] = {"github": {"updated_at": "old", "ok": True}}
dash.STATE_WROTE[sp] = (dash.settled(st_race), time_mod.monotonic())
dash.write_state(sp, st_race, race_key)
assert sp not in dash.STATE_SOURCES, "sources were remembered for a file this state was not built from"
assert dash.collect(A, sources=())["sources"]["github"].get("error") == "raced"

# The store's mark moves on a commit to the DB or to its -wal, and no connection is opened to read it.
fake_db = root / "fake.db"
real_path = dash.store_db.path
dash.store_db.path = lambda: fake_db
try:
    m0 = dash.db_version()
    fake_db.write_text("x")
    m1 = dash.db_version()
    pathlib.Path(f"{fake_db}-wal").write_text("w")
    m2 = dash.db_version()
    assert m0 != m1 != m2 and m0 != m2, (m0, m1, m2)
finally:
    dash.store_db.path = real_path

# The signature follows the patrol file beside a custom fleet dir, and prs.json gets a beat of its own.
custom = pathlib.Path(tempfile.mkdtemp(prefix="custom-root-"))
(custom / "dashboard").mkdir()
real_fs = os.environ.get("ORCH_FLEET_STATE")
os.environ["ORCH_FLEET_STATE"] = str(custom / "dashboard")
try:
    s0 = dash.render_signature()
    (custom / "patrol-verdicts.json").write_text("{}")
    assert dash.render_signature() != s0, "patrol-verdicts.json beside a custom fleet dir is not in the signature"
finally:
    os.environ["ORCH_FLEET_STATE"] = real_fs if real_fs is not None else str(fl_dir)
assert dash.FLEET_BEAT_S["prs.json"] < 1800 and dash.FLEET_BEAT_S["memory.json"] < 300
beat = type("S", (), {"saved": {}})()
dash.quiet_save(beat, **{"prs.json": '{"prs": {}}'})
beat.wrote["prs.json"] = (beat.wrote["prs.json"][0], beat.wrote["prs.json"][1] - dash.FLEET_BEAT_S["prs.json"] - 1)
before_beat = (fl_dir / "prs.json").stat().st_mtime_ns
time_mod.sleep(0.01)
dash.quiet_save(beat, **{"prs.json": '{"prs": {}}'})
assert (fl_dir / "prs.json").stat().st_mtime_ns != before_beat, "an unchanged prs.json never gets a beat"

print("dash.py collect/serve: all pass")
