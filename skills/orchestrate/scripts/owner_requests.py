"""The owner's requests to a coordinator, kept so none is lost when many arrive at once.

hooks/request_log.py appends each human prompt an orchestrator or lead receives; the coordinator routes each one to
where the work happens (`link`) and closes it (`done` with evidence, or `drop` with a reason). Requests are the small,
conversational ones (a check, a reply, a merge, a look at something): a request that grows into tracker-sized work is
linked to its ticket and closed here, and nothing here writes to the tracker. The fleet tick settles the rest
(reconcile): it links a request to the cards, board rows and PRs its session started right after it, closes one whose
links have all finished, and drops an unlinked one whose terminal is gone. An unlinked request past TTL_MIN expires:
it stays open in the file and can still be closed, but no count includes it.

One append-only file, <fleet state>/requests.jsonl (0600), one JSON object a line:
  {"ev": "open", "id": "r7", "ts", "session", "handle", "text"}   text masked and capped
  {"ev": "link", "id", "ts", "to"} | {"ev": "done", "id", "ts", "evidence"} | {"ev": "drop", "id", "ts", "reason"}
A request's state is the fold of its lines; a later link adds to the earlier ones. A row the fleet tick wrote
carries "by": "auto".
"""
import datetime as dt, fcntl, json, os, re

import fleet

TEXT_MAX = 200
NOTE_MAX = 300
UNLINKED_MIN = 30  # an open request with no link for this long is flagged
TTL_MIN = 12 * 60  # and for this long, expires
LINK_WINDOW_MIN = 30  # what a session starts this soon after a request, and before its next one, is linked to it
PR_RE = re.compile(r"[\w.-]+/[\w.-]+#\d+")


def path():
    return fleet.state_dir() / "requests.jsonl"


def _rows(f):
    f.seek(0)
    out = []
    for line in f:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("id"), str) and row.get("ev") in ("open", "link", "done", "drop"):
            out.append(row)
    return out


def _append(make):
    """Runs make(rows) under the file lock and appends what it returns (a row, a list of rows, or None for nothing),
    so two prompts never share an id."""
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        out = make(_rows(f))
        for row in out if isinstance(out, list) else [out] if out else []:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return out


def read():
    # ponytail: re-reads the whole file per prompt and per fleet tick; ~300 B a request, so years before it matters
    try:
        with open(path(), encoding="utf-8") as f:
            return _rows(f)
    except OSError:
        return []


def fold(rows):
    """id -> request, in the order they were opened."""
    out = {}
    for r in rows:
        if r["ev"] == "open":
            out[r["id"]] = {"id": r["id"], "ts": r.get("ts"), "session": r.get("session"), "handle": r.get("handle"),
                            "text": r.get("text") or "", "status": "open", "links": []}
        elif r["id"] in out:
            q = out[r["id"]]
            if r["ev"] == "link":
                q["links"].append(r.get("to"))
            else:
                q.update(status=r["ev"], closed=r.get("ts"), note=r.get("evidence") or r.get("reason"))
    return out


def add(text, session=None, handle=None, now=None):
    """Opens a request, or returns the session's latest one when it is still open with the same text (a prompt sent
    twice)."""
    t, same = now or fleet.utcnow(), []

    def make(rows):
        masked = fleet.mask(" ".join(text.split()), TEXT_MAX)
        last = [q for q in fold(rows).values() if q["session"] == session][-1:]
        if last and last[0]["status"] == "open" and last[0]["text"] == masked:
            same.append(last[0])
            return None
        n = max([int(r["id"][1:]) for r in rows if r["ev"] == "open" and r["id"][1:].isdigit()] or [0]) + 1
        return {"ev": "open", "id": f"r{n}", "ts": fleet.iso(t), "session": session, "handle": handle, "text": masked}
    return _append(make) or same[0]


def close(rid, ev, note=None, to=None, now=None):
    """link (to), done (evidence) or drop (reason). Refuses an unknown id and a request already closed."""
    t = now or fleet.utcnow()

    def make(rows):
        q = fold(rows).get(rid)
        if not q:
            raise ValueError(f"no request {rid}")
        if q["status"] != "open":
            raise ValueError(f"{rid} is already {q['status']}")
        row = {"ev": ev, "id": rid, "ts": fleet.iso(t)}
        if ev == "link":
            row["to"] = fleet.mask(to, NOTE_MAX)
        else:
            row["evidence" if ev == "done" else "reason"] = fleet.mask(note, NOTE_MAX)
        return row
    return _append(make)


def age_min(q, now):
    t = fleet.parse(q.get("ts"))
    return int((now - t).total_seconds() // 60) if t else 0


def expired(q, now):
    return q["status"] == "open" and not q["links"] and age_min(q, now) >= TTL_MIN


def summary(now=None, requests=None):
    """What a report or the dashboard shows: open count, the oldest's age, the open ones left unlinked too long, and
    how many expired (left out of the rest)."""
    now = now or fleet.utcnow()
    all_open = [q for q in (requests if requests is not None else fold(read())).values() if q["status"] == "open"]
    open_ = [q for q in all_open if not expired(q, now)]
    stale = [q for q in open_ if not q["links"] and age_min(q, now) >= UNLINKED_MIN]
    return {"open": len(open_), "oldest_min": max([age_min(q, now) for q in open_] or [0]),
            "unlinked": [{"id": q["id"], "age_min": age_min(q, now), "text": q["text"][:80]} for q in stale],
            "expired": len(all_open) - len(open_)}


def finished(to, prs, cards, seen, board_rows):
    """Evidence that a link's target is over, or a falsy value: a merged or closed PR, a card seen live before and gone
    now (cards is None when the card list cannot be trusted), a board row marked done. A link in any other form is
    never over, so a request with one is left to the coordinator."""
    to = (to or "").strip()
    if PR_RE.fullmatch(to):
        p = prs.get(to) or {}
        return p.get("state") in ("MERGED", "CLOSED") and f"{to} {p['state'].lower()} {p.get('merged_at') or ''}".strip()
    if to.startswith("card:"):
        name = to[5:]
        return cards is not None and name in seen and name not in cards and f"{to} removed (last seen {seen[name]})"
    row = board_rows.get(to.removeprefix("board:")) or {}
    return row.get("status") == "done" and f"board:{row['id']} done {row.get('done_at') or ''}".strip()


def reconcile(requests, now, prs, sessions, board_rows, by_handle, seen, orca_ok):
    """The rows the fleet tick appends: links to what a request's session started right after it, done for one whose
    links are all over, drop for an unlinked one whose terminal is gone.

    A card, board row or PR is linked only when exactly one request from its maker's terminal came in the
    LINK_WINDOW_MIN before it was made: a turn that answered two prompts cannot say which one the card was for.
    prs is key -> {state, merged_at, created, session}; sessions are the fleet's (id -> id, name, parent, created);
    board_rows is board.json's rows; by_handle maps each live terminal to its session. seen (card name -> last time
    it was live) is updated in place and keeps only cards some open request links to. Nothing card- or
    terminal-based happens unless the Orca read just succeeded and listed sessions, so one failed read closes nothing.
    """
    stamp, out = fleet.iso(now), []
    trusted = orca_ok and bool(sessions)
    cards = {s["name"] for s in sessions.values()} if trusted else None
    window = dt.timedelta(minutes=LINK_WINDOW_MIN)

    def before(at):
        return fleet.iso(fleet.parse(at) - window) if at and fleet.parse(at) else None
    made = [(f"card:{s['name']}", s.get("created"), lambda h, s=s: by_handle.get(h) == s.get("parent"))
            for s in sessions.values() if s.get("parent") and s.get("parent") != s["id"]]
    for k, r in board_rows.items():
        up = board_rows.get(r.get("parent")) or {}
        if not (up.get("by") == r.get("by") and (up.get("created_at") or "") >= (before(r.get("created_at")) or "~")):
            made.append((f"board:{k}", r.get("created_at"), lambda h, r=r: bool(h) and h == r.get("by")))
    made += [(k, p.get("created"), lambda h, p=p: bool(p.get("session")) and by_handle.get(h) == p["session"])
             for k, p in prs.items()]
    new_links = {}
    for to, at, mine in made:
        if not (start := before(at)):
            continue
        asked = [q for q in requests.values() if mine(q.get("handle")) and start <= (q.get("ts") or "") <= at]
        if len(asked) == 1 and asked[0]["status"] == "open" and to not in asked[0]["links"]:
            new_links.setdefault(asked[0]["id"], []).append(to)
            out.append({"ev": "link", "id": asked[0]["id"], "ts": stamp, "to": to, "by": "auto"})
    for q in requests.values():
        if q["status"] != "open":
            continue
        links = q["links"] + new_links.get(q["id"], [])
        if cards is not None:
            for to in links:
                if to.startswith("card:") and to[5:] in cards:
                    seen[to[5:]] = stamp
        over = [finished(to, prs, cards, seen, board_rows) for to in links]
        if links and all(over):
            out.append({"ev": "done", "id": q["id"], "ts": stamp, "by": "auto",
                        "evidence": fleet.mask("auto: " + "; ".join(over), NOTE_MAX)})
        elif not links and trusted and q.get("handle") and q["handle"] not in by_handle:
            out.append({"ev": "drop", "id": q["id"], "ts": stamp, "by": "auto", "reason": "auto: its terminal is gone"})
    closed = {r["id"] for r in out if r["ev"] != "link"}
    linked = {to[5:] for q in requests.values() if q["status"] == "open" and q["id"] not in closed
              for to in q["links"] if to.startswith("card:")}
    linked |= {r["to"][5:] for r in out if r["ev"] == "link" and r["id"] not in closed and r["to"].startswith("card:")}
    for name in [n for n in seen if n not in linked]:
        del seen[name]
    return out


def apply(rows):
    """Appends the rows reconcile returned, skipping any the file has overtaken since (closed, or already linked
    there), and returns those written."""
    def make(current):
        state, keep = fold(current), []
        for r in rows:
            q = state.get(r["id"])
            if not q or q["status"] != "open" or r["ev"] == "link" and r["to"] in q["links"]:
                continue
            keep.append(r)
            if r["ev"] == "link":
                q["links"].append(r["to"])
            else:
                q["status"] = r["ev"]
        return keep
    return _append(make)


def ago(minutes):
    return f"{minutes}m" if minutes < 60 else f"{minutes // 60}h{minutes % 60:02d}m" if minutes < 1440 else f"{minutes // 1440}d"


def line(s):
    """One line for a report; empty when nothing is open."""
    if not s["open"]:
        return ""
    flag = f" · 미연결 {UNLINKED_MIN}분+ {len(s['unlinked'])}건" if s["unlinked"] else ""
    return f"요청 {s['open']}건 열림 · 가장 오래된 것 {ago(s['oldest_min'])}{flag}"
