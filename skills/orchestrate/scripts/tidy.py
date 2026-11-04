"""What is over leaves the board on evidence, without anyone asking: the fleet tick runs run() after each collect, and
the owner closes the rest from the dashboard or `orch board done`. Every close says when and on what it was judged.

An item is closed only on evidence this file can check: a PR merged or closed (prs.json, or the merged state Orca
observed on a card's linked PR), a session gone from Orca's list, or the owner's word. A text names a PR only by its
full owner/repo#N (a bare #N could be any repository's), and a card only by a slug-shaped key (two hyphenated words
or more, case ignored) standing alone in the text: its display name or its worktree directory, never a bare ticket ID (keys_of). Anything less
certain is not closed: assess() calls it `check`, and the report shows it as 확인 필요 instead of 진행 중 or 보류.
Nothing the owner or another person acts on is closed by the tick: a doing item waiting on a person or the owner's,
a decision, an inbox item of a type the owner acts on (OWNER_TYPES: a command to run, a decision, a login...), a row
the report lists as the owner's turn or another person's wait.

Closes live in agent-skills.db (store.py), in one transaction with their event: a board item's in its `item` row (b<N>,
which rewording keeps), row:<board row id>, card:<session id> (a fleet row) and ext:<inbox key> in `closure`, as do old
ids that had no item left at the import (doing:<k> and the like, req:<id> from the removed owner-request ledger; never
reopened). An old doing:/later:/do:/todo:<k> id still finds its item through `alias`. A reopen keeps the evidence it
undid, and the tick does not close that id again on the same evidence. Judging happens outside the transaction; inside
it each close checks that the item is still as judged, and that nothing reopened it. tidy does not write
inbox-external.jsonl: fleet.external_items reads the file and `closure` together, with `ext_mark` saying which lines came
before the close or reopen.
"""
import json, pathlib, re

import fleet
import store

POST_MERGE = re.compile(r"릴리스|배포|apply|deploy|release|rollout|롤아웃", re.I)
MERGE_WORD = re.compile(r"머지|merge", re.I)
SLUG = re.compile(r"[a-z0-9][a-z0-9._]*(?:-[a-z0-9._]+)+")
TICKET_ONLY = re.compile(r"[a-z0-9][a-z0-9_]*-\d+")  # Jira keys may hold "_", Linear team keys may start with a digit
CARD_KINDS = ("task", "standalone")
OWNER_TYPES = fleet.STICKY_TYPES + ("decision",)
STALE_H = 8  # a moving board row this long without a word, and nothing live to vouch for it, is 확인 필요


def latest():
    """id -> its last close or reopen ({ev, id, ts, by, rule, evidence, label}); {} before `orch db import`."""
    try:
        return store.latest()
    except store.NotMigrated:
        return {}


def closed():
    return {k: r for k, r in latest().items() if r["ev"] == "done"}


# ---------------------------------------------------------------- evidence

def cards_of(state, details=None):
    """Card sessions by id, each with its current PR key (fleet.current_pr) or None, and newest_pr: the newest PR it
    is linked to whatever its state, the evidence a doing item about a busy card is checked on. details: prs.json's."""
    by_session = fleet.prs_by_session((state or {}).get("prs"))
    return {s["id"]: {**s, "pr": fleet.current_pr(s, by_session, details),
                      "newest_pr": next(iter(fleet.pr_candidates(s, by_session)[0]), None)}
            for s in (state or {}).get("sessions") or [] if s.get("kind") in CARD_KINDS}


def keys_of(card):
    """The lowercase slug-shaped names a text may call a card by: its display name and its worktree directory name,
    unless that name is a bare ticket ID (workers are named `abc-1`). Nor the branch's last segment: a follow-up item
    cites its parent's ticket ("ABC-1 follow-up"), and a branch tail is often a generic word pair (fix-tests), so
    either would close the wrong item."""
    raw = [(card.get("name") or "").strip(), pathlib.PurePath(card.get("path") or "").name]
    return {k.lower() for k in raw if k and SLUG.fullmatch(k.lower()) and not TICKET_ONLY.fullmatch(k.lower())}


def named(text, cards):
    """The one card the text names, or None (none, or more than one). Case is ignored: a ticket ID is upper case in
    prose and lower case in a worktree name. The owner field is a person, never a card key."""
    low = (text or "").lower()
    hits = [c for c in cards.values()
            if any(re.search(rf"(?<![\w-]){re.escape(k)}(?![\w-])", low) for k in keys_of(c))]
    return hits[0] if len(hits) == 1 else None


def merged(p):
    return p.get("state") == "MERGED" or bool(p.get("merged_at"))


def orca_merged(cards, keys):
    """PR details for keys that only Orca's observed linkedPR says are merged (prs.json may not hold them)."""
    out = {}
    for c in cards.values():
        lp = c.get("linked_pr") if isinstance(c.get("linked_pr"), dict) else {}
        if lp.get("key") and lp["key"] in keys and str(lp.get("state") or "").lower() == "merged":
            out[lp["key"]] = {"state": "MERGED"}
    return out


def pr_line(k, p):
    return f"{k} " + ("머지 " + (p.get("merge_sha") or "")[:7] if merged(p) else "닫힘")


def refs_over(keys, prs, closed_ok=False):
    """The evidence that every PR in keys is merged (or closed, with closed_ok), else None. No keys: None."""
    keys = [k for k in dict.fromkeys(keys) if k]
    ps = [prs.get(k) or {} for k in keys]
    if not keys or not all(merged(p) or closed_ok and p.get("state") == "CLOSED" for p in ps):
        return None
    return " · ".join(pr_line(k, p).strip() for k, p in zip(keys, ps))


def trigger_met(text, cards, prs):
    """The evidence that a held item's trigger (a to-do's --after) is met, else None: it says merge, and every PR it
    names, or the PR of the card it names, has merged."""
    if not MERGE_WORD.search(text):
        return None
    card = named(text, cards)
    return refs_over(fleet.pr_refs(text) + [card and card["pr"]], prs)


def assess(store, state, prs, me=None, now=None):
    """id -> {verdict: done | check, rule, evidence, label, and what write() checks again: the item's `at` (updated_at),
    an ext: item's `mark`} for what the evidence settles now. state.json's sessions and PR list, prs.json's details
    (key -> detail), the open board."""
    import board  # imports this module, so not at the top
    now = now or fleet.utcnow()
    cards = cards_of(state, prs)
    prs = {**orca_merged(cards, {k for c in cards.values() for k in (c["pr"], c["newest_pr"])}), **prs}
    live = {s["id"] for s in cards.values() if s.get("phase") in ("working", "waiting")}
    # orch patrol's D: doing items that named no PR or card for a day. Shown as ❔, never closed on it.
    noref = set((fleet.read_json(fleet.state_dir().parent / "patrol-verdicts.json", {}) or {}).get("doing_noref") or ())
    out = {}

    def put(iid, verdict, rule, evidence, label, **seen):
        out[iid] = {"verdict": verdict, "rule": rule, "evidence": fleet.mask(evidence), "label": fleet.mask(label), **seen}

    for d in store.get("doing") or []:
        if d.get("waiting") or board.is_owner(d.get("owner"), me):
            continue
        text = f"{d['title']} {d.get('next') or ''}"
        card = named(text, cards)
        # A busy card's merged PR is not its current PR, but a line about the card may still be about that PR: ❔.
        ev = refs_over(fleet.pr_refs(text) + [card and (card["pr"] or card["newest_pr"])], prs)
        if not ev:
            if d["id"] in noref:
                put(d["id"], "check", "no_ref", "PR·카드 참조 없이 24h+ · 참조를 달거나 닫기", d["title"])
            continue
        if card and card["id"] in live:
            put(d["id"], "check", "pr_merged_card_busy", f"{ev} · 카드 {card['name']} 작업 중", d["title"])
        elif POST_MERGE.search(d.get("next") or ""):
            put(d["id"], "check", "pr_merged_steps_left", f"{ev} · 남은 단계: {d['next']}", d["title"])
        else:
            put(d["id"], "done", "pr_merged", ev + (f" (카드 {card['name']})" if card else ""), d["title"], item=d)
    for l in store.get("later") or []:
        if ev := trigger_met(l["trigger"], cards, prs):
            put(l["id"], "check", "trigger_met", f"조건 충족: {ev}", l["title"])
    for t in store.get("owner_todo") or []:
        if ev := refs_over(fleet.pr_refs(t["title"]), prs, closed_ok=True):
            put(t["id"], "done", "pr_over", ev, t["title"], item=t)
    for rid, r in (store.get("rows") or {}).items():
        at = fleet.parse(r.get("reported_at"))
        card = cards.get(r.get("worktree")) or named(f"{rid} {r.get('title') or ''}", cards)  # `--worktree` first
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
    for it in fleet.external_marked():
        if it["type"] in OWNER_TYPES:
            continue
        ev = refs_over(fleet.pr_refs(" ".join(filter(None, (it.get("title"), it.get("url"), it.get("command"))))), prs, closed_ok=True)
        if ev:
            put(it["key"], "done", "pr_over", ev, it["title"], mark=it["mark"])
        elif it.get("session") and names and it["session"] not in names:
            put(it["key"], "done", "session_gone", f"세션 {it['session']} 이 Orca 목록에 없음", it["title"], mark=it["mark"])
    return out


# ---------------------------------------------------------------- writes

def fits(row):
    """Whether a close stays under 4 KiB as a tidy line: `orch db export --legacy` must be able to write it for older
    code, whose fleet.append_line refuses more when its file rotates. One that does not is left unwritten, never cut."""
    return len(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode()) < 4064


def last_mark(key):
    """The mark of the last inbox line of key (without its ext: prefix), or None."""
    rows = fleet.inbox_lines().get(key) or []
    return rows[-1][1] if rows else None


def last_add(key):
    """The inbox line that added key (without its ext: prefix), or None."""
    return next((row for row, _ in reversed(fleet.inbox_lines().get(key) or []) if row.get("op") == "add"), None)


def no_reopen(iid):
    """Why iid cannot be reopened, or None."""
    if iid.startswith("req:"):
        return "the owner-request ledger was removed"
    if iid.startswith(store.ORPHAN):
        return "its board item left the board before stable ids"
    if iid.startswith("ext:") and not last_add(iid[4:]):
        return "its inbox line is gone"
    return None


def can_reopen(iid):
    return no_reopen(iid) is None


def stands(cur, iid, mark, closures):
    """Whether cur is a close that still holds: an inbox key added again after it, at another mark, is open again."""
    return cur.get("ev") == "done" and not (iid.startswith("ext:") and (closures.get(iid[4:]) or {}).get("ext_mark") != mark)


def undone(cur, v):
    """The owner reopened this id on the evidence v brings again."""
    return bool(cur) and cur.get("ev") == "reopen" and cur.get("evidence") == v["evidence"]


def write(ids, undo=False, why=None, can=None, now=None, by="owner"):
    """Close (or, with undo, reopen) each id, in one transaction. Returns (written, [(id, reason)]). can: id -> label of what
    may be closed now (board.render()'s open list); an id not in it, or already in the asked state, is not written. An
    old content-hash id means its item."""
    ts, written, refused = fleet.iso(now or fleet.utcnow()), [], []
    with store.tx() as db:
        have, closures = store.latest(db), store.ext_closures(db)
        for asked in dict.fromkeys(ids):
            iid = store.resolve(db, asked)
            cur = have.get(iid) or {}
            mark = last_mark(iid[4:]) if iid.startswith("ext:") else None
            if undo and cur.get("ev") != "done":
                refused.append((asked, "not closed"))
            elif not undo and stands(cur, iid, mark, closures):
                refused.append((asked, "already closed"))
            elif not undo and iid not in (can or {}):
                refused.append((asked, "no open board item, row or card by that id"))
            elif undo:
                if why_not := no_reopen(iid):
                    refused.append((asked, why_not))
                else:
                    store.reopen_unit(db, iid, ts, by, mark=mark)
                    have[iid] = {**cur, "ev": "reopen"}
                    written.append(iid)
            else:
                row = {"ev": "done", "id": iid, "ts": ts, "by": by, "rule": "manual",
                       "evidence": fleet.mask(why or "오너가 완료 처리"), "label": fleet.mask((can or {}).get(iid))}
                if fits(row):
                    store.close_unit(db, row, mark)
                    have[iid] = row
                    written.append(iid)
                else:
                    refused.append((asked, "line over 4 KiB: shorten --why"))
    return written, refused


def run(now=None, dry=False):
    """One tidy pass over the files the collector just wrote. Returns the closes it made (dry: would make)."""
    now = now or fleet.utcnow()
    sd = fleet.state_dir()
    state = fleet.read_json(sd / "state.json", None)
    if not state or not state.get("sessions"):  # an empty list is more likely a bad read than every session gone
        return []
    try:
        board_now = store.board()
    except store.NotMigrated:
        return []
    prs_file = fleet.read_json(sd / "prs.json", {}) or {}
    prs = {k: v["detail"] for k, v in (prs_file.get("prs") or {}).items() if v.get("detail")}
    found = {k: v for k, v in assess(board_now, state, prs, prs_file.get("me"), now).items() if v["verdict"] == "done"}
    ts, rows = fleet.iso(now), []

    def wanted(db):
        """The closes that still stand in this transaction: nothing reopened or closed the id, or changed the item."""
        have, closures = store.latest(db), store.ext_closures(db)
        for iid, v in found.items():
            cur = have.get(iid) or {}
            row = {"ev": "done", "id": iid, "ts": ts, "by": "auto", "rule": v["rule"], "evidence": v["evidence"], "label": v["label"]}
            if (not stands(cur, iid, v.get("mark"), closures) and not undone(cur, v) and fits(row)
                    and (not v.get("item") or store.item_same(db, v["item"]))):
                yield row, v.get("mark")
    if dry:
        with store.read() as db:
            return [row for row, _ in wanted(db)]
    with store.tx() as db:
        for row, mark in wanted(db):
            store.close_unit(db, row, mark)
            rows.append(row)
    return rows


def timeline(keep=200):
    """Closes and reopens as timeline events, newest last."""
    try:
        got = store.closing_events(keep)
    except store.NotMigrated:
        return []
    return [{"at": r["at"], "kind": f"tidy_{r['ev']}" + ("_auto" if r["by"] == "auto" else ""),
             "text": f"{r['label'] or r['id']} · {r['rule']}: {r['evidence'] or ''}".strip(" :")} for r in got]
