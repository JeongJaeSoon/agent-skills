"""Rules of the review_wait collector: PR states, the reminder decisions, the send-log and the night window.
Run: python3 test_review_wait.py"""
import datetime as dt, json, pathlib, sys, tempfile, zoneinfo
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import prs as p

ME, TZ = "owner", zoneinfo.ZoneInfo("Asia/Tokyo")
H = 3600


def at(day, hour, minute=0):
    """2026-10-<day> <hour>:<minute> local; the 14th is a Wednesday, the 17th and 18th a weekend."""
    return dt.datetime(2026, 10, day, hour, minute, tzinfo=TZ).timestamp()


def iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = at(14, 11)
cfg = {**p.DEFAULTS, "kinds": ["review_wait"], "timezone": "Asia/Tokyo", "self_bots": ["owner-bot"],
       "channel": "CFAKE", "repo_channels": {"acme/tools": "CTOOLS"},
       "reviewers": {"bob": {"slack": "U0FAKEBOB"}, "carol": {"slack": "U0FAKECAROL", "channel": "CCAROL"}},
       "review_bots": {"rabbit[bot]": "@rabbit review"}, "holidays": ["2026-10-19"], "urgent_labels": ["security"]}


def mine(n, repo="acme/widgets", reqs=(), reviews=(), threads=(), events=(), decision="REVIEW_REQUIRED", head=None,
         labels=(), draft=False, idle=0, pushed=None, author=ME, kind="User"):
    return {"id": f"PR_{repo}_{n}", "number": n, "title": f"Mine {n}", "url": f"https://github.com/{repo}/pull/{n}",
            "isDraft": draft, "createdAt": iso(NOW - 3 * 86400), "updatedAt": iso(NOW - idle * 86400),
            "headRefOid": head or f"h{n}", "additions": 5, "deletions": 1, "reviewDecision": decision, "state": "OPEN",
            "repository": {"nameWithOwner": repo}, "author": {"__typename": kind, "login": author},
            "labels": {"nodes": [{"name": l} for l in labels]},
            "reviewRequests": {"nodes": [{"asCodeOwner": False, "requestedReviewer": r} for r in reqs]},
            "latestReviews": {"nodes": list(reviews)}, "reviewThreads": {"nodes": list(threads)},
            "timelineItems": {"nodes": list(events)},
            "commits": {"nodes": [{"commit": {"committedDate": iso(pushed or NOW - 2 * 86400)}}]}}


def u(login):
    return {"__typename": "User", "login": login}


def asked(login, t):
    return {"__typename": "ReviewRequestedEvent", "createdAt": iso(t), "requestedReviewer": u(login)}


def review(login, state, oid, kind="User"):
    return {"author": {"__typename": kind, "login": login}, "state": state, "submittedAt": iso(NOW - H), "commit": {"oid": oid}}


def thread(opener, last, t, resolved=False):
    return {"isResolved": resolved, "opener": {"nodes": [{"author": {"login": opener}}]},
            "last": {"nodes": [{"author": {"login": last}, "createdAt": iso(t)}]}}


def row(pr, who, action, t, head=None, source="sent", thread="CFAKE:1.0", post_at=None):
    return {"pr": pr, "reviewer": who, "head": head or "h" + pr.split("#")[1], "action": action, "source": source,
            "at": t, "post_at": post_at, "thread": thread, "scheduled": None}


def state(pr, **kw):
    return p.wait_state(mine(pr, **kw), ME, cfg)


def acts(states, log=(), now=NOW, c=cfg):
    return [(a["action"], a["pr"], a["reviewer"]) for a in p.decide(states, list(log), now, c)]


W = "acme/widgets#"

# States: what each PR waits on.
s = state(1, reqs=[u("bob"), {"__typename": "Team", "slug": "core"}], events=[asked("bob", NOW - H)])
assert s["pending"] == ["bob"] and s["teams"] == ["core"] and s["asked"]["bob"] == NOW - H and not s["no_reviewer"]
assert state(2)["no_reviewer"] and not state(2, decision=None)["no_reviewer"], "no review required → not no_reviewer"
assert not state(2, reviews=[review("bob", "COMMENTED", "h2")])["no_reviewer"]
s = state(3, threads=[thread("bob", "bob", NOW - H), thread("bob", ME, NOW - H), thread("bob", "bob", NOW - 3 * 86400),
                      thread("bob", "bob", NOW - H, resolved=True), thread("bob", "owner-bot", NOW - H)])
assert s["unanswered"] == 1, "only an open thread whose last word is someone else's, after the last push"
s = state(4, reviews=[review("bob", "APPROVED", "old")], reqs=[u("bob")])
assert s["stale_approvals"] == ["bob"] and s["pending"] == [], "approval on an older head, re-requested: stale, not waiting"
for other in ("APPROVED", None, "CHANGES_REQUESTED"):
    assert state(4, reviews=[review("bob", "APPROVED", "old")], decision=other)["stale_approvals"] == [], \
        f"{other}: approved anyway, no review required, or waiting on someone else"
assert state(4, reviews=[review("bob", "APPROVED", "h4")])["stale_approvals"] == []
dismissed = {"__typename": "ReviewDismissedEvent", "createdAt": iso(NOW - H), "previousReviewState": "APPROVED",
             "review": {"author": {"login": "bob"}}}
assert state(5, reviews=[review("bob", "DISMISSED", "h5")], events=[dismissed])["stale_approvals"] == ["bob"]
assert state(5, reviews=[review("bob", "DISMISSED", "h5")])["stale_approvals"] == [], "a dismissed change request is not an approval"
s = state(6, reviews=[review("rabbit", "CHANGES_REQUESTED", "h6", kind="Bot")], threads=[thread("rabbit", "rabbit", NOW - H, resolved=True)])
assert s["bot_stale"] == ["rabbit"]
assert state(6, reviews=[review("rabbit", "CHANGES_REQUESTED", "h6", kind="Bot")],
             threads=[thread("rabbit", ME, NOW - H)])["bot_stale"] == [], "a bot thread still open → not stale"
assert state(7, labels=["security"])["urgent"]

# request: batched per reviewer; due at batch_min waiting, or the oldest waited 2h, or urgent.
one = state(10, reqs=[u("bob")], events=[asked("bob", NOW - H)])
assert acts([one]) == [], "one request, waited 1h: hold"
assert acts([one], now=NOW + H) == [("request", W + "10", "bob")], "the oldest waited 2h"
two = state(11, reqs=[u("bob")], events=[asked("bob", NOW - 10 * 60)])
assert acts([one, two]) == [("request", W + "10", "bob"), ("request", W + "11", "bob")], "two waiting: one batch"
assert acts([state(12, reqs=[u("bob")], events=[asked("bob", NOW)], labels=["security"])]) == [("request", W + "12", "bob")]
assert acts([state(13, reqs=[u("dave")], events=[asked("dave", NOW - 5 * H)])]) == [], "unmapped reviewer: no Slack"
assert p.notices(state(13, reqs=[u("dave")]), cfg) == ["Slack 매핑 없는 리뷰어: @dave"]
noid = {**cfg, "reviewers": {**cfg["reviewers"], "erin": {"channel": "CFAKE"}}}
assert acts([state(13, reqs=[u("erin")], labels=["security"])], c=noid) == [], "mapped without a Slack id: no action"

# One request per PR per reviewer: a row already there (any head) moves the PR to the reminder path.
assert acts([one, two], [row(W + "10", "bob", "request", NOW - 2 * H)]) == [], "#11 alone waited 10m: hold"
assert acts([two], [row(W + "11", "bob", "request", NOW - 2 * H, head="older")], now=NOW) == [], \
    "a new push on a waiting PR is not a reason to request again within the day"

# remind: one business day after the request, once, in its thread.
req = [row(W + "10", "bob", "request", at(13, 15))]
assert acts([one], req, now=at(14, 14, 59)) == []
r = p.decide([one], req, at(14, 15), cfg)
assert [(a["action"], a["thread"], a["post_at"]) for a in r] == [("remind", "CFAKE:1.0", None)]
assert acts([one], req + [row(W + "10", "bob", "remind", at(14, 15))], now=at(15, 16)) == [], "once"
fri = [row(W + "10", "bob", "request", at(16, 10))]
assert acts([one], fri, now=at(18, 12)) == [], "a weekend is not a business day"
assert acts([one], fri, now=at(19, 12)) == [], "a configured holiday is not either"
assert acts([one], fri, now=at(20, 10)) == [("remind", W + "10", "bob")]
sched = [row(W + "10", "bob", "request", at(13, 22), source="scheduled", post_at=at(14, 9))]
assert acts([one], sched, now=at(14, 23)) == [] and acts([one], sched, now=at(15, 9)) == [("remind", W + "10", "bob")], \
    "the business day counts from when a scheduled request posted"
nothread = p.decide([one], [row(W + "10", "bob", "request", at(13, 10), thread=None)], NOW, cfg)
assert nothread[0]["thread"] is None, "no thread known: the round searches for it and never posts top-level"

# rerequest: a voided approval, once per head; a new push lifts the 24h rule.
stale4 = state(4, reviews=[review("bob", "APPROVED", "old")], head="h4")
assert acts([stale4]) == [("rerequest", W + "4", "bob")]
assert acts([stale4], [row(W + "4", "bob", "rerequest", NOW - H, head="h4")]) == [], "same head, within 24h: no"
assert acts([stale4], [row(W + "4", "bob", "rerequest", NOW - 5 * 86400, head="h4")]) == [], "same head: never again"
newer = state(4, reviews=[review("bob", "APPROVED", "old")], head="h4b")
assert acts([newer], [row(W + "4", "bob", "rerequest", NOW - H, head="h4")]) == [("rerequest", W + "4", "bob")], \
    "a new push within the hour is the exception"
assert p.decide([stale4], [row(W + "4", "bob", "request", NOW - 3 * 86400, head="h0", thread="CFAKE:9.0")], NOW, cfg)[0]["thread"] == "CFAKE:9.0"

# retrigger: the bot's trigger comment, once per head; immediately even at night (no human is pinged).
bot = state(6, reviews=[review("rabbit", "CHANGES_REQUESTED", "h6", kind="Bot")])
r = p.decide([bot], [], at(14, 23), cfg)
assert [(a["action"], a["comment"], a["post_at"], a["channel"]) for a in r] == [("retrigger", "@rabbit review", None, None)]
assert acts([bot], [row(W + "6", "rabbit", "retrigger", NOW - H)]) == []
assert acts([state(6, reviews=[review("other", "CHANGES_REQUESTED", "h6", kind="Bot")])]) == []
assert p.notices(state(6, reviews=[review("other", "CHANGES_REQUESTED", "h6", kind="Bot")], decision=None), cfg) == \
    ["봇 재리뷰 필요(트리거 미설정): @other"]

# A skipped row (moot at send time) stops only that action at that head; a new head offers it again.
assert acts([one], [row(W + "10", "bob", "request", NOW - 3 * H, source="skipped")], now=NOW + 3 * H) == []
assert acts([stale4], [row(W + "4", "bob", "rerequest", NOW, head="h4", source="skipped")]) == []
assert acts([newer], [row(W + "4", "bob", "rerequest", NOW, head="h4", source="skipped")]) == [("rerequest", W + "4", "bob")]

# A refused row stops every later action for that (PR, reviewer); no retry in any form.
assert acts([one], [row(W + "10", "bob", "request", NOW - 3 * 86400, source="refused")], now=NOW + 3 * H) == []
assert acts([stale4], [row(W + "4", "bob", "rerequest", NOW, head="old", source="refused")]) == []

# Night and non-business days: 09:00 on the next business day is when a business-hours round runs again.
assert p.post_at(at(14, 20, 59), cfg) is None
assert p.post_at(at(14, 21), cfg) == at(15, 9)
assert p.post_at(at(15, 2), cfg) == at(15, 9), "after midnight, before 09:00: the same morning"
assert p.post_at(at(16, 22), cfg) == at(20, 9), "Friday night skips the weekend and the holiday"
assert p.post_at(at(17, 12), cfg) == at(20, 9)
# after_hours: the default holds every Slack action for a business-hours round; schedule-all schedules it; send-now sends.
assert cfg["after_hours"] == "schedule-certain-only" and p.decide([one], [], at(14, 22), cfg) == []
assert acts([one], now=at(15, 9)) == [("request", W + "10", "bob")], "the held request is decided again in the morning"
sched_all = {**cfg, "after_hours": "schedule-all"}
night = p.decide([one], [], at(14, 22), sched_all)
assert night[0]["post_at"] == at(15, 9) and night[0]["channel"] == "CFAKE"
now_mode = p.decide([one], [], at(14, 22), {**cfg, "after_hours": "send-now"})
assert [(a["action"], a["post_at"]) for a in now_mode] == [("request", None)]
# While a scheduled request to a reviewer is pending, the next batch to them waits: it could not be merged into it.
pending = [row(W + "10", "bob", "request", at(14, 22), source="scheduled", post_at=at(15, 9))]
three = state(14, reqs=[u("bob")], events=[asked("bob", at(14, 20))])
assert acts([one, three], pending, now=at(14, 23), c=sched_all) == []
assert acts([one, three], pending, now=at(15, 9, 5)) == [("request", W + "14", "bob")]

skipped_later = [row(W + "10", "bob", "request", at(14, 22), source="skipped", post_at=at(15, 9))]
assert acts([one, three], skipped_later, now=at(15, 9, 5)) == [("request", W + "14", "bob")], \
    "a skipped row with post_at does not hold the reviewer's batch"

# weekend_days replaces Saturday/Sunday; 2026-10-16 is a Friday and 2026-10-17 a Saturday.
fri_sat = {**cfg, "weekend_days": [4, 5]}
assert not p.business_day(dt.date(2026, 10, 16), fri_sat) and p.business_day(dt.date(2026, 10, 18), fri_sat)
assert p.post_at(at(15, 22), fri_sat) == at(18, 9), "Thursday night skips Friday and Saturday"
assert acts([one], now=at(16, 11), c=fri_sat) == [] and acts([one], now=at(18, 11), c=fri_sat) == [("request", W + "10", "bob")]

# A request carries the reviewer+assignee edit unless assign_reviewers is off; other actions never do.
r = p.decide([one], [], NOW + 2 * H, cfg)
assert r[0]["github"] == "gh pr edit https://github.com/acme/widgets/pull/10 --add-reviewer bob --add-assignee bob", r
assert p.decide([one], [], NOW + 2 * H, {**cfg, "assign_reviewers": False})[0]["github"] is None
assert p.decide([one], [row(W + "10", "bob", "request", at(13, 10))], NOW, cfg)[0]["github"] is None, "a remind does not"

# profiles: the first match overrides the top-level keys for that repo only; a match without a slash names an owner.
prof = {**cfg, "profiles": [{"match": "acme/tool*", "after_hours": "send-now", "assign_reviewers": False},
                            {"match": "acme", "night_hour": 23}]}
assert p.repo_cfg(prof, "acme/tools")["after_hours"] == "send-now" and p.repo_cfg(prof, "acme/tools")["night_hour"] == 21
assert p.repo_cfg(prof, "Acme/Widgets")["night_hour"] == 23 and p.repo_cfg(prof, "other/x") is prof
tools = state(15, repo="acme/tools", reqs=[u("bob")], labels=["security"])
late = {a["pr"]: a for a in p.decide([one, tools], [], at(14, 22), prof)}
assert set(late) == {W + "10", "acme/tools#15"} and late["acme/tools#15"]["github"] is None, late
assert late[W + "10"]["post_at"] is None and late["acme/tools#15"]["post_at"] is None, "21:00-23:00 is business hours for acme"
assert p.decide([one, tools], [], at(14, 23, 30), prof)[0]["pr"] == "acme/tools#15", "acme/widgets holds after 23:00"
for bad in ({"after_hours": "later"}, {"weekend_days": [7]}, {"weekend_days": "sat"}, {"profiles": [{"night_hour": 22}]},
            {"profiles": [{"match": "acme/*", "kinds": ["review_wait"]}]}, {"profiles": [{"match": "acme", "after_hours": "x"}]}):
    path = pathlib.Path(tempfile.mkdtemp()) / "c.json"
    path.write_text(json.dumps({"prs": bad}))
    try:
        p.config(path)
        raise AssertionError(bad)
    except p.ConfigError:
        pass

# Channels: reviewer > repository > default.
r = p.decide([state(20, reqs=[u("carol")], labels=["security"]), state(21, repo="acme/tools", reqs=[u("bob")], labels=["security"])],
             [], NOW, cfg)
assert {(a["pr"], a["channel"], a["slack_user"]) for a in r} == {(W + "20", "CCAROL", "U0FAKECAROL"),
                                                           ("acme/tools#21", "CTOOLS", "U0FAKEBOB")}

# The collector: exclusions, program PRs (repo-qualified, parked included, idle programs not), notices and actions.
progs = pathlib.Path(tempfile.mkdtemp())
fresh, old = iso(NOW - 86400), iso(NOW - 40 * 86400)
for slug, repo, rows in (("live", "acme/widgets", [{"ev": "spawned", "pr": 30, "ts": fresh}, {"ev": "landed", "pr": 31, "ts": fresh}]),
                         ("held", "acme/widgets", [{"ev": "landed", "pr": 32, "ts": old}, {"ev": "parked", "pr": 32, "ts": fresh}]),
                         ("elsewhere", "acme/other", [{"ev": "spawned", "pr": 33, "ts": fresh}]),
                         ("idle", "acme/widgets", [{"ev": "spawned", "pr": 39, "ts": old}]),
                         ("stopped", "acme/widgets", [{"ev": "spawned", "pr": 40, "ts": fresh}, {"ev": "stop", "ts": fresh}])):
    (progs / slug).mkdir()
    (progs / slug / "program.json").write_text(json.dumps({"repo": repo}))
    (progs / slug / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows) + "not json\n")
p.STATE = pathlib.Path(tempfile.mkdtemp()) / "prs"
c = {**cfg, "programs_dir": str(progs), "exclude_repos": ["acme/legacy"]}
own = [mine(30, reqs=[u("bob")]), mine(32, reqs=[u("bob")], events=[asked("bob", NOW - 3 * H)]),
       mine(33, reqs=[u("bob")], labels=["security"]), mine(34, draft=True), mine(35, idle=40), mine(36, repo="acme/legacy"),
       mine(37, threads=[thread("bob", "bob", NOW - H)], reviews=[review("bob", "COMMENTED", "h37")]),
       mine(38, author="owner-bot", kind="Bot", reviews=[review("rabbit", "CHANGES_REQUESTED", "h38", kind="Bot")]),
       mine(39, reqs=[u("bob")], events=[asked("bob", NOW - 3 * H)]),
       mine(40, reqs=[u("bob")], events=[asked("bob", NOW - 3 * H)])]
plan = p.compute(Fake := type("F", (), {"viewer": lambda self: ME, "mine": lambda self, b: own, "reviewed": lambda self, me, since: []})(), c, {}, NOW)
why = {e["pr"]: e["why"] for e in plan["excluded"]}
assert why == {W + "30": "프로그램 PR (live)", W + "32": "프로그램 PR (held)", W + "34": "draft", W + "35": "stale: 40일 움직임 없음",
               "acme/legacy#36": "제외 대상 저장소"}, why
got = {(i["key"], i.get("action")) for i in plan["new"]}
assert got == {("acme/widgets#39@h39/request/bob", "request"), ("acme/widgets#40@h40/request/bob", "request"), ("acme/widgets#33@h33/request/bob", "request"),
               ("acme/widgets#37@h37/review_wait", None), ("acme/widgets#38@h38/retrigger/rabbit", "retrigger"),
               ("acme/widgets#38@h38/review_wait", None)}, got
assert next(i for i in plan["new"] if i["key"].endswith("#38@h38/review_wait"))["why"] == ["리뷰어 없음"]
assert next(i for i in plan["new"] if i["number"] == 37)["why"] == ["답하지 않은 스레드 1건"]
assert all(i["kind"] == "review_wait" and i["once"] is False for i in plan["new"] if "action" in i)
text = p.body(plan)
assert "[내 PR 대기] acme/widgets#37" in text and "리뷰 의뢰 → @bob" in text and "봇 재리뷰 트리거 → @rabbit" in text
assert "U0FAKE" not in text and "Slack U" not in text, "the Slack user id is not a permalink list"

# A title exclude drops the PR before its state is read: no notice and no reminder, while the rest stay as they were.
drop = {**mine(41, reqs=[u("bob")], events=[asked("bob", NOW - 3 * H)]), "title": "Remove legacy config"}
tfake = type("F", (), {"viewer": lambda self: ME, "mine": lambda self, b: own + [drop], "reviewed": lambda self, me, since: []})()
assert "acme/widgets#41@h41/request/bob" in {i["key"] for i in p.compute(tfake, c, {}, NOW)["new"]}
tplan = p.compute(tfake, {**c, "exclude_title_patterns": ["^Remove legacy"]}, {}, NOW)
assert {i["key"] for i in tplan["new"]} == {i["key"] for i in plan["new"]}, tplan["new"]
assert {e["pr"]: e["why"] for e in tplan["excluded"]} == {**why, W + "41": "제외 대상 제목"}

# Actions are not capped by max_items: a reviewer's batch must not split into two messages.
capped = p.compute(Fake, {**c, "max_items": 0}, {}, NOW)
assert len([i for i in capped["new"] if "action" in i]) == 4 and capped["more"] == 2

# log + commit: the send-log decides actions, the ledger only notices. A failed send stays due.
p.write_json(p.STATE / "plan.json", plan)
k = "acme/widgets#39@h39/request/bob"
rowed = p.log(p.STATE, k, "found", NOW, thread="CFAKE:5.5", at=NOW - 600)
assert rowed == {"pr": W + "39", "reviewer": "bob", "head": "h39", "action": "request", "source": "found", "at": NOW - 600,
                 "post_at": None, "thread": "CFAKE:5.5", "scheduled": None}
p.log(p.STATE, "acme/widgets#38@h38/retrigger/rabbit", "sent", NOW)
for bad in (("nope", "sent"), (k, "maybe")):
    try:
        p.log(p.STATE, *bad, NOW)
        raise AssertionError(f"log must refuse {bad}")
    except SystemExit:
        pass
p.commit(p.STATE, c, NOW)
assert set(p.read_json(p.STATE / "ledger.json", {})) == {"acme/widgets#37@h37/review_wait", "acme/widgets#38@h38/review_wait"}
after = p.compute(Fake, c, p.read_json(p.STATE / "ledger.json", {}), NOW + 60)
assert {i["key"] for i in after["new"]} == {"acme/widgets#33@h33/request/bob", "acme/widgets#40@h40/request/bob"}, "the unlogged request is still due"

# schedule: the prompt carries a kind's step only when that kind is configured; --edit keeps the automation id.
script = pathlib.Path("/repo/skills/shepherd-prs/scripts/prs.py")
with_wait = p.schedule_cmd(script, {**c, "inbox_to": "term_x"}, "path:/repo")
prompt = with_wait[with_wait.index("--prompt") + 1]
assert "actions" in prompt and "slack-post" in prompt and "--source refused" in prompt and "직접 하지 않는다" not in prompt
plain = p.schedule_cmd(script, {**c, "kinds": ["review_request"]}, "path:/repo")
assert "actions" not in plain[plain.index("--prompt") + 1] and "직접 하지 않는다" in plain[plain.index("--prompt") + 1]
both = p.schedule_cmd(script, {**c, "kinds": ["unreleased", "review_wait"]}, "path:/repo")
both = both[both.index("--prompt") + 1]
assert both.index("prs.py actions") < both.index("prs.py dispatch"), "the unreleased step gates commit, so it runs last whatever the config order"
edit = p.schedule_cmd(script, c, "path:/repo", edit="auto-1")
assert edit[:4] == ["orca", "automations", "edit", "auto-1"] and edit[edit.index("--trigger") + 1] == c["cron"]
assert edit[edit.index("--timezone") + 1] == "Asia/Tokyo" and "--precheck" in edit

# remind_author: others' PRs where the owner asked for changes and the author stayed silent.
def theirs(n, reviews=(), said=(), head=None, author="dana", kind="User", draft=False, repo="acme/widgets"):
    return {"id": f"PR_{repo}_{n}", "number": n, "title": f"Theirs {n}", "url": f"https://github.com/{repo}/pull/{n}",
            "isDraft": draft, "createdAt": iso(NOW - 5 * 86400), "updatedAt": iso(NOW - 86400), "headRefOid": head or f"h{n}",
            "additions": 3, "deletions": 1, "state": "OPEN", "repository": {"nameWithOwner": repo},
            "author": {"__typename": kind, "login": author}, "reviews": {"nodes": list(reviews)},
            "comments": {"nodes": [{"author": {"login": w}, "createdAt": iso(t)} for w, t in said if w != "thread"]},
            "reviewThreads": {"nodes": [{"comments": {"nodes": [{"author": {"login": author}, "createdAt": iso(t)}]}}
                                        for w, t in said if w == "thread"]}}


def mine_review(state, t, oid, body="", inline=()):
    return {"state": state, "body": body, "submittedAt": iso(t), "commit": {"oid": oid},
            "comments": {"nodes": [{"body": b} for b in inline]}}


def ask(n, **kw):
    return p.author_wait(theirs(n, **kw), ME, cfg)


T = at(13, 10)
assert ask(50, reviews=[mine_review("CHANGES_REQUESTED", T, "h50")])["since"] == T
assert ask(51, reviews=[mine_review("COMMENTED", T, "h51", inline=["[MUST] handle the empty case"])])["author"] == "dana", \
    "a MUST label inline counts like a change request"
assert ask(51, reviews=[mine_review("COMMENTED", T, "h51", body="nit: rename")]) is None, "a plain comment asks nothing"
assert ask(52, reviews=[mine_review("CHANGES_REQUESTED", T, "h52"), mine_review("APPROVED", T + H, "h52")]) is None
assert ask(52, reviews=[mine_review("CHANGES_REQUESTED", T, "h52"), mine_review("COMMENTED", T + H, "h52")])["since"] == T, \
    "a later plain comment keeps the change request standing"
assert ask(53, reviews=[mine_review("CHANGES_REQUESTED", T, "old")]) is None, "a push since the ask: re-review, not a reminder"
assert ask(54, reviews=[mine_review("CHANGES_REQUESTED", T, "h54")], said=[("dana", T + H)]) is None, "the author answered"
assert ask(54, reviews=[mine_review("CHANGES_REQUESTED", T, "h54")], said=[("thread", T + H)]) is None, "in a review thread"
assert ask(54, reviews=[mine_review("CHANGES_REQUESTED", T, "h54")], said=[("dana", T - H), ("erin", T + H)]) is not None
assert p.slack_thread(["https://x.slack.com/archives/C0FAKE/p1760000000123456"]) == "C0FAKE:1760000000.123456"
assert p.slack_thread(["https://x.slack.com/archives/C0FAKE/p1760000000999999?thread_ts=1760000000.123456&cid=C0FAKE"]) \
    == "C0FAKE:1760000000.123456", "a reply's permalink points at its thread"
assert p.slack_thread(["not a link"]) is None
assert ask(59, reviews=[mine_review("CHANGES_REQUESTED", T, "h59")]) and \
    p.author_wait({**theirs(59, reviews=[mine_review("CHANGES_REQUESTED", T, "h59")]), "author": None}, ME, cfg)["bot"] is False, \
    "a deleted account (null author) does not crash the round"

ac = {**cfg, "reviewers": {**cfg["reviewers"], "dana": {"slack": "U0FAKEDANA"}}, "author_wake_prefix": {"helper[bot]": "@helper"}}
human = {**ask(50, reviews=[mine_review("CHANGES_REQUESTED", T, "h50")]), "thread": "C0FAKE:1.0"}
bot = {**p.author_wait(theirs(55, author="helper[bot]", kind="Bot", reviews=[mine_review("CHANGES_REQUESTED", T, "h55")]), ME, cfg),
       "thread": "C0FAKE:2.0"}
due = at(14, 10)
assert p.decide([], [], at(14, 9, 59), ac, [human]) == [], "one business day has not passed"
assert p.decide([], [], at(14, 22), ac, [human]) == [], "outside business hours: wait for the morning round"
r = {a["pr"]: a for a in p.decide([], [], due, ac, [human, bot])}
assert (r[W + "50"]["thread"], r[W + "50"]["slack_user"], r[W + "50"]["prefix"]) == ("C0FAKE:1.0", "U0FAKEDANA", None)
assert (r[W + "55"]["thread"], r[W + "55"]["prefix"], r[W + "55"]["reviewer"]) == (None, "@helper", "helper[bot]"), \
    "a bot gets a PR comment with its wake prefix, even when a Slack thread exists"
assert p.decide([], [], due, cfg, [human])[0]["thread"] is None, "no Slack id for the author: a PR comment"
assert p.decide([], [], due, ac, [{**human, "thread": None}])[0]["prefix"] is None
assert p.decide([], [row(W + "50", "dana", "remind_author", due, head="h50")], due + 86400 * 3, ac, [human]) == [], "once per head"
assert p.decide([], [row(W + "50", "dana", "remind_author", due, head="h50", source="refused")], due, ac,
                [{**human, "head": "h50b"}]) == [], "refused: never again"

p.STATE = pathlib.Path(tempfile.mkdtemp()) / "prs"
seen = p.STATE.parent / "mentions" / "seen.json"
seen.parent.mkdir(parents=True)
seen.write_text(json.dumps({"x": {"category": "review", "pr": "acme/Widgets#50",
                                  "permalink": "https://x.slack.com/archives/C0FAKE/p1760000000123456"}}))
others = [theirs(50, reviews=[mine_review("CHANGES_REQUESTED", T, "h50")]),
          theirs(56, draft=True, reviews=[mine_review("CHANGES_REQUESTED", T, "h56")]),
          theirs(57, author="owner-bot", kind="Bot", reviews=[mine_review("CHANGES_REQUESTED", T, "h57")]),
          theirs(58, reviews=[mine_review("APPROVED", T, "h58")])]
afake = type("F", (), {"viewer": lambda self: ME, "mine": lambda self, b: [], "reviewed": lambda self, me, since: others})()
aplan = p.compute(afake, {**ac, "mentions_seen": str(seen)}, {}, due)
assert [(i["key"], i["thread"], i["once"]) for i in aplan["new"]] == [("acme/widgets#50@h50/remind_author/dana", "C0FAKE:1760000000.123456", False)], \
    aplan["new"]
assert "리뷰 지적 대응 리마인드(작성자) → @dana" in p.body(aplan)
p.write_json(p.STATE / "plan.json", aplan)
assert p.log(p.STATE, "acme/widgets#50@h50/remind_author/dana", "sent", due)["action"] == "remind_author"
assert p.compute(afake, {**ac, "mentions_seen": str(seen)}, {}, due + 60)["new"] == [], "the send-log holds it at this head"

# A row appended by hand (no action/source/at) once made decide() raise KeyError 'action' every round.
import os, stat, subprocess
hand = pathlib.Path(tempfile.mkdtemp()) / "sendlog.jsonl"
hand.write_text("\n".join(json.dumps(x) for x in [
    {"date": "2026-10-13T10:00:00+09:00", "pr": W + "10", "head": "h10", "reviewer": "bob", "slack_ts": "1.0", "by": "lead"},
    {"pr": W + "11", "head": "h11", "reviewer": "bob", "slack_ts": str(at(13, 10))},
    {"pr": W + "12", "reviewer": "bob"}, {"note": "no pr"}, [1],
    row(W + "13", "bob", "request", at(13, 9))]) + "\n")
rows = p.sendlog_rows(hand)
assert [(r["pr"], r["action"], r["source"], r["at"]) for r in rows] == [
    (W + "10", "request", "sent", at(13, 10)), (W + "11", "request", "sent", at(13, 10)),
    (W + "13", "request", "sent", at(13, 9))], rows
assert acts([one, two], rows, now=at(13, 12)) == [], "a hand-written request counts: bob is not asked again the same day"
assert acts([one, two], rows, now=at(14, 10)) == [("remind", W + "10", "bob"), ("remind", W + "11", "bob")]

# A GitHub 5xx is retried; any other failure is not.
gh = pathlib.Path(tempfile.mkdtemp())
calls = gh / "calls"
fake = gh / "gh"
fake.write_text(f"""#!/bin/sh
echo x >> {calls}
[ "$(wc -l < {calls})" -le "$FAILS" ] && {{ echo "gh: $ERR" >&2; exit 1; }}
echo '{{"data":{{"viewer":{{"login":"owner"}}}}}}'
""")
fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
p.GH, p.GH_RETRY_WAITS = str(fake), (0, 0)
for fails, err, ok, n in ((2, "HTTP 502", True, 3), (3, "HTTP 504", False, 3), (1, "HTTP 401", False, 1)):
    calls.unlink(missing_ok=True)
    os.environ.update(FAILS=str(fails), ERR=err)
    try:
        assert p.gh_graphql("query{viewer{login}}") == {"viewer": {"login": "owner"}} and ok, (fails, err)
    except RuntimeError as e:
        assert not ok and err in str(e), (fails, err, e)
    assert len(calls.read_text().splitlines()) == n, (fails, err)

# CLI: actions prints one JSON line per action in plan.json; log appends the row and parses numbers.
tmp = pathlib.Path(tempfile.mkdtemp())
conf = tmp / "agent-skills.json"
conf.write_text(json.dumps({"prs": {"kinds": ["review_wait"]}}))
p.write_json(tmp / "prs" / "plan.json", plan)
run = lambda *a: subprocess.run([sys.executable, str(pathlib.Path(p.__file__)), *a, "--config", str(conf)],
                                env={**os.environ, "AGENT_SKILLS_STATE": str(tmp)}, capture_output=True, text=True)
lines = [json.loads(x) for x in run("actions").stdout.splitlines()]
assert sorted(x["action"] for x in lines) == ["request", "request", "request", "retrigger"] and "post_at" in lines[0]
assert all((x["github"] is not None) == (x["action"] == "request") for x in lines), lines
conf.write_text(json.dumps({"prs": {"kinds": ["review_wait"], "profiles": [{"match": "acme/*", "after_hours": "send-now"}]}}))
pol = json.loads(run("policy", "acme/widgets").stdout)
assert pol["after_hours"] == "send-now" and pol["weekend_days"] == [5, 6] and pol["assign_reviewers"] is True, pol
assert json.loads(run("policy", "other/x").stdout)["after_hours"] == "schedule-certain-only"
conf.write_text(json.dumps({"prs": {"kinds": ["review_wait"]}}))
out = run("log", "--key", k, "--source", "scheduled", "--scheduled", "Q9", "--post-at", str(at(15, 9)), "--thread", "CFAKE:7.7")
assert out.returncode == 0, out
assert p.read_jsonl(tmp / "prs" / "sendlog.jsonl")[0]["post_at"] == at(15, 9)
assert run("log", "--key", k, "--source", "bogus").returncode != 0

print("test_review_wait: ok")
