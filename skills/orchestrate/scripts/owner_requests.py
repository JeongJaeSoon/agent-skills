"""The owner's requests to a coordinator, kept so none is lost when many arrive at once.

hooks/request_log.py appends each human prompt an orchestrator or lead receives; the coordinator routes each one to
where the work happens (`link`) and closes it (`done` with evidence, or `drop` with a reason). Requests are the small,
conversational ones (a check, a reply, a merge, a look at something): a request that grows into tracker-sized work is
linked to its ticket and closed here, and nothing here writes to the tracker. The fleet tick settles the rest
(reconcile): it links a request to the cards, board rows and PRs its session started right after it, closes one whose
links have all finished, and drops an unlinked one whose terminal has been gone for GONE_MIN. An unlinked request
past TTL_MIN expires: it stays open in the file and can still be closed, but no count includes it.

One append-only file, <fleet state>/requests.jsonl (0600), one JSON object a line:
  {"ev": "open", "id": "r7", "ts", "session", "handle", "text"}   text masked and capped
  {"ev": "link", "id", "ts", "to"} | {"ev": "done", "id", "ts", "evidence"} | {"ev": "drop", "id", "ts", "reason"}
A request's state is the fold of its lines; a later link adds to the earlier ones. A row the fleet tick wrote
carries "by": "auto".
"""
import collections, datetime as dt, fcntl, json, os, re

import fleet

TEXT_MAX = 200
NOTE_MAX = 300
UNLINKED_MIN = 30  # an open request with no link for this long is flagged
TTL_MIN = 12 * 60  # and for this long, expires
LINK_WINDOW_MIN = 30  # what a session starts this soon after a request, and before its next one, is linked to it
GONE_MIN = 10  # a terminal missing from Orca's list this long, over two trusted reads, is gone: one read can drop a few
CHATTY_MAX = 20
# An acknowledgement or a thanks; with a short question, it asks for nothing a card or PR would be made for.
ACK = re.compile(r"(?:ok(?:ay)?|yes|no|thanks?(?: you)?|thx|lgtm|got it|ㅇㅋ|ㅇㅇ|ㄱㄱ|응|네|넵|예|좋아요?|좋네요?|고마워요?|감사(?:합니다|해요)?|알겠(?:어|어요|습니다)|확인(?:했어|했어요|했습니다)?)[\s.!~ㅋㅎ]*", re.I)


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
    """Runs make(rows) under the file lock and appends the rows it returns, so two prompts never share an id."""
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        out = make(_rows(f))
        for row in out:
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
    """(request, True) for a new one, or (the session's latest, False) when that is still open with the same text: a
    prompt sent twice."""
    t, same = now or fleet.utcnow(), []

    def make(rows):
        masked = fleet.mask(" ".join(text.split()), TEXT_MAX)
        last = next((q for q in reversed(fold(rows).values()) if q["session"] == session), None)
        if last and last["status"] == "open" and last["text"] == masked:
            same.append(last)
            return []
        n = max([int(r["id"][1:]) for r in rows if r["ev"] == "open" and r["id"][1:].isdigit()] or [0]) + 1
        return [{"ev": "open", "id": f"r{n}", "ts": fleet.iso(t), "session": session, "handle": handle, "text": masked}]
    rows = _append(make)
    return (rows[0], True) if rows else (same[0], False)


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
        return [row]
    return _append(make)[0]


def age_min(q, now):
    t = fleet.parse(q.get("ts"))
    return int((now - t).total_seconds() // 60) if t else 0


def expired(q, now):
    return q["status"] == "open" and not q["links"] and age_min(q, now) >= TTL_MIN


def visible(q, now):
    """Open and not expired: what every count and list shows."""
    return q["status"] == "open" and not expired(q, now)


def summary(now=None, requests=None):
    """What a report or the dashboard shows: open count, the oldest's age, the open ones left unlinked too long, and
    how many expired (left out of the rest)."""
    now = now or fleet.utcnow()
    all_open = [q for q in (requests if requests is not None else fold(read())).values() if q["status"] == "open"]
    open_ = [q for q in all_open if visible(q, now)]
    stale = [q for q in open_ if not q["links"] and age_min(q, now) >= UNLINKED_MIN]
    return {"open": len(open_), "oldest_min": max([age_min(q, now) for q in open_] or [0]),
            "unlinked": [{"id": q["id"], "age_min": age_min(q, now), "text": q["text"][:80]} for q in stale],
            "expired": len(all_open) - len(open_)}


def chatty(text):
    t = (text or "").strip()
    return len(t) < CHATTY_MAX and (t.endswith(("?", "？")) or bool(ACK.fullmatch(t)))


def link_pr(to, prs):
    """(key, the cached PR) when the link is one PR and nothing else, else None."""
    to = (to or "").strip()
    refs = fleet.pr_refs(to)
    if len(refs) == 1 and not fleet.PR_REF_RE.sub("", to).removeprefix("https://").strip("/ "):
        return refs[0], prs.get(refs[0]) or {}
    return None


def card(to):
    return to[5:] if (to or "").startswith("card:") else None


def finished(to, prs, cards, seen, board_rows):
    """Evidence that a link's target is over, or a falsy value: a merged PR, a card whose worktree was seen live under
    that name and is gone now (cards, name -> worktree id, is None when the list cannot be trusted; a renamed card
    keeps its worktree, so it is not gone), a board row marked done. A PR closed unmerged is not over: the work it
    carried did not land, so the coordinator decides. A link in any other form is never over either."""
    to = (to or "").strip()
    if pr := link_pr(to, prs):
        return pr[1].get("state") == "MERGED" and f"{pr[0]} merged {pr[1].get('merged_at') or ''}".strip()
    if name := card(to):
        return (cards is not None and name in seen and seen[name]["id"] not in cards.values()
                and f"{to} removed (last seen {seen[name]['at']})")
    row = board_rows.get(to.removeprefix("board:")) or {}
    return row.get("status") == "done" and f"board:{row['id']} done {row.get('done_at') or ''}".strip()


def reconcile(requests, now, prs, sessions, board_rows, by_handle, seen, missing, orca_ok):
    """The rows the fleet tick appends: links to what a request's session started right after it, done for one whose
    links are all over, drop for an unlinked one whose terminal is gone.

    A card, board row or PR is linked only when exactly one request from its maker's terminal came in the
    LINK_WINDOW_MIN before it was made: a turn that answered two prompts cannot say which one the card was for. A
    chatty request (an acknowledgement, a short question) is never linked and does not count.
    prs is key -> {state, merged_at, created, session}; sessions are the fleet's (id -> id, name, parent, created);
    board_rows is board.json's rows; by_handle maps each live terminal to its session. seen (card name -> its
    worktree id and the last time it was live) is updated in place and keeps only cards some open request links to;
    missing (terminal -> when a trusted read first lacked it, and how many reads have) likewise, for the terminals of
    open unlinked requests. Nothing card- or terminal-based happens unless the Orca read just succeeded and listed
    sessions, so one failed read closes nothing.
    """
    stamp, out = fleet.iso(now), []
    cards = {s["name"]: s["id"] for s in sessions.values()} if orca_ok and sessions else None
    window = dt.timedelta(minutes=LINK_WINDOW_MIN)

    def start_of(at):
        t = fleet.parse(at) if at else None
        return fleet.iso(t - window) if t else None
    # What made a card or PR is a session, a board row a terminal: each request is filed under both.
    asked_by = collections.defaultdict(list)
    for q in requests.values():
        if chatty(q["text"]):
            continue
        asked_by[q.get("handle")].append(q)
        if sid := by_handle.get(q.get("handle")):
            asked_by[("session", sid)].append(q)
    made = [(f"card:{s['name']}", s.get("created"), ("session", s["parent"]))
            for s in sessions.values() if s.get("parent") and s["parent"] != s["id"]]
    made += [(k, p.get("created"), ("session", p.get("session"))) for k, p in prs.items()]
    for k, r in board_rows.items():
        up, start = board_rows.get(r.get("parent")) or {}, start_of(r.get("created_at"))
        rides = up.get("by") == r.get("by") and start and (up.get("created_at") or "") >= start  # on its parent's link
        if r.get("by") and not rides:
            made.append((f"board:{k}", r.get("created_at"), r["by"]))
    oldest = min((q["ts"] for q in requests.values() if q["status"] == "open" and q.get("ts")), default="~")
    new_links = collections.defaultdict(list)
    for to, at, who in made:
        if not (start := start_of(at)) or at < oldest:
            continue
        asked = [q for q in asked_by.get(who, ()) if start <= (q.get("ts") or "") <= at]
        if len(asked) == 1 and asked[0]["status"] == "open" and to not in asked[0]["links"]:
            new_links[asked[0]["id"]].append(to)
            out.append({"ev": "link", "id": asked[0]["id"], "ts": stamp, "to": to, "by": "auto"})
    linked, waiting = set(), set()
    for q in requests.values():
        if q["status"] != "open":
            continue
        links = q["links"] + new_links[q["id"]]
        for name in filter(None, map(card, links)):
            if cards is not None and name in cards:
                seen[name] = {"id": cards[name], "at": stamp}
        over = [finished(to, prs, cards, seen, board_rows) for to in links]
        if links and all(over):
            out.append({"ev": "done", "id": q["id"], "ts": stamp, "by": "auto",
                        "evidence": fleet.mask("auto: " + "; ".join(over), NOTE_MAX)})
        elif not links and cards is not None and (h := q.get("handle")) and h not in by_handle:
            m = missing.setdefault(h, {"since": stamp, "at": None, "reads": 0})
            if m["at"] != stamp:  # one read however many of its requests are open
                m.update(at=stamp, reads=m["reads"] + 1)
            waiting.add(h)
            if m["reads"] >= 2 and now - fleet.parse(m["since"]) >= dt.timedelta(minutes=GONE_MIN):
                out.append({"ev": "drop", "id": q["id"], "ts": stamp, "by": "auto", "reason": "auto: its terminal is gone"})
        else:
            linked |= set(filter(None, map(card, links)))
    for name in [n for n in seen if n not in linked]:
        del seen[name]
    if cards is not None:  # a terminal back in a trusted list, or with nothing left to drop, starts over
        for h in [h for h in missing if h not in waiting]:
            del missing[h]
    return out


def apply(rows):
    """Appends the rows reconcile returned, skipping any the file has overtaken since (closed, or already linked
    there). Returns (the rows written, the requests as they now stand)."""
    after = []

    def make(current):
        keep = []
        for r in rows:
            q = fold(current + keep).get(r["id"])  # ponytail: a fold per row; a tick writes a handful
            if q and q["status"] == "open" and not (r["ev"] == "link" and r["to"] in q["links"]):
                keep.append(r)
        after.append(fold(current + keep))
        return keep
    return _append(make), after[0]


def ago(minutes):
    return f"{minutes}m" if minutes < 60 else f"{minutes // 60}h{minutes % 60:02d}m" if minutes < 1440 else f"{minutes // 1440}d"


def line(s):
    """One line for a report; empty when nothing is open."""
    if not s["open"]:
        return ""
    flag = f" · 미연결 {UNLINKED_MIN}분+ {len(s['unlinked'])}건" if s["unlinked"] else ""
    return f"요청 {s['open']}건 열림 · 가장 오래된 것 {ago(s['oldest_min'])}{flag}"
