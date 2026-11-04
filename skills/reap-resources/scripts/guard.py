#!/usr/bin/env python3
"""What every cleaner checks at the moment it acts, whichever cleaner it is: a lock per target, the rule's owner, and
guards that only ever keep.

Locks: <state>/locks/<kind>-<hash>.lock, taken without waiting and always in LOCK_ORDER (card, compose project,
container, volume, process, serve port, the rest), once at the top of a cleaner. An inner call in the same thread that asks for
a lock already held goes on without taking it again. A lock another process holds means that process is acting on
the target; the caller leaves it. Locks only keep two cleaners from acting at the same moment: the recheck each one
does inside its lock is what keeps a target already handled from being handled twice.

Ownership: <state>/sweep-owner.json, {"generation": N, "rules": {"<rule>": "sweep"}, "interval": seconds}. Without the
file every rule is its old cleaner's, as before it existed. A file that cannot be read stops every old cleaner for
that round. A rule the sweep owns goes back to its old cleaner while the sweep's last good round
(<state>/sweep-last.json) is older than three intervals.

A card's own guards (patrol, HEAD, new terminals, a human's prompt, a parked session) are cardguard.guard's, which
each card cleaner runs inside cardguard.card_lock, the same card lock as here.
"""
import contextlib, fcntl, hashlib, json, math, os, pathlib, sys, threading, time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import reap  # noqa: E402

LOCK_ORDER = ("card", "project", "container", "volume", "proc", "serve", "other")
_LOCAL = threading.local()  # .held: (kind, key) -> open lock file, in this thread's call chain


def held():
    """This thread's held locks. A forked child starts with none: the parent's are not its to re-enter."""
    if getattr(_LOCAL, "pid", None) != os.getpid():
        _LOCAL.held, _LOCAL.pid = {}, os.getpid()
    return _LOCAL.held

# The rule each cleaner's target is acted on under. A card's rule follows its cards.py verdict.
RULE_OF = {"compose-stack": "stack:registered", "stack-volume": "volume:prefixed", "stopped-container": "container:stopped",
           "labeled-volume": "volume:labeled", "tmpdir": "trash:tmpdir",
           "evidence": "trash:evidence", "gui-app": "gui:registered", "orca-worker": "worker:reclaimable",
           "program": "program:archive", "state-file": "state:rotate", "user-folder": "user-folder:trash",
           "orphan": "proc:orphan", "chrome-window": "browser:chrome"}
CARD_RULE = {"삭제": "card:landed", "터미널만 닫기": "card:close-idle"}
HARD_RULE = {"card": "card:strict", "stack": "stack:gone", "listener": "proc:listener", "serve": "serve:dark",
             "container": "container:stopped", "volume": "volume:labeled"}
FALLBACK_INTERVALS = 3
DEFAULT_INTERVAL = 1800


def state():
    return reap.STATE


# ---------------------------------------------------------------- locks

def lock_file(kind, key):
    return state() / "locks" / f"{kind}-{hashlib.sha1(str(key).encode()).hexdigest()[:16]}.lock"


@contextlib.contextmanager
def hold(targets):
    """Take every (kind, key) lock in LOCK_ORDER without waiting. Yields True once all are held (by this call or by
    one further up in this thread), False when another process or thread holds one or a lock file cannot be made; then
    nothing taken here stays held."""
    mine = held()
    todo = sorted({t for t in targets if t not in mine}, key=lambda t: (LOCK_ORDER.index(t[0]), str(t[1])))
    taken = []
    try:
        for t in todo:
            f = None
            try:
                p = lock_file(*t)
                p.parent.mkdir(parents=True, exist_ok=True)
                f = open(p, "w")
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:  # held elsewhere, or a lock file that cannot be made: either way, not this one's to act on
                if f:
                    f.close()
                while taken:
                    mine.pop(taken.pop()).close()
                yield False
                return
            mine[t] = f
            taken.append(t)
        yield True
    finally:
        for t in reversed(taken):
            mine.pop(t).close()


def targets(item):
    """The locks a reap.py or reap_hard.py item is acted on under."""
    kind, key = item["kind"], item.get("key") or ""
    if kind in ("card", "orca-worktree"):
        return [("card", reap.real(item.get("name") or item.get("id") or item.get("path")))]
    if kind in ("compose-stack", "stack"):
        return [("project", item.get("name") or item.get("id"))]
    if kind in ("stack-volume", "labeled-volume", "volume"):
        return [("volume", item["name"])]
    if kind in ("stopped-container", "container"):
        return [("container", item["cid"])]
    if kind == "docker" and key.startswith("volume:"):  # the same volume, whichever kind names it
        return [("volume", key.partition(":")[2])]
    if kind == "serve":
        return [("serve", str(item["port"]))]
    if kind == "gui-app":
        return [("proc", str(item.get("gpid")))]
    if kind == "listener" or key.startswith("proc:"):
        return [("proc", str(item["pid"]))]
    return [("other", key or f"{kind}:{item.get('id')}")]


def rule(item, cleaner="reap"):
    """The rule an item is acted on under: reap.py's kinds ("reap") or reap-hard's ("hard"). None for a manual kind."""
    if cleaner == "hard":
        return HARD_RULE.get(item["kind"])
    if item["kind"] == "card":
        return CARD_RULE.get(item.get("verdict"))
    return RULE_OF.get(item["kind"])


# ---------------------------------------------------------------- ownership

class Unreadable(Exception):
    """The ownership file is there but cannot be read: no old cleaner acts this round."""


def ownership():
    """The ownership file, or None when there is none."""
    path = state() / "sweep-owner.json"
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    except OSError as e:
        raise Unreadable(f"{path}: {e.strerror}")
    except ValueError as e:  # not UTF-8
        raise Unreadable(f"{path}: {e}")
    try:
        return checked_ownership(json.loads(text))
    except (ValueError, OverflowError) as e:  # OverflowError: an integer too large for a float
        raise Unreadable(f"{path}: {e}")


def checked_ownership(own):
    interval = own.get("interval", DEFAULT_INTERVAL) if isinstance(own, dict) else None
    rules = own.get("rules", {}) if isinstance(own, dict) else None
    if not isinstance(rules, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in rules.items()):
        raise ValueError("not an ownership object")
    if not isinstance(own, dict) or \
            not isinstance(own.get("generation", 0), int) or isinstance(interval, bool) or \
            not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval <= 0:
        raise ValueError("not an ownership object")
    return own


def old_acts(rule_name, own=None, now=None):
    """(True, None) when the old cleaner acts on rule_name, (False, why) when the sweep does. A rule the sweep owns
    falls back to the old cleaner, with a note, while the sweep's last good round is too old."""
    if rule_name is None or own is None or (own.get("rules") or {}).get(rule_name) != "sweep":
        return True, None
    interval = float(own.get("interval", DEFAULT_INTERVAL))
    path = state() / "sweep-last.json"
    try:
        last = json.loads(path.read_text())["t"]
        if isinstance(last, bool) or not isinstance(last, (int, float)) or not math.isfinite(last):
            raise TypeError
    except FileNotFoundError:
        last = None  # the sweep never had a good round
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return False, f"{path} 을 읽지 못해 {rule_name} 을 누가 처리할지 모름"
    if last is None or (now or time.time()) - last > FALLBACK_INTERVALS * interval:
        return True, f"sweep 회차가 {FALLBACK_INTERVALS}주기 넘게 없어 옛 주체가 {rule_name} 을 대신 처리"
    return False, f"{rule_name} 은 sweep 소유(세대 {own.get('generation')})"


def check_owner(rule_name, notes=None):
    """Read the ownership file again, right before acting inside the target's lock. None to go on, else why to leave
    the target. An unreadable file leaves it too."""
    try:
        go, why = old_acts(rule_name, ownership())
    except Unreadable as e:
        return f"소유권 파일을 읽지 못함: {e}"
    if go and why and notes is not None:
        notes.append(why)
    return None if go else why


# ---------------------------------------------------------------- guards (each returns why to keep, or None)

class NoProtect(Exception):
    pass


def read_protect(path, missing_ok=False):
    """{"port": {int}, "pid": {int}, "path": [str], "project": {str}}. A line it cannot read stops the pass, and so
    does a missing file unless missing_ok: a guess at what to keep is how a lead's card goes."""
    out = {"port": set(), "pid": set(), "path": [], "project": set()}
    try:
        text = pathlib.Path(path).expanduser().read_text()
    except FileNotFoundError as e:
        if missing_ok:
            return out
        raise NoProtect(f"보호 목록 {path} 을 읽지 못함({e.strerror}): 아무것도 하지 않는다")
    except OSError as e:
        raise NoProtect(f"보호 목록 {path} 을 읽지 못함({e.strerror}): 아무것도 하지 않는다")
    except ValueError as e:
        raise NoProtect(f"보호 목록 {path} 을 읽지 못함({e}): 아무것도 하지 않는다")
    for n, line in enumerate(text.splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        kind, _, value = line.partition(" ")
        value = value.strip()
        if kind in ("port", "pid") and value.isascii() and value.isdigit() and len(value) <= 10:
            out[kind].add(int(value))
        elif kind == "path" and value:
            out["path"].append(reap.real(os.path.expanduser(value)))
        elif kind == "project" and value:
            out["project"].add(value)
        else:
            raise NoProtect(f"보호 목록 {path}:{n} 을 읽지 못함: {line!r}")
    return out


def protected_path(p, protect):
    return any(reap.under(p, q) or reap.under(q, p) for q in protect["path"])


def settings():
    """~/.claude/agent-skills.json as the cleaners read it: {} when there is none, None when it is there and cannot be
    read or is not an object."""
    path = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG", "~/.claude/agent-skills.json")).expanduser()
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def protect_file():
    """The protect file the cleaners share: reap-hard's `protect_file` setting, or its default path. A settings file
    that cannot be read, or a setting of the wrong shape, raises NoProtect: guessing which file was meant could skip
    the person's list."""
    data = settings()
    cfg = data.get("reap-hard", {}) if isinstance(data, dict) else None
    path = cfg.get("protect_file", "~/.config/agent-skills/reap-protect.txt") if isinstance(cfg, dict) else None
    if not isinstance(path, str) or not path:
        raise NoProtect("설정 reap-hard.protect_file 을 읽지 못함: 아무것도 하지 않는다")
    return path


def always_on():
    """reap.pressure.always_on, the compose projects always kept. No settings file, or one that is not JSON, is none
    (as before guard.py); a `reap`/`pressure`/`always_on` of the wrong shape raises ValueError, which stops the caller."""
    path = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG", "~/.claude/agent-skills.json")).expanduser()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return set()
    names = data
    for k in ("reap", "pressure", "always_on"):  # a level left out (or null) is none; any other shape is unreadable
        if not isinstance(names, dict):
            raise ValueError("설정 reap.pressure.always_on 을 읽지 못함")
        names = names.get(k)
        if names is None:
            return set()
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError("설정 reap.pressure.always_on 을 읽지 못함")
    return set(names)


def proc(pid, start, cmd=None):
    """Why to keep a process: gone or another process on the pid, or a person's shell in its tree."""
    rows = reap.processes()
    r = rows.get(pid)
    if not r or r["start"] != start or (cmd is not None and r["cmd"] != cmd):
        return "사라졌거나 다른 프로세스"
    if human := reap.human_in_tree(pid, rows):
        return f"트리에 사람이 띄운 셸·그 자식(pid {human[0]})"
    return None


def project(name, protect):
    if name in protect["project"] or name in always_on():
        return "보호 목록·상시 서비스의 프로젝트"
    return None


def port(n, protect):
    return "보호 포트" if n in protect["port"] else None


LOCK_BUSY = "대상 잠금을 잡지 못함(다른 정리 주체가 처리 중)"


def keep(i, protect, cleaner="reap", rule_name=None, notes=None):
    """Why to leave one target, inside its lock: the owner first, then the guards. A guard that fails keeps it, so
    one target's read error never ends a cleaner's whole round."""
    try:
        return check_owner(rule_name if rule_name is not None else rule(i, cleaner), notes) or \
            item(i, protect, cleaner)
    except Exception as e:
        return f"검사를 마치지 못함({type(e).__name__}: {str(e)[:120]})"


def item(i, protect, cleaner="reap"):
    """The guards for one reap.py or reap_hard.py target, by kind: why to keep it, or None."""
    kind = i["kind"]
    if kind == "card":  # cardguard.guard, inside cardguard.card_lock, in the cleaner's own act
        return None
    if kind in ("compose-stack", "stack"):
        return project(i.get("name") or i.get("id"), protect)
    if kind in ("stopped-container", "container", "labeled-volume", "volume"):
        hit = sorted(set(i.get("ports") or ()) & protect["port"])
        why = project(i["name"], protect) or (i.get("project") and project(i["project"], protect)) or \
            (f"보호 포트 {hit[0]}" if hit else None)
        if why or kind in ("stopped-container", "container"):
            return why
    if kind in ("stack-volume", "labeled-volume", "volume") or (i.get("key") or "").startswith("volume:"):
        owners = i.get("owners")
        return next(filter(None, (project(o, protect) for o in owners or ())), None) or \
            reap.owner_hold(owners, reap.owner_facts())
    if kind == "serve":
        return port(i["port"], protect) or port(i["local"], protect)
    if kind == "gui-app":
        return proc(i["gpid"], i["start"])
    if kind == "listener" or (i.get("key") or "").startswith("proc:"):
        if i["pid"] in protect["pid"]:
            return "보호 pid"
        return proc(i["pid"], i["start"], i.get("cmd") if cleaner == "reap" else None)
    path = i.get("name") if kind in ("tmpdir", "evidence", "user-folder", "program") else None
    if path and protected_path(reap.real(path), protect):
        return "보호 목록의 경로"
    return None
