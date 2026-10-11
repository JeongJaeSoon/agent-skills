#!/usr/bin/env python3
"""Bookkeeping for watch-prs: which pull requests need the owner, ranked, sent once per head.

Usage:
  python3 prs.py precheck [--config PATH]
  python3 prs.py plan [--body] [--config PATH]
  python3 prs.py round
  python3 prs.py result KEY ok|failed [NOTE]
  python3 prs.py merges [--config PATH]
  python3 prs.py verify [--config PATH]
  python3 prs.py actions [--config PATH]
  python3 prs.py log --key KEY --source SOURCE [--thread CHANNEL:TS] [--scheduled ID] [--post-at EPOCH] [--at EPOCH]
  python3 prs.py commit [--config PATH]
  python3 prs.py dispatch [--config PATH]
  python3 prs.py schedule [--workspace SELECTOR] [--provider claude] [--edit AUTOMATION_ID] [--write]

precheck asks GitHub (through `gh`) for the items and exits 0 when some are not in the ledger yet,
1 when nothing is new or a run still holds plan.json (younger than lock_minutes), and 2 when it
could not ask (no config, `gh` failed). It uses no model and writes nothing.
plan prints the same items as JSON and writes nothing. plan --body is the run's step: it prints the
coordinator message (empty when nothing is new), keeps it in body.txt, and, when something is new,
writes the items to plan.json, which is also the run lock, and puts each ownergate step the owner has
to run on the dashboard inbox (`orch-dash inbox add`, keyed by the ledger key). round prints the
steps an allow rule lets the round run (key, tab, command) from plan.json; result records how one
went in body.txt, and a failed one goes to the owner. commit records what plan.json delivered,
so the same (repo, number, headRefOid) is never sent twice. Only kinds with a PROMPT_STEPS entry become new
(and so reach plan.json); items of other kinds are listed as uncovered and never recorded.
actions prints the Slack and bot actions plan.json holds; log records one of them in the send-log
(sendlog.jsonl), which is what decides them, not the ledger.
merges prints the `gh pr merge` command for each ready_to_merge item in plan.json; the round runs
them itself. verify then checks each is MERGED, waits for the merge commit's CI and rewrites body.txt.
dispatch reads plan.json and prints one
`orca orchestration worker-start` line per repo with due unreleased items, its brief kept in brief-<run>-<n>.txt;
it runs nothing, and the round only forwards the lines: worker-start works only from the coordinator terminal bound
to the Run. schedule prints the
`orca automations create` command (`edit <id>` with --edit), its steps built from the configured kinds, and
runs it only with --write.
Config: ~/.claude/agent-skills.json → "prs" (see SKILL.md). State: <--state or $AGENT_SKILLS_STATE>/prs
(default ~/.local/state/agent-skills/prs). The ledger holds keys, node ids and timestamps, never PR text.
Slack review requests come from watch-mentions' seen ledger (<state>/mentions/seen.json, rows of
category review with a `pr`); a missing ledger means GitHub only.
"""
import datetime as dt, json, os, pathlib, re, shlex, subprocess, sys, time, zoneinfo


def opt(name, default=None):
    a = sys.argv
    i = a.index(name) + 1 if name in a else 0
    return a[i] if 0 < i < len(a) else default


CONFIG = pathlib.Path("~/.claude/agent-skills.json").expanduser()
STATE_HOME = pathlib.Path("~/.local/state/agent-skills").expanduser()
STATE = pathlib.Path(opt("--state") or os.environ.get("AGENT_SKILLS_STATE") or STATE_HOME).expanduser() / "prs"
GH = os.environ.get("AGENT_SKILLS_GH", "gh")
CLAUDE = os.environ.get("AGENT_SKILLS_CLAUDE", "claude")
ORCH_DASH = os.environ.get("AGENT_SKILLS_ORCH_DASH") or str(pathlib.Path(__file__).resolve().parents[3] / "bin" / "orch-dash")
ORCA = os.environ.get("AGENT_SKILLS_ORCA", "orca")
# Kinds without a collector are reserved so later flows extend COLLECTORS, not the CLI. Order is precedence:
# when two collectors return the same PR head, the later kind wins.
KINDS = ("review_request", "review_wait", "ready_to_merge", "unreleased", "ownergate")
LABELS = {"review_request": "리뷰 요청", "review_wait": "내 PR 대기", "ready_to_merge": "머지", "unreleased": "dev 확인 전",
          "ownergate": "소유자 승인"}
DEFAULTS = {"kinds": ["review_request"], "orgs": [], "repos": [], "exclude_orgs": [], "exclude_repos": [],
            "ignore_bots": [], "self_bots": [], "ownergate": [], "inbox_to": None, "max_items": 10, "stale_days": 30,
            "keep_days": 60, "lock_minutes": 60, "cron": "*/15 * * * *", "timezone": None, "mentions_seen": None,
            "deploy": {}, "dispatch_run": None, "worker_agent": "claude", "release_approval": None,
            "channel": None, "repo_channels": {}, "reviewers": {}, "review_bots": {}, "holidays": [],
            "urgent_labels": [], "batch_min": 2, "batch_wait_hours": 2, "night_hour": 21,
            "morning_hour": 9, "programs_dir": None,
            "merge_repos": [], "merge_deny_repos": [], "merge_methods": ["squash", "merge", "rebase"],
            "hold_labels": ["hold", "do-not-merge", "wip"], "required_bots": [],
            "review_verdict_pattern": r"^\**(?:결론|verdict)(?:\**\s*[:：]|\s*[:：]\s*\**)\s*\**(pass|fail)\**\s*$",
            "ci_wait_minutes": 15}
REFERENCE = pathlib.Path(__file__).resolve().parents[2] / "deliver-ticket/references/post-merge-release.md"
EXIT_NEW, EXIT_NOTHING, EXIT_ERROR = 0, 1, 2
SOURCES = ("sent", "scheduled", "found", "refused", "no_thread", "skipped")

PR_FIELDS = """id number title url isDraft createdAt updatedAt mergedAt mergeCommit{oid} headRefOid additions deletions reviewDecision state
 changedFiles files(first:100){nodes{path}} repository{nameWithOwner} author{__typename login}
 reviewRequests(first:50){nodes{asCodeOwner requestedReviewer{__typename ... on User{login} ... on Team{slug}}}}
 reviews(author:$me,last:1,states:[APPROVED,CHANGES_REQUESTED,COMMENTED,DISMISSED]){nodes{commit{oid}}}"""
SEARCH_Q = ("query($q:String!,$me:String!,$after:String){search(query:$q,type:ISSUE,first:50,after:$after)"
            "{pageInfo{hasNextPage endCursor} nodes{... on PullRequest{" + PR_FIELDS + "}}}}")
NODES_Q = "query($ids:[ID!]!,$me:String!){nodes(ids:$ids){... on PullRequest{" + PR_FIELDS + "}}}"
PR_Q = ("query($owner:String!,$name:String!,$number:Int!,$me:String!)"
        "{repository(owner:$owner,name:$name){pullRequest(number:$number){" + PR_FIELDS + "}}}")
PR_REF = re.compile(r"^([\w.-]+)/([\w.-]+)#(\d+)$")
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

    def search_merge(self, q, me):
        return self._paged(MERGE_SEARCH_Q, q=q, me=me)

    def mine(self, self_bots):
        """The owner's open PRs, plus a self bot's PRs assigned to the owner: a bot app is often shared org-wide."""
        qs = ["author:@me"] + [f"author:app/{bot_name(b)} assignee:@me" for b in self_bots]
        return list({n["id"]: n for q in qs for n in self._paged(MINE_Q, q=f"is:pr is:open archived:false {q}")}.values())

    def nodes(self, ids, me, query=NODES_Q):
        return [n for i in range(0, len(ids), 50) for n in gh_graphql(query, partial=True, ids=ids[i:i + 50], me=me)["nodes"] if n]

    def merge_state(self, ids, me):
        return self.nodes(ids, me, MERGED_Q)

    def pr(self, ref, me):
        owner, name, number = PR_REF.match(ref).groups()
        repo = gh_graphql(PR_Q, partial=True, owner=owner, name=name, number=int(number), me=me).get("repository")
        return (repo or {}).get("pullRequest")

    def revision(self, cmd):
        """The commit the dev environment runs, from the repo's declared revision_cmd."""
        out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        sha = out.stdout.strip()
        if out.returncode or not sha:
            raise RuntimeError(f"revision_cmd exit {out.returncode}: {out.stderr.strip()[:200]}")
        return sha

    def contains(self, repo, commit, deployed):
        # compare/{commit}...{deployed}: "ahead" or "identical" means deployed already has commit.
        out = subprocess.run([GH, "api", f"repos/{repo}/compare/{commit}...{deployed}", "--jq", ".status"],
                             capture_output=True, text=True, timeout=60)
        if out.returncode:
            raise RuntimeError(f"gh compare: {out.stderr.strip()[:200]}")
        return out.stdout.strip() in ("ahead", "identical")


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
            "reason": reason, "why": why, "source": "slack" if reason == "slack" else "github", "slack": []}


def rank(items):
    """Stale last; then the owner as the only blocking reviewer, a human author, older first, smaller first."""
    return sorted(items, key=lambda i: (i.get("stale", False), not i.get("only_blocker"), not i.get("human", True),
                                        -i["age_days"], i["size"]))


def slack_reviews(cfg):
    """owner/repo#N (lowercased: GitHub names are case-insensitive) → permalinks of the Slack review requests."""
    rows = read_json(cfg.get("mentions_seen") or STATE.parent / "mentions" / "seen.json", {})
    refs = {}
    for row in rows.values() if isinstance(rows, dict) else []:
        if isinstance(row, dict) and row.get("category") == "review" and PR_REF.match(str(row.get("pr") or "")):
            links = refs.setdefault(row["pr"].lower(), [])
            if isinstance(row.get("permalink"), str) and row["permalink"] not in links:
                links.append(row["permalink"])
    return refs


def slack_skip(pr, me, cfg):
    """A Slack request names the owner directly, so only the PR's own state can rule it out."""
    if pr["state"] != "OPEN":
        return "닫힘"
    if pr["isDraft"]:
        return "draft"
    if (pr.get("author") or {}).get("login") == me:
        return "본인 PR"
    mine = pr["reviews"]["nodes"]
    if mine and (mine[0].get("commit") or {}).get("oid") == pr["headRefOid"]:
        return "head 를 이미 리뷰함"
    return out_of_scope(pr, cfg)


def collect_review_requests(gh, me, cfg, ledger, now):
    """Personal review requests, ledger PRs the owner reviewed that got new commits without a re-request,
    and Slack review requests: one item per PR, the Slack permalinks attached."""
    items, excluded, seen, found = [], [], set(), {}
    for pr in gh.search("is:pr is:open archived:false review-requested:@me", me):
        seen.add(pr["id"])
        name = f"{pr['repository']['nameWithOwner']}#{pr['number']}"
        found[name.lower()] = pr
        why = (out_of_scope(pr, cfg) or (None if personal_request(pr, me) else "팀·CODEOWNERS 요청뿐")
               or ("draft" if pr["isDraft"] else None))
        if why:
            excluded.append({"pr": name, "why": why})
        else:
            items.append(item(pr, me, cfg, now, "requested"))
    watch = sorted({v["id"] for v in ledger.values() if v.get("kind") == "review_request" and v.get("id")} - seen)
    for pr in gh.nodes(watch, me) if watch else []:
        found.setdefault(f"{pr['repository']['nameWithOwner']}#{pr['number']}".lower(), pr)
        mine = pr["reviews"]["nodes"]
        if (pr["state"] == "OPEN" and not pr["isDraft"] and not out_of_scope(pr, cfg)
                and mine and (mine[0].get("commit") or {}).get("oid") != pr["headRefOid"]):
            items.append(item(pr, me, cfg, now, "new_commits"))
    # Merge on the PR node id: a Slack ref in another case, or naming a renamed repo, is still the same PR.
    listed = {i["id"]: i for i in items}
    for ref, links in sorted(slack_reviews(cfg).items()):
        pr = found.get(ref) or gh.pr(ref, me)
        if not pr:
            excluded.append({"pr": ref, "why": "Slack 요청, PR 을 읽지 못함"})
            continue
        name = f"{pr['repository']['nameWithOwner']}#{pr['number']}"
        excluded = [e for e in excluded if e["pr"] != name]
        if pr["id"] in listed:
            hit = listed[pr["id"]]
            hit["slack"] += [u for u in links if u not in hit["slack"]]
            hit["source"] = "slack" if hit["source"] == "slack" else "github+slack"
        elif why := slack_skip(pr, me, cfg):
            excluded.append({"pr": name, "why": f"Slack 요청, {why}"})
        else:
            listed[pr["id"]] = {**item(pr, me, cfg, now, "slack"), "slack": list(links)}
            items.append(listed[pr["id"]])
    return items, excluded


# GitHub accepts an approval on an older commit and keeps it through later pushes, so the step first
# checks that the head is still the one this message describes.
APPROVE = ('[ "$(gh pr view {number} -R {repo} --json headRefOid -q .headRefOid)" = {head} ] && '
           'gh api -X POST repos/{repo}/pulls/{number}/reviews -f event=APPROVE -f commit_id={head} '
           '|| {{ echo "not approved: head moved or the call failed"; false; }}')
ALLOW_RULE_MIN = 20  # a shorter phrase could open unrelated allow rules


def automode_allow():
    """The allow rules auto mode applies now. Read only; a failed read covers nothing."""
    try:
        out = subprocess.run([CLAUDE, "auto-mode", "config"], capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout).get("allow", []) if out.returncode == 0 else []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def deploys_prod(pr, gate):
    """True, False, or None when unknown. Unknown is treated as production."""
    if gate.get("prod") is not None and not isinstance(gate["prod"], bool):
        raise ValueError(f"ownergate {gate.get('name')}: prod must be true, false or absent")
    paths = [n["path"] for n in (pr.get("files") or {}).get("nodes", [])]
    prefixes = gate.get("prod_paths") or []
    if gate.get("prod") is True or any(p.startswith(x) for p in paths for x in prefixes):
        return True
    if prefixes:
        return False if (pr.get("changedFiles") or 0) <= len(paths) else None
    return False if gate.get("prod") is False else None


def diff_summary(pr):
    paths = [n["path"] for n in (pr.get("files") or {}).get("nodes", [])]
    n = pr.get("changedFiles") or len(paths)
    more = f" 외 {n - 3}개" if n > 3 else ""
    return f"파일 {n}개 ±{pr['additions'] + pr['deletions']}: {', '.join(paths[:3])}{more}"


def rule_draft(gate, prod):
    if prod is not False:
        return f"{gate['name']}: 머지가 프로덕션 배포(또는 모름)라 회차가 할 규칙은 만들지 않는다. 소유자 몫으로 남는다."
    authors = ", ".join(gate.get("authors", []))
    repos = ", ".join(gate.get("repos", [])) or "any repository in scope"
    return (f"allow: Scheduled watch-prs round approving {gate['name']} PRs: in a turn started by the watch-prs automation, "
            f"the agent may run the approval line `prs.py round` prints for that PR "
            f"(`gh api -X POST repos/<repo>/pulls/<n>/reviews -f event=APPROVE -f commit_id=<head sha>` behind a head "
            f"check) once, as a standalone command, on an open PR in {repos} whose author is the app {authors} and whose title matches "
            f"`{gate.get('title') or '.*'}`, at the head the round's plan listed, when merging it does not deploy to "
            f"production. Not covered: a PR by a person, another author or repository, merging, a production deploy.")


def gate_match(gate, pr):
    author = pr.get("author") or {}
    return (bot_name(author.get("login")) in {bot_name(a) for a in gate.get("authors", [])}
            and (not gate.get("repos") or pr["repository"]["nameWithOwner"] in gate["repos"])
            and re.search(gate.get("title") or "", pr["title"]))


def collect_ownergate(gh, me, cfg, ledger, now):
    """App PRs a configured gate says wait on the owner's approval; each is marked for the round or for the owner."""
    gates = [{"name": (g.get("authors") or ["gate"])[0], **g} for g in cfg["ownergate"]]
    for g in gates:  # a config error shows on every run, not only when some PR matches
        re.compile(g.get("title") or "")
        deploys_prod({}, g)
    found = {}
    for login in dict.fromkeys(a for g in gates for a in g.get("authors", [])):
        for pr in gh.search(f"is:pr is:open archived:false draft:false review-requested:@me author:app/{bot_name(login)}", me):
            found[pr["id"]] = pr
    items, excluded, allow = [], [], None
    scope = {**cfg, "self_bots": [], "ignore_bots": []}  # a bot ignored for reviews can still need the owner's approval
    for pr in found.values():
        matched = [g for g in gates if gate_match(g, pr)]
        why = (out_of_scope(pr, scope)
               or (None if (pr.get("author") or {}).get("__typename") == "Bot" else "사람 작성")
               or (None if matched else "게이트 조건 밖")
               or (None if pr.get("reviewDecision") == "REVIEW_REQUIRED" else "승인 대기 아님"))
        if why:
            excluded.append({"pr": f"{pr['repository']['nameWithOwner']}#{pr['number']}", "why": why})
            continue
        prods = [deploys_prod(pr, g) for g in matched]
        prod = True if True in prods else None if None in prods else False
        phrases = [g.get("allow_rule") or "" for g in matched]
        # A step the round already tried (plan --body marks it) is never the round's again: the owner takes it.
        eligible = prod is False and f"attempt:{key(pr)}" not in ledger and all(len(x) >= ALLOW_RULE_MIN for x in phrases)
        if eligible and allow is None:
            allow = automode_allow()
        covered = eligible and all(any(r.startswith(x) for r in allow) for x in phrases)
        it = item(pr, me, cfg, now, "gate")
        it.update(kind="ownergate", gate=matched[0]["name"], prod=prod, diff=diff_summary(pr),
                  command=APPROVE.format(repo=pr["repository"]["nameWithOwner"], number=pr["number"], head=pr["headRefOid"]),
                  run_by="round" if covered else "owner", rule_draft=None if covered else rule_draft(matched[0], prod))
        items.append(it)
    return items, excluded


MERGE_FIELDS = """id number title url isDraft createdAt headRefOid headRefName baseRefName additions deletions
 reviewDecision mergeStateStatus state author{__typename login} labels(first:100){nodes{name}}
 repository{nameWithOwner defaultBranchRef{name} squashMergeAllowed mergeCommitAllowed rebaseMergeAllowed}
 reviewThreads(first:100){totalCount nodes{isResolved}} reviews(last:100){totalCount nodes{author{__typename login} authorAssociation state body commit{oid}}}
 comments(last:100){totalCount nodes{author{__typename login} body}} commits(last:1){nodes{commit{statusCheckRollup{state}}}}"""
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
    """Branches checked out in a live Orca worktree (a worker, a lead's card), and PRs a live orchestrate program
    has recorded (program_prs). Someone else is driving those; this round does not merge them.

    A program's PR reaches its ledger only at verdict or land, so the worktree branch is the net that holds
    before review. Raises when Orca cannot be read or its list is truncated."""
    out = subprocess.run([ORCA, "worktree", "list", "--limit", "10000", "--json"], capture_output=True, text=True, timeout=60)
    if out.returncode:
        raise RuntimeError(f"orca worktree list: {out.stderr.strip()[:200]}")
    res = json.loads(out.stdout)["result"]
    if res.get("truncated"):
        raise RuntimeError("orca worktree list is truncated")
    branches = {w["branch"].removeprefix("refs/heads/") for w in res["worktrees"] if w.get("branch") and not w.get("isArchived")}
    return {"branches": branches, "prs": {f"{repo}#{n}" for repo, n in program_prs(cfg, now, stopped=True)}}


TRUSTED = {"OWNER", "MEMBER", "COLLABORATOR"}


def review_record(pr, me, cfg):
    """A passing review of this head when none is required. A review result is a comment or review body from the
    owner or a review_bots bot that names the full head sha and has a verdict line (an independent review's sticky
    comment); its last verdict line counts. Any fail result at this head holds the PR, whatever else passed. Without
    one, a pass result or an approval on this commit from a collaborator other than the owner is the record."""
    head = pr["headRefOid"]
    verdict = re.compile(cfg["review_verdict_pattern"], re.I | re.M)
    bots = {bot_name(b) for b in cfg["review_bots"]}

    def reviewer(n):
        author = n.get("author") or {}
        return author.get("login") == me or (author.get("__typename") == "Bot" and bot_name(author.get("login")) in bots)
    results = {found[-1].lower() for n in pr["reviews"]["nodes"] + pr["comments"]["nodes"]
               if reviewer(n) and head in (n.get("body") or "") and (found := verdict.findall(n.get("body") or ""))}
    if "fail" in results:
        return False
    return "pass" in results or any(
        r["state"] == "APPROVED" and (r.get("author") or {}).get("login") != me
        and r.get("authorAssociation") in TRUSTED and (r.get("commit") or {}).get("oid") == head
        for r in pr["reviews"]["nodes"])


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
    if any(pr[c]["totalCount"] > len(pr[c]["nodes"]) for c in ("reviews", "comments")):
        return None, "리뷰·코멘트가 100건을 넘어 확인 불가"
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


def collect_unreleased(gh, me, cfg, ledger, now):
    """The owner's PRs merged within lookback_days in a repo with a deploy declaration, not yet handed to a release worker.

    Membership does not depend on the dev revision: a repo that auto-syncs dev is usually synced before a
    15-minute round looks, and the merge still needs its behaviour and logs checked. The revision decides the
    worker's action instead: deployed false means release (or wait for the sync) first.
    """
    items, excluded = [], []
    for repo, decl in sorted(cfg["deploy"].items()):
        since = dt.datetime.fromtimestamp(now - decl.get("lookback_days", 2) * 86400, dt.timezone.utc)
        due = [pr for pr in gh.search(f"is:pr is:merged author:{me} repo:{repo} merged:>={since:%Y-%m-%dT%H:%M:%SZ}", me)
               if not delivered(ledger, key(pr), "unreleased") and pr.get("mergeCommit")]
        if not due:
            continue
        try:
            if not decl.get("revision_cmd"):
                raise RuntimeError("revision_cmd 없음")
            deployed = gh.revision(decl["revision_cmd"])
            got = [(pr, gh.contains(repo, pr["mergeCommit"]["oid"], deployed)) for pr in due]
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            # Still an item: dropping it would let the merge age out of lookback_days unseen. The worker checks dev itself.
            deployed, failed = None, f"dev 리비전 확인 실패: {str(e)[:200]}"
            got = [(pr, None) for pr in due]
        else:
            failed = None
        for pr, live in got:
            merged = days(pr["mergedAt"], now)
            items.append({"kind": "unreleased", "key": key(pr), "id": pr["id"], "repo": repo, "number": pr["number"],
                          "title": pr["title"], "url": pr["url"], "author": me, "human": True, "age_days": merged,
                          "idle_days": merged, "size": pr["additions"] + pr["deletions"], "only_blocker": False,
                          "stale": False, "reason": "merged", "merge_commit": pr["mergeCommit"]["oid"],
                          "deployed_revision": deployed, "deployed": live,
                          "why": [failed or ("dev 에 반영됨, 동작 확인 전" if live else "dev 에 아직 없음")]})
    return items, excluded


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


def program_prs(cfg, now, stopped=False):
    """{(repo, number): slug} for PRs a live orchestrate program drives; `orch wait` follows those, and a parked
    one is held on purpose. Live: not stopped (`stop` after the last `resume`) and a ledger event within
    stale_days, so an abandoned program's PRs come back to reminders. stopped=True keeps a stopped program's PRs:
    a stop halts new workers, but the coordinator, or a human gate, still owns what it has."""
    root = pathlib.Path(cfg["programs_dir"] or os.environ.get("PROGRAMS_HOME") or "~/.claude/programs").expanduser()
    out = {}
    for prog in root.glob("*/program.json"):
        rows = read_jsonl(prog.parent / "ledger.jsonl")
        marks = [r.get("ev") for r in rows if r.get("ev") in ("stop", "resume")]
        last = max((ts(r["ts"]) for r in rows if isinstance(r.get("ts"), str)), default=0)
        if (marks and marks[-1] == "stop" and not stopped) or now - last > cfg["stale_days"] * 86400:
            continue
        repo = read_json(prog, {}).get("repo")
        out.update({(repo, r["pr"]): prog.parent.name for r in rows if isinstance(r.get("pr"), int)})
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


def slack_id(who, cfg):
    return (cfg["reviewers"].get(who) or {}).get("slack")


def review_bots(cfg):
    return {bot_name(b): comment for b, comment in cfg["review_bots"].items()}


def wait_action(kind, s, who, cfg, at, thread=None, comment=None):
    person = cfg["reviewers"].get(who, {})
    slack = kind != "retrigger"
    return {"action": kind, "pr": s["pr"], "head": s["head"], "reviewer": who, "url": s["url"], "title": s["title"],
            "slack_user": person.get("slack"), "thread": thread, "comment": comment, "post_at": at if slack else None,
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
    scheduled = {r["reviewer"] for r in sendlog
                 if r["action"] == "request" and r["source"] == "scheduled" and (r.get("post_at") or 0) > now}
    for s in states:
        for who in s["pending"] + s["stale_approvals"]:
            mine = by.get((s["pr"], who), [])
            if not slack_id(who, cfg) or any(r["source"] == "refused" for r in mine):
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
    unmapped = [w for w in s["pending"] + s["stale_approvals"] if not slack_id(w, cfg)]
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


COLLECTORS = {"review_request": collect_review_requests, "review_wait": collect_review_wait,
              "ready_to_merge": collect_ready_to_merge, "unreleased": collect_unreleased, "ownergate": collect_ownergate}


def delivered(ledger, k, kind):
    """Kinds share the key shape owner/repo#N@head, so a head sent under an earlier kind comes back once under a
    later one: a PR merged by ready_to_merge is still due as unreleased, and a review request that turned into an
    owner gate is sent again as one. A head already sent under a later kind is not sent again under an earlier one."""
    if k not in ledger:
        return False
    sent = ledger[k].get("kind", kind)  # a row from before kinds were recorded counts as this kind's
    return sent not in KINDS or KINDS.index(kind) <= KINDS.index(sent)  # an unknown kind (another version) is sent


def compute(gh, cfg, ledger, now):
    """Only kinds with a PROMPT_STEPS entry become new: the round has no step for the others, so they stay out of
    plan.json (which is what commit records) and take no max_items slot; their keys are listed under uncovered."""
    me = gh.viewer()
    items, excluded = [], []
    for kind in cfg["kinds"]:
        if kind not in COLLECTORS:
            raise RuntimeError(f"kind {kind!r} is not implemented yet (known: {', '.join(KINDS)})")
        got, out = COLLECTORS[kind](gh, me, cfg, ledger, now)
        items += got
        excluded += out
    best = {}
    for i in items:
        if i["key"] not in best or KINDS.index(i["kind"]) > KINDS.index(best[i["key"]]["kind"]):
            best[i["key"]] = i
    items = list(best.values())
    unsent = [i for i in rank(items) if not delivered(ledger, i["key"], i["kind"])]
    new = [i for i in unsent if i["kind"] in PROMPT_STEPS]
    # Never capped: actions (a reviewer's batch split across rounds would become two messages) and unreleased
    # merges (a repo's batch split across rounds would start two release workers; ranked last, they would starve).
    acts, new = [i for i in new if not i.get("once", True)], [i for i in new if i.get("once", True)]
    capped = [i for i in new if i["kind"] != "unreleased"]
    keep = {i["key"] for i in capped[: cfg["max_items"]]}
    return {"run": now, "viewer": me, "new": acts + [i for i in new if i["kind"] == "unreleased" or i["key"] in keep],
            "more": max(0, len(capped) - cfg["max_items"]),
            "known": [i["key"] for i in items if delivered(ledger, i["key"], i["kind"])], "excluded": excluded,
            "uncovered": [i["key"] for i in unsent if i["kind"] not in PROMPT_STEPS]}


def body(plan):
    if not plan["new"]:
        return ""
    head = f"PR {len(plan['new'])}건이 나를 기다린다" + (f" (그 밖에 {plan['more']}건은 다음 회차)" if plan["more"] else "")
    lines, drafts = [head], []
    for n, i in enumerate(plan["new"], 1):
        if i.get("cmd"):
            lines.append(f"{n}. [{LABELS[i['kind']]}] {i['repo']}#{i['number']} {i['title']} — "
                         f"{i.get('result') or i['method'] + ' 로 머지 예정'} — {i['url']}")
            continue
        tag = {"new_commits": "새 커밋, 재요청 없음", "slack": "Slack 리뷰 요청"}.get(i["reason"], LABELS[i["kind"]])
        if i["kind"] != "ownergate":
            slack = f" · Slack {' '.join(i['slack'])}" if i.get("slack") else ""
            lines.append(f"{n}. [{tag}] {i['repo']}#{i['number']} {i['title']} — @{i['author']} · {i['age_days']}일 · "
                         f"±{i['size']} · {', '.join(i['why'])} — {i['url']}{slack}")
            continue
        prod = {True: "예", False: "아니오", None: "모름"}[i["prod"]]
        lines.append(f"{n}. [{tag}] {i['repo']}#{i['number']} {i['title']} — @{i['author']} · {i['age_days']}일 · "
                     f"{i['diff']} · 프로덕션 배포: {prod} — {i['url']}")
        lines.append(f"   ! {i['command']}" if i["run_by"] == "owner" else f"   규칙이 덮음, 이 회차가 실행: {i['command']}")
        if i["rule_draft"] and i["rule_draft"] not in drafts:
            drafts.append(i["rule_draft"])
    if drafts:
        lines.append("없는 규칙 (tune-automode 초안, 설정은 고치지 않았다):")
        lines += [f"- {d}" for d in drafts]
    lines += plan.get("inbox_failed", [])
    if plan.get("uncovered"):
        lines.append(f"회차 단계(PROMPT_STEPS)가 없는 kind 의 항목 {len(plan['uncovered'])}건은 보내지도 기록하지도 않았다: "
                     f"{', '.join(plan['uncovered'])}")
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
        by_id = {n["id"]: n for n in gh.merge_state(list(acted), plan.get("viewer"))}
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
    except (RuntimeError, OSError, ValueError, KeyError, TypeError, re.error, subprocess.TimeoutExpired) as e:
        return EXIT_ERROR, f"GitHub 를 읽지 못함: {e}"
    keys = [i["key"] for i in plan["new"]]
    if not keys:
        late = f", 회차 단계가 없어 보내지 않는 항목 {len(plan['uncovered'])}건: {', '.join(plan['uncovered'])}" \
            if plan["uncovered"] else ""
        return EXIT_NOTHING, f"새 항목 없음 (제외 {len(plan['excluded'])}건{late})"
    return EXIT_NEW, f"새 항목 {len(keys)}건: " + ", ".join(keys)


def commit(state, cfg, now):
    plan = read_json(state / "plan.json", None)
    if not plan:
        raise SystemExit("commit: no plan.json; run plan first")
    ledger = read_json(state / "ledger.json", {})
    # A round step with no recorded result (a crash, a refused `result`) stays out, so the next plan
    # brings it back; its attempt mark then hands it to the owner.
    once = [i for i in plan["new"] if i.get("once", True) and (i.get("run_by") != "round" or i.get("done"))]
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


def inbox_add(i):
    cmd = [ORCH_DASH, "inbox", "add", "--type", "run_command", "--title", f"{i['repo']}#{i['number']} 승인: {i['title']}"[:200],
           "--url", i["url"], "--command", i["command"], "--key", f"prs:{i['key']}"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        failed = r.returncode and (r.stderr.strip()[:200] or f"exit {r.returncode}")
    except (OSError, subprocess.TimeoutExpired) as e:
        failed = str(e)
    return f"대시보드 inbox 등록 실패 {i['repo']}#{i['number']}: {failed}" if failed else None


def deliver(state, plan):
    """The run's message and the owner's inbox items. Before the send: a resent plan reuses each --key, so nothing doubles.
    Each round step gets its attempt mark here, so a later plan hands it to the owner even if this run never
    reaches `round` or `result`."""
    steps = [i for i in plan["new"] if i.get("run_by") == "round"]
    if steps:
        ledger = read_json(state / "ledger.json", {})
        ledger.update({f"attempt:{i['key']}": {"at": plan["run"], "kind": "attempt"} for i in steps})
        write_json(state / "ledger.json", ledger)
    failed = [inbox_add(i) for i in plan["new"] if i["kind"] == "ownergate" and i["run_by"] == "owner"]
    # Kept in plan.json so body() still shows them after verify rewrites body.txt.
    plan["inbox_failed"] = [f for f in failed if f]
    if plan["new"]:
        write_json(state / "plan.json", plan)
    text = body(plan)
    state.mkdir(parents=True, exist_ok=True)
    (state / "body.txt").write_text(text)
    return text


def result(state, key, ok, note=""):
    """How one round step went, appended to body.txt; a step that did not go through is handed to the owner."""
    plan = read_json(state / "plan.json", None)
    i = next((x for x in (plan or {}).get("new", []) if x["key"] == key and x.get("run_by") == "round"), None)
    if not i or not i.get("handed"):
        raise SystemExit(f"result: {key} is not a round step `round` handed out")
    if ok:
        i["done"] = True
        write_json(state / "plan.json", plan)
        line = f"회차가 실행함: {i['repo']}#{i['number']}"
    else:
        i["run_by"] = "owner"
        write_json(state / "plan.json", plan)
        line = "\n".join(x for x in (f"회차 실행 안 됨({note or '이유 없음'}), 소유자 몫: {i['repo']}#{i['number']}",
                                       f"   ! {i['command']}", inbox_add(i)) if x)
    with (state / "body.txt").open("a") as fh:
        fh.write("\n" + line)
    return line


def round_steps(state):
    """The steps an allow rule lets this round run, each handed out once per run."""
    plan = read_json(state / "plan.json", None) or {"new": []}
    marked = read_json(state / "ledger.json", {})
    steps = [i for i in plan["new"] if i.get("run_by") == "round" and not i.get("handed") and f"attempt:{i['key']}" in marked]
    for i in steps:
        i["handed"] = True
    if steps:
        write_json(state / "plan.json", plan)
    return [f"{i['key']}\t{i['command']}" for i in steps]


def release_brief(repo, items, cfg):
    decl = cfg["deploy"][repo]
    lines = [f"{repo} 에서 머지된 PR 을 dev 에서 확인하고 증거를 각 PR 에 sticky comment 로 남긴다.",
             f"절차: {REFERENCE} 를 따른다.", "PR:"]
    lines += [f"- #{i['number']} {i['url']} merge {i['merge_commit']} · "
              + ("dev 리비전을 읽지 못함, 직접 확인" if i["deployed"] is None else "dev 에 반영됨" if i["deployed"]
                 else f"dev({i['deployed_revision']}) 에 아직 없음") for i in items]
    lines += ["배포 선언:"] + [f"- {k}: {decl[k]}" for k in ("dev_release", "revision_cmd", "logs", "verify", "ticket") if decl.get(k)]
    lines += [f"승인: {cfg['release_approval']}" if cfg.get("release_approval")
              else "승인: dev 배포 승인이 없다. 동기화를 기다리는 것 말고 배포가 필요하면 코디네이터에게 묻는다.",
              "사람만: 프로덕션 배포, dev 밖의 환경, 되돌릴 수 없는 작업. 하지 않고 코디네이터에게 보고한다.",
              "권한 프롬프트·auto mode 분류기·hook·사람이 거부한 행위는 어떤 형태로도 다시 시도하지 않는다"
              "(재실행, 스크립트·다른 도구로 옮기기, 대상 이름 바꾸기, 서브에이전트나 다른 세션에 넘기기 모두 금지). "
              "무엇을 하려던 것인지, 거부 종류, 정확한 명령을 보고한다.",
              "끝나면 worker_done 한 통에 PR 별 sticky comment URL, 확인한 리비전, 읽은 로그 줄을 담는다."]
    return "\n".join(lines)


def dispatch_cmds(plan, cfg):
    """One worker-start per repo with due unreleased items; items of other kinds are only messaged."""
    by_repo = {}
    for i in plan["new"]:
        if i["kind"] == "unreleased" and i["repo"] in cfg["deploy"]:
            by_repo.setdefault(i["repo"], []).append(i)
    cmds = []
    for repo, items in sorted(by_repo.items()):
        decl = cfg["deploy"][repo]
        cmd = ["orca", "orchestration", "worker-start", "--spec", release_brief(repo, items, cfg),
               "--task-title", f"dev 확인: {repo} " + " ".join(f"#{i['number']}" for i in items),
               "--repo", decl.get("orca_repo") or f"name:{repo.split('/')[1]}", "--worktree", "new-top-level",
               "--agent", cfg["worker_agent"], "--json"]
        if cfg.get("dispatch_run"):
            cmd[-1:-1] = ["--run", cfg["dispatch_run"]]
        cmds.append(cmd)
    return cmds


# A kind's own step in the automation prompt, between the coordinator send and commit; {run[sub]} is the prs.py
# call for that subcommand. None: the message is the whole action. A kind missing here has no step, so schedule
# refuses it and compute keeps its items out of plan.json and the ledger.
# Steps run in this dict's order, not the config's: the unreleased step ends by gating commit, which deletes plan.json.
PROMPT_STEPS = {
    "review_request": None,
    "ready_to_merge": None,   # its merge step precedes the send, so schedule_cmd builds it
    "ownergate": None,  # its round/result step runs before the send: PRE_SEND_STEP
    "review_wait": (
        "그다음 `{run[actions]}` 가 출력하는 행동을 하나씩 처리하고, 처리할 때마다 `{run[log]} --key <key> --source <결과>` 로 "
        "기록한다. Slack 행동(request·remind·rerequest)은 slack-post 스킬을 먼저 읽고 그 규칙대로 channel 에 보낸다: "
        "request 는 같은 reviewer 의 것을 한 메시지로 묶고, 보내기 전에 channel 에서 그 PR 의 기존 의뢰를 찾아 있으면 보내지 않고 "
        "`--source found --thread <channel>:<ts> --at <ts>` 로 기록한다. remind·rerequest 는 PR 마다 thread 에 답글로만 보낸다"
        "(reply_broadcast false). thread 가 비어 있으면 channel 에서 그 PR 의 의뢰 스레드를 찾고, 없으면 최상위로 올리지 않고 "
        "`--source no_thread` 로 기록한다. post_at 이 있으면 바로 보내지 않고 그 시각으로 예약하고 "
        "`--source scheduled --scheduled <id> --post-at <post_at>`(스레드 답글이면 `--thread <channel>:<부모 ts>` 도) 로, 보냈으면 "
        "`--source sent --thread <channel>:<ts>` 로 기록한다. retrigger 는 그 PR 에 `gh pr comment <url> --body <comment>` 를 "
        "달고 `--source sent` 로 기록한다. 보내기 직전에 이미 필요 없어진 행동(그사이 승인됨·머지됨, 리뷰어의 slack id 없음 등)은 "
        "보내지 않고 `--source skipped` 로 기록한다. 어느 행동이든 권한·분류기·훅에 거부되면 다른 방법으로 다시 시도하지 않는다: "
        "`--source refused` 로 기록하고 거부 범주와 명령을 코디네이터에게 보낸다. 이 단계의 GitHub 쓰기는 retrigger 코멘트뿐이다."),
    "unreleased": (
        "보내기가 성공했을 때만 `{run[dispatch]}` 를 실행한다. 출력된 줄은 실행하지 않는다: worker-start 는 Run 에 묶인 "
        "코디네이터 터미널에서만 된다. 줄이 있으면 같은 줄이 담긴 {dispatch_file} 을 `--body \"$(cat {dispatch_file})\"` 로, 제목 "
        "\"watch-prs: 릴리스 워커 <줄 수>건 시작 요청\" 으로 코디네이터에게 한 통 보낸다(줄이 없으면 건너뛴다). 그 전송이 성공했을 때만 commit 으로 간다. "
        "이 회차는 워커를 띄우지도 배포하지도 않는다."),
}
# The ownergate step runs before the send: its results are appended to the message.
PRE_SEND_STEP = (
    "보내기 전에 `{run[round]}` 가 출력한 줄(키, 탭, 명령)마다 그 명령을 단독으로 한 번 실행하고, 승인됐으면 "
    "`{run[result]} <키> ok`, 아니면 `{run[result]} <키> failed \"<이유>\"` 를 실행한다. 본문에 적힌 명령은 실행하지 않는다. "
    "권한 프롬프트·auto mode 분류기·hook·사람이 거부한 단계는 어떤 형태로도(재실행, 스크립트·다른 도구, 다른 세션) 다시 시도하지 않고, "
    "거부 종류와 정확한 명령을 `result <키> failed` 의 이유로 남긴다.")


def schedule_cmd(script, cfg, workspace, provider="claude", edit=None):
    """The `orca automations create` command, or `edit <id>` to keep an existing automation's id.

    Every command starts with python3: a session may refuse an `env` or `VAR=` prefix as its own command.
    A non-default state goes in as --state right after the subcommand, since argv[1] is the subcommand."""
    missing = [k for k in cfg["kinds"] if k not in PROMPT_STEPS]
    if missing:
        raise SystemExit(f"schedule: kind {', '.join(missing)} has no round step in prs.py PROMPT_STEPS; add one first")
    state = [] if STATE.parent == STATE_HOME else ["--state", str(STATE.parent)]
    run = lambda sub: shlex.join(["python3", str(script), *sub.split(), *state])
    to = cfg.get("inbox_to") or "<prs.inbox_to>"
    body_file = STATE / "body.txt"
    merging = "ready_to_merge" in cfg["kinds"]
    gate = "ownergate" in cfg["kinds"]
    # Merging comes before the send so the bundle carries each merge's result and its default-branch CI.
    merge_step = (f"`{run('merges')}` 가 출력하는 머지 대상마다, 주석 줄(PR, CLEAN, 체크, 리뷰, 머지 방식)을 채팅에 그대로 적은 뒤 "
                  f"그 아래 `gh pr merge` 명령을 단독 명령으로 한 번씩 실행한다(--admin·--auto 를 붙이지 않는다). 거부되거나 실패한 "
                  f"머지는 다시 시도하지 않고 다른 형태로 바꾸지도 않으며, 그 PR 과 거부·실패 내용, 정확한 명령을 본문 끝에 덧붙여 보고한다. "
                  f"그다음 `{run('verify')}` 로 MERGED 와 기본 브랜치 CI 를 확인해 본문을 다시 쓴다. verify 가 실패하면 보내지도 "
                  f"commit 하지도 않고 그 오류를 남긴 채 끝낸다. 성공하면 ") if merging else ""
    subs = {s: run(s) for s in ("actions", "log", "dispatch", "round", "result")}
    # After verify, which rewrites body.txt: the round's results are appended to the message it sends.
    first = PRE_SEND_STEP.format(run=subs) + " 그다음 " if gate else ""
    steps = " ".join(step.format(run=subs, dispatch_file=shlex.quote(str(STATE / "dispatch.txt")))
                     for k, step in PROMPT_STEPS.items() if k in cfg["kinds"] and step)
    if merging:
        reads = (" 이 회차는 머지 단계" + ("와 `round` 가 낸 승인" if gate else "") + " 밖에서 승인·리뷰·배포를 직접 하지 않는다."
                 + ("" if "review_wait" in cfg["kinds"] else " 코멘트·재요청도 하지 않는다."))
    elif "review_wait" in cfg["kinds"]:
        reads = " GitHub 쓰기는 retrigger 코멘트" + ("와 `round` 가 낸 승인" if gate else "") + "뿐이다."
    else:
        reads = (" 이 회차는 감지하고 넘기기만 한다: 리뷰·코멘트·재요청·배포를 직접 하지 않는다"
                 + (" (예외는 `round` 가 낸 단계뿐이다)." if gate else "."))
    reads += (" 권한 프롬프트·auto mode 분류기·hook·사람이 거부한 행동은 어떤 형태로도(재실행, 스크립트·다른 도구, 다른 세션) "
              "다시 시도하지 않고, 무엇을 하려던 것인지와 거부 종류, 정확한 명령을 보고한다.")
    prompt = (f"watch-prs 회차다. `{run('plan --body')}` 를 한 번 실행한다. 출력이 비어 있으면 아무것도 보내지 않고 끝낸다. "
              f"아니면 {merge_step}{first}`orca orchestration send --to {shlex.quote(to)} --type status --subject \"watch-prs: <N>건\" "
              f"--body \"$(cat {shlex.quote(str(body_file))})\" --json` 로 한 통만 보낸다(N 은 본문 첫 줄의 건수). "
              + (steps + " " if steps else "")
              + f"코디네이터 전송이 성공했을 때만 마지막에 `{run('commit')}` 을 실행한다." + reads)
    if edit:
        return ["orca", "automations", "edit", edit, "--trigger", cfg["cron"], "--precheck", run("precheck"),
                "--prompt", prompt, "--json"] + (["--timezone", cfg["timezone"]] if cfg.get("timezone") else [])
    cmd = ["orca", "automations", "create", "--name", "agent-skills watch-prs", "--trigger", cfg["cron"],
           "--provider", provider, "--workspace-mode", "existing", "--workspace", workspace,
           "--precheck", run("precheck"), "--prompt", prompt, "--json"]
    if cfg.get("timezone"):
        cmd[cmd.index("--provider"):cmd.index("--provider")] = ["--timezone", cfg["timezone"]]
    return cmd


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    # Positional arguments after the subcommand; --state and --config may sit anywhere among them.
    args, rest = [], iter(sys.argv[2:])
    for a in rest:
        if a in ("--state", "--config"):
            next(rest, None)
        else:
            args.append(a)
    cfg = config(opt("--config", CONFIG))
    now = time.time()
    if cmd == "precheck":
        code, why = precheck(GitHub(), cfg, STATE, now)
        print(why, file=sys.stderr)
        sys.exit(code)
    if cmd in ("plan", "merges", "verify", "actions", "log", "commit", "dispatch", "schedule") and cfg is None:
        sys.exit(f'{cmd}: set "prs" in {CONFIG}')
    if cmd == "plan" and "--body" not in sys.argv:
        print(json.dumps(compute(GitHub(), cfg, read_json(STATE / "ledger.json", {}), now), ensure_ascii=False, indent=1))
    elif cmd == "plan":
        if lock_held(STATE, cfg, now):
            sys.exit("plan: another run holds plan.json; it frees after lock_minutes")
        plan = compute(GitHub(), cfg, read_json(STATE / "ledger.json", {}), now)
        if plan["new"]:
            write_json(STATE / "plan.json", plan)
        print(deliver(STATE, plan))
    elif cmd == "round":
        print("\n".join(round_steps(STATE)))
    elif cmd == "result" and len(args) > 1 and args[1] in ("ok", "failed"):
        print(result(STATE, args[0], args[1] == "ok", " ".join(args[2:])))
    elif cmd == "merges":
        # Recorded before the round runs them: a refused or failed merge is never retried at that head, even
        # when verify or commit never happens.
        ledger = read_json(STATE / "ledger.json", {})
        acts = [i for i in read_json(STATE / "plan.json", {}).get("new", [])
                if i.get("cmd") and not delivered(ledger, i["key"], i["kind"])]
        ledger.update({i["key"]: {"at": now, "kind": i["kind"], "id": i["id"]} for i in acts})
        write_json(STATE / "ledger.json", ledger)
        for i in acts:
            print(f"# {i['repo']}#{i['number']} {i['why'][0]}\n{i['cmd']}")
    elif cmd == "verify":
        print(verify(GitHub(), STATE, cfg))
    elif cmd == "actions":
        for i in (read_json(STATE / "plan.json", None) or {"new": []})["new"]:
            if "action" in i:
                print(json.dumps({k: i[k] for k in ("key", "action", "url", "title", "reviewer", "slack_user", "channel",
                                                     "thread", "post_at", "comment")}, ensure_ascii=False))
    elif cmd == "log":
        num = lambda name: float(opt(name)) if opt(name) else None
        row = log(STATE, opt("--key"), opt("--source"), now, thread=opt("--thread"), scheduled=opt("--scheduled"),
                  post_at=num("--post-at"), at=num("--at"))
        print(json.dumps(row, ensure_ascii=False))
    elif cmd == "dispatch":
        plan = read_json(STATE / "plan.json", None)
        if not plan:
            sys.exit("dispatch: no plan.json; run plan --body first")
        # The brief is multi-line, so it goes to a file and each printed command stays on one line.
        lines = []
        for n, line in enumerate(dispatch_cmds(plan, cfg)):
            i = line.index("--spec")
            # Named by run: the coordinator may start the line after the next round has written its own briefs.
            brief = STATE / f"brief-{int(plan['run'])}-{n}.txt"
            brief.write_text(line[i + 1])
            lines.append(shlex.join(line[:i] + line[i + 2:]) + f' --spec "$(cat {shlex.quote(str(brief))})"')
        # The round forwards these as a message body; a file keeps `$(cat …)` from expanding in the round's shell.
        (STATE / "dispatch.txt").write_text("\n".join(lines))
        print("\n".join(lines))
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
