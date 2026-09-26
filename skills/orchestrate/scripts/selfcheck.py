"""The dashboard checking itself: what the page is served (state.json) against a fresh read of Orca and GitHub.

Each mismatch class that someone once caught by watching the page by hand is encoded here, so the dashboard raises it
as a Needs you item instead of a person polling for it. A difference counts only once a second read, GRACE_S later,
still shows it: the collector trails Orca by up to one tick. Each class becomes one item, keyed by the subjects it
names, so a mismatch that lasts stays one item however often it is seen.
"""
import collections, datetime as dt, re, time

import fleet

EVERY = 300           # seconds between checks while nothing is off
GRACE_S = 90          # re-read after this before a difference counts; longer than the fleet's idle tick (60 s)
ACTIVITY_LAG_S = 300  # a session's shown last activity may trail its agents' own times by this much
PR_OVERDUE_S = fleet.TIER_EVERY["cold"] + GRACE_S  # an open PR is probed at least this often, page open or not
PR_BATCH = 50
BAD_TEXT_RE = re.compile(r"\b(None|null|undefined|NaN)\b")

TITLES = {
    "session_missing": "Orca 에 있는 세션이 대시보드에 없음",
    "session_ghost": "Orca 에서 사라진 세션이 대시보드에 남음",
    "tree_parent": "세션 트리의 부모가 어긋남",
    "pr_state": "GitHub 에서 닫힌 PR 이 열린 채로 보임",
    "item_answered": "이미 답한 턴 항목이 남음",
    "mail_answered": "답장이 간 질문 메일이 남음",
    "prompt_gone": "끝난 입력·권한 대기가 남음",
    "item_session_gone": "없는 세션에 달린 항목",
    "activity_stale": "세션의 마지막 활동 시각이 에이전트보다 뒤처짐",
    "item_text": "항목 제목이 비었거나 None 이 보임",
}
CHECKS = tuple(TITLES)


def read_live(f, state):
    """A fresh read of what the state claims to show: Orca's worktrees, terminals and inbox, and GitHub's state of
    every PR the page shows open (a table row or an item). Raises fleet.SourceError when Orca cannot be read."""
    fast = f.fetch_fast()
    keys = {p["key"] for p in state.get("prs") or [] if p.get("state") == "OPEN"}
    keys |= {pr_of(i) for i in state.get("items") or [] if i.get("source") == "github"} - {None}
    prs = {}
    if f.rate is None or f.rate >= fleet.RATE_FLOOR:
        keys = sorted(keys)
        for n in range(0, len(keys), PR_BATCH):
            chunk = keys[n:n + PR_BATCH]
            try:
                data, _ = f.graphql(fleet.pr_query(chunk, "state mergedAt closedAt"))
            except fleet.SourceError:
                break
            for j, k in enumerate(chunk):
                pr = (data.get(f"p{j}") or {}).get("pullRequest")
                if pr:
                    prs[k] = pr
    return {**fast, "prs": prs}


def pr_of(item):
    """The PR key ("owner/repo#n") of a GitHub item, whose key is pr:<key>:<type>."""
    k = item.get("key") or ""
    return k[3:].rsplit(":", 1)[0] if k.startswith("pr:") else None


def findings(state, live, probed, cfg, now):
    """{check: [(subject, detail)]} for every place the state disagrees with the live read."""
    out = collections.defaultdict(list)
    ss = {s["id"]: s for s in state.get("sessions") or []}
    root = state.get("root")
    gen = fleet.parse(state.get("generated_at")) or now
    wts = {w.get("worktreeId"): w for w in live.get("worktrees") or []}
    panes = {a.get("paneKey"): a for w in wts.values() for a in w.get("agents") or [] if a.get("paneKey")}

    # Sessions against Orca's worktrees, with the collector's own rule for which worktrees are sessions. An empty
    # list is more likely a bad read than every worktree gone at once.
    if wts:
        for wid, w in wts.items():
            if w.get("isArchived") or wid in ss:
                continue
            if w.get("isMainWorktree") and not w.get("agents") and not cfg.get("include_main_worktrees"):
                continue
            out["session_missing"].append((wid, f"{w.get('displayName') or w.get('path')}: Orca 에 있고 대시보드에 없음"))
        for sid, s in ss.items():
            w = wts.get(sid)
            if not w or w.get("isArchived"):
                out["session_ghost"].append((sid, f"{s['name']}: Orca 에서 {'보관됨' if w else '없음'}"))

    # The tree: every parent exists, the root has none, and every chain ends at the root.
    for sid, s in ss.items():
        p = s.get("parent")
        if sid == root and p:
            out["tree_parent"].append((sid, f"{s['name']}: 최상위인데 부모 {p} 가 있음"))
        elif p and p not in ss:
            out["tree_parent"].append((sid, f"{s['name']}: 부모 {p} 가 세션 목록에 없음"))
        elif root and sid != root:
            node, seen = sid, {sid}
            while node != root and ss[node].get("parent") in ss and ss[node]["parent"] not in seen:
                node = ss[node]["parent"]
                seen.add(node)
            if node != root:
                out["tree_parent"].append((sid, f"{s['name']}: 부모를 따라가도 최상위에 닿지 않음"))

    # PRs the page shows open that GitHub has merged or closed, once the collector has had its chance: it probed
    # well after the change, or the change is older than any tier allows.
    shown = {p["key"]: f"#{p['number']} {p.get('title') or ''}".strip() for p in state.get("prs") or []
             if p.get("state") == "OPEN"}
    for i in state.get("items") or []:
        if i.get("source") == "github" and pr_of(i):
            shown.setdefault(pr_of(i), f"{pr_of(i)} 항목 {i['type']}")
    for k, label in sorted(shown.items()):
        pr = (live.get("prs") or {}).get(k)
        if not pr or pr.get("state") in (None, "OPEN"):
            continue
        changed = fleet.parse(pr.get("mergedAt") or pr.get("closedAt"))
        # A probe right after the change may still have read OPEN; one GRACE_S later should not have.
        seen_after = probed.get(k) and changed and probed[k] > changed + dt.timedelta(seconds=GRACE_S)
        if seen_after or (changed and (gen - changed).total_seconds() > PR_OVERDUE_S):
            out["pr_state"].append((k, f"{label}: 대시보드 OPEN, GitHub {pr['state']} ({fleet.iso(changed)})"))

    # Needs you items that are already dealt with.
    answered = fleet.answered_mail(live.get("messages") or [])
    for i in state.get("items") or []:
        key, src, name = i.get("key") or "", i.get("source"), (ss.get(i.get("session")) or {}).get("name") or i.get("session")
        if src == "selfcheck":
            continue
        if i.get("session") and i["session"] not in ss:
            out["item_session_gone"].append((key, f"{i.get('title')}: 세션 {i['session']} 이 목록에 없음"))
        if not (i.get("title") or "").strip() or BAD_TEXT_RE.search(i.get("title") or ""):
            out["item_text"].append((key, f"{i.get('type')} 항목 제목 {i.get('title')!r}"))
        if src == "turn" and key.startswith("msg:"):
            pane, _, h = key[4:].rpartition(":")
            a = panes.get(pane)
            if not a:
                continue
            since = fleet.ms_iso(a.get("stateStartedAt")) or ""
            if a.get("state") in ("working", "waiting") and since > (i.get("at") or ""):
                out["item_answered"].append((key, f"{name}: {i.get('title')} 뒤로 새 턴이 {since} 에 시작됨"))
            elif a.get("state") == "done" and fleet.digest(fleet.mask(a.get("lastAssistantMessage"), 1200)) != h:
                out["item_answered"].append((key, f"{name}: {i.get('title')} 뒤로 다른 턴이 끝남"))
        elif src == "orca-mail" and key[5:] in answered:
            out["mail_answered"].append((key, f"{i.get('title')}: 스레드에 답장이 있음"))
        elif src == "orca" and key.startswith("wait:") and wts:
            a = next((a for p, a in panes.items() if key.startswith(f"wait:{p}:")), None)
            if not a or a.get("state") != "waiting":
                out["prompt_gone"].append((key, f"{name}: 에이전트가 {a.get('state') if a else '없음'}"))

    # A session's shown last activity against its worktree's and agents' own times, up to when the state was made.
    for sid, s in ss.items():
        w = wts.get(sid)
        if not w:
            continue
        times = [fleet.ms_iso(w.get("lastActivityAt"))] + [fleet.ms_iso(a.get("updatedAt")) for a in w.get("agents") or []]
        latest = max((t for t in map(fleet.parse, filter(None, times)) if t <= gen), default=None)
        shown_at = fleet.parse(s.get("last_activity"))
        if latest and (not shown_at or (latest - shown_at).total_seconds() > ACTIVITY_LAG_S):
            out["activity_stale"].append((sid, f"{s['name']}: 보이는 {s.get('last_activity')}, 실제 {fleet.iso(latest)}"))
    return out


class SelfCheck:
    """Runs findings() on the fleet loop and keeps the Needs you items for what two reads in a row agree on."""

    def __init__(self, now=fleet.utcnow, clock=time.monotonic):
        self.now, self.clock = now, clock
        # seen: (check, subject) pairs the previous read found; confirmed: those two reads in a row found
        self.last, self.seen, self.confirmed, self.since = None, set(), set(), {}
        self.items = []

    def due(self):
        if self.last is None:
            return True
        return self.clock() - self.last >= (GRACE_S if self.seen - self.confirmed else EVERY)

    def run(self, f):
        """One check against f.state. Returns True when the items changed."""
        state = f.state
        if not state:
            return False
        self.last = self.clock()
        try:
            live = read_live(f, state)
        except fleet.SourceError:
            return False
        with f.lock:
            probed = {k: fleet.parse(v.get("probed_at")) for k, v in f.prs.items()}
        now = self.now()
        found = findings(state, live, probed, fleet.config(), now)
        pairs = {(c, sub) for c, rows in found.items() for sub, _ in rows}
        confirmed = self.confirmed = pairs & self.seen
        self.seen = pairs
        items = []
        for c in CHECKS:
            rows = [(sub, d) for sub, d in found.get(c, []) if (c, sub) in confirmed]
            if not rows:
                self.since.pop(c, None)
                continue
            at = self.since.setdefault(c, fleet.iso(now))
            items.append({"key": f"check:{c}:{fleet.digest(*sorted(sub for sub, _ in rows))}", "type": "selfcheck",
                          "check": c, "session": None, "title": f"대시보드 점검: {TITLES[c]} ({len(rows)}건)",
                          "detail": fleet.mask("; ".join(d for _, d in rows[:10]) + (" …" if len(rows) > 10 else ""), 600),
                          "at": at, "source": "selfcheck"})
        changed = [i["key"] for i in items] != [i["key"] for i in self.items]
        self.items = items
        return changed
