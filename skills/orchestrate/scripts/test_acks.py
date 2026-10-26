"""Offline tests for acks.py through `orch-dash ack`, the brief render and the dashboard's POST, on invented data.

Run: python3 test_acks.py
"""
import datetime as dt, json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-acks-"))
env = {**os.environ, "ORCH_FLEET_STATE": str(tmp / "dashboard"), "ORCH_BRIEF_LAST": str(tmp / "brief-last"),
       "ORCH_FLEET_CONFIG": str(tmp / "none.json"), "AGENT_SKILLS_STATE": str(tmp / "state"), "TZ": "UTC",
       "AGENT_SKILLS_CONFIG": str(tmp / "none.json")}
os.environ.update(env)
sys.path.insert(0, str(HERE))
import acks, board, fleet, owner_requests, stages  # noqa: E402


def run(script, *args):
    r = subprocess.run([sys.executable, str(HERE / script), *args], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def brief():
    code, out, err = run("prog.py", "brief", "render", "--periodic", "--json", "--no-refresh")
    assert code == 0, (out, err)
    return json.loads(out)


now = fleet.utcnow()
# Work: a finished group with one finished row, a finished row with an open child, an open row.
stages.add("g", "릴리스", group=True)
stages.add("g1", "배포 스크립트", parent="g")
stages.add("w", "로그인 개편")
stages.add("w1", "세션 만료", parent="w")
stages.add("open", "결제 화면")
for k in ("g1", "g", "w"):
    stages.finish(k)
# Brief: a finished lead, a finished lead with a sub-task still moving, a moving lead.
board.lead_add("lead-a", "term_0FAKE1", "acme/tools")
board.set_row("lead-a", title="릴리스 도구", status="done", progress="2/2", next="—")
board.lead_add("lead-b", "term_0FAKE2", "acme/app")
board.set_row("lead-b", title="청구 내보내기", status="done", next="—")
board.set_row("lead-b/t", title="CSV 열", status="moving", next="CI · 워커")
board.lead_add("lead-c", "term_0FAKE3", "acme/web")
board.set_row("lead-c", title="설치 가이드", status="moving", next="리뷰 반영")
for ago, text in ((600, "릴리스 노트 봐줘"), (30, "acme/app#1 머지되면 알려줘")):
    owner_requests.add(text, "s-demo", "term_0FAKE0", now=now - dt.timedelta(minutes=ago))

can = acks.ackable()
assert {"stage:g", "stage:g1", "board:lead-a", "req:r1", "req:r2"} <= set(can), sorted(can)
for no in ("stage:w", "stage:w1", "stage:open", "board:lead-b", "board:lead-b/t", "board:lead-c"):
    assert no not in can, (no, sorted(can))

# The CLI refuses what is not over and writes the rest; a bare rN is a request.
code, out, err = run("dash.py", "ack", "stage:g", "stage:w", "board:lead-c", "r2", "nonsense")
assert code == 1 and out == "acked 2: stage:g req:r2", (code, out, err)
assert "refused stage:w:" in err and "refused board:lead-c:" in err and "refused nonsense:" in err, err
assert set(acks.acked()) == {"stage:g", "req:r2"}
code, out, _ = run("dash.py", "ack", "stage:g")  # already acked: nothing written, nothing refused
assert code == 0 and out == "acked 0", out
code, out, err = run("dash.py", "unack", "stage:g", "stage:g1")
assert code == 1 and out == "unacked 1: stage:g" and "refused stage:g1: not acked" in err, (out, err)
assert set(acks.acked()) == {"req:r2"}

# --done-before: only what finished (a request: was asked) before the cutoff, per kind.
code, out, err = run("dash.py", "ack", "--done-before", "6h", "--kind", "req")
assert code == 0 and out == "acked 1: req:r1", (out, err)  # r2 is 30 min old and acked already
code, out, err = run("dash.py", "ack", "--done-before", "24h")
assert code == 0 and out == "acked 0", (out, err)  # every row finished just now
code, out, err = run("dash.py", "ack", "--done-before", "soon")
assert code == 1 and "an age is N" in err, err
code, out, err = run("dash.py", "unack", "r1", "r2")
assert code == 0 and out == "unacked 2: req:r1 req:r2", (out, err)

# The brief: an acked ✅ row leaves the table, every open row stays, and the requests line counts only unacked ones.
before = brief()
assert {d["id"] for d in before["done"]} == {"board:lead-a"}, before["done"]
assert "릴리스 도구" in before["text"] and "요청 2건 열림" in before["text"], before["text"]
code, out, err = run("dash.py", "ack", "board:lead-a", "r1")
assert code == 0, err
after = brief()
assert "릴리스 도구" not in after["text"] and "acme/tools" not in after["text"], after["text"]  # its section goes too
for kept in ("청구 내보내기", "CSV 열", "설치 가이드"):
    assert kept in after["text"], (kept, after["text"])
assert "확인함으로 숨긴 행 1" in after["text"] and "요청 1건 열림" in after["text"], after["text"]
assert after["done"] == [{**before["done"][0], "acked": True}], after["done"]
assert owner_requests.summary()["open"] == 2, "the orchestrator's or lead's own count keeps an acked request"
code, out, _ = run("dash.py", "acks", "--json")
assert {r["id"] for r in json.loads(out)} == {"board:lead-a", "req:r1"}, out

# The file is the owner's alone and only ever appended to.
f = acks.path()
assert f.stat().st_mode & 0o777 == 0o600, oct(f.stat().st_mode)
assert [json.loads(line)["ev"] for line in f.read_text().splitlines()] == ["ack", "ack", "unack", "ack", "unack", "unack", "ack", "ack"]

# The dashboard's POST: token and same-origin guard as every other write, then the same acks.write.
import dash  # noqa: E402
import http.server, threading, urllib.request, urllib.error  # noqa: E402
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), dash.Handler)
port = srv.server_address[1]
dash.HEALTH["port"] = port
threading.Thread(target=srv.serve_forever, daemon=True).start()


def post(body, token=dash.TOKEN, origin=None):
    h = {"Content-Type": "application/json", "X-Dash-Token": token, **({"Origin": origin} if origin else {})}
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/fleet/ack", json.dumps(body).encode(), h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None


assert post({"ids": ["stage:g"]}, token="wrong")[0] == 403
assert post({"ids": ["stage:g"]}, origin="http://evil.example.test")[0] == 403
code, out = post({"ids": ["stage:g", "stage:open"]})
assert code == 200 and out == {"written": ["stage:g"], "refused": [["stage:open", acks.why_not("stage:open")]]}, out
with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/fleet/brief", timeout=30) as r:
    marks = {row["id"]: (row["ack"], row["hidden"]) for row in json.loads(r.read())["stages"]["rows"]}
assert marks == {"g": ("acked", True), "g1": ("ackable", True), "w": (None, False), "w1": (None, False),
                 "open": (None, False)}, marks  # a row under an acked group hides with it
code, out = post({"ids": ["stage:g"], "undo": True})
assert code == 200 and out["written"] == ["stage:g"], out
assert post({"ids": "stage:g"})[0] == 404, "ids is a list"
def work():
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/fleet/brief", timeout=30) as r:
        return {row["id"]: (row["ack"], row["hidden"]) for row in json.loads(r.read())["stages"]["rows"]}


# An ack holds only while what it hid is over: a new open row under an acked group shows the group again.
assert post({"ids": ["stage:g"]})[1]["written"] == ["stage:g"]
stages.add("g2", "롤백 스크립트", parent="g")
assert "stage:g" not in acks.ackable(kinds={"stage"})
marks = work()
assert marks["g"] == ("acked", False) and marks["g2"] == (None, False) and marks["g1"] == ("ackable", False), marks
stages.finish("g2")
assert work()["g2"] == ("ackable", True)

# A held row, and a row whose PR closed unmerged (the board makes it ⏸️ PR 닫힘), are not over: refused.
board.lead_add("lead-d", "term_0FAKE4", "acme/app")
board.set_row("lead-d", title="닫힌 실험", status="done", pr="acme/app#9", next="—")
board.lead_add("lead-e", "term_0FAKE5", "acme/app")
board.set_row("lead-e", title="보류한 일", status="held", next="다음 분기")
(tmp / "dashboard" / "prs.json").write_text(json.dumps({"prs": {"acme/app#9": {"probed_at": fleet.iso(now), "detail": {
    "key": "acme/app#9", "url": "https://example.test/9", "state": "CLOSED", "merged_at": None, "draft": False}}}}))
assert "PR 닫힘" in brief()["text"]
code, out = post({"ids": ["board:lead-d", "board:lead-e"]})
assert code == 200 and out["written"] == [] and [i for i, _ in out["refused"]] == ["board:lead-d", "board:lead-e"], out

# A forged Host is refused even with the right token: a rebinding DNS name cannot reach the write.
req = urllib.request.Request(f"http://127.0.0.1:{port}/api/fleet/ack", json.dumps({"ids": ["r1"]}).encode(),
                             {"Content-Type": "application/json", "X-Dash-Token": post.__defaults__[0],
                              "Host": "evil.example.test"})
try:
    urllib.request.urlopen(req, timeout=10)
    raise AssertionError("a forged Host was let through")
except urllib.error.HTTPError as e:
    assert e.code == 403, e.code
srv.shutdown()
print("test_acks: ok")
