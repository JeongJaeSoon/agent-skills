"""Who holds a shared resource: a base branch's exclusive landing lane, or anything else lanes take turns on (a
browser profile, a monitoring login). The fleet collector lists them into state.json for the dashboard and the
orch-panel mod.

A lane lock is `ExclusiveLock` in prog.py. A resource hold uses the same shape: one file per resource under
$PROGRAMS_HOME/_locks holding the current holder, and lock_acquired / lock_released rows in a ledger
(_locks/holds.jsonl here, since a hold need not belong to a program). A hold not refreshed within its ttl
belonged to a session that died holding it, and the next reader breaks it.
"""
import datetime as dt, fcntl, json, os, pathlib, re

import fleet

RESOURCE_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,39}")
TTL_MIN = 120


def locks_dir():
    return pathlib.Path(os.environ.get("PROGRAMS_HOME", "~/.claude/programs")).expanduser() / "_locks"


def _file(resource):
    return locks_dir() / f"res__{resource}.lock"


def _log(ev, **fields):
    row = {"ts": fleet.iso(fleet.utcnow()), "ev": ev, **{k: v for k, v in fields.items() if v is not None}}
    with (locks_dir() / "holds.jsonl").open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _locked(fn):
    locks_dir().mkdir(parents=True, exist_ok=True)
    with (locks_dir() / "holds.lock").open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        return fn()


def _current(resource):
    """The live hold on resource, breaking it first when its ttl ran out."""
    held = fleet.read_json(_file(resource), None)
    if not held:
        return None
    at = fleet.parse(held.get("ts"))
    age = fleet.utcnow() - at if at else dt.timedelta(0)
    if age >= dt.timedelta(minutes=held.get("ttl_min") or TTL_MIN):
        _file(resource).unlink(missing_ok=True)
        _log("lock_released", resource=resource, by=held.get("by"), note=f"broken after {str(age).split('.')[0]}")
        return None
    return held


def check(resource):
    if not RESOURCE_RE.fullmatch(resource or ""):
        raise ValueError("a resource name is lowercase letters, digits, '.', '_' or '-' (browser, grafana-login)")


def hold(resource, by, note=None, ttl_min=TTL_MIN):
    """Take resource for by, or refresh by's own hold. Raises with the holder when someone else has it."""
    check(resource)

    def go():
        held = _current(resource)
        if held and held.get("by") != by:
            age = fleet.utcnow() - (fleet.parse(held.get("ts")) or fleet.utcnow())
            raise ValueError(f"{resource} is held by {held.get('by')} for {int(age.total_seconds() // 60)}m"
                             + (f" ({held['note']})" if held.get("note") else "")
                             + "; wait, ask them, or `orch release --force` if they are gone")
        kept = held.get("note") if held else None
        row = {"resource": resource, "by": by, "note": fleet.mask(note, 120) or kept, "ttl_min": ttl_min,
               "ts": fleet.iso(fleet.utcnow())}
        fleet.write_atomic(_file(resource), json.dumps(row, ensure_ascii=False))
        if not held:
            _log("lock_acquired", resource=resource, by=by, note=row["note"])
        return row
    return _locked(go)


def release(resource, by, force=False):
    check(resource)

    def go():
        held = _current(resource)
        if not held:
            return None
        if held.get("by") != by and not force:
            raise ValueError(f"{resource} is held by {held.get('by')}, not {by}; --force releases it anyway")
        _file(resource).unlink(missing_ok=True)
        _log("lock_released", resource=resource, by=held.get("by"), note=f"forced by {by}" if held.get("by") != by else None)
        return held
    return _locked(go)


def current():
    """Every hold now: resource holds, then the lane locks prog.py keeps beside them."""
    out = []
    if not locks_dir().is_dir():
        return out
    for path in sorted(locks_dir().glob("*.lock")):
        if path.name == "holds.lock":
            continue
        if path.name.startswith("res__"):
            held = _locked(lambda: _current(path.name[5:-5]))
            if held:
                out.append({"kind": "resource", "resource": held["resource"], "by": held.get("by"),
                            "note": held.get("note"), "at": held.get("ts")})
            continue
        held = fleet.read_json(path, None)
        if held:
            # ExclusiveLock names the file `<owner>__<repo>@<base>.lock`.
            out.append({"kind": "lane", "resource": path.stem.replace("__", "/"),
                        "by": f"#{held['pr']}" if held.get("pr") else None,
                        "note": held.get("program"), "at": held.get("ts")})
    return out
