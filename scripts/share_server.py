#!/usr/bin/env python3
"""Serve a share folder on loopback, showing a folder of images as a gallery.

Usage:
  share_server.py serve [--root DIR] [--port N]   # foreground; defaults from config
  share_server.py install [--here]                # macOS: keep `serve` running as a LaunchAgent
  share_server.py uninstall

A folder with index.html serves that page. A folder without one lists its entries, and when it holds images
(png, jpg, webp, gif) they come first as a thumbnail grid grouped by name with the size/theme suffix
(-1280, -390, -dark, ...) dropped; clicking one opens it full size, arrow keys step through them.

Binds 127.0.0.1 only; publishing on a network (e.g. `tailscale serve`) is left to the owner. Hidden entries and
symlinks that resolve outside the root are neither listed nor served. Settings live under the "share-server" key
of ~/.claude/agent-skills.json (see DEFAULTS); "label" lets an existing LaunchAgent be taken over in place, and
the plist it replaces is kept next to it as <name>.plist.bak-<time>.
"""
import html, http.server, io, json, os, pathlib, plistlib, re, shutil, subprocess, sys, time
from urllib.parse import quote, unquote, urlsplit

CONFIG = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG", "~/.claude/agent-skills.json")).expanduser()
STATE_DIR = pathlib.Path(os.environ.get("SHARE_SERVER_STATE", "~/.local/state/agent-skills")).expanduser()
LOG = STATE_DIR / "share-server.log"
HOST = "127.0.0.1"
DEFAULTS = {"root": "~/share", "port": 13201, "label": "io.github.jeongjaesoon.share-server"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
# Trailing variant tokens that name the same shot at another width or theme.
VARIANT = re.compile(r"(?:[-_.@](?:\d{3,4}w?|\d{3,4}x\d{3,4}|[23]x|dark|light|mobile|desktop|tablet))+$", re.I)


def config():
    try:
        mine = json.loads(CONFIG.read_text()).get("share-server") or {}
    except (OSError, ValueError):
        mine = {}
    return {**DEFAULTS, **mine}


def group_key(name):
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return VARIANT.sub("", stem) or stem


def groups(names):
    """[(key, [names])] in name order; names keep their sorted order inside a group."""
    out = {}
    for n in sorted(names):
        out.setdefault(group_key(n), []).append(n)
    return sorted(out.items())


class Handler(http.server.SimpleHTTPRequestHandler):
    server_version = "share-server"
    sys_version = ""

    def __init__(self, *args, root, **kw):
        self.root = os.path.realpath(root)
        super().__init__(*args, directory=self.root, **kw)

    def inside(self, path):
        real = os.path.realpath(path)
        return real == self.root or real.startswith(self.root + os.sep)

    def send_head(self):
        parts = unquote(urlsplit(self.path).path).split("/")
        if any(p.startswith(".") and p not in (".", "..") or p == ".." for p in parts) or \
                not self.inside(self.translate_path(self.path)):
            self.send_error(404)
            return None
        return super().send_head()

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def visible(self, folder, name):
        return not name.startswith(".") and self.inside(os.path.join(folder, name))

    def list_directory(self, path):
        try:
            names = sorted(n for n in os.listdir(path) if self.visible(path, n))
        except OSError:
            self.send_error(404)
            return None
        dirs = [n for n in names if os.path.isdir(os.path.join(path, n))]
        images = [n for n in names if n not in dirs and os.path.splitext(n)[1].lower() in IMAGE_EXT]
        others = [n for n in names if n not in dirs and n not in images]
        title = html.escape(unquote(urlsplit(self.path).path))
        body = page(title, dirs, images, others).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
        self.end_headers()
        return io.BytesIO(body)


def link(name, slash=""):
    return f'<a href="{quote(name)}{slash}">{html.escape(name)}{slash}</a>'


def page(title, dirs, images, others):
    entries = [f"<li>{link(n, '/')}</li>" for n in dirs] + [f"<li>{link(n)}</li>" for n in others]
    gallery = ""
    if images:
        sections = []
        for key, names in groups(images):
            figs = "".join(
                f'<figure><a href="{quote(n)}" data-i><img src="{quote(n)}" loading="lazy" alt=""></a>'
                f"<figcaption>{html.escape(n)}</figcaption></figure>" for n in names)
            sections.append(f"<section><h2>{html.escape(key)} <small>{len(names)}</small></h2>"
                            f'<div class="grid">{figs}</div></section>')
        gallery = "".join(sections) + LIGHTBOX
    files = f"<ul>{''.join(entries)}</ul>" if entries else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{CSS}</style></head><body>
<h1>{title}</h1>{'<p class="up"><a href="../">../</a></p>' if title != '/' else ''}
{gallery}{files}</body></html>"""


CSS = """
:root{color-scheme:light dark;--bg:#fff;--fg:#1d1d1f;--mute:#6e6e73;--line:#d2d2d7;--card:#f5f5f7}
@media (prefers-color-scheme:dark){:root{--bg:#111;--fg:#f5f5f7;--mute:#a1a1a6;--line:#38383a;--card:#1c1c1e}}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,system-ui,sans-serif}
h1{font-size:18px;margin:0 0 8px;word-break:break-all}
h2{font-size:14px;margin:24px 0 8px;word-break:break-all}h2 small{color:var(--mute);font-weight:400}
a{color:inherit}.up{margin:0 0 8px}ul{padding-left:20px}li{margin:2px 0;word-break:break-all}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}
figure{margin:0;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}
figure a{display:block;aspect-ratio:4/3;background:var(--card)}
figure img{width:100%;height:100%;object-fit:contain;display:block}
figcaption{padding:6px 8px;font-size:12px;color:var(--mute);word-break:break-all;border-top:1px solid var(--line)}
#lb{position:fixed;inset:0;background:rgba(0,0,0,.92);display:none;flex-direction:column;z-index:9}
#lb.on{display:flex}#lb img{flex:1;min-height:0;object-fit:contain;width:100%}
#lb p{margin:0;padding:8px 16px;color:#eee;display:flex;gap:16px;align-items:center;word-break:break-all}
#lb p span{flex:1}#lb button{background:none;border:1px solid #666;color:#eee;border-radius:6px;padding:4px 10px;
font:inherit;cursor:pointer}
@media (max-width:480px){.grid{grid-template-columns:repeat(2,1fr);gap:8px}}
"""

LIGHTBOX = """
<div id="lb" role="dialog" aria-modal="true" aria-label="image viewer"><img alt=""><p>
<button data-d="-1" aria-label="previous">&larr;</button><span></span><a target="_blank">original</a>
<button data-d="1" aria-label="next">&rarr;</button><button data-x aria-label="close">&times;</button></p></div>
<script>
(() => {
  const links = [...document.querySelectorAll('a[data-i]')], lb = document.getElementById('lb');
  const img = lb.querySelector('img'), cap = lb.querySelector('span'), orig = lb.querySelector('p a');
  let i = -1;
  const show = n => {
    i = (n + links.length) % links.length;
    img.src = orig.href = links[i].href;
    cap.textContent = `${i + 1}/${links.length} · ${links[i].closest('figure').querySelector('figcaption').textContent}`;
    lb.classList.add('on');
  };
  const close = () => { lb.classList.remove('on'); img.removeAttribute('src'); links[i]?.focus(); i = -1; };
  links.forEach((a, n) => a.addEventListener('click', e => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;
    e.preventDefault(); show(n);
  }));
  lb.addEventListener('click', e => {
    const b = e.target.closest('button');
    if (b) b.dataset.x !== undefined ? close() : show(i + +b.dataset.d);
    else if (e.target === lb) close();
  });
  addEventListener('keydown', e => {
    if (i < 0) return;
    if (e.key === 'ArrowRight') show(i + 1);
    else if (e.key === 'ArrowLeft') show(i - 1);
    else if (e.key === 'Escape') close();
  });
})();
</script>"""


def serve(root, port):
    root = str(pathlib.Path(root).expanduser())
    if not os.path.isdir(root):
        print(f"{root} is not a directory", file=sys.stderr)
        return 1
    httpd = http.server.ThreadingHTTPServer((HOST, int(port)), lambda *a: Handler(*a, root=root))
    print(f"serving {root} on http://{HOST}:{port}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


# ---------------------------------------------------------------- launchd

def plist_path(cfg=None):
    return pathlib.Path(f"~/Library/LaunchAgents/{(cfg or config())['label']}.plist").expanduser()


def agent_target(cfg=None):
    return f"gui/{os.getuid()}/{(cfg or config())['label']}"


def spec(cfg=None):
    cfg = cfg or config()
    return {"Label": cfg["label"],
            "ProgramArguments": [shutil.which("python3") or sys.executable, "-I", str(pathlib.Path(__file__).resolve()),
                                 "serve", "--root", str(pathlib.Path(cfg["root"]).expanduser()),
                                 "--port", str(int(cfg["port"]))],
            "RunAtLoad": True, "KeepAlive": True, "ProcessType": "Background",
            "StandardOutPath": str(LOG), "StandardErrorPath": str(LOG)}


def plist_spec():
    try:
        return plistlib.loads(plist_path().read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None


def loaded():
    return subprocess.run(["launchctl", "print", agent_target()], capture_output=True).returncode == 0


def cmd_install(here_ok):
    if sys.platform != "darwin":
        print("no launchd on this OS; run `share_server.py serve` under your service manager instead", file=sys.stderr)
        return 1
    repo = pathlib.Path(__file__).resolve().parents[1]
    if not (repo / ".git").is_dir() and not here_ok:  # a worktree goes away under launchd
        print(f"{repo} is not a main git checkout; install from the checkout sessions load (or pass --here)",
              file=sys.stderr)
        return 1
    plist, want = plist_path(), spec()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    plist.parent.mkdir(parents=True, exist_ok=True)
    if plist.exists() and plist_spec() != want:
        backup = plist.with_name(f"{plist.name}.bak-{time.strftime('%Y%m%d%H%M%S')}")
        shutil.copy2(plist, backup)
        print(f"kept the previous plist as {backup}")
    subprocess.run(["launchctl", "bootout", agent_target()], capture_output=True)
    plist.write_bytes(plistlib.dumps(want))
    for _ in range(5):  # bootstrap right after bootout can fail with error 5 until the old job is gone
        p = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)], capture_output=True, text=True)
        if p.returncode == 0:
            print(f"wrote {plist} and loaded {agent_target()} (http://{HOST}:{config()['port']}/)")
            return 0
        time.sleep(1)
    print(f"launchctl bootstrap failed: {p.stderr.strip()}", file=sys.stderr)
    return 1


def cmd_uninstall():
    subprocess.run(["launchctl", "bootout", agent_target()], capture_output=True)
    plist_path().unlink(missing_ok=True)
    print(f"removed {agent_target()}")
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "serve":
        cfg = config()
        opts = dict(zip(argv[2::2], argv[3::2]))
        return serve(opts.get("--root", cfg["root"]), opts.get("--port", cfg["port"]))
    if cmd == "install":
        return cmd_install("--here" in argv)
    if cmd == "uninstall":
        return cmd_uninstall()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
