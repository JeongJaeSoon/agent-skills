#!/usr/bin/env python3
"""Bookkeeping for watch-prs: which pull requests need the owner, ranked, sent once per head.

Usage:
  python3 prs.py precheck [--config PATH]
  python3 prs.py plan [--body] [--config PATH]
  python3 prs.py commit [--config PATH]
  python3 prs.py schedule [--workspace SELECTOR] [--provider claude] [--write]

precheck asks GitHub (through `gh`) for the items and exits 0 when some are not in the ledger yet,
1 when nothing is new or a run still holds plan.json (younger than lock_minutes), and 2 when it
could not ask (no config, `gh` failed). It uses no model and writes nothing.
plan prints the same items as JSON and writes nothing. plan --body is the run's step: it prints the
coordinator message (empty when nothing is new), keeps it in body.txt, and, when something is new,
writes the items to plan.json, which is also the run lock. commit records what plan.json delivered,
so the same (repo, number, headRefOid) is never sent twice. schedule prints the
`orca automations create` command and runs it only with --write.
Config: ~/.claude/agent-skills.json → "prs" (see SKILL.md). State: $AGENT_SKILLS_STATE/prs
(default ~/.local/state/agent-skills/prs). The ledger holds keys, node ids and timestamps, never PR text.
"""
import datetime as dt, json, os, pathlib, shlex, subprocess, sys, time

CONFIG = pathlib.Path("~/.claude/agent-skills.json").expanduser()
STATE = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser() / "prs"
GH = os.environ.get("AGENT_SKILLS_GH", "gh")
# Only review_request has a collector yet; the rest are reserved so later flows extend COLLECTORS, not the CLI.
KINDS = ("review_request", "review_wait", "ready_to_merge", "unreleased", "ownergate")
LABELS = {"review_request": "리뷰 요청"}
DEFAULTS = {"kinds": ["review_request"], "orgs": [], "repos": [], "exclude_orgs": [], "exclude_repos": [],
            "ignore_bots": [], "self_bots": [], "inbox_to": None, "max_items": 10, "stale_days": 30,
            "keep_days": 60, "lock_minutes": 60, "cron": "*/15 * * * *", "timezone": None}
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
    """The three reads watch-prs needs. Tests replace it with a fake holding fixture nodes."""

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


COLLECTORS = {"review_request": collect_review_requests}


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
    prompt = (f"watch-prs 회차다. `{env} plan --body` 를 한 번 실행한다. 출력이 비어 있으면 아무것도 보내지 않고 끝낸다. "
              f"아니면 `orca orchestration send --to {shlex.quote(to)} --type status --subject \"watch-prs: <N>건\" "
              f"--body \"$(cat {shlex.quote(str(body_file))})\" --json` 로 한 통만 보내고(N 은 본문 첫 줄의 건수), 보내기가 성공했을 때만 "
              f"`{env} commit` 을 실행한다. 이 회차는 읽기만 한다: 리뷰·코멘트·재요청 같은 GitHub 쓰기는 하지 않는다.")
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
    if cmd in ("plan", "commit", "schedule") and cfg is None:
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
