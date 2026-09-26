#!/usr/bin/env python3
"""Fill a secret into a browser field after the owner approves it with Touch ID.

The value is read only after approval, reaches the browser through stdin of a child
process (never argv), and is never printed or logged. See ../SKILL.md.
"""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent
CONFIG = pathlib.Path(os.environ.get("XDG_CONFIG_HOME", pathlib.Path.home() / ".config")) / "secure-fill" / "allow.json"
LOG = pathlib.Path(os.environ.get("XDG_STATE_HOME", pathlib.Path.home() / ".local" / "state")) / "secure-fill" / "log.jsonl"
CACHE = pathlib.Path(os.environ.get("XDG_CACHE_HOME", pathlib.Path.home() / ".cache")) / "secure-fill"

EXIT = {
    "filled": 0,
    "usage": 2,
    "denied": 10,
    "origin-mismatch": 11,
    "not-found": 12,
    "not-allowed": 13,
    "no-field": 14,
    "fill-failed": 15,
    "config-error": 16,
}
HINT = {
    "filled": "value entered; the tool did not submit the form unless --submit was given",
    "denied": "the owner did not approve (or Touch ID is unavailable); do not retry without asking the owner",
    "origin-mismatch": "the active tab is not on the requested origin; navigate there first",
    "not-found": "no secret stored for this item; ask the owner to add it (SKILL.md owner setup)",
    "not-allowed": "item/origin pair is not in the owner's allowlist; ask the owner, never edit it yourself",
    "no-field": "no focused input on the page; focus the field (or pass --selector) and run again",
    "fill-failed": "could not deliver the value to the browser; see the reason and SKILL.md owner setup",
    "config-error": "the owner's allowlist is malformed; ask the owner to fix it",
}
ITEM_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
DEFAULT_PORT = {"http": 80, "https": 443}


class Outcome(Exception):
    def __init__(self, result, reason=""):
        super().__init__(result)
        self.result, self.reason = result, reason


def normalize_origin(url):
    """scheme://host[:port] as browsers spell location.origin, or None if unusable."""
    try:
        parts = urllib.parse.urlsplit(url.strip())
        port = parts.port
    except (ValueError, AttributeError):
        return None
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORT or not parts.hostname or "@" in parts.netloc:
        return None
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    return f"{scheme}://{host}" + (f":{port}" if port and port != DEFAULT_PORT[scheme] else "")


def parse_origin_arg(text):
    """An --origin or allowlist entry: an origin only, no path, query or fragment."""
    origin = normalize_origin(text)
    parts = urllib.parse.urlsplit(text.strip())
    if origin is None or parts.path not in ("", "/") or parts.query or parts.fragment:
        return None
    return origin


def load_allowlist(text):
    """{"items": {name: {"origins": [...], "keychain": {...}?, "env": NAME?}}} -> {name: entry}."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise Outcome("config-error", f"allow.json is not JSON (line {e.lineno})")
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, dict):
        raise Outcome("config-error", 'allow.json needs a top-level "items" object')
    out = {}
    for name, entry in items.items():
        if not ITEM_RE.match(name) or not isinstance(entry, dict):
            raise Outcome("config-error", f"bad item entry {name!r}")
        origins = entry.get("origins")
        if not isinstance(origins, list) or not origins:
            raise Outcome("config-error", f"{name}: origins must be a non-empty list")
        parsed = [parse_origin_arg(o) if isinstance(o, str) else None for o in origins]
        if None in parsed:
            raise Outcome("config-error", f"{name}: every origin must look like https://host[:port]")
        kc = entry.get("keychain", {})
        env = entry.get("env")
        if not isinstance(kc, dict) or not all(isinstance(v, str) for v in kc.values()):
            raise Outcome("config-error", f"{name}: keychain must be an object of strings")
        if env is not None and not isinstance(env, str):
            raise Outcome("config-error", f"{name}: env must be a string")
        out[name] = {
            "origins": set(parsed),
            "keychain": {"service": kc.get("service", "secure-fill"), "account": kc.get("account", name),
                         "path": kc.get("path")},
            "env": env,
        }
    return out


# ---- secret sources --------------------------------------------------------------------------

def keychain_source(run):
    def read(entry):
        kc = entry["keychain"]
        argv = ["security", "find-generic-password", "-s", kc["service"], "-a", kc["account"], "-w"]
        if kc["path"]:
            argv.append(os.path.expanduser(kc["path"]))
        p = run(argv)
        return p.stdout.rstrip("\n") or None if p.returncode == 0 else None
    return read


def env_source(entry):
    return os.environ.get(entry["env"]) or None if entry["env"] else None


# ---- helpers that talk to macOS --------------------------------------------------------------

def _run(argv, stdin=None, timeout=120):
    return subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout)


def helper_path(run=_run):
    """Compile sfhelper.swift once per source hash; the binary lives outside the repo."""
    src = HERE / "sfhelper.swift"
    digest = hashlib.sha256(src.read_bytes()).hexdigest()[:12]
    binary = CACHE / f"sfhelper-{digest}"
    if not binary.exists():
        CACHE.mkdir(parents=True, exist_ok=True, mode=0o700)
        p = run(["xcrun", "swiftc", "-O", "-o", str(binary), str(src)], timeout=600)
        if p.returncode != 0:
            raise Outcome("fill-failed", "could not compile sfhelper.swift (install Xcode command line tools)")
    return str(binary)


def touch_id_gate(run=_run):
    def approve(reason):
        return run([helper_path(run), "auth", reason], timeout=300).returncode == 0
    return approve


def paste_via_helper(run, bundle_id, secret, submit, clear_after=3):
    p = run([helper_path(run), "paste", bundle_id, str(clear_after), "1" if submit else "0"], stdin=secret)
    reasons = {3: "the terminal app lacks Accessibility permission (SKILL.md owner setup)",
               4: "browser app is not running", 5: "browser app did not come to the front"}
    if p.returncode != 0:
        raise Outcome("fill-failed", reasons.get(p.returncode, f"sfhelper paste exited {p.returncode}"))


def applescript_string(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def fill_js(origin, selector, secret, submit):
    """Check and fill in one page turn, so the tab cannot change origin between the two."""
    return (
        "(function(){"
        f"if (location.origin !== {json.dumps(origin)}) return 'origin-mismatch';"
        f"var sel = {json.dumps(selector)};"
        "var el = sel ? document.querySelector(sel) : document.activeElement;"
        "if (!el || !('value' in el) || el === document.body) return 'no-field';"
        "var d = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value');"
        f"var v = {json.dumps(secret)};"
        "if (d && d.set) d.set.call(el, v); else el.value = v;"
        "el.dispatchEvent(new Event('input', {bubbles: true}));"
        "el.dispatchEvent(new Event('change', {bubbles: true}));"
        f"if ({json.dumps(submit)} && el.form) el.form.requestSubmit();"
        "return 'ok';})()"
    )


# ---- browsers --------------------------------------------------------------------------------

class Chrome:
    name = "chrome"
    bundle_id = "com.google.Chrome"

    def __init__(self, run=_run):
        self.run = run

    def _osascript(self, script):
        # The script travels on stdin: it can carry the secret, and argv is visible to `ps`.
        return self.run(["osascript", "-"], stdin=script)

    def current_origin(self):
        p = self._osascript(
            'if application "Google Chrome" is running then\n'
            'tell application "Google Chrome" to return URL of active tab of front window\n'
            "end if\n")
        return normalize_origin(p.stdout) if p.returncode == 0 and p.stdout.strip() else None

    def fill(self, origin, selector, secret, submit):
        p = self._osascript(
            'tell application "Google Chrome" to execute active tab of front window javascript '
            + applescript_string(fill_js(origin, selector, secret, submit)) + "\n")
        if p.returncode == 0:
            result = p.stdout.strip()
            if result == "ok":
                return "dom"
            raise Outcome(result if result in ("origin-mismatch", "no-field") else "fill-failed", "page script")
        # "Allow JavaScript from Apple Events" is off: fall back to the clipboard.
        if selector:
            raise Outcome("fill-failed", "--selector needs View > Developer > Allow JavaScript from Apple Events")
        if self.current_origin() != origin:
            raise Outcome("origin-mismatch", "tab changed before paste")
        paste_via_helper(self.run, self.bundle_id, secret, submit)
        return "paste"


class Orca:
    """Orca's built-in browser. Its fill/type/eval commands take the value in argv, so they
    are used only to read the page, never to carry the secret."""
    name = "orca"
    bundle_id = "com.stablyai.orca"

    def __init__(self, run=_run, page=None):
        self.run, self.page = run, page

    def _orca(self, *argv):
        p = self.run(["orca", *argv, *(["--page", self.page] if self.page else []), "--json"])
        if p.returncode != 0:
            return None
        try:
            return json.loads(p.stdout).get("result")
        except (json.JSONDecodeError, AttributeError):
            return None

    def current_origin(self):
        result = self._orca("get", "--what", "url")
        url = result.get("value") if isinstance(result, dict) else result
        return normalize_origin(url) if isinstance(url, str) else None

    def focused_field(self):
        result = self._orca("eval", "--expression",
                            "document.hasFocus() && !!document.activeElement"
                            " && ['INPUT','TEXTAREA'].includes(document.activeElement.tagName)")
        value = result.get("value") if isinstance(result, dict) else result
        return value is True

    def fill(self, origin, selector, secret, submit):
        if selector:
            raise Outcome("fill-failed", "--selector is not supported for orca; focus the field instead")
        if self.current_origin() != origin:
            raise Outcome("origin-mismatch", "tab changed before paste")
        # Without this, Cmd+V could land in the agent's own terminal pane.
        if not self.focused_field():
            raise Outcome("no-field", "the Orca browser pane has no focused input")
        paste_via_helper(self.run, self.bundle_id, secret, submit)
        return "paste"


# ---- main flow -------------------------------------------------------------------------------

def fill_secret(item, origin, browser, allowlist, approve, sources, selector=None, submit=False):
    """Returns the fill method. Raises Outcome on every other result."""
    entry = allowlist.get(item)
    if entry is None or origin not in entry["origins"]:
        raise Outcome("not-allowed", "item/origin not in allow.json")
    current = browser.current_origin()
    if current != origin:
        raise Outcome("origin-mismatch", f"active tab is {current or 'unknown'}")
    if not approve(f'fill "{item}" into {origin} ({browser.name})'):
        raise Outcome("denied")
    secret = next((s for s in (src(entry) for src in sources) if s), None)
    if secret is None:
        raise Outcome("not-found", "neither keychain nor env has a value")
    return browser.fill(origin, selector, secret, submit)


def append_log(record, path=LOG):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main(argv=None, run=_run, approve=None, sources=None, config=CONFIG, log=LOG, out=sys.stdout):
    ap = argparse.ArgumentParser(prog="secure-fill", description=__doc__.splitlines()[0])
    ap.add_argument("--item", required=True, help="allowlist item name (not the value)")
    ap.add_argument("--origin", required=True, help="expected origin, e.g. https://dev.example.test")
    ap.add_argument("--target", required=True, choices=["chrome", "orca"])
    ap.add_argument("--selector", help="CSS selector (chrome with JavaScript from Apple Events only)")
    ap.add_argument("--orca-page", help="Orca page id from `orca tab list --json`")
    ap.add_argument("--submit", action="store_true", help="submit the form after filling")
    a = ap.parse_args(argv)

    origin = parse_origin_arg(a.origin) if ITEM_RE.match(a.item) else None
    record = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
              "item": a.item, "origin": origin or a.origin, "target": a.target}
    warnings = []
    try:
        if origin is None:
            raise Outcome("usage", "--item must match [A-Za-z0-9._-] and --origin must be scheme://host[:port]")
        try:
            text = pathlib.Path(config).read_text()
        except FileNotFoundError:
            raise Outcome("not-allowed", f"no allowlist at {config}")
        if os.access(config, os.W_OK):
            warnings.append("allowlist-writable-by-this-user")
        browser = Chrome(run) if a.target == "chrome" else Orca(run, a.orca_page)
        record["method"] = fill_secret(
            a.item, origin, browser, load_allowlist(text),
            approve or touch_id_gate(run), sources or [keychain_source(run), env_source],
            a.selector, a.submit)
        result, reason = "filled", ""
    except Outcome as o:
        result, reason = o.result, o.reason
    except subprocess.TimeoutExpired:
        result, reason = "fill-failed", "a helper timed out"
    record.update(result=result, **({"reason": reason} if reason else {}),
                  **({"warnings": warnings} if warnings else {}))
    append_log(record, pathlib.Path(log))
    line = f"secure-fill: {result} item={a.item} origin={record['origin']} target={a.target}"
    if reason:
        line += f" ({reason})"
    print(line, file=out)
    if result in HINT:
        print(f"  {HINT[result]}", file=out)
    for w in warnings:
        print(f"  warning: {w} (owner: see SKILL.md owner setup)", file=out)
    return EXIT[result]


if __name__ == "__main__":
    sys.exit(main())
