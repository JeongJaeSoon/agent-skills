#!/usr/bin/env python3
"""Program bookkeeping for run-program: the numbers a coordinator decides from.

Usage: python3 prog.py <command> <slug> [options]

  init <slug> --repo OWNER/NAME --run RUN_ID [--linear-project NAME] [--predicate ID,ID,...]
              [--merge-policy autonomous|human-gate] [--ceiling 6] [--deadline ISO8601]
  status <slug>                      predicate, flow, growth and the next move, from live sources
  record <slug> <event> [--ticket T] [--pr N] [--sha S] [--note TEXT]
  verdict <slug> --pr N --source WHO [--result pass|fail] [--note TEXT]
  land-check <slug> --pr N           may this PR land now? prints the exact merge command if so
  landed <slug> --pr N               record the merge and print the main-CI watch command
  wait <slug> [--timeout-ms 540000] [--rounds 3]
                                     block until the Run inbox holds actionable mail; acks
                                     heartbeat-only batches; never acks actionable ones

Store: ~/.claude/programs/<slug>/ (program.json holds identifiers only; ledger.jsonl is
append-only). Events: spawned, ready, verdict, landed, main_green, main_red, land_failed,
admitted, parked, approved, stop, resume.
"""
import datetime as dt, json, math, os, pathlib, subprocess, sys

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


def cap_from(events, ceiling):
    # AIMD: +1 per landing proven green on main, halve on red main or a failed landing.
    cap = 1
    for e in events:
        if e["ev"] == "main_green":
            cap = min(ceiling, cap + 1)
        elif e["ev"] in ("main_red", "land_failed"):
            cap = max(1, cap // 2)
    return cap


def pr_state(events):
    """Latest lifecycle state per PR: ready -> verdict -> landed -> main_green|main_red."""
    st = {}
    for e in events:
        pr = e.get("pr")
        if pr is None:
            continue
        s = st.setdefault(pr, {"pr": pr})
        if e["ev"] in ("ready", "verdict", "landed", "main_green", "main_red", "land_failed", "approved"):
            s[e["ev"]] = e
            if e["ev"] != "approved":
                s["state"] = e["ev"]
    return st


def patch_id(repo, pr):
    diff = run(["gh", "pr", "diff", str(pr), "--repo", repo]).stdout
    r = subprocess.run(["git", "patch-id", "--stable"], input=diff, capture_output=True, text=True)
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


def linear_issues(cfg):
    proj = cfg.get("linear_project")
    if not proj:
        return None
    res = orca_json("linear", "list-issues", "--project", proj, "--include-archived")
    return res.get("issues", [])


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
        "linear_project": opt(argv, "--linear-project"),
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
    issues = linear_issues(cfg)
    by_id = {i["identifier"]: i for i in issues or []}
    pred = cfg["predicate"]
    done = [t for t in pred if (by_id.get(t, {}).get("state") or {}).get("type") in ("completed",)]
    open_ = [t for t in pred if t not in done]
    met = pred and not open_
    lines.append(f"predicate: {len(done)}/{len(pred)} done" + (" — met" if met else f" (open: {', '.join(open_[:8])}{'…' if len(open_) > 8 else ''})")
                 if issues is not None else f"predicate: {len(pred)} items (no linear_project; check by hand)")

    # 2. flow
    workers = orca_json("orchestration", "worker-list", "--run", cfg["run"]).get("workers", [])
    live = [w for w in workers if (w.get("projection") or {}).get("outcome") == "in_progress"]
    waiting = [w["dispatchId"] for w in live if ((w.get("projection") or {}).get("stage") or {}).get("activity") == "waiting"]
    st = pr_state(events)
    ready = [s for s in st.values() if s.get("state") in ("ready", "verdict")]
    human = [s for s in ready if cfg["merge_policy"] == "human-gate" and "approved" not in s]
    window = dt.timedelta(hours=3)
    landed_recent = [e for e in events if e["ev"] == "landed" and tnow - parse_ts(e["ts"]) <= window]
    fails_recent = [e for e in events if e["ev"] in ("main_red", "land_failed") and tnow - parse_ts(e["ts"]) <= window]
    cap = cap_from(events, cfg["ceiling"])
    hours = max((tnow - t0).total_seconds() / 3600, 0.25)
    rate = len([e for e in events if e["ev"] == "landed"]) / hours
    lines.append(f"flow: in-flight {len(live)}/{cap} cap (ceiling {cfg['ceiling']}) · ready-to-land {len(ready)}"
                 f" · human-wait {len(human)} · landed {len(landed_recent)} in 3h · {rate:.1f}/h overall"
                 + (f" · idle-waiting: {', '.join(waiting)} (prompt? worker-read --source terminal)" if waiting else ""))

    # 3. growth
    admitted = {e.get("ticket") for e in events if e["ev"] == "admitted"}
    parked = {e.get("ticket") for e in events if e["ev"] == "parked"}
    if issues is not None:
        new = [i for i in issues if parse_ts(i["createdAt"]) > t0 and i["identifier"] not in pred]
        derived = [i for i in new if any(l.get("name") == "follow-up" for l in i.get("labels") or [])
                   or (i.get("description") or "").startswith("파생:")]
        untriaged = [i["identifier"] for i in derived if i["identifier"] not in admitted | parked]
        ratio = len(new) / max(len(pred), 1)
        lines.append(f"growth: {len(new)} tickets filed since start outside the predicate ({ratio:.1f} per predicate item),"
                     f" {len(derived)} marked derived"
                     f" · admitted {len(admitted)} · parked {len(parked)}"
                     + (f" · untriaged: {', '.join(untriaged[:6])}" if untriaged else ""))

    # 4. next move, first matching rule wins
    last_red = max((e["ts"] for e in events if e["ev"] == "main_red"), default=None)
    last_green = max((e["ts"] for e in events if e["ev"] == "main_green"), default=None)
    stopped = any(e["ev"] == "stop" for e in events) and \
        max(e["ts"] for e in events if e["ev"] in ("stop", "resume")) in [e["ts"] for e in events if e["ev"] == "stop"]
    budget_note = ""
    if cfg.get("deadline"):
        dl = parse_ts(cfg["deadline"])
        used = (tnow - t0) / max(dl - t0, dt.timedelta(seconds=1))
        budget_note = f" (budget {used:.0%} used)"
    if met:
        nxt = "predicate met: confirm on the real artifact, then Close"
    elif stopped:
        nxt = "STOP line active: spawn nothing; let in-flight finish"
    elif last_red and (not last_green or last_red > last_green):
        nxt = "SAFETY STOP: main is red — land nothing, one fix task, then record main_green"
    elif budget_note and used >= 0.7:
        nxt = f"stop spawning{budget_note}: land what is verified"
    elif len(ready) >= 3 or len(human) >= 3:
        nxt = "stop spawning implementation: land the ready queue first"
    elif len(landed_recent) == 0 and len(fails_recent) >= 2:
        nxt = "stop spawning: no landing and 2+ failures in 3h — find the cause"
    elif len(live) < cap:
        nxt = f"may spawn {cap - len(live)} more"
    else:
        nxt = "at cap: drain and land"
    lines.append(f"next: {nxt}{budget_note if 'budget' not in nxt else ''}")
    print("\n".join(lines))


def cmd_record(argv):
    p = Program(argv[0])
    ev = argv[1]
    pr = opt(argv, "--pr")
    row = p.append(ev, ticket=opt(argv, "--ticket"), pr=int(pr) if pr else None, sha=opt(argv, "--sha"), note=opt(argv, "--note"))
    print(json.dumps(row, ensure_ascii=False))


def cmd_verdict(argv):
    p = Program(argv[0])
    pr = int(opt(argv, "--pr"))
    src = opt(argv, "--source")
    if not src:
        sys.exit("--source names who produced the verdict (e.g. codex-review, verifier:codex, worker-selfproof)")
    v = pr_view(p.cfg["repo"], pr)
    row = p.append("verdict", pr=pr, sha=v["headRefOid"], patch_id=patch_id(p.cfg["repo"], pr),
                   source=src, result=opt(argv, "--result", "pass"), note=opt(argv, "--note"))
    print(json.dumps(row, ensure_ascii=False))


def cmd_land_check(argv):
    p = Program(argv[0])
    repo, pr = p.cfg["repo"], int(opt(argv, "--pr"))
    events = p.events()
    st = pr_state(events).get(pr, {})
    v = pr_view(repo, pr)
    problems = []
    last_red = max((e["ts"] for e in events if e["ev"] == "main_red"), default=None)
    last_green = max((e["ts"] for e in events if e["ev"] == "main_green"), default=None)
    if last_red and (not last_green or last_red > last_green):
        problems.append("safety stop: main is red")
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
        problems.append("human-gate: waiting for the user's go (record approved)")
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


COMMANDS = {"init": cmd_init, "status": cmd_status, "record": cmd_record, "verdict": cmd_verdict,
            "land-check": cmd_land_check, "landed": cmd_landed, "wait": cmd_wait}

if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    COMMANDS[sys.argv[1]](sys.argv[2:])
