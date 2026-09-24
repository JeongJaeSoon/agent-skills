#!/usr/bin/env python3
"""Progress dashboard for an orchestrate program: JSON-first and model-free.

Usage: python3 dash.py <command> [options]

  collect <slug>                     rebuild <store>/<slug>/dashboard/state.json from program.json,
                                     the ledger, the tracker, GitHub and Orca (each source optional)
  serve [--port 4780] [--interval 60] [--host 127.0.0.1]
                                     serve the dashboard; full collect every --interval seconds,
                                     ledger and notes re-read every 3 s
  note <slug> --kind risk|digest|decision --text TEXT [--author NAME]
                                     add a human-readable line to the dashboard
  demo [--port 4780] [--live] [--no-serve]
                                     a realistic fake program in a temp store, served offline

Store: $PROGRAMS_HOME or ~/.claude/programs. The dashboard writes only under <slug>/dashboard/.
"""
import datetime as dt, hashlib, http.server, json, os, pathlib, re, subprocess, sys, tempfile, threading, time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import prog  # noqa: E402  ledger arithmetic lives there; never re-derive cap or main state here

ASSETS = HERE.parent / "assets" / "dashboard"
SOURCES = ("tracker", "github", "orca")
NOTE_KINDS = ("risk", "digest", "decision")
SERIES_DAYS = 7
SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}")
TICKET_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9]{0,9}-\d+)\b")
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

def fetch_issues(cfg):
    tr = cfg.get("tracker") or {}
    project = tr.get("project") or cfg.get("linear_project")
    if not project:
        return None
    path = tracker_path()
    if not path.exists():
        raise SourceError(f"tracker.py not found at {path}")
    base = [sys.executable, str(path)] + (["--adapter", tr["adapter"]] if tr.get("adapter") else [])
    out = sh(base + ["list", "--project", project, "--limit", "500"])
    issues = out.get("issues", []) if isinstance(out, dict) else list(out)
    have = {i.get("id") for i in issues}
    for pid in cfg.get("predicate") or []:
        if pid not in have:  # predicate items can live outside the project
            try:
                got = sh(base + ["get", pid], timeout=20)
                issues.append(got.get("issue", got) if isinstance(got, dict) else got)
            except SourceError:
                pass
    return issues


PR_FIELDS = "number,title,url,state,isDraft,headRefOid,headRefName,baseRefName,mergeStateStatus,createdAt,mergedAt,statusCheckRollup,reviews"


def fetch_prs(cfg):
    """Every open PR (the land order needs them all), plus closed and merged ones from the program window."""
    base = ["gh", "pr", "list", "--repo", cfg["repo"], "--json", PR_FIELDS]
    open_ = sh(base + ["--state", "open", "--limit", "100"])
    since = (prog.parse_ts(cfg["created_at"]) - dt.timedelta(days=1)).date().isoformat()
    window = sh(base + ["--state", "all", "--search", f"created:>={since}", "--limit", "200"])
    seen = {p["number"] for p in open_}
    return open_ + [p for p in window if p["number"] not in seen]


def orca_result(*args):
    out = sh(["orca", "orchestration", *args, "--json"])
    if isinstance(out, dict) and out.get("ok") is False:
        raise SourceError(json.dumps(out.get("error"))[:300])
    return out.get("result", out) if isinstance(out, dict) else {}


def fetch_orca(cfg):
    """Workers, tasks and the Run's objective: three local, read-only CLI calls."""
    return {"workers": orca_result("worker-list", "--run", cfg["run"]).get("workers", []),
            "tasks": orca_result("task-list", "--run", cfg["run"]).get("tasks", []),
            "run": orca_result("run-show", "--id", cfg["run"]).get("run") or {}}


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


def shape_pr(raw, events, known):
    rollup = raw.get("statusCheckRollup") or []
    failed, pending, _ = prog.ci_summary(rollup)
    reviews = [r for r in raw.get("reviews") or [] if r.get("state") != "PENDING"]
    rounds = len({(r.get("commit") or {}).get("oid") or r.get("submittedAt") for r in reviews})
    verdicts = sum(1 for e in events if e["ev"] == "verdict" and e.get("pr") == raw["number"])
    return {"number": raw["number"], "title": raw.get("title"), "url": raw.get("url"),
            "state": (raw.get("state") or "").lower(), "draft": bool(raw.get("isDraft")),
            "head": raw.get("headRefOid"), "branch": raw.get("headRefName"), "base": raw.get("baseRefName"),
            "ci": "none" if not rollup else "fail" if failed else "pending" if pending else "pass",
            "verdict": verdict_of(raw["number"], raw.get("headRefOid"), events),
            "review_rounds": max(rounds, verdicts), "ticket": pr_ticket(raw, events, known),
            "created_at": raw.get("createdAt"), "merged_at": raw.get("mergedAt")}


def refresh_pr(pr, events):
    """Re-apply ledger facts to a PR kept from an earlier collect."""
    return {**pr, "verdict": verdict_of(pr["number"], pr.get("head"), events),
            "ticket": pr_ticket({"number": pr["number"], "headRefName": pr.get("branch"), "title": pr.get("title")},
                                events, set()) or pr.get("ticket")}


def ms_iso(ms):
    return iso(dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)) if ms else None


def shape_workers(raw, events):
    by_dispatch, spawned_at = {}, {}
    for e in events:
        if e["ev"] == "spawned" and e.get("ticket"):
            spawned_at.setdefault(e["ticket"], e["ts"])
            for d in re.findall(r"ctx_[0-9a-f]+", " ".join(str(e.get(k) or "") for k in ("note", "dispatch"))):
                by_dispatch[d] = e["ticket"]
    out = []
    for w in raw:
        p = w.get("projection") or {}
        stage, live = p.get("stage") or {}, p.get("liveness") or {}
        wt_path = ((w.get("resource") or {}).get("worktreeId") or "").split("::", 1)[-1] or None
        wt = (wt_path or "").rstrip("/").rsplit("/", 1)[-1]
        ticket = by_dispatch.get(w.get("dispatchId"))
        if not ticket:
            m = TICKET_RE.search(wt)
            ticket = m.group(1).upper() if m else None
        act = stage.get("activity")
        out.append({"dispatch": w.get("dispatchId"), "task": w.get("taskId"), "ticket": ticket,
                    "worktree": wt or None, "worktree_path": wt_path,
                    "model": (p.get("provider") or {}).get("model") or (p.get("provider") or {}).get("id"),
                    "liveness": live.get("verdict") or w.get("terminalState"),
                    "activity": {"working": "running", "waiting": "waiting"}.get(act, "idle"),
                    "outcome": p.get("outcome") or w.get("workerState"), "stage": stage.get("detail"),
                    "since": spawned_at.get(ticket) or ms_iso(live.get("observedAt"))})
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
    "lock_acquired": "#{pr} took the exclusive lane", "lock_released": "#{pr} left the exclusive lane", "lane": "#{pr} lane classified",
}


# Fields already said by the text; anything else an event carries (base, blocks, reason, ...) is appended.
KNOWN_FIELDS = {"ts", "ev", "ticket", "pr", "sha", "note", "patch_id", "source", "result"}


def activity_of(events, notes):
    rows = []
    for e in events:
        if e["ev"] == "verdict":
            text = f"verdict {e.get('result', 'pass')} on #{e.get('pr')} by {e.get('source') or '?'}"
        else:
            text = EV_TEXT.get(e["ev"], e["ev"]).format(ticket=e.get("ticket") or "?", pr=e.get("pr") or "?",
                                                        sha7=(e.get("sha") or "?")[:7])
        if e.get("ticket") and "{ticket}" not in EV_TEXT.get(e["ev"], e["ev"]):
            text += f" ({e['ticket']})"
        extra = [f"{k}={','.join(map(str, v)) if isinstance(v, list) else v}" for k, v in e.items()
                 if k not in KNOWN_FIELDS and v not in (None, "", [])]
        if extra:
            text += " · " + " ".join(extra)
        if e.get("note"):
            text += f" — {e['note']}"
        rows.append({"ts": e["ts"], "kind": e["ev"], "text": text, "ticket": e.get("ticket"), "pr": e.get("pr")})
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
    for t in raw:
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
                    "created_at": t.get("created_at"), "completed_at": t.get("completed_at")})
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
    landed |= {pr_ticket.get(e.get("pr")) for e in events if e["ev"] == "landed"}
    holders = {(l.get("holder") or {}).get("ticket") for l in (landing or {}).values()}
    order_state = {o.get("ticket"): o.get("state") for o in order or [] if o.get("ticket")}
    spawned = {e.get("ticket") for e in events if e["ev"] == "spawned"}
    for n in nodes.values():
        tk, sts = n["ticket"], {t["status"] for t in n["tasks"]}
        title = issue.get(tk, {}).get("title") or next((t["title"] for t in n["tasks"]), None)
        if issue.get(tk, {}).get("state_type") == "completed" or tk in landed:
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


def _add(a, b):
    return {k: a.get(k, 0) + b.get(k, 0) for k in USAGE_KEYS}


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
    with path.open("rb") as f:
        f.seek(entry["offset"])
        chunk = f.read(size - entry["offset"])
    end = chunk.rfind(b"\n") + 1  # a half-written last line waits for the next scan
    for line in chunk[:end].splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
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
    root, per_worker, total = projects_dir(), {}, {}
    for w in workers:
        if (w.get("model") or "").startswith(("gpt", "o3", "o4", "codex")) or not w.get("worktree_path"):
            continue  # Codex rollouts are not mapped to worktrees; shown as n/a
        d = root / re.sub(r"[/.]", "-", w["worktree_path"])
        if not d.is_dir():
            continue
        sums = {}
        for f in sorted(d.glob("*.jsonl")):
            try:
                files[str(f)] = scan_transcript(f, files.get(str(f), {}), since)
            except OSError:
                continue
            e = files[str(f)]
            sums = _add(_add(sums, e["committed"]), e.get("pending") or {})
        if sums:
            per_worker[w["dispatch"]] = {**sums, "total": sum(sums.values())}
    # Two workers can share a worktree over time; count each directory once for the program total.
    seen_dirs = set()
    for w in workers:
        if w["dispatch"] in per_worker and w.get("worktree_path") not in seen_dirs:
            seen_dirs.add(w.get("worktree_path"))
            total = _add(total, per_worker[w["dispatch"]])
    write_atomic(cache_path, json.dumps(cache))
    return {"total": {**total, "total": sum(total.values())} if total else None, "workers": per_worker}


# ---------------------------------------------------------------- summary and series

def summarize(cfg, events, issues, workers, tnow, order):
    pred = cfg.get("predicate") or []
    by_id = {i["id"]: i for i in issues or []}
    done = [p for p in pred if by_id.get(p, {}).get("state_type") == "completed"]
    tickets_done = bool(pred) and len(done) == len(pred) if issues is not None else None
    verified = prog.final_check_current(events)
    roles = {e.get("note") for e in events if e["ev"] == "spawned" and e.get("role")}
    live = [w for w in workers if w.get("outcome") == "in_progress" and w["dispatch"] not in roles]
    units = prog.live_units(events, [w["dispatch"] for w in live])
    ready = prog.ready_prs(events)
    human = [s for s in ready if cfg.get("merge_policy") == "human-gate" and "approved" not in s]
    main, cap = prog.main_state(events), prog.cap_from(events, cfg.get("ceiling", 6))
    within = lambda e, h: tnow - prog.parse_ts(e["ts"]) <= dt.timedelta(hours=h)
    landed = [e for e in events if e["ev"] == "landed"]
    triage = triage_map(events)
    derived = [i for i in issues or [] if i.get("derived")]
    nxt, used = prog.next_move(cfg, events, tnow, tickets_done=tickets_done,
                               live=units, human=len(human),
                               order=[e for e in order if e.get("state") not in ("gone", "unknown")])
    return {
        "predicate_total": len(pred), "predicate_done": len(done) if issues is not None else None,
        "final_check": verified, "main": main, "cap": cap, "ceiling": cfg.get("ceiling", 6),
        "in_flight": units, "ready_to_land": len(ready), "human_wait": len(human),
        "landed_total": len(landed), "landed_24h": sum(1 for e in landed if within(e, 24)),
        "derived_total": len(derived) if issues is not None else None,
        "derived_per_item": round(len(derived) / max(len(pred), 1), 2) if issues is not None else None,
        "parked": sum(1 for v in triage.values() if v == "parked"),
        "admitted": sum(1 for v in triage.values() if v == "admitted"),
        "budget_used": round(used, 3) if used is not None else None, "stopped": prog.stopped(events),
        "ready_prs": [s["pr"] for s in ready], "human_wait_prs": [s["pr"] for s in human],
        "idle_waiting": [w["dispatch"] for w in live if w.get("activity") == "waiting"],
        "untriaged": [i["id"] for i in derived if not i.get("triage")],
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


FETCHERS = {"tracker": fetch_issues, "github": fetch_prs, "orca": fetch_orca}


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
    with lock_for(slug):
        return _merge(slug, d, cfg, fetched, interval)


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
    events = read_jsonl(d / "ledger.jsonl")
    dash = d / "dashboard"
    dash.mkdir(exist_ok=True)
    prev = read_json(dash / "state.json", {}) or {}
    srcs = {k: dict(v) for k, v in (prev.get("sources") or {}).items() if isinstance(v, dict)}
    errors = [e for e in prev.get("errors") or [] if e.split(":", 1)[0] not in fetched]
    tnow = utcnow()

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

    raw_prs, fresh = outcome("github")
    prs = ([shape_pr(p, events, known) for p in raw_prs] if fresh
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
        workers = shape_workers(raw_orca["workers"], events)
        tasks = shape_tasks(raw_orca["tasks"], workers)
        objective = (raw_orca.get("run") or {}).get("objective")
        try:
            usage = collect_usage(dash, workers, cfg)
        except Exception as e:  # usage is optional; never block the rest of the collect
            usage = prev.get("usage")
            errors.append(f"usage: {e!r}"[:300])
    else:
        workers, tasks = prev.get("workers") or [], prev.get("tasks") or []
        objective, usage = prev.get("run_objective"), prev.get("usage")
    for w in workers:
        w["tokens"] = ((usage or {}).get("workers") or {}).get(w["dispatch"])
    srcs["ledger"] = {"updated_at": iso(tnow), "ok": True, "events": len(events)}

    notes = read_jsonl(dash / "notes.jsonl")
    summary = summarize(cfg, events, issues, workers, tnow, order)
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
    if not history and issues is not None:
        history = backfill(cfg, events, issues)
        if history:
            write_atomic(hist_path, "".join(json.dumps(r) + "\n" for r in history))
    row = series_row(tnow, cfg, events, issues, summary["in_flight"])
    if (usage or {}).get("total"):
        row["tokens"] = usage["total"]["total"]
    if not history or strip_t(history[-1]) != strip_t(row):
        with hist_path.open("a") as f:
            f.write(json.dumps(row) + "\n")
        history.append(row)
    horizon = tnow - dt.timedelta(days=SERIES_DAYS)
    series = [r for r in history if prog.parse_ts(r["t"]) >= horizon]

    state = {
        "generated_at": iso(tnow), "slug": slug, "repo": cfg.get("repo"), "run": cfg.get("run"),
        "merge_policy": cfg.get("merge_policy"), "created_at": cfg.get("created_at"),
        "deadline": cfg.get("deadline"), "interval": interval or prev.get("interval") or 60,
        "run_objective": objective, "summary": summary, "predicate": predicate, "landing": landing,
        "land_order": order, "tasks": tasks, "graph": build_graph(tasks, events, issues, order, landing), "usage": usage,
        "issues": issues, "prs": sorted(prs, key=lambda p: -p["number"]), "workers": workers,
        "series": series, "activity": activity_of(events, notes), "notes": notes[-100:][::-1],
        "errors": errors, "sources": srcs,
    }
    write_atomic(dash / "state.json", json.dumps(state, ensure_ascii=False, indent=1))
    return state


# ---------------------------------------------------------------- serve

ORCA_EVERY = 20      # local CLI, no network: keep workers under the UI's 60 s warn line
LEDGER_EVERY = 30    # rewrite even when nothing changed, so the ledger's freshness stays honest
FAST_TICK = 3


def programs():
    root = home()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "program.json").exists() and SLUG_RE.fullmatch(p.name))


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


def slow_loop(interval):
    """Tracker and GitHub: network and rate limits, so once per interval."""
    first = True
    while True:
        for slug in programs():
            try:
                st = collect(slug, SOURCES if first else ("tracker", "github"), interval)
                if st["errors"]:
                    log(f"{slug}: " + " | ".join(st["errors"]))
            except Exception as e:  # keep serving; the next round retries
                log(f"{slug}: collect failed: {e!r}")
        first = False
        time.sleep(interval)


def fast_loop(interval):
    """Ledger and notes on change (and every LEDGER_EVERY s), Orca every ORCA_EVERY s."""
    seen, last_orca, last_write = {}, {}, {}
    while True:
        time.sleep(FAST_TICK)
        now = time.monotonic()
        for slug in programs():
            sig = signature(slug)
            orca_due = now - last_orca.get(slug, now) >= ORCA_EVERY
            last_orca.setdefault(slug, now)
            ledger_due = seen.get(slug) not in (None, sig) or now - last_write.get(slug, now) >= LEDGER_EVERY
            last_write.setdefault(slug, now)
            seen[slug] = sig
            if not (orca_due or ledger_due):
                continue
            try:
                collect(slug, ("orca",) if orca_due else (), interval)
                last_write[slug] = now
                if orca_due:
                    last_orca[slug] = now
            except Exception as e:
                log(f"{slug}: refresh failed: {e!r}")


STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8")}


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "orchestrate-dash"

    def log_message(self, *args):
        pass

    def send(self, code, body=b"", ctype="application/json", etag=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
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
        if path == "/api/programs":
            rows = []
            for slug in programs():
                cfg = read_json(home() / slug / "program.json", {})
                st = read_json(home() / slug / "dashboard" / "state.json", {}) or {}
                rows.append({"slug": slug, "repo": cfg.get("repo"), "run": cfg.get("run"),
                             "merge_policy": cfg.get("merge_policy"), "generated_at": st.get("generated_at"),
                             "summary": st.get("summary"), "errors": len(st.get("errors") or [])})
            return self.send(200, json.dumps(rows, ensure_ascii=False).encode())
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
            body = f.read_bytes()
            etag = '"' + hashlib.sha1(body).hexdigest()[:20] + '"'
            if self.headers.get("If-None-Match") == etag:
                return self.send(304, etag=etag)
            return self.send(200, body, etag=etag)
        self.send(404, b'{"error":"not found"}')


def serve(host, port, interval):
    threading.Thread(target=slow_loop, args=(interval,), daemon=True).start()
    threading.Thread(target=fast_loop, args=(interval,), daemon=True).start()
    httpd = http.server.ThreadingHTTPServer((host, port), Handler)
    log(f"serving {home()} on http://{host}:{port}/ (tracker+github every {interval}s, "
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
    serve(prog.opt(argv, "--host", "127.0.0.1"), int(prog.opt(argv, "--port", "4780")),
          int(prog.opt(argv, "--interval", "60")))


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
    for slug in programs():
        collect(slug, interval=20)
    log(f"demo store at {root}")
    if "--no-serve" in argv:
        print(root)
        return
    if "--live" in argv:
        threading.Thread(target=dash_demo.live, args=(root,), daemon=True).start()
    serve("127.0.0.1", int(prog.opt(argv, "--port", "4780")), 20)


COMMANDS = {"collect": cmd_collect, "serve": cmd_serve, "note": cmd_note, "demo": cmd_demo}

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] not in COMMANDS or (args[0] in ("collect", "note") and len(args) < 2):
        sys.exit(__doc__)
    try:
        COMMANDS[args[0]](args[1:])
    except (ValueError, FileNotFoundError) as e:
        sys.exit(str(e))
