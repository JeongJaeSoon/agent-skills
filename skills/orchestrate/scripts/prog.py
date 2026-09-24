#!/usr/bin/env python3
"""Program bookkeeping for orchestrate: the numbers a coordinator decides from.

Usage: orch <command> <slug> [options]

  init <slug> --repo OWNER/NAME --run RUN_ID [--tracker-project NAME] [--tracker linear|jira]
              [--predicate ID,ID,...] [--merge-policy autonomous|human-gate] [--ceiling 6]
              [--deadline ISO8601] [--note NOTES_PATH] [--final-check TEXT]
  set <slug> <merge_policy|ceiling|deadline|predicate|exclusive_paths|note|final_check> VALUE
                                     mirror a change made in the program note
  status <slug>                      predicate, flow, growth and the next move, from live sources
  record <slug> <event> [--ticket T] [--pr N] [--sha S] [--class K] [--role R] [--note TEXT]
                                     `record spawned --role guardian --note <dispatchId>` for a standing
                                     role; roles do not count against the cap
                                     `record reprioritized --pr N --class urgent` moves a PR in the land order
                                     main_green / main_red need --sha of a landed merge commit
  verdict <slug> --pr N --sha REVIEWED_HEAD --source WHO [--result pass|fail] [--note TEXT]
  gate <slug> --pr N [--parent TASK_ID]
                                     human-gate: open an Orca decision gate "land PR N?" on a
                                     coordinator-owned landing Task; the user resolves it in Orca
  dep <slug> --ticket A --after B[,C]
                                     A cannot land before B and C: a land-after edge (A stacked on
                                     B's branch), or one found after dispatch. Start-after edges are
                                     Orca task deps (task-create --deps), read from the Run
  queue <slug> [--json]              the land order and why each PR is not moving
  land <slug> --pr N [--class main-fix|gate|urgent|normal] [--wait-minutes M]
                                     the one way a program PR reaches its base. Normal lane: lands
                                     as soon as it is ready (verdict patch-id, CI green at head, no
                                     conflict, deps landed, main not red), behind or not, unless the
                                     base changed files it also changes. Exclusive lane (migrations,
                                     CI, Dockerfile, compose, exclusive_paths, gates): one PR per
                                     base at a time, on the latest base, in land order. Exit 0
                                     landed, 2 yield (nothing to do yet), 3 act (fix, update or
                                     re-review now), 1 refused
  land-check <slug> --pr N [--main-fix]
                                     readiness only, no merge (coordinator diagnostics)
  landed <slug> --pr N               record a merge made outside `land` and print the main-CI watch
  backfill <slug> --since ISO8601 [--dry-run]
                                     a program registered mid-flight: add the PRs merged from --since
                                     (when its work began) until it, at their merge time with their main
                                     push CI result, ahead of the program's own rows, and move created_at
                                     to the first of them. Rerun to add CI results that came in since
  heavy <slug|-> [--wait-minutes 60] -- <command…>
                                     run a heavy local command (compose stack, image build, local
                                     E2E) holding one of heavy_slots (2) machine-wide slots; `-`
                                     outside a program
  wait <slug> [--timeout-ms 540000] [--rounds 3]
                                     coordinator only: block until the Run inbox holds actionable
                                     mail; acks heartbeat-only batches; never acks actionable ones

Store: ~/.claude/programs/<slug>/ (program.json holds identifiers only; ledger.jsonl is
append-only). Events: spawned, ready, verdict, landed, main_green, main_red, land_failed,
admitted, parked, approved, gate_opened, stop, resume, predicate_verified, config, dep,
land_check, yield, lane, lock_acquired, lock_released, reprioritized.
Tickets come from the tracker adapter (use-tracker/scripts/tracker.py), never from a tracker directly.
"""
import contextlib, datetime as dt, fcntl, fnmatch, heapq, json, os, pathlib, re, subprocess, sys, time

TRACKER = pathlib.Path(__file__).resolve().parents[2] / "use-tracker" / "scripts" / "tracker.py"
HOME = pathlib.Path(os.environ.get("PROGRAMS_HOME", "~/.claude/programs")).expanduser()
PASSING = {"SUCCESS", "SKIPPED", "NEUTRAL"}
ACTIONABLE = {"worker_done", "escalation", "question"}


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def run(cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"{' '.join(cmd[:4])} … failed ({r.returncode}): {r.stderr.strip()[:400]}")
    return r


def orca_json(*args):
    out = json.loads(run(["orca", *args, "--json"]).stdout)
    if not out.get("ok", True):
        sys.exit(f"orca {' '.join(args[:2])}: {json.dumps(out.get('error'))[:400]}")
    return out.get("result", out)


def gh_json(*args):
    return json.loads(run(["gh", *args]).stdout)


def opt(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


class Program:
    def __init__(self, slug):
        self.dir = HOME / slug
        self.slug = slug
        path = self.dir / "program.json"
        if not path.exists():
            sys.exit(f"no program '{slug}' at {self.dir}; run init first")
        self.cfg = json.loads(path.read_text())
        self.ledger_path = self.dir / "ledger.jsonl"

    def events(self):
        if not self.ledger_path.exists():
            return []
        return [json.loads(l) for l in self.ledger_path.read_text().splitlines() if l.strip()]

    def save(self):
        # A shared file overwritten in place was once left empty mid-write; replace it whole.
        tmp = self.dir / "program.json.tmp"
        tmp.write_text(json.dumps(self.cfg, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, self.dir / "program.json")

    @contextlib.contextmanager
    def locked(self):
        """Held to append to the ledger or rewrite it: backfill replaces the file, and a row appended to the
        file it replaced would be lost."""
        with (self.dir / "ledger.lock").open("a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield

    def append(self, ev, **fields):
        row = {"ts": now(), "ev": ev, **{k: v for k, v in fields.items() if v is not None}}
        with self.locked(), self.ledger_path.open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row


def landed_shas(events):
    return {e["sha"] for e in events if e["ev"] == "landed" and e.get("sha")}


def cap_from(events, ceiling):
    # AIMD over merge commits: +1 the first time a landed commit is proven green on main,
    # halve the first time one goes red, and on every failed landing.
    landed, seen, cap = landed_shas(events), set(), 1
    for e in events:
        if e["ev"] in ("main_green", "main_red"):
            key = (e["ev"], e.get("sha"))
            if e.get("sha") not in landed or key in seen:
                continue
            seen.add(key)
        if e["ev"] == "main_green":
            cap = min(ceiling, cap + 1)
        elif e["ev"] in ("main_red", "land_failed"):
            cap = max(1, cap // 2)
    return cap


def main_state(events):
    """red, pending or green, judged in landing order.

    The newest landed commit that has a result decides: a red there stays red while the repair
    landed after it is still pending, and a late green for an older commit never clears it.
    A result without --sha (ledgers from before it was required) belongs to the last landing
    before it.
    """
    order, result, last = [], {}, None
    for e in events:
        if e["ev"] == "landed" and e.get("sha"):
            order.append(e["sha"]); last = e["sha"]
        elif e["ev"] in ("main_green", "main_red"):
            sha = e.get("sha") or last
            if sha in order:
                result[sha] = e["ev"]
    if not order:
        return "green"
    decided = [s for s in order if s in result]
    if decided and result[decided[-1]] == "main_red":
        return "red"
    return "green" if order[-1] in result else "pending"


def final_check_current(events):
    """A predicate_verified counts only if nothing it covered changed after it: no landing, no predicate edit."""
    since = max((i for i, e in enumerate(events) if e["ev"] == "landed"
                 or (e["ev"] == "config" and (e.get("note") or "").startswith("predicate="))), default=-1)
    return any(e["ev"] == "predicate_verified" for e in events[since + 1:])


def pr_state(events):
    """Latest event of each kind per PR, in ledger order."""
    st = {}
    for e in events:
        if e.get("pr") is not None:
            st.setdefault(e["pr"], {"pr": e["pr"]})[e["ev"]] = e
    return st


def ready_prs(events):
    return [s for s in pr_state(events).values() if ("ready" in s or "verdict" in s) and "landed" not in s]


def stopped(events):
    marks = [e["ev"] for e in events if e["ev"] in ("stop", "resume")]
    return bool(marks) and marks[-1] == "stop"


KLASS = {"main-fix": 0, "gate": 1, "urgent": 2, "normal": 3}
LANDING = {"aging_hours": 2.0, "stale_hours": 3.0, "max_backlog_hours": 2.0, "land_interval_minutes": 25,
           "exclusive_stale_minutes": 90, "heavy_slots": 2,
           # Changes that can break a merge they do not textually touch land one at a time, on the latest base.
           "exclusive_paths": ["*migrations/*", ".github/*", "*Dockerfile*", "*compose*.yml", "*compose*.yaml"]}


def knob(cfg, key):
    return (cfg.get("landing") or {}).get(key, LANDING[key])


def pr_ticket(events):
    return {e["pr"]: e["ticket"] for e in events if e.get("pr") is not None and e.get("ticket")}


def dep_map(events, extra=None):
    """{ticket: tickets it lands after}: Orca task deps (extra) plus ledger `dep` events."""
    after = {t: set(b) for t, b in (extra or {}).items()}
    for e in events:
        if e["ev"] == "dep":
            after.setdefault(e["ticket"], set()).update(e.get("after") or [])
    return after


def live_units(events, dispatches):
    """Live workers as the cap counts them: the layers of one land-after chain (ledger `dep`) land as one
    merge, so they count once."""
    ticket = {}
    for e in events:
        if e["ev"] == "spawned" and e.get("ticket"):
            for d in re.findall(r"ctx_[0-9a-f]+", str(e.get("note") or "")):
                ticket[d] = e["ticket"]
    live = {ticket.get(d, d) for d in dispatches}
    root = {t: t for t in live}

    def find(t):
        while root[t] != t:
            t = root[t]
        return t
    for t, below in dep_map(events).items():
        for b in below:
            if t in root and b in root:
                root[find(t)] = find(b)
    return len({find(t) for t in live})


TICKET_ID = re.compile(r"\b[A-Z0-9]*[A-Z][A-Z0-9]*-\d+\b")  # 94S-12, ENG-3


def ticket_of(task):
    # The brief opens with the ticket ID; a card name like "W-372" only looks like one.
    m = TICKET_ID.search(((task.get("spec") or "").splitlines() or [""])[0])
    if m:
        return m.group(0)
    word = ((task.get("display_name") or task.get("task_title") or "").split() or [""])[0]
    return word if TICKET_ID.fullmatch(word) else None


OPEN_STATES = {"triage", "backlog", "unstarted", "started"}


def landed_but_open(events, workers, tasks, worktrees, issues_by_id=None):
    """Cards left behind: (ticket, dispatch, terminal still active, path) for each Orca worktree whose every
    dispatch worked a ticket that has landed. A released worker's card stays until `orca worktree rm`.

    A ticket can take several PRs, a backfilled landing among them: while the tracker says it is open,
    a landing is not its live worker's end. Without the tracker, a landing is."""
    ticket_by_task = {t["id"]: ticket_of(t) for t in tasks}
    landed = {pr_ticket(events).get(e.get("pr")) for e in events if e["ev"] == "landed"} - {None}
    still_open = {t for t in landed if ((issues_by_id or {}).get(t) or {}).get("state_type") in OPEN_STATES}
    present = {w["id"]: w.get("path") for w in worktrees if not w.get("isMainWorktree")}
    cards = {}
    for w in workers:
        wt = (w.get("resource") or {}).get("worktreeId")
        if wt in present:
            cards.setdefault(wt, []).append(w)
    out = []
    for wt, ws in cards.items():
        tickets = {ticket_by_task.get(w.get("taskId")) for w in ws}
        if tickets <= landed:
            active = [w for w in ws if w.get("terminalState") == "active"]
            if active and tickets & still_open:
                continue
            out.append((sorted(tickets)[0], (active or ws)[-1]["dispatchId"], bool(active), present[wt]))
    return sorted(out)


def run_tasks(run_id):
    r = run(["orca", "orchestration", "task-list", "--run", run_id, "--json"], check=False)
    try:
        out = json.loads(r.stdout)
    except ValueError:
        return []
    tasks = out.get("result", out) if isinstance(out, dict) else out
    return tasks.get("tasks", []) if isinstance(tasks, dict) else tasks or []


def orca_deps(run_id):
    """Dependencies as the Run's tasks declare them (`task-create --deps`), keyed by ticket.

    Orca deps are the source of truth; they are immutable once set, so a superseded task
    (status failed) is skipped in favour of its replacement, and a dependency found after
    dispatch is recorded with `orch dep` instead. Returns {} when Orca cannot answer.
    """
    return deps_from_tasks(run_tasks(run_id))


def deps_from_tasks(tasks):
    by_id = {t["id"]: ticket_of(t) for t in tasks}
    after = {}
    for t in tasks:
        tk = ticket_of(t)
        if not tk or t.get("status") == "failed":
            continue
        deps = t.get("deps") or []
        deps = json.loads(deps) if isinstance(deps, str) else deps
        after.setdefault(tk, set()).update(by_id[d] for d in deps if by_id.get(d))
    return after


def unblocks(ticket, after, open_tickets):
    """How many open tickets wait on this one, directly or through others."""
    rev = {}
    for t, befores in after.items():
        for b in befores:
            rev.setdefault(b, set()).add(t)
    seen, todo = set(), [ticket]
    while todo:
        for t in rev.get(todo.pop(), ()):
            if t not in seen:
                seen.add(t)
                todo.append(t)
    return len(seen & open_tickets)


def row_state(row, verdict):
    """ready, catching_up (only an update-branch or CI time stands between it and landing), or blocked."""
    if row is None:
        return "gone", ["PR is no longer open"]
    blocked, catching = [], []
    failed, pending, _ = ci_summary(row.get("statusCheckRollup"))
    mss = row.get("mergeStateStatus")
    if row.get("isDraft"):
        blocked.append("draft")
    if mss == "DIRTY":
        blocked.append("conflicts with base")
    if failed:
        blocked.append("CI failed: " + ", ".join(failed[:3]))
    if not verdict or verdict.get("result") != "pass":
        blocked.append("no passing verdict")
    if mss == "BEHIND":
        catching.append("behind base")
    if pending:
        catching.append(f"CI pending ({len(pending)})")
    elif mss == "BLOCKED" and not failed:
        blocked.append("branch protection blocks it (required review or check)")
    if mss == "UNKNOWN":
        catching.append("GitHub is computing mergeability")
    if blocked:
        return "blocked", blocked + catching
    if catching:
        return "catching_up", catching
    note = [] if verdict.get("sha") == row.get("headRefOid") else [
        f"verdict is for {verdict['sha'][:8]}; land re-checks the patch-id at {row['headRefOid'][:8]}"]
    return "ready", note


def land_order(events, rows, cfg, when, stacks=None, deps=None):
    """Every unlanded program PR in the order it may land, with why each is not moving.

    Class first (main-fix, gate, urgent, normal). A PR that has waited aging_hours counts as
    urgent, so nothing starves behind a stream of fresh, easy PRs. Within a class the PR that
    unblocks more open tickets goes first, then the one that has waited longest. Normal-lane PRs
    land in parallel as soon as they are ready; the order decides only who takes the exclusive
    lane next and what the coordinator unsticks first.

    A GitHub stack is one landing unit that lands from its top in one merge: a lower layer waits
    for the top while the top is not blocked, and the top is ready only when every open layer
    below it is.
    """
    st, tickets, after = pr_state(events), pr_ticket(events), dep_map(events, deps)
    rows = {r["number"]: r for r in rows}
    landed_t = {tickets.get(e.get("pr")) for e in events if e["ev"] == "landed"}
    pending = ready_prs(events)
    open_t = {tickets.get(s["pr"]) for s in pending} - {None}
    stack_of = {}
    for members in (stacks or {}).values():
        live = [m for m in members if m in rows]
        if len(live) >= 2:
            stack_of.update({m: live for m in live})
    out = []
    for s in pending:
        pr = s["pr"]
        first = min(parse_ts(e["ts"]) for e in events if e.get("pr") == pr and e["ev"] in ("ready", "verdict"))
        klass = next((e["klass"] for e in reversed(events) if e.get("pr") == pr and e.get("klass")), "normal")
        age_h = (when - first).total_seconds() / 3600
        rank = KLASS.get(klass, 3)
        if age_h >= knob(cfg, "aging_hours"):
            rank = min(rank, KLASS["urgent"])
        ticket = tickets.get(pr)
        state, reasons = row_state(rows.get(pr), s.get("verdict"))
        live = stack_of.get(pr, [])
        below_t = {tickets.get(b) for b in live[:live.index(pr)]} if live else set()
        waits = sorted(b for b in after.get(ticket, ()) if b not in landed_t | below_t)
        if waits and state != "gone":
            state, reasons = "waiting", [f"lands after {', '.join(waits)}"] + reasons
        lane = (s.get("lane") or {}).get("exclusive", False) or klass == "gate"
        out.append({"pr": pr, "ticket": ticket, "klass": klass, "rank": rank,
                    "unblocks": unblocks(ticket, after, open_t) if ticket else 0,
                    "since": first.isoformat(), "age_h": round(age_h, 2), "state": state, "reasons": reasons,
                    "exclusive": bool(lane), "base": (rows.get(pr) or {}).get("baseRefName"), "stack": None})
    by_pr = {e["pr"]: e for e in out}
    own = {e["pr"]: e["state"] for e in out}  # before stack rules rewrite them
    for live in {tuple(v) for v in stack_of.values()}:
        base = rows[live[0]].get("baseRefName")
        for i, m in enumerate(live):
            e = by_pr.get(m)
            if not e:
                continue
            e["stack"], e["base"] = list(live), base
            upper = [by_pr.get(u) for u in live[i + 1:]]
            if any(u and own[u["pr"]] not in ("blocked", "gone") for u in upper):
                top = max(u["pr"] for u in upper if u and own[u["pr"]] not in ("blocked", "gone"))
                e["state"], e["reasons"] = "waiting", [f"lands with its stack from #{top}"] + e["reasons"]
                continue
            unready = [f"#{b} {own.get(b, 'not in the program')}" for b in live[:i] if own.get(b) != "ready"]
            if unready and e["state"] == "ready":
                e["state"], e["reasons"] = "catching_up", [f"stack layer {', '.join(unready)}"] + e["reasons"]
    out.sort(key=lambda e: (e["state"] == "gone", e["rank"], -e["unblocks"], e["since"], e["pr"]))
    return out


def exclusive_turn(order, pr):
    """The exclusive-lane entry that goes before this one on its base, or None when it is this PR's turn.

    Only exclusive entries that could land soon contend (ready, or catching up); blocked and
    waiting ones are passed over, so a stuck PR never holds the lane.
    """
    base = next(e for e in order if e["pr"] == pr).get("base")
    for e in order:
        if e["pr"] == pr:
            return None
        if e["exclusive"] and e.get("base") == base and e["state"] in ("ready", "catching_up"):
            return e
    return None


def pr_files(repo, pr):
    return [f["path"] for f in gh_json("pr", "view", str(pr), "--repo", repo, "--json", "files").get("files", [])]


def is_exclusive(cfg, files):
    pats = knob(cfg, "exclusive_paths")
    return sorted(f for f in files if any(fnmatch.fnmatch(f, g) for g in pats))


def compare(repo, base, head):
    return gh_json("api", f"repos/{repo}/compare/{base}...{head}")


def base_overlap(repo, base, c):
    """Files this PR changes that the base also changed since the PR branched, or [] when not behind.

    Without required up-to-date branches a PR merges while behind; this is the mechanical floor of
    the pre-merge self-check (the worker still judges contracts and test assumptions itself).
    """
    if not c.get("behind_by"):
        return []
    mine = {f["filename"] for f in c.get("files") or []}
    moved = gh_json("api", f"repos/{repo}/compare/{c['merge_base_commit']['sha']}...{base}")
    return sorted(mine & {f["filename"] for f in moved.get("files") or []})


def open_rows(repo):
    return gh_json("pr", "list", "--repo", repo, "--state", "open", "--limit", "200", "--json",
                   "number,title,headRefOid,mergeStateStatus,isDraft,statusCheckRollup,baseRefName")


def repo_stacks(repo):
    """{stack id: [PR numbers bottom→top]} from GitHub stacked PRs, or {} when unavailable.

    This is the endpoint the gh-stack extension itself reads; it is not a documented API, so a
    failure only turns stack-aware landing off, never landing itself.
    """
    r = run(["gh", "api", f"repos/{repo}/cli_internal/pulls/stacks"], check=False)
    try:
        return {s["id"]: s["pull_requests"] for s in json.loads(r.stdout)} if r.returncode == 0 else {}
    except (ValueError, KeyError, TypeError):
        return {}


def patch_id(repo, pr):
    diff = run(["gh", "pr", "diff", str(pr), "--repo", repo]).stdout
    # --verbatim: whitespace-only edits (Python indentation) must change the id and void the verdict.
    r = subprocess.run(["git", "patch-id", "--verbatim"], input=diff, capture_output=True, text=True)
    return (r.stdout.split() or [""])[0]


def pr_view(repo, pr):
    return gh_json("pr", "view", str(pr), "--repo", repo, "--json",
                   "state,isDraft,headRefOid,headRefName,baseRefName,mergeStateStatus,mergeable,statusCheckRollup,mergeCommit,url")


def ci_summary(rollup):
    # No checks on a head usually means CI has not reported yet, so it never counts as green.
    # A repo with no CI at all therefore cannot land; add a require_ci knob if one ever joins a program.
    if not rollup:
        return [], ["no CI checks reported yet"], []
    failed, pending, runs = [], [], set()
    for c in rollup or []:
        name = c.get("name") or c.get("context")
        concl = (c.get("conclusion") or c.get("state") or "").upper()
        status = (c.get("status") or "COMPLETED").upper()
        url = c.get("detailsUrl") or c.get("targetUrl") or ""
        if "/actions/runs/" in url:
            runs.add(url.split("/actions/runs/")[1].split("/")[0])
        if status != "COMPLETED" or concl in ("", "PENDING", "EXPECTED"):
            pending.append(name)
        elif concl not in PASSING:
            failed.append(f"{name}={concl}")
    return failed, pending, sorted(runs)


def tracker_issues(cfg):
    """Normalized issues (use-tracker schema) for the program's tracker project, or None if unset."""
    t = cfg.get("tracker") or {}
    proj = t.get("project") or cfg.get("linear_project")  # linear_project: programs made before the adapter
    if not proj:
        return None
    cmd = [sys.executable, str(TRACKER)] + (["--adapter", t["adapter"]] if t.get("adapter") else []) + ["list", "--project", proj]
    return json.loads(run(cmd).stdout)


def gate_resolution(cfg, events, pr):
    """The resolution of the newest landing gate opened for this PR, or None while it is pending."""
    opened = [e for e in events if e["ev"] == "gate_opened" and e.get("pr") == pr]
    if not opened:
        return None
    res = orca_json("orchestration", "gate-list", "--task", opened[-1]["task"], "--run", cfg["run"])
    for g in res.get("gates", res if isinstance(res, list) else []):
        if g.get("id") == opened[-1]["gate"] and g.get("status") == "resolved":
            return g.get("resolution")
    return None


def is_derived(issue):
    """The follow-up convention from use-tracker: label `follow-up` or a first body line `파생: …`."""
    return "follow-up" in (issue.get("labels") or []) or (issue.get("description") or "").lstrip().startswith("파생:")


def cmd_init(argv):
    slug = argv[0]
    d = HOME / slug
    if (d / "program.json").exists():
        sys.exit(f"{d}/program.json exists; programs are resumed, not re-initialised")
    repo, run_id = opt(argv, "--repo"), opt(argv, "--run")
    if not repo or not run_id:
        sys.exit("init needs --repo OWNER/NAME and --run RUN_ID")
    policy = opt(argv, "--merge-policy", "autonomous")
    if policy not in ("autonomous", "human-gate"):
        sys.exit("--merge-policy is autonomous or human-gate")
    root = pathlib.Path(__file__).resolve().parents[3]
    # A checkout answers with its commit; an installed plugin sits in a cache dir named by its version.
    skills_commit = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                                   capture_output=True, text=True).stdout.strip() or root.name
    cfg = {
        "slug": slug, "repo": repo, "run": run_id,
        "tracker": {k: v for k, v in {"adapter": opt(argv, "--tracker"),
                                        "project": opt(argv, "--tracker-project") or opt(argv, "--linear-project")}.items() if v},
        "predicate": [p for p in (opt(argv, "--predicate", "") or "").split(",") if p],
        "merge_policy": policy, "ceiling": int(opt(argv, "--ceiling", "6")),
        "deadline": opt(argv, "--deadline"), "created_at": now(), "skills_commit": skills_commit,
        "note": opt(argv, "--note"), "final_check": opt(argv, "--final-check"),
    }
    (d / "briefs").mkdir(parents=True, exist_ok=True)
    (d / "program.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    print(f"initialised {d}")


def cmd_status(argv):
    p = Program(argv[0])
    cfg, events = p.cfg, p.events()
    t0, tnow = parse_ts(cfg["created_at"]), dt.datetime.now(dt.timezone.utc)
    lines = []

    # 1. predicate
    issues = tracker_issues(cfg)
    by_id = {i["id"]: i for i in issues or []}
    pred = cfg["predicate"]
    done = [t for t in pred if by_id.get(t, {}).get("state_type") == "completed"]
    open_ = [t for t in pred if t not in done]
    tickets_done = bool(pred) and not open_ and issues is not None
    verified = final_check_current(events)
    lines.append(f"predicate: {len(done)}/{len(pred)} tickets done"
                 + ((" — final check recorded" if verified else " — final check not yet recorded") if tickets_done
                    else f" (open: {', '.join(open_[:8])}{'…' if len(open_) > 8 else ''})")
                 if issues is not None else f"predicate: {len(pred)} items (no tracker project; check by hand)")

    # 2. flow
    workers = orca_json("orchestration", "worker-list", "--run", cfg["run"]).get("workers", [])
    roles = {e.get("note"): e["role"] for e in events if e["ev"] == "spawned" and e.get("role")}
    running = [w for w in workers if (w.get("projection") or {}).get("outcome") == "in_progress"]
    live = [w for w in running if w["dispatchId"] not in roles]
    role_live = [f"{roles[w['dispatchId']]} {w['dispatchId']}" for w in running if w["dispatchId"] in roles]
    units = live_units(events, [w["dispatchId"] for w in live])
    waiting = [w["dispatchId"] for w in live if ((w.get("projection") or {}).get("stage") or {}).get("activity") == "waiting"]
    ready = ready_prs(events)
    human = [s for s in ready if cfg["merge_policy"] == "human-gate" and "approved" not in s]
    main = main_state(events)
    window = dt.timedelta(hours=3)
    landed_recent = [e for e in events if e["ev"] == "landed" and tnow - parse_ts(e["ts"]) <= window]
    fails_recent = [e for e in events if e["ev"] in ("main_red", "land_failed") and tnow - parse_ts(e["ts"]) <= window]
    cap = cap_from(events, cfg["ceiling"])
    hours = max((tnow - t0).total_seconds() / 3600, 0.25)
    rate = len([e for e in events if e["ev"] == "landed"]) / hours
    tasks = run_tasks(cfg["run"])
    order = land_order(events, open_rows(cfg["repo"]), cfg, tnow, repo_stacks(cfg["repo"]), deps_from_tasks(tasks))
    order = [e for e in order if e["state"] != "gone"]
    gaps = sorted(parse_ts(b["ts"]) - parse_ts(a["ts"]) for a, b in zip(landed_recent, landed_recent[1:]))
    interval = (gaps[len(gaps) // 2].total_seconds() / 60 if gaps else knob(cfg, "land_interval_minutes"))
    backlog_h = len(order) * interval / 60
    stale = [e for e in order if e["age_h"] >= knob(cfg, "stale_hours")]
    lines.append(f"flow: main {main} · in-flight {units}/{cap} cap (ceiling {cfg['ceiling']}) · ready-to-land {len(ready)}"
                 f" · human-wait {len(human)} · landed {len(landed_recent)} in 3h · {rate:.1f}/h overall"
                 + (f" · idle-waiting: {', '.join(waiting)} (prompt? worker-read --source terminal)" if waiting else "")
                 + (f" · roles: {', '.join(role_live)}" if role_live else ""))
    lines.append(f"land order ({len(order)}, ~{interval:.0f} min per landing, backlog ~{backlog_h:.1f}h): "
                 + (" → ".join(f"#{e['pr']}{'*' if e['state'] == 'ready' else ''}" for e in order[:8]) or "empty")
                 + ("  (* ready; `orch queue` says why the rest wait)" if order else ""))
    for e in stale:
        lines.append(f"  STALE {fmt_entry(order.index(e) + 1, e)}")
    worktrees = orca_json("worktree", "list").get("worktrees", [])
    for tk, dispatch, active, path in landed_but_open(events, workers, tasks, worktrees, by_id):
        rm = f"`orca worktree rm --worktree path:{path}`"
        lines.append(f"  LANDED-BUT-OPEN {tk} (dispatch {dispatch}): "
                     + (f"once its main CI is recorded, worker-release, close its terminals and {rm}" if active
                        else f"released, but its card is still there: {rm}"))

    # 3. growth
    admitted = {e.get("ticket") for e in events if e["ev"] == "admitted"}
    parked = {e.get("ticket") for e in events if e["ev"] == "parked"}
    if issues is not None:
        new = [i for i in issues if parse_ts(i["created_at"]) > t0 and i["id"] not in pred]
        derived = [i for i in new if is_derived(i)]
        untriaged = [i["id"] for i in derived if i["id"] not in admitted | parked]
        ratio = len(new) / max(len(pred), 1)
        lines.append(f"growth: {len(new)} tickets filed since start outside the predicate ({ratio:.1f} per predicate item),"
                     f" {len(derived)} marked derived"
                     f" · admitted {len(admitted)} · parked {len(parked)}"
                     + (f" · untriaged: {', '.join(untriaged[:6])}" if untriaged else ""))

    # 4. next move
    nxt, used = next_move(cfg, events, tnow, tickets_done=tickets_done if issues is not None else None,
                          live=units, human=len(human), order=order)
    budget_note = f" (budget {used:.0%} used)" if used is not None else ""
    lines.append(f"next: {nxt}{budget_note}")
    print("\n".join(lines))


def next_move(cfg, events, tnow, *, tickets_done, live, human, order):
    """Step 4 of `status`, first matching rule wins; safety outranks completion. orch-dash shows the same line.

    tickets_done is None when the tracker could not say: a current predicate_verified then decides Close.
    Returns (next line, share of the deadline used or None)."""
    used = None
    if cfg.get("deadline"):
        t0, dl = parse_ts(cfg["created_at"]), parse_ts(cfg["deadline"])
        used = (tnow - t0) / max(dl - t0, dt.timedelta(seconds=1))
    verified = final_check_current(events)
    window = dt.timedelta(hours=3)
    landed_recent = [e for e in events if e["ev"] == "landed" and tnow - parse_ts(e["ts"]) <= window]
    fails_recent = [e for e in events if e["ev"] in ("main_red", "land_failed") and tnow - parse_ts(e["ts"]) <= window]
    gaps = sorted(parse_ts(b["ts"]) - parse_ts(a["ts"]) for a, b in zip(landed_recent, landed_recent[1:]))
    interval = gaps[len(gaps) // 2].total_seconds() / 60 if gaps else knob(cfg, "land_interval_minutes")
    backlog_h = len(order) * interval / 60
    stale = [e for e in order if e.get("age_h", 0) >= knob(cfg, "stale_hours")]
    cap = cap_from(events, cfg.get("ceiling", 6))
    if stopped(events):
        return "STOP line active: spawn nothing; let in-flight finish", used
    if main_state(events) == "red":
        return "SAFETY STOP: main is red — land only the fix (orch land <slug> --pr N --class main-fix), then record main_green --sha", used
    if verified and tickets_done is not False:
        return "predicate met and verified: Close", used
    if tickets_done:
        return "tickets done: run the final check on the real artifact, then record predicate_verified", used
    if (used or 0) >= 0.7:
        return "stop spawning: land what is verified", used
    if stale:
        return (f"unstick first: {len(stale)} PR(s) waited ≥{knob(cfg, 'stale_hours'):g}h — for each, remove the reason"
                " `queue` gives (fix task, stack the dependent chain, reprioritize), not more parallel work"), used
    if backlog_h > knob(cfg, "max_backlog_hours") or human >= 3:
        return (f"stop spawning implementation: landing backlog ~{backlog_h:.1f}h — finish before starting"
                " (stack dependent or independent ready PRs so one merge lands several)"), used
    if not landed_recent and len(fails_recent) >= 2:
        return "stop spawning: no landing and 2+ failures in 3h — find the cause", used
    if live < cap:
        return f"may spawn {cap - live} more", used
    return "at cap: drain and land", used


OWNED = {"approved": "land, gate", "gate_opened": "gate", "landed": "land, landed", "land_check": "land",
         "land_failed": "land", "ready": "land-check", "lane": "land", "yield": "land",
         "lock_acquired": "land", "lock_released": "land", "dep": "dep", "config": "set"}


def cmd_record(argv):
    p = Program(argv[0])
    ev = argv[1]
    pr, sha = opt(argv, "--pr"), opt(argv, "--sha")
    if ev in ("main_green", "main_red") and sha not in landed_shas(p.events()):
        sys.exit(f"{ev} needs --sha of a merge commit recorded by `landed` (the commit that CI run tested)")
    if ev == "verdict":
        sys.exit("use the verdict command; it pins the reviewed head and its patch-id")
    if ev in OWNED:
        # A hand-written `approved` would pass a human gate nobody resolved.
        sys.exit(f"{ev} is written only by the command that checks it ({OWNED[ev]})")
    klass = opt(argv, "--class")
    if klass and klass not in KLASS:
        sys.exit(f"--class is one of {', '.join(KLASS)}")
    row = p.append(ev, ticket=opt(argv, "--ticket"), pr=int(pr) if pr else None, sha=sha, klass=klass,
                   role=opt(argv, "--role"), note=opt(argv, "--note"))
    print(json.dumps(row, ensure_ascii=False))


def cmd_set(argv):
    p = Program(argv[0])
    key, value = argv[1], argv[2]
    if key == "merge_policy" and value not in ("autonomous", "human-gate"):
        sys.exit("merge_policy is autonomous or human-gate")
    split = lambda v: [x for x in v.split(",") if x]
    conv = {"ceiling": int, "predicate": split, "exclusive_paths": split}.get(key, str)
    if key not in ("merge_policy", "ceiling", "deadline", "predicate", "exclusive_paths", "note", "final_check"):
        sys.exit("settable: merge_policy, ceiling, deadline, predicate, exclusive_paths, note, final_check")
    if key == "exclusive_paths":
        p.cfg.setdefault("landing", {})[key] = conv(value)
    else:
        p.cfg[key] = conv(value)
    p.save()
    print(json.dumps(p.append("config", note=f"{key}={value}"), ensure_ascii=False))


def cmd_verdict(argv):
    p = Program(argv[0])
    pr, sha, src = int(opt(argv, "--pr")), opt(argv, "--sha"), opt(argv, "--source")
    if not src or not sha:
        sys.exit("verdict needs --sha (the head that was reviewed) and --source (who reviewed: codex-review, verifier:codex, live:<feature>)")
    if src.lower().startswith(("self", "author", "implementer")):
        sys.exit("a verdict comes from a reviewer other than the implementer (codex-review; subagent-review for a trivial diff, deliver-ticket §3; verifier:<model>; live:<feature>)")
    head = pr_view(p.cfg["repo"], pr)["headRefOid"]
    if not head.startswith(sha):
        sys.exit(f"head is {head[:8]}, not the reviewed {sha[:8]}: the new head has not been reviewed")
    row = p.append("verdict", pr=pr, sha=head, patch_id=patch_id(p.cfg["repo"], pr),
                   source=src, result=opt(argv, "--result", "pass"), note=opt(argv, "--note"))
    print(json.dumps(row, ensure_ascii=False))


def cmd_dep(argv):
    p = Program(argv[0])
    ticket, after = opt(argv, "--ticket"), [t for t in (opt(argv, "--after") or "").split(",") if t]
    if not ticket or not after:
        sys.exit("dep needs --ticket A --after B[,C]")
    print(json.dumps(p.append("dep", ticket=ticket, after=after), ensure_ascii=False))


def fmt_entry(i, e):
    head = f"{i}. #{e['pr']} {e['ticket'] or '-'} [{e['klass']}] {e['state']} · waited {e['age_h']:.1f}h"
    if e["unblocks"]:
        head += f" · unblocks {e['unblocks']}"
    if e["exclusive"]:
        head += " · exclusive lane"
    return head + (f" — {'; '.join(e['reasons'])}" if e["reasons"] else "")


def cmd_queue(argv):
    p = Program(argv[0])
    order = land_order(p.events(), open_rows(p.cfg["repo"]), p.cfg, dt.datetime.now(dt.timezone.utc),
                       repo_stacks(p.cfg["repo"]), orca_deps(p.cfg["run"]))
    if "--json" in argv:
        print(json.dumps(order, ensure_ascii=False, indent=1))
        return
    for i, e in enumerate(order, 1):
        print(fmt_entry(i, e))
    if not order:
        print("land order: empty")


class ExclusiveLock:
    """The exclusive lane of one repo base branch, shared by every program on this machine.

    The holder keeps it from its first turn (restack, CI) until it lands; each attempt refreshes
    it, and one idle for exclusive_stale_minutes belonged to a lander that died, so it is broken.
    """

    def __init__(self, p, base):
        self.p = p
        self.path = HOME / "_locks" / (f"{p.cfg['repo']}@{base}".replace("/", "__") + ".lock")

    def holder(self):
        try:
            held = json.loads(self.path.read_text() or "{}")
        except (OSError, ValueError):
            return None
        age = dt.datetime.now(dt.timezone.utc) - parse_ts(held.get("ts", now()))
        if age >= dt.timedelta(minutes=knob(self.p.cfg, "exclusive_stale_minutes")):
            self.path.unlink(missing_ok=True)
            self.p.append("lock_released", pr=held.get("pr"), note=f"broken after {age}")
            return None
        return held

    def take(self, pr):
        """True if this PR now holds the lane (newly or already)."""
        held = self.holder()
        mine = {"pr": pr, "program": self.p.slug, "ts": now()}
        if held:
            if (held.get("pr"), held.get("program")) != (pr, self.p.slug):
                return False
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(mine))
            os.replace(tmp, self.path)
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        os.write(fd, json.dumps(mine).encode())
        os.close(fd)
        self.p.append("lock_acquired", pr=pr, note=self.path.name)
        return True

    def release(self, pr):
        held = self.holder()
        if held and (held.get("pr"), held.get("program")) == (pr, self.p.slug):
            self.path.unlink(missing_ok=True)
            self.p.append("lock_released", pr=pr, note=self.path.name)


def note_attempt(p, events, pr, outcome, reasons):
    """Refresh the reservation at most every 10 minutes, or when the outcome changes."""
    last = pr_state(events).get(pr, {}).get("land_check")
    if last and last.get("outcome") == outcome and \
            dt.datetime.now(dt.timezone.utc) - parse_ts(last["ts"]) < dt.timedelta(minutes=10):
        return
    p.append("land_check", pr=pr, outcome=outcome, note="; ".join(reasons)[:300])


KEEP = " A clean update keeps the verdict (same patch-id); re-review only if you resolved conflicts."

ADVICE = {
    "conflicts with base": "rebase onto the base, resolve, re-run the review on the resolution, record a new verdict",
    "no passing verdict": "finish the review loop, then orch verdict",
    "draft": "gh pr ready",
    "behind base": "gh pr update-branch {pr} --repo {repo} (or rebase and restack migrations), then land again",
}


def update_advice(unit, repo):
    """How to bring a PR, or a stack bottom-up, onto the latest base. update-branch merges a PR's own base
    into it, so on a stack top it pulls only the layer below."""
    if len(unit) == 1:
        return f"gh pr update-branch {unit[0]} --repo {repo}"
    return ("update the stack bottom-up, each after the previous finishes: "
            + "; ".join(f"gh pr update-branch {n} --repo {repo}" for n in unit)
            + " (tell the lower layers' owners)")


def merge_unit(p, pr, unit, klass, events):
    """Merge a PR (or a stack from its top) after re-checking every layer at its current head."""
    repo, states, runs = p.cfg["repo"], pr_state(events), []
    for n in unit:
        v = pr_view(repo, n)
        if patch_id(repo, n) != (states.get(n, {}).get("verdict") or {}).get("patch_id"):
            note_attempt(p, events, pr, "blocked", [f"patch of #{n} changed since its verdict"])
            return 3, f"act: the patch of #{n} changed since its verdict (conflict fix, restack or edit): re-review, then orch verdict"
        failed, pending, r_ = ci_summary(v["statusCheckRollup"])
        runs += r_
        if failed or pending or v["mergeStateStatus"] in ("BEHIND", "DIRTY"):
            return 2, f"#{n} moved since the order was read; land again"
    head = v["headRefOid"]
    if len(unit) == 1:
        r = run(["gh", "pr", "merge", str(pr), "--repo", repo, "--squash", "--match-head-commit", head], check=False)
    else:
        # A stacked PR refuses `gh pr merge`; merge-async on the top lands it and every layer below.
        r = run(["gh", "api", "-X", "PUT", f"repos/{repo}/pulls/{pr}/merge-async",
                 "-f", "merge_method=squash", "-f", f"sha={head}"], check=False)
    merged = {}
    for _ in range(90 if len(unit) > 1 else 1):
        merged = {n: pr_view(repo, n) for n in unit}
        if all(x["state"] == "MERGED" for x in merged.values()) or r.returncode != 0:
            break
        time.sleep(10)
    for n, x in merged.items():
        if x["state"] == "MERGED" and "landed" not in states.get(n, {}):
            p.append("landed", pr=n, sha=(x.get("mergeCommit") or {}).get("oid"),
                     klass=klass if klass != "normal" else None, note=f"stack of {len(unit)}" if len(unit) > 1 else None)
    if merged[pr]["state"] != "MERGED":
        why = (r.stderr or r.stdout).strip()[:300] or "merge-async did not finish in 15 minutes"
        p.append("land_failed", pr=pr, note=why)
        if "not up to date" in why or "behind" in why.lower():
            return 3, f"act: the base requires up-to-date branches: {update_advice(unit, repo)}, then land again"
        return 1, f"merge refused: {why}"
    sha = (merged[pr].get("mergeCommit") or {}).get("oid")
    return 0, (f"landed {' '.join('#' + str(n) for n in unit)} (merge commit {sha[:8]}; CI runs {' '.join(runs) or '-'}; read their logs in the report). "
               f"Watch main: gh run list --repo {repo} --commit {sha} --json databaseId,workflowName,status, "
               f"then gh run watch <id> --repo {repo} --exit-status and orch record {p.slug} main_green|main_red --pr {pr} --sha {sha}")


def attempt(p, pr, klass):
    """One pass: returns (exit_code, message)."""
    events, cfg, repo = p.events(), p.cfg, p.cfg["repo"]
    st = pr_state(events).get(pr, {})
    if "landed" in st:
        return 0, f"#{pr} already landed as {st['landed'].get('sha', '')[:8]}"
    if stopped(events):
        return 1, "STOP line active: nothing lands"
    if cfg["merge_policy"] == "human-gate" and "approved" not in st and "gate_opened" in st:
        if gate_resolution(cfg, events, pr) == "land":
            p.append("approved", pr=pr, note="orca gate resolved: land")
            events, st = p.events(), pr_state(p.events()).get(pr, {})
    main = main_state(events)
    if main == "red" and klass != "main-fix":
        return 1, "safety stop: main is red; only the repair lands (--class main-fix)"
    order = land_order(events, open_rows(repo), cfg, dt.datetime.now(dt.timezone.utc), repo_stacks(repo),
                       orca_deps(cfg["run"]))
    me = next((e for e in order if e["pr"] == pr), None)
    if me is None or me["state"] == "gone":
        v = pr_view(repo, pr)
        if v["state"] == "MERGED":
            sha = (v.get("mergeCommit") or {}).get("oid")
            if "landed" in st:  # a stack layer lands with its top, whose land call recorded it
                return 0, (f"#{pr} already landed as {(st['landed'].get('sha') or '')[:8]} ({st['landed'].get('note') or 'recorded'});"
                           " main CI ran once on the stack top's commit, and the top's owner records it")
            p.append("landed", pr=pr, sha=sha, note="merged outside land")
            return 0, f"#{pr} was merged outside land; recorded"
        return 1, f"#{pr} is {v['state']}"
    if me["state"] == "waiting":
        if st.get("yield", {}).get("note") != me["reasons"][0]:
            p.append("yield", pr=pr, note=me["reasons"][0])
        return 2, f"yield: {me['reasons'][0]}"
    if me["state"] == "blocked":
        note_attempt(p, events, pr, "blocked", me["reasons"])
        todo = [ADVICE.get(r, r).format(pr=pr, repo=repo) for r in me["reasons"]]
        return 3, "act: " + " | ".join(todo)
    unit = (me["stack"] or [pr])[: (me["stack"] or [pr]).index(pr) + 1]
    touched = is_exclusive(cfg, [f for n in unit for f in pr_files(repo, n)])
    if bool(touched) != bool((st.get("lane") or {}).get("exclusive")):
        p.append("lane", pr=pr, exclusive=bool(touched), note=", ".join(touched[:5]) or None)
        me["exclusive"] = bool(touched) or me["klass"] == "gate"
    if me["exclusive"]:
        # PRs ahead whose lane is not known yet (they have not called land) are classified now.
        for e in order[:order.index(me)]:
            if e.get("base") == me["base"] and e["state"] in ("ready", "catching_up") and not e["exclusive"] \
                    and "lane" not in pr_state(events).get(e["pr"], {}):
                hit = is_exclusive(cfg, pr_files(repo, e["pr"]))
                p.append("lane", pr=e["pr"], exclusive=bool(hit), note=", ".join(hit[:5]) or None)
                e["exclusive"] = bool(hit)
        lane = ExclusiveLock(p, me["base"] or "main")
        ahead = exclusive_turn(order, pr)
        held = lane.holder()
        if held and (held.get("pr"), held.get("program")) != (pr, p.slug):
            return 2, f"yield: the exclusive lane of {me['base']} is held by #{held.get('pr')} ({held.get('program')})"
        if not held and ahead:
            return 2, f"yield: #{ahead['pr']} ({ahead['ticket']}) takes the exclusive lane first"
        if not lane.take(pr):
            return 2, "yield: another lander just took the exclusive lane"
        if me["state"] == "catching_up":
            note_attempt(p, events, pr, "catching_up", me["reasons"])
            return 3, ("act: you hold the exclusive lane; bring the branch onto the latest base (restack migrations),"
                       f" let CI run, land again — {'; '.join(me['reasons'])}." + KEEP)
        if compare(repo, me["base"] or "main", pr_view(repo, pr)["headRefOid"]).get("behind_by"):
            return 3, (f"act: you hold the exclusive lane and it lands on the latest base only: {update_advice(unit, repo)},"
                       " let CI run, land again." + KEEP)
        code, msg = merge_unit(p, pr, unit, klass, events)
        if code == 0:
            lane.release(pr)
        return code, msg
    if me["state"] == "catching_up":
        note_attempt(p, events, pr, "catching_up", me["reasons"])
        if "behind base" in me["reasons"]:
            return 3, f"act: {update_advice(unit, repo)}, then land again"
        return 2, f"not yet: {'; '.join(me['reasons'])}"
    base = me["base"] or "main"
    overlap = base_overlap(repo, base, compare(repo, base, pr_view(repo, pr)["headRefOid"]))
    if overlap:
        note_attempt(p, events, pr, "act", [f"base changed {', '.join(overlap[:5])}"])
        return 3, (f"act: since your branch point the base changed files you also change ({', '.join(overlap[:5])}):"
                   f" {update_advice(unit, repo)}, check your contract and test assumptions still hold,"
                   " let CI run, land again." + KEEP)
    return merge_unit(p, pr, unit, klass, events)


def cmd_land(argv):
    p = Program(argv[0])
    pr, klass = int(opt(argv, "--pr")), opt(argv, "--class")
    if klass and klass not in KLASS:
        sys.exit(f"--class is one of {', '.join(KLASS)}")
    st = pr_state(p.events()).get(pr, {})
    if "ready" not in st or (klass and klass != st["ready"].get("klass", "normal")):
        p.append("ready", pr=pr, ticket=opt(argv, "--ticket"), klass=klass)
    klass = klass or next((e["klass"] for e in reversed(p.events()) if e.get("pr") == pr and e.get("klass")), "normal")
    deadline = time.monotonic() + 60 * float(opt(argv, "--wait-minutes", "0"))
    while True:
        code, msg = attempt(p, pr, klass)
        print(msg, flush=True)
        if code != 2 or time.monotonic() >= deadline:
            sys.exit(code)
        time.sleep(60)


def cmd_land_check(argv):
    p = Program(argv[0])
    repo, pr = p.cfg["repo"], int(opt(argv, "--pr"))
    events = p.events()
    st = pr_state(events).get(pr, {})
    v = pr_view(repo, pr)
    problems = []
    main = main_state(events)
    if main == "red" and "--main-fix" not in argv:
        problems.append("safety stop: main is red (only the repairing PR lands, with land --class main-fix)")
    if stopped(events):
        problems.append("STOP line active")
    if main == "pending":
        print("note: main CI for the last landing has not reported yet")
    if v["state"] != "OPEN" or v["isDraft"]:
        problems.append(f"PR is {v['state']}{' draft' if v['isDraft'] else ''}")
    verdict = st.get("verdict")
    if not verdict or verdict.get("result") != "pass":
        problems.append("no passing verdict recorded (orch verdict)")
    else:
        cur = patch_id(repo, pr)
        if cur != verdict.get("patch_id"):
            problems.append(f"patch changed since verdict ({verdict.get('patch_id', '')[:10]} -> {cur[:10]}): re-review")
        elif v["headRefOid"] != verdict.get("sha"):
            print(f"note: head moved {verdict['sha'][:8]} -> {v['headRefOid'][:8]} with the same patch-id; verdict holds, CI must pass at the new head")
    if p.cfg["merge_policy"] == "human-gate" and "approved" not in st:
        resolution = gate_resolution(p.cfg, events, pr)
        if resolution == "land":
            p.append("approved", pr=pr, note="orca gate resolved: land")
        elif resolution:
            problems.append(f"human-gate: the user resolved the gate as '{resolution}'")
        elif any(e["ev"] == "gate_opened" and e.get("pr") == pr for e in events):
            problems.append("human-gate: gate open in Orca, waiting for the user")
        else:
            problems.append(f"human-gate: open the gate first (orch gate {p.slug} --pr {pr})")
    mss = v["mergeStateStatus"]
    if mss == "DIRTY":
        problems.append("conflicts with base: fix task in the PR's worktree")
    elif mss == "BEHIND":
        problems.append(f"behind base: gh pr update-branch {pr} --repo {repo}, then wait for CI")
    failed, pending, runs = ci_summary(v["statusCheckRollup"])
    if failed:
        problems.append("CI failed: " + ", ".join(failed))
    if pending:
        problems.append("CI pending: " + ", ".join(pending[:5]))
    print(f"PR {v['url']} head {v['headRefOid'][:8]} mergeState {mss} runs {' '.join(runs) or '-'}")
    for r in runs:
        print(f"  read the log before landing: gh run view {r} --repo {repo} --log | tail -40")
    if problems:
        print("HOLD: " + " | ".join(problems))
        sys.exit(1)
    print(f"READY: orch land {p.slug} --pr {pr}")


def cmd_landed(argv):
    p = Program(argv[0])
    repo, pr = p.cfg["repo"], int(opt(argv, "--pr"))
    v = pr_view(repo, pr)
    if v["state"] != "MERGED":
        p.append("land_failed", pr=pr, note=f"state {v['state']}")
        sys.exit(f"PR {pr} is {v['state']}, recorded land_failed")
    sha = (v.get("mergeCommit") or {}).get("oid")
    p.append("landed", pr=pr, sha=sha)
    print(f"landed #{pr} as {sha[:8]}. Watch main CI in the background, then record main_green or main_red:")
    print(f"  gh run list --repo {repo} --commit {sha} --json databaseId,workflowName,status")
    print(f"  gh run watch <databaseId> --repo {repo} --exit-status")


def cmd_gate(argv):
    p = Program(argv[0])
    pr = int(opt(argv, "--pr"))
    if p.cfg["merge_policy"] != "human-gate":
        sys.exit("gates are for merge_policy human-gate; autonomous programs land with orch land")
    v = pr_view(p.cfg["repo"], pr)
    spec = (f"Land PR #{pr} ({v['url']}) with orch land once the user resolves the gate. "
            "Coordinator-owned; no worker is dispatched for this Task.")
    args = ["orchestration", "task-create", "--run", p.cfg["run"], "--spec", spec, "--task-title", f"Land #{pr}"]
    if opt(argv, "--parent"):
        args += ["--parent", opt(argv, "--parent")]
    task = orca_json(*args)
    task_id = (task.get("task") or task).get("id")
    gate = orca_json("orchestration", "gate-create", "--task", task_id,
                     "--question", f"Land PR #{pr}: {v['title']}?", "--options", json.dumps(["land", "hold"]))
    gate_id = (gate.get("gate") or gate).get("id")
    print(json.dumps(p.append("gate_opened", pr=pr, task=task_id, gate=gate_id), ensure_ascii=False))
    print(f"the user resolves it in Orca (or: orca orchestration gate-resolve --id {gate_id} --resolution land)")


def ticket_in(keys, *texts):
    """The first ticket ID with one of the program's team keys in the texts (title, then branch)."""
    for text in texts:
        for key, num in re.findall(r"(?<![A-Za-z0-9])([A-Za-z0-9]+)-(\d+)", text or ""):
            if key.upper() in keys:
                return f"{key.upper()}-{num}"
    return None


def main_results(repo, base, since):
    """{commit: (main_green|main_red, ts)} from the push CI on base. Every workflow of a commit counts;
    a cancelled run (superseded) does not, and a commit with a run still going has no result yet."""
    runs = gh_json("run", "list", "--repo", repo, "--branch", base, "--event", "push", "--created", f">={since}",
                   "--limit", "5000", "--json", "headSha,status,conclusion,updatedAt")
    by_sha = {}
    for r in runs:
        if r.get("conclusion") != "cancelled":
            by_sha.setdefault(r["headSha"], []).append(r)
    out = {}
    for sha, rs in by_sha.items():
        if all(r.get("status") == "completed" for r in rs):
            red = any((r.get("conclusion") or "").upper() not in PASSING for r in rs)
            out[sha] = ("main_red" if red else "main_green", max(r["updatedAt"] for r in rs))
    return out


def cmd_backfill(argv):
    """Merges from before the program was registered, as landings at their real merge time with their
    main push CI result, so cap, rate and the dashboard count them. Rows carry note "backfill" and go
    before the program's own rows; a PR already in the ledger is skipped, so a rerun adds only what is new
    and the results that came in since."""
    p = Program(argv[0])
    cfg, repo = p.cfg, p.cfg["repo"]
    if not opt(argv, "--since"):
        sys.exit("backfill needs --since ISO8601, when the program's work began: older merges in the repo are not its landings")
    since = parse_ts(opt(argv, "--since"))
    since = (since if since.tzinfo else since.replace(tzinfo=dt.timezone.utc)).astimezone(dt.timezone.utc)
    text = p.ledger_path.read_text() if p.ledger_path.exists() else ""
    events = [json.loads(l) for l in text.splitlines() if l.strip()]
    until = parse_ts(cfg["created_at"])
    have = {e.get("pr") for e in events if e["ev"] == "landed"}
    merged = gh_json("pr", "list", "--repo", repo, "--state", "merged", "--limit", "5000",
                     "--search", f"merged:>={since.date().isoformat()}", "--json", "number,title,headRefName,mergedAt,mergeCommit")
    merged = sorted((m for m in merged if m.get("mergeCommit") and m["number"] not in have
                     and since <= parse_ts(m["mergedAt"]) < until), key=lambda m: m["mergedAt"])
    # CI still running at an earlier backfill: its landings are asked again.
    decided = {e.get("sha") for e in events if e["ev"] in ("main_green", "main_red")}
    pending = [e for e in events if e["ev"] == "landed" and e.get("note") == "backfill" and e.get("sha") not in decided]
    rows = backfill_rows(cfg, merged, pending, events) if merged or pending else []
    if "--dry-run" in argv:
        # The window is the coordinator's judgment: in a repo shared with other work, read what it holds.
        for m in merged:
            print(f"  #{m['number']} {m['mergedAt']} {m['title']}")
    if rows:
        write_backfill(p, text, events, rows, "--dry-run" in argv)
    else:
        print(f"nothing to backfill: every PR merged between {since.isoformat()} and {cfg['created_at']} is in the ledger")
    if "--dry-run" in argv:
        return
    # The program's clock starts at its first landing, or rate, growth and the dashboard window miss the
    # history. Reconciled from the ledger on every run, so a run stopped between the two files is repaired.
    first = min((e["ts"] for e in p.events() if e["ev"] == "landed" and e.get("note") == "backfill"),
                key=parse_ts, default=None)
    if first and parse_ts(first) < parse_ts(p.cfg["created_at"]):
        p.cfg["created_at"] = first
        p.save()
        p.append("config", note=f"created_at={first} (backfill)")
        print(f"created_at moved to {first}")


def utc(ts):
    return parse_ts(ts).astimezone(dt.timezone.utc).isoformat()


def backfill_rows(cfg, merged, pending, events):
    """Landing rows for the merged PRs, and result rows for them and for the pending backfilled landings."""
    base = cfg.get("base") or gh_json("repo", "view", cfg["repo"], "--json", "defaultBranchRef")["defaultBranchRef"]["name"]
    # Matched by commit, not by the PR's base: a stack's lower layer names a branch yet lands on the base.
    results = main_results(cfg["repo"], base, min(utc(t) for t in [m["mergedAt"] for m in merged] + [e["ts"] for e in pending])[:10])
    keys = {t.rsplit("-", 1)[0].upper() for t in cfg["predicate"] + [e["ticket"] for e in events if e.get("ticket")]}
    rows = [{"ts": utc(m["mergedAt"]), "ev": "landed", "pr": m["number"], "sha": m["mergeCommit"]["oid"],
             "ticket": ticket_in(keys, m["title"], m["headRefName"]), "note": "backfill"} for m in merged]
    for sha in [r["sha"] for r in rows] + [e["sha"] for e in pending]:
        if sha in results:
            ev, at = results[sha]
            rows.append({"ts": utc(at), "ev": ev, "sha": sha, "note": "backfill"})
    return sorted(({k: v for k, v in r.items() if v is not None} for r in rows), key=lambda r: parse_ts(r["ts"]))


def write_backfill(p, text, events, rows, dry_run):
    landed = sum(r["ev"] == "landed" for r in rows)
    red = sum(r["ev"] == "main_red" for r in rows)
    print(f"backfill {landed} landings ({sum('ticket' in r for r in rows)} with a ticket),"
          f" {len(rows) - landed - red} main green, {red} main red")
    if dry_run:
        return
    old = [e for e in events if e.get("note") == "backfill"]
    own = [e for e in events if e.get("note") != "backfill"]
    # Every landing merged before the program's own rows, so history goes first; a result that came in
    # later (CI finishing after registration) takes its place in time among them, as cap_from reads in order.
    merged = heapq.merge(sorted(old + rows, key=lambda r: parse_ts(r["ts"])), own, key=lambda r: parse_ts(r["ts"]))
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in merged)
    with p.locked():
        if (p.ledger_path.read_text() if p.ledger_path.exists() else "") != text:
            sys.exit("the ledger changed while backfilling (a lander appended); run backfill again")
        (p.dir / f"ledger.jsonl.bak-{now().replace(':', '')}").write_text(text)
        tmp = p.dir / "ledger.jsonl.tmp"
        tmp.write_text(body)
        os.replace(tmp, p.ledger_path)
    print(f"written; the old ledger is kept beside it as {p.dir.name}/ledger.jsonl.bak-*")


def batch(res):
    return res.get("deliveryId"), res.get("messages") or []


def cmd_wait(argv):
    p = Program(argv[0])
    timeout = opt(argv, "--timeout-ms", "540000")
    rounds = int(opt(argv, "--rounds", "3"))
    run_id = p.cfg["run"]
    me = os.environ.get("ORCA_TERMINAL_HANDLE")
    workers = orca_json("orchestration", "worker-list", "--run", run_id).get("workers", []) if me else []
    if me and me in {w.get("agentTerminalHandle") for w in workers}:
        sys.exit("orch wait reads and acks the coordinator's Run inbox, and this terminal is one of the Run's"
                 " workers. Wake on your own mailbox (check --terminal $ORCA_TERMINAL_HANDLE --wait) or a"
                 " background command such as `gh run watch <id>`.")
    for i in range(rounds):
        res = orca_json("orchestration", "check", "--run", run_id, "--wait", "--types", ",".join(sorted(ACTIONABLE)),
                        "--timeout-ms", timeout)
        did, msgs = batch(res)
        if not did:
            # A typed wait never wakes for heartbeats, so they pile up in the FIFO; pull that batch.
            did, msgs = batch(orca_json("orchestration", "check", "--run", run_id))
        work = [m for m in msgs if m.get("type") != "heartbeat"]
        if work:
            for m in work:
                body = (m.get("body") or "").replace("\n", " ")
                print(f"INBOX id={m.get('id')} type={m.get('type')} from={m.get('from_handle')} "
                      f"subject={m.get('subject')} payload={str(m.get('payload'))[:200]} body={body[:800]}")
            print(f"ACTIONABLE delivery={did} ({len(work)} of {len(msgs)}): process every message, then "
                  f"orca orchestration check --run {run_id} --ack {did} --json")
            return
        if did:
            orca_json("orchestration", "check", "--run", run_id, "--ack", did)
        print(f"wait {i + 1}/{rounds}: nothing actionable ({len(msgs)} heartbeat(s) acked)", flush=True)
    print(f"EMPTY x{rounds}: orca orchestration worker-list --run {run_id} --json, act on projection.nextAction;"
          f" a worker whose stage.activity is 'waiting' may be stuck on a permission prompt (worker-read --source terminal)")


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def cmd_heavy(argv):
    """Docker stacks from parallel cards starved the whole machine (Orca UI included), so heavy local work
    queues on slots shared by every program on this machine, like CI shares its runners."""
    if "--" not in argv or argv.index("--") == len(argv) - 1:
        sys.exit("heavy <slug|-> [--wait-minutes 60] -- <command…>")
    cut = argv.index("--")
    cmd = argv[cut + 1:]
    cfg, owner = ({}, "-") if argv[0] == "-" else (Program(argv[0]).cfg, argv[0])
    slots = HOME / "_heavy"
    slots.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 60 * float(opt(argv[:cut], "--wait-minutes", "60"))
    while True:
        for i in range(int(knob(cfg, "heavy_slots"))):
            path = slots / f"slot-{i}"
            held = json.loads(path.read_text() or "{}") if path.exists() else {}
            if held and not pid_alive(held.get("pid", 0)):
                path.unlink(missing_ok=True)
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                continue
            os.write(fd, json.dumps({"pid": os.getpid(), "program": owner, "cmd": " ".join(cmd)[:200], "ts": now()}).encode())
            os.close(fd)
            try:
                sys.exit(subprocess.run(cmd).returncode)
            finally:
                path.unlink(missing_ok=True)
        if time.monotonic() >= deadline:
            holders = [json.loads(f.read_text() or "{}") for f in sorted(slots.glob("slot-*"))]
            sys.exit(f"heavy: every slot busy after waiting: {json.dumps(holders, ensure_ascii=False)}")
        time.sleep(15)


COMMANDS = {"init": cmd_init, "set": cmd_set, "status": cmd_status, "record": cmd_record, "verdict": cmd_verdict,
            "gate": cmd_gate, "dep": cmd_dep, "queue": cmd_queue, "land": cmd_land, "land-check": cmd_land_check,
            "landed": cmd_landed, "heavy": cmd_heavy, "wait": cmd_wait, "backfill": cmd_backfill}

if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in COMMANDS or {"-h", "--help"} & set(sys.argv[2:(sys.argv + ["--"]).index("--")]):
        sys.exit(__doc__)
    COMMANDS[sys.argv[1]](sys.argv[2:])
