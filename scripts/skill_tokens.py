#!/usr/bin/env python3
"""Static token cost of this plugin: what it puts into a session's context, without running a session (docs/token-usage.md).

Usage:
  python3 scripts/skill_tokens.py static [--json] [--top N] [--listing-budget N] [--base REF | --baseline FILE]
                                         [--exact [--model M]] [--root DIR]
  python3 scripts/skill_tokens.py calibrate [--model M]

static measures the checkout this script lives in (or --root DIR):
  always    the skill listing line of each SKILL.md and the agent listing line of each agents/*.md, every turn
  hooks     each hook in hooks/hooks.json, its events and when it injects text (hooks are not run; the size of what
            they inject comes from transcripts)
  invoke    each SKILL.md body plus the references/*.md it points at (one level), when the skill is loaded
  repo      CLAUDE.md and AGENTS.md, read only by sessions working in this repository
  dups      sentences of 60+ characters repeated in two or more files
--base REF measures where HEAD branched from REF (`git merge-base`, from `git archive`, no checkout) and prints the
difference; --baseline FILE compares with an earlier --json output.

Token counts are an offline approximation by character class (APPROX_PER_CHAR) unless --exact, which calls the
count_tokens API with ANTHROPIC_API_KEY (never printed or stored) and fails without it. calibrate fits new coefficients
from that API on this checkout's files and prints them; it changes nothing. No other path calls any model.
"""
import json, os, pathlib, re, subprocess, sys, tarfile, tempfile, unicodedata, urllib.error, urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
APPROX_VERSION = "chars-v1"
# Tokens per character, by class. Uncalibrated until `calibrate` is run with an API key (docs/token-usage.md §1.2).
APPROX_PER_CHAR = {"alnum": 0.25, "punct": 0.5, "space": 0.1, "hangul": 0.9, "cjk": 1.2, "other": 1.0}
DUP_MIN_CHARS = 60
COUNT_URL = "https://api.anthropic.com/v1/messages/count_tokens"
DEFAULT_MODEL = "claude-opus-5-5"
REF_RE = re.compile(r"(?<![\w/.-])((?:\.\./)*(?:(?:skills/)?([\w-]+)/)?references/[\w./-]+?\.md)\b")
REFUSAL_ONLY = "no additionalContext; only its reason when it blocks or denies"

# When each hook in hooks.json puts text into the context. Hooks are not run here: what they inject depends on
# session state, so its size comes from transcripts (docs/token-usage.md §2). A hook missing here fails the test.
HOOKS = {
    "onboard.py": "once per profile, until setup is applied or declined",
    "reorient.py": "sessions with a declared orchestrator or lead role: each prompt, and session start",
    "decision.py": "Orca terminals with an open dashboard decision they registered: each prompt",
    "request_log.py": "role sessions: each human prompt",
    "prose_check.py": "a commit, PR or tracker write whose text breaks write-plainly",
    "guard.py": REFUSAL_ONLY,
    "orchestrator_guard.py": REFUSAL_ONLY,
    "pressure_gate.py": REFUSAL_ONLY,
    "permission.py": REFUSAL_ONLY,
    "claim_check.py": REFUSAL_ONLY,
}


def char_class(c):
    o = ord(c)
    if o < 128:
        if c.isalnum():
            return "alnum"
        return "space" if c.isspace() else "punct"
    if 0xAC00 <= o <= 0xD7A3 or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F:
        return "hangul"
    if o >= 0x2E80 and unicodedata.east_asian_width(c) in ("W", "F"):
        return "cjk"
    return "other"


def class_counts(text):
    n = dict.fromkeys(APPROX_PER_CHAR, 0)
    for c in text:
        n[char_class(c)] += 1
    return n


def approx_tokens(text, coef=None):
    coef = coef or APPROX_PER_CHAR
    return round(sum(coef[k] * v for k, v in class_counts(text).items()))


def display_width(text):
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def api_counter(model):
    """count(text) → tokens from the count_tokens API, minus the fixed overhead of a one-message request."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise SystemExit("--exact needs ANTHROPIC_API_KEY in the environment")

    def raw(text):
        body = json.dumps({"model": model, "messages": [{"role": "user", "content": text}]}).encode()
        req = urllib.request.Request(COUNT_URL, data=body, method="POST", headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)["input_tokens"]
        except urllib.error.HTTPError as e:
            raise SystemExit(f"count_tokens failed: HTTP {e.code}") from None
        except urllib.error.URLError as e:
            raise SystemExit(f"count_tokens failed: {e.reason}") from None

    overhead = raw(".") - 1
    cache = {}

    def count(text):
        if not text.strip():
            return 0
        if text not in cache:
            cache[text] = max(raw(text) - overhead, 0)
        return cache[text]
    return count


# ---- reading the plugin ----

def split_frontmatter(text):
    m = re.match(r"---\r?\n(.*?)\r?\n---\r?\n?", text, re.S)
    if not m:
        return {}, text
    fm = {}
    for line in m.group(1).splitlines():
        k = re.match(r"([\w-]+):\s*(.*)$", line)
        if k:
            fm[k.group(1)] = unquote(k.group(2).strip())
    return fm, text[m.end():]


def unquote(v):
    if v.startswith('"') and v.endswith('"') and len(v) >= 2:
        try:
            return json.loads(v)
        except ValueError:
            return v[1:-1]
    if v.startswith("'") and v.endswith("'") and len(v) >= 2:
        return v[1:-1].replace("''", "'")
    return v


def plugin_name(root):
    try:
        return json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError):
        return root.name


def skill_listing_line(plugin, name, fm):
    if str(fm.get("disable-model-invocation", "")).lower() == "true":
        return f"- {plugin}:{name}"
    return f"- {plugin}:{name}: {fm.get('description', '')}"


def agent_listing_line(plugin, name, fm):
    tools = fm.get("tools") or "All tools"
    return f"- {plugin}:{name}: {fm.get('description', '')} (Tools: {tools})"


def references(root, skill_dir, body):
    """[(path relative to root, cross)] for each references/*.md the body names, own skill first."""
    out, seen = [], set()
    for m in REF_RE.finditer(body):
        rel, named = m.group(1), m.group(2)
        candidates = []
        if named and (root / "skills" / named).is_dir():
            candidates.append(root / "skills" / named / rel[rel.index("references/"):])
        candidates.append((skill_dir / rel).resolve())
        candidates.append(skill_dir / rel[rel.index("references/"):])
        hit = next((c for c in candidates if c.is_file()), None)
        if hit is None:
            others = [p for p in (root / "skills").glob("*/" + rel[rel.index("references/"):]) if p.is_file()]
            hit = others[0] if len(others) == 1 else None
        if hit is None:
            continue
        hit = hit.resolve()
        try:
            key = hit.relative_to(root.resolve()).as_posix()
        except ValueError:  # a symlink out of the checkout: not this plugin's text
            print(f"warning: {rel} in {skill_dir.name} resolves outside {root} ({hit}): not counted", file=sys.stderr)
            continue
        if key not in seen:
            seen.add(key)
            out.append((key, skill_dir.resolve() not in hit.parents))
    return out


def hook_scripts(root):
    try:
        cfg = json.loads((root / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    found = {}
    for event, groups in cfg.get("hooks", {}).items():
        for g in groups:
            for h in g.get("hooks", []):
                m = re.search(r"hooks/([\w-]+\.py)", h.get("command", ""))
                if m:
                    found.setdefault(m.group(1), []).append(event)
    return found


def sentences(body):
    for line in body.splitlines():
        line = re.sub(r"^\s*(?:[-*>]|\d+\.|\|)\s*", "", line)
        for s in re.split(r"(?<=[.!?。])\s+", line):
            norm = re.sub(r"\s+", " ", re.sub(r"[*_`]", "", s)).strip().lower()
            if len(norm) >= DUP_MIN_CHARS:
                yield norm


def measure(root, count=None):
    root = pathlib.Path(root)
    count = count or approx_tokens
    plugin = plugin_name(root)
    skills, agents, invoke, texts = [], [], [], {}
    for p in sorted((root / "skills").glob("*/SKILL.md")):
        text = p.read_text(encoding="utf-8", errors="replace")
        fm, body = split_frontmatter(text)
        name = fm.get("name") or p.parent.name
        line = skill_listing_line(plugin, name, fm)
        skills.append({"name": name, "tokens": count(line), "width": display_width(line)})
        refs = []
        for rel, cross in references(root, p.parent, body):
            refs.append({"path": rel, "tokens": count((root / rel).read_text(encoding="utf-8", errors="replace")), "cross": cross})
            texts[rel] = split_frontmatter((root / rel).read_text(encoding="utf-8", errors="replace"))[1]
        # A named reference is often read for one section or not at all, so refs are an upper bound kept apart from the body.
        invoke.append({"skill": name, "tokens": count(text), "refs": refs, "refs_tokens": sum(r["tokens"] for r in refs)})
        texts[p.relative_to(root).as_posix()] = body
    for p in sorted((root / "agents").glob("*.md")):
        text = p.read_text(encoding="utf-8", errors="replace")
        fm, body = split_frontmatter(text)
        name = fm.get("name") or p.stem
        line = agent_listing_line(plugin, name, fm)
        agents.append({"name": name, "tokens": count(line), "width": display_width(line), "body_tokens": count(body)})
        texts[p.relative_to(root).as_posix()] = body
    hooks = [{"name": script, "events": sorted(set(events)), "when": HOOKS.get(script, "unknown: not in skill_tokens.HOOKS")}
             for script, events in sorted(hook_scripts(root).items())]
    repo = [{"path": n, "tokens": count((root / n).read_text(encoding="utf-8", errors="replace"))}
            for n in ("CLAUDE.md", "AGENTS.md") if (root / n).is_file()]
    where = {}
    for path, body in texts.items():
        for s in set(sentences(body)):
            where.setdefault(s, []).append(path)
    dups = sorted(({"text": s, "files": sorted(f), "tokens": count(s), "save": count(s) * (len(f) - 1)}
                   for s, f in where.items() if len(f) > 1), key=lambda d: (-d["save"], d["text"]))
    totals = {"always_tokens": sum(x["tokens"] for x in skills + agents),
              "always_width": sum(x["width"] for x in skills + agents),
              "skills_width": sum(x["width"] for x in skills),
              "invoke_tokens": sum(x["tokens"] for x in invoke),
              "refs_tokens": sum({r["path"]: r["tokens"] for x in invoke for r in x["refs"]}.values()),
              "repo_tokens": sum(x["tokens"] for x in repo),
              "dup_save_tokens": sum(d["save"] for d in dups)}
    return {"version": 1, "approx": APPROX_VERSION if count is approx_tokens else "exact", "plugin": plugin,
            "totals": totals, "skills": skills, "agents": agents, "hooks": hooks, "invoke": invoke, "repo": repo,
            "dups": dups}


def merge_base(ref, repo=REPO):
    """The commit HEAD branched from ref. Comparing with ref itself would count what ref gained since as this
    branch's change; ref is returned as is when HEAD contains it or there is no common commit."""
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True).stdout.strip()
    point = git("merge-base", ref, "HEAD")
    return point if point and point != git("rev-parse", "--verify", "-q", ref + "^{commit}") else ref


def measure_ref(ref, count=None, repo=REPO):
    """measure() of a git ref, extracted from `git archive` into a temporary directory."""
    paths = ["skills", "agents", "hooks", "scripts", ".claude-plugin", "CLAUDE.md", "AGENTS.md"]
    ls = subprocess.run(["git", "-C", str(repo), "ls-tree", "--name-only", ref, "--"] + paths,
                        capture_output=True, text=True)
    if ls.returncode:
        raise SystemExit(f"git ls-tree {ref}: {ls.stderr.strip()}")
    have = ls.stdout.split()
    with tempfile.TemporaryDirectory(prefix="skill-tokens-base-") as tmp:
        arc = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", ref, "--"] + have, capture_output=True)
        if arc.returncode:
            raise SystemExit(f"git archive {ref}: {arc.stderr.decode(errors='replace').strip()}")
        tar_path = pathlib.Path(tmp) / "base.tar"
        tar_path.write_bytes(arc.stdout)
        root = pathlib.Path(tmp) / "tree"
        with tarfile.open(tar_path) as tf:
            if hasattr(tarfile, "data_filter"):
                tf.extractall(root, filter="data")
            else:
                tf.extractall(root)
        return measure(root, count)


# ---- output ----

def fmt(n):
    return f"{n:,}"


def signed(n):
    return f"{n:+,}"


def render(rep, top, budget=None):
    t = rep["totals"]
    out = [f"approximation: {rep['approx']} (relative comparison; --exact for API counts)", "",
           "ALWAYS (every turn; an upper bound: over the listing budget, descriptions are dropped)",
           "                                   tokens   width"]
    share = f"   {100 * t['skills_width'] / budget:.0f}% of listing budget {fmt(budget)}" if budget else ""
    out.append(f"  skill listing, {len(rep['skills'])} skills    {fmt(sum(s['tokens'] for s in rep['skills'])):>9} "
               f"{fmt(t['skills_width']):>7}{share}")
    out.append(f"  agent listing, {len(rep['agents'])} agents    {fmt(sum(a['tokens'] for a in rep['agents'])):>9} "
               f"{fmt(sum(a['width'] for a in rep['agents'])):>7}")
    out.append(f"  longest descriptions: " + ", ".join(
        f"{s['name']} {s['width']}" for s in sorted(rep["skills"], key=lambda s: -s["width"])[:top]))
    out += ["", "HOOKS (inject only when the condition holds; sizes come from transcripts)"]
    for h in rep["hooks"]:
        out.append(f"  {h['name']:<22} {'/'.join(h['events']):<34} {h['when']}")
    out += ["", f"ON INVOKE, top {top}       SKILL.md   named refs (if all read)   refs from other skills"]
    for x in sorted(rep["invoke"], key=lambda x: -x["tokens"])[:top]:
        out.append(f"  {x['skill']:<22} {fmt(x['tokens']):>8}   {fmt(x['refs_tokens']):>8} in {len(x['refs']):>2}"
                   f"{'':>14}{sum(r['cross'] for r in x['refs']):>2}")
    out.append(f"  all {len(rep['invoke'])} skills          {fmt(t['invoke_tokens']):>8}   "
               f"{fmt(t.get('refs_tokens', 0)):>8} (each file once)")
    if rep["repo"]:
        out += ["", "REPO-LOCAL (sessions working in this repository only)"]
        out += [f"  {r['path']:<26} {fmt(r['tokens']):>9}" for r in rep["repo"]]
    out += ["", f"DUPLICATES ({DUP_MIN_CHARS}+ chars, 2+ files), top {top}   saving if kept once: "
                f"{fmt(t['dup_save_tokens'])}"]
    for d in rep["dups"][:top]:
        text = d["text"] if len(d["text"]) <= 70 else d["text"][:67] + "..."
        out.append(f"  {d['save']:>5}  x{len(d['files'])}  \"{text}\"")
        out.append(f"         {', '.join(d['files'])}")
    return "\n".join(out)


def diff(old, new, top):
    """{totals: {key: delta}, items: [(kind, name, old, new)] by size of change, hooks: {added, removed}}."""
    totals = {k: new["totals"].get(k, 0) - old["totals"].get(k, 0) for k in new["totals"]}
    items = []

    def index(rep, kind):
        if kind == "refs":
            return {r["path"]: r["tokens"] for x in rep.get("invoke", []) for r in x.get("refs", [])}
        key = {"skills": "name", "agents": "name", "invoke": "skill", "repo": "path"}[kind]
        return {x[key]: x["tokens"] for x in rep.get(kind, [])}
    for kind in ("skills", "agents", "invoke", "refs", "repo"):
        a, b = index(old, kind), index(new, kind)
        for name in sorted(a.keys() | b.keys()):
            if a.get(name, 0) != b.get(name, 0):
                items.append((kind, name, a.get(name, 0), b.get(name, 0)))
    items.sort(key=lambda i: -abs(i[3] - i[2]))
    was, now = {h["name"] for h in old.get("hooks", [])}, {h["name"] for h in new.get("hooks", [])}
    return {"totals": totals, "items": items[:top] if top else items,
            "hooks": {"added": sorted(now - was), "removed": sorted(was - now)}}


def render_diff(d, label):
    t = d["totals"]
    out = [f"vs {label}: always {signed(t['always_tokens'])} tokens ({signed(t['always_width'])} width), "
           f"on invoke {signed(t['invoke_tokens'])}, named refs {signed(t.get('refs_tokens', 0))}, "
           f"repo-local {signed(t['repo_tokens'])}, duplicates {signed(t['dup_save_tokens'])}"]
    kinds = {"skills": "listing", "agents": "agent listing", "invoke": "on invoke", "refs": "reference",
             "repo": "repo-local"}
    for kind, name, a, b in d["items"]:
        out.append(f"  {kinds[kind]:<13} {name:<28} {fmt(a):>8} -> {fmt(b):>8}  ({signed(b - a)})")
    for verb in ("added", "removed"):
        if d["hooks"][verb]:
            out.append(f"  hooks {verb}: {', '.join(d['hooks'][verb])}")
    if not d["items"] and not any(d["hooks"].values()):
        out.append("  no change")
    return "\n".join(out)


# ---- calibration ----

def fit(rows):
    """Least-squares coefficients per class from [(class_counts, tokens)], classes never seen keep their default."""
    keys = [k for k in APPROX_PER_CHAR if any(c[k] for c, _ in rows)]
    n = len(keys)
    a = [[sum(c[i] * c[j] for c, _ in rows) for j in keys] + [sum(c[i] * y for c, y in rows)] for i in keys]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(a[r][col]))
        a[col], a[piv] = a[piv], a[col]
        if abs(a[col][col]) < 1e-12:
            continue
        for r in range(n):
            if r != col:
                f = a[r][col] / a[col][col]
                a[r] = [x - f * y for x, y in zip(a[r], a[col])]
    coef = dict(APPROX_PER_CHAR)
    for i, k in enumerate(keys):
        if abs(a[i][i]) >= 1e-12:
            coef[k] = round(max(a[i][n] / a[i][i], 0.0), 4)
    return coef


def error_stats(rows, coef):
    errs = sorted(abs(sum(coef[k] * v for k, v in c.items()) - y) / y for c, y in rows if y)
    if not errs:
        return {"median": 0.0, "max": 0.0}
    return {"median": round(100 * errs[len(errs) // 2], 1), "max": round(100 * errs[-1], 1)}


def calibrate(root, count):
    files = sorted([*root.glob("skills/*/SKILL.md"), *root.glob("skills/*/references/**/*.md"), *root.glob("agents/*.md")])
    rows = []
    for p in files:
        text = p.read_text(encoding="utf-8", errors="replace")
        rows.append((class_counts(text), count(text)))
    coef = fit(rows)
    return {"files": len(rows), "coefficients": coef, "error_now": error_stats(rows, APPROX_PER_CHAR),
            "error_fitted": error_stats(rows, coef)}


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]

    if hasattr(sys.stdout, "reconfigure"):  # Korean and CJK text prints under any locale
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    def opt(flag, default=None):
        if flag in args:
            i = args.index(flag)
            if i + 1 >= len(args):
                raise SystemExit(f"{flag} needs a value")
            return args[i + 1]
        return default

    def number(flag, default):
        try:
            return int(opt(flag, default))
        except ValueError:
            raise SystemExit(f"{flag} needs an integer\n\n{__doc__}") from None
    root = pathlib.Path(opt("--root", REPO)).resolve()
    model = opt("--model", DEFAULT_MODEL)
    count = api_counter(model) if "--exact" in args or cmd == "calibrate" else None
    if cmd == "calibrate":
        print(json.dumps(calibrate(root, count), indent=2))
        return 0
    if cmd != "static":
        print(__doc__, file=sys.stderr)
        return 2
    top = number("--top", "10")
    budget = number("--listing-budget", "0") or None
    rep = measure(root, count)
    base, baseline = opt("--base"), opt("--baseline")
    if base or baseline:
        point = merge_base(base, root) if base else None
        label = (base if point == base else f"{base} (merge-base {point[:10]})") if base else baseline
        old = measure_ref(point, count, root) if base else json.loads(pathlib.Path(baseline).read_text(encoding="utf-8"))
        if old.get("approx") != rep["approx"]:
            print(f"warning: comparing {old.get('approx')} with {rep['approx']} counts", file=sys.stderr)
        d = diff(old, rep, top)
        if "--json" in args:
            print(json.dumps({"base": base or baseline, "merge_base": point, **d}, ensure_ascii=False, indent=2))
        else:
            print(render_diff(d, label))
        return 0
    if "--json" in args:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(render(rep, top, budget))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
