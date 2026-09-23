#!/usr/bin/env python3
"""Program bookkeeping for run-program: the numbers a coordinator decides from.

Usage: python3 prog.py <command> <slug> [options]

  init <slug> --repo OWNER/NAME --run RUN_ID [--linear-project NAME] [--predicate ID,ID,...]
              [--merge-policy autonomous|human-gate] [--ceiling 6] [--deadline ISO8601]
  set <slug> <merge_policy|ceiling|deadline|predicate> VALUE
                                     mirror a change made in the program note
  status <slug>                      predicate, flow, growth and the next move, from live sources
  record <slug> <event> [--ticket T] [--pr N] [--sha S] [--note TEXT]
                                     main_green / main_red need --sha of a landed merge commit
  verdict <slug> --pr N --sha REVIEWED_HEAD --source WHO [--result pass|fail] [--note TEXT]
  land-check <slug> --pr N [--main-fix]
                                     may this PR land now? prints the exact merge command if so;
                                     --main-fix lets the PR that repairs a red main through
  landed <slug> --pr N               record the merge and print the main-CI watch command
  wait <slug> [--timeout-ms 540000] [--rounds 3]
                                     block until the Run inbox holds actionable mail; acks
                                     heartbeat-only batches; never acks actionable ones

Store: ~/.claude/programs/<slug>/ (program.json holds identifiers only; ledger.jsonl is
append-only). Events: spawned, ready, verdict, landed, main_green, main_red, land_failed,
admitted, parked, approved, stop, resume, predicate_verified, config.
"""
import datetime as dt, json, os, pathlib, subprocess, sys

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
    """green, red or pending for the newest landed merge commit; results for older commits never clear it."""
    landed = [e for e in events if e["ev"] == "landed" and e.get("sha")]
    if not landed:
        return "green"
    sha = landed[-1]["sha"]
    results = [e["ev"] for e in events if e["ev"] in ("main_green", "main_red") and e.get("sha") == sha]
    return {"main_green": "green", "main_red": "red"}[results[-1]] if results else "pending"


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
    tickets_done = bool(pred) and not open_ and issues is not None
    last_land = max((i for i, e in enumerate(events) if e["ev"] == "landed"), default=-1)
    verified = any(e["ev"] == "predicate_verified" for e in events[last_land + 1:])
    lines.append(f"predicate: {len(done)}/{len(pred)} tickets done"
                 + ((" — final check recorded" if verified else " — final check not yet recorded") if tickets_done
                    else f" (open: {', '.join(open_[:8])}{'…' if len(open_) > 8 else ''})")
                 if issues is not None else f"predicate: {len(pred)} items (no linear_project; check by hand)")

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
    lines.append(f"flow: main {main} · in-flight {len(live)}/{cap} cap (ceiling {cfg['ceiling']}) · ready-to-land {len(ready)}"
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
    elif len(ready) >= 3 or len(human) >= 3:
        nxt = "stop spawning implementation: land the ready queue first"
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
    row = p.append(ev, ticket=opt(argv, "--ticket"), pr=int(pr) if pr else None, sha=sha, note=opt(argv, "--note"))
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


COMMANDS = {"init": cmd_init, "set": cmd_set, "status": cmd_status, "record": cmd_record, "verdict": cmd_verdict,
            "land-check": cmd_land_check, "landed": cmd_landed, "wait": cmd_wait}

if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    COMMANDS[sys.argv[1]](sys.argv[2:])
