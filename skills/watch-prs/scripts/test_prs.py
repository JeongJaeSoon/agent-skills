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
       decision="REVIEW_REQUIRED", head=None, reviewed=None, state="OPEN", title=None, files=(), asker=("User", "carol")):
    """asker: who made each User review request (the timeline's ReviewRequestedEvent actor)."""
    return {"id": f"PR_{repo}_{n}", "number": n, "title": title or f"Change {n}", "url": f"https://github.com/{repo}/pull/{n}",
            "isDraft": draft, "createdAt": iso(NOW - age * DAY), "updatedAt": iso(NOW - idle * DAY),
            "headRefOid": head or f"h{n}", "additions": size, "deletions": 0, "reviewDecision": decision, "state": state,
            "repository": {"nameWithOwner": repo}, "author": {"__typename": kind, "login": author},
            "reviewRequests": {"nodes": list(reqs)}, "changedFiles": len(files),
            "files": {"nodes": [{"path": x} for x in files]},
            "reviews": {"nodes": [{"commit": {"oid": reviewed}}] if reviewed else []},
            "timelineItems": {"nodes": [{"actor": {"__typename": asker[0], "login": asker[1]},
                                         "requestedReviewer": r["requestedReviewer"]}
                                        for r in reqs if r["requestedReviewer"]["__typename"] == "User"]}}


class Fake:
    def __init__(self, search, nodes=(), merged=(), deployed=None, live=(), mine=(), gated=()):
        self.found, self.by_id, self.own, self.gated = list(search), {n["id"]: n for n in nodes}, list(mine), list(gated)
        self.merged, self.deployed, self.live, self.queries = list(merged), deployed, set(live), []

    def mine(self, self_bots):
        return self.own

    def viewer(self):
        return ME

    def search(self, q, me):
        assert me == ME
        self.queries.append(q)
        if "is:merged" in q:
            return [m for m in self.merged if f"repo:{m['repository']['nameWithOwner']} " in q]
        assert "review-requested:@me" in q
        if "author:app/" in q:
            return [x for x in self.gated if q.endswith("author:app/" + p.bot_name(x["author"]["login"]))]
        return self.found

    def revision(self, cmd):
        if isinstance(self.deployed, Exception):
            raise self.deployed
        return self.deployed

    def contains(self, repo, commit, deployed):
        return commit in self.live

    def nodes(self, ids, me):
        return [self.by_id[i] for i in ids if i in self.by_id]

    def pr(self, ref, me):
        repo, n = ref.lower().replace("acme/old-widgets", "acme/widgets").split("#")  # a renamed repo resolves
        return {x["id"]: x for x in self.found}.get(f"PR_{repo}_{n}") or self.by_id.get(f"PR_{repo}_{n}")


state = pathlib.Path(tempfile.mkdtemp()) / "prs"
cfg = {**p.DEFAULTS, "self_bots": ["owner-bot"], "ignore_bots": ["noisy[bot]"], "exclude_repos": ["acme/legacy"],
       "mentions_seen": str(state.parent / "no-mentions.json")}

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

# A kind without a collector fails loudly instead of silently sending nothing.
assert set(p.COLLECTORS) == set(p.KINDS)
# A head comes back once under a later kind, never under an earlier one: no ping-pong between two kinds.
assert not p.delivered({"k": {"kind": "ready_to_merge"}}, "k", "unreleased")
assert not p.delivered({"k": {"kind": "review_request"}}, "k", "ownergate")
assert p.delivered({"k": {"kind": "ownergate"}}, "k", "review_request") and p.delivered({"k": {}}, "k", "unreleased")
assert p.delivered({"k": {"kind": "retired_kind"}}, "k", "review_request"), "an unknown kind in the ledger is sent, not a crash"
try:
    p.compute(Fake([]), {**cfg, "kinds": ["merge_queue"]}, {}, NOW)
    raise AssertionError("unimplemented kind must raise")
except RuntimeError as e:
    assert "merge_queue" in str(e)

# ownergate: app PRs a gate names that still need an approval; never a person's PR.
gate = {"name": "access", "authors": ["grant-bot[bot]"], "repos": ["acme/access"], "title": r"^\[Auto\] ", "prod_paths": ["deploy/"]}
gcfg = {**cfg, "kinds": ["review_request", "ownergate"], "ownergate": [gate], "ignore_bots": ["grant-bot"]}
A = dict(repo="acme/access", author="grant-bot", kind="Bot", reqs=[user(ME), team("sec")])
gated = [
    pr(1, **{**A, "reqs": [user(ME)]}, title="[Auto] Add access for 1 user", files=["members/a.json"], age=2),
    pr(2, **A, title="[Auto] Add access for 2 users", decision="APPROVED"),
    pr(3, repo="acme/access", author="grant-bot", kind="User", reqs=[user(ME)], title="[Auto] Add access by hand"),
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
                         'gh api -X POST repos/acme/access/pulls/1/reviews -f event=APPROVE -f commit_id=h1 '
                         '|| { echo "not approved: head moved or the call failed"; false; }')
# The line fails (non-zero) when the head moved or the API call failed, so nobody records it as approved.
for head_now, api_rc, want in (("h1", 0, 0), ("h9", 0, 1), ("h1", 1, 1)):
    shim = pathlib.Path(tempfile.mkdtemp())
    (shim / "gh").write_text(f'#!/bin/sh\n[ "$1" = pr ] && {{ echo {head_now}; exit 0; }}\nexit {api_rc}\n')
    (shim / "gh").chmod(0o755)
    rc = subprocess.run(["sh", "-c", g1["command"]], env={**os.environ, "PATH": f"{shim}:{os.environ['PATH']}"},
                        capture_output=True).returncode
    assert (rc != 0) == bool(want), (head_now, api_rc, rc)
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
assert p.compute(Fake([], gated=gated), {**gcfg, "ownergate": [{**gate, "allow_rule": "Scheduled"}]}, {}, NOW)["new"][0]["run_by"] \
    == "owner", "a phrase shorter than ALLOW_RULE_MIN covers nothing"

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
# verify rebuilds body.txt from plan.json, so the failure has to live there too.
assert p.body(p.read_json(gstate / "plan.json", {})).splitlines()[-1] == "대시보드 inbox 등록 실패 acme/access#5: exit 3"
p.ORCH_DASH = str(fake_dash)
p.deliver(gstate, covered)
# A title that carries the marker text is not a round step: steps come from plan.json only.
assert [i["key"] for i in covered["new"] if i["run_by"] == "round"] == ["acme/access#1@h1"]
# result: a step that went through is noted; one that did not goes to the owner and the inbox.
try:
    p.result(gstate, "acme/access#1@h1", True)
    raise AssertionError("a step round never handed out has no result")
except SystemExit:
    pass
assert p.round_steps(gstate) == ["acme/access#1@h1\t" + g1["command"]]
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

# deliver marks each round step before the run can reach it. A step with no result (round never called, a crash,
# a refused `result`) is not committed, and the next plan gives it to the owner instead of running it again.
for reach_round in (False, True):
    rstate = pathlib.Path(tempfile.mkdtemp())
    rplan = p.compute(Fake([], gated=gated), gcfg, {}, NOW)
    p.write_json(rstate / "plan.json", rplan)
    p.deliver(rstate, rplan)
    if reach_round:
        assert p.round_steps(rstate) == ["acme/access#1@h1\t" + g1["command"]]
        assert p.round_steps(rstate) == [], "a step is handed out once per run"
    p.commit(rstate, gcfg, NOW)
    rledger = p.read_json(rstate / "ledger.json", {})
    assert "acme/access#1@h1" not in rledger and "attempt:acme/access#1@h1" in rledger
    again = p.compute(Fake([], gated=gated), gcfg, rledger, NOW)
    assert [(i["key"], i["run_by"]) for i in again["new"]] == [("acme/access#1@h1", "owner")], again["new"]
# A step that went through is committed and not sent again.
rstate = pathlib.Path(tempfile.mkdtemp())
rplan = p.compute(Fake([], gated=gated), gcfg, {}, NOW)
p.write_json(rstate / "plan.json", rplan)
p.deliver(rstate, rplan)
p.round_steps(rstate)
assert p.result(rstate, "acme/access#1@h1", True) == "회차가 실행함: acme/access#1"
# A non-boolean prod is a config error (precheck exit 2), never "not production".
for bad in ({**gate, "prod": "true"}, {**gate, "title": "("}):  # checked even when no PR matches
    for prs_found in (gated, []):
        assert p.precheck(Fake([], gated=prs_found), {**gcfg, "ownergate": [bad]}, pathlib.Path(tempfile.mkdtemp()), NOW)[0] == p.EXIT_ERROR
# plan.json written but deliver never marked the step (it failed in between): round hands out nothing.
lost = pathlib.Path(tempfile.mkdtemp())
p.write_json(lost / "plan.json", rplan)
assert p.round_steps(lost) == []
p.commit(rstate, gcfg, NOW)
assert p.compute(Fake([], gated=gated), gcfg, p.read_json(rstate / "ledger.json", {}), NOW)["new"] == []

# Only a person asking the owner personally makes an item, in the gate too: a team or CODEOWNERS request, or a
# request the app made itself (even naming the owner as a User), is never one. So a gate cannot fire for an app
# that requests its own reviewers.
by_app = pr(7, **A, title="[Auto] Add access for 7 users", files=["members/e.json"], asker=("Bot", "grant-bot"))
via_team = pr(8, **{**A, "reqs": [team("sec"), user(ME, code_owner=True)]}, title="[Auto] Add access for 8 users")
held = p.compute(Fake([], gated=[gated[0], by_app, via_team]), gcfg, {}, NOW)
assert [i["number"] for i in held["new"]] == [1], held["new"]
assert {e["pr"]: e["why"] for e in held["excluded"] if e["pr"] != "acme/access#1"} == {
    "acme/access#7": "사람이 아닌 계정의 요청", "acme/access#8": "팀·CODEOWNERS 요청뿐"}, held["excluded"]
# Slack review requests from watch-mentions' seen ledger: one entry per PR, permalinks attached.
seen_file = state.parent / "mentions-seen.json"
link = lambda n: f"https://example.test/archives/C0FAKE/p{n}"
row = lambda ref, n, category="review": {"at": NOW, "category": category, "permalink": link(n), "pr": ref}
p.write_json(seen_file, {
    "C0FAKE/1": row("acme/widgets#1", 1), "C0FAKE/2": row("Acme/Widgets#1", 2),   # GitHub lists #1 too: merged
    "C0FAKE/11": row("acme/old-widgets#1", 11),                                    # #1 under its old repo name
    "C0FAKE/3": row("acme/widgets#5", 3),                                          # team-only on GitHub: Slack lists it
    "C0FAKE/4": row("acme/widgets#20", 4),                                         # not in the search: fetched
    "C0FAKE/5": row("acme/widgets#21", 5), "C0FAKE/6": row("acme/widgets#22", 6),  # merged PR, reviewed at head
    "C0FAKE/7": row("acme/widgets#23", 7), "C0FAKE/8": row("acme/gone#1", 8),      # my own PR, unreadable
    "C0FAKE/9": row("acme/widgets#24", 9, "work"), "C0FAKE/10": {"at": NOW, "category": "review", "permalink": link(10)},
    "C0FAKE/12": {**row("acme/widgets#20", 12), "permalink": ["not", "a", "link"]},  # hand-edited row
})
extra = [pr(20, reqs=[], age=2), pr(21, reqs=[], state="MERGED"), pr(22, reqs=[], head="h22", reviewed="h22"),
         pr(23, author=ME, reqs=[]), pr(24, reqs=[])]
scfg = {**cfg, "mentions_seen": str(seen_file)}
mixed = p.compute(Fake(found, extra), scfg, {}, NOW)
by = {i["number"]: i for i in mixed["new"]}
assert sorted(by) == [1, 2, 3, 4, 5, 11, 20], sorted(by)
assert len(mixed["new"]) == len(by), "one entry per PR, whatever case or old name Slack used"
assert by[1]["source"] == "github+slack" and sorted(by[1]["slack"]) == sorted([link(1), link(2), link(11)])
assert by[1]["reason"] == "requested"
assert by[5]["source"] == by[20]["source"] == "slack" and by[20]["slack"] == [link(4)] and by[20]["key"] == "acme/widgets#20@h20"
assert by[2]["source"] == "github" and by[2]["slack"] == []
why = {e["pr"]: e["why"] for e in mixed["excluded"]}
assert why["acme/widgets#21"] == "Slack 요청, 닫힘" and why["acme/widgets#22"] == "Slack 요청, head 를 이미 리뷰함"
assert why["acme/widgets#23"] == "Slack 요청, 본인 PR" and why["acme/gone#1"] == "Slack 요청, PR 을 읽지 못함"
assert "acme/widgets#5" not in why and "acme/widgets#24" not in why and len(why) == len(mixed["excluded"])
p.write_json(seen_file, {"C0FAKE/1": row("acme/widgets#7", 1)})  # a draft GitHub already excluded: one reason, Slack's
assert [e for e in p.compute(Fake(found), scfg, {}, NOW)["excluded"] if e["pr"] == "acme/widgets#7"] == \
    [{"pr": "acme/widgets#7", "why": "Slack 요청, draft"}]
lines = {l.split("] ")[1].split(" ")[0]: l for l in p.body(mixed).splitlines()[1:]}
assert lines["acme/widgets#1"].endswith(f"— https://github.com/acme/widgets/pull/1 · Slack {link(11)} {link(1)} {link(2)}")
assert "[Slack 리뷰 요청] acme/widgets#20" in lines["acme/widgets#20"]
p.write_json(seen_file, {"C0FAKE/4": row("acme/widgets#20", 4)})
# A Slack-only item is new for precheck, and once sent its head is known like any other.
assert p.precheck(Fake([], extra), scfg, state.parent / "slack-state", NOW) == (p.EXIT_NEW, "새 항목 1건: acme/widgets#20@h20")
sent = {i["key"]: {"at": NOW, "kind": i["kind"], "id": i["id"]} for i in mixed["new"]}
assert p.compute(Fake(found, extra), scfg, sent, NOW)["new"] == []
# No seen ledger, or one that is not a JSON object: GitHub only, same plan as before.
seen_file.write_text("[]")
assert p.compute(Fake(found), scfg, {}, NOW)["new"] == plan["new"]

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
# Unreleased merges are not capped: they rank after older items, so a cap would starve them or split a repo's batch.
crowd = [pr(n, reqs=[user(ME)], age=30 + n) for n in range(40, 45)]
uncapped = p.compute(Fake(crowd, merged=[merged(20), merged(21)], deployed="d0", live={"m20"}), {**rcfg, "max_items": 2}, {}, NOW)
assert sorted(i["number"] for i in uncapped["new"] if i["kind"] == "unreleased") == [20, 21], uncapped["new"]
assert sum(i["kind"] == "review_request" for i in uncapped["new"]) == 2 and uncapped["more"] == 3

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
env = {**os.environ, "AGENT_SKILLS_GH": str(fake_gh), "AGENT_SKILLS_STATE": str(tmp / "state"), "AGENT_SKILLS_ORCH_DASH": p.ORCH_DASH}
# By default the Slack side is read from watch-mentions' state next to this one.
p.write_json(tmp / "state" / "mentions" / "seen.json", {"C0FAKE/1": row("acme/widgets#2", 1)})
script = str(pathlib.Path(p.__file__))
run = lambda *a: subprocess.run([sys.executable, script, *a, "--config", str(conf)], env=env, capture_output=True, text=True)
r0 = run("precheck"); assert r0.returncode == 0, r0
diag = run("plan")
assert diag.returncode == 0 and len(json.loads(diag.stdout)["new"]) == 5
assert not (tmp / "state" / "prs" / "plan.json").exists(), "the diagnostic plan takes no lock"
out = run("plan", "--body")
assert out.returncode == 0 and out.stdout.startswith("PR 5건이 나를 기다린다"), out
assert "acme/widgets#2 Change 2" in out.stdout and f"· Slack {link(1)}" in out.stdout, out.stdout
assert run("commit").returncode == 0
r = run("precheck")
assert r.returncode == 1 and "새 항목 없음" in r.stderr, r
assert run("plan", "--body").stdout.strip() == ""
assert not (tmp / "state" / "prs" / "plan.json").exists(), "a plan with nothing new holds no lock"
# One ledger PR GitHub can no longer resolve (deleted repo, lost access) must not fail the whole lookup.
p.GH = str(fake_gh)
assert p.GitHub().nodes(["PR_gone"], ME) == []
assert p.GitHub().pr("acme/gone#1", ME) is None
# dispatch prints one runnable line per due repo from plan.json, and nothing without a plan.
assert run("dispatch").returncode != 0
conf.write_text(json.dumps({"prs": {"deploy": {"acme/widgets": decl}}}))
p.write_json(tmp / "state" / "prs" / "plan.json", plan2)
d = run("dispatch")
printed = d.stdout.splitlines()
assert d.returncode == 0 and len(printed) == 1 and shlex.split(printed[0])[:3] == ["orca", "orchestration", "worker-start"], d
brief = tmp / "state" / "prs" / f"brief-{int(plan2['run'])}-0.txt"
assert printed[0].endswith(f'--spec "$(cat {brief})"'), "named by run, so the next round cannot overwrite it"
assert "#20 " in brief.read_text()
assert (tmp / "state" / "prs" / "dispatch.txt").read_text() == d.stdout.rstrip("\n")
(tmp / "state" / "prs" / "plan.json").unlink()
broken = {**env, "AGENT_SKILLS_GH": "/nonexistent/gh"}
assert subprocess.run([sys.executable, script, "precheck", "--config", str(conf)], env=broken, capture_output=True).returncode == 2
assert subprocess.run([sys.executable, script, "precheck", "--config", str(tmp / "none.json")], env=env, capture_output=True).returncode == 2

# schedule: prints the create command with the precheck wired in; --write is the only path that creates.
line = p.schedule_cmd(pathlib.Path("/repo/skills/watch-prs/scripts/prs.py"),
                     {**cfg, "inbox_to": "term_x", "timezone": "Asia/Tokyo"}, "path:/repo")
assert line[:3] == ["orca", "automations", "create"]
assert line[line.index("--trigger") + 1] == "*/15 * * * *"
assert line[line.index("--timezone") + 1] == "Asia/Tokyo"
assert line[line.index("--precheck") + 1] == "python3 /repo/skills/watch-prs/scripts/prs.py precheck"
prompt = line[line.index("--prompt") + 1]
# The round commands are exactly precheck, plan --body and commit: an owner's allow rule names those forms.
assert "`python3 /repo/skills/watch-prs/scripts/prs.py plan --body`" in prompt and "--to term_x" in prompt
assert "전송이 성공했을 때만 마지막에 `python3 /repo/skills/watch-prs/scripts/prs.py commit`" in prompt
assert "dispatch" not in prompt and "--kinds" not in prompt, "review_request alone has no step after the message"
# A scheduled session may refuse a command that starts with `env` or a `VAR=` assignment, so neither appears.
no_env = lambda argv: "env " not in " ".join(argv) and not re.search(r"(^|[\s`])[A-Z_]{2,}=", " ".join(argv))
assert no_env(line), line
# Each configured kind brings its step: unreleased adds the dispatch lines before commit.
line = p.schedule_cmd(pathlib.Path("/r/prs.py"), {**rcfg, "inbox_to": "term_x"}, "path:/r")
prompt = line[line.index("--prompt") + 1]
assert no_env(line), line
assert re.search(r"보내기가 성공했을 때만 `python3 /r/prs.py dispatch` 를 실행한다", prompt), "a failed send must not dispatch"
# A scheduled round cannot start a worker (consumer_fenced): it forwards the lines and the coordinator starts them.
assert "출력된 줄은 실행하지 않는다" in prompt and "릴리스 워커 <줄 수>건 시작 요청" in prompt
assert re.search(r'--body "\$\(cat \S*dispatch\.txt\)"', prompt), "lines go as a file so $(cat …) stays literal"
assert prompt.index("dispatch") < prompt.index("prs.py commit` 을")
assert "리뷰·코멘트·재요청·배포를 직접 하지 않는다" in prompt, "without review_wait the round writes nothing to GitHub"
# ownergate's round/result run and are recorded before the send, and start with python3 like the rest.
gated_line = p.schedule_cmd(pathlib.Path("/r/prs.py"), {**rcfg, "inbox_to": "term_x", "kinds": ["review_request", "unreleased", "ownergate"]}, "path:/r")
prompt = gated_line[gated_line.index("--prompt") + 1]
assert no_env(gated_line), gated_line
assert prompt.index("`python3 /r/prs.py round`") < prompt.index("orca orchestration send") and "`python3 /r/prs.py result <키> failed" in prompt, prompt
assert "(예외는 `round` 가 낸 단계뿐이다)" in prompt
# The prompt follows cfg["kinds"]: a kind not listed adds none of its steps.
only_review = p.schedule_cmd(pathlib.Path("/r/prs.py"), {**cfg, "inbox_to": "term_x"}, "path:/r")
plain = only_review[only_review.index("--prompt") + 1]
assert " round`" not in plain and " dispatch`" not in plain and "(예외는" not in plain
assert "다시 시도하지 않고" in plain, "every prompt forbids retrying a refused act"
four = p.schedule_cmd(pathlib.Path("/r/prs.py"), {**rcfg, "kinds": ["review_request", "review_wait", "unreleased", "ownergate"]}, "path:/r")
full = four[four.index("--prompt") + 1]
assert full.index(" round`") < full.index("orca orchestration send") < full.index(" actions`") < full.index(" dispatch`") \
    < full.index(" commit`"), full
assert "retrigger 코멘트와 `round` 가 낸 승인뿐이다." in full and ". GitHub 쓰기는 retrigger 코멘트뿐이다" not in full, \
    "the round's write list names its approvals; only review_wait's own step is scoped to the comment"
# With ready_to_merge too: merges and verify (which rewrites body.txt) come first, then round/result, then the send.
five = p.schedule_cmd(pathlib.Path("/r/prs.py"), {**rcfg, "kinds": list(p.KINDS)}, "path:/r")
every = five[five.index("--prompt") + 1]
assert every.index(" merges`") < every.index(" verify`") < every.index(" round`") < every.index("orca orchestration send"), every
assert "머지 단계와 `round` 가 낸 승인 밖에서" in every and "다시 시도하지 않고, 무엇을" in every, every
# review_wait's actions and log lines start with python3 too, with the state flag right after the subcommand.
saved_state, p.STATE = p.STATE, pathlib.Path("/s/prs")
try:
    line = p.schedule_cmd(pathlib.Path("/r/prs.py"), {**rcfg, "kinds": ["review_request", "review_wait"]}, "path:/r", edit="auto1")
finally:
    p.STATE = saved_state
prompt = line[line.index("--prompt") + 1]
assert line[:4] == ["orca", "automations", "edit", "auto1"] and no_env(line), line
assert line[line.index("--precheck") + 1] == "python3 /r/prs.py precheck --state /s"
assert "`python3 /r/prs.py actions --state /s`" in prompt and "`python3 /r/prs.py log --state /s --key <key>" in prompt, prompt
# A state folder off the default goes in as a flag after the subcommand, not through the environment.
# round and result as the prompt writes them: --state right after the subcommand, --config anywhere.
cli_state = pathlib.Path(tempfile.mkdtemp())
cli_plan = p.compute(Fake([], gated=gated), gcfg, {}, NOW)
p.write_json(cli_state / "prs" / "plan.json", cli_plan)
p.deliver(cli_state / "prs", cli_plan)
handed = run("round", "--state", str(cli_state))
assert handed.returncode == 0 and handed.stdout.startswith("acme/access#1@h1\t"), handed
done = run("result", "--state", str(cli_state), "acme/access#1@h1", "failed", "classifier", "refused")
assert done.returncode == 0 and done.stdout.startswith("회차 실행 안 됨(classifier refused), 소유자 몫"), done
assert calls.read_text().splitlines()[-1].endswith("--key prs:acme/access#1@h1"), "the CLI uses the fake dashboard, never the real one"
dry = run("schedule")
assert dry.returncode == 0 and dry.stdout.startswith("orca automations create") and "--write" in dry.stdout
assert f"precheck --state {tmp / 'state'}" in dry.stdout and "AGENT_SKILLS_STATE" not in dry.stdout, dry.stdout

# A kind without a PROMPT_STEPS entry: schedule refuses it, and its items never reach plan.json, so commit never
# records them. They take no max_items slot and do not wake a round of their own.
saved, p.PROMPT_STEPS = p.PROMPT_STEPS, {k: v for k, v in p.PROMPT_STEPS.items() if k != "unreleased"}
try:
    try:
        p.schedule_cmd(pathlib.Path("/r/prs.py"), rcfg, "path:/r")
        raise AssertionError("schedule accepted a kind without a step")
    except SystemExit as e:
        assert "unreleased" in str(e), e
    # The bot-authored request ranks below both human merges, so a cap taken before the step filter would starve it.
    bot_rr = pr(7, author="deps[bot]", kind="Bot", reqs=[user(ME)], decision=None)
    fake = lambda: Fake([bot_rr], merged=[merged(20), merged(21)], deployed="d0", live={"m20"})
    only = p.compute(fake(), {**rcfg, "max_items": 1}, {}, NOW)
    assert [i["number"] for i in only["new"]] == [7] and only["more"] == 0 and len(only["uncovered"]) == 2, only
    assert "보내지도 기록하지도 않았다" in p.body(only)
    st = tmp / "nostep"
    p.write_json(st / "plan.json", only)
    assert p.commit(st, rcfg, NOW) == 1
    assert list(p.read_json(st / "ledger.json", {})) == [only["new"][0]["key"]], "unreleased must not be recorded"
    merges_only = lambda: Fake([], merged=[merged(20), merged(21)], deployed="d0", live={"m20"})
    assert p.precheck(merges_only(), rcfg, tmp / "fresh", NOW)[0] == p.EXIT_NOTHING
finally:
    p.PROMPT_STEPS = saved
assert p.precheck(merges_only(), rcfg, tmp / "fresh", NOW)[0] == p.EXIT_NEW
assert [i["kind"] for i in p.compute(fake(), rcfg, {}, NOW)["new"]] == ["unreleased", "unreleased", "review_request"]
assert "보내지도" not in p.body(plan2)

# ready_to_merge: the owner's PRs in merge_repos that may merge now, each held back by the first gate that applies.
def mpr(n, repo="acme/widgets", draft=False, labels=(), branch=None, base="main", merge_state="CLEAN", decision=None,
        checks="SUCCESS", threads=(), total=None, reviews="record", comments=(), squash=True, merge=True, rebase=False):
    head = f"m{n}ead000"
    if reviews == "record":  # an independent review's result names the head: the no-review path's record
        reviews, comments = [], [(ME, "User", f"independent review — head {head}\n결론: pass")] + list(comments)
    return {"id": f"M_{repo}_{n}", "number": n, "title": f"Ship {n}", "url": f"https://github.com/{repo}/pull/{n}",
            "isDraft": draft, "createdAt": iso(NOW - 2 * DAY), "headRefOid": head, "headRefName": branch or f"b{n}",
            "baseRefName": base, "additions": 3, "deletions": 1, "reviewDecision": decision, "mergeStateStatus": merge_state,
            "state": "OPEN", "author": {"__typename": "User", "login": ME}, "labels": {"nodes": [{"name": x} for x in labels]},
            "repository": {"nameWithOwner": repo, "defaultBranchRef": {"name": "main"}, "squashMergeAllowed": squash,
                           "mergeCommitAllowed": merge, "rebaseMergeAllowed": rebase},
            "reviewThreads": {"totalCount": len(threads) if total is None else total,
                              "nodes": [{"isResolved": t} for t in threads]},
            # Fixture order is time order: reviews first, then comments.
            "reviews": {"totalCount": len(reviews), "nodes": [
                {"author": {"__typename": r[1], "login": r[0]}, "state": r[2], "body": "", "commit": {"oid": r[3]},
                 "authorAssociation": r[4] if len(r) > 4 else "COLLABORATOR", "createdAt": iso(NOW - 100 + k)}
                for k, r in enumerate(reviews)][-100:]},
            "comments": {"totalCount": len(comments),
                         "nodes": [{"author": {"__typename": t, "login": a}, "body": b} for a, t, b in comments]},
            "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": checks} if checks else None}}]}}


class MergeFake(Fake):
    def __init__(self, found, merged=()):
        super().__init__([])
        self.mfound, self.queries, self.rounds = list(found), [], list(merged)

    def search_merge(self, q, me):
        self.queries.append(q)
        return self.mfound

    def merge_state(self, ids, me):
        return self.rounds.pop(0) if len(self.rounds) > 1 else self.rounds[0]


mcfg = {**cfg, "kinds": ["ready_to_merge"], "merge_repos": ["acme/widgets", "acme/infra"], "merge_deny_repos": ["acme/infra"],
        "required_bots": [], "review_bots": {"reviewbot[bot]": "@reviewbot review"}}
owned = {"branches": {"lead-card"}, "prs": {"acme/widgets#21"}}
real_owned = p.owned_elsewhere
p.owned_elsewhere = lambda c, now: owned
H = lambda n: f"m{n}ead000"
cases = {
    1: (mpr(1), None),                                                                   # no review required, head reviewed
    2: (mpr(2, decision="APPROVED", reviews=[("bob", "User", "APPROVED", H(2))]), None),  # approved
    3: (mpr(3, decision="REVIEW_REQUIRED", merge_state="BLOCKED"), "mergeStateStatus BLOCKED"),
    4: (mpr(4, decision="CHANGES_REQUESTED"), "리뷰 CHANGES_REQUESTED"),
    5: (mpr(5, checks="FAILURE"), "체크 FAILURE"),
    6: (mpr(6, checks="PENDING"), "체크 PENDING"),
    7: (mpr(7, checks=None), "체크 없음"),
    8: (mpr(8, threads=[True, False]), "미해결 스레드"),
    9: (mpr(9, threads=[True] * 100, total=101), "미해결 스레드"),                       # more threads than one page
    10: (mpr(10, draft=True), "draft"),
    11: (mpr(11, labels=["Do-Not-Merge"]), "보류 라벨: do-not-merge"),
    12: (mpr(12, branch="lead-card"), "다른 worktree·프로그램이 진행 중"),                 # checked out in a live card
    13: (mpr(13, repo="acme/infra"), "머지 금지 저장소"),
    14: (mpr(14, repo="other/repo"), "머지 허용 목록 밖"),
    15: (mpr(15, base="feature-a"), "stack 하위 PR (base feature-a)"),
    16: (mpr(16, decision="REVIEW_REQUIRED", reviews=[("bob", "User", "APPROVED", "old"), ("bob", "User", "DISMISSED", "old")]),
         "승인이 해제됨: review_wait 로"),                                              # retarget dismissed the approval
    17: (mpr(17, squash=False, merge=False), "허용된 머지 방식 없음"),
    18: (mpr(18, reviews=[]), "head 리뷰 기록 없음: review_wait 로"),                     # CLEAN alone is not a review
    19: (mpr(19, reviews=[], comments=[("mallory", "User", f"LGTM {H(19)}")]), "head 리뷰 기록 없음: review_wait 로"),
    20: (mpr(20, reviews=[], comments=[("reviewbot", "Bot", f"Reviewed {H(20)}\n**Verdict:** pass")]), None),  # a review bot
    23: (mpr(23, reviews=[], comments=[(ME, "User", f"head {H(23)}\n결론: fail")]), "head 리뷰 기록 없음: review_wait 로"),
    24: (mpr(24, reviews=[(ME, "User", "COMMENTED", H(24))]), "head 리뷰 기록 없음: review_wait 로"),  # a self-reply
    25: (mpr(25, reviews=[], comments=[("previewbot", "Bot", f"Deployed {H(25)}\n결론: pass")]), "head 리뷰 기록 없음: review_wait 로"),
    26: (mpr(26, reviews=[("bob", "User", "CHANGES_REQUESTED", "old"), (ME, "User", "COMMENTED", H(26))],
             comments=[(ME, "User", f"head {H(26)}\n결론: pass")]), "변경 요청이 남아 있음"),
    27: (mpr(27, reviews=[("bob", "User", "APPROVED", H(27))]), None),                    # approved on this head, none required
    28: (mpr(28, reviews=[("bob", "User", "CHANGES_REQUESTED", "old"), ("bob", "User", "APPROVED", H(28))]), None),
    29: (mpr(29, reviews=[("drive-by", "User", "APPROVED", H(29), "NONE")]), "head 리뷰 기록 없음: review_wait 로"),
    30: (mpr(30, comments=[(ME, "User", f"second review — head {H(30)}\n결론: fail")]), "head 리뷰 기록 없음: review_wait 로"),
    31: (mpr(31, reviews=[], comments=[(ME, "User", f"head {H(31)}\n> 결론: pass\n- `결론: pass`\n결론: pass / fail\n결론: fail")]),
         "head 리뷰 기록 없음: review_wait 로"),                                              # quoted passes, real fail
    32: (mpr(32, reviews=[("bob", "User", "APPROVED", "old")] * 101), "리뷰·코멘트가 100건을 넘어 확인 불가"),
    33: (mpr(33, reviews=[("bob", "User", "APPROVED", H(33))], comments=[(ME, "User", f"head {H(33)}\n결론: fail")]),
         "head 리뷰 기록 없음: review_wait 로"),                                              # a fail outweighs an approval
    34: (mpr(34, reviews=[], comments=[("reviewbot", "User", f"head {H(34)}\n결론: pass")]),
         "head 리뷰 기록 없음: review_wait 로"),                                              # a User named like the bot
    21: (mpr(21), "다른 worktree·프로그램이 진행 중"),                                    # in an active program's ledger
    22: (mpr(22, reviews=[], comments=[(ME, "User", "review of an older head m0ld000")]), "head 리뷰 기록 없음: review_wait 로"),
}
for n, (x, want) in cases.items():
    got = p.merge_readiness(x, mcfg, owned, ME)
    assert got[1] == want, (n, got)
assert p.merge_readiness(mpr(1), {**mcfg, "merge_methods": ["rebase", "merge"]}, owned, ME) == ("merge", None), \
    "first allowed in preference order"
assert p.merge_readiness(mpr(1), mcfg, None, ME)[1] == "프로그램 소유 여부 확인 불가", "ownership unknown fails closed"

# Required review bots must have reviewed this head; a rate-limited bot leaves no review, an old review does not count.
bcfg = {**mcfg, "required_bots": ["reviewbot[bot]"]}
assert p.merge_readiness(mpr(1), bcfg, owned, ME)[1] == "필수 봇 reviewbot[bot] 이 head 를 리뷰하지 않음"
assert p.merge_readiness(mpr(1, reviews=[("reviewbot", "Bot", "COMMENTED", "old")], comments=[(ME, "User", f"{H(1)}\n결론: pass")]), bcfg, owned, ME)[1].startswith("필수 봇")
assert p.merge_readiness(mpr(1, reviews=[("reviewbot", "Bot", "COMMENTED", H(1))], comments=[(ME, "User", f"{H(1)}\n결론: pass")]), bcfg, owned, ME) == ("squash", None)

# The collector: search scoped to merge_repos, one item per ready PR with the exact merge command; the rest excluded.
fake = MergeFake([c[0] for c in cases.values()])
mplan = p.compute(fake, mcfg, {}, NOW)
assert fake.queries == ["is:pr is:open archived:false author:@me repo:acme/widgets repo:acme/infra"], fake.queries
assert [i["number"] for i in mplan["new"]] == [1, 2, 20, 27, 28], mplan["new"]
one = mplan["new"][0]
assert one["key"] == f"acme/widgets#1@{H(1)}" and one["method"] == "squash"
assert one["cmd"] == f"gh pr merge 1 -R acme/widgets --squash --match-head-commit {H(1)}", one["cmd"]
mwhy = {e["pr"]: e["why"] for e in mplan["excluded"]}
assert len(mwhy) == len(cases) - 5 and mwhy["acme/widgets#12"] == "다른 worktree·프로그램이 진행 중"
# An empty merge_repos merges nothing and does not search.
empty = MergeFake([mpr(1)])
assert p.compute(empty, {**mcfg, "merge_repos": []}, {}, NOW)["new"] == [] and empty.queries == []
# Orca unreadable: nothing is ready, the reason says why.
def broken_orca(c, now):
    raise RuntimeError("orca down")
p.owned_elsewhere = broken_orca
assert {e["why"] for e in p.compute(MergeFake([mpr(1)]), mcfg, {}, NOW)["excluded"]} == {"프로그램 소유 여부 확인 불가"}
p.owned_elsewhere = lambda c, now: owned

# verify: MERGED plus the merge commit's CI, waiting while it is pending; a red run and a refused merge go in the body.
mstate = pathlib.Path(tempfile.mkdtemp()) / "prs"
two = {**mplan, "new": mplan["new"][:2]}
p.write_json(mstate / "plan.json", two)
pending = [{"id": "M_acme/widgets_1", "state": "MERGED", "mergeCommit": {"oid": "abc1234def", "statusCheckRollup": {"state": "PENDING"}}},
           {"id": "M_acme/widgets_2", "state": "OPEN", "mergeCommit": None}]
red = [{"id": "M_acme/widgets_1", "state": "MERGED", "mergeCommit": {"oid": "abc1234def", "statusCheckRollup": {"state": "FAILURE",
        "contexts": {"nodes": [{"name": "test", "conclusion": "FAILURE", "detailsUrl": "https://ci.example.test/run/9"},
                               {"name": "lint", "conclusion": "SUCCESS", "detailsUrl": "https://ci.example.test/run/8"}]}}}},
       pending[1]]
clock = iter([0, 0, 10, 30, 40])
slept = []
text = p.verify(MergeFake([], [pending, red]), mstate, mcfg, clock=lambda: next(clock), sleep=slept.append)
assert slept == [30], slept
lines = {l.split("] ")[1].split(" ")[0]: l for l in text.splitlines()[1:]}
assert "머지됨 abc1234, 기본 브랜치 CI red: https://ci.example.test/run/9 —" in lines["acme/widgets#1"], lines
assert "머지 안 됨 (state OPEN)" in lines["acme/widgets#2"], lines
assert (mstate / "body.txt").read_text() == text
assert all(i.get("result") for i in p.read_json(mstate / "plan.json", {})["new"])
# CI still running at the deadline: reported as such, not as green.
p.write_json(mstate / "plan.json", two)
clock = iter([0, 0, 901, 901])
text = p.verify(MergeFake([], [pending]), mstate, mcfg, clock=lambda: next(clock), sleep=lambda s: None)
assert "기본 브랜치 CI PENDING" in text
# A default branch without CI: no checks attach to the merge commit; after the grace it is reported, not awaited.
bare = [{**pending[0], "mergeCommit": {"oid": "abc1234def", "statusCheckRollup": None}}, pending[1]]
p.write_json(mstate / "plan.json", two)
clock = iter([0, 0, 30, 30, 150, 150])
slept = []
text = p.verify(MergeFake([], [bare]), mstate, mcfg, clock=lambda: next(clock), sleep=slept.append)
assert slept == [30, 30] and "기본 브랜치 CI 없음" in text, (slept, text)
# commit records the merged heads: the same head is never offered again, merged or refused.
assert p.commit(mstate, mcfg, NOW) == 2
assert p.compute(MergeFake([c[0] for c in cases.values()][:2]), mcfg, p.read_json(mstate / "ledger.json", {}), NOW)["new"] == []

# A config that collects ready_to_merge schedules a round that merges; without it the round stays read-only.
mline = p.schedule_cmd(pathlib.Path("/repo/skills/watch-prs/scripts/prs.py"), {**mcfg, "inbox_to": "term_x"}, "path:/repo")
mprompt = mline[mline.index("--prompt") + 1]
assert all(s in mprompt for s in ("merges", "verify", "gh pr merge", "--admin", "다시 시도하지 않", "commit")), mprompt
ro = p.schedule_cmd(pathlib.Path("/x/prs.py"), {**cfg, "inbox_to": "t"}, "path:/x")
ro = ro[ro.index("--prompt") + 1]
assert "merges" not in ro and "감지하고 넘기기만" in ro
assert "코멘트·재요청도 하지 않는다" in mprompt
assert no_env(mline) and "`python3 /repo/skills/watch-prs/scripts/prs.py merges`" in mprompt, mprompt
# With review_wait too, merging still precedes the send and the actions step follows it.
both = p.schedule_cmd(pathlib.Path("/x/prs.py"), {**mcfg, "kinds": ["ready_to_merge", "review_wait"], "inbox_to": "t"}, "path:/x")
both = both[both.index("--prompt") + 1]
assert both.index(" merges`") < both.index("orca orchestration send") < both.index(" actions`") < both.index(" commit`"), both

# owned_elsewhere: branches of live Orca worktrees, and PRs in the ledger of a live program (program_prs).
otmp = pathlib.Path(tempfile.mkdtemp())
trees = {"result": {"truncated": False, "worktrees": [
    {"branch": "refs/heads/lead-card", "isArchived": False}, {"branch": "refs/heads/old-card", "isArchived": True},
    {"branch": None, "isArchived": False}]}}
(otmp / "trees.json").write_text(json.dumps(trees))
fake_orca = otmp / "orca"
fake_orca.write_text(f"#!/bin/sh\ncat {shlex.quote(str(otmp / 'trees.json'))}\n")
fake_orca.chmod(fake_orca.stat().st_mode | stat.S_IEXEC)
home = otmp / "programs"
for slug, ts, n in (("live", NOW - DAY, 7), ("old", NOW - 40 * DAY, 8), ("stopped", NOW - DAY, 9)):
    (home / slug).mkdir(parents=True)
    (home / slug / "program.json").write_text(json.dumps({"repo": "acme/widgets"}))
    row = json.dumps({"ts": iso(ts).replace("Z", "+00:00"), "ev": "verdict", "pr": n})
    stop = json.dumps({"ts": iso(ts).replace("Z", "+00:00"), "ev": "stop"}) + "\n" if slug == "stopped" else ""
    (home / slug / "ledger.jsonl").write_text(row + "\n" + stop + '{"ts": "torn')   # a writer mid-append
p.ORCA = str(fake_orca)
got = real_owned({**mcfg, "programs_dir": str(home)}, NOW)
# A stopped program still owns its PRs for merging (a human gate may be pending), though review_wait resumes them.
assert got == {"branches": {"lead-card"}, "prs": {"acme/widgets#7", "acme/widgets#9"}}, got
assert set(p.program_prs({**mcfg, "programs_dir": str(home)}, NOW)) == {("acme/widgets", 7)}
trees["result"]["truncated"] = True
(otmp / "trees.json").write_text(json.dumps(trees))
for broken in (str(fake_orca), "/nonexistent/orca"):
    p.ORCA = broken
    try:
        real_owned({**mcfg, "programs_dir": str(home)}, NOW)
        raise AssertionError("a truncated or unreadable Orca list must raise")
    except (RuntimeError, OSError):
        pass

# merges records each attempt before the round runs it: a refused merge whose verify never ran is not offered again.
mtmp = pathlib.Path(tempfile.mkdtemp())
p.write_json(mtmp / "prs" / "plan.json", {**mplan, "new": mplan["new"][:1]})
mconf = mtmp / "agent-skills.json"
mconf.write_text(json.dumps({"prs": {"kinds": ["ready_to_merge"], "merge_repos": ["acme/widgets"]}}))
out = subprocess.run([sys.executable, script, "merges", "--config", str(mconf)], capture_output=True, text=True,
                     env={**os.environ, "AGENT_SKILLS_STATE": str(mtmp)})
assert out.returncode == 0 and out.stdout.splitlines()[1] == mplan["new"][0]["cmd"], out
assert list(p.read_json(mtmp / "prs" / "ledger.json", {})) == [mplan["new"][0]["key"]]

# Kinds share the ledger key shape: the head ready_to_merge recorded is still due as unreleased once merged.
shared = {"acme/widgets#20@m20": {"at": NOW, "kind": "ready_to_merge", "id": "x"}}
assert p.delivered(shared, "acme/widgets#20@m20", "ready_to_merge")
assert not p.delivered(shared, "acme/widgets#20@m20", "unreleased") and not p.delivered({}, "k", "unreleased")

# exclude_title_patterns: a matching title is dropped at collection by every kind, so it is never ranked, sent,
# merged or dispatched; another PR in the same repo stays. review_wait is covered in test_review_wait.py.
class AllFake(Fake):
    def __init__(self, mfound, **kw):
        super().__init__(**kw)
        self.mfound = mfound

    def search_merge(self, q, me):
        return self.mfound


DROP = "Remove legacy config"
tseen = pathlib.Path(tempfile.mkdtemp()) / "seen.json"
p.write_json(tseen, {f"C0FAKE/{n}": {"at": NOW, "category": "review", "permalink": link(n), "pr": f"acme/widgets#{n}"}
                     for n in (64, 71)})
tfake = lambda: AllFake(
    [{**mpr(67), "title": DROP}, mpr(68)],
    search=[pr(60, reqs=[user(ME)], title=DROP), pr(61, reqs=[user(ME)])],
    nodes=[pr(62, head="h62b", reviewed="h62", title=DROP), pr(63, head="h63b", reviewed="h63"),
           pr(64, title=DROP), pr(71)],
    gated=[pr(65, **A, title="[Auto] Remove access", files=["members/c.json"]),
           pr(66, **A, title="[Auto] Add access for 3 users", files=["members/d.json"])],
    merged=[{**merged(69), "title": DROP}, merged(70)], deployed="d0")
tledger = {f"acme/widgets#{n}@h{n}": {"at": NOW, "kind": "review_request", "id": f"PR_acme/widgets_{n}"} for n in (62, 63)}
tcfg = {**cfg, "kinds": [k for k in p.KINDS if k != "review_wait"], "mentions_seen": str(tseen),
        "merge_repos": ["acme/widgets"], "deploy": {"acme/widgets": decl}, "ownergate": [gate], "ignore_bots": ["grant-bot"]}
kept = p.compute(tfake(), {**tcfg, "exclude_title_patterns": [rf"^{DROP}", r"^\[Auto\] Remove"]}, tledger, NOW)
assert sorted((i["kind"], i["number"]) for i in kept["new"]) == [("ownergate", 66), ("ready_to_merge", 68),
    ("review_request", 61), ("review_request", 63), ("review_request", 71), ("unreleased", 70)], kept["new"]
assert {(e["pr"], e["why"]) for e in kept["excluded"] if "제목" in e["why"]} == {
    ("acme/widgets#60", "제외 대상 제목"), ("acme/widgets#64", "Slack 요청, 제외 대상 제목"),
    ("acme/access#65", "제외 대상 제목"), ("acme/widgets#67", "제외 대상 제목"), ("acme/widgets#69", "제외 대상 제목")}, kept["excluded"]
assert not kept["known"] and not kept["uncovered"]
# Case-sensitive unless the pattern says otherwise.
assert 60 in [i["number"] for i in p.compute(tfake(), {**tcfg, "exclude_title_patterns": ["^remove legacy"]}, tledger, NOW)["new"]]
assert 60 not in [i["number"] for i in p.compute(tfake(), {**tcfg, "exclude_title_patterns": ["(?i)^remove legacy"]}, tledger, NOW)["new"]]
# An empty list changes nothing: every one of those PRs is back.
alln = sorted(i["number"] for i in p.compute(tfake(), {**tcfg, "exclude_title_patterns": []}, tledger, NOW)["new"])
assert alln == [60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71], alln

# config: a missing or null key is the empty list; a bad regex or a non-list fails the load, and every command exits 2.
tconf = pathlib.Path(tempfile.mkdtemp()) / "agent-skills.json"
for raw in ({}, {"exclude_title_patterns": None}, {"exclude_title_patterns": []}):
    tconf.write_text(json.dumps({"prs": raw}))
    assert p.config(tconf)["exclude_title_patterns"] == [], raw
for bad, msg in ((["^ok", "["], "invalid regex '['"), ("^Remove", "must be a list"), ([1], "must be a list"),
                 ("", "must be a list"), (False, "must be a list"), ({}, "must be a list")):
    tconf.write_text(json.dumps({"prs": {"exclude_title_patterns": bad}}))
    try:
        p.config(tconf)
        raise AssertionError(f"config must refuse {bad!r}")
    except p.ConfigError as e:
        assert msg in str(e), e
    for sub in ("precheck", "plan"):
        r = subprocess.run([sys.executable, script, sub, "--config", str(tconf)], env=env, capture_output=True, text=True)
        assert r.returncode == p.EXIT_ERROR and "exclude_title_patterns" in r.stderr and "Traceback" not in r.stderr, r

# A review request counts only when a person made it: an app naming the owner as a User reviewer is not a person
# asking. The latest request of the owner decides; no record of who asked counts as not a person.
asked = lambda n, *actors: {**pr(n, reqs=[user(ME)]), "timelineItems": {"nodes": [
    {"actor": {"__typename": k, "login": "x"}, "requestedReviewer": {"__typename": "User", "login": ME}} for k in actors]}}
askers = [pr(80, reqs=[user(ME)]), pr(81, reqs=[user(ME)], asker=("Bot", "assign-bot")), asked(82, "Bot", "User"),
          asked(83, "User", "Bot"), asked(84), {**asked(85, "User"), "timelineItems": {"nodes": [{
              "actor": None, "requestedReviewer": {"__typename": "User", "login": ME}}]}}]
got = p.compute(Fake(askers), cfg, {}, NOW)
assert sorted(i["number"] for i in got["new"]) == [80, 82], got["new"]
assert {e["pr"]: e["why"] for e in got["excluded"]} == {f"acme/widgets#{n}": "사람이 아닌 계정의 요청" for n in (81, 83, 84, 85)}

# The new-commits path never brings back a PR whose current request of the owner is a team, CODEOWNERS or app one:
# the search still returns that PR, so it is excluded there and skipped by the ledger path.
reviewed = {f"acme/widgets#{n}@h{n}": {"at": NOW, "kind": "review_request", "id": f"PR_acme/widgets_{n}"} for n in (86, 87, 88)}
now_req = [pr(86, reqs=[team("core")], head="h86b", reviewed="h86"),
           pr(87, reqs=[user(ME, code_owner=True)], head="h87b", reviewed="h87"),
           pr(88, reqs=[user(ME)], head="h88b", reviewed="h88", asker=("Bot", "assign-bot"))]
back = p.compute(Fake(now_req, now_req), cfg, reviewed, NOW)
assert back["new"] == [], back["new"]
assert {e["pr"]: e["why"] for e in back["excluded"]} == {"acme/widgets#86": "팀·CODEOWNERS 요청뿐",
    "acme/widgets#87": "팀·CODEOWNERS 요청뿐", "acme/widgets#88": "사람이 아닌 계정의 요청"}, back["excluded"]
# With no request left at all, new commits after the owner's review still come back once, as before.
gone = [pr(86, reqs=[], head="h86b", reviewed="h86")]
assert [i["reason"] for i in p.compute(Fake([], gone), cfg, reviewed, NOW)["new"]] == ["new_commits"]

print("test_prs: ok")
