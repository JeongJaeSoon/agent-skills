"""The automatic close of owner orders (SSOT 5c), shadow first.

An open order is a candidate when the session that received it said every unit it needs is linked (`orch order
links-complete`), it is not the owner's to act on, and every real unit it names has ended on its source of truth: a PR
GitHub shows merged, a card whose session is idle with its PR merged and no step after the merge, a subagent whose last
run stopped, a board item that is done, a decision that was answered. A `--stand-in` link is not a unit. What is not
known is not ended: an archived or deleted card, an unreadable source, a PR closed without a merge, a dropped board
item, a withdrawn decision, a stale session list all leave the order open.

tidy runs tick() after its own closes. With `orchestrate.order_auto_close` unset (the default) a candidate only gets a
`would_close` event; `orch order shadow` sets those against what the sessions closed by hand. With the key true the same
judgement closes the order, in a transaction that reads the links and the receiving session's word again. The evidence
is normalized to the fields that identify the end (PR key and merge commit, card id and its PR's merge commit, agent
id and run, board id and its close event, decision id), so a timestamp moving cannot close the order a second time; an
order a Reopen undid is closed again only when that evidence changed.
"""
import json, os, pathlib, subprocess, sys

import fleet, rowstate, store, tidy

STATE_MAX_MIN = 15  # a session list older than this says nothing about who is working


def enabled():
    """`orchestrate.order_auto_close` of agent-skills.json is true. Anything else, an unreadable file included, is off."""
    path = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG") or "~/.claude/agent-skills.json").expanduser()
    try:
        return (json.loads(path.read_text()).get("orchestrate") or {}).get("order_auto_close") is True
    except (OSError, ValueError, AttributeError):
        return False


def gh_merged(key):
    """The merge commit of `owner/repo#N` when GitHub says it is merged, else None (also when gh cannot say)."""
    import order  # imports this module's neighbours; not needed until a PR has to be asked
    return order.merged(key)


def load(now=None):
    """(state, prs_file) as the collector wrote them; None / {} when missing."""
    sd = fleet.state_dir()
    return fleet.read_json(sd / "state.json", None), fleet.read_json(sd / "prs.json", {}) or {}


def gather(db, state, prs_file, board_now, now, merged_fn=gh_merged):
    """The facts unit_end() reads, taken once per pass."""
    prs = {k: v["detail"] for k, v in (prs_file.get("prs") or {}).items() if v.get("detail")}
    cards = tidy.cards_of(state, prs)
    prs = {**tidy.orca_merged(cards, {k for c in cards.values() for k in (c["pr"], c["newest_pr"])}), **prs}
    at = fleet.parse((state or {}).get("generated_at"))
    orca = ((state or {}).get("sources") or {}).get("orca") or {}
    fresh = bool(at) and (now - at).total_seconds() <= STATE_MAX_MIN * 60 and not orca.get("error")
    # fleet.inbox_lines and fleet.subagents turn an unreadable file into an empty or an older answer; an order is
    # closed on neither, so each file is opened here first and a failure is "not read".
    try:
        read_bytes(fleet.state_dir() / "inbox-external.jsonl")
        owner_items = [i for i in fleet.external_items(now) if i["type"] in tidy.OWNER_TYPES and i.get("session")]
    except (OSError, ValueError):
        owner_items = None  # not read: no order is called free of the owner's turn
    try:
        agents = ledger(read_bytes(fleet.state_dir() / fleet.SUBAGENTS).decode(errors="replace").splitlines())
    except OSError:
        agents = None
    if owner_items is not None:  # the collector's own items, human-gate waits among them
        owner_items += [i for i in (state or {}).get("items") or [] if i.get("type") in tidy.OWNER_TYPES and i.get("session")]
    board_now = board_now or {}
    return {"db": db, "prs": prs, "cards": cards, "fresh": fresh, "owner_items": owner_items, "merged_fn": merged_fn,
            "rows": board_now.get("rows") or {}, "agents": agents, "me": prs_file.get("me"),
            "board": {lane: board_now.get(lane) or [] for lane in ("doing", "todo", "owner_todo")}, "board_now": board_now,
            "decisions": store.decisions("open", db=db)}


def ledger(lines):
    """(runs, unsure): the subagent ledger folded as fleet.subagents does, and the agent ids whose latest run cannot be
    told: a line the reader skips (cut off by a crash, not a v1 row) that comes after the agent's last good line may be
    its restart, so the older stop in the ledger is not evidence."""
    last, bad = {}, []
    for i, line in enumerate(lines):
        try:
            r = json.loads(line)
        except ValueError:
            r = None
        if (isinstance(r, dict) and r.get("v") == 1 and r.get("event") in ("start", "stop") and isinstance(r.get("agent"), str)
                and isinstance(r.get("at"), str) and fleet.parse(r["at"])):
            last[r["agent"]] = i
        elif line.strip():
            bad.append(i)
    return fleet._fold(lines), {a for a, i in last.items() if any(b > i for b in bad)}


def read_bytes(path):
    """The file's bytes, b"" when it does not exist; any other failure to read is an OSError."""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return b""


def pr_end(key, F):
    """("done", merge commit) | ("open", why) | ("unknown", why). The collector's detail decides; GitHub is asked only
    about a PR it has not seen, or one it shows merged without the commit."""
    d = F["prs"].get(key)
    if d and not tidy.merged(d):
        return "open", f"{key} closed without a merge" if d.get("state") == "CLOSED" else f"{key} is not merged"
    sha = d.get("merge_sha") if d else None
    sha = sha or F["merged_fn"](key)
    return ("done", sha) if sha else ("unknown", f"{key} not shown merged")


def card_closure(ident, db):
    """A card with no PR ends only on a done close that was not tidy's "PR closed" (that one is no merge)."""
    # a close the card inherits from the row an annotation took the place of (row:<id> is its alias) counts as its own
    done = db.execute("SELECT state, rule FROM closure WHERE unit=?", (store._alias_closure(db, f"card:{ident}"),)).fetchone()
    if done and done["state"] == "done" and done["rule"] != "pr_closed":
        return "done", {"card": ident, "closure": "done"}
    return "open", f"card {ident} has no PR and no done close"


def row_done(r):
    """A finished board row keeps its owner and next step as a record; neither is a turn or a step that is left."""
    return r.get("status") == "done" or bool(r.get("done_at"))


def steps_left(ident, F):
    """Whether a board line about the card still names a step after the merge: a row (its `--worktree`, else the card its
    id and title name, as the board links it) or a doing item (the card its title or next step names)."""
    for rid, r in F["rows"].items():
        if row_done(r):
            continue
        card = F["cards"].get(r.get("worktree")) or tidy.named(f"{rid} {r.get('title') or ''}", F["cards"])
        if card and card["id"] == ident and tidy.POST_MERGE.search(r.get("next") or ""):
            return True
    # the card's annotation names a step after merge (an owner block's next is the owner's turn, db_turn's)
    if tidy.POST_MERGE.search(((F["board_now"].get("annots") or {}).get(f"card:{ident}") or {}).get("next") or ""):
        return True
    return any((c := tidy.doing_card(d, F["cards"])) and c["id"] == ident and tidy.POST_MERGE.search(d.get("next") or "")
               for d in F["board"]["doing"])


def db_turn(real, cards_all, me, decisions, board_now):
    """(code, why) when something kept in the DB waits on the owner at the order's units, else None: an open decision
    from a linked card's terminals or about a linked PR (its link, title or body), an open board row the owner holds at
    a linked card, an open board item the owner holds that names a linked card or PR. A PR's requested reviewer or a
    closed item's assignee is history, not a turn. Also read again inside the close transaction."""
    cards = {u[5:]: cards_all.get(u[5:]) for u in real if u.startswith("card:")}
    handles = {t.get("handle") for c in cards.values() if c for t in c.get("terminals") or []}
    prs = {u[3:] for u in real if u.startswith("pr:")} | {c["pr"] for c in cards.values() if c and c.get("pr")}
    for d in decisions:
        if d.get("handle") in handles or set(fleet.pr_refs(f"{d.get('link')} {d.get('title')} {d.get('body')}")) & prs:
            return "owner_turn", "an open decision about a linked card or PR waits for an answer"
    for rid, r in (board_now.get("rows") or {}).items():
        if row_done(r):
            continue
        card = cards_all.get(r.get("worktree")) or tidy.named(f"{rid} {r.get('title') or ''}", cards_all)
        if card and card["id"] in cards and (r.get("status") == "owner" or rowstate.is_owner(r.get("owner"), me)):
            return "owner_turn", "an open board row waits on the owner at a linked card"
    for cid in cards:  # an owner block written on the card's annotation
        if rowstate.is_owner(((board_now.get("annots") or {}).get(f"card:{cid}") or {}).get("owner"), me):
            return "owner_turn", "a card annotation waits on the owner at a linked card"
    for lane in ("doing", "todo", "owner_todo"):
        for it in board_now.get(lane) or []:
            text = f"{it.get('title') or ''} {it.get('next') or ''}"
            names = (c := tidy.named(text, cards_all)) and c["id"] in cards or set(fleet.pr_refs(text)) & prs
            if names and (lane == "owner_todo" or rowstate.is_owner(it.get("owner") or it.get("who"), me)):
                return "owner_turn", f"an open {lane} item owned by the owner names a linked card or PR"
    return None


def owner_turn(real, F):
    """(code, why) when the owner has a turn at the order's units, else None; unreadable evidence counts as a turn."""
    if not F["me"]:
        return "owner_unread", "the owner's login is unknown"
    cards = {u[5:] for u in real if u.startswith("card:")}
    if any(i.get("session") in cards for i in F["owner_items"]):  # an inbox item or a human-gate wait on the session
        return "owner_turn", "an inbox item waits on the owner at a linked card"
    return db_turn(real, F["cards"], F["me"], F["decisions"], F["board_now"])


def unit_end(unit, F):
    """("done", evidence) | ("open", why) | ("unknown", why) for one linked unit."""
    kind, _, ident = unit.partition(":")
    db = F["db"]
    if kind == "pr":
        verdict, got = pr_end(ident, F)
        return (verdict, {"pr": ident, "merge": got}) if verdict == "done" else (verdict, got)
    if kind == "card":
        c = F["cards"].get(ident)
        if not F["fresh"]:
            return "unknown", "session list is stale or unread"
        if not c:
            return "unknown", f"card {ident} is not in the session list"
        if c.get("phase") in ("working", "waiting"):
            return "open", f"card {ident} is {c['phase']}"
        if c["pr"]:
            verdict, sha = pr_end(c["pr"], F)
            if verdict != "done":
                return verdict, sha
            if steps_left(ident, F):
                return "open", f"card {ident} has a step after the merge"
            return "done", {"card": ident, "pr": c["pr"], "merge": sha}
        return card_closure(ident, db)
    if kind == "agent":
        if F["agents"] is None:
            return "unknown", "the subagent ledger could not be read"
        runs, unsure = F["agents"]
        if ident in unsure:
            return "unknown", f"agent {ident}: an unreadable ledger line follows its last run"
        a = runs.get(ident)
        if not a or not a.get("started_at"):
            return "unknown", f"agent {ident} has no run in the ledger"
        return ("done", {"agent": ident, "run": a["started_at"], "ended": "stopped"}) if a.get("stopped_at") \
            else ("open", f"agent {ident} has not stopped")
    if kind == "board":
        r = db.execute("SELECT status FROM item WHERE id=?", (ident,)).fetchone()
        if not r:
            return "unknown", f"board item {ident} not found"
        if r["status"] != "done":
            return "open", f"board item {ident} is {r['status']}"
        seq = db.execute("SELECT max(seq) FROM event WHERE entity='item' AND id=? AND ev='done'", (ident,)).fetchone()[0]
        return ("done", {"board": ident, "closed_event": seq}) if seq else ("unknown", f"board item {ident} has no close event")
    if kind == "decision":
        r = db.execute("SELECT status FROM decision WHERE id=?", (ident,)).fetchone()
        if not r:
            return "unknown", f"decision {ident} not found"
        return ("done", {"decision": ident, "status": "answered"}) if r["status"] == "done" else ("open", f"decision {ident} is {r['status']}")
    return "unknown", f"unit {unit} has no known source"


def judge(o, F):
    """{id, close, why, detail, units, snapshot, links}: whether tidy would close order `o` and, when not, the first
    thing that holds it. The checks run cheapest first, so a PR is asked about only for an order that got that far."""
    real = [u for u in o["links"] if u not in o["stand_ins"]]
    out = {"id": o["id"], "close": False, "units": {}, "snapshot": None, "links": real}

    def no(why, detail=""):
        return {**out, "why": why, "detail": detail}
    if not real:
        return no("no_link", "no real unit (a stand-in is not one)")
    if not o["links_complete_at"]:
        return no("scope_unconfirmed", "the receiving session has not said every unit is linked")
    if o["owner_action"]:
        return no("owner_action", "marked --owner-action: closed by the session only")
    if F["owner_items"] is None:
        return no("owner_unread", "the owner's inbox could not be read")
    if turn := owner_turn(real, F):
        return no(*turn)
    units, held = {}, []
    for u in real:
        verdict, ev = unit_end(u, F)
        if verdict == "done":
            units[u] = ev
        else:
            held.append((verdict, u, ev))
    if held:
        first = sorted(held, key=lambda h: h[0] != "open")[0]  # an open unit explains more than an unread one
        return no("partial" if first[0] == "open" else "unread", f"{first[1]}: {first[2]}")
    if o["kind"] == "feedback" and not any(u.startswith("pr:") for u in real):
        return no("feedback_needs_pr", "feedback closes on a merged PR, a to-do or card is not evidence")
    snap = json.dumps(units, sort_keys=True, separators=(",", ":"))
    if o["reopen_evidence"] == store.EVIDENCE_UNKNOWN:  # reopened with its units unread: this reading is the baseline
        return {**no("baseline", "reopened while its units could not be read"), "units": units, "snapshot": snap}
    if snap == o["reopen_evidence"]:
        return no("same_evidence", "a Reopen undid a close on this same evidence")
    return {**out, "close": True, "why": "units_done", "detail": "", "units": units, "snapshot": snap}


def recheck(v):
    """A check(db) for store.order_auto, run in its write transaction: every fact the judgement read is read again, DB
    and files alike (session list, subagent ledger, inbox, PR cache), and each unit must still end on the evidence it
    was judged on and the owner must still have no turn. A PR the judgement got from GitHub is not asked again: a merge
    does not undo."""
    known = {ev["pr"]: ev["merge"] for ev in v["units"].values() if "pr" in ev and "merge" in ev}

    def check(db):
        state, prs_file = load()
        F = gather(db, state, prs_file, store.board(db), fleet.utcnow(), merged_fn=known.get)
        for u, ev in v["units"].items():
            if unit_end(u, F) != ("done", ev):
                raise ValueError(f"{u} changed while it was judged")
        if F["owner_items"] is None or owner_turn(v["links"], F):
            raise ValueError("the owner has a turn at its units")
    return check


def tick(state, prs_file, board_now, now, merged_fn=gh_merged):
    """One pass for tidy: judge every open order; write a would_close event, or with the key on close it. Returns the
    verdicts that closed or would close."""
    try:
        orders = store.orders("open")
    except store.NotMigrated:
        return []
    if not orders:
        return []
    on, out = enabled(), []
    with store.read() as db:
        F = gather(db, state, prs_file, board_now, now, merged_fn)
        verdicts = [(o, judge(o, F)) for o in orders]
    for o, v in verdicts:
        if v["why"] == "baseline":
            store.order_baseline(o["id"], v["snapshot"], v["links"])
        elif v["close"] and store.order_auto(o["id"], v["snapshot"], v["units"], v["links"], close=on,
                                             recheck=recheck(v)):  # False: it moved, the next pass reads it again
            out.append(v)
    return out


def snapshot_now(o, now=None):
    """The normalized evidence of the units of order row `o` as they are, for the close event of a close by hand (done or
    drop) and for a Reopen that finds none there; None when any unit is not ended or cannot be read."""
    state, prs_file = load()
    now = now or fleet.utcnow()
    try:
        board_now = store.board()
    except store.NotMigrated:
        board_now = None
    with store.read() as db:
        F = gather(db, state, prs_file, board_now, now)
        units = {}
        for u in (x for x in o["links"] if x not in o["stand_ins"]):
            verdict, ev = unit_end(u, F)
            if verdict != "done":
                return None
            units[u] = ev
    return json.dumps(units, sort_keys=True, separators=(",", ":")) if units else None


def compare(now=None, merged_fn=gh_merged):
    """Set the shadow's candidates against what the sessions did. Returns (lines, wrong): each candidate as agree
    (closed done by hand after it), pending (still open), differs (dropped), reopened (reopened after it); and the orders
    closed done by hand that never were a candidate, with what holds them now. wrong counts differs and reopened: the
    closes the key would have gotten wrong."""
    now = now or fleet.utcnow()
    state, prs_file = load()
    try:
        board_now = store.board()
    except store.NotMigrated:
        board_now = None
    with store.read() as db:
        orders = store.orders(db=db)
        if orders is None:
            raise store.NotMigrated(f"orders are not set up: run {store.order_setup_command()}")
        events = {}
        for r in db.execute("SELECT seq, ts, id, ev FROM event WHERE entity='order' AND ev IN ('would_close','done','dropped','reopen','links_open') ORDER BY seq"):
            events.setdefault(r["id"], []).append(dict(r))
        F = gather(db, state, prs_file, board_now, now, merged_fn)
        lines, counts, wrong = [], {}, 0
        for o in orders:
            evs = events.get(o["id"], [])
            cand = next((e for e in evs if e["ev"] == "would_close"), None)
            if cand:
                later = [e for e in evs if e["seq"] > cand["seq"]]
                if any(e["ev"] == "reopen" for e in later):
                    word, note = "reopened", f"reopened after the candidate of {cand['ts']}"
                elif o["status"] == "open":
                    word, note = "pending", f"candidate since {cand['ts']}; the session has not closed it"
                elif o["status"] == "dropped":
                    word, note = "differs", f"candidate of {cand['ts']}, but the session dropped it"
                else:
                    word, note = "agree", f"candidate of {cand['ts']}, closed {o['closed_at']} by {o['close_by'] or 'hand'}"
                wrong += word in ("differs", "reopened")
            elif o["status"] == "done" and o["close_by"] != "auto":
                v = judge(o, F)
                word, note = "missed", f"closed by hand {o['closed_at']}, no candidate; now: {v['why']} {v['detail']}".rstrip()
            else:
                continue
            counts[word] = counts.get(word, 0) + 1
            lines.append(f"{o['id']:<5} {word:<9} {note}")
    head = "shadow: " + (", ".join(f"{w} {n}" for w, n in sorted(counts.items())) or "no candidate yet")
    return [head, *lines], wrong
