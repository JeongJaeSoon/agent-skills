"""Units: one row per session, Brief row, subagent or open PR, joined once for every screen that lists them.

build(inp) is a pure function of rowstate.inputs(): it keeps no store and decides no status. A row's state is whatever
rowstate.judged() said for it; a session the brief gave no row gets a synthetic row, the same dict shape fleet_rows
writes, and goes through the same judged(). Nothing here computes a status from a phase or a PR.

A PR claimed by two or more sessions is linked to none of them ("link unknown"): the collector keeps only the first
claimant, so the candidates are read from `candidates`. The ticket id of a unit is computed here once.

`python3 units.py` prints the build as JSON, which is what the dashboard's /api/fleet/units serves.
"""
import json, re, sys, urllib.parse

import board, fleet, rowstate

# A ticket id is letters or digits, a dash and digits, with a letter in the prefix; only upper case counts. Not \b: an
# underscore is a word character there, and "feat/ABC-12_fix" still holds a ticket.
TICKET_RE = re.compile(r"(?<![A-Za-z0-9])(?=[A-Z0-9]*[A-Z])[A-Z0-9]{2,10}-\d{1,6}(?![A-Za-z0-9])")
# Standards and formats that read like a ticket id ("UTF-8", "SHA-256").
NOT_TICKET = re.compile(r"^(UTF|SHA|MD|AES|RSA|ISO|TLS|SSL|HTTP|CRC|USB|GPT|WCAG|IPV|TCP|UDP|X)-")
KIND = {"orchestrator": "lead", "orchestration": "lead", "task": "worker", "standalone": "solo"}
SESSION_KEYS = ("id", "name", "phase", "kind", "model", "model_tag", "terminal", "orca_parent", "parent", "run", "dispatch",
                "dispatch_status", "dispatch_live", "project", "branch", "workspace_status", "last_activity", "unread",
                "missed", "needs_owner")


def tickets_of(s, titles, url_prefix):
    """[{"id", "url", "from"}]: the card's own tracker links first, then ids in its name, its branch, its brief rows' titles
    and its task title."""
    out = [{"id": r.get("key") or r.get("provider") or "ticket", "url": r["url"], "from": "refs"}
           for r in s.get("refs") or [] if r.get("type") != "pr" and r.get("url")]
    for src, text in (("name", s.get("name")), ("branch", s.get("branch")),
                      *(("row", t) for t in titles), ("title", s.get("task_title"))):
        for k in TICKET_RE.findall(str(text or "")):
            if not NOT_TICKET.match(k) and not any(o["id"] == k for o in out):
                out.append({"id": k, "url": url_prefix + urllib.parse.quote(k, safe="") if url_prefix else None, "from": src})
    return out


def synthetic_row(s, by_session, details):
    """The row fleet_rows would write for a session that has none, so judged() reads it like any other."""
    status, nxt = board.PHASE_ROW.get(s.get("phase"), ("held", s.get("phase") or "—"))
    rep = s.get("report") or {}
    if rep.get("next") and s.get("phase") != "offline":
        nxt = rep["next"]
    kind = "lead" if s.get("kind") in ("orchestration", "orchestrator") else "task"
    return {"id": f"fleet:{s['id']}", "kind": kind, "title": board.one_line(s.get("name") if kind == "lead" else board.card_title(s)),
            "status": status, "next": board.one_line(nxt), "session": s["id"],
            "pr": fleet.current_pr({**s, "report": None} if kind == "lead" else s, by_session, details),
            "reported_at": max(filter(None, (s.get("last_activity"), rep.get("at"))), default=None), "parent": None,
            "repo": s.get("gh_repo") or s.get("project") or "—", "program": None}


def lead_above(s, by_id):
    """The nearest session above s that is a lead (kind orchestration), else None. A parent cycle ends the walk."""
    seen, p = {s["id"]}, by_id.get(s.get("parent"))
    while p and p["id"] not in seen:
        if p.get("kind") == "orchestration":
            return p
        seen.add(p["id"])
        p = by_id.get(p.get("parent"))
    return None


def parent_source(s, runs, by_id):
    """How the collector found s's parent: the Run its Dispatch belongs to, Orca's own parent field, or neither (the
    root, filled in)."""
    if not s.get("parent"):
        return None
    if s.get("dispatch") and (runs.get(s.get("run")) or {}).get("session") == s["parent"]:
        return "dispatch"
    return "orca" if s.get("orca_parent") == s["parent"] and s["parent"] in by_id else "fallback"


def pr_entry(key, p, details, via, candidates=None):
    d = (details or {}).get(key) or {}
    p = p or {}
    out = {"key": key, "state": p.get("state") or d.get("state"), "ci": p.get("ci") or d.get("ci"),
           "decision": p.get("decision") or d.get("decision"), "url": p.get("url") or d.get("url"),
           "number": p.get("number") or d.get("number") or (int(tail) if (tail := key.rsplit("#", 1)[-1]).isdigit() else None),
           "via": via}
    if candidates is not None:
        out["candidates"] = candidates
    return out


def build(inp, ticket_url=""):
    """{"units", "groups", "counts", "link_unknown", "unknown"} from rowstate.inputs(). ticket_url: the tracker's issue
    URL prefix (fleet.ticket_url()), passed in so the same input always builds the same units."""
    state = inp.get("state") or {}
    facts, details = inp["facts"], inp.get("prs") or {}
    sessions = [s for s in state.get("sessions") or [] if s.get("id")]
    by_id = {s["id"]: s for s in sessions}
    prs = {p["key"]: p for p in state.get("prs") or [] if p.get("key")}
    by_session = fleet.prs_by_session(state.get("prs"))
    rows, states, session_of = inp["rows"], inp["states"], inp["session_of"]
    candidates, runs = inp.get("candidates") or {}, inp.get("runs") or {}
    url_prefix = ticket_url or ""
    stored_rows = inp.get("board_rows") or {}

    def ambiguous(key):
        """Whether several sessions claimed the PR: then no session unit shows it, whichever way it was found (a render
        filling in a row's PR, a stored row, Orca's own link). A board row with no session keeps its own --pr."""
        return len(candidates.get(key) or ()) > 1

    # Rows by the session their work runs in; a row with none is an item of its own.
    on_session, items, agents = {}, [], []
    for rid, r in rows.items():
        sid = session_of.get(rid)
        if r.get("subagent"):
            agents.append(rid)
        elif sid in by_id:
            on_session.setdefault(sid, []).append(rid)
        else:
            items.append(rid)

    def row_ref(rid, sid):
        r = rows[rid]
        via = ("own" if r.get("session") == sid else "worktree" if r.get("worktree") == sid
               else "linked" if r.get("linked") == sid else "inherited")
        return {"id": rid, "state": states.get(rid), "via": via, "title": r.get("title"), "pr": r.get("pr")}

    units = {}
    for s in sessions:
        sid = s["id"]
        refs = [row_ref(rid, sid) for rid in on_session.get(sid, [])]
        if not refs:  # no row: the same judgment on the same kind of row, not a second one
            r = synthetic_row(s, by_session, details)
            lead = None
            refs = [{"id": r["id"], "state": rowstate.judged(r, facts, lead, sid), "via": "synthetic",
                     "title": r["title"], "pr": r["pr"]}]
            rows = {**rows, r["id"]: r}
        mine = []
        for key, p in prs.items():
            cands = candidates.get(key)
            if cands is None:  # an older collector: its single owner is all there is
                if p.get("session") == sid:
                    mine.append(pr_entry(key, p, details, "session"))
            elif cands == [sid]:
                mine.append(pr_entry(key, p, details, "session", cands))
        for rf in refs:
            if rf["pr"] and not ambiguous(rf["pr"]) and not any(m["key"] == rf["pr"] for m in mine):
                mine.append(pr_entry(rf["pr"], prs.get(rf["pr"]), details, "row", candidates.get(rf["pr"])))
        lp = s.get("linked_pr") if isinstance(s.get("linked_pr"), dict) else {}
        # Orca's own link, as it observed it; its key may be missing, so a PR of this number that several sessions claimed
        # counts too.
        shared = lp.get("number") and any(ambiguous(k) and sid in candidates[k] and (k == lp.get("key") or k.rsplit("#", 1)[-1] == str(lp["number"]))
                                          for k in candidates)
        for m in mine:  # a PR Orca links but the collector has not read yet: Orca's number, url and state stand in
            if lp.get("key") and m["key"] == lp["key"]:
                m.update({k: m[k] or lp.get(src) for k, src in (("url", "url"), ("state", "state"), ("ci", "checksStatus"))})
        if not mine and lp.get("number") and not shared:
            mine.append({"key": lp.get("key"), "state": lp.get("state"), "ci": lp.get("checksStatus"), "decision": None,
                         "url": lp.get("url"), "number": lp["number"], "via": "orca"})
        best = min((rf["state"] for rf in refs if rf["state"]), key=lambda st: rowstate.rank(st["status"]), default=None)
        units[f"card:{sid}"] = {
            "id": f"card:{sid}", "kind": KIND.get(s.get("kind"), "solo"), "title": refs[0]["title"] or s.get("name"),
            "session": {k: s.get(k) for k in SESSION_KEYS}, "rows": refs, "prs": mine, "state": best,
            "pr_unknown": sorted(k for k, c in candidates.items() if len(c) > 1 and sid in c),
            "tickets": tickets_of(s, [rf["title"] for rf in refs], url_prefix),
            "report": s.get("report"), "who": best and best.get("who"),
            "owner_turn": any(rf["state"] and rf["state"]["status"] == "owner" for rf in refs),
            "dirty": (inp.get("dirty") or {}).get(sid), "project": s.get("project"), "subagents": len(s.get("subagents") or []),
            "group": None,
            # the program a stored board row names; a fleet row's `program` is its lead's own name
            "program": next((rows[rf["id"]].get("program") for rf in refs
                             if rf["id"] in stored_rows and rows[rf["id"]].get("program")), None)}
    for rid in agents:
        r = rows[rid]
        host = session_of.get(rid) or r.get("host")
        units[rid] = {"id": rid, "kind": "subagent", "title": r.get("title"), "session": None, "host": host, "rows": [],
                      "prs": [], "state": states.get(rid), "tickets": [], "report": None, "who": None, "owner_turn": False,
                      "dirty": None, "project": (by_id.get(host) or {}).get("project"), "subagents": 0, "group": None}
    for rid in items:
        r = rows[rid]
        st = states.get(rid)
        units[f"row:{rid}"] = {
            "id": f"row:{rid}", "kind": "item", "title": r.get("title"), "session": None, "rows": [row_ref(rid, None)],
            "prs": [pr_entry(r["pr"], prs.get(r["pr"]), details, "row", candidates.get(r["pr"]))] if r.get("pr") else [],
            "state": st, "tickets": [{"id": k, "url": url_prefix + urllib.parse.quote(k, safe="") if url_prefix else None,
                                       "from": "row"} for k in dict.fromkeys(TICKET_RE.findall(str(r.get("title") or "")))
                                      if not NOT_TICKET.match(k)],
            "report": None, "who": st and st.get("who"), "owner_turn": bool(st) and st["status"] == "owner", "dirty": None,
            "project": r.get("repo"), "subagents": 0, "group": None, "program": r.get("program")}

    # A PR no single session owns: kept apart so it is never linked by guess.
    row_prs = {p["key"] for u in units.values() for p in u["prs"] if p.get("key")}
    link_unknown = sorted(k for k, c in candidates.items() if len(c) > 1)
    for key, p in prs.items():
        if p.get("state") != "OPEN" or key in row_prs:
            continue
        cands = candidates.get(key)
        # Owned by one session still in the list; a claimant that left the list owns nothing.
        owners = [c for c in (cands if cands is not None else [p.get("session")]) if c in by_id]
        if len(owners) == 1 and len(cands or ()) < 2:
            continue
        units[f"pr:{key}"] = {"id": f"pr:{key}", "kind": "pr", "title": p.get("title") or key, "session": None, "rows": [],
                              "prs": [pr_entry(key, p, details, "none", cands or [])], "state": None, "tickets": [],
                              "report": None, "who": None, "owner_turn": False, "dirty": None, "project": p.get("repo"),
                              "subagents": 0, "group": None, "link": "unknown" if len(cands or ()) > 1 else "none"}
    groups = place(units, by_id, runs)
    counts = {"units": len(units), "groups": len(groups), "link_unknown": len(link_unknown),
              "kinds": {k: sum(u["kind"] == k for u in units.values()) for k in sorted({u["kind"] for u in units.values()})}}
    return {"v": 1, "units": list(units.values()), "groups": groups, "counts": counts, "link_unknown": link_unknown,
            "unknown": list(inp.get("unknown") or [])}


def place(units, by_id, runs):
    """Put every unit in exactly one group and return the groups: under its lead; else its program; else Solo. The
    orchestrator heads the first group, a PR no session owns the last."""
    groups = {}

    def put(gid, by, key, title, u, lead=None):
        g = groups.setdefault(gid, {"id": gid, "by": by, "key": key, "title": title, "lead": lead, "units": []})
        g["units"].append(u["id"])
        u["group"] = {"by": by, "key": key, "id": gid, "parent_source": u.pop("_ps", None)}

    for u in units.values():
        s = by_id.get((u.get("session") or {}).get("id")) if u["session"] else by_id.get(u.get("host"))
        if u["kind"] == "pr":
            put("pr", "solo", "pr", "Pull requests no session owns", u)
        elif s and s.get("kind") == "orchestrator":
            put("root", "solo", "root", s.get("name") or "orchestrator", u)
        elif s:
            lead = s if s.get("kind") == "orchestration" else lead_above(s, by_id)
            u["_ps"] = parent_source(s, runs, by_id) if u["kind"] != "subagent" else None
            program = u.get("program") or (units.get(f"card:{u.get('host')}") or {}).get("program")
            if lead:
                put(f"lead:{lead['id']}", "lead", lead["id"], lead.get("name") or lead["id"], u, f"card:{lead['id']}")
            elif program:
                put(f"project:{program}", "project", program, program, u)
            else:
                put("solo", "solo", "", "Solo", u)
        elif u.get("program"):
            put(f"project:{u['program']}", "project", u["program"], u["program"], u)
        else:
            put("solo", "solo", "", "Solo", u)
    order = lambda g: (g["id"] != "root", g["by"] != "lead", g["id"] == "pr", g["by"] != "project", g["title"].casefold())  # noqa: E731
    return sorted(groups.values(), key=order)


def report():
    return build(rowstate.inputs(), fleet.ticket_url() or "")


if __name__ == "__main__":
    json.dump(report(), sys.stdout, ensure_ascii=False, default=str)
