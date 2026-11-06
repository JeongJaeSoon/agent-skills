"""The board the brief skill's reports are rendered from: one row per lead, phase and sub-task, with its status,
progress, next step and the time it was last confirmed, and the renderer that turns it into the report.

The board lives in agent-skills.db (store.py). A card's or lead's own facts stay Orca's: the write bans below refuse
what would copy them here. The orchestrator or lead writes rows (`orch board set`) as reports come in; `orch brief render` (on request) and
`orch brief render --periodic` (on a schedule) read the board, the fleet collector's sessions and PR list
(state.json, prs.json, collected again first when older than state_min), the open decisions and the last sent table
(brief-last/<scope>.json) and print the report. The model passes only judgment: the headline sentence, decisions,
an insight block. The time, rows, bars and sections are the renderer's. PR state is never stored here: the renderer joins it from prs.json each time, so the two cannot disagree.
Nothing open is hidden for being old: a row past its staleness limit shows its age, a PR list probed long ago says so.
Only finished work folds: a ✅ row a day after its merge, a stage row finished or a week unchanged (the dashboard keeps both).

render() is the one implementation of the brief skill's report rules (sections, sub-task rows, the
same-reviewer fold, next steps under 진행 중 and 보류, bold for changed rows); test_board.py pins its output.
"""
import collections, datetime as dt, difflib, json, os, pathlib, re, sys, urllib.parse

import fleet
import acks
import rowstate
import stages
import store as store_db  # `store` is the board dict everywhere below
import tidy
from stages import with_descendants

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "brief" / "scripts"))
import table_width  # noqa: E402

KINDS = ("lead", "phase", "task")
# What each status means and which rule gives it is rowstate.py's; the board only stores what a person set.
ICON = rowstate.ICON
ALIASES = {v: k for k, v in ICON.items() if k != "check"} | {"⏸": "held"}  # VS16 dropped, as some keyboards and pastes do
OWNER_NAMES = ("오너", "owner")
COLS = 78
LIMITS = {"row_min": 90, "pr_warn_min": 30, "pr_stale_min": 120, "state_min": 5, "done_fold_h": 24, "stage_idle_d": 7,
          "decision_stale_h": 12, "order_unlinked_min": 10, "subagent_stale_h": fleet.SUBAGENT_STALE_H}  # overridden by the dashboard config's "board" key
FILLED = ("fleet:", "agent:")  # the ids of rows derived from the fleet (--fill), not written to the board
DONE_KEEP_S = 7 * 86400  # a finished lead stays this long with its rows, then leaves the board
HEADER = ["", "리드/Phase", "PR", "진행", "다음", "세션"]


def table_rows_of(head, body):
    """The rows a repository's table is written from, the header first."""
    return [head] + body


PR_LEGEND = (("❗", "사람이 풀 것"), ("✓", "머지 가능"), ("CI✗", "CI 실패"), ("CI…", "CI 진행"), ("?", "PR 상태 없음"))
SKILLS_CONFIG = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG", "~/.claude/agent-skills.json")).expanduser()
ASK_WORD = {"request": "의뢰", "remind": "리마인드", "rerequest": "재요청"}
ASK_GONE = ("sent", "scheduled", "found")  # send-log sources where the message went out, or was already there


def last_path(scope):
    base = os.environ.get("ORCH_BRIEF_LAST")
    return (pathlib.Path(base).expanduser() if base else fleet.state_dir().parent / "brief-last") / f"{scope}.json"


def read():
    """The open board, each item with its id. Raises store.NotMigrated before `orch db import`."""
    return store_db.board()


def change(fn):
    """Run fn(store) in one DB write transaction, each change with its event; a finished lead leaves here."""
    def go(store):
        out = fn(store)
        cutoff = fleet.iso(fleet.utcnow() - dt.timedelta(seconds=DONE_KEEP_S))
        gone = {k for k, r in store["rows"].items() if not r.get("parent") and (r.get("done_at") or "9") < cutoff}
        store["rows"] = {k: r for k, r in store["rows"].items() if k not in with_descendants(store["rows"], gone)}
        store["order"] = [k for k in store["order"] if k in store["rows"]]
        return out
    return store_db.change_board(go, os.environ.get("ORCA_TERMINAL_HANDLE"))


def fleet_view():
    """state.json's sessions by id, or None when the collector has not written it: what the write bans below check."""
    state = fleet.read_json(fleet.state_dir() / "state.json", None)
    return state if state and state.get("sessions") else None


def no_fleet(what):
    print(f"warning: no fleet state; {what} not checked against Orca", file=sys.stderr)


def linked_card(rid, row, fields, cards):
    """The card a row about rid is, by its --worktree or by the name in its id and title."""
    title = fields.get("title") or (row or {}).get("title") or rid.rsplit("/", 1)[-1]
    return cards.get((row or {}).get("worktree")) or tidy.named(f"{rid} {title}", cards)


def check_row_write(rid, row, fields):
    """A card's PR and worktree are Orca's: a linked row shows them without storing them. An unlinked row may name its
    card with --worktree, and that is all it stores."""
    pr, worktree = fields.get("pr"), fields.get("worktree")
    if pr is not None and not fleet.FULL_PR_KEY_RE.fullmatch(pr):
        raise ValueError(f"--pr is OWNER/REPO#N (e.g. acme/app#441), not {pr!r}")
    if pr is None and worktree is None and not fields.get("title"):
        return
    state = fleet_view()
    if state is None:
        return None if pr is None and worktree is None else no_fleet("--pr/--worktree")
    cards = tidy.cards_of(state)
    card = linked_card(rid, row, fields, cards)
    if pr is not None and (card or worktree in cards):
        raise ValueError(f"row {rid} is linked to card {(card or cards[worktree])['id']}: Orca knows its PR, so --pr is not stored")
    if worktree is not None and card and worktree != card["id"]:
        raise ValueError(f"row {rid} is linked to card {card['id']}: drop the row to link another")
    if row and (card or worktree in cards):
        row.pop("pr", None)  # Orca's from now on


def one_line(text, limit=None):
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


def priority_of(text):
    """`RANK:NAME`, the owner's priority group of a row (and of the rows under it): groups sort by RANK, then NAME."""
    m = re.fullmatch(r"(\d{1,3}):\s*(\S.*)", (text or "").strip())
    name = one_line(m[2]) if m else ""
    if not m or " — " in name:  # the dashboard splits the summary line at the first " — "; checked once whitespace is folded
        raise ValueError("--priority is RANK:NAME (a number, then the group's name without ' — '; groups show in RANK order), or - to clear")
    return f"{int(m[1])}:{name}"


def worktree_of(handle):
    """The Orca worktree whose terminal the handle is, from the fleet collector's last state; None when not seen."""
    state = fleet.read_json(fleet.state_dir() / "state.json", None) or {}
    return next((s["id"] for s in state.get("sessions") or [] for t in s.get("terminals") or [] if t.get("handle") == handle), None)


def lead_add(name, handle, repo, run=None, stale_min=None, program=None, worktree=None):
    if not name or not handle or not repo:
        raise ValueError("board lead add needs a name, --handle and --repo")
    # A handle changes when the lead's terminal restarts; its worktree does not, so the brief links the lead by that.
    worktree = worktree or worktree_of(handle)
    state = fleet_view()
    if state is None:
        no_fleet("lead add")
    elif any(s.get("id") == worktree and s.get("kind") == "orchestration" for s in state["sessions"]):
        raise ValueError(f"Orca already knows lead {worktree}: `orch board set {name} --kind lead --repo {repo} --worktree {worktree}`"
                         " (with --program P) links a row to it")

    def go(store):
        store["leads"][name] = {"name": name, "handle": handle, "repo": repo, "run": run, "started_at": fleet.iso(fleet.utcnow()),
                                **({"worktree": worktree} if worktree else {}),
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
        if rid.startswith("prio:"):  # the ids of the priority rules (board priority set)
            raise ValueError("a row id cannot start with prio: (those are priority rules)")
        parent = fields.get("parent") or (rid.rsplit("/", 1)[0] if "/" in rid else None)
        if parent and parent not in rows:
            raise ValueError(f"no row {parent} to be the parent; add it first")
        kind = fields.get("kind") or ("task" if parent else "lead")
        if not parent and kind != "lead" and not fields.get("repo"):
            raise ValueError(f"a top-level {kind} row needs --repo")
        if not parent and not fields.get("repo"):
            raise ValueError(f"a lead row ({rid}) needs --repo")
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
        if k in ("owner", "after", "priority") and v.strip() in ("-", "—", ""):  # the record path's way to take a wait back
            row.pop(k, None)
            continue
        if k == "after" and not re.fullmatch(r"[\w.-]+/[\w.-]+#\d+", v.strip()):
            raise ValueError("--after is the PR this row waits to merge first: owner/repo#N")
        if k == "parent":
            raise ValueError(f"{rid} already hangs off {row.get('parent')}; drop it to move it")
        if k == "kind" and v not in KINDS:
            raise ValueError(f"--kind is one of {', '.join(KINDS)}")
        row[k] = (status_of(v) if k == "status" else progress_of(v) if k == "progress" else
                  priority_of(v) if k == "priority" else
                  one_line(v) if isinstance(v, str) else v)
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
    """Leads with no program in a repository that has two or more leads: no program report (--scope) covers them."""
    by_repo = {}
    for r in rows.values():
        if not r.get("parent") and r["kind"] == "lead":
            by_repo.setdefault(r.get("repo"), []).append(r)
    return [r["id"] for leads in by_repo.values() if len(leads) > 1 for r in leads if not r.get("program")]


def unnamed_warning():
    ids = unnamed_leads(read()["rows"])
    return ids and (f"warning: no --program on {', '.join(ids)}, beside another lead in the same repository; a program "
                    f"report (--scope) leaves them out until each gets --program <project>")


def set_row(rid, **fields):
    """Add or update one row; only the fields given change. reported_at becomes now: the row was confirmed now."""
    if not rid:
        raise ValueError("board set needs a row id")

    def go(store):
        check_row_write(rid, store["rows"].get(rid), fields)
        return put(store, rid, fields)
    return change(go)


MATCH_KINDS = ("worktree", "program", "repo")  # most specific first: the order a rule is looked up in


def match_of(text):
    """`KIND:VALUE` a priority rule matches by: the Orca worktree a card runs in, a lead's program, or its repository."""
    kind, _, value = (text or "").strip().partition(":")
    value = one_line(value)
    if kind not in MATCH_KINDS or not value or (kind == "repo" and not re.fullmatch(r"[\w.-]+/[\w.-]+", value)):
        raise ValueError(f"the match is worktree:ID, program:NAME or repo:OWNER/NAME, not {text!r}")
    return f"{kind}:{value}"


def priority_set(match, text):
    """Rows that match get the group `text` (RANK:NAME) unless they or a nearer row carry their own --priority. This is
    how work the board has no row of (the fleet's lead and card rows) gets a group."""
    match, group = match_of(match), priority_of(text)

    def go(store):
        store["groups"][f"prio:{match}"] = {"id": f"prio:{match}", "kind": store_db.GROUP_KIND, "parent": None,
                                            "match": match, "priority": group}
    return change(go)


def priority_drop(match):
    match = match_of(match)

    def go(store):
        if store["groups"].pop(f"prio:{match}", None) is None:
            raise ValueError(f"no priority rule for {match}")
    return change(go)


def priority_rules(groups):
    """{KIND: {VALUE: RANK:NAME}} from the board's rule rows."""
    out = {k: {} for k in MATCH_KINDS}
    for g in groups.values():
        kind, _, value = g["match"].partition(":")
        out[kind][value] = g["priority"]
    return out


def drop(rid):
    def go(store):
        if rid not in store["rows"]:
            raise ValueError(f"no row {rid}")
        gone = with_descendants(store["rows"], {rid})
        store["rows"] = {k: r for k, r in store["rows"].items() if k not in gone}
    return change(go)


def live_work(title):
    """What Orca already shows for title: the live card it names, or a subagent by id or description. None when neither."""
    state = fleet_view()
    if state is None:
        return no_fleet("doing add")
    alive = [s for s in state["sessions"] if s.get("state") != "offline"]
    if card := tidy.named(title, tidy.cards_of({**state, "sessions": alive})):
        return f"card {card['id']}"
    low = title.lower()
    return next((f"subagent {a['agent']}" for s in alive for a in s.get("subagents") or []
                 if not a.get("stale") and any(w and w.lower() in low for w in (a.get("agent"), a.get("desc")))), None)


def doing_add(title, owner, next_, waiting=False, force=False, why=None):
    """waiting: owner is a person this work waits on (a reply, a review), not the agent moving it. force: add it though
    Orca shows that work, with why in the item's event."""
    if not all((x or "").strip() for x in (title, owner, next_)):
        raise ValueError("board doing add needs a title, --owner (who moves it) and --next (the next event)")
    if force and not (why or "").strip():
        raise ValueError("board doing add --force needs --why")
    if not force and (seen := live_work(title)):
        raise ValueError(f"{seen} already shows this work in its own row; --force --why W adds it anyway")
    item = {"title": one_line(title), "owner": one_line(owner), "next": one_line(next_),
            **({"waiting": True, "since": fleet.iso(fleet.utcnow())} if waiting else {}),
            **({"forced": one_line(why)} if force else {})}
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


def shown_later(q, shut):
    """The store indices of the held items the queue shows, in turn: not closed, and of one written twice before
    later_add merged them, the newest wording. Its positions (1 first) are the numbers the list shows and the
    commands take."""
    shown = []
    for i in reversed(range(len(q))):
        l = q[i]
        if l["id"] not in shut and not any(same_later(l, q[k]) or work_key(l) == work_key(q[k]) for k in shown):
            shown.insert(0, i)
    return shown


def queue_at(q, n):
    """The store index of the held item the list shows at n."""
    shown = shown_later(q, tidy.closed())
    if not 1 <= n <= len(shown):
        raise ValueError(f"no later item {n}; there are {len(shown)}")
    return shown[n - 1]


def queue_insert(q, item, to):
    """Put item before the one the list shows at to (1 first; None or past the end: last)."""
    shown = shown_later(q, tidy.closed())
    q.insert(len(q) if to is None or to > len(shown) else shown[to - 1], item)


def later_add(title, trigger, due=None, who=None, at=None):
    """Add a held item to the queue at position at (1 first; the end by default), or reword the one it repeats in its
    place (moved to at when given). The list's order is the queue's: the first is next."""
    if not (title or "").strip() or not (trigger or "").strip():
        raise ValueError("board later add needs a title and --trigger (what makes it next)")
    at = queue_pos(at)
    item = {"title": one_line(title), "trigger": one_line(trigger), **({"due": due} if due else {}),
            **({"who": one_line(who)} if (who or "").strip() else {})}

    def go(store):
        q = store["later"]
        old = next((i for i, l in enumerate(q) if same_later(l, item)), None)
        if old is not None:
            item["id"] = q.pop(old)["id"]
        if at is None and old is not None:
            q.insert(old, item)
        else:
            queue_insert(q, item, at)
    return change(go)


def queue_pos(at):
    if at is None:
        return None
    if not str(at).isdigit() or int(at) < 1:
        raise ValueError("--at and --to take a queue position, 1 or more")
    return int(at)


def later_move(n, to):
    """Move held item n to position to in the queue (past the end: last)."""
    to = queue_pos(to)
    if to is None:
        raise ValueError("board later move needs --to N")

    def go(store):
        q = store["later"]
        queue_insert(q, q.pop(queue_at(q, n)), to)
    return change(go)


def later_drop(n):
    def go(store):
        store["later"].pop(queue_at(store["later"], n))
    return change(go)


def moment(due, now):
    """--due as the ISO8601 moment it names now, or None. A to-do keeps this, not the words: `10/1` read again next
    year would name next year's."""
    at = fleet.due_at(due, None, now)
    return at and at.isoformat()


def check_due(due, now):
    at = due and moment(due, now)
    if due and not at:
        raise ValueError("--due is YYYY-MM-DD[ HH:MM], M/D[ HH:MM] or ISO8601")
    return at


def todo_add(title, due=None, after=None, who=None):
    """A to-do: work that will be done when its date (--due) or condition (--after) comes, by who. Rewords the one it
    repeats, as later_add does."""
    if not (title or "").strip() or not ((due or "").strip() or (after or "").strip()) or not (who or "").strip():
        raise ValueError("board todo add needs a title, --due D or --after C (when it starts) and --who W (who does it);"
                         " work with no when is `board doing add` or `board later add`")
    now = fleet.utcnow()
    due = check_due(due, now)
    item = {"title": one_line(title), "who": one_line(who), **({"due": due} if due else {}),
            **({"after": one_line(after)} if after else {}), "added": fleet.iso(now)}

    def go(store):
        same = (i for i, t in enumerate(store["todo"]) if (t.get("due"), t.get("after")) == (item.get("due"), item.get("after"))
                and difflib.SequenceMatcher(None, t["title"], item["title"]).ratio() >= 0.6)
        old = next(same, None)
        if old is None:
            store["todo"].append(item)
        else:
            store["todo"][old] = {**item, "id": store["todo"][old]["id"]}
    return change(go)


def todo_drop(n):
    def go(store):
        if not 1 <= n <= len(store["todo"]):
            raise ValueError(f"no todo item {n}; there are {len(store['todo'])}")
        store["todo"].pop(n - 1)
    return change(go)


def todo_from_later(n, due=None, who=None):
    """Move held item n to the to-do list: its trigger becomes --after, its --due carries over unless due is given."""
    if not (who or "").strip():
        raise ValueError("board todo from-later needs --who W (who does it)")
    now = fleet.utcnow()
    due = check_due(due, now)

    def go(store):
        l = store["later"].pop(queue_at(store["later"], n))
        due_ = due or l.get("due") and (moment(l["due"], now) or one_line(l["due"]))
        store["todo"].append({"id": l["id"], "title": l["title"], "who": one_line(who), **({"due": due_} if due_ else {}),
                              "after": l["trigger"], "added": fleet.iso(now)})
    return change(go)


def set_now(lines):
    """Replace the owner's now-list. A line it already has keeps its id, so a close on it stands."""
    def go(store):
        had = {t["title"]: t for t in reversed(store["owner_todo"])}
        store["owner_todo"] = [had.pop(t, None) or {"title": t} for t in (one_line(l) for l in lines if l.strip())]
    return change(go)


todo_when = fleet.todo_when


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


is_owner = rowstate.is_owner


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


# A row's session as the 세션 column says it: its agent is working, it stopped (a prompt waits on a person, or its turn
# ended STALL_MIN ago), or no session runs it (ended, agent exited, or the row links none).
SESSION_WORD = {"working": "작업 중", "stalled": "멈춤", "none": "세션 없음"}
STALL_MIN = 15  # dash.py IDLE_MIN: a turn that ended this long ago is a stop, not a pause between turns


def session_state(s, now):
    if not s or s.get("phase") not in ("working", "waiting", "idle"):
        return "none"
    if s["phase"] == "working":
        return "working"
    at = fleet.parse(s.get("last_activity"))
    return "stalled" if s["phase"] == "waiting" or not at or (now - at).total_seconds() >= STALL_MIN * 60 else "working"


def last_report(s):
    """(line, at) a session last reported: its report file's newest milestone, else its agent's last reply."""
    rep = s.get("report") or {}
    if rep.get("next"):
        return rep["next"], rep.get("at")
    a = next((a for a in s.get("agents") or [] if a.get("last")), None)
    return (fleet.first_line(a["last"]), a.get("updated")) if a else (None, None)


# The dispatch convention puts the goal sentence and its brief's path on the spec's first line, which Orca makes the
# task's name: that is an instruction, not a name, so such a card goes by its worktree's name.
BRIEF_POINTER_RE = re.compile(r"(?:브리프|[Bb]rief)\s*[:：]\s*[~/]|^/goal\b")


def card_title(s):
    task = s.get("task_title")
    return s.get("name") if not task or BRIEF_POINTER_RE.search(task) and s.get("name") else task


# A session's phase (fleet.py phase_of) read as a board status and next step.
PHASE_ROW = {"working": ("moving", "작업 중"), "idle": ("moving", "턴 종료"), "waiting": ("owner", "확인 대기"),
             "open": ("held", "에이전트 없음"), "offline": ("held", "세션 꺼짐")}
PATROL_CHECK_S = 3600  # patrol rounds every 30 min; a `check` older than two of them says nothing of now


def patrol_checks(now):
    """Card path -> (why, when the round judged it), for the cards the last patrol round found stalled on screen or in
    a repeat (patrol.py H, R: patrol-verdicts.json `check`); {} when that round is over PATROL_CHECK_S old or
    unreadable."""
    v = fleet.read_json(fleet.state_dir().parent / "patrol-verdicts.json", {}) or {}
    t, check = v.get("t"), v.get("check")
    if not isinstance(t, (int, float)) or not isinstance(check, dict) or now.timestamp() - t > PATROL_CHECK_S:
        return {}
    at = dt.datetime.fromtimestamp(t, dt.timezone.utc)
    return {k: (str(w), at) for k, w in check.items() if k}


def stalled_why(s, checks):
    """Patrol's reason for this session, only while the turn patrol saw is still the last: a session at work again, or
    active since, got past it."""
    why, judged = checks.get(s.get("path")) or (None, None)
    active = fleet.parse(s.get("last_activity")) if s.get("last_activity") else None
    return None if s.get("phase") != "idle" or active and judged and active > judged else why


def fleet_rows(state, leads, board_rows, details=None, now=None, stale_s=None, checks=None, notes=None):
    """Board rows built from the fleet collector's sessions (state.json), for what nobody wrote to the board: each lead
    session whose terminal no board lead holds, the cards under it, and the cards the orchestrator dispatched
    itself. A lead the board holds gets no row of its own, but its cards hang under its board row. Each card carries its
    current PR (fleet.current_pr; details: prs.json's); a card whose PR a board row already has is left to that row.
    Then each session's started subagents (subagent_rows). Returns (rows, order); reported_at is the session's last
    activity, so a quiet one shows its age like a stale board row. checks (patrol_checks) turns the row of a card
    patrol found stalled, while its turn is still ended, into ❔ with patrol's reason. notes, when given, is filled with
    omitted ({session id: why a session gets no row of its own}) and card (the sessions that count as cards)."""
    checks = checks or {}
    state = state or {}
    sessions = state.get("sessions") or []
    held = {l.get("handle"): name for name, l in leads.items()}
    on_board = {r.get("pr") for r in board_rows.values()} - {None}
    # A worktree a board row or board lead links (`--worktree`, or by name in render) is that row's work, not a row of
    # its own.
    linked = {w for r in board_rows.values() for w in (r.get("worktree"), r.get("linked"))} - {None}
    lead_wt = {l["worktree"]: name for name, l in leads.items() if l.get("worktree")}
    lead_wt.update({r["worktree"]: rid for rid, r in board_rows.items() if r.get("worktree") and r.get("kind") == "lead"})
    by_session, under = fleet.prs_by_session(state.get("prs")), {}
    pr_of = {s.get("id"): fleet.current_pr(s, by_session, details) for s in sessions}
    # Whether a session ever had a PR is evidence of work even once the PR it shows is gone (merged while it works on).
    had_pr = {s.get("id") for s in sessions if fleet.pr_candidates(s, by_session)[0]}
    for s in sessions:
        under.setdefault(s.get("parent"), []).append(s)
    rows, order, lead_row = {}, [], {}  # lead_row: lead session -> the row its cards hang under

    def add(rid, s, kind, parent=None, program=None):
        status, nxt = PHASE_ROW.get(s.get("phase"), ("held", s.get("phase") or "—"))
        rep = s.get("report") or {}
        if rep.get("next") and s.get("phase") != "offline":
            nxt = rep["next"]  # the card's own last milestone says more than "작업 중"
        why = stalled_why(s, checks)
        # A lead reads as its session name (as in the sidebar), a card as the task it was dispatched with.
        title = s.get("name") if kind == "lead" else card_title(s)
        rows[rid] = {"id": rid, "kind": kind, "title": one_line(title or rid), "status": "check" if why else status,
                     "next": one_line(nxt), **({"why": one_line(why)} if why else {}),
                     "session": s.get("id"),
                     # a lead's digest links its cards' PRs, which are the cards' rows
                     "pr": pr_of.get(s.get("id")) if kind != "lead"
                     else fleet.current_pr({**s, "report": None}, by_session, details),
                     "reported_at": max(filter(None, (s.get("last_activity"), rep.get("at"))), default=None), "parent": parent,
                     # a card under a lead takes the lead's repo, so the lead heads one section
                     "repo": None if parent else s.get("gh_repo") or s.get("project") or "—", "program": program}
        order.append(rid)

    def omitted(s):
        """Why a session gets no row of its own, else []."""
        pr = pr_of.get(s.get("id"))
        # A dispatch that completed with no PR has reported to its dispatcher and has nothing left to land.
        over = s.get("dispatch_status") == "completed" and s.get("id") not in had_pr
        return [why for why, hit in (("offline", s.get("phase") == "offline" and not pr), ("pr_on_board", pr in on_board),
                                     ("dispatch_done", over), ("linked", s.get("id") in linked)) if hit]

    def shown(s):
        return not omitted(s)

    for lead in sessions:
        if lead.get("kind") != "orchestration":
            continue
        board_lead = next((held[t.get("handle")] for t in lead.get("terminals") or [] if t.get("handle") in held),
                          lead_wt.get(lead.get("id")))  # a restarted terminal has a new handle, the same worktree
        if board_lead and board_lead not in board_rows:
            # Its rows aged off the board on purpose (a finished lead leaves after DONE_KEEP_S). That holds while its session
            # is idle with nothing to show; one that works on, or has a card, is live work and gets a fleet row.
            if lead.get("phase") not in ("working", "waiting") and not any(shown(s) for s in under.get(lead["id"], [])):
                continue
            board_lead = None
        lid = f"fleet:{lead['id']}"
        if not board_lead:
            add(lid, lead, "lead", program=one_line(lead.get("name") or lid))
        lead_row[lead["id"]] = board_lead or lid
        for s in under.get(lead["id"], []):
            if shown(s):
                add(f"{lid}/{s['id']}", s, "task", board_lead or lid)
    kind = {s.get("id"): s.get("kind") for s in sessions}
    for s in sessions:
        # A card made with `orca worktree create` and no Run is standalone: it is work when Orca records the
        # orchestrator as its parent (a /goal card `skills-sync card` started, before it has reported anything), or
        # when it has a report, a PR or a dispatch; not a resident folder such as an automations runner. The parent
        # the collector inferred (the root, for anything unparented) is no evidence on its own.
        card = s.get("kind") == "task" or s.get("kind") == "standalone" and (
            kind.get(s.get("orca_parent")) == "orchestrator" or s.get("report") or s.get("id") in had_pr or s.get("dispatch"))
        if card and kind.get(s.get("parent")) == "orchestrator" and shown(s):
            add(f"fleet:{s['id']}", s, "task")
        if notes is not None and card:
            notes.setdefault("card", []).append(s.get("id"))
    if notes is not None:
        notes.setdefault("card", [])
        notes["omitted"] = {s.get("id"): why for s in sessions if (why := omitted(s))}
    subagent_rows(sessions, {**board_rows, **rows}, rows, order, lead_row, now, stale_s)
    return rows, order


def subagent_rows(sessions, every, rows, order, lead_row, now=None, stale_s=None):
    """A row per started subagent of each session (fleet.attach_subagents put them on the session): under the lead
    or phase its session's row sits under, else a top-level row of its own (the orchestrator's own subagents, a
    top-level card's). Its session goes in `host`, never `session` or `linked`, so closing the row or a merge of the
    session's PR touches no card, and the session's PR is not counted twice. A start with no stop seen for stale_s
    (board.subagent_stale_h) or under an offline session is ❔, never 🔄. now/stale_s None: the collector's verdict."""
    row_of = {sid: rid for sid, rid in lead_row.items()}
    row_of.update({w: rid for rid, r in every.items() for w in (r.get("worktree"), r.get("linked")) if w})
    row_of.update({r["session"]: rid for rid, r in every.items() if r.get("session")})
    by_id = {s.get("id"): s for s in sessions}

    def host_row(sid):
        """The row of the session, else of the nearest session above it that has one (a worker left off the table)."""
        seen = set()
        while sid and sid not in seen:
            if sid in row_of and row_of[sid] in every:
                return every[row_of[sid]]
            seen.add(sid)
            sid = (by_id.get(sid) or {}).get("parent")
        return None
    for s in sessions:
        host = host_row(s.get("id"))
        parent = host and (host["id"] if host.get("kind") in ("lead", "phase") else host.get("parent"))
        for a in s.get("subagents") or []:
            stale = (fleet.subagent_stale(a, s.get("phase"), now, stale_s) if now and stale_s is not None
                     else a.get("stale"))
            rid = f"agent:{a.get('agent')}"
            # The row's age (reported_at, the start) follows the line as for any quiet row, so the reason names none.
            rows[rid] = {"id": rid, "kind": "task", "subagent": a.get("agent"), "host": s.get("id"),
                         "title": one_line(a.get("desc") or f"{a.get('type') or ''} 서브에이전트".strip()),
                         "status": "check" if stale else "moving",
                         "next": "서브에이전트 작업 중" + (f" ({a['type']})" if a.get("type") else ""),
                         "why": stale and ("세션 꺼짐, 종료 기록 없음" if s.get("phase") == "offline"
                                           else "종료 기록 없음"),
                         "session": None, "pr": None, "reported_at": a.get("started_at"), "parent": parent,
                         "host_row": host and host["id"],  # the row the group of this one follows (priority rules)
                         # a row of its own takes its host row's section and program, so a program's report keeps it
                         "repo": None if parent else (host or {}).get("repo") or s.get("gh_repo") or s.get("project") or "—",
                         "program": None if parent else (host or {}).get("program")}
            order.append(rid)


def unlisted(state, sessions, rows, store, cards, shut, lead_session, now, state_min, scope="top-level"):
    """The sessions the report should show and does not: working or waiting, with a parent Orca recorded (its parent
    worktree, a Dispatch or a Run of its own), not the orchestrator, under the report's scope; less every session a row,
    a board lead, a doing item or a tidy close stands for. A string instead when the fleet read cannot be trusted to
    say what is missing (a source failed, or state.json is older than state_min)."""
    if state is None:  # no collector here at all: the report has no session rows to miss
        return []
    bad = [k for k in ("orca", "runs") if ((state.get("sources") or {}).get(k) or {}).get("error")]
    if bad:
        return f"{'·'.join(bad)} 읽기 실패"
    at = fleet.parse(state.get("generated_at"))
    if not at or (now - at).total_seconds() > state_min * 60:
        return f"세션 목록 {ago((now - at).total_seconds()) if at else '시각 없음'} 기준"
    shown, row_prs = set(), {r.get("pr") for r in rows.values()} - {None}
    for r in rows.values():
        # A subagent's row stands for its host session too: that session's work is on the report.
        shown |= {r.get("session"), r.get("worktree"), r.get("linked"), r.get("host")}
        if r.get("kind") == "lead" and (lead := store["leads"].get(r["id"])):
            shown.add(lead_session(lead))
    shown |= {c["id"] for d in store["doing"] if d["id"] not in shut and (c := tidy.doing_card(d, cards))}
    shown |= {c["id"] for c in cards.values() if c["pr"] and c["pr"] in row_prs}
    shown |= {k.removeprefix("card:") for k in shut if k.startswith("card:")}

    def in_scope(sid):
        """A program's report covers what hangs under a session its rows show; the top-level one covers all."""
        seen = set()
        while sid and sid not in seen:
            if sid in shown:
                return True
            seen.add(sid)
            sid = (sessions.get(sid) or {}).get("parent")
        return False
    return [sid for sid, s in sessions.items()
            if s.get("phase") in ("working", "waiting") and s.get("kind") != "orchestrator"
            and (s.get("orca_parent") or s.get("dispatch") or s.get("run")) and sid not in shown
            and (scope == "top-level" or in_scope(s.get("parent")))]


def item_of(r):
    """The tidy id a row is closed by: its card for a fleet row, else the board row."""
    return f"card:{r['session']}" if r.get("session") else f"row:{r['id']}"


GENERIC_NEXT = {nxt for _, nxt in PHASE_ROW.values()} | {""}
ASK_REASONS = ("owner-asked", "hard-after-review", "owner-hands-only")
CANNOT_PROCEED = "진행 못 함"
DECISION_FORM = (f"<question>: <options> → 추천: <X> — <reason> · 답이 없으면 <X | {CANNOT_PROCEED}>"
                 f" · 근거: <{' | '.join(ASK_REASONS)}>")
ASK_GATE = "skills/orchestrate/references/owner-ask-gate.md"


def ask_lint(if_no_answer, reason, recommendation=None):
    """Why an owner ask fails the owner-ask gate (ASK_GATE), or None: a known reason tag, and an if-no-answer that is
    the recommendation itself or CANNOT_PROCEED."""
    if reason not in ASK_REASONS:
        return f"근거 is one of {' | '.join(ASK_REASONS)}, not {reason!r}" if reason else "근거 is missing"
    y = (if_no_answer or "").strip()
    if not y:
        return f"답이 없으면 is missing: the recommendation, or {CANNOT_PROCEED}"
    if y not in (CANNOT_PROCEED, (recommendation or "").strip()):
        return f"답이 없으면 is the recommendation ({recommendation!r}) or {CANNOT_PROCEED}, not {y!r}"
    return None


def ask_line(if_no_answer, reason):
    """The gate's fields as `orch decide add` puts them on the body's first line, where the page and the report read them."""
    return f"답이 없으면 {if_no_answer.strip()} · 근거: {reason}"


def ask_line_of(body):
    first = (body or "").split("\n", 1)[0]
    return first if first.startswith("답이 없으면 ") and "· 근거: " in first else None


def decision_lint(item):
    """Why a brief --decision item does not hold DECISION_FORM and the owner-ask gate, or None."""
    question, sep, tail = item.partition("→ 추천:")
    if not sep:
        return "→ 추천: is missing"
    if not question.removeprefix("(미등록)").strip():
        return "the question before → 추천: is empty"
    # The fixed fields are split off from the right, so a reason may quote them.
    tail, sep, tag = tail.rpartition("· 근거:")
    if not sep:
        return "· 근거: is missing at the end"
    rec, sep, default = tail.rpartition("· 답이 없으면")
    if not sep:
        return "· 답이 없으면 is missing"
    rec, sep, why = rec.partition(" — ")
    if not sep or not rec.strip() or not why.strip():
        return "the recommendation needs '<X> — <reason>', both filled"
    return ask_lint(default, tag.strip(), rec.strip())


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
        out.append(f"세션 목록 {age} · 갱신 실패: {one_line(err)}")
    if gh_err:
        out.append(f"PR 목록 갱신 실패: {one_line(gh_err)}")
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
           decisions=(), insight=None, fresh=False, inputs=None):
    """The brief skill's report as text, with what the caller needs around it. periodic (§1 Periodic report) adds
    the skipped actions, the reap line and a token warning line; the stage table is one line pointing at it (stages.pointer). fill adds
    fleet_rows() for the leads and cards the board does not have; fresh collects the fleet again when its files are
    old. decisions are the model's own items (DECISION_FORM), numbered after the registered ones; insight goes after
    보류, under everything the human acts on.

    Returns {"text", "headline_facts", "changed", "stale", "width_ok", "done", "open", "table_rows", "snapshot"}. done is
    drop_acked()'s; table_rows has one list per repository table in the text, in order, one {label, ack, done} per body
    row: the id its Ack or Done button posts, or None (the page has only the text, so it places the buttons by position).
    snapshot is what commit() saves for the next report to compare against. inputs, when a dict, is filled with what the
    render read and resolved, for rowstate.inputs(); it changes nothing in the report or the return value."""
    now = now or fleet.utcnow()
    tg = channel == "telegram"
    pr_plain = {}

    def no_links(s):
        """The terminal text of a line: a PR link as owner/repo#N, any other link as its label."""
        if tg:  # Telegram text keeps whatever link a caller wrote, such as an --insight
            return s
        for link, key in pr_plain.items():
            s = s.replace(link, key)
        return re.sub(r"\[([^\]]+)\]\(https?://[^)\s]+\)", r"\1", s)
    sd = fleet.state_dir()
    limits = {**LIMITS, **(fleet.config().get("board") or {})}
    for d in decisions:
        if why := decision_lint(d):
            raise ValueError(f"--decision is one line: {DECISION_FORM}\n{why} ({ASK_GATE})")
    collected = refresh(now, limits["state_min"]) if fresh else None
    try:  # one snapshot, so a close and the open list cannot disagree within a report
        seen = store_db.view()
        store, open_dec, db_known = seen["board"], seen["decisions"], True
        order_open = seen["orders_unlinked"] or []  # None until `orch order setup`: no line
        shut = {k: r for k, r in seen["latest"].items() if r["ev"] == "done"}
    except store_db.NotMigrated:
        store, db_known, shut = {"rows": {}, "order": [], "leads": {}, **{l: [] for l in store_db.LANES}}, False, {}
        order_open = []
        try:  # decisions are in decisions.json until the import, and `--decision` has registered there: list them
            open_dec = fleet.open_decisions()
        except store_db.NotMigrated:  # a DB that is not imported: unknown, and the headline and the ❔ rows say so
            open_dec = []
    prs_file = fleet.read_json(sd / "prs.json", {}) or {}
    me = prs_file.get("me")
    prs = {k: {**v["detail"], "probed_at": v.get("probed_at")} for k, v in (prs_file.get("prs") or {}).items() if v.get("detail")}
    state = fleet.read_json(sd / "state.json", None)
    live = None if state is None else {t.get("handle") for s in state.get("sessions") or [] for t in s.get("terminals") or []}
    sessions = {s.get("id"): s for s in (state or {}).get("sessions") or []}
    wt_of_handle = {t.get("handle"): sid for sid, s in sessions.items() for t in s.get("terminals") or []}
    last = fleet.read_json(last_path(scope), None)
    last_rows = last.get("rows") if isinstance(last, dict) and isinstance(last.get("rows"), dict) else None
    verdicts, cards = tidy.assess(store, state, prs, me, now), tidy.cards_of(state, prs)
    by_session = fleet.prs_by_session((state or {}).get("prs"))
    holds, held_by = fleet.held_prs(store, shut, prs, now, cards, hold_why := {}, me=me), {}  # held_by: row id -> the PR a held item covers
    # The PRs a to-do holds and no held item does (that one's text wins): their rows read 📅, not ⏸️.
    todo_held = {k for k, (kind, _) in hold_why.items() if kind == "do"}
    board_rows = {k: dict(v) for k, v in store["rows"].items()} if inputs is not None else None  # before the card links below
    for rid, r in store["rows"].items():
        # A board row about a card is the card's row: it shows the card's current PR (its own --pr is only the last
        # candidate), so the card is not listed a second time. The card is the one `--worktree` links, else the one
        # the row's id and title name.
        if card := cards.get(r.get("worktree")) or tidy.named(f"{rid} {r.get('title') or ''}", cards):
            r["linked"] = card["id"]
            r["pr"] = fleet.current_pr(card, by_session, prs, extra=[r["pr"]] if r.get("pr") else ())
    checks = patrol_checks(now)
    if fill:
        extra, extra_order = fleet_rows(state, store["leads"], store["rows"], prs, now,
                                        float(limits["subagent_stale_h"]) * 3600, checks, notes=inputs)
        store["rows"], store["order"] = {**extra, **store["rows"]}, store["order"] + extra_order
    rows = store["rows"]

    everyone = rows  # every row, before a --scope report keeps only its program's: a group does not depend on the scope

    def up(r, field, table=None):
        table = rows if table is None else table
        while r is not None:
            if r.get(field):
                return r[field]
            r = table.get(r.get("parent"))
        return None

    rules = priority_rules(store.get("groups") or {})

    def worktrees(n):
        """The Orca worktrees a row stands for: a card's own, the one a row names, a board lead's recorded or handle's."""
        lead = store["leads"].get(n["id"]) if n.get("kind") == "lead" else None
        return [w for w in (n.get("session"), n.get("worktree"), n.get("linked"), lead and lead_session(lead)) if w]

    def prio(r):
        """A row's priority group: its own `priority`, else the nearest parent's own or its worktree's rule, else the
        program's rule, else the repository's. A subagent row is first its host's. A fleet row (a lead or card the
        board has no row of) has only rules."""
        if r.get("host"):  # a subagent follows the row subagent_rows() found for its host: the host's own row first
            host = everyone.get(r.get("host_row"))  # (that row's own value, then the host worktree's rule), else a row above it
            mine = host is not None and host is not r and r["host"] in worktrees(host)
            if mine and host.get("priority"):
                return host["priority"]
            if g := rules["worktree"].get(r["host"]):
                return g
            if host is not None and host is not r and (g := prio(host)):
                return g
        n = r
        while n is not None:
            if n.get("priority"):
                return n["priority"]
            if g := next((rules["worktree"][w] for w in worktrees(n) if w in rules["worktree"]), None):
                return g
            n = everyone.get(n.get("parent"))
        return rules["program"].get(up(r, "program", everyone)) or rules["repo"].get(up(r, "repo", everyone))

    def lead_of(r):
        while r.get("parent") in rows:
            r = rows[r["parent"]]
        return r

    def lead_session(lead):
        """The lead's worktree: the one lead add recorded while Orca still lists it, else the one its handle is now in."""
        wt = lead.get("worktree")
        return wt if wt in sessions else wt_of_handle.get(lead.get("handle")) or wt

    def session_of(r):
        """The worktree a row's work runs in: a fleet row's own, one `board set --worktree` gave, the card the row
        names, the session a subagent runs under, a board lead's; else its parent's, since a phase or task row with
        none is its lead's work."""
        while r is not None:
            lead = store["leads"].get(r["id"]) if r["kind"] == "lead" else None
            sid = (r.get("session") or r.get("worktree") or r.get("linked") or r.get("host")
                   or (lead and lead_session(lead)))
            if sid:
                return sid
            r = rows.get(r.get("parent"))
        return None

    if scope != "top-level":
        rows = {k: r for k, r in rows.items() if up(r, "program") == scope}
    order = [k for k in store["order"] if k in rows]
    repo_name = rowstate.repo_names(up(r, "repo") for r in rows.values())

    def repo_of(r):
        repo = up(r, "repo")
        return repo_name.get(repo, repo) or "—"

    oldest_probe, changed, stale, stale_board, facts, unknown = None, [], [], [], [], set()
    waits = {}  # row id -> its RowState, for the rows waiting on a person or a gap in the record
    stalled = {}
    for sid, s in sessions.items():
        if why := stalled_why(s, checks):
            stalled[sid] = one_line(why)
            # A card's PR is its work; the PR in a lead's report is a card's, so a stalled lead says nothing of it.
            if s.get("kind") not in ("orchestration", "orchestrator") and (pr := fleet.current_pr(s, by_session, prs)):
                stalled[pr] = one_line(why)
    facts_in = rowstate.gather(now=now, prs=prs, me=me, shut=shut, verdicts=verdicts, holds=holds, hold_why=hold_why,
                               leads=store["leads"], state=state, decisions=open_dec, state_min=limits["state_min"],
                               stalled=stalled, db_unknown=not db_known)
    states = {}  # row id -> RowState: every status shown below is rowstate.judge()'s, never decided here

    def lead_record(r):
        """The lead judge() checks r's session against: the board's, else the session a row of a lead Orca knows links."""
        top = lead_of(r)
        return store["leads"].get(top["id"]) or ({"worktree": top["worktree"]} if top.get("kind") == "lead" and top.get("worktree") else None)

    def judged(r):
        if r["id"] not in states:
            states[r["id"]] = rowstate.judged(r, facts_in, lead_record(r), session_of(r))
        return states[r["id"]]

    def review_of(r):
        """The one reviewer a row waits on, when judge() says the review is what it waits on: rows that share one fold."""
        js = judged(r)
        return (js["what"] == "리뷰" or "오너 login 모름" in js["unknown"]) and waits_on(prs.get(r.get("pr")))

    def derive(r):
        """(status, next, stale suffix): rowstate.judge()'s state, and the row's age when it is past its limit."""
        lead = lead_record(r)
        st = judged(r)
        status, nxt = st["status"], st["next"]
        if status == "held" and st["hold"]:
            held_by[r["id"]] = st["hold"]
        if status in ("owner", "waiting", "check"):
            waits[r["id"]] = st
        suffix = ""
        at = fleet.parse(r.get("reported_at"))
        limit = (lead or {}).get("stale_after_min") or limits["row_min"]
        # A ❔ subagent's age is the evidence for its ❔, so it shows whatever row_min says.
        if status != "done" and at and ((now - at).total_seconds() > limit * 60 or status == "check" and r.get("subagent")):
            suffix = f" · {ago((now - at).total_seconds())}"
            stale.append(r["id"])
            if not r["id"].startswith(FILLED):  # a fleet row's age is its session's quiet, not a record left behind
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
        if tg or not p.get("url"):
            return f"{pr_num(key)}{warn}"
        link = f"[{pr_num(key)}]({p['url']})"
        pr_plain[link] = key  # the terminal text writes owner/repo#N, which the terminal links itself
        return f"{link}{warn}"

    def pr_status(key, base):
        """The PR's state, and its merge target when that is not the section's base."""
        p = prs.get(key)
        if not p:
            return "?"
        to = p.get("base")
        return pr_state(p) + (f"→{to}" if to and to != base else "")

    # Each display line: {key, section, depth (0 head, 1 sub-task), status, name, title, progress, next, suffix}
    sections, lines, bullets = [], [], {}

    def section_of(r):
        sec = repo_of(r)
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
        st = rowstate.who_state(rev, me)
        if st == "check":
            return st, f"{rev} 대기", {"why": "오너 login 모름", "next": f"{rowstate.AGENT_ACTION}: 오너 login 모름",
                                      "since": since, "unblocks": unblocks}
        if st == "owner":
            return st, "오너 리뷰", {"what": "리뷰", "since": since, "unblocks": unblocks}
        return st, f"{rev} 대기", {"who": rev, "what": "리뷰", "since": since, "unblocks": unblocks}

    def task_lines(head, sec, tasks):
        out = []
        for t in tasks:
            st, nxt, suf = derive(t)
            out.append((t, st, nxt, suf))
        by_rev = {}
        for t, st, *_ in out:
            # Only rows judge() says wait on the review fold: one a hold or a gap stops keeps its own row.
            rev = review_of(t)
            if rev:
                by_rev.setdefault(rev, []).append(t["id"])
        folded = {k for ids in by_rev.values() if len(ids) >= 2 for k in ids}
        statuses = []
        for t, st, nxt, suf in out:
            statuses.append(st)
            if t["id"] in folded:
                continue
            tail = t["id"].rsplit("/", 1)[-1]
            name = t["title"] if t.get("pr") or t["title"] == tail or t["id"].startswith(FILLED) else f"{tail} {t['title']}"
            line(t["id"], sec, 1, st, name, t.get("pr"), bar(t.get("progress")), nxt, suf)
        for rev, ids in by_rev.items():
            if len(ids) < 2:
                continue
            ts = [rows[i] for i in ids]
            progs = {tuple(t.get("progress") or ()) for t in ts}
            nexts = {t.get("next") or "" for t in ts}
            st, name, waits[f"{head}/~{rev}"] = reviewer_wait(rev, ts)
            members[f"{head}/~{rev}"] = list(ids)
            line(f"{head}/~{rev}", sec, 1, st, name, None,
                 bar(ts[0].get("progress")) if len(progs) == 1 else "—",
                 nexts.pop() if len(nexts) == 1 else "", fold=[t["pr"] for t in ts])
        return statuses

    def head_of(t):
        """The phase a sub-task hangs under, else its lead."""
        while t.get("parent") in rows and rows[t["parent"]]["kind"] == "task":
            t = rows[t["parent"]]
        return rows.get(t.get("parent"))

    members, as_group = {}, set()  # a reviewer row's folded rows; leads shown only as their phases
    alone = [rows[k] for k in order if not rows[k].get("parent") and rows[k]["kind"] == "task"]
    alone_rev = {}
    for t in alone:
        if rev := review_of(t):
            alone_rev.setdefault((repo_of(t), rev), []).append(t)
    for k in order:
        r = rows[k]
        if r.get("parent"):
            continue
        sec = section_of(r)
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
        if r not in heads:  # a lead that works only in phases is their section, not a row
            as_group.add(k)
        for h in heads:
            mine = [t for t in tasks if head_of(t) is h]
            home = [t for t in mine if repo_of(t) == sec]
            st, nxt, suf = derive(h)
            at = len(lines)
            line(h["id"], sec, 0, st, h["title"], h.get("pr"), bar(h.get("progress")), nxt, suf)
            kid_status = task_lines(h["id"], sec, home)
            for t in mine:
                if t not in home:  # another repository: its own section, under a row with only the lead's name
                    tsec = section_of(t)
                    if not any(l["key"] == f"{k}@{tsec}" for l in lines):
                        line(f"{k}@{tsec}", tsec, 0, None, r["title"], None, "—", "—")
                    kid_status += task_lines(f"{k}@{tsec}", tsec, [t])
            # The head's shown status is the snapshot's too, so the stage table's 지금 cell agrees with this row.
            states[h["id"]] = rowstate.roll_up(states[h["id"]], kid_status)
            lines[at]["status"] = states[h["id"]]["status"]

    lines, done, hidden = drop_acked(lines, prs, rows, acks.acked())

    # Per group: how many rows move, wait on a person or a condition, or are done. Counted from the rows themselves, so
    # a reviewer fold, a bullet and a ✅ row folded away still count once each; a row with rows under it is their
    # roll-up, not a row of its own, and a row the owner acked leaves the count. Rows with no group are in none.
    parents = {r.get("parent") for r in rows.values()}
    leaf = {k: rows[k] for k in states if k in rows and k not in parents and k not in hidden and k not in as_group}
    tally = {}
    for k, r in leaf.items():
        if g := prio(r):
            tally.setdefault(g, [0, 0, 0])[{"moving": 0, "done": 2}.get(states[k]["status"], 1)] += 1

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
    folded_keys = {lines[i]["key"] for i in gone}
    lines[:] = [l for i, l in enumerate(lines) if i not in gone]
    sections[:] = [s for s in sections if any(l["sec"] == s for l in lines) or any(b == s for b, _ in bullets)]
    # From here on the rows and tables are in report order, so the lists built from them follow it too.
    lines[:] = rowstate.order_rows(lines)
    sections[:] = rowstate.order_tables({s: [l["status"] for l in lines if l["sec"] == s]
                                         + [reviewer_wait(rev, items)[0] for (b, rev), items in bullets.items() if b == s]
                                         for s in sections})

    # A doing item about a card that has a row is that row: one line, with the doing item's next step when the card's
    # own says no more than its session's phase.
    card_line = {c: l for l in lines if (r := rows.get(l["key"])) and (c := r.get("session") or r.get("linked"))}
    # Such an item stays open under its own id (the row's Done closes the row only) and is placed in the row's table.
    doing, merged_doing = [], []
    for d in store["doing"]:
        iid, card = d["id"], tidy.doing_card(d, cards)
        js = rowstate.judge_item("doing", d, facts_in, iid, card and card["id"])
        if js["status"] == "done":
            continue
        # The row says whether its session runs, so a doing item about it needs no ❔ of its own.
        if card and (l := card_line.get(card["id"])) and (js["status"] == "moving" or js["why"] == "세션 없음") and iid not in verdicts:
            if l["own"] == "moving" and l["next"] in GENERIC_NEXT:
                l["next"] = d["next"]
            merged_doing.append((iid, d, js, l))
            continue
        doing.append((iid, d, js))
    later = [store["later"][i] for i in shown_later(store["later"], shut)]
    todos = [t for t in store["owner_todo"] if t["id"] not in shut]
    # A to-do whose date or condition has come goes to the owner's list or 진행 중, and stays there until Done.
    planned = [(t["id"], t, todo_when(t, now)) for t in store["todo"] if t["id"] not in shut]
    arrived = [p for p in planned if fleet.todo_come(p[1], now, prs, cards)]
    upcoming = [p for p in planned if p not in arrived]

    # A section's merge target is the base most of its PRs share; a row with another base names its own.
    bases = {}
    for sec in sections:
        keys = [k for l in lines if l["sec"] == sec for k in ([l["pr"]] if l["pr"] else l["fold"] or [])]
        keys += [i["pr"] for (bsec, _), items in bullets.items() if bsec == sec for i in items]
        seen = [prs[k]["base"] for k in keys if (prs.get(k) or {}).get("base")]
        if seen:
            bases[sec] = max(dict.fromkeys(seen), key=seen.count)
    for l in lines:
        r = rows.get(l["key"])
        sid = session_of(r) if r and not l["fold"] else None
        st = session_state(sessions.get(sid), now) if state is not None and (sid or r) else None
        # A finished row has no session cell but still shares the worktree; a subagent's row names its task, not it.
        l["sid"] = sid if sid in sessions and "subagent" not in r else None
        l["session"] = None if l["status"] in (None, "done") or st is None else {"id": sid if sid in sessions else None,
                                                                                   "state": st, "word": SESSION_WORD[st]}
        if l["session"] and l["session"]["id"]:
            s = sessions[sid]
            line_, at = last_report(s)
            l["session"].update(name=one_line(s.get("name")), last=line_, at=at, pr=(r or {}).get("pr"),
                                model=s.get("model_tag") if st != "none" else "")
        l["pr_state"] = pr_status(l["pr"], bases.get(l["sec"])) if l["pr"] else ""
        if l["fold"]:  # the numbers by base, each base but the section's named once; the states go to the snapshot only
            to = {}
            for i, k in enumerate(l["fold"]):
                to.setdefault((prs.get(k) or {}).get("base"), []).append(pr_text(k) if i == 0 else pr_num(k))
            l["cell"] = "·".join("·".join(nums) + (f"→{b}" if b and b != bases.get(l["sec"]) else "") for b, nums in to.items())
            l["pr_state"] =" · ".join(pr_status(k, bases.get(l["sec"])) for k in l["fold"])
        else:
            l["cell"] = f"{pr_text(l['pr'])} {l['pr_state']}".strip() if l["pr"] else ""

    # A row alone on its session is named as Orca names the worktree, so the table's name finds it in the Cmd+J palette
    # (prefix and substring match, so a cut name does too). Rows sharing a session keep their titles: the worktree's
    # name would say the same thing twice, and the 세션 cell has it.
    on_session = collections.Counter(l["sid"] for l in lines)
    for l in lines:
        name = l["sid"] and one_line(sessions[l["sid"]].get("name") or "")
        if name and l["session"] and on_session[l["sid"]] == 1 and name.lower() not in l["name"].lower():
            l.update(name=name, orca_name=True)

    # Bold: a row the last report did not have, or whose status, progress, next, PR state or session changed.
    snap = {}
    for l in lines:
        snap[l["key"]] = {"status": l["status"], "progress": l["progress"], "next": l["next"],
                          "pr": l["pr_state"], "session": l["session"] and l["session"]["state"]}
        prev = (last_rows or {}).get(l["key"])
        # A fleet row (--fill) is not in a report the board committed, so only a change to one already seen counts.
        if last_rows is None:
            continue
        if (not l["key"].startswith(FILLED) if prev is None
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
    missing = (unlisted(state, sessions, rows, store, cards, shut, lead_session, now, limits["state_min"], scope)
               if fill else [])
    if isinstance(missing, str):  # a failed refresh already says how old the list is
        facts.append("누락 검사 안 함" if collected else f"누락 검사 안 함: {missing}")
    if collected:
        facts.append(collected)
    if missing and not isinstance(missing, str):
        names = [one_line(sessions[m].get("name") or m) for m in missing]
        facts.append(f"보고서에 없는 세션 {len(missing)} ({', '.join(names[:3])}{' 외' if len(names) > 3 else ''})")
    if not db_known:
        facts.append(f"이행 대기: 보드·결정 DB (`{store_db.migrate_command()}`)")
    fleet.external_marked(None, lost := [])
    if lost:
        facts.append(f"인박스 기록 어긋남 {len(lost)} ({', '.join(lost[:3])}{' 외' if len(lost) > 3 else ''}: 마지막 줄로 판정)")
    if traces := store_db.legacy_traces():
        facts.append(f"옛 코드가 쓴 흔적 {', '.join(traces)}")
    old = [d for d in open_dec if (at := fleet.parse(fleet.decision_age_from(d)))
           and (now - at).total_seconds() > limits["decision_stale_h"] * 3600]
    if old:
        facts.append(f"오래된 결정 {len(old)} ({', '.join(d['id'] for d in old)} · `orch decide keep|drop`)")
    # An order is only a lead until a card, PR or decision carries it: one still unlinked after the grace is work nobody has.
    loose = [o for o in order_open if (at := fleet.parse(o["created_at"]))
             and (now - at).total_seconds() > limits["order_unlinked_min"] * 60]
    if loose:
        first = loose[0]  # oldest first
        facts.append(f"연결 없는 오너 지시 {len(loose)} (가장 오래된 {first['id']} "
                     f"{ago((now - fleet.parse(first['created_at'])).total_seconds())} · `orch order link`)")
    # Judged as the relay judges it: the registering terminal, else the root's writable agent terminal, else nowhere.
    # A decision added outside Orca has no handle and always goes to the root, so only a missing root terminal counts.
    # The relay types into the target as it is; a terminal whose agent exited refuses the line, so that is no route.
    usable = {t.get("handle") for s in (state or {}).get("sessions") or [] for t in s.get("terminals") or []
              if t.get("agent") and t.get("connected") and t.get("writable")}
    no_route = [d for d in open_dec if live is not None and fleet.decision_target(d, state)[1] not in usable]
    rerouted = [d for d in open_dec if live is not None and d.get("handle") and d["handle"] not in live
                and d not in no_route]
    if no_route:
        facts.append(f"응답 경로 없음 {len(no_route)} ({', '.join(d['id'] for d in no_route)})")
    if rerouted:
        facts.append(f"등록 터미널 없음 {len(rerouted)} ({', '.join(d['id'] for d in rerouted)} · 답은 오케스트레이터로)")
    by_pr = rowstate.by_pr(states, rows)
    stage_line = stages.pointer(*stages.current(stages.table(state), now, limits["stage_idle_d"] * 86400, state, by_pr), tg)
    queued = [l for l in later if (verdicts.get(l["id"]) or {}).get("verdict") != "check"]
    lists_line = lists_pointer(len(upcoming), len(queued), tg)
    if sum(folded.values()):
        facts.append(f"접음 완료 {sum(folded.values())} (대시보드)")

    def mark(l, st):
        return "📅" if st == "held" and held_by.get(l["key"]) in todo_held else ICON.get(st, "—")

    def bar_shown(l):
        return l["progress"] not in ("", "—", None)

    def session_text(l):
        """The 세션 cell: the session's state, and the worktree to find it by unless the row's name already says it."""
        s = l["session"]
        if not s:
            return "—"
        name = s.get("name")
        word = s["word"] + (f" {s['model']}" if s.get("model") else "")  # O, S or H; `!` when off orchestrate.models
        if not name or name.lower() in l["shown"].lower():
            return word
        return f"{word} · {name}"

    def session_link(l, cell):
        """Its state word as a link to the session's dashboard page while the server runs; a cut cell keeps the word."""
        s = l["session"]
        url = s and s.get("id") and s["state"] != "none" and stages.dash_url("session/" + urllib.parse.quote(s["id"], safe=""))
        return cell.replace(s["word"], f"[{s['word']}]({url})", 1) if url else cell

    used = {l["status"] for l in lines}
    legend = ("🔄 진행 · 🙋 오너 차례 · ⏳ 다른 사람 대기 · ⏸️ 보류 · "
              + "".join(f"{ICON[s]} {w} · " for s, w in (("done", "완료"), ("blocked", "막힘"), ("check", "확인 필요")) if s in used)
              + ("📅 할 일로 넘김 · " if any(mark(l, l["status"]) == "📅" for l in lines) else "")
              + ("⏰ 도래 · " if arrived else "")
              + ("막대=단계(추정)" if any(bar_shown(l) for l in lines) else "")).removesuffix(" · ")
    pr_states = " ".join(l["pr_state"] for l in lines if not l["fold"])
    pr_legend = [f"{m} {w}" for m, w in PR_LEGEND if m in pr_states]
    if re.search(r"(?<![\w/])[0-9a-f]{7}(?![\w/])", pr_states):
        pr_legend.append("7자리=머지 sha")
    local = now.astimezone()
    head = f"{local:%H:%M} · " + " · ".join(([headline] if headline else []) + facts[1 if headline else 0:])
    out = [head if tg else f"**{head}**"]
    notes = [x for x in (stage_line, lists_line) if x]
    if lines:
        notes += [legend] + (["PR: " + " · ".join(pr_legend)] if pr_legend else [])
    if tg:
        out += [x for note in notes for x in ("", note)]
    elif notes:  # one quote block: the guide lines read as a margin note, apart from the sections below
        out += [""] + [x for i, note in enumerate(notes) for x in ((">",) if i else ()) + (f"> {note}",)]

    def heading(text):
        """A section's title: `##` on the terminal, where markdown draws it; the Telegram text is plain."""
        return text if tg else f"## {text}"
    width_ok = True
    swaps = []  # (first line in out, count, lines): a table's rows with every cell whole, for the dashboard's copy
    ackable, table_rows = {d["id"] for d in done if not d["acked"]}, []

    def label(l):
        """A phase is named with its lead outside the table, where no section heading says whose it is."""
        r = rows.get(l["key"])
        return f"{lead_of(r)['title']} {l['name']}" if r and r["kind"] == "phase" and not l.get("orca_name") else l["name"]

    def next_cell(l):
        """The 다음 cell: the row's next step, or rowstate's pointer to the list below that holds it."""
        if l["status"] is None:
            return "—"
        if mark(l, l["own"]) == "📅":  # the to-do that holds it is on the To-do page, not in a list here
            return "→ 📅 할 일"
        w = waits.get(l["key"]) or {}
        nxt = w.get("next") or l["next"]
        if nxt in GENERIC_NEXT and l["session"] and l["own"] not in rowstate.POINTER:  # the 세션 column says it
            return l["suffix"].removeprefix(" · ") or "—"
        return rowstate.cell_next({"status": l["own"], "next": nxt}, l["suffix"])

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

    # Every row shows once, in its section's table, with its next step in the 다음 column (rowstate.cell_next). A row that
    # waits on the owner, another person or a condition points down to that list, which holds one reference line per
    # row ("what · where → name"). 카드 없는 일 lists only what has no row: doing items, flagged held items, arrived to-dos.
    # Names are unique across the whole report (the dashboard matches buttons and list references by them): a repeat
    # takes the next ·N no other row already shows, a name that itself ends in ·N included. A lead's name over its
    # sub-tasks in another repository is that lead again, not a row, and keeps the name; a row in its table does not.
    # A table shared with another lead's rows names its phases as the lists do: `ph1` alone does not say whose it is.
    used, heads = set(), {(l["sec"], l["name"]) for l in lines if l["status"] is None}
    shared = {s for s in sections if len({lead_of(rows[l["key"]])["id"] for l in lines if l["sec"] == s and l["key"] in rows}) > 1}
    for l in lines:
        l["base"] = label(l) if l["sec"] in shared else l["name"]
        if l["status"] is None:
            l["shown"] = l["base"]
            continue
        shown, n = l["base"], 1
        while shown in used or (l["sec"], shown) in heads:
            n += 1
            shown = f"{l['base']}·{n}"
        used.add(shown)
        l["shown"] = shown

    def ref_name(l):
        return label(l) + l["shown"][len(l["base"]):]

    placement, list_rows = {}, {"owner": [], "waiting": [], "held": [], "moving": []}

    def place(pid, where):
        placement.setdefault(pid, []).append(where)

    moving, held, turn, theirs, bullet_of = [], [], [], [], {}
    for sec in sections:
        for l in (l for l in lines if l["sec"] == sec):
            st, w = l["own"], waits.get(l["key"]) or {}
            if st in (None, "done"):
                continue
            where = l["cell"].split(" ")[0] if l["pr"] else l["cell"]
            ref = None
            if st == "held":
                if mark(l, st) != "📅":  # a row a to-do holds is the to-do's: the pointer line counts it
                    held.append(f"{l['next'] or '—'} → {ref_name(l)}")
                    ref = "held"
            elif st == "owner":
                turn.append(turn_item(w.get("what") or l["next"] or "응답", where, w.get("unblocks") or ref_name(l)))
                ref = "owner"
            elif st == "waiting":
                theirs.append(theirs_item(w["who"], f"{w['what']} {where}".strip() if w.get("what") else "", w.get("since"),
                                          w.get("unblocks") or ref_name(l), l["next"], l["fold"] or [l["pr"]]))
                ref = "waiting"
            if ref:
                place(l["key"], f"ref:{ref}")
                list_rows[ref].append({"id": l["key"], "label": ref_name(l)})
        for (bsec, rev), items in bullets.items():
            if bsec != sec:
                continue
            st, name, w = reviewer_wait(rev, items)
            nums = "·".join(pr_num(i["pr"]) for i in items)
            bullet_of[(bsec, rev)] = f"{ICON[st]} {len(items)}건 {name}" + (f" — {w['next']}" if st == "check" else "")
            if st == "owner":
                turn.append(turn_item("리뷰", nums, w["unblocks"]))
            elif st == "waiting":
                theirs.append(theirs_item(rev, f"리뷰 {nums}", w["since"], w["unblocks"], "", [i["pr"] for i in items]))
            for i in items:
                place(i["id"], f"table:{bsec}")
                if st in ("owner", "waiting"):
                    place(i["id"], f"ref:{st}")

    def listed(where, into, iid, title, text):
        """One list line for an item with no row: its text goes out, and list_rows keeps that exact text, so the
        dashboard puts the item's Done on that line and no other."""
        into.append(text)
        list_rows[where].append({"id": iid, "label": title, "text": text})

    for iid, d, js in doing:
        place(iid, f"list:{js['status']}")
        if js["status"] == "owner":
            listed("owner", turn, iid, d["title"], turn_item(d["next"], "", d["title"]))
        elif js["status"] == "waiting":
            listed("waiting", theirs, iid, d["title"], theirs_item(d["owner"], "", d.get("since"), d["title"], d["next"]))
        else:
            listed("moving", moving, iid, d["title"], f"{ICON[js['status']]} {d['title']} — {js['next']} · {d['owner']}")
    for l in later:
        iid = l["id"]
        if (js := rowstate.judge_item("later", l, facts_in, iid))["status"] == "check":
            listed("moving", moving, iid, l["title"], f"{ICON['check']} {l['title']} — {js['next']}")
            place(iid, "list:check")
    due_mine = []
    for iid, t, when in arrived:
        js = rowstate.judge_item("do", t, facts_in, iid)
        place(iid, f"list:{js['status']}")
        if js["status"] == "owner":
            listed("owner", due_mine, iid, t["title"], f"⏰ {t['title']} · {when} 도래")
        else:
            listed("moving", moving, iid, t["title"], f"⏰ {t['title']} — {when} 도래 · {t['who']}")

    # The owner's priority groups, one line each, above everything else; no group on any row, no section.
    if tally:
        out += ["", heading("🧭 우선순위")]
        out.append("")
        for g in sorted(tally, key=lambda g: (int(g.split(":", 1)[0]), g)):
            name, (mv, wt, dn) = g.split(":", 1)[1], tally[g]
            # The first open row of the group in the order the tables are printed: each section's lines, then its bullets.
            def mine(k):
                return k in leaf and prio(leaf[k]) == g and states[k]["status"] != "done"
            open_rows = []
            for sec in sections:
                # A row's own line carries judge()'s step as the table shows it, a doing item's step included.
                open_rows += [(ref_name(l) if k == l["key"] or l["key"] in members else rows[k]["title"], k,
                               l["next"] if k == l["key"] else states[k]["next"])
                              for l in lines if l["sec"] == sec for k in members.get(l["key"], [l["key"]]) if mine(k)]
                open_rows += [(i["title"], i["id"], states[i["id"]]["next"]) for (bsec, _), items in bullets.items()
                              if bsec == sec for i in items if mine(i["id"])]
            first = open_rows[0] if open_rows else None
            if first:
                shown, _, nxt = first
                nxt = f"{shown} — {nxt}" if nxt and nxt not in GENERIC_NEXT else shown
            out.append(("" if tg else "- ") + (name if tg else f"**{name}**")
                       + f" — 진행 {mv} · 대기 {wt} · 완료 {dn} · 다음: {nxt if first else '없음'}")

    # What the owner acts on comes first; what explains the rest comes last.
    todo =[t["title"] for t in todos] + due_mine + turn
    out += ["", heading("🙋 지금 오너가 할 일") + ("" if todo else " — 없음")]
    out += [""] + [it if tg else f"- {it}" for it in todo] if todo else []
    own, decisions = list(decisions), open_dec
    repeated =set((last or {}).get("decisions") or []) if last_rows is not None else set()
    if decisions or own:
        items = []
        for i, d in enumerate(decisions, 1):
            rec = d.get("recommend") and d["options"][d["recommend"] - 1]
            text = (f"{i}. ({d['id']}) {d['title']}" + (f" → 추천: {rec['label']}" + (f" — {rec['description']}" if rec["description"] else "")
                    if rec else " — 선택지: " + " / ".join(o["label"] for o in d["options"]) if d["options"] else ""))
            text += f" · {gate}" if (gate := ask_line_of(d.get("body"))) else ""
            items.append(text + (" (지난 보고와 동일)" if d["id"] in repeated else ""))
        items += [f"{i}. {one_line(d)}" for i, d in enumerate(own, len(decisions) + 1)]
        if tg:
            out += ["", "❓ 결정 필요 — " + items[0]] + [x for it in items[1:] for x in ("", it)]
        else:
            out += ["", heading("❓ 결정 필요"), ""] + items

    for sec in sections:
        mine = [l for l in lines if l["sec"] == sec]
        title = sec + (f" → {bases[sec]}" if sec in bases else "")
        out += ["", heading(f"📋 {title}") if tg else heading(title)]

        def drawn(l):
            """Placement of a row as it is written out: a folded reviewer row stands for its members, a card row for
            the doing items merged into it; the fold row itself is no item."""
            if l["status"] is None:
                return
            if l["key"] not in members:
                place(l["key"], f"table:{title}")
            for m in members.get(l["key"], ()):
                place(m, f"table:{title}")
            for iid, _, _, row_l in merged_doing:
                if row_l is l:
                    place(iid, f"table:{title}")
        if tg:
            for l in mine:
                name = (f"{l['shown']} {l['cell']}" if l["fold"] is None else f"{l['cell']} {l['shown']}").strip()
                name += " (바뀜)" if l["key"] in changed else ""
                if l["status"] is None:  # the lead's name over its sub-tasks in another repository
                    out.append(name)
                    continue
                sess = l["session"] and session_text(l)
                tail = [x for x in (l["progress"] if bar_shown(l) else None, next_cell(l), sess) if x and x != "—"]
                out.append(("└ " if l["depth"] else "") + f"{mark(l, l['status'])} {name}"
                           + (f" — {' · '.join(tail)}" if tail else ""))
                drawn(l)
        elif mine:  # a section of folded bullets only has no table
            # The 진행 column shows only where a row has a bar: nothing records one by itself (`--progress` only).
            bars = any(bar_shown(l) for l in mine)
            drop = () if bars else (HEADER.index("진행"),)
            head = [h for c, h in enumerate(HEADER) if c not in drop]

            def dress(i, c, s):
                l = mine[i - 1]
                if c == len(head) - 1:
                    return session_link(l, s)
                return ("└ " if l["depth"] else "") + (f"**{s}**" if l["key"] in changed else s) if c == 1 else s
            # No cell is cut: a name or a reason cut to fit reads alike in every row. A table that is wider than the
            # width goes out as one whole line per row instead. The dashboard has the page's width and keeps the table.
            cells = lambda: [[x for c, x in enumerate(r) if c not in drop] for r in
                             ([mark(l, l["status"]), l["shown"], l["cell"] or "—", l["progress"], next_cell(l),
                               session_text(l)] for l in mine)]
            whole = table_rows_of(head, [[dress(i, c, s) for c, s in enumerate(r)] for i, r in enumerate(cells(), 1)])
            table = whole
            head_md = ["| " + " | ".join(head).strip() + " |", "|---" * len(head) + "|"]
            if table_width.table_width([[no_links(c) for c in r] for r in table]) > COLS:  # as the terminal draws it
                # One whole line per row, not a table.
                swaps.append((len(out) + 1, len(whole) - 1, head_md + [table_width.md_row(row) for row in whole[1:]]))
                out += [""] + table_width.as_lines(whole[1:], 2)
            else:
                out += [""] + head_md
                swaps.append((len(out), len(table) - 1, [table_width.md_row(row) for row in whole[1:]]))
                out += [table_width.md_row(row) for row in table[1:]]
            # Placed from the rows written, by the whole name each one shows (names are unique in the report): a row the
            # table lost is missing and one it wrote twice is a duplicate, whatever the lines handed to it said.
            by_name = {l["shown"]: l for l in mine if l["status"] is not None}
            written = [re.sub(r"^\*\*(.*)\*\*$", r"\1", re.sub(r"^└ ", "", row[1])) for row in table[1:]]
            for name in written:
                if name in by_name:
                    drawn(by_name[name])
            # Done on every open row the board can close; Ack on a ✅ row not yet acknowledged.
            table_rows.append({"table": title, "rows": [
                {"id": l["key"], "label": l["shown"], "status": mark(l, l["status"]), "session": l["session"],
                 "ack": f"board:{l['key']}" if f"board:{l['key']}" in ackable else None,
                 "done": item_of(rows[l["key"]]) if l["key"] in rows and l["own"] not in (None, "done") else None}
                for l in mine]})
        if folded.get(sec):
            out += ["", f"{ICON['done']} 그 전 완료 {folded[sec]}건 (대시보드)"]
        for (bsec, rev), items in bullets.items():
            if bsec != sec:
                continue
            out += ["", f"- {bullet_of[(bsec, rev)]} — 분야 {'·'.join(i['title'] for i in items)}"]
    if moving:
        out += ["", heading("📌 카드 없는 일"), ""] + [it if tg else f"- {it}" for it in moving]
    if theirs:
        out += ["", heading("⏳ 다른 사람 대기"), ""] + [it if tg else f"- {it}" for it in theirs]
    # Only the ⏸️ rows: the held items are a queue the pointer line counts (todo_tables lists it).
    if held := list(dict.fromkeys(held)):
        out += ["", heading("⏸️ 보류"), ""] + [it if tg else f"- {it}" for it in held]
    if insight:
        out += ["", insight.strip()]
    if periodic and skipped:
        out += ["", skipped]
    if periodic and (reaped := reap_line(now)):
        out += ["", reaped]
    if periodic and (tokens := token_line(now)):
        out += ["", tokens]
    opened =[{"id": item_of(rows[l["key"]]), "kind": "row", "label": label(l), "status": l["own"]}
              for l in lines if l["key"] in rows and l["own"] not in (None, "done")]
    opened += [{"id": i, "kind": "doing", "label": d["title"], "status": js["status"]} for i, d, js in doing]
    opened += [{"id": i, "kind": "doing", "label": d["title"], "status": js["status"], "row": item_of(rows[l["key"]])}
               for i, d, js, l in merged_doing if l["key"] in rows]
    opened += [{"id": l["id"], "kind": "later", "label": f"{l['trigger']} → {l['title']}", "status": "held",
                "title": l["title"], "trigger": l["trigger"], "who": l.get("who"), "n": n}
               for n, l in enumerate(later, 1)]
    opened += [{"id": i, "kind": "do", "label": f"{when} → {t['title']} · {t['who']}", "due": (i, t, when) in arrived,
                "status": rowstate.judge_item("do", t, facts_in, i)["status"] if (i, t, when) in arrived else "planned",
                "title": t["title"], "who": t["who"], "when": when, "after": t.get("after"),
                **dict(zip(("date", "cond"), fleet.todo_parts(t, now))),
                "at": fleet.iso(fleet.todo_start(t, now))}
               for i, t, when in planned]
    opened += [{"id": t["id"], "kind": "todo", "label": t["title"], "status": "owner"} for t in todos]
    for o in opened:
        v = verdicts.get(o["id"]) or {}
        o["check"] = v.get("evidence") if v.get("verdict") == "check" else None
    for t in todos:
        place(t["id"], "list:owner")
        list_rows["owner"].append({"id": t["id"], "label": t["title"], "text": t["title"]})
    # What must show, from the inputs rather than from what was drawn: every row in scope less the ones the owner acked,
    # the ✅ rows folded into a count and the leads shown only as their phases (a folded reviewer row's members and a
    # merged doing item are placed with the row that stands for them), then every open item with no row.
    folded_members = {m for k, ms in members.items() if k in hidden | folded_keys for m in ms}
    expected = [k for k in order if k not in hidden | folded_keys | as_group | folded_members]
    expected += [i for i, *_ in doing] + [i for i, *_ in merged_doing if _[-1]["key"] not in hidden | folded_keys]
    expected += [i for i, _, _ in arrived] + [t["id"] for t in todos]
    expected += [l["id"] for l in later if rowstate.judge_item("later", l, facts_in, l["id"])["status"] == "check"]
    dup_ids, missing_ids, extra_ids = rowstate.check_placement(placement, expected)
    whole = list(out)
    for at, n, lines_ in reversed(swaps):
        whole[at:at + n] = lines_
    if inputs is not None:
        inputs.update(facts=facts_in, state=state, prs=prs, board_rows=board_rows, rows=rows, states=states,
                      session_of={k: session_of(r) for k, r in rows.items()},
                      lead={k: lead for k, r in rows.items() if (lead := lead_record(r))})
    # A terminal expands `[label](url)` to label plus the whole URL, so a row is a line of URL. The terminal text drops the
    # links (a PR as owner/repo#N, which the terminal links by itself; a session word as the word); `full` keeps them.
    return {"text": no_links("\n".join(out)), "full": "\n".join(whole), "headline_facts": facts, "changed": changed, "stale": stale, "width_ok": width_ok, "done": done,
            "open": opened, "table_rows": table_rows, "list_rows": list_rows, "placement": placement, "now_by_pr": by_pr,
            "states": states,
            "duplicates": dup_ids, "missing": missing_ids + extra_ids,
            "snapshot": {"v": 1, "ts": fleet.iso(now), "rows": snap, "decisions": [d["id"] for d in decisions]}}


def lists_pointer(todos, queued, tg=False):
    """The brief's one line in place of the to-do list and the held queue: how many of each, and where they are (the
    dashboard's To-do page; on Telegram the command, as stages.pointer does)."""
    parts = [f"{w} {n}건" for w, n in (("📅 할 일", todos), ("⏸️ 보류 큐", queued)) if n]
    if not parts:
        return None
    where = "orch board todo list --telegram" if tg else stages.dash_url("todo") or "`orch board todo list`"
    return " · ".join(parts + [where])


QUEUE_HEADER = ["#", "할 일", "다음 차례 조건", "담당"]
TODO_HEADER = ["", "언제", "할 일", "담당"]


def todo_order(items):
    """The to-dos as the list shows them: one whose time came first, then by first date, undated last as added."""
    far = "~"  # after every ISO date
    return sorted(items, key=lambda o: (not o["due"], o.get("at") or far))


def todo_tables(now=None, tg=False, fresh=True):
    """`orch board todo list`: the to-dos (📅 waiting, ⏰ come) and the held queue (its first is next), each a table
    a table when it fits 78 columns, else one whole line per row, or lines on Telegram. Returns (text, width_ok)."""
    items = render(now=now, fill=True, periodic=False, fresh=fresh and now is None)["open"]
    todos = todo_order([o for o in items if o["kind"] == "do"])
    queue = sorted((o for o in items if o["kind"] == "later"), key=lambda o: o["n"])
    rows_t = [["⏰" if o["due"] else "📅", o["when"] or "—", o["title"], o["who"] or "—"] for o in todos]
    # The captions count as the brief's pointer line does: to-dos whose time has not come, held items whose trigger
    # tidy has not found met (❔, listed in their place).
    rows_q = [[("▶ " if i == 0 else "") + (ICON["check"] if o["check"] else "") + str(o["n"]), o["title"], o["trigger"],
               o.get("who") or "—"] for i, o in enumerate(queue)]
    due, check = sum(o["due"] for o in todos), sum(bool(o["check"]) for o in queue)
    out, ok = [], True
    for head, cap, rows, empty in ((TODO_HEADER, f"📅 할 일 {len(todos) - due}건 · ⏰ 도래 {due}", rows_t, "📅 할 일 없음"),
                                   (QUEUE_HEADER, f"⏸️ 보류 큐 {len(queue) - check}건 · ▶ 다음 차례"
                                    + (f" · {ICON['check']} 확인 필요 {check}" if check else ""), rows_q, "⏸️ 보류 큐 없음")):
        out += [""] if out else []
        if not rows:
            out.append(empty)
            continue
        out.append(cap if tg else f"**{cap}**")
        if tg:
            out += [""] + [f"{r[0]} " + " · ".join(r[1:]) for r in rows]
            continue
        if table_width.table_width([head] + rows) > COLS:  # nothing is cut: one whole line per row instead of a table
            out += [""] + table_width.as_lines(rows, 1)
            continue
        out += ["", ("| " + " | ".join(head) + " |").replace("|  |", "| |"), "|" + "---|" * len(head)]
        out += [table_width.md_row(row) for row in rows]
    return "\n".join(out), ok


def reap_line(now):
    """The last reap-hard pass, so a stopped cleanup shows in the report; None when it never ran here."""
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "scripts"))
        import reap_hard
    except ImportError:  # an install without the repo's scripts/
        return None
    return reap_hard.last_line(now.timestamp())


def token_line(now, max_age_h=26):
    """One line when the dashboard's last token usage report (token_usage.py) has warnings; None otherwise, or when
    that report is older than a day and a bit, so a stopped collect does not keep an old warning up."""
    rep = fleet.read_json(fleet.state_dir() / "tokens.json", None)
    at = fleet.parse((rep or {}).get("generated_at"))
    if not rep or not rep.get("warnings") or not at or (now - at).total_seconds() > max_age_h * 3600:
        return None
    ws = rep["warnings"]
    more = f" 외 {len(ws) - 2}건" if len(ws) > 2 else ""
    return f"🔥 토큰 경고 {len(ws)}건: {'; '.join(w['text'] for w in ws[:2])}{more} (대시보드 Tokens)"


def close(ids, undo=False, why=None):
    """Close board items, rows and cards (tidy.py), or reopen them: `orch board done|reopen` and the dashboard's
    buttons. Only what the report lists as open now can be closed."""
    can = None if undo else {o["id"]: o["label"] for o in render(fill=True)["open"]}
    return tidy.write(ids, undo=undo, why=why, can=can)


def commit(scope, snapshot):
    """Save what was sent, for the next report's bold."""
    fleet.write_atomic(last_path(scope), json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n")
