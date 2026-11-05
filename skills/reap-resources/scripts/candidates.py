#!/usr/bin/env python3
"""Cleanup candidates for patrol's v2 verdict fields, from the judging today's cleaners already do.

Nothing here judges anew: the janitor and pressure kinds come from reap.scan (cards.judge and reap.judge_* behind it)
and, for a kind only the pressure round reaps, its live-card filter (pressure.keep_live); the strict card, stack,
listener and serve rules come from reap_hard.scan_cards and scan_rest. Each target becomes one candidate named by the
rule that chose it, so two cleaners covering one resource stay two rules. A candidate only the pressure round would
reap carries "when": "pressure", and every candidate "seen": when the scan it came from started. Nothing reads these
fields yet and nothing here acts; `collect` only reads.

A candidate keeps the sources it relied on in `needs`; one whose source did not read `ok` this round goes to
`reports` instead. A scan that returned but noted an unreadable part fails that part's source. Kinds that only a
model closed today (chrome-window) and kinds with no rule go to `reports` too.
"""
import copy, json, pathlib, sys, threading, time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts"))
import reap  # noqa: E402

# reap item kind -> rule. A card's rule follows its cards.py verdict.
RULE_OF = {"compose-stack": "stack:registered", "stack-volume": "volume:prefixed", "tmpdir": "trash:tmpdir",
           "evidence": "trash:evidence", "gui-app": "gui:registered", "orca-worker": "worker:reclaimable",
           "program": "program:archive", "state-file": "state:rotate", "user-folder": "user-folder:trash",
           "orphan": "proc:orphan"}
CARD_RULE = {"삭제": "card:landed", "터미널만 닫기": "card:close-idle"}
HARD_RULE = {"card": "card:strict", "stack": "stack:gone", "listener": "proc:listener", "serve": "serve:dark"}
REPORT_ONLY = ("chrome-window",)  # the janitor's model closed these after a URL check; v2 only reports them
# What a card candidate relies on besides its scan: patrol's own activity reads of this round.
CARD_NEEDS = ("terminals", "waits", "workers")
IDENTITY = tuple(dict.fromkeys(reap.ACT_IDENTITY + ("verdict", "close")))
# How today's scans word a part they could not read.
UNREAD = ("읽지 못함", "실패", "응답 없음", "not read", "timed out")
RUN_TIMEOUT_S = 60
HARD_SOURCES = ("protect", "reap_hard_cards", "reap_hard_rest")


def scan_kinds(cfg, pressure_kinds, state_dir, now):
    """The kinds the scheduled janitor scans (its daily ones only when due) and the pressure round's safe kinds."""
    kinds = set(reap.SCHEDULED) | set(pressure_kinds)
    return {k for k in kinds if k not in reap.DAILY or reap.user_folder_due(state_dir, now, kind=k)}


def unread_kinds(notes, kinds):
    """The kinds a scan's notes say it could not read; "*" for a note naming no kind."""
    out = set()
    for n in notes:
        if not any(m in n for m in UNREAD):
            continue
        head = n.split(":", 1)[0].strip()
        out.add(head if head in kinds else "card" if "카드" in n else "*")
    return out


def identity(item):
    return {k: item[k] for k in IDENTITY if item.get(k) not in (None, "", [])}


def entry(rule, kind, key, item, needs, why, **extra):
    return {"rule": rule, "kind": kind, "key": key, "identity": identity(item), "needs": sorted(needs), "why": why,
            "item": item, **extra}


def from_reap(items, pressure_only=frozenset()):
    """(candidates, reports, alerts) from reap.scan items: every target, and the cards a person decides."""
    cands, reports, alerts = [], [], []
    for i in items:
        if i["kind"] == "card" and i.get("verdict") == "사람 결정":
            alerts.append({"kind": "card", "key": i["key"], "path": i["name"], "decide": i["why"]})
            continue
        if not i.get("target"):
            if "보고만" in (i.get("why") or ""):  # today's scans already show these as report-only
                reports.append({"kind": i["kind"], "key": i["key"], "why": i["why"]})
            continue
        if i["kind"] in REPORT_ONLY:
            reports.append({"kind": i["kind"], "key": i["key"], "why": f"{i['why']}(보고만)"})
            continue
        rule = CARD_RULE.get(i.get("verdict")) if i["kind"] == "card" else RULE_OF.get(i["kind"])
        if not rule:
            reports.append({"kind": i["kind"], "key": i["key"], "why": f"규칙 없음: {i['why']}(보고만)"})
            continue
        needs = {"reap_scan", f"reap_scan:{i['kind']}", *(CARD_NEEDS if i["kind"] == "card" else ())}
        extra = {}
        if i["kind"] in pressure_only:
            needs.add("pressure_live")
            extra["when"] = "pressure"
        cands.append(entry(rule, i["kind"], i["key"], i, needs, i["why"], **extra))
    return cands, reports, alerts


def from_hard(items, source):
    """(candidates, reports) from reap_hard's scans."""
    cands, reports = [], []
    for i in items:
        if not i.get("target"):
            continue
        key = f"card:{i['id']}" if i["kind"] == "card" else f"{i['kind']}:{i['id']}"
        rule = HARD_RULE.get(i["kind"])
        if not rule:
            reports.append({"kind": i["kind"], "key": key, "why": f"규칙 없음: {i['why']}(보고만)"})
            continue
        # A listener is judged from the machine read the card scan took.
        needs = {source, "protect", *(("reap_hard_cards",) if i["kind"] == "listener" else ()),
                 *(CARD_NEEDS if i["kind"] == "card" else ())}
        cands.append(entry(rule, i["kind"], key, i, needs, i["why"]))
    return cands, reports


class Timings(dict):
    """{name: seconds}, as JSON. .started: {name: the wall time that scan first started}, which its candidates carry
    as "seen"; per collect() call, never shared between calls."""

    def __init__(self):
        super().__init__()
        self.started = {}


def timed(timings, name, fn):
    if isinstance(timings, Timings):
        timings.started.setdefault(name, time.time())
    t0 = time.monotonic()
    try:
        return fn()
    finally:
        timings[name] = round(timings.get(name, 0) + time.monotonic() - t0, 2)


def collect(sources, now=None, reap_scan=None, hard=None, pressure_kinds=None, state_dir=None, live=None,
            progress=None):
    """{sources, timings, candidates, reports, alerts, notes}. `sources` holds patrol's own reads of this round
    ({name: "ok"|"failed"|"skipped"}); this adds reap_scan (and reap_scan:<kind>), pressure_live, protect,
    reap_hard_cards and reap_hard_rest. `progress` gets sources and timings as they come in, so a round cut short
    still shows where its time went. reap_scan(kinds), hard(timings) and live(targets, notes) stand in for the
    machine in tests."""
    now = now or time.time()
    state_dir = state_dir or reap.STATE
    out = progress if progress is not None else {}
    out.update(sources=dict(sources), timings=Timings(), candidates=[], reports=[], alerts=[], notes=[])
    src, timings, notes = out["sources"], out["timings"], out["notes"]
    cfg = reap.config()
    if pressure_kinds is None:
        import pressure
        pressure_kinds = pressure.safe_kinds(pressure.config())
    kinds = scan_kinds(cfg, pressure_kinds, state_dir, now)
    pressure_only = set(pressure_kinds) - set(reap.SCHEDULED)
    hours = float(cfg.get("hours") or 6)

    def default_scan(kinds):
        repo_list = reap.repos([]) if {"branch", "worktree", "card"} & kinds else []
        return reap.scan(kinds, hours, cfg, repo_list)

    def reap_part(p):
        t = p["timings"]
        try:
            p["seen"] = time.time()
            items, more = timed(t, "reap_scan", lambda: (reap_scan or default_scan)(kinds))
            p["notes"] += more
            bad = unread_kinds(more, kinds)
            p["sources"]["reap_scan"] = "failed" if "*" in bad else "ok"
            p["sources"].update({f"reap_scan:{k}": "failed" if k in bad else "ok" for k in sorted(kinds)})
            # The pressure round reaps its own kinds only outside a card with a live terminal, on a copy of the scan.
            pressure_items = [copy.deepcopy(i) for i in items if i["kind"] in pressure_only and i.get("target")]
            if pressure_items:
                got = timed(t, "pressure_live", lambda: (live or default_live)(pressure_items, p["notes"]))
                p["sources"]["pressure_live"] = "failed" if got is None else "ok"
                by_key = {i["key"]: i for i in pressure_items}
                items = [by_key.get(i["key"], i) if i["kind"] in pressure_only else i for i in items]
            c, r, a = from_reap(items, pressure_only)
            p["cands"] += [{**x, "seen": p["seen"]} for x in c]
            p["reports"] += r
            p["alerts"] += a
        except (Exception, SystemExit) as e:
            p["sources"]["reap_scan"] = "failed"
            p["notes"].append(f"reap scan: {type(e).__name__}: {str(e)[:120]}")

    def hard_part(p):
        t, hard_t = p["timings"], time.time()
        try:
            got = (hard or default_hard)(t)
        except (Exception, SystemExit) as e:
            got = {"notes": [f"reap-hard scan: {type(e).__name__}: {str(e)[:120]}"]}
        for s in HARD_SOURCES:
            p["sources"][s] = got.get(s, "failed")
        p["notes"] += got.get("notes") or []
        for source in ("reap_hard_cards", "reap_hard_rest"):
            st = t.started.get(source, hard_t)  # a stand-in scan that does not time itself: before any of it ran
            c, r = from_hard(got.get(source + "_items") or [], source)
            p["cands"] += [{**x, "seen": st} for x in c]
            p["reports"] += r

    # The two scans wait on gh, git, lsof and orca, so they run side by side. Each part writes only its own dict, and
    # a part is merged only once its thread has finished: a round cut short (patrol's SIGALRM, which only this thread
    # receives) merges the finished part, marks the other's sources timed out, and leaves that daemon thread to die
    # with the process.
    parts = [dict(fn=fn, owns=owns, sources={}, timings=Timings(), notes=[], cands=[], reports=[], alerts=[])
             for fn, owns in ((reap_part, ("reap_scan",)), (hard_part, HARD_SOURCES))]
    threads = [threading.Thread(target=p["fn"], args=(p,), daemon=True) for p in parts]
    for th in threads:
        th.start()
    cands, merged = [], set()

    def merge(final):
        for n, (p, th) in enumerate(zip(parts, threads)):
            if n in merged:
                continue
            if th.is_alive():
                if final:
                    src.update({s: "timeout" for s in p["owns"]})
                    timings.update(dict(p["timings"]))  # where its time went so far
                continue
            merged.add(n)
            src.update(p["sources"])
            timings.update(p["timings"])
            timings.started.update(p["timings"].started)
            notes.extend(p["notes"])
            cands.extend(p["cands"])
            out["reports"] += p["reports"]
            out["alerts"] += p["alerts"]

    try:
        for th in threads:
            while th.is_alive():
                th.join(0.2)
                merge(False)
    finally:
        merge(True)
    for c in cands:
        missing = [n for n in c["needs"] if src.get(n) != "ok"]
        if missing:
            out["reports"].append({"kind": c["kind"], "key": c["key"], "rule": c["rule"],
                                   "why": f"이번 회차에 읽지 못한 소스 {', '.join(missing)}: {c['why']}(보고만)"})
        else:
            out["candidates"].append(c)
    return out


def default_live(targets, notes):
    import pressure
    return pressure.keep_live(targets, notes)


def default_hard(timings):
    """reap-hard's scans as its --dry-run would run them, with no card counted as closed in this pass. A scan that
    noted an unreadable part fails its source."""
    import reap_hard
    cfg = reap_hard.config()
    out = {"notes": []}
    try:
        protect = timed(timings, "protect", lambda: reap_hard.read_protect(cfg["protect_file"]))
    except reap_hard.NoProtect as e:
        out.update(protect="failed", notes=[f"reap-hard: {e}"])
        return out
    out["protect"] = "ok"
    now = time.time()
    try:
        items, notes, m = timed(timings, "reap_hard_cards", lambda: reap_hard.scan_cards(cfg, protect, now))
        out.update(reap_hard_cards="failed" if notes else "ok", reap_hard_cards_items=items)
        out["notes"] += notes
    except (Exception, SystemExit) as e:
        out.update(reap_hard_cards="failed", reap_hard_rest="failed")
        out["notes"].append(f"reap-hard cards: {type(e).__name__}: {str(e)[:120]}")
        return out
    last = reap_hard.read_last() or {}
    try:
        rest, notes, _ = timed(timings, "reap_hard_rest",
                               lambda: reap_hard.scan_rest(cfg, protect, set(last.get("dark") or ()), [], m, now))
        out.update(reap_hard_rest="failed" if notes else "ok", reap_hard_rest_items=rest)
        out["notes"] += notes
    except (Exception, SystemExit) as e:
        out["reap_hard_rest"] = "failed"
        out["notes"].append(f"reap-hard rest: {type(e).__name__}: {str(e)[:120]}")
    return out


if __name__ == "__main__":
    reap.RUN_TIMEOUT = RUN_TIMEOUT_S
    print(json.dumps(collect({n: "ok" for n in CARD_NEEDS}), ensure_ascii=False, indent=1, default=str))
