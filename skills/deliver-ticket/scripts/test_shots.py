"""Offline tests for shots.py, with ego-browser replaced by a stub.

Run: python3 test_shots.py
"""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).parent
SHOTS = HERE / "shots.py"
sys.path.insert(0, str(HERE))
from shots import table

tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
# The stub records the script it was given and answers with whatever reply.json holds.
stub = tmp / "ego-browser"
stub.write_text(f"""#!/usr/bin/env python3
import sys, pathlib
pathlib.Path({str(tmp / 'script.js')!r}).write_text(sys.stdin.read())
print("[ego-browser:notice] noise", file=sys.stderr)
print("SHOTS_RESULT " + pathlib.Path({str(tmp / 'reply.json')!r}).read_text(), file=sys.stderr)
""")
stub.chmod(0o755)
ledger = tmp / "ledger.jsonl"
env = {**os.environ, "EGO_BROWSER": str(stub), "AGENT_SKILLS_LEDGER": str(ledger)}


def run(*args, reply=None):
    if reply is not None:
        (tmp / "reply.json").write_text(json.dumps(reply))
    return subprocess.run([sys.executable, str(SHOTS), *args], capture_output=True, text=True, env=env)


def embedded_input():
    first = (tmp / "script.js").read_text().splitlines()[0]
    assert first.startswith("const INPUT = ") and first.endswith(";"), first
    return json.loads(first[len("const INPUT = "):-1])


# capture: inputs travel as one JSON literal, so a hostile URL stays a string.
url = 'http://127.0.0.1:5173/?q=a";process.exit(1);"'
shot = lambda w, t, sw: {"path": f"/o/card-{w}-{t}.png", "width": w, "theme": t, "innerWidth": w, "scrollWidth": sw, "overflow": sw > w}
r = run("capture", "--url", url, "--name", "card", "--out", str(tmp / "out"),
        reply={"shots": [shot(1440, "light", 1440), shot(390, "dark", 390)]})
assert r.returncode == 0, r.stderr
assert "scrollWidth/innerWidth=390/390 ok" in r.stdout, r.stdout
inp = embedded_input()
assert inp["url"] == url and inp["widths"] == [1440, 390] and inp["themes"] == ["light", "dark"], inp
assert inp["fullPage"] is True and inp["out"] == str((tmp / "out").resolve()), inp

r = run("capture", "--url", url, "--name", "card", "--out", str(tmp), "--widths", "390", "--themes", "dark",
        reply={"shots": [shot(390, "dark", 608)]})
assert r.returncode == 1 and "608/390 OVERFLOW" in r.stdout, (r.returncode, r.stdout)

for bad in (["--name", "../x"], ["--name", "card.final"], ["--name", "ok", "--themes", "sepia"]):
    r = run("capture", "--url", url, "--out", str(tmp), *(["--name", "ok"] if bad[0] != "--name" else []), *bad)
    assert r.returncode != 0 and r.stderr, (bad, r.stderr)

# upload: files are checked before the browser opens.
shots_dir = tmp / "shots"
shots_dir.mkdir()
img = shots_dir / "card-1440-light.png"
img.write_bytes(b"\x89PNG\r\n\x1a\n")
other = shots_dir / "card-390-dark.png"
other.write_bytes(b"\x89PNG\r\n\x1a\n")
(tmp / "script.js").unlink()
for files, why in (([str(tmp / "nope.png")], "not a file"), ([str(SHOTS)], "not an image")):
    r = run("upload", "--repo", "acme/app", "--pr", "1", "--out", str(tmp / "u.json"), *files)
    assert r.returncode != 0 and why in r.stderr, (why, r.stderr)
big = tmp / "big.png"
with open(big, "wb") as f:
    f.truncate(10 * 1024 * 1024 + 1)
r = run("upload", "--repo", "acme/app", "--pr", "1", "--out", str(tmp / "u.json"), str(big))
assert r.returncode != 0 and "10 MB" in r.stderr, r.stderr
dotted = tmp / "card.final.png"
dotted.write_bytes(b"\x89PNG\r\n\x1a\n")
r = run("upload", "--repo", "acme/app", "--pr", "1", "--out", str(tmp / "u.json"), str(dotted))
assert r.returncode != 0 and "plain file stem" in r.stderr, r.stderr
r = run("upload", "--repo", "acme/app;rm", "--pr", "1", "--out", str(tmp / "u.json"), str(img))
assert r.returncode != 0 and "owner/repo" in r.stderr, r.stderr
assert not (tmp / "script.js").exists(), "browser ran despite a rejected input"

U = "https://github.com/user-attachments/assets/"
r = run("upload", "--repo", "acme/app", "--pr", "1", "--out", str(tmp / "u.json"), str(img), str(other),
        reply={"urls": {"card-1440-light": U + "a", "card-390-dark": U + "b"}, "failed": 0, "cleared": True})
assert r.returncode == 0 and "uploaded 2 of 2" in r.stdout, (r.stdout, r.stderr)
assert json.loads((tmp / "u.json").read_text()) == {"card-1440-light": U + "a", "card-390-dark": U + "b"}
inp = embedded_input()
assert inp["prUrl"] == "https://github.com/acme/app/pull/1" and inp["files"] == [str(img), str(other)], inp

r = run("upload", "--repo", "acme/app", "--pr", "1", "--out", str(tmp / "u.json"), str(img), str(other),
        reply={"urls": {"card-1440-light": U + "a"}, "failed": 1, "cleared": True})
assert r.returncode == 1 and "missing: card-390-dark" in r.stderr, r.stderr
assert json.loads((tmp / "u.json").read_text())["card-390-dark"] is None
r = run("upload", "--repo", "acme/app", "--pr", "1", "--out", str(tmp / "u.json"), str(img),
        reply={"urls": {"card-1440-light": U + "a"}, "failed": 0, "cleared": False})
assert r.returncode == 1 and "still holds text" in r.stderr, r.stderr

# table: one width x theme grid per subject, desktop above mobile, retake tags folded in.
md = table({
    "settings-390-light": U + "1", "settings-1440-dark-final": U + "2",
    "settings-1440-light": U + "3", "settings-390-dark": U + "4",
    "empty-state-1280-light": U + "5", "search-results": U + "6",
})
assert md == f"""### 스크린샷

**settings**

| | light | dark |
| --- | --- | --- |
| 1440 | <img width="360" alt="settings-1440-light" src="{U}3"> | <img width="360" alt="settings-1440-dark-final" src="{U}2"> |
| 390 | <img width="180" alt="settings-390-light" src="{U}1"> | <img width="180" alt="settings-390-dark" src="{U}4"> |

**empty-state**

| | light |
| --- | --- |
| 1280 | <img width="360" alt="empty-state-1280-light" src="{U}5"> |

**search-results**

<img width="600" alt="search-results" src="{U}6">
""", md
# A tagged retake wins its cell in either order; two retakes for one cell stop the table.
for order in (["card-390-dark", "card-390-dark-final"], ["card-390-dark-final", "card-390-dark"]):
    md = table({n: U + n for n in order})
    assert f'src="{U}card-390-dark-final"' in md and f'src="{U}card-390-dark"' not in md, md
# A subject that itself ends in -<width>-<theme> keeps it; the last pair is the shot's.
md = table({f"card-390-dark-{w}-{t}": U + f"{w}{t}" for w in (1440, 390) for t in ("light", "dark")})
assert "**card-390-dark**" in md and md.count("<img") == 4, md
assert table({"settings-390-dark-final": U + "f"}).count("**settings**") == 1
try:
    table({"card-390-dark-a": U + "a", "card-390-dark-b": U + "b"})
    raise AssertionError("two retakes must stop the table")
except SystemExit as e:
    assert "two retakes" in str(e)
try:
    table({"x-390-light": None})
    raise AssertionError("a missing URL must stop the table")
except SystemExit as e:
    assert "upload it first" in str(e)

# The browser error path prints only known diagnostics, never the raw output.
stub.write_text("""#!/bin/sh
echo 'Error: page.goto failed: net::ERR_ABORTED at https://u:SECRET1@example.test/cb?code=SECRET2' >&2
echo 'Authorization: Bearer SECRET3 {"token":"SECRET4"} Cookie: a=1; b=SECRET5' >&2
exit 1
""")
r = run("capture", "--url", url, "--name", "card", "--out", str(tmp))
assert r.returncode != 0 and "SECRET" not in r.stderr, r.stderr
assert "page.goto failed, net::ERR_ABORTED" in r.stderr, r.stderr

# Run the generated JavaScript itself against a fake page and DOM, so the page calls are checked too.
fake = tmp / "ego-node.mjs"
fake.write_text(r"""#!/usr/bin/env node
import fs from "node:fs";
const log = (x) => fs.appendFileSync(process.env.FAKE_LOG, JSON.stringify(x) + "\n");
const box = { value: process.env.FAKE_DRAFT || "" };
globalThis.window = { innerWidth: 0 };
globalThis.document = {
  documentElement: { scrollWidth: 390, scrollHeight: 2400 },
  // FAKE_UI picks the comment UI: "old" (hidden file input), "new" (button + file chooser) or "none".
  querySelector: (sel) => {
    const ui = process.env.FAKE_UI || "old";
    if (ui === "old" && (sel === "#fc-new_comment_field" || sel === "#new_comment_field")) return box;
    if (ui === "new" && sel === 'textarea[aria-label="Markdown value"]') return box;
    return null;
  },
  querySelectorAll: (sel) => (sel === "button" && process.env.FAKE_UI === "new" ? [{ textContent: "Paste, drop, or click to add files" }] : []),
};
const page = {
  goto: async (u) => log(["goto", u]),
  waitForSelector: async () => {},
  waitForTimeout: async () => {},
  evaluate: async (fn, arg) => fn(arg),
  fill: async (s, v) => { log(["fill", v]); box.value = v; },
  setInputFiles: async (s, files) => {
    log(["setInputFiles", files.length]);
    box.value = files.map((f) => { const n = f.split("/").pop().replace(/\.png$/, ""); return `<img width="1" alt="${n}" src="https://github.com/user-attachments/assets/${n}">`; }).join("\n");
  },
  click: async (s) => log(["click", s]),
  waitForFileChooser: async () => ({
    // The new UI writes alt "Image" for every file, whatever it is called.
    // FAKE_SLOW: the first upload has not finished when the poll ends and lands with the next one.
    setFiles: async (f) => { log(["setFiles", f]); if (process.env.FAKE_SLOW && !box.late) { box.late = true; return; } box.value += `![Image](https://github.com/user-attachments/assets/${f.split("/").pop().replace(/\.png$/, "")})\n`; },
  }),
  cdp: async (m, p) => {
    log(["cdp", m, p]);
    if (m === "Emulation.setDeviceMetricsOverride") window.innerWidth = p.width;
    return m === "Page.captureScreenshot" ? { data: Buffer.from("png").toString("base64") } : {};
  },
  screenshot: async ({ path }) => fs.writeFileSync(path, "png"),
};
globalThis.taskSpace = async () => ({ page: () => page, finish: async () => log(["finish"]) });
console.log = (...a) => process.stderr.write(a.join(" ") + "\n");
const script = fs.readFileSync(0, "utf8");
await new (async () => {}).constructor(script)();
""")
fake.chmod(0o755)
env["EGO_BROWSER"] = str(fake)
flog = tmp / "fake.log"
env["FAKE_LOG"] = str(flog)


def calls():
    out = [json.loads(l) for l in flog.read_text().splitlines()] if flog.exists() else []
    flog.unlink(missing_ok=True)
    return out


r = run("capture", "--url", "http://127.0.0.1:1/", "--name", "card", "--out", str(tmp / "js"), "--widths", "390", "--themes", "dark")
assert r.returncode == 0 and "390/390 ok" in r.stdout, (r.stdout, r.stderr)
c = calls()
assert all(p["height"] == 900 for k, m, p in (x for x in c if x[0] == "cdp") if m == "Emulation.setDeviceMetricsOverride"), c
shot = [x[2] for x in c if x[:2] == ["cdp", "Page.captureScreenshot"]]
assert shot and shot[0]["captureBeyondViewport"] and shot[0]["clip"]["height"] == 2400, shot
assert (tmp / "js" / "card-390-dark.png").read_bytes() == b"png"

r = run("upload", "--repo", "acme/app", "--pr", "1", "--out", str(tmp / "u.json"), str(img), str(other))
assert r.returncode == 0 and "uploaded 2 of 2" in r.stdout and "draft cleared True" in r.stdout, (r.stdout, r.stderr)
assert ["setInputFiles", 2] in calls()

# New comment UI: no file input, a button opens a file chooser, one file per chooser, keyed by stem.
env["FAKE_UI"] = "new"
r = run("upload", "--repo", "acme/app", "--pr", "1", "--out", str(tmp / "u.json"), str(img), str(other))
assert r.returncode == 0 and "uploaded 2 of 2" in r.stdout and "draft cleared True" in r.stdout, (r.stdout, r.stderr)
assert json.loads((tmp / "u.json").read_text()) == {"card-1440-light": U + "card-1440-light", "card-390-dark": U + "card-390-dark"}
c = calls()
assert [x[0] for x in c if x[0] in ("click", "setFiles", "setInputFiles")] == ["click", "setFiles", "click", "setFiles"], c
assert ["fill", ""] in c
env["FAKE_SLOW"] = "1"
r = run("upload", "--repo", "acme/app", "--pr", "1", "--out", str(tmp / "u.json"), "--polls", "1", str(img), str(other))
c = calls()
assert [x[0] for x in c if x[0] in ("click", "setFiles")] == ["click", "setFiles"], c
assert r.returncode == 1 and "missing: card-1440-light" in r.stderr and "missing: card-390-dark" in r.stderr, (r.stdout, r.stderr)
del env["FAKE_SLOW"]
env["FAKE_UI"] = "none"
r = run("upload", "--repo", "acme/app", "--pr", "1", "--out", str(tmp / "u.json"), str(img))
assert r.returncode != 0 and "no comment box" in r.stderr, r.stderr
assert not any(x[0] in ("fill", "click") for x in calls())
env["FAKE_UI"] = "new"
env["FAKE_DRAFT"] = "half-written review"
r = run("upload", "--repo", "acme/app", "--pr", "1", "--out", str(tmp / "u.json"), str(img))
assert r.returncode != 0 and "unsent draft" in r.stderr, r.stderr
assert not any(x[0] in ("fill", "click") for x in calls())
del env["FAKE_UI"]
env["FAKE_DRAFT"] = "half-written review"
r = run("upload", "--repo", "acme/app", "--pr", "1", "--out", str(tmp / "u.json"), str(img))
assert r.returncode != 0 and "unsent draft" in r.stderr, r.stderr
c = calls()
assert not any(x[0] in ("fill", "setInputFiles") for x in c), c
del env["FAKE_DRAFT"]

# upload registers its evidence directory in the reap ledger.
rows = [json.loads(l) for l in ledger.read_text().splitlines()]
assert rows and all(r["kind"] == "evidence" and r["links"]["pr"] == "acme/app#1" and r["id"] == str(shots_dir) for r in rows), rows

# The janitor only removes a directory of images, so urls.json may not sit inside it.
calls()
for out in (shots_dir / "u.json", shots_dir / "sub" / "u.json"):
    r = run("upload", "--repo", "acme/app", "--pr", "1", "--out", str(out), str(img))
    assert r.returncode != 0 and "keep urls.json outside" in r.stderr, (out, r.stderr)
assert calls() == [], "browser ran with urls.json inside the image directory"

# upload refuses evidence inside a git worktree, before the browser opens.
wt = tmp / "wt"
wt.mkdir()
subprocess.run(["git", "init", "-q", str(wt)], check=True)
inside = wt / "card-390-dark.png"
inside.write_bytes(b"\x89PNG\r\n\x1a\n")
calls()
r = run("upload", "--repo", "acme/app", "--pr", "1", "--out", str(tmp / "u.json"), str(inside))
assert r.returncode != 0 and "inside a git worktree" in r.stderr, r.stderr
r = run("upload", "--repo", "acme/app", "--pr", "1", "--out", str(wt / "sub" / "u.json"), str(img))
assert r.returncode != 0 and "inside a git worktree" in r.stderr, r.stderr
assert calls() == [], "browser ran for evidence inside a worktree"
# GIT_DIR must not redirect the check to another repository and let the file through.
env["GIT_DIR"] = str(tmp / "no-such.git")
r = run("upload", "--repo", "acme/app", "--pr", "1", "--out", str(tmp / "u.json"), str(inside))
assert r.returncode != 0 and "inside a git worktree" in r.stderr, r.stderr
del env["GIT_DIR"]
assert calls() == []

# check: HEAD each URL, require an image type and a PR comment that references it.
import http.server, threading


class Handler(http.server.BaseHTTPRequestHandler):
    def reply(self):
        status, ctype = {"/ok": (200, "image/png"), "/page": (200, "text/html"),
                         "/forbidden": (403, "text/plain")}.get(self.path, (404, "text/plain"))
        if self.path == "/redirect":
            # GitHub redirects to a URL pre-signed for GET only: S3 answers 403 to a HEAD.
            self.send_response(302)
            self.send_header("Location", "/signed")
            self.end_headers()
            return False
        if self.path == "/signed":
            status, ctype = (200, "image/png") if self.command == "GET" else (403, "application/xml")
        if status == 200 and self.headers.get("Range"):
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        return True

    def do_HEAD(self):
        self.reply()

    def do_GET(self):
        if self.reply():
            self.wfile.write(b"x")

    def log_message(self, *args):
        pass


server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}"
bindir = tmp / "bin"
bindir.mkdir()
gh = bindir / "gh"
gh.write_text("""#!/bin/sh
case "$*" in
  *issues/*/comments*) [ "$FAKE_GH_COMMENTS" = ERR ] && { echo "gh: HTTP 404" >&2; exit 1; }; cat "$FAKE_GH_BODIES" ;;
  *"--jq .private"*) [ "$FAKE_GH_PRIVATE" = ERR ] && { echo "gh: HTTP 403" >&2; exit 1; }; echo "$FAKE_GH_PRIVATE" ;;
  *) echo "unexpected: $*" >&2; exit 1 ;;
esac
""")
gh.chmod(0o755)
env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
env["FAKE_GH_BODIES"] = str(tmp / "bodies.txt")


def check_run(urls, posted, private):
    (tmp / "c.json").write_text(json.dumps(urls))
    (tmp / "bodies.txt").write_text("\n".join(posted) + "\n")
    env["FAKE_GH_PRIVATE"] = "true" if private else "false"
    return run("check", str(tmp / "c.json"), "--repo", "acme/app", "--pr", "1")


good, html, gone = base + "/ok", base + "/page", base + "/gone"
r = check_run({"a-390-dark": good}, [f'<img src="{good}">'], private=False)
assert r.returncode == 0 and "VERIFIED  a-390-dark: image/png" in r.stdout, (r.stdout, r.stderr)
r = check_run({"a": html}, [html], private=False)
assert r.returncode == 1 and "NOT VERIFIED  a: HTTP 206 text/html" in r.stdout, r.stdout
# A URL that is only the start of a longer posted one is not posted.
r = check_run({"a": good}, [f'<img src="{good}def">'], private=False)
assert r.returncode == 1 and "no PR comment references it" in r.stdout, r.stdout
r = check_run({"a": good}, [f'<img src="{good}.png">'], private=False)
assert r.returncode == 1 and "no PR comment references it" in r.stdout, r.stdout
r = check_run({"a": good}, [f"![a](https://example.test/redirect?next={good})"], private=False)
assert r.returncode == 1 and "no PR comment references it" in r.stdout, r.stdout
for posted in (f"![a]({good}) and more", f"see {good}.", f"{good}, and {good}", f"<{good}>", f"x\n{good}"):
    r = check_run({"a": good}, [posted], private=False)
    assert r.returncode == 0, (posted, r.stdout)
r = check_run({"a": good}, ["no images here"], private=False)
assert r.returncode == 1 and "no PR comment references it" in r.stdout, r.stdout
r = check_run({"a": gone}, [gone], private=False)
assert r.returncode == 1 and "NOT VERIFIED  a: HTTP 404" in r.stdout, r.stdout
r = check_run({"a": None}, [], private=False)
assert r.returncode == 1 and "upload it first" in r.stdout, r.stdout
# A private repo's images answer 404 without a session: inconclusive, not a failure, unless another one failed.
r = check_run({"a": gone}, [gone], private=True)
assert r.returncode == 2 and r.stdout.startswith("INCONCLUSIVE  a: private repo"), (r.returncode, r.stdout)
r = check_run({"a": gone, "b": good}, [gone], private=True)
assert r.returncode == 1 and "INCONCLUSIVE  a" in r.stdout and "NOT VERIFIED  b" in r.stdout, r.stdout
redirect = base + "/redirect"
r = check_run({"a": redirect}, [redirect], private=False)
assert r.returncode == 0 and "VERIFIED  a: image/png" in r.stdout, (r.returncode, r.stdout)
forbidden = base + "/forbidden"
r = check_run({"a": forbidden}, [forbidden], private=True)
assert r.returncode == 2 and r.stdout.startswith("INCONCLUSIVE  a: private repo: HTTP 403"), (r.returncode, r.stdout)
# The same 403 on a public repo is a real failure.
r = check_run({"a": forbidden}, [forbidden], private=False)
assert r.returncode == 1 and "NOT VERIFIED  a: HTTP 403" in r.stdout, r.stdout
# When gh cannot read the repo's visibility (a token without access answers 403/404), a 401/403 image
# is still INCONCLUSIVE rather than a verdict on the image.
env["FAKE_GH_PRIVATE"] = "ERR"
(tmp / "c.json").write_text(json.dumps({"a": forbidden}))
(tmp / "bodies.txt").write_text(forbidden + "\n")
r = run("check", str(tmp / "c.json"), "--repo", "acme/app", "--pr", "1")
assert r.returncode == 2 and "INCONCLUSIVE  a: visibility unknown: HTTP 403" in r.stdout, (r.returncode, r.stdout, r.stderr)
# A token that cannot read a private repo's comments gets 404 there too: inconclusive, not a crash.
env["FAKE_GH_COMMENTS"] = "ERR"
r = check_run({"a": good, "b": None}, [], private=True)
assert r.returncode == 1 and "INCONCLUSIVE  a: cannot read the PR comments" in r.stdout and "NOT VERIFIED  b" in r.stdout, (r.returncode, r.stdout, r.stderr)
r = check_run({"a": good}, [], private=True)
assert r.returncode == 2 and "INCONCLUSIVE  a: cannot read" in r.stdout, (r.returncode, r.stdout, r.stderr)
del env["FAKE_GH_COMMENTS"]
server.shutdown()

print("test_shots: ok")
