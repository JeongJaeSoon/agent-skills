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

import fleet, store, tidy

RULE = "order_units_done"
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
    try:
        owner_items = [i for i in fleet.external_items(now) if i["type"] in tidy.OWNER_TYPES and i.get("session")]
    except (OSError, ValueError):
        owner_items = None  # not read: no order is called free of the owner's turn
    return {"db": db, "prs": prs, "cards": cards, "fresh": fresh, "owner_items": owner_items, "merged_fn": merged_fn,
            "rows": (board_now or {}).get("rows") or {}, "agents": fleet.subagents()}


def pr_end(key, F):
    """("done", merge commit) | ("open", why) | ("unknown", why). The collector's detail decides; GitHub is asked only
    about a PR it has not seen, or one it shows merged without the commit."""
    d = F["prs"].get(key)
    if d and not tidy.merged(d):
        return "open", f"{key} closed without a merge" if d.get("state") == "CLOSED" else f"{key} is not merged"
    sha = d.get("merge_sha") if d else None
    sha = sha or F["merged_fn"](key)
    return ("done", sha) if sha else ("unknown", f"{key} not shown merged")


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
            if any(r.get("worktree") == ident and tidy.POST_MERGE.search(r.get("next") or "") for r in F["rows"].values()):
                return "open", f"card {ident} has a step after the merge"
            return "done", {"card": ident, "pr": c["pr"], "merge": sha}
        done = db.execute("SELECT state, rule FROM closure WHERE unit=?", (f"card:{ident}",)).fetchone()
        if done and done["state"] == "done" and done["rule"] != "pr_closed":
            return "done", {"card": ident, "closure": "done"}
        return "open", f"card {ident} has no PR and no done close"
    if kind == "agent":
        a = F["agents"].get(ident)
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
    cards = {u[5:] for u in real if u.startswith("card:")}
    if F["owner_items"] is None:
        return no("owner_unread", "the owner's inbox could not be read")
    if turn := [i for i in F["owner_items"] if i.get("session") in cards]:
        return no("owner_turn", f"{len(turn)} inbox item(s) wait on the owner at a linked card")
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
    if snap == o["reopen_evidence"]:
        return no("same_evidence", "a Reopen undid a close on this same evidence")
    return {**out, "close": True, "why": "units_done", "detail": "", "units": units, "snapshot": snap}


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
        if not v["close"]:
            continue
        try:
            if on:
                store.order_close(o["id"], "done", "auto", evidence={"rule": RULE, "units": v["units"], "snapshot": v["snapshot"]},
                                  check=lambda row, v=v: store.auto_still(row, v["links"], v["snapshot"]))
            elif not store.order_shadow(o["id"], v["snapshot"], v["units"], v["links"]):
                continue
        except ValueError:
            continue  # moved while it was judged: the next pass reads it again
        out.append(v)
    return out


def snapshot_now(oid, now=None):
    """The normalized evidence of an order's units as they are, for a Reopen of a close by hand; None when any is
    not ended or unread."""
    state, prs_file = load()
    now = now or fleet.utcnow()
    o = store.order_get(oid)
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
