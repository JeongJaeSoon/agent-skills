#!/usr/bin/env python3
"""PR watcher for the Monitor tool: one line when a PR needs a turn, nothing otherwise.

Usage: pr_watch.py --repo OWNER/REPO [--repo ...] [--branch REGEX] [--land-check SLUG]
                   [--interval 300] [--limit 100] [--once]

Each round lists the PRs of every --repo whose head branch matches --branch and prints a line only for:
  MERGED or CLOSED, CI turning to failure, `orch land-check` turning READY (with --land-check),
  a conflict (DIRTY) appearing or clearing, a new or reopened PR.
--land-check SLUG must name a program whose repo is the one watched (read from its program.json).
BLOCKED, UNKNOWN, CLEAN, UNSTABLE and pending checks come and go without a line. The first round only records
the current state, in memory: a restarted watcher starts silent again, so give Monitor a long timeout_ms.
A round where `gh` fails or prints something unreadable is skipped. --interval is 300 s or more: a shorter one wakes the session for
changes nobody can act on. Run it as `Monitor` with `timeout_ms` set; it stops when the Monitor does.
"""
import argparse, json, os, pathlib, re, shutil, subprocess, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import prog  # noqa: E402

MIN_INTERVAL = 300
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
            fail = failing(p.get("statusCheckRollup") or [])
            dirty = None if p["mergeStateStatus"] == "UNKNOWN" else p["mergeStateStatus"] == "DIRTY"
            ready = False
            if land_slug and p["state"] == "OPEN" and not dirty and not fail:
                lines = run(["orch", "land-check", land_slug, "--pr", str(p["number"])]).strip().splitlines()
                # Only a READY or HOLD line is a verdict: a bare note, or "HOLD: PR state unknown …" (gh gave it
                # nothing), says nothing about readiness.
                verdict = [ln for ln in lines if ln.startswith(("READY", "HOLD"))]
                ready = None if not verdict or "state unknown" in verdict[-1] else verdict[-1].startswith("READY")
            cur[f"{repo}#{p['number']}"] = {"state": p["state"], "dirty": dirty, "fail": fail, "ready": ready}
    return cur


def failing(checks):
    """True when the latest run of any check ended in something other than a pass. The rollup keeps every run of a
    head, so which one is latest (a rerun, a cancelled-and-replaced run, one still going) is prog.ci_summary's call,
    the same one `orch land-check` makes."""
    return bool(prog.ci_summary(checks)[0])


def carry(old, new):
    """None means "could not tell" (mergeability still UNKNOWN, a failed land-check): keep what was last known, so a
    blip never reads as a change and back. An open PR that left the list (older than --limit, or a short read) keeps
    its last state too, so it neither reopens as "new" nor loses a merge seen after it returns. A finished PR is
    dropped: back in the list it reads as new, and memory stays bounded by the open PRs."""
    for key, o in old.items():
        if o["state"] == "OPEN":
            new.setdefault(key, o)
    for key, n in new.items():
        for field in ("dirty", "ready"):
            if n[field] is None:
                n[field] = (old.get(key) or {}).get(field, False)
    return new


def events(old, new):
    for key, n in sorted(new.items()):
        o = old.get(key)
        if o is None:
            if n["state"] == "OPEN":
                yield f"{key} opened"
            continue
        if n["state"] != o["state"] and n["state"] in ("MERGED", "CLOSED"):
            yield f"{key} {n['state']}"
        elif n["state"] == "OPEN" and o["state"] != "OPEN":
            yield f"{key} reopened"
        if n["state"] != "OPEN":
            continue
        if n["dirty"] != o["dirty"]:
            yield f"{key} {'conflict (DIRTY)' if n['dirty'] else 'conflict resolved'}"
        if n["fail"] and not o["fail"]:
            yield f"{key} CI failed"
        if n["ready"] and not o["ready"]:
            yield f"{key} land-check READY"


def watch(read, interval, emit, sleep, rounds=None, old=None):
    n = 0
    while True:
        new = read()
        if new is not None:
            new = carry(old or {}, new)
            if old is not None:
                for line in events(old, new):
                    emit(line)
            old = new
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
    ap.add_argument("--limit", type=int, default=100,
                    help="newest PRs read per repo, before --branch filters them: raise it for a quiet branch")
    ap.add_argument("--once", action="store_true", help="one round (nothing prints), then exit")
    args = ap.parse_args(argv)
    if args.interval < MIN_INTERVAL:
        ap.error(f"--interval must be {MIN_INTERVAL} seconds or more")
    if args.land_check and len(args.repo) != 1:
        ap.error("--land-check names one program, which belongs to one repo: pass exactly one --repo")
    if args.land_check:
        owned = program_repo(args.land_check)
        if owned is None:
            ap.error(f"--land-check {args.land_check}: no readable program.json under {programs_home()}")
        if owned.lower() != args.repo[0].lower():
            ap.error(f"--land-check {args.land_check} belongs to {owned}, not {args.repo[0]}: land-check would judge another repo's PR")
    return args


def programs_home():
    return pathlib.Path(os.environ.get("PROGRAMS_HOME") or "~/.claude/programs").expanduser()


def program_repo(slug):
    """The repo a program was initialised for, or None when program.json is missing or unreadable."""
    try:
        repo = json.loads((programs_home() / slug / "program.json").read_text()).get("repo")
    except (OSError, ValueError, AttributeError):
        return None
    return repo if isinstance(repo, str) else None


def main(argv):
    args = parse(argv)
    try:
        watch(lambda: snapshot(args.repo, args.branch, args.land_check, args.limit), args.interval,
              lambda line: print(line, flush=True), time.sleep, rounds=1 if args.once else None)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
