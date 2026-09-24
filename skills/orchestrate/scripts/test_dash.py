"""Offline tests for dash.py: every source from fixtures, a failing source, series dedupe, atomic writes.

Run: python3 test_dash.py
"""
import http.server, json, os, pathlib, sys, tempfile, threading, urllib.request, urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import dash, dash_demo

root = pathlib.Path(tempfile.mkdtemp(prefix="test-dash-"))
os.environ.update(dash_demo.build(root))
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

# The real tracker adapter, when present, satisfies the same interface through TRACKER_FIXTURES.
real = pathlib.Path(__file__).resolve().parents[2] / "use-tracker/scripts/tracker.py"
if real.exists():
    os.environ["TRACKER_PY"] = str(real)
    os.environ["TRACKER_FIXTURES"] = str(fx / "tracker" / "Launchpad GA")
    st = dash.collect(A, sources=("tracker",))
    assert st["sources"]["tracker"]["ok"] and st["summary"]["predicate_done"] == 5, st["errors"]
    print("  (checked against the real use-tracker/scripts/tracker.py)")

print("dash.py collect/serve: all pass")
