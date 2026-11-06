#!/usr/bin/env python3
"""One standing reflect round's bookkeeping: the lock, the cursor, the last standing PR and waiting.json.

Usage: reflect_round.py begin
       reflect_round.py settle [--repo OWNER/REPO]
       reflect_round.py end --round ROUND [--advance] [--waiting PR_URL | --clear-waiting]

begin takes the round lock ($PROGRAMS_HOME/_standing/reflect/lock, a directory; one older than skill_usage's
LOCK_STALE_HOURS is stale and replaced) and exits 1 while another round holds it. It reads every ledger under
$PROGRAMS_HOME once, and from those rows writes round.json (the round id and each ledger's newest ts) and
round-rows.jsonl (the rows the precheck counts as new, each with its ledger and evidence pointer). It prints the round id.

settle prints the newest PR whose branch starts `reflect/standing-`: `merged <url>`, `closed <url>`, `open <url>` or
`none`. It asks gh in --repo, else in the checkout `reflect.skill_repository` names; exit 2 when it can do neither.

end does nothing and exits 2 unless --round names the round round.json holds (a round that outlived its lock must not
finish the one that replaced it). Then it writes or deletes waiting.json, moves the cursor (--advance: each ledger to
the ts begin read, never back, and a key whose program left $PROGRAMS_HOME is dropped), removes the round files and releases the lock. Without --advance the next round reads
the same rows again.
"""
import contextlib, datetime as dt, fcntl, json, os, pathlib, secrets, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "orchestrate" / "scripts"))
sys.path.insert(0, str(HERE))
import skill_usage  # noqa: E402
import orchestrator_feedback  # noqa: E402

BRANCH = "reflect/standing-"


def home():
    return skill_usage.programs_home() / "_standing" / "reflect"


@contextlib.contextmanager
def guarded(base):
    """Taking, replacing and releasing the lock happen one round at a time."""
    base.mkdir(parents=True, exist_ok=True)
    with open(base / "round.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def pointer(name, row):
    """The lessons-ledger Evidence for a ledger row: its own evidence, else <slug>:<ev>:<ts>:<pr or sha>."""
    return row.get("evidence") or f"{name}:{row.get('ev')}:{row.get('ts')}:{row.get('pr') or row.get('sha') or ''}"


def started(lock):
    return skill_usage.iso(dt.datetime.fromtimestamp(lock.stat().st_mtime, dt.timezone.utc))


def release(lock):
    for f in lock.iterdir():  # an older round wrote its start time into the lock as a file
        f.unlink()
    lock.rmdir()


def begin(now=None):
    base = home()
    lock = base / "lock"
    with guarded(base):
        if lock.is_dir():
            if skill_usage.lock_held(skill_usage.programs_home(), now):
                print(f"skip: a round holds the lock since {started(lock)}")
                return 1
            print(f"stale lock from {started(lock)} removed")
            release(lock)
        lock.mkdir()
        round_id = f"{skill_usage.iso(dt.datetime.now(dt.timezone.utc))}-{secrets.token_hex(4)}"
        read = skill_usage.ledgers(skill_usage.programs_home())
        newest = {name: max((r["ts"] for r in rows), key=skill_usage.parse_ts) for name, rows in read.items()}
        try:
            found = skill_usage.new_findings(skill_usage.programs_home(), read)
        except Exception as e:  # the agent reads the ledgers itself rather than skip a round the precheck woke
            print(f"the ledger check failed ({e!r}): read the ledgers by hand")
            found = {}
        lines = [json.dumps({"ledger": name, "pointer": pointer(name, r), **r}, ensure_ascii=False)
                 for name, rows in found.items() for r in rows]
        skill_usage.write_atomic(base / "round-rows.jsonl", "".join(l + "\n" for l in lines))
        skill_usage.write_atomic(base / "round.json", json.dumps({"round": round_id, "newest": newest}))
    print(f"round: {round_id}")
    for name, rows in found.items():
        print(f"new: {name} {len(rows)} ({', '.join(sorted({r['ev'] for r in rows}))})")
    print(f"rows: {base / 'round-rows.jsonl'} ({len(lines)})")
    waiting = orchestrator_feedback.read_json(base / "waiting.json", {})
    if isinstance(waiting, dict) and waiting.get("pr"):
        print(f"waiting: {waiting['pr']}")
    return 0


def gh_prs(repo=None, cwd=None, run=subprocess.run):
    # Searched by branch: a plain list of the newest 100 PRs misses the standing PR in a busy repository. A search in a
    # repository gh cannot see comes back empty, not failed, so the repository is checked first.
    for cmd in (["gh", "repo", "view", *([repo] if repo else []), "--json", "nameWithOwner"],
                ["gh", "pr", "list", "--state", "all", "--limit", "100", "--search", f"head:{BRANCH}",
                 "--json", "number,state,headRefName,createdAt,url", *(["-R", repo] if repo else [])]):
        try:
            out = run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            return None, str(e)
        if out.returncode:
            return None, out.stderr.strip() or f"gh exited {out.returncode}"
    try:
        prs = json.loads(out.stdout)
    except ValueError as e:
        return None, f"gh printed no JSON ({e})"
    return (prs, None) if isinstance(prs, list) else (None, "gh printed no PR list")


def settle(repo=None, list_prs=gh_prs):
    cwd = None
    if not repo:
        cwd = orchestrator_feedback.config().get("skill_repository")
        if not cwd:
            print("settle: no --repo and no reflect.skill_repository")
            return 2
        cwd = str(pathlib.Path(cwd).expanduser())
    prs, err = list_prs(repo, cwd)
    if prs is None:
        print(f"settle: gh failed: {err}")
        return 2
    mine = [p for p in prs if isinstance(p, dict) and str(p.get("headRefName", "")).startswith(BRANCH)]
    if not mine:
        print("none")
        return 0
    last = max(mine, key=lambda p: skill_usage.parse_ts(p.get("createdAt")) or skill_usage.parse_ts("1970-01-01"))
    print(f"{str(last.get('state', 'unknown')).lower()} {last.get('url') or last.get('number')}")
    return 0


def end(round_id, advance=False, waiting=None, clear_waiting=False):
    base = home()
    lock, round_path = base / "lock", base / "round.json"
    with guarded(base):
        snap = orchestrator_feedback.read_json(round_path, None)
        if not isinstance(snap, dict) or snap.get("round") != round_id:
            found = snap.get("round") if isinstance(snap, dict) else None
            print(f"end: round {round_id} is not the open round ({found or 'none'}): nothing written, lock kept")
            return 2
        # waiting.json first: a round cut off after the cursor moved must still leave the wake for its waiting rows.
        if waiting:
            skill_usage.write_atomic(base / "waiting.json", json.dumps({"pr": waiting}))
            print(f"waiting: {waiting}")
        elif clear_waiting:
            (base / "waiting.json").unlink(missing_ok=True)
            print("waiting: cleared")
        if advance:
            cursor = orchestrator_feedback.read_json(base / "cursor.json", {})
            cursor = cursor if isinstance(cursor, dict) else {}
            for name, ts in (snap.get("newest") or {}).items():
                old = skill_usage.parse_ts(cursor.get(name))
                if old is None or (skill_usage.parse_ts(ts) and skill_usage.parse_ts(ts) > old):
                    cursor[name] = ts
            # A program archived at Close takes its directory out of $PROGRAMS_HOME; its key would stay for good.
            gone = [name for name in cursor if not (skill_usage.programs_home() / name / "ledger.jsonl").exists()]
            for name in gone:
                del cursor[name]
            skill_usage.write_atomic(base / "cursor.json", json.dumps(cursor, ensure_ascii=False, indent=1))
            print(f"cursor: {len(snap.get('newest') or {})} ledgers moved to what begin read"
                  + (f", dropped {len(gone)} archived" if gone else ""))
        for f in (round_path, base / "round-rows.jsonl"):
            f.unlink(missing_ok=True)
        if lock.is_dir():
            release(lock)
            print("lock released")
        else:
            print("no lock to release")
    return 0


def main(argv):
    def usage():
        print("\n".join(__doc__.splitlines()[2:5]), file=sys.stderr)
        return 2
    cmd, rest = (argv[0], argv[1:]) if argv else (None, [])
    if cmd == "begin" and not rest:
        return begin()
    if cmd == "settle" and (not rest or (len(rest) == 2 and rest[0] == "--repo" and not rest[1].startswith("-"))):
        return settle(rest[1] if rest else None)
    if cmd != "end":
        return usage()
    opts, i = {}, 0
    while i < len(rest):
        flag = rest[i]
        if flag in ("--advance", "--clear-waiting") and flag not in opts:
            opts[flag], i = True, i + 1
        elif flag in ("--round", "--waiting") and flag not in opts and i + 1 < len(rest) and not rest[i + 1].startswith("-"):
            opts[flag], i = rest[i + 1], i + 2
        else:
            return usage()
    if "--round" not in opts or ("--waiting" in opts and "--clear-waiting" in opts):
        return usage()
    return end(opts["--round"], "--advance" in opts, opts.get("--waiting"), "--clear-waiting" in opts)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
