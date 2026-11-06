"""Offline tests for stacks.py: the config gate, /readyz and the deployed sha, how far behind main, failures.

Run: python3 test_stacks.py
"""
import json, os, pathlib, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import stacks

tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-stacks-"))
cfg = tmp / "agent-skills.json"
os.environ["AGENT_SKILLS_CONFIG"] = str(cfg)
SHA = "a" * 40
NOW = "2026-01-01T00:00:00Z"


def configure(value):
    cfg.write_text(json.dumps({"dashboard": value}))


# Off by default: no file, no key, a wrong shape.
assert stacks.configured() == [] and stacks.collect(NOW) == []
cfg.write_text("{not json")
assert stacks.configured() == []
for bad in ({}, {"stacks": "x"}, {"stacks": {"name": "a"}}, {"stacks": [1, None, "s"]}):
    configure(bad)
    assert stacks.configured() == [] and stacks.collect(NOW) == [], bad

# Entries: name, an http(s) url and an owner/repo are needed; a bad one is skipped, not fatal.
good = {"name": "stg", "url": "https://stack.example.test/", "repo": "acme/app"}
configure({"stacks": [good, {**good, "name": ""}, {**good, "url": "ftp://x.test"}, {**good, "repo": "app"},
                      {**good, "name": "two", "sha_field": "build"}, {**good, "name": "three", "sha_field": "a b"}]})
got = stacks.configured()
assert [e["name"] for e in got] == ["stg", "two", "three"], got
assert got[0]["url"] == "https://stack.example.test" and got[0]["sha_field"] is None
assert got[1]["sha_field"] == "build" and got[2]["sha_field"] is None, "a field name must be a plain word"
configure({"stacks": [{**good, "name": f"s{i}"} for i in range(20)]})
assert len(stacks.configured()) == stacks.MAX_STACKS

# deployed_sha: the field named, else the usual keys; only hex goes on.
assert stacks.deployed_sha(json.dumps({"sha": SHA})) == SHA
assert stacks.deployed_sha(json.dumps({"commit": SHA.upper()})) == SHA
assert stacks.deployed_sha(json.dumps({"build": SHA, "sha": "b" * 40}), "build") == SHA
assert stacks.deployed_sha(json.dumps({"sha": "main; rm -rf"})) is None
assert stacks.deployed_sha("ok") is None and stacks.deployed_sha("[1]") is None and stacks.deployed_sha("{}") is None


class Calls:
    def __init__(self, answer, behind=3):
        self.answer, self.n, self.asked = answer, behind, []

    def get(self, url):
        self.asked.append(url)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def behind(self, repo, sha):
        self.asked.append((repo, sha))
        if isinstance(self.n, Exception):
            raise self.n
        return self.n


entry = {"name": "stg", "url": "https://stack.example.test", "repo": "acme/app", "sha_field": None}


def run(answer, behind=3):
    c = Calls(answer, behind)
    return stacks.probe(entry, NOW, get=c.get, behind=c.behind), c


# A ready stack with a sha: how far behind main it is.
row, c = run((200, json.dumps({"sha": SHA, "ok": True})))
assert row == {"name": "stg", "url": "https://stack.example.test", "repo": "acme/app", "ready": True, "status": 200,
               "sha": SHA, "behind": 3, "error": None, "checked_at": NOW}, row
assert c.asked == ["https://stack.example.test/readyz", ("acme/app", SHA)], c.asked

# Not ready (503) is an answer: ready False, the sha still read.
row, _ = run((503, json.dumps({"sha": SHA})))
assert row["ready"] is False and row["status"] == 503 and row["sha"] == SHA and row["error"] is None

# No answer at all: the row says why and nothing else is asked.
row, c = run(OSError("Connection refused"))
assert row["ready"] is None and row["status"] is None and row["error"] == "readyz: Connection refused", row
assert len(c.asked) == 1

# No sha in the body: ready is known, behind is not, and GitHub is not asked.
row, c = run((200, "ok"))
assert row["ready"] is True and row["sha"] is None and row["behind"] is None and row["error"] is None
assert c.asked == ["https://stack.example.test/readyz"]

# GitHub fails: the readiness stays, the error says what failed.
for err in (OSError("HTTP 404: No commit found"), ValueError("bad int")):
    row, _ = run((200, json.dumps({"sha": SHA})), behind=err)
    assert row["ready"] is True and row["sha"] == SHA and row["behind"] is None and row["error"].startswith("behind: "), row

# collect() goes through every configured stack and one failing stack does not hide the others.
configure({"stacks": [good, {**good, "name": "other", "url": "https://other.example.test"}]})


def get(url):
    if url.startswith("https://stack."):
        raise OSError("down")
    return 200, json.dumps({"sha": SHA})


rows = stacks.collect(NOW, get=get, behind=lambda repo, sha: 0)
assert [r["name"] for r in rows] == ["stg", "other"], rows
assert rows[0]["error"] == "readyz: down" and rows[1]["ready"] is True and rows[1]["behind"] == 0, rows

# URLs: a plain http(s) host, optional port and path. Anything that would move /readyz or leak into state.json is out.
for ok in ("https://a.example.test", "http://a.example.test:8080/x/", "http://[::1]:4000"):
    assert stacks.clean_url(ok) == ok.rstrip("/"), ok
for bad in ("ftp://a.test", "https://", "https://u:p@a.test", "https://a.test?x=y", "https://a.test#f", "https://a.test:99999",
            "https://a.test/\nx", "https://a b.test", "https://[::1", "a.test", None, 5):
    assert stacks.clean_url(bad) is None, bad
# Repos: GitHub's owner/name, ASCII, no dot segments.
assert stacks.clean_repo("acme/app.v2") == "acme/app.v2"
for bad in ("app", "a/b/c", "../..", "acme/..", "acme/.", "-x/app", "acme/ap p", "acme/앱", None):
    assert stacks.clean_repo(bad) is None, bad

# A surprise inside one stack is that stack's row; the others still come back.
def boom(url):
    if "bad" in url:
        raise RuntimeError("secret-token-in-message")
    return 200, "{}"


rows = stacks.collect(NOW, [{**entry, "name": "bad", "url": "https://bad.example.test"}, {**entry, "name": "fine"}], get=boom)
assert rows[0]["error"] == "readyz: unreadable" and "secret" not in json.dumps(rows) and rows[1]["ready"] is True, rows
rows = stacks.collect(NOW, [entry], get=lambda u: (200, json.dumps({"sha": SHA})), behind=lambda r, s: 1 / 0)
assert rows[0]["ready"] is True and rows[0]["error"] == "behind: unreadable", rows

# gh failing quietly (no stderr) and the real http_get/gh_behind error paths keep their text out of the row.
import subprocess  # noqa: E402
real = subprocess.run
subprocess.run = lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "")
try:
    stacks.gh_behind("acme/app", SHA)
    raise SystemExit("gh_behind should fail")
except OSError as e:
    assert str(e) == "github compare failed"
finally:
    subprocess.run = real

# One slow stack does not hold the others past the deadline.
import threading  # noqa: E402
gate = threading.Event()


def slow(url):
    if "slow" in url:
        gate.wait(5)
    return 200, "{}"


rows = stacks.collect(NOW, [{**entry, "name": "slow", "url": "https://slow.example.test"}, {**entry, "name": "quick"}],
                      deadline=0.5, get=slow)
assert rows[0]["error"] == "timed out" and rows[1]["ready"] is True, rows
# While that one is still stuck it is not asked again (no threads pile up behind it), and the others go on.
slow_entry = {**entry, "name": "slow", "url": "https://slow.example.test"}
quick_entry = {**entry, "name": "quick"}
again = stacks.collect(NOW, [slow_entry, quick_entry], deadline=0.5, get=slow)
assert again[0]["error"] == "previous read still running" and again[1]["ready"] is True, again
assert sum(not f.done() for f in stacks._inflight.values()) == 1, "one stuck read, not one per collect"
gate.set()
for f in list(stacks._inflight.values()):
    f.result(timeout=5)
assert stacks.collect(NOW, [slow_entry], get=slow)[0]["ready"] is True, "and it reads again once that has ended"

# Two entries with one name and url are one stack: the first is read, so a result never belongs to the wrong repo.
twin = stacks.normalized([entry, {**entry, "repo": "acme/other"}, {**entry, "url": "https://other.example.test"}])
assert [(e["repo"], e["url"]) for e in twin] == [("acme/app", entry["url"]), ("acme/app", "https://other.example.test")], twin

# Entries handed in directly are cleaned like configured ones.
assert stacks.collect(NOW, [{}, {"name": 1}, None, {**entry, "url": "file:///etc/passwd"}, {**entry, "repo": "acme/app/../../x"}]) == []

# The real http_get against a local server: an error status still carries its body, a redirect is not followed, a refused
# connection, a silent server and one that drips chunks all end by themselves at the one clock.
import http.server, time  # noqa: E402


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/big"):  # chunked, so its size is not known up front
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b"%x\r\n" % 8192 + b"y" * 8192 + b"\r\n")
            except OSError:
                pass
            return
        if self.path.startswith("/silent"):
            time.sleep(4)
            return
        if self.path.startswith("/drip"):
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                for _ in range(40):
                    self.wfile.write(b"1\r\nx\r\n")
                    self.wfile.flush()
                    time.sleep(0.1)
            except OSError:
                pass
            return
        if self.path.startswith("/down"):
            self.send_response(503)
            body = json.dumps({"sha": SHA}).encode()
        elif self.path.startswith("/moved"):
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/elsewhere")
            body = b""
        else:
            self.send_response(200)
            body = json.dumps({"commit": SHA}).encode()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


http.server.ThreadingHTTPServer.daemon_threads = True
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"
assert stacks.http_get(base + "/readyz") == (200, json.dumps({"commit": SHA}))
status, body = stacks.http_get(base + "/down/readyz")
assert status == 503 and stacks.deployed_sha(body) == SHA, (status, body)
assert stacks.http_get(base + "/moved/readyz")[0] == 302
# A body past MAX_BODY, of a size the server did not announce, is refused without being held whole.
try:
    stacks.http_get(base + "/big/readyz")
    raise SystemExit("an oversized body should be refused")
except OSError as e:
    assert str(e) == "invalid response", str(e)
# {a,b} in a path is one literal request, not two (curl globbing off).
assert stacks.http_get(base + "/{first,second}/readyz")[0] == 200
# A .curlrc that follows redirects does not change the policy (-q): HOME points at one that does.
home = pathlib.Path(tempfile.mkdtemp(prefix="test-stacks-home-"))
(home / ".curlrc").write_text("location\n")
real_home = os.environ.get("HOME")
os.environ["HOME"] = str(home)
try:
    assert stacks.http_get(base + "/moved/readyz")[0] == 302, "the redirect is still not followed"
finally:
    os.environ["HOME"] = real_home if real_home is not None else ""
stacks.HTTP_TIMEOUT_S = 1
for path in ("/silent", "/drip"):
    t0 = time.monotonic()
    try:
        stacks.http_get(base + path)
        raise SystemExit(f"{path} should time out")
    except OSError as e:
        assert str(e) == "timed out", (path, str(e))
    assert time.monotonic() - t0 < 3, (path, time.monotonic() - t0)
stacks.HTTP_TIMEOUT_S = 5
srv.shutdown()
srv.server_close()
try:
    stacks.http_get(base + "/readyz")
    raise SystemExit("http_get should fail")
except OSError as e:
    assert str(e) == "connection refused", str(e)
print("ok")
