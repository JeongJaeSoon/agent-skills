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
env = {**os.environ, "EGO_BROWSER": str(stub)}


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

for bad in (["--name", "../x"], ["--name", "ok", "--themes", "sepia"]):
    r = run("capture", "--url", url, "--out", str(tmp), *(["--name", "ok"] if bad[0] != "--name" else []), *bad)
    assert r.returncode != 0 and r.stderr, (bad, r.stderr)

# upload: files are checked before the browser opens.
img = tmp / "card-1440-light.png"
img.write_bytes(b"\x89PNG\r\n\x1a\n")
other = tmp / "card-390-dark.png"
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
try:
    table({"x-390-light": None})
    raise AssertionError("a missing URL must stop the table")
except SystemExit as e:
    assert "upload it first" in str(e)

# The browser error path prints no query string or credential-looking value.
stub.write_text("#!/bin/sh\necho 'Error: goto https://example.test/cb?code=SECRET1 failed, token=SECRET2' >&2\nexit 1\n")
r = run("capture", "--url", url, "--name", "card", "--out", str(tmp))
assert r.returncode != 0 and "SECRET" not in r.stderr and "<redacted>" in r.stderr, r.stderr

# Run the generated JavaScript itself against a fake page and DOM, so the page calls are checked too.
fake = tmp / "ego-node.mjs"
fake.write_text(r"""#!/usr/bin/env node
import fs from "node:fs";
const log = (x) => fs.appendFileSync(process.env.FAKE_LOG, JSON.stringify(x) + "\n");
const box = { value: process.env.FAKE_DRAFT || "" };
globalThis.window = { innerWidth: 0 };
globalThis.document = {
  documentElement: { scrollWidth: 390, scrollHeight: 2400 },
  querySelector: () => box,
  querySelectorAll: () => [],
};
const page = {
  goto: async (u) => log(["goto", u]),
  waitForSelector: async () => {},
  waitForTimeout: async () => {},
  evaluate: async (fn) => fn(),
  fill: async (s, v) => { log(["fill", v]); box.value = v; },
  setInputFiles: async (s, files) => {
    log(["setInputFiles", files.length]);
    box.value = files.map((f) => { const n = f.split("/").pop().replace(/\.png$/, ""); return `<img width="1" alt="${n}" src="https://github.com/user-attachments/assets/${n}">`; }).join("\n");
  },
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

env["FAKE_DRAFT"] = "half-written review"
r = run("upload", "--repo", "acme/app", "--pr", "1", "--out", str(tmp / "u.json"), str(img))
assert r.returncode != 0 and "unsent draft" in r.stderr, r.stderr
c = calls()
assert not any(x[0] in ("fill", "setInputFiles") for x in c), c

print("test_shots: ok")
