"""Rules of prs.py: who counts as a personal request, ranking, the ledger, precheck exit codes, schedule.
Run: python3 test_prs.py"""
import json, os, pathlib, shlex, stat, subprocess, sys, tempfile
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
       decision="REVIEW_REQUIRED", head=None, reviewed=None, state="OPEN", title=None, files=()):
    return {"id": f"PR_{repo}_{n}", "number": n, "title": title or f"Change {n}", "url": f"https://github.com/{repo}/pull/{n}",
            "isDraft": draft, "createdAt": iso(NOW - age * DAY), "updatedAt": iso(NOW - idle * DAY),
            "headRefOid": head or f"h{n}", "additions": size, "deletions": 0, "reviewDecision": decision, "state": state,
            "repository": {"nameWithOwner": repo}, "author": {"__typename": kind, "login": author},
            "reviewRequests": {"nodes": list(reqs)}, "changedFiles": len(files),
            "files": {"nodes": [{"path": x} for x in files]},
            "reviews": {"nodes": [{"commit": {"oid": reviewed}}] if reviewed else []}}


class Fake:
    def __init__(self, search, nodes=(), gated=()):
        self.found, self.by_id, self.gated = list(search), {n["id"]: n for n in nodes}, list(gated)

    def viewer(self):
        return ME

    def search(self, q, me):
        assert "review-requested:@me" in q and me == ME
        if "author:app/" in q:
            return [x for x in self.gated if q.endswith("author:app/" + p.bot_name(x["author"]["login"]))]
        return self.found

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
assert p.commit(state, cfg, NOW) == 5
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

# Only review_request has a collector; a later kind fails loudly instead of silently sending nothing.
assert set(p.COLLECTORS) < set(p.KINDS)
try:
    p.compute(Fake([]), {**cfg, "kinds": ["review_wait"]}, {}, NOW)
    raise AssertionError("unimplemented kind must raise")
except RuntimeError as e:
    assert "review_wait" in str(e)

# ownergate: app PRs a gate names that still need an approval; never a person's PR.
gate = {"name": "access", "authors": ["grant-bot[bot]"], "repos": ["acme/access"], "title": r"^\[Auto\] ", "prod_paths": ["deploy/"]}
gcfg = {**cfg, "kinds": ["review_request", "ownergate"], "ownergate": [gate], "ignore_bots": ["grant-bot"]}
A = dict(repo="acme/access", author="grant-bot", kind="Bot", reqs=[team("sec")])
gated = [
    pr(1, **{**A, "reqs": [user(ME)]}, title="[Auto] Add access for 1 user", files=["members/a.json"], age=2),
    pr(2, **A, title="[Auto] Add access for 2 users", decision="APPROVED"),
    pr(3, repo="acme/access", author="grant-bot", kind="User", title="[Auto] Add access by hand"),
    pr(4, **A, title="Bump version"),
    pr(5, **A, title="[Auto] Rotate role", files=["deploy/prod.yml", "members/b.json"], age=9),
    pr(6, **{**A, "repo": "acme/other"}, title="[Auto] Add access elsewhere"),
]
allow_rules = []
p.automode_allow = lambda: allow_rules
owner_only = p.compute(Fake([gated[0]], gated=gated), gcfg, {}, NOW)
assert [(i["number"], i["kind"], i["run_by"], i["prod"]) for i in owner_only["new"]] == \
    [(1, "ownergate", "owner", False), (5, "ownergate", "owner", True)], owner_only["new"]
# #1 is also in the review search, where ignore_bots drops it; the gate still takes it.
assert {e["pr"]: e["why"] for e in owner_only["excluded"]} == {"acme/access#1": "무시하는 봇이 작성",
    "acme/access#2": "승인 대기 아님", "acme/access#3": "사람 작성", "acme/access#4": "게이트 조건 밖",
    "acme/other#6": "게이트 조건 밖"}, owner_only["excluded"]
g1 = owner_only["new"][0]
assert g1["command"] == ('[ "$(gh pr view 1 -R acme/access --json headRefOid -q .headRefOid)" = h1 ] && '
                         'gh api -X POST repos/acme/access/pulls/1/reviews -f event=APPROVE -f commit_id=h1 || echo "not approved: head moved"')
assert g1["diff"] == "파일 1개 ±10: members/a.json" and g1["rule_draft"].startswith("allow: Scheduled watch-prs round approving access PRs")
gtext = p.body(owner_only).splitlines()
assert gtext[1].startswith("1. [소유자 승인] acme/access#1 [Auto] Add access for 1 user — @grant-bot · 2일 · 파일 1개 ±10") \
    and gtext[1].endswith("프로덕션 배포: 아니오 — https://github.com/acme/access/pull/1"), gtext[1]
assert gtext[2] == "   ! " + g1["command"]
assert "프로덕션 배포: 예" in gtext[3] and gtext[4].startswith("   ! ")
assert gtext[5].startswith("없는 규칙") and gtext[6] == "- " + g1["rule_draft"] and "프로덕션 배포(또는 모름)" in gtext[7]

# The same head from two collectors is one item; the later kind in KINDS wins.
both = p.compute(Fake([gated[0]], gated=gated[:1]), {**gcfg, "ignore_bots": []}, {}, NOW)
assert [(i["key"], i["kind"]) for i in both["new"]] == [("acme/access#1@h1", "ownergate")], both["new"]
# A head already sent as a review request comes back once as an owner gate, then not again.
was_review = {"acme/access#1@h1": {"at": NOW, "kind": "review_request", "id": "x"}}
assert [i["key"] for i in p.compute(Fake([], gated=gated[:1]), gcfg, was_review, NOW)["new"]] == ["acme/access#1@h1"]
assert p.compute(Fake([], gated=gated[:1]), gcfg, {"acme/access#1@h1": {"at": NOW, "kind": "ownergate"}}, NOW)["new"] == []

# Production: more changed files than were read is unknown, which stays with the owner.
assert p.deploys_prod({**gated[0], "changedFiles": 250}, gate) is None
assert p.deploys_prod(gated[0], {"prod": False}) is False and p.deploys_prod(gated[0], {}) is None
# Two gates on one author: a title only the second matches is still an item; the stricter production answer wins.
prd = pr(7, **A, title="[prd] Rotate", files=["members/c.json"])
two = {**gcfg, "ownergate": [gate, {"name": "prd", "authors": ["grant-bot"], "title": r"^\[(prd|Auto)\]", "prod": True}]}
got = p.compute(Fake([], gated=[prd, gated[0]]), two, {}, NOW)["new"]
assert [(i["number"], i["gate"], i["prod"]) for i in got] == [(1, "access", True), (7, "prd", True)], got

# An allow rule that starts with the gate's phrase moves the non-production item to the round; production stays.
gate["allow_rule"] = "Scheduled watch-prs round approving access PRs"
allow_rules.append("Scheduled watch-prs round approving access PRs: in a turn started by ...")
covered = p.compute(Fake([], gated=gated), gcfg, {}, NOW)
assert [(i["number"], i["run_by"]) for i in covered["new"]] == [(1, "round"), (5, "owner")]
assert p.body(covered).splitlines()[2] == "   규칙이 덮음, 이 회차가 실행: " + g1["command"]
allow_rules[0] = "Other rule. Scheduled watch-prs round approving access PRs"
assert p.compute(Fake([], gated=gated), gcfg, {}, NOW)["new"][0]["run_by"] == "owner", "the phrase must open the rule"
allow_rules.clear()
assert p.compute(Fake([], gated=gated), gcfg, {}, NOW)["new"][0]["run_by"] == "owner", "a rule that went away covers nothing"
allow_rules.append("Scheduled watch-prs round approving access PRs: in a turn started by ...")

# A bad title regex is "could not check" (2), not "nothing new" (1).
assert p.precheck(Fake([], gated=gated), {**gcfg, "ownergate": [{**gate, "title": "["}]}, pathlib.Path(tempfile.mkdtemp()), NOW)[0] == p.EXIT_ERROR

# deliver: owner steps go on the dashboard inbox with the ledger key before the send; a failed add is in the message.
calls = pathlib.Path(tempfile.mkdtemp()) / "calls"
fake_dash = calls.parent / "orch-dash"
fake_dash.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n")
fake_dash.chmod(0o755)
p.ORCH_DASH = str(fake_dash)
gstate = pathlib.Path(tempfile.mkdtemp())
p.write_json(gstate / "plan.json", covered)
text = p.deliver(gstate, covered)
added = calls.read_text().splitlines()
assert len(added) == 1 and added[0].startswith("inbox add --type run_command --title acme/access#5 승인: [Auto] Rotate role")
assert added[0].endswith("--key prs:acme/access#5@h5") and "--command " + covered["new"][1]["command"] in added[0]
assert text == p.body(covered) == (gstate / "body.txt").read_text()
dead = calls.parent / "dead"
dead.write_text("#!/bin/sh\nexit 3\n")
dead.chmod(0o755)
p.ORCH_DASH = str(dead)
assert p.deliver(gstate, covered).splitlines()[-1] == "대시보드 inbox 등록 실패 acme/access#5: exit 3"
p.ORCH_DASH = str(fake_dash)
p.deliver(gstate, covered)
# A title that carries the marker text is not a round step: steps come from plan.json only.
assert [i["key"] for i in covered["new"] if i["run_by"] == "round"] == ["acme/access#1@h1"]
# result: a step that went through is noted; one that did not goes to the owner and the inbox.
assert p.result(gstate, "acme/access#1@h1", False, "classifier refused").startswith("회차 실행 안 됨(classifier refused), 소유자 몫: acme/access#1")
assert p.read_json(gstate / "plan.json", {})["new"][0]["run_by"] == "owner"
assert calls.read_text().splitlines()[-1].endswith("--key prs:acme/access#1@h1")
assert (gstate / "body.txt").read_text().endswith("   ! " + g1["command"])
try:
    p.result(gstate, "acme/access#1@h1", True)
    raise AssertionError("a step already handed to the owner is not a round step")
except SystemExit:
    pass
assert p.commit(gstate, gcfg, NOW) == 2
assert p.read_json(gstate / "ledger.json", {})["acme/access#5@h5"]["kind"] == "ownergate"

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
broken = {**env, "AGENT_SKILLS_GH": "/nonexistent/gh"}
assert subprocess.run([sys.executable, script, "precheck", "--config", str(conf)], env=broken, capture_output=True).returncode == 2
assert subprocess.run([sys.executable, script, "precheck", "--config", str(tmp / "none.json")], env=env, capture_output=True).returncode == 2

# schedule: prints the create command with the precheck wired in; --write is the only path that creates.
line = p.schedule_cmd(pathlib.Path("/repo/skills/watch-prs/scripts/prs.py"), {**cfg, "inbox_to": "term_x", "timezone": "Asia/Tokyo"}, "path:/repo")
assert line[:3] == ["orca", "automations", "create"]
assert line[line.index("--trigger") + 1] == "*/15 * * * *"
assert line[line.index("--timezone") + 1] == "Asia/Tokyo"
assert line[line.index("--precheck") + 1].endswith("/repo/skills/watch-prs/scripts/prs.py precheck")
prompt = line[line.index("--prompt") + 1]
assert all(w in prompt for w in ("plan --body", "--to term_x", "commit", " round", "result <키> failed"))
dry = run("schedule")
assert dry.returncode == 0 and dry.stdout.startswith("orca automations create") and "--write" in dry.stdout

print("test_prs: ok")
