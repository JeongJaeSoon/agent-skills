#!/usr/bin/env python3
"""Where the dashboard's data comes from and where it goes, and what agent-skills changed about itself.

flow(): a static map of producers -> local state files -> consumers, filled with what stat says now: path, last
change, size or line count, the last row's writer, and three problems: a store older than its producer's interval
(stale), a file in a state directory the map does not know (unread), a consumer with none of its inputs (starved).
changes(): skill changes from git log (bounded by days and count), open PRs from the collector's prs.json and the
improvement loop's signals ledger (tail only). No model call, no file read whole except the small prs.json.

Usage: flow.py flow | flow.py changes [--days N]      (prints the JSON the dashboard serves)
"""
import datetime as dt, json, os, pathlib, re, subprocess, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fleet  # noqa: E402

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


PRODUCERS = {
    "fleet": "fleet collector · dash serve loop",
    "skills": "skill usage collect · dash serve, 15 min",
    "prstats": "PR stats collect · dash serve",
    "orch": "orch CLI · board, decide, stage, sent, brief --commit",
    "permission": "permission hook",
    "guard": "role guard hook",
    "shepherdprs": "shepherd-prs round · automation",
    "mentions": "watch-mentions round · automation",
    "sync": "skills-sync · launchd",
    "programs": "program leads · orch ledger",
    "cards": "card sessions · report files",
    "human": "you · config",
}
CONSUMERS = {
    "page": "dashboard · Overview, Inbox, PRs, Sessions, Graph, Timeline",
    "brief": "orch brief render · Brief panel, /brief",
    "work": "dashboard · Work",
    "skillspage": "dashboard · Skills",
    "statspage": "dashboard · PR stats",
    "sentpage": "dashboard · Sent",
    "fleet": "fleet collector (its own memory)",
    "programview": "dashboard · program views, measure-delivery",
    "reflect": "reflect precheck · improvement loop",
    "shepherdprs": "shepherd-prs round",
    "reorient": "reorient hook",
    "skillsrc": "every skill reading its config",
}
BRIEF_RULE = ("rows = sessions in state.json (orchestrator → lead → card) + the newest block of each card's report "
              "file (the brief's REPORT line); board.json adds only phase, progress, decisions, holds and corrections; "
              "PR state is joined from prs.json at render")
# id, path (relative to its root), root, writers, readers, the interval past which it is stale (s), derivation rule
STORES = [
    ("state", "state.json", "dash", ["fleet"], ["page", "brief"], 300, "sessions, PRs, inbox, timeline: one collect"),
    ("prs", "prs.json", "dash", ["fleet"], ["brief", "page"], 1800, "open PRs of every session, refreshed by turn"),
    ("events", "events.jsonl", "dash", ["fleet"], ["fleet"], None, "phase changes; the timeline reads its tail"),
    ("prevents", "pr-events.jsonl", "dash", ["fleet"], ["fleet"], None, "review and CI changes per PR"),
    ("memory", "memory.json", "dash", ["fleet"], ["fleet"], 300, "prompt hashes, last states, sticky inbox"),
    ("avatars", "avatars", "dash", ["fleet"], ["page"], None, None),
    ("prompts", "prompts", "dash", ["permission"], ["fleet"], None, "one file per pending permission prompt"),
    ("decisions", "decisions.json", "dash", ["orch"], ["fleet", "brief"], None, "`orch decide` records"),
    ("inbox", "inbox-external.jsonl", "dash", ["orch"], ["fleet"], None, "items added from outside a session"),
    ("sends", "sends.jsonl", "dash", ["fleet"], ["fleet"], None, "text sent from the page"),
    ("sent", "sent", "dash", ["orch"], ["sentpage"], None, "`orch sent` log"),
    ("board", "board.json", "dash", ["orch"], ["brief"], None, "only what cannot be derived: " + BRIEF_RULE),
    ("stages", "stages.json", "dash", ["orch"], ["work"], None, "`orch stage` per worktree"),
    ("skillsj", "skills.json", "dash", ["skills"], ["skillspage"], 3600, "usage per skill from transcripts"),
    ("skillcache", "skill-usage-cache.json", "dash", ["skills"], ["skills"], 3600, "per-transcript counts, by size+mtime"),
    ("prstats", "pr-stats.json", "dash", ["prstats"], ["statspage"], 86400, None),
    ("brieflast", "brief-last", "base", ["orch"], ["brief"], None, "the last sent table, for bold = changed"),
    ("syncj", "sync.json", "base", ["sync"], ["fleet"], 3600, "last skills-sync result"),
    ("reload", "reload-pending.json", "base", ["sync"], ["fleet"], None, "reloads not yet sent, per terminal"),
    ("prsledger", "prs/ledger.json", "base", ["shepherdprs"], ["shepherdprs"], None, "what shepherd-prs already acted on"),
    ("sendlog", "prs/sendlog.jsonl", "base", ["shepherdprs"], ["shepherdprs"], None, "reminders sent"),
    ("mseen", "mentions/seen.json", "base", ["mentions"], ["shepherdprs"], None, "threads already picked up"),
    ("roles", "roles", "base", ["guard"], ["reorient"], None, "one file per session's role"),
    ("signals", "_skill-usage/ledger.jsonl", "programs", ["skills"], ["reflect"], None,
     "one row per flagged editable skill per week"),
    ("ledgers", "*/ledger.jsonl", "programs", ["programs"], ["programview"], None, "one ledger per program"),
    ("config", "agent-skills.json", "claude", ["human"], ["skillsrc"], None, None),
]


def root(name):
    return {"dash": fleet.state_dir(), "base": base(), "programs": programs(),
            "claude": pathlib.Path("~/.claude").expanduser()}[name]


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
    """Stat only; a directory or glob is its entry count and newest entry, a .jsonl also its line count and last row."""
    entries = sorted(pathlib.Path(p.anchor).glob(str(p)[len(p.anchor):])) if "*" in str(p) else None
    if entries is None and p.is_dir():
        entries = list(p.iterdir())
    if entries is not None:
        mt = [e.stat().st_mtime for e in entries if e.exists()]
        out = {"exists": bool(entries) or p.exists(), "entries": len(entries), "mtime": max(mt) if mt else None}
    else:
        try:
            st = p.stat()
        except OSError:
            return {"exists": False}
        out = {"exists": True, "size": st.st_size, "mtime": st.st_mtime}
        if p.suffix == ".jsonl":
            out["lines"], out["last"] = line_count(p, st), last_row(p, st.st_size)
    age = now - out["mtime"] if out.get("mtime") else None
    out["age"] = round(age) if age is not None else None
    out["stale"] = bool(stale_s and age is not None and age > stale_s)
    return out


def flow(sessions=None, now=None):
    now = now or time.time()
    stores = []
    for sid, rel, r, writers, readers, stale_s, rule in STORES:
        p = root(r) / rel
        stores.append({"id": sid, "name": rel, "path": shown(p), "writers": writers, "readers": readers, "stale_after": stale_s,
                       "rule": rule, **stat_node(p, stale_s, now)})
    reports = sorted({(s.get("report") or {}).get("path") for s in sessions or [] if (s.get("report") or {}).get("path")})
    if reports:
        st = [stat_node(pathlib.Path(x), None, now) for x in reports]
        mt = [x["mtime"] for x in st if x.get("mtime")]
        stores.append({"id": "reports", "name": "report files", "path": f"{len(reports)} report files named by briefs", "writers": ["cards"],
                       "readers": ["fleet", "brief"], "rule": "newest heading or bullet block, its 다음/Next line, last PR ref",
                       "exists": True, "entries": len(reports), "mtime": max(mt) if mt else None,
                       "age": round(now - max(mt)) if mt else None, "stale": False,
                       "files": [{"path": shown(x), "age": s.get("age")} for x, s in zip(reports, st)]})
    known = {pathlib.Path(root(r) / rel).name for _, rel, r, *_ in STORES if "/" not in rel}
    unread = []
    for d in (fleet.state_dir(), base()):
        try:
            names = sorted(e.name for e in os.scandir(d) if e.is_file())
        except OSError:
            continue
        unread += [shown(d / n) for n in names if n not in known and not n.startswith(".") and not n.endswith((".lock", ".1"))
                   and not re.search(r"\.prev\.|\.bak", n)]
    fed = {c: [s["id"] for s in stores if c in s["readers"] and s.get("exists")] for c in CONSUMERS}
    return {"generated_at": fleet.utcnow().isoformat(timespec="seconds").replace("+00:00", "Z"),
            "producers": [{"id": k, "label": v} for k, v in PRODUCERS.items()],
            "consumers": [{"id": k, "label": v, "starved": not fed[k], "rule": BRIEF_RULE if k == "brief" else None}
                          for k, v in CONSUMERS.items()],
            "stores": stores, "unread": unread}


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
