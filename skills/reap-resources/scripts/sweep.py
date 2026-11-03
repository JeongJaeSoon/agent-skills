#!/usr/bin/env python3
"""The v2 cleaner, shadow stage only: reads one patrol round's `candidates` and records what it would do, closing and
deleting nothing.

  sweep.py run --shadow [--round RID] [--pressure]   decide on the newest complete round (or RID); one line in
                                                    <state>/sweep-shadow.jsonl
  sweep.py diff [--hours 48] [--json]               the shadow's "would act" against what the old cleaners did
                                                    (reap-hard.jsonl, reap-actions.jsonl) in the same hours

There is no real mode yet: `run` without --shadow refuses. reap-hard starts `run --shadow` detached after each pass
that is not a dry run. One run at a time (<state>/sweep.lock); a run gives up after RUN_BUDGET_S.

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
import contextlib, datetime as dt, fcntl, json, math, os, pathlib, signal, sys, time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import guard, reap  # noqa: E402

FRESH_S = 3600
KEEP_LINES = 500  # five days of 15-minute rounds
REPEAT_FAILS, REST_S = 2, 86400
DEFAULT_CAP = 50
RUN_BUDGET_S, CMD_TIMEOUT_S = 600, 60
HARD_RULES = set(guard.HARD_RULE.values())
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


def lags():
    interval = 900
    with contextlib.suppress(OSError, ValueError, AttributeError, TypeError):
        n = (json.loads(CONFIG.read_text()).get("reap-hard") or {}).get("interval", 900)  # reap-hard's own file
        interval = float(n) if not isinstance(n, bool) and float(n) > 0 else 900
    return {"hard": 2 * interval + 600, "reap": JANITOR_LAG_S}


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
def run_lock():
    """<state>/sweep.lock without waiting: True while held, False when another run has it."""
    reap.STATE.mkdir(parents=True, exist_ok=True)
    with open(reap.STATE / "sweep.lock", "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True


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
    for r in read_jsonl(reap.STATE / "reap-actions.jsonl"):
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
    for k in MATCH_IDENTITY:
        if k not in plan:
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
    keys, rank = {}, {"matched": 0, "pending": 1, "excess": 2}
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
            k = keys.setdefault(row["key"], {"key": row["key"], "rule": row.get("rule"), "state": state,
                                             "rounds": 0, "first": rt})
            k["rounds"] += 1
            if rank[state] > rank[k["state"]]:
                k["state"] = state
    rows = sorted(keys.values(), key=lambda k: k["key"])
    by_rule = {}
    for k in rows:
        by_rule.setdefault(k["rule"], {"matched": 0, "pending": 0, "excess": 0})[k["state"]] += 1
    decided = sorted(r["round_t"] for r in rounds if not r.get("refused"))
    edges = [since] + decided + [now]
    return {"hours": hours, "rounds": len(rounds), "refused": sum(1 for r in rounds if r.get("refused")),
            "decided": len(decided), "max_gap_s": max(b - a for a, b in zip(edges, edges[1:])),
            "by_rule": by_rule, "excess": [k for k in rows if k["state"] == "excess"], "rows": rows}


def verdict(d):
    """passed, excess or not yet: a window with a stretch over MAX_GAP_S and no decided round in it (none at all, or
    the shadow down for a while) proves nothing either way."""
    if d["excess"]:
        return "excess"
    return "passed" if d["decided"] and d["max_gap_s"] <= MAX_GAP_S else "not yet"


def print_diff(d):
    print(f"sweep 그림자 비교: 최근 {d['hours']:g}시간, 회차 {d['rounds']}개(거부 {d['refused']}, 판정한 회차 사이 "
          f"가장 긴 공백 {d['max_gap_s'] / 3600:.1f}시간)")
    if not d["by_rule"]:
        print("- 처리하겠다고 한 대상 없음")
    for rule_name, c in sorted(d["by_rule"].items()):
        print(f"- {rule_name}: 일치 {c['matched']} · 대기 {c['pending']} · 넘침 {c['excess']}")
    for r in d["excess"]:
        print(f"  넘침 {r['key']} ({r['rule']})")
    v = verdict(d)
    pending = sum(c["pending"] for c in d["by_rule"].values())
    # Pending plans are inside their lag: the window ending a lag ago is the one they prove.
    print("판정: " + (f"통과(넘침 0, 지연 안이라 아직 대기 {pending}건)" if v == "passed" else f"넘침 {len(d['excess'])}건" if v == "excess" else
                    f"아직 판정 못 함(판정한 회차가 {MAX_GAP_S // 3600}시간 넘게 빈 구간이 있음)"))


class Budget(BaseException):
    """The run's budget ran out. Not SystemExit or Exception, which the guards' own reads catch as "could not see"."""


def give_up(*_):
    raise Budget()


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "run":
        if "--shadow" not in argv:
            print("sweep 실제 모드는 아직 없다(그림자 단계): run --shadow 만 쓴다", file=sys.stderr)
            return 2
        reap.RUN_TIMEOUT = CMD_TIMEOUT_S
        signal.signal(signal.SIGALRM, give_up)
        signal.alarm(RUN_BUDGET_S)
        try:
            res = run_shadow(reap.opt("--round"), "--pressure" in argv)
        except Budget:
            print(f"sweep: {RUN_BUDGET_S}초 안에 끝나지 않아 그만둠", file=sys.stderr)
            return 4
        finally:
            signal.alarm(0)
        print(json.dumps({k: res.get(k) for k in ("round", "refused", "acts", "skips")}, ensure_ascii=False))
        return 0
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
