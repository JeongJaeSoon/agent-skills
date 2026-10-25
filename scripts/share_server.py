#!/usr/bin/env python3
"""Serve a share folder on loopback, showing a folder of images as a gallery.

Usage:
  share_server.py serve [--root DIR] [--port N]   # foreground; defaults from config
  share_server.py install [--here]                # macOS: keep `serve` running as a LaunchAgent
  share_server.py uninstall

A folder with index.html serves that page. A folder without one lists its entries, and when it holds images
(png, jpg, webp, gif) they come first as a thumbnail grid grouped by name with the size/theme suffix
(-1280, -390, -dark, ...) dropped; clicking one opens it full size, arrow keys step through them.
A .md file is rendered as a page (raw HTML in it is shown as text); `?raw` returns the source. Text files are
sent as UTF-8.

Binds 127.0.0.1 only; publishing on a network (e.g. `tailscale serve`) is left to the owner. Hidden entries and
symlinks that resolve outside the root are neither listed nor served. Settings live under the "share-server" key
of ~/.claude/agent-skills.json (see DEFAULTS); "label" lets an existing LaunchAgent be taken over in place, and
the plist it replaces is kept next to it as <name>.plist.bak-<time>.
"""
import html, http.server, io, json, os, pathlib, plistlib, re, shutil, subprocess, sys, time
from urllib.parse import parse_qs, quote, unquote, urlsplit

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

    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".md": "text/markdown", ".log": "text/plain", ".jsonl": "text/plain"}

    def __init__(self, *args, root, **kw):
        self.root = os.path.realpath(root)
        super().__init__(*args, directory=self.root, **kw)

    def inside(self, path):
        """The resolved path stays under the root and no part of it below the root is hidden."""
        real = os.path.realpath(path)
        if real == self.root:
            return True
        return real.startswith(self.root + os.sep) and \
            not any(p.startswith(".") for p in os.path.relpath(real, self.root).split(os.sep))

    def send_head(self):
        parts = unquote(urlsplit(self.path).path).split("/")
        path = self.translate_path(self.path)
        # The stdlib opens a folder's index.html itself, following a symlink wherever it points.
        indexes = [os.path.join(path, n) for n in ("index.html", "index.htm")] if os.path.isdir(path) else []
        if any(p.startswith(".") and p not in (".", "..") or p == ".." for p in parts) or not self.inside(path) or \
                any(os.path.lexists(i) and not self.inside(i) for i in indexes):
            self.send_error(404)
            return None
        if path.lower().endswith(".md") and os.path.isfile(path) and \
                "raw" not in parse_qs(urlsplit(self.path).query, keep_blank_values=True):
            return self.markdown(path)
        return super().send_head()

    def guess_type(self, path):
        kind = super().guess_type(path)
        # Without a charset browsers read text as the locale's legacy encoding (EUC-KR, Shift_JIS, ...).
        if kind.startswith("text/") and kind != "text/html" or kind == "application/json":
            return f"{kind}; charset=utf-8"
        return kind

    def markdown(self, path):
        try:
            with open(path, "rb") as f:
                text = f.read().decode("utf-8", errors="replace")
        except OSError:
            self.send_error(404)
            return None
        name = html.escape(os.path.basename(path))
        body = (f'<!doctype html>\n<html><head><meta charset="utf-8">'
                f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{name}</title>'
                f'<style>{CSS}{MD_CSS}</style></head><body><nav><a href="./">./</a> '
                f'<a href="?raw">{name} 원문</a></nav><article>{md_html(text)}</article></body></html>').encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'")
        self.end_headers()
        return io.BytesIO(body)

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


# ---------------------------------------------------------------- markdown

FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t](.*))?$")
RULE = re.compile(r"^ {0,3}([-*_])(?:\s*\1){2,}\s*$")
ITEM = re.compile(r"^( {0,3})([-*+]|\d{1,9}[.)])(\s+|$)")
TABLE_SEP = re.compile(r"\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?")
SAFE_URL = re.compile(r"^(?:https?:|mailto:|[^:]*$)", re.I)
# Past these the text is shown escaped but unformatted: the inline patterns are quadratic on adversarial input
# and a deep nest would exhaust the stack.
MAX_SOURCE, MAX_INLINE, MAX_DEPTH = 1 << 20, 4096, 16


def md_html(text):
    if len(text) > MAX_SOURCE:
        return f"<pre>{html.escape(text)}</pre>"
    return blocks(text.replace("\r\n", "\n").replace("\t", "    ").split("\n"))


def is_table(lines, i):
    return "|" in lines[i] and i + 1 < len(lines) and "-" in lines[i + 1] and \
        bool(TABLE_SEP.fullmatch(lines[i + 1].strip())) and len(cells(lines[i + 1])) == len(cells(lines[i]))


def starts_block(line):
    return bool(FENCE.match(line) or HEADING.match(line) or RULE.match(line) or ITEM.match(line)
                or line.lstrip().startswith(">"))


def blocks(lines, tight=False, depth=0):
    if depth > MAX_DEPTH:
        return f"<p>{html.escape(chr(10).join(lines))}</p>"
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
        elif m := FENCE.match(line):
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith(m.group(1)):
                j += 1
            lang = line.strip()[len(m.group(1)):].strip().split(" ")[0]
            cls = f' class="language-{html.escape(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{html.escape(chr(10).join(lines[i + 1:j]))}</code></pre>")
            i = j + 1
        elif RULE.match(line):
            out.append("<hr>")
            i += 1
        elif m := HEADING.match(line):
            n, title = len(m.group(1)), (m.group(2) or "").strip()
            closed = title.rstrip("#")
            if closed != title and (not closed or closed[-1] in " \t"):
                title = closed.rstrip()
            out.append(f"<h{n}>{inline(title)}</h{n}>")
            i += 1
        elif line.lstrip().startswith(">"):
            j = i
            while j < len(lines) and lines[j].strip() and (lines[j].lstrip().startswith(">") or j > i):
                j += 1
            quoted = [re.sub(r"^\s*> ?", "", l) for l in lines[i:j]]
            out.append(f"<blockquote>{blocks(quoted, depth=depth + 1)}</blockquote>")
            i = j
        elif ITEM.match(line):
            i = listing(lines, i, out, depth)
        elif is_table(lines, i):
            i = table(lines, i, out)
        else:
            j = i + 1
            while j < len(lines) and lines[j].strip() and not starts_block(lines[j]) and not is_table(lines, j):
                j += 1
            para = inline("\n".join(l.lstrip() for l in lines[i:j]).rstrip())
            out.append(para if tight else f"<p>{para}</p>")
            i = j
    return "\n".join(out)


def listing(lines, i, out, depth):
    first = ITEM.match(lines[i])
    ordered = first.group(2)[0].isdigit()
    items, loose = [], False
    while i < len(lines) and (m := ITEM.match(lines[i])) and m.group(2)[0].isdigit() == ordered:
        width = len(m.group(0)) if m.group(3) else len(m.group(0)) + 1
        body, j = [lines[i][len(m.group(0)):]], i + 1
        while j < len(lines):
            l = lines[j]
            if l.strip() and (len(l) - len(l.lstrip())) >= width:
                body.append(l[width:])
            elif l.strip() and not starts_block(l) and body[-1].strip():
                body.append(l.strip())  # lazy continuation of the paragraph
            elif not l.strip() and j + 1 < len(lines) and (len(lines[j + 1]) - len(lines[j + 1].lstrip())) >= width \
                    and lines[j + 1].strip():
                body.append("")
                loose = True
            else:
                break
            j += 1
        items.append(body)
        if j + 1 < len(lines) and not lines[j].strip() and (n := ITEM.match(lines[j + 1])) and \
                n.group(2)[0].isdigit() == ordered:
            loose = True
            j += 1
        i = j
    tag = "ol" if ordered else "ul"
    start = int(first.group(2)[:-1]) if ordered else 1
    attr = f' start="{start}"' if start != 1 else ""
    lis = []
    for body in items:
        box = re.match(r"\[([ xX])\]\s+", body[0])
        if box:
            body = [body[0][box.end():]] + body[1:]
        check = f'<input type="checkbox" disabled{" checked" if box.group(1) != " " else ""}> ' if box else ""
        lis.append(f"<li>{check}{blocks(body, tight=not loose, depth=depth + 1)}</li>")
    out.append(f"<{tag}{attr}>{''.join(lis)}</{tag}>")
    return i


def cells(row):
    """Split a table row on pipes outside code spans and backslash escapes."""
    row = row.strip()
    row = row[1:] if row.startswith("|") else row
    row = row[:-1] if row.endswith("|") and not row.endswith("\\|") else row
    parts, cur, tick, k = [], "", 0, 0
    while k < len(row):
        ch = row[k]
        if ch == "\\" and k + 1 < len(row):
            cur += row[k:k + 2] if row[k + 1] != "|" or tick else "|"
            k += 2
            continue
        if ch == "`":
            n = len(row[k:]) - len(row[k:].lstrip("`"))
            tick = 0 if tick == n else (tick or n)
            cur += "`" * n
            k += n
            continue
        if ch == "|" and not tick:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
        k += 1
    return parts + [cur.strip()]


def table(lines, i, out):
    aligns = []
    for c in cells(lines[i + 1]):
        a = "center" if c.startswith(":") and c.endswith(":") else "right" if c.endswith(":") else \
            "left" if c.startswith(":") else ""
        aligns.append(f' style="text-align:{a}"' if a else "")
    head = cells(lines[i])
    rows, j = [], i + 2
    while j < len(lines) and lines[j].strip() and "|" in lines[j]:
        rows.append(cells(lines[j]))
        j += 1

    def tr(row, tag):
        row = (row + [""] * len(head))[:len(head)]
        return "<tr>" + "".join(f"<{tag}{aligns[k] if k < len(aligns) else ''}>{inline(c)}</{tag}>"
                                for k, c in enumerate(row)) + "</tr>"
    out.append(f'<div class="table"><table><thead>{tr(head, "th")}</thead>'
               f'<tbody>{"".join(tr(r, "td") for r in rows)}</tbody></table></div>')
    return j


def inline(text, outer=None):
    stash = [] if outer is None else outer

    def keep(fragment):
        stash.append(fragment)
        return f"\x00{len(stash) - 1}\x00"

    def href(url):
        url = url.strip().strip("<>")
        return html.escape(url) if SAFE_URL.match(url) else "#"

    if outer is None:
        if len(text) > MAX_INLINE:
            return html.escape(text)
        text = text.replace("\x00", "")
        # One pass so a backslash escapes a backtick outside a code span but stays literal inside one.
        text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|~<>])|(`+)(.+?)\2",
                      lambda m: keep(html.escape(m.group(1)) if m.group(1) else
                                     f"<code>{html.escape(m.group(3).strip())}</code>"), text, flags=re.S)
    url = r"\(((?:[^()\s]|\([^()\s]*\))+)(?:\s+\"[^\"]*\")?\)"
    text = re.sub(r"!\[([^\[\]]*)\]" + url,
                  lambda m: keep(f'<img src="{href(m.group(2))}" alt="{html.escape(m.group(1))}">'), text)
    text = re.sub(r"\[([^\[\]]+)\]" + url,
                  lambda m: keep(f'<a href="{href(m.group(2))}">{inline(m.group(1), stash)}</a>'), text)
    if outer is None:  # link text gets no autolinks: no links inside links
        text = re.sub(r"<(https?://[^>\s]+)>",
                      lambda m: keep(f'<a href="{href(m.group(1))}">{html.escape(m.group(1))}</a>'), text)
        text = re.sub(r"https?://[^\s<>\"']+[^\s<>\"'.,;:!?)\]]",
                      lambda m: keep(f'<a href="{href(m.group(0))}">{html.escape(m.group(0))}</a>'), text)
    text = html.escape(text)
    # A span never crosses the next delimiter, which keeps each pattern linear.
    text = re.sub(r"\*\*(?=\S)((?:(?!\*\*).)+?)(?<=\S)\*\*|__(?=\S)((?:(?!__).)+?)(?<=\S)__",
                  lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", text, flags=re.S)
    text = re.sub(r"(?<![\w*])\*(?=\S)([^*]+?)(?<=\S)\*(?!\*)|(?<!\w)_(?=\S)([^_]+?)(?<=\S)_(?!\w)",
                  lambda m: f"<em>{m.group(1) or m.group(2)}</em>", text)
    text = re.sub(r"~~(?=\S)((?:(?!~~).)+?)(?<=\S)~~", r"<del>\1</del>", text, flags=re.S)
    text = re.sub(r"(?<=  )\n|\\\n", "<br>\n", text)
    return resolve(text, stash)


def resolve(text, stash):
    while "\x00" in text:
        text = re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)
    return text


MD_CSS = """
nav{display:flex;gap:16px;margin:0 auto 16px;max-width:880px;font-size:13px;color:var(--mute)}
article{max-width:880px;margin:0 auto;font-size:15px;line-height:1.65;overflow-wrap:break-word}
article *{word-break:normal}
article h1{font-size:24px;margin:24px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}
article h2{font-size:19px;margin:28px 0 10px;padding-bottom:4px;border-bottom:1px solid var(--line)}
article h3{font-size:16px;margin:20px 0 8px}article h4,article h5,article h6{font-size:15px;margin:16px 0 6px}
article a{color:#0a66d8}@media (prefers-color-scheme:dark){article a{color:#6cb2ff}}
article code{font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--card);
border:1px solid var(--line);border-radius:4px;padding:0 4px}
article pre{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:12px;overflow-x:auto}
article pre code{border:0;padding:0;background:none}
article blockquote{margin:12px 0;padding:0 12px;border-left:3px solid var(--line);color:var(--mute)}
article hr{border:0;border-top:1px solid var(--line);margin:24px 0}
article img{max-width:100%}article ul,article ol{padding-left:24px}
.table{overflow-x:auto;margin:12px 0}
article table{border-collapse:collapse;font-size:14px}
article th,article td{border:1px solid var(--line);padding:6px 10px;vertical-align:top;min-width:64px}
article th{background:var(--card);text-align:left}
@media (max-width:480px){article{font-size:14px}article table{font-size:13px}}
"""


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
