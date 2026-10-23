#!/usr/bin/env python3
"""Capture screenshots, upload them as GitHub user-attachments, and render the sticky-comment section.

  shots.py capture --url URL --name SUBJECT --out DIR [--widths 1440,390] [--themes light,dark]
  shots.py upload --repo OWNER/REPO --pr N --out urls.json FILE...
  shots.py table urls.json

Both browser steps run through `ego-browser nodejs`, which uses the browser's own signed-in
session: no token or cookie goes through argv, the generated script or this script's output.
The browser runtime does not see the shell environment, so inputs are embedded in the generated
script as one JSON literal. EGO_BROWSER overrides the binary (tests use a stub).
"""
import argparse, json, os, pathlib, re, subprocess, sys

RESULT_TAG = "SHOTS_RESULT "
NAME_RE = re.compile(r"^(?P<subject>.+?)-(?P<width>\d{3,4})-(?P<theme>light|dark)(?:-.+)?$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# GitHub rejects attached images above 10 MB.
MAX_BYTES = 10 * 1024 * 1024
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

CAPTURE_JS = r"""
const fs = await import("node:fs/promises");
await fs.mkdir(INPUT.out, { recursive: true });
const task = await taskSpace(INPUT.space);
const page = task.page("p1");
const shots = [];
for (const width of INPUT.widths) {
  for (const theme of INPUT.themes) {
    await page.cdp("Emulation.setDeviceMetricsOverride", { width, height: INPUT.height, deviceScaleFactor: 1, mobile: false });
    await page.cdp("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: theme }] });
    // Navigate after emulating: an app may read the theme and width only once, at load.
    await page.goto(INPUT.url);
    if (INPUT.waitFor) await page.waitForSelector(INPUT.waitFor, { timeout: 30000 });
    await page.waitForTimeout(INPUT.settleMs);
    const m = await page.evaluate(() => ({
      innerWidth: window.innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      scrollHeight: document.documentElement.scrollHeight,
    }));
    if (INPUT.fullPage && m.scrollHeight > INPUT.height) {
      await page.cdp("Emulation.setDeviceMetricsOverride", { width, height: Math.min(m.scrollHeight, INPUT.maxHeight), deviceScaleFactor: 1, mobile: false });
      await page.waitForTimeout(300);
    }
    const path = `${INPUT.out}/${INPUT.name}-${width}-${theme}.png`;
    await page.screenshot({ path });
    shots.push({ path, width, theme, innerWidth: m.innerWidth, scrollWidth: m.scrollWidth, overflow: m.scrollWidth > width });
  }
}
await task.finish({ keep: [] });
console.log("SHOTS_RESULT " + JSON.stringify({ shots }));
"""

UPLOAD_JS = r"""
const task = await taskSpace(INPUT.space);
const page = task.page("p1");
await page.goto(INPUT.prUrl);
await page.waitForSelector("#fc-new_comment_field", { state: "attached", timeout: 30000 });
await page.fill("#new_comment_field", "");
let value = "", failed = 0;
try {
  // Attaching to the new-comment box uploads each file and writes its markdown into the box.
  // The comment itself is never submitted; only the asset URLs are kept.
  await page.setInputFiles("#fc-new_comment_field", INPUT.files);
  for (let i = 0; i < INPUT.polls; i++) {
    await page.waitForTimeout(2000);
    ({ value, failed } = await page.evaluate(() => ({
      value: document.querySelector("#new_comment_field")?.value ?? "",
      failed: document.querySelectorAll(".js-upload-markdown-image.is-failed").length,
    })));
    const done = (value.match(/user-attachments\/assets\//g) || []).length;
    if (failed > 0 || (done >= INPUT.files.length && !/Uploading/.test(value))) break;
  }
} finally {
  await page.fill("#new_comment_field", "");
}
const urls = {};
for (const m of value.matchAll(/<img[^>]*alt="([^"]+)"[^>]*src="(https:\/\/github\.com\/user-attachments\/assets\/[^"]+)"/g)) urls[m[1].replace(/\.[a-z]+$/i, "")] = m[2];
for (const m of value.matchAll(/!\[([^\]]*)\]\((https:\/\/github\.com\/user-attachments\/assets\/[^)\s]+)\)/g)) urls[m[1].replace(/\.[a-z]+$/i, "")] = m[2];
const cleared = (await page.evaluate(() => document.querySelector("#new_comment_field")?.value ?? "")) === "";
await task.finish({ keep: [] });
console.log("SHOTS_RESULT " + JSON.stringify({ urls, failed, cleared }));
"""


def run_browser(js, inp):
    script = "const INPUT = " + json.dumps(inp) + ";\n" + js
    proc = subprocess.run([os.environ.get("EGO_BROWSER", "ego-browser"), "nodejs"],
                          input=script, capture_output=True, text=True)
    # ego-browser prints the script's console output on stderr.
    for line in (proc.stdout + "\n" + proc.stderr).splitlines():
        if line.startswith(RESULT_TAG):
            return json.loads(line[len(RESULT_TAG):])
    sys.exit(f"ego-browser gave no result (exit {proc.returncode}):\n{proc.stdout[-2000:]}{proc.stderr[-2000:]}")


def ints(s):
    return [int(x) for x in s.split(",") if x]


def capture(a):
    if not SAFE_NAME.match(a.name):
        sys.exit(f"--name must be a plain file stem: {a.name!r}")
    themes = a.themes.split(",")
    if not set(themes) <= {"light", "dark"}:
        sys.exit(f"--themes takes light and/or dark: {a.themes!r}")
    out = str(pathlib.Path(a.out).resolve())
    res = run_browser(CAPTURE_JS, {
        "space": f"visual-evidence {a.name}", "url": a.url, "name": a.name, "out": out,
        "widths": ints(a.widths), "themes": themes, "height": a.height, "maxHeight": a.max_height,
        "fullPage": not a.viewport_only, "waitFor": a.wait_for, "settleMs": a.settle_ms,
    })
    for s in res["shots"]:
        flag = "OVERFLOW" if s["overflow"] else "ok"
        print(f"{s['path']}  scrollWidth/innerWidth={s['scrollWidth']}/{s['innerWidth']} {flag}")
    return 1 if any(s["overflow"] for s in res["shots"]) else 0


def upload(a):
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", a.repo):
        sys.exit(f"--repo must be owner/repo: {a.repo!r}")
    files = [pathlib.Path(f).resolve() for f in a.files]
    for f in files:
        if not f.is_file():
            sys.exit(f"not a file: {f}")
        if f.suffix.lower() not in IMAGE_SUFFIXES:
            sys.exit(f"not an image GitHub accepts: {f}")
        if f.stat().st_size > MAX_BYTES:
            sys.exit(f"over 10 MB, GitHub rejects it: {f}")
        if not SAFE_NAME.match(f.stem):
            sys.exit(f"rename to a plain file stem first: {f.name!r}")
    stems = [f.stem for f in files]
    if len(set(stems)) != len(stems):
        sys.exit("two files share a name; the URLs are keyed by file name")
    res = run_browser(UPLOAD_JS, {
        "space": f"visual-evidence upload {a.repo} {a.pr}",
        "prUrl": f"https://github.com/{a.repo}/pull/{a.pr}",
        "files": [str(f) for f in files], "polls": a.polls,
    })
    urls = {s: res["urls"].get(s) for s in stems}
    pathlib.Path(a.out).write_text(json.dumps(urls, indent=2) + "\n")
    missing = [s for s, u in urls.items() if not u]
    print(f"uploaded {len(stems) - len(missing)} of {len(stems)}, failed {res['failed']}, draft cleared {res['cleared']}")
    for s in missing:
        print(f"missing: {s}", file=sys.stderr)
    if not res["cleared"]:
        print("the comment box still holds text: clear it in the browser before leaving the page", file=sys.stderr)
    return 1 if missing or res["failed"] or not res["cleared"] else 0


def img(name, url, width):
    return f'<img width="{width}" alt="{name}" src="{url}">'


def table(urls):
    """Group `<subject>-<width>-<light|dark>[-tag]` names into one width x theme table per subject."""
    groups, singles = {}, []
    for name, url in urls.items():
        if not url:
            raise SystemExit(f"no URL for {name}: upload it first")
        m = NAME_RE.match(name)
        if m:
            groups.setdefault(m["subject"], {})[(int(m["width"]), m["theme"])] = (name, url)
        else:
            singles.append((name, url))
    out = ["### 스크린샷", ""]
    for subject, shots in groups.items():
        themes = [t for t in ("light", "dark") if any(k[1] == t for k in shots)]
        out += [f"**{subject}**", "", "| | " + " | ".join(themes) + " |", "| --- |" + " --- |" * len(themes)]
        for w in sorted({k[0] for k in shots}, reverse=True):
            size = 360 if w >= 768 else 180
            cells = [img(*shots[(w, t)], size) if (w, t) in shots else "—" for t in themes]
            out.append(f"| {w} | " + " | ".join(cells) + " |")
        out.append("")
    for name, url in singles:
        out += [f"**{name}**", "", img(name, url, 600), ""]
    return "\n".join(out).rstrip() + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture")
    c.add_argument("--url", required=True)
    c.add_argument("--name", required=True, help="file stem; shots land as <name>-<width>-<theme>.png")
    c.add_argument("--out", required=True)
    c.add_argument("--widths", default="1440,390")
    c.add_argument("--themes", default="light,dark")
    c.add_argument("--height", type=int, default=900)
    c.add_argument("--max-height", type=int, default=8000)
    c.add_argument("--viewport-only", action="store_true")
    c.add_argument("--wait-for", help="CSS selector that must appear before the shot")
    c.add_argument("--settle-ms", type=int, default=500)
    u = sub.add_parser("upload")
    u.add_argument("--repo", required=True)
    u.add_argument("--pr", type=int, required=True)
    u.add_argument("--out", required=True, help="JSON file of name -> user-attachments URL")
    u.add_argument("--polls", type=int, default=60, help="2-second polls to wait for the uploads")
    u.add_argument("files", nargs="+")
    t = sub.add_parser("table")
    t.add_argument("urls")
    a = p.parse_args(argv)
    if a.cmd == "capture":
        return capture(a)
    if a.cmd == "upload":
        return upload(a)
    sys.stdout.write(table(json.loads(pathlib.Path(a.urls).read_text())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
