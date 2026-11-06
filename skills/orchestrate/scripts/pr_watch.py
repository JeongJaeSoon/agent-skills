#!/usr/bin/env python3
"""PR watcher for the Monitor tool: one line when a PR needs a turn, nothing otherwise.

Usage: pr_watch.py --repo OWNER/REPO [--repo ...] [--branch REGEX] [--land-check SLUG]
                   [--interval 300] [--limit 50] [--state PATH] [--once]

Each round lists the PRs of every --repo whose head branch matches --branch and prints a line only for:
  MERGED or CLOSED, CI turning to failure, `orch land-check` turning READY (with --land-check),
  a conflict (DIRTY) appearing or clearing, a new PR opening.
BLOCKED, UNKNOWN, CLEAN, UNSTABLE and pending checks come and go without a line. The first round only records
the current state; with --state a restarted watcher compares against the file instead. A round where `gh` fails
or prints something unreadable is skipped. --interval is 300 s or more: a shorter one wakes the session for
changes nobody can act on. Run it as `Monitor` with `timeout_ms` set; it stops when the Monitor does.
"""
import argparse, json, os, pathlib, re, shutil, subprocess, sys, time

MIN_INTERVAL = 300
FAILED = ("FAILURE", "ERROR", "TIMED_OUT")
ORCH = shutil.which("orch") or str(pathlib.Path(__file__).resolve().parents[3] / "bin" / "orch")


def sh(args):
    if args[0] == "orch":
        args = [ORCH, *args[1:]]
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=120).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def snapshot(repos, branch, land_slug, limit, run=sh):
    """{"owner/repo#N": {state, dirty, fail, ready}} for the matching PRs, or None when gh could not be read."""
    cur = {}
    for repo in repos:
        out = run(["gh", "pr", "list", "-R", repo, "--state", "all", "--limit", str(limit),
                   "--json", "number,state,headRefName,mergeStateStatus,statusCheckRollup"])
        try:
            prs = json.loads(out)
        except ValueError:
            return None
        for p in prs:
            if not re.search(branch, p["headRefName"]):
                continue
            checks = p.get("statusCheckRollup") or []
            fail = any((c.get("conclusion") or c.get("state") or "").upper() in FAILED for c in checks)
            ready = False
            if land_slug and p["state"] == "OPEN" and p["mergeStateStatus"] == "CLEAN":
                lines = run(["orch", "land-check", land_slug, "--pr", str(p["number"])]).strip().splitlines()
                ready = bool(lines) and lines[-1].startswith("READY")
            cur[f"{repo}#{p['number']}"] = {"state": p["state"], "dirty": p["mergeStateStatus"] == "DIRTY",
                                            "fail": fail, "ready": ready}
    return cur


def events(old, new):
    for key, n in sorted(new.items()):
        o = old.get(key)
        if o is None:
            if n["state"] == "OPEN":
                yield f"{key} opened"
            continue
        if n["state"] != o["state"] and n["state"] in ("MERGED", "CLOSED"):
            yield f"{key} {n['state']}"
        if n["state"] != "OPEN":
            continue
        if n["dirty"] != o["dirty"]:
            yield f"{key} {'conflict (DIRTY)' if n['dirty'] else 'conflict resolved'}"
        if n["fail"] and not o["fail"]:
            yield f"{key} CI failed"
        if n["ready"] and not o["ready"]:
            yield f"{key} land-check READY"


def load(path):
    try:
        data = json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save(path, state):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state))
    os.replace(tmp, path)


def watch(read, interval, emit, store, sleep, rounds=None, old=None):
    n = 0
    while True:
        new = read()
        if new is not None:
            if old is not None:
                for line in events(old, new):
                    emit(line)
            old = new
            store(new)
        n += 1
        if rounds is not None and n >= rounds:
            return
        sleep(interval)


def parse(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", action="append", required=True, help="OWNER/REPO, repeatable")
    ap.add_argument("--branch", default="", help="regex a head branch must match (default: all)")
    ap.add_argument("--land-check", dest="land_check", help="program slug; turns on the READY line")
    ap.add_argument("--interval", type=int, default=MIN_INTERVAL, help=f"seconds between rounds, {MIN_INTERVAL} or more")
    ap.add_argument("--limit", type=int, default=50, help="PRs read per repo")
    ap.add_argument("--state", help="file that keeps the state across restarts")
    ap.add_argument("--once", action="store_true", help="one round, then exit")
    args = ap.parse_args(argv)
    if args.interval < MIN_INTERVAL:
        ap.error(f"--interval must be {MIN_INTERVAL} seconds or more")
    return args


def main(argv):
    args = parse(argv)
    old = load(args.state) if args.state else None
    store = (lambda s: save(args.state, s)) if args.state else (lambda s: None)
    try:
        watch(lambda: snapshot(args.repo, args.branch, args.land_check, args.limit), args.interval,
              lambda line: print(line, flush=True), store, time.sleep, rounds=1 if args.once else None, old=old)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
