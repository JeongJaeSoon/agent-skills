"""The owner's requests to a coordinator, kept so none is lost when many arrive at once.

hooks/request_log.py appends each human prompt an orchestrator or lead receives; the coordinator routes each one to
where the work happens (`link`) and closes it (`done` with evidence, or `drop` with a reason). Requests are the small,
conversational ones (a check, a reply, a merge, a look at something): a request that grows into tracker-sized work is
linked to its ticket and closed here, and nothing here writes to the tracker.

One append-only file, <fleet state>/requests.jsonl (0600), one JSON object a line:
  {"ev": "open", "id": "r7", "ts", "session", "handle", "text"}   text masked and capped
  {"ev": "link", "id", "ts", "to"} | {"ev": "done", "id", "ts", "evidence"} | {"ev": "drop", "id", "ts", "reason"}
A request's state is the fold of its lines; a later link adds to the earlier ones.
"""
import fcntl, json, os

import fleet

TEXT_MAX = 200
NOTE_MAX = 300
UNLINKED_MIN = 30  # an open request with no link for this long is flagged


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
    """Runs make(rows) under the file lock and appends the row it returns, so two prompts never share an id."""
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        row = make(_rows(f))
        f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return row


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
    t = now or fleet.utcnow()

    def make(rows):
        n = max([int(r["id"][1:]) for r in rows if r["ev"] == "open" and r["id"][1:].isdigit()] or [0]) + 1
        one = " ".join(text.split())
        return {"ev": "open", "id": f"r{n}", "ts": fleet.iso(t), "session": session, "handle": handle,
                "text": fleet.mask(one, TEXT_MAX)}
    return _append(make)


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


def summary(now=None, requests=None):
    """What a report or the dashboard shows: open count, the oldest's age, and the open ones left unlinked too long."""
    now = now or fleet.utcnow()
    open_ = [q for q in (requests if requests is not None else fold(read())).values() if q["status"] == "open"]
    stale = [q for q in open_ if not q["links"] and age_min(q, now) >= UNLINKED_MIN]
    return {"open": len(open_), "oldest_min": max([age_min(q, now) for q in open_] or [0]),
            "unlinked": [{"id": q["id"], "age_min": age_min(q, now), "text": q["text"][:80]} for q in stale]}


def ago(minutes):
    return f"{minutes}m" if minutes < 60 else f"{minutes // 60}h{minutes % 60:02d}m" if minutes < 1440 else f"{minutes // 1440}d"


def line(s):
    """One line for a report; empty when nothing is open."""
    if not s["open"]:
        return ""
    flag = f" · 미연결 {UNLINKED_MIN}분+: " + " ".join(q["id"] for q in s["unlinked"]) if s["unlinked"] else ""
    return f"요청 {s['open']}건 열림 · 가장 오래된 것 {ago(s['oldest_min'])}{flag}"
