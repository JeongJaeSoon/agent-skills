"""Row state: the one judgment of what a brief row waits on, and of where the report shows it.

judge(row, facts) is the only place a row's status is decided: 🔄 the agent acts next, 🙋 only the owner can unblock
it, ⏳ another person, ⏸️ a condition (a held item, a preceding PR, a machine wait, a closed PR), ❔ a gap in the record
the agent fills, 🔴 its lead session is gone, ✅ done. Rules run top to bottom and the first that applies decides;
a later rule never rewrites an earlier one's result, and what later rules also found stays in `also`. A fact a rule
needs and cannot read is unknown, not false: the rule stops there with ❔ and names what is missing.

gather() is the one adapter that reads the facts (agent-skills.db's board, closes and decisions as the render passes them,
state, prs, the external inbox, the pressure queue, program files and ledgers). brief render, the dashboard's Brief and
Stages and `orch stage show` all read states through snapshot() or judged(); none of them decides a status of its own.
judged() is judge(), except that before `orch db import` it says the closes and decisions are unknown, which judge's facts
cannot tell it.
"""
import fnmatch, json, os, pathlib, re

import fleet

ICON = {"moving": "🔄", "owner": "🙋", "waiting": "⏳", "check": "❔", "held": "⏸️", "blocked": "🔴", "done": "✅"}
OWNER_NAMES = ("오너", "owner")
# What a merged PR's row may still have to do (tidy.POST_MERGE): the row stays open on it.
POST_MERGE = re.compile(r"릴리스|배포|apply|deploy|release|rollout|롤아웃", re.I)
OWNER_TYPES = ("approval", "run_command", "login", "verify_failed", "question")  # fleet.STICKY_TYPES
AGENT_ACTION = "에이전트 조치 필요"
# What the rules read off an open PR; the collector (fleet.shape_pr) always writes them, so a missing one is unknown.
PR_FIELDS = ("draft", "reviewers", "ci", "decision", "merge_state")
# The lists around the tables (the owner's above, the others below), and the word a row's 다음 cell points at them with.
POINTER = {"owner": "↑ 오너", "waiting": "↓ 대기", "held": "↓ 보류"}  # short: the 78-column table cuts at 8
# How long an ended pressure entry stays its row's ❔ and a line of the reboot check. The queue keeps it for 7 days, and
# nothing closes one a session retried under other text (a new id) or dropped, so without a limit both outlive it.
QUEUE_ENDED = ("gave_up", "failed")
QUEUE_ENDED_H = 3
LIVE_PHASES = ("working", "waiting", "idle")  # a session that can still move a doing item (board.session_state)


class RowState(dict):
    """status, next, why, who, what, since, hold (the PR key a held item holds it by), also (later rules that matched
    too), unknown (facts it could not read)."""

    def __init__(self, status, next_="", why="", **kw):
        super().__init__(status=status, next=next_ or "", why=why or "", who=kw.get("who"), what=kw.get("what"),
                         since=kw.get("since"), hold=kw.get("hold"), also=list(kw.get("also") or ()),
                         unknown=list(kw.get("unknown") or ()))

    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k) from None


def is_team(who):
    return (who or "").strip().lstrip("@").lower().startswith("team:")


def is_owner(who, me):
    w = (who or "").strip().lstrip("@").lower()
    if is_team(w):
        return False
    return w in OWNER_NAMES or bool(me) and w == me.lower()


def merged(p):
    return bool(p) and (p.get("state") == "MERGED" or bool(p.get("merged_at")))


def who_state(login, me):
    """The status of a wait on one person: the owner's, another's, or ❔ when the owner's login is not known."""
    if not me and not is_team(login):
        return "check"
    return "owner" if is_owner(login, me) else "waiting"


def asked(p):
    """The people and teams (no bots) a PR has requested a review from. A team is never the owner."""
    return [x["login"] for x in (p or {}).get("reviewers") or [] if x.get("requested") and not x.get("bot")]


def reviewable(p):
    """An open PR a requested review is the next event of: not a draft, CI not failing, no change request."""
    return (p.get("state") == "OPEN" and not p.get("draft") and p.get("ci") != "failure"
            and p.get("decision") != "CHANGES_REQUESTED")


# ---------------------------------------------------------------- facts (the file adapter; 4b-1 reads the DB)

def gate_of(evs, open_gates):
    """A PR's landing gate from its ledger events and the open Orca gate ids the collector saw (None: runs not read):
    none (never opened), pending (its newest gate is in the open list), approved (approved or landed after the
    newest gate), else unknown. A gate missing from the open list is no evidence of an answer: it may have been
    answered, cancelled, or opened after the last collect."""
    opened = [i for i, e in enumerate(evs) if e.get("ev") == "gate_opened"]
    if not opened:
        return "none"
    last = opened[-1]
    if any(e.get("ev") in ("approved", "landed") for e in evs[last + 1:]):
        return "approved"
    return "pending" if open_gates is not None and evs[last].get("gate") in open_gates else "unknown"


def program_facts(open_gates=None, home=None, unread=None):
    """{PR key: [{"program", "policy", "gate", "verdict"}]} for every PR a program's ledger names, read from
    $PROGRAMS_HOME/<slug>/program.json and ledger.jsonl. policy None: no valid merge_policy. verdict: the newest
    verdict's {sha, pass} for that PR, or None. unread (a set, filled in): the repositories whose program files could
    not be read, so which of their PRs a program owns is unknown; "*" when the programs directory itself could not."""
    home = pathlib.Path(home or os.environ.get("PROGRAMS_HOME", "~/.claude/programs")).expanduser()
    out, unread = {}, unread if unread is not None else set()
    try:
        dirs = sorted(d for d in home.iterdir() if d.is_dir() and not d.name.startswith(("_", "."))) if home.exists() else []
    except OSError:
        unread.add("*")
        return out
    for d in dirs:
        if not (d / "program.json").exists():
            continue
        cfg = fleet.read_json(d / "program.json", None)
        if not isinstance(cfg, dict) or not cfg.get("repo"):
            unread.add(cfg.get("repo") if isinstance(cfg, dict) and cfg.get("repo") else "*")
            continue
        policy = cfg.get("merge_policy") if cfg.get("merge_policy") in ("autonomous", "human-gate") else None
        try:
            lines = (d / "ledger.jsonl").read_text().splitlines() if (d / "ledger.jsonl").exists() else []
            events = [json.loads(l) for l in lines if l.strip()]
        except (OSError, ValueError):
            unread.add(cfg["repo"])
            continue
        per = {}
        for e in events or []:
            if isinstance(e, dict) and isinstance(e.get("pr"), int):
                per.setdefault(e["pr"], []).append(e)
        for n, evs in per.items():
            v = next((e for e in reversed(evs) if e.get("ev") == "verdict"), None)
            verdict = v and {"sha": v.get("sha") or "", "pass": str(v.get("result") or "pass").lower() in ("pass", "ok")}
            out.setdefault(f"{cfg['repo']}#{n}", []).append(
                {"program": d.name, "policy": policy, "verdict": verdict,
                 "gate": gate_of(evs, open_gates)})
    return out


def pressure_facts(sessions, now):
    """{worktree id: [queue entries]} for the pressure queue entries whose cwd is exactly one session's worktree path
    (reap-resources' pressure.py, read only), or None when the queue cannot be read. An entry two sessions match is
    linked to neither. The link is by path only: a session restarted in the same worktree inherits a waiting entry
    until its deadline (queue_minutes) turns it into gave_up."""
    try:
        import sys
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "reap-resources" / "scripts"))
        import pressure
        # queue_read() reads a broken file as an empty queue; here that is a queue not read. No file is an empty one.
        if pressure.QUEUE.exists() and not isinstance(json.loads(pressure.QUEUE.read_text()), dict):
            return None
        q = pressure.queue_read()
    except Exception:  # an install without reap-resources, or a queue that cannot be read
        return None
    by_path = {}
    for sid, s in sessions.items():
        if s.get("path"):
            by_path.setdefault(os.path.realpath(s["path"]), []).append(sid)
    out = {}
    for e in q.values():
        hit = by_path.get(os.path.realpath(e.get("cwd") or "")) if e.get("cwd") else None
        if hit and len(hit) == 1:
            out.setdefault(hit[0], []).append(queue_state(e, now))
    return out


def queue_state(e, now):
    """The entry with its state as of now: past its deadline a waiting entry has given up and a retrying one has
    failed, whether or not pressure's own round has run since to write that down."""
    try:
        late = float(e.get("deadline")) < now.timestamp()
    except (TypeError, ValueError):
        late = False
    if late and e.get("state") in ("waiting", "retrying"):
        # Its own deadline ended it: an `ended` it still carries is from before it was reopened.
        return {**e, "state": "gave_up" if e["state"] == "waiting" else "failed", "ended": e["deadline"]}
    return e


def queue_over(e, now):
    """Whether an entry ended and is past its QUEUE_ENDED_H. The earlier of ended and deadline: a sweep that runs late
    writes a late `ended` on an entry whose deadline ended it. An ended entry with neither is not over."""
    times = [t for t in (e.get("ended"), e.get("deadline")) if isinstance(t, (int, float))]
    return e.get("state") in QUEUE_ENDED and bool(times) and now.timestamp() - min(times) >= QUEUE_ENDED_H * 3600


def agent_merges(cfg=None):
    """agent-skills.json → board.agent_merges: repository patterns (owner/repo, globs) whose PRs the agent merges when
    no program says otherwise. None by default."""
    path = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG", "~/.claude/agent-skills.json")).expanduser()
    try:
        cfg = cfg if cfg is not None else json.loads(path.read_text())
        pats = (cfg.get("board") or {}).get("agent_merges") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [p for p in pats if isinstance(p, str)]


def gather(*, now, prs, me, shut, verdicts, holds, hold_why, leads, state, decisions, owner_items=None,
           programs=None, pressure=None, merges=None, state_min=5, programs_unread=None, stalled=None, db_unknown=False):
    """The facts judge() reads, from what the render already holds plus the program, pressure and config files.
    stalled: session id, and that session's current PR -> patrol's reason, for a card patrol found stalled and that has
    not moved since. db_unknown: the board, closes and decisions could not be read (NotMigrated)."""
    unread = set(programs_unread or ())
    sessions = {s.get("id"): s for s in (state or {}).get("sessions") or []}
    src = (state or {}).get("sources") or {}
    at = fleet.parse((state or {}).get("generated_at"))
    known = (state is not None and not (src.get("orca") or {}).get("error")
             and (at is None or (now - at).total_seconds() <= state_min * 60))
    live = None if state is None else {t.get("handle") for s in sessions.values() for t in s.get("terminals") or []}
    # The open Orca gates the collector saw this tick; None when its runs source failed or there is no state.
    open_gates = None if state is None or (src.get("runs") or {}).get("error") else {
        i["key"].removeprefix("gate:") for i in state.get("items") or [] if str(i.get("key", "")).startswith("gate:")}
    if pressure is None:
        pressure = pressure_facts(sessions, now)
    return {"now": now, "me": me, "prs": prs, "shut": shut, "verdicts": verdicts, "holds": holds, "hold_why": hold_why,
            "leads": leads, "sessions": sessions, "sessions_known": known, "live": live, "decisions": decisions,
            "owner_items": owner_items if owner_items is not None else
            [i for i in fleet.external_items(now) if i["type"] in OWNER_TYPES and i.get("session")],
            "programs": programs if programs is not None else program_facts(open_gates, unread=unread),
            "programs_unread": unread, "stalled": stalled or {}, "db_unknown": db_unknown,
            "pressure": pressure or {}, "pressure_known": pressure is not None,
            "agent_merges": merges if merges is not None else agent_merges()}


# ---------------------------------------------------------------- judge

def canonical(r):
    """The id a row is closed by (tidy): its card for a fleet row, else the board row."""
    return f"card:{r['session']}" if r.get("session") else f"row:{r['id']}"


def merge_actor(key, f):
    """Who merges an open PR once nothing else stands in its way: "agent", "owner" (its gate waits on the owner), or
    None (unknown). A program's policy comes first; two programs naming the PR with different policies are unknown."""
    repo = key.rsplit("#", 1)[0]
    unread = f.get("programs_unread") or ()
    if "*" in unread or repo in unread:  # a program whose files could not be read may own this PR, with another policy
        return None
    progs = f["programs"].get(key) or []
    if progs:
        prog = program_of(key, f)
        if prog is None or prog["policy"] is None:
            return None
        if prog["policy"] == "autonomous":
            return "agent"
        return {"none": "agent", "approved": "agent", "pending": "owner"}.get(prog["gate"])
    if any(fnmatch.fnmatchcase(repo, p) for p in f["agent_merges"]):
        return "agent"
    if f["me"] and repo.split("/", 1)[0].lower() == f["me"].lower():
        return "agent"
    return None


def program_of(key, f):
    """The one program record for a PR, or None when programs naming it disagree on the policy."""
    progs = f["programs"].get(key) or []
    if len({p["policy"] for p in progs}) != 1:
        return None
    return progs[-1]


def merge_next(key, p, f):
    """The agent's next step on a PR it merges, from the evidence: never 머지 without a passing review of the head."""
    if p.get("draft"):
        return "초안 → ready"
    if p.get("merge_state") == "DIRTY":
        return "충돌 해소"
    if p.get("decision") == "CHANGES_REQUESTED":
        return "리뷰 지적 반영"
    if p.get("ci") in ("failure", "pending"):
        return "체크"
    prog = program_of(key, f)
    if not prog:
        return "리뷰·머지"
    v = prog.get("verdict")
    head = p.get("head") or ""
    if not v or not head or not v["sha"] or not (head.startswith(v["sha"]) or v["sha"].startswith(head)):
        return "리뷰"
    if not v["pass"]:
        return "리뷰 지적 반영"
    return "머지" if p.get("ci") == "success" and p.get("merge_state") in ("CLEAN", "HAS_HOOKS") else "체크"


def patrol_reason(r, stalled, sid):
    """Patrol's verdict on the row's own session. A task's sid may be its lead's, so only a lead row reads it; a row with
    no card link reaches the card by the PR, which is what hides the card's own row."""
    own = r.get("session") or r.get("linked") or r.get("worktree")
    if own:
        return stalled.get(own)
    return r.get("kind") == "lead" and stalled.get(sid) or stalled.get(r.get("pr"))


def judge(r, f, lead=None, sid=None):
    """The RowState of one row. lead: the board lead the row hangs under (None for fleet rows with none); sid: the
    worktree the row's work runs in (its own, else its lead's)."""
    me, now = f["me"], f["now"]
    status, nxt = r.get("status") or "moving", r.get("next") or ""
    key = r.get("pr")
    p = f["prs"].get(key) if key else None
    tid = canonical(r)
    s = f["sessions"].get(r.get("session") or sid) if (r.get("session") or sid) else None
    busy = bool(s) and s.get("phase") in ("working", "waiting")
    since = r.get("since") or (p or {}).get("created")
    found = []  # (status, kwargs) of every rule that applies, first one decides

    def hit(st, nx="", why="", **kw):
        found.append((st, dict(next_=nx, why=why, since=kw.pop("since", since), **kw)))

    def stop_unknown(*missing):
        return RowState("check", f"{AGENT_ACTION}: {' · '.join(missing)}", " · ".join(missing), unknown=missing,
                        since=since, also=[st for st, _ in found])

    def gap(missing):
        """A rule that may apply needs a fact it cannot read: an earlier rule's result stands, else ❔ here."""
        return _first(found, unknown=[missing]) if found else stop_unknown(missing)

    # 1 ✅: only a confirmed completion returns here; a merged PR with work left goes on through the rules below. A row
    # marked done whose PR closed unmerged is not: GitHub outranks the board, and rule 3 holds it as PR 닫힘.
    if tid in f["shut"] or status == "done" and not (p and p.get("state") == "CLOSED" and not merged(p)):
        return RowState("done")
    if key and (p is None or not merged(p) and p.get("state") not in ("OPEN", "CLOSED")):
        return stop_unknown("PR 상태 모름")

    def lacks(*fields):
        """The open PR's details a rule reads and the collector did not write: that rule stops (gap), not the row."""
        return is_open and any(k not in p for k in fields)
    left = None
    if merged(p):
        if busy and r.get("session"):
            left = "PR 머지됨 · 작업 중"
        elif POST_MERGE.search(nxt):
            left = nxt
        else:
            return RowState("done")
    is_open = bool(p) and p.get("state") == "OPEN"
    queue = f["pressure"].get(sid, []) if sid else []

    # 2 🙋 only the owner can unblock it
    for d in f["decisions"]:
        link = d.get("link") or ""
        if key and (link == key or key in fleet.pr_refs(link) or p.get("url") and link == p["url"]) or link == f"row:{r['id']}":
            hit("owner", nxt, what=f"결정 {d['id']}: {d.get('title') or ''}".strip(), since=d.get("created_at"))
    for it in f["owner_items"]:
        if sid and it.get("session") == sid:
            hit("owner", nxt, what=it.get("title") or it.get("type"), since=it.get("at"))
    prog = program_of(key, f) if is_open else None
    if prog and prog["policy"] == "human-gate" and prog["gate"] == "pending":
        hit("owner", nxt, what="gate 응답")
    if r.get("owner") and not me and not is_owner(r["owner"], me) and not is_team(r["owner"]):
        return gap("오너 login 모름")  # --owner may name the owner by login
    if r.get("owner") and is_owner(r["owner"], me) or status == "owner" and not r.get("owner"):
        hit("owner", nxt, what=nxt or "응답")
    # A missing detail stops a rule only while the rule may still apply: a draft asks no review, and a PR whose
    # requested reviewers are known not to include the owner needs no draft flag to say the owner is not asked.
    draft_on = is_open and p.get("draft") is True
    if is_open and not draft_on:
        if lacks("reviewers"):
            return gap("PR 상세 모름")
        people = asked(p)
        if not me and any(not is_team(a) for a in people):  # a team is never the owner; a person may be
            return gap("오너 login 모름")
        if any(is_owner(a, me) for a in people):
            if lacks("draft"):
                return gap("PR 상세 모름")
            hit("owner", nxt, what="리뷰")

    # 3 ⏸️ a condition holds it
    if p and p.get("state") == "CLOSED":
        hit("held", "PR 닫힘")
    if is_open and (h := f["holds"].get(key)):
        own = fleet.drop_date(nxt, f["hold_why"].get(key, (None, None))[1], now)
        hit("held", h if own in GENERIC_NEXT or own == h else f"{h} · {own}", hold=key)
    if r.get("after"):
        pa = f["prs"].get(r["after"])
        if pa is None:
            if not found:
                return stop_unknown(f"선행 {r['after']} 상태 모름")
        elif not merged(pa) and pa.get("state") == "OPEN":
            hit("held", f"선행 {r['after']} 머지 대기")
        elif not merged(pa):
            hit("check", f"{AGENT_ACTION}: 선행 {r['after']} 닫힘", f"선행 {r['after']} 닫힘")
    if sid and not f.get("pressure_known", True):
        return gap("pressure 대기열 모름")
    for e in queue:
        if e.get("state") == "waiting":
            hit("held", f"기계 대기: {e.get('what')}")
    if status == "held" and not (r.get("session") and is_open):  # a fleet row's held is its phase, not a hold
        hit("held", nxt)

    # 4 ⏳ another person
    named = r.get("owner") and not is_owner(r["owner"], me)  # ⏳ already, whatever the review says
    others = [a for a in asked(p) if not is_owner(a, me)] if is_open and not draft_on else []
    off = draft_on or not others or p.get("ci") == "failure" or p.get("decision") == "CHANGES_REQUESTED"
    if not off and not named and lacks("draft", "ci", "decision"):
        return gap("PR 상세 모름")
    if not off and not lacks("draft", "ci", "decision") and reviewable(p):
        hit("waiting", "" if nxt in GENERIC_NEXT else nxt, who=", ".join(others), what="리뷰")
    if named:
        hit("waiting", nxt, who=r["owner"])

    # 5 🔴 its lead's session is gone
    if status == "moving" and lead and not lead.get("closed_at"):
        if not f["sessions_known"]:  # never 🔴 on a list that cannot be trusted, and never 🔄 either
            return gap("세션 목록 모름")
        else:
            gone = (sid not in f["sessions"] or f["sessions"][sid].get("phase") == "offline") if sid \
                else f["live"] is not None and lead.get("handle") not in f["live"]
            if gone:
                hit("blocked", "재개·정리")  # the 세션 column already says the session is gone

    # 6 ❔ a gap the agent fills
    v = f["verdicts"].get(tid)
    if v and v.get("verdict") == "check":
        hit("check", f"{AGENT_ACTION}: {v['evidence']}", v["evidence"])
    if status == "check":
        hit("check", f"{AGENT_ACTION}: {r.get('why') or '상태 모름'}", r.get("why") or "상태 모름")
    elif why := patrol_reason(r, f["stalled"], sid):
        hit("check", f"{AGENT_ACTION}: {why}", why)
    for e in queue:
        if e.get("state") in QUEUE_ENDED and not queue_over(e, f["now"]):
            hit("check", f"{AGENT_ACTION}: pressure {e['state']} {e.get('what')}", f"pressure {e['state']}")
    # The PR's own facts come before the row's word: a ready PR the agent merges is 🔄 whatever "waiting" the row says.
    if lacks(*PR_FIELDS):
        return gap("PR 상세 모름")
    if is_open:
        quiet = not busy and bool(r.get("session") or status in ("held", "waiting"))
        # Whether an agent works on it decides the draft and the quiet PR: a stale session list is no evidence either way.
        if not f["sessions_known"] and (p.get("draft") or not ready(p) and (r.get("session") or status in ("held", "waiting"))):
            return gap("세션 목록 모름")
        if p.get("draft") and not busy:  # the session list is trusted and nobody works on the draft
            hit("check", f"{AGENT_ACTION}: 초안 · 세션 없음 — 재개하거나 보류로 기록", "초안 · 세션 없음")
        elif ready(p):
            # Nothing on the PR is the agent's to fix: who merges it is what decides whether it moves.
            actor = merge_actor(key, f)
            if actor is None and prog and prog["policy"] == "human-gate" and prog["gate"] == "unknown":
                hit("check", f"{AGENT_ACTION}: gate 상태 확인 필요 — orch land 로 응답 읽기", "gate 상태 모름")
            elif actor is None and ({"*", key.rsplit("#", 1)[0]} & set(f.get("programs_unread") or ())):
                hit("check", f"{AGENT_ACTION}: 프로그램 파일 못 읽음", "프로그램 파일 못 읽음")
            elif actor is None:
                hit("check", f"{AGENT_ACTION}: 머지 주체 기록 없음", "머지 주체 기록 없음")
            elif actor == "agent":  # its next step comes from the evidence, never from a word like 머지 대기
                hit("moving", merge_next(key, p, f))
        elif quiet and p.get("merge_state") == "DIRTY":  # a conflict nobody is resolving is not "moving"
            hit("check", f"{AGENT_ACTION}: 충돌 · 세션 없음 — 재개하거나 보류로 기록", "충돌 · 세션 없음")
        elif quiet:  # no agent on it now, and the PR has the agent's work left (CI, a review to answer)
            hit("moving", merge_next(key, p, f))
    if status == "waiting" and not r.get("owner") and not (is_open and asked(p)):
        hit("check", f"{AGENT_ACTION}: 누구 대기인지 기록 없음", "누구 대기인지 기록 없음")

    # 7 🔄
    hit("moving", left or nxt)
    return _first(found)


def judged(r, f, lead=None, sid=None):
    """judge(), or ❔ for every row while the DB holding its closes and decisions cannot be read."""
    if f.get("db_unknown"):
        gap = "결정·닫힘 DB 이행 대기"
        return RowState("check", f"{AGENT_ACTION}: {gap}", gap, unknown=[gap])
    return judge(r, f, lead, sid)


def judge_item(kind, item, f, iid, session=None):
    """The RowState of a list item that is not a table row: a doing item, a held item (later), an arrived to-do (do).
    The same rules as judge(), on the facts such an item has. session: the worktree a doing item names."""
    if iid in f["shut"]:
        return RowState("done")
    v = f["verdicts"].get(iid)
    if v and v.get("verdict") == "check":
        return RowState("check", f"{AGENT_ACTION}: {v['evidence']}", v["evidence"])
    if kind == "later":
        return RowState("held", item.get("trigger") or "")
    who = item.get("owner") if kind == "doing" else item.get("who")
    nxt = item.get("next") or ""
    if who and is_owner(who, f["me"]):
        return RowState("owner", nxt, what=nxt)
    if kind == "doing" and item.get("waiting"):
        if not f["me"]:
            return RowState("check", f"{AGENT_ACTION}: 오너 login 모름", "오너 login 모름", unknown=["오너 login 모름"])
        return RowState("waiting", nxt, who=who, since=item.get("since"))
    if kind == "doing":  # 🔄 says something is moving it: a board line alone does not
        if not f["sessions_known"]:
            return RowState("check", f"{AGENT_ACTION}: 세션 목록 모름", "세션 목록 모름", unknown=["세션 목록 모름"])
        if (f["sessions"].get(session) or {}).get("phase") not in LIVE_PHASES:
            return RowState("check", f"세션 없음 · {nxt}" if nxt else "세션 없음", "세션 없음", who=who)
    return RowState("moving", nxt, who=who)


def ready(p):
    """An open PR nothing on it is the agent's to fix: not a draft, no conflict, no change request, CI passed or none,
    and no human reviewer requested."""
    return (reviewable(p) and p.get("ci") in ("success", None) and p.get("merge_state") != "DIRTY"
            and not asked(p))


def _first(found, unknown=()):
    st, kw = found[0]
    return RowState(st, also=[x for x, _ in found[1:] if x != st], unknown=unknown, **kw)


# The next steps a session's phase writes (board.PHASE_ROW): they say nothing a hold or a merge step would not.
GENERIC_NEXT = {"작업 중", "턴 종료", "확인 대기", "에이전트 없음", "세션 꺼짐", ""}


# ---------------------------------------------------------------- place

# The owner's turn outranks another person's: the head row shows what the owner can unblock now.
WORST = ("blocked", "owner", "waiting", "check", "held", "moving")


def roll_up(st, kids):
    """A head row's state as every reader shows it: its own, with the status of its worst open sub-task (WORST
    order). A ✅ head stays ✅. "own" keeps judge()'s status for the 다음 cell and the Done button."""
    if st["status"] == "done":
        return {**st, "own": "done"}
    open_ = [st["status"]] + [k for k in kids if k not in ("done", None)]
    return {**st, "own": st["status"], "status": min(open_, key=lambda s: WORST.index(s) if s in WORST else len(WORST))}


# A table's and the stage table's order: what waits on the owner or on a fill-in first, what is over last. WORST above
# answers another question (which sub-task a head's icon shows), so the two differ on ⏳.
ROW_ORDER = ("owner", "blocked", "check", "moving", "waiting", "held", "done")
ATTENTION = ("owner", "blocked", "check")


def rank(status):
    return ROW_ORDER.index(status) if status in ROW_ORDER else len(ROW_ORDER)


def order_rows(lines):
    """The display lines of each table by status, the same status keeping its order. A head and its └ rows move as one
    block, at the rank of the block's best row, so an open sub-task is not buried under its ✅ or nameless head."""
    tables = {}
    for l in lines:
        blocks = tables.setdefault(l["sec"], [])
        if l["depth"] and blocks:
            blocks[-1].append(l)
        else:
            blocks.append([l])
    return [x for blocks in tables.values()
            for head, *subs in sorted(blocks, key=lambda b: min(rank(l["status"]) for l in b))
            for x in (head, *sorted(subs, key=lambda l: rank(l["status"])))]


def order_tables(tables):
    """{table name: its rows' statuses} as names in report order: a table with a row in ATTENTION first, then the most
    🔄 rows, then the name."""
    return sorted(tables, key=lambda n: (not set(tables[n]) & set(ATTENTION), -list(tables[n]).count("moving"), n.casefold()))


def repo_names(names):
    """{spelling: the one name its table goes by}: GitHub's owner/name is case-insensitive, so its spellings are one
    repository. A bare name stays apart even when it is another's tail: it is a project or directory name (fleet's
    fallback with no GitHub remote), and `app` may belong to any owner."""
    first = {}
    return {n: first.setdefault(n.casefold(), n) if "/" in n else n for n in names if n}


def order_tree(rows):
    """The stage rows (each with its children right after it) with siblings by status and each row's children still
    under it. A row's rank is its `now`; with none, a finished row is ✅ and any other goes after the ⏸️ rows, since
    no status says it is moving. A group counts only by the rows under it."""
    ids, kids = {r["id"] for r in rows}, {}
    for r in rows:
        kids.setdefault(r.get("parent") if r.get("parent") in ids else None, []).append(r)

    def own(r):
        if r.get("group"):
            return len(ROW_ORDER) + 1
        return rank(r["now"]) if r.get("now") else rank("done") if r.get("done_at") else rank("held") + .5

    def walk(parent):
        blocks = []
        for r in kids.get(parent, ()):
            sub, low = walk(r["id"])
            blocks.append((min(low, own(r)), [r, *sub]))
        blocks.sort(key=lambda b: b[0])
        return [x for _, b in blocks for x in b], min((low for low, _ in blocks), default=len(ROW_ORDER) + 1)
    return walk(None)[0]


def ref_list(st):
    """The list that references a row (🙋 ⏳ ⏸️), or None: 🔄 ❔ 🔴 ✅ rows say their next step in the table."""
    return {"owner": "owner", "waiting": "waiting", "held": "held"}.get(st["status"])


def cell_next(st, suffix=""):
    """The row's 다음 cell: its next step, or a pointer to the list that says it."""
    if st["status"] == "done":
        return "—"
    if st["status"] in POINTER:
        return POINTER[st["status"]]
    return (st["next"] or "—") + suffix


def by_pr(states, rows):
    """{PR key: status} for each PR exactly one judged row links (states holds one entry per row shown, aliases
    already merged): the stage table's 지금 cell. A PR two rows link has none, even when they agree: which row the
    stage row means is unknown."""
    seen = {}
    for rid, st in states.items():
        if (pr := (rows.get(rid) or {}).get("pr")):
            seen.setdefault(pr, []).append(st["status"])
    return {k: v[0] for k, v in seen.items() if len(v) == 1}


def snapshot(now=None):
    """The brief's states without its text, for a reader that is not the brief (`orch stage show`): {"rows": {row id:
    RowState}, "by_pr": by_pr()}. The same render() the brief and the dashboard run, so no reader judges on its own."""
    import board  # board imports this module
    out = board.render(now=now, fill=True, periodic=False)
    return {"rows": out["states"], "by_pr": out["now_by_pr"]}


def check_placement(placement, expected):
    """(duplicates, missing, unexpected): ids placed in a table or list more than once, expected ids placed nowhere,
    and placed ids nothing expected. placement: {id: ["table:…" | "list:…" | "ref:…", ...]}."""
    shown = {i: [w for w in ws if not w.startswith("ref:")] for i, ws in placement.items()}
    dup = sorted(i for i, ws in shown.items() if len(ws) > 1)
    dup += sorted(i for i, ws in placement.items() if sum(w.startswith("ref:") for w in ws) > 1)
    missing = sorted(set(expected) - {i for i, ws in shown.items() if ws})
    unexpected = sorted({i for i, ws in shown.items() if ws} - set(expected))
    return dup, missing, unexpected
