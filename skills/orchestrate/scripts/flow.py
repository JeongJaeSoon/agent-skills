#!/usr/bin/env python3
"""Where the dashboard's data comes from and where it goes, and what agent-skills changed about itself.

flow(): the registry (registry.STORES) drawn as producers -> stores -> consumers, filled with what stat says now: path,
last change, size or line count, the last row's writer. Each store is present, off (its feature's config key is
missing), not yet (never written) or error (unreadable, or a directory where a file belongs); a consumer with no
input present is starved for the worst of those. Also: a store older than its interval (stale), a file under a state
root no store names (unread), a fact with two canonical stores (conflicts).
changes(): skill changes from git log (bounded by days and count), open PRs from the collector's prs.json and the
improvement loop's signals ledger (tail only). No model call, no file read whole except the small prs.json.

Usage: flow.py flow | flow.py changes [--days N]      (prints the JSON the dashboard serves)
"""
import datetime as dt, fnmatch, json, os, pathlib, re, stat, subprocess, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fleet, registry  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
TAIL = 4096          # bytes read from the end of a .jsonl for its last row
SIGNALS_TAIL = 64 * 1024
GIT_DAYS, GIT_MAX = 60, 400


def base():
    """The shared state root: watchers and hooks write beside the fleet state directory."""
    return pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or fleet.state_dir().parent).expanduser()


def programs():
    return pathlib.Path(os.environ.get("PROGRAMS_HOME", "~/.claude/programs")).expanduser()


BRIEF_RULE = ("rows = sessions in state.json (orchestrator → lead → card); a card's progress is its own report "
              "(the file its brief's REPORT line names, else its newest milestone message to its Run) and its PR the "
              "current-PR rule over prs.json; board.json adds rows' phase and progress, leads Orca cannot see, "
              "to-dos, held items and doing lines; decisions come from decisions.json, closures from tidy.jsonl, "
              "bold from brief-last")
SCAN_DEPTH = 4       # directories below a state root the unregistered-file walk enters
# Roles drawn as boxes in the map; locks, backups and removed stores are listed in the table only.
DRAWN = {registry.CANONICAL, registry.DERIVED, registry.CACHE, registry.LOG, registry.HAND}


def root(name):
    xdg = os.environ.get("XDG_STATE_HOME")
    return {"D": fleet.state_dir(), "S": base(), "P": programs(), "C": pathlib.Path("~/.claude").expanduser(),
            "K": pathlib.Path("~/.config/agent-skills").expanduser(),
            "X": pathlib.Path(xdg).expanduser() if xdg else pathlib.Path("~/.local/state").expanduser(),
            "H": pathlib.Path.home()}[name]


def place(store):
    """(root key, path pattern below it) for a file store; None for an outside source or a DB column."""
    if store.path.startswith(("ext:", "db:")):
        return None
    r, _, rel = store.path.partition("/")
    return r, rel


def config_has(key, cfg):
    cur = cfg
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def shown(p):
    home = str(pathlib.Path.home())
    return "~" + str(p)[len(home):] if str(p).startswith(home) else str(p)


_lines = {}  # path -> (inode, size, mtime_ns, lines): a grown file is counted from the old end only


def line_count(p, st):
    old = _lines.get(p)
    if old and old[:3] == (st.st_ino, st.st_size, st.st_mtime_ns):
        return old[3]
    start, n = (old[1], old[3]) if old and old[0] == st.st_ino and old[1] <= st.st_size else (0, 0)
    with open(p, "rb") as f:
        f.seek(start)
        for chunk in iter(lambda: f.read(1 << 16), b""):
            n += chunk.count(b"\n")
    _lines[p] = (st.st_ino, st.st_size, st.st_mtime_ns, n)
    return n


def last_row(p, size):
    with open(p, "rb") as f:
        f.seek(max(0, size - TAIL))
        tail = f.read().splitlines()
    try:
        row = json.loads(tail[-1])
    except (ValueError, IndexError):
        return None
    return " · ".join(str(row[k]) for k in ("ev", "kind", "skill", "type", "action") if isinstance(row.get(k), str))[:80] or None


def stat_node(p, stale_s, now):
    """Stat only; a glob is its entry count and newest entry, a .jsonl also its line count and last row. state: present,
    error (unreadable, or a directory where a file belongs), or None when nothing is there."""
    if "*" in str(p):
        try:
            entries = sorted(pathlib.Path(p.anchor).glob(str(p)[len(p.anchor):]))
        except OSError as e:
            return {"exists": False, "state": registry.ERROR, "error": str(e)}
        # A pattern with an extension names files; `dir/*` may hold directories too.
        files_only = "." in p.name.lstrip("*")
        mt, bad = [], []
        for e in entries:
            try:
                st = e.stat()
            except OSError as err:
                bad.append(f"{e.name}: {err.strerror or err}")
                continue
            if files_only and stat.S_ISDIR(st.st_mode):
                bad.append(f"{e.name}: a directory where a file belongs")
            mt.append(st.st_mtime)
        out = {"exists": bool(entries), "entries": len(entries), "mtime": max(mt) if mt else None}
        if bad:
            return {**out, "state": registry.ERROR, "error": "; ".join(bad[:3]) + (f" (+{len(bad) - 3})" if len(bad) > 3 else "")}
    else:
        try:
            st = p.stat()
        except FileNotFoundError:
            return {"exists": False, "state": None}
        except OSError as e:
            return {"exists": False, "state": registry.ERROR, "error": str(e)}
        if stat.S_ISDIR(st.st_mode):
            return {"exists": True, "state": registry.ERROR, "error": "a directory where a file belongs", "mtime": st.st_mtime}
        out = {"exists": True, "size": st.st_size, "mtime": st.st_mtime}
        if p.suffix == ".jsonl":
            try:
                out["lines"], out["last"] = line_count(p, st), last_row(p, st.st_size)
            except OSError as e:
                return {**out, "state": registry.ERROR, "error": str(e)}
    out["state"] = registry.PRESENT if out["exists"] else None
    age = now - out["mtime"] if out.get("mtime") else None
    out["age"] = round(age) if age is not None else None
    out["stale"] = bool(stale_s and age is not None and age > stale_s)
    return out


def producers_of(store):
    by = {m: pid for pid, (_, mods) in registry.PRODUCERS.items() for m in mods}
    return sorted({by.get(w if w.startswith("ext:") else w.split(":", 1)[0], "human") for w in store.writers})


def unregistered(stores):
    """Files under the state roots no store pattern names. Program directories are a lead's own files (registered as
    hand files), so only the programs home's top level and its `_` directories are walked."""
    # Roots that are one directory (an override can point two at the same place) are walked once, with every
    # pattern of each.
    tops = {}
    for key in ("D", "S", "P"):
        tops.setdefault(os.path.realpath(root(key)), set()).add(key)
    pats = {t: [] for t in tops}
    for s in stores:
        if (pl := place(s)) and pl[0] in ("D", "S", "P"):
            t = os.path.realpath(root(pl[0]))
            pats[t].append((pl[0], pl[1]))
    out, problems = [], []
    for top, keys in tops.items():
        others = {t for t in tops if t != top}
        for dirpath, dirnames, filenames in os.walk(top, onerror=lambda e: problems.append(f"{shown(e.filename)}: {e.strerror}")):
            d = pathlib.Path(dirpath)
            rel_d = d.relative_to(top)
            depth = len(rel_d.parts)
            if "P" in keys and depth == 0:
                dirnames[:] = [n for n in dirnames if n.startswith("_")]
            own = [p for k, p in pats[top]
                   # `P/*/...` names a program's files; the `_` directories beside the programs need their own pattern.
                   if not (k == "P" and (rel_d.parts[:1] or ("",))[0].startswith("_") and p.startswith("*/"))]
            # Another root is walked as itself; a directory a `dir/*` pattern names whole is not entered.
            dirnames[:] = [n for n in dirnames if os.path.realpath(d / n) not in others
                           and str(rel_d / n if depth else pathlib.Path(n)) + "/*" not in own]
            if depth >= SCAN_DEPTH and dirnames:
                problems.append(f"{shown(d)}: deeper than {SCAN_DEPTH} levels, not walked")
                dirnames[:] = []
            for n in sorted(filenames):
                rel = str(rel_d / n) if depth else n
                if any(fnmatch.fnmatchcase(rel, p) for p in own) or _scratch(n, filenames):
                    continue
                out.append(shown(d / n))
    return out, problems


TEMP_PREFIXES = (".card-watch-", ".self-check-", ".stuck-", ".settings-")


def _scratch(name, siblings):
    """A writer's temporary (mkstemp names it `.<file>.` or with its own prefix), or Finder's folder file."""
    if name == ".DS_Store" or name.startswith(TEMP_PREFIXES):
        return True
    return name.startswith(".") and any(name[1:].startswith((s + ".", s + "-")) for s in siblings if not s.startswith("."))


def flow(sessions=None, now=None, stores=None):
    now = now or time.time()
    stores = registry.STORES if stores is None else stores
    cfg = fleet.read_json(root("C") / "agent-skills.json", {}) or {}
    out = []
    for s in stores:
        pl = place(s)
        node = {"id": s.id, "name": s.path.split("/", 1)[-1] if pl else s.path, "store": s.path,
                "facts": s.facts, "writers": producers_of(s), "writer_fns": list(s.writers), "readers": list(s.readers),
                "stale_after": s.stale_s, "rule": s.rule, "feature": s.feature,
                "drawn": any(r in DRAWN for r in s.facts.values())}
        off = bool(s.feature) and not config_has(s.feature, cfg)
        if s.path.startswith("ext:"):
            node.update(path=s.path, exists=not off, state=registry.OFF if off else "outside")
        elif s.path.startswith("db:"):
            node.update(path=s.path, exists=False, state=registry.NOT_YET)
        else:
            p = root(pl[0]) / pl[1]
            node.update(path=shown(p), **stat_node(p, s.stale_s, now))
            if node["state"] is None:
                node["state"] = registry.OFF if off else registry.NOT_YET
        if s.id == "reports":
            node.update(report_files(sessions, now))
        out.append(node)
    states = {n["id"]: n["state"] for n in out}
    consumers = []
    for cid, label in registry.CONSUMERS.items():
        ins = [n for n in out if cid in n["readers"]]
        if cid == "nobody" or not ins:
            continue
        got = {st: [n["id"] for n in ins if n["state"] == st] for st in (registry.PRESENT, "outside", registry.ERROR,
                                                                          registry.NOT_YET, registry.OFF)}
        # Fed by anything present; otherwise starved for the worst reason: an error, then a store never written,
        # then a feature switched off.
        state = registry.PRESENT if got[registry.PRESENT] or got["outside"] else next(
            st for st in (registry.ERROR, registry.NOT_YET, registry.OFF) if got[st])
        consumers.append({"id": cid, "label": label, "state": state, "starved": state != registry.PRESENT,
                          "inputs": {k: v for k, v in got.items() if v},
                          "rule": BRIEF_RULE if cid == "brief" else None})
    canon = {}
    for s in stores:
        for f, role in s.facts.items():
            if role == registry.CANONICAL:
                canon.setdefault(f, []).append(s.id)
    known = lambda f, ids: (registry.DUAL_CANONICAL.get(f) or ((), None))[1] if set(  # noqa: E731
        (registry.DUAL_CANONICAL.get(f) or ((),))[0]) == set(ids) else None
    conflicts = [{"fact": f, "stores": ids, "known": known(f, ids)} for f, ids in sorted(canon.items()) if len(ids) > 1]
    unread, problems = unregistered(stores)
    return {"generated_at": fleet.utcnow().isoformat(timespec="seconds").replace("+00:00", "Z"),
            "producers": [{"id": k, "label": v[0]} for k, v in registry.PRODUCERS.items()],
            "consumers": consumers, "stores": out, "states": states, "conflicts": conflicts,
            "unread": unread, "scan_problems": problems}


def report_files(sessions, now):
    """The report files the sessions' briefs name, as entries of the card-report source."""
    reports = sorted({(s.get("report") or {}).get("path") for s in sessions or [] if (s.get("report") or {}).get("path")})
    st = [stat_node(pathlib.Path(x), None, now) for x in reports]
    mt = [x["mtime"] for x in st if x.get("mtime")]
    return {"entries": len(reports), "mtime": max(mt) if mt else None, "age": round(now - max(mt)) if mt else None,
            "files": [{"path": shown(x), "age": s.get("age")} for x, s in zip(reports, st)]}


# ------------------------------------------------------------ skill changes

SUBJECT_SKILL = re.compile(r"^\w+\(([\w-]+)\)")
PR_REF = re.compile(r"\(#(\d+)\)\s*$")
LESSON = re.compile(r"\bL-\d+\b")


def git(*args):
    r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=20)
    return r.stdout if r.returncode == 0 else ""


def origin_repo():
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", git("remote", "get-url", "origin"))
    return m.group(1) if m else None


_git = {}  # (HEAD, days) -> rows


def commits(days):
    head = git("rev-parse", "HEAD").strip()
    if (head, days) in _git:
        return _git[(head, days)]
    out = git("log", f"--since={days}.days", f"--max-count={GIT_MAX}", "--format=%x1e%H%x1f%cI%x1f%s%x1f%b%x1f",
              "--name-only", "--", "skills", "agents", "hooks")
    rows = []
    for rec in out.split("\x1e")[1:]:
        sha, at, subject, body, files = (rec.split("\x1f") + [""] * 5)[:5]
        touched = sorted({f.split("/")[1] for f in files.split() if f.startswith("skills/") and f.count("/") >= 2})
        scope = SUBJECT_SKILL.match(subject)
        # The subject's scope names the skill a change is for; other skills it touched are listed, not counted.
        main = [scope.group(1)] if scope and scope.group(1) in touched else touched[:1] or (
            [scope.group(1)] if scope else ["(plugin)"])
        also = [t for t in touched if t not in main]
        pr = PR_REF.search(subject)
        lessons = sorted(set(LESSON.findall(subject + " " + body)))
        rows.append({"at": at, "skills": main, "also": also, "change": PR_REF.sub("", subject).strip(), "sha": sha[:10],
                     "pr": int(pr.group(1)) if pr else None, "lessons": lessons,
                     "status": "merged" if pr else "applied", "source": "git"})
    _git.clear()
    _git[(head, days)] = rows
    return rows


def open_prs(repo):
    rows = []
    for d in ((fleet.read_json(fleet.state_dir() / "prs.json", {}) or {}).get("prs") or {}).values():
        d = d.get("detail") or {}
        if d.get("repo") != repo or d.get("state") != "OPEN":
            continue
        scope = SUBJECT_SKILL.match(d.get("title") or "")
        rows.append({"at": d.get("updated") or d.get("created"), "skills": [scope.group(1)] if scope else ["(plugin)"],
                     "change": d.get("title"), "pr": d.get("number"), "url": d.get("url"), "lessons": [],
                     "status": "in PR", "source": "prs.json"})
    return rows


def signals(since):
    p = programs() / "_skill-usage" / "ledger.jsonl"
    try:
        with open(p, "rb") as f:
            start = max(0, p.stat().st_size - SIGNALS_TAIL)
            f.seek(start)
            lines = f.read().decode(errors="replace").splitlines()[1 if start else 0:]  # a mid-file seek cuts a row
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("ev") != "signal" or (r.get("ts") or "") < since:
            continue
        rows.append({"at": r["ts"], "skills": [str(r.get("skill", "")).split(":")[-1]],
                     "change": f"{r.get('flag')}: {r.get('suggest') or r.get('note') or ''}"[:160], "pr": None,
                     "lessons": [], "status": "proposed", "source": "signals", "basis": r.get("evidence")})
    return rows


def lessons_ledger():
    """reflect.ledger when it is a plain file on this machine; a vault path is not readable here."""
    cfg = fleet.read_json(pathlib.Path("~/.claude/agent-skills.json").expanduser(), {}) or {}
    p = ((cfg.get("reflect") or {}).get("ledger") or "")
    if not p:
        return {"configured": False, "note": "reflect.ledger is not set in ~/.claude/agent-skills.json"}
    p = pathlib.Path(p).expanduser()
    if not p.is_file():
        return {"configured": True, "readable": False, "path": shown(p), "note": "not a plain file on this machine"}
    st = p.stat()
    return {"configured": True, "readable": True, "path": shown(p), "mtime": st.st_mtime, "lines": line_count(p, st)}


def changes(days=GIT_DAYS, now=None):
    since = ((now or fleet.utcnow()) - dt.timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")
    repo = origin_repo()
    rows = sorted(commits(days) + open_prs(repo) + signals(since), key=lambda r: r["at"] or "", reverse=True)
    for r in rows:
        if r.get("pr") and not r.get("url") and repo:
            r["url"] = f"https://github.com/{repo}/pull/{r['pr']}"
    per = {}
    for r in rows:
        for s in r["skills"]:
            p = per.setdefault(s, {"skill": s, "merged": 0, "applied": 0, "in PR": 0, "proposed": 0, "last": r["at"]})
            p[r["status"]] += 1
    return {"generated_at": fleet.utcnow().isoformat(timespec="seconds").replace("+00:00", "Z"), "days": days,
            "repo": repo, "rows": rows, "skills": sorted(per.values(), key=lambda p: p["last"] or "", reverse=True),
            "lessons": lessons_ledger(), "limits": {"git_max": GIT_MAX, "signals_tail_bytes": SIGNALS_TAIL}}


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["flow"]:
        print(json.dumps(flow(), ensure_ascii=False, indent=1))
    elif a[:1] == ["changes"]:
        print(json.dumps(changes(int(a[a.index("--days") + 1]) if "--days" in a else GIT_DAYS), ensure_ascii=False, indent=1))
    else:
        sys.exit(__doc__)
