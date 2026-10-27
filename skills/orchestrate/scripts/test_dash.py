"""Offline tests for dash.py: every source from fixtures, a failing source, series dedupe, atomic writes.

Run: python3 test_dash.py
"""
import http.server, json, os, pathlib, re, sys, tempfile, threading, urllib.request, urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).parent))
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
assert [p["slug"] for p in progs if p["slug"] in (A, B)] == [B, A]
done = {p["slug"]: (p["closed"], bool(p["last_event"])) for p in progs}
assert done["docs-refresh"] == (True, True) and done["metrics-spike"] == (False, True) and not done[A][0], done
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
# A decision answered on the page is recorded through its own endpoint, behind the same Host and token guard; the send
# box's endpoint (and its idle check) is not in its path.
answers, dash.fleet.decision_answer = [], lambda *a: answers.append(a) or (200, {"ok": True, "delivered": False})
body = json.dumps({"decision": "d1", "answer": "CSV"}).encode()
assert post({"Host": host}, "/api/fleet/decide") == 403 and answers == []
assert post({"Host": host, "X-Dash-Token": dash.TOKEN}, "/api/fleet/decide") == 200
assert answers == [("d1", "CSV")], answers
sk = json.loads(urllib.request.urlopen(f"{base}/api/fleet/skills").read())
assert {"agent-skills", "user", "other"} <= {x["source"] for x in sk["sources"]}, sk["sources"]
assert next(r for r in sk["skills"] if r["name"] == "agent-skills:teach")["flags"] == ["unused_30d"]
try:
    urllib.request.urlopen(urllib.request.Request(f"{base}/api/fleet/skills", headers={"Host": f"evil.example:{srv.server_address[1]}"}))
    raise AssertionError("a rebinding name read skill usage")
except urllib.error.HTTPError as e:
    assert e.code == 403, e.code
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
assert "| 🔴 | 로그인 개편 | #41 ? |" in br["text"] and br["width_ok"] is True and "snapshot" not in br, br
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
real_page, dash.sent.page = dash.sent.page, lambda *a: {}["boom"]
try:
    assert sent_api("?days=1") == {"error": "sent: KeyError: 'boom'"}
finally:
    dash.sent.page = real_page
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
real_sleep, real_collect, rounds, collected = dash.time.sleep, dash.collect, [], []
dash.collect = lambda slug, *a, **k: collected.append(slug) or {"errors": []}
def sleep(_):
    rounds.append(1)
    if len(rounds) == 2:
        raise StopIteration
dash.time.sleep = sleep
try:
    dash.slow_loop(1)
except StopIteration:
    pass
dash.time.sleep, dash.collect = real_sleep, real_collect
assert collected.count(B) == 2 and A in collected, collected  # the second round reached B through closed()
(store / B / "ledger.jsonl").write_text(clean)

print("dash.py collect/serve: all pass")
