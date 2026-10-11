"""Rules of prs.py: who counts as a personal request, ranking, the ledger, precheck exit codes, unreleased merges, dispatch, schedule.
Run: python3 test_prs.py"""
import json, os, pathlib, re, shlex, stat, subprocess, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import prs as p

ME = "owner"
NOW = 1_800_000_000.0
DAY = 86400


def iso(t):
    import datetime as dt
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def user(login, code_owner=False):
    return {"asCodeOwner": code_owner, "requestedReviewer": {"__typename": "User", "login": login}}


def team(slug):
    return {"asCodeOwner": True, "requestedReviewer": {"__typename": "Team", "slug": slug}}


def pr(n, repo="acme/widgets", author="alice", kind="User", reqs=(), draft=False, age=5, idle=1, size=10,
       decision="REVIEW_REQUIRED", head=None, reviewed=None, state="OPEN"):
    return {"id": f"PR_{repo}_{n}", "number": n, "title": f"Change {n}", "url": f"https://github.com/{repo}/pull/{n}",
            "isDraft": draft, "createdAt": iso(NOW - age * DAY), "updatedAt": iso(NOW - idle * DAY),
            "headRefOid": head or f"h{n}", "additions": size, "deletions": 0, "reviewDecision": decision, "state": state,
            "repository": {"nameWithOwner": repo}, "author": {"__typename": kind, "login": author},
            "reviewRequests": {"nodes": list(reqs)},
            "reviews": {"nodes": [{"commit": {"oid": reviewed}}] if reviewed else []}}


class Fake:
    def __init__(self, search, nodes=(), merged=(), deployed=None, live=()):
        self.found, self.by_id = list(search), {n["id"]: n for n in nodes}
        self.merged, self.deployed, self.live, self.queries = list(merged), deployed, set(live), []

    def viewer(self):
        return ME

    def search(self, q, me):
        assert me == ME
        self.queries.append(q)
        if "is:merged" in q:
            return [m for m in self.merged if f"repo:{m['repository']['nameWithOwner']} " in q]
        assert "review-requested:@me" in q
        return self.found

    def revision(self, cmd):
        if isinstance(self.deployed, Exception):
            raise self.deployed
        return self.deployed

    def contains(self, repo, commit, deployed):
        return commit in self.live

    def nodes(self, ids, me):
        return [self.by_id[i] for i in ids if i in self.by_id]


cfg = {**p.DEFAULTS, "self_bots": ["owner-bot"], "ignore_bots": ["noisy[bot]"], "exclude_repos": ["acme/legacy"]}
state = pathlib.Path(tempfile.mkdtemp()) / "prs"

# Who is in: a personal, non-CODEOWNERS request on a non-draft PR whose author is not a self or ignored bot.
found = [
    pr(1, reqs=[user(ME)], age=3, size=50),                                     # only blocker, human
    pr(2, reqs=[user(ME), team("core")], age=40, size=5),                       # human, old
    pr(3, author="depbot", kind="Bot", reqs=[user(ME), team("sec")], age=60),  # bot author
    pr(4, reqs=[user(ME)], idle=90, age=100),                                   # stale
    pr(5, reqs=[team("core")]),                                                 # team only
    pr(6, reqs=[user(ME, code_owner=True)]),                                    # CODEOWNERS as user
    pr(7, reqs=[user(ME)], draft=True, decision="CHANGES_REQUESTED"),           # draft
    pr(8, author="owner-bot", kind="Bot", reqs=[user(ME)]),                     # self bot
    pr(9, author="noisy", kind="Bot", reqs=[user(ME)]),                         # ignored bot ([bot] suffix normalised)
    pr(10, repo="acme/legacy", reqs=[user(ME)]),                                # excluded repo
    pr(11, reqs=[user(ME), user("carol")], age=40, size=500),                   # human, old, big
]
plan = p.compute(Fake(found), cfg, {}, NOW)
order = [i["number"] for i in plan["new"]]
assert order == [1, 2, 11, 3, 4], order
why = {e["pr"]: e["why"] for e in plan["excluded"]}
assert why == {"acme/widgets#5": "팀·CODEOWNERS 요청뿐", "acme/widgets#6": "팀·CODEOWNERS 요청뿐", "acme/widgets#7": "draft",
               "acme/widgets#8": "본인 봇이 작성", "acme/widgets#9": "무시하는 봇이 작성", "acme/legacy#10": "제외 대상 저장소"}, why
first = plan["new"][0]
assert first["only_blocker"] and "남은 리뷰어가 나뿐" in first["why"] and first["key"] == "acme/widgets#1@h1"
assert plan["new"][-1]["stale"] and plan["new"][-1]["why"][0].startswith("stale:")
assert plan["new"][3]["why"] == ["봇 작성"]

# An org allowlist keeps only those orgs; a repo entry adds one repo outside them.
only = p.compute(Fake([pr(1, reqs=[user(ME)]), pr(2, repo="other/x", reqs=[user(ME)]), pr(3, repo="third/y", reqs=[user(ME)])]),
                 {**cfg, "orgs": ["acme"], "repos": ["third/y"]}, {}, NOW)
assert [i["repo"] for i in only["new"]] == ["acme/widgets", "third/y"]

# max_items caps the message; the rest wait for the next run.
capped = p.compute(Fake(found), {**cfg, "max_items": 2}, {}, NOW)
assert [i["number"] for i in capped["new"]] == [1, 2] and capped["more"] == 3

# body: one line per item, ranked, with repo#N, title, author, age, why.
text = p.body(plan)
lines = text.splitlines()
assert lines[0] == "PR 5건이 나를 기다린다" and len(lines) == 6
assert lines[1].startswith("1. [리뷰 요청] acme/widgets#1 Change 1 — @alice · 3일 · ±50 · 남은 리뷰어가 나뿐, 사람 작성")
assert p.body({**plan, "new": []}) == ""

# precheck: 0 with new items, 1 while a run holds plan.json, 1 after commit, 2 without config.
assert p.precheck(Fake(found), None, state, NOW)[0] == p.EXIT_ERROR
assert p.precheck(Fake(found), cfg, state, NOW)[0] == p.EXIT_NEW
p.write_json(state / "plan.json", plan)
assert p.precheck(Fake(found + [pr(12, reqs=[user(ME)])]), cfg, state, NOW + 60)[0] == p.EXIT_NOTHING, \
    "a run between plan and commit is not overlapped, even when something else arrived"
assert p.precheck(Fake(found), cfg, state, NOW + 61 * 60)[0] == p.EXIT_NEW, "a dead run's plan stops blocking"

# commit records exactly what plan delivered; the same (repo, N, head) is not sent twice.
assert p.commit(state, cfg, NOW) == (5, [])
assert not (state / "plan.json").exists()
ledger = p.read_json(state / "ledger.json", {})
assert set(ledger) == {i["key"] for i in plan["new"]}
assert p.precheck(Fake(found), cfg, state, NOW + 120)[0] == p.EXIT_NOTHING
again = p.compute(Fake(found), cfg, ledger, NOW)
assert again["new"] == [] and len(again["known"]) == 5

# The owner reviewed #1 at h1; it left the search. New commits re-surface it once, then never at that head.
rest = [x for x in found if x["number"] != 1]
pushed = pr(1, reqs=[], head="h1b", reviewed="h1", decision="APPROVED")
back = p.compute(Fake(rest, [pushed]), cfg, ledger, NOW)
assert [(i["number"], i["reason"], i["key"]) for i in back["new"]] == [(1, "new_commits", "acme/widgets#1@h1b")], back["new"]
assert p.body(back).splitlines()[1].startswith("1. [새 커밋, 재요청 없음] acme/widgets#1")
p.write_json(state / "plan.json", back)
p.commit(state, cfg, NOW)
ledger = p.read_json(state / "ledger.json", {})
assert p.compute(Fake(rest, [pushed]), cfg, ledger, NOW)["new"] == []
# Reviewed at the current head, merged, or never reviewed by the owner (request withdrawn): nothing.
for gone in (pr(1, head="h1b", reviewed="h1b"), pr(1, head="h1c", reviewed="h1", state="MERGED"), pr(1, head="h1c")):
    assert p.compute(Fake(rest, [gone]), cfg, ledger, NOW)["new"] == [], gone

# The ledger forgets entries older than keep_days, unless the PR is still showing up (known).
still = "acme/widgets#2@h2"
p.write_json(state / "plan.json", {"run": NOW + 59 * DAY, "new": [], "known": [still]})
p.commit(state, cfg, NOW + 59 * DAY)
p.write_json(state / "plan.json", {"run": NOW + 61 * DAY, "new": []})
p.commit(state, cfg, NOW + 61 * DAY)
assert list(p.read_json(state / "ledger.json", {})) == [still]

# A kind without a collector fails loudly instead of silently sending nothing.
assert set(p.COLLECTORS) < set(p.KINDS)
try:
    p.compute(Fake([]), {**cfg, "kinds": ["review_wait"]}, {}, NOW)
    raise AssertionError("unimplemented kind must raise")
except RuntimeError as e:
    assert "review_wait" in str(e)

# unreleased: the owner's merges in a declared repo within lookback_days; the dev revision decides the action, not membership.
def merged(n, repo="acme/widgets", hours=3, sha=None):
    return {**pr(n, repo=repo, author=ME, age=1), "state": "MERGED", "mergedAt": iso(NOW - hours * 3600),
            "mergeCommit": {"oid": sha or f"m{n}"}}

decl = {"dev_release": "auto-sync", "revision_cmd": "print-dev-sha", "logs": "dev log viewer",
        "verify": "verify-widgets", "lookback_days": 1, "orca_repo": "name:widgets"}
rcfg = {**cfg, "kinds": ["review_request", "unreleased"], "deploy": {"acme/widgets": decl}, "dispatch_run": "run_x",
        "release_approval": "dev 배포 승인됨(테스트)"}
fake = Fake([pr(1, reqs=[user(ME)])], merged=[merged(20), merged(21)], deployed="d0", live={"m20"})
both = p.compute(fake, rcfg, {}, NOW)
rel = [i for i in both["new"] if i["kind"] == "unreleased"]
assert [(i["number"], i["deployed"], i["merge_commit"], i["key"]) for i in rel] == \
    [(20, True, "m20", "acme/widgets#20@h20"), (21, False, "m21", "acme/widgets#21@h21")], rel
assert [i["kind"] for i in both["new"]][0] == "review_request"
q = [x for x in fake.queries if "is:merged" in x][0]
assert f"author:{ME}" in q and "repo:acme/widgets " in q and "merged:>=2027-01-14T08:00:00Z" in q, q
lines = p.body({**both, "new": rel}).splitlines()
assert lines[1].startswith("1. [dev 확인 전] acme/widgets#20 Change 20") and "dev 에 반영됨, 동작 확인 전" in lines[1]
assert "dev 에 아직 없음" in lines[2]

# Nothing declared, or everything already handed on: no revision probe at all.
silent = Fake([], merged=[merged(20)], deployed=RuntimeError("must not run"))
assert p.compute(silent, {**rcfg, "deploy": {}}, {}, NOW)["new"] == []
assert p.compute(silent, rcfg, {"acme/widgets#20@h20": {"at": NOW, "kind": "unreleased"}}, NOW)["new"] == []

# A probe that fails still hands the merge on, flagged, so it cannot age out of lookback_days unseen.
broke = p.compute(Fake([pr(1, reqs=[user(ME)])], merged=[merged(20)], deployed=RuntimeError("exit 1")), rcfg, {}, NOW)
assert [(i["kind"], i.get("deployed")) for i in broke["new"]] == [("review_request", None), ("unreleased", None)]
assert broke["new"][1]["why"] == ["dev 리비전 확인 실패: exit 1"] and "dev 리비전 확인 실패" in p.body(broke)
assert "dev 리비전을 읽지 못함" in p.dispatch_cmds(broke, rcfg)[0][4]
assert "revision_cmd 없음" in p.compute(Fake([], merged=[merged(20)], deployed="d0"),
                                        {**rcfg, "deploy": {"acme/widgets": {}}}, {}, NOW)["new"][0]["why"][0]

# The real probes: revision_cmd through the shell, containment through gh's compare status.
gh_dir = pathlib.Path(tempfile.mkdtemp())
cmp_gh = gh_dir / "gh"
cmp_gh.write_text('#!/bin/sh\ncase "$2" in *m20...d0) echo ahead;; *m21...d0) echo behind;; *same...d0) echo identical;; *) echo boom >&2; exit 1;; esac\n')
cmp_gh.chmod(0o755)
p.GH = str(cmp_gh)
real = p.GitHub()
assert [real.contains("acme/widgets", c, "d0") for c in ("m20", "m21", "same")] == [True, False, True]
try:  # gh failing (bad sha, lost access) is an error, not "not deployed"
    real.contains("acme/widgets", "unknown", "d0")
    raise AssertionError("a failed compare must raise")
except RuntimeError as e:
    assert "boom" in str(e)
assert real.revision("printf 'abc123\\n'") == "abc123"
for bad in ("exit 3", "true"):
    try:
        real.revision(bad)
        raise AssertionError(f"{bad} must raise")
    except RuntimeError:
        pass

# dispatch: one worker-start per repo; the brief carries the procedure, the declaration and the approval.
two = {**rcfg["deploy"], "acme/gadgets": {"revision_cmd": "x"}}
plan2 = p.compute(Fake([], merged=[merged(20), merged(21), merged(30, repo="acme/gadgets")], deployed="d0", live={"m20"}),
                  {**rcfg, "deploy": two}, {}, NOW)
cmds = p.dispatch_cmds(plan2, {**rcfg, "deploy": two})
assert [c[c.index("--repo") + 1] for c in cmds] == ["name:gadgets", "name:widgets"]
w = cmds[1]
assert w[:3] == ["orca", "orchestration", "worker-start"] and w[w.index("--run") + 1] == "run_x"
assert w[w.index("--worktree") + 1] == "new-top-level" and w[w.index("--agent") + 1] == "claude"
spec = w[w.index("--spec") + 1]
for want in ("post-merge-release.md", "#20 ", "merge m20", "dev 에 반영됨", "#21 ", "dev(d0) 에 아직 없음", "- revision_cmd: print-dev-sha",
             "- verify: verify-widgets", "승인: dev 배포 승인됨(테스트)", "사람만: 프로덕션", "다시 시도하지 않는다"):
    assert want in spec, want
assert pathlib.Path(spec.split("절차: ")[1].split(" ")[0]).exists(), "the brief points at a reference that exists"
assert "--run" not in p.dispatch_cmds(plan2, {**rcfg, "deploy": two, "dispatch_run": None})[0]
assert "승인이 없다" in p.dispatch_cmds(plan2, {**rcfg, "deploy": two, "release_approval": None})[0][4]
assert p.dispatch_cmds(plan, rcfg) == [], "review requests are messaged, never dispatched"

# CLI end to end with a fake gh: exit codes and the --body output.
tmp = pathlib.Path(tempfile.mkdtemp())
fake_gh = tmp / "gh"
fixture = tmp / "search.json"
fixture.write_text(json.dumps({"data": {"search": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": found}}}))
fake_gh.write_text(f"""#!/bin/sh
body=$(cat)
case "$body" in *"query{{viewer"*) echo '{{"data":{{"viewer":{{"login":"{ME}"}}}}}}';; *search*) cat {shlex.quote(str(fixture))};;
  *) echo '{{"data":{{"nodes":[null]}},"errors":[{{"message":"Could not resolve to a node"}}]}}'; exit 1;; esac
""")
fake_gh.chmod(fake_gh.stat().st_mode | stat.S_IEXEC)
conf = tmp / "agent-skills.json"
conf.write_text(json.dumps({"prs": {"self_bots": ["owner-bot"], "ignore_bots": ["noisy"], "exclude_repos": ["acme/legacy"]}}))
env = {**os.environ, "AGENT_SKILLS_GH": str(fake_gh), "AGENT_SKILLS_STATE": str(tmp / "state")}
script = str(pathlib.Path(p.__file__))
run = lambda *a: subprocess.run([sys.executable, script, *a, "--config", str(conf)], env=env, capture_output=True, text=True)
r0 = run("precheck"); assert r0.returncode == 0, r0
diag = run("plan")
assert diag.returncode == 0 and len(json.loads(diag.stdout)["new"]) == 5
assert not (tmp / "state" / "prs" / "plan.json").exists(), "the diagnostic plan takes no lock"
out = run("plan", "--body")
assert out.returncode == 0 and out.stdout.startswith("PR 5건이 나를 기다린다"), out
assert run("commit").returncode == 0
r = run("precheck")
assert r.returncode == 1 and "새 항목 없음" in r.stderr, r
assert run("plan", "--body").stdout.strip() == ""
assert not (tmp / "state" / "prs" / "plan.json").exists(), "a plan with nothing new holds no lock"
# One ledger PR GitHub can no longer resolve (deleted repo, lost access) must not fail the whole lookup.
p.GH = str(fake_gh)
assert p.GitHub().nodes(["PR_gone"], ME) == []
# dispatch prints one runnable line per due repo from plan.json, and nothing without a plan.
assert run("dispatch").returncode != 0
conf.write_text(json.dumps({"prs": {"deploy": {"acme/widgets": decl}}}))
p.write_json(tmp / "state" / "prs" / "plan.json", plan2)
d = run("dispatch")
printed = d.stdout.splitlines()
assert d.returncode == 0 and len(printed) == 1 and shlex.split(printed[0])[:3] == ["orca", "orchestration", "worker-start"], d
assert printed[0].endswith(f'--spec "$(cat {tmp / "state" / "prs" / "brief-0.txt"})"')
assert "#20 " in (tmp / "state" / "prs" / "brief-0.txt").read_text()
(tmp / "state" / "prs" / "plan.json").unlink()
broken = {**env, "AGENT_SKILLS_GH": "/nonexistent/gh"}
assert subprocess.run([sys.executable, script, "precheck", "--config", str(conf)], env=broken, capture_output=True).returncode == 2
assert subprocess.run([sys.executable, script, "precheck", "--config", str(tmp / "none.json")], env=env, capture_output=True).returncode == 2

# schedule: prints the create command with the precheck wired in; --write is the only path that creates.
line = p.schedule_cmd(pathlib.Path("/repo/skills/watch-prs/scripts/prs.py"), {**cfg, "inbox_to": "term_x", "timezone": "Asia/Tokyo"}, "path:/repo")
assert line[:3] == ["orca", "automations", "create"]
assert line[line.index("--trigger") + 1] == "*/15 * * * *"
assert line[line.index("--timezone") + 1] == "Asia/Tokyo"
assert line[line.index("--precheck") + 1] == "python3 /repo/skills/watch-prs/scripts/prs.py precheck"
prompt = line[line.index("--prompt") + 1]
assert "plan --body --kinds review_request`" in prompt and "--to term_x" in prompt
assert "보내기가 성공했을 때만 `python3 /repo/skills/watch-prs/scripts/prs.py commit --kinds review_request`" in prompt
assert "dispatch" not in prompt, "review_request alone has no step after the message"
# A scheduled session may refuse a command that starts with `env` or a `VAR=` assignment, so neither appears.
no_env = lambda argv: "env " not in " ".join(argv) and not re.search(r"(^|[\s`])[A-Z_]{2,}=", " ".join(argv))
assert no_env(line), line
# Each configured kind brings its step: unreleased adds the dispatch lines, and commit covers both kinds.
line = p.schedule_cmd(pathlib.Path("/r/prs.py"), {**rcfg, "inbox_to": "term_x"}, "path:/r")
prompt = line[line.index("--prompt") + 1]
assert no_env(line), line
assert re.search(r"보내기가 성공했을 때만 `python3 /r/prs.py dispatch` 를 실행해", prompt), "a failed send must not start workers"
assert prompt.index("dispatch") < prompt.index("commit --kinds review_request,unreleased` 을") and "다시 시도하지 않고" in prompt
# A kind with no step is refused at schedule time rather than committed behind a prompt that never acts on it.
saved, p.STEPS = p.STEPS, {k: v for k, v in p.STEPS.items() if k != "unreleased"}
try:
    p.schedule_cmd(pathlib.Path("/r/prs.py"), rcfg, "path:/r")
    raise AssertionError("schedule accepted a kind without a step")
except SystemExit as e:
    assert "unreleased" in str(e), e
finally:
    p.STEPS = saved
# A state folder off the default goes in as a flag after the subcommand, not through the environment.
dry = run("schedule")
assert dry.returncode == 0 and dry.stdout.startswith("orca automations create") and "--write" in dry.stdout
assert f"precheck --state {tmp / 'state'}" in dry.stdout and "AGENT_SKILLS_STATE" not in dry.stdout, dry.stdout
# commit --kinds records only the kinds the round's prompt acts on; the rest come back next round, named in the body.
p.write_json(tmp / "state" / "prs" / "plan.json", plan2)
c = run("commit", "--kinds", "review_request")
assert c.returncode == 0 and "commit: 0 items recorded" in c.stdout and "acme/widgets#20" in c.stderr, c
assert not any(k.startswith("acme/widgets#20") for k in p.read_json(tmp / "state" / "prs" / "ledger.json", {})), "unreleased must not be recorded"
assert "단계가 없는 kind: unreleased" in p.body(plan2, ["review_request"]) and "단계가 없는" not in p.body(plan2, None)

print("test_prs: ok")
