"""The stage table the brief skill reports and the dashboard's Brief page draws: one row per work item, its PR merge, dev check
and prod check, each cell a mark and the evidence behind it.

Most rows are generated: each fleet tick derives a program's rows from its ledger, its Run's cards and GitHub
(stage_auto.py, stages-auto.json). The orchestrator or lead writes a cell where it verified it (`orch stage set`), a
generated row's id included, and that manual cell wins over the generated one; it adds a row (`orch stage add`) for
work outside any program. A recorded row with a generated row's PR is folded into that row when the table is read. The next report and the dashboard read the same record instead of each rebuilding it. What
a person recorded lives in the fleet state directory (stages.json, beside state.json), not a program's ledger: work
outside any program needs a row too. The merge cell of a recorded row with a PR is never written: it is read from the
fleet collector's PR list each time the table is shown. A group row (`--group`) is only a heading for
the rows under it, a feature or an epic: it has no PR and no cells.
"""
import datetime as dt, fcntl, json, pathlib, re, sys

import fleet, stage_auto

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "brief" / "scripts"))
import table_width  # noqa: E402

COLS = 78

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
        row.update(title=fleet.mask(" ".join(title.split())), pr=pr or row.get("pr"), parent=parent or row.get("parent"))
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
    evidence = fleet.mask(" ".join((evidence or "").split())) or None
    # The brief skill: a cell with no evidence is not ✅.
    if mark == "ok" and not evidence:
        raise ValueError("✅ needs --evidence (a version, a time, a count)")

    def go(store):
        row = store["rows"].get(rid)
        made = stage_auto.read()["rows"].get(rid)
        if not row and made and not made.get("group"):
            # Cells only, over a generated row: its title, PR and place stay the tick's.
            row = store["rows"][rid] = {"id": rid, "cells": {}, "overlay": True, "created_at": fleet.iso(fleet.utcnow())}
        if not row:
            raise ValueError(f"no row {rid}; add it first with `orch stage add {rid} --title …`")
        if row.get("group"):
            raise ValueError(f"{rid} is a group row; it has no cells, set them on the rows under it")
        row["cells"][col] = {"mark": mark, "evidence": evidence, "by": fleet.mask(by), "at": fleet.iso(fleet.utcnow())}
        return row
    return change(go)


def set_env(prod=None, dev=None):
    def go(store):
        store["env"] = {**store["env"], **{k: fleet.mask(v) for k, v in (("prod", prod), ("dev", dev)) if v},
                        "at": fleet.iso(fleet.utcnow())}
        return store["env"]
    return change(go)


def set_next(lines):
    return change(lambda store: store.update(next=[fleet.mask(" ".join(l.split())) for l in lines if l.strip()]))


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


def merged(store, made):
    """Generated rows with the recorded ones over them: a recorded cell replaces the generated cell of its column, a
    recorded row of the same id its fields. A recorded row with a generated row's PR is the same work: it folds into that
    row, its title and cells over the generated ones, and the rows under it move along; so does a PR-less row under a
    program's group whose id names one of its predicate items (by_item). A recorded group left with no row of its own
    goes. An overlay whose generated row is gone has nothing to sit on."""
    rows = dict(made["rows"])
    by_pr = {r["pr"]: k for k, r in rows.items() if r.get("pr")}
    into = {}
    for k, r in store["rows"].items():
        if k in rows:
            rows[k] = {**rows[k], **{f: v for f, v in r.items() if v and f not in ("cells", "overlay")},
                       "cells": {**rows[k].get("cells", {}), **r.get("cells", {})}}
        elif (on := not r.get("group") and (by_pr.get(r.get("pr")) or by_item(k, r, made["rows"], set(into.values())))):
            into[k] = on
            rows[on] = {**rows[on], **{f: v for f, v in r.items() if v and f in ("title", "done_at")},
                        "cells": {**rows[on].get("cells", {}), **r.get("cells", {})}}
        elif not r.get("overlay"):
            rows[k] = r
    for k, r in rows.items():
        if r.get("parent") in into:
            rows[k] = {**r, "parent": into[r["parent"]]}
    kids = {}
    for k, r in store["rows"].items():
        kids.setdefault(r.get("parent"), set()).add(k)
    gone = {k for k in kids if (store["rows"].get(k) or {}).get("group") and k not in made["rows"] and kids[k] <= set(into)}
    rows = {k: r for k, r in rows.items() if k not in gone}
    return rows, made["order"] + [k for k in store["order"] if k in rows and k not in made["rows"]]


def by_item(rid, row, made, taken):
    """The generated row a PR-less recorded row stands for: it hangs under a program's generated group, and its id past
    the group's (`<slug>-release` under `<slug>`) names words of exactly one predicate item there (`<slug>/P1-release`).
    None when no item or several match, or another recorded row already folded into it: two rows beat a wrong merge."""
    parent = row.get("parent")
    if row.get("pr") or not (made.get(parent) or {}).get("group") or not rid.lower().startswith(f"{parent.lower()}-"):
        return None
    want = set(rid[len(parent) + 1:].lower().split("-"))
    hits = [k for k, r in made.items()
            if r.get("parent") == parent and r.get("item") and want <= set(k.split("/", 1)[1].lower().split("-"))]
    return hits[0] if len(hits) == 1 and hits[0] not in taken else None


def table(state=None):
    """Rows in display order (each row followed by the rows under it, `depth` levels down), a recorded row's merge
    cell filled from the PR list, and `refs`: each owner/repo#N the rows name -> its URL."""
    store = read()
    state = fleet.read_json(fleet.state_dir() / "state.json", {}) if state is None else state
    prs = {p.get("key"): p for p in (state or {}).get("prs") or []}
    all_rows, order = merged(store, stage_auto.read())
    rows = []

    def walk(parent, depth):
        for k in order:
            if k in all_rows and all_rows[k].get("parent") == parent:
                rows.append({**all_rows[k], "depth": depth})
                walk(k, depth + 1)
    walk(None, 0)
    out = []
    for r in rows:
        cells = dict(r.get("cells") or {})
        # A generated row's merge cell is evidence or nothing; a recorded row's says what holds its PR.
        auto = merge_cell(r.get("pr"), prs) if r.get("pr") and r.get("source") != "auto" else None
        if auto:
            cells["merge"] = auto
        out.append({**r, "cells": cells})
    return {"rows": out, "env": store["env"], "next": store["next"], "refs": ref_urls(out, store["next"], prs)}


REF = re.compile(r"\b([\w.-]+/[\w.-]+)#(\d+)\b")


def ref_urls(rows, nxt, prs):
    """owner/repo#N -> /pull/N for a PR (in the collector's list, or a row's PR), else /issues/N: GitHub redirects
    an /issues/ URL of a PR to the PR, never the other way."""
    known = set(prs) | {r["pr"] for r in rows if r.get("pr")}
    texts = [r.get(f) or "" for r in rows for f in ("pr", "title")] + list(nxt) + [
        c.get("evidence") or "" for r in rows for c in r["cells"].values()]
    return {f"{m[1]}#{m[2]}": f"https://github.com/{m[1]}/{'pull' if f'{m[1]}#{m[2]}' in known else 'issues'}/{m[2]}"
            for t in texts for m in REF.finditer(t)}


def current(t, now, idle_s, state=None):
    """table() without the rows the report has no use for: finished (`done`, or every column ✅ or 해당 없음) or
    unchanged for idle_s, its PR's merge in state's PR list counting as a change. A row with a ❌, ⚠️ or 🔄 cell is
    stuck, not finished, and stays however quiet. A row stays while a row under it stays, and with no time to judge by. Returns (table, how many work rows left); with no row left, 다음 순서 goes
    too. The dashboard reads table() whole."""
    rows = {r["id"]: r for r in t["rows"]}
    merged = {p.get("key"): p.get("merged_at") for p in (state or {}).get("prs") or []}

    def live(r):
        if r.get("group") or r.get("done_at"):
            return False
        marks = {(r["cells"].get(c) or {}).get("mark") for c in COLUMNS}
        if marks & {"fail", "partial", "checking"}:
            return True
        if marks <= {"ok", "na"}:
            return False
        times = [fleet.parse(x) for x in [r.get("created_at"), r.get("at"), merged.get(r.get("pr"))] + [c.get("at") for c in r["cells"].values()]]
        last = max(filter(None, times), default=None)
        return not last or (now - last).total_seconds() <= idle_s
    keep = set()
    for r in t["rows"]:
        k = r["id"] if live(r) else None
        while k and k not in keep:
            keep.add(k)
            k = (rows.get(k) or {}).get("parent")
    out = [r for r in t["rows"] if r["id"] in keep]
    gone = sum(1 for r in t["rows"] if not r.get("group") and r["id"] not in keep)
    return {**t, "rows": out, "next": t["next"] if out else []}, gone


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
    header = ["작업", "feature·대표 PR", "PR 머지", "dev 확인", "prod 확인"]
    lines = [l for l in (env_line(t["env"]), LEGEND) if l] + ["", "| " + " | ".join(header) + " |", "|---|---|---|---|---|"]
    groups = {r["id"] for r in t["rows"] if r.get("group")}
    body = []
    for r in t["rows"]:
        if r.get("group"):
            body.append([r["title"], "", "", "", ""])
            continue
        name = f"ㄴ {r['title']}" if r.get("parent") not in (None, *groups) else r["title"]
        if r.get("done_at"):
            name += " (끝남)"
        body.append([name, r.get("pr") or "–"] + [cell_text(r["cells"].get(c)) for c in COLUMNS])
    # Only the name and PR cells are cut to fit (the PR column is a plain key here, not a link), as in the brief's
    # tables, each whole text in the list under it. A table its stage cells alone make too wide overflows.
    rows, notes = table_width.fit([header] + body, COLS, (0, 1),
                                  deco=lambda i, c, x: f"**{x}**" if c == 0 and t["rows"][i - 1].get("group") else x)
    lines += ["| " + " | ".join(x.replace("|", "\\|") for x in row) + " |" for row in rows[1:]]
    lines += [""] + [f"- [{n}] {x}" for n, x in enumerate(notes, 1)] if notes else []
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
