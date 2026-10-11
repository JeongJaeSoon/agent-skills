#!/usr/bin/env python3
"""Bookkeeping for watch-prs: which pull requests need the owner, ranked, sent once per head.

Usage:
  python3 prs.py precheck [--config PATH]
  python3 prs.py plan [--body] [--config PATH]
  python3 prs.py actions [--config PATH]
  python3 prs.py log --key KEY --source SOURCE [--thread CHANNEL:TS] [--scheduled ID] [--post-at EPOCH] [--at EPOCH]
  python3 prs.py commit [--config PATH]
  python3 prs.py schedule [--workspace SELECTOR] [--provider claude] [--edit AUTOMATION_ID] [--write]

precheck asks GitHub (through `gh`) for the items and exits 0 when some are not in the ledger yet,
1 when nothing is new or a run still holds plan.json (younger than lock_minutes), and 2 when it
could not ask (no config, `gh` failed). It uses no model and writes nothing.
plan prints the same items as JSON and writes nothing. plan --body is the run's step: it prints the
coordinator message (empty when nothing is new), keeps it in body.txt, and, when something is new,
writes the items to plan.json, which is also the run lock. commit records what plan.json delivered,
so the same (repo, number, headRefOid) is never sent twice. actions prints the Slack and bot actions plan.json
holds; log records one of them in the send-log (sendlog.jsonl), which is what decides them, not the ledger. schedule prints the
`orca automations create` command (`edit <id>` with --edit) and runs it only with --write.
Config: ~/.claude/agent-skills.json → "prs" (see SKILL.md). State: $AGENT_SKILLS_STATE/prs
(default ~/.local/state/agent-skills/prs). The ledger holds keys, node ids and timestamps, never PR text.
"""
import datetime as dt, json, os, pathlib, shlex, subprocess, sys, time, zoneinfo

CONFIG = pathlib.Path("~/.claude/agent-skills.json").expanduser()
STATE = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser() / "prs"
GH = os.environ.get("AGENT_SKILLS_GH", "gh")
# Kinds without a collector are reserved so later flows extend COLLECTORS, not the CLI.
KINDS = ("review_request", "review_wait", "ready_to_merge", "unreleased", "ownergate")
LABELS = {"review_request": "리뷰 요청", "review_wait": "내 PR 대기"}
DEFAULTS = {"kinds": ["review_request"], "orgs": [], "repos": [], "exclude_orgs": [], "exclude_repos": [],
            "ignore_bots": [], "self_bots": [], "inbox_to": None, "max_items": 10, "stale_days": 30,
            "keep_days": 60, "lock_minutes": 60, "cron": "*/15 * * * *", "timezone": None,
            "channel": None, "repo_channels": {}, "reviewers": {}, "review_bots": {}, "holidays": [],
            "urgent_labels": [], "batch_min": 2, "batch_wait_hours": 2, "night_hour": 21,
            "morning_hour": 9, "programs_dir": None}
EXIT_NEW, EXIT_NOTHING, EXIT_ERROR = 0, 1, 2
SOURCES = ("sent", "scheduled", "found", "refused", "no_thread", "skipped")

PR_FIELDS = """id number title url isDraft createdAt updatedAt headRefOid additions deletions reviewDecision state
 repository{nameWithOwner} author{__typename login}
 reviewRequests(first:50){nodes{asCodeOwner requestedReviewer{__typename ... on User{login} ... on Team{slug}}}}
 reviews(author:$me,last:1,states:[APPROVED,CHANGES_REQUESTED,COMMENTED,DISMISSED]){nodes{commit{oid}}}"""
SEARCH_Q = ("query($q:String!,$me:String!,$after:String){search(query:$q,type:ISSUE,first:50,after:$after)"
            "{pageInfo{hasNextPage endCursor} nodes{... on PullRequest{" + PR_FIELDS + "}}}}")
NODES_Q = "query($ids:[ID!]!,$me:String!){nodes(ids:$ids){... on PullRequest{" + PR_FIELDS + "}}}"
MINE_FIELDS = """id number title url isDraft createdAt updatedAt headRefOid additions deletions reviewDecision state
 repository{nameWithOwner} author{__typename login} labels(first:20){nodes{name}}
 reviewRequests(first:50){nodes{asCodeOwner requestedReviewer{__typename ... on User{login} ... on Team{slug}}}}
 latestReviews(first:50){nodes{author{__typename login} state submittedAt commit{oid}}}
 reviewThreads(first:100){nodes{isResolved opener:comments(first:1){nodes{author{login}}}
  last:comments(last:1){nodes{author{login} createdAt}}}}
 timelineItems(last:50,itemTypes:[REVIEW_REQUESTED_EVENT,REVIEW_DISMISSED_EVENT]){nodes{__typename
  ... on ReviewRequestedEvent{createdAt requestedReviewer{__typename ... on User{login}}}
  ... on ReviewDismissedEvent{createdAt previousReviewState review{author{login}}}}}
 commits(last:1){nodes{commit{committedDate}}}"""
MINE_Q = ("query($q:String!,$after:String){search(query:$q,type:ISSUE,first:25,after:$after)"
          "{pageInfo{hasNextPage endCursor} nodes{... on PullRequest{" + MINE_FIELDS + "}}}}")


def opt(name, default=None):
    a = sys.argv
    i = a.index(name) + 1 if name in a else 0
    return a[i] if 0 < i < len(a) else default


def config(path=CONFIG):
    try:
        raw = json.loads(pathlib.Path(path).read_text()).get("prs")
    except (OSError, ValueError):
        raw = None
    return None if raw is None else {**DEFAULTS, **raw}


def read_json(path, default):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    tmp.replace(path)


def gh_graphql(query, partial=False, **variables):
    """partial: keep the data GitHub did return when some nodes failed (a deleted repo, lost access)."""
    # --input keeps list variables (nodes ids) as JSON arrays; -f would send them as strings.
    body = json.dumps({"query": query, "variables": {k: v for k, v in variables.items() if v is not None}})
    out = subprocess.run([GH, "api", "graphql", "--input", "-"], input=body, capture_output=True, text=True, timeout=60)
    try:
        data = json.loads(out.stdout).get("data")
    except ValueError:
        data = None
    if data is None or (out.returncode and not partial):
        raise RuntimeError(f"gh api graphql: {out.stderr.strip()[:300]}")
    return data


class GitHub:
    """The reads watch-prs needs. Tests replace it with a fake holding fixture nodes."""

    def viewer(self):
        return gh_graphql("query{viewer{login}}")["viewer"]["login"]

    def _paged(self, query, **variables):
        nodes, after = [], None
        while True:
            page = gh_graphql(query, after=after, **variables)["search"]
            nodes += [n for n in page["nodes"] if n]
            if not page["pageInfo"]["hasNextPage"]:
                return nodes
            after = page["pageInfo"]["endCursor"]

    def search(self, q, me):
        return self._paged(SEARCH_Q, q=q, me=me)

    def mine(self, self_bots):
        """The owner's open PRs, plus a self bot's PRs assigned to the owner: a bot app is often shared org-wide."""
        qs = ["author:@me"] + [f"author:app/{bot_name(b)} assignee:@me" for b in self_bots]
        return list({n["id"]: n for q in qs for n in self._paged(MINE_Q, q=f"is:pr is:open archived:false {q}")}.values())

    def nodes(self, ids, me):
        return [n for i in range(0, len(ids), 50) for n in gh_graphql(NODES_Q, partial=True, ids=ids[i:i + 50], me=me)["nodes"] if n]


def key(pr):
    return f"{pr['repository']['nameWithOwner']}#{pr['number']}@{pr['headRefOid']}"


def bot_name(login):
    return (login or "").lower().removesuffix("[bot]")


def ts(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def days(since, now):
    return int((now - ts(since)) // 86400)


def repo_out_of_scope(repo, cfg):
    org = repo.split("/")[0]
    if repo in cfg["exclude_repos"] or org in cfg["exclude_orgs"]:
        return "제외 대상 저장소"
    if (cfg["orgs"] or cfg["repos"]) and org not in cfg["orgs"] and repo not in cfg["repos"]:
        return "대상 저장소 아님"
    return None


def out_of_scope(pr, cfg):
    why = repo_out_of_scope(pr["repository"]["nameWithOwner"], cfg)
    if why:
        return why
    author = pr.get("author") or {}
    name = bot_name(author.get("login"))
    if name in {bot_name(b) for b in cfg["self_bots"]}:
        return "본인 봇이 작성"
    if name in {bot_name(b) for b in cfg["ignore_bots"]}:
        return "무시하는 봇이 작성"
    return None


def personal_request(pr, me):
    """The owner as a User reviewer, not through a team or CODEOWNERS; GitHub's search qualifier mixes those in."""
    return any(n["requestedReviewer"] and n["requestedReviewer"].get("__typename") == "User"
               and n["requestedReviewer"].get("login") == me and not n["asCodeOwner"]
               for n in pr["reviewRequests"]["nodes"])


def only_blocker(pr, me):
    reqs = [n["requestedReviewer"] for n in pr["reviewRequests"]["nodes"] if n["requestedReviewer"]]
    return pr.get("reviewDecision") == "REVIEW_REQUIRED" and reqs == [{"__typename": "User", "login": me}]


def item(pr, me, cfg, now, reason):
    author = pr.get("author") or {}
    human = author.get("__typename") == "User"
    idle = days(pr["updatedAt"], now)
    stale = idle > cfg["stale_days"]
    why = []
    if stale:
        why.append(f"stale: {idle}일 움직임 없음, 요청 해제 제안")
    if only_blocker(pr, me):
        why.append("남은 리뷰어가 나뿐")
    why.append("사람 작성" if human else "봇 작성")
    return {"kind": "review_request", "key": key(pr), "id": pr["id"], "repo": pr["repository"]["nameWithOwner"],
            "number": pr["number"], "title": pr["title"], "url": pr["url"], "author": author.get("login"),
            "human": human, "age_days": days(pr["createdAt"], now), "idle_days": idle,
            "size": pr["additions"] + pr["deletions"], "only_blocker": only_blocker(pr, me), "stale": stale,
            "reason": reason, "why": why}


def rank(items):
    """Stale last; then the owner as the only blocking reviewer, a human author, older first, smaller first."""
    return sorted(items, key=lambda i: (i["stale"], not i["only_blocker"], not i["human"], -i["age_days"], i["size"]))


def collect_review_requests(gh, me, cfg, ledger, now):
    """Personal review requests, plus ledger PRs the owner reviewed that got new commits without a re-request."""
    items, excluded, seen = [], [], set()
    for pr in gh.search("is:pr is:open archived:false review-requested:@me", me):
        seen.add(pr["id"])
        name = f"{pr['repository']['nameWithOwner']}#{pr['number']}"
        why = (out_of_scope(pr, cfg) or (None if personal_request(pr, me) else "팀·CODEOWNERS 요청뿐")
               or ("draft" if pr["isDraft"] else None))
        if why:
            excluded.append({"pr": name, "why": why})
        else:
            items.append(item(pr, me, cfg, now, "requested"))
    watch = sorted({v["id"] for v in ledger.values() if v.get("kind") == "review_request" and v.get("id")} - seen)
    for pr in gh.nodes(watch, me) if watch else []:
        mine = pr["reviews"]["nodes"]
        if (pr["state"] == "OPEN" and not pr["isDraft"] and not out_of_scope(pr, cfg)
                and mine and (mine[0].get("commit") or {}).get("oid") != pr["headRefOid"]):
            items.append(item(pr, me, cfg, now, "new_commits"))
    return items, excluded


def read_jsonl(path):
    """Rows of a JSONL file; a torn line (another writer mid-append) is skipped, not fatal."""
    try:
        lines = pathlib.Path(path).read_text().splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def local(t, cfg):
    return dt.datetime.fromtimestamp(t, zoneinfo.ZoneInfo(cfg["timezone"]) if cfg.get("timezone") else None)


def business_day(d, cfg):
    return d.weekday() < 5 and d.isoformat() not in cfg["holidays"]


def next_business_day(d, cfg):
    d += dt.timedelta(days=1)
    while not business_day(d, cfg):
        d += dt.timedelta(days=1)
    return d


def one_business_day_after(t, cfg):
    l = local(t, cfg)
    return dt.datetime.combine(next_business_day(l.date(), cfg), l.timetz()).timestamp()


def post_at(now, cfg):
    """None in business hours; otherwise the next business morning, for which a Slack message is scheduled instead."""
    l = local(now, cfg)
    if business_day(l.date(), cfg) and cfg["morning_hour"] <= l.hour < cfg["night_hour"]:
        return None
    early = business_day(l.date(), cfg) and l.hour < cfg["morning_hour"]
    day = l.date() if early else next_business_day(l.date(), cfg)
    return dt.datetime.combine(day, dt.time(cfg["morning_hour"], tzinfo=l.tzinfo)).timestamp()


def program_prs(cfg, now):
    """{(repo, number): slug} for PRs a live orchestrate program drives (its ledger written within stale_days);
    `orch wait` follows those, and a parked one is held on purpose. Programs have no closed state, so an idle
    ledger is how an abandoned program's PRs come back to reminders."""
    root = pathlib.Path(cfg["programs_dir"] or os.environ.get("PROGRAMS_HOME") or "~/.claude/programs").expanduser()
    out = {}
    for prog in root.glob("*/program.json"):
        ledger = prog.parent / "ledger.jsonl"
        try:
            if now - ledger.stat().st_mtime > cfg["stale_days"] * 86400:
                continue
        except OSError:
            continue
        repo = read_json(prog, {}).get("repo")
        out.update({(repo, r["pr"]): prog.parent.name for r in read_jsonl(ledger) if isinstance(r.get("pr"), int)})
    return out


def login(node):
    return ((node or {}).get("author") or {}).get("login") or ""


def wait_state(pr, me, cfg):
    """What one of the owner's PRs waits on, from GitHub alone."""
    owner = {me.lower()} | {bot_name(b) for b in cfg["self_bots"]}
    bots = review_bots(cfg)
    reqs = [n["requestedReviewer"] for n in pr["reviewRequests"]["nodes"] if n["requestedReviewer"]]
    asked, dismissed = {}, set()
    for e in pr["timelineItems"]["nodes"]:
        if e["__typename"] == "ReviewRequestedEvent" and (e.get("requestedReviewer") or {}).get("login"):
            asked[e["requestedReviewer"]["login"]] = ts(e["createdAt"])
        elif e["__typename"] == "ReviewDismissedEvent" and e.get("previousReviewState") == "APPROVED":
            dismissed.add(login(e.get("review")))
    reviews = {login(r): r for r in pr["latestReviews"]["nodes"] if login(r)}
    people = {who for who, r in reviews.items() if r["author"].get("__typename") == "User" and who.lower() not in owner}
    # A push (or a retarget) voided the approval: dismissed, or still APPROVED on an older head while the PR needs one.
    # Only REVIEW_REQUIRED: null means no review is required, CHANGES_REQUESTED means it waits on someone else.
    stale = sorted(who for who in people if pr.get("reviewDecision") == "REVIEW_REQUIRED" and (
        (reviews[who]["state"] == "APPROVED" and (reviews[who].get("commit") or {}).get("oid") != pr["headRefOid"])
        or (reviews[who]["state"] == "DISMISSED" and who in dismissed)))
    pending = [r["login"] for r in reqs if r.get("__typename") == "User" and r["login"] not in stale]
    head_at = max((ts(c["commit"]["committedDate"]) for c in pr["commits"]["nodes"]), default=0)
    open_threads = [t for t in pr["reviewThreads"]["nodes"] if not t["isResolved"]]
    unanswered = sum(1 for t in open_threads for c in t["last"]["nodes"][-1:]
                     if bot_name(login(c)) not in owner and ts(c["createdAt"]) > head_at)
    bot_stale = sorted(who for who, r in reviews.items() if r["state"] == "CHANGES_REQUESTED"
                       and (r["author"].get("__typename") == "Bot" or bot_name(who) in bots)
                       and not any(bot_name(login(c)) == bot_name(who) for t in open_threads for c in t["opener"]["nodes"]))
    repo = pr["repository"]["nameWithOwner"]
    return {"pr": f"{repo}#{pr['number']}", "repo": repo, "head": pr["headRefOid"], "url": pr["url"],
            "title": pr["title"], "pending": pending, "asked": {w: asked.get(w, ts(pr["createdAt"])) for w in pending},
            "teams": [r["slug"] for r in reqs if r.get("__typename") == "Team"], "stale_approvals": stale,
            "unanswered": unanswered, "bot_stale": bot_stale,
            "no_reviewer": pr.get("reviewDecision") == "REVIEW_REQUIRED" and not reqs and not people,
            "urgent": any(l["name"] in cfg["urgent_labels"] for l in pr["labels"]["nodes"])}


def review_bots(cfg):
    return {bot_name(b): comment for b, comment in cfg["review_bots"].items()}


def wait_action(kind, s, who, cfg, at, thread=None, comment=None):
    person = cfg["reviewers"].get(who, {})
    slack = kind != "retrigger"
    return {"action": kind, "pr": s["pr"], "head": s["head"], "reviewer": who, "url": s["url"], "title": s["title"],
            "slack": person.get("slack"), "thread": thread, "comment": comment, "post_at": at if slack else None,
            "channel": (person.get("channel") or cfg["repo_channels"].get(s["repo"]) or cfg["channel"]) if slack else None}


def decide(states, sendlog, now, cfg):
    """The actions due now, from PR states, the send-log and the clock; pure, so each reminder rule is a test.

    request    pending reviewer with no row for this PR yet. Batched per reviewer: due once they have batch_min
               waiting, the oldest waited batch_wait_hours, or one is urgent, and never while a request to them
               is still scheduled (a scheduled message cannot be edited, so the next batch waits for it).
    remind     once per request row, one business day after it (or after its scheduled post), in its thread.
    rerequest  a voided approval, once per head: the same reviewer is not pinged again until a new push.
    retrigger  a review bot's stale CHANGES_REQUESTED, once per head, with the bot's trigger comment.
    A refused row stops every action for that (PR, reviewer); a skipped row (the round judged it moot) stops
    only that action at that head. Unmapped reviewers get no action, only a notice.
    Slack actions outside business hours carry post_at: the next business morning."""
    at = post_at(now, cfg)
    acts, held, bots, by = [], {}, review_bots(cfg), {}
    for r in sendlog:
        by.setdefault((r["pr"], r["reviewer"]), []).append(r)
    scheduled = {r["reviewer"] for r in sendlog if r["action"] == "request" and (r.get("post_at") or 0) > now}
    for s in states:
        for who in s["pending"] + s["stale_approvals"]:
            mine = by.get((s["pr"], who), [])
            if who not in cfg["reviewers"] or any(r["source"] == "refused" for r in mine):
                continue
            skip = {r["action"] for r in mine if r["source"] == "skipped" and r["head"] == s["head"]}
            mine = [r for r in mine if r["source"] != "skipped"]
            asks = [r for r in mine if r["action"] in ("request", "rerequest")]
            thread = next((r["thread"] for r in reversed(mine) if r.get("thread")), None)
            if who in s["stale_approvals"]:
                if "rerequest" not in skip and not any(r["head"] == s["head"] for r in asks):
                    acts.append(wait_action("rerequest", s, who, cfg, at, thread))
            elif not asks:
                if "request" not in skip:
                    held.setdefault(who, []).append(s)
            else:
                last = max(asks, key=lambda r: r["at"])
                since = max(last["at"], last.get("post_at") or 0)
                reminded = any(r["action"] == "remind" and r["at"] >= last["at"] for r in mine)
                if "remind" not in skip and not reminded and now >= one_business_day_after(since, cfg):
                    acts.append(wait_action("remind", s, who, cfg, at, thread))
        for bot in s["bot_stale"]:
            comment = bots.get(bot_name(bot))
            if comment and not any(r["head"] == s["head"] or r["source"] == "refused" for r in by.get((s["pr"], bot), [])):
                acts.append(wait_action("retrigger", s, bot, cfg, None, comment=comment))
    for who, waiting in held.items():
        if who in scheduled:
            continue
        oldest = min(s["asked"][who] for s in waiting)
        if (len(waiting) >= cfg["batch_min"] or now - oldest >= cfg["batch_wait_hours"] * 3600
                or any(s["urgent"] for s in waiting)):
            acts += [wait_action("request", s, who, cfg, at) for s in waiting]
    return acts


def notices(s, cfg):
    """What the coordinator hears about once per head: states no Slack action covers."""
    out = []
    if s["no_reviewer"]:
        out.append("리뷰어 없음")
    if s["unanswered"]:
        out.append(f"답하지 않은 스레드 {s['unanswered']}건")
    unmapped = [w for w in s["pending"] + s["stale_approvals"] if w not in cfg["reviewers"]]
    if unmapped:
        out.append("Slack 매핑 없는 리뷰어: " + ", ".join(f"@{w}" for w in unmapped))
    if s["teams"] and not s["pending"]:
        out.append("팀 리뷰 대기: " + ", ".join(s["teams"]))
    stuck = [b for b in s["bot_stale"] if bot_name(b) not in review_bots(cfg)]
    if stuck:
        out.append("봇 재리뷰 필요(트리거 미설정): " + ", ".join(f"@{b}" for b in stuck))
    return out


def describe(a, cfg):
    when = f" · {local(a['post_at'], cfg).strftime('%m-%d %H:%M')} 예약" if a["post_at"] else ""
    return {"request": f"리뷰 의뢰 → @{a['reviewer']}", "remind": f"리마인드 → @{a['reviewer']}",
            "rerequest": f"재리뷰 요청(승인 무효) → @{a['reviewer']}",
            "retrigger": f"봇 재리뷰 트리거 → @{a['reviewer']}"}[a["action"]] + when


def wait_item(pr, now, k, why):
    author = pr.get("author") or {}
    return {"kind": "review_wait", "key": k, "id": pr["id"], "repo": pr["repository"]["nameWithOwner"],
            "number": pr["number"], "title": pr["title"], "url": pr["url"], "author": author.get("login"),
            "human": author.get("__typename") == "User", "age_days": days(pr["createdAt"], now),
            "idle_days": days(pr["updatedAt"], now), "size": pr["additions"] + pr["deletions"],
            "only_blocker": False, "stale": False, "reason": "waiting", "why": why}


def collect_review_wait(gh, me, cfg, ledger, now):
    """The owner's open PRs waiting on people: one notice per head for the coordinator, and the actions decide() finds
    due. Actions carry once=False: the send-log, not the ledger, decides whether they were done."""
    programs = program_prs(cfg, now)
    items, excluded, states = [], [], {}
    for pr in gh.mine(cfg["self_bots"]):
        repo, n = pr["repository"]["nameWithOwner"], pr["number"]
        idle = days(pr["updatedAt"], now)
        why = (repo_out_of_scope(repo, cfg) or ("draft" if pr["isDraft"] else None)
               or (f"stale: {idle}일 움직임 없음" if idle > cfg["stale_days"] else None)
               or (f"프로그램 PR ({programs[(repo, n)]})" if (repo, n) in programs else None))
        if why:
            excluded.append({"pr": f"{repo}#{n}", "why": why})
            continue
        s = wait_state(pr, me, cfg)
        states[s["pr"]] = (pr, s)
        if why := notices(s, cfg):
            items.append(wait_item(pr, now, f"{key(pr)}/review_wait", why))
    for a in decide([s for _, s in states.values()], read_jsonl(STATE / "sendlog.jsonl"), now, cfg):
        pr = states[a["pr"]][0]
        items.append({**wait_item(pr, now, f"{key(pr)}/{a['action']}/{a['reviewer']}", [describe(a, cfg)]), **a, "once": False})
    return items, excluded


def log(state, k, source, now, **fields):
    """Append the outcome of one plan.json action to the send-log, the record decide() reads."""
    plan = read_json(state / "plan.json", None) or {"new": []}
    act = next((i for i in plan["new"] if i["key"] == k and "action" in i), None)
    if act is None:
        raise SystemExit(f"log: {k} is not an action in plan.json")
    if source not in SOURCES:
        raise SystemExit(f"log: source must be one of {', '.join(SOURCES)}")
    row = {"pr": act["pr"], "reviewer": act["reviewer"], "head": act["head"], "action": act["action"],
           "source": source, "at": fields.get("at") or now, "post_at": fields.get("post_at"),
           "thread": fields.get("thread"), "scheduled": fields.get("scheduled")}
    state.mkdir(parents=True, exist_ok=True)
    with (state / "sendlog.jsonl").open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


COLLECTORS = {"review_request": collect_review_requests, "review_wait": collect_review_wait}


def compute(gh, cfg, ledger, now):
    me = gh.viewer()
    items, excluded = [], []
    for kind in cfg["kinds"]:
        if kind not in COLLECTORS:
            raise RuntimeError(f"kind {kind!r} is not implemented yet (known: {', '.join(KINDS)})")
        got, out = COLLECTORS[kind](gh, me, cfg, ledger, now)
        items += got
        excluded += out
    new = [i for i in rank(items) if i["key"] not in ledger]
    # Actions are never capped: a reviewer's batch split across rounds would become two messages.
    acts, new = [i for i in new if not i.get("once", True)], [i for i in new if i.get("once", True)]
    return {"run": now, "viewer": me, "new": acts + new[: cfg["max_items"]], "more": max(0, len(new) - cfg["max_items"]),
            "known": [i["key"] for i in items if i["key"] in ledger], "excluded": excluded}


def body(plan):
    if not plan["new"]:
        return ""
    head = f"PR {len(plan['new'])}건이 나를 기다린다" + (f" (그 밖에 {plan['more']}건은 다음 회차)" if plan["more"] else "")
    lines = [head]
    for n, i in enumerate(plan["new"], 1):
        tag = "새 커밋, 재요청 없음" if i["reason"] == "new_commits" else LABELS[i["kind"]]
        lines.append(f"{n}. [{tag}] {i['repo']}#{i['number']} {i['title']} — @{i['author']} · {i['age_days']}일 · "
                     f"±{i['size']} · {', '.join(i['why'])} — {i['url']}")
    return "\n".join(lines)


def lock_held(state, cfg, now):
    """plan.json is the run lock: written by plan, removed by commit. A run that dies frees it after lock_minutes."""
    plan = read_json(state / "plan.json", None)
    return bool(plan) and now - plan.get("run", 0) < cfg["lock_minutes"] * 60


def precheck(gh, cfg, state, now):
    if cfg is None:
        return EXIT_ERROR, 'config 에 "prs" 가 없음'
    if lock_held(state, cfg, now):
        return EXIT_NOTHING, "이전 회차가 아직 진행 중(plan.json)"
    ledger = read_json(state / "ledger.json", {})
    try:
        plan = compute(gh, cfg, ledger, now)
    except (RuntimeError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as e:
        return EXIT_ERROR, f"GitHub 를 읽지 못함: {e}"
    keys = [i["key"] for i in plan["new"]]
    if not keys:
        return EXIT_NOTHING, f"새 항목 없음 (제외 {len(plan['excluded'])}건)"
    return EXIT_NEW, f"새 항목 {len(keys)}건: " + ", ".join(keys)


def commit(state, cfg, now):
    plan = read_json(state / "plan.json", None)
    if not plan:
        raise SystemExit("commit: no plan.json; run plan first")
    ledger = read_json(state / "ledger.json", {})
    once = [i for i in plan["new"] if i.get("once", True)]
    for i in once:
        ledger[i["key"]] = {"at": plan["run"], "kind": i["kind"], "id": i["id"]}
    # A request left open for months is still known; only keys that stopped appearing age out.
    for k in plan.get("known", []):
        if k in ledger:
            ledger[k]["at"] = plan["run"]
    horizon = now - cfg["keep_days"] * 86400
    ledger = {k: v for k, v in ledger.items() if v["at"] >= horizon}
    write_json(state / "ledger.json", ledger)
    (state / "plan.json").unlink()
    return len(once)


# A kind's own step in the automation prompt, between the coordinator send and commit; {env} is the prs.py call.
PROMPT_STEPS = {
    "review_wait": (
        "그다음 `{env} actions` 가 출력하는 행동을 하나씩 처리하고, 처리할 때마다 `{env} log --key <key> --source <결과>` 로 "
        "기록한다. Slack 행동(request·remind·rerequest)은 slack-post 스킬을 먼저 읽고 그 규칙대로 channel 에 보낸다: "
        "request 는 같은 reviewer 의 것을 한 메시지로 묶고, 보내기 전에 channel 에서 그 PR 의 기존 의뢰를 찾아 있으면 보내지 않고 "
        "`--source found --thread <channel>:<ts> --at <ts>` 로 기록한다. remind·rerequest 는 PR 마다 thread 에 답글로만 보낸다"
        "(reply_broadcast false). thread 가 비어 있으면 channel 에서 그 PR 의 의뢰 스레드를 찾고, 없으면 최상위로 올리지 않고 "
        "`--source no_thread` 로 기록한다. post_at 이 있으면 바로 보내지 않고 그 시각으로 예약하고 "
        "`--source scheduled --scheduled <id> --post-at <post_at>`(스레드 답글이면 `--thread <channel>:<부모 ts>` 도) 로, 보냈으면 "
        "`--source sent --thread <channel>:<ts>` 로 기록한다. retrigger 는 그 PR 에 `gh pr comment <url> --body <comment>` 를 "
        "달고 `--source sent` 로 기록한다. 보내기 직전에 이미 필요 없어진 행동(그사이 승인됨, 리뷰어의 slack id 없음 등)은 "
        "보내지 않고 `--source skipped` 로 기록한다. 어느 행동이든 권한·분류기·훅에 거부되면 다른 방법으로 다시 시도하지 않는다: "
        "`--source refused` 로 기록하고 거부 범주와 명령을 코디네이터에게 보낸다. GitHub 쓰기는 retrigger 코멘트뿐이다."),
}


def schedule_cmd(script, cfg, workspace, provider="claude", edit=None):
    """The `orca automations create` command, or `edit <id>` to keep an existing automation's id."""
    env = " ".join(["env", f"AGENT_SKILLS_STATE={shlex.quote(str(STATE.parent))}", "python3", shlex.quote(str(script))])
    to = cfg.get("inbox_to") or "<prs.inbox_to>"
    body_file = STATE / "body.txt"
    steps = " ".join(PROMPT_STEPS[k].format(env=env) for k in cfg["kinds"] if k in PROMPT_STEPS)
    reads = "" if steps else " 이 회차는 읽기만 한다: 리뷰·코멘트·재요청 같은 GitHub 쓰기는 하지 않는다."
    prompt = (f"watch-prs 회차다. `{env} plan --body` 를 한 번 실행한다. 출력이 비어 있으면 아무것도 보내지 않고 끝낸다. "
              f"아니면 `orca orchestration send --to {shlex.quote(to)} --type status --subject \"watch-prs: <N>건\" "
              f"--body \"$(cat {shlex.quote(str(body_file))})\" --json` 로 한 통만 보낸다(N 은 본문 첫 줄의 건수). "
              + (steps + " " if steps else "")
              + f"코디네이터 전송이 성공했을 때만 마지막에 `{env} commit` 을 실행한다." + reads)
    if edit:
        return ["orca", "automations", "edit", edit, "--trigger", cfg["cron"], "--precheck", f"{env} precheck",
                "--prompt", prompt, "--json"] + (["--timezone", cfg["timezone"]] if cfg.get("timezone") else [])
    cmd = ["orca", "automations", "create", "--name", "agent-skills watch-prs", "--trigger", cfg["cron"],
           "--provider", provider, "--workspace-mode", "existing", "--workspace", workspace,
           "--precheck", f"{env} precheck", "--prompt", prompt, "--json"]
    if cfg.get("timezone"):
        cmd[cmd.index("--provider"):cmd.index("--provider")] = ["--timezone", cfg["timezone"]]
    return cmd


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    cfg = config(opt("--config", CONFIG))
    now = time.time()
    if cmd == "precheck":
        code, why = precheck(GitHub(), cfg, STATE, now)
        print(why, file=sys.stderr)
        sys.exit(code)
    if cmd in ("plan", "actions", "log", "commit", "schedule") and cfg is None:
        sys.exit(f'{cmd}: set "prs" in {CONFIG}')
    if cmd == "plan" and "--body" not in sys.argv:
        print(json.dumps(compute(GitHub(), cfg, read_json(STATE / "ledger.json", {}), now), ensure_ascii=False, indent=1))
    elif cmd == "plan":
        if lock_held(STATE, cfg, now):
            sys.exit("plan: another run holds plan.json; it frees after lock_minutes")
        plan = compute(GitHub(), cfg, read_json(STATE / "ledger.json", {}), now)
        if plan["new"]:
            write_json(STATE / "plan.json", plan)
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / "body.txt").write_text(body(plan))
        print(body(plan))
    elif cmd == "actions":
        for i in (read_json(STATE / "plan.json", None) or {"new": []})["new"]:
            if "action" in i:
                print(json.dumps({k: i[k] for k in ("key", "action", "url", "title", "reviewer", "slack", "channel",
                                                     "thread", "post_at", "comment")}, ensure_ascii=False))
    elif cmd == "log":
        num = lambda name: float(opt(name)) if opt(name) else None
        row = log(STATE, opt("--key"), opt("--source"), now, thread=opt("--thread"), scheduled=opt("--scheduled"),
                  post_at=num("--post-at"), at=num("--at"))
        print(json.dumps(row, ensure_ascii=False))
    elif cmd == "commit":
        print(f"commit: {commit(STATE, cfg, now)} items recorded")
    elif cmd == "schedule":
        script = pathlib.Path(__file__).resolve()
        line = schedule_cmd(script, cfg, opt("--workspace") or f"path:{script.parents[3]}", opt("--provider", "claude"),
                            opt("--edit"))
        print(shlex.join(line))
        if "--write" in sys.argv:
            sys.exit(subprocess.run(line).returncode)
        print("\n(출력만 했다. 실제로 만들려면 --write)")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
