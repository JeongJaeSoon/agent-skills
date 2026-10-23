#!/usr/bin/env python3
"""Measure a rendered page against the ui-review rules in headless Chrome.

  ui_measure.py TARGET [--widths 1280,390] [--scope SEL] [--exclude SEL]... [--before JS]
                [--baseline TARGET] [--json OUT] [--grid 4] [--allow 6,10]

TARGET is a URL or a local HTML file. Prints one markdown table per width and exits 1 when any
check FAILs (WARN does not fail), 2 when the page or the scope cannot be measured. Chrome is driven over --remote-debugging-pipe, so only the
standard library is needed; CHROME overrides the binary. The browser runs with a throwaway
profile and is stopped by the PID this script started.
"""
import argparse, json, os, pathlib, shutil, subprocess, sys, tempfile, time

HERE = pathlib.Path(__file__).parent
CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]
GENERIC_LABELS = ["새로 만들기", "만들기", "추가", "새로", "New", "Create", "Add", "+"]
TOGGLE_LABELS = ["켜짐", "꺼짐", "켜기", "끄기", "On", "Off", "Enabled", "Disabled"]


def find_chrome():
    if os.environ.get("CHROME"):
        return os.environ["CHROME"]
    for p in CHROME_PATHS:
        if os.path.exists(p):
            return p
    for n in ("google-chrome", "chromium", "chromium-browser"):
        if shutil.which(n):
            return shutil.which(n)
    return None


class Chrome:
    """One headless Chrome, CDP messages framed by NUL over fds 3 (in) and 4 (out)."""

    def __init__(self, binary):
        self.profile = tempfile.mkdtemp(prefix="ui-review-")
        to_chrome_r, self.to_chrome = os.pipe()
        self.from_chrome, from_chrome_w = os.pipe()
        args = [binary, "--headless=new", "--remote-debugging-pipe", f"--user-data-dir={self.profile}",
                "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--hide-scrollbars",
                "--disable-extensions", "--mute-audio", "about:blank"]
        # The shell moves the pipe ends to the fd numbers Chrome expects, then execs it (same PID).
        self.proc = subprocess.Popen(["/bin/sh", "-c", f'exec "$@" 3<&{to_chrome_r} 4>&{from_chrome_w}', "sh", *args],
                                     pass_fds=(to_chrome_r, from_chrome_w), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(to_chrome_r)
        os.close(from_chrome_w)
        self.buf, self.next_id, self.events = b"", 0, []

    def send(self, method, params=None, session=None, timeout=60):
        self.next_id += 1
        msg = {"id": self.next_id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        os.write(self.to_chrome, json.dumps(msg).encode() + b"\0")
        while True:
            m = self._read(timeout)
            if m.get("id") == self.next_id:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error'].get('message')}")
                return m.get("result", {})
            self.events.append(m)

    def wait_event(self, method, session, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for i, e in enumerate(self.events):
                if e.get("method") == method and e.get("sessionId") == session:
                    del self.events[: i + 1]
                    return e
            self.events.append(self._read(deadline - time.time()))
        raise TimeoutError(method)

    def _read(self, timeout):
        import select
        while b"\0" not in self.buf:
            ready, _, _ = select.select([self.from_chrome], [], [], max(timeout, 0.1))
            if not ready:
                raise TimeoutError("no reply from Chrome")
            chunk = os.read(self.from_chrome, 1 << 20)
            if not chunk:
                raise RuntimeError("Chrome closed the pipe")
            self.buf += chunk
        raw, self.buf = self.buf.split(b"\0", 1)
        return json.loads(raw)

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        for fd in (self.to_chrome, self.from_chrome):
            try:
                os.close(fd)
            except OSError:
                pass
        shutil.rmtree(self.profile, ignore_errors=True)


def to_url(target):
    if "://" in target or target.startswith("about:"):
        return target
    return pathlib.Path(target).resolve().as_uri()


def measure(chrome, target, width, height, opts, before=None, settle_ms=500):
    tid = chrome.send("Target.createTarget", {"url": "about:blank"})["targetId"]
    sid = chrome.send("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
    try:
        chrome.send("Emulation.setDeviceMetricsOverride", {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False}, sid)
        # mobile: True would let the page zoom out to fit, hiding overflow at the phone width.
        chrome.send("Page.enable", {}, sid)
        nav = chrome.send("Page.navigate", {"url": to_url(target)}, sid)
        # A dead server or a wrong path loads Chrome's error page, which would then be measured.
        if nav.get("errorText"):
            raise RuntimeError(f"cannot open {target}: {nav['errorText']}")
        chrome.wait_event("Page.loadEventFired", sid)
        time.sleep(settle_ms / 1000)
        if before:
            evaluate(chrome, sid, f"(async () => {{ {before} }})()")
            time.sleep(settle_ms / 1000)
        src = (HERE / "measure.js").read_text()
        return evaluate(chrome, sid, f"({src})({json.dumps(opts)})")
    finally:
        chrome.send("Target.closeTarget", {"targetId": tid})


def evaluate(chrome, sid, expr):
    r = chrome.send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True}, sid)
    if "exceptionDetails" in r:
        d = r["exceptionDetails"]
        raise RuntimeError(d.get("exception", {}).get("description") or d.get("text"))
    return r["result"].get("value")


def compare_baseline(page, base):
    """Component signatures the page uses that the baseline page does not, per kind both have."""
    items = []
    for kind, sigs in page["sigs"].items():
        known = base["sigs"].get(kind)
        if not known:
            continue
        for s, where in sigs.items():
            if s not in known:
                items.append({"el": where, "detail": f"{kind} {s}; the baseline uses {' | '.join(sorted(known))}"})
    return {"id": "baseline", "status": "FAIL" if items else "PASS", "items": items[:20], "count": len(items)}


def table(width, result):
    esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")
    lines = [f"#### {width}px · {result['elements']} elements · coverage {round(result['coverage'] * 100)}%", "",
             "| check | 판정 | 건수 | 예 |", "|---|---|---|---|"]
    for c in result["checks"]:
        first = c["items"][0] if c["items"] else None
        ex = f"{esc(first['el'])}: {esc(first['detail'])}" if first else ""
        lines.append(f"| {c['id']} | {c['status']} | {c['count']} | {ex} |")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("target")
    ap.add_argument("--widths", default="1280,390")
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--scope", help="CSS selector of the region to measure (default body)")
    ap.add_argument("--exclude", action="append", default=[], help="CSS selector left out, repeatable")
    ap.add_argument("--before", help="JS run after load and before measuring (open a tab, pick a screen)")
    ap.add_argument("--baseline", help="URL or file of an existing screen whose components this page must match")
    ap.add_argument("--baseline-before", help="--before for the baseline page")
    ap.add_argument("--grid", type=float, default=4)
    ap.add_argument("--allow", default="", help="comma-separated spacing values allowed off the grid")
    ap.add_argument("--near-miss", type=float, default=6, help="edges this close but not equal are misaligned")
    ap.add_argument("--center-tol", type=float, default=1)
    ap.add_argument("--sparse", type=float, default=0.2, help="warn when less of the first screen holds content")
    ap.add_argument("--settle-ms", type=int, default=500)
    ap.add_argument("--json", help="write every width's full result here")
    ap.add_argument("--max-items", type=int, default=20)
    a = ap.parse_args(argv)

    binary = find_chrome()
    if not binary:
        print("ui_measure: no Chrome found; set CHROME to its binary", file=sys.stderr)
        return 2
    opts = {"scope": a.scope, "exclude": a.exclude, "grid": a.grid, "nearMiss": a.near_miss, "centerTol": a.center_tol,
            "sparse": a.sparse, "allow": [float(x) for x in a.allow.split(",") if x], "genericLabels": GENERIC_LABELS, "toggleLabels": TOGGLE_LABELS,
            "maxItems": a.max_items}
    chrome = Chrome(binary)
    out, failed = {}, False
    try:
        for w in [int(x) for x in a.widths.split(",")]:
            r = measure(chrome, a.target, w, a.height, opts, a.before, a.settle_ms)
            if a.baseline:
                r["checks"].append(compare_baseline(r, measure(chrome, a.baseline, w, a.height, {**opts, "scope": None}, a.baseline_before, a.settle_ms)))
            out[w] = r
            failed |= any(c["status"] == "FAIL" for c in r["checks"])
            print(table(w, r) + "\n")
    except (RuntimeError, TimeoutError) as e:
        print(f"ui_measure: {e}", file=sys.stderr)
        return 2
    finally:
        chrome.close()
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print("판정: FAIL" if failed else "판정: PASS (측정 항목만. 시나리오·육안 항목은 따로)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
