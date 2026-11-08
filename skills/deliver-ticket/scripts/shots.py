#!/usr/bin/env python3
"""Capture screenshots, upload them as GitHub user-attachments, and render the sticky-comment section.

  shots.py capture --url URL --name SUBJECT --out DIR [--widths 1440,390] [--themes light,dark]
  shots.py upload --repo OWNER/REPO --pr N --out urls.json FILE...
  shots.py check urls.json --repo OWNER/REPO --pr N
  shots.py table urls.json

`upload` refuses files or an --out inside a git worktree, or an --out inside the image directory
(the janitor removes only directories of images), and registers the image directories in the reap
ledger once the URLs are saved. `check` runs after the sticky comment is posted: an uploaded
URL answers 404 until a comment references it.

Both browser steps run through `ego-browser nodejs`, which uses the browser's own signed-in
session: no token or cookie goes through argv, the generated script or this script's output.
The browser runtime does not see the shell environment, so inputs are embedded in the generated
script as one JSON literal. EGO_BROWSER overrides the binary (tests use a stub).
"""
import argparse, json, os, pathlib, re, subprocess, sys, urllib.error, urllib.request

REAP = pathlib.Path(__file__).resolve().parents[2] / "reap-resources" / "scripts" / "reap.py"
RESULT_TAG = "SHOTS_RESULT "
# Greedy subject: the last -<width>-<theme> is the shot's own, so a subject may contain one too.
NAME_RE = re.compile(r"^(?P<subject>.+)-(?P<width>\d{3,4})-(?P<theme>light|dark)(?:-(?P<tag>[A-Za-z0-9_-]+))?$")
# No dots: GitHub's alt text keeps everything before the extension, and URLs are keyed by it.
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
# Browser output can carry URLs with tokens, so a failure reports only these known diagnostics.
DIAGNOSTIC = re.compile(r"(?:page|task)\.\w+ failed|net::ERR_[A-Z_]+|EGO_[A-Z_]+|Timeout \d+ms exceeded|\b(?:Syntax|Type|Reference)Error\b")
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
    const path = `${INPUT.out}/${INPUT.name}-${width}-${theme}.png`;
    if (INPUT.fullPage) {
      // Capture beyond the viewport rather than resizing it, so 100vh areas, height media
      // queries and fixed bars keep the layout they have at the real viewport height.
      const height = Math.min(Math.max(m.scrollHeight, INPUT.height), INPUT.maxHeight);
      const shot = await page.cdp("Page.captureScreenshot", { format: "png", captureBeyondViewport: true, clip: { x: 0, y: 0, width, height, scale: 1 } });
      await fs.writeFile(path, Buffer.from(shot.data, "base64"));
    } else {
      await page.screenshot({ path });
    }
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
// Two comment UIs exist. The old one has a hidden file input (#fc-new_comment_field); the new one has
// only a "click to add files" button that opens a file chooser, and a textarea with a generated id.
const OLD = { box: "#new_comment_field", input: "#fc-new_comment_field" };
const NEW = { box: 'textarea[aria-label="Markdown value"]', button: 'button:has-text("click to add files")' };
const present = (sel) => page.evaluate((s) => !!document.querySelector(s), sel);
let ui = null;
for (let i = 0; i < 30 && !ui; i++) {
  if (await present(OLD.input)) ui = "old";
  else if ((await present(NEW.box)) && (await page.evaluate(() => [...document.querySelectorAll("button")].some((b) => /click to add files/.test(b.textContent))))) ui = "new";
  if (!ui) await page.waitForTimeout(1000);
}
const box = ui === "old" ? OLD.box : NEW.box;
const read = () => page.evaluate((s) => document.querySelector(s)?.value ?? "", box);
// The new UI writes ![Image](url) whatever the file is called, so shots are keyed by upload order, not alt.
const assetUrls = (text) => [...text.matchAll(/https:\/\/github\.com\/user-attachments\/assets\/[^"')\s>]+/g)].map((m) => m[0]);
const draft = ui ? await read() : "";
let value = "", failed = 0;
const urls = {};
if (!ui || draft !== "") {
  await task.finish({ keep: [] });
  console.log("SHOTS_RESULT " + JSON.stringify(ui ? { draft: true } : { noBox: true }));
} else {
  try {
    // Attaching to the new-comment box uploads each file and writes its markdown into the box.
    // The comment itself is never submitted; only the asset URLs are kept.
    if (ui === "old") {
      await page.setInputFiles(OLD.input, INPUT.files);
      for (let i = 0; i < INPUT.polls; i++) {
        await page.waitForTimeout(2000);
        ({ value, failed } = await page.evaluate(() => ({
          value: document.querySelector("#new_comment_field")?.value ?? "",
          failed: document.querySelectorAll(".js-upload-markdown-image.is-failed").length,
        })));
        const done = assetUrls(value).length;
        if (failed > 0 || (done >= INPUT.files.length && !/Uploading/.test(value))) break;
      }
      for (const m of value.matchAll(/<img[^>]*alt="([^"]+)"[^>]*src="(https:\/\/github\.com\/user-attachments\/assets\/[^"]+)"/g)) urls[m[1].replace(/\.(png|jpe?g|gif|webp)$/i, "")] = m[2];
      for (const m of value.matchAll(/!\[([^\]]*)\]\((https:\/\/github\.com\/user-attachments\/assets\/[^)\s]+)\)/g)) urls[m[1].replace(/\.(png|jpe?g|gif|webp)$/i, "")] = m[2];
    } else {
      // One file per chooser, so the URL that appears belongs to that file.
      for (let n = 0; n < INPUT.files.length; n++) {
        let got = false;
        const seen = new Set(assetUrls(await read()));
        const chooserPromise = page.waitForFileChooser({ timeout: 15000 });
        await page.click(NEW.button);
        const chooser = await chooserPromise;
        await chooser.setFiles(INPUT.files[n]);
        for (let i = 0; i < INPUT.polls; i++) {
          await page.waitForTimeout(2000);
          value = await read();
          const fresh = assetUrls(value).find((u) => !seen.has(u));
          if (fresh && !/Uploading/.test(value)) { urls[INPUT.stems[n]] = fresh; got = true; break; }
        }
        // A late URL from this file would be taken for the next one's, so stop here.
        if (!got) break;
      }
    }
  } finally {
    await page.fill(box, "");
  }
  const cleared = (await read()) === "";
  await task.finish({ keep: [] });
  console.log("SHOTS_RESULT " + JSON.stringify({ urls, failed, cleared }));
}
"""


def run_browser(js, inp):
    script = "const INPUT = " + json.dumps(inp) + ";\n" + js
    proc = subprocess.run([os.environ.get("EGO_BROWSER", "ego-browser"), "nodejs"],
                          input=script, capture_output=True, text=True)
    # ego-browser prints the script's console output on stderr.
    for line in (proc.stdout + "\n" + proc.stderr).splitlines():
        if line.startswith(RESULT_TAG):
            return json.loads(line[len(RESULT_TAG):])
    found = list(dict.fromkeys(DIAGNOSTIC.findall(proc.stdout + proc.stderr)))
    sys.exit(f"ego-browser gave no result (exit {proc.returncode}): {', '.join(found) or 'no known diagnostic'}. "
             "Its raw output is not printed, since it can carry URLs with tokens.")


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
    out = pathlib.Path(a.out).resolve()
    dirs = sorted({str(f.parent) for f in files})
    for d in dirs:
        if out == pathlib.Path(d) or pathlib.Path(d) in out.parents:
            # The janitor reports, and never removes, a directory that holds a non-image file.
            sys.exit(f"--out {out} is inside the image directory {d}: keep urls.json outside it")
    for d in [*dirs, str(out.parent)]:
        if in_worktree(pathlib.Path(d)):
            sys.exit(f"{d} is inside a git worktree: keep evidence in a scratchpad or temporary directory")
    res = run_browser(UPLOAD_JS, {
        "space": f"visual-evidence upload {a.repo} {a.pr}",
        "prUrl": f"https://github.com/{a.repo}/pull/{a.pr}",
        "files": [str(f) for f in files], "stems": stems, "polls": a.polls,
    })
    if res.get("noBox"):
        sys.exit("no comment box on the PR page: not signed in to GitHub, or its comment UI changed again")
    if res.get("draft"):
        sys.exit("the PR's comment box holds an unsent draft: post or clear it in the browser, then rerun")
    urls = {s: res["urls"].get(s) for s in stems}
    out.write_text(json.dumps(urls, indent=2) + "\n")
    missing = [s for s, u in urls.items() if not u]
    print(f"uploaded {len(stems) - len(missing)} of {len(stems)}, failed {res['failed']}, draft cleared {res['cleared']}")
    for s in missing:
        print(f"missing: {s}", file=sys.stderr)
    if not res["cleared"]:
        print("the comment box still holds text: clear it in the browser before leaving the page", file=sys.stderr)
    registered = all([register(d, a.repo, a.pr) for d in dirs])
    return 1 if missing or res["failed"] or not res["cleared"] or not registered else 0


def in_worktree(path):
    d = path
    while not d.is_dir():
        d = d.parent
    # GIT_DIR and friends would pick another repository, and a localized message would hide the reason.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | {"LC_ALL": "C"}
    proc = subprocess.run(["git", "-C", str(d), "rev-parse", "--show-toplevel"], capture_output=True, text=True, env=env)
    if proc.returncode == 0:
        return True
    if "not a git repository" in proc.stderr:
        return False
    sys.exit(f"cannot tell whether {d} is inside a git worktree: {proc.stderr.strip()[:200]}")


def register(directory, repo, pr):
    """Put the evidence directory in the reap ledger so the janitor removes it after the PR closes."""
    proc = subprocess.run([sys.executable, str(REAP), "ledger", "add", "--kind", "evidence",
                           "--path", directory, "--pr", f"{repo}#{pr}"], capture_output=True, text=True)
    if proc.returncode:
        print(f"could not register {directory} in the reap ledger: {proc.stderr.strip() or proc.stdout.strip()}", file=sys.stderr)
    return proc.returncode == 0


def head_status(url):
    """Return (HTTP status, content type); status 0 means the request itself failed."""
    # GET, not HEAD: GitHub redirects to a storage URL signed for GET, which answers a HEAD with 403.
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Range": "bytes=0-0"}), timeout=15) as r:
            return r.status, r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, OSError):
        return 0, ""


def check(a):
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", a.repo):
        sys.exit(f"--repo must be owner/repo: {a.repo!r}")
    urls = json.loads(pathlib.Path(a.urls).read_text())
    proc = subprocess.run(["gh", "api", "--paginate", f"repos/{a.repo}/issues/{a.pr}/comments", "--jq", ".[].body"],
                          capture_output=True, text=True)
    # Unreadable comments (a token without access) cannot show whether the image was posted.
    bodies = proc.stdout if proc.returncode == 0 else None
    # A token without access to a private repo gets 403/404 here too; that is "unknown", not a crash.
    proc = subprocess.run(["gh", "api", f"repos/{a.repo}", "--jq", ".private"], capture_output=True, text=True)
    private = proc.stdout.strip() == "true" if proc.returncode == 0 else None
    verdicts = set()
    for name, url in urls.items():
        if not url:
            verdict, why = "NOT VERIFIED", "no URL; upload it first"
        elif bodies is None:
            verdict, why = "INCONCLUSIVE", "cannot read the PR comments with this gh login"
        elif not re.search(r"(?<![\w/%~=&?#:.-])" + re.escape(url) + r"(?![\w/%~-]|\.[\w/%~-])", bodies):
            # A bare substring test would count .../abc as posted when only .../abcdef is.
            verdict, why = "NOT VERIFIED", "no PR comment references it, so GitHub answers 404"
        else:
            status, ctype = head_status(url)
            if status in (200, 206) and ctype.startswith("image/"):
                verdict, why = "VERIFIED", ctype
            elif private and status in (0, 401, 403, 404):
                verdict, why = "INCONCLUSIVE", f"private repo: HTTP {status} without a signed-in session"
            elif private is None and status in (401, 403):
                verdict, why = "INCONCLUSIVE", f"visibility unknown: HTTP {status}, gh cannot read the repo"
            else:
                verdict, why = "NOT VERIFIED", f"HTTP {status} {ctype}".strip()
        print(f"{verdict}  {name}: {why}")
        verdicts.add(verdict)
    # Exit 1 when any image failed, 2 when none failed but some could not be judged.
    return 1 if "NOT VERIFIED" in verdicts else 2 if "INCONCLUSIVE" in verdicts else 0


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
            cell = groups.setdefault(m["subject"], {})
            key = (int(m["width"]), m["theme"])
            if key in cell:
                # A tagged retake replaces the untagged shot of the same cell; two retakes are ambiguous.
                other = cell[key]
                if other[2] and m["tag"]:
                    raise SystemExit(f"two retakes for one cell: {other[0]} and {name}; upload only one")
                if other[2]:
                    continue
            cell[key] = (name, url, m["tag"])
        else:
            singles.append((name, url))
    out = ["### 스크린샷", ""]
    for subject, shots in groups.items():
        themes = [t for t in ("light", "dark") if any(k[1] == t for k in shots)]
        out += [f"**{subject}**", "", "| | " + " | ".join(themes) + " |", "| --- |" + " --- |" * len(themes)]
        for w in sorted({k[0] for k in shots}, reverse=True):
            size = 360 if w >= 768 else 180
            cells = [img(*shots[(w, t)][:2], size) if (w, t) in shots else "—" for t in themes]
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
    k = sub.add_parser("check")
    k.add_argument("urls")
    k.add_argument("--repo", required=True)
    k.add_argument("--pr", type=int, required=True)
    t = sub.add_parser("table")
    t.add_argument("urls")
    a = p.parse_args(argv)
    if a.cmd == "capture":
        return capture(a)
    if a.cmd == "upload":
        return upload(a)
    if a.cmd == "check":
        return check(a)
    sys.stdout.write(table(json.loads(pathlib.Path(a.urls).read_text())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
