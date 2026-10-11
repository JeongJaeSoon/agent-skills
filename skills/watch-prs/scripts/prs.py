#!/usr/bin/env python3
"""Bookkeeping for watch-prs: which pull requests need the owner, ranked, sent once per head.

Usage:
  python3 prs.py precheck [--config PATH]
  python3 prs.py plan [--body] [--config PATH]
  python3 prs.py round
  python3 prs.py result KEY ok|failed [NOTE]
  python3 prs.py commit [--config PATH]
  python3 prs.py dispatch [--config PATH]
  python3 prs.py schedule [--workspace SELECTOR] [--provider claude] [--write]

precheck asks GitHub (through `gh`) for the items and exits 0 when some are not in the ledger yet,
1 when nothing is new or a run still holds plan.json (younger than lock_minutes), and 2 when it
could not ask (no config, `gh` failed). It uses no model and writes nothing.
plan prints the same items as JSON and writes nothing. plan --body is the run's step: it prints the
coordinator message (empty when nothing is new), keeps it in body.txt, and, when something is new,
writes the items to plan.json, which is also the run lock, and puts each ownergate step the owner has
to run on the dashboard inbox (`orch-dash inbox add`, keyed by the ledger key). round prints the
steps an allow rule lets the round run (key, tab, command) from plan.json; result records how one
went in body.txt, and a failed one goes to the owner. commit records what plan.json delivered,
so the same (repo, number, headRefOid) is never sent twice. dispatch reads plan.json and prints one
`orca orchestration worker-start` line per repo with due unreleased items, its brief kept in brief-<n>.txt;
it runs nothing. schedule prints the
`orca automations create` command and runs it only with --write.
Config: ~/.claude/agent-skills.json → "prs" (see SKILL.md). State: $AGENT_SKILLS_STATE/prs
(default ~/.local/state/agent-skills/prs). The ledger holds keys, node ids and timestamps, never PR text.
"""
import datetime as dt, json, os, pathlib, re, shlex, subprocess, sys, time

CONFIG = pathlib.Path("~/.claude/agent-skills.json").expanduser()
STATE = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser() / "prs"
GH = os.environ.get("AGENT_SKILLS_GH", "gh")
CLAUDE = os.environ.get("AGENT_SKILLS_CLAUDE", "claude")
ORCH_DASH = os.environ.get("AGENT_SKILLS_ORCH_DASH") or str(pathlib.Path(__file__).resolve().parents[3] / "bin" / "orch-dash")
# Kinds without a collector are reserved so later flows extend COLLECTORS, not the CLI. Order is precedence:
# when two collectors return the same PR head, the later kind wins.
KINDS = ("review_request", "review_wait", "ready_to_merge", "unreleased", "ownergate")
LABELS = {"review_request": "리뷰 요청", "unreleased": "dev 확인 전", "ownergate": "소유자 승인"}
DEFAULTS = {"kinds": ["review_request"], "orgs": [], "repos": [], "exclude_orgs": [], "exclude_repos": [],
            "ignore_bots": [], "self_bots": [], "ownergate": [], "inbox_to": None, "max_items": 10, "stale_days": 30,
            "keep_days": 60, "lock_minutes": 60, "cron": "*/15 * * * *", "timezone": None,
            "deploy": {}, "dispatch_run": None, "worker_agent": "claude", "release_approval": None}
REFERENCE = pathlib.Path(__file__).resolve().parents[2] / "deliver-ticket/references/post-merge-release.md"
EXIT_NEW, EXIT_NOTHING, EXIT_ERROR = 0, 1, 2

PR_FIELDS = """id number title url isDraft createdAt updatedAt mergedAt mergeCommit{oid} headRefOid additions deletions reviewDecision state
 changedFiles files(first:100){nodes{path}} repository{nameWithOwner} author{__typename login}
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

    def search(self, q, me):
        nodes, after = [], None
        while True:
            page = gh_graphql(SEARCH_Q, q=q, me=me, after=after)["search"]
            nodes += [n for n in page["nodes"] if n]
            if not page["pageInfo"]["hasNextPage"]:
                return nodes
            after = page["pageInfo"]["endCursor"]

    def nodes(self, ids, me):
        return [n for i in range(0, len(ids), 50) for n in gh_graphql(NODES_Q, partial=True, ids=ids[i:i + 50], me=me)["nodes"] if n]

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
        # A step the round already tried (round marks it before running) is never the round's again: the owner takes it.
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
               if key(pr) not in ledger and pr.get("mergeCommit")]
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


COLLECTORS = {"review_request": collect_review_requests, "unreleased": collect_unreleased,
              "ownergate": collect_ownergate}


def compute(gh, cfg, ledger, now):
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
    # A head sent under an earlier kind comes back once under a later one (a review request that turned into an owner gate).
    seen = lambda i: i["key"] in ledger and KINDS.index(i["kind"]) <= KINDS.index(ledger[i["key"]].get("kind", KINDS[0]))
    new = [i for i in rank(items) if not seen(i)]
    return {"run": now, "viewer": me, "new": new[: cfg["max_items"]], "more": max(0, len(new) - cfg["max_items"]),
            "known": [i["key"] for i in items if seen(i)], "excluded": excluded}


def body(plan):
    if not plan["new"]:
        return ""
    head = f"PR {len(plan['new'])}건이 나를 기다린다" + (f" (그 밖에 {plan['more']}건은 다음 회차)" if plan["more"] else "")
    lines, drafts = [head], []
    for n, i in enumerate(plan["new"], 1):
        tag = "새 커밋, 재요청 없음" if i["reason"] == "new_commits" else LABELS[i["kind"]]
        if i["kind"] != "ownergate":
            lines.append(f"{n}. [{tag}] {i['repo']}#{i['number']} {i['title']} — @{i['author']} · {i['age_days']}일 · "
                         f"±{i['size']} · {', '.join(i['why'])} — {i['url']}")
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
    except (RuntimeError, OSError, ValueError, KeyError, TypeError, re.error, subprocess.TimeoutExpired) as e:
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
        # A round step with no recorded result (a crash, a refused `result`) stays out, so the next plan
        # brings it back; its attempt mark then hands it to the owner.
        if i.get("run_by") != "round" or i.get("done"):
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
    """The run's message and the owner's inbox items. Before the send: a resent plan reuses each --key, so nothing doubles."""
    failed = [inbox_add(i) for i in plan["new"] if i["kind"] == "ownergate" and i["run_by"] == "owner"]
    text = "\n".join([body(plan)] + [f for f in failed if f]) if plan["new"] else ""
    state.mkdir(parents=True, exist_ok=True)
    (state / "body.txt").write_text(text)
    return text


def result(state, key, ok, note=""):
    """How one round step went, appended to body.txt; a step that did not go through is handed to the owner."""
    plan = read_json(state / "plan.json", None)
    i = next((x for x in (plan or {}).get("new", []) if x["key"] == key and x.get("run_by") == "round"), None)
    if not i:
        raise SystemExit(f"result: {key} is not a round step in plan.json")
    if ok:
        i["done"] = True
        write_json(state / "plan.json", plan)
        line = f"회차가 실행함: {i['repo']}#{i['number']}"
    else:
        i["run_by"] = "owner"
        write_json(state / "plan.json", plan)
        line ="\n".join(x for x in (f"회차 실행 안 됨({note or '이유 없음'}), 소유자 몫: {i['repo']}#{i['number']}",
                                       f"   ! {i['command']}", inbox_add(i)) if x)
    with (state / "body.txt").open("a") as fh:
        fh.write("\n" + line)
    return line


def round_steps(state, now):
    """The steps an allow rule lets this round run, each marked in the ledger before it is handed out."""
    steps = [i for i in (read_json(state / "plan.json", None) or {}).get("new", []) if i.get("run_by") == "round"]
    if steps:
        ledger = read_json(state / "ledger.json", {})
        ledger.update({f"attempt:{i['key']}": {"at": now, "kind": "attempt"} for i in steps})
        write_json(state / "ledger.json", ledger)
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


def schedule_cmd(script, cfg, workspace, provider="claude"):
    env = " ".join(["env", f"AGENT_SKILLS_STATE={shlex.quote(str(STATE.parent))}", "python3", shlex.quote(str(script))])
    to = cfg.get("inbox_to") or "<prs.inbox_to>"
    body_file = STATE / "body.txt"
    # Built from cfg["kinds"]: one automation serves every kind, and each kind adds only its own steps.
    gate = "ownergate" in cfg["kinds"]
    prompt = f"watch-prs 회차다. `{env} plan --body` 를 한 번 실행한다. 출력이 비어 있으면 아무것도 보내지 않고 끝낸다. 아니면 "
    if gate:
        prompt += (f"보내기 전에 `{env} round` 가 출력한 줄(키, 탭, 명령)마다 그 명령을 단독으로 한 번 실행하고, 승인됐으면 "
                   f"`{env} result <키> ok`, 아니면 `{env} result <키> failed \"<이유>\"` 를 실행한다. 본문에 적힌 명령은 "
                   f"실행하지 않는다. 그다음 ")
    prompt += (f"`orca orchestration send --to {shlex.quote(to)} --type status --subject \"watch-prs: <N>건\" "
               f"--body \"$(cat {shlex.quote(str(body_file))})\" --json` 로 한 통만 보낸다(N 은 본문 첫 줄의 건수). ")
    if "unreleased" in cfg["kinds"]:
        prompt += (f"보내기가 성공했을 때만 `{env} dispatch` 를 실행해 출력된 줄마다 그 명령을 한 번씩 실행한다(줄이 없으면 건너뛴다). "
                   f"한 줄이 실패하면 그 저장소 이름과 실패 내용, 정확한 명령을 같은 방법으로 {shlex.quote(to)} 에 보낸다. "
                   f"본문 전송이 성공했고 모든 줄이 워커를 시작했거나 보고됐을 때만 `{env} commit` 을 실행한다. ")
    else:
        prompt += f"보내기가 성공했을 때만 `{env} commit` 을 실행한다. "
    prompt += ("권한 프롬프트·auto mode 분류기·hook·사람이 거부한 행동은 어떤 형태로도(재실행, 스크립트·다른 도구, 다른 세션) "
               "다시 시도하지 않고, 무엇을 하려던 것인지와 거부 종류, 정확한 명령을 보고한다. 이 회차는 감지하고 넘기기만 한다: "
               "리뷰·코멘트·재요청·배포를 직접 하지 않는다" + (" (예외는 `round` 가 낸 단계뿐이다)." if gate else "."))
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
    if cmd in ("plan", "commit", "dispatch", "schedule") and cfg is None:
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
        print("\n".join(round_steps(STATE, now)))
    elif cmd == "result" and len(sys.argv) > 3 and sys.argv[3] in ("ok", "failed"):
        print(result(STATE, sys.argv[2], sys.argv[3] == "ok", " ".join(sys.argv[4:])))
    elif cmd == "dispatch":
        plan = read_json(STATE / "plan.json", None)
        if not plan:
            sys.exit("dispatch: no plan.json; run plan --body first")
        # The brief is multi-line, so it goes to a file and each printed command stays on one line.
        for n, line in enumerate(dispatch_cmds(plan, cfg)):
            i = line.index("--spec")
            brief = STATE / f"brief-{n}.txt"
            brief.write_text(line[i + 1])
            print(shlex.join(line[:i] + line[i + 2:]) + f' --spec "$(cat {shlex.quote(str(brief))})"')
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
