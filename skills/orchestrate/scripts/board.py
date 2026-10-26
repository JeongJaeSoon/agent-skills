"""The board the brief skill's reports are rendered from: one row per lead, phase and sub-task, with its status,
progress, next step and the time it was last confirmed, and the renderer that turns it into the report.

The orchestrator or lead writes rows (`orch board set`) as reports come in; `orch brief render` (on request) and
`orch brief render --periodic` (on a schedule) read the board, the fleet collector's sessions and PR list
(state.json, prs.json, collected again first when older than state_min), the open decisions and the last sent table
(brief-last/<scope>.json) and print the report. The model passes only judgment: the headline sentence, decisions,
an insight block. The time, rows, bars and sections are the renderer's. PR state is never stored here: the renderer joins it from prs.json each time, so the two cannot disagree.
Nothing open is hidden for being old: a row past its staleness limit shows its age, a PR list probed long ago says so.
Only finished work folds: a ✅ row a day after its merge, a stage row finished or a week unchanged (the dashboard keeps both).

render() is the one implementation of the brief skill's report rules (sections, sub-task rows, the
same-reviewer fold, next steps under 진행 중 and 보류, bold for changed rows); test_board.py pins its output.
"""
import datetime as dt, difflib, fcntl, json, os, pathlib, re, shutil, sys

import fleet
import acks
import owner_requests
import stages
from stages import with_descendants

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "brief" / "scripts"))
import table_width  # noqa: E402

KINDS = ("lead", "phase", "task")
# owner: only the owner can unblock it. waiting: another person's review or reply. check: a row waiting on a person
# with nothing that says who, which the renderer derives and nobody sets.
ICON = {"moving": "🔄", "owner": "🙋", "waiting": "⏳", "check": "❔", "held": "⏸️", "blocked": "🔴", "done": "✅"}
ALIASES = {v: k for k, v in ICON.items() if k != "check"} | {"⏸": "held"}  # VS16 dropped, as some keyboards and pastes do
# The owner's turn outranks another person's: the head row shows what the owner can unblock now.
WORST = ("blocked", "owner", "waiting", "check", "held", "moving")
OWNER_NAMES = ("오너", "owner")
COLS = 78
LIMITS = {"row_min": 90, "pr_warn_min": 30, "pr_stale_min": 120, "state_min": 5, "done_fold_h": 24, "stage_idle_d": 7}  # overridden by the dashboard config's "board" key
DONE_KEEP_S = 7 * 86400  # a finished lead stays this long with its rows, then leaves the board
HEADER = ["", "리드/Phase", "PR", "진행"]
PR_LEGEND = (("❗", "사람이 풀 것"), ("✓", "머지 가능"), ("CI✗", "CI 실패"), ("CI…", "CI 진행"), ("?", "PR 상태 없음"))
UNNAMED = "(프로젝트 미지정)"
SKILLS_CONFIG = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG", "~/.claude/agent-skills.json")).expanduser()
ASK_WORD = {"request": "의뢰", "remind": "리마인드", "rerequest": "재요청"}
ASK_GONE = ("sent", "scheduled", "found")  # send-log sources where the message went out, or was already there


def path():
    return fleet.state_dir() / "board.json"


def last_path(scope):
    base = os.environ.get("ORCH_BRIEF_LAST")
    return (pathlib.Path(base).expanduser() if base else fleet.state_dir().parent / "brief-last") / f"{scope}.json"


def read():
    store = fleet.read_json(path(), {}) or {}
    return {"v": 1, "rows": store.get("rows") or {}, "order": store.get("order") or [], "leads": store.get("leads") or {},
            "doing": store.get("doing") or [], "later": store.get("later") or [], "owner_todo": store.get("owner_todo") or []}


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
    if s not in ICON or s == "check":
        raise ValueError(f"--status is one of {', '.join(s for s in ICON if s != 'check')} (or {' '.join(ALIASES)})")
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
        # created_at and by let the fleet tick link the row to the owner request that terminal was answering.
        row = {"id": rid, "kind": kind, "title": rid.rsplit("/", 1)[-1], "parent": parent, "status": "moving", "next": "",
               "created_at": fleet.iso(fleet.utcnow()), "by": os.environ.get("ORCA_TERMINAL_HANDLE")}
        rows[rid] = row
        store["order"].append(rid)
    was = (row.get("status"), row.get("owner"))
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
    if row["status"] not in ("waiting", "owner"):
        row.pop("since", None)
    elif "since" not in row or was != (row["status"], row.get("owner")):
        row["since"] = row["reported_at"]
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


def doing_add(title, owner, next_, waiting=False):
    """waiting: owner is a person this work waits on (a reply, a review), not the agent moving it."""
    if not all((x or "").strip() for x in (title, owner, next_)):
        raise ValueError("board doing add needs a title, --owner (who moves it) and --next (the next event)")
    item = {"title": one_line(title, 120), "owner": one_line(owner, 40), "next": one_line(next_, 60),
            **({"waiting": True, "since": fleet.iso(fleet.utcnow())} if waiting else {})}
    return change(lambda store: store["doing"].append(item))


def doing_drop(n):
    def go(store):
        if not 1 <= n <= len(store["doing"]):
            raise ValueError(f"no doing item {n}; there are {len(store['doing'])}")
        store["doing"].pop(n - 1)
    return change(go)


def same_later(a, b):
    """One held item written twice: the same trigger and a title reworded rather than another piece of work."""
    return a["trigger"] == b["trigger"] and difflib.SequenceMatcher(None, a["title"], b["title"]).ratio() >= 0.6


def work_key(item):
    """A held item's words and trigger, case and spacing aside, to catch one written twice. Its asides (a bracketed
    PR, a dash and a stage) stay in: they are what tells two held items apart."""
    return tuple(" ".join((item.get(f) or "").casefold().split()) for f in ("title", "trigger"))


def later_add(title, trigger, due=None):
    """Add a held item, or reword the one it repeats."""
    if not (title or "").strip() or not (trigger or "").strip():
        raise ValueError("board later add needs a title and --trigger (what brings it back)")
    item = {"title": one_line(title, 120), "trigger": one_line(trigger, 60), **({"due": due} if due else {})}

    def go(store):
        old = next((i for i, l in enumerate(store["later"]) if same_later(l, item)), None)
        if old is None:
            store["later"].append(item)
        else:
            store["later"][old] = item
    return change(go)


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


def last_asks():
    """{(pr, reviewer): (action, at, channel, thread)} of the newest review request or reminder that went out, lower-cased
    keys. Read from the send-log agent-skills.json → "brief" → "sendlog" names (watch-prs writes one); {} without the
    key or the file. A row written by hand has no action, and a `date` or `slack_ts` for its time: a request."""
    try:
        lines = pathlib.Path(json.loads(SKILLS_CONFIG.read_text())["brief"]["sendlog"]).expanduser().read_text().splitlines()
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    out = {}
    for line in lines:
        try:
            r = json.loads(line)
            action = r.get("action", "request")
            if action not in ASK_WORD or r.get("source", "sent") not in ASK_GONE:
                continue
            at = r.get("post_at") or r.get("at")
            at = float(at if at is not None else r["slack_ts"] if r.get("slack_ts") else fleet.parse(r["date"]).timestamp())
            k = (r["pr"].lower(), r["reviewer"].lower())
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if k not in out or at >= out[k][1]:
            out[k] = (action, at, r.get("channel"), r.get("thread"))
    return out


def is_owner(who, me):
    w = (who or "").strip().lstrip("@").lower()
    return w in OWNER_NAMES or bool(me) and w == me.lower()


def blocker(r, status, p, me):
    """Whom a row waiting on a person waits on: (status, detail). The PR's requested reviewers decide first, then the
    row's --owner; with neither, "check": the report asks rather than guesses."""
    if status == "owner":
        return "owner", {}
    if p and p.get("state") == "OPEN":
        if p.get("decision") == "CHANGES_REQUESTED":
            return "check", {"why": "수정요청 받음"}
        asked = [x["login"] for x in p.get("reviewers") or [] if x.get("requested") and not x.get("bot")]
        if asked and not me:  # prs.json has no viewer login: a request to the owner looks like anyone's
            return "check", {"why": "오너 login 모름"}
        if asked:
            return (("owner", {"what": "리뷰"}) if any(is_owner(a, me) for a in asked)
                    else ("waiting", {"who": ", ".join(asked), "what": "리뷰"}))
    if r.get("owner"):
        return ("owner", {}) if is_owner(r["owner"], me) else ("waiting", {"who": r["owner"]})
    return "check", {"why": "누구 대기인지 기록 없음"}


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


# A session's phase (fleet.py phase_of) read as a board status and next step.
PHASE_ROW = {"working": ("moving", "작업 중"), "idle": ("moving", "턴 종료"), "waiting": ("owner", "확인 대기"),
             "open": ("held", "에이전트 없음"), "offline": ("held", "세션 꺼짐")}


def fleet_rows(state, leads, board_rows):
    """Board rows built from the fleet collector's sessions (state.json), for what nobody wrote to the board: each lead
    session whose terminal no board lead holds, the cards under it, and the cards the orchestrator dispatched
    itself. A lead the board holds gets no row of its own, but its cards hang under its board row. Each card carries its
    newest PR; a card whose PR a board row already has is left to that row. Returns
    (rows, order); reported_at is the session's last activity, so a quiet one shows its age like a stale board row."""
    state = state or {}
    sessions = state.get("sessions") or []
    held = {l.get("handle"): name for name, l in leads.items()}
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
        rep = s.get("report") or {}
        if rep.get("next") and s.get("phase") != "offline":
            nxt = rep["next"]  # the card's own last milestone says more than "작업 중"
        # A lead reads as its session name (as in the sidebar), a card as the task it was dispatched with.
        title = s.get("name") if kind == "lead" else s.get("task_title") or s.get("name")
        rows[rid] = {"id": rid, "kind": kind, "title": one_line(title or rid, 80), "status": status, "next": one_line(nxt, 120),
                     # a lead's digest links its cards' PRs, which are the cards' rows
                     "pr": pr_of.get(s.get("id")) or (rep.get("pr") if kind != "lead" else None),
                     "reported_at": max(filter(None, (s.get("last_activity"), rep.get("at"))), default=None), "parent": parent,
                     # a card under a lead takes the lead's repo, so the lead heads one section
                     "repo": None if parent else s.get("gh_repo") or s.get("project") or "—", "program": program}
        order.append(rid)

    def shown(s):
        pr = pr_of.get(s.get("id")) or (s.get("report") or {}).get("pr")
        return (s.get("phase") != "offline" or pr) and pr not in on_board

    for lead in sessions:
        if lead.get("kind") != "orchestration":
            continue
        board_lead = next((held[t.get("handle")] for t in lead.get("terminals") or [] if t.get("handle") in held), None)
        if board_lead and board_lead not in board_rows:  # its rows were dropped from the board on purpose
            continue
        lid = f"fleet:{lead['id']}"
        if not board_lead:
            add(lid, lead, "lead", program=one_line(lead.get("name") or lid, 80))  # two leads in one repo: a section each
        for s in under.get(lead["id"], []):
            if shown(s):
                add(f"{lid}/{s['id']}", s, "task", board_lead or lid)
    kind = {s.get("id"): s.get("kind") for s in sessions}
    for s in sessions:
        # A card made with `orca worktree create` and no Run is standalone: it is work when it has a report file, a PR
        # or a dispatch, not a resident folder such as an automations runner.
        card = s.get("kind") == "task" or s.get("kind") == "standalone" and (
            s.get("report") or pr_of.get(s.get("id")) or s.get("dispatch"))
        if card and kind.get(s.get("parent")) == "orchestrator" and shown(s):
            add(f"fleet:{s['id']}", s, "task")
    return rows, order


DECISION_FORM = "<question>: <options> → 추천: <X> — <reason> · 답이 없으면 <default>"


def refresh(now, limit_min):
    """Collect the fleet again when state.json is older than limit_min: with no dashboard server running nothing else
    rewrites it, and a report read from it lists yesterday's cards. Returns a headline fact when it stays old."""
    at = fleet.parse((fleet.read_json(fleet.state_dir() / "state.json", None) or {}).get("generated_at"))
    if at and (now - at).total_seconds() <= limit_min * 60:
        return None
    try:
        f = fleet.Fleet()
        got = f.tick()
        err, gh_err = f.sources["orca"]["error"], f.sources["github"]["error"]
    except Exception as e:  # a collector fault must not take the report down with it
        got, err, gh_err = None, f"{type(e).__name__}: {e}", None
    out = []
    if got is None or err:
        age = f"{ago((now - at).total_seconds())} 기준" if at else "없음"
        out.append(f"세션 목록 {age} · 갱신 실패: {one_line(err, 60)}")
    if gh_err:
        out.append(f"PR 목록 갱신 실패: {one_line(gh_err, 60)}")
    return " · ".join(out) or None


def drop_acked(lines, prs, rows, have):
    """(lines without the rows the owner acked, every ackable row as {id, label, at, acked}, the hidden keys).
    Ackable is ✅, and for a head row every sub-task row under it too (acks.py): a closed PR's row is held, not done."""
    done, hidden = [], set()
    heads = [i for i, l in enumerate(lines) if l["depth"] == 0] + [len(lines)]
    for a, b in zip(heads, heads[1:]):
        group = lines[a:b]
        for l, whole in [(group[0], group)] + [(l, [l]) for l in group[1:]]:
            if not all(x["status"] == "done" for x in whole):
                continue
            aid, p, r = f"board:{l['key']}", prs.get(l["pr"]) or {}, rows.get(l["key"]) or {}
            done.append({"id": aid, "label": l["name"], "at": p.get("merged_at") or p.get("closed_at") or r.get("reported_at"),
                         "acked": aid in have})
            if aid in have:
                hidden |= {x["key"] for x in whole}
    return [l for l in lines if l["key"] not in hidden], done, hidden


def render(scope="top-level", channel="terminal", now=None, headline=None, skipped=None, fill=False, periodic=True,
           decisions=(), insight=None, fresh=False):
    """The brief skill's report as text, with what the caller needs around it. periodic (§1 Periodic report) adds
    the skipped actions and the reap line; on request (periodic=False) the stage table goes first. fill adds
    fleet_rows() for the leads and cards the board does not have; fresh collects the fleet again when its files are
    old. decisions are the model's own items (DECISION_FORM), numbered after the registered ones; insight goes after
    진행 중, so the report still ends on what the human acts on.

    Returns {"text", "headline_facts", "changed", "stale", "width_ok", "done", "snapshot"}. done is drop_acked()'s;
    snapshot is what commit() saves for the next report to compare against."""
    now = now or fleet.utcnow()
    tg = channel == "telegram"
    sd = fleet.state_dir()
    limits = {**LIMITS, **(fleet.config().get("board") or {})}
    for d in decisions:
        if "→ 추천:" not in d:
            raise ValueError(f"--decision is one line: {DECISION_FORM}")
    collected = refresh(now, limits["state_min"]) if fresh else None
    store = read()
    prs_file = fleet.read_json(sd / "prs.json", {}) or {}
    me = prs_file.get("me")
    prs = {k: {**v["detail"], "probed_at": v.get("probed_at")} for k, v in (prs_file.get("prs") or {}).items() if v.get("detail")}
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
    unnamed, unnamed_secs = set(unnamed_leads(rows)), set()

    oldest_probe, changed, stale, stale_board, facts, unknown = None, [], [], [], [], set()
    waits = {}  # row id -> blocker()'s detail, for the rows waiting on a person

    def derive(r):
        """(status, next, stale suffix) after the PR join and the session check."""
        status, nxt = r["status"], r.get("next") or ""
        p = prs.get(r.get("pr"))
        # The row's own PR outranks what the board says of it: GitHub has moved on since the row was written.
        if p and (p.get("merged_at") or p.get("state") == "MERGED"):
            status, nxt = "done", ""
        elif p and p.get("state") == "CLOSED":
            status, nxt = "held", "PR 닫힘"
        lead = store["leads"].get(lead_of(r)["id"])
        if status == "moving" and live is not None and lead and not lead.get("closed_at") and lead["handle"] not in live:
            status, nxt = "blocked", "세션 없음"
        if status in ("waiting", "owner"):
            status, waits[r["id"]] = blocker(r, status, p, me)
            waits[r["id"]].setdefault("since", r.get("since") or (p or {}).get("created"))
        suffix = ""
        at = fleet.parse(r.get("reported_at"))
        limit = (lead or {}).get("stale_after_min") or limits["row_min"]
        if status != "done" and at and (now - at).total_seconds() > limit * 60:
            suffix = f" · {ago((now - at).total_seconds())}"
            stale.append(r["id"])
            if not r["id"].startswith("fleet:"):  # a fleet row's age is its session's quiet, not a record left behind
                stale_board.append((now - at).total_seconds())
        return status, nxt, suffix

    def pr_text(key):
        nonlocal oldest_probe
        p = prs.get(key)
        if not p:
            unknown.add(key)
            return pr_num(key)
        probed = fleet.parse(p.get("probed_at"))
        # A merged or closed PR cannot change, and the collector probes it only once a day: its probe age means nothing.
        final = p.get("state") in ("MERGED", "CLOSED")
        if probed and not final and (oldest_probe is None or probed < oldest_probe):
            oldest_probe = probed
        warn = "⚠️" if not final and (not probed or (now - probed).total_seconds() > limits["pr_stale_min"] * 60) else ""
        return (f"{pr_num(key)}{warn}" if tg or not p.get("url") else f"[{pr_num(key)}]({p['url']}){warn}")

    def pr_status(key, base):
        """The PR's state, and its merge target when that is not the section's base."""
        p = prs.get(key)
        if not p:
            return "?"
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
        lines.append({"key": key, "sec": sec, "depth": depth, "status": status, "own": status, "name": name, "pr": pr,
                      "progress": progress, "next": nxt, "suffix": suffix, "fold": fold})

    def reviewer_wait(rev, ts):
        """(status, name) of the rows folded on one reviewer, with their detail in waits."""
        since = min(filter(None, (t.get("since") or (prs.get(t.get("pr")) or {}).get("created") for t in ts)), default=None)
        unblocks = "·".join(t["title"] for t in ts)
        if not me:
            return "check", f"{rev} 대기", {"why": "오너 login 모름", "since": since, "unblocks": unblocks}
        if is_owner(rev, me):
            return "owner", "오너 리뷰", {"what": "리뷰", "since": since, "unblocks": unblocks}
        return "waiting", f"{rev} 대기", {"who": rev, "what": "리뷰", "since": since, "unblocks": unblocks}

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
            st, name, waits[f"{head}/~{rev}"] = reviewer_wait(rev, ts)
            line(f"{head}/~{rev}", sec, 1, st, name, None,
                 bar(ts[0].get("progress")) if len(progs) == 1 else "—",
                 nexts.pop() if len(nexts) == 1 else "", fold=[t["pr"] for t in ts])
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

    lines, done, hidden = drop_acked(lines, prs, rows, have := acks.acked())

    # A ✅ row merged (or marked done) over done_fold_h ago leaves the table for one count per section; the dashboard
    # keeps it. A head stays while any row under it stays, and a row with no time to judge by stays.
    def done_long_ago(l):
        r = rows.get(l["key"]) or {}
        at = fleet.parse((prs.get(l["pr"]) or {}).get("merged_at") or r.get("done_at") or r.get("reported_at"))
        return l["status"] == "done" and at and (now - at).total_seconds() > limits["done_fold_h"] * 3600
    gone, folded = set(), {}
    for i in reversed(range(len(lines))):
        l = lines[i]
        kids = [j for j in range(i + 1, len(lines)) if lines[j]["sec"] == l["sec"]]
        kids = kids[:next((n for n, j in enumerate(kids) if lines[j]["depth"] == 0), len(kids))] if not l["depth"] else []
        if all(j in gone for j in kids) and (done_long_ago(l) or l["status"] is None and kids):
            gone.add(i)
            folded[l["sec"]] = folded.get(l["sec"], 0) + (l["status"] is not None)
    lines[:] = [l for i, l in enumerate(lines) if i not in gone]
    sections[:] = [s for s in sections if any(l["sec"] == s for l in lines) or any(b == s for b, _ in bullets)]

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
    if not lines:
        facts.append("리드·카드 행 없음")
    elif last_rows is None:
        facts.append("지난 보고 없음, 굵게 없음")
    elif changed:
        facts.append(f"바뀐 행 {len(changed)}")
    else:
        facts.append("변화 없음")
    if oldest_probe and (now - oldest_probe).total_seconds() > limits["pr_warn_min"] * 60:
        facts.append(f"PR 상태 {ago((now - oldest_probe).total_seconds())} 기준")
    if unknown:
        facts.append(f"PR 상태 없음 {len(unknown)}")
    if stale_board:
        facts.append(f"오래된 board 행 {len(stale_board)} ({'' if len(stale_board) == 1 else '최대 '}{ago(max(stale_board))})")
    if hidden:
        facts.append(f"확인함으로 숨긴 행 {len(hidden)}")
    if collected:
        facts.append(collected)
    stage_t, stage_gone = stages.current(stages.table(state), now, limits["stage_idle_d"] * 86400, state) if not periodic else (None, 0)
    if sum(folded.values()) or stage_gone:
        facts.append("접음 " + ", ".join(f"{w} {n}" for w, n in (("완료", sum(folded.values())), ("단계표", stage_gone)) if n)
                     + " (대시보드)")

    used = {l["status"] for l in lines}
    legend = ("🔄 진행 · 🙋 오너 차례 · ⏳ 다른 사람 대기 · ⏸️ 보류 · "
              + "".join(f"{ICON[s]} {w} · " for s, w in (("done", "완료"), ("blocked", "막힘"), ("check", "확인 필요")) if s in used)
              + "막대=단계(추정)")
    states = " ".join(l["pr_state"] for l in lines if not l["fold"])
    pr_legend = [f"{m} {w}" for m, w in PR_LEGEND if m in states]
    if re.search(r"(?<![\w/])[0-9a-f]{7}(?![\w/])", states):
        pr_legend.append("7자리=머지 sha")
    local = now.astimezone()
    head = f"{local:%H:%M} · " + " · ".join(([headline] if headline else []) + facts[1 if headline else 0:])
    out = [head if tg else f"**{head}**"]
    width_ok = True
    if stage_t and stage_t["rows"]:
        stage = stages.render_telegram(stage_t) if tg else stages.render_md(stage_t)
        out += ["", stage]
        width_ok &= all(table_width.table_width(t) <= COLS for _, t in table_width.tables(stage))
    if lines:
        out += ["", legend] + (["", "PR: " + " · ".join(pr_legend)] if pr_legend else [])
    many = {repo for repo, prog in sections if sum(1 for s in sections if s[0] == repo) > 1}

    def label(l):
        """A phase is named with its lead outside the table, where no section heading says whose it is."""
        r = rows.get(l["key"])
        return f"{lead_of(r)['title']} {l['name']}" if r and r["kind"] == "phase" else l["name"]

    def since(at):
        at = fleet.parse(at)
        return f" · {ago((now - at).total_seconds())}부터" if at else ""

    def turn_item(what, where, unblocks):
        return f"{what}{f' · {where}' if where else ''} → {unblocks}"

    asks = last_asks()

    def asked(who, keys):
        """` · 마지막 리마인드 rev-a 3h 전 (C0FAKE 스레드)`: what last went out to the people this waits on, if logged."""
        found = [(a, w) for k in keys for w in who.split(", ")
                 if (a := asks.get(((k or "").lower(), w.strip().lstrip("@").lower())))]
        if not found:
            return ""
        (action, at, channel, thread), w = max(found, key=lambda f: f[0][1])
        when = ago(now.timestamp() - at) if at <= now.timestamp() else f"{dt.datetime.fromtimestamp(at).astimezone():%m-%d %H:%M} 예약"
        where = " ".join(filter(None, (channel, thread and "스레드")))
        return f" · 마지막 {ASK_WORD[action]} {w} {when}{f' ({where})' if where else ''}"

    def theirs_item(who, what, at, unblocks, nxt, keys=()):
        return (f"{who}{f' · {what}' if what else ''}{since(at)}{asked(who, keys)} → {unblocks}"
                f"{f' · 다음: {nxt}' if nxt else ''}")

    # The next steps leave the tables, so no cell is ever cut: a held row's goes to 보류 as its condition, one the owner
    # unblocks to 지금 오너가 할 일, one another person unblocks to 다른 사람 대기, every other open row's to 진행 중 in
    # table order, a sub-task after its head row. Each row goes to one list, by its own status, not its children's.
    moving, held, turn, theirs = [], [], [], []
    for sec in sections:
        head, head_out = None, False
        for l in (l for l in lines if l["sec"] == sec):
            if l["depth"] == 0:
                head, head_out = l, False
            st, w = l["own"], waits.get(l["key"]) or {}
            if st in (None, "done"):
                continue
            if st == "held":
                held.append(f"{l['next'] or '—'} → {label(l)}")
                continue
            where = l["cell"].split(" ")[0] if l["pr"] else l["cell"]
            if st == "owner":
                turn.append(turn_item(w.get("what") or l["next"] or "응답", where, w.get("unblocks") or label(l)))
                continue
            if st == "waiting":
                theirs.append(theirs_item(w["who"], f"{w['what']} {where}".strip() if w.get("what") else "", w.get("since"),
                                          w.get("unblocks") or label(l), l["next"], l["fold"] or [l["pr"]]))
                continue
            if l["depth"] and not head_out:
                # a head that waits on a person is an item of its own list: here it only names the sub-tasks' group
                moving.append(" ".join(([ICON[head["own"]]] if head["own"] not in (None, "owner", "waiting") else [])
                                       + [label(head)]))
                head_out = True
            nxt = f"확인 필요: {w['why']}" if st == "check" else l["next"] or "—"
            moving.append(("└ " if l["depth"] else "") + f"{ICON[st]} {label(l)} — {nxt + l['suffix']}")
            head_out = head_out or not l["depth"]
    bullet_of = {}
    for (bsec, rev), items in bullets.items():
        st, name, w = reviewer_wait(rev, items)
        nums = "·".join(pr_num(i["pr"]) for i in items)
        bullet_of[(bsec, rev)] = f"{ICON[st]} {len(items)}건 {name}"
        if st == "owner":
            turn.append(turn_item("리뷰", nums, w["unblocks"]))
        elif st == "waiting":
            theirs.append(theirs_item(rev, f"리뷰 {nums}", w["since"], w["unblocks"], "", [i["pr"] for i in items]))
        else:
            moving.append(f"{ICON[st]} {w['unblocks']} — 확인 필요: {w['why']}")
    for d in store["doing"]:
        if is_owner(d["owner"], me):
            turn.append(turn_item(d["next"], "", d["title"]))
        elif d.get("waiting"):
            theirs.append(theirs_item(d["owner"], "", d.get("since"), d["title"], d["next"]))
        else:
            moving.append(f"{ICON['moving']} {d['title']} — {d['next']} · {d['owner']}")

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
                out.append(("└ " if l["depth"] else "") + f"{ICON[l['status']]} {name} — {l['progress']}")
        elif mine:  # a section of folded bullets only has no table
            table = [HEADER]
            for l in mine:
                name = ("└ " if l["depth"] else "") + (f"**{l['name']}**" if l["key"] in changed else l["name"])
                table.append([ICON.get(l["status"], "—"), name, l["cell"] or "—", l["progress"]])
            # Nothing is cut to fit: a table too wide overflows, and the render reports it.
            width_ok &= table_width.table_width(table) <= COLS
            out += ["", "| | 리드/Phase | PR | 진행 |", "|---|---|---|---|"]
            out += ["| " + " | ".join(c.replace("|", "\\|") for c in row) + " |" for row in table[1:]]
        if folded.get(sec):
            out += ["", f"{ICON['done']} 그 전 완료 {folded[sec]}건 (대시보드)"]
        for (bsec, rev), items in bullets.items():
            if bsec != sec:
                continue
            out += ["", f"- {bullet_of[(bsec, rev)]} — 분야 {'·'.join(i['title'] for i in items)}"]
    if periodic and skipped:
        out += ["", skipped]
    if periodic and (reaped := reap_line(now)):
        out += ["", reaped]
    if moving:
        out += ["", "🔄 진행 중" if tg else "🔄 **진행 중**", ""] + [it if tg else f"- {it}" for it in moving]
    if theirs:
        out += ["", "⏳ 다른 사람 대기" if tg else "⏳ **다른 사람 대기**", ""] + [it if tg else f"- {it}" for it in theirs]
    if insight:
        out += ["", insight.strip()]
    own, decisions = list(decisions), fleet.open_decisions()
    asked = set((last or {}).get("decisions") or []) if last_rows is not None else set()
    if decisions or own:
        items = []
        for i, d in enumerate(decisions, 1):
            rec = d.get("recommend") and d["options"][d["recommend"] - 1]
            text = (f"{i}. ({d['id']}) {d['title']}" + (f" → 추천: {rec['label']}" + (f" — {rec['description']}" if rec["description"] else "")
                    if rec else " — 선택지: " + " / ".join(o["label"] for o in d["options"]) if d["options"] else ""))
            items.append(text + (" (지난 보고와 동일)" if d["id"] in asked else ""))
        items += [f"{i}. {one_line(d, 400)}" for i, d in enumerate(own, len(decisions) + 1)]
        out += ["", ("❓ 결정 필요" if tg else "❓ **결정 필요**") + " — " + items[0]] + [x for it in items[1:] for x in ("", it)]
    todo = store["owner_todo"] + turn
    out += ["", ("🙋 지금 오너가 할 일" if tg else "🙋 **지금 오너가 할 일**") + ("" if todo else " — 없음")]
    out += [""] + [it if tg else f"- {it}" for it in todo] if todo else []
    shown = {k: q for k, q in owner_requests.fold(owner_requests.read()).items() if f"req:{k}" not in have}
    if asked := owner_requests.line(owner_requests.summary(now, shown)):
        out += ["", ("📝 요청" if tg else "📝 **요청**") + " — " + asked]
    later = []
    for l in reversed(store["later"]):  # a held item written twice before later_add merged them: the newest wording
        if not any(same_later(l, k) or work_key(l) == work_key(k) for k in later):
            later.insert(0, l)
    if held := list(dict.fromkeys(held + [f"{l['trigger']} → {l['title']}" for l in later])):
        out += ["", "⏸️ 보류" if tg else "⏸️ **보류**", ""] + [it if tg else f"- {it}" for it in held]
    return {"text": "\n".join(out), "headline_facts": facts, "changed": changed, "stale": stale, "width_ok": width_ok, "done": done,
            "snapshot": {"v": 1, "ts": fleet.iso(now), "rows": snap, "decisions": [d["id"] for d in decisions]}}


def reap_line(now):
    """The last reap-hard pass, so a stopped cleanup shows in the report; None when it never ran here."""
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "scripts"))
        import reap_hard
    except ImportError:  # an install without the repo's scripts/
        return None
    return reap_hard.last_line(now.timestamp())


def commit(scope, snapshot):
    """Save what was sent, for the next report's bold."""
    fleet.write_atomic(last_path(scope), json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n")
