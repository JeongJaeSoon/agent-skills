#!/usr/bin/env python3
"""Bookkeeping for watch-prs: which pull requests need the owner, ranked, sent once per head.

Usage:
  python3 prs.py precheck [--config PATH]
  python3 prs.py plan [--body] [--config PATH]
  python3 prs.py merges [--config PATH]
  python3 prs.py verify [--config PATH]
  python3 prs.py commit [--config PATH]
  python3 prs.py schedule [--workspace SELECTOR] [--provider claude] [--write]

precheck asks GitHub (through `gh`) for the items and exits 0 when some are not in the ledger yet,
1 when nothing is new or a run still holds plan.json (younger than lock_minutes), and 2 when it
could not ask (no config, `gh` failed). It uses no model and writes nothing.
plan prints the same items as JSON and writes nothing. plan --body is the run's step: it prints the
coordinator message (empty when nothing is new), keeps it in body.txt, and, when something is new,
writes the items to plan.json, which is also the run lock. commit records what plan.json delivered,
so the same (repo, number, headRefOid) is never sent twice. merges prints the `gh pr merge` command for each
ready_to_merge item in plan.json; the round runs them itself. verify then checks each is MERGED, waits for the
merge commit's CI and rewrites body.txt. schedule prints the
`orca automations create` command and runs it only with --write.
Config: ~/.claude/agent-skills.json → "prs" (see SKILL.md). State: $AGENT_SKILLS_STATE/prs
(default ~/.local/state/agent-skills/prs). The ledger holds keys, node ids and timestamps, never PR text.
"""
import datetime as dt, json, os, pathlib, re, shlex, subprocess, sys, time

CONFIG = pathlib.Path("~/.claude/agent-skills.json").expanduser()
STATE = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser() / "prs"
GH = os.environ.get("AGENT_SKILLS_GH", "gh")
ORCA = os.environ.get("AGENT_SKILLS_ORCA", "orca")
# Kinds without a collector are reserved so later flows extend COLLECTORS, not the CLI.
KINDS = ("review_request", "review_wait", "ready_to_merge", "unreleased", "ownergate")
LABELS = {"review_request": "리뷰 요청", "ready_to_merge": "머지"}
DEFAULTS = {"kinds": ["review_request"], "orgs": [], "repos": [], "exclude_orgs": [], "exclude_repos": [],
            "ignore_bots": [], "self_bots": [], "inbox_to": None, "max_items": 10, "stale_days": 30,
            "keep_days": 60, "lock_minutes": 60, "cron": "*/15 * * * *", "timezone": None,
            "merge_repos": [], "merge_deny_repos": [], "merge_methods": ["squash", "merge", "rebase"],
            "hold_labels": ["hold", "do-not-merge", "wip"], "required_bots": [], "review_bots": [],
            "review_verdict_pattern": r"^\**(?:결론|verdict)(?:\**\s*[:：]|\s*[:：]\s*\**)\s*\**(pass|fail)\**\s*$", "ci_wait_minutes": 15,
            "programs_home": os.environ.get("PROGRAMS_HOME", "~/.claude/programs"), "program_days": 7}
EXIT_NEW, EXIT_NOTHING, EXIT_ERROR = 0, 1, 2

PR_FIELDS = """id number title url isDraft createdAt updatedAt headRefOid additions deletions reviewDecision state
 repository{nameWithOwner} author{__typename login}
 reviewRequests(first:50){nodes{asCodeOwner requestedReviewer{__typename ... on User{login} ... on Team{slug}}}}
 reviews(author:$me,last:1,states:[APPROVED,CHANGES_REQUESTED,COMMENTED,DISMISSED]){nodes{commit{oid}}}"""
SEARCH_Q = ("query($q:String!,$me:String!,$after:String){search(query:$q,type:ISSUE,first:50,after:$after)"
            "{pageInfo{hasNextPage endCursor} nodes{... on PullRequest{" + PR_FIELDS + "}}}}")
NODES_Q = "query($ids:[ID!]!,$me:String!){nodes(ids:$ids){... on PullRequest{" + PR_FIELDS + "}}}"


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
    # Only variables the query declares: GraphQL rejects an unused one, and the readers share one argument list.
    body = json.dumps({"query": query, "variables": {k: v for k, v in variables.items()
                                                     if v is not None and f"${k}:" in query}})
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

    def search(self, q, me, query=SEARCH_Q):
        nodes, after = [], None
        while True:
            page = gh_graphql(query, q=q, me=me, after=after)["search"]
            nodes += [n for n in page["nodes"] if n]
            if not page["pageInfo"]["hasNextPage"]:
                return nodes
            after = page["pageInfo"]["endCursor"]

    def nodes(self, ids, me, query=NODES_Q):
        return [n for i in range(0, len(ids), 50) for n in gh_graphql(query, partial=True, ids=ids[i:i + 50], me=me)["nodes"] if n]

    def search_merge(self, q, me):
        return self.search(q, me, MERGE_SEARCH_Q)

    def merged(self, ids, me):
        return self.nodes(ids, me, MERGED_Q)


def key(pr):
    return f"{pr['repository']['nameWithOwner']}#{pr['number']}@{pr['headRefOid']}"


def bot_name(login):
    return (login or "").lower().removesuffix("[bot]")


def days(since, now):
    return int((now - dt.datetime.fromisoformat(since.replace("Z", "+00:00")).timestamp()) // 86400)


def out_of_scope(pr, cfg):
    repo = pr["repository"]["nameWithOwner"]
    org = repo.split("/")[0]
    if repo in cfg["exclude_repos"] or org in cfg["exclude_orgs"]:
        return "제외 대상 저장소"
    if (cfg["orgs"] or cfg["repos"]) and org not in cfg["orgs"] and repo not in cfg["repos"]:
        return "대상 저장소 아님"
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
    return sorted(items, key=lambda i: (i.get("stale", False), not i.get("only_blocker"), not i.get("human", True),
                                        -i["age_days"], i["size"]))


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


MERGE_FIELDS = """id number title url isDraft createdAt headRefOid headRefName baseRefName additions deletions
 reviewDecision mergeStateStatus state author{__typename login} labels(first:100){nodes{name}}
 repository{nameWithOwner defaultBranchRef{name} squashMergeAllowed mergeCommitAllowed rebaseMergeAllowed}
 reviewThreads(first:100){totalCount nodes{isResolved}} reviews(last:100){totalCount nodes{author{__typename login} authorAssociation state body createdAt commit{oid}}}
 comments(last:100){nodes{author{__typename login} body createdAt}} commits(last:1){nodes{commit{statusCheckRollup{state}}}}"""
MERGE_SEARCH_Q = ("query($q:String!,$after:String){search(query:$q,type:ISSUE,first:50,after:$after)"
                  "{pageInfo{hasNextPage endCursor} nodes{... on PullRequest{" + MERGE_FIELDS + "}}}}")
MERGED_Q = """query($ids:[ID!]!){nodes(ids:$ids){... on PullRequest{id state mergeCommit{oid statusCheckRollup{state
 contexts(first:50){nodes{... on CheckRun{name conclusion detailsUrl} ... on StatusContext{context state targetUrl}}}}}}}}"""
ALLOWED = {"squash": "squashMergeAllowed", "merge": "mergeCommitAllowed", "rebase": "rebaseMergeAllowed"}


def read_jsonl(path):
    """Rows of a JSONL file another process may be appending to: a missing file or a torn line is skipped."""
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


def owned_elsewhere(cfg, now):
    """Branches checked out in a live Orca worktree (a worker, a lead's card), and PRs an orchestrate program
    active within program_days has recorded. Someone else is driving those; this round does not merge them.

    A program's PR reaches its ledger only at verdict or land, so the worktree branch is the net that holds
    before review. Raises when Orca cannot be read or its list is truncated."""
    out = subprocess.run([ORCA, "worktree", "list", "--limit", "10000", "--json"], capture_output=True, text=True, timeout=60)
    if out.returncode:
        raise RuntimeError(f"orca worktree list: {out.stderr.strip()[:200]}")
    res = json.loads(out.stdout)["result"]
    if res.get("truncated"):
        raise RuntimeError("orca worktree list is truncated")
    branches = {w["branch"].removeprefix("refs/heads/") for w in res["worktrees"] if w.get("branch") and not w.get("isArchived")}
    prs = set()
    for prog in pathlib.Path(cfg["programs_home"]).expanduser().glob("*/program.json"):
        repo = read_json(prog, {}).get("repo")
        events = read_jsonl(prog.parent / "ledger.jsonl")
        last = max((dt.datetime.fromisoformat(e["ts"]).timestamp() for e in events if e.get("ts")), default=0)
        if repo and now - last < cfg["program_days"] * 86400:
            prs |= {f"{repo}#{e['pr']}" for e in events if e.get("pr")}
    return {"branches": branches, "prs": prs}


TRUSTED = {"OWNER", "MEMBER", "COLLABORATOR"}


def review_record(pr, me, cfg):
    """A passing review of this head when none is required: an approval on this commit from a collaborator other than
    the owner, or, when the latest review result naming the full head sha (an independent review's sticky comment,
    from the owner or one of review_bots) ends on a pass verdict. A later fail overrides an earlier pass."""
    head = pr["headRefOid"]
    verdict = re.compile(cfg["review_verdict_pattern"], re.I | re.M)
    bots = {bot_name(b) for b in cfg["review_bots"]}

    def login(n):
        return (n.get("author") or {}).get("login")
    if any(r["state"] == "APPROVED" and login(r) != me and r.get("authorAssociation") in TRUSTED
           and (r.get("commit") or {}).get("oid") == head for r in pr["reviews"]["nodes"]):
        return True
    results = sorted((n.get("createdAt") or "", found[-1].lower())
                     for n in pr["reviews"]["nodes"] + pr["comments"]["nodes"]
                     if (login(n) == me or bot_name(login(n)) in bots) and head in (n.get("body") or "")
                     and (found := verdict.findall(n.get("body") or "")))
    return bool(results) and results[-1][1] == "pass"


def merge_readiness(pr, cfg, owned, me):
    """(merge method, None) when the PR may merge now, else (None, why). Checked in this order so the reason
    names the first gate that holds it."""
    repo = pr["repository"]
    name, head = repo["nameWithOwner"], pr["headRefOid"]
    if name in cfg["merge_deny_repos"]:
        return None, "머지 금지 저장소"
    if name not in cfg["merge_repos"]:
        return None, "머지 허용 목록 밖"
    if pr["isDraft"]:
        return None, "draft"
    held = {lb["name"].lower() for lb in pr["labels"]["nodes"]} & {h.lower() for h in cfg["hold_labels"]}
    if held:
        return None, f"보류 라벨: {', '.join(sorted(held))}"
    if owned is None:
        return None, "프로그램 소유 여부 확인 불가"
    if pr["headRefName"] in owned["branches"] or f"{name}#{pr['number']}" in owned["prs"]:
        return None, "다른 worktree·프로그램이 진행 중"
    if pr["baseRefName"] != (repo.get("defaultBranchRef") or {}).get("name"):
        return None, f"stack 하위 PR (base {pr['baseRefName']})"
    if pr["reviews"]["totalCount"] > len(pr["reviews"]["nodes"]):
        return None, "리뷰가 100건을 넘어 확인 불가"
    latest = {}
    for r in pr["reviews"]["nodes"]:
        if r["state"] != "COMMENTED":
            latest[(r.get("author") or {}).get("login")] = r["state"]
    if "CHANGES_REQUESTED" in latest.values():
        return None, "변경 요청이 남아 있음"
    if "DISMISSED" in latest.values():
        return None, "승인이 해제됨: review_wait 로"
    if pr["mergeStateStatus"] != "CLEAN":
        return None, f"mergeStateStatus {pr['mergeStateStatus']}"
    if pr.get("reviewDecision") not in ("APPROVED", None, ""):
        return None, f"리뷰 {pr['reviewDecision']}"
    if pr.get("reviewDecision") != "APPROVED" and not review_record(pr, me, cfg):
        return None, "head 리뷰 기록 없음: review_wait 로"
    rollup = (pr["commits"]["nodes"] or [{}])[0].get("commit", {}).get("statusCheckRollup")
    if not rollup or rollup["state"] != "SUCCESS":
        return None, f"체크 {rollup['state'] if rollup else '없음'}"
    threads = pr["reviewThreads"]
    if threads["totalCount"] > len(threads["nodes"]) or any(not t["isResolved"] for t in threads["nodes"]):
        return None, "미해결 스레드"
    for bot in cfg["required_bots"]:
        # A bot that was rate-limited or skipped leaves a comment, not a review on this head.
        if not any(bot_name((r.get("author") or {}).get("login")) == bot_name(bot) and r["state"] in ("APPROVED", "COMMENTED")
                   and (r.get("commit") or {}).get("oid") == head for r in pr["reviews"]["nodes"]):
            return None, f"필수 봇 {bot} 이 head 를 리뷰하지 않음"
    method = next((m for m in cfg["merge_methods"] if repo.get(ALLOWED.get(m, ""))), None)
    return (method, None) if method else (None, "허용된 머지 방식 없음")


def merge_cmd(pr, method):
    return shlex.join(["gh", "pr", "merge", str(pr["number"]), "-R", pr["repository"]["nameWithOwner"], f"--{method}",
                       "--match-head-commit", pr["headRefOid"]])


def collect_ready_to_merge(gh, me, cfg, ledger, now):
    """The owner's open PRs in merge_repos that may merge now: approved, or reviewed at this head where no review is
    required; CLEAN, checks green, driven by no one else."""
    if not cfg["merge_repos"]:
        return [], [{"pr": "*", "kind": "ready_to_merge", "why": "merge_repos 비어 있음: 머지하지 않음"}]
    q = "is:pr is:open archived:false author:@me " + " ".join(f"repo:{r}" for r in cfg["merge_repos"])
    found = gh.search_merge(q, me)
    if not found:
        return [], []
    try:
        owned = owned_elsewhere(cfg, now)
    except Exception:  # any failure to read ownership holds every PR back
        owned = None
    items, excluded = [], []
    for pr in found:
        method, why = merge_readiness(pr, cfg, owned, me)
        repo = pr["repository"]["nameWithOwner"]
        if why:
            excluded.append({"pr": f"{repo}#{pr['number']}", "kind": "ready_to_merge", "why": why})
            continue
        review = "승인됨" if pr.get("reviewDecision") == "APPROVED" else "리뷰 불요, head 리뷰 기록 있음"
        items.append({"kind": "ready_to_merge", "key": key(pr), "id": pr["id"], "repo": repo, "number": pr["number"],
                      "title": pr["title"], "url": pr["url"], "age_days": days(pr["createdAt"], now),
                      "size": pr["additions"] + pr["deletions"], "method": method, "cmd": merge_cmd(pr, method),
                      "why": [f"CLEAN, 체크 green, {review}, {method}"]})
    return items, excluded


COLLECTORS = {"review_request": collect_review_requests, "ready_to_merge": collect_ready_to_merge}


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
    return {"run": now, "viewer": me, "new": new[: cfg["max_items"]], "more": max(0, len(new) - cfg["max_items"]),
            "known": [i["key"] for i in items if i["key"] in ledger], "excluded": excluded}


def body(plan):
    if not plan["new"]:
        return ""
    head = f"PR {len(plan['new'])}건이 나를 기다린다" + (f" (그 밖에 {plan['more']}건은 다음 회차)" if plan["more"] else "")
    lines = [head]
    for n, i in enumerate(plan["new"], 1):
        if i.get("cmd"):
            lines.append(f"{n}. [{LABELS[i['kind']]}] {i['repo']}#{i['number']} {i['title']} — "
                         f"{i.get('result') or i['method'] + ' 로 머지 예정'} — {i['url']}")
            continue
        tag = "새 커밋, 재요청 없음" if i["reason"] == "new_commits" else LABELS[i["kind"]]
        lines.append(f"{n}. [{tag}] {i['repo']}#{i['number']} {i['title']} — @{i['author']} · {i['age_days']}일 · "
                     f"±{i['size']} · {', '.join(i['why'])} — {i['url']}")
    return "\n".join(lines)


NO_CI_GRACE = 120


def merge_outcome(node, waited=0):
    """(result line, CI settled) for a PR the round tried to merge: merged or not, and the merge commit's CI.
    A merge commit with no checks after NO_CI_GRACE seconds is taken as a branch without CI."""
    if not node or node.get("state") != "MERGED":
        return f"머지 안 됨 (state {(node or {}).get('state', '?')}): 거부·실패는 이 head 에서 다시 시도하지 않음", True
    commit = node["mergeCommit"] or {}
    rollup = commit.get("statusCheckRollup") or {}
    sha = (commit.get("oid") or "")[:7]
    if rollup.get("state") == "SUCCESS":
        return f"머지됨 {sha}, 기본 브랜치 CI green", True
    if rollup.get("state") in ("FAILURE", "ERROR"):
        bad = [c.get("detailsUrl") or c.get("targetUrl") for c in rollup["contexts"]["nodes"]
               if c.get("conclusion") in ("FAILURE", "TIMED_OUT", "CANCELLED") or c.get("state") in ("FAILURE", "ERROR")]
        return f"머지됨 {sha}, 기본 브랜치 CI red: {', '.join(u for u in bad if u) or '실패 체크'}", True
    if not rollup and waited >= NO_CI_GRACE:
        return f"머지됨 {sha}, 기본 브랜치 CI 없음 ({NO_CI_GRACE}초 동안 체크가 붙지 않음)", True
    return f"머지됨 {sha}, 기본 브랜치 CI {rollup.get('state') or '아직 시작 안 됨'}", False


def verify(gh, state, cfg, clock=time.time, sleep=time.sleep):
    """After the round ran the merge commands: record each command item's outcome in plan.json and rewrite body.txt.
    Waits up to ci_wait_minutes for the merge commits' CI to settle."""
    plan = read_json(state / "plan.json", None)
    if not plan:
        raise SystemExit("verify: no plan.json; run plan --body first")
    acted = {i["id"]: i for i in plan["new"] if i.get("cmd")}
    start = clock()
    deadline = start + cfg["ci_wait_minutes"] * 60
    outcomes = {}
    while acted:
        by_id = {n["id"]: n for n in gh.merged(list(acted), plan.get("viewer"))}
        waited = clock() - start
        outcomes = {k: merge_outcome(by_id.get(k), waited) for k in acted}
        if all(done for _, done in outcomes.values()) or clock() >= deadline:
            break
        sleep(30)
    for k, i in acted.items():
        i["result"] = outcomes[k][0]
    write_json(state / "plan.json", plan)
    text = body(plan)
    (state / "body.txt").write_text(text)
    return text


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
    for i in plan["new"]:
        ledger[i["key"]] = {"at": plan["run"], "kind": i["kind"], "id": i["id"]}
    # A request left open for months is still known; only keys that stopped appearing age out.
    for k in plan.get("known", []):
        if k in ledger:
            ledger[k]["at"] = plan["run"]
    horizon = now - cfg["keep_days"] * 86400
    ledger = {k: v for k, v in ledger.items() if v["at"] >= horizon}
    write_json(state / "ledger.json", ledger)
    (state / "plan.json").unlink()
    return len(plan["new"])


def schedule_cmd(script, cfg, workspace, provider="claude"):
    env = " ".join(["env", f"AGENT_SKILLS_STATE={shlex.quote(str(STATE.parent))}", "python3", shlex.quote(str(script))])
    to = cfg.get("inbox_to") or "<prs.inbox_to>"
    body_file = STATE / "body.txt"
    merging = "ready_to_merge" in cfg["kinds"]
    merge_step = (f"`{env} merges` 가 출력하는 머지 대상마다, 주석 줄(PR, CLEAN, 체크, 리뷰, 머지 방식)을 채팅에 그대로 적은 뒤 "
                  f"그 아래 `gh pr merge` 명령을 단독 명령으로 한 번씩 실행한다(--admin·--auto 를 붙이지 않는다). 거부되거나 실패한 "
                  f"머지는 다시 시도하지 않고 다른 형태로 바꾸지도 않는다. 그다음 `{env} verify` 로 MERGED 와 기본 브랜치 CI 를 확인해 "
                  f"본문을 다시 쓴다. verify 가 실패하면 보내지도 commit 하지도 않고 그 오류를 남긴 채 끝낸다. 성공하면 ") if merging else ""
    prompt = (f"watch-prs 회차다. `{env} plan --body` 를 한 번 실행한다. 출력이 비어 있으면 아무것도 보내지 않고 끝낸다. "
              f"아니면 {merge_step}`orca orchestration send --to {shlex.quote(to)} --type status --subject \"watch-prs: <N>건\" "
              f"--body \"$(cat {shlex.quote(str(body_file))})\" --json` 로 한 통만 보내고(N 은 본문 첫 줄의 건수), 보내기가 성공했을 때만 "
              f"`{env} commit` 을 실행한다. "
              + ("머지 말고는 GitHub 에 쓰지 않는다: 리뷰·승인·코멘트·재요청을 하지 않는다." if merging else
                 "이 회차는 읽기만 한다: 리뷰·코멘트·재요청 같은 GitHub 쓰기는 하지 않는다."))
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
    if cmd in ("plan", "merges", "verify", "commit", "schedule") and cfg is None:
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
    elif cmd == "merges":
        # Recorded before the round runs them: a refused or failed merge is never retried at that head, even
        # when verify or commit never happens.
        acts = [i for i in read_json(STATE / "plan.json", {}).get("new", []) if i.get("cmd")]
        ledger = read_json(STATE / "ledger.json", {})
        ledger.update({i["key"]: {"at": now, "kind": i["kind"], "id": i["id"]} for i in acts})
        write_json(STATE / "ledger.json", ledger)
        for i in acts:
            print(f"# {i['repo']}#{i['number']} {i['why'][0]}\n{i['cmd']}")
    elif cmd == "verify":
        print(verify(GitHub(), STATE, cfg))
    elif cmd == "commit":
        print(f"commit: {commit(STATE, cfg, now)} items recorded")
    elif cmd == "schedule":
        script = pathlib.Path(__file__).resolve()
        line = schedule_cmd(script, cfg, opt("--workspace") or f"path:{script.parents[3]}", opt("--provider", "claude"))
        print(shlex.join(line))
        if "--write" in sys.argv:
            sys.exit(subprocess.run(line).returncode)
        print("\n(출력만 했다. 실제로 만들려면 --write)")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
