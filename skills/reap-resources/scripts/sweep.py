#!/usr/bin/env python3
"""The v2 cleaner: reads one patrol round's `candidates`. The shadow records what it would do and closes nothing; the
real mode acts, only on the rules `sweep own` handed it (reap-hard's four: OWNABLE).

  sweep.py run --shadow [--round RID] [--pressure]   decide on the newest complete round (or RID); one line in
                                                    <state>/sweep-shadow.jsonl
  sweep.py run --dry-run [--rule R] [--json]        decide every rule as if owned, print it, write and act on nothing:
                                                    set it beside `reap-hard run --dry-run`
  sweep.py run [--round RID]                        act on the owned rules' candidates: sweep.jsonl, sweep-last.json
  sweep.py own RULE | release RULE                  hand a rule to the sweep, or back to reap-hard (sweep-owner.json)
  sweep.py install [--dry-run]                      turn on the Orca automation that runs `tick` (= skills-sync setup
                                                    enable sweep); no LaunchAgent is written
  sweep.py uninstall                                take down the LaunchAgent an earlier install wrote
  sweep.py config                                   the "sweep" settings key that carries today's reap-hard values
  sweep.py tick                                     what the automation runs: patrol, shadow, reap-hard, real round;
                                                    one tick at a time (<state>/sweep-tick.lock)
  sweep.py diff [--hours 48] [--json]               the shadow's "would act" against what the old cleaners (and the
                                                    sweep itself) did in the same hours

reap-hard starts `run --shadow` detached after each pass that is not a dry run, unless `tick` runs it. One run at a
time (<state>/sweep.lock); a run gives up after RUN_BUDGET_S.

A round is used only when it is complete, under an hour old and not already decided (a refused round is tried again).
Each candidate goes through the checks the real mode will use, in order; the first that fails leaves it:
  needs     every source the candidate relied on read `ok` this round
  when      a pressure-only candidate only in a --pressure run
  owner     the shadow decides every rule as if it owned it, and records who owns it now (guard.old_acts)
  repeat    a target that failed `REPEAT_FAILS` times in a row rests a day (the real mode records failures)
  cap       at most `max_per_round` (sweep.max_per_round, default 50) acts per round
  lock      the real mode takes the target's locks here; the shadow takes none, so it never makes an old cleaner
            leave a target
  guard     guard.item (a card: cardguard.guard): the shared keep-only checks, read fresh
The protect file must be there and readable, as for reap-hard; otherwise the round is refused whole.
"""
import contextlib, datetime as dt, fcntl, json, math, os, pathlib, signal, subprocess, sys, time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import guard, reap  # noqa: E402

FRESH_S = 3600
KEEP_LINES = 500  # five days of 15-minute rounds
REPEAT_FAILS, REST_S = 2, 86400
DEFAULT_CAP = 50
RUN_BUDGET_S, CMD_TIMEOUT_S = 600, 60
HARD_RULES = set(guard.HARD_RULE.values())
# The rules `sweep own` may hand over in this stage: reap-hard's four patrol-fed rules. The janitor's and the pressure
# round's come with the stage after.
OWNABLE = ("card:strict", "stack:gone", "proc:listener", "serve:dark")
LABEL = "io.github.jeongjaesoon.sweep"
LAUNCHCTL = "launchctl"
PLATFORM = sys.platform
PATROL_WAIT_S = 400  # patrol's own budgets: 120 s for the round, 240 s for the candidate scans
STEP_TIMEOUT_S = 900
# How long after a patrol round an old cleaner may take to act on the same target: reap-hard two of its passes and
# ten minutes (its `interval`, 900 s by default), the janitor 4 hours (every 3 hours, and on load). Never an act
# that ended before the round: patrol's scans read after it, so what they still saw is not what that act removed
# (a card made again at the same path and HEAD looks the same).
JANITOR_LAG_S = 4 * 3600
# The longest stretch with no decided round a window may have and still count: patrol rounds come every 30 minutes,
# so this is four in a row missing.
MAX_GAP_S = 2 * 3600
# Rules whose act has the same effect on a target, for matching the shadow against the old cleaners: a compose
# project brought down is one effect whether the janitor's ledger or reap-hard's gone folders chose it.
EFFECT = {**reap.CARD_EFFECT, "stack:registered": "stack:down", "stack:gone": "stack:down"}
CONFIG = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG", "~/.claude/agent-skills.json")).expanduser()


def norm_key(key):
    """One key per target across the cleaners: the janitor's compose-stack:P and reap-hard's stack:P are one project."""
    return "stack:" + key.partition(":")[2] if str(key).startswith("compose-stack:") else key
MATCH_IDENTITY = reap.ACT_IDENTITY


def shadow_log():
    return reap.STATE / "sweep-shadow.jsonl"


def fails_file():
    return reap.STATE / "sweep-fails.json"


def real_log():
    return reap.STATE / "sweep.jsonl"


def last_file():
    return reap.STATE / "sweep-last.json"


def settings():
    """The `sweep` key of the settings file: {"max_per_round": N}."""
    cfg = {}
    with contextlib.suppress(OSError, ValueError, AttributeError):
        cfg = json.loads(CONFIG.read_text()).get("sweep") or {}
    cfg = cfg if isinstance(cfg, dict) else {}
    cap = cfg.get("max_per_round", DEFAULT_CAP)
    return {"max_per_round": cap if isinstance(cap, int) and not isinstance(cap, bool) and cap >= 0 else DEFAULT_CAP}


def read_jsonl(path):
    try:
        lines = path.read_text().splitlines()
    except (OSError, ValueError):
        return []
    out = []
    for line in lines:
        with contextlib.suppress(ValueError):
            row = json.loads(line)
            if isinstance(row, dict):
                out.append(row)
    return out


def append_keep(path, row, keep=KEEP_LINES):
    """Append one line, keeping the last `keep`. Callers hold sweep.lock, so two runs never rewrite it at once."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = read_jsonl(path)[-(keep - 1):] + [row]
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    tmp.replace(path)


def read_fails():
    try:
        d = json.loads(fails_file().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def record_result(fails, key, ok, now):
    """A failed act counts toward a day's rest; a good one clears the count. Returns True when this failure starts the
    rest (the one alert)."""
    if ok:
        fails.pop(key, None)
        return False
    f = fails.setdefault(key, {"n": 0, "until": 0})
    f["n"] += 1
    if f["n"] >= REPEAT_FAILS and f["until"] <= now:
        f["until"] = now + REST_S
        return True
    return False


def resting(fails, key, now):
    f = fails.get(key) or {}
    try:
        return isinstance(f, dict) and float(f.get("until") or 0) > now
    except (TypeError, ValueError):
        return True  # a record that cannot be read keeps resting


def refuse_round(v, rid, done, now):
    """Why the whole round is not decided on, or None."""
    if not isinstance(v, dict):
        return "순찰 판정 파일을 읽지 못함"
    if rid and v.get("round") != rid:
        return f"판정 파일의 회차 {v.get('round')} 가 {rid} 가 아님"
    if not v.get("round"):
        return "판정 파일에 회차 id 가 없음(v2 필드 전)"
    if v.get("complete") is not True:
        return f"회차 {v['round']} 가 끝나지 않음(complete 아님)"
    try:
        age = now - float(v.get("t") or 0)
    except (TypeError, ValueError):
        return "판정 파일의 시각을 읽지 못함"
    if age > FRESH_S:
        return f"회차 {v['round']} 가 {int(age // 60)}분 전 것"
    if v["round"] in done:
        return f"회차 {v['round']} 는 이미 판정함"
    return None


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def cleaner_of(rule_name):
    return "hard" if rule_name in HARD_RULES else "reap"


def interval():
    """Seconds between passes: the `sweep` (else `reap-hard`) setting, 900 by default."""
    with contextlib.suppress(OSError, ValueError, AttributeError, TypeError):
        data = json.loads(CONFIG.read_text())
        n = (guard.hard_cfg({"reap-hard": data.get("reap-hard") or {}, "sweep": data.get("sweep") or {}}) or {}
             ).get("interval", 900)
        if not isinstance(n, bool) and math.isfinite(float(n)) and float(n) > 0:
            return float(n)
    return 900.0


def lags():
    return {"hard": 2 * interval() + 600, "reap": JANITOR_LAG_S}


def shadow_keep(item, protect, cleaner):
    """The shared keep-only guards without the owner check: the shadow decides each rule as if it owned it. A card
    goes through cardguard.guard, as both card cleaners' acts do, with the head and terminals its scan saw."""
    try:
        if item.get("kind") == "card":
            import cardguard
            path, head = item.get("name") or item.get("id") or item.get("path"), item.get("head")
            if not path or not head or item.get("handles") is None:
                return "판정 때 본 HEAD·터미널이 후보에 없음"
            ok, why = cardguard.guard(reap.real(path), head, item["handles"])
            return None if ok else why
        return guard.item(item, protect, cleaner)
    except Exception as e:
        return f"검사를 마치지 못함({type(e).__name__}: {str(e)[:120]})"


def decide(v, protect, now, pressure=False, own=None, fails=None, cap=DEFAULT_CAP, keep=None, lock=None):
    """One row per candidate: {rule, key, identity, decision: act|skip, why, owner}. keep stands in for the guards in
    tests; lock(targets) -> bool is the real mode's lock step (None in the shadow: no lock taken)."""
    keep = keep or shadow_keep
    fails = fails or {}
    sources = v.get("sources") if isinstance(v.get("sources"), dict) else {}
    rows, acts = [], 0
    for c in v.get("candidates") or ():
        if not isinstance(c, dict):
            continue
        rule_name, key = c.get("rule"), c.get("key")
        item = c.get("item") if isinstance(c.get("item"), dict) else {}
        if item.get("kind") == "card" and "handles" not in item:  # a janitor card: its plan's terminals
            item = {**item, "handles": item.get("close")}
        row = {"rule": rule_name, "key": key, "kind": c.get("kind"), "identity": c.get("identity") or {}}
        if finite(c.get("seen")):  # when the scan that saw it started: the start of its diff window
            row["seen_t"] = float(c["seen"])
        try:
            go, owner_note = guard.old_acts(rule_name, own, now)
        except Exception as e:
            go, owner_note = True, f"소유권을 읽지 못함({type(e).__name__})"
        row["owner"] = "old" if go else "sweep"
        if owner_note:
            row["owner_note"] = owner_note
        missing = [n for n in c.get("needs") or () if sources.get(n) != "ok"]
        if not rule_name or not key:
            why = "규칙이나 키가 없는 후보"
        elif missing:
            why = f"이번 회차에 읽지 못한 소스 {', '.join(missing)}"
        elif c.get("when") == "pressure" and not pressure:
            why = "pressure 회차에서만"
        elif resting(fails, key, now):
            why = f"연속 {REPEAT_FAILS}회 실패로 하루 쉼"
        elif acts >= cap:
            why = f"회차 상한 {cap}건"
        elif lock is not None and not lock(guard.targets(item)):
            why = guard.LOCK_BUSY
        else:
            why = keep(item, protect, cleaner_of(rule_name))
        row.update(decision="skip" if why else "act", why=why or c.get("why"))
        acts += not why
        rows.append(row)
    return rows


@contextlib.contextmanager
def file_lock(name):
    """<state>/NAME without waiting: True while held, False when another process has it."""
    reap.STATE.mkdir(parents=True, exist_ok=True)
    with open(reap.STATE / name, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True


def run_lock():
    return file_lock("sweep.lock")


def run_shadow(rid=None, pressure=False, now=None):
    now = now or time.time()
    with run_lock() as got:
        if not got:
            return {"refused": "다른 sweep 회차가 도는 중"}
        log = shadow_log()
        past = read_jsonl(log)
        done = {r.get("round") for r in past if not r.get("refused")}
        out = {"t": now, "mode": "shadow", "pressure": pressure, "round": rid}
        try:
            v = json.loads((reap.STATE / "patrol-verdicts.json").read_text())
        except (OSError, ValueError):
            v = None
        out["refused"] = refuse_round(v, rid, done, now)
        if not out["refused"]:
            out.update(round=v["round"], round_t=float(v["t"]))
            seen = v.get("candidates_t")  # when its scans started: an act before that was on what came before
            if finite(seen):
                out["seen_t"] = max(float(seen), out["round_t"])
            try:
                own = guard.ownership()
                protect = guard.read_protect(guard.protect_file())
            except (guard.Unreadable, guard.NoProtect) as e:
                out["refused"] = str(e)
            else:
                rows = decide(v, protect, now, pressure, own, read_fails(), settings()["max_per_round"])
                out.update(rows=rows, acts=sum(r["decision"] == "act" for r in rows),
                           skips=sum(r["decision"] == "skip" for r in rows))
        last = past[-1] if past else {}
        if out["refused"] and (out["refused"].endswith("이미 판정함") or
                               (last.get("refused") == out["refused"] and last.get("round") == out["round"])):
            return out  # nothing new to record
        append_keep(log, out)
        return out


# ---------------------------------------------------------------- diff

def parse_ts(s):
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z"):
        with contextlib.suppress(ValueError, TypeError):
            return dt.datetime.strptime(s, fmt).timestamp()
    return None


def effect(rule_name):
    return EFFECT.get(rule_name, rule_name)


def old_actions(since):
    """{key: [{t, effect, identity}]} the old cleaners really acted on since `since`: reap-hard passes and reap.py /
    cards.py acts (their logs say whether anything happened)."""
    out = {}
    for r in read_jsonl(reap.STATE / "reap-hard.jsonl"):
        t = r.get("t") if isinstance(r.get("t"), (int, float)) else parse_ts(r.get("ts"))
        rule_name = guard.HARD_RULE.get(r.get("kind"))
        if t is None or t < since or not rule_name or r.get("dry_run"):
            continue
        key, ident = norm_key(f"{r.get('kind')}:{r.get('id')}"), r.get("identity") or {}
        if not r.get("error") and (eff := reap.act_effect(rule_name, r.get("kind"), r.get("result"))):
            out.setdefault(key, []).append({"t": t, "effect": effect(eff), "identity": ident, "handles": None})
        if r.get("handles"):  # the terminals it really closed, whether the removal then went through or not
            out.setdefault(key, []).append({"t": t, "effect": "card:close", "identity": ident,
                                            "handles": r["handles"]})
    # The sweep's own acts (a rule it owns) count as an act on the target, in the same shape as reap.py's.
    for r in read_jsonl(reap.STATE / "reap-actions.jsonl") + read_jsonl(real_log()):
        t = r.get("t")
        if not isinstance(t, (int, float)) or t < since:
            continue
        ident, key = r.get("identity") or {}, norm_key(r.get("key"))
        if r.get("ok") and r.get("effect"):
            out.setdefault(key, []).append(
                {"t": t, "effect": effect(r["effect"]), "identity": ident, "handles": r.get("handles")})
        if r.get("handles") and r.get("effect") != "card:close":
            # Terminals it really closed before it failed or went on to remove the card: a close, whatever came after.
            out.setdefault(key, []).append(
                {"t": t, "effect": "card:close", "identity": ident, "handles": r["handles"]})
    return out


def answers(row, acts):
    """Whether the old acts (those inside the row's window) did what the shadow's row planned: one act of the same
    effect on the same target, or for a close, every terminal the plan would close among those these acts really
    closed, over however many runs."""
    mine = [a for a in acts if a["effect"] == effect(row.get("rule"))
            and same_target(row.get("identity") or {}, a["identity"])]
    if effect(row.get("rule")) != "card:close":
        return bool(mine)
    closed = {h for a in mine for h in a["handles"] or ()}
    return bool(closed) and set((row.get("identity") or {}).get("close") or ()) <= closed


def same_target(plan, did):
    """Whether an act was on the target the plan named: every field the plan carries is in the act's record and
    agrees (a card made again on the same path has another HEAD; a serve entry re-pointed has another local port), and
    a list the plan names (the files of one state-file act) is all inside the act's. A record that does not say is
    not proof."""
    # The same folder inode is the same card: its HEAD may have moved between the scan and the removal.
    same_folder = plan.get("ino") is not None and plan.get("ino") == did.get("ino")
    for k in MATCH_IDENTITY:
        if k not in plan or (same_folder and k == "head"):
            continue
        if k not in did:
            return False
        a, b = plan[k], did[k]
        if isinstance(a, list) and isinstance(b, list):
            if not set(map(str, a)) <= set(map(str, b)):
                return False
        elif a != b:
            return False
    return True


def gone_card(key):
    """A card target whose folder no longer exists. Only cards: a path check is proof of removal for them, and for no
    other kind of target."""
    if not str(key).startswith("card:"):
        return False
    try:
        os.lstat(str(key).partition(":")[2])
    except (FileNotFoundError, NotADirectoryError):
        return True
    except OSError:  # a permission error does not prove the folder is gone
        return False
    return False


def volume_inspect(name):
    """(returncode, stderr) of `docker volume inspect`; (124, "") when it could not run."""
    try:
        r = subprocess.run(["docker", "volume", "inspect", name], capture_output=True, text=True, env=reap.ENV,
                           timeout=CMD_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return 124, ""
    return r.returncode, r.stderr


def gone_volume(key):
    """A stack volume docker says no longer exists. Only that answer counts: a daemon that is down or slow, or a volume
    that is still there, leaves the target as it was. A stack's owner removes its own volumes (a teardown, a purge) in
    the hours between a round and the janitor, and leaves no record in the cleaners' logs."""
    if not str(key).startswith("stack-volume:"):
        return False
    rc, err = volume_inspect(str(key).partition(":")[2])
    return rc != 0 and "no such volume" in err.lower()


def diff(hours=48.0, now=None):
    """Every round's "act" row, matched to an old cleaner's act of the same effect on the same target that ended
    between the scan that saw the target (the row's seen_t, else the round's candidates_t, else the round's start) and
    its rule's lag after the round. Unmatched past the lag is excess; inside it, pending. A key is
    as bad as its worst round."""
    now = now or time.time()
    since = now - hours * 3600
    lag_of = lags()
    rounds = [r for r in read_jsonl(shadow_log()) if isinstance(r.get("t"), (int, float)) and r["t"] >= since]
    for r in rounds:
        r["round_t"] = r["round_t"] if isinstance(r.get("round_t"), (int, float)) else r["t"]
    acted = old_actions(min([since] + [r["round_t"] for r in rounds]))
    keys, rank = {}, {"matched": 0, "external": 1, "pending": 2, "excess": 3}
    gone = {}  # one docker question per target, however many rounds named it
    for r in rounds:
        rt = r["round_t"]
        st = r["seen_t"] if isinstance(r.get("seen_t"), (int, float)) else rt  # the window opens at the scans
        for row in r.get("rows") or ():
            if row.get("decision") != "act":
                continue
            lag = lag_of[cleaner_of(row.get("rule"))]
            # The window opens when the scan that saw the target started, never before the round.
            opens = max(rt, row["seen_t"]) if finite(row.get("seen_t")) else st
            hit = answers(row, [a for a in acted.get(norm_key(row.get("key")), ()) if opens <= a["t"] <= rt + lag])
            state = "matched" if hit else "pending" if now < rt + lag else "excess"
            if state == "excess" and not acted.get(norm_key(row["key"])):
                if row["key"] not in gone:
                    gone[row["key"]] = gone_card(row["key"]) or gone_volume(row["key"])
                if gone[row["key"]]:
                    state = "external"  # nobody the cleaners know of acted on it, and it is gone: cleaned from outside
            k = keys.setdefault(row["key"], {"key": row["key"], "rule": row.get("rule"), "state": state,
                                             "rounds": 0, "first": rt})
            k["rounds"] += 1
            if rank[state] > rank[k["state"]]:
                k["state"] = state
    rows = sorted(keys.values(), key=lambda k: k["key"])
    by_rule = {}
    for k in rows:
        if k["state"] != "external":
            by_rule.setdefault(k["rule"], {"matched": 0, "pending": 0, "excess": 0})[k["state"]] += 1
    decided = sorted(r["round_t"] for r in rounds if not r.get("refused"))
    edges = [since] + decided + [now]
    return {"hours": hours, "rounds": len(rounds), "refused": sum(1 for r in rounds if r.get("refused")),
            "decided": len(decided), "max_gap_s": max(b - a for a, b in zip(edges, edges[1:])),
            "by_rule": by_rule, "excess": [k for k in rows if k["state"] == "excess"],
            "external": [k for k in rows if k["state"] == "external"], "rows": rows}


def verdict(d):
    """passed, excess or not yet: a window with a stretch over MAX_GAP_S and no decided round in it (none at all, or
    the shadow down for a while) proves nothing either way."""
    if d["excess"]:
        return "excess"
    return "passed" if d["decided"] and d["max_gap_s"] <= MAX_GAP_S else "not yet"


def print_diff(d):
    print(f"sweep 그림자 비교: 최근 {d['hours']:g}시간, 회차 {d['rounds']}개(거부 {d['refused']}, 판정한 회차 사이 "
          f"가장 긴 공백 {d['max_gap_s'] / 3600:.1f}시간)")
    if not d["by_rule"] and not d.get("external"):
        print("- 처리하겠다고 한 대상 없음")
    for rule_name, c in sorted(d["by_rule"].items()):
        print(f"- {rule_name}: 일치 {c['matched']} · 대기 {c['pending']} · 넘침 {c['excess']}")
    for r in d["excess"]:
        print(f"  넘침 {r['key']} ({r['rule']})")
    for r in d.get("external") or ():
        print(f"  외부 정리 {r['key']} ({r['rule']}): 정리기 기록에 없고 이미 사라짐(워커 end-session, lead 의 worktree rm, 스택 소유자의 teardown 등)")
    v = verdict(d)
    pending = sum(c["pending"] for c in d["by_rule"].values())
    # Pending plans are inside their lag: the window ending a lag ago is the one they prove.
    print("판정: " + (f"통과(넘침 0, 지연 안이라 아직 대기 {pending}건)" if v == "passed" else f"넘침 {len(d['excess'])}건" if v == "excess" else
                    f"아직 판정 못 함(판정한 회차가 {MAX_GAP_S // 3600}시간 넘게 빈 구간이 있음)"))


# ---------------------------------------------------------------- real mode

ALREADY = "이미 판정함"


def opt(argv, name):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None


def stamp(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(t))


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False))
    tmp.replace(path)


def read_last():
    try:
        d = json.loads(last_file().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def act_item(c, protect, row):
    """Act on one candidate of a rule the sweep owns. Inside the target's lock: the owner read again (the rule may have
    gone back to its old cleaner, or the sweep's rounds stopped), the shared guards, then reap-hard's own act, which
    judges the target again and closes only what the scan saw. Fills `row` as it goes, so a budget that ends the run
    mid-act still leaves what was closed."""
    rule_name, item = c["rule"], c["item"]
    with guard.hold(guard.targets(item)) as got:
        if not got:
            row["result"] = f"건너뜀: {guard.LOCK_BUSY}"
            return
        try:
            go, why = guard.old_acts(rule_name, guard.ownership(), time.time())
        except guard.Unreadable as e:
            row["result"] = f"건너뜀: {guard.OWNER_UNREADABLE}: {e}"
            return
        if go:
            row["result"] = f"건너뜀: {why or rule_name + ' 이 옛 주체로 돌아감'}"
            return
        try:
            why = guard.item(item, protect, "hard")
        except Exception as e:
            why = f"{guard.CHECK_FAILED}({type(e).__name__}: {str(e)[:120]})"
        if why:
            row["result"] = f"건너뜀: {why}"
            return
        sys.path.insert(0, str(HERE.parents[2] / "scripts"))
        import reap_hard  # noqa: E402
        done = {}
        try:
            row["result"] = reap_hard.act(item, reap_hard.config(), protect, done)
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as e:
            row["error"] = str(e)[:300]
        except BaseException as e:  # the run's budget: recorded with what it closed first, then let go up
            row["error"] = f"{type(e).__name__}: {str(e)[:300]}"
            raise
        finally:
            if done.get("closed"):
                row["handles"] = done["closed"]
            if done.get("identity"):
                row["identity"] = done["identity"]


def log_act(c, row, rid):
    """One line in sweep.jsonl per act, shaped like reap-actions.jsonl, which `sweep diff` matches against. Returns
    whether it really did something."""
    item = c["item"]
    ok = "error" not in row and reap.acted(row.get("result"))
    rec = {"t": time.time(), "round": rid, "key": c["key"], "kind": item["kind"], "rule": c["rule"],
           "identity": row.get("identity") or reap.act_identity(item),
           "result": str(row.get("result") or "실패: " + row.get("error", ""))[:200], "ok": ok,
           "effect": reap.act_effect(c["rule"], item["kind"], row.get("result")) if ok else None}
    if row.get("handles"):
        rec["handles"] = sorted(row["handles"])
    with contextlib.suppress(OSError):  # a log line is never worth an act left half-reported
        append_keep(real_log(), rec)
    return ok


def run_round(rid=None, pressure=False, dry=False, now=None, acter=None):
    """Decide on the newest complete patrol round and, unless dry, act on its candidates of the rules the sweep owns.
    dry decides every rule as if owned, takes no lock and writes nothing, so it can be compared with a reap-hard
    --dry-run at the same moment. A real round writes sweep-last.json when it is decided, even with nothing owned: that
    is the "last good round" an owner's old cleaner waits on before it takes a rule back. A round already acted on
    refreshes it too, while that round is still fresh."""
    now = now or time.time()
    acter = acter or act_item
    # A dry-run reads only: no lock file, no state folder made, and a real round or `own` is not held up by it.
    with (contextlib.nullcontext(True) if dry else run_lock()) as got:
        if not got:
            return {"refused": "다른 sweep 회차가 도는 중"}
        last = read_last()
        out = {"t": now, "mode": "dry-run" if dry else "real", "pressure": pressure, "round": rid}
        try:
            v = json.loads((reap.STATE / "patrol-verdicts.json").read_text())
        except (OSError, ValueError):
            v = None
        out["refused"] = refuse_round(v, rid, set() if dry or not last.get("round") else {last["round"]}, now)
        if out["refused"]:
            if out["refused"].endswith(ALREADY) and not dry:
                save_json(last_file(), {**last, "t": now, "at": stamp(now)})
            return out
        out.update(round=v["round"], round_t=float(v["t"]))
        try:
            own = guard.ownership()
            protect = guard.read_protect(guard.protect_file())
        except (guard.Unreadable, guard.NoProtect) as e:
            out["refused"] = str(e)
            return out
        cap, fails = settings()["max_per_round"], read_fails()
        if dry:
            rows = decide(v, protect, now, pressure, own, fails, cap)
            out.update(rows=rows, acts=sum(r["decision"] == "act" for r in rows),
                       skips=sum(r["decision"] == "skip" for r in rows))
            return out
        mine = [c for c in v.get("candidates") or () if isinstance(c, dict) and c.get("rule") in OWNABLE
                and not guard.old_acts(c["rule"], own, now)[0]]
        # The guards run inside each target's lock (act_item), so decide only counts the cap and the rest here.
        rows = decide({**v, "candidates": mine}, protect, now, pressure, own, fails, cap, keep=lambda *a: None)
        rested = []
        try:
            for c, row in zip(mine, rows):
                if row["decision"] != "act":
                    continue
                res = {}
                try:
                    acter(c, protect, res)
                finally:
                    row["ok"] = log_act(c, res, v["round"])
                row["result"] = res.get("result") or "실패: " + res.get("error", "")
                if "error" in res:
                    row["failed"] = True
                    if record_result(fails, c["key"], False, now):
                        rested.append(c["key"])
                elif row["ok"]:
                    record_result(fails, c["key"], True, now)
        finally:
            save_json(fails_file(), fails)
        out.update(rows=rows, acted=sum(bool(r.get("ok")) for r in rows), failed=sum(bool(r.get("failed")) for r in rows),
                   skipped=sum(r["decision"] == "act" and not r.get("ok") and not r.get("failed") for r in rows),
                   kept=sum(r["decision"] == "skip" for r in rows), rested=rested)
        # A candidate of a rule the file gives the sweep but that its old cleaner still holds (no good round of the
        # sweep's yet, or a stale one): this write is that good round, so reap-hard leaves the rule from now on. The round
        # is then not recorded as done, and the next pass acts on it while it is fresh.
        deferred = any(isinstance(c, dict) and c.get("rule") in OWNABLE and guard.old_acts(c["rule"], own, now)[0]
                       and ((own or {}).get("rules") or {}).get(c["rule"]) == "sweep" for c in v.get("candidates") or ())
        out["deferred"] = deferred
        save_json(last_file(), {"t": now, "at": stamp(now), "round": last.get("round") if deferred else v["round"],
                                "owned": sorted(r for r in OWNABLE if ((own or {}).get("rules") or {}).get(r) == "sweep"),
                                **{k: out[k] for k in ("acted", "failed", "skipped", "kept", "rested")}})
        return out


def print_run(out, rule_filter=None):
    dry = out.get("mode") == "dry-run"
    head = "dry-run(아무것도 하지 않음)" if dry else "실행"
    if out.get("refused"):
        print(f"sweep {head}: 거부 — {out['refused']}")
        return
    print(f"sweep {head} 회차 {out['round']}")
    counts = {}
    for r in out["rows"]:
        if r.get("rule") not in OWNABLE or (rule_filter and r["rule"] != rule_filter):
            continue
        c = counts.setdefault(r["rule"], [0, 0])
        c[r["decision"] != "act"] += 1
        tail = f" → {r['result']}" if r.get("result") else ""
        print(f"- {r['rule']} {r['key']}: {'처리' if r['decision'] == 'act' else '남김'} — {r.get('why')}{tail}"
              f"  [소유 {r.get('owner')}]")
    for rule_name, (a, k) in sorted(counts.items()):
        print(f"규칙 {rule_name}: 처리 {a} · 남김 {k}")
    if not counts:
        print("reap-hard 규칙에 해당하는 후보 없음")


# ---------------------------------------------------------------- ownership

def set_owner(rule_name, owned):
    """Hand a rule to the sweep (`own`) or back to its old cleaner (`release`): the ownership file rewritten whole and
    its generation raised, only when something changes. (exit code, message)."""
    if rule_name not in OWNABLE:
        return 2, f"{rule_name!r} 은 이 단계에서 넘길 수 있는 규칙이 아님. 가능: {', '.join(OWNABLE)}"
    with run_lock() as got:
        if not got:
            return 1, "다른 sweep 회차가 도는 중: 끝난 뒤 다시"
        try:
            own = guard.ownership()  # a file that cannot be read is left as it is, whatever it says
        except guard.Unreadable as e:
            return 1, f"소유권 파일을 읽지 못해 바꾸지 않음: {e}"
        own = own or {"generation": 0, "rules": {}}
        rules = dict(own.get("rules") or {})
        if (rules.get(rule_name) == "sweep") == owned:
            return 0, f"{rule_name}: 이미 {'sweep' if owned else '옛 주체'} 소유(세대 {own.get('generation', 0)}), 바꾼 것 없음"
        if owned:
            rules[rule_name] = "sweep"
        else:
            rules.pop(rule_name)
        gen = own.get("generation", 0) + 1
        save_json(reap.STATE / "sweep-owner.json", {**own, "generation": gen, "rules": rules, "interval": interval()})
    if owned:
        note = "" if read_last().get("t") else (
            " sweep 의 첫 정상 회차(sweep-last.json)가 생길 때까지는 옛 주체가 이 규칙을 계속 처리한다.")
        return 0, f"{rule_name}: sweep 소유로 바꿈(세대 {gen}). 다음 옛 회차부터 건너뛴다.{note}"
    return 0, f"{rule_name}: 옛 주체에게 돌려줌(세대 {gen}). 다음 회차부터 reap-hard 가 처리한다."


# ---------------------------------------------------------------- install, config, tick

def plist_path():
    return pathlib.Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def agent_target(label=LABEL):
    return f"gui/{os.getuid()}/{label}"


def launchctl(*args):
    return subprocess.run([LAUNCHCTL, *args], capture_output=True, text=True)


def carried_config():
    """The `sweep` settings key that keeps today's reap-hard values (defaults written out), for a person to merge in."""
    sys.path.insert(0, str(HERE.parents[2] / "scripts"))
    import reap_hard  # noqa: E402
    cfg = reap_hard.config()
    block = {k: cfg[k] for k in ("interval", "idle_minutes", "workspaces", "protect_file", "serve_ranges")}
    return {"sweep": {**block, "max_per_round": settings()["max_per_round"]}}


def print_config():
    print("~/.claude/agent-skills.json 의 최상위에 아래 \"sweep\" 키를 더하면 reap-hard 설정이 그대로 이어진다"
          "(이 명령은 설정 파일을 고치지 않는다. 옛 \"reap-hard\" 키는 두어도 된다: 같은 이름이면 \"sweep\" 쪽이 이긴다).")
    print(json.dumps(carried_config(), ensure_ascii=False, indent=2))


def cmd_install(argv):
    """Turn on the Orca automation that runs `sweep tick`: the same as `skills-sync setup enable sweep`, whose
    definition (automations.SWEEP) is the only one. No LaunchAgent is written any more; `--here` no longer applies
    because the automation points at the checkout's files."""
    sys.path.insert(0, str(HERE.parents[2] / "scripts"))
    import automations  # noqa: E402
    job = automations.SWEEP
    if "--dry-run" in argv:
        w = job.want()
        print(f"[dry-run] Orca automation {job.job_name!r} 로 등록할 것(만들지도 켜지도 않음): "
              f"trigger {w['trigger']}, precheck-timeout {w['precheck-timeout']}초")
        print(f"  precheck: {w['precheck']}")
        print_config()
        return 0
    code, msg = job.enable(False)
    print(f"{job.name}: {msg}", file=sys.stderr if code else sys.stdout)
    if not code:
        print_config()
        print("전환 중에는 옛 LaunchAgent 가 같이 돌 수 있지만 tick 은 한 번에 하나만 돌아(sweep-tick.lock) 안전하다. "
              "첫 tick 이 `skills-sync setup status` 와 <state>/jobs/sweep.json 에 ok 로 남은 것을 본 뒤 "
              "`sweep.py uninstall` 로 LaunchAgent 를 내린다.")
        if PLATFORM == "darwin" and launchctl("print", agent_target()).returncode == 0:
            print(f"옛 LaunchAgent {agent_target()} 가 아직 올라 있음")
    return code


def cmd_uninstall():
    """Take down the LaunchAgent an earlier `install` wrote. The Orca automation is left as it is."""
    loaded = PLATFORM == "darwin" and launchctl("print", agent_target()).returncode == 0
    if loaded:
        launchctl("bootout", agent_target())
    existed = plist_path().exists()
    plist_path().unlink(missing_ok=True)
    print(f"{agent_target()} 를 내리고 plist 를 지움. Orca automation sweep 은 그대로 둔다." if loaded or existed
          else "내릴 LaunchAgent 가 없음. Orca automation sweep 은 그대로 둔다.")
    return 0


def budgeted(fn):
    """fn() under the run budget; None when the budget ran out."""
    reap.RUN_TIMEOUT = CMD_TIMEOUT_S
    signal.signal(signal.SIGALRM, give_up)
    signal.alarm(RUN_BUDGET_S)
    try:
        return fn()
    except Budget:
        print(f"sweep: {RUN_BUDGET_S}초 안에 끝나지 않아 그만둠", file=sys.stderr)
        return None
    finally:
        signal.alarm(0)


def tick():
    """What the Orca automation (or, during a switch, the old LaunchAgent) runs each interval. One tick at a time
    (<state>/sweep-tick.lock): a second one, from the other scheduler or a slow earlier tick, leaves at once with 0 and
    does nothing, so the patrol and the old cleaner are not started twice and no tick waits behind another."""
    with file_lock("sweep-tick.lock") as got:
        if not got:
            print("sweep: 다른 tick 이 도는 중이라 이번 tick 은 건너뜀")
            return 0
        return run_tick()


def run_patrol(timeout, cmd=None):
    """`patrol --if-due` in a session of its own, so that it can cut its scans by their group. What the round leaves in
    that group ends with it, on a timeout too: subprocess.run would stop only the direct child. Its exit code."""
    proc = subprocess.Popen(cmd or [sys.executable, str(reap.ORCH), "patrol", "--if-due"],
                            stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
        raise
    with contextlib.suppress(OSError):
        os.killpg(proc.pid, signal.SIGKILL)
    return proc.returncode


PATROL_IDLE_POLL_S = 2


def wait_patrol_idle(deadline):
    """True once no patrol round holds its run lock (<state>/patrol-run.lock, held until the candidate scans end),
    whoever started the round; False when `deadline` (a time.monotonic() value) passes first."""
    while True:
        with file_lock("patrol-run.lock") as free:
            if free:
                return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(PATROL_IDLE_POLL_S)


def run_tick():
    """In order, each step on its own so one failing leaves the next:
    patrol (due rounds only), the shadow, reap-hard for the rules the sweep does not own (it leaves an owned one by
    itself), then the sweep's real round. reap-hard goes first so that a rule just handed over, whose first good round
    the sweep has not written yet, is still its old cleaner's in that pass: the real round writes sweep-last.json
    after it. The worst exit code."""
    code = 0
    started = time.monotonic()
    try:
        rc = run_patrol(PATROL_WAIT_S)
        print(f"patrol 종료 코드 {rc}")
        code = max(code, 1 if rc else 0)
        # A round the patrol automation already runs makes the call above leave at once with 0; the shadow and the real
        # round refuse a round that is not complete, so wait for it inside the same budget instead of missing it.
        if not wait_patrol_idle(started + PATROL_WAIT_S):
            print("patrol 라운드가 예산 안에 끝나지 않음", file=sys.stderr)
            code = max(code, 1)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"patrol 을 돌리지 못함: {type(e).__name__}", file=sys.stderr)
        code = max(code, 1)

    def hard():
        r = subprocess.run([sys.executable, str(HERE.parents[2] / "scripts" / "reap_hard.py"), "run", "--no-patrol"],
                           stdin=subprocess.DEVNULL, timeout=STEP_TIMEOUT_S)
        return {"code": r.returncode}

    for name, fn in (("shadow", run_shadow), ("reap-hard", hard), ("real", run_round)):
        try:
            res = budgeted(fn)
        except Exception as e:  # one step's failure (a log that cannot be written, a hung child) leaves the others
            print(f"{name}: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
            code = max(code, 1)
            continue
        if res is None:
            code = max(code, 4)
            continue
        print(name, json.dumps({k: res.get(k) for k in ("round", "refused", "acts", "acted", "failed")}, ensure_ascii=False))
        code = max(code, res.get("code") or (1 if res.get("failed") else 0))
    return code


class Budget(BaseException):
    """The run's budget ran out. Not SystemExit or Exception, which the guards' own reads catch as "could not see"."""


def give_up(*_):
    raise Budget()


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "run":
        if "--shadow" in argv:
            res = budgeted(lambda: run_shadow(opt(argv, "--round"), "--pressure" in argv))
            if res is None:
                return 4
            print(json.dumps({k: res.get(k) for k in ("round", "refused", "acts", "skips")}, ensure_ascii=False))
            return 0
        dry = "--dry-run" in argv
        res = budgeted(lambda: run_round(opt(argv, "--round"), "--pressure" in argv, dry))
        if res is None:
            return 4
        if "--json" in argv:
            print(json.dumps(res, ensure_ascii=False, indent=1))
        elif dry:
            print_run(res, opt(argv, "--rule"))
        else:
            print(json.dumps({k: res.get(k) for k in ("round", "refused", "acted", "failed", "skipped", "kept")},
                             ensure_ascii=False))
        return 1 if res.get("failed") or (dry and res.get("refused")) else 0
    if cmd in ("own", "release"):
        code, msg = set_owner(argv[2] if len(argv) > 2 else "", cmd == "own")
        print(msg, file=sys.stderr if code else sys.stdout)
        return code
    if cmd == "config":
        print_config()
        return 0
    if cmd == "install":
        return cmd_install(argv)
    if cmd == "uninstall":
        return cmd_uninstall()
    if cmd == "tick":
        return tick()
    if cmd == "diff":
        d = diff(float(reap.opt("--hours") or 48))
        if "--json" in argv:
            print(json.dumps(d, ensure_ascii=False, indent=1))
        else:
            print_diff(d)
        return {"passed": 0, "excess": 1, "not yet": 3}[verdict(d)]
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
