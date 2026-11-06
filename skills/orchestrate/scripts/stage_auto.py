"""The stage table's generated rows: each fleet tick derives them from what the programs, their Orca Runs and GitHub
already record, so the table fills without anyone running `orch stage add`.

Rows of a program (~/.claude/programs/<slug>/):
- a group row `<slug>`, the heading;
- one row `<slug>/<item>` per predicate item (program.json `predicate`), `item` true;
- one row `<slug>/<ticket>` per worker card of the program's Run (`run`) whose ticket is not a predicate item, the
  ticket read from the card title's first word (`role-terms: …`, `ABC-12 …`); a card with none, or a second card of a
  ticket another card already holds, is `<slug>/<task id>` (row_key). A card qualified past its ticket (`ABC-1 K4: …`,
  ledger ticket ABC-1-K4) whose qualifier names exactly one predicate item (`P6-K4-ready`) is that item's row.
A card keeps the row it got first: each tick stores which card holds which row (programs.<slug>.cards), so a card
that leaves the Run (archived) keeps its row, and a later card of its ticket never takes that row's id, ack or PR.
A row's PR is the one the ledger names for its ticket (`ready`, `landed`, … with `ticket`), else one the fleet
collector saw the worker session of that card own (matched by task id, else by a task title no other card shares), else one whose branch is named after
the ticket, else (a predicate item only) one whose title leads with it (`ABC-1: …`). With several, an
open one is shown before a merged one. Cards and sessions are archived once the work lands, so a landed row's
evidence is the ledger and GitHub: the collector keeps every PR a program's ledger names (fleet.program_prs), with
its branch, after the search stops finding it.

Cells, each from evidence or left empty, never inferred:
- merge: ✅ with the 7-character merge sha, from a ledger `landed` event's sha (a fold onto another PR's branch,
  `onto`, is not a landing) or the PR's merge commit in the collector's list. An open or closed PR leaves it empty.
- dev, prod: `na` (해당 없음) unless program.json `stages` names that column (`orch set <slug> stages dev,prod`); a named
  column stays empty, since no source the tick reads records a deploy, and `orch stage set` adds that evidence.
A manual dev or prod cell (`orch stage set` on the generated row's id) wins over the generated one: the orchestrator or lead
writes a cell only after checking something the tick cannot see, or to correct it, and the tick rewrites its rows every
minute. The merge cell is never manual here: `orch stage set --col merge` is refused, and a recorded one is ignored.

Each row ends on its own (`done_at`):
- every cell ✅ or 해당 없음: stages.table() stamps the latest cell's time, after the manual cells are laid over;
- stopped (`stopped`, shown as 중단) when nothing merged and it has no PR or a closed one: the ticket's last ledger word is
  `released` (by ticket, or by the dispatch id a `spawned` row noted) or `parked`, not a later `spawned`, `admitted` or
  `ready`; or, for a card row that is not a predicate item, its card ended (Orca status completed or failed, or gone
  from a Run read that succeeded), stamped once at the first tick that saw it and kept in the summary's cards;
- the program's Close (a `predicate_verified` after its last landing, as `orch status` counts it) ends the rest.
A finished row leaves after the stage table's 7 days, like any finished row.

Which programs: one whose ledger had an event in the last ACTIVE_S. `_`-prefixed directories are not programs.

Written to stages-auto.json beside stages.json, never into it: stages.json holds only what a person recorded. The file
is rewritten only when its rows, order or summary change; the collector's memory.json `stage_auto_at` says when a
tick last built them.
"""
import collections, datetime as dt, json, os, pathlib, re

import fleet, prog

ACTIVE_S = 7 * 86400
WARN_H = 6  # dashboard config "board": {"stage_warn_h": N}
LEAD_WORD = re.compile(r"\s*([\w.-]+):")
DEPLOY = ("dev", "prod")
STOP, GO = ("released", "parked"), ("spawned", "admitted", "ready")
ENDED = ("completed", "failed")


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


def qualified(title, ledger_tickets):
    """The card's ticket, or `<ticket>-<word>` when the ledger names that ticket after the title's second word
    (`ABC-1 K4: …` spawned as ABC-1-K4)."""
    k = ticket_of(title)
    words = (title or "").split()
    if k and len(words) > 1 and f"{k}-{words[1].rstrip(':')}" in ledger_tickets:
        return f"{k}-{words[1].rstrip(':')}"
    return k


def row_key(task, ledger_tickets, taken, items=()):
    """A card's row key: qualified(), or the one predicate item whose words hold the qualifier (ABC-1-K4 ->
    P6-K4-ready; none or several: the qualified ticket). A card whose key another card of the Run already holds keys
    by its task id: two cards of one ticket are two rows, each with its own PR."""
    k = qualified(task.get("title"), ledger_tickets)
    if k and k != ticket_of(task.get("title")):
        word = k.rsplit("-", 1)[1].lower()
        hits = [i for i in items if word in i.lower().split("-")]
        k = hits[0] if len(hits) == 1 else k
    return k if k and k not in taken else task["id"]


def recent(cfg, events, now):
    """The program's last ledger event (its creation without one), or None when that is older than ACTIVE_S."""
    last = max(filter(None, (fleet.parse(e.get("ts")) for e in events)), default=fleet.parse(cfg.get("created_at")))
    return last if last and (now - last).total_seconds() <= ACTIVE_S else None


def active(cfg, events, now):
    """What build counts as active: moved in the last ACTIVE_S, not finished, not stopped."""
    return bool(recent(cfg, events, now)) and not prog.final_check_at(events) and not prog.stopped(events)


def build(programs, state, now, held=None):
    """(rows, order, programs summary) from [(program.json, events)] and the fleet state (sessions, tasks, prs).
    held: the last tick's programs summary, whose `cards` (task id -> its row key and title) this tick keeps."""
    prs = {p.get("key"): p for p in state.get("prs") or []}
    rows, order, summary = {}, [], {}
    for cfg, events in programs:
        slug, repo, run = cfg["slug"], cfg.get("repo"), cfg.get("run")
        if not (last := recent(cfg, events, now)):
            continue
        done_at, created = prog.final_check_at(events), cfg.get("created_at") or fleet.iso(last)
        tickets = list(dict.fromkeys(cfg.get("predicate") or []))
        items = set(tickets)
        ledger_tickets = {e["ticket"] for e in events if isinstance(e.get("ticket"), str)}
        kept = ((held or {}).get(slug) or {}).get("cards") or {}
        cards = {tid: {"id": tid, "title": c.get("title")} for tid, c in kept.items()}
        cards.update({t["id"]: t for t in state.get("tasks") or [] if run and t.get("run") == run})
        card_key = {tid: c["key"] for tid, c in kept.items() if c.get("key")}
        title, alias = {}, {}
        for t in cards.values():
            if t["id"] not in card_key:
                card_key[t["id"]] = row_key(t, ledger_tickets, set(card_key.values()), items)
            k = card_key[t["id"]]
            if k not in tickets:
                tickets.append(k)
            title.setdefault(k, t.get("title"))
            if k in items and (q := qualified(t.get("title"), ledger_tickets)) != k:
                alias[q] = k
        # A ledger ticket that names a card folded into an item counts as that item from here on.
        events = [{**e, "ticket": alias[e["ticket"]]} if isinstance(e.get("ticket"), str) and e["ticket"] in alias else e
                  for e in events]
        # A session finds its card by task id; by title only when no other card shares it, else neither row takes its PR.
        twice = {x for x, n in collections.Counter(t.get("title") for t in cards.values()).items() if n > 1}
        by_title = {t.get("title"): card_key[t["id"]] for t in cards.values() if t.get("title") not in twice}
        # ticket -> PR keys, in the order they were first named.
        named = {}
        for e in events:
            if e.get("pr") is not None and e.get("ticket") and repo:
                named.setdefault(e["ticket"], []).append(fleet.pr_key(repo, e["pr"]))
        for s in state.get("sessions") or []:
            if run and s.get("run") == run and (k := card_key.get(s.get("task")) or s.get("task_title") not in twice and (
                    by_title.get(s.get("task_title")) or ticket_of(s.get("task_title")))):
                named.setdefault(k, []).extend(p["key"] for p in prs.values() if p.get("session") == s["id"])
        # A card's branch is named after its ticket (`<user>/role-terms`); its PR outlives the card's session.
        claimed = {x for keys in named.values() for x in keys}
        for p in sorted(prs.values(), key=lambda p: p.get("created") or ""):
            if p.get("repo") != repo or (fleet.parse(p.get("created")) or last) < fleet.parse(created):
                continue
            k = (p.get("branch") or "").rsplit("/", 1)[-1]
            k = alias.get(k, k)
            if k in tickets:
                named.setdefault(k, []).append(p["key"])
            else:
                # The ledger may never tie a predicate item to its PR (`landed` names the PR, `config` the tickets):
                # a title that leads with an item does, unless the ledger or a session already gave the PR to a row.
                lead = ticket_of(p.get("title"))
                if lead in items and p["key"] not in claimed:
                    named.setdefault(lead, []).append(p["key"])
        landed = {fleet.pr_key(repo, e["pr"]): e for e in events
                  if e.get("ev") == "landed" and e.get("pr") is not None and e.get("sha") and not e.get("onto") and repo}
        rows[slug] = {"id": slug, "title": slug, "group": True, "cells": {}, "source": "auto", "program": slug,
                      "created_at": created, "done_at": done_at}
        order.append(slug)
        deploy = cfg.get("stages") or []
        deploy = deploy.split(",") if isinstance(deploy, str) else deploy
        words = last_words(events)
        live = {t["id"]: t for t in state.get("tasks") or [] if run and t.get("run") == run}
        runs_ok = ((state.get("sources") or {}).get("runs") or {}).get("ok")
        card_of = {k: tid for tid, k in card_key.items()}
        ended = {}
        for k in tickets:
            keys = list(dict.fromkeys(named.get(k) or []))
            pr = next((x for x in reversed(keys) if x not in landed and (prs.get(x) or {}).get("state") == "OPEN"), None) \
                or (keys[-1] if keys else None)
            cells = {}
            if merge := merge_cell(pr, landed, prs):
                cells["merge"] = merge
            for c in DEPLOY:
                if c not in deploy:
                    cells[c] = {"mark": "na", "auto": True, "from": "program.json stages"}
            stopped = end_at = None
            tid = card_of.get(k)
            if tid and k not in items:
                had = kept.get(tid, {}).get("ended")
                # The card's end is kept whatever its PR does: only the card read working again lifts it, and a
                # failed Run read that cannot see the card keeps it.
                if (live[tid].get("status") in ENDED) if tid in live else bool(runs_ok or had):
                    ended[tid] = had or fleet.iso(now)
            # Only no PR or a closed one: a PR whose state this tick does not know may be open.
            if not merge and (not pr or (prs.get(pr) or {}).get("state") == "CLOSED"):
                w = words.get(k)
                if w and w["ev"] in STOP:
                    stopped, end_at = w["ev"], w["ts"]
                elif tid in ended:
                    stopped, end_at = "card ended", ended[tid]
            rid = f"{slug}/{k}"
            nums = {int(x.rsplit("#", 1)[1]) for x in keys if x.startswith(f"{repo}#")}
            at = max((e["ts"] for e in events if e.get("ts") and (e.get("ticket") == k or e.get("pr") in nums)),
                     default=fleet.iso(last))
            # title stays cut for the text brief; the dashboard shows title_full.
            full = " ".join((title.get(k) or k).split())
            rows[rid] = {"id": rid, "title": fleet.mask(full, 120), "title_full": fleet.mask(full), "pr": pr,
                         "parent": slug, "cells": cells, "source": "auto", "program": slug, "item": k in items, "created_at": created,
                         "at": at, "done_at": end_at or done_at, **({"stopped": stopped} if stopped else {})}
            order.append(rid)
        summary[slug] = {"active": not done_at and not prog.stopped(events), "rows": len(tickets),
                         "cards": {tid: {"key": card_key[tid], "title": t.get("title"),
                                         **({"ended": ended[tid]} if tid in ended else {})} for tid, t in cards.items()}}
    return rows, order, summary


def last_words(events):
    """ticket -> its last stop (STOP) or go (GO) ledger event. A `released` row without a ticket names its dispatch id
    in its note, as the `spawned` row of that ticket did."""
    by_note = {e["note"]: e["ticket"] for e in events
               if e["ev"] == "spawned" and isinstance(e.get("ticket"), str) and isinstance(e.get("note"), str)}
    out = {}
    for e in events:
        if e["ev"] in STOP + GO and e.get("ts"):
            k = e.get("ticket") if isinstance(e.get("ticket"), str) else by_note.get(e.get("note")) if e["ev"] == "released" else None
            if k:
                out[k] = e
    return out


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
    """Rebuild stages-auto.json from the programs on disk and this tick's state, writing it only when that changed."""
    now = now or fleet.utcnow()
    old = read()
    rows, order, summary = build(load_programs(), state, now, old["programs"])
    body = {"rows": rows, "order": order, "programs": summary}
    if not old["generated_at"] or {k: old[k] for k in body} != body:
        fleet.write_atomic(path(), json.dumps({"generated_at": fleet.iso(now), **body}, ensure_ascii=False, indent=1) + "\n")
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
    # The file is rewritten only on a change, so the collector's last build time counts too.
    built = (fleet.read_json(fleet.state_dir() / "memory.json", {}) or {}).get("stage_auto_at")
    at = max(filter(None, (fleet.parse(auto["generated_at"]), fleet.parse(built))), default=None)
    fresh = at and (now - at).total_seconds() <= hours * 3600
    stale = [] if fresh else sorted(progs)
    empty = sorted(k for k, p in progs.items() if fresh and not p.get("rows"))
    parts = ([f"no stage row for active program {', '.join(empty)}"] if empty else []) + (
        [f"stage rows of {', '.join(stale)} not refreshed in {hours:g} h (is orch-dash running? `orch-dash ensure`)"]
        if stale else [])
    return f"Stage table: {'; '.join(parts)}." if parts else ""
