#!/usr/bin/env python3
"""One standing reflect round's bookkeeping: the lock, the cursor, the last standing PR and waiting.json.

Usage: reflect_round.py begin
       reflect_round.py settle [--repo OWNER/REPO]
       reflect_round.py end [--advance] [--waiting PR_URL | --clear-waiting]

begin takes the round lock ($PROGRAMS_HOME/_standing/reflect/lock, a directory; one older than skill_usage's
LOCK_STALE_HOURS is stale and replaced) and exits 1 while another round holds it. It then snapshots the newest `ts` of
every ledger under $PROGRAMS_HOME into round.json, and writes the rows the precheck counts as new (skill_usage
new_findings) to round-rows.jsonl, one per line with its ledger and its evidence pointer. The newest ts is read first,
so a row appended during the round is read again next round rather than skipped.

settle prints the newest PR whose branch starts `reflect/standing-`: `merged <url>`, `closed <url>`, `open <url>` or
`none`. It asks gh in --repo, else in the checkout `reflect.skill_repository` names; exit 2 when it can do neither.

end moves the cursor (--advance: each ledger to the ts begin saw, never back), writes or deletes waiting.json, removes
the round files and releases the lock. Without --advance the next round reads the same rows again.
"""
import datetime as dt, json, pathlib, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "orchestrate" / "scripts"))
sys.path.insert(0, str(HERE))
import skill_usage  # noqa: E402
import orchestrator_feedback  # noqa: E402

BRANCH = "reflect/standing-"


def home():
    return skill_usage.programs_home() / "_standing" / "reflect"


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
    base.mkdir(parents=True, exist_ok=True)
    lock = base / "lock"
    try:
        lock.mkdir()
    except FileExistsError:
        if skill_usage.lock_held(skill_usage.programs_home(), now):
            print(f"skip: a round holds the lock since {started(lock)}")
            return 1
        print(f"stale lock from {started(lock)} removed")
        try:
            release(lock)
            lock.mkdir()
        except (FileExistsError, FileNotFoundError):  # another round replaced the stale lock first
            print("skip: another round took the lock")
            return 1
    newest = {}
    for led in sorted(skill_usage.programs_home().glob("*/ledger.jsonl")):
        rows = skill_usage._jsonl(led)
        if rows:
            newest[led.parent.name] = max((r["ts"] for r in rows), key=skill_usage.parse_ts)
    try:
        found = skill_usage.new_findings(skill_usage.programs_home())
    except Exception as e:  # the agent reads the ledgers itself rather than skip a round the precheck woke
        print(f"the ledger check failed ({e!r}): read the ledgers by hand; the cursor still moves only with end --advance")
        found = {}
    lines = [json.dumps({"ledger": name, "pointer": pointer(name, r), **r}, ensure_ascii=False)
             for name, rows in found.items() for r in rows]
    skill_usage.write_atomic(base / "round-rows.jsonl", "".join(l + "\n" for l in lines))
    skill_usage.write_atomic(base / "round.json", json.dumps({"started": started(lock), "newest": newest}))
    for name, rows in found.items():
        print(f"new: {name} {len(rows)} ({', '.join(sorted({r['ev'] for r in rows}))})")
    print(f"rows: {base / 'round-rows.jsonl'} ({len(lines)})")
    waiting = orchestrator_feedback.read_json(base / "waiting.json", {})
    if isinstance(waiting, dict) and waiting.get("pr"):
        print(f"waiting: {waiting['pr']}")
    return 0


def gh_prs(repo=None, cwd=None):
    # Searched by branch: a plain list of the newest 100 PRs misses the standing PR in a busy repository. A search in a
    # repository gh cannot see comes back empty, not failed, so the repository is checked first.
    for cmd in (["gh", "repo", "view", *([repo] if repo else []), "--json", "nameWithOwner"],
                ["gh", "pr", "list", "--state", "all", "--limit", "100", "--search", f"head:{BRANCH}",
                 "--json", "number,state,headRefName,createdAt,url", *(["-R", repo] if repo else [])]):
        try:
            out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            return None, str(e)
        if out.returncode:
            return None, out.stderr.strip() or f"gh exited {out.returncode}"
    try:
        return json.loads(out.stdout), None
    except ValueError as e:
        return None, f"gh printed no JSON ({e})"


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
    mine = [p for p in prs if str(p.get("headRefName", "")).startswith(BRANCH)]
    if not mine:
        print("none")
        return 0
    last = max(mine, key=lambda p: skill_usage.parse_ts(p.get("createdAt")) or skill_usage.parse_ts("1970-01-01"))
    print(f"{str(last.get('state', 'unknown')).lower()} {last.get('url') or last.get('number')}")
    return 0


def end(advance=False, waiting=None, clear_waiting=False):
    base = home()
    lock, round_path = base / "lock", base / "round.json"
    if advance:
        snap = orchestrator_feedback.read_json(round_path, None)
        if not isinstance(snap, dict):
            print(f"end: no {round_path} (begin did not run): cursor not moved, lock kept")
            return 2
        cursor = orchestrator_feedback.read_json(base / "cursor.json", {})
        cursor = cursor if isinstance(cursor, dict) else {}
        for name, ts in (snap.get("newest") or {}).items():
            old = skill_usage.parse_ts(cursor.get(name))
            if old is None or (skill_usage.parse_ts(ts) and skill_usage.parse_ts(ts) > old):
                cursor[name] = ts
        skill_usage.write_atomic(base / "cursor.json", json.dumps(cursor, ensure_ascii=False, indent=1))
        print(f"cursor: {len(snap.get('newest') or {})} ledgers moved to what begin saw")
    if waiting:
        skill_usage.write_atomic(base / "waiting.json", json.dumps({"pr": waiting}))
        print(f"waiting: {waiting}")
    elif clear_waiting:
        (base / "waiting.json").unlink(missing_ok=True)
        print("waiting: cleared")
    for f in (round_path, base / "round-rows.jsonl"):
        f.unlink(missing_ok=True)
    if lock.is_dir():
        release(lock)
        print("lock released")
    else:
        print("no lock to release")
    return 0


def main(argv):
    def opt(flag):
        return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else None
    if argv[:1] == ["begin"]:
        return begin()
    if argv[:1] == ["settle"]:
        return settle(opt("--repo"))
    if argv[:1] == ["end"] and not ("--waiting" in argv and "--clear-waiting" in argv):
        if "--waiting" in argv and not opt("--waiting"):
            print("end: --waiting needs the standing PR's URL", file=sys.stderr)
            return 2
        return end("--advance" in argv, opt("--waiting"), "--clear-waiting" in argv)
    print("\n".join(__doc__.splitlines()[2:5]), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
