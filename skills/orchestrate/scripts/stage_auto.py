"""The stage table's generated rows: each fleet tick derives them from what the programs, their Orca Runs and GitHub
already record, so the table fills without anyone running `orch stage add`.

Rows of a program (~/.claude/programs/<slug>/):
- a group row `<slug>`, the heading;
- one row `<slug>/<item>` per predicate item (program.json `predicate`);
- one row `<slug>/<ticket>` per worker card of the program's Run (`run`) whose ticket is not a predicate item, the
  ticket read from the card title's first word (`role-terms: …`, `ABC-12 …`); a card with none is `<slug>/<task id>`.
A row's PR is the one the ledger names for its ticket (`ready`, `landed`, … with `ticket`), else one the fleet
collector saw a worker session of that ticket own. With several, an open one is shown before a merged one.

Cells, each from evidence or left empty, never inferred:
- merge: ✅ with the 7-character merge sha, from a ledger `landed` event's sha (a fold onto another PR's branch,
  `onto`, is not a landing) or the PR's merge commit in the collector's list. An open or closed PR leaves it empty.
- dev, prod: empty. No source the tick reads records a release or a deploy; `orch stage set` adds that evidence.
A manual cell (`orch stage set` on the generated row's id) wins over the generated one: the orchestrator or lead writes a cell
only after checking something the tick cannot see, or to correct it, and the tick rewrites its rows every minute.

Which programs: one whose ledger had an event in the last ACTIVE_S. A program whose final check is current (a
`predicate_verified` after its last landing, as `orch status` counts Close) is finished: its rows carry `done_at`
and leave after the stage table's 7 days, like any finished row. `_`-prefixed directories are not programs.

Written to stages-auto.json beside stages.json, never into it: stages.json holds only what a person recorded.
"""
import datetime as dt, json, os, pathlib, re

import fleet, prog

ACTIVE_S = 7 * 86400
WARN_H = 6  # dashboard config "board": {"stage_warn_h": N}
LEAD_WORD = re.compile(r"\s*([\w.-]+):")


def path():
    return fleet.state_dir() / "stages-auto.json"


def read():
    store = fleet.read_json(path(), {}) or {}
    return {"rows": store.get("rows") or {}, "order": store.get("order") or [], "programs": store.get("programs") or {},
            "generated_at": store.get("generated_at")}


def read_lines(path):
    try:
        return path.read_text().splitlines()
    except OSError:
        return []


def ledger_row(line):
    """The event, or None for a torn last line while prog.py appends or a hand-edited row without a string ev."""
    try:
        e = json.loads(line)
    except ValueError:
        return None
    return e if isinstance(e, dict) and isinstance(e.get("ev"), str) else None


def programs_home():
    """Read per call, not prog.HOME: `dash.py demo` points PROGRAMS_HOME at its invented store after prog is imported."""
    return pathlib.Path(os.environ.get("PROGRAMS_HOME", "~/.claude/programs")).expanduser()


def load_programs(only=None):
    """[(program.json, ledger events)] for every program directory, or for the one named `only`."""
    out = []
    home = programs_home()
    for f in [home / only / "program.json"] if only else sorted(home.glob("*/program.json")):
        if f.parent.name.startswith("_"):
            continue
        try:
            cfg = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        events = [e for e in map(ledger_row, read_lines(f.parent / "ledger.jsonl")) if e]
        out.append(({**cfg, "slug": cfg.get("slug") or f.parent.name}, events))
    return out


def ticket_of(title):
    m = LEAD_WORD.match(title or "")
    if m:
        return m.group(1)
    word = ((title or "").split() or [""])[0]
    return word if prog.TICKET_ID.fullmatch(word) else None


def recent(cfg, events, now):
    """The program's last ledger event (its creation without one), or None when that is older than ACTIVE_S."""
    last = max(filter(None, (fleet.parse(e.get("ts")) for e in events)), default=fleet.parse(cfg.get("created_at")))
    return last if last and (now - last).total_seconds() <= ACTIVE_S else None


def active(cfg, events, now):
    """What build counts as active: moved in the last ACTIVE_S, not finished, not stopped."""
    return bool(recent(cfg, events, now)) and not prog.final_check_at(events) and not prog.stopped(events)


def build(programs, state, now):
    """(rows, order, programs summary) from [(program.json, events)] and the fleet state (sessions, tasks, prs)."""
    prs = {p.get("key"): p for p in state.get("prs") or []}
    rows, order, summary = {}, [], {}
    for cfg, events in programs:
        slug, repo, run = cfg["slug"], cfg.get("repo"), cfg.get("run")
        if not (last := recent(cfg, events, now)):
            continue
        done_at, created = prog.final_check_at(events), cfg.get("created_at") or fleet.iso(last)
        tickets = list(dict.fromkeys(cfg.get("predicate") or []))
        title = {}
        for t in state.get("tasks") or []:
            if run and t.get("run") == run:
                k = ticket_of(t.get("title")) or t["id"]
                if k not in tickets:
                    tickets.append(k)
                title.setdefault(k, t.get("title"))
        # ticket -> PR keys, in the order they were first named.
        named = {}
        for e in events:
            if e.get("pr") is not None and e.get("ticket") and repo:
                named.setdefault(e["ticket"], []).append(fleet.pr_key(repo, e["pr"]))
        for s in state.get("sessions") or []:
            if run and s.get("run") == run and (k := ticket_of(s.get("task_title"))):
                named.setdefault(k, []).extend(p["key"] for p in prs.values() if p.get("session") == s["id"])
        # A card's branch is named after its ticket (`<user>/role-terms`); its PR outlives the card's session.
        for p in sorted(prs.values(), key=lambda p: p.get("created") or ""):
            k = (p.get("branch") or "").rsplit("/", 1)[-1]
            if p.get("repo") == repo and k in tickets and (fleet.parse(p.get("created")) or last) >= fleet.parse(created):
                named.setdefault(k, []).append(p["key"])
        landed = {fleet.pr_key(repo, e["pr"]): e for e in events
                  if e.get("ev") == "landed" and e.get("pr") is not None and e.get("sha") and not e.get("onto") and repo}
        rows[slug] = {"id": slug, "title": slug, "group": True, "cells": {}, "source": "auto", "program": slug,
                      "created_at": created, "done_at": done_at}
        order.append(slug)
        for k in tickets:
            keys = list(dict.fromkeys(named.get(k) or []))
            pr = next((x for x in reversed(keys) if x not in landed and (prs.get(x) or {}).get("state") == "OPEN"), None) \
                or (keys[-1] if keys else None)
            cells = {}
            if merge := merge_cell(pr, landed, prs):
                cells["merge"] = merge
            rid = f"{slug}/{k}"
            nums = {int(x.rsplit("#", 1)[1]) for x in keys if x.startswith(f"{repo}#")}
            at = max((e["ts"] for e in events if e.get("ts") and (e.get("ticket") == k or e.get("pr") in nums)),
                     default=fleet.iso(last))
            rows[rid] = {"id": rid, "title": fleet.mask(" ".join((title.get(k) or k).split()), 120), "pr": pr,
                         "parent": slug, "cells": cells, "source": "auto", "program": slug, "created_at": created,
                         "at": at, "done_at": done_at}
            order.append(rid)
        summary[slug] = {"active": not done_at and not prog.stopped(events), "rows": len(tickets)}
    return rows, order, summary


def merge_cell(pr, landed, prs):
    if not pr:
        return None
    if e := landed.get(pr):
        return {"mark": "ok", "evidence": e["sha"][:7], "at": e.get("ts"), "auto": True, "from": "ledger landed"}
    p = prs.get(pr) or {}
    if p.get("state") == "MERGED" and p.get("merge_sha"):
        return {"mark": "ok", "evidence": p["merge_sha"][:7], "at": p.get("merged_at"), "auto": True, "from": "GitHub merge commit"}
    return None


def refresh(state, now=None):
    """Rebuild stages-auto.json from the programs on disk and this tick's state."""
    now = now or fleet.utcnow()
    rows, order, summary = build(load_programs(), state, now)
    fleet.write_atomic(path(), json.dumps({"generated_at": fleet.iso(now), "rows": rows, "order": order,
                                           "programs": summary}, ensure_ascii=False, indent=1) + "\n")
    return rows


def warn_hours():
    try:
        return float(((fleet.config().get("board") or {}).get("stage_warn_h")) or WARN_H)
    except (TypeError, ValueError):
        return WARN_H


def warning(slug=None, now=None):
    """One line when the stage table is not keeping up with an active program, else "": the program has no work row,
    or the tick has not rewritten its rows in stage_warn_h hours. slug: a lead's program; None: every active one.
    Reads files only, so a prompt hook can call it."""
    now, hours = now or fleet.utcnow(), warn_hours()
    auto = read()
    if slug and slug not in auto["programs"]:
        # Not in the last tick's summary: quiet for ACTIVE_S, stopped or finished (no warning), or newer than that tick.
        own = load_programs(slug)
        if not own or not active(*own[0], now):
            return ""
        progs = {slug: {"active": True, "rows": 0}}
    else:
        progs = {k: v for k, v in auto["programs"].items() if v.get("active") and (not slug or k == slug)}
    at = fleet.parse(auto["generated_at"])
    fresh = at and (now - at).total_seconds() <= hours * 3600
    stale = [] if fresh else sorted(progs)
    empty = sorted(k for k, p in progs.items() if fresh and not p.get("rows"))
    parts = ([f"no stage row for active program {', '.join(empty)}"] if empty else []) + (
        [f"stage rows of {', '.join(stale)} not refreshed in {hours:g} h (is orch-dash running? `orch-dash ensure`)"]
        if stale else [])
    return f"Stage table: {'; '.join(parts)}." if parts else ""
