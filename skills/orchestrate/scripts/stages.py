"""The stage table brief-status reports and the orch-panel mod draws: one row per work item, its PR merge, dev check
and prod check, each cell a mark and the evidence behind it.

The coordinator writes a cell where it verified it (`orch stage set`), so the next report and the mod read the same
record instead of each rebuilding it. It lives in the fleet state directory (stages.json, beside state.json), not a
program's ledger: work outside any program needs a row too. The merge cell of a row with a PR is never written: it is
read from the fleet collector's PR list each time the table is shown. A group row (`--group`) is only a heading for
the rows under it, a feature or an epic: it has no PR and no cells.
"""
import datetime as dt, fcntl, json

import fleet

MARKS = ("ok", "fail", "partial", "checking", "na")
ALIASES = {"✅": "ok", "❌": "fail", "⚠️": "partial", "⚠": "partial", "🔄": "checking", "해당없음": "na", "해당 없음": "na"}
COLUMNS = ("merge", "dev", "prod")
EMOJI = {"ok": "✅", "fail": "❌", "partial": "⚠️", "checking": "🔄", "na": "해당 없음"}
LEGEND = "✅ 완료·확인 · ❌ 미완료·실패 · ⚠️ 일부·추정 · 🔄 확인 중 · 해당 없음"
DONE_KEEP_S = 7 * 86400  # a finished row stays this long after `done`, then leaves the table


def path():
    return fleet.state_dir() / "stages.json"


def read():
    store = fleet.read_json(path(), {}) or {}
    return {"rows": store.get("rows") or {}, "order": store.get("order") or [], "env": store.get("env") or {},
            "next": store.get("next") or []}


def change(fn):
    """Run fn(store) on stages.json under a lock and replace the file whole; returns what fn returns."""
    path().parent.mkdir(parents=True, exist_ok=True)
    with (fleet.state_dir() / "stages.lock").open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        store = read()
        out = fn(store)
        cutoff = fleet.iso(fleet.utcnow() - dt.timedelta(seconds=DONE_KEEP_S))
        gone = {k for k, r in store["rows"].items() if (r.get("done_at") or "9") < cutoff}
        store["rows"] = {k: r for k, r in store["rows"].items() if k not in with_descendants(store["rows"], gone)}
        store["order"] = [k for k in store["order"] if k in store["rows"]]
        fleet.write_atomic(path(), json.dumps(store, ensure_ascii=False, indent=1) + "\n")
        return out


def with_descendants(rows, ids):
    ids = set(ids)
    while True:
        more = {k for k, r in rows.items() if r.get("parent") in ids} - ids
        if not more:
            return ids
        ids |= more


def mark_of(text):
    m = ALIASES.get((text or "").strip(), (text or "").strip().lower())
    if m not in MARKS:
        raise ValueError(f"--mark is one of {', '.join(MARKS)} (or ✅ ❌ ⚠️ 🔄)")
    return m


def add(rid, title, pr=None, parent=None, group=False):
    if not rid or not title.strip():
        raise ValueError("stage add needs an id and --title")

    def go(store):
        rows = store["rows"]
        if parent and parent not in rows:
            raise ValueError(f"no row {parent} to be the parent")
        # Under a group, one level of work rows and their children; a work row holds children, a child holds none.
        above = rows[parent].get("parent") if parent else None
        if above and not rows.get(above, {}).get("group"):
            raise ValueError(f"{parent} is a child row; a child hangs off a top-level row or a group's row")
        row = rows.get(rid) or {"id": rid, "cells": {}, "created_at": fleet.iso(fleet.utcnow())}
        row.update(title=fleet.mask(" ".join(title.split()), 120), pr=pr or row.get("pr"), parent=parent or row.get("parent"))
        if group or row.get("group"):
            if row["pr"] or row["parent"] or row["cells"]:
                raise ValueError(f"a group row ({rid}) is a top-level heading: no --pr, no --parent, no cells")
            row["group"] = True
        store["rows"][rid] = row
        if rid not in store["order"]:
            store["order"].append(rid)
        return row
    return change(go)


def set_cell(rid, col, mark, evidence=None, by=None):
    if col not in COLUMNS:
        raise ValueError(f"--col is one of {', '.join(COLUMNS)}")
    mark = mark_of(mark)
    evidence = fleet.mask(" ".join((evidence or "").split()), 160) or None
    # brief-status: a cell with no evidence is not ✅.
    if mark == "ok" and not evidence:
        raise ValueError("✅ needs --evidence (a version, a time, a count)")

    def go(store):
        row = store["rows"].get(rid)
        if not row:
            raise ValueError(f"no row {rid}; add it first with `orch stage add {rid} --title …`")
        if row.get("group"):
            raise ValueError(f"{rid} is a group row; it has no cells, set them on the rows under it")
        row["cells"][col] = {"mark": mark, "evidence": evidence, "by": fleet.mask(by, 40), "at": fleet.iso(fleet.utcnow())}
        return row
    return change(go)


def set_env(prod=None, dev=None):
    def go(store):
        store["env"] = {**store["env"], **{k: fleet.mask(v, 40) for k, v in (("prod", prod), ("dev", dev)) if v},
                        "at": fleet.iso(fleet.utcnow())}
        return store["env"]
    return change(go)


def set_next(lines):
    return change(lambda store: store.update(next=[fleet.mask(" ".join(l.split()), 200) for l in lines if l.strip()]))


def finish(rid, drop=False):
    def go(store):
        if rid not in store["rows"]:
            raise ValueError(f"no row {rid}")
        if drop:
            gone = with_descendants(store["rows"], {rid})
            store["rows"] = {k: r for k, r in store["rows"].items() if k not in gone}
        else:
            store["rows"][rid]["done_at"] = fleet.iso(fleet.utcnow())
    return change(go)


def merge_cell(pr, prs):
    """The merge column from the collector's PR list: merged with its date, else what holds it."""
    p = prs.get(pr)
    if not p:
        return None
    if p.get("merged_at") or p.get("state") == "MERGED":
        at = fleet.parse(p.get("merged_at"))
        return {"mark": "ok", "evidence": f"{at.month}/{at.day}" if at else "머지됨", "auto": True}
    if p.get("state") == "CLOSED":
        return {"mark": "fail", "evidence": "닫힘", "auto": True}
    why = ("CI 실패" if p.get("ci") == "failure" else "변경 요청" if p.get("decision") == "CHANGES_REQUESTED"
           else "draft" if p.get("draft") else "리뷰 대기")
    return {"mark": "fail", "evidence": why, "auto": True}


def table(state=None):
    """Rows in display order (each row followed by the rows under it, `depth` levels down), merge cells filled from
    the PR list."""
    store = read()
    state = fleet.read_json(fleet.state_dir() / "state.json", {}) if state is None else state
    prs = {p.get("key"): p for p in (state or {}).get("prs") or []}
    rows = []

    def walk(parent, depth):
        for k in store["order"]:
            if k in store["rows"] and store["rows"][k].get("parent") == parent:
                rows.append({**store["rows"][k], "depth": depth})
                walk(k, depth + 1)
    walk(None, 0)
    out = []
    for r in rows:
        cells = dict(r.get("cells") or {})
        auto = merge_cell(r.get("pr"), prs) if r.get("pr") else None
        if auto:
            cells["merge"] = auto
        out.append({**r, "cells": cells})
    return {"rows": out, "env": store["env"], "next": store["next"]}


def cell_text(cell):
    if not cell:
        return "–"
    extra = " ".join(x for x in (cell.get("evidence"), cell.get("by") and f"({cell['by']})") if x)
    return f"{EMOJI[cell['mark']]} {extra}".strip()


def env_line(env):
    parts = [f"{k}: {env[k]}" for k in ("prod", "dev") if env.get(k)]
    return " · ".join(parts)


def render_md(t):
    if not t["rows"]:
        return ""
    lines = [l for l in (env_line(t["env"]), LEGEND) if l] + [
        "", "| 작업 | feature·대표 PR | PR 머지 | dev 확인 | prod 확인 |", "|---|---|---|---|---|"]
    groups = {r["id"] for r in t["rows"] if r.get("group")}
    for r in t["rows"]:
        if r.get("group"):
            lines.append("| **" + r["title"].replace("|", "\\|") + "** |  |  |  |  |")
            continue
        name = f"ㄴ {r['title']}" if r.get("parent") not in (None, *groups) else r["title"]
        if r.get("done_at"):
            name += " (끝남)"
        cols = [name, r.get("pr") or "–"] + [cell_text(r["cells"].get(c)) for c in COLUMNS]
        lines.append("| " + " | ".join(x.replace("|", "\\|") for x in cols) + " |")
    if t["next"]:
        lines += ["", "다음 순서"] + [f"{i}. {n}" for i, n in enumerate(t["next"], 1)]
    return "\n".join(lines)


def render_telegram(t):
    if not t["rows"]:
        return ""
    env = env_line(t["env"])
    lines = [f"📋 작업별 ({env})" if env else "📋 작업별"]
    groups = {r["id"] for r in t["rows"] if r.get("group")}
    for r in t["rows"]:
        if r.get("group"):
            lines.append(f"[{r['title']}]")
            continue
        head = f"ㄴ {r['title']}" if r.get("parent") not in (None, *groups) else f"- {r['title']}"
        pr = f" ({r['pr']})" if r.get("pr") else ""
        cells = " · ".join(f"{label} {cell_text(r['cells'].get(c))}" for c, label in zip(COLUMNS, ("머지", "dev", "prod")))
        lines.append(f"{head}{pr} — {cells}")
    if t["next"]:
        lines.append("다음 순서: " + " / ".join(t["next"]))
    return "\n".join(lines)
