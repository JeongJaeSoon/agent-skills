#!/usr/bin/env python3
"""Progress dashboard for an orchestrate program: JSON-first and model-free.

Usage: orch-dash <command> [options]

  collect <slug>                     rebuild <store>/<slug>/dashboard/state.json from program.json,
                                     the ledger, the tracker, GitHub and Orca (each source optional)
  serve [--port P] [--interval 60] [--host 127.0.0.1]
                                     serve the dashboard; full collect every --interval seconds,
                                     ledger and notes re-read every 3 s. One server per store.
  ensure [--port P]                  start `serve` in the background unless this store's server,
                                     running this code, already answers; restart it if it runs
                                     older code, move it if it answers on another port.
                                     `orch init` and `orch status` call it. P defaults to
                                     $ORCH_DASH_PORT, else the port this store last served on, else 4780.
                                     With this store's LaunchAgent loaded, launchd starts and restarts it instead.
  install-agent [--port P]           macOS: run `serve` from this checkout as a LaunchAgent (login, crash, code
                                     update); `$ORCH_DASH_LABEL` names it. uninstall-agent removes it.
  note <slug> --kind risk|digest|decision --text TEXT [--author NAME]
                                     add a human-readable line to the dashboard
  demo [--port 4780] [--live] [--no-serve] [--crowd N] [--layout]
                                     a realistic fake program in a temp store, served offline
  fleet                              one collect of the fleet view (every Orca session), prints a summary
  skills [--no-signals]              one collect of skill usage from local transcripts (skill_usage.py), prints a summary
  prstats [--out PATH]               one collect of my PR counts from GitHub search (pr_stats.py), prints a summary;
                                     saves even a drop the scheduled collect held back as suspicious
  inbox add --type T --title TEXT [--session ID] [--url URL] [--command CMD] [--key KEY]
  inbox resolve --key KEY            put an item in (or take it out of) the fleet inbox
  ack <id>... | ack --done-before AGE [--kind stage|board]... | unack <id>... | acks [--all] [--json]
                                     mark finished stage rows (stage:<row>) and finished brief rows (board:<row>)
                                     as checked, so the dashboard and the brief leave them out; a row in progress,
                                     waiting or held is refused. AGE: 90m, 24h, 7d (see acks.py)
  adopt [--apply] [--undo all|<worktree>]
                                     show (or write, when config adopt.write_orca_parent is on) each session's
                                     Orca parent worktree; every write is logged and --undo restores it

Store: $PROGRAMS_HOME or ~/.claude/programs. The dashboard writes only under <slug>/dashboard/.
Fleet state: $ORCH_FLEET_STATE or ~/.local/state/agent-skills/dashboard (see fleet.py). ORCH_FLEET=off disables it,
ORCH_SKILLS=off disables only the skill usage collect, and ORCH_PR_STATS=off only the PR stats collect.
"""
import concurrent.futures, copy, datetime as dt, fcntl, functools, hashlib, http.server, itertools, json, os, pathlib, plistlib, re, shutil, signal, socket, subprocess, sys, tempfile, threading, time, urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import prog  # noqa: E402  ledger arithmetic lives there; never re-derive cap or main state here
import fleet  # noqa: E402
import selfcheck  # noqa: E402
import skill_usage  # noqa: E402
import token_usage  # noqa: E402
import pr_stats  # noqa: E402
import sent  # noqa: E402
import stages  # noqa: E402
import flow  # noqa: E402
import acks  # noqa: E402
import tidy  # noqa: E402
import store as store_db  # noqa: E402
import board  # noqa: E402
import jobs  # noqa: E402
sys.path.append(str(HERE.parents[2] / "scripts"))
try:
    from sync import reload_row  # noqa: E402
except ImportError:  # an install without the repo's scripts/
    def reload_row(row):
        return False

ASSETS = HERE.parent / "assets" / "dashboard"
SOURCES = ("tracker", "stages", "github", "orca")
NOTE_KINDS = ("risk", "digest", "decision")
SERIES_DAYS = 30  # charts cover the program from its start, up to this far back
SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}")
# A team key may start with a digit (94S-135) but has a letter, so a date (2026-09) is not a ticket.
TICKET_RE = re.compile(r"\b((?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{1,10}-\d+)\b")
STATE_ORDER = {"started": 0, "unstarted": 1, "triage": 2, "backlog": 3, "completed": 4, "canceled": 5}


class SourceError(Exception):
    pass


def home():
    return pathlib.Path(os.environ.get("PROGRAMS_HOME", "~/.claude/programs")).expanduser()


def tracker_path():
    return pathlib.Path(os.environ.get("TRACKER_PY") or
                        pathlib.Path(__file__).resolve().parents[2] / "use-tracker/scripts/tracker.py")


def iso(t):
    return t.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def ts(s):
    return prog.parse_ts(s) if s else None


def read_json(path, default=None):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return default


def read_jsonl(path):
    try:
        lines = pathlib.Path(path).read_text().splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue  # a torn last line while prog.py appends; it is complete on the next read
    return rows


def ledger_events(path):
    """(events, rejected): every ledger reader indexes e["ev"], so a hand-edited row that is not an object with a
    string ev is set aside instead of raising there; _merge reports the rejected ones on the program's row."""
    events, rejected = [], []
    for e in read_jsonl(path):
        (events if isinstance(e, dict) and isinstance(e.get("ev"), str) else rejected).append(e)
    return events, rejected


def write_atomic(path, text):
    path = pathlib.Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def sh(cmd, timeout=45):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise SourceError(f"{cmd[0]} not found")
    except subprocess.TimeoutExpired:
        raise SourceError(f"timed out after {timeout}s")
    if r.returncode:
        tail = (r.stderr or r.stdout).strip().splitlines()
        raise SourceError(tail[-1][:300] if tail else f"exit {r.returncode}")
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise SourceError("output was not JSON")


# ---------------------------------------------------------------- sources

def tracker_cmd(cfg):
    """(tracker.py argv prefix, project), or None when the program has no tracker project."""
    tr = cfg.get("tracker") or {}
    project = tr.get("project") or cfg.get("linear_project")
    if not project:
        return None
    path = tracker_path()
    if not path.exists():
        raise SourceError(f"tracker.py not found at {path}")
    return [sys.executable, str(path)] + (["--adapter", tr["adapter"]] if tr.get("adapter") else []), project


def fetch_issues(cfg):
    if not tracker_cmd(cfg):
        return None
    base, project = tracker_cmd(cfg)
    out = sh(base + ["list", "--project", project, "--limit", "500"])
    issues = out.get("issues", []) if isinstance(out, dict) else list(out)
    have = {i.get("id") for i in issues}
    admitted = prog.admitted_tickets(ledger_events(program_dir(cfg["slug"]) / "ledger.jsonl")[0]) if cfg.get("slug") else []
    for pid in dict.fromkeys((cfg.get("predicate") or []) + admitted):
        if pid not in have:  # predicate items and admitted follow-ups can live outside the project
            try:
                got = sh(base + ["get", pid], timeout=20)
                issues.append(got.get("issue", got) if isinstance(got, dict) else got)
            except SourceError:
                pass
    return issues


STAGE_CALLS = 24          # tracker calls per collect, the top-level list included
STAGE_RECHECK_H = 6       # a closed leaf is asked again for new children after this long


def fetch_stages(cfg):
    """Stage bars: every top-level issue of the tracker project that has children, counted over its leaf
    descendants (canceled ones left out).

    The tracker's list carries no parent links, so the tree is crawled within STAGE_CALLS per collect and
    cached in tree.json. Order: known parents (their children's states, new children), nodes never visited,
    open leaves, then closed leaves last asked over STAGE_RECHECK_H ago; oldest-asked first in each group.
    Until every node under a stage has been asked once, the stage reports how many are left."""
    if not tracker_cmd(cfg):
        return None
    base, project = tracker_cmd(cfg)
    path = program_dir(cfg["slug"]) / "dashboard" / "tree.json"
    path.parent.mkdir(exist_ok=True)
    tree = read_json(path, {}) or {}
    kids, info, seen = tree.get("kids") or {}, tree.get("info") or {}, tree.get("seen") or {}

    def children(pid):
        rows = sh(base + (["children", pid] if pid else ["children", "--project", project]), timeout=30)
        rows = rows if isinstance(rows, list) else rows.get("issues", [])
        for i in rows:
            info[i["id"]] = {k: i.get(k) for k in ("title", "state_type", "url", "created_at")}
        return [i["id"] for i in rows]

    top = children(None)  # a failure here fails the source: the last good stages stay on screen

    def below(roots):  # a reparent seen half-way can leave a cycle in the cache: visit each node once
        out, seen_here, stack = [], set(), list(reversed(roots))  # tracker order: oldest (the epics) first
        while stack:
            n = stack.pop()
            if n in seen_here:
                continue
            seen_here.add(n)
            out.append(n)
            stack += reversed(kids.get(n) or [])
        return out

    nodes, recheck = below(top), iso(utcnow() - dt.timedelta(hours=STAGE_RECHECK_H))
    by_age = lambda ns: sorted(ns, key=lambda n: seen.get(n, ""))
    leaf = lambda n: n in seen and not kids.get(n)
    parents = by_age(n for n in nodes if kids.get(n))
    rest = ([n for n in nodes if n not in seen]
            + by_age(n for n in nodes if leaf(n) and (info.get(n) or {}).get("state_type") in prog.OPEN_STATES)
            + by_age(n for n in nodes if leaf(n) and seen[n] < recheck
                     and (info.get(n) or {}).get("state_type") not in prog.OPEN_STATES))
    # Parents first but at most half the calls, so a wide tree still discovers and rechecks; oldest-asked first.
    k = max((STAGE_CALLS - 1) // 2, STAGE_CALLS - 1 - len(rest))
    todo = parents[:k] + rest[:STAGE_CALLS - 1 - min(k, len(parents))]

    def visit(pid):
        try:
            return pid, children(pid)
        except SourceError:  # due again next collect; counted as failed meanwhile
            return pid, None

    failed = 0
    with concurrent.futures.ThreadPoolExecutor(2) as pool:  # Orca's runtime drops connections under more
        for pid, ids in pool.map(visit, todo):
            if ids is None:
                failed += 1
            else:
                kids[pid], seen[pid] = ids, iso(utcnow())
    write_atomic(path, json.dumps({"kids": kids, "info": info, "seen": seen}))

    stages = []
    for root in sorted((n for n in top if kids.get(n)), key=lambda n: ((info.get(n) or {}).get("created_at") or "", n)):
        sub_nodes = below([root])
        leaves = [info.get(n) or {} for n in sub_nodes if not kids.get(n) and n != root]
        leaves = [x for x in leaves if x.get("state_type") != "canceled"]
        count = lambda t: sum(x.get("state_type") == t for x in leaves)
        stages.append({"id": root, **(info.get(root) or {}), "total": len(leaves), "done": count("completed"),
                       "started": count("started"), "unvisited": sum(n not in seen for n in sub_nodes)})
    return {"stages": stages, "project": project, "failed": failed}


PR_FIELDS = "number,title,url,state,isDraft,headRefOid,headRefName,baseRefName,mergeStateStatus,createdAt,mergedAt"
CHECK_FIELDS = ",statusCheckRollup,reviews"


WINDOW_LIMIT = 500
REPO_NAMES = {}  # the name a program wrote -> the name GitHub answers to now; a rename keeps the old name working
ROUND_PRS = [None]  # during a slow_round: canonical repo -> {"open": rows, "window": [(since, rows)]}
ROUND_LOCK = threading.Lock()


def canonical_repo(repo):
    """The repo's current owner/name. Programs started before a rename still carry the old name and GitHub redirects it,
    so without this the same repo is listed once per name. Lower-cased when GitHub does not answer: names are
    case-insensitive."""
    key = repo.lower()
    if key not in REPO_NAMES:
        try:
            REPO_NAMES[key] = sh(["gh", "api", f"repos/{repo}", "--jq", "{n: .full_name}"], timeout=20)["n"].lower()
        except (SourceError, KeyError, TypeError, AttributeError):
            return key  # not remembered: asked again next time
    return REPO_NAMES[key]


def round_prs(repo, kind, since, ask):
    """ask() once per repo and round: a dozen programs on one repo shared one `gh pr list` each time. Outside a round
    (`orch-dash collect`, a first read) ROUND_PRS is None and every caller asks. A window that began no later than
    this program's, and was not cut at its limit, holds everything this one would have listed. A failure is kept for
    the round too, so a repo gh cannot answer is asked once, not once per program."""
    with ROUND_LOCK:
        if ROUND_PRS[0] is None:
            return ask()
        mine = ROUND_PRS[0].setdefault(repo, {"open": None, "window": []})
        if kind == "open":
            if mine["open"] is None:
                mine["open"] = _try(ask)
            return _unwrap(mine["open"], lambda rows: rows)
        for s, got in mine["window"]:
            if s <= since and (isinstance(got, Exception) or len(got) < WINDOW_LIMIT or s == since):
                return _unwrap(got, lambda rows: [p for p in rows if (p.get("createdAt") or "")[:10] >= since])
        got = _try(ask)
        mine["window"].append((since, got))
        return _unwrap(got, lambda rows: rows)


def _try(ask):
    try:
        return ask()
    except SourceError as e:
        return e


def _unwrap(got, narrow):
    if isinstance(got, Exception):
        raise got
    return narrow(copy.deepcopy(got))


def fetch_prs(cfg):
    """Every open PR with its checks and reviews (the land order needs them all), plus closed and merged ones
    from the program window without them.

    gh asks GraphQL for 100 PRs a page; with rollups and reviews, a page of a repo with 200 PRs timed out
    (HTTP 504) on every collect. A closed PR keeps the CI and rounds it had when a collect saw it open.
    """
    repo = canonical_repo(cfg["repo"])
    base = ["gh", "pr", "list", "--repo", repo]
    open_ = round_prs(repo, "open", None, lambda: sh(base + ["--json", PR_FIELDS + CHECK_FIELDS, "--state", "open", "--limit", "100"]))
    since = (prog.parse_ts(cfg["created_at"]) - dt.timedelta(days=1)).date().isoformat()
    window = round_prs(repo, "window", since, lambda: sh(
        base + ["--json", PR_FIELDS, "--state", "all", "--search", f"created:>={since}", "--limit", str(WINDOW_LIMIT)]))
    seen = {p["number"] for p in open_}
    return open_ + [p for p in window if p["number"] not in seen]


def orca_result(*args):
    out = sh(["orca", "orchestration", *args, "--json"])
    if isinstance(out, dict) and out.get("ok") is False:
        raise SourceError(json.dumps(out.get("error"))[:300])
    return out.get("result", out) if isinstance(out, dict) else {}


FLEET_SNAPSHOT_TTL_S = 30  # the fleet tick reads worktrees every 10-60 s; older than this, ask Orca directly


def fleet_worktrees():
    """The fleet tick's `worktree ps` rows in `worktree list`'s shape (id, path, isMainWorktree: all landed_but_open reads),
    or None when there is no tick younger than FLEET_SNAPSHOT_TTL_S. Ownership stays with the fleet tick: this reads."""
    f = FLEET
    try:
        at = ((f.sources.get("orca") or {}).get("updated_at")) if f else None
        rows = (f.fast or {}).get("worktrees") if f else None
        if rows is None or not at or (utcnow() - prog.parse_ts(at)).total_seconds() > FLEET_SNAPSHOT_TTL_S:
            return None
        return [{**w, "id": w.get("worktreeId") or w.get("id")} for w in rows]
    except (AttributeError, TypeError, ValueError):
        return None


def fetch_orca(cfg):
    """Workers, tasks, the Run's objective and the worktrees (cards) still on disk: local, read-only CLI calls. The
    worktrees come from the fleet tick's snapshot while it is fresh, so a collect does not list them a second time."""
    shared = fleet_worktrees()
    wts = {"worktrees": shared} if shared is not None else sh(["orca", "worktree", "list", "--json"])
    import orca_settings  # a lead started as a worker shares its coordinator's Run: only the units under it

    def call(*argv):
        return orca_result(*argv[1:])
    # worker-list pages at 100 rows; one run passed 86 in two days
    workers = orca_settings.all_workers(call, "--run", cfg["run"], "--limit", "100", error=SourceError)
    tasks = orca_result("task-list", "--run", cfg["run"]).get("tasks", [])
    workers = orca_settings.lead_scope(workers, cfg.get("lead_dispatch"))
    return {"workers": workers,
            "tasks": orca_settings.scope_tasks(tasks, workers, cfg.get("lead_dispatch")),
            "run": orca_settings.with_full(call, "orchestration", "run-show", "--id", cfg["run"]).get("run") or {},
            "worktrees": (wts.get("result", wts) if isinstance(wts, dict) else {}).get("worktrees", [])}


# ---------------------------------------------------------------- shaping

def labels_of(issue):
    return [l.get("name") if isinstance(l, dict) else l for l in issue.get("labels") or []]


def triage_map(events):
    out = {}
    for e in events:
        if e["ev"] in ("admitted", "parked") and e.get("ticket"):
            out[e["ticket"]] = e["ev"]
    return out


def shape_issues(issues, cfg, events):
    """Annotate normalized issues and keep the ones this program touches."""
    t0, pred = prog.parse_ts(cfg["created_at"]), cfg.get("predicate") or []
    triage = triage_map(events)
    in_ledger = {e.get("ticket") for e in events if e.get("ticket")}
    out = []
    for i in issues:
        iid = i.get("id")
        created = ts(i.get("created_at"))
        new = created is not None and created > t0 and iid not in pred
        # A re-annotated row has lost its description, so it keeps the flag it was given then.
        derived = iid in triage or i.get("derived") or (new and ("follow-up" in labels_of(i)
                                                                 or (i.get("description") or "").startswith("파생:")))
        row = {**i, "labels": labels_of(i), "derived": bool(derived), "triage": triage.get(iid),
               "in_predicate": iid in pred}
        row.pop("description", None)  # can be long; the table links out instead
        if row["in_predicate"] or derived or iid in in_ledger or new:
            out.append(row)
    out.sort(key=lambda r: (not r["in_predicate"], STATE_ORDER.get(r.get("state_type"), 9),
                            -(ts(r.get("updated_at")) or t0).timestamp()))
    return out


def pr_ticket(raw, events, known):
    for e in events:
        if e.get("pr") == raw["number"] and e.get("ticket"):
            return e["ticket"]
    for text in (raw.get("headRefName") or "", raw.get("title") or ""):
        for m in TICKET_RE.findall(text):
            if m.upper() in known or not known:
                return m.upper()
    return None


def verdict_of(number, head, events):
    v = prog.pr_state(events).get(number, {}).get("verdict")
    if not v:
        return "none"
    if v.get("result") != "pass":
        return "fail"
    vsha = v.get("sha") or ""
    # Same-patch head moves still hold at land-check (patch-id); here a moved head reads as stale.
    return "pass" if not head or not vsha or head.startswith(vsha) or vsha.startswith(head) else "stale"


def shape_pr(raw, events, known, prev=None):
    """prev: this PR as an earlier collect shaped it, for the checks a closed PR is fetched without."""
    rollup = raw.get("statusCheckRollup") or []
    failed, pending, _ = prog.ci_summary(rollup)
    reviews = [r for r in raw.get("reviews") or [] if r.get("state") != "PENDING"]
    rounds = len({(r.get("commit") or {}).get("oid") or r.get("submittedAt") for r in reviews})
    verdicts = sum(1 for e in events if e["ev"] == "verdict" and e.get("pr") == raw["number"])
    if "statusCheckRollup" in raw:
        ci = "none" if not rollup else "fail" if failed else "pending" if pending else "pass"
    else:
        # Only what was seen at this head: a commit pushed just before the merge was never checked here.
        seen = prev if prev and prev.get("head") == raw.get("headRefOid") else {}
        # A run still going when it closed is never seen finishing, so only a finished result carries over.
        ci = seen.get("ci") if seen.get("ci") in ("pass", "fail") else "unknown"
        rounds = seen.get("review_rounds") or 0
    return {"number": raw["number"], "title": raw.get("title"), "url": raw.get("url"),
            "state": (raw.get("state") or "").lower(), "draft": bool(raw.get("isDraft")),
            "head": raw.get("headRefOid"), "branch": raw.get("headRefName"), "base": raw.get("baseRefName"),
            "ci": ci,
            "verdict": verdict_of(raw["number"], raw.get("headRefOid"), events),
            "review_rounds": max(rounds, verdicts), "ticket": pr_ticket(raw, events, known),
            "created_at": raw.get("createdAt"), "merged_at": raw.get("mergedAt")}


def refresh_pr(pr, events):
    """Re-apply ledger facts to a PR kept from an earlier collect."""
    return {**pr, "verdict": verdict_of(pr["number"], pr.get("head"), events),
            "ticket": pr_ticket({"number": pr["number"], "headRefName": pr.get("branch"), "title": pr.get("title")},
                                events, set()) or pr.get("ticket")}


def orca_time(s):
    """Orca task timestamps are "YYYY-MM-DD HH:MM:SS" in UTC."""
    return s.replace(" ", "T") + "Z" if s and "T" not in s and not s.endswith("Z") else s


def ms_iso(ms):
    return iso(dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)) if ms else None


def shape_workers(raw, events, tasks=()):
    by_dispatch, spawned_at, dispatched_at = {}, {}, {}
    created = {t.get("id"): orca_time(t.get("created_at")) for t in tasks}
    for e in events:
        if e["ev"] == "spawned" and e.get("ticket"):
            spawned_at.setdefault(e["ticket"], e["ts"])
            for d in re.findall(r"ctx_[0-9a-f]+", " ".join(str(e.get(k) or "") for k in ("note", "dispatch"))):
                by_dispatch[d] = e["ticket"]
                dispatched_at.setdefault(d, e["ts"])
    out = []
    for w in raw:
        p = w.get("projection") or {}
        stage, live = p.get("stage") or {}, p.get("liveness") or {}
        wt_path = ((w.get("resource") or {}).get("worktreeId") or "").split("::", 1)[-1] or None
        wt = (wt_path or "").rstrip("/").rsplit("/", 1)[-1]
        ticket = by_dispatch.get(w.get("dispatchId")) or prog.dispatch_tickets([w], tasks).get(w.get("dispatchId"))
        if not ticket:
            m = TICKET_RE.search(wt)
            ticket = m.group(1).upper() if m else None
        act = stage.get("activity")
        out.append({"dispatch": w.get("dispatchId"), "task": w.get("taskId"), "ticket": ticket,
                    "worktree": wt or None, "worktree_path": wt_path,
                    # Older Orca versions omit provider metadata; keep their transcript discovery.
                    # Only an explicit unsupported provider excludes this host adapter, never a model name.
                    "transcript_expected": (p.get("provider") or {}).get("id") in (None, "", "claude"),
                    "model": (p.get("provider") or {}).get("model") or (p.get("provider") or {}).get("id"),
                    "liveness": live.get("verdict") or w.get("terminalState"),
                    "activity": {"working": "running", "waiting": "waiting", "idle": "idle"}.get(act, "unknown"),
                    "outcome": p.get("outcome") or w.get("workerState"), "stage": stage.get("detail"),
                    # A retry reuses the ticket (and the task): only its own spawn row dates this dispatch.
                    "since": dispatched_at.get(w.get("dispatchId")) or spawned_at.get(ticket) or created.get(w.get("taskId")),
                    "seen_at": ms_iso(live.get("observedAt")), "liveness_reason": live.get("reason"),
                    # Orca stopped hearing from a worker it still counts as in progress.
                    "stale": "stale" in ((p.get("attention") or {}).get("categories") or [])})
    out.sort(key=lambda w: (w["outcome"] != "in_progress", w["activity"] != "waiting", w.get("since") or ""))
    return out


EV_TEXT = {
    "dep": "dependency recorded", "land_check": "land-check on #{pr}", "yield": "#{pr} yielded",
    "reprioritized": "land order reprioritized",
    "spawned": "spawned {ticket}", "ready": "#{pr} ready for review", "landed": "landed #{pr} as {sha7}",
    "main_green": "main green at {sha7}", "main_red": "main red at {sha7}", "land_failed": "landing #{pr} failed",
    "admitted": "{ticket} admitted into scope", "parked": "{ticket} parked as follow-up",
    "approved": "#{pr} approved for landing", "stop": "STOP line set", "resume": "resumed",
    "predicate_verified": "predicate verified on the real artifact", "config": "config changed",
    "signal": "lesson signal: {kind}",
    "lock_acquired": "#{pr} took the exclusive lane", "lock_released": "#{pr} left the exclusive lane", "lane": "#{pr} lane classified",
}


# Fields already said by the text; anything else an event carries (base, blocks, reason, ...) is appended.
KNOWN_FIELDS = {"ts", "ev", "ticket", "pr", "sha", "note", "patch_id", "source", "result", "kind"}


def activity_of(events, notes):
    rows = []
    for e in events:
        if e["ev"] == "verdict":
            text = f"verdict {e.get('result', 'pass')} on #{e.get('pr')} by {e.get('source') or '?'}"
        else:
            text = EV_TEXT.get(e["ev"], e["ev"]).format(ticket=e.get("ticket") or "?", pr=e.get("pr") or "?",
                                                        sha7=(e.get("sha") or "?")[:7], kind=e.get("kind") or "?")
        if e.get("ticket") and "{ticket}" not in EV_TEXT.get(e["ev"], e["ev"]):
            text += f" ({e['ticket']})"
        extra = [f"{k}={','.join(map(str, v)) if isinstance(v, list) else v}" for k, v in e.items()
                 if k not in KNOWN_FIELDS and v not in (None, "", [])]
        if extra:
            text += " · " + " ".join(extra)
        if e.get("note"):
            text += f" — {e['note']}"
        rows.append({"ts": e["ts"], "kind": e["ev"], "text": text, "ticket": e.get("ticket"), "pr": e.get("pr"),
                     "sha": e.get("sha"), "note": e.get("note")})
    for n in notes:
        rows.append({"ts": n["ts"], "kind": "note", "note_kind": n.get("kind"), "text": n.get("text"),
                     "author": n.get("author")})
    # Newest first; within one second the later ledger line wins.
    rows = [r for _, r in sorted(enumerate(rows), key=lambda x: (prog.parse_ts(x[1]["ts"]), x[0]), reverse=True)]
    return rows[:300]


# ---------------------------------------------------------------- tasks and the dependency graph

DEP_FIELDS = ("on", "after", "blocked_by", "depends_on")


def shape_tasks(raw, workers):
    ticket_of = {w["dispatch"]: w.get("ticket") for w in workers}
    out = []
    for t in sorted(raw, key=lambda t: t.get("created_at") or ""):
        deps = t.get("deps") or []
        if isinstance(deps, str):
            try:
                deps = json.loads(deps)
            except ValueError:
                deps = []
        title = t.get("display_name") or t.get("task_title") or t.get("id")
        m = TICKET_RE.search(title or "")
        out.append({"id": t.get("id"), "title": title, "status": t.get("status"), "deps": list(deps),
                    "parent": t.get("parent_id"), "dispatch": t.get("dispatch_id"),
                    "ticket": (m.group(1).upper() if m else None) or ticket_of.get(t.get("dispatch_id")),
                    "created_at": t.get("created_at"), "completed_at": t.get("completed_at"), "superseded_by": None})
    # A failed or canceled task is history once a later task took its ticket.
    for i, t in enumerate(out):
        later = next((u["id"] for u in out[i + 1:] if t["ticket"] and u["ticket"] == t["ticket"]), None)
        if t["status"] in ("failed", "canceled", "cancelled") and later:
            t["superseded_by"] = later
    return out


def build_graph(tasks, events, issues, order, landing):
    """Nodes keyed by ticket when known (else task id); edge (a, b) means a must finish before b."""
    key_of = {t["id"]: t["ticket"] or t["id"] for t in tasks}
    nodes, edges = {}, set()
    for t in tasks:
        n = nodes.setdefault(key_of[t["id"]], {"id": key_of[t["id"]], "ticket": t["ticket"], "tasks": []})
        n["tasks"].append(t)
        for d in t["deps"]:
            if d in key_of and key_of[d] != key_of[t["id"]]:
                edges.add((key_of[d], key_of[t["id"]]))
    for e in events:
        if e["ev"] != "dep" or not e.get("ticket"):
            continue
        for f in DEP_FIELDS:
            for before in ([e[f]] if isinstance(e.get(f), str) else e.get(f) or []):
                for k in (before, e["ticket"]):
                    nodes.setdefault(k, {"id": k, "ticket": k, "tasks": []})
                if before != e["ticket"]:
                    edges.add((before, e["ticket"]))
    issue = {i["id"]: i for i in issues or []}
    landed = {e.get("ticket") for e in events if e["ev"] == "landed" and e.get("ticket")}
    pr_ticket = {e.get("pr"): e.get("ticket") for e in events if e.get("pr") and e.get("ticket")}
    landed = (landed | {pr_ticket.get(e.get("pr")) for e in events if e["ev"] == "landed"}) - {None}
    holders = {(l.get("holder") or {}).get("ticket") for l in (landing or {}).values()}
    order_state = {o.get("ticket"): o.get("state") for o in order or [] if o.get("ticket")}
    spawned = {e.get("ticket") for e in events if e["ev"] == "spawned"}
    for n in nodes.values():
        tk, sts = n["ticket"], {t["status"] for t in n["tasks"]}
        title = issue.get(tk, {}).get("title") or next((t["title"] for t in n["tasks"]), None)
        # A ticket can take several PRs: while the tracker says it is open, a landing does not finish it.
        if issue.get(tk, {}).get("state_type") == "completed" or \
                (tk in landed and issue.get(tk, {}).get("state_type") not in prog.OPEN_STATES):
            status = "done"
        elif tk in holders or order_state.get(tk) == "ready":
            status = "landing"
        elif sts and sts <= {"completed"} and tk not in issue:
            status = "done"  # a ticket-less task (a verifier, a chore) is done when Orca says so
        elif order_state.get(tk) == "blocked" or "blocked" in sts:
            status = "blocked"
        elif "dispatched" in sts or tk in spawned or order_state.get(tk) in ("catching_up", "waiting"):
            status = "in_progress"
        else:
            status = "waiting"
        n.update(status=status, title=title)
        n.pop("tasks")
    # Longest-path layering; a cycle (bad data) stops deepening after len(nodes) rounds.
    depth = {k: 0 for k in nodes}
    for _ in range(len(nodes)):
        changed = False
        for a, b in edges:
            if depth[b] < depth[a] + 1:
                depth[b] = depth[a] + 1
                changed = True
        if not changed:
            break
    for k, n in nodes.items():
        n["depth"] = depth[k]
    # Critical path: the longest chain through unfinished nodes.
    live = {k for k, n in nodes.items() if n["status"] != "done"}
    best, prev_of = {k: 1 for k in live}, {}
    for k in sorted(live, key=lambda k: depth[k]):
        for a, b in edges:
            if b == k and a in live and best[a] + 1 > best[k]:
                best[k], prev_of[k] = best[a] + 1, a
    path = []
    if best:
        k = max(best, key=lambda k: (best[k], -depth[k]))
        while k:
            path.append(k)
            k = prev_of.get(k)
    return {"nodes": sorted(nodes.values(), key=lambda n: (n["depth"], n["id"])),
            "edges": sorted(list(e) for e in edges), "critical": path[::-1]}


# ---------------------------------------------------------------- token usage (Claude Code transcripts)

USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def projects_dir():
    return pathlib.Path(os.environ.get("CLAUDE_PROJECTS_DIR", "~/.claude/projects")).expanduser()


def transcript_dir(path):
    return projects_dir() / re.sub(r"[^A-Za-z0-9]", "-", path)


def _add(a, b):
    return {k: a.get(k, 0) + b.get(k, 0) for k in USAGE_KEYS}


PARKING_TOOLS = {"ScheduleWakeup", "Monitor"}
MOTION_V = 2  # raise when step_motion's rules change, so a cached motion is read again (scan_transcript)


def step_motion(m, row):
    """What the session does after this row: works on a turn, waits on a tool call, or has ended its turn.

    `parked` marks a turn that armed its own wake-up (ScheduleWakeup, Monitor, a background command) and got a
    result that is not an error, so a finished turn is waiting on purpose rather than for someone to type.

    A /reload-* row (sync.reload_row) leaves it as it was: the skills-sync broadcast types those into idle sessions,
    and no reply follows, so counting one would hold a finished session at working until its next real turn."""
    if reload_row(row):
        return m
    t, kind = row.get("timestamp"), row.get("type")
    msg = row.get("message") if isinstance(row.get("message"), dict) else {}
    content = msg.get("content")
    blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
    if kind == "user":
        prompt = isinstance(content, str) or any(b.get("type") == "text" for b in blocks)
        armed = any(b.get("type") == "tool_result" and b.get("tool_use_id") in m.get("arming", ())
                    and not b.get("is_error") for b in blocks)
        return {"state": "working", "since": t, "parked": not prompt and (m.get("parked", False) or armed),
                "arming": [] if prompt else m.get("arming", [])}
    if kind != "assistant":
        return m
    tools = [b for b in blocks if b.get("type") == "tool_use"]
    if tools:
        inp = tools[-1].get("input") if isinstance(tools[-1].get("input"), dict) else {}
        arming = m.get("arming", []) + [b["id"] for b in tools if b.get("id") and b.get("name") in PARKING_TOOLS
                                        or b.get("id") and isinstance(b.get("input"), dict) and b["input"].get("run_in_background")]
        return {"state": "tool", "since": t, "tool": tools[-1].get("name"),
                "detail": str(inp.get("description") or inp.get("command") or inp.get("prompt") or "")[:80],
                "parked": m.get("parked", False), "arming": arming[-20:]}
    if msg.get("stop_reason") == "end_turn":
        return {"state": "idle", "since": t, "parked": m.get("parked", False), "arming": m.get("arming", [])}
    return m if m.get("state") == "working" else {**m, "state": "working", "since": t}


REPEAT_TURNS = 3
REPEAT_MIN = 30
# What differs between two tellings of the same thing: times, durations and ids. Any other number (a ticket, an exit
# code, a count of checks) is kept, since a change there is progress. In a tool's input or result an id or a hash is
# the work itself (another commit, another file), so there only times go.
_TIMES = r"\d{4}-\d\d-\d\dT[\d:.]+Z?|\b\d{1,2}:\d\d(?::\d\d)?\b|\b\d+(?:\.\d+)?\s?(?:ms|s|sec|m|min|h)\b"
_IDS = (r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b|\b(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b"
        r"|\b[a-z]+_[A-Za-z0-9]{8,}\b")
REPEAT_VOLATILE = re.compile(f"{_TIMES}|{_IDS}")
WORK_VOLATILE = re.compile(_TIMES)


def _norm(text, volatile=REPEAT_VOLATILE):
    return volatile.sub("#", " ".join(str(text or "").split()))


def _work(content):
    return _digest(_norm(_flat(content), WORK_VOLATILE))


def _digest(text):
    return hashlib.sha1(text.encode()).hexdigest()


def _flat(content):
    """The whole of a row's content as text, images and other blocks included, so a change in any of them shows."""
    return content if isinstance(content, str) else json.dumps(content, sort_keys=True, default=str)


def repeat_turns(rows):
    """The session telling the same thing over and over with nothing changing in between, or None.

    Rows are a transcript's timed rows in order. A turn starts at a prompt (or a typed /reload-*, a turn of its own
    with nothing in it); its tool calls, their inputs and their results are its work. A streak is REPEAT_TURNS or more
    turns at the transcript's end, each ended, with the same work and the same error, else the same last assistant
    text, else the same prompt and reply (after _norm), spanning REPEAT_MIN minutes or more. A turn still running ends any streak,
    so long work never reads as one. Returns {kind, text, count, since, digest}; kind is error, assistant, user or
    reload."""
    turns = []
    for row in rows:
        t, kind = row.get("timestamp") or "", row.get("type")
        msg = row.get("message") if isinstance(row.get("message"), dict) else {}
        content = msg.get("content")
        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
        if reload_row(row):
            if kind == "user" and not row.get("isMeta"):  # one turn per command, not per row it leaves
                turns.append({"reload": content.split("</command-name>")[0].split(">")[-1], "at": t, "end": t})
            continue
        real = next((x for x in reversed(turns) if "reload" not in x), None)
        if kind == "user" and not row.get("isMeta"):
            results = [b for b in blocks if b.get("type") == "tool_result"]
            text = content if isinstance(content, str) else " ".join(str(b.get("text") or "") for b in blocks
                                                                     if b.get("type") == "text")
            if (text.strip() or blocks) and not results:  # an image with no text is a prompt too
                turns.append({"user": _norm(text if text.strip() else _flat(blocks)), "assistant": "", "error": "",
                              "work": [], "at": t, "end": t, "ended": False})
            elif real and results:
                real["work"] += [("result", _work(b.get("content")), bool(b.get("is_error"))) for b in results]
                real.update(end=t, ended=False)
            continue
        if not real:  # the tail starts inside a turn whose prompt it cut off
            continue
        if kind == "assistant":
            tools = [b for b in blocks if b.get("type") == "tool_use"]
            real["work"] += [("tool", b.get("name"), _work(b.get("input"))) for b in tools]
            text = " ".join(str(b.get("text") or "") for b in blocks if b.get("type") == "text")
            if text.strip():
                real["assistant"] = _norm(text)
            if row.get("isApiErrorMessage"):
                real.update(error=_norm(text), ended=True)
            elif msg.get("stop_reason") == "end_turn":
                real["ended"] = True
            elif tools:
                real["ended"] = False
            real["end"] = t
        elif kind == "system" and row.get("level") == "error":
            real.update(error=_norm(_flat(row.get("content"))), ended=True, end=t)
        elif kind == "system" and row.get("subtype") == "turn_duration":
            real["ended"] = True

    def span_ok(streak):
        if len(streak) < REPEAT_TURNS:
            return False
        a, b = fleet.parse(streak[0]["at"]), fleet.parse(streak[-1]["end"])
        return bool(a and b) and (b - a).total_seconds() >= REPEAT_MIN * 60

    def found(kind, text, streak):
        return {"kind": kind, "text": text[:160], "count": len(streak), "since": streak[0]["at"],
                "digest": _digest(f"{kind}|{text}")[:12]}

    real = [x for x in turns if "reload" not in x]
    after = real[-1]["end"] if real else ""
    reloads = [x for x in turns if "reload" in x and x["at"] > after]
    if real and not real[-1]["ended"]:
        return None
    if span_ok(reloads):
        return found("reload", " ".join(dict.fromkeys(x["reload"] for x in reloads)), reloads)
    if not real:
        return None
    for kind in ("error", "assistant", "user"):
        text, work = real[-1][kind], real[-1]["work"]
        if not text:
            continue
        streak = []
        for x in reversed(real):
            # The same prompt with a reply that changed may be news: only an unchanged (or absent) reply counts.
            if not x["ended"] or x[kind] != text or x["work"] != work \
                    or kind == "user" and x["assistant"] != real[-1]["assistant"]:
                break
            streak.append(x)
        if span_ok(streak[::-1]):
            return found(kind, text, streak[::-1])
    return None


def scan_transcript(path, entry, since):
    """Parse only the bytes appended since the last scan.

    Claude Code writes one row per content block, each repeating the message's usage, so rows are
    folded by message.id: the last row of a message counts once, when the next message starts.
    """
    size = path.stat().st_size
    if size < entry.get("offset", 0):
        entry = {}
    entry.setdefault("offset", 0)
    entry.setdefault("committed", {})
    # A cache written before motion existed, or before step_motion's rules last changed: read the tail once.
    if entry["offset"] and entry.get("motion_v") != MOTION_V:
        entry["motion"] = None
        with path.open("rb") as f:
            f.seek(max(0, entry["offset"] - 262144))
            for line in f.read(entry["offset"] - f.tell()).splitlines()[1:]:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("timestamp"):
                    entry["motion"] = step_motion(entry.get("motion") or {}, row)
    entry["motion_v"] = MOTION_V
    with path.open("rb") as f:
        f.seek(entry["offset"])
        chunk = f.read(size - entry["offset"])
    end = chunk.rfind(b"\n") + 1  # a half-written last line waits for the next scan
    for line in chunk[:end].splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("timestamp"):
            entry["motion"] = step_motion(entry.get("motion") or {}, row)
        msg = row.get("message") if isinstance(row.get("message"), dict) else {}
        usage = msg.get("usage")
        if row.get("type") != "assistant" or not usage or (row.get("timestamp") or "") < since:
            continue
        usage = {k: int(usage.get(k) or 0) for k in USAGE_KEYS}
        if entry.get("pending_id") not in (None, msg.get("id")):
            entry["committed"] = _add(entry["committed"], entry.get("pending") or {})
        entry["pending_id"], entry["pending"] = msg.get("id"), usage
    entry["offset"] += end
    return entry


def collect_usage(dash, workers, cfg):
    cache_path = dash / "usage-cache.json"
    cache = read_json(cache_path, {}) or {}
    files = cache.setdefault("files", {})
    since = cfg.get("created_at", "").replace("+00:00", "Z")[:19]
    root, per_worker, total, motions = projects_dir(), {}, {}, {}
    for w in workers:
        if not w.get("worktree_path") or not w.get("transcript_expected", False):
            continue  # No transcript location; usage remains unknown.
        d = transcript_dir(w["worktree_path"])
        if not d.is_dir():
            continue
        sums, motion = {}, None
        start = iso(prog.parse_ts(w["since"]) - dt.timedelta(minutes=5)) if w.get("since") else ""
        for f in sorted(d.glob("*.jsonl")):
            try:
                files[str(f)] = scan_transcript(f, files.get(str(f), {}), since)
            except OSError:
                continue
            e = files[str(f)]
            sums = _add(_add(sums, e["committed"]), e.get("pending") or {})
            m = e.get("motion") or {}
            # The session written to last is the live one; one older than this dispatch belongs to an earlier
            # worker of a reused worktree.
            if m.get("since", "") > (motion or {}).get("since", "") and m["since"] >= start:
                motion = m
        if sums:
            per_worker[w["dispatch"]] = {**sums, "total": sum(sums.values())}
        if motion:
            motions[w["dispatch"]] = motion
    # Two workers can share a worktree over time; count each directory once for the program total.
    seen_dirs = set()
    for w in workers:
        if w["dispatch"] in per_worker and w.get("worktree_path") not in seen_dirs:
            seen_dirs.add(w.get("worktree_path"))
            total = _add(total, per_worker[w["dispatch"]])
    write_atomic(cache_path, json.dumps(cache))
    return {"total": {**total, "total": sum(total.values())} if total else None, "workers": per_worker,
            "motion": motions}


# ---------------------------------------------------------------- summary and series

# Minutes before an in-flight worker counts as stalled when session evidence is available.
IDLE_MIN = 15        # its turn ended and nothing it armed (ScheduleWakeup, Monitor, background job) will wake it
START_MIN = 10       # dispatched, but its session has written nothing since
TOOL_MIN = 45        # one tool call still running (a CI watch past this is worth a look)
# A turn should be writing: the last row is input (a prompt, a tool result, a task-notification) and nothing
# followed. A notification that lands after the turn ended can sit there without ever starting a turn.
SILENT_MIN = 20


def parked_tickets(events):
    """Tickets the lead put on hold (`record parked --ticket`) and has not admitted since."""
    last = {}
    for e in events:
        if e["ev"] in ("parked", "admitted") and e.get("ticket") and not e.get("pr"):
            last[e["ticket"]] = e["ev"]
    return {t for t, ev in last.items() if ev == "parked"}


def stalls(workers, tnow, held=frozenset()):
    """`held`: parked tickets, whose idle worker waits on purpose (often on the human) and wakes nobody."""
    import autowake
    out, idle_min = [], autowake.minutes("stall_idle_min") or IDLE_MIN
    mins = lambda ts: (tnow - prog.parse_ts(ts)).total_seconds() / 60
    for w in workers:
        m = w.get("motion") or {}
        if w.get("ticket") in held:
            continue
        if w.get("outcome") != "in_progress":
            continue
        row = {"dispatch": w["dispatch"], "ticket": w.get("ticket") or w.get("worktree")}
        if not m and w.get("transcript_expected", False) and w.get("since") and mins(w["since"]) >= START_MIN:
            out.append({**row, "kind": "start_unconfirmed", "minutes": round(mins(w["since"]))})
        elif m.get("state") == "idle" and not m.get("parked") and mins(m["since"]) >= idle_min:
            out.append({**row, "kind": "idle", "minutes": round(mins(m["since"]))})
        elif m.get("state") == "tool" and mins(m["since"]) >= TOOL_MIN:
            out.append({**row, "kind": "long_tool", "minutes": round(mins(m["since"])), "tool": m.get("tool"),
                        "detail": m.get("detail")})
        elif m.get("state") == "working" and mins(m["since"]) >= SILENT_MIN:
            out.append({**row, "kind": "silent", "minutes": round(mins(m["since"]))})
    return out


_CONTAINS = {}
_cache_lock = threading.RLock()  # guards _CONTAINS: the loops read and renew it while a save rebuilds it
# After one failed compare gh is not called again for this long, and never while core quota is below GH_MIN_CORE
# (until its reset). The quota is read with `gh api rate_limit`, which costs none. A flat window for plain failures,
# since the error text carries no reset time.
GH_BACKOFF_S = 300
GH_MIN_CORE = 500
GH_CHECK_S = 60
_gh_down_until = 0.0  # time.monotonic() before which no compare is asked
_gh_check_at = 0.0
_gh_lock = threading.Lock()


def gh_quota_low():
    """Seconds until core quota resets when it is below GH_MIN_CORE, else 0. Unreadable counts as fine: the compare
    then fails and backs off by itself."""
    try:
        r = sh(["gh", "api", "rate_limit", "--jq", "{n: .resources.core.remaining, t: .resources.core.reset}"], timeout=15)
        return max(0.0, r["t"] - time.time()) if r["n"] < GH_MIN_CORE else 0.0
    except (SourceError, KeyError, TypeError):
        return 0.0


GH_COMPARES_PER_ROUND = 40  # new compare questions one warm-up may ask; the rest wait for the next round
_budget = [GH_COMPARES_PER_ROUND]
_cache_state = {"loaded": False, "dirty": False}
CACHE_MAX = 20000  # answers kept; the oldest go first


def cache_path():
    return home() / ".compare-cache.json"


def load_compare_cache():
    """Answers survive a restart: a commit's ancestry never changes, and every restart used to ask them all again."""
    if _cache_state["loaded"]:
        return
    disk = read_compare_cache()
    with _cache_lock:
        for k, v in disk.items():
            _CONTAINS.setdefault(k, v)
        _cache_state["loaded"] = True


def read_compare_cache():
    out = {}
    try:
        data = read_json(cache_path(), {})
        for repo, pairs in (data.items() if isinstance(data, dict) else ()):
            for k, v in (pairs.items() if isinstance(pairs, dict) else ()):
                older, _, newer = k.partition("...")
                if newer and isinstance(v, bool):
                    out[(repo, older, newer)] = v
    except Exception as e:  # a damaged cache is a cold cache, never a failed collect
        log(f"compare cache ignored: {e!r}")
    return out


def cached(key):
    """The remembered answer, moved to the young end so that eviction keeps what the current ledgers still ask."""
    with _cache_lock:
        v = _CONTAINS.pop(key, None)
        if v is not None:
            _CONTAINS[key] = v
        return v


def remember(key, value):
    with _cache_lock:
        _CONTAINS[key] = value
        _cache_state["dirty"] = True


def save_compare_cache():
    if not _cache_state["dirty"]:
        return
    try:
        with cache_path().with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)  # the server and a hand `collect` both save: merge what the other wrote
            disk = read_compare_cache()
            with _cache_lock:  # the loops read and renew the dict meanwhile
                merged = {k: v for k, v in disk.items() if k not in _CONTAINS}  # the other's go first
                merged.update(_CONTAINS)
                _CONTAINS.clear()
                _CONTAINS.update(merged)
                # The file keeps the newest CACHE_MAX answers; memory keeps twice that, so a ledger's active pairs
                # (a few thousand, renewed by cached() every round) are never evicted into "unknown" while pairs of
                # programs long gone do leave. Oldest first: the dict keeps insertion order.
                for k in list(_CONTAINS)[:-2 * CACHE_MAX]:
                    del _CONTAINS[k]
                out = {}
                for (repo, older, newer), v in list(_CONTAINS.items())[-CACHE_MAX:]:
                    out.setdefault(repo, {})[f"{older}...{newer}"] = v
                _cache_state["dirty"] = False
            write_atomic(cache_path(), json.dumps(out))
    except OSError as e:
        _cache_state["dirty"] = True
        log(f"compare cache not saved: {e!r}")


def local_contains(repo_dir, older, newer):
    """The answer from a local clone, or None when there is none or it does not know a commit. Replace refs and
    grafts rewrite ancestry, and a shallow clone's "no" may only mean the history was cut: those go to GitHub."""
    git = ["git", "--no-replace-objects", "-C", str(repo_dir)]
    run = lambda *a: subprocess.run(git + list(a), capture_output=True, text=True, timeout=15)
    try:
        grafts = run("rev-parse", "--git-path", "info/grafts").stdout.strip()
        if grafts and (pathlib.Path(repo_dir) / grafts).exists():
            return None
        shallow = run("rev-parse", "--is-shallow-repository").stdout.strip() != "false"  # read before the answer
        rc = run("merge-base", "--is-ancestor", older, newer).returncode
        if rc == 1 and shallow:
            return None
    except (OSError, subprocess.TimeoutExpired):
        return None
    return {0: True, 1: False}.get(rc)


def gh_contains(repo, older, newer, repo_dir=None):
    """Whether commit `older` is in `newer`'s history: True or False, or None when unknown. A local clone
    (`repo_dir`) answers first at no cost to GitHub. Answers are cached on disk, since commits do not change. An
    unknown one is not cached, but it stops further gh calls: for GH_BACKOFF_S after a failure, until the reset when
    the quota is low, and past GH_COMPARES_PER_ROUND new questions in a warm-up round."""
    global _gh_down_until, _gh_check_at
    load_compare_cache()
    key = (repo, older, newer)
    if (hit := cached(key)) is not None:
        return hit
    if repo_dir and (known := local_contains(repo_dir, older, newer)) is not None:
        remember(key, known)
        return known
    with _gh_lock:  # the slow and fast loops share the quota check and the backoff: one asker at a time
        if (hit := cached(key)) is not None:
            return hit
        if _budget[0] <= 0:
            return None
        if time.monotonic() < _gh_down_until:
            return None
        if time.monotonic() >= _gh_check_at:
            wait = gh_quota_low()
            _gh_check_at = time.monotonic() + GH_CHECK_S
            if wait:
                _gh_down_until = time.monotonic() + wait
                return None
        _budget[0] -= 1
        try:
            st = sh(["gh", "api", f"repos/{repo}/compare/{older}...{newer}", "--jq", "{s: .status}"])["s"]
        except (SourceError, KeyError, TypeError):
            _gh_down_until = time.monotonic() + GH_BACKOFF_S  # counted from the failure, not from before a 45 s call
            _gh_check_at = 0.0  # when the backoff ends the quota is read first: a limit hit then waits for the reset
            return None
        if st not in ("ahead", "identical", "behind", "diverged"):  # anything else is not an answer
            return None
        remember(key, answer := st in ("ahead", "identical"))
        return answer


GAP_MAX_AGE_D = 14  # a landing still without a main CI row after this long is stale history, not a gap to chase


def ledger_gaps(cfg, events, workers, prs, tnow, contains=None):
    """What happened that the ledger never heard about. Cap, land order and CI state are computed from the
    ledger, so each gap makes them wrong; the fix is to record, never to keep a side file."""
    mapped = {d for e in events if e["ev"] == "spawned"
              for d in re.findall(r"ctx_[0-9a-f]+", " ".join(str(e.get(k) or "") for k in ("note", "dispatch")))}
    landed = {e.get("pr") for e in events if e["ev"] == "landed"}
    decided = {e.get("sha") for e in events if e["ev"] in ("main_green", "main_red")} | set(prog.stack_lower(events))
    # Merged into another PR's branch (a top-down fold, `land --onto-lower`): its commit reaches the base only in
    # that PR's landing, whose CI run is the one to record.
    heads = {x.get("branch") for x in prs} - {None}
    folded = {x["number"] for x in prs if x.get("base") in heads}
    # A branch CI started by hand runs at the branch tip, so a green there covers every earlier landing it contains.
    contains = contains or (lambda older, newer: gh_contains(cfg["repo"], older, newer, cfg.get("repo_dir")))
    greens = [g for g in events if g["ev"] == "main_green" and g.get("sha")]
    # An unknown answer (None) counts as covered: a gap is reported only when GitHub said the green does not contain it.
    covered = lambda e: any(g["sha"] != e["sha"] and prog.parse_ts(g["ts"]) >= prog.parse_ts(e["ts"])
                            and contains(e["sha"], g["sha"]) is not False for g in greens)
    t0 = prog.parse_ts(cfg["created_at"])
    # Only the bases this program lands into: another session's merge into main is not this ledger's gap.
    ours = {x.get("base") for x in prs if x["number"] in landed} | {x.get("branch") for x in prs if x["number"] in landed}
    ours = (ours | {cfg.get("base")}) - {None}
    return {
        # A Dispatch whose Orca Task (or worktree name) gives its ticket needs no `spawned` line.
        "spawns": [w["dispatch"] for w in workers if w.get("outcome") == "in_progress" and w["dispatch"] not in mapped
                   and not w.get("ticket")],
        "landings": sorted(p["number"] for p in prs if p["state"] == "merged" and p.get("merged_at")
                           and prog.parse_ts(p["merged_at"]) >= t0 and p["number"] not in landed
                           and (not ours or p.get("base") in ours)),
        "ci": [e.get("pr") for e in events if e["ev"] == "landed" and e.get("sha") not in decided and e.get("pr") not in folded
               and dt.timedelta(hours=2) < tnow - prog.parse_ts(e["ts"]) <= dt.timedelta(days=GAP_MAX_AGE_D)
               and not covered(e)],
    }


def spare(summary, tasks):
    """Free landing units under the cap and the Orca tasks whose deps are done. Slots are offered only when the
    next move (the rule `orch status` prints) is "may spawn N more"; otherwise its line says why not.
    A human-gate `Land #N` task is the lead's, never a worker's, so it is not offered."""
    if not (summary.get("next") or "").startswith("may spawn"):
        return {"slots": 0, "ready": [], "held": summary.get("next")}
    done = {t["id"] for t in tasks if t["status"] == "completed"}
    ready = [t.get("ticket") or t["title"] for t in tasks
             if t["status"] in ("ready", "pending") and set(t["deps"]) <= done
             and not (t["title"] or "").startswith(prog.GATE_TASK)]
    return {"slots": max(0, (summary["cap"] or 0) - (summary["in_flight"] or 0)), "ready": ready}


def summarize(cfg, events, issues, workers, tnow, order, leftover=0):
    pred = cfg.get("predicate") or []
    by_id = {i["id"]: i for i in issues or []}
    done = [p for p in pred if by_id.get(p, {}).get("state_type") == "completed"]
    tickets_done = (bool(pred) and len(done) == len(pred) and not prog.open_admitted(events, by_id)
                    if issues is not None else None)
    verified = prog.final_check_current(events)
    roles = {e.get("note") for e in events if e["ev"] == "spawned" and e.get("role")}
    live = [w for w in workers if w.get("outcome") == "in_progress" and w["dispatch"] not in roles]
    units = prog.live_units(events, [w["dispatch"] for w in live], {w["dispatch"]: w["ticket"] for w in live if w.get("ticket")})
    ready = prog.ready_prs(events)
    human = [s for s in ready if cfg.get("merge_policy") == "human-gate" and "approved" not in s]
    main, cap = prog.main_state(events), prog.cap_from(events, cfg.get("ceiling", 6))
    within = lambda e, h: tnow - prog.parse_ts(e["ts"]) <= dt.timedelta(hours=h)
    landed = [e for e in events if e["ev"] == "landed"]
    triage = triage_map(events)
    derived = [i for i in issues or [] if i.get("derived")]
    pr_tk = prog.pr_ticket(events)
    acted = {e.get("ticket") for e in events if e["ev"] == "spawned"} | {pr_tk.get(e.get("pr")) for e in landed}
    nxt, used = prog.next_move(cfg, events, tnow, tickets_done=tickets_done,
                               live=units, human=len(human), leftover=leftover,
                               order=[e for e in order if e.get("state") not in ("gone", "unknown")])
    return {
        "predicate_total": len(pred), "predicate_done": len(done) if issues is not None else None,
        "final_check": verified, "resting": resting(verified, events[-1].get("ts") if events else None, tnow),
        "main": main, "cap": cap, "ceiling": cfg.get("ceiling", 6),
        "in_flight": units, "ready_to_land": len(ready), "human_wait": len(human),
        "landed_total": len(landed), "landed_24h": sum(1 for e in landed if within(e, 24)),
        "derived_total": len(derived) if issues is not None else None,
        "derived_per_item": round(len(derived) / max(len(pred), 1), 2) if issues is not None else None,
        "parked": sum(1 for v in triage.values() if v == "parked"),
        "admitted": sum(1 for v in triage.values() if v == "admitted"),
        "budget_used": round(used, 3) if used is not None else None, "stopped": prog.stopped(events),
        "ready_prs": [s["pr"] for s in ready], "human_wait_prs": [s["pr"] for s in human],
        "idle_waiting": [w["dispatch"] for w in live if w.get("activity") == "waiting"],
        "stale_workers": [w["dispatch"] for w in workers if w.get("outcome") == "in_progress" and w.get("stale")],
        # Work that already landed or started on a ticket, or its close, is the lead's answer.
        "untriaged": [i["id"] for i in derived if not i.get("triage") and i.get("state_type") in prog.OPEN_STATES
                      and i["id"] not in acted],
        "next": nxt,
    }


def series_row(t, cfg, events, issues, in_flight):
    """One point of the time series. `events` must already be cut at t."""
    pred = cfg.get("predicate") or []
    base = {"t": iso(t), "in_flight": in_flight, "ready": len(prog.ready_prs(events)),
            "landed": sum(1 for e in events if e["ev"] == "landed"), "cap": prog.cap_from(events, cfg.get("ceiling", 6))}
    if issues is None:
        return {**base, "done": None, "open": None, "derived": None}
    admitted = {e["ticket"] for e in events if e["ev"] == "admitted" and e.get("ticket")}
    scope = set(pred) | admitted
    by_id = {i["id"]: i for i in issues}
    done = open_ = 0
    for tid in scope:
        i = by_id.get(tid, {})
        fin = ts(i.get("completed_at"))
        cancel = ts(i.get("canceled_at"))
        if i.get("state_type") == "completed" and (fin is None or fin <= t):
            done += 1
        elif not (i.get("state_type") == "canceled" and (cancel is None or cancel <= t)):
            open_ += 1
    derived = sum(1 for i in issues if i.get("derived") and (ts(i.get("created_at")) or t) <= t)
    return {**base, "done": done, "open": open_, "derived": derived}


def backfill(cfg, events, issues):
    """Rebuild the series from timestamps once, so a dashboard started mid-program is not empty.

    Historical in-flight is approximated from the ledger: a spawned ticket counts until its PR is ready.
    """
    t0 = prog.parse_ts(cfg["created_at"])
    stamps = {prog.parse_ts(e["ts"]) for e in events}
    for i in issues or []:
        for k in ("created_at", "completed_at"):
            if ts(i.get(k)) and ts(i.get(k)) > t0:
                stamps.add(ts(i.get(k)))
    rows, last = [], None
    for t in sorted(s for s in stamps if s >= t0):
        cut = [e for e in events if prog.parse_ts(e["ts"]) <= t]
        spawned = {e["ticket"] for e in cut if e["ev"] == "spawned" and e.get("ticket")}
        finished = {e["ticket"] for e in cut if e["ev"] in ("ready", "verdict", "landed") and e.get("ticket")}
        row = series_row(t, cfg, cut, issues, len(spawned - finished))
        if last is None or strip_t(row) != strip_t(last):
            rows.append(row)
            last = row
    return rows


def strip_t(row):
    return {k: v for k, v in row.items() if k != "t"}


# ---------------------------------------------------------------- collect

def program_dir(slug):
    if not SLUG_RE.fullmatch(slug or ""):
        raise ValueError(f"bad slug {slug!r}")
    d = home() / slug
    if not (d / "program.json").exists():
        raise FileNotFoundError(f"no program '{slug}' at {d}")
    return d


_locks, _locks_guard = {}, threading.Lock()


def lock_for(slug):
    with _locks_guard:
        return _locks.setdefault(slug, threading.Lock())


FETCHERS = {"tracker": fetch_issues, "stages": fetch_stages, "github": fetch_prs, "orca": fetch_orca}


def collect(slug, sources=SOURCES, interval=None):
    """Write state.json. Sources not in `sources`, and sources that fail, keep their last good section.

    Fetching runs outside the per-program lock so a slow `gh` never delays a ledger refresh.
    """
    d = program_dir(slug)
    cfg = json.loads((d / "program.json").read_text())
    fetched = {}
    for name in sources:
        try:
            fetched[name] = (True, FETCHERS[name](cfg), iso(utcnow()))
        except SourceError as e:
            fetched[name] = (False, str(e), iso(utcnow()))
    if "github" in sources and not closed(slug):  # a program at rest has no gap worth a GitHub call
        warm_contains(d, cfg)
    with lock_for(slug):
        return _merge(slug, d, cfg, fetched, interval)


def dated(rows):
    """(events, bad ts): the rows every view below can parse a timezone-aware ts from."""
    events, bad = [], []
    for e in rows:
        ts = e.get("ts")
        try:
            if prog.parse_ts(ts).tzinfo is not None:  # a naive ts parses but fails every comparison with now
                events.append(e)
                continue
        except (ValueError, TypeError, AttributeError):
            pass
        bad.append(ts)
    return events, bad


def warm_contains(d, cfg):
    """Asks GitHub, outside the program lock, the compare questions ledger_gaps will put. _merge runs under that lock
    and the fast loop waits on it, so a gh call there stopped every collect behind it."""
    _budget[0] = GH_COMPARES_PER_ROUND
    try:
        prev = read_json(d / "dashboard" / "state.json", {}) or {}
        ledger_gaps(cfg, dated(ledger_events(d / "ledger.jsonl")[0])[0], [], prev.get("prs") or [], utcnow())
    except Exception as e:  # a warm-up is best effort: the merge reads whatever the cache holds
        log(f"{d.name}: compare warm-up failed: {e!r}")
    finally:
        save_compare_cache()


def landing_of(cfg, events, prs):
    """Per base branch: who holds the exclusive lane (ledger lock_acquired/lock_released) and who is ready behind it."""
    base_of = {p["number"]: p.get("base") for p in prs}
    ticket_of = {p["number"]: p.get("ticket") for p in prs}
    default = cfg.get("base") or "main"
    out = {}
    slot = lambda b: out.setdefault(b, {"holder": None, "queue": []})
    for e in events:
        if e["ev"] not in ("lock_acquired", "lock_released"):
            continue
        base = e.get("base") or base_of.get(e.get("pr")) or default
        if e["ev"] == "lock_acquired":
            slot(base)["holder"] = {"pr": e.get("pr"), "ticket": e.get("ticket") or ticket_of.get(e.get("pr")),
                                    "since": e["ts"]}
        else:
            h = slot(base)["holder"]
            if h and (e.get("pr") is None or h.get("pr") == e.get("pr")):
                slot(base)["holder"] = None
    holding = {s["holder"]["pr"] for s in out.values() if s["holder"]}
    for st in prog.ready_prs(events):
        if st["pr"] in holding:
            continue
        first = st.get("ready") or st.get("verdict")
        base = first.get("base") or base_of.get(st["pr"]) or default
        slot(base)["queue"].append({"pr": st["pr"], "ticket": first.get("ticket") or ticket_of.get(st["pr"]),
                                    "ready_since": first["ts"]})
    for s in out.values():
        s["queue"].sort(key=lambda q: q["ready_since"])
    return out


def land_order(events, pr_rows, cfg, now, tasks=None):
    return prog.land_order(events, pr_rows, cfg, now, deps=prog.deps_from_tasks(tasks or []))


def _merge(slug, d, cfg, fetched, interval):
    rows, rejected = ledger_events(d / "ledger.jsonl")
    events, bad = dated(rows)
    dash = d / "dashboard"
    dash.mkdir(exist_ok=True)
    read_key = file_key(dash / "state.json")  # before the read: a file replaced after it is not the one prev came from
    prev = read_json(dash / "state.json", {}) or {}
    kept = STATE_SOURCES.get(dash / "state.json")
    if kept and prev and kept[0] == file_key(dash / "state.json"):
        prev["sources"] = kept[1]  # successes a skipped write left only in memory; a file another process wrote since wins
    srcs = {k: dict(v) for k, v in (prev.get("sources") or {}).items() if isinstance(v, dict)}
    # The ledger is read on every collect, so its error is recomputed rather than carried over.
    errors = [e for e in prev.get("errors") or [] if e.split(":", 1)[0] not in fetched and not e.startswith("ledger:")]
    tnow = utcnow()
    if bad:  # every view below parses ts, so those lines are left out rather than failing the whole collect
        errors.append(f"ledger: {len(bad)} event(s) with a bad ts left out, first {bad[0]!r}")
    if rejected:
        errors.append(f"ledger: {len(rejected)} row(s) without an ev left out, first {rejected[0]!r:.80}")

    def outcome(name):
        """(value, fresh): fresh only when this collect fetched the source successfully."""
        if name not in fetched:
            return None, False
        ok, val, at = fetched[name]
        if ok:
            srcs[name] = {"updated_at": at, "ok": True}
            return val, True
        # The timestamp stays at the last success, so the UI shows how old the data on screen is.
        srcs[name] = {"updated_at": (srcs.get(name) or {}).get("updated_at"), "ok": False, "error": val,
                      "failed_at": at}
        errors.append(f"{name}: {val}")
        return None, False

    raw_issues, fresh = outcome("tracker")
    if fresh and raw_issues is None:
        srcs["tracker"]["configured"] = False
        issues = None
    elif fresh:
        issues = shape_issues(raw_issues, cfg, events)
    else:
        issues = shape_issues(prev["issues"], cfg, events) if prev.get("issues") is not None else None
    known = {i["id"] for i in issues or []}
    raw_stages, fresh = outcome("stages")
    stages = raw_stages if fresh else prev.get("stages")
    if fresh and raw_stages is None:
        srcs["stages"]["configured"] = False

    raw_prs, fresh = outcome("github")
    prev_pr = {p["number"]: p for p in prev.get("prs") or []}
    prs = ([shape_pr(p, events, known, prev_pr.get(p["number"])) for p in raw_prs] if fresh
           else [refresh_pr(p, events) for p in prev.get("prs") or []])
    # Raw open rows feed land_order; kept beside state.json so a ledger-only refresh can re-rank them.
    rows_path = dash / "pr_rows.json"
    if fresh:
        pr_rows = [{k: p.get(k) for k in ("number", "headRefOid", "mergeStateStatus", "isDraft",
                                          "statusCheckRollup", "baseRefName")}
                   for p in raw_prs if p.get("state") == "OPEN"]
        write_atomic(rows_path, json.dumps(pr_rows))
    else:
        pr_rows = read_json(rows_path, [])
    raw_orca, fresh_orca = outcome("orca")
    if fresh_orca:
        events = prog.with_orca_spawns(events, raw_orca.get("workers") or [], raw_orca.get("tasks") or [])
    try:
        # Orca down: rank on the last shaped tasks, or dependent PRs would look ready.
        dep_tasks = raw_orca.get("tasks") if fresh_orca else [
            {"id": t["id"], "display_name": t["title"], "status": t["status"], "deps": t["deps"]}
            for t in prev.get("tasks") or []]
        order = land_order(events, pr_rows, cfg, tnow, dep_tasks)
    except Exception as e:  # a bug in the ranking must not take the rest of the dashboard down
        order = prev.get("land_order") or []
        errors.append(f"land_order: {e!r}"[:300])
    landing = landing_of(cfg, events, prs)
    ticket_of = {p["number"]: p.get("ticket") for p in prs}
    if not fresh and not rows_path.exists():
        # GitHub has never answered: a missing row means "unknown", not "closed".
        for entry in order:
            if entry.get("state") == "gone":
                entry.update(state="unknown", reasons=["GitHub has not answered yet"])
    for entry in order:
        entry.setdefault("ticket", None)
        entry["ticket"] = entry["ticket"] or ticket_of.get(entry.get("pr"))

    if fresh_orca:
        workers = shape_workers(raw_orca["workers"], events, raw_orca["tasks"])
        tasks = shape_tasks(raw_orca["tasks"], workers)
        cards = [{"ticket": tk, "dispatch": d, "active": a, "path": path} for tk, d, a, path in prog.landed_but_open(
            events, raw_orca["workers"], raw_orca["tasks"], raw_orca.get("worktrees") or [], {i["id"]: i for i in issues or []})]
        objective = (raw_orca.get("run") or {}).get("objective")
        try:
            usage = collect_usage(dash, workers, cfg)
        except Exception as e:  # usage is optional; never block the rest of the collect
            usage = prev.get("usage")
            errors.append(f"usage: {e!r}"[:300])
    else:
        workers, tasks, cards = prev.get("workers") or [], prev.get("tasks") or [], prev.get("landed_open") or []
        objective, usage = prev.get("run_objective"), prev.get("usage")
    for w in workers:
        w["tokens"] = ((usage or {}).get("workers") or {}).get(w["dispatch"])
        w["motion"] = ((usage or {}).get("motion") or {}).get(w["dispatch"])
    srcs["ledger"] = {"updated_at": iso(tnow), "ok": True, "events": len(events)}

    notes = read_jsonl(dash / "notes.jsonl")
    summary = summarize(cfg, events, issues, workers, tnow, order, sum(not c["active"] for c in cards))
    summary["stalls"], summary["spare"] = stalls(workers, tnow, parked_tickets(events)), spare(summary, tasks)
    # Under the program lock: answers already fetched by warm_contains only, never a gh call.
    load_compare_cache()
    summary["gaps"] = ledger_gaps(cfg, events, workers, prs, tnow,
                                  contains=lambda older, newer: cached((cfg["repo"], older, newer)))
    open_prs = [p for p in prs if p["state"] == "open" and not p.get("draft") and p.get("created_at")]
    oldest = min(open_prs, key=lambda p: p["created_at"], default=None)
    summary["oldest_open_pr"] = ({"pr": oldest["number"], "since": oldest["created_at"],
                                  "age_h": round((tnow - prog.parse_ts(oldest["created_at"])).total_seconds() / 3600, 2)}
                                 if oldest else None)
    by_id = {i["id"]: i for i in issues or []}
    predicate = [{"id": p, **{k: by_id.get(p, {}).get(k) for k in ("title", "state", "state_type", "url")}}
                 for p in cfg.get("predicate") or []]

    hist_path = dash / "history.jsonl"
    history = read_jsonl(hist_path)
    t0 = prog.parse_ts(cfg["created_at"])
    first = min((t for t in (prog.parse_ts(e["ts"]) for e in events) if t >= t0), default=None)
    # `orch backfill` puts landings before the series began: rebuild it, as a rebuilt series starts at the first one.
    stale = bool(history) and first is not None and first < prog.parse_ts(history[0]["t"])
    if (not history or stale) and issues is not None:
        rebuilt = backfill(cfg, events, issues)
        if stale:
            # Keep what only a live collect saw (in-flight, tokens) and recount the ledger's values under it.
            start = prog.parse_ts(history[0]["t"])
            rebuilt = [r for r in rebuilt if prog.parse_ts(r["t"]) < start] + [
                {**r, **series_row(prog.parse_ts(r["t"]), cfg, [e for e in events if prog.parse_ts(e["ts"]) <= prog.parse_ts(r["t"])],
                                   issues, r.get("in_flight"))} for r in history]
        history = rebuilt
        if history:
            write_atomic(hist_path, "".join(json.dumps(r) + "\n" for r in history))
    row = series_row(tnow, cfg, events, issues, summary["in_flight"])
    if (usage or {}).get("total"):
        row["tokens"] = usage["total"]["total"]
    if not history or strip_t(history[-1]) != strip_t(row):
        with hist_path.open("a") as f:
            f.write(json.dumps(row) + "\n")
        history.append(row)
    horizon = max(t0, tnow - dt.timedelta(days=SERIES_DAYS))
    series = [r for r in history if prog.parse_ts(r["t"]) >= horizon]
    before = [r for r in history if prog.parse_ts(r["t"]) < horizon]
    if before and (not series or prog.parse_ts(series[0]["t"]) > horizon):
        # The values in force when the window opens, so every line starts at its left edge.
        series.insert(0, {**before[-1], "t": iso(horizon)})

    state = {
        "generated_at": iso(tnow), "slug": slug, "repo": cfg.get("repo"), "run": cfg.get("run"),
        "merge_policy": cfg.get("merge_policy"), "created_at": cfg.get("created_at"),
        "deadline": cfg.get("deadline"), "interval": interval or prev.get("interval") or 60,
        "run_objective": objective, "summary": summary, "predicate": predicate, "landing": landing,
        "land_order": order, "landed_open": cards, "stages": stages, "tasks": tasks, "graph": build_graph(tasks, events, issues, order, landing), "usage": usage,
        "issues": issues, "prs": sorted(prs, key=lambda p: -p["number"]), "workers": workers,
        "series": series, "series_from": iso(horizon), "activity": activity_of(events, notes), "notes": notes[-100:][::-1],
        "errors": errors, "sources": srcs,
    }
    write_state(dash / "state.json", state, read_key)
    return state


STATE_BEAT_S = 90  # longest an unchanged state.json goes unwritten: prog.py reads one older than 3 minutes as stale
STATE_WROTE = {}   # path -> (digest of the state without what moves by itself, monotonic time of the write)
STATE_SOURCES = {}  # path -> (file_key of the file then on disk, the `sources` of a state that was not written)


def file_key(path):
    try:
        s = path.stat()
        return (s.st_mtime_ns, s.st_size)
    except OSError:
        return None


def settled(state):
    """The state minus what changes on its own between two collects: when it ran, each source's last good time, and
    the start of the series window (a moving 30-day horizon). Everything else is data."""
    s = {k: v for k, v in state.items() if k not in ("generated_at", "series_from")}
    s["sources"] = {n: {k: v for k, v in src.items() if k != "updated_at"} for n, src in (state.get("sources") or {}).items()}
    s["series"] = [{**r, "t": None} if r.get("t") == state.get("series_from") else r for r in state.get("series") or []]
    s["land_order"] = [{k: v for k, v in e.items() if k != "age_h"} for e in state.get("land_order") or []]  # a clock, not data
    if isinstance(s.get("summary"), dict) and isinstance(s["summary"].get("oldest_open_pr"), dict):
        s["summary"] = {**s["summary"], "oldest_open_pr": {k: v for k, v in s["summary"]["oldest_open_pr"].items() if k != "age_h"}}
    return hashlib.sha1(json.dumps(s, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def write_state(path, state, read_key):
    """A ~0.5 MB file rewritten per program every 20-30 s was most of this server's disk traffic. Rewrite it when the data
    changed, and otherwise every STATE_BEAT_S so generated_at still says the collector is alive."""
    digest, now = settled(state), time.monotonic()
    last = STATE_WROTE.get(path)
    if last and last[0] == digest and now - last[1] < STATE_BEAT_S and path.exists():
        # The next collect reads prev from disk: it must still see these times. Kept only for the file this state was built
        # from; if another process replaced it since the read, theirs is newer and nothing of ours is remembered.
        if file_key(path) == read_key:
            STATE_SOURCES[path] = (read_key, state["sources"])
        else:
            STATE_SOURCES.pop(path, None)
        return False
    write_atomic(path, json.dumps(state, ensure_ascii=False, indent=1))
    STATE_WROTE[path] = (digest, now)
    STATE_SOURCES.pop(path, None)
    return True


# ---------------------------------------------------------------- serve

ORCA_EVERY = 20      # local CLI, no network: keep workers under the UI's 60 s warn line
LEDGER_EVERY = 30    # rewrite even when nothing changed, so the ledger's freshness stays honest
FAST_TICK = 3


def programs():
    root = home()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "program.json").exists() and SLUG_RE.fullmatch(p.name))


LEDGER_TAIL = {}  # ledger path -> ((mtime_ns, size), (closed, last event ts)); the sidebar polls every program's ledger


def ledger_tail(slug):
    f = program_dir(slug) / "ledger.jsonl"
    try:
        s = f.stat()
        key = (s.st_mtime_ns, s.st_size)
    except OSError:
        key = None
    hit = LEDGER_TAIL.get(f)
    if not hit or hit[0] != key:
        events = ledger_events(f)[0]
        hit = LEDGER_TAIL[f] = (key, (prog.final_check_current(events), events[-1].get("ts") if events else None))
    return hit[1]


QUIET = dt.timedelta(days=3)  # the sidebar folds a program away at the same line (fleet.js ARCHIVE_MS)


def resting(done, last_ts, now=None):
    """A timestamp that does not parse counts as moving: the fast loop runs every program, and one bad ledger line
    must not stop it for the rest (_merge reports it on the program's row)."""
    try:
        quiet = bool(last_ts) and (now or dt.datetime.now(dt.timezone.utc)) - prog.parse_ts(last_ts) > QUIET
    except (ValueError, TypeError, AttributeError):
        quiet = False
    return bool(done) or quiet


def closed(slug):
    """A program with a current final check, or no ledger event for QUIET, has stopped moving, so only its ledger is
    still watched. A landing, a predicate edit or any other new event reopens it."""
    try:
        return resting(*ledger_tail(slug))
    except FileNotFoundError:  # listed by programs(), then its directory was removed: nothing left to watch
        return True


def signature(slug):
    d = home() / slug
    out = []
    for p in (d / "ledger.jsonl", d / "dashboard" / "notes.jsonl", d / "program.json"):
        try:
            s = p.stat()
            out.append((s.st_mtime_ns, s.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def log(msg):
    print(f"[dash {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def slow_round(interval, first):
    with ROUND_LOCK:
        ROUND_PRS[0] = {}  # the PR lists programs on one repo share, for this round only
    try:
        for slug in programs():
            try:  # one program's failure, closed() and its directory vanishing included, must not end the round
                if not first and closed(slug):
                    continue
                st = collect(slug, SOURCES if first else ("tracker", "stages", "github"), interval)
                if st["errors"]:
                    log(f"{slug}: " + " | ".join(st["errors"]))
            except Exception as e:  # keep serving; the next round retries
                log(f"{slug}: collect failed: {e!r}")
    finally:
        with ROUND_LOCK:
            ROUND_PRS[0] = None


# The GitHub stage (tracker, PRs, compare) runs every GITHUB_EVERY_S, set by `dashboard.github_every_s` in
# ~/.claude/agent-skills.json. 5 minutes: CI and review state move on that scale, a landing or a manual refresh
# runs it at once, and one round costs a request per open PR page plus the compares, so 60 s spent the hourly quota.
GITHUB_EVERY_S, GITHUB_EVERY_MIN_S, GITHUB_EVERY_MAX_S = 300, 60, 86400
SLOW_WAKE = threading.Event()  # set: run the GitHub stage now (a landing, a manual refresh)
_warned = []


def github_every():
    path = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG") or "~/.claude/agent-skills.json").expanduser()
    try:
        n = (json.loads(path.read_text()).get("dashboard") or {}).get("github_every_s")
    except (OSError, ValueError, AttributeError):
        n = None
    if isinstance(n, int) and not isinstance(n, bool) and GITHUB_EVERY_MIN_S <= n <= GITHUB_EVERY_MAX_S:
        return n
    if not _warned:
        _warned.append(1)
        log(f"dashboard.github_every_s is not set to an integer from {GITHUB_EVERY_MIN_S} to {GITHUB_EVERY_MAX_S} in {path}; "
            f"using {GITHUB_EVERY_S}s")
    return GITHUB_EVERY_S


_landings = {}


def note_landings(slug):
    """Wakes the GitHub stage when a landed or main_green row is new in this program's ledger: the PR list and the
    CI answer change then, and waiting out the period would show the old state."""
    n = sum(e.get("ev") in ("landed", "main_green") for e in ledger_events(program_dir(slug) / "ledger.jsonl")[0])
    if n > _landings.get(slug, 0):  # serve() seeds the programs it starts with, so a new one counts from zero
        SLOW_WAKE.set()
    _landings[slug] = n


def slow_loop(interval):
    """Tracker and GitHub: network and rate limits, so once per github_every() unless something wakes it."""
    first = True
    while True:
        SLOW_WAKE.clear()
        try:
            slow_round(github_every(), first)  # the page's freshness warning is a multiple of this
        except Exception as e:  # programs() itself failed: log and try again next round
            log(f"slow loop round failed: {e!r}")
        first = False
        beat()
        tick("slow")
        SLOW_WAKE.wait(github_every())


def fast_round(state, interval):
    seen, last_orca, last_write = state
    now = time.monotonic()
    for slug in programs():
        try:
            sig = signature(slug)
            orca_due = now - last_orca.get(slug, now) >= ORCA_EVERY and not closed(slug)
            last_orca.setdefault(slug, now)
            ledger_due = seen.get(slug) not in (None, sig) or now - last_write.get(slug, now) >= LEDGER_EVERY
            last_write.setdefault(slug, now)
            if seen.get(slug) != sig:
                note_landings(slug)
            seen[slug] = sig
            if not (orca_due or ledger_due):
                continue
            collect(slug, ("orca",) if orca_due else (), interval)
            last_write[slug] = now
            if orca_due:
                last_orca[slug] = now
        except Exception as e:
            log(f"{slug}: refresh failed: {e!r}")
        finally:
            tick("fast")  # progress, not only a finished round: many slow collects must not read as a stopped loop


def fast_loop(interval):
    """Ledger and notes on change (and every LEDGER_EVERY s), Orca every ORCA_EVERY s."""
    state = ({}, {}, {})
    while True:
        time.sleep(FAST_TICK)
        beat()
        try:
            fast_round(state, github_every())
        except Exception as e:  # programs() itself failed: the loop must outlive it
            log(f"fast loop round failed: {e!r}")
        tick("fast")  # a collect that hangs stops the tick, and ensure restarts the server


def code_loop():
    """Apart from the collectors: a dead or stuck collector must not freeze the stale check with it."""
    while True:
        time.sleep(CODE_CHECK_S)
        try:
            check_code()
            check_loops()
        except Exception as e:
            log(f"code check failed: {e!r}")


def health_view():
    """stale also means a collector loop stopped: the heartbeat alone and the code hash both look fine then."""
    return {**HEALTH, "stale": bool(HEALTH.get("stale")) or not loops_alive(HEALTH)}


def check_loops():
    """The fast loop stopped finishing rounds (a call that never returns): under launchd, exit so it starts again,
    instead of waiting for someone to run `ensure`. Elsewhere /api/health reports stale and the banner says what to run."""
    if HEALTH.get("stale"):
        return  # other code is on disk: check_code restarts into it, but only after it has imported it
    if not loops_alive(HEALTH, RESTART_STALE) and under_agent():
        log(f"fast loop silent since {HEALTH['ticks'].get('fast')}; exiting so launchd starts it again")
        restart()


STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/fleet.js": ("fleet.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "orchestrate-dash"

    def log_message(self, *args):
        pass

    def send(self, code, body=b"", ctype="application/json", etag=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        if HEALTH.get("version"):  # an open page compares it with the one it loaded under, and reloads on a change
            self.send_header("X-Dash-Version", HEALTH["version"])
        if etag:
            self.send_header("ETag", etag)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in STATIC:
            name, ctype = STATIC[path]
            return self.send(200, (ASSETS / name).read_bytes(), ctype)
        if path.startswith("/api/") and not self.local_only():
            return self.send(403, b'{"error":"forbidden"}')
        if path == "/api/health":
            return self.send(200, json.dumps(health_view()).encode())
        if path.startswith("/api/fleet/"):
            return self.fleet_get(path)
        if path == "/api/programs":
            rows = []
            for slug in programs():
                cfg = read_json(home() / slug / "program.json", {})
                st = read_json(home() / slug / "dashboard" / "state.json", {}) or {}
                done, last_event = ledger_tail(slug)
                rows.append({"slug": slug, "repo": cfg.get("repo"), "run": cfg.get("run"),
                             "merge_policy": cfg.get("merge_policy"), "generated_at": st.get("generated_at"),
                             "summary": st.get("summary"), "errors": len(st.get("errors") or []),
                             # the sidebar folds a program away once it is closed or quiet with no lead on screen
                             "closed": done, "last_event": last_event})
            return self.send_cached(json.dumps(rows, ensure_ascii=False).encode())
        m = re.fullmatch(r"/api/([^/]+)/state", path)
        if m:
            slug = m.group(1)
            try:
                d = program_dir(slug)
            except (ValueError, FileNotFoundError) as e:
                return self.send(404, json.dumps({"error": str(e)}).encode())
            f = d / "dashboard" / "state.json"
            if not f.exists():
                collect(slug, sources=())  # ledger-only, cheap; the background loop fills the rest
            return self.send_cached(f.read_bytes())
        self.send(404, b'{"error":"not found"}')

    def send_cached(self, body, ctype="application/json"):
        etag = '"' + hashlib.sha1(body).hexdigest()[:20] + '"'
        if self.headers.get("If-None-Match") == etag:
            return self.send(304, etag=etag)
        return self.send(200, body, ctype, etag=etag)

    def local_only(self):
        """Host and Origin must name this server: a page on another site (or a rebinding DNS name) is refused."""
        port = HEALTH.get("port")
        ok = {f"127.0.0.1:{port}", f"localhost:{port}"}
        origin = self.headers.get("Origin")
        return self.headers.get("Host") in ok and (origin is None or origin.split("//", 1)[-1] in ok)

    def fleet_get(self, path):
        if path == "/api/fleet/state":
            # Only the page says it is looking (?viewed=1, app.js on a visible tab). A script or a curl reading the file
            # used to count as a view and kept the 10 s Orca poll and the per-tick PR probes running.
            if re.search(r"[?&]viewed=1\b", self.path):
                if time.monotonic() - FLEET_VIEWED[0] > FLEET_VIEW_S:
                    FLEET_FORCE[0] = True  # the page just opened: runs and every open PR now, not when their turn comes
                    FLEET_WAKE.set()
                FLEET_VIEWED[0] = time.monotonic()
            f = fleet.state_dir() / "state.json"
            if not f.exists():
                return self.send(503, b'{"error":"first fleet collect has not finished"}')
            return self.send_cached(f.read_bytes())
        if path == "/api/fleet/skills":
            f = fleet.state_dir() / "skills.json"
            if not f.exists():
                return self.send(503, b'{"error":"first skill usage collect has not finished"}')
            return self.send_cached(f.read_bytes())
        if path == "/api/fleet/tokens":
            f = token_usage.out_path()
            if not f.exists():
                return self.send(503, b'{"error":"first token usage collect has not finished"}')
            return self.send_cached(f.read_bytes())
        if path == "/api/fleet/stats":
            f = pr_stats.out_path()
            if not f.exists():
                return self.send(503, b'{"error":"first PR stats collect has not finished"}')
            return self.send_cached(f.read_bytes())
        if path == "/api/fleet/jobs":
            # Read on every poll, not folded into state.json: a tick that fails never rewrites state.json.
            return self.send_cached(json.dumps(jobs.snapshot(JOBS), ensure_ascii=False).encode())
        if path == "/api/fleet/pressure":
            hours = re.search(r"[?&]hours=(\d{1,2})\b", self.path)
            return self.send_cached(json.dumps(pressure_report(int(hours.group(1)) if hours else 6), ensure_ascii=False).encode())
        if path in ("/api/fleet/flow", "/api/fleet/changes"):
            return self.send_cached(flow_report(path.rsplit("/", 1)[1]))
        if path == "/api/fleet/brief":
            return self.send_cached(json.dumps(brief_report(), ensure_ascii=False).encode())
        if path == "/api/fleet/units":
            return self.send_cached(json.dumps(units_report(), ensure_ascii=False).encode())
        if path == "/api/fleet/sent":
            # The `orch sent` log is read on every poll, like the brief: a send shows on the next poll, not the next collect.
            days = re.search(r"[?&]days=(\d{1,2})\b", self.path)
            n = max(1, min(int(days.group(1)), sent.DAYS_MAX)) if days else 1
            cache = SENT_CACHES.setdefault(n, RenderCache(lambda v: "error" not in v, sent_signature))
            return self.send_cached(json.dumps(cache.get(lambda: sent_render(n)), ensure_ascii=False).encode())
        if path == "/api/fleet/token":
            return self.send(200, json.dumps({"token": TOKEN}).encode())
        m = re.fullmatch(r"/api/fleet/avatar/([A-Za-z0-9-]{1,39}(?:\[bot\])?)", path)
        png = m and fleet.state_dir() / "avatars" / f"{m.group(1)}.png"
        if png and png.exists():
            return self.send_cached(png.read_bytes(), "image/png")
        self.send(404, b'{"error":"not found"}')

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not self.local_only() or self.headers.get("X-Dash-Token") != TOKEN:
            return self.send(403, b'{"error":"forbidden"}')
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(n, 65536)) or b"{}")
        except ValueError:
            return self.send(400, b'{"error":"bad json"}')
        if path == "/api/fleet/dismiss" and isinstance(body.get("key"), str) and FLEET:
            fleet.dismiss(FLEET, body["key"])
        elif (path in ("/api/fleet/ack", "/api/fleet/done") and isinstance(body.get("ids"), list)
              and all(isinstance(i, str) for i in body["ids"])):
            written, refused = (acks.write if path.endswith("ack") else board.close)(body["ids"][:500], undo=body.get("undo") is True)
            FLEET_WAKE.set()
            return self.send(200, json.dumps({"written": written, "refused": refused}).encode())
        elif path == "/api/fleet/send" and isinstance(body.get("session"), str) and isinstance(body.get("text"), str):
            handle = body.get("handle") if isinstance(body.get("handle"), str) else None
            code, out = fleet.send(body["session"], body["text"], handle)
            FLEET_WAKE.set()
            return self.send(code, json.dumps(out).encode())
        elif path == "/api/refresh":  # runs the GitHub stage now instead of at its next period
            SLOW_WAKE.set()
            return self.send(200, b'{"ok":true}')
        elif path == "/api/fleet/open" and isinstance(body.get("session"), str):
            code, out = fleet.open_session(body["session"])
            return self.send(code, json.dumps(out).encode())
        elif path == "/api/fleet/decide" and all(isinstance(body.get(k), str) for k in ("decision", "answer")):
            code, out = fleet.decision_answer(body["decision"], body["answer"])
            FLEET_WAKE.set()
            return self.send(code, json.dumps(out).encode())
        elif path == "/api/fleet/prompt" and all(isinstance(body.get(k), str) for k in ("session", "prompt", "action")):
            code, out = fleet.answer_prompt(body["session"], body["prompt"], body["action"])
            FLEET_WAKE.set()
            return self.send(code, json.dumps(out).encode())
        else:
            return self.send(404, b'{"error":"not found"}')
        FLEET_WAKE.set()
        self.send(200, b'{"ok":true}')


TOKEN = hashlib.sha256(os.urandom(32)).hexdigest()
FLEET = None
FLEET_VIEWED, FLEET_WAKE, FLEET_FORCE = [0.0], threading.Event(), [False]
FLEET_VIEW_S, FLEET_IDLE_EVERY = 300, 60  # poll Orca every 10 s only while a page has polled in the last 5 min
CHECK = selfcheck.SelfCheck()


# Longest each fleet file goes unwritten while only its clocks moved, and the clock fields themselves. board.py reads
# state.json's age in minutes; stage_auto.warning reads memory.json's stage_auto_at against an hours-long limit, and
# flow.py shows a store stale by its mtime.
# registry.py judges fleetmem stale at 300 s and prs at 1800 s, by mtime
FLEET_BEAT_S = {"state.json": 30, "memory.json": 120, "prs.json": 600}
FLEET_CLOCKS = {"state.json": ("generated_at",), "memory.json": ("stage_auto_at",)}


def fleet_digest(name, text):
    """The text of one fleet file without the fields that only record when it was built: those move every tick."""
    if name not in FLEET_CLOCKS:
        return text
    try:
        data = json.loads(text)
    except ValueError:
        return text
    built = data.get("generated_at")
    for k in FLEET_CLOCKS[name]:
        data.pop(k, None)
    if name == "state.json":
        data["sources"] = {n: {k: v for k, v in s.items() if k != "updated_at"} for n, s in (data.get("sources") or {}).items()}
        # An item with no time of its own is stamped with the tick's: that is the clock again, not news.
        data["items"] = [{k: v for k, v in i.items() if not (k == "at" and v == built)} if isinstance(i, dict) else i
                         for i in data.get("items") or []]
    return hashlib.sha1(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def quiet_save(f, **files):
    """Fleet.save for the served process. The text it compares differs on every tick (generated_at, each source's
    updated_at, stage_auto_at), so a 0.7 MB state.json and a 0.8 MB memory.json were rewritten every 10-16 s with nothing new
    in them. Written when the data changed, else every FLEET_BEAT_S[name]."""
    wrote, now = f.__dict__.setdefault("wrote", {}), time.monotonic()
    for name, text in files.items():
        digest = fleet_digest(name, text)
        last = wrote.get(name)
        # Only a file that carries a clock needs the beat; the others hold the same bytes until their data changes.
        if last and last[0] == digest and now - last[1] < FLEET_BEAT_S.get(name, float("inf")) \
                and (fleet.state_dir() / name).exists():
            continue
        fleet.write_atomic(fleet.state_dir() / name, text)
        wrote[name] = (digest, now)
        f.saved[name] = text


def fleet_wait():
    return fleet.ORCA_EVERY if time.monotonic() - FLEET_VIEWED[0] < FLEET_VIEW_S else FLEET_IDLE_EVERY


def fleet_loop():
    while True:
        try:
            force, FLEET_FORCE[0] = FLEET_FORCE[0], False
            st = FLEET.tick(force=force, viewed=time.monotonic() - FLEET_VIEWED[0] < FLEET_VIEW_S)
            errs = [f"{k}: {v['error']}" for k, v in ((st or {}).get("sources") or {}).items() if v.get("error")]
            if errs:
                log("fleet: " + " | ".join(errs))
            # A source that failed is the sources panel's; the tick itself ran.
            jobs.record("fleet-tick", result=f"Needs you {len((st or {}).get('items') or [])}"
                        + (f" · source errors: {' | '.join(errs)}" if errs else ""), next_in=fleet_wait(), event=FLEET.event)
        except Exception as e:  # keep serving; the next tick retries
            log(f"fleet: tick failed: {e!r}")
            jobs.record("fleet-tick", error=repr(e), next_in=fleet_wait(), event=FLEET.event)
        try:
            relayed, reloaded = fleet.relay_decisions(), fleet.retry_reloads()
            for did in relayed:
                log(f"fleet: relayed the answer to {did}")
            for h in reloaded:
                log(f"fleet: reloaded {h}")
            jobs.record("relay", n=len(relayed) + len(reloaded), result=f"answers relayed {len(relayed)} · reloads sent {len(reloaded)}",
                        next_in=fleet_wait(), event=FLEET.event)
        except Exception as e:
            log(f"fleet: relay failed: {e!r}")
            jobs.record("relay", error=repr(e), next_in=fleet_wait(), event=FLEET.event)
        try:
            closed = tidy.run()
            for r in closed:
                log(f"fleet: tidy closed {r['id']} ({r['rule']}: {r['evidence']})")
            jobs.record("tidy", n=len(closed), result=f"closed {len(closed)}", next_in=fleet_wait(), event=FLEET.event)
        except Exception as e:
            log(f"fleet: tidy failed: {e!r}")
            jobs.record("tidy", error=repr(e), next_in=fleet_wait(), event=FLEET.event)
        try:
            if CHECK.due():
                was = CHECK.last
                if CHECK.run(FLEET):
                    FLEET.checks = CHECK.items
                    FLEET_WAKE.set()  # tick again now, so the page shows the items without waiting a full interval
                if CHECK.last != was:  # it read; without a state yet it did not
                    jobs.record("selfcheck", n=len(CHECK.items), result=f"Needs you items {len(CHECK.items)}",
                                next_in=selfcheck.GRACE_S if CHECK.seen - CHECK.confirmed else selfcheck.EVERY, event=FLEET.event)
        except Exception as e:
            log(f"fleet: self-check failed: {e!r}")
            jobs.record("selfcheck", error=repr(e), next_in=selfcheck.EVERY, event=FLEET.event)
        FLEET_WAKE.wait(fleet_wait())
        FLEET_WAKE.clear()


SKILLS_EVERY = 900


def skills_loop():
    while True:
        f = fleet.state_dir() / "skills.json"
        try:
            if not f.exists() or time.time() - f.stat().st_mtime >= SKILLS_EVERY:
                rep = skill_usage.refresh()
                added = (rep.get("signals") or {}).get("added")
                if added:
                    log(f"skills: {added} new usage signal(s) in {skill_usage.signals_path()}")
                jobs.record("skill-usage", n=added or 0, result=f"new usage signals {added or 0}", next_in=SKILLS_EVERY,
                            event=FLEET.event)
        except Exception as e:
            log(f"skills: collect failed: {e!r}")
            jobs.record("skill-usage", error=repr(e), next_in=SKILLS_EVERY, event=FLEET.event)
        time.sleep(60)


TOKENS_EVERY = 900


def tokens_loop():
    while True:
        f = token_usage.out_path()
        try:
            if not f.exists() or time.time() - f.stat().st_mtime >= TOKENS_EVERY:
                rep = token_usage.refresh()
                c = rep.get("collect") or {}
                jobs.record("token-usage", n=len(rep["warnings"]),
                            result=f"warnings {len(rep['warnings'])} · {c.get('parsed', 0)} transcripts read in {c.get('seconds', 0)}s",
                            next_in=TOKENS_EVERY, event=FLEET.event)
        except Exception as e:
            log(f"tokens: collect failed: {e!r}")
            jobs.record("token-usage", error=repr(e), next_in=TOKENS_EVERY, event=FLEET.event)
        time.sleep(60)


PR_STATS_EVERY = 6 * 3600  # dozens of searches a collect; counts by month do not need fresher
PR_STATS_RETRY = 3600  # after a failure, mostly a rate limit or an expired login


def pr_stats_loop():
    f = pr_stats.out_path()
    last = read_json(f) if f.exists() else None
    due = f.stat().st_mtime + (PR_STATS_RETRY if last and last.get("error") else PR_STATS_EVERY) if last else 0
    while True:
        if time.time() >= due:
            try:
                pr_stats.refresh(guard=True)
                due = time.time() + PR_STATS_EVERY
                jobs.record("pr-stats", result="counts refreshed", next_in=PR_STATS_EVERY, event=FLEET.event)
            except Exception as e:
                log(f"pr stats: collect failed: {e!r}")
                pr_stats.record_error(e)
                due = time.time() + PR_STATS_RETRY
                jobs.record("pr-stats", error=repr(e), next_in=PR_STATS_RETRY, event=FLEET.event)
        time.sleep(60)


# orch-dash's own jobs, as the Automations page lists them: (jobs.record key, name, how often in short, cadence in
# words, the job whose run this one rides on). The page folds a rider under that job's row.
JOBS = (
    ("fleet-tick", "Fleet tick", jobs.short(fleet.ORCA_EVERY),
     f"every {fleet.ORCA_EVERY}s while a page is open, else {FLEET_IDLE_EVERY}s", None),
    ("tidy", "Self-clean (tidy)", None, "after each fleet collect", "fleet-tick"),
    ("relay", "Decision relay · reload retry", None, "after each fleet collect", "fleet-tick"),
    ("selfcheck", "Self-check", jobs.short(selfcheck.EVERY),
     f"every {selfcheck.EVERY // 60}m, {selfcheck.GRACE_S}s while a finding waits for its second read", None),
    ("skill-usage", "Skill usage collect", jobs.short(SKILLS_EVERY), f"every {SKILLS_EVERY // 60}m", None),
    ("token-usage", "Token usage collect", jobs.short(TOKENS_EVERY), f"every {TOKENS_EVERY // 60}m", None),
    ("pr-stats", "PR stats collect", jobs.short(PR_STATS_EVERY),
     f"every {PR_STATS_EVERY // 3600}h, {PR_STATS_RETRY // 3600}h after a failure", None),
)


def code_files():
    # Every module the server may import, lazily too, from beside it and the two other places board.py and fleet.py
    # reach: one left out would not restart it.
    near = [*HERE.glob("*.py"), HERE.parents[1] / "brief" / "scripts" / "table_width.py",
            *(HERE.parents[2] / "scripts" / n for n in ("sync.py", "reap_hard.py", "skill_tokens.py"))]
    return (*sorted(f for f in near if f.exists() and not f.name.startswith("test_")), *sorted(ASSETS.glob("*")))


def code_version():
    """Changes whenever the served code does."""
    h = hashlib.sha1()
    for f in code_files():
        h.update(f.read_bytes())
    return h.hexdigest()[:12]


def code_mtime():
    """Newest file time of the code. Two installs sharing a store (a checkout and the plugin cache) differ in
    version; only the newer one replaces the other, so they never take turns killing each other."""
    return max(f.stat().st_mtime for f in code_files())


def git_sha():
    try:
        out = subprocess.run(["git", "-C", str(HERE), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return (out.stdout.strip() or None) if out.returncode == 0 else None  # None outside a checkout (the plugin cache)


CODE_CHECK_S = 30


def check_code():
    """Marks the server stale once the code on disk is not the code it runs (a merge landed after it started), so the
    page can say so instead of serving old routes as 404s."""
    try:
        disk = code_version()
    except OSError:  # a file mid-replace: the next check reads it whole
        return
    settled = disk == HEALTH.get("disk_version")  # same on two checks: a checkout is not still writing files
    HEALTH.update(disk_version=disk, stale=disk != HEALTH.get("version"))
    if HEALTH["stale"] and settled and under_agent() and HEALTH.get("broken") != disk:
        if not loads():  # keep serving the old code with its stale banner rather than crash-loop on a bad commit
            HEALTH["broken"] = disk
            log(f"code on disk ({disk}) does not import; still serving {HEALTH['version']}")
            return
        log(f"code on disk changed ({HEALTH['version']} -> {disk}); exiting so launchd starts the new code")
        restart()


def loads():
    try:
        return subprocess.run([sys.executable, "-c", "import dash"], cwd=HERE, capture_output=True,
                              timeout=60).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def restart():
    os._exit(0)  # KeepAlive starts it again at once: it ran longer than ThrottleInterval


HEALTH = {"heartbeat": None}


def beat():
    HEALTH["heartbeat"] = iso(utcnow())


LOOP_STALE = dt.timedelta(minutes=5)
# The server restarts itself later than health reports stale: a long but healthy first round (many pages, a big
# transcript scan) must not put launchd in a restart loop. At most one self-restart per this long.
RESTART_STALE = dt.timedelta(minutes=15)


def tick(name):
    """When each collector loop last finished a round. The heartbeat alone says the process lives: the slow loop
    kept it fresh while the fast loop was dead."""
    HEALTH.setdefault("ticks", {})[name] = iso(utcnow())


def loops_alive(h, limit=LOOP_STALE):
    """False when the fast loop has not finished a round for `limit`. A server that reports no ticks (older
    code) counts as alive; the version check restarts it anyway."""
    ticks = h.get("ticks")
    if not isinstance(ticks, dict):
        return True
    try:
        return utcnow() - prog.parse_ts(ticks["fast"]) <= limit
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def watch_migrate(poll=0.5, kill=os.kill, port=None):
    """A server that slipped past the start checks (they are not atomic with the start) ends itself once `orch db
    migrate` holds its lock, so it never writes the state during the import. It leaves its port first: the migrate
    starts it again from that, whether or not its own probes saw it."""
    while True:
        time.sleep(poll)
        if store_db.cutover_running():
            log(MIGRATING)
            if port:
                store_db.mark_dash_yielded(port)
            kill(os.getpid(), signal.SIGTERM)
            return


def serve(host, port, interval, make_fleet=None):
    global FLEET
    home().mkdir(parents=True, exist_ok=True)
    # The collectors' `orca` children inherit this cwd; started from a worktree that is later removed, every one
    # dies at startup with uv_cwd ENOENT. The store outlives any worktree.
    os.chdir(home())
    lock = (home() / ".dash.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # held for the life of the process
    except BlockingIOError:
        if not under_agent():
            sys.exit(f"another orch-dash already serves {home()} (see {home() / '.dash.json'})")
        # Exiting would only make launchd retry every ThrottleInterval; wait and take over the moment it goes.
        log(f"another orch-dash serves {home()} (pid {(read_json(home() / '.dash.json') or {}).get('pid')}); "
            "waiting for its store lock")
        fcntl.flock(lock, fcntl.LOCK_EX)
    httpd = http.server.ThreadingHTTPServer((host, port), Handler)  # bind before the loops start
    if store_db.cutover_running():  # checked again after the bind: a migrate that starts later finds this server up
        sys.exit(MIGRATING)
    HEALTH.update(pid=os.getpid(), store=str(home()), version=code_version(), mtime=code_mtime(), port=port,
                  started_at=iso(utcnow()), git=git_sha(), script=str(HERE / "dash.py"))
    check_code()
    beat()
    tick("slow"), tick("fast")  # a fresh process is not a stuck one
    write_atomic(home() / ".dash.json", json.dumps(HEALTH))
    for slug in programs():  # the first slow round covers what is there; only later rows wake the stage
        try:
            note_landings(slug)
        except Exception as e:
            log(f"{slug}: landing baseline failed: {e!r}")
    SLOW_WAKE.clear()
    threading.Thread(target=slow_loop, args=(interval,), daemon=True).start()
    threading.Thread(target=fast_loop, args=(interval,), daemon=True).start()
    threading.Thread(target=code_loop, daemon=True).start()
    threading.Thread(target=watch_migrate, kwargs={"port": port}, daemon=True).start()
    if os.environ.get("ORCH_FLEET") != "off":
        FLEET = (make_fleet or fleet.Fleet)()
        FLEET.save = functools.partial(quiet_save, FLEET)
        threading.Thread(target=fleet_loop, daemon=True).start()
        if os.environ.get("ORCH_SKILLS") != "off":
            threading.Thread(target=skills_loop, daemon=True).start()
        if os.environ.get("ORCH_TOKENS") != "off":
            threading.Thread(target=tokens_loop, daemon=True).start()
        if os.environ.get("ORCH_PR_STATS") != "off":
            threading.Thread(target=pr_stats_loop, daemon=True).start()
    log(f"serving {home()} on http://{host}:{port}/ (tracker+github every {github_every()}s, "
        f"orca every {ORCA_EVERY}s, ledger every {FAST_TICK}s)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------- commands

def cmd_collect(argv):
    st = collect(argv[0])
    s = st["summary"]
    print(f"{argv[0]}: predicate {s['predicate_done']}/{s['predicate_total']} · main {s['main']} · "
          f"in-flight {s['in_flight']}/{s['cap']} · next: {s['next']}")
    for e in st["errors"]:
        print(f"  error: {e}")


def cmd_serve(argv):
    if store_db.cutover_running():  # a server that wins the race with `ensure`'s check still ends here
        sys.exit(MIGRATING)
    serve(prog.opt(argv, "--host", "127.0.0.1"), int(prog.opt(argv, "--port") or default_port()),
          int(prog.opt(argv, "--interval", "60")))


def brief_report():
    rep = periodic_report()
    return rep | {"stages": stage_report(rep.get("now_by_pr"), "now_by_pr" in rep and rep["now_by_pr"] is not None)}


RENDER_MAX_AGE_S = 30  # a render prints ages ("3 min ago"): never serve one older than this, whatever the inputs say
# The files the brief and units renders read, outside the store: the fleet's own, and the state root's beside it
# (janitor plan, patrol verdicts, pressure queue, pending reloads). A file the render starts reading goes here, or it
# shows up to RENDER_MAX_AGE_S late.
RENDER_FILES = ("state.json", "prs.json", "inbox-external.jsonl", "stages.json", "stages-auto.json", "tokens.json")
RENDER_ROOT_FILES = ("janitor-plan.json", "patrol-verdicts.json", "pressure-queue.json", "reload-pending.json")
def db_version():
    """The store's change mark: mtime and size of the DB and its -wal. A commit lands in the -wal (or in the DB itself
    without one), so `orch board set`, `decide add` or any other process moves it. No connection is opened here:
    registry.py lets store.py alone import sqlite3."""
    try:
        p = store_db.path()
    except OSError:
        return None
    return tuple(file_key(pathlib.Path(f"{p}{suffix}")) for suffix in ("", "-wal"))


def state_root():
    return pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser()


def keys_of(paths):
    return tuple(file_key(p) for p in paths)


def pressure_signature():
    """pressure.py history reads its own files beside the others in the state root: its history, queue, plan and reading."""
    return keys_of(sorted(state_root().glob("pressure*")) + [PRESSURE])


def sent_signature():
    """What /api/fleet/sent is built from: the fleet's sent rows (replaced once a runs cycle), and the `orch sent` log files."""
    f = FLEET
    log = sorted(sent.sent_dir().glob("*.jsonl")) + [fleet.state_dir() / "sends.jsonl"]
    return (id(getattr(f, "sent", None)), len(getattr(f, "sent", None) or []), getattr(f, "sent_inbox_since", None), keys_of(log))


def render_signature():
    """What the brief's render reads, cheaply: the collector's files by mtime and size, the store by db_version."""
    out = [db_version()]
    root = state_root()
    # board.py reads patrol-verdicts.json beside the fleet's dir, rowstate.py reads the janitor's under AGENT_SKILLS_STATE
    roots = [root] + ([fleet.state_dir().parent] if fleet.state_dir().parent != root else [])
    paths = [fleet.state_dir() / n for n in RENDER_FILES] + [r / n for r in roots for n in RENDER_ROOT_FILES]
    try:  # every program's ledger and config: the render reads them through the board and the stage table
        paths += [home() / s / n for s in programs() for n in ("ledger.jsonl", "program.json")]
    except OSError:
        pass
    for p in paths:
        try:
            s = p.stat()
            out.append((s.st_mtime_ns, s.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


class RenderCache:
    """One rendered answer, kept while its inputs' signature is unchanged and it is younger than RENDER_MAX_AGE_S.
    Only a good answer is kept: a timeout or a missing renderer is asked again on the next request."""

    def __init__(self, ok, signature=None):
        self.ok, self.signature, self.lock, self.sig, self.at, self.value = ok, signature, threading.Lock(), None, 0.0, None

    def get(self, build):
        with self.lock:  # a second request waits for the first render instead of starting its own child
            sig, now = (self.signature or render_signature)(), time.monotonic()
            if self.value is not None and sig == self.sig and now - self.at < RENDER_MAX_AGE_S:
                return self.value
            value = build()
            if self.ok(value):
                self.sig, self.at, self.value = sig, now, value
            else:
                self.value = None
            return value


BRIEF_CACHE = RenderCache(lambda v: "text" in v)
UNITS_CACHE = RenderCache(lambda v: "error" not in v)


def units_report():
    """units.py's build of the brief's own render, in a child process so a slow render times out here and not in the
    server. Rendered again only when its inputs changed (RenderCache): the units are a join, not a second source."""
    return UNITS_CACHE.get(units_render)


def units_render():
    try:
        r = subprocess.run([sys.executable, str(HERE / "units.py")], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return {"error": "units.py timed out after 30 s"}
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {"error": (r.stderr.strip().splitlines() or [f"units.py exited {r.returncode}"])[-1]}


def periodic_report():
    return BRIEF_CACHE.get(periodic_render)


def periodic_render():
    """The periodic report exactly as `orch brief render` prints it: the renderer is the one implementation of
    the table rules, so the page shows its output and never builds a second copy. Rendered again when the board, the
    collector's files or RENDER_MAX_AGE_S say so (RenderCache), so a `orch board set` shows on the next poll. A lead or card that never wrote to the board still shows,
    from its session and its report file. --no-refresh: this server's collector is what keeps state.json fresh, and a
    collect per poll while its orca source is down would hit the timeout on every one."""
    try:
        r = subprocess.run([sys.executable, str(HERE / "prog.py"), "brief", "render", "--periodic", "--json",
                            "--no-refresh"], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return {"message": "orch brief render timed out after 30 s"}
    try:
        out = json.loads(r.stdout)
    except ValueError:  # an empty board, or a renderer this checkout does not have yet: its own words
        return {"message": (r.stdout or r.stderr).strip() or f"orch brief render exited {r.returncode}"}
    keys = ("text", "full", "changed", "stale", "width_ok", "done", "open", "table_rows", "list_rows", "placement", "duplicates",
            "missing", "now_by_pr")
    return {k: out.get(k) for k in keys} | {"closed": closed_recent()}


PRESSURE = HERE.parents[1] / "reap-resources" / "scripts" / "pressure.py"


SENT_CACHES = {}  # days -> RenderCache, keyed by the fleet's sent rows and the log files


def sent_render(days):
    try:
        return sent.page(FLEET.sent if FLEET else [], days, inbox_since=getattr(FLEET, "sent_inbox_since", None))
    except Exception as e:  # the page says what failed (fleet.js shows "error") instead of never loading
        return {"error": f"sent: {type(e).__name__}: {e}"}


PRESSURE_CACHES = {}  # hours -> RenderCache: the page asks for 6, 24 and so on, each answer is its own


def pressure_report(hours=6):
    """reap-resources' pressure history, in a child process: its files, thresholds and config are that script's, so this
    server keeps no copy of them. It reads files only; the LaunchAgent's round does the measuring. Run again only when
    one of those files changed (RenderCache), else the page's poll re-read a day of history for nothing."""
    cache = PRESSURE_CACHES.setdefault(hours, RenderCache(lambda v: "error" not in v, pressure_signature))
    return cache.get(lambda: pressure_render(hours))


def pressure_render(hours):
    try:
        r = subprocess.run([sys.executable, str(PRESSURE), "history", "--hours", str(hours)], capture_output=True,
                           text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return {"error": "pressure.py history timed out after 15 s"}
    except OSError as e:
        return {"error": f"pressure.py history: {e}"}
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {"error": (r.stderr.strip().splitlines() or [f"pressure.py history exited {r.returncode}"])[-1]}


def closed_recent(days=7):
    """What was closed in the last days, newest first, for the page's Reopen buttons."""
    cut = iso(utcnow() - dt.timedelta(days=days))
    return sorted(({"id": k, **r, "reopen": tidy.can_reopen(k)} for k, r in tidy.closed().items() if (r.get("ts") or "") >= cut),
                  key=lambda r: r.get("ts") or "", reverse=True)


def stage_report(by_pr=None, now_known=True):
    """The stage table whole, generated rows and recorded ones, for the Stages page: the brief's text has only a line pointing here.
    Read per request, so an `orch stage set` shows on the next poll. Counted by stages.counted(), the brief line's count.
    by_pr: the same render's now_by_pr, each row's 지금; now_known False when that render failed."""
    try:
        state = fleet.read_json(fleet.state_dir() / "state.json", {}) or {}
        return acks.mark_stages(stages.counted(stages.table(state), state, by_pr=by_pr, now_known=now_known))
    except (OSError, ValueError) as e:
        return {"error": f"stages: {type(e).__name__}: {e}"}


FLOW_CACHE, FLOW_TTL = {}, {"flow": 30, "changes": 60}


def flow_report(kind):
    """Data flow and skill changes, built in the request at most once per TTL: stat, tails and a bounded git log."""
    at, body = FLOW_CACHE.get(kind, (0, None))
    if body is None or time.monotonic() - at > FLOW_TTL[kind]:
        try:
            # Before this process's first tick, the sessions come from the last state.json on disk.
            st = getattr(FLEET, "state", None) or fleet.read_json(fleet.state_dir() / "state.json", {}) or {}
            out = flow.flow(st.get("sessions")) if kind == "flow" else flow.changes()
        except Exception as e:  # the panel says what failed instead of never loading
            out = {"error": f"{kind}: {type(e).__name__}: {e}"}
        body = json.dumps(out, ensure_ascii=False).encode()
        FLOW_CACHE[kind] = (time.monotonic(), body)
    return body


def health(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            return json.loads(r.read())
    except (OSError, ValueError):
        return None


def default_port():
    """A restart keeps the URL the human bookmarked: the env, else the port this store last served on."""
    return int(os.environ.get("ORCH_DASH_PORT") or (read_json(home() / ".dash.json") or {}).get("port") or 4780)


def stop(h, port):
    """Identity checked through its own health answer: the pid is this store's server, not a stranger."""
    os.kill(h["pid"], signal.SIGTERM)
    until = time.monotonic() + 10
    while time.monotonic() < until and health(port):
        time.sleep(0.2)


# ---------------------------------------------------------------- launchd

MIGRATING = "orch db migrate is running: the dashboard server starts again when it ends"


def agent_label():
    return os.environ.get("ORCH_DASH_LABEL") or "io.github.jeongjaesoon.orch-dash"


def agent_plist():
    return pathlib.Path(f"~/Library/LaunchAgents/{agent_label()}.plist").expanduser()


def agent_target():
    return f"gui/{os.getuid()}/{agent_label()}"


def under_agent():
    return os.environ.get("XPC_SERVICE_NAME") == agent_label()  # launchd sets it to the job's label


def agent_status():
    """{"pid": int | None, "port": int} of the loaded LaunchAgent that serves this store, else None."""
    if sys.platform != "darwin" or not agent_plist().exists():
        return None
    try:  # a hand-edited plist must not break every `orch status`: treat it as no agent
        spec = plistlib.loads(agent_plist().read_bytes())
        store = (spec.get("EnvironmentVariables") or {}).get("PROGRAMS_HOME", "~/.claude/programs")
        args = spec.get("ProgramArguments") or []
        port = int(args[args.index("--port") + 1]) if "--port" in args else 4780
        if pathlib.Path(store).expanduser().resolve() != home().resolve():
            return None
        p = subprocess.run(["launchctl", "print", agent_target()], capture_output=True, text=True)
    except Exception:
        return None
    if p.returncode:
        return None  # written but not loaded (booted out): not a manager
    pid = re.search(r"^\tpid = (\d+)$", p.stdout, re.M)
    return {"pid": int(pid.group(1)) if pid else None, "port": port}


def kickstart(kill):
    subprocess.run(["launchctl", "kickstart", *(["-k"] if kill else []), agent_target()], capture_output=True)


def fresh(h):
    return bool(h.get("heartbeat")) and utcnow() - prog.parse_ts(h["heartbeat"]) <= dt.timedelta(minutes=5)


def ensure_agent(a):
    """launchd is the only manager: stop this store's servers it did not start, and kickstart it when its own
    is missing or runs code older than what is on disk. The code it runs is the plist's, not this caller's."""
    port, url = a["port"], f"http://127.0.0.1:{a['port']}/"
    last = (read_json(home() / ".dash.json") or {}).get("port")
    for p in dict.fromkeys((last, port)):
        if p and (h := health(p)) and h.get("store") == str(home()) and h.get("pid") != a["pid"]:
            stop(h, p)  # its store lock keeps launchd's server waiting
    h = health(port)
    if h and h.get("store") != str(home()):
        return False, f"port {port} serves another store ({h.get('store')}); fix {agent_plist()}"
    if h and h.get("pid") == a["pid"] and fresh(h) and not h.get("stale") and loops_alive(h):
        return True, f"dashboard: {url} (launchd)"
    if h and h.get("pid") == a["pid"] and fresh(h) and h.get("broken") == h.get("disk_version"):
        return True, f"dashboard: {url} (launchd; the code on disk does not import, so it keeps running {h['version']})"
    kickstart(kill=bool(h and h.get("pid") == a["pid"]))
    until = time.monotonic() + 20
    while time.monotonic() < until:
        h, a = health(port), agent_status() or a
        if h and h.get("pid") == a["pid"] and not h.get("stale") and loops_alive(h):
            return True, f"dashboard started by launchd: {url}"
        time.sleep(0.3)
    return False, f"launchd did not bring the dashboard up on port {port}; see {home() / '.dash.log'}"


def launchd_path():
    # launchd starts with a bare PATH; the collectors need the python3, git, gh and orca found now.
    dirs = [os.path.dirname(w) for w in map(shutil.which, ("python3", "git", "gh", "orca")) if w]
    return ":".join(dict.fromkeys(dirs + ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]))


def agent_spec(port):
    env = {"PATH": launchd_path()}
    env.update({k: os.environ[k] for k in ("PROGRAMS_HOME", "ORCH_DASH_LABEL", "ORCH_FLEET", "ORCH_FLEET_STATE",
                                           "ORCH_SKILLS", "ORCH_PR_STATS", "ORCH_CLAUDE_HOMES") if k in os.environ})
    return {"Label": agent_label(),
            "ProgramArguments": ["/usr/bin/env", "python3", str(HERE / "dash.py"), "serve", "--port", str(port)],
            "EnvironmentVariables": env, "WorkingDirectory": str(home()), "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 30,
            "StandardOutPath": str(home() / ".dash.log"), "StandardErrorPath": str(home() / ".dash.log")}


def install_agent(port):
    if store_db.cutover_running():
        return False, MIGRATING
    if sys.platform != "darwin":
        return False, "no launchd on this OS; `orch init` and `orch status` keep the dashboard up with `ensure`"
    if not (HERE.parents[2] / ".git").is_dir():  # a worktree or the plugin cache goes away under launchd
        return False, f"{HERE.parents[2]} is not a main git checkout; install from the checkout sessions load"
    domain, want = f"gui/{os.getuid()}", plistlib.dumps(agent_spec(port))
    if not (agent_plist().exists() and agent_plist().read_bytes() == want and agent_status()):
        home().mkdir(parents=True, exist_ok=True)
        agent_plist().parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["launchctl", "bootout", agent_target()], capture_output=True)
        agent_plist().write_bytes(want)
        for _ in range(5):  # bootstrap right after bootout can fail with error 5 until the old job is gone
            p = subprocess.run(["launchctl", "bootstrap", domain, str(agent_plist())], capture_output=True, text=True)
            if p.returncode == 0:
                break
            time.sleep(1)
        else:
            return False, f"launchctl bootstrap failed: {p.stderr.strip()}"
        print(f"wrote {agent_plist()} and loaded {agent_target()}")
    a = agent_status()
    return ensure_agent(a) if a else (False, f"{agent_target()} is not loaded")


def uninstall_agent():
    if sys.platform == "darwin":
        subprocess.run(["launchctl", "bootout", agent_target()], capture_output=True)
    agent_plist().unlink(missing_ok=True)
    return True, f"removed {agent_target()}; `ensure` starts the dashboard itself again"


def ensure(port):
    """(ok, message). Starts, restarts or moves only a server it can identify as this store's."""
    if store_db.cutover_running():  # a restarted session's `orch wait` must not bring the server back mid-import
        return False, MIGRATING
    if a := agent_status():
        return ensure_agent(a)
    last = (read_json(home() / ".dash.json") or {}).get("port")
    if last and last != port and (o := health(last)) and o.get("store") == str(home()):
        stop(o, last)  # its store lock would make the new server exit
    h, want = health(port), code_version()
    if h and h.get("store") == str(home()):
        stale = not fresh(h) or not loops_alive(h)
        if not stale and (h.get("version") == want or (h.get("mtime") or 0) >= code_mtime()):
            return True, f"dashboard: http://127.0.0.1:{port}/"
        stop(h, port)
    elif h:
        return False, f"port {port} serves another store ({h.get('store')}); pass --port"
    else:
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return False, f"port {port} is taken by something that is not orch-dash; pass --port"
    home().mkdir(parents=True, exist_ok=True)
    logf = (home() / ".dash.log").open("a")
    subprocess.Popen([sys.executable, str(HERE / "dash.py"), "serve", "--port", str(port)], stdout=logf,
                     stderr=logf, stdin=subprocess.DEVNULL, start_new_session=True, cwd=home())
    until = time.monotonic() + 10
    while time.monotonic() < until:
        h = health(port)
        if h and h.get("version") == want:
            return True, f"dashboard started: http://127.0.0.1:{port}/"
        time.sleep(0.2)
    return False, f"dashboard did not come up on port {port}; see {home() / '.dash.log'}"


def cmd_ensure(argv):
    ok, msg = ensure(int(prog.opt(argv, "--port") or default_port()))
    print(msg)
    sys.exit(0 if ok else 1)


def cmd_install_agent(argv):
    ok, msg = install_agent(int(prog.opt(argv, "--port") or default_port()))
    print(msg)
    sys.exit(0 if ok else 1)


def cmd_uninstall_agent(argv):
    print(uninstall_agent()[1])


def cmd_note(argv):
    slug, kind, text = argv[0], prog.opt(argv, "--kind"), prog.opt(argv, "--text")
    if kind not in NOTE_KINDS or not text:
        sys.exit(f"note needs --kind {'|'.join(NOTE_KINDS)} and --text")
    d = program_dir(slug) / "dashboard"
    d.mkdir(exist_ok=True)
    row = {"ts": iso(utcnow()), "kind": kind, "text": text.strip()[:500],
           "author": prog.opt(argv, "--author") or os.environ.get("USER")}
    with (d / "notes.jsonl").open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False))


def cmd_demo(argv):
    import dash_demo
    root = pathlib.Path(tempfile.mkdtemp(prefix="orchestrate-dash-demo-"))
    env = dash_demo.build(root)
    os.environ.update(env)
    dash_demo.seed_holds(root)
    dash_demo.seed_board()
    for slug in programs():
        collect(slug, interval=20)
    log(f"demo store at {root}")
    if "--no-serve" in argv:
        print(root)
        return
    world = dash_demo.FakeFleetWorld(utcnow(), crowd=int(prog.opt(argv, "--crowd", "0")), layout="--layout" in argv)
    world.seed_sent()
    world.seed_timeline()
    world.seed_jobs()
    if "--live" in argv:
        threading.Thread(target=dash_demo.live, args=(root, 20, world), daemon=True).start()
    serve("127.0.0.1", int(prog.opt(argv, "--port", "4780")), 20, make_fleet=world.fleet)


def cmd_fleet(argv):
    st = fleet.Fleet().tick(force=True) or {}
    kinds = {}
    for s in st.get("sessions", []):
        kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
    print(f"fleet: {len(st.get('sessions', []))} sessions {kinds} · {len(st.get('prs', []))} PRs · "
          f"inbox {st.get('counts', {})}")
    for k, v in (st.get("sources") or {}).items():
        if v.get("error"):
            print(f"  error: {k}: {v['error']}")


def cmd_skills(argv):
    skill_usage.main(argv)


def cmd_prstats(argv):
    pr_stats.main(argv)


def cmd_inbox(argv):
    if argv[:1] == ["add"]:
        kind, title = prog.opt(argv, "--type"), prog.opt(argv, "--title")
        if not kind or not title:
            sys.exit("inbox add needs --type and --title")
        row = fleet.inbox_add(kind, title, prog.opt(argv, "--session"), prog.opt(argv, "--url"),
                              prog.opt(argv, "--command"), prog.opt(argv, "--key"))
    elif argv[:1] == ["resolve"] and prog.opt(argv, "--key"):
        row = fleet.inbox_resolve(prog.opt(argv, "--key"))
    elif argv[:1] == ["list"]:
        for i in fleet.external_items():
            print(f"{i['at']}  {i['type']}  {i['key'][4:]}  {i['title']}")
        return
    else:
        sys.exit("usage: orch-dash inbox add --type T --title TEXT [...] | inbox resolve --key KEY | inbox list")
    print(json.dumps(row, ensure_ascii=False))


def cmd_ack(argv, undo=False):
    ids, can = list(itertools.takewhile(lambda a: not a.startswith("--"), argv)), None
    try:
        if not undo and (age := prog.opt(argv, "--done-before")):
            kinds = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "--kind"] or acks.KINDS
            old, found = acks.older_than(age, kinds)
            can = None if ids else found  # ids named as well may be of another kind: write() looks them up
            ids += old
        elif not ids:
            sys.exit("usage: orch-dash ack <id>... | ack --done-before AGE [--kind stage|board]... | unack <id>...")
        written, refused = acks.write(ids, undo=undo, can=can)
    except (ValueError, OSError) as e:
        sys.exit(str(e))
    print(f"{'unacked' if undo else 'acked'} {len(written)}" + (f": {' '.join(written)}" if written else ""))
    for aid, why in refused:
        print(f"refused {aid}: {why}", file=sys.stderr)
    sys.exit(1 if refused else 0)


def cmd_acks(argv):
    have = acks.acked()
    can = acks.ackable() if "--all" in argv else acks.ackable(kinds={acks.kind(k) for k in have})
    rows = [{"id": k, "acked": have.get(k), **can.get(k, {"label": None, "at": None})}
            for k in ({**can, **have} if "--all" in argv else have)]
    if "--json" in argv:
        return print(json.dumps(rows, ensure_ascii=False, indent=1))
    for r in rows:
        print(f"{r['id']}  {'acked ' + r['acked'] if r['acked'] else 'not acked'}  {r['label'] or '(gone)'}")
    print(f"{len(have)} acked" + (f" · {len(set(can) - set(have))} more can be acked" if "--all" in argv else ""))


def cmd_adopt(argv):
    sys.exit(fleet.adopt("--apply" in argv, prog.opt(argv, "--undo")))


COMMANDS = {"collect": cmd_collect, "serve": cmd_serve, "ensure": cmd_ensure, "note": cmd_note, "demo": cmd_demo,
            "fleet": cmd_fleet, "skills": cmd_skills, "prstats": cmd_prstats, "inbox": cmd_inbox, "adopt": cmd_adopt,
            "ack": cmd_ack, "unack": lambda argv: cmd_ack(argv, undo=True), "acks": cmd_acks,
            "install-agent": cmd_install_agent, "uninstall-agent": cmd_uninstall_agent}

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] not in COMMANDS or (args[0] in ("collect", "note") and len(args) < 2):
        sys.exit(__doc__)
    try:
        COMMANDS[args[0]](args[1:])
    except (ValueError, FileNotFoundError) as e:
        sys.exit(str(e))
