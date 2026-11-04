"""`orch migrate`: move the fleet state written before cards ran on Orca's own fields to the Orca-first model.

Before, a card's progress was a hand-written board doing line; now the card's Orca worktree (workspaceStatus,
linkedPR, its Task) is the record and the doing list keeps only waits no card covers. Nothing is deleted:
  migrate                 dry-run. Lists the doing lines that name exactly one card (tidy.named, or an owner field that
                          is one card's whole key) and would close, the ones the next tidy tick closes anyway, the ones
                          kept for a step left after the merge, board leads, and open decisions
  migrate --apply         backs up agent-skills.db through `orch db backup`'s copy to <state>/backup-orca-v2-<ts>/
                          (0700 dir, 0600 files), writes migrate.json there, then closes each listed doing line
                          (by "migrate", rule "moved_to_orca") in one transaction. A second run finds nothing left
                          and writes nothing. It also prints the same undo as an `orch board reopen` line.
  migrate --restore DIR   reopens each id DIR/migrate.json closed that is still closed by migrate.
A doing line waiting on someone, or the owner's, or one the owner reopened, is left alone. Leads and decisions are
only reported: a running lead started before this version still writes its board lead, and the fleet shows both.
"""
import json, os, pathlib, sys

import board, fleet, tidy
import store as store_db  # `store` is the board dict below

RULE = "moved_to_orca"


def plan(now=None):
    """What --apply would do: {close: [{id, label, evidence}], tick: [...], leads: [...], decisions: [...]}."""
    sd = fleet.state_dir()
    state = fleet.read_json(sd / "state.json", None) or {}
    store = board.read()
    prs_file = fleet.read_json(sd / "prs.json", {}) or {}
    me = prs_file.get("me")
    prs = {k: v["detail"] for k, v in (prs_file.get("prs") or {}).items() if v.get("detail")}
    have = tidy.latest()
    verdicts = tidy.assess(store, state, prs, me, now)
    cards = tidy.cards_of(state)
    close, tick, steps = [], [], []
    for d in store["doing"]:
        iid = d["id"]
        cur = have.get(iid) or {}
        if cur.get("ev") == "done" or cur.get("ev") == "reopen" or d.get("waiting") or board.is_owner(d.get("owner"), me):
            continue
        v = verdicts.get(iid) or {}
        if v.get("verdict") == "done":
            tick.append({"id": iid, "label": d["title"], "evidence": v["evidence"]})
            continue
        # A step left after the merge (a release, a deploy) lives only in this line, not on the card.
        if v or tidy.POST_MERGE.search(d.get("next") or ""):
            steps.append({"id": iid, "label": d["title"], "evidence": v.get("evidence") or f"남은 단계: {d.get('next')}"})
            continue
        # Older orchestrators put the card's name in --owner: the whole field must be one card's key.
        owner = (d.get("owner") or "").strip().lower()
        by_owner = [c for c in cards.values() if owner and owner in tidy.keys_of(c)]
        if card := tidy.named(f"{d['title']} {d.get('next') or ''}", cards) or (by_owner[0] if len(by_owner) == 1 else None):
            close.append({"id": iid, "label": d["title"],
                          "evidence": f"Orca 카드 {card.get('name') or card['id']} ({card.get('phase') or '?'})"})
    handles = {t.get("handle") for s in state.get("sessions") or [] for t in s.get("terminals") or []}
    leads = [{"name": n, "handle": l.get("handle"), "in_fleet": l.get("handle") in handles}
             for n, l in store["leads"].items() if not l.get("closed_at")]
    try:
        decisions = [{"id": d.get("id"), "title": d.get("title")} for d in fleet.open_decisions()]
    except store_db.NotMigrated:
        decisions = []
    return {"close": close, "tick": tick, "steps": steps, "leads": leads, "decisions": decisions}


def backup(now):
    dest = fleet.state_dir() / f"backup-orca-v2-{now.strftime('%Y%m%dT%H%M%S%fZ')}"
    dest.mkdir(mode=0o700)
    os.chmod(dest, 0o700)
    store_db.backup(dest / store_db.DB_NAME)
    return dest


def apply(now=None):
    """Returns (backup dir or None, closed ids)."""
    now = now or fleet.utcnow()
    ts = fleet.iso(now)
    found = [{"ev": "done", "id": c["id"], "ts": ts, "by": "migrate", "rule": RULE, "evidence": fleet.mask(c["evidence"]),
              "label": fleet.mask(c["label"])} for c in plan(now)["close"]]
    found = [r for r in found if tidy.fits(r)]
    if not found:
        return None, []
    dest = backup(now)
    fleet.write_atomic(dest / "migrate.json", json.dumps({"at": ts, "closed": [r["id"] for r in found]}, ensure_ascii=False) + "\n")
    os.chmod(dest / "migrate.json", 0o600)
    closed = []
    with store_db.tx() as db:
        have = store_db.latest(db)
        for r in found:
            if (have.get(r["id"]) or {}).get("ev") not in ("done", "reopen"):  # plan() read before this transaction
                store_db.close_unit(db, r)
                closed.append(r["id"])
    return dest, closed


def restore(dest, now=None):
    """Returns (reopened ids, ids a restore already reopened, ids something after migrate changed)."""
    record = fleet.read_json(dest / "migrate.json", None)
    if not isinstance(record, dict) or not isinstance(record.get("closed"), list):
        raise ValueError(f"{dest}/migrate.json is missing or not a migrate record")
    ts, opened, again, skipped = fleet.iso(now or fleet.utcnow()), [], [], []
    with store_db.tx() as db:
        have = store_db.latest(db)
        for iid in record["closed"]:
            cur = have.get(iid) or {}
            if cur.get("ev") == "done" and cur.get("by") == "migrate":
                store_db.reopen_unit(db, iid, ts, "migrate", "restore")
                opened.append(iid)
            elif cur.get("ev") == "reopen" and cur.get("by") == "migrate":
                again.append(iid)
            else:
                skipped.append(iid)
    return opened, again, skipped


def report(p):
    out = [f"닫을 doing 줄 (카드를 가리킴, --apply 가 닫음): {len(p['close'])}"]
    out += [f"  {c['id']}  {c['label']}  ← {c['evidence']}" for c in p["close"]]
    out.append(f"다음 tidy tick 이 닫을 doing 줄 (migrate 는 손대지 않음): {len(p['tick'])}")
    out += [f"  {c['id']}  {c['label']}  ← {c['evidence']}" for c in p["tick"]]
    out.append(f"머지 뒤 단계가 남은 doing 줄 (그대로 둠; 그 단계는 이 줄에만 있음): {len(p['steps'])}")
    out += [f"  {c['id']}  {c['label']}  ← {c['evidence']}" for c in p["steps"]]
    out.append(f"열린 board 리드 (그대로 둠; fleet 에 보이면 새 리드는 lead add 하지 않음): {len(p['leads'])}")
    out += [f"  {l['name']}  {l['handle']}  {'fleet 에 있음' if l['in_fleet'] else 'fleet 에 없음'}" for l in p["leads"]]
    out.append(f"열린 결정 (그대로 둠; Task 를 막는 결정이면 gate 로 다시 묻기): {len(p['decisions'])}")
    out += [f"  {d['id']}  {d['title']}" for d in p["decisions"]]
    return "\n".join(out)


def main(argv):
    try:
        run(argv)
    except store_db.NotMigrated as e:
        sys.exit(f"the board is not in {store_db.DB_NAME} yet ({e}); orch db cutover-plan 4b lists the steps")


def run(argv):
    if argv[:1] == ["--apply"] and len(argv) == 1:
        dest, ids = apply()
        if not ids:
            print("nothing to migrate")
            return
        print(f"backup: {dest}")
        print(f"closed {len(ids)}: " + " ".join(ids))
        print(f"undo: orch migrate --restore {dest}")
        print("undo with older code: orch board reopen " + " ".join(ids))
    elif argv[:1] == ["--restore"] and len(argv) == 2:
        try:
            ids, again, skipped = restore(pathlib.Path(argv[1]).expanduser())
        except ValueError as e:
            sys.exit(str(e))
        print(f"reopened {len(ids)}" + (": " + " ".join(ids) if ids else ""))
        if again:
            print("already reopened: " + " ".join(again))
        if skipped:
            print("left as they are (changed after migrate): " + " ".join(skipped))
    elif not argv:
        print(report(plan()))
        print("dry-run: nothing written. orch migrate --apply to close the first list.")
    else:
        sys.exit("usage: orch migrate [--apply | --restore BACKUP_DIR]")
