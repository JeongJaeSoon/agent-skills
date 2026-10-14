"""The board the brief skill's periodic report is rendered from: one row per lead, phase and sub-task, with its status,
progress, next step and the time it was last confirmed, and the renderer that turns it into the report.

The coordinator writes rows (`orch board set`) as reports come in; `orch brief render --periodic` reads the board, the
fleet collector's PR list (prs.json), the open decisions and the last sent table (brief-last/<scope>.json) and prints
the report. PR state is never stored here: the renderer joins it from prs.json each time, so the two cannot disagree.
Nothing is hidden for being old: a row past its staleness limit shows its age, a PR list probed long ago says so.

render() is the one implementation of the brief skill's periodic report rules (sections, sub-task rows, the
same-reviewer fold, cell caps and footnotes, bold for changed rows); test_board.py pins its output.
"""
import datetime as dt, fcntl, json, os, pathlib, re, shutil, sys

import fleet
from stages import with_descendants

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "brief" / "scripts"))
import table_width  # noqa: E402

KINDS = ("lead", "phase", "task")
ICON = {"moving": "🔄", "waiting": "⏳", "held": "⏸", "blocked": "🔴", "done": "✅"}
ALIASES = {v: k for k, v in ICON.items()}
WORST = ("blocked", "waiting", "held", "moving")
CAPS = {"name": 17, "pr": 17, "next_min": 12}  # next takes what the others leave: 2+17+17+14+12+16 borders = 78
COLS = 78
LIMITS = {"row_min": 90, "pr_warn_min": 30, "pr_stale_min": 120}  # overridden by the dashboard config's "board" key
DONE_KEEP_S = 7 * 86400  # a finished lead stays this long with its rows, then leaves the board
MARKS = "¹²³⁴⁵⁶⁷⁸⁹"
HEADER = ["", "리드/Phase", "PR", "진행", "다음"]
PR_LEGEND = (("❗", "사람이 풀 것"), ("✓", "머지 가능"), ("CI✗", "CI 실패"), ("CI…", "CI 진행"))
UNNAMED = "(프로젝트 미지정)"
LINK = r"\[([^\]]*)\]\([^)]*\)"


def path():
    return fleet.state_dir() / "board.json"


def last_path(scope):
    base = os.environ.get("ORCH_BRIEF_LAST")
    return (pathlib.Path(base).expanduser() if base else fleet.state_dir().parent / "brief-last") / f"{scope}.json"


def read():
    store = fleet.read_json(path(), {}) or {}
    return {"v": 1, "rows": store.get("rows") or {}, "order": store.get("order") or [], "leads": store.get("leads") or {},
            "later": store.get("later") or [], "owner_todo": store.get("owner_todo") or []}


def change(fn):
    """Run fn(store) on board.json under a lock and replace the file whole, keeping the previous one as board.prev.json."""
    path().parent.mkdir(parents=True, exist_ok=True)
    with (fleet.state_dir() / "board.lock").open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        store = read()
        out = fn(store)
        cutoff = fleet.iso(fleet.utcnow() - dt.timedelta(seconds=DONE_KEEP_S))
        gone = {k for k, r in store["rows"].items() if not r.get("parent") and (r.get("done_at") or "9") < cutoff}
        store["rows"] = {k: r for k, r in store["rows"].items() if k not in with_descendants(store["rows"], gone)}
        store["order"] = [k for k in store["order"] if k in store["rows"]]
        if path().exists():
            shutil.copyfile(path(), fleet.state_dir() / "board.prev.json")
        fleet.write_atomic(path(), json.dumps(store, ensure_ascii=False, indent=1) + "\n")
        return out


def one_line(text, limit):
    return fleet.mask(" ".join((text or "").split()), limit)


def status_of(text):
    s = ALIASES.get((text or "").strip(), (text or "").strip().lower())
    if s not in ICON:
        raise ValueError(f"--status is one of {', '.join(ICON)} (or {' '.join(ICON.values())})")
    return s


def progress_of(text):
    if text in ("-", "—", ""):
        return None
    try:
        n, m = (int(x) for x in text.split("/"))
    except ValueError:
        raise ValueError("--progress is N/M (stages passed out of the item's stages), or - for no basis") from None
    if not 0 <= n <= m or m == 0:
        raise ValueError("--progress N/M needs 0 <= N <= M and M > 0")
    return [n, m]


def lead_add(name, handle, repo, run=None, stale_min=None, program=None):
    if not name or not handle or not repo:
        raise ValueError("board lead add needs a name, --handle and --repo")

    def go(store):
        store["leads"][name] = {"name": name, "handle": handle, "repo": repo, "run": run, "started_at": fleet.iso(fleet.utcnow()),
                                **({"stale_after_min": int(stale_min)} if stale_min else {})}
        if name not in store["rows"]:
            put(store, name, {"kind": "lead", "repo": repo, "program": program, "status": "moving"})
        elif program:
            put(store, name, {"program": program})
        return store["leads"][name]
    return change(go)


def lead_close(name):
    def go(store):
        if name not in store["leads"]:
            raise ValueError(f"no lead {name}")
        store["leads"][name]["closed_at"] = fleet.iso(fleet.utcnow())
    return change(go)


def put(store, rid, fields):
    rows = store["rows"]
    row = rows.get(rid)
    if not row:
        parent = fields.get("parent") or (rid.rsplit("/", 1)[0] if "/" in rid else None)
        if parent and parent not in rows:
            raise ValueError(f"no row {parent} to be the parent; add it first")
        kind = fields.get("kind") or ("task" if parent else "lead")
        if not parent and kind != "lead" and not fields.get("repo"):
            raise ValueError(f"a top-level {kind} row needs --repo")
        if not parent and not fields.get("repo"):
            raise ValueError(f"a lead row ({rid}) needs --repo")
        row = {"id": rid, "kind": kind, "title": rid.rsplit("/", 1)[-1], "parent": parent, "status": "moving", "next": ""}
        rows[rid] = row
        store["order"].append(rid)
    for k, v in fields.items():
        if v is None or k == "parent" and v == row.get("parent"):
            continue
        if k == "next" and v.strip() in ("-", "—"):
            v = ""
        if k == "parent":
            raise ValueError(f"{rid} already hangs off {row.get('parent')}; drop it to move it")
        if k == "kind" and v not in KINDS:
            raise ValueError(f"--kind is one of {', '.join(KINDS)}")
        row[k] = (status_of(v) if k == "status" else progress_of(v) if k == "progress" else
                  one_line(v, 120) if k in ("title", "next") else one_line(v, 60) if isinstance(v, str) else v)
    if row.get("progress") is None:
        row.pop("progress", None)
    row.update(source="coordinator", reported_at=fleet.iso(fleet.utcnow()))
    if row["status"] == "done":
        row.setdefault("done_at", row["reported_at"])
    else:
        row.pop("done_at", None)
    return row


def unnamed_leads(rows):
    """Leads with no program in a repository that has two or more leads: their rows would share one table."""
    by_repo = {}
    for r in rows.values():
        if not r.get("parent") and r["kind"] == "lead":
            by_repo.setdefault(r.get("repo"), []).append(r)
    return [r["id"] for leads in by_repo.values() if len(leads) > 1 for r in leads if not r.get("program")]


def unnamed_warning():
    ids = unnamed_leads(read()["rows"])
    return ids and (f"warning: no --program on {', '.join(ids)}, beside another lead in the same repository; the report "
                    f"heads them {UNNAMED} until each gets --program <project>")


def set_row(rid, **fields):
    """Add or update one row; only the fields given change. reported_at becomes now: the row was confirmed now."""
    if not rid:
        raise ValueError("board set needs a row id")
    return change(lambda store: put(store, rid, fields))


def drop(rid):
    def go(store):
        if rid not in store["rows"]:
            raise ValueError(f"no row {rid}")
        gone = with_descendants(store["rows"], {rid})
        store["rows"] = {k: r for k, r in store["rows"].items() if k not in gone}
    return change(go)


def later_add(title, trigger, due=None):
    if not (title or "").strip() or not (trigger or "").strip():
        raise ValueError("board later add needs a title and --trigger (what brings it back)")
    item = {"title": one_line(title, 120), "trigger": one_line(trigger, 60), **({"due": due} if due else {})}
    return change(lambda store: store["later"].append(item))


def later_drop(n):
    def go(store):
        if not 1 <= n <= len(store["later"]):
            raise ValueError(f"no later item {n}; there are {len(store['later'])}")
        store["later"].pop(n - 1)
    return change(go)


def set_todo(lines):
    return change(lambda store: store.update(owner_todo=[one_line(l, 200) for l in lines if l.strip()]))


# ---------------------------------------------------------------- render

def waits_on(p):
    """The one person an open PR waits on, or None: no draft, no failing CI, no change request, one requested reviewer."""
    if not p or p.get("state") != "OPEN" or p.get("draft") or p.get("ci") == "failure" or p.get("decision") == "CHANGES_REQUESTED":
        return None
    pending = [r for r in p.get("reviewers") or [] if r.get("requested")]
    if len(pending) != 1 or pending[0].get("team") or pending[0].get("bot"):
        return None
    return pending[0]["login"]


def pr_state(p):
    """A PR's state in a few short tokens: the merge sha once merged, else what stands between it and the merge."""
    if p.get("merged_at") or p.get("state") == "MERGED":
        return (p.get("merge_sha") or "")[:7] or "머지됨"
    if p.get("state") == "CLOSED":
        return "닫힘"
    ms, ci, dec, out = p.get("merge_state"), p.get("ci"), p.get("decision"), []
    if p.get("draft"):
        out.append("초안")
    if ms == "DIRTY":
        out.append("❗DIRTY")
    if dec == "REVIEW_REQUIRED":
        out.append("❗리뷰")
    elif dec == "CHANGES_REQUESTED":
        out.append("수정요청")
    if ci in ("failure", "pending"):
        out.append("CI✗" if ci == "failure" else "CI…")
    if ms == "BEHIND":
        out.append("BEHIND")
    elif ms == "BLOCKED" and not out:  # blocked by something the tokens above do not name
        out.append("BLOCKED")
    elif ms == "UNSTABLE" and not out:
        out.append("UNSTABLE")
    return " ".join(out) or ("✓" if ms in ("CLEAN", "HAS_HOOKS") else "열림")


def bar(progress):
    if not progress:
        return "—"
    n, m = progress
    full = int(8 * n / m + 0.5)  # round half up: round() rounds 0.5 to even
    return "█" * full + "░" * (8 - full) + f" {n}/{m}"


def ago(seconds):
    m = int(seconds // 60)
    return f"{m}분 전" if m < 60 else f"{m // 60}h 전" if m < 48 * 60 else f"{m // 1440}일 전"


def pr_num(key):
    return "#" + key.rsplit("#", 1)[-1]


def clip(text, budget):
    """text cut to fit budget columns with a trailing …"""
    out = ""
    for ch in text:
        if table_width.text_width(out + ch + "…") > budget:
            break
        out += ch
    return out.rstrip() + "…"


# A session's phase (fleet.py phase_of) read as a board status and next step.
PHASE_ROW = {"working": ("moving", "작업 중"), "idle": ("moving", "턴 종료"), "waiting": ("waiting", "확인 대기 · 사람"),
             "open": ("held", "에이전트 없음"), "offline": ("held", "세션 꺼짐")}


def fleet_rows(state, leads, board_rows):
    """Board rows built from the fleet collector's sessions (state.json), for what nobody wrote to the board: each lead
    session whose terminal no board lead holds, the cards under it, and the cards the top-level coordinator dispatched
    itself. Each card carries its newest PR; a card whose PR a board row already has is left to that row. Returns
    (rows, order); reported_at is the session's last activity, so a quiet one shows its age like a stale board row."""
    state = state or {}
    sessions = state.get("sessions") or []
    held = {l.get("handle") for l in leads.values()}
    on_board = {r.get("pr") for r in board_rows.values()} - {None}
    pr_of, under = {}, {}
    for p in state.get("prs") or []:  # newest first
        if p.get("session"):
            pr_of.setdefault(p["session"], p.get("key"))
    for s in sessions:
        under.setdefault(s.get("parent"), []).append(s)
    rows, order = {}, []

    def add(rid, s, kind, parent=None, program=None):
        status, nxt = PHASE_ROW.get(s.get("phase"), ("held", s.get("phase") or "—"))
        # A lead reads as its session name (as in the sidebar), a card as the task it was dispatched with.
        title = s.get("name") if kind == "lead" else s.get("task_title") or s.get("name")
        rows[rid] = {"id": rid, "kind": kind, "title": one_line(title or rid, 80), "status": status, "next": nxt,
                     "pr": pr_of.get(s.get("id")), "reported_at": s.get("last_activity"), "parent": parent,
                     # a card under a lead takes the lead's repo, so the lead heads one section
                     "repo": None if parent else s.get("gh_repo") or s.get("project") or "—", "program": program}
        order.append(rid)

    def shown(s):
        pr = pr_of.get(s.get("id"))
        return (s.get("phase") != "offline" or pr) and pr not in on_board

    for lead in sessions:
        if lead.get("kind") != "orchestration" or held & {t.get("handle") for t in lead.get("terminals") or []}:
            continue
        lid = f"fleet:{lead['id']}"
        add(lid, lead, "lead", program=one_line(lead.get("name") or lid, 80))  # two leads in one repo: a section each
        for s in under.get(lead["id"], []):
            if shown(s):
                add(f"{lid}/{s['id']}", s, "task", lid)
    kind = {s.get("id"): s.get("kind") for s in sessions}
    for s in sessions:
        if s.get("kind") == "task" and kind.get(s.get("parent")) == "orchestrator" and shown(s):
            add(f"fleet:{s['id']}", s, "task")
    return rows, order


def render(scope="top-level", channel="terminal", now=None, headline=None, skipped=None, fill=False):
    """The periodic report (brief skill §1 Periodic report items 1–8) as text, with what the caller needs around it.
    fill adds fleet_rows() for the leads and cards the board does not have.

    Returns {"text", "headline_facts", "changed", "stale", "width_ok", "snapshot"}; text is "" when the board has no
    rows in scope. snapshot is what commit() saves for the next report to compare against."""
    now = now or fleet.utcnow()
    tg = channel == "telegram"
    sd = fleet.state_dir()
    store = read()
    limits = {**LIMITS, **(fleet.config().get("board") or {})}
    prs = {k: {**v["detail"], "probed_at": v.get("probed_at")}
           for k, v in ((fleet.read_json(sd / "prs.json", {}) or {}).get("prs") or {}).items() if v.get("detail")}
    state = fleet.read_json(sd / "state.json", None)
    live = None if state is None else {t.get("handle") for s in state.get("sessions") or [] for t in s.get("terminals") or []}
    last = fleet.read_json(last_path(scope), None)
    last_rows = last.get("rows") if isinstance(last, dict) and isinstance(last.get("rows"), dict) else None
    if fill:
        extra, extra_order = fleet_rows(state, store["leads"], store["rows"])
        store["rows"], store["order"] = {**extra, **store["rows"]}, store["order"] + extra_order
    rows = store["rows"]

    def up(r, field):
        while r is not None:
            if r.get(field):
                return r[field]
            r = rows.get(r.get("parent"))
        return None

    def lead_of(r):
        while r.get("parent") in rows:
            r = rows[r["parent"]]
        return r

    if scope != "top-level":
        rows = {k: r for k, r in rows.items() if up(r, "program") == scope}
    order = [k for k in store["order"] if k in rows]
    if not order:
        return {"text": "", "headline_facts": [], "changed": [], "stale": [], "width_ok": True, "snapshot": None}
    unnamed, unnamed_secs = set(unnamed_leads(rows)), set()

    oldest_probe, changed, stale, facts = None, [], [], []

    def derive(r):
        """(status, next, stale suffix) after the PR join and the session check."""
        status, nxt = r["status"], r.get("next") or ""
        p = prs.get(r.get("pr"))
        if r["kind"] == "task" and p and (p.get("merged_at") or p.get("state") == "MERGED"):
            status = "done"
        lead = store["leads"].get(lead_of(r)["id"])
        if status == "moving" and live is not None and lead and not lead.get("closed_at") and lead["handle"] not in live:
            status, nxt = "blocked", "세션 없음"
        suffix = ""
        at = fleet.parse(r.get("reported_at"))
        limit = (lead or {}).get("stale_after_min") or limits["row_min"]
        if status != "done" and at and (now - at).total_seconds() > limit * 60:
            suffix = f" · {ago((now - at).total_seconds())}"
            stale.append(r["id"])
        return status, nxt, suffix

    def pr_text(key):
        nonlocal oldest_probe
        p = prs.get(key)
        if not p:
            return pr_num(key)
        probed = fleet.parse(p.get("probed_at"))
        if probed and (oldest_probe is None or probed < oldest_probe):
            oldest_probe = probed
        warn = "⚠️" if not probed or (now - probed).total_seconds() > limits["pr_stale_min"] * 60 else ""
        return (f"{pr_num(key)}{warn}" if tg or not p.get("url") else f"[{pr_num(key)}]({p['url']}){warn}")

    def pr_status(key, base):
        """The PR's state, and its merge target when that is not the section's base."""
        p = prs.get(key)
        if not p:
            return ""
        to = p.get("base")
        return pr_state(p) + (f"→{to}" if to and to != base else "")

    def worst(own, kids):
        if own == "done":
            return "done"
        open_ = [own] + [k for k in kids if k != "done"]
        return min(open_, key=lambda s: WORST.index(s) if s in WORST else len(WORST))

    # Each display line: {key, section, depth (0 head, 1 sub-task), status, name, title, progress, next, suffix}
    sections, lines, bullets = [], [], {}

    def section_of(r):
        sec = (up(r, "repo"), up(r, "program"))
        if sec not in sections:
            sections.append(sec)
        return sec

    def line(key, sec, depth, status, name, pr, progress, nxt, suffix="", fold=None):
        """pr: the row's PR key; fold: the PR keys of a same-reviewer row."""
        lines.append({"key": key, "sec": sec, "depth": depth, "status": status, "name": name, "pr": pr,
                      "progress": progress, "next": nxt, "suffix": suffix, "fold": fold})

    def task_lines(head, sec, tasks):
        out = []
        for t in tasks:
            st, nxt, suf = derive(t)
            out.append((t, st, nxt, suf))
        by_rev = {}
        for t, st, *_ in out:
            rev = st != "done" and waits_on(prs.get(t.get("pr")))
            if rev:
                by_rev.setdefault(rev, []).append(t["id"])
        folded = {k for ids in by_rev.values() if len(ids) >= 2 for k in ids}
        statuses = []
        for t, st, nxt, suf in out:
            statuses.append(st)
            if t["id"] in folded:
                continue
            tail = t["id"].rsplit("/", 1)[-1]
            name = t["title"] if t.get("pr") or t["title"] == tail or t["id"].startswith("fleet:") else f"{tail} {t['title']}"
            line(t["id"], sec, 1, st, name, t.get("pr"), bar(t.get("progress")), nxt, suf)
        for rev, ids in by_rev.items():
            if len(ids) < 2:
                continue
            ts = [rows[i] for i in ids]
            progs = {tuple(t.get("progress") or ()) for t in ts}
            nexts = {t.get("next") or "" for t in ts}
            line(f"{head}/~{rev}", sec, 1, "waiting", f"{rev} 대기", None,
                 bar(ts[0].get("progress")) if len(progs) == 1 else "—",
                 nexts.pop() if len(nexts) == 1 and "" not in nexts else f"리뷰 · {rev}", fold=[t["pr"] for t in ts])
        return statuses

    def head_of(t):
        """The phase a sub-task hangs under, else its lead."""
        while t.get("parent") in rows and rows[t["parent"]]["kind"] == "task":
            t = rows[t["parent"]]
        return rows.get(t.get("parent"))

    alone = [rows[k] for k in order if not rows[k].get("parent") and rows[k]["kind"] == "task"]
    alone_rev = {}
    for t in alone:
        rev = t["status"] != "done" and waits_on(prs.get(t.get("pr")))
        if rev:
            alone_rev.setdefault((up(t, "repo"), up(t, "program"), rev), []).append(t)
    for k in order:
        r = rows[k]
        if r.get("parent"):
            continue
        sec = section_of(r)
        if k in unnamed:
            unnamed_secs.add(sec)
        if r["kind"] == "task":  # a sub-task with no lead: its own row, or a bullet when it waits with others on one person
            group = next((g for g in alone_rev.values() if r in g and len(g) >= 2), None)
            if group:
                bullets.setdefault((sec, waits_on(prs.get(r["pr"]))), []).append(r)
                continue
            st, nxt, suf = derive(r)
            line(k, sec, 0, st, r["title"], r.get("pr"), bar(r.get("progress")), nxt, suf)
            continue
        family = with_descendants(rows, {k}) - {k}
        phases = [rows[c] for c in order if c in family and rows[c]["kind"] == "phase"]
        tasks = [rows[c] for c in order if c in family and rows[c]["kind"] == "task"]
        heads = ([r] if any(head_of(t) is r for t in tasks) or not phases else []) + phases
        for h in heads:
            mine = [t for t in tasks if head_of(t) is h]
            home = [t for t in mine if up(t, "repo") == sec[0]]
            st, nxt, suf = derive(h)
            at = len(lines)
            line(h["id"], sec, 0, st, h["title"], h.get("pr"), bar(h.get("progress")), nxt, suf)
            kid_status = task_lines(h["id"], sec, home)
            for t in mine:
                if t not in home:  # another repository: its own section, under a row with only the lead's name
                    tsec = (up(t, "repo"), None)
                    if tsec not in sections:
                        sections.append(tsec)
                    if not any(l["key"] == f"{k}@{tsec[0]}" for l in lines):
                        line(f"{k}@{tsec[0]}", tsec, 0, None, r["title"], None, "—", "—")
                    kid_status += task_lines(f"{k}@{tsec[0]}", tsec, [t])
            lines[at]["status"] = worst(st, kid_status)

    # A section's merge target is the base most of its PRs share; a row with another base names its own.
    bases = {}
    for sec in sections:
        keys = [k for l in lines if l["sec"] == sec for k in ([l["pr"]] if l["pr"] else l["fold"] or [])]
        keys += [i["pr"] for (bsec, _), items in bullets.items() if bsec == sec for i in items]
        seen = [prs[k]["base"] for k in keys if (prs.get(k) or {}).get("base")]
        if seen:
            bases[sec] = max(dict.fromkeys(seen), key=seen.count)
    for l in lines:
        l["pr_state"] = pr_status(l["pr"], bases.get(l["sec"])) if l["pr"] else ""
        if l["fold"]:  # the numbers, each with its base when not the section's; the states go to the snapshot only
            to = [(prs.get(k) or {}).get("base") for k in l["fold"]]
            l["cell"] = "·".join((pr_text(k) if i == 0 else pr_num(k)) + (f"→{b}" if b and b != bases.get(l["sec"]) else "")
                                 for i, (k, b) in enumerate(zip(l["fold"], to)))
            l["pr_state"] = " · ".join(pr_status(k, bases.get(l["sec"])) for k in l["fold"])
        else:
            l["cell"] = f"{pr_text(l['pr'])} {l['pr_state']}".strip() if l["pr"] else ""

    # Bold: a row the last report did not have, or whose status, progress, next or PR state changed.
    snap = {}
    for l in lines:
        snap[l["key"]] = {"status": l["status"], "progress": l["progress"], "next": l["next"],
                          "pr": l["pr_state"]}
        prev = (last_rows or {}).get(l["key"])
        # A fleet row (--fill) is not in a report the board committed, so only a change to one already seen counts.
        if last_rows is None:
            continue
        if (not l["key"].startswith("fleet:") if prev is None
                else any(prev.get(f) != v for f, v in snap[l["key"]].items() if f in prev)):
            changed.append(l["key"])
    if last_rows is None:
        facts.append("지난 보고 없음, 굵게 없음")
    elif changed:
        facts.append(f"바뀐 행 {len(changed)}")
    else:
        facts.append("변화 없음")
    if oldest_probe and (now - oldest_probe).total_seconds() > limits["pr_warn_min"] * 60:
        facts.append(f"PR 상태 {ago((now - oldest_probe).total_seconds())} 기준")

    used = {l["status"] for l in lines}
    legend = ("🔄 진행 · ⏳ 사람 대기 · ⏸ 보류 · " + "".join(f"{ICON[s]} {w} · " for s, w in (("done", "완료"), ("blocked", "막힘"))
                                                         if s in used) + "막대=단계(추정)")
    states = " ".join(l["pr_state"] for l in lines if not l["fold"])
    pr_legend = [f"{m} {w}" for m, w in PR_LEGEND if m in states]
    if re.search(r"(?<![\w/])[0-9a-f]{7}(?![\w/])", states):
        pr_legend.append("7자리=머지 sha")
    local = now.astimezone()
    head = f"{local:%H:%M} · " + " · ".join(([headline] if headline else []) + facts[1 if headline else 0:])
    out = [head if tg else f"**{head}**", "", legend] + (["", "PR: " + " · ".join(pr_legend)] if pr_legend else [])
    many = {repo for repo, prog in sections if sum(1 for s in sections if s[0] == repo) > 1}
    width_ok, n_mark = True, 0

    def mark():
        nonlocal n_mark
        n_mark += 1
        return MARKS[n_mark - 1] if n_mark <= len(MARKS) else f"({n_mark})"

    def fit(text, cap, note, notes):
        """text, or its head cut to cap with a mark whose footnote carries note."""
        if table_width.text_width(text) <= cap:
            return text
        m = mark()
        notes.append(f"{m} {note}")
        return clip(text, cap - table_width.text_width(m)) + m

    for sec in sections:
        repo, prog = sec
        mine = [l for l in lines if l["sec"] == sec]
        if sec in unnamed_secs:
            prog, many = UNNAMED, many | {repo}
        title = (f"{repo} — {prog}" if repo in many and prog else repo) + (f" → {bases[sec]}" if sec in bases else "")
        out += ["", f"📋 {title}" if tg else f"**{title}**"]
        if tg:
            for l in mine:
                name = (f"{l['name']} {l['cell']}" if l["fold"] is None else f"{l['cell']} {l['name']}").strip()
                name += " (바뀜)" if l["key"] in changed else ""
                if l["status"] is None:  # the lead's name over its sub-tasks in another repository
                    out.append(name)
                    continue
                body = f"{ICON[l['status']]} {name} — {l['progress']} · {(l['next'] or '—') + l['suffix']}"
                out.append(("└ " if l["depth"] else "") + body)
        elif mine:  # a section of folded bullets only has no table
            cells, notes = [], []
            # A long progress (100/100) or a full PR column takes from the name, so next keeps its minimum.
            w = table_width.text_width
            fixed = (max(w(ICON.get(l["status"], "—")) for l in mine) + max(min(w(l["cell"] or "—"), CAPS["pr"]) for l in mine)
                     + max(w(x) for x in [l["progress"] for l in mine] + [HEADER[3]]))
            name_cap = min(CAPS["name"], COLS - table_width.BORDER_PER_COL * len(HEADER) - table_width.BORDER_EDGE
                           - fixed - CAPS["next_min"])
            for l in mine:  # name and PR first: the next column takes the width they leave
                prefix = "└ " if l["depth"] else ""
                name = prefix + fit(l["name"], name_cap - table_width.text_width(prefix), l["name"], notes)
                if l["fold"] and table_width.text_width(l["cell"]) > CAPS["pr"]:
                    m = mark()
                    plain = re.sub(LINK, r"\1", l["cell"])  # the numbers with their bases, no link
                    notes.append(f"{m} {plain}")
                    cell = f"{len(l['fold'])}건{m}"
                elif table_width.text_width(l["cell"]) > CAPS["pr"]:  # keep the state's head: ❗ comes first
                    m, num = mark(), pr_text(l["pr"])
                    notes.append(f"{m} {pr_num(l['pr'])} {l['pr_state']}")
                    room = CAPS["pr"] - table_width.text_width(f"{num} {m}")
                    cell = (f"{num} {clip(l['pr_state'], room)}" if room > 1 else num) + m
                else:
                    cell = l["cell"] or "—"
                cells.append([l, name, cell])
            wide = [max(table_width.text_width(r[i]) for r in [HEADER] + [[ICON.get(l["status"], "—"), n, c, l["progress"]]
                                                                         for l, n, c in cells]) for i in range(4)]
            room = max(CAPS["next_min"], COLS - table_width.BORDER_PER_COL * len(HEADER) - table_width.BORDER_EDGE - sum(wide))
            table = [HEADER]
            for l, name, cell in cells:
                nxt = (l["next"] or "—") + l["suffix"]
                nxt = fit(nxt, room, nxt, notes)
                if l["key"] in changed:
                    name = f"**{name}**"
                table.append([ICON.get(l["status"], "—"), name, cell, l["progress"], nxt])
            width_ok &= table_width.table_width(table) <= COLS
            out += ["", "| | 리드/Phase | PR | 진행 | 다음 |", "|---|---|---|---|---|"]
            out += ["| " + " | ".join(c.replace("|", "\\|") for c in row) + " |" for row in table[1:]]
            if notes:
                out += [""] + notes
        for (bsec, rev), items in bullets.items():
            if bsec != sec:
                continue
            out += ["", f"- ⏳ {len(items)}건 {rev} 대기 — 분야 {'·'.join(i['title'] for i in items)}"]
    if skipped:
        out += ["", skipped]
    decisions = fleet.open_decisions()
    asked = set((last or {}).get("decisions") or []) if last_rows is not None else set()
    if decisions:
        items = []
        for i, d in enumerate(decisions, 1):
            rec = d.get("recommend") and d["options"][d["recommend"] - 1]
            text = (f"{i}. ({d['id']}) {d['title']}" + (f" → 추천: {rec['label']}" + (f" — {rec['description']}" if rec["description"] else "")
                    if rec else " — 선택지: " + " / ".join(o["label"] for o in d["options"]) if d["options"] else ""))
            items.append(text + (" (지난 보고와 동일)" if d["id"] in asked else ""))
        out += ["", ("❓ 결정 필요" if tg else "❓ **결정 필요**") + " — " + items[0]] + [x for it in items[1:] for x in ("", it)]
    todo = store["owner_todo"]
    out += ["", ("🙋 지금 오너가 할 일" if tg else "🙋 **지금 오너가 할 일**") + " — " + (todo[0] if todo else "없음")]
    out += [x for it in todo[1:] for x in ("", it)]
    if store["later"]:
        out += ["", ("⏸ 나중에" if tg else "⏸ **나중에**") + " — " + " · ".join(f"{l['trigger']} {l['title']}" for l in store["later"])]
    return {"text": "\n".join(out), "headline_facts": facts, "changed": changed, "stale": stale, "width_ok": width_ok,
            "snapshot": {"v": 1, "ts": fleet.iso(now), "rows": snap, "decisions": [d["id"] for d in decisions]}}


def commit(scope, snapshot):
    """Save what was sent, for the next report's bold."""
    fleet.write_atomic(last_path(scope), json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n")
