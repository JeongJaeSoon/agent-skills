"""What the owner has checked and no longer needs on screen: finished Work rows, finished brief rows, and their own
requests. `orch-dash ack` writes here and the dashboard's buttons call the same functions, so the CLI is the record.

An ack is the owner's view, not the work's state: an acked request is still open for `orch req`, an acked stage row
is still in stages.json. Only what is over can be acked (a Work row with `done_at`, a brief row that is ✅, and
every one of its sub-task rows too); a row in progress, waiting on someone or held (a closed PR's row too) is refused, so
acking never hides what still needs someone. Requests are the owner's own: every one still shown (open, not expired) can be acked.

One append-only file, <fleet state>/acks.jsonl (0600), one JSON object a line:
  {"ev": "ack" | "unack", "id": "req:r7" | "stage:<row id>" | "board:<brief row key>", "ts"}
The set of acked ids is the fold of its lines.
"""
import datetime as dt, fcntl, re

import fleet

KINDS = ("req", "stage", "board")


def path():
    return fleet.state_dir() / "acks.jsonl"


def acked():
    """id -> when it was acked."""
    out = {}
    for r in fleet.read_lines(path()):
        if not isinstance(r, dict) or not isinstance(r.get("id"), str):
            continue
        if r.get("ev") == "ack":
            out[r["id"]] = r.get("ts")
        elif r.get("ev") == "unack":
            out.pop(r["id"], None)
    return out


def kind(aid):
    return aid.split(":", 1)[0]


def stage_done(rows):
    """stage:<id> -> {label, at} for the ackable Work rows: done, with every row under them done too."""
    import stages
    out = {}
    for k, r in rows.items():
        family = [rows[i] for i in stages.with_descendants(rows, {k})]
        if all(x.get("done_at") for x in family):
            out[f"stage:{k}"] = {"label": r.get("title") or k, "at": max(x["done_at"] for x in family)}
    return out


def mark_stages(table):
    """The Work table with each row's `ack`: "acked", "ackable" or None, and `hidden` when it or a row above it is acked
    and still done."""
    rows = {r["id"]: r for r in table["rows"]}
    have, can = acked(), stage_done(rows)

    def hidden(r):
        return r is not None and ((aid := f"stage:{r['id']}") in have and aid in can or hidden(rows.get(r.get("parent"))))
    for r in table["rows"]:
        aid = f"stage:{r['id']}"
        r.update(ack="acked" if aid in have else "ackable" if aid in can else None, hidden=hidden(r))
    return table


def ackable(now=None, kinds=KINDS):
    """id -> {label, at}: what of these kinds may be acked right now, acked or not. at is when it finished (a request:
    when it was asked), for --done-before. Only a board: id costs a brief render."""
    import board, owner_requests, stages  # board imports this module
    out = {}
    if "req" in kinds:
        out.update({f"req:{q['id']}": {"label": q["text"], "at": q.get("ts")}
                    for q in owner_requests.fold(owner_requests.read()).values()
                    if owner_requests.visible(q, now or fleet.utcnow())})
    if "stage" in kinds:
        out.update(stage_done(stages.read()["rows"]))
    if "board" in kinds:
        try:
            done = board.render(now=now, fill=True)["done"]
        except (OSError, ValueError):  # no board yet, or one the renderer refuses: nothing there to ack
            done = []
        out.update({d["id"]: {"label": d["label"], "at": d["at"]} for d in done})
    return out


def why_not(aid):
    if kind(aid) not in KINDS or ":" not in aid:
        return f"an id is req:rN, stage:<row> or board:<row> (got {aid})"
    return {"req": "no open request by that id",
            "stage": "no such row, or it or a row under it is not done",
            "board": "no such brief row, or it or a row under it is still in progress, waiting or held"}[kind(aid)]


def write(ids, undo=False, now=None, can=None):
    """Ack (or, with undo, un-ack) each id. Returns (written, [(id, reason)]): an id that cannot be acked is refused
    and the rest still go in; one already in the asked state is neither. can: ackable()'s answer, when the caller has it."""
    ids = list(dict.fromkeys("req:" + i if re.fullmatch(r"r\d+", i) else i for i in ids))
    ts = fleet.iso(now or fleet.utcnow())
    if not undo and can is None:
        can = ackable(now, {kind(i) for i in ids})
    path().parent.mkdir(parents=True, exist_ok=True)
    written, refused = [], []
    with (fleet.state_dir() / "acks.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        have = acked()
        for aid in ids:
            if undo and aid not in have:
                refused.append((aid, "not acked"))
            elif not undo and aid not in can:
                refused.append((aid, why_not(aid)))
            elif undo or aid not in have:
                fleet.append_line(path(), {"ev": "unack" if undo else "ack", "id": aid, "ts": ts})
                written.append(aid)
    return written, refused


def older_than(age, kinds=KINDS, now=None):
    """(ids not acked yet that finished, a request: was asked, longer ago than age ("24h", "7d", "90m"), ackable())."""
    m = re.fullmatch(r"(\d+)([mhd])", age or "")
    if not m:
        raise ValueError(f"an age is N followed by m, h or d (got {age!r})")
    now = now or fleet.utcnow()
    cutoff = now - dt.timedelta(minutes=int(m.group(1)) * {"m": 1, "h": 60, "d": 1440}[m.group(2)])
    have, can = acked(), ackable(now, kinds)
    return [aid for aid, v in can.items() if aid not in have and (t := fleet.parse(v["at"])) and t <= cutoff], can
