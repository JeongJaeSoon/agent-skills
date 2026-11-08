#!/usr/bin/env python3
"""One gh poller per CI target, however many sessions wait on it.

Usage: orch ci-wait <owner/repo#N | run-id> [-R owner/repo] --deadline-min M [--interval S] [--head SHA]

The first caller for a target takes a non-blocking flock (reap-resources guard.hold) and polls GitHub: `gh pr view
--json headRefOid,statusCheckRollup` for a PR, `gh run view --json ...` for a run id. Every other caller for the same
target subscribes: it reads the holder's state file and spends no gh call. When the holder dies (any way, SIGKILL
included) the OS releases the flock and the next subscriber takes over; no PID is written or read.

A PR is finished when its check rollup is non-empty and every check completed; an empty rollup counts as pending
(a repo without CI uses its declared no-CI lane, not this command). A run is finished when its status is completed.
Polls are at least MIN_INTERVAL_S apart across holders (the state file keeps the last poll time), and --interval below
it is raised to it. --deadline-min is required on every call and bounds every sleep and every gh call.

A done result is handed to a caller only when its poll is no older than MIN_INTERVAL_S, since that is the freshest
state one caller could have read itself. --head SHA also requires the result to be for that head.

Exit: 0 CI succeeded, 1 CI failed, 2 usage, 3 deadline passed, 4 error (gh, auth or the state file).
State: <state>/ci-wait/<hash>.json, {gen, target, head, checks_sig, state: polling|done, result, polled_at, summary}.
"""
import argparse, hashlib, json, os, pathlib, re, signal, subprocess, sys, time, uuid

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "reap-resources" / "scripts"))  # run with -I, so nothing else is on sys.path
import guard  # noqa: E402

MIN_INTERVAL_S = 60
SUB_POLL_S = 2  # how often a subscriber reads the state file; no gh call
GH_TIMEOUT_S = 30
MAX_DEADLINE_MIN = 7 * 24 * 60
OK = ("SUCCESS", "SKIPPED", "NEUTRAL")


class Err(Exception):
    pass


def effective_interval(asked):
    return max(int(asked), MIN_INTERVAL_S)


def lock_key(target):
    return "ci-wait:" + target


def lock_path(target):
    return guard.lock_file("other", lock_key(target))


def state_path(target):
    return guard.state() / "ci-wait" / (hashlib.sha1(target.encode()).hexdigest()[:16] + ".json")


def read_state(target):
    try:
        data = json.loads(state_path(target).read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):  # no file yet, or a torn one the next write replaces
        return {}
    except OSError as e:
        raise Err(f"state file: {e}")


def write_state(target, data):
    path = state_path(target)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data))
        os.replace(tmp, path)
    except OSError as e:
        raise Err(f"state file: {e}")


def check_store(target):
    """The lock and state directories must be usable: guard.hold reports a lock it cannot make as 'held elsewhere',
    which would otherwise wait out the deadline."""
    try:
        for p in (lock_path(target), state_path(target)):
            p.parent.mkdir(parents=True, exist_ok=True)
        lock_path(target).open("a").close()
    except OSError as e:
        raise Err(f"state directory: {e}")


def gh(args, deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError
    try:
        p = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=min(GH_TIMEOUT_S, left))
    except subprocess.TimeoutExpired:
        if time.monotonic() >= deadline:
            raise TimeoutError
        raise Err(f"gh {' '.join(args[:2])} timed out")
    except OSError as e:
        raise Err(f"gh: {e}")
    if time.monotonic() >= deadline:  # an answer that arrives after the deadline is no result
        raise TimeoutError
    if p.returncode:
        raise Err(f"gh {' '.join(args[:2])} failed: {p.stderr.strip()[:300]}")
    try:
        return json.loads(p.stdout)
    except ValueError:
        raise Err(f"gh {' '.join(args[:2])} printed no JSON")


def snapshot(kind, repo, ident, deadline):
    """{head, sig, pending, failed, summary} of the target now."""
    repo_args = ["-R", repo] if repo else []
    if kind == "run":
        r = gh(["run", "view", ident, *repo_args, "--json", "headSha,attempt,status,conclusion"], deadline)
        done = r.get("status") == "completed"
        return {"head": r.get("headSha") or "", "sig": f"{ident}/{r.get('attempt')}", "pending": not done,
                "failed": done and (r.get("conclusion") or "").upper() not in OK,
                "summary": f"run {ident} {r.get('status')} {r.get('conclusion') or ''}".strip()}
    r = gh(["pr", "view", ident, *repo_args, "--json", "headRefOid,statusCheckRollup"], deadline)
    checks = r.get("statusCheckRollup") or []
    pending = failed = 0
    for c in checks:
        if c.get("__typename") == "StatusContext":
            state = (c.get("state") or "").upper()
            pending += state in ("PENDING", "EXPECTED")
            failed += state not in ("PENDING", "EXPECTED", "SUCCESS")
        else:
            if (c.get("status") or "").upper() != "COMPLETED":
                pending += 1
            elif (c.get("conclusion") or "").upper() not in OK:
                failed += 1
    sig = hashlib.sha1(json.dumps(sorted((c.get("name") or c.get("context") or "", c.get("startedAt") or "")
                                         for c in checks)).encode()).hexdigest()[:12]
    return {"head": r.get("headRefOid") or "", "sig": sig, "pending": pending > 0 or not checks, "failed": failed > 0,
            "summary": f"{len(checks)} checks, {pending} pending, {failed} failed" if checks else "no checks yet"}


def verdict(st, want_head):
    """The exit code of a done state, or None when it is not a result this caller can use."""
    if st.get("state") != "done" or time.time() - st.get("polled_at", 0) > MIN_INTERVAL_S:
        return None
    if want_head and st.get("head") != want_head:
        return None
    return 0 if st.get("result") == "success" else 1


def report(target, st, rc, via):
    print(f"ci-wait: {st.get('result')} {target} head {str(st.get('head'))[:12]} ({st.get('summary')}){via}", flush=True)
    return rc


def lead(target, kind, repo, ident, args, interval, deadline):
    """Holder loop. Returns an exit code, or raises Err / TimeoutError."""
    prev = read_state(target)
    rc = verdict(prev, args.head)
    if rc is not None:  # a result the last holder wrote a moment ago is still the freshest answer there is
        return report(target, prev, rc, " [reused]")
    gen, shown = uuid.uuid4().hex, None
    due = prev.get("polled_at", 0) + interval  # a new holder keeps the previous holder's spacing
    while True:
        wait = min(due - time.time(), deadline - time.monotonic())
        if wait > 0:
            time.sleep(wait)
        if time.monotonic() >= deadline:
            raise TimeoutError
        # Stamped before the query: a holder killed mid-call still leaves the time of its call for the next one.
        write_state(target, {"gen": gen, "target": target, "state": "polling", "polled_at": time.time(),
                             "summary": "polling"})
        snap = snapshot(kind, repo, ident, deadline)
        now = time.time()
        st = {"gen": gen, "target": target, "head": snap["head"], "checks_sig": snap["sig"], "polled_at": now,
              "summary": snap["summary"], "state": "polling"}
        if not snap["pending"]:
            st.update(state="done", result="failure" if snap["failed"] else "success")
        write_state(target, st)
        if st["state"] == "done" and (not args.head or snap["head"] == args.head):
            return report(target, st, 1 if snap["failed"] else 0, "")
        if (snap["head"], snap["summary"]) != shown:
            shown = (snap["head"], snap["summary"])
            print(f"ci-wait: waiting {target} head {snap['head'][:12]} ({snap['summary']})", flush=True)
        due = now + interval


def parse_target(args):
    """(kind, repo, ident, target key); ValueError for anything but owner/repo#N or a run id."""
    m = re.fullmatch(r"([\w.-]+/[\w.-]+)#(\d+)", args.target)
    if m:
        return "pr", m[1], m[2], f"pr:{m[1]}#{m[2]}"
    if args.target.isdigit():
        return "run", args.repo, args.target, f"run:{args.target}"
    raise ValueError(f"target must be owner/repo#N or a run id, not {args.target!r}")


def run(args):
    kind, repo, ident, target = parse_target(args)
    interval = effective_interval(args.interval)
    if 0 < args.interval < interval:
        print(f"ci-wait: --interval {args.interval} raised to {interval} (minimum {MIN_INTERVAL_S})", file=sys.stderr)
    deadline = time.monotonic() + args.deadline_min * 60
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # exiting releases the flock
    try:
        check_store(target)
        while True:
            if time.monotonic() >= deadline:  # a subscriber resumed late gets no result either
                raise TimeoutError
            st = read_state(target)  # one read: the verdict and the report must describe the same result
            rc = verdict(st, args.head)
            if rc is not None:
                return report(target, st, rc, " [from the holder]")
            with guard.hold([("other", lock_key(target))]) as got:
                if got:
                    return lead(target, kind, repo, ident, args, interval, deadline)
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError
            time.sleep(min(SUB_POLL_S, left))
    except TimeoutError:
        try:
            last = read_state(target)
        except Err:
            last = {}
        print(f"ci-wait: deadline of {args.deadline_min:g} min passed for {target}; last seen: "
              f"{last.get('summary', 'nothing')}", file=sys.stderr)
        return 3
    except Err as e:
        print(f"ci-wait: {e}", file=sys.stderr)
        return 4


def main(argv):
    ap = argparse.ArgumentParser(prog="orch ci-wait", description=__doc__.split("\n\n")[0])
    ap.add_argument("target", help="owner/repo#N or a run id")
    ap.add_argument("-R", "--repo", help="owner/repo for a run id")
    ap.add_argument("--deadline-min", type=float, required=True, help="give up after this many minutes (exit 3)")
    ap.add_argument("--interval", type=int, default=0, help=f"seconds between polls; below {MIN_INTERVAL_S} (or unset) it is {MIN_INTERVAL_S}")
    ap.add_argument("--head", help="accept only a result for this head SHA")
    args = ap.parse_args(argv)
    if not 0 < args.deadline_min <= MAX_DEADLINE_MIN:  # also false for nan
        ap.error(f"--deadline-min must be more than 0 and at most {MAX_DEADLINE_MIN} (7 days)")
    try:
        parse_target(args)
    except ValueError as e:
        ap.error(str(e))
    return run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
