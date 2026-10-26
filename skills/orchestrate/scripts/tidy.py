"""What is over leaves the board on evidence, without anyone asking: the fleet tick runs run() after each collect, and
the owner closes the rest from the dashboard or `orch board done`. Every close says when and on what it was judged.

An item is closed only on evidence this file can check: a PR merged or closed (prs.json), a session gone from Orca's
list, or the owner's word. A text names a PR only by its full owner/repo#N (a bare #N could be any repository's),
and a card only by its slug-shaped name (two hyphenated words or more) standing alone in the text. Anything less
certain is not closed: assess() calls it `check`, and the report shows it as 확인 필요 instead of 진행 중 or 보류.
Nothing the owner or another person acts on is closed by the tick: a doing item waiting on a person or the owner's,
a decision, a row the report lists as the owner's turn or another person's wait.

One append-only file, <fleet state>/tidy.jsonl (0600), one JSON object a line:
  {"ev": "done" | "reopen", "id", "ts", "by": "auto" | "owner", "rule", "evidence", "label"}
ids: doing:<k> later:<k> todo:<k> (k: a digest of the item's words, so rewording it makes a new item), row:<board row
id>, card:<session id> (a fleet row), ext:<inbox key>, req:<request id>. The state of an id is its last line. A
reopen keeps the evidence it undid, and the tick does not close that id again on the same evidence.
"""
import fcntl, re

import fleet

POST_MERGE = re.compile(r"릴리스|배포|apply|deploy|release|rollout|롤아웃", re.I)
MERGE_WORD = re.compile(r"머지|merge", re.I)
SLUG = re.compile(r"[a-z0-9][a-z0-9._]*(?:-[a-z0-9._]+)+")
CARD_KINDS = ("task", "standalone")
STALE_H = 8  # a moving board row this long without a word, and nothing live to vouch for it, is 확인 필요
OWNER_NAMES = ("오너", "owner")


def path():
    return fleet.state_dir() / "tidy.jsonl"


def key(*parts):
    return fleet.digest(*parts)[:8]


def item_id(kind, item):
    return f"{kind}:" + (key(item["title"], item["trigger"]) if kind == "later" else key(item["title"] if kind == "doing" else item))


def latest():
    """id -> its last line."""
    out = {}
    for r in fleet.read_lines(path()):
        if isinstance(r, dict) and isinstance(r.get("id"), str) and r.get("ev") in ("done", "reopen"):
            out[r["id"]] = r
    return out


def closed():
    return {k: r for k, r in latest().items() if r["ev"] == "done"}


def is_owner(who, me):
    w = (who or "").strip().lstrip("@").lower()
    return w in OWNER_NAMES or bool(me) and w == me.lower()


# ---------------------------------------------------------------- evidence

def cards_of(state):
    """Card sessions by id, each with its newest PR key (or None)."""
    pr_of = {}
    for p in (state or {}).get("prs") or []:  # newest first
        if p.get("session"):
            pr_of.setdefault(p["session"], p.get("key"))
    return {s["id"]: {**s, "pr": pr_of.get(s["id"]) or (s.get("report") or {}).get("pr")}
            for s in (state or {}).get("sessions") or [] if s.get("kind") in CARD_KINDS}


def named(text, cards):
    """The one card the text names, or None (none, or more than one)."""
    hits = [c for c in cards.values() if SLUG.fullmatch(c.get("name") or "")
            and re.search(rf"(?<![\w-]){re.escape(c['name'])}(?![\w-])", text or "")]
    return hits[0] if len(hits) == 1 else None


def pr_line(k, p):
    return f"{k} " + ("머지 " + (p.get("merge_sha") or "")[:7] if p.get("state") == "MERGED" else "닫힘")


def refs_over(keys, prs, closed_ok=False):
    """The evidence that every PR in keys is merged (or closed, with closed_ok), else None. No keys: None."""
    keys = [k for k in dict.fromkeys(keys) if k]
    ps = [prs.get(k) or {} for k in keys]
    if not keys or not all(p.get("state") == "MERGED" or closed_ok and p.get("state") == "CLOSED" for p in ps):
        return None
    return " · ".join(pr_line(k, p).strip() for k, p in zip(keys, ps))


def assess(store, state, prs, me=None, now=None):
    """id -> {verdict: done | check, rule, evidence, label} for what the evidence settles now. state.json's sessions
    and PR list, prs.json's details (key -> detail), board.json's store."""
    now = now or fleet.utcnow()
    cards = cards_of(state)
    live = {s["id"] for s in cards.values() if s.get("phase") in ("working", "waiting")}
    out = {}

    def put(iid, verdict, rule, evidence, label):
        out[iid] = {"verdict": verdict, "rule": rule, "evidence": fleet.mask(evidence, 200), "label": fleet.mask(label, 120)}

    for d in store.get("doing") or []:
        if d.get("waiting") or is_owner(d.get("owner"), me):
            continue
        text = f"{d['title']} {d.get('next') or ''}"
        card = named(text, cards)
        ev = refs_over(fleet.pr_refs(text) + [card and card["pr"]], prs)
        if not ev:
            continue
        if card and card["id"] in live:
            put(item_id("doing", d), "check", "pr_merged_card_busy", f"{ev} · 카드 {card['name']} 작업 중", d["title"])
        elif POST_MERGE.search(d.get("next") or ""):
            put(item_id("doing", d), "check", "pr_merged_steps_left", f"{ev} · 남은 단계: {d['next']}", d["title"])
        else:
            put(item_id("doing", d), "done", "pr_merged", ev + (f" (카드 {card['name']})" if card else ""), d["title"])
    for l in store.get("later") or []:
        if not MERGE_WORD.search(l["trigger"]):
            continue
        card = named(l["trigger"], cards)
        if ev := refs_over(fleet.pr_refs(l["trigger"]) + [card and card["pr"]], prs):
            put(item_id("later", l), "check", "trigger_met", f"조건 충족: {ev}", l["title"])
    for t in store.get("owner_todo") or []:
        if ev := refs_over(fleet.pr_refs(t), prs, closed_ok=True):
            put(item_id("todo", t), "done", "pr_over", ev, t)
    for rid, r in (store.get("rows") or {}).items():
        at = fleet.parse(r.get("reported_at"))
        card = named(f"{rid} {r.get('title') or ''}", cards)
        if (r.get("status") == "moving" and not r.get("pr") and not (card and (card["pr"] or card["id"] in live))
                and at and (now - at).total_seconds() > STALE_H * 3600):
            put(f"row:{rid}", "check", "stale", f"{int((now - at).total_seconds() // 3600)}h 갱신 없음 · 근거 PR·작업 중 세션 없음",
                r.get("title") or rid)
    for c in cards.values():
        p = prs.get(c["pr"]) or {}
        if c.get("phase") in ("offline", "open") and p.get("state") == "CLOSED":
            put(f"card:{c['id']}", "done", "pr_closed", f"{pr_line(c['pr'], p)} · 세션 {c.get('phase')}",
                c.get("task_title") or c.get("name") or c["id"])
    names = {c["id"] for c in cards.values()} | {s["id"] for s in (state or {}).get("sessions") or []}
    for it in fleet.external_items({}):
        ev = refs_over(fleet.pr_refs(" ".join(filter(None, (it.get("title"), it.get("url"), it.get("command"))))), prs, closed_ok=True)
        if ev:
            put(it["key"], "done", "pr_over", ev, it["title"])
        elif it.get("session") and names and it["session"] not in names:
            put(it["key"], "done", "session_gone", f"세션 {it['session']} 이 Orca 목록에 없음", it["title"])
    return out


def requests_over(rows_here, rows_marked, cards_done, prs, cards):
    """req:<id> -> evidence for each open request every link of which is over and one of which was closed here: a
    board row or card this ledger closed. Owner_requests' own reconcile in the fleet tick closes the rest (a merged
    PR, a board row marked done, a card gone); it cannot see what only this ledger closed."""
    import owner_requests  # imports fleet; kept local like board's
    by_name = {c.get("name"): c["id"] for c in cards.values()}
    out = {}
    for q in owner_requests.fold(owner_requests.read()).values():
        if q["status"] != "open" or not q["links"]:
            continue
        ev, here = [], False
        for to in q["links"]:
            to = (to or "").strip()
            refs = fleet.pr_refs(to)
            if to.startswith("board:") and to[6:] in rows_here | rows_marked:
                ev.append(f"{to} 완료")
                here |= to[6:] in rows_here
            elif to.startswith("card:") and by_name.get(to[5:]) in cards_done:
                ev.append(f"{to} 정리됨")
                here = True
            elif len(refs) == 1 and (e := refs_over(refs, prs)):
                ev.append(e)
            else:
                break
        else:
            if here:
                out[f"req:{q['id']}"] = {"verdict": "done", "rule": "links_over", "evidence": "; ".join(ev),
                                         "label": q["text"]}
    return out


# ---------------------------------------------------------------- writes

def append(rows):
    path().parent.mkdir(parents=True, exist_ok=True)
    fleet.rotate(path())
    for r in rows:
        fleet.append_line(path(), r)


def write(ids, undo=False, why=None, can=None, now=None, by="owner"):
    """Close (or, with undo, reopen) each id. Returns (written, [(id, reason)]). can: id -> label of what may be
    closed now (board.render()'s open list); an id not in it, or already in the asked state, is not written."""
    ts, written, refused = fleet.iso(now or fleet.utcnow()), [], []
    path().parent.mkdir(parents=True, exist_ok=True)
    with (fleet.state_dir() / "tidy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        have = latest()
        rows = []
        for iid in dict.fromkeys(ids):
            cur = have.get(iid) or {}
            if undo and cur.get("ev") != "done":
                refused.append((iid, "not closed"))
            elif not undo and cur.get("ev") == "done":
                refused.append((iid, "already closed"))
            elif not undo and iid not in (can or {}):
                refused.append((iid, "no open board item, row or card by that id"))
            else:
                rows.append({"ev": "reopen" if undo else "done", "id": iid, "ts": ts, "by": by,
                             "rule": "manual", "evidence": cur.get("evidence") if undo else fleet.mask(why or "오너가 완료 처리", 200),
                             "label": cur.get("label") if undo else fleet.mask((can or {}).get(iid), 120)})
                written.append(iid)
        append(rows)
    return written, refused


def run(now=None, dry=False):
    """One tidy pass over the files the collector just wrote. Returns the lines it wrote (dry: would write)."""
    import owner_requests
    now = now or fleet.utcnow()
    sd = fleet.state_dir()
    state = fleet.read_json(sd / "state.json", None)
    if not state or not state.get("sessions"):  # an empty list is more likely a bad read than every session gone
        return []
    store = fleet.read_json(sd / "board.json", {}) or {}
    prs_file = fleet.read_json(sd / "prs.json", {}) or {}
    prs = {k: v["detail"] for k, v in (prs_file.get("prs") or {}).items() if v.get("detail")}
    found = {k: v for k, v in assess(store, state, prs, prs_file.get("me"), now).items() if v["verdict"] == "done"}
    with (sd / "tidy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        have = latest()
        shut = {k for k, r in have.items() if r["ev"] == "done"} | set(found)
        marked = {k for k, r in (store.get("rows") or {}).items() if r.get("status") == "done"}
        found |= requests_over({k[4:] for k in shut if k.startswith("row:")}, marked,
                               {k[5:] for k in shut if k.startswith("card:")}, prs, cards_of(state))
        ts, rows = fleet.iso(now), []
        for iid, v in found.items():
            cur = have.get(iid) or {}
            if cur.get("ev") == "done" or cur.get("ev") == "reopen" and cur.get("evidence") == v["evidence"]:
                continue
            rows.append({"ev": "done", "id": iid, "ts": ts, "by": "auto", "rule": v["rule"], "evidence": v["evidence"],
                         "label": v["label"]})
        if dry:
            return rows
        for r in list(rows):
            if r["id"].startswith("ext:"):
                fleet.inbox_resolve(r["id"][4:])
            elif r["id"].startswith("req:"):
                try:
                    owner_requests.close(r["id"][4:], "done", "auto: " + r["evidence"])
                except ValueError:  # the fleet tick closed it since the fold: not this ledger's close
                    rows.remove(r)
        append(rows)
    return rows


def timeline(keep=200):
    """The tidy lines as timeline events, newest last."""
    return [{"at": r.get("ts"), "kind": f"tidy_{r['ev']}" + ("_auto" if r.get("by") == "auto" else ""),
             "text": f"{r.get('label') or r['id']} · {r.get('rule')}: {r.get('evidence') or ''}".strip(" :")}
            for r in fleet.read_lines(path(), keep) if isinstance(r, dict) and r.get("ev") in ("done", "reopen")]
