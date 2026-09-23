#!/usr/bin/env python3
"""Program bookkeeping for orchestrate: the numbers a coordinator decides from.

Usage: python3 prog.py <command> <slug> [options]

  init <slug> --repo OWNER/NAME --run RUN_ID [--tracker-project NAME] [--tracker linear|jira]
              [--predicate ID,ID,...] [--merge-policy autonomous|human-gate] [--ceiling 6]
              [--deadline ISO8601]
  set <slug> <merge_policy|ceiling|deadline|predicate> VALUE
                                     mirror a change made in the program note
  status <slug>                      predicate, flow, growth and the next move, from live sources
  record <slug> <event> [--ticket T] [--pr N] [--sha S] [--class K] [--note TEXT]
                                     `record reprioritized --pr N --class urgent` moves a PR in the land order
                                     main_green / main_red need --sha of a landed merge commit
  verdict <slug> --pr N --sha REVIEWED_HEAD --source WHO [--result pass|fail] [--note TEXT]
  gate <slug> --pr N [--parent TASK_ID]
                                     human-gate: open an Orca decision gate "land PR N?" on a
                                     coordinator-owned landing Task; the user resolves it in Orca
  dep <slug> --ticket A --after B[,C]
                                     A cannot land before B and C (feeds the land order)
  queue <slug> [--json]              the land order and why each PR is not moving
  land <slug> --pr N [--class main-fix|gate|urgent|normal] [--wait-minutes M]
                                     the one way a program PR reaches its base: takes its turn in
                                     the land order, re-checks readiness at the current head under
                                     the base-branch lock, merges, records. Exit 0 landed, 2 yield
                                     (not your turn; nothing to do), 3 act (your PR needs a fix,
                                     update or re-review now), 1 refused
  land-check <slug> --pr N [--main-fix]
                                     readiness only, no merge (coordinator diagnostics)
  landed <slug> --pr N               record a merge made outside `land` and print the main-CI watch
  heavy <slug|-> [--wait-minutes 60] -- <command…>
                                     run a heavy local command (compose stack, image build, local
                                     E2E) holding one of heavy_slots (2) machine-wide slots; `-`
                                     outside a program
  wait <slug> [--timeout-ms 540000] [--rounds 3]
                                     block until the Run inbox holds actionable mail; acks
                                     heartbeat-only batches; never acks actionable ones

Store: ~/.claude/programs/<slug>/ (program.json holds identifiers only; ledger.jsonl is
append-only). Events: spawned, ready, verdict, landed, main_green, main_red, land_failed,
admitted, parked, approved, gate_opened, stop, resume, predicate_verified, config, dep,
land_check, yield, lock_acquired, lock_released, reprioritized.
Tickets come from the tracker adapter (use-tracker/scripts/tracker.py), never from a tracker directly.
"""
import datetime as dt, json, os, pathlib, subprocess, sys, time

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

    def append(self, ev, **fields):
        row = {"ts": now(), "ev": ev, **{k: v for k, v in fields.items() if v is not None}}
        with self.ledger_path.open("a") as f:
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
LANDING = {"aging_hours": 2.0, "stale_hours": 3.0, "reserve_minutes": 40, "max_backlog_hours": 2.0,
           "land_interval_minutes": 25, "lock_stale_minutes": 20, "heavy_slots": 2}


def knob(cfg, key):
    return (cfg.get("landing") or {}).get(key, LANDING[key])


def pr_ticket(events):
    return {e["pr"]: e["ticket"] for e in events if e.get("pr") is not None and e.get("ticket")}


def dep_map(events):
    after = {}
    for e in events:
        if e["ev"] == "dep":
            after.setdefault(e["ticket"], set()).update(e.get("after") or [])
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


def land_order(events, rows, cfg, when, stacks=None):
    """Every unlanded program PR in the order it may land, with why each is not moving.

    Class first (main-fix, gate, urgent, normal). A PR that has waited aging_hours counts as
    urgent, so nothing starves behind a stream of fresh, easy PRs. Within a class the PR that
    unblocks more open tickets goes first, then the one that has waited longest.

    A GitHub stack is one landing unit that lands from its top in one merge: a lower layer waits
    for the top while the top is not blocked, and the top is ready only when every open layer
    below it is.
    """
    st, tickets, after = pr_state(events), pr_ticket(events), dep_map(events)
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
        reserved = None
        lc = s.get("land_check")
        if state == "catching_up" and lc and lc.get("outcome") in ("act", "catching_up"):
            until = parse_ts(lc["ts"]) + dt.timedelta(minutes=knob(cfg, "reserve_minutes"))
            reserved = until if until > when else None
        out.append({"pr": pr, "ticket": ticket, "klass": klass, "rank": rank,
                    "unblocks": unblocks(ticket, after, open_t) if ticket else 0,
                    "since": first.isoformat(), "age_h": round(age_h, 2), "state": state, "reasons": reasons,
                    "reserved_until": reserved.isoformat() if reserved else None,
                    "base": (rows.get(pr) or {}).get("baseRefName"), "stack": None})
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


def turn(order, pr):
    """The entry this PR yields to, or None when it is this PR's turn.

    Only an entry ahead of it on the same base contends: one that is ready, or the one catching
    up inside its reservation. Blocked and waiting entries are passed over, so a stuck PR never
    holds the line; a reservation lasts reserve_minutes from its last land attempt, so a dead
    worker's PR loses its place on its own.
    """
    base = next(e for e in order if e["pr"] == pr).get("base")
    for e in order:
        if e["pr"] == pr:
            return None
        if e.get("base") == base and (e["state"] == "ready" or (e["state"] == "catching_up" and e["reserved_until"])):
            return e
    return None


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
    skills_commit = subprocess.run(
        ["git", "-C", str(pathlib.Path(__file__).resolve().parents[3]), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True).stdout.strip() or None
    cfg = {
        "slug": slug, "repo": repo, "run": run_id,
        "tracker": {k: v for k, v in {"adapter": opt(argv, "--tracker"),
                                        "project": opt(argv, "--tracker-project") or opt(argv, "--linear-project")}.items() if v},
        "predicate": [p for p in (opt(argv, "--predicate", "") or "").split(",") if p],
        "merge_policy": policy, "ceiling": int(opt(argv, "--ceiling", "6")),
        "deadline": opt(argv, "--deadline"), "created_at": now(), "skills_commit": skills_commit,
        "note": opt(argv, "--note"),
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
    live = [w for w in workers if (w.get("projection") or {}).get("outcome") == "in_progress"]
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
    order = land_order(events, open_rows(cfg["repo"]), cfg, tnow, repo_stacks(cfg["repo"]))
    order = [e for e in order if e["state"] != "gone"]
    gaps = sorted(parse_ts(b["ts"]) - parse_ts(a["ts"]) for a, b in zip(landed_recent, landed_recent[1:]))
    interval = (gaps[len(gaps) // 2].total_seconds() / 60 if gaps else knob(cfg, "land_interval_minutes"))
    backlog_h = len(order) * interval / 60
    stale = [e for e in order if e["age_h"] >= knob(cfg, "stale_hours")]
    lines.append(f"flow: main {main} · in-flight {len(live)}/{cap} cap (ceiling {cfg['ceiling']}) · ready-to-land {len(ready)}"
                 f" · human-wait {len(human)} · landed {len(landed_recent)} in 3h · {rate:.1f}/h overall"
                 + (f" · idle-waiting: {', '.join(waiting)} (prompt? worker-read --source terminal)" if waiting else ""))
    lines.append(f"land order ({len(order)}, ~{interval:.0f} min per landing, backlog ~{backlog_h:.1f}h): "
                 + (" → ".join(f"#{e['pr']}{'*' if e['state'] == 'ready' else ''}" for e in order[:8]) or "empty")
                 + ("  (* ready; `prog.py queue` says why the rest wait)" if order else ""))
    for e in stale:
        lines.append(f"  STALE {fmt_entry(order.index(e) + 1, e)}")

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

    # 4. next move, first matching rule wins; safety outranks completion
    budget_note, used = "", 0
    if cfg.get("deadline"):
        dl = parse_ts(cfg["deadline"])
        used = (tnow - t0) / max(dl - t0, dt.timedelta(seconds=1))
        budget_note = f" (budget {used:.0%} used)"
    if stopped(events):
        nxt = "STOP line active: spawn nothing; let in-flight finish"
    elif main == "red":
        nxt = "SAFETY STOP: main is red — land only the fix (land-check --main-fix), then record main_green --sha"
    elif tickets_done and verified:
        nxt = "predicate met and verified: Close"
    elif tickets_done:
        nxt = "tickets done: run the final check on the real artifact, then record predicate_verified"
    elif used >= 0.7:
        nxt = "stop spawning: land what is verified"
    elif stale:
        nxt = (f"unstick first: {len(stale)} PR(s) waited ≥{knob(cfg, 'stale_hours'):g}h — for each, remove the reason"
               " `queue` gives (fix task, stack the dependent chain, reprioritize), not more parallel work")
    elif backlog_h > knob(cfg, "max_backlog_hours") or len(human) >= 3:
        nxt = (f"stop spawning implementation: landing backlog ~{backlog_h:.1f}h — finish before starting"
               " (stack dependent or independent ready PRs so one merge lands several)")
    elif len(landed_recent) == 0 and len(fails_recent) >= 2:
        nxt = "stop spawning: no landing and 2+ failures in 3h — find the cause"
    elif len(live) < cap:
        nxt = f"may spawn {cap - len(live)} more"
    else:
        nxt = "at cap: drain and land"
    lines.append(f"next: {nxt}{budget_note}")
    print("\n".join(lines))


def cmd_record(argv):
    p = Program(argv[0])
    ev = argv[1]
    pr, sha = opt(argv, "--pr"), opt(argv, "--sha")
    if ev in ("main_green", "main_red") and sha not in landed_shas(p.events()):
        sys.exit(f"{ev} needs --sha of a merge commit recorded by `landed` (the commit that CI run tested)")
    if ev == "verdict":
        sys.exit("use the verdict command; it pins the reviewed head and its patch-id")
    klass = opt(argv, "--class")
    if klass and klass not in KLASS:
        sys.exit(f"--class is one of {', '.join(KLASS)}")
    row = p.append(ev, ticket=opt(argv, "--ticket"), pr=int(pr) if pr else None, sha=sha, klass=klass, note=opt(argv, "--note"))
    print(json.dumps(row, ensure_ascii=False))


def cmd_set(argv):
    p = Program(argv[0])
    key, value = argv[1], argv[2]
    if key == "merge_policy" and value not in ("autonomous", "human-gate"):
        sys.exit("merge_policy is autonomous or human-gate")
    conv = {"ceiling": int, "predicate": lambda v: [x for x in v.split(",") if x]}.get(key, str)
    if key not in ("merge_policy", "ceiling", "deadline", "predicate"):
        sys.exit("settable: merge_policy, ceiling, deadline, predicate")
    p.cfg[key] = conv(value)
    (p.dir / "program.json").write_text(json.dumps(p.cfg, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(p.append("config", note=f"{key}={value}"), ensure_ascii=False))


def cmd_verdict(argv):
    p = Program(argv[0])
    pr, sha, src = int(opt(argv, "--pr")), opt(argv, "--sha"), opt(argv, "--source")
    if not src or not sha:
        sys.exit("verdict needs --sha (the head that was reviewed) and --source (who reviewed: codex-review, verifier:codex, live:<feature>)")
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
    if e["reserved_until"]:
        head += f" · reserved until {e['reserved_until'][11:16]}Z"
    return head + (f" — {'; '.join(e['reasons'])}" if e["reasons"] else "")


def cmd_queue(argv):
    p = Program(argv[0])
    order = land_order(p.events(), open_rows(p.cfg["repo"]), p.cfg, dt.datetime.now(dt.timezone.utc),
                       repo_stacks(p.cfg["repo"]))
    if "--json" in argv:
        print(json.dumps(order, ensure_ascii=False, indent=1))
        return
    for i, e in enumerate(order, 1):
        print(fmt_entry(i, e))
    if not order:
        print("land order: empty")


class BaseLock:
    """One lander per base branch at a time: an O_EXCL file in the program store.

    A lock older than lock_stale_minutes belongs to a lander that died mid-way; it is broken and
    the break is recorded, because the merge it may have made is caught by `landed` checks below.
    """

    def __init__(self, p, base, pr):
        self.p, self.pr = p, pr
        self.path = p.dir / "locks" / f"{base.replace('/', '__')}.lock"

    def __enter__(self):
        self.path.parent.mkdir(exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, json.dumps({"pr": self.pr, "ts": now(), "pid": os.getpid()}).encode())
                os.close(fd)
                self.p.append("lock_acquired", pr=self.pr, note=self.path.name)
                return self
            except FileExistsError:
                held = json.loads(self.path.read_text() or "{}")
                age = dt.datetime.now(dt.timezone.utc) - parse_ts(held.get("ts", now()))
                if age < dt.timedelta(minutes=knob(self.p.cfg, "lock_stale_minutes")):
                    return None
                self.path.unlink(missing_ok=True)
                self.p.append("lock_released", pr=held.get("pr"), note=f"broken after {age}")
        return None

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)
        self.p.append("lock_released", pr=self.pr, note=self.path.name)


def note_attempt(p, events, pr, outcome, reasons):
    """Refresh the reservation at most every 10 minutes, or when the outcome changes."""
    last = pr_state(events).get(pr, {}).get("land_check")
    if last and last.get("outcome") == outcome and \
            dt.datetime.now(dt.timezone.utc) - parse_ts(last["ts"]) < dt.timedelta(minutes=10):
        return
    p.append("land_check", pr=pr, outcome=outcome, note="; ".join(reasons)[:300])


ADVICE = {
    "conflicts with base": "rebase onto the base, resolve, re-run the review on the resolution, record a new verdict",
    "no passing verdict": "finish the review loop, then prog.py verdict",
    "draft": "gh pr ready",
    "behind base": "gh pr update-branch {pr} --repo {repo} (or rebase and restack migrations), then land again",
}


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
    order = land_order(events, open_rows(repo), cfg, dt.datetime.now(dt.timezone.utc), repo_stacks(repo))
    me = next((e for e in order if e["pr"] == pr), None)
    if me is None or me["state"] == "gone":
        v = pr_view(repo, pr)
        if v["state"] == "MERGED":
            p.append("landed", pr=pr, sha=(v.get("mergeCommit") or {}).get("oid"), note="merged outside land")
            return 0, f"#{pr} was merged outside land; recorded"
        return 1, f"#{pr} is {v['state']}"
    ahead = turn(order, pr)
    if me["state"] == "waiting":
        if st.get("yield", {}).get("note") != me["reasons"][0]:
            p.append("yield", pr=pr, note=me["reasons"][0])
        return 2, f"yield: {me['reasons'][0]}"
    if me["state"] == "blocked":
        note_attempt(p, events, pr, "blocked", me["reasons"])
        todo = [ADVICE.get(r, r).format(pr=pr, repo=repo) for r in me["reasons"]]
        return 3, "act: " + " | ".join(todo)
    if ahead:
        why = "ready" if ahead["state"] == "ready" else f"catching up, reserved until {ahead['reserved_until'][11:16]}Z"
        if st.get("yield", {}).get("note") != f"#{ahead['pr']}":
            p.append("yield", pr=pr, note=f"#{ahead['pr']}")
        hint = " Do not update your branch yet: another landing would make it behind again." \
            if "behind base" in me["reasons"] else ""
        if not me["stack"] and not ahead["stack"]:
            hint += (f" To land in the same merge instead of one CI cycle later, stack on it: rebase onto"
                     f" #{ahead['pr']}'s branch, `gh stack link {ahead['pr']} {pr}`, push, and once your CI is green"
                     f" `land --pr {pr}` lands both.")
        return 2, f"yield: #{ahead['pr']} ({ahead['ticket']}) goes first — {why}.{hint}"
    if me["state"] == "catching_up":
        note_attempt(p, events, pr, "catching_up", me["reasons"])
        if "behind base" in me["reasons"]:
            return 3, "act: your turn and the line is held for you — " + ADVICE["behind base"].format(pr=pr, repo=repo)
        return 2, f"your turn, held for you: {'; '.join(me['reasons'])}"
    # ready and first: the full check under the lock, then merge.
    with BaseLock(p, me["base"] or "main", pr) as lock:
        if lock is None:
            return 2, f"yield: another lander holds the {me['base']} lock"
        unit = (me["stack"] or [pr])[: (me["stack"] or [pr]).index(pr) + 1]
        states, runs = pr_state(events), []
        for n in unit:
            v = pr_view(repo, n)
            if patch_id(repo, n) != (states.get(n, {}).get("verdict") or {}).get("patch_id"):
                note_attempt(p, events, pr, "blocked", [f"patch of #{n} changed since its verdict"])
                return 3, f"act: the patch of #{n} changed since its verdict (conflict fix, restack or edit): re-review, then prog.py verdict"
            failed, pending, r_ = ci_summary(v["statusCheckRollup"])
            runs += r_
            if failed or pending or v["mergeStateStatus"] in ("BEHIND", "DIRTY"):
                return 2, f"#{n} moved while taking the lock; land again"
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
            return 1, f"merge refused: {why}"
        sha = (merged[pr].get("mergeCommit") or {}).get("oid")
    return 0, (f"landed {' '.join('#' + str(n) for n in unit)} (top {sha[:8]}; CI runs {' '.join(runs) or '-'}; read their logs in the report). "
               f"Watch main: gh run list --repo {repo} --commit {sha} --json databaseId,workflowName,status, "
               f"then gh run watch <id> --repo {repo} --exit-status and prog.py record {p.slug} main_green|main_red --pr {pr} --sha {sha}")


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
        problems.append("safety stop: main is red (only the repairing PR lands, with --main-fix)")
    if stopped(events):
        problems.append("STOP line active")
    if main == "pending":
        print("note: main CI for the last landing has not reported yet")
    if v["state"] != "OPEN" or v["isDraft"]:
        problems.append(f"PR is {v['state']}{' draft' if v['isDraft'] else ''}")
    verdict = st.get("verdict")
    if not verdict or verdict.get("result") != "pass":
        problems.append("no passing verdict recorded (prog.py verdict)")
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
            problems.append(f"human-gate: open the gate first (prog.py gate {p.slug} --pr {pr})")
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
    print(f"LAND: gh pr merge {pr} --repo {repo} --squash --match-head-commit {v['headRefOid']}")


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
        sys.exit("gates are for merge_policy human-gate; autonomous programs land after land-check")
    v = pr_view(p.cfg["repo"], pr)
    spec = (f"Land PR #{pr} ({v['url']}) with prog.py land-check once the user resolves the gate. "
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


def batch(res):
    return res.get("deliveryId"), res.get("messages") or []


def cmd_wait(argv):
    p = Program(argv[0])
    timeout = opt(argv, "--timeout-ms", "540000")
    rounds = int(opt(argv, "--rounds", "3"))
    run_id = p.cfg["run"]
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
            "landed": cmd_landed, "heavy": cmd_heavy, "wait": cmd_wait}

if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    COMMANDS[sys.argv[1]](sys.argv[2:])
