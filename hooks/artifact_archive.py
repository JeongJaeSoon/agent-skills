#!/usr/bin/env python3
"""PostToolUse hook (Artifact tool) of the agent-skills plugin: keep a versioned copy of every published page in the
backup folder and rebuild its index.html. The folder is `artifact-backup.public_dir`, which a share server may
serve; a page marked <meta name="backup" content="no">, flagged by agent-guard, or published while agent-guard is
missing goes to `artifact-backup.private_dir` instead. Settings and paths: hooks/lib/artifact_links.py.

The hook tells the model which links to give the owner and queues the same pair for artifact_links_stop.py. A run
that is killed (the hook timeout) leaves an in-flight marker; the next run reports it in errors.log and backs that
publish up again, so a publish is not silently lost. Any other failure is logged and never blocks the tool result.
"""
import contextlib, datetime, fcntl, filecmp, html, json, os, re, shutil, subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import artifact_links as al, config

NO_BACKUP_RE = re.compile(r'<meta\s+name=["\']backup["\']\s+content=["\']no["\']', re.I)
SCAN_TIMEOUT = 20
RECOVER_BEFORE = 5  # seconds a publish may take and still leave room for one retry within the hook timeout
OTHER = '기타'
# Sub-heading inside a category, from title then cwd; no match means no sub-heading.
SUBCATEGORY_RULES = [
    ('설명서', re.compile(r'eli5|안내서|가이드|guide|쉽게 보기|읽기 지도|기동 절차|\bAPI$', re.I)),
    ('기획·사업', re.compile(r'사업|기획서|로드맵|roadmap', re.I)),
    ('화면·UI', re.compile(r'캡처|capture|시안|스크린샷|screens?\b|화면|전후|비교|툴팁|툴바|여백|토글|story|stepper|uikit|mock', re.I)),
    ('운영·검증', re.compile(r'verify|e2e|검증|장애|soak|부하|마무리 보드|배포|incident', re.I)),
    ('설계·결정', re.compile(r'설계|결정|아키텍처|architecture|구조|모델|카탈로그|catalog|재검토|검토|개정|관계|클래스도|조사|design', re.I)),
]
SUBCATEGORIES = [name for name, _ in SUBCATEGORY_RULES]


def category_rules():
    """[(name, compiled regex)] from `artifact-backup.categories`; a rule that is not a [name, regex] pair is skipped."""
    rules = []
    for item in al.settings().get('categories') or []:
        try:
            rules.append((str(item[0]), re.compile(str(item[1]), re.I)))
        except (IndexError, TypeError, KeyError, re.error):
            continue
    return rules


def subclassify(title, cwd):
    for hay in (title or '', (cwd or '').replace('/orca/workspaces/', '/')):
        for name, rx in SUBCATEGORY_RULES:
            if rx.search(hay):
                return name
    return None


def classify(title, cwd):
    # Orca worktree paths all contain "orca"; only the repo name after it says what the page is about.
    hay = f'{title or ""} {(cwd or "").replace("/orca/workspaces/", "/")}'
    for name, rx in category_rules():
        if rx.search(hay):
            return name
    return OTHER


def log_error(msg):
    try:
        os.makedirs(al.state_dir(), exist_ok=True)
        with open(os.path.join(al.state_dir(), 'errors.log'), 'a', encoding='utf-8') as f:
            f.write(f'{datetime.datetime.now().isoformat(timespec="seconds")} {msg}\n')
    except Exception:
        pass


def index_path(root):
    return al.public_index() if root == al.public_dir() else os.path.join(root, 'index.jsonl')


def read_rows(root):
    return al.read_rows(index_path(root))


@contextlib.contextmanager
def index_lock():
    """Serializes changes to the public index across hook runs (a rewrite must not drop a row appended meanwhile)."""
    os.makedirs(al.state_dir(), exist_ok=True)
    fd = os.open(os.path.join(al.state_dir(), 'index.lock'), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


@contextlib.contextmanager
def key_lock(key):
    path = os.path.join(al.state_dir(), 'locks')
    os.makedirs(path, exist_ok=True)
    fd = os.open(os.path.join(path, re.sub(r'[^A-Za-z0-9_-]', '_', key)[:100] + '.lock'), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def append_row(root, row, locked=False):
    path = index_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with contextlib.nullcontext() if locked or root != al.public_dir() else index_lock():
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')


def migrate_served_files():
    """Move the index and error log out of the served folder, where an older version of this hook kept them."""
    for name, dest in (('index.jsonl', al.public_index()), ('errors.log', os.path.join(al.state_dir(), 'errors.log'))):
        old = os.path.join(al.public_dir(), name)
        if not os.path.isfile(old):
            continue
        os.makedirs(al.state_dir(), exist_ok=True)
        if os.path.exists(dest):
            with open(old, encoding='utf-8') as src, open(dest, 'a', encoding='utf-8') as out:
                out.write(src.read())
            os.remove(old)
        else:
            shutil.move(old, dest)


def agent_guard():
    return shutil.which('agent-guard')


def snapshot(path, stage, n):
    """Copy one source into the owner-only staging folder. What is scanned and what is published is this copy, so a
    source edited while the scan runs cannot reach the public folder unscanned."""
    os.makedirs(os.path.join(stage, str(n)))
    snap = os.path.join(stage, str(n), os.path.basename(path))
    shutil.copy2(path, snap)
    return snap


def exclusion_reason(stage, files):
    """Why this page must stay off the public share, or None. `files` are the snapshots of every local file of the
    publish; the staging folder is scanned once, so the time spent does not grow with their number."""
    for path in files:
        with open(path, encoding='utf-8', errors='replace') as f:
            if NO_BACKUP_RE.search(f.read()):
                return 'meta backup=no'
    guard = agent_guard()
    if not guard:
        log_error('agent-guard not found; kept private')
        return 'agent-guard not found'
    try:
        r = subprocess.run([guard, 'scan-path', stage], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=SCAN_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 'agent-guard scan timed out'
    # Non-zero covers both a finding and a scanner failure; either way keep it private.
    return f'agent-guard exit {r.returncode}' if r.returncode else None


def private_folder(path):
    for d in (al.private_dir(), path):
        os.makedirs(d, mode=0o700, exist_ok=True)
        os.chmod(d, 0o700)


def lock_down(path):
    """Owner-only modes for a private copy (a file, or a moved supporting-files tree)."""
    if os.path.isdir(path):
        os.chmod(path, 0o700)
        for name in os.listdir(path):
            lock_down(os.path.join(path, name))
    else:
        os.chmod(path, 0o600)


def move_into(src, dst):
    """Move a file or a tree to dst, merging into a directory a killed earlier move left part-way (shutil.move would
    nest the source inside it)."""
    if os.path.isdir(src) and os.path.isdir(dst):
        for name in os.listdir(src):
            move_into(os.path.join(src, name), os.path.join(dst, name))
        os.rmdir(src)
    else:
        shutil.move(src, dst)


def make_private(key):
    """Move a key's public versions and rows to the private store (a page that once leaked stays private). The
    caller holds the key's lock; the global index lock covers only the short rewrite of the public index, never the
    file moves, which can be a full copy across filesystems."""
    mine = [r for r in read_rows(al.public_dir()) if r.get('key') == key]
    if not mine:
        return
    folder = mine[-1]['dir']
    target = os.path.join(al.private_dir(), os.path.basename(folder))
    private_folder(target)
    # The private rows come first: a run killed part-way still leaves the key private, and the next run finishes
    # the move (save_version calls this again for a private key that has public rows left).
    have = {r.get('copy') for r in read_rows(al.private_dir())}
    for r in mine:
        r['dir'] = target
        r['copy'] = os.path.join(target, os.path.basename(r['copy']))
        if r['copy'] not in have:
            append_row(al.private_dir(), r)
    for name in os.listdir(folder) if os.path.isdir(folder) else []:
        move_into(os.path.join(folder, name), os.path.join(target, name))
        lock_down(os.path.join(target, name))
    if os.path.isdir(folder):
        os.rmdir(folder)
    with index_lock():
        keep = [r for r in read_rows(al.public_dir()) if r.get('key') != key]
        fd, tmp = tempfile.mkstemp(dir=al.state_dir(), prefix='.tmp-')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + '\n' for r in keep)
        os.replace(tmp, al.public_index())


PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Artifact 백업</title>
<style>
:root{{--bg:#fafaf9;--fg:#1c1917;--muted:#78716c;--line:#e7e5e4;--link:#1d4ed8;--tag:#f5f5f4}}
@media (prefers-color-scheme: dark){{:root{{--bg:#1c1917;--fg:#f5f5f4;--muted:#a8a29e;--line:#44403c;--link:#93c5fd;--tag:#292524}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Segoe UI",system-ui,sans-serif}}
main{{max-width:760px;margin:0 auto;padding:24px 16px 48px}}
h1{{font-size:20px;margin:0 0 4px}}
.sub{{color:var(--muted);font-size:13px;margin:0 0 20px}}
h2{{font-size:16px;margin:28px 0 4px;display:flex;gap:8px;align-items:baseline}}
h2 small,h3 small{{color:var(--muted);font-weight:400;font-size:13px}}
h3{{font-size:14px;margin:16px 0 2px;display:flex;gap:8px;align-items:baseline;color:var(--muted)}}
nav{{display:flex;flex-wrap:wrap;gap:6px 12px;font-size:13px;margin:0 0 4px}}
ul{{list-style:none;margin:0;padding:0}}
li{{padding:12px 0;border-top:1px solid var(--line)}}
.t{{font-weight:600;overflow-wrap:anywhere}}
.m{{color:var(--muted);font-size:13px;margin-top:2px;display:flex;flex-wrap:wrap;gap:4px 12px}}
a{{color:var(--link);text-decoration:none}} a:hover{{text-decoration:underline}}
.tag{{background:var(--tag);border:1px solid var(--line);border-radius:4px;padding:0 6px;font-size:12px}}
</style></head><body><main>
<h1>Artifact 백업</h1>
<p class="sub">{count}개 · 분류별, 분류 안은 최신 게시순 · {generated} 갱신</p>
<nav>{nav}</nav>
{sections}
</main></body></html>
"""


def write_index():
    # Read, render and replace under one lock: a render made before a page went private must not land after it.
    with index_lock():
        render_index()


def render_index():
    public = al.public_dir()
    private = al.private_dir()
    latest, versions, category, subcategory = {}, {}, {}, {}
    for root in (public, private):
        for row in read_rows(root):
            if row.get('key'):
                row['_private'] = root == private
                latest[row['key']] = row
                versions[row['key']] = versions.get(row['key'], 0) + 1
                if row.get('category'):
                    category[row['key']] = row['category']
                if row.get('subcategory'):
                    subcategory[row['key']] = row['subcategory']
    names = [name for name, _ in category_rules()] + [OTHER]
    groups = {}
    for key, row in latest.items():
        cat = category.get(key) or classify(row.get('title'), row.get('cwd'))
        groups.setdefault(cat if cat in names or cat in category.values() else OTHER, []).append(row)
    order = names + sorted(c for c in groups if c not in names)
    sections, nav = [], []
    for n, cat in enumerate(c for c in order if c in groups):
        rows = sorted(groups[cat], key=lambda r: r.get('time') or '', reverse=True)
        nav.append(f'<a href="#c{n}">{html.escape(cat)} {len(rows)}</a>')
        parts = [f'<h2 id="c{n}">{html.escape(cat)}<small>{len(rows)}개</small></h2>']
        for sub in [None] + SUBCATEGORIES:
            subrows = [r for r in rows if subcategory.get(r['key']) == sub
                       or (sub is None and subcategory.get(r['key']) not in SUBCATEGORIES)]
            if not subrows:
                continue
            if sub:
                parts.append(f'<h3>{html.escape(sub)}<small>{len(subrows)}개</small></h3>')
            parts.append('<ul>\n' + '\n'.join(index_item(r, versions, public) for r in subrows) + '\n</ul>')
        sections.append('\n'.join(parts))
    page = PAGE.format(count=len(latest), generated=f'{datetime.datetime.now():%Y-%m-%d %H:%M}',
                       nav=''.join(nav), sections='\n'.join(sections))
    os.makedirs(public, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=public, prefix='.tmp-', suffix='.html')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(page)
    os.chmod(tmp, 0o644)
    os.replace(tmp, os.path.join(public, 'index.html'))


def index_item(row, versions, public):
    # A private page's title may be what the secret scan caught: the served index names it only by its key.
    title = html.escape(f'비공개 {row["key"][:8]}' if row['_private']
                        else row.get('title') or os.path.basename(row.get('dir') or ''))
    links = []
    if row.get('url') and not row['_private']:
        links.append(f'<a href="{html.escape(row["url"])}">claude.ai</a>')
    if row['_private']:
        links.append('<span class="tag">백업 비공개</span>')
    else:
        rel = os.path.relpath(row['copy'], public)
        links.append(f'<a href="{html.escape(rel)}">최신 사본</a>')
        links.append(f'버전 {versions[row["key"]]}개')
    when = html.escape((row.get('time') or '').replace('T', ' '))
    return (f'<li><div class="t">{title}</div><div class="m"><span>{when}</span>'
            + ''.join(f'<span>{l}</span>' for l in links) + '</div></li>')


def supporting_files(inp, cwd):
    """(published path, local source) pairs from the publish input's `files`, skipping server-side copies. `files` is a
    map {published: source} or a list of paths (or of {path} objects) that are served where they lie."""
    base = os.path.join(cwd or os.getcwd(), inp.get('root') or '')
    files = inp.get('files') or {}
    pairs = []
    if isinstance(files, list):
        for f in files:
            path = f.get('path') if isinstance(f, dict) else f
            pairs.append((path, path))
    elif isinstance(files, dict):
        for pub, src in files.items():
            if isinstance(src, dict):
                src = src.get('from')
            pairs.append((pub, src))
    out = []
    for pub, src in pairs:
        if not isinstance(pub, str) or not isinstance(src, str):
            continue
        norm = os.path.normpath(pub)
        if os.path.isabs(norm) or norm.startswith('..'):
            continue
        src = src if os.path.isabs(src) else os.path.join(base, src)
        if os.path.isfile(src):
            out.append((norm, src))
    return out


def state_path(session_id):
    return os.path.join(al.state_dir(), 'links', re.sub(r'[^A-Za-z0-9_-]', '_', session_id) + '.json')


def record_pending(session_id, entry):
    path = state_path(session_id)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    lock = os.open(path + '.lock', os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with open(path, encoding='utf-8') as f:
                pending = json.load(f).get('pending') or []
        except (OSError, ValueError):
            pending = []
        pending = [p for p in pending if p.get('key') != entry['key']] + [entry]
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.tmp-')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'pending': pending}, f, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        os.close(lock)


def announce(data, key, url, dest, private):
    """Hook output naming the links the model must give, and the same pair queued for the Stop check."""
    # The exact version this publish wrote, not the folder listing.
    backup = None if private else al.backup_url(dest)
    if not url and not backup:
        return None
    lines = [f'claude.ai link: {url}' if url else 'claude.ai link: not found in the tool result']
    if backup:
        lines.append(f'backup link: {backup}')
    elif private:
        lines.append('backup link: none - this page was kept private (secret scan, backup=no or no agent-guard); '
                     'do not give a public backup link for it')
    else:
        lines.append('backup link: none - artifact-backup.base_url is not set in ~/.claude/agent-skills.json, so a '
                     'saved copy has no public URL')
        log_error('artifact-backup.base_url unset; no backup link announced')
    to = 'the caller in your final report' if data.get('agent_id') else 'the owner in your reply'
    if data.get('session_id'):
        try:
            record_pending(data['session_id'], {'key': key, 'url': url, 'backup': backup,
                                                'time': datetime.datetime.now().isoformat(timespec='seconds'),
                                                'epoch': time.time()})
        except Exception as e:
            log_error(f'link state: {type(e).__name__}: {e}')
    return {'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext':
            f'Artifact backed up. Give {to} these links verbatim:\n' + '\n'.join(lines)}}


def primary_page(extras):
    """The page of a publish with no file_path: its index.html, else the first .html among the published files."""
    for want in (lambda p: os.path.basename(p) == 'index.html', lambda p: p.endswith('.html')):
        hit = next((e for e in extras if want(e[0])), None)
        if hit:
            return hit
    return None


def archive(data):
    inp = data.get('tool_input') or {}
    action = inp.get('action') or 'publish'
    src = inp.get('file_path')
    # Asset uploads carry no page of ours to keep.
    if action != 'publish' or inp.get('asset'):
        return None
    extras = supporting_files(inp, data.get('cwd'))
    if not src or not os.path.isfile(src):
        # A publish through `files` alone: its page is one of the files.
        page = primary_page(extras)
        if not page:
            return None
        src = page[1]
        extras = [e for e in extras if e is not page]
    resp = json.dumps(data.get('tool_response'), ensure_ascii=False)
    m = al.URL_RE.search(resp) or al.URL_RE.search(inp.get('url') or '')
    url = m.group(0) if m else None

    private_folder(al.private_dir())
    stage = tempfile.mkdtemp(dir=al.private_dir(), prefix='.scan-')
    try:
        return store(data, url, src, extras, stage)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def store(data, url, orig, orig_extras, stage):
    # Everything below reads the snapshots, never the sources again.
    src = snapshot(orig, stage, 0)
    extras = [(pub, snapshot(f, stage, n)) for n, (pub, f) in enumerate(orig_extras, 1)]
    now = datetime.datetime.now()
    # One folder per artifact (keyed by URL id when known), one file per publish.
    key = url.rsplit('/', 1)[1] if url else os.path.splitext(os.path.basename(orig))[0]
    slug = re.sub(r'[^A-Za-z0-9._-]+', '-', os.path.splitext(os.path.basename(orig))[0])[:60]
    # One run at a time per artifact, from the private check to the index row: a run that read "public" must not
    # publish after another run moved the page to the private store.
    with key_lock(key):
        return save_version(data, url, orig, key, slug, now, src, extras)


def save_version(data, url, orig, key, slug, now, src, extras):
    stage = os.path.dirname(os.path.dirname(src))
    # A retry of a killed run, checked under the key's lock: a later publish already stands as the newest version
    # and as the links it announced, so the retry must not become newer than it.
    if data.get('_restore_at') is not None and newer_version(key, data['_restore_at']):
        log_error(f'killed publish of {key[:8]} was superseded by a newer version; not restored')
        return None
    private = any(r.get('key') == key for r in read_rows(al.private_dir()))
    if not private and exclusion_reason(stage, [src] + [f for _, f in extras]):
        make_private(key)
        private = True
    elif private:
        make_private(key)  # finishes a move that was killed part-way; a no-op when no public row is left
    root = al.private_dir() if private else al.public_dir()
    last = None
    for row in read_rows(root):
        if row.get('key') == key:
            last = row
    folder = (last or {}).get('dir') or os.path.join(root, f'{now:%Y-%m-%d}-{slug}-{key[:8]}')
    if private:
        private_folder(folder)
    os.makedirs(folder, exist_ok=True)
    # The same content again (the old settings.json hook and the plugin both run, or a republish changed nothing)
    # reuses the newest version, so every run announces the same link.
    same = (last and os.path.isfile(last.get('copy') or '') and filecmp.cmp(src, last['copy'], shallow=False)
            and all(os.path.isfile(os.path.join(folder, pub)) and filecmp.cmp(f, os.path.join(folder, pub), shallow=False)
                    for pub, f in extras))
    if same:
        out = announce(data, key, url, last['copy'], private)
        refresh_index()  # a page that just went private must leave the served index too
        return out
    stem = f'{now:%Y%m%d-%H%M%S}'
    ext = os.path.splitext(orig)[1] or '.html'
    dest, n = os.path.join(folder, stem + ext), 1
    while True:  # two publishes in one second keep both versions; O_EXCL makes the name ours
        try:
            os.close(os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            break
        except FileExistsError:
            n += 1
            dest = os.path.join(folder, f'{stem}-{n}{ext}')
    shutil.copy2(src, dest)
    os.chmod(dest, 0o600 if private else 0o644)
    # Supporting files land at their published paths so the copy's relative links resolve.
    for pub, extra in extras:
        target = os.path.join(folder, pub)
        os.makedirs(os.path.dirname(target), mode=0o700 if private else 0o777, exist_ok=True)
        shutil.copy2(extra, target)
        os.chmod(target, 0o600 if private else 0o644)

    with open(src, encoding='utf-8', errors='replace') as f:
        text = f.read(200_000)
    t = re.search(r'<title>(.*?)</title>', text[:8192], re.S | re.I)
    title = t.group(1).strip() if t else None
    earlier = read_rows(al.public_dir()) + read_rows(al.private_dir())
    # A category set on an earlier version (possibly corrected by hand) sticks to the key.
    category = next((r['category'] for r in reversed(earlier) if r.get('key') == key and r.get('category')), None)
    category = category or classify(title, data.get('cwd'))
    subcategory = next((r['subcategory'] for r in reversed(earlier) if r.get('key') == key and r.get('subcategory')), None)
    subcategory = subcategory or subclassify(title, data.get('cwd'))
    append_row(root, {'time': now.isoformat(timespec='seconds'), 'epoch': now.timestamp(), 'key': key, 'url': url, 'title': title,
                      'source': os.path.abspath(orig), 'copy': dest, 'dir': folder,
                      'session': data.get('session_id'), 'cwd': data.get('cwd'), 'category': category,
                      'subcategory': subcategory})
    out = announce(data, key, url, dest, private)
    refresh_index()
    return out


def refresh_index():
    try:
        write_index()
    except Exception as e:  # the links announced matter more than the index page
        log_error(f'index: {type(e).__name__}: {e}')


def inflight_dir():
    return os.path.join(al.state_dir(), 'inflight')


def write_marker(path, mark):
    """Whole or not at all: a reader never sees a half-written marker."""
    fd, tmp = tempfile.mkstemp(dir=inflight_dir(), prefix='.tmp-')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(mark, f, ensure_ascii=False)
    os.replace(tmp, path)


def start_marker(data):
    os.makedirs(inflight_dir(), exist_ok=True)
    path = os.path.join(inflight_dir(), f'{os.getpid()}-{time.time_ns()}.json')
    write_marker(path, {'pid': os.getpid(), 'at': time.time(), 'data': data})
    return path


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def newer_version(key, at):
    for root in (al.public_dir(), al.private_dir()):
        for r in read_rows(root):
            try:
                when = r.get('epoch') or datetime.datetime.fromisoformat(r.get('time')).timestamp()
            except (TypeError, ValueError):
                continue
            if r.get('key') == key and when >= at:
                return True
    return False


def recover_killed(limit=1):
    """A marker whose process is gone belongs to a run that was killed before it finished: say so, and back that
    publish up again once (the file may have changed since, which is better than no copy)."""
    try:
        names = sorted(os.listdir(inflight_dir()))
    except OSError:
        return
    for name in names:
        if name.startswith('.'):
            continue  # a marker being written
        path = os.path.join(inflight_dir(), name)
        try:
            with open(path, encoding='utf-8') as f:
                mark = json.load(f)
        except (OSError, ValueError):
            mark = None
        if mark is not None and alive(mark.get('pid', 0)):
            continue
        if not mark or mark.get('tries', 0) >= 2:
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        # The marker stays until the retry finishes, owned by this process meanwhile: a retry that is killed
        # too is found again, up to twice.
        mark.update(pid=os.getpid(), tries=mark.get('tries', 0) + 1)
        write_marker(path, mark)
        src = (mark['data'].get('tool_input') or {}).get('file_path')
        log_error(f'previous run was killed before it finished ({src}); backing it up again')
        try:
            archive(dict(mark['data'], agent_id=None, session_id=None, _restore_at=mark.get('at', 0)))
            os.remove(path)
        except Exception as e:
            log_error(f'recovery failed: {type(e).__name__}: {e}')
        limit -= 1
        if limit <= 0:
            return


def main():
    data = json.load(sys.stdin)
    if (data.get('tool_input') or {}).get('action', 'publish') != 'publish':
        return
    migrate_served_files()
    began = time.monotonic()
    marker = start_marker(data)
    try:
        out = archive(data)
    finally:
        try:
            os.remove(marker)
        except OSError:
            pass
    if out:
        print(json.dumps(out, ensure_ascii=False), flush=True)
    # This publish comes first. A killed run's retry may be a slow scan, so it runs only when this one was quick,
    # and the links above are already out.
    if time.monotonic() - began < RECOVER_BEFORE:
        recover_killed(limit=1)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:  # never block the tool result on archiving
        log_error(f'{type(e).__name__}: {e}')
        config.log_error('artifact_archive', e)
    sys.exit(0)
