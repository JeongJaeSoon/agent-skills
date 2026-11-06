"""Offline tests for fleet.py: hierarchy, inbox, PR events, unread, chat guards and adoption, all on invented data.

Run: python3 test_fleet.py
"""
import contextlib, datetime as dt, io, json, os, pathlib, re, shutil, subprocess, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import dash_demo

root = pathlib.Path(tempfile.mkdtemp(prefix="test-fleet-"))
os.environ.update(dash_demo.build(root))
import fleet  # noqa: E402  after the env points the state directory at root

fleet.CLAUDE_HOMES = ()  # only the CLAUDE_CONFIG_DIR a block points at, never this machine's sessions

fetch_runs = fleet.fetch_runs  # later swapped for the fake world's

state_dir = pathlib.Path(os.environ["ORCH_FLEET_STATE"])
now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
clock = [now]
world = dash_demo.FakeFleetWorld(now)
secret = "ghp_" + "a1" * 18
world.worktrees[0]["agents"][0]["prompt"] = f"deploy with GH_TOKEN={secret}"
f = world.fleet(clock=lambda: clock[0])


def items(st, t=None):
    return [i for i in st["items"] if t is None or i["type"] == t]


# Hierarchy: the session bound to the newest Run is the root; workers sit under it, sessions you opened sit under it too.
st = f.tick(force=True)
ss = {s["id"]: s for s in st["sessions"]}
assert st["root"] == "wt-coord", st["root"]

# Test stacks (stacks.py): off without `dashboard.stacks`; with it, one row per stack, read at most once a minute.
assert st["stacks"] == [], "no key, no stacks, no network"
_cfg = pathlib.Path(os.environ["AGENT_SKILLS_CONFIG"])
_cfg.write_text(json.dumps({"dashboard": {"stacks": [{"name": "stg", "url": "https://stack.example.test", "repo": "acme/app"}]}}))
_asked = []
f.fetch_stacks = lambda at, entries: _asked.append(at) or [{"name": e["name"], "ready": True} for e in entries]
st = f.tick(force=True)
assert st["stacks"] == [{"name": "stg", "ready": True}] and len(_asked) == 1, st["stacks"]
f.tick()
assert len(_asked) == 1, "within STACKS_EVERY the rows are kept, not read again"
_cfg.write_text(json.dumps({"dashboard": {"stacks": [{"name": "other", "url": "https://other.example.test", "repo": "acme/app"}]}}))
assert f.tick()["stacks"] == [{"name": "other", "ready": True}] and len(_asked) == 2, "a changed entry is read at once"
_cfg.unlink()
assert f.tick(force=True)["stacks"] == [] and len(_asked) == 2, "removing the key turns it off"
_cfg.write_text(json.dumps({"dashboard": {"stacks": [{"name": "other", "url": "https://other.example.test", "repo": "acme/app"}]}}))
assert f.tick()["stacks"] == [{"name": "other", "ready": True}] and len(_asked) == 3, "and setting it again reads at once"
f.fetch_stacks = lambda at, entries: 1 / 0
assert f.tick(force=True)["stacks"][0]["error"] == "collect: ZeroDivisionError", "a broken fetcher does not stop the tick"
_cfg.unlink()
f.tick(force=True)
assert ss["wt-coord"]["kind"] == "orchestrator" and ss["wt-coord"]["parent"] is None
assert all(ss[w]["kind"] == "task" and ss[w]["parent"] == "wt-coord" for w in ("wt-login", "wt-export", "wt-docs"))
assert ss["wt-scratch"]["kind"] == "standalone" and ss["wt-scratch"]["parent"] == "wt-coord"
# A lead: the root dispatched it and it runs its own Run; its workers hang under it whether its Run or Orca's parent says so.
assert (ss["wt-lead"]["kind"], ss["wt-lead"]["parent"]) == ("orchestration", "wt-coord")
assert [(ss[w]["kind"], ss[w]["parent"]) for w in ("wt-invoice", "wt-tax")] == [("task", "wt-lead"), ("standalone", "wt-lead")]
assert {w: ss[w]["phase"] for w in ss} == {"wt-coord": "working", "wt-login": "idle", "wt-export": "working",
                                            "wt-docs": "waiting", "wt-scratch": "idle", "wt-old": "offline",
                                            "wt-lead": "working", "wt-invoice": "working", "wt-tax": "idle"}, ss
assert ss["wt-login"]["task_title"] == "ACME-101 login flow" and ss["wt-login"]["terminal"] == "term_login"
assert all(v["ok"] for v in st["sources"].values()), st["sources"]

# A subagent the hook logged from a session's terminal comes back on that session; one that stopped does not.
assert all(s["subagents"] == [] for s in st["sessions"]), st["sessions"]
for row in ({"event": "start", "agent": "sub1", "handle": "term_login", "type": "Explore", "desc": "Read the login flow"},
            {"event": "start", "agent": "sub2", "handle": "term_login"}, {"event": "stop", "agent": "sub2"}):
    fleet.append_line(state_dir / fleet.SUBAGENTS, {"v": 1, "at": fleet.iso(now), **row})
ss = {s["id"]: s for s in f.tick(force=True)["sessions"]}
assert ss["wt-login"]["subagents"] == [{"agent": "sub1", "type": "Explore", "desc": "Read the login flow",
                                        "started_at": fleet.iso(now), "stale": False}], ss["wt-login"]
(state_dir / fleet.SUBAGENTS).unlink()

# Every row names its project: Orca's project for a session, the task's worktree for a gate, the terminal for a decision.
assert {s["id"]: s["project"] for s in st["sessions"]} == {"wt-coord": "platform", "wt-login": "launchpad", "wt-export": "launchpad",
                                                         "wt-docs": "launchpad", "wt-scratch": "tools", "wt-old": "tools",
                                                         "wt-lead": "platform", "wt-invoice": "launchpad", "wt-tax": "launchpad"}
assert [(i["project"], i["session"]) for i in items(st, "approval") if i["source"] == "orca-gate"] == [("launchpad", "wt-coord")]
assert [i["project"] for i in items(st, "decision")] == ["platform"] and all(i["project"] for i in items(st))
assert {t["id"]: t["project"] for t in st["tasks"]} == {"task_login": "launchpad", "task_export": "launchpad", "task_docs": "launchpad",
                                                       "task_lead": "platform", "task_invoice": "launchpad",
                                                       **{t: None for t, _ in dash_demo.PLUGIN_TASKS}}  # cards with no worktree left
assert fleet.project_of("/h/orca/workspaces/acme-api/fix-1") == "acme-api"
assert fleet.project_of("/h/orca/workspaces/acme-api/fix-1", "acme-api", "o/renamed") == "renamed"  # the repository was renamed on GitHub
assert fleet.project_of("/h/orca/workspaces/acme-api/fix-1", "orca-name", None) == "orca-name"
# a released worktree's origin is unreadable: its task takes the project its workspace directory's live sessions have now
assert fleet.workspace_projects([{"path": "/h/orca/workspaces/acme-api/fix-2", "project": "renamed"}, {"path": "/src/x", "project": "x"},
                                 {"path": None, "project": "y"}, {"path": "/h/orca/workspaces/none/a", "project": None}]) == {"acme-api": "renamed"}
assert fleet.project_of("/src/app-wt", None, "o/app") == "app" and fleet.project_of("/src/app-wt") == "app-wt"
assert fleet.project_of(None) is None

# Secrets never reach disk.
for name in ("state.json", "memory.json"):
    assert secret not in (state_dir / name).read_text(), name

# PRs by branch, plus mine that no worktree has checked out (found by the search, open or merged within a day), and
# what each one asks of you: only an owned PR raises items.
prs = {p["number"]: p for p in st["prs"]}
assert st["prs"] and all(isinstance(p.get("stack"), list) and "base" in p for p in st["prs"]), "every PR carries its stack chain"
assert {n: prs[n]["session"] for n in prs} == {41: "wt-login", 42: "wt-export", 43: "wt-docs", 44: "wt-invoice", 7: None, 6: None}, prs
assert (prs[7]["repo"], prs[7]["merge_state"], prs[7]["coderabbit"], prs[6]["state"]) == ("acme/tools", "BEHIND", "rate_limited", "MERGED")
assert (prs[42]["unresolved"], prs[43]["draft"], prs[43]["author"]) == (1, True, "dev-erin"), prs[42]
assert [fleet.coderabbit({"commits": world.roll("SUCCESS", cr)}) for cr in (("PENDING", "Review in progress"), ("SUCCESS", "Review completed"),
        ("SUCCESS", "Review skipped: automatic reviews are disabled"), ("FAILURE", "Review failed"))] == [
        "in_progress", "reviewed", "skipped", "failed"]
assert fleet.coderabbit({"commits": {"nodes": [{"commit": {"status": None}}]}}) is None
assert {r["login"]: r["status"] for r in prs[43]["reviewers"]} == {"rev-carol": "requested", "team:docs": "requested"}
assert {r["login"]: r["status"] for r in prs[42]["reviewers"]} == {"rev-bob": "changes_requested", "lint-bot[bot]": "commented"}
got = sorted((i["type"], i["session"]) for i in items(st))
assert got == sorted([("prompt", "wt-docs"), ("question", "wt-export"), ("approval", "wt-coord"), ("login", "wt-scratch"),
                      ("decision", "wt-coord"), ("ready_to_merge", "wt-login"), ("changes_requested", "wt-export"),
                      ("review_comment_received", "wt-export"), ("ci_failed", "wt-export"),
                      ("watcher_down", "wt-coord")]), got  # the demo orchestrator never ran `orch watch-cards`

# A session's badge is the number of Needs you items on it: the badges plus the sessionless items are the inbox.
def badges_agree(st):
    per = {s["id"]: sum(1 for i in st["items"] if i.get("session") == s["id"]) for s in st["sessions"]}
    assert {s["id"]: s["unread"] for s in st["sessions"]} == per, per
    owed = {s["id"]: sum(1 for i in st["items"] if i.get("session") == s["id"] and fleet.needs_rank(i) < 2) for s in st["sessions"]}
    assert {s["id"]: s["needs_owner"] for s in st["sessions"]} == owed, owed
    assert sum(per.values()) + sum(1 for i in st["items"] if i.get("session") not in per) == len(st["items"])


badges_agree(st)
assert ss["wt-export"]["unread"] == 4 and ss["wt-docs"]["unread"] == 1 and ss["wt-scratch"]["unread"] == 1, ss
assert ss["wt-export"]["needs_owner"] < ss["wt-export"]["unread"] and ss["wt-login"]["needs_owner"] >= 1, ss  # ci_failed is the agent's
assert not (state_dir / "pr-events.jsonl").exists()  # the first snapshot is a baseline, not a change

# Dismissing an item removes it from the inbox and from its session's badge alike.
fleet.dismiss(f, next(i["key"] for i in items(st, "ci_failed")))
st = f.tick(force=True)
ss = {s["id"]: s for s in st["sessions"]}
assert ss["wt-export"]["unread"] == 3 and not items(st, "ci_failed"), (ss["wt-export"], items(st, "ci_failed"))
badges_agree(st)

# The owner's now-list shows in Needs you through the collector, and a dismiss does not hide it: only `orch board done` does.
real_open = fleet.board_open
fleet.board_open = lambda: {"owner_todo": [{"id": "b9", "title": "acme/app#3 리뷰", "updated_at": "2026-10-30T09:00:00Z"}]}
st = f.tick(force=True)
assert [i["title"] for i in items(st, "owner_todo")] == ["acme/app#3 리뷰"], items(st, "owner_todo")
fleet.dismiss(f, "b9")
st = f.tick(force=True)
assert [i["key"] for i in items(st, "owner_todo")] == ["b9"], items(st, "owner_todo")
fleet.board_open = lambda: {}
st = f.tick(force=True)
assert not items(st, "owner_todo")
fleet.board_open = real_open
badges_agree(st)

# Needs you order: what only the owner settles, then a merge call waiting on them, then the rest; newest first in each.
ranks = [fleet.needs_rank(i) for i in items(st)]
assert ranks == sorted(ranks) and ranks.count(0) == 5 and ranks.count(1) == 1, [(i["type"], r) for i, r in zip(items(st), ranks)]
for r in set(ranks):
    ats = [i.get("at") or "" for i in items(st) if fleet.needs_rank(i) == r]
    assert ats == sorted(ats, reverse=True), (r, ats)
assert [i["type"] for i in items(st)][5] == "ready_to_merge"
# Newest first by the moment, not the text: skills-sync's `+0900` 15:31 is 06:31Z, older than a `Z` 10:00.
mixed = [{"type": "reload_pending", "at": "2026-10-27T15:31:21+0900"}, {"type": "sync_stalled", "at": "2026-10-27T10:00:00Z"},
         {"type": "custom", "at": None}, {"type": "decision", "at": "2026-10-26T00:00:00Z"}]
assert [i["type"] for i in sorted(mixed, key=fleet.needs_order)] == ["decision", "sync_stalled", "reload_pending", "custom"]
# macOS's /usr/bin/python3 is 3.9, whose fromisoformat takes no `+0900`.
for py in [sys.executable] + [p for p in ("/usr/bin/python3",) if os.path.exists(p)]:
    out = subprocess.run([py, "-c", "import fleet; print(fleet.parse('2026-10-27T15:31:21+0900').isoformat())"],
                         cwd=pathlib.Path(__file__).parent, capture_output=True, text=True)
    assert out.stdout.strip() == "2026-10-27T15:31:21+09:00", (py, out.stdout, out.stderr)
# A PR a held board item names goes last, marked with the hold, until its date comes or the item leaves the board.
import store  # noqa: E402
ahead = (now + dt.timedelta(days=2)).astimezone().strftime("%m/%d 09:00")


def hold(trigger):
    store.load_board({"later": [{"title": "acme/launchpad#42 마무리", "trigger": trigger}]})
    return f.tick(force=True)


st = hold(f"{ahead} 이후")
pr42 = [i for i in items(st) if i.get("pr") == "acme/launchpad#42"]
assert pr42 and all(i["held"] == f"{ahead} 이후" for i in pr42) and items(st)[-len(pr42):] == pr42, [(i["type"], i.get("held")) for i in items(st)]
assert not any(i.get("held") for i in items(st) if i.get("pr") != "acme/launchpad#42")
st = hold((now - dt.timedelta(days=1)).astimezone().strftime("%m/%d") + " 이후")
assert not any(i.get("held") for i in items(st)) and [fleet.needs_rank(i) for i in items(st)] == ranks
st = hold("acme/launchpad#41 머지 뒤")  # no date: held while the PR its trigger names is open
assert all(i.get("held") for i in items(st) if i.get("pr") == "acme/launchpad#42")
assert not any(i.get("held_todo") for i in items(st))
# A to-do naming the PR holds it too, tagged as the to-do's, not a held item's.
store.load_board({"todo": [{"title": "acme/launchpad#42 머지·배포", "who": "오너", "after": "acme/launchpad#41 머지 뒤"}]})
pr42 = [i for i in items(f.tick(force=True)) if i.get("pr") == "acme/launchpad#42"]
assert pr42 and all(i["held"] == "acme/launchpad#41 머지 뒤" and i["held_todo"] for i in pr42), pr42
store.load_board()
st = f.tick(force=True)
assert not any(i.get("held") for i in items(st)) and [fleet.needs_rank(i) for i in items(st)] == ranks
assert fleet.trigger_at("2026-11-02 14:30", now).strftime("%Y-%m-%d %H:%M") == "2026-11-02 14:30"
assert fleet.trigger_at("v1.2 배포 뒤", now) is None and fleet.trigger_at("acme/app#9 머지 뒤", now) is None

# A reload skills-sync could not send sits on its terminal's session (and counts there), titled with the terminal;
# one for a terminal Orca no longer lists is gone.
pending_path = state_dir.parent / "reload-pending.json"
fleet.write_atomic(pending_path, json.dumps({"since": "t1", "failed": {
    "term_login": {"reason": "composer holds a draft"}, "term_export": {"reason": "title shows the agent is busy"},
    "term_gone": {"reason": "x"}}}))
st = f.tick(force=True)
assert not items(st, "reload_pending"), "still being retried: not the owner's yet"
fleet.RELOAD_FAILS.update(term_login=fleet.RELOAD_ALERT_TRIES, term_export=fleet.RELOAD_ALERT_TRIES)
st = f.tick(force=True)
assert sorted((i["session"], i["title"], i["detail"]) for i in items(st, "reload_pending")) == [
    ("wt-export", "Reload not sent: ◑ ACME-102", "title shows the agent is busy"),
    ("wt-login", "Reload not sent: ✳ ACME-101", "composer holds a draft")], items(st, "reload_pending")
badges_agree(st)


# Once its session is idle the collector sends the reload again through skills-sync; a working one is left alone.
class FakeBroadcast:
    def __init__(self):
        self.calls = []

    def broadcast(self, kind, dry_run, only=None):
        self.calls.append((kind, dry_run, only))
        p = json.loads(pending_path.read_text())
        p["failed"].pop(only)
        fleet.write_atomic(pending_path, json.dumps(p))
        return 0


fake = FakeBroadcast()
assert fleet.retry_reloads(fake) == ["term_login"] and fake.calls == [(None, False, "term_login")], fake.calls
assert fleet.retry_reloads(fake) == [] and len(fake.calls) == 1  # nothing idle is left pending
fleet.write_atomic(pending_path, json.dumps({**json.loads(pending_path.read_text()), "failed": {
    "term_login": {"reason": "composer holds a draft"}, "term_export": {"reason": "title shows the agent is busy"}}}))
assert fleet.retry_reloads(fake) == [] and len(fake.calls) == 1  # refused again: not retried for RELOAD_RETRY_S
fake.broadcast(None, False, only="term_login")
fake.calls.pop()
st = f.tick(force=True)
assert [i["session"] for i in items(st, "reload_pending")] == ["wt-export"], items(st, "reload_pending")
fleet.RELOAD_TRIED.clear()
pending_path.unlink()
assert fleet.retry_reloads(fake) == [] and len(fake.calls) == 1
st = f.tick(force=True)
assert any(e["kind"] == "item_resolved" and e["text"] == "reload_delivered: Reload not sent: ◑ ACME-102" for e in st["timeline"])
# A reload is the owner's only once the idle terminal refused the dashboard's retry reload_alert_tries times in a row,
# and it leaves with a timeline line when it is delivered.
class Refusing(FakeBroadcast):
    def broadcast(self, kind, dry_run, only=None):
        self.calls.append(only)
        return 1


fleet.write_atomic(pending_path, json.dumps({"since": "t2", "failed": {"term_login": {"reason": "prompt box not recognized"}}}))
refusing = Refusing()
for n in range(fleet.RELOAD_ALERT_TRIES):
    assert not items(f.tick(force=True), "reload_pending"), n
    fleet.RELOAD_TRIED.clear()
    assert fleet.retry_reloads(refusing) == []
assert refusing.calls == ["term_login"] * fleet.RELOAD_ALERT_TRIES
st = f.tick(force=True)
assert [i["session"] for i in items(st, "reload_pending")] == ["wt-login"], items(st, "reload_pending")
delivered = lambda st: sum(e["kind"] == "item_resolved" and e["text"] == "reload_delivered: Reload not sent: ✳ ACME-101"  # noqa: E731
                           for e in st["timeline"])
n = delivered(st)
pending_path.unlink()
st = f.tick(force=True)
assert not items(st, "reload_pending") and delivered(st) == n + 1
assert delivered(f.tick(force=True)) == n + 1, "once"

# A push after approval, and CI recovering, become PR events for the platform side and new inbox state.
world.advance()
clock[0] = now + dt.timedelta(minutes=10)
st = f.tick(force=True)
rows = [json.loads(l) for l in (state_dir / "pr-events.jsonl").read_text().splitlines()]
assert sorted((r["pr"], r["kind"]) for r in rows) == [(41, "approval_stale"), (41, "head_pushed"), (42, "checks_recovered")], rows
r41 = next(r for r in rows if r["pr"] == 41)
assert r41["owner"] == "ctx_login" and r41["owner_kind"] == "dispatch" and r41["repo"] == "acme/launchpad", r41
assert all(len(json.dumps(r)) < 4096 for r in rows)
assert [i["session"] for i in items(st, "approval_stale")] == ["wt-login"] and not items(st, "ready_to_merge"), items(st)
assert {e["kind"] for e in st["timeline"]} >= {"pr_approval_stale", "pr_checks_recovered"}

# A prompt typed into a session is an event; the items still open on it keep counting.
world.worktrees[3]["agents"][0]["prompt"] = "Approved, go ahead."
clock[0] = now + dt.timedelta(minutes=11)
st = f.tick(force=True)
ss = {s["id"]: s for s in st["sessions"]}
assert any(e["kind"] == "prompt" and e["session"] == "wt-docs" for e in st["timeline"])
badges_agree(st)

# Chat: only a connected agent terminal of a listed session, one line, every attempt logged, secrets masked.
class Sender:
    def __init__(self, result):
        self.result, self.calls = result, []

    def try_send(self, handle, text):
        self.calls.append((handle, text))
        return self.result


ok = Sender((True, None))
assert fleet.send("wt-export", "use CSV", sender=ok) == (200, {"ok": True, "reason": None, "typed": True, "fallback": None})
assert ok.calls == [("term_export", "use CSV")]
code, out = fleet.send("wt-export", "x", sender=Sender((False, "sent, but the screen does not show it was taken")))
assert code == 200 and out["typed"] and out["fallback"] is None, out  # typed: the page must not offer a blind retry
code, out = fleet.send("wt-export", "x", sender=Sender((False, "title shows the agent is busy")))
assert not out["typed"] and out["fallback"] == "copy", out
never = Sender((True, None))
assert fleet.send("wt-scratch", "x", handle="term_shell", sender=never)[0] == 403  # a plain shell is not a chat target
assert fleet.send("wt-nowhere", "x", sender=never)[0] == 403
assert fleet.send("wt-export", "two\nlines", sender=never)[0] == 400
assert fleet.send("wt-export", "   ", sender=never)[0] == 400
assert never.calls == []
fleet.load_sync, real_load = (lambda: None), fleet.load_sync
code, out = fleet.send("wt-export", f"token {secret}")
fleet.load_sync = real_load
assert out == {"ok": False, "reason": "safety check unavailable (scripts/sync.py not found)", "fallback": "copy"}, out
log = (state_dir / "sends.jsonl").read_text()
assert len(log.splitlines()) == 8 and secret not in log, log

# The brief's Orca button names a session by its worktree id: the handle is the one its agent terminal has now.
# `orca terminal switch` moves only the desktop on the machine that runs it, so a switch Orca confirms is still the
# host's alone, and one it does not confirm is no success. Every reply carries what to type or find on another device.
export = next(s for s in st["sessions"] if s["id"] == "wt-export")
where = f"workspace: {export['name']}\nterminal: term_export\nworktree: {export['path']}"
switched = []


def switch_to(focus):
    """Orca's whole `terminal switch --json` reply around this focus, as the default switch returns it."""
    return lambda h: switched.append(h) or {"ok": True, "result": {"focus": focus}}


code, out = fleet.open_session("wt-export", switch=switch_to({"handle": "term_export", "tabId": "t1", "navigated": True}))
assert (code, out["ok"], out["navigated"], out["copy"]) == (200, True, True, where) and switched == ["term_export"], out
assert "host" in out["reason"] and out["warning"] is None, out
code, out = fleet.open_session("wt-export", switch=switch_to({"handle": "term_export", "tabId": "t1", "navigated": False}))
assert (out["ok"], out["navigated"], out["copy"]) == (False, False, where) and out["reason"], out
# A reply without the field is passed through as the CLI printed it, with one warning line: unknown is not success.
raw = {"ok": True, "result": {"focus": {"handle": "term_export", "tabId": "t1"}}}
fleet.run_json, real_run_json = (lambda cmd, timeout: switched.append(cmd[-2]) or raw), fleet.run_json
code, out = fleet.open_session("wt-export")
fleet.run_json = real_run_json
assert (out["ok"], out["navigated"], out["result"], out["copy"]) == (False, None, raw, where), out
assert out["warning"] and "\n" not in out["warning"], out
# A long reply reaches the page whole; the log keeps a cut copy under its 4 KiB line cap.
big = {"ok": True, "result": {"focus": {"handle": "term_export"}, "extra": "x" * 5000}}
code, out = fleet.open_session("wt-export", switch=lambda h: switched.append(h) or big)
assert out["result"] == big and len(json.loads((state_dir / "sends.jsonl").read_text().splitlines()[-1])["result"]) <= 1001, out["warning"]
switched.pop()
code, out = fleet.open_session("wt-export", switch=lambda h: switched.append(h))
assert (out["ok"], out["navigated"], out["result"]) == (False, None, None) and out["warning"], out
assert fleet.open_session("wt-nowhere", switch=switched.append)[0] == 409 and switched == ["term_export"] * 4


def refuse(handle):
    raise fleet.SourceError("no such terminal")


code, out = fleet.open_session("wt-export", switch=refuse)
assert (code, out["ok"], out["reason"], out["copy"]) == (200, False, "no such terminal", where), out
# With the paired server's name set, the copy is the command that switches the device it is typed on.
pathlib.Path(os.environ["ORCH_FLEET_CONFIG"]).write_text(json.dumps({"orca_environment": "home server"}))
code, out = fleet.open_session("wt-export", switch=switch_to({"navigated": True}))
assert out["copy"] == "orca terminal switch --environment 'home server' --terminal term_export", out
pathlib.Path(os.environ["ORCH_FLEET_CONFIG"]).write_text(json.dumps({"orca_environment": ""}))
assert fleet.open_session("wt-export", switch=switch_to({"navigated": True}))[1]["copy"] == where
pathlib.Path(os.environ["ORCH_FLEET_CONFIG"]).unlink()
(state_dir / "sends.jsonl").write_text(log)  # the counts below are of sends

# Permission prompts. Fake screens in the shape Claude Code 2.1.282 draws (measured); every name here is invented.
docs = next(i for i in items(st) if i["session"] == "wt-docs")
assert docs["type"] == "prompt" and docs["prompt"] == "prompt-docs-1" and docs["handle"] == "term_docs", docs
assert docs["answers"] == ["approve", "deny"] and docs["detail"] == "npm publish --dry-run", docs
RULE = "─" * 72
IDLE = ["⏺ Done.", RULE, "❯ ", RULE, "  ~/work/app ⎇ main"]
BASH = [RULE, " Bash command", " Tip: auto mode handles these prompts for you — choose \"switch to auto mode\" below",
        "   touch /tmp/demo-app/out.txt", "   Create out.txt", " Do you want to proceed?", " ❯ 1. Yes",
        "   2. Yes, and always allow access to /tmp/demo-app from this project",
        "   3. Yes, and switch to auto mode · auto mode handles these prompts for you", "   4. No", " Esc to cancel · Tab to amend"]
HOOK_ASK = [RULE, " Tool use", "   plugin:demo:chat — post a message (MCP)", "   channel: \"general\"", "   text: \"release notes are",
            "   ready for review\"", " │ Hook PreToolUse:mcp__plugin_demo_chat__post requires confirmation for this tool:", " │ unconfirmed destination.",
            " Do you want to proceed?", " ❯ 1. Yes", "   2. Yes, and don't ask again for plugin:demo:chat — post a message commands in",
            "   /Users/someone/work/a-long-project-path", "   3. No", " Esc to cancel · Tab to amend"]
QUESTION = [RULE, "←  ☐ Format  ✔ Submit  →", "Which export format?", "❯ 1. CSV (Recommended)", "     Finance opens it in a spreadsheet",
            "  2. JSON", "  3. Type something.", RULE, "  4. Chat about this", "Enter to select · Tab/Arrow keys to navigate · Esc to cancel"]
TRUST = [RULE, " Accessing workspace:", " /tmp/demo-app", " Quick safety check: Is this a project you created or one you trust?",
         " ❯ No, exit", "   Yes, I trust this folder", " Enter to confirm · Esc to cancel"]
STICKY = [RULE, " Bash command", "   make deploy", " Do you want to proceed?", " ❯ 1. Yes, and don't ask again for make commands",
          "   2. No", " Esc to cancel · Tab to amend"]
bash_rec = {"id": "p-bash", "handle": "term_docs", "tool": "Bash", "input": {"command": "touch /tmp/demo-app/out.txt"}}
hook_rec = {"id": "p-hook", "handle": "term_docs", "tool": "mcp__plugin_demo_chat__post",
            "input": {"channel": "general", "text": "release notes are ready for review"}}
d = fleet.parse_dialog(BASH)
assert [l for _, l in d["options"]] == ["Yes", "Yes, and always allow access to /tmp/demo-app from this project",
                                        "Yes, and switch to auto mode", "No"], d
assert (fleet.option_for(d, "approve"), fleet.option_for(d, "deny")) == ("1", "4")
d = fleet.parse_dialog(HOOK_ASK)
assert len(d["options"]) == 3 and d["options"][1][1].endswith("a-long-project-path"), d  # a wrapped label stays one option
assert (fleet.option_for(d, "approve"), fleet.option_for(d, "deny")) == ("1", "3")
assert fleet.prompt_verdict(HOOK_ASK, hook_rec)[0] and fleet.prompt_verdict(BASH, bash_rec)[0]
assert fleet.prompt_verdict(BASH, hook_rec) == (None, "the dialog on screen is not the recorded request")
assert fleet.prompt_verdict(BASH, {"input": {"command": "touch /tmp/demo-app/other.txt"}})[0] is None
wrapped = BASH[:3] + ["   touch /tmp/demo-", "   app/out.txt"] + BASH[4:]
assert fleet.prompt_verdict(wrapped, bash_rec)[0], "a command wrapped at the terminal edge still matches"
for screen in (IDLE, QUESTION, TRUST, []):
    assert fleet.parse_dialog(screen) is None and fleet.prompt_verdict(screen, bash_rec)[1] == "no permission dialog on screen"
assert fleet.option_for(fleet.parse_dialog(STICKY), "approve") is None, "a Yes that saves a rule is never pressed"
assert fleet.option_for(fleet.parse_dialog(STICKY), "deny") == "2"


class FakeSync:
    """sync.py's lock, screen reader and orca call. Reads return the screens in order, the last one repeating."""
    Stop = type("Stop", (Exception,), {})
    SETTLE_S = CONFIRM_S = 0

    def __init__(self, *screens, busy=False):
        self.screens, self.busy, self.sent = list(screens), busy, []

    @contextlib.contextmanager
    def locked(self, wait_s=0):
        yield not self.busy

    def screen(self, handle):
        return (self.screens.pop(0) if len(self.screens) > 1 else self.screens[0]), None

    def orca(self, *args):
        self.sent.append(args)
        return {}


def arm(rec):
    fleet.write_atomic(state_dir / "prompts" / "term_docs.json", json.dumps({"v": 1, "at": fleet.iso(fleet.utcnow()), **rec}))


arm(bash_rec)
fs = FakeSync(BASH, BASH, IDLE)
assert fleet.answer_prompt("wt-docs", "p-bash", "approve", sender=fs) == (200, {"ok": True, "reason": None, "typed": True, "key": "1", "fallback": None})
assert fs.sent == [("terminal", "send", "--terminal", "term_docs", "--text", "1")]
assert not (state_dir / "prompts" / "term_docs.json").exists(), "an answered prompt is gone"
arm(bash_rec)
fs = FakeSync(BASH, BASH, IDLE)
assert fleet.answer_prompt("wt-docs", "p-bash", "deny", sender=fs)[1]["key"] == "4" and fs.sent[0][-1] == "4"
arm(bash_rec)
fs = FakeSync(BASH, BASH, BASH)
out = fleet.answer_prompt("wt-docs", "p-bash", "approve", sender=fs)[1]
assert not out["ok"] and out["typed"] and out["fallback"] is None, out  # pressed: the page must not offer a blind retry
MOVED = BASH[:6] + ["   1. Yes", " ❯ 2. " + BASH[7].strip()[3:]] + BASH[8:]  # the human moved the cursor
# Above the dialog the pending tool's bullet blinks between reads; that alone is no reason to refuse.
arm(bash_rec)
fs = FakeSync(["  Creating out.txt"] + BASH, ["⏺ Creating out.txt"] + BASH, IDLE)
assert fleet.answer_prompt("wt-docs", "p-bash", "approve", sender=fs)[1]["ok"] and fs.sent[0][-1] == "1"
refusals = [  # (screens, action, reason): nothing is pressed
    ((IDLE,), "approve", "no permission dialog on screen"),
    ((QUESTION,), "approve", "no permission dialog on screen"),
    ((HOOK_ASK,), "approve", "the dialog on screen is not the recorded request"),
    ((BASH, MOVED), "approve", "the dialog changed between two reads (someone may be answering it)"),
    (([],), "deny", "not a connected, writable Claude terminal"),
]
for screens, action, reason in refusals:
    arm(bash_rec)
    fs = FakeSync(*screens)
    assert fleet.answer_prompt("wt-docs", "p-bash", action, sender=fs) == (200, {"ok": False, "reason": reason, "typed": False,
                                                                                  "key": None, "fallback": "open"}), reason
    assert fs.sent == [] and (state_dir / "prompts" / "term_docs.json").exists()
arm({**bash_rec, "input": {"command": "make deploy"}})
fs = FakeSync(STICKY)
out = fleet.answer_prompt("wt-docs", "p-bash", "approve", sender=fs)[1]
assert out["reason"].startswith("no one-time Yes option") and fs.sent == [], out
fs = FakeSync(BASH, busy=True)
assert fleet.answer_prompt("wt-docs", "p-bash", "approve", sender=fs)[1]["reason"].startswith("a sync or broadcast is running")
assert fleet.answer_prompt("wt-docs", "p-other", "approve", sender=fs)[0] == 409  # a prompt id the page did not see
assert fleet.answer_prompt("wt-export", "p-bash", "approve", sender=fs)[0] == 409  # another session's terminal
assert fleet.answer_prompt("wt-docs", "p-bash", "always", sender=fs)[0] == 400
assert fs.sent == []
opened = []
fleet.orca, real_orca = (lambda *a, **k: opened.append(a) or {}), fleet.orca
assert fleet.answer_prompt("wt-docs", "p-bash", "open") == (200, {"ok": True, "reason": None})
fleet.orca = real_orca
assert opened == [("terminal", "switch", "--terminal", "term_docs")]
rows = [json.loads(l) for l in (state_dir / "sends.jsonl").read_text().splitlines()[8:]]
assert len(rows) == 15 and all(r["text"].startswith("prompt ") and "code" in r for r in rows), rows

# Adoption: a dry run by default, a write only with the config flag, and undo restores the logged previous value.
calls = []
real_ps_table = fleet.ps_table
fleet.fetch_fast, fleet.fetch_runs, fleet.ps_table = world.fetch_fast, world.fetch_runs, lambda: {}
fleet.orca = lambda *a, **k: calls.append(a) or {}
world.worktrees[2]["parentWorktreeId"] = "wt-elsewhere"  # a parent the user chose is left alone
assert fleet.adopt() == 0 and calls == []
assert fleet.adopt(apply=True) == 1 and calls == []
pathlib.Path(os.environ["ORCH_FLEET_CONFIG"]).write_text(json.dumps({"adopt": {"write_orca_parent": True}}))
assert fleet.adopt(apply=True) == 0
assert sorted(c[3:] for c in calls) == [(f"id:{w}", "--parent-worktree", f"id:{p}") for w, p in (
    ("wt-docs", "wt-coord"), ("wt-invoice", "wt-lead"), ("wt-lead", "wt-coord"), ("wt-login", "wt-coord"),
    ("wt-old", "wt-coord"), ("wt-scratch", "wt-coord"))], calls
calls.clear()
assert fleet.adopt(undo="all") == 0
assert sorted(calls) == sorted(("worktree", "set", "--worktree", f"id:{w}", "--no-parent")
                               for w in ("wt-docs", "wt-invoice", "wt-lead", "wt-login", "wt-old", "wt-scratch")), calls
calls.clear()
fleet.adopt(undo="all")
assert calls == []  # the latest row per worktree is now an undo: nothing to restore twice
fleet.ps_table = real_ps_table

# Mail asking a coordinator stays until someone replies, even after the coordinator's inbox loop acked it; a day at most.
ago = lambda h: fleet.iso(now - dt.timedelta(hours=h))
mail = lambda mid, typ, frm, to, h, **kw: {"id": mid, "type": typ, "from_handle": frm, "to_handle": to, "read": 1, "thread_id": None,
                                             "subject": f"subject {mid}", "body": "invented", "created_at": ago(h), **kw}
world.messages += [
    mail("m2", "escalation", "term_docs", "run:run_demo", 1),                          # acked, no reply: stays
    mail("m3", "question", "term_login", "term_coord", 0.6, thread_id="m3"),          # `ask` opens a thread on itself
    mail("m4", "status", "term_coord", "term_login", 0.5, thread_id="m3"),            # ... and the reply names it
    mail("m5", "question", "term_export", "term_coord", 0.4, thread_id="t-older"),    # asked inside an older thread
    mail("m6", "status", "term_coord", "term_export", 0.3, thread_id="t-older"),      # ... answered in that thread
    mail("m7", "question", "term_login", "term_coord", 0.2, thread_id="m7"),
    mail("m8", "status", "term_login", "term_coord", 0.1, thread_id="m7"),            # the asker's own follow-up is no answer
    mail("m9", "decision_gate", "term_docs", "term_coord", 25),                       # acked over a day ago: gone
    mail("m10", "question", "term_docs", "term_coord", 30, read=0),                   # never read: stays, as before
]
st = f.tick(force=True)
assert sorted(i["key"] for i in items(st, "question")) == ["mail:m1", "mail:m10", "mail:m2", "mail:m7"], items(st, "question")
assert fleet.answered_mail(world.messages) == {"m3", "m5"}

# Decisions: `orch decide` keeps them in agent-skills.db; the fleet shows the open ones on the terminal that added them.
import store  # noqa: E402
dec_state = pathlib.Path(tempfile.mkdtemp(prefix="test-decide-"))
body_file = dec_state / "body.md"
body_file.write_text(f"Pick one.\n<img src=x onerror=alert(1)>\nGH_TOKEN={secret}\n")
env = {**os.environ, "ORCH_FLEET_STATE": str(dec_state), "ORCA_TERMINAL_HANDLE": "term_coord"}


def orch(*args, cmd="decide"):
    r = subprocess.run([sys.executable, str(pathlib.Path(__file__).parent / "prog.py"), cmd, *args],
                       capture_output=True, text=True, env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def saved_decision(did):
    return {d["id"]: d for d in json.loads(orch("dump", "decision", cmd="db")[1])["decision"]}[did]


# Before `orch db import` no DB appears: test_store.py covers decisions.json standing in for it.
assert orch("list")[0] == 0 and not (dec_state / store.DB_NAME).exists()
assert orch("import", "--apply", "--rehearsal", cmd="db")[0] == 0
assert (dec_state / "decisions.json").is_dir()  # the tombstone: older code's write_atomic fails on it
assert orch("add", "--title", "Export format", "--body-file", str(body_file), "--option", "CSV::finance uses sheets",
            "--option", "JSON", "--recommend", "1", "--link", "https://example.com/t/1",
            "--if-no-answer", "CSV", "--reason", "owner-asked") == (0, "d1", "")
assert orch("add", "--title", "Second", "--option", "A::a", "--recommend", "1", "--if-no-answer", "진행 못 함",
            "--reason", "owner-hands-only") == (0, "d2", "")
code, out, _ = orch("list")
assert code == 0 and out.splitlines()[1:3] == ["    1. CSV (recommended) — finance uses sheets", "    2. JSON"], out
saved = saved_decision("d1")
assert saved["handle"] == "term_coord" and saved["status"] == "open" and secret not in saved["body"], saved
assert orch("add", "--title", "x", "--option", "A", "--recommend", "2")[0] == 1
assert orch("add", "--option", "A")[0] == 1  # no title
assert orch("done", "d1", "--answer", "CSV") == (0, "d1 done", "")
assert orch("done", "d1")[2] == "d1 is already done" and orch("drop", "d9")[2] == "no decision d9"
assert orch("drop", "d2") == (0, "d2 dropped", "")
assert orch("drop", "d2")[2] == "d2 is already dropped"
assert orch("drop", "d1") == (0, "d1 dropped", "")  # a done decision can be taken back (a misread answer)
saved = saved_decision("d1")
assert saved["status"] == "dropped" and saved["answer"] == "CSV", saved
assert orch("list")[1] == ""
# Closed decisions stay in the table (no 7-day delete); each change left its event in the same transaction.
events = json.loads(orch("dump", "event", cmd="db")[1])["event"]
assert [(e["id"], e["ev"]) for e in events if e["entity"] == "decision"] == [
    ("d1", "add"), ("d2", "add"), ("d1", "done"), ("d2", "dropped"), ("d1", "dropped")], events

fleet.decision_close("d1", "dropped")  # the demo world's own decision, so only this test's show below
d = fleet.decision_add("Ship the export?", "See https://example.com/x", ["Yes::now", "No"], 1, handle="term_coord")
lost = fleet.decision_add("From a closed terminal", handle="term_gone")
st = f.tick(force=True)
got = {i["decision"]: i for i in items(st, "decision")}
assert got[d["id"]]["session"] == "wt-coord" and got[d["id"]]["handle"] == "term_coord", got
assert got[d["id"]]["options"] == [{"label": "Yes", "description": "now"}, {"label": "No", "description": ""}]
assert got[lost["id"]]["session"] == st["root"] and got[lost["id"]]["handle"] is None, got  # the page picks the root's terminal
assert st["counts"]["decision"] == 2
fleet.decision_close(d["id"], "done", "Yes")
assert [i["decision"] for i in items(f.tick(force=True), "decision")] == [lost["id"]]
# Answering on the page records the answer and closes the decision first; typing it into the coordinator's terminal is
# best effort. A busy coordinator (the usual case) leaves it undelivered, and a later collect relays it once it is idle.
busy = Sender((False, "title shows the agent is busy"))
code, out = fleet.decision_answer(lost["id"], "  Yes,\n  ship it ", sender=busy)
assert code == 200 and out["ok"] and not out["delivered"] and out["reason"] == "title shows the agent is busy", out
assert busy.calls == [("term_coord", f"decision {lost['id']}: Yes, ship it")], busy.calls  # the root's terminal
saved = {x["id"]: x for x in store.decisions()}[lost["id"]]
assert saved["status"] == "done" and saved["answer"] == "Yes, ship it" and saved["delivered"] is False, saved
assert not items(f.tick(force=True), "decision")
assert fleet.decision_answer(lost["id"], "No", sender=busy)[0] == 409  # a second tab, or a double click
assert fleet.decision_answer(d["id"], "   ", sender=busy)[0] == 400
assert fleet.relay_decisions(busy) == [] and len(busy.calls) == 1  # its session is working: no screen read at all
world.worktrees[0]["agents"][0]["state"] = "done"  # the coordinator's turn ends
f.tick(force=True)
assert fleet.relay_decisions(busy) == [] and len(busy.calls) == 2  # idle session, but the title still says busy
assert fleet.pending_relays()[0]["id"] == lost["id"]
unconfirmed = Sender((False, "sent, but the screen does not show it was taken"))
assert fleet.relay_decisions(unconfirmed) == [lost["id"]]  # typed: a retry could send it twice
assert fleet.pending_relays() == [] and fleet.relay_decisions(ok := Sender((True, None))) == [] and ok.calls == []
# An idle coordinator gets the line at once; the send box itself still refuses a busy terminal.
now_d = fleet.decision_add("Now?", options=["Yes"], handle="term_coord")
code, out = fleet.decision_answer(now_d["id"], "Yes", sender=ok)
assert out["delivered"] and ok.calls == [("term_coord", f"decision {now_d['id']}: Yes")], (out, ok.calls)
assert fleet.send("wt-coord", "x", sender=busy)[1]["ok"] is False
# The coordinator's own `done` stops a pending relay; `drop` cancels it; an answer typed elsewhere is never relayed.
for did, verb in ((fleet.decision_add("A", handle="term_coord")["id"], "done"), (fleet.decision_add("B", handle="term_coord")["id"], "drop")):
    fleet.decision_answer(did, "x", sender=busy)
    r = subprocess.run([sys.executable, str(pathlib.Path(__file__).parent / "prog.py"), "decide", verb, did],
                       capture_output=True, text=True, env={**env, "ORCH_FLEET_STATE": str(state_dir)})
    assert r.returncode == 0, r.stderr
assert fleet.pending_relays() == [], fleet.pending_relays()
world.worktrees[0]["agents"][0]["state"] = "working"

# The page renders a decision escaped: app.js's helpers and fleet.js itself, run in node on a hostile item.
if shutil.which("node"):
    assets = pathlib.Path(__file__).resolve().parents[1] / "assets" / "dashboard"
    app = (assets / "app.js").read_text().splitlines()

    def take(name):
        """One top-level declaration of app.js: a one-line const, or a block that ends at the next line "}" / "};"."""
        i = next(i for i, l in enumerate(app) if l.startswith((f"const {name} =", f"function {name}(")))
        if app[i].rstrip().endswith("{"):
            return "\n".join(app[i:next(j for j in range(i, len(app)) if app[j] in ("}", "};")) + 1])
        return app[i]
    helpers = "\n".join(take(n) for n in ("ICON", "ESC", "esc", "safeUrl", "icon", "ms", "rel", "relSpan", "tag", "link"))
    hostile = {"key": "decision:d1", "type": "decision", "session": "wt-coord", "decision": "d1", "source": "orch decide",
               "title": "<script>alert(1)</script>", "detail": "<b>x</b>", "at": ago(0.1), "url": "javascript:alert(1)",
               "body": "<img src=x onerror=alert(1)> see https://example.com/a?b=1&c=2. and javascript:alert(2)",
               "options": [{"label": "<i>CSV</i>", "description": "\"quoted\" 'x'"}, {"label": "JSON", "description": ""}], "recommend": 1,
               "project": "<u>p\"q</u>"}
    turn = {"key": "msg:t1", "type": "approval", "session": "wt-coord", "title": "Merge?", "detail": "Done.\n<b>Shall I merge?</b>", "at": ago(0.1)}
    flat = {"key": "msg:t2", "type": "approval", "session": "wt-coord", "title": "Merge?", "detail": "one line", "at": ago(0.1)}
    js = f"""{helpers}
{(assets / "fleet.js").read_text()}
const st = {{sessions: [{{id: "wt-coord", name: "orchestrator", terminals: []}}]}};
process.stdout.write([{json.dumps(hostile)}, {json.dumps(turn)}, {json.dumps(flat)}].map((it) => itemRow(st, it)).join("\\n-----\\n"));"""
    html, turn_html, flat_html = subprocess.run(["node", "-e", "const vm = require('vm'); vm.runInNewContext(require('fs').readFileSync(0, 'utf8'), "
                                         "{document: {addEventListener() {}}, process});"],
                          input=js, capture_output=True, text=True, check=True).stdout.split("\n-----\n")
    for raw in ("<script", "<img", "<i>", "<b>x", 'href="javascript', "\"quoted\"", "<u>", 'p"q'):
        assert raw not in html, (raw, html)
    assert "&lt;i&gt;CSV&lt;/i&gt;" in html
    assert html.count('data-decide="opt:') == 2 and 'data-decide="text"' in html and 'id="decide-d1"' in html
    assert 'data-group="fleet_project" data-val="&lt;u&gt;p&quot;q&lt;/u&gt;"' in html, "the project badge filters, escaped"
    # Nothing expands in place: no Details chip, no opened body, however many lines the detail has.
    for row in (html, turn_html, flat_html):
        assert "Details" not in row and "data-open" not in row and 'class="pre"' not in row and "aria-expanded" not in row, row

    # Outside its own session page a row opens that session: with no live session, or on that page, it stays put.
    assert all('data-go="#/fleet/session/wt-coord"' in row for row in (html, turn_html, flat_html))
    js = f"""{helpers}
{(assets / "fleet.js").read_text()}
const st = {{sessions: [{{id: "wt-coord", name: "orchestrator", terminals: []}}]}};
const it = (session) => ({{key: "mail:m1", type: "question", session, title: "t", detail: "one line"}});
const out = {{gone: itemRow(st, it("wt-gone")), none: itemRow(st, it(null)), own: itemRow(st, it("wt-coord"), {{showSession: false}})}};
const matches = (el, sel) => sel.split(",").some((one) => one[0] === "[" ? one.slice(6, -1).replace(/-(\\w)/g, (_, c) => c.toUpperCase()) in el.dataset
  : one[0] === "." ? el.cls.includes(one.slice(1)) : el.tag === one);
const node = (tag, dataset = {{}}, parent = null, cls = []) => ({{tag, dataset, parent, cls,
  closest(sel) {{ for (let n = this; n; n = n.parent) if (matches(n, sel)) return n; return null; }}}});
const li = node("li", {{go: "#/fleet/session/wt-coord"}}), grow = node("div", {{}}, li);
const click = (target) => {{ location.hash = ""; handlers.click({{target}}); return location.hash; }};
out.clicks = {{row: click(node("span", {{}}, grow)), link: click(node("a", {{}}, grow)), button: click(node("button", {{copy: "x"}}, li))}};
selection = "picked"; out.clicks.selecting = click(grow);
process.stdout.write(JSON.stringify(out));"""
    ctx = ("const vm = require('vm'), handlers = {}, sb = {process, handlers, S: {}, location: {hash: '', origin: 'https://dash.example.test'}, selection: '', CSS: {escape: String},"
           " getSelection: () => sb.selection, renderView() {}, document: {addEventListener(t, f) { handlers[t] = f; }, querySelector() { return null; }}};"
           "vm.runInNewContext(require('fs').readFileSync(0, 'utf8'), sb);")
    got = json.loads(subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout)
    assert not any("data-go" in got[k] for k in ("gone", "none", "own")), got
    assert got["clicks"] == {"row": "#/fleet/session/wt-coord", "link": "", "button": "", "selecting": ""}, got["clicks"]
    # A decision says how to answer it in the coordinator's terminal too, with the recommended option filled in.
    assert "<span class=\"mono\">d1: &lt;i&gt;CSV&lt;/i&gt;</span>" in html, html

    # A ticket id in a brief cell becomes a link to state.ticket_url; with none, text stays as it was. Ids inside an
    # existing link, a tag attribute or a standard's name ("UTF-8") are left alone.
    js = f"""{helpers}
{(assets / "fleet.js").read_text()}
const cell = "[ABC-19\\u2028ABC-20](https://x.test/z) ABC-12<b>x</b> [ABC-13](https://x.test/ABC-14) UTF-8 `ABC-15` feat-16 <i title=\\"ABC-17\\">y</i>";
const S = {{fleet: {{ticket_url: "https://tracker.example.test/acme/issue/"}}}};
const on = briefInline(cell);
S.fleet = {{}};
process.stdout.write(JSON.stringify({{on, off: briefInline(cell)}}));"""
    got = json.loads(subprocess.run(["node", "-e", "const vm = require('vm'); vm.runInNewContext(require('fs').readFileSync(0, 'utf8'), "
                                    "{document: {addEventListener() {}}, process});"], input=js, capture_output=True, text=True, check=True).stdout)
    link_to = lambda k: f'<a class="link" href="https://tracker.example.test/acme/issue/{k}" target="_blank" rel="noopener noreferrer">{k}</a>'
    assert link_to("ABC-12") in got["on"] and link_to("ABC-15") in got["on"], got["on"]
    assert link_to("ABC-13") not in got["on"] and link_to("ABC-14") not in got["on"], got["on"]
    assert got["on"].count("<a ") == 5 and "ABC-19 ABC-20</a>" in got["on"], "a line separator inside a link text keeps it one link"
    assert "<i " not in got["on"] and link_to("ABC-17") in got["on"], "raw markup is escaped text, so an id in it is only text"
    assert 'href="https://x.test/ABC-14"' in got["on"] and ">ABC-13</a>" in got["on"] and "UTF-8" in got["on"] and "tracker.example.test/acme/issue/UTF" not in got["on"]
    assert "tracker.example.test" not in got["off"] and "ABC-12" in got["off"], got["off"]

    # Automations and shared resources render escaped; an overdue or failed automation is flagged and counted.
    js = f"""{helpers}
{take("hhmm")}
{(assets / "fleet.js").read_text()}
const old = new Date(Date.now() - 3 * 3600e3).toISOString(), soon = new Date(Date.now() + 600e3).toISOString();
const st = {{sessions: [{{id: "wt-coord", name: "<b>coord</b>", phase: "idle"}}], sources: {{automations: {{ok: true}}}},
  automations: [{{id: "a1", key: "k1", name: "<script>x</script>", rrule: "17 */3 * * *", enabled: true, next_run_at: soon, last_run_at: old,
                  recent: [{{status: "completed", at: old, summary: "<img src=x> " + "lastsum ".repeat(60) + "END"}}, {{status: "skipped_precheck", at: old}}]}},
                {{id: "a2", key: "k2", name: "late", rrule: "0 9 * * *", enabled: true, next_run_at: old, recent: []}},
                {{id: "a3", key: "k3", name: "broken", rrule: "*/20 * * * *", enabled: true, next_run_at: soon, recent: [{{status: "failed", at: old}}]}},
                {{id: "a4", key: "k4", name: "off", rrule: "x", enabled: false, next_run_at: old, recent: [{{status: "failed", at: old}}]}},
                {{id: "a5", key: "orch-dash", name: "agent-skills orch-dash", rrule: "*/10 * * * *", enabled: true, next_run_at: soon, recent: []}},
                {{id: "a9", key: null, name: "somebody else's failing job", rrule: "x", enabled: true, next_run_at: soon, recent: [{{status: "failed", at: old}}]}}],
  holds: [{{kind: "resource", resource: "browser", by: "term_coord", session: "wt-coord", note: "<i>login</i>", at: old}},
          {{kind: "lane", resource: "acme/app@main", by: "#7", note: null, at: soon}}]}};
F.jobsAt = Date.now();  // as /api/fleet/jobs answered it a moment ago
const entry = (a, extra = {{}}) => ({{key: a.key, job_name: "agent-skills " + a.key, default: true, what: "", registered: true, orca: a, record: null, jobs: [], ...extra}});
const own = [{{key: "tidy", name: "Self-clean", cadence: "after each fleet collect", runs_in: "orch-dash",
               record: {{at: old, ok: true, n: 0, result: "closed 0", acted_at: old, acted_n: 3, next_at: soon}}}},
             {{key: "pr-stats", name: "PR stats", cadence: "every 6h", runs_in: "orch-dash",
               record: {{at: old, ok: false, error: "<b>HTTP 502</b> " + "x".repeat(400), failed_at: old, warned_at: old, next_at: null}}}},
             {{key: "skill-usage", name: "Skill usage", cadence: "every 15m", runs_in: "orch-dash", record: null}},
             {{key: "patrol", name: "Session patrol", cadence: "started by reap-hard", runs_in: "reap-hard", record: null, error: "<b>ValueError</b>('bad file')"}}];
F.jobs = {{automations: [...st.automations.filter((a) => a.key).map((a) => entry(a, a.key === "orch-dash" ? {{jobs: own}} : {{}})),
                         entry({{key: "pstack-upstream"}}, {{orca: null, registered: false}}),
                         {{key: "share-server", job_name: null, default: false, what: "", registered: null, orca: null, record: null, jobs: []}},
                         entry({{key: "sync"}}, {{orca: null, registered: true, record: {{at: old, ok: false, error: "<b>rebase</b> conflict", n: null, next_at: null}}}})],
          rules: [{{name: "Inbox item expiry", rule: "7 days <i>after</i>", applied: "each fleet collect"}}]}};
const out = {{closed: fleetAutomations(st)}};
F.jobOpen.add("orch-dash"); out.open = fleetAutomations(st); F.jobOpen.delete("orch-dash");
process.stdout.write(JSON.stringify({{autos: out.closed, open: out.open, holds: holdsCard(st), troubled: autoTroubled(st).map((a) => a.name), nav: listed(st).length}}));"""
    got = json.loads(subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout)
    for raw in ("<script", "<img", "<i>", "<b>coord"):
        assert raw not in got["autos"] + got["holds"], raw
    assert got["troubled"] == ["late", "broken"], got["troubled"]
    assert "every 3h at :17" in got["autos"] and "daily 9:00" in got["autos"] and "every 20m" in got["autos"], got["autos"]
    assert "past its run time" in got["autos"] and "toned tone-bad" in got["autos"], "an overdue run is flagged and its next-run cell toned"
    assert got["autos"].count('class="run tone-good"') == 2 and 'class="run "' in got["autos"], "a completed and a skipped block"
    assert got["holds"].index("browser") < got["holds"].index("acme/app@main") and "landing lane" in got["holds"], got["holds"]
    # A long last result wraps whole in its cell instead of being cut with an ellipsis.
    autos_html = got["autos"]
    assert "lastsum " * 60 + "END" in autos_html and "ellipsis" not in autos_html, autos_html
    # One list: a row per registry entry, an Orca automation the registry lacks (no key) is not listed or counted.
    assert "somebody else" not in autos_html and "somebody else" not in got["open"] and got["nav"] == 5, got["nav"]
    assert autos_html.count("<b title=") == 8, "one row for each of the 8 registry entries, none twice"
    assert "not registered" in autos_html and "skills-sync setup enable pstack-upstream" in autos_html
    assert "LaunchAgent" in autos_html and "its state is not read here" in autos_html
    assert "&lt;b&gt;rebase&lt;/b&gt; conflict" in autos_html and "<b>rebase" not in autos_html, "a job's own file stands in when Orca has no run"
    assert "closed 0" not in autos_html and "Self-clean" not in autos_html and "▸ 4 jobs" in autos_html, "its jobs stay folded until opened"
    # The jobs that run inside an automation open under its row: a record as written, a failure whole with its link to the timeline, no record said.
    jobs_html = got["open"]
    assert "▾ 4 jobs" in jobs_html and "Session patrol" in jobs_html
    assert "closed 0" in jobs_html and "3 · " in jobs_html, jobs_html
    assert "▲ failed" in jobs_html and "x" * 400 in jobs_html and 'href="#/fleet/timeline"' in jobs_html and "Timeline warning" in jobs_html
    assert "<b>HTTP" not in jobs_html and "<i>after" not in jobs_html, "job text is escaped"
    assert 'data-tl-open="job|pr-stats"' in jobs_html and 'aria-expanded="false"' in jobs_html, "a long error opens in place"
    assert "not read" in jobs_html and "&lt;b&gt;ValueError" in jobs_html, "a row it could not read says why, the rest still show"
    assert jobs_html.count("no record") == 1 and "Rules applied on read" in jobs_html and "Inbox item expiry" in jobs_html
    assert "no change" in jobs_html and "last acted: 3" in jobs_html, "a run that counted 0 says no change, its last change kept"
    assert '<div class="muted">closed 0</div>' in jobs_html and 'title="closed 0"' not in jobs_html, "a 0 run's words show, not on hover"

    # Jobs riding on the fleet tick fold into its row: closed, one summary; opened, a line each with its own result.
    tick = {"at": fleet.iso(now), "ok": True, "n": None, "result": "Needs you 4", "next_at": fleet.iso(now)}
    js = f"""{helpers}
{take("hhmm")}
{(assets / "fleet.js").read_text()}
F.jobsAt = Date.now(); const old = new Date(Date.now() - 3 * 3600e3).toISOString();
const st = {{sources: {{automations: {{ok: true}}}}, automations: []}};
const dash = {{key: "orch-dash", job_name: "agent-skills orch-dash", default: true, what: "", registered: true, orca: null, record: null, jobs: [
  {{key: "fleet-tick", name: "Fleet tick", every: "10s", cadence: "every 10s while a page is open", runs_in: "orch-dash", group: null, record: {json.dumps(tick)}}},
  {{key: "tidy", name: "Self-clean", every: null, cadence: "after each fleet collect", runs_in: "orch-dash", group: "fleet-tick",
    record: {{at: old, ok: true, n: 0, result: "closed 0", next_at: null}}}},
  {{key: "relay", name: "Relay", every: null, cadence: "after each fleet collect", runs_in: "orch-dash", group: "fleet-tick",
    record: {{at: old, ok: true, n: 2, result: "answers relayed 2", next_at: null}}}},
  {{key: "brief-rows", name: "Brief rows", every: null, cadence: "after each fleet collect", runs_in: "orch-dash", group: "fleet-tick", record: null}},
  {{key: "patrol", name: "Session patrol", every: null, cadence: "started by reap-hard", runs_in: "reap-hard", group: null, record: null, why: "no round yet"}}]}};
F.jobs = {{automations: [dash], rules: []}};
const out = {{closed: fleetAutomations(st)}};
F.jobOpen.add("orch-dash"); out.top = fleetAutomations(st);
F.jobOpen.add("fleet-tick"); out.open = fleetAutomations(st); F.jobOpen.delete("fleet-tick");
F.jobs.automations[0].jobs[2].record.n = 0; out.quiet = fleetAutomations(st);
F.jobOpen.delete("orch-dash"); F.jobs.automations[0].jobs[1].record = {{at: old, ok: false, error: "boom", failed_at: old, next_at: null}};
out.rider = fleetAutomations(st);
process.stdout.write(JSON.stringify(out));"""
    got = json.loads(subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout)
    # Closed, the automation's row says in one line what its jobs did; opened, a line for each job that is not a rider.
    assert "▸ 5 jobs" in got["closed"] and "Fleet tick" not in got["closed"] and "Self-clean" not in got["closed"], got["closed"]
    assert "▾ 5 jobs" in got["top"] and ">10s<" in got["top"] and "Session patrol" in got["top"], got["top"]
    assert "▸ 3 more" in got["top"] and "Relay: <b>answers relayed 2</b>" in got["top"] and "Self-clean</b>" not in got["top"], got["top"]
    # A rider with no record is counted as such, not as no change.
    assert "Relay: <b>answers relayed 2</b> · <span class=\"muted\">1 no change · 1 no record</span>" in got["top"], got["top"]
    assert "3 more: 2 no change · 1 no record" in got["quiet"] or "2 no change · 1 no record" in got["quiet"], got["quiet"]
    assert 'class="jsub"' in got["open"] and "Self-clean</b>" in got["open"], got["open"]
    # A rider that failed shows on the closed automation row, though the job it rides on ran fine.
    assert "Self-clean: " in got["rider"] and "▲ failed" in got["rider"] and "▸ 5 jobs" in got["rider"], got["rider"]
    assert "no record · no round yet" in got["top"] and 'class="is-done"' in got["top"], got["top"]

    # A brief row's session shows its model tag like the Sessions table's model cell, in the brief table's 세션 cell and in
    # the session page's brief-rows card: a tag after the word, a warn tag when it ends in `!`, nothing without a model.
    js = f"""{helpers}
{(assets / "fleet.js").read_text()}
const sess = (model, word = "working") => ({{id: "wt-a", name: "a", state: "working", word, last: "x", at: null, pr: null, ...(model === undefined ? {{}} : {{model}})}});
F.brief = {{table_rows: [{{rows: [{{status: "🔄", label: "row-one", session: sess("O")}}, {{status: "🔄", label: "row-two", session: sess("S!")}},
                                  {{status: "🔄", label: "row-three", session: sess("")}}, {{status: "🔄", label: "row-four", session: sess()}}]}}]}};
const cards = briefRowsCard({{id: "wt-a"}}, {{prs: []}}).split("</li>");
process.stdout.write(JSON.stringify({{cell: [sess("O"), sess("S!"), sess(""), sess()].map((s) => sessCell(s, "x")), cards}}));"""
    got = json.loads(subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout)
    ok_html, warn_html, empty_html, none_html = got["cell"]
    assert 'class="model-mark"' in ok_html and ">O<" in ok_html and "warn" not in ok_html.split("model-mark")[1].split("</span>")[0], ok_html
    assert 'class="model-mark"' in warn_html and ">S!<" in warn_html and "warn" in warn_html.split("model-mark")[1], warn_html
    assert ok_html.index("working") < ok_html.index("model-mark"), "the model comes after the word tag"
    assert "model-mark" not in empty_html and "model-mark" not in none_html, (empty_html, none_html)
    assert 'class="model-mark"' in got["cards"][0] and ">O<" in got["cards"][0], got["cards"][0]
    assert 'class="model-mark"' in got["cards"][1] and ">S!<" in got["cards"][1] and "warn" in got["cards"][1].split("model-mark")[1], got["cards"][1]
    assert "model-mark" not in got["cards"][2] and "model-mark" not in got["cards"][3], got["cards"]

    # Monitors render escaped; every one not running is flagged and counted, a dead one with its exit code.
    js = f"""{helpers}
{(assets / "fleet.js").read_text()}
const old = new Date(Date.now() - 3600e3).toISOString();
const st = {{sessions: [{{id: "wt-coord", name: "coord", phase: "idle"}}], sources: {{monitors: {{ok: true}}}},
  monitors: [{{id: "b1", kind: "bash", status: "dead", exit: 1, command: "<script>x</script>", description: "<b>wait</b>", session: "wt-coord",
               session_name: "lead:<i>x</i>", started_at: old, last_at: old, summary: "<img src=x>"}},
             {{id: "b2", kind: "bash", status: "lost", command: "tail -f log", description: "tail", started_at: old, last_at: old}},
             {{id: "m1", kind: "monitor", status: "running", command: "gh pr checks", description: "ci", started_at: old, last_at: old, event: "pass"}},
             {{id: "b3", kind: "bash", status: "dead", seen: true, exit: 1, command: "gh run watch 7", description: "run", started_at: old, last_at: old}}]}};
process.stdout.write(JSON.stringify({{html: fleetMonitors(st), troubled: monTroubled(st).map((m) => m.id), empty: fleetMonitors({{}}),
  warned: fleetMonitors({{sources: {{monitors: {{ok: true, warning: "<b>lead</b>: transcript not found"}}}}}})}}));"""
    got = json.loads(subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout)
    for raw in ("<script", "<img", "<i>", "<b>wait"):
        assert raw not in got["html"], raw
    assert got["troubled"] == ["b1", "b2"], "a failure its session received is listed without a ▲"
    assert "○ dead, seen" in got["html"], got["html"]
    assert "exit 1" in got["html"] and "no end record" in got["html"] and ">Monitor<" in got["html"] and 'href="#/fleet/session/wt-coord"' in got["html"], got["html"]
    assert "No monitors" in got["empty"] and "partial" not in got["empty"], got["empty"]
    assert "partial" in got["warned"] and "&lt;b&gt;lead" in got["warned"], got["warned"]

    # The header always shows the automations chip: neutral with the count, ▲ when one is troubled; monitors only when ▲.
    js = f"""{helpers}
{(assets / "fleet.js").read_text()}
const el = {{}}, $ = (sel) => (el[sel] = el[sel] || {{}}), S = {{}}, old = new Date(Date.now() - 3600e3).toISOString();
const ok = {{id: "a1", key: "sync", name: "ok", enabled: true, recent: [{{status: "completed", at: old}}]}}, out = {{}};
for (const [k, st] of Object.entries({{calm: {{sessions: [], automations: [ok, ok, {{...ok, key: null, recent: [{{status: "failed", at: old}}]}}]}}, none: {{sessions: []}},
    bad: {{sessions: [], automations: [ok, {{...ok, recent: [{{status: "failed", at: old}}]}}], monitors: [{{status: "lost"}}, {{status: "running"}}]}}}})) {{
  S.state = st; fleetHeader(); out[k] = $("#top-right").innerHTML;
}}
process.stdout.write(JSON.stringify(out));"""
    got = json.loads(subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout)
    assert "Automations 2" in got["calm"] and "tone-bad" not in got["calm"] and "Monitors" not in got["calm"], got["calm"]
    assert "Automations 0" in got["none"], got["none"]
    assert "Automations ▲1" in got["bad"] and "Monitors ▲1" in got["bad"], got["bad"]

    # Sent instructions render escaped; a long text folds, a short one shows whole; source and search filter the rows.
    long_text = "<script>x</script>\n" + "\n".join(f"step {i}" for i in range(10))
    js = f"""{helpers}
{take("chips")}
const S = {{filters: {{}}, section: "sent"}}, isFleet = () => true;
{(assets / "fleet.js").read_text()}
F.sentDays = "1";
F.sent = {{total: 3, days: 1, rows: [
  {{ts: new Date().toISOString(), from: "term_coord", to: "<b>card</b>", kind: "card", channel: "orca worktree create", text: {json.dumps(long_text)}, source: "log", also: ["transcript"]}},
  {{ts: new Date().toISOString(), from: "lead", to: "general-purpose", kind: "subagent", channel: "Agent", subject: "<i>review</i>", text: "short <img src=x>", source: "transcript"}},
  {{ts: new Date().toISOString(), from: "dashboard", to: "wt-docs", kind: "terminal", channel: "dashboard", text: "dry-run only", source: "dashboard"}}]}};
const out = {{all: fleetSent({{}})}};
S.filters.sent_src = "transcript"; out.tx = fleetSent({{}});
S.filters.sent_src = "all"; S.filters.sent_q = "DRY"; out.q = fleetSent({{}});
F.sent = {{rows: [], total: 0}}; S.filters.sent_q = ""; out.empty = fleetSent({{sources: {{sent: {{ok: false, error: "<b>ps</b>"}}}}}});
process.stdout.write(JSON.stringify(out));"""
    got = json.loads(subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout)
    for raw in ("<script", "<img", "<i>", "<b>card", "<b>ps"):
        assert raw not in got["all"] + got["empty"], raw
    assert got["all"].count("<details") == 1 and "short &lt;img" in got["all"] and "(" + str(len(long_text)) + " chars)" in got["all"], got["all"]
    assert ">transcript<" in got["all"].split("</tr>")[0], "a logged send also seen in a transcript names both sources"
    assert "card" in got["tx"] and "general-purpose" in got["tx"] and "wt-docs" not in got["tx"], got["tx"]
    assert "wt-docs" in got["q"] and "general-purpose" not in got["q"], got["q"]
    assert "Nothing sent" in got["empty"] and "collection failed" in got["empty"], got["empty"]

    # Avatars: a team has none to ask for, and one that failed is not asked for again on the next redraw.
    js = f"""{helpers}
{(assets / "fleet.js").read_text()}
const out = {{team: avatarNode(0, 0, 10, "team:docs"), user: avatarNode(0, 0, 10, "rev-bob"), bot: avatarNode(0, 0, 10, "lint-bot[bot]")}};
avatarMissed({{dataset: {{login: "rev-bob"}}, remove() {{}}}});
out.again = avatarNode(0, 0, 10, "rev-bob");
process.stdout.write(JSON.stringify(out));"""
    got = json.loads(subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout)
    assert "<image" not in got["team"] and ">te</text>" in got["team"], got["team"]
    assert '/api/fleet/avatar/rev-bob"' in got["user"] and 'onerror="avatarMissed(this)"' in got["user"], got["user"]
    assert "/api/fleet/avatar/lint-bot%5Bbot%5D" in got["bot"], got["bot"]
    assert "<image" not in got["again"], got["again"]

    # The PR page: grouped by repository, escaped, filtered, and the merged group apart from the open ones.
    pr = lambda n, repo, **kw: {"key": f"{repo}#{n}", "repo": repo, "number": n, "url": f"https://github.com/{repo}/pull/{n}",
                                "title": f"t{n}", "state": "OPEN", "ci": "success", "merge_state": "CLEAN", "decision": "APPROVED",
                                "created": ago(5), "updated": ago(1), "reviewers": [], "unresolved": 0, "session": None, **kw}
    rows = [pr(1, "acme/app", title="<script>x</script>", url="javascript:alert(1)", author="<b>me</b>", coderabbit="rate_limited",
               unresolved=2, session="wt-coord"),
            pr(2, "acme/app", ci="failure", decision="REVIEW_REQUIRED", merge_state="BLOCKED"),
            pr(3, "acme/tools", draft=True, decision=None),
            pr(4, "acme/tools", state="MERGED", merged_at=ago(2)), pr(5, "acme/tools", state="MERGED", merged_at=ago(30))]
    js = f"""{helpers}
{chr(10).join(take(n) for n in ("chips", "sorted", "th", "ago"))}
{(assets / "fleet.js").read_text()}
const st = {{prs: {json.dumps(rows)}, sessions: [{{id: "wt-coord", name: "orchestrator", phase: "idle"}}], sources: {{github: {{updated_at: "{ago(0)}"}}}}}};
var S = {{filters: {{}}, sort: {{}}, state: st}};
const out = {{all: fleetPrs(st), routed: FLEET_SECTIONS.filter((s) => !FLEET_VIEWS[s.id]).map((s) => s.id), icons: FLEET_SECTIONS.map((s) => s.icon)}};
S.filters.fleet_prs = "ci"; out.ci = fleetPrs(st);
S.filters.fleet_prs = "review"; out.review = fleetPrs(st);
S.filters.fleet_prs = "mergeable"; S.filters.fleet_prs_repo = "acme/tools"; out.mergeable = fleetPrs(st);
process.stdout.write(JSON.stringify(out));"""
    got = json.loads(subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout)
    page = got["all"]
    assert got["routed"] == [], f"sidebar sections without a view: {got['routed']}"
    drawn = set(re.findall(r"^  (\w+): '", (assets / "app.js").read_text(), re.M))
    assert len(set(got["icons"])) == len(got["icons"]) and set(got["icons"]) <= drawn, ("one icon per section", got["icons"])
    for raw in ("<script", "<b>me", 'href="javascript'):
        assert raw not in page, (raw, page)
    assert page.index("<h3 class=\"mono\">acme/app</h3>") < page.index("<h3 class=\"mono\">acme/tools</h3>"), page
    open_part, merged_part = page.split('id="prs-merged"')
    assert ">#4<" in merged_part and ">#4<" not in open_part and ">#5<" not in page, "merged within 24 h only, apart"
    assert "rate-limited" in open_part and "2 unresolved review thread" in open_part and "#/fleet/session/wt-coord" in open_part
    num = lambda html: sorted(set(__import__("re").findall(r">#(\d+)<", html.split('id="prs-merged"')[0])))
    assert (num(got["ci"]), num(got["review"]), num(got["mergeable"])) == (["2"], ["2"], []), got
    assert "Updated" in page and "data-rel" in page

    # Timeline: every kind the collector writes reads as one state (its rail icon), an unknown kind as info.
    states = {
        "done": ["pr_merged", "pr_approved", "pr_checks_recovered", "request_done", "turn_done", "item_verify_ok", "mail_worker_done"],
        "active": ["prompt", "pr_head_pushed", "pr_review_requested", "request_link", "mail_status"],
        "wait": ["item_approval", "item_question", "item_decision", "item_run_command", "item_login", "mail_question",
                 "pr_review_comment", "pr_approval_stale"],
        "bad": ["pr_checks_failed", "pr_changes_requested", "item_verify_failed", "requests_error", "mail_escalation"],
        "info": ["item_resolved", "inbox_aged_out", "request_drop", "pr_closed", "some_new_kind"],
    }
    events = [{"at": ago(n / 600), "kind": "turn_done", "session": "wt-coord", "text": f"ev{n}"} for n in range(60)]
    events[0] = {"at": ago(0), "kind": "pr_checks_failed", "session": "wt-coord", "pr": "acme/app#1", "url": "javascript:alert(1)",
                 "text": "<img src=x onerror=alert(1)>"}
    events[1] = {"at": ago(0.01), "kind": "pr_merged", "session": None, "pr": "acme/app#2", "url": "https://github.com/acme/app/pull/2", "text": "#2"}
    dates = "\n".join(take(n) for n in ("hhmm", "shortDate", "dayLabel"))
    js = f"""{dates}
{helpers}
{(assets / "fleet.js").read_text()}
const st = {{sessions: [{{id: "wt-coord", name: "coordinator", terminals: []}}], timeline: {json.dumps(events)}}};
const kinds = {json.dumps(states)};
const out = {{states: Object.fromEntries(Object.entries(kinds).map(([k, ks]) => [k, ks.filter((x) => eventState(x) !== k)])),
  page: timelineList(st, st.timeline, {{fold: "all"}}), own: timelineList(st, st.timeline.slice(0, 3), {{here: "wt-coord"}}),
  short: timelineList(st, st.timeline.slice(0, 3))}};
handlers.click({{target: {{closest: (sel) => sel === "[data-tl-more]" ? {{dataset: {{tlMore: "all"}}}} : null}}}});
out.more = timelineList(st, st.timeline, {{fold: "all"}});
const past = {json.dumps({"at": ago(50), "kind": "turn_done", "session": "wt-coord", "text": "old"})};
out.job = timelineList(st, [{{at: past.at, kind: "job_failed", session: null, job: "pr-stats", text: "pr-stats: boom…"}}]);
out.past = timelineList(st, [past]); out.pastTm = hhmm(past.at); out.pastDate = shortDate(past.at);
const desc = (scrollHeight) => {{ const el = {{dataset: {{tlOpen: "d" + scrollHeight}}, scrollHeight, clientHeight: 20, attrs: {{}}, cls: [],
  classList: {{toggle: (c, on) => {{ el.cls = on ? [c] : []; }}}}, setAttribute: (k, v) => {{ el.attrs[k] = v; }},
  closest: (sel) => sel === "[data-tl-open]" ? el : null}}; return el; }};
const clipped = desc(40), fits = desc(20);
handlers.click({{target: clipped}}); out.opened = [F.tlOpen.has("d40"), clipped.attrs["aria-expanded"], clipped.cls];
F.tlOpen.add(`${{past.at}}|turn_done|wt-coord`); out.openRow = timelineList(st, [past]);
handlers.keydown({{key: " ", target: clipped, preventDefault() {{}}}}); out.closed = [F.tlOpen.has("d40"), clipped.attrs["aria-expanded"], clipped.cls];
handlers.keydown({{key: "Enter", target: clipped, preventDefault() {{}}}}); out.reopened = F.tlOpen.has("d40");
handlers.click({{target: fits}}); out.fits = [F.tlOpen.has("d20"), fits.attrs["aria-expanded"] ?? null];
process.stdout.write(JSON.stringify(out));"""
    got = json.loads(subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout)
    assert got["states"] == {k: [] for k in states}, got["states"]
    page = got["page"]
    assert "<img" not in page and 'href="javascript' not in page and "&lt;img" in page, page
    assert 'href="https://github.com/acme/app/pull/2"' in page and "acme/app#1" not in page, "a PR link only with a safe URL"
    assert page.count('class="tl-ev ') == 50 and 'data-tl-more="all"' in page and "Show 10 more" in page, "50 shown, the rest behind a button"
    assert got["more"].count('class="tl-ev ') == 60 and "data-tl-more" not in got["more"], "the button shows the rest"
    assert "Today" in page and page.count("tl-day") == 1 and "tl-bad" in page and "tl-done" in page
    assert "data-tl-more" not in got["short"] and got["short"].count('data-go="#/fleet/session/wt-coord"') == 2, got["short"]
    assert "data-go" not in got["own"] and "tl-by" not in got["own"], "on its own session page a row goes nowhere"
    # A row under an earlier day's head still shows its time; the head already names the day.
    assert f'>{got["pastTm"]}</span>' in got["past"] and f'>{got["pastDate"]}</span>' not in got["past"], got["past"]
    # A clipped description is a keyboard control that opens and closes; one that fits does not open.
    assert 'role="button" tabindex="0" aria-expanded="false"' in got["past"], got["past"]
    assert 'aria-expanded="true"' in got["openRow"] and 'tl-desc open' in got["openRow"], got["openRow"]
    assert got["opened"] == [True, "true", ["open"]] and got["closed"] == [False, "false", []] and got["reopened"], got
    assert got["fits"] == [False, None], got["fits"]
    # A job's warning line is cut to fit; it links to the Automations row that holds the whole error.
    assert "tl-bad" in got["job"] and 'href="#/fleet/automations"' in got["job"] and "Full error" in got["job"], got["job"]

    # The overview card keeps 8 rows, and missed items are picked first even when they come last.
    rows = [{"key": f"k{n}", "type": "reload_pending", "session": "wt-coord", "title": f"row{n}", "missed": n >= 15} for n in range(17)]
    js = f"""{helpers}
{(assets / "fleet.js").read_text()}
const S = {{filters: {{}}}};
process.stdout.write(itemList({{sessions: []}}, {json.dumps(rows)}, {{limit: 8}}));"""
    card = subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout
    assert card.count("<li class=\"item") == 8 and __import__("re").findall(r">row(\d+)<", card)[:3] == ["15", "16", "0"], card
    # Nothing in an item is cut: the text wraps.
    assert "ellipsis" not in card and "row0" in card, card

    # The stage table opens on the rows stages.counted() marks `open`, the ones the brief's line counts. Each program's
    # other rows (finished, stopped, or unchanged past stage_idle_d) wait behind its own "▸ N folded", and a program
    # with none open is one dimmed heading line after the open ones; Ack all sits inside an opened program, not on top.
    stage = {"rows": [{"id": "g2", "title": "past group", "group": True, "done_at": fleet.iso(now)},
                      {"id": "g2/a", "parent": "g2", "title": "past row", "cells": {}, "done_at": fleet.iso(now), "ack": "ackable"},
                      {"id": "g2/b", "parent": "g2", "title": "past row two", "cells": {}, "done_at": fleet.iso(now), "ack": "ackable",
                       "stopped": "released"},
                      {"id": "g1", "title": "live group", "group": True, "open": True},
                      {"id": "g1/a", "parent": "g1", "title": "open row", "cells": {}, "open": True},
                      {"id": "g1/c", "parent": "g1", "title": "idle row", "cells": {}, "open": False},
                      # Done a month ago with an open row under it: counted open, so shown however old.
                      {"id": "g1/old", "parent": "g1", "title": "old parent", "cells": {}, "open": True,
                       "done_at": fleet.iso(now - dt.timedelta(days=30))},
                      {"id": "g1/old/kid", "parent": "g1/old", "title": "open kid", "cells": {}, "open": True},
                      {"id": "g1/b", "parent": "g1", "title": "finished row", "cells": {}, "done_at": fleet.iso(now)}],
             "refs": {}, "env": {"prod": "v9.9.9", "at": fleet.iso(now - dt.timedelta(days=17))}, "open": 3, "folded": 4}
    js = f"""{helpers}
{(assets / "fleet.js").read_text()}
const S = {{filters: {{}}}};
const out = {{open: stageSection({json.dumps(stage)})}};
F.brief = {{stages: {json.dumps(stage)}}}; out.count = stageOpen();
F.stageFold.add("g1"); F.stageFold.add("g2"); out.all = stageSection({json.dumps(stage)});
process.stdout.write(JSON.stringify(out));"""
    got = json.loads(subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout)
    assert got["count"] == 3, "the sidebar and the overview card count what the server counted"
    assert "open row" in got["open"] and "live group" in got["open"] and "past group" in got["open"], got["open"]
    assert "old parent" in got["open"] and "open kid" in got["open"], got["open"]
    assert got["open"].index("live group") < got["open"].index("past group"), "a program with open rows comes first"
    assert "▸ 2 folded" in got["open"] and "Ack all" not in got["open"], got["open"]
    assert not any(t in got["open"] for t in ("finished row", "past row", "idle row")), got["open"]
    assert all(t in got["all"] for t in ("finished row", "past row", "past row two", "idle row")) and "Ack all 2" in got["all"], got["all"]
    assert "· stopped" in got["all"], got["all"]
    assert "Feature/epic" not in got["open"] and got["open"].count("<th>") == 5, "no always-empty first column"
    # An env line nobody re-recorded for 7 days folds into a closed details, still readable when opened.
    assert "<details class=\"meta\">" in got["open"] and "v9.9.9" in got["open"], got["open"]

    # The Brief points at the Stages page in one line; an old #/fleet/work link opens Stages.
    js = f"""{helpers}
{(assets / "fleet.js").read_text()}
const S = {{filters: {{}}}};
F.brief = {{text: "x", stages: {json.dumps(stage)}, done: [], open: [], closed: []}};
process.stdout.write(JSON.stringify({{brief: fleetBrief(), stages: fleetStages(), moved: fleetSection("work")}}));"""
    got = json.loads(subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout)
    assert 'href="#/fleet/stages"' in got["brief"] and "3 in progress" in got["brief"] and "open row" not in got["brief"], got["brief"]
    assert "open row" in got["stages"] and got["moved"] == "stages", got

    # The To-do page lists the brief answer's "do" items, one that has come first (⏰), then by date, undated last,
    # and the held queue by its place, its first marked ▶; each row has the Open items' Done button. When is the date
    # alone (a condition is a chip, its words a grey line under the work), a come date past today is overdue, the work
    # opens past two lines, and the sidebar counts as the brief's pointer: no come to-do, no held item to check.
    todos = [{"id": "do:undated", "kind": "do", "label": "x", "title": "ready 로 전환", "who": "lead-a", "when": "#5 머지 후", "after": "#5 머지 후", "date": None, "cond": "#5 머지 후", "at": None, "due": False},
             {"id": "do:later", "kind": "do", "label": "x", "title": "릴리스 노트", "who": "writer", "when": "11/3(화) · v2 뒤", "after": "v2 뒤", "date": "11/3(화)", "cond": "v2 뒤", "at": "2099-11-03T00:00:00Z", "due": False},
             {"id": "do:sooner", "kind": "do", "label": "x", "title": "배포 창", "who": "오케스트레이터", "when": "10/30(금) 09:00", "after": None, "date": "10/30(금) 09:00", "cond": None, "at": "2099-10-30T09:00:00Z", "due": False},
             {"id": "do:come", "kind": "do", "label": "x", "title": "회고 정리", "who": "오너", "when": "1/1(수)", "after": None, "date": "1/1(수)", "cond": None, "at": "2020-01-01T00:00:00Z", "due": True},
             {"id": "later:b", "kind": "later", "label": "x", "status": "held", "title": "second held", "trigger": "v3 뒤", "who": None, "n": 2, "check": "조건 충족: acme/app#5 머지"},
             {"id": "later:a", "kind": "later", "label": "x", "status": "held", "title": "first held", "trigger": "QA 뒤", "who": "writer", "n": 1, "check": None},
             {"id": "row:x", "kind": "row", "label": "a row", "status": "moving"}]
    js = f"""{helpers}
{(assets / "fleet.js").read_text()}
const S = {{filters: {{}}}};
F.brief = {{text: "x", done: [], open: {json.dumps(todos)}, closed: []}};
process.stdout.write(JSON.stringify({{page: fleetTodo(), section: fleetSection("todo"), n: todoItems().length, count: todoCount()}}));"""
    got = json.loads(subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout)
    page = got["page"]
    assert got["section"] == "todo" and got["n"] == 4 and got["count"] == 4 and "a row" not in page, got
    order = [page.index(t) for t in ("회고 정리", "배포 창", "릴리스 노트", "ready 로 전환", "first held", "second held")]
    assert order == sorted(order), page
    assert page.count('title="come">⏰') == 1 and page.count('title="waiting">📅') == 3, page
    assert '<td class="todo-when"><span>10/30(금) 09:00</span></td>' in page and "11/3(화) · v2 뒤" not in page, page
    assert page.count(">condition</span>") == 1 and page.count('todo-sub" data-tl-open="todo-sub:do:undated"') == 1, page
    assert 'class="is-late"' in page and page.count("d overdue</span>") == 1 and "3 · ⏰ 1 come · 1 overdue" in page, page
    assert 'class="tl-desc todo-text" data-tl-open="todo:do:sooner"' in page and '<span class="todo-who" title="오케스트레이터">' in page, page
    assert all(f'data-done="{k}"' in page for k in ("do:undated", "do:later", "do:sooner", "do:come", "later:a", "later:b")), page
    assert page.count("▶ ") == 1 and page.index("▶ 1") < page.index("first held") and ">❔2</td>" in page and "1 · ❔ 1 check" in page, page
    assert "<th>Work · next when</th>" in page and "orch board todo add &lt;" not in page, page

    # The Brief page draws the renderer's `full` text, where no cell is cut; repository tables share one set of column
    # widths; the sections under them become tables.
    text = "\n".join([
        "| | 리드/Phase | PR | 진행 | 다음 | 세션 |", "|---|---|---|---|---|---|",
        "| ❔ | **lead-one** | — | — | 에이전트 조치 필요: 세션 꺼짐 · 6h 전 | 작업 중 |",
        "| 🙋 | └ **ABC-1 rollout: release then prod, then the final check** | — | — | ↓ 오너 | 멈춤 |",
        "| ⏳ | └ ABC-1 K4: client plugins for both versions | [#7](https://github.com/acme/plugins/pull/7) ❗리뷰 | — | ↓ 대기 | 세션 없음 |", "",
        "📌 **카드 없는 일**", "", "- ❔ guard-fix — 에이전트 조치 필요: 세션 꺼짐 · 6h 전", "- 🔄 rollout plan — 수 10/28: release · coordinator", "- └ 🔴 child card — 턴 종료", "",
        "⏳ **다른 사람 대기**", "", "- team:reviewers · 리뷰 [#7](https://github.com/acme/plugins/pull/7) · 3일 전부터 → ABC-1 K4 · 다음: 리뷰 대기 · 코디네이터", "",
        "❓ **결정 필요** — 1. (d1) settle the copy → 추천: 적용", "", "2. (d2) pick a date → 추천: Friday", "",
        "🙋 **지금 오너가 할 일** — 없음", "",
        "⏸️ **보류**", "", "- 10/30 09:00 → acme/app#9 머지 + 배포"])
    js = f"""{helpers}
{(assets / "fleet.js").read_text()}
process.stdout.write(briefHtml({json.dumps(text)}));"""
    page = subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout
    assert "…[" not in page and "- [1]" not in page and "then the final check</b>" in page and "└ ABC-1 K4: client plugins for both versions" in page, page
    assert 'class="brief-table brepo"' in page and '<col class="bpr">' in page and '<col class="bnext">' in page and 'class="slide"' in page, page
    assert re.findall(r'<h4 class="bhead">(.*?)</h4>', page) == ["📌 카드 없는 일", "⏳ 다른 사람 대기", "❓ 결정 필요",
                                                                 '🙋 지금 오너가 할 일 <span class="muted">— 없음</span>', "⏸️ 보류"], page
    assert '<div class="slide">에이전트 조치 필요: 세션 꺼짐 · 6h 전</div>' in page and '<div class="slide">↓ 오너</div>' in page, page
    assert "<td>에이전트 조치 필요: 세션 꺼짐 · 6h 전</td>" in page and "release · orchestrator</td>" in page and "코디네이터" not in page, page
    assert "<th>다음</th>" in page and "<td>리뷰 대기 · 오케스트레이터</td>" in page and "<td>ABC-1 K4</td>" in page, page
    assert '<td class="bmark">2.</td><td>(d2) pick a date</td><td>Friday</td>' in page, page
    assert '<td class="bmark">🔴</td><td>└ child card</td><td>턴 종료</td>' in page, page
    assert "<td>acme/app#9 머지 + 배포</td>" in page, page
    assert "❔" not in page and page.count('class="qmark"') == 2, "❔ is drawn as a toned mark, not the pale emoji"

    # The pointer lines' dashboard URLs are links, to this page's own origin: 127.0.0.1 is dead from another device.
    pointers = "\n".join(["**09:00 · x**", "", "단계표 2행 (접음 1) · http://127.0.0.1:4780/#/fleet/stages", "",
                          "📅 할 일 1건 · ⏸️ 보류 큐 2건 · http://127.0.0.1:4780/#/fleet/todo", "",
                          "단계표 3행 · `orch stage show`", "", "보기 http://example.test/<x>"])
    js = f"""{helpers}
{(assets / "fleet.js").read_text()}
process.stdout.write(briefHtml({json.dumps(pointers)}));"""
    page = subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout
    assert '단계표 2행 (접음 1) · <a class="link" href="#/fleet/stages">https://dash.example.test/#/fleet/stages</a>' in page, page
    assert '· <a class="link" href="#/fleet/todo">https://dash.example.test/#/fleet/todo</a>' in page, page
    assert '<p>단계표 3행 · <span class="mono">orch stage show</span></p>' in page, page
    assert 'href="http://example.test' not in page and "&lt;x&gt;" in page, page

    # The terminal text titles its sections `## ...` and quotes its guide lines: the page draws the same sections, the
    # repository title in bold, and the quote without its `>` marks.
    titled = "\n".join(["**09:00 · x**", "", "> 단계표 2행 (접음 1) · http://127.0.0.1:4780/#/fleet/stages", ">",
                        "> PR: ❗ 사람이 풀 것", "", "## 🧭 우선순위", "",
                        "- **Group A** — 진행 1 · 대기 0 · 완료 2 · 다음: t1 — CI 확인", "",
                        "## 🙋 지금 오너가 할 일 — 없음", "", "## ❓ 결정 필요", "",
                        "1. (d1) settle the copy → 추천: 적용", "2. (d2) pick a date → 추천: Friday", "", "## acme/app → main", "",
                        "## ⏸️ 보류", "", "- 10/30 09:00 → acme/app#9 머지 + 배포"])
    js = f"""{helpers}
{(assets / "fleet.js").read_text()}
process.stdout.write(briefHtml({json.dumps(titled)}));"""
    page = subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout
    assert re.findall(r'<h4 class="bhead">(.*?)</h4>', page) == ["🧭 우선순위", '🙋 지금 오너가 할 일 <span class="muted">— 없음</span>',
                                                                 "❓ 결정 필요", "⏸️ 보류"], page
    assert "<td><b>Group A</b></td><td>진행 1 · 대기 0 · 완료 2 · 다음: t1 — CI 확인</td>" in page, page
    assert "<p><b>acme/app → main</b></p>" in page and "##" not in page and "&gt;" not in page, page
    assert '<td class="bmark">2.</td><td>(d2) pick a date</td><td>Friday</td>' in page and "<p>PR: ❗ 사람이 풀 것</p>" in page, page
    assert '<p>단계표 2행 (접음 1) · <a class="link" href="#/fleet/stages">' in page, page

    # A lead with sessions under it folds. Open by default; the toggle is a <button> tied to the rows it hides. Folded,
    # the rows stay in the page hidden, and the lead's row says how many and the most urgent mark, 🙋 before 🔴.
    fold_world = {"sessions": [
        {"id": "wt-root", "kind": "orchestrator", "name": "root", "phase": "working", "parent": None},
        {"id": "lead:1", "kind": "orchestration", "name": "lead-one", "phase": "working", "parent": "wt-root", "subagents": [{"desc": "own helper", "started_at": ago(0.1)}]},
        {"id": "w-a", "kind": "task", "name": "w-a", "phase": "working", "parent": "lead:1", "subagents": [{"desc": "kid helper", "started_at": ago(0.1)}]},
        {"id": "w-b", "kind": "task", "name": "w-b", "phase": "idle", "parent": "lead:1"},
        {"id": "w-c", "kind": "task", "name": "w-c", "phase": "waiting", "parent": "w-b"},
        {"id": "lead-empty", "kind": "orchestration", "name": "lead-empty", "phase": "idle", "parent": "wt-root"},
        {"id": "lead-2", "kind": "orchestration", "name": "lead-two", "phase": "idle", "parent": "wt-root"},
        {"id": "w-d", "kind": "task", "name": "w-d", "phase": "idle", "parent": "lead-2"}], "prs": []}
    fold_rows = [{"session": {"id": "w-a"}, "status": "🔴"}, {"session": {"id": "w-d"}, "status": "🔴"}]
    storage_ok = "const mem = {}; globalThis.localStorage = {getItem: (k) => mem[k] ?? null, setItem: (k, v) => { mem[k] = v; }};"
    storage_bad = "globalThis.localStorage = {getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }};"
    def sessions_js(setup, body):
        return f"""{helpers}
{take("store")}
{(assets / "fleet.js").read_text()}
const S = {{filters: {{}}}};
{setup}
const st = {json.dumps(fold_world)};
F.brief = {{table_rows: [{{rows: {json.dumps(fold_rows)}}}]}};
F.units = {{units: []}};
{body}"""
    def fold_run(setup, body):
        return json.loads(subprocess.run(["node", "-e", ctx], input=sessions_js(setup, body), capture_output=True, text=True, check=True).stdout)
    rows_of = lambda page: re.findall(r"<tr(?: [^>]*)?>.*?</tr>", page[page.index("<tbody>"):], re.S)  # noqa: E731
    row_with = lambda page, name: next(r for r in rows_of(page) if f"<b>{name}</b>" in r or f">{name}<" in r)  # noqa: E731
    got = fold_run(storage_ok, """const open = fleetSessions(st); foldLead("lead:1"); const shut = fleetSessions(st);
process.stdout.write(JSON.stringify({open, shut, kept: mem[FOLD_KEY]}));""")
    lead = row_with(got["open"], "lead-one")
    btn = re.search(r"<button[^>]*data-fold-lead[^>]*>", lead).group(0)
    assert 'type="button"' in btn and 'aria-expanded="true"' in btn and 'aria-controls="' in btn and "aria-label=" in btn, btn
    assert "data-fold-lead" not in row_with(got["open"], "lead-empty") and "data-fold-lead" not in row_with(got["open"], "w-b"), got["open"]
    assert "data-fold-lead" not in row_with(got["open"], "root"), "the orchestrator is not a lead"
    ids = re.search(r'aria-controls="([^"]*)"', btn).group(1).split()
    assert len(ids) == 4, ids  # three sessions and the subagent of one; the lead's own subagent is not under the fold
    assert not any(r.startswith("<tr hidden") for r in rows_of(got["open"])) and "fold-sum" not in got["open"], got["open"]
    shut = got["shut"]
    btn = re.search(r"<button[^>]*data-fold-lead[^>]*>", row_with(shut, "lead-one")).group(0)
    assert 'aria-expanded="false"' in btn, btn
    assert all(f'id="{i}"' in shut for i in ids), "aria-controls names rows in the page"
    for name in ("w-a", "kid helper", "w-b", "w-c"):
        assert row_with(shut, name).startswith("<tr hidden"), (name, shut)
    for name in ("own helper", "lead-two", "w-d", "lead-empty"):
        assert not row_with(shut, name).startswith("<tr hidden"), (name, shut)
    sum_html = re.search(r'<span class="fold-sum[^>]*>.*?</span>', row_with(shut, "lead-one")).group(0)
    assert "3 sessions" in sum_html and "🙋" in sum_html and "🔴" not in sum_html, sum_html  # w-c waits, w-a is blocked
    assert json.loads(got["kept"]) == ["lead:1"], got["kept"]
    # A second read of the page, as after a reload, starts from what was stored; a redraw keeps it; unfolding clears it.
    got = fold_run(storage_ok + ' mem["orch-dash:lead-fold"] = JSON.stringify(["lead-2"]);',
                   """const shut = fleetSessions(st), again = fleetSessions(st); foldLead("lead-2"); const open = fleetSessions(st);
process.stdout.write(JSON.stringify({shut, again, open, kept: mem[FOLD_KEY]}));""")
    assert row_with(got["shut"], "w-d").startswith("<tr hidden") and got["shut"] == got["again"], got["shut"]
    sum_html = re.search(r'<span class="fold-sum[^>]*>.*?</span>', row_with(got["shut"], "lead-two")).group(0)
    assert "1 session<" in sum_html.replace(" · ", "<") and "🔴" in sum_html, sum_html  # one session, and its brief row's mark
    assert "<tr hidden" not in got["open"] and json.loads(got["kept"]) == [], got
    # A row a to-do holds reads 📅 in the table and ranks as held; a session with only finished rows has nothing urgent.
    got = fold_run(storage_ok, """F.brief.table_rows[0].rows.push({session: {id: "x"}, status: "📅"}, {session: {id: "y"}, status: "📅"}, {session: {id: "y"}, status: "⏳"});
process.stdout.write(JSON.stringify([urgentMark([{id: "x"}]), urgentMark([{id: "y"}]), urgentMark([{id: "z"}]),
  urgentMark([{id: "z", unread: 1, needs_owner: 0}]), urgentMark([{id: "z", unread: 1, needs_owner: 1}])]));""")
    assert got == ["📅", "⏳", "", "", "🙋"], got  # an inbox item the agent handles, like failing CI, is not the owner's turn
    # Storage that throws, or holds junk: open, and the toggle still works for this page.
    for setup in (storage_bad, storage_ok + ' mem["orch-dash:lead-fold"] = "{not json";', storage_ok + ' mem["orch-dash:lead-fold"] = "7";'):
        got = fold_run(setup, """const open = fleetSessions(st); foldLead("lead:1"); const shut = fleetSessions(st);
process.stdout.write(JSON.stringify({open, shut}));""")
        assert "<tr hidden" not in got["open"] and row_with(got["shut"], "w-a").startswith("<tr hidden"), setup

    # Each repository row gets the button of the table_rows entry with its label, at the right end, whatever the order;
    # a table no entry names gets none. The Finished rows card leaves out a row whose Ack is already in the table, and
    # Open items one whose Done is: a list item with no row of its own gets its Done in its list.
    two = "\n".join(["| | 리드/Phase | PR | 다음 | 세션 |", "|---|---|---|---|---|", "| ✅ | done lead | — | — | — |",  # no bar, no 진행
                     "| 🔴 | stuck lead | — | 재개·정리 | 세션 없음 |", "| 🔄 | **busy lead** | — | 리뷰 | 작업 중 |", "",
                     "| | 리드/Phase | PR | 진행 | 다음 | 세션 |", "|---|---|---|---|---|---|", "| ⏳ | oth… | — | — | ↓ 대기 | 멈춤 |", "",  # a label no reply row has: no button
                     "📌 **카드 없는 일**", "", "- 🔄 loose item — 정리 · lead"])
    busy = {"id": "wt-busy", "name": "busy-card", "state": "working", "word": "작업 중", "last": "PR 열고 <리뷰> 대기",
            "at": "2026-10-28T09:00:00Z", "pr": "acme/plugins#7"}
    rows = [{"table": "acme/a", "rows": [
                {"id": "b", "label": "stuck lead", "status": "🔴", "session": {"id": None, "state": "none", "word": "세션 없음"}, "ack": None, "done": "row:b"},
                {"id": "a", "label": "done lead", "status": "✅", "session": None, "ack": "board:a", "done": None},
                {"id": "c", "label": "busy lead", "status": "🔄", "session": busy, "ack": None, "done": "row:c"}]},
            {"table": "acme/b", "rows": [
                {"id": "o1", "label": "other one", "status": "⏳", "session": None, "ack": None, "done": "row:o1"},
                {"id": "o2", "label": "other two", "status": "⏳", "session": None, "ack": None, "done": "row:o2"}]}]
    lists = {"moving": [{"id": "doing:x", "label": "loose item", "text": "🔄 loose item — 정리 · lead"}], "owner": [], "waiting": [], "held": []}
    js = f"""{helpers}
{(assets / "fleet.js").read_text()}
const S = {{filters: {{}}}};
F.brief = {{text: {json.dumps(two)}, table_rows: {json.dumps(rows)}, list_rows: {json.dumps(lists)},
  done: [{{id: "board:a", label: "done lead", acked: false}}, {{id: "board:old", label: "folded lead", acked: false}}],
  open: [{{id: "row:b", kind: "row", label: "stuck lead"}}, {{id: "row:c", kind: "row", label: "busy lead"}},
         {{id: "doing:x", kind: "doing", label: "loose item"}}, {{id: "row:gone", kind: "row", label: "folded away"}}], closed: []}};
F.undo = {{attr: "ack", ids: "board:z", label: "<i>gone</i>"}};
process.stdout.write(JSON.stringify({{page: fleetBrief()}}));"""
    page = json.loads(subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout)["page"]
    first, second, third = page.split('<div class="table-wrap">')[1:4]
    assert first.count('<td class="bact"') == 3 and 'data-ack="board:a"' in first and 'data-done="row:b"' in first, first
    assert first.index('data-ack="board:a"') < first.index('data-done="row:b"') < first.index('data-done="row:c"'), first
    assert 'data-row-label="stuck lead"' in first and '<col class="bact">' in first and "bact" not in second, (first, second)
    assert 'data-done="doing:x"' in third, third
    opened = page.split("<h3>Open items</h3>")[1]
    assert "folded away" in opened and not any(w in opened for w in ("stuck lead", "busy lead", "loose item")), opened
    assert all(page.count(f'data-done="{i}"') == 1 for i in ("row:b", "row:c", "doing:x", "row:gone")), page
    # A lead's name over its sub-tasks in another repository equal to a sub-task's: neither row gets that Done, and a
    # list line that only contains an item's text does not get it either.
    clash = "\n".join(["| | 리드/Phase | PR | 진행 | 다음 | 세션 |", "|---|---|---|---|---|---|", "| | Z | — | — | — | — |",
                       "| 🔄 | └ Z | — | — | 리뷰 | — |", "", "🙋 **지금 오너가 할 일**", "", "- 배포 조사", "- 배포"])
    crows = [{"table": "acme/b", "rows": [
        {"id": "z", "label": "Z", "status": "—", "session": None, "ack": None, "done": None},
        {"id": "z/c", "label": "Z", "status": "🔄", "session": None, "ack": None, "done": "row:z/c"}]}]
    clists = {"moving": [], "owner": [{"id": "todo:1", "label": "배포", "text": "배포"}], "waiting": [], "held": []}
    cjs = f"""{helpers}
{(assets / "fleet.js").read_text()}
const S = {{filters: {{}}}};
F.brief = {{text: {json.dumps(clash)}, table_rows: {json.dumps(crows)}, list_rows: {json.dumps(clists)}, done: [],
  open: [{{id: "row:z/c", kind: "row", label: "Z"}}, {{id: "todo:1", kind: "todo", label: "배포"}}], closed: []}};
process.stdout.write(JSON.stringify({{page: fleetBrief()}}));"""
    cpage = json.loads(subprocess.run(["node", "-e", ctx], input=cjs, capture_output=True, text=True, check=True).stdout)["page"]
    ctable = cpage.split('<div class="table-wrap">')[1]
    assert 'data-done="row:z/c"' not in ctable, ctable
    assert cpage.count('data-done="todo:1"') == 1 and cpage.index("배포 조사") < cpage.index('data-done="todo:1"'), cpage
    # A row's 세션 cell links to its session by worktree id, opens it in Orca and quotes its last report (escaped); one
    # no session runs says so with no link or button; a table with no entries keeps the plain text.
    cells = re.findall(r'<td class="bsess">(.*?)</td>', first, re.S)
    assert len(cells) == 3 and cells[0] == "—" and "세션 없음" in cells[1] and "href" not in cells[1] and "data-open" not in cells[1], cells
    assert 'href="#/fleet/session/wt-busy"' in cells[2] and 'data-open-session="wt-busy"' in cells[2], cells[2]
    assert "PR 열고 &lt;리뷰&gt; 대기" in cells[2] and 'data-rel="2026-10-28T09:00:00Z"' in cells[2], cells[2]
    assert "bsess" not in second.split("<tbody>")[1] and "멈춤" in second, second
    # The Orca button's last outcome: a confirmed switch says it was the host's window only, an unconfirmed one says so
    # with Orca's reply; both offer what to type or find on another device, escaped.
    copy = "workspace: <b>w</b>\nterminal: term_x\nworktree: /tmp/\"w\""
    notes = {"host": {"ok": True, "navigated": True, "reason": "switched in the host's Orca window only", "warning": None, "copy": copy, "name": "<i>n</i>"},
             "unknown": {"ok": False, "navigated": None, "reason": "switch not confirmed", "copy": copy, "name": "n",
                         "warning": "Orca's reply to terminal switch has no navigated field", "result": {"focus": {"tabId": "<t>"}}},
             "none": {"ok": False, "reason": "no connected terminal in this session", "name": "n"}}
    js = f"""{helpers}
{(assets / "fleet.js").read_text()}
const n = {json.dumps(notes)};
process.stdout.write(JSON.stringify({{host: openNote(n.host), unknown: openNote(n.unknown), none: openNote(n.none), unset: openNote(null)}}));"""
    got = json.loads(subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout)
    assert "host&#39;s Orca window only" in got["host"] and "tone-warn" in got["host"] and "not switched" not in got["host"], got["host"]
    assert "&lt;i&gt;n&lt;/i&gt;" in got["host"], got["host"]
    assert "n: not switched (switch not confirmed)." in got["unknown"] and "tone-bad" in got["unknown"], got["unknown"]
    assert "no navigated field" in got["unknown"] and "&lt;t&gt;" in got["unknown"], got["unknown"]
    for k in ("host", "unknown"):
        assert got[k].count("data-copy=") == 1 and "<b>" not in got[k] and '/tmp/"w"' not in got[k], got[k]
        assert "workspace: &lt;b&gt;w&lt;/b&gt;\nterminal: term_x" in got[k] and "data-open-close" in got[k], got[k]
    assert "data-copy" not in got["none"] and "no connected terminal" in got["none"] and got["unset"] == "", got
    # Two clicks whose replies come back in the opposite order: the note stays the last click's.
    js = f"""{helpers}
{(assets / "fleet.js").read_text()}
const pending = {{}};
fleetPost = (path, body) => new Promise((resolve) => {{ pending[body.session] = resolve; }});
showOpenNote = () => {{}};
(async () => {{
  const a = openSession({{dataset: {{openSession: "A"}}}}), b = openSession({{dataset: {{openSession: "B"}}}});
  pending.B({{ok: true, copy: "terminal B"}}); await b;
  pending.A({{ok: true, copy: "terminal A"}}); await a;
  process.stdout.write(JSON.stringify(F.open));
}})();"""
    ctx_s = ctx.replace("sb = {process,", "sb = {process, S: {state: {sessions: []}},")
    got = json.loads(subprocess.run(["node", "-e", ctx_s], input=js, capture_output=True, text=True, check=True).stdout)
    assert (got["name"], got["copy"]) == ("B", "terminal B"), got
    # The session page lists the rows that session runs, with their PR and when it last reported.
    js = f"""{helpers}
{(assets / "fleet.js").read_text()}
const S = {{filters: {{}}}};
F.brief = {{text: "x", table_rows: {json.dumps(rows)}}};
process.stdout.write(JSON.stringify([briefRowsCard({{id: "wt-busy"}}, {{prs: [{{key: "acme/plugins#7", number: 7, url: "https://github.com/acme/plugins/pull/7"}}]}}),
  briefRowsCard({{id: "wt-none"}}, {{prs: []}})]));"""
    mine, none = json.loads(subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout)
    assert "busy lead" in mine and ">#7</a>" in mine and "last report" in mine and "stuck lead" not in mine, mine
    assert "No brief row is linked to this session." in none, none
    finished = page.split("<h3>Finished rows</h3>")[1].split("<h3>")[0]
    assert "folded lead" in finished and "done lead" not in finished and "Ack all 2" in finished, finished
    assert 'Acked <b>&lt;i&gt;gone&lt;/i&gt;</b>' in page and 'data-ack="board:z" data-undo' in page, page

    # Kind reads as a role: the lead says "lead", a session under it says "worker" whether its Run or Orca's parent put it
    # there, a standalone under the root stays "standalone". The overview's lead card counts the lead's sessions and PRs.
    sess = [{"id": "wt-root", "name": "root", "kind": "orchestrator", "parent": None, "phase": "working"},
            {"id": "wt-lead", "name": "billing lead", "kind": "orchestration", "parent": "wt-root", "phase": "working", "project": "p"},
            {"id": "wt-a", "name": "a", "kind": "task", "parent": "wt-lead", "phase": "working"},
            {"id": "wt-b", "name": "b", "kind": "standalone", "parent": "wt-lead", "phase": "idle"},
            {"id": "wt-c", "name": "c", "kind": "standalone", "parent": "wt-root", "phase": "idle"},
            {"id": "wt-d", "name": "d", "kind": "task", "parent": "wt-root", "phase": "idle"}]
    prs = [{"session": "wt-b", "state": "OPEN"}, {"session": "wt-lead", "state": "OPEN"}, {"session": "wt-a", "state": "MERGED"},
           {"session": "wt-c", "state": "OPEN"}]
    js = f"""{helpers}
{chr(10).join(take(n) for n in ("chips",))}
{(assets / "fleet.js").read_text()}
const st = {{sessions: {json.dumps(sess)}, prs: {json.dumps(prs)}}};
var S = {{filters: {{}}, sort: {{}}, state: st}};
F.brief = {{table_rows: []}}; F.units = {{units: []}};
const out = {{labels: Object.fromEntries(st.sessions.map((s) => [s.id, kindLabel(st, s)[0]])), table: fleetSessions(st), card: leadCard(st)}};
out.one = leadCard({{sessions: st.sessions.filter((s) => s.id !== "wt-a"), prs: st.prs.filter((p) => p.session !== "wt-lead")}});
S.filters.fleet_project = "other"; out.hidden = leadCard(st);
out.none = leadCard({{sessions: st.sessions.filter((s) => s.kind !== "orchestration")}});
process.stdout.write(JSON.stringify(out));"""
    got = json.loads(subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout)
    assert got["labels"] == {"wt-root": "orchestrator", "wt-lead": "lead", "wt-a": "worker", "wt-b": "worker",
                             "wt-c": "standalone", "wt-d": "worker"}, got["labels"]
    assert re.findall(r'class="tag[^"]*">(?:<[^>]*>)*([a-z]+)</span>', got["table"]) == \
        ["orchestrator", "lead", "worker", "worker", "worker", "standalone"], got["table"]
    assert "billing lead" in got["card"] and "2 sessions · 1 working · 2 open PRs" in got["card"], got["card"]
    assert "1 session · 0 working · 1 open PR<" in got["one"], got["one"]
    assert got["hidden"] == got["none"] == "", "no card without a lead in view"

    # A page open across a restart onto new code reloads once: not on the version it first saw, not twice for one version.
    js = "\n".join(take(n) for n in ("LS", "store", "S", "versionChanged")) + """
const out = [versionChanged(null), versionChanged("a"), versionChanged("a"), versionChanged("b"), versionChanged("b"), versionChanged("c")];
process.stdout.write(JSON.stringify(out));"""
    ss = ("const vm = require('vm'), kv = {}, sessionStorage = {getItem: (k) => kv[k] ?? null, setItem: (k, v) => { kv[k] = v; }};"
          "vm.runInNewContext(require('fs').readFileSync(0, 'utf8'), {process, sessionStorage, localStorage: sessionStorage});")
    got = json.loads(subprocess.run(["node", "-e", ss], input=js, capture_output=True, text=True, check=True).stdout)
    assert got == [False, False, False, True, False, True], got

    # The sidebar tree: no second home row, the coordinator on top, then units grouped by their most urgent member.
    # A lead keeps its program and its idle card under it; the groups' open state is remembered, a re-render's toggle is not.
    sess = [{"id": "wt-root", "name": "root", "kind": "orchestrator", "parent": None, "phase": "working"},
            {"id": "wt-lead", "name": "lead one", "kind": "orchestration", "parent": "wt-root", "phase": "working", "run": "run_l"},
            {"id": "wt-l1", "name": "lead card idle", "kind": "task", "parent": "wt-lead", "phase": "idle"},
            {"id": "wt-l2", "name": "lead card working", "kind": "task", "parent": "wt-lead", "phase": "working"},
            {"id": "wt-w", "name": "asks you", "kind": "standalone", "parent": "wt-root", "phase": "idle", "unread": 1},
            {"id": "wt-i1", "name": "idle one with a long name that the sidebar cuts", "kind": "standalone", "parent": "wt-root", "phase": "idle"},
            {"id": "wt-i2", "name": "idle two", "kind": "standalone", "parent": "wt-root", "phase": "open"},
            {"id": "wt-o", "name": "gone lead", "kind": "orchestration", "parent": "wt-root", "phase": "offline", "run": "run_o"},
            {"id": "wt-x", "name": "gone card", "kind": "standalone", "parent": "wt-root", "phase": "offline"}]
    js = "\n".join(take(n) for n in ("LS", "store", "S", "isFleet", "mainTone")) + f"""
{helpers}
{(assets / "fleet.js").read_text()}
const fire = (e) => handlers.toggle.forEach((f) => f(e));
const st = {{sessions: {json.dumps(sess)}}}, progs = [{{slug: "prog-l", run: "run_l", summary: {{}}, generated_at: new Date().toISOString()}}];
S.slug = "fleet";
const out = {{plain: navTree(st, progs)}};
F.sessionId = "wt-i2"; out.current = navTree(st, progs); F.sessionId = null;
fire({{target: {{dataset: {{fold: "running", was: "1"}}, open: true}}}});
out.redraw = kv["orch-dash:sb-folds"] ?? null;
fire({{target: {{dataset: {{fold: "idle", was: ""}}, open: true}}}});
fire({{target: {{dataset: {{fold: "needs", was: "1"}}, open: false}}}});
out.stored = navTree(st, progs);
process.stdout.write(JSON.stringify(out));"""
    ls = ("const vm = require('vm'), kv = {}, handlers = {}, localStorage = {getItem: (k) => kv[k] ?? null, setItem: (k, v) => { kv[k] = v; }};"
          "vm.runInNewContext(require('fs').readFileSync(0, 'utf8'), {process, kv, handlers, localStorage,"
          " document: {addEventListener(t, f) { (handlers[t] ||= []).push(f); }}});")
    got = json.loads(subprocess.run(["node", "-e", ls], input=js, capture_output=True, text=True, check=True).stdout)
    tree = got["plain"]
    opened = lambda html: dict(re.findall(r'data-fold="(\w+)" data-was="1?" (open)?', html))
    assert 'href="#/fleet/overview"' not in tree, "the brand is the only home link"
    assert re.findall(r'data-fold="(\w+)"', tree) == ["needs", "running", "idle", "offline"], tree
    assert opened(tree) == {"needs": "open", "running": "open", "idle": "", "offline": ""}, opened(tree)
    names = re.findall(r'<span class="sb-text">([^<]+)</span>', tree)
    assert names == ["root", "Needs you", "asks you", "Running", "lead one", "prog-l", "lead card idle", "lead card working",
                     "Idle", "idle one with a long name that the sidebar cuts", "idle two", "Offline", "gone lead"], names
    # A group header is a depth-0 row whose chevron carries the phase colour; the rows under it start one step in.
    depth = dict((n, int(d)) for d, n in re.findall(r'--d:(\d)".*?<span class="sb-text">([^<]+)</span>', tree, re.S))
    assert depth == {"root": 0, "asks you": 1, "lead one": 1, "prog-l": 2, "lead card idle": 2, "lead card working": 2,
                     "idle one with a long name that the sidebar cuts": 1, "idle two": 1, "gone lead": 1}, depth
    heads = re.findall(r'<summary class="nav-item"[^>]*>(.*?)<span class="sb-text">', tree, re.S)
    assert [re.search(r'class="chev ([^"]*)"', h).group(1).strip() for h in heads] == \
        ["ph-waiting", "ph-working", "ph-idle", "ph-offline"] and not any("dot-s" in h or "pulse" in h for h in heads), heads
    assert 'title="idle one with a long name that the sidebar cuts · standalone · idle"' in tree, "the full name is the tooltip"
    assert '<span class="count">2</span>' in tree.split('data-fold="idle"')[1].split("</summary>")[0], "the idle fold counts its sessions"
    assert opened(got["current"])["idle"] == "open", "the fold holding the page's session opens"
    assert got["redraw"] is None, "a re-render's own toggle is not stored"
    assert opened(got["stored"]) == {"needs": "", "running": "open", "idle": "open", "offline": ""}, opened(got["stored"])
else:
    print("test_fleet: node not found, rendering check skipped")

# PR freshness, counted per probed PR. A quiet PR (cold: no update for days, no CI running, not approved, its session
# not working) waits for its tier with no page open, is probed every PR tick while a page is open, and at once when
# its session's phase changes. A full read that fails is retried at the next probe instead of being forgotten.
probed, real_gql, fail_detail = [], f.graphql, [False]
os.environ["PROGRAMS_HOME"] = str(root / "no-programs")  # the demo ledgers' PRs are not in this world's GitHub


def counting(q):
    if "reviewThreads" in q and fail_detail[0]:
        raise fleet.SourceError("detail read failed")
    if "reviewThreads" not in q:
        probed.extend(int(n) for n in __import__("re").findall(r"pullRequest\(number:(\d+)\)", q))
    return real_gql(q)


f.graphql = counting
ci43 = world.prs[43]["commits"]
world.prs[43]["commits"] = {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]}
clock[0] = now + dt.timedelta(days=3)
f.tick(force=True)
assert {41, 42, 43} <= set(probed), probed


def pr_tick(**kw):
    probed.clear()
    clock[0] += dt.timedelta(seconds=100)
    f.last["prs"] = 0.0  # the 90 s PR tick has come round
    return f.tick(**kw)


pr_tick()
assert 43 not in probed and 42 in probed and 41 in probed, probed  # #42's session works, #41 is approved: both hot
pr_tick(viewed=True)
assert 43 in probed, probed
world.worktrees[3]["agents"][0]["state"] = "done"  # wt-docs' turn ends: its PR is probed without waiting for the tick
probed.clear()
f.tick()
assert probed == [43], probed
world.prs[43]["state"], fail_detail[0] = "MERGED", True
pr_tick(viewed=True)
assert next(p for p in f.state["prs"] if p["number"] == 43)["state"] == "OPEN"  # the read failed
fail_detail[0] = False
st = pr_tick(viewed=True)
assert next(p for p in st["prs"] if p["number"] == 43)["state"] == "MERGED", "retried, not forgotten"
# A PR opened where no worktree has its branch is on the page after one PR tick; a failed search keeps the list.
world.prs[8] = dict(world.prs[7], number=8, headRefOid="h8a", title="feat: opened from a released worktree",
                    url="https://github.com/acme/tools/pull/8")
assert 8 in {p["number"] for p in pr_tick(viewed=True)["prs"]}
# A PR of mine that no session owns keeps its tier, but one the search sees updated is read on that tick.
world.prs[7].update(updatedAt=fleet.iso(clock[0]), title="chore: pin node 22")
st = pr_tick()
assert 7 in probed and next(p for p in st["prs"] if p["number"] == 7)["title"] == "chore: pin node 22", probed
fail_search = lambda q: ({}, ["search failed"]) if "search(" in q else real_gql(q)
f.graphql = fail_search
assert {6, 7, 8} <= {p["number"] for p in pr_tick(viewed=True)["prs"]}
assert f.sources["github"]["ok"] is False
del world.prs[8]
f.graphql = counting
assert 8 not in {p["number"] for p in pr_tick(viewed=True)["prs"]}, "closed or gone from the search: off the page"
# Unless the board names it: a row about a PR the search no longer finds still gets its state, not "?".
world.prs[8] = dict(world.prs[7], number=8, state="CLOSED", url="https://github.com/acme/tools/pull/8")
store.load_board({"rows": {"x": {"id": "x", "pr": "acme/tools#8"}}, "later": [{"title": "t", "trigger": "acme/tools#7 머지"}]})
assert fleet.board_prs() == {"acme/tools#8", "acme/tools#7"}, fleet.board_prs()
assert next(p for p in pr_tick(viewed=True)["prs"] if p["number"] == 8)["state"] == "CLOSED"
# A row whose --pr is a bare number (a coordinator report wrote "441") names no repository to probe. It used to reach
# pr_query's `repo, n = k.split("#")`, raise out of every tick and freeze state.json's generated_at.
store.load_board({"rows": {"x": {"id": "x", "pr": "acme/tools#8"}, "bare": {"id": "bare", "pr": "441"}},
                  "later": [{"title": "t", "trigger": "acme/tools#7 머지"}]})
assert fleet.board_prs() == {"acme/tools#8", "acme/tools#7"}, fleet.board_prs()
before = f.state["generated_at"]
clock[0] += dt.timedelta(seconds=30)
assert pr_tick(viewed=True)["generated_at"] != before, "a tick with a bare-number row still saves a fresh state"
store.load_board({"rows": {"x": {"id": "x", "pr": "acme/tools#8"}}, "later": [{"title": "t", "trigger": "acme/tools#7 머지"}]})
# A closed board item no longer holds its PR: prs.json drops it on the next PR tick.
import tidy  # noqa: E402
held = {"row:x": "x", store.board()["later"][0]["id"]: "t"}
assert tidy.write(list(held), can=held)[0] == list(held)
assert fleet.board_prs() == set(), fleet.board_prs()
assert 8 not in {p["number"] for p in pr_tick(viewed=True)["prs"]}
assert tidy.write(list(held), undo=True)[0] == list(held)
store.load_board()
# Nor while a program's ledger names it: the stage table reads a landed PR's branch from here once its card is gone.
prog_dir = root / "no-programs" / "p1"
prog_dir.mkdir(parents=True)
(prog_dir / "program.json").write_text(json.dumps({"repo": "acme/tools", "created_at": fleet.iso(clock[0])}))
(prog_dir / "ledger.jsonl").write_text(json.dumps({"ts": fleet.iso(clock[0]), "ev": "landed", "pr": 8, "sha": "8" * 40}) + "\n")
assert fleet.program_prs(clock[0]) == {"acme/tools#8"}
assert next(p for p in pr_tick(viewed=True)["prs"] if p["number"] == 8)["state"] == "CLOSED"
shutil.rmtree(prog_dir)
# A ledger pr or program repo that is not a full owner/repo#N (a bare repo "tools") is skipped with one warning line;
# the other entries still count and the tick still saves.
prog_dir.mkdir(parents=True)
(prog_dir / "program.json").write_text(json.dumps({"repo": "tools", "created_at": fleet.iso(clock[0])}))
(prog_dir / "ledger.jsonl").write_text(json.dumps({"ts": fleet.iso(clock[0]), "ev": "landed", "pr": 441, "sha": "8" * 40}) + "\n")
warned = io.StringIO()
with contextlib.redirect_stderr(warned):
    assert fleet.program_prs(clock[0]) == set()
assert "tools#441" in warned.getvalue(), warned.getvalue()
before = f.state["generated_at"]
clock[0] += dt.timedelta(seconds=30)
assert pr_tick(viewed=True)["generated_at"] != before, "a ledger with a bare repo still saves a fresh state"
shutil.rmtree(prog_dir)
# A program file it cannot read (a ledger ts without a zone, a program.json that is not an object) costs the stage
# table its rows, not the tick: prs.json is still written and the error is kept once.
for program, row in [({"repo": "acme/tools", "created_at": fleet.iso(clock[0])},
                      {"ts": "2026-10-27T02:00:00", "ev": "landed", "pr": 8}), ([], None)]:
    f.mem.pop("stage_auto_error", None)
    prog_dir.mkdir(parents=True)
    (prog_dir / "program.json").write_text(json.dumps(program))
    (prog_dir / "ledger.jsonl").write_text(json.dumps(row) + "\n" if row else "")
    world.prs[7].update(updatedAt=fleet.iso(clock[0] + dt.timedelta(seconds=100)),
                        title=f"chore: pin node {program and 'naive' or 'list'}")
    pr_tick(viewed=True)
    saved = json.loads((state_dir / "prs.json").read_text())["prs"]
    assert any(d.get("detail", {}).get("title") == world.prs[7]["title"] for d in saved.values()), "prs.json moved"
    assert "stage_auto_error" in f.mem, f.mem
    shutil.rmtree(prog_dir)
# A detail read before the branch was collected (a probe signature without it) is read again, not left a day.
k43 = next(k for k in f.prs if k.endswith("#43"))
assert f.prs[k43]["detail"]["state"] == "MERGED"
f.prs[k43]["detail"].pop("branch"), f.prs[k43]["probe"].pop()
pr_tick()
assert "branch" in f.prs[k43]["detail"], "a merged PR waits a day for its tier otherwise"
del world.prs[8]
assert 8 not in {p["number"] for p in pr_tick(viewed=True)["prs"]}, "off the board: the search decides again"
# Few GraphQL points left: the PR tick waits (up to 15 min) instead of spending them.
f.rate, f.last["prs"] = 100, fleet.time.monotonic() - 100
probed.clear()
f.tick(viewed=True)
assert probed == [], probed
f.rate = None
world.prs[43]["state"], world.prs[43]["commits"], world.worktrees[3]["agents"][0]["state"] = "OPEN", ci43, "waiting"
f.graphql = real_gql

# Items settled in reality leave on their own. A turn item goes once its pane gets a later prompt or starts a later
# turn, once the same agent finishes a later turn, or once its worktree is gone.
def resolved(st):
    return {e["text"].split(":")[0] for e in st["timeline"] if e["kind"] == "item_resolved"} - {"reload_delivered", "reload_dropped"}


scratch = world.worktrees[4]["agents"][0]
st = f.tick(force=True)
assert [i["session"] for i in items(st, "login")] == ["wt-scratch"] and not resolved(st), "the same last turn keeps it"
clock[0] += dt.timedelta(hours=1)
world.worktrees[0]["agents"][0]["prompt"] = "Check on the scratch session."  # another session's prompt is no reply
st = f.tick(force=True)
assert items(st, "login") and not resolved(st), "with no reply and no new turn it stays"
scratch.update(state="working", lastAssistantMessage="Logged in; checking the release script again.")
st = f.tick(force=True)
assert not items(st, "login") and resolved(st) == {"answered"}, "a turn under way means it was answered"
ask = "Login required: run `gh auth login` in a terminal, then tell me."
scratch.update(state="done", lastAssistantMessage=f"Still no GitHub access.\n{ask}")
st = f.tick(force=True)
assert len(items(st, "login")) == 1, items(st, "login")
clock[0] += dt.timedelta(minutes=5)
scratch["prompt"] = "Logged in, go on."  # answered before Orca shows the new turn
st = f.tick(force=True)
assert not items(st, "login"), items(st, "login")
scratch.update(lastAssistantMessage=f"Access expired again.\n{ask}")
f.tick(force=True)
scratch.update(lastAssistantMessage="The release script works now.")
st = f.tick(force=True)
assert not items(st, "login") and resolved(st) == {"answered", "superseded"}, (items(st, "login"), resolved(st))
login = world.worktrees[1]
login["agents"][0]["lastAssistantMessage"] = "Shall I merge #41?"
st = f.tick(force=True)
assert [i["session"] for i in items(st, "approval") if i["source"] == "turn"] == ["wt-login"]
login["isArchived"] = True
st = f.tick(force=True)
assert not [i for i in items(st, "approval") if i["source"] == "turn"] and "session_gone" in resolved(st)
login["isArchived"] = False
# Mail from a worker whose dispatch has settled is no longer waiting on anyone, whichever way it names the dispatch.
kept = len(world.messages)
world.messages += [{"id": "m20", "type": "escalation", "from_handle": "dispatch:ctx_docs", "to_handle": "term_coord", "read": True,
                    "subject": "Blocked on publish rights", "created_at": fleet.iso(clock[0])},
                   {"id": "m21", "type": "question", "from_handle": "term_login", "to_handle": "run:run_demo", "read": False,
                    "payload": json.dumps({"dispatchId": "ctx_login"}), "subject": "Which base branch?", "created_at": fleet.iso(clock[0])}]
st = f.tick(force=True)
before = {i["key"] for i in items(st, "question")}
assert {"mail:m20", "mail:m21"} <= before, before
workers = {w["dispatchId"]: w for w in world.runs["workers"]}
workers["ctx_docs"]["dispatchStatus"] = workers["ctx_login"]["dispatchStatus"] = "completed"
st = f.tick(force=True)
assert {i["key"] for i in items(st, "question")} == before - {"mail:m20", "mail:m21"}, items(st, "question")
assert fleet.mail_dispatch({"payload": "not json", "from_handle": "term_x"}) is None
workers["ctx_docs"]["dispatchStatus"] = workers["ctx_login"]["dispatchStatus"] = "pending"
del world.messages[kept:]

# A sender that mailed the same coordinator again after an escalation has moved past it; a heartbeat is not that.
at = lambda minutes: fleet.iso(clock[0] + dt.timedelta(minutes=minutes))
world.messages.append({"id": "m30", "type": "escalation", "from_handle": "term_scratch", "to_handle": "term_coord",
                       "read": True, "subject": "Blocked on the npm token", "created_at": at(0)})
world.messages.append({"id": "m31", "type": "heartbeat", "from_handle": "term_scratch", "to_handle": "term_coord",
                       "subject": "alive", "created_at": at(1)})
st = f.tick(force=True)
assert "mail:m30" in {i["key"] for i in items(st, "question")}, items(st, "question")
world.messages.append({"id": "m32", "type": "status", "from_handle": "term_scratch", "to_handle": "term_coord",
                       "subject": "Token found, publishing", "created_at": at(2)})
st = f.tick(force=True)
assert "mail:m30" not in {i["key"] for i in items(st, "question")}, items(st, "question")
del world.messages[kept:]

# A turn item whose PRs have all merged or closed is done; one naming a PR still open, or one the PR cache does not
# know, stays. A bare #n names no PR: it may be a step number or another repository's PR.
turn_items = lambda st: [i for i in items(st, "approval") if i["source"] == "turn"]
for text, stays in (("Shall I merge https://github.com/acme/tools/pull/6?", False), ("Shall I merge acme/tools#6 and acme/tools#99?", True),
                    ("Shall I merge acme/tools#7?", True), ("Step #6 is done. Shall I start the next one?", True),
                    ("Shall I merge acme/tools#6?", False)):
    scratch.update(state="done", lastAssistantMessage=text)
    st = f.tick(force=True)
    assert bool(turn_items(st)) == stays, (text, turn_items(st))
assert "prs_closed" in resolved(st), resolved(st)

# A turn item on a session with no agent left for 10 minutes has no one to answer it. One read without the agent is
# not enough, and an empty read of every agent is more likely a bad read than all of them gone at once.
login["agents"][0]["lastAssistantMessage"] = "Shall I merge #41 now?"
st = f.tick(force=True)
assert [i["session"] for i in turn_items(st)] == ["wt-login"], turn_items(st)
saved_agents = {w["worktreeId"]: w["agents"] for w in world.worktrees}
for w in world.worktrees:
    w["agents"] = []
clock[0] += dt.timedelta(minutes=11)
st = f.tick(force=True)
assert [i["session"] for i in turn_items(st)] == ["wt-login"] and "agent_gone" not in resolved(st), turn_items(st)
for w in world.worktrees:
    w["agents"] = saved_agents[w["worktreeId"]]
login["agents"] = []
st = f.tick(force=True)
assert [i["session"] for i in turn_items(st)] == ["wt-login"], "a single read without the agent is not enough"
clock[0] += dt.timedelta(minutes=11)
st = f.tick(force=True)
assert not turn_items(st) and "agent_gone" in resolved(st), (turn_items(st), resolved(st))
login["agents"] = saved_agents["wt-login"]

# worker-list without --run is scoped to the Run bound to the calling terminal, so a server started from a
# coordinator's terminal saw only that Run's workers: every Run is listed by name, each page followed.
def fake_orca(*args, timeout=30):
    if args[1] == "run-list":
        return {"runs": [{"id": "run_a"}, {"id": "run_b"}, {"id": "run_legacy_local", "legacy": True}]}
    if args[1] == "worker-list":
        run = args[args.index("--run") + 1] if "--run" in args else "run_a"
        if run == "run_b" and "--cursor" not in args:
            return {"workers": [{"dispatchId": "ctx_b1"}], "page": {"hasMore": True, "nextCursor": "c2"}}
        return {"workers": [{"dispatchId": f"ctx_{run}"}]}
    return {}


real_orca, fleet.orca = fleet.orca, fake_orca
try:
    got = fetch_runs()
finally:
    fleet.orca = real_orca
assert [r["id"] for r in got["runs"]] == ["run_a", "run_b"], got["runs"]
assert sorted(w["dispatchId"] for w in got["workers"]) == ["ctx_b1", "ctx_run_a", "ctx_run_b", "ctx_run_legacy_local"], got

# A run whose workers and tasks have all settled is read again only after SETTLED_EVERY, unless its coordinator is on
# screen: it cannot dispatch anything new without one.
calls, on_screen = [], []
def settled_orca(*args, timeout=30):
    calls.append(args[1] if args[0] == "orchestration" else args[0])
    if args[1] == "run-list":
        return {"runs": [{"id": "run_done", "coordinator_handle": "term_c"}, {"id": "run_busy", "coordinator_handle": "term_d"}]}
    if args[1] == "list":
        return {"terminals": [{"handle": h} for h in on_screen]}
    if args[1] == "worker-list":
        status = "completed" if "run_done" in args else "dispatched"
        return {"workers": [{"dispatchId": f"ctx_{args[3]}", "dispatchStatus": status}]}
    if args[1] == "task-list":
        return {"tasks": [{"status": "completed" if "run_done" in args else "pending"}]}
    return {}


real_orca, fleet.orca = fleet.orca, settled_orca
fleet.RUN_CACHE.clear()
try:
    first = fetch_runs()
    calls.clear()
    second = fetch_runs()
    assert calls.count("worker-list") == 1 and second == first, (calls, second)
    on_screen.append("term_c")
    calls.clear()
    fetch_runs()
    assert calls.count("worker-list") == 2, calls
    on_screen.clear()
    fleet.RUN_CACHE["run_done"] = (fleet.time.monotonic() - fleet.SETTLED_EVERY - 1, fleet.RUN_CACHE["run_done"][1])
    calls.clear()
    fetch_runs()
    assert calls.count("worker-list") == 2, calls
finally:
    fleet.orca = real_orca
    fleet.RUN_CACHE.clear()

# The terminals the tick already listed are passed to fetch_runs, which then asks Orca for no list of its own; called
# alone it still asks.
term_calls = []
def term_orca(*args, timeout=30):
    term_calls.append(args[:2])
    if args[1] == "run-list":
        return {"runs": [{"id": "run_x", "coordinator_handle": "term_c"}]}
    return {}


real_orca, fleet.orca = fleet.orca, term_orca
fleet.RUN_CACHE.clear()
try:
    fetch_runs({"term_c"})
    assert ("terminal", "list") not in term_calls, term_calls
    fetch_runs()
    assert ("terminal", "list") in term_calls, term_calls
finally:
    fleet.orca = real_orca
    fleet.RUN_CACHE.clear()

seen_live = []
tick_fleet = fleet.Fleet(fetch_fast=lambda: {"worktrees": [], "terminals": [{"handle": "term_t"}], "messages": []},
                         fetch_runs=lambda live=None: (seen_live.append(live), {"runs": [], "workers": [], "tasks": [], "gates": []})[1],
                         graphql=lambda *a, **k: ({}, []), ps_table=lambda: {}, fetch_automations=lambda: [],
                         fetch_monitors=lambda **k: [], read_screen=lambda h: [])
tick_fleet.tick(force=True)
assert seen_live == [{"term_t"}], seen_live

# The tree's root is the orchestrator even when a lead it dispatched runs a newer Run.
def tree(run_list, workers, wts, orca_parent=None, declared=(), paths=None):
    fast = {"worktrees": [{"worktreeId": w, "path": (paths or {}).get(w, f"/work/{w}"), "displayName": w, "agents": [],
                           "parentWorktreeId": (orca_parent or {}).get(w)} for w in wts],
            "terminals": [{"handle": f"term_{w}", "worktreeId": w} for w in wts]}
    sessions, root = fleet.build_sessions(fast, {"runs": run_list, "workers": workers, "tasks": []}, {}, declared)
    return root, {sid: (s["kind"], s["parent"]) for sid, s in sessions.items()}


run = lambda rid, coord, at: {"id": rid, "coordinator_handle": f"term_{coord}", "updated_at": at}
worker = lambda rid, wt, status="dispatched": {"runId": rid, "dispatchId": f"ctx_{wt}", "dispatchStatus": status,
                                               "resource": {"worktreeId": wt}}
root_wt, got = tree([run("r-top", "top", "t1"), run("r-prog", "prog", "t2")],
                    [worker("r-top", "prog"), worker("r-top", "w1"), worker("r-prog", "w2")], ["top", "prog", "w1", "w2", "solo"])
assert root_wt == "top", root_wt
assert got == {"top": ("orchestrator", None), "prog": ("orchestration", "top"), "w1": ("task", "top"),
               "w2": ("task", "prog"), "solo": ("standalone", "top")}, got
root_wt, got = tree([run("r-top", "top", "t1")], [worker("r-top", "w1", "completed")], ["top", "w1", "solo"])
assert root_wt == "top" and got == {"top": ("orchestrator", None), "w1": ("task", "top"), "solo": ("standalone", "top")}, got
# Two leads that dispatched each other, under the orchestrator: neither is left in a loop.
root_wt, got = tree([run("r-top", "top", "t1"), run("r-a", "a", "t2"), run("r-b", "b", "t3")],
                    [worker("r-b", "a"), worker("r-a", "b")], ["top", "a", "b"])
assert root_wt == "top" and got["top"] == ("orchestrator", None), got
for sid in got:
    hops = 0
    while got[sid][1]:
        sid, hops = got[sid][1], hops + 1
        assert hops < len(got), got
    assert sid == "top", got

# Orca's own parent field places a session no Run dispatched, e.g. a worker a lead made with `orca worktree create`;
# a dispatching Run's coordinator still wins, and a parent that is gone, the session itself, or a loop falls to the root.
root_wt, got = tree([run("r-top", "top", "t1"), run("r-lead", "lead", "t2")], [worker("r-top", "lead"), worker("r-top", "w1")],
                    ["top", "lead", "w1", "w2", "gone", "self", "x", "y"],
                    {"top": "lead", "w1": "lead", "w2": "lead", "gone": "archived", "self": "self", "x": "y", "y": "x"})
assert root_wt == "top" and got["top"] == ("orchestrator", None), got
assert got["lead"] == ("orchestration", "top") and got["w1"] == ("task", "top") and got["w2"] == ("standalone", "lead"), got
assert got["gone"] == ("standalone", "top") and got["self"] == ("standalone", "top"), got
for sid in ("x", "y"):
    hops = 0
    while got[sid][1]:
        sid, hops = got[sid][1], hops + 1
        assert hops < len(got), got
    assert sid == "top", got
root_wt, got = tree([run("r-top", "top", "t9"), run("r-a", "a", "t2"), run("r-b", "b", "t3")], [], ["top", "a", "b"], {"b": "a"})
assert root_wt == "top" and got["a"] == ("orchestration", "top") and got["b"] == ("orchestration", "a"), got

# A lead started with worker-start works on the orchestrator's Run, and so do its units: a unit whose Task's parent
# is the lead's Task hangs under the lead, not the Run's coordinator; the declared lead is an orchestration row.
unit = lambda wt, task, parent=None: {**worker("r-top", wt), "taskId": task,
                                      "projection": {"parent": {"taskId": parent} if parent else None}}
root_wt, got = tree([run("r-top", "top", "t1")],
                    [unit("lead", "t-lead"), unit("u1", "t-u1", "t-lead"), unit("u2", "t-u2", "t-u1"), unit("w9", "t-w9")],
                    ["top", "lead", "u1", "u2", "w9"], declared=[("/work/lead", "lead", "lead")])
assert root_wt == "top" and got == {"top": ("orchestrator", None), "lead": ("orchestration", "top"), "u1": ("task", "lead"),
                                    "u2": ("task", "u1"), "w9": ("task", "top")}, got

# A top level with no Run of its own, beside a lead's Run: the declared roles keep the lead off the root. Folder
# contexts share one path, and the session name picks the declared one; a declared lead with no Run is still a lead.
declared = [("/home/bench", "top", "orchestrator"), ("/work/ops", "ops-1", "lead"), ("/work/net", "net-1", "lead")]
root_wt, got = tree([run("r-ops", "ops", "t1")], [worker("r-ops", "w1")], ["top", "bench", "ops", "net", "w1"],
                    {"net": "top"}, declared, {"top": "/home/bench", "bench": "/home/bench"})
assert root_wt == "top", root_wt
assert got == {"top": ("orchestrator", None), "bench": ("standalone", "top"), "ops": ("orchestration", "top"),
               "net": ("orchestration", "top"), "w1": ("task", "ops")}, got
root_wt, got = tree([run("r-ops", "ops", "t1")], [], ["ops"], declared=declared[1:2])
assert root_wt is None and got == {"ops": ("orchestration", None)}, (root_wt, got)
root_wt, _ = tree([run("r-ops", "ops", "t1")], [], ["ops", "a", "b"], declared=[("/x", "c", "orchestrator")],
                  paths={"a": "/x", "b": "/x"})
assert root_wt == "ops", root_wt  # two contexts on that path and neither has the declared name: not guessed

# declared_roles reads the live sessions' ROLE records, else a `--name` that states the role.
home = root / "roles-home"
(home / "sessions").mkdir(parents=True)
for pid, sid, name, cwd in [(1, "s-top", "🔥 top", "/a"), (2, "s-lead", "lead:ops", "/b"), (3, "s-none", "lead:x", "/c"),
                            (4, "s-plain", "card", "/d"), (5, "s-dead", "orchestrator", "/e")]:
    (home / "sessions" / f"{pid}.json").write_text(json.dumps({"pid": pid, "sessionId": sid, "name": name, "cwd": cwd,
                                                               "procStart": "Mon"}))
state_root = root / "roles-state"
(state_root / "roles").mkdir(parents=True)
(state_root / "roles" / "s-top.json").write_text('{"role": "orchestrator"}')
(state_root / "roles" / "s-none.json").write_text('{"role": "none"}')
saved = {k: os.environ.get(k) for k in ("CLAUDE_CONFIG_DIR", "AGENT_SKILLS_STATE")}
os.environ.update(CLAUDE_CONFIG_DIR=str(home), AGENT_SKILLS_STATE=str(state_root))
try:
    got = fleet.declared_roles({p: (0, "Mon", "claude") for p in (1, 2, 3, 4)})
finally:
    for k, v in saved.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
assert got == [("/a", "🔥 top", "orchestrator"), ("/b", "lead:ops", "lead")], got

# A launcher that gives each profile its own config dir registers the session there only: every home is read.
profile = root / "profiles" / "p1" / "home"
(profile / "sessions").mkdir(parents=True)
(profile / "sessions" / "6.json").write_text(json.dumps({"pid": 6, "sessionId": "s-orca", "name": "orchestrator",
                                                         "cwd": "/f", "procStart": "Mon"}))
procs = {p: (0, "Mon", "claude") for p in (1, 2, 6)}
fleet.CLAUDE_HOMES = (str(root / "profiles" / "*" / "home"),)
os.environ["CLAUDE_CONFIG_DIR"] = str(home)
try:
    got = fleet.live_sessions(procs)
    assert sorted(got) == ["s-lead", "s-orca", "s-top"] and got["s-orca"]["home"] == profile, got
    os.environ["ORCH_CLAUDE_HOMES"] = str(profile)  # when set, it alone names the dirs
    assert sorted(fleet.live_sessions(procs)) == ["s-orca"]
finally:
    fleet.CLAUDE_HOMES = ()
    for k in ("ORCH_CLAUDE_HOMES", "CLAUDE_CONFIG_DIR"):
        os.environ.pop(k, None)

# A session's last activity is the later of Orca's worktree time and its agents' own: the worktree time stands still
# while an agent in it works.
def last_activity(wt_at, *agent_at):
    fast = {"worktrees": [{"worktreeId": "w", "path": "/work/w", "lastActivityAt": wt_at,
                           "agents": [{"paneKey": f"p{n}", "state": "working", "updatedAt": at} for n, at in enumerate(agent_at)]}],
            "terminals": []}
    return fleet.build_sessions(fast, {"runs": [], "workers": [], "tasks": []}, {})[0]["w"]["last_activity"]


hour = 3600 * 1000
assert last_activity(10 * hour, 20 * hour, 15 * hour) == fleet.ms_iso(20 * hour)
assert last_activity(10 * hour, 5 * hour) == fleet.ms_iso(10 * hour)
assert last_activity(10 * hour) == fleet.ms_iso(10 * hour) and last_activity(None) is None

# Classification of a finished turn's last lines.
assert fleet.classify("Build done. E2E test failed on the login page.") == "verify_failed"
assert fleet.classify("Login required: run `gh auth login`, then tell me.") == "login"
assert fleet.classify("Shall I merge it?") == "approval"
assert fleet.classify("Refactored the parser.") == "fyi"

# Automations: Orca's list, each with its last six runs newest first, output masked to its first line.
assert [a["name"] for a in st["automations"]] == ["agent-skills janitor", "agent-skills watch-prs", "agent-skills pstack-upstream", "agent-skills orch-dash", "agent-skills reap-hard", "my own nightly note"] and st["sources"]["automations"]["ok"], st["automations"]
secret_out = "## posted with xoxb-" + "1" * 12 + "-" + "a" * 24 + "\nsecond line"


auto_calls = []


def fake_orca(*args, timeout=30):
    auto_calls.append(args)
    if args[1] == "list":
        return {"automations": [{"id": "a1", "name": "watch-mentions", "rrule": "*/20 9-20 * * 1-5", "enabled": True,
                                 "nextRunAt": 1791546000000, "lastRunAt": 1791544909459}]}
    return {"runs": [{"runNumber": n, "status": "failed" if n == 9 else "completed", "startedAt": 1791544800000 + n,
                      "outputSnapshot": {"content": secret_out}} for n in range(1, 10)]}


real_orca, fleet.orca = fleet.orca, fake_orca
try:
    autos = fleet.fetch_automations()
finally:
    fleet.orca = real_orca
assert [r["status"] for r in autos[0]["recent"]] == ["failed"] + ["completed"] * 5, autos
# The registry name rides on the row; an Orca name the registry lacks has none (the page lists only the registry's).
assert autos[0]["key"] is None, "a name that is not 'agent-skills <registry name>' belongs to no registry row"
_list = fake_orca
fake_orca = lambda *a, **k: ({"automations": [{"id": "a1", "name": "agent-skills watch-mentions", "rrule": "x", "enabled": True},
                                               {"id": "a2", "name": "someone else's", "rrule": "x", "enabled": True}]}
                             if a[1] == "list" else {"runs": []})
fleet.orca = fake_orca
try:
    assert {a["name"]: a["key"] for a in fleet.fetch_automations()} == {"agent-skills watch-mentions": "watch-mentions", "someone else's": None}
finally:
    fleet.orca, fake_orca = real_orca, _list
# Only the newest page is asked for (twice what is shown): without --limit Orca reads an automation's whole history.
assert ("automations", "runs", "--id", "a1", "--limit", str(2 * fleet.AUTOMATION_RECENT)) in auto_calls, auto_calls

# Pages are ordered by createdAt, not runNumber, and Orca halves a page whose reply is too large and hands back a cursor:
# the cursor is followed until twice the shown count is in, then the newest runNumbers are kept.
pages = {None: ([{"runNumber": n} for n in (7, 5, 6)], "c1"), "c1": ([{"runNumber": n} for n in (4, 3, 8)], "c2"),
         "c2": ([{"runNumber": n} for n in (2, 1)], None)}
page_calls = []
def paged_orca(*args, timeout=30):
    cursor = args[args.index("--cursor") + 1] if "--cursor" in args else None
    page_calls.append((int(args[args.index("--limit") + 1]), cursor))
    rows, nxt = pages[cursor]
    return {"runs": rows, "nextCursor": nxt}


real_orca, fleet.orca = fleet.orca, paged_orca
try:
    got_rows = fleet.recent_runs("a1")
finally:
    fleet.orca = real_orca
assert page_calls == [(12, None), (9, "c1"), (6, "c2")], page_calls
assert sorted((r["runNumber"] for r in got_rows), reverse=True)[:fleet.AUTOMATION_RECENT] == [8, 7, 6, 5, 4, 3], got_rows

# A tie on createdAt that runs past the cut keeps being read, or the highest runNumber could stay on a later page; with
# distinct createdAt the read stops at twice the shown count.
def run_rows(numbers, tie):
    return [{"runNumber": n, "createdAt": 100 if tie else 1000 - n} for n in numbers]


for tie, expect_calls, expect_rows in ((True, 4, 15), (False, 3, 13)):
    pages = {None: (run_rows(range(1, 6), tie), "p1"), "p1": (run_rows(range(6, 11), tie), "p2"),
             "p2": (run_rows(range(11, 14), tie), "p3"), "p3": (run_rows(range(14, 16), tie), None)}
    page_calls = []
    fleet.orca = paged_orca
    try:
        got_rows = fleet.recent_runs("a1")
    finally:
        fleet.orca = real_orca
    assert len(page_calls) == expect_calls, (tie, page_calls)
    assert len(got_rows) == expect_rows, (tie, len(got_rows))
    if tie:
        assert max(r["runNumber"] for r in got_rows) == 15, got_rows

# A history that cannot be read to the end of a tie, and a cursor that does not advance, are errors, not a wrong row.
endless = iter(range(1, 1000))
def tied_forever(*args, timeout=30):
    n = next(endless)
    return {"runs": [{"runNumber": n, "createdAt": 100}], "nextCursor": f"c{n}"}


def stuck_cursor(*args, timeout=30):
    return {"runs": [{"runNumber": 1}], "nextCursor": "undefined:r1"}


for name, fake in (("tied_forever", tied_forever), ("stuck_cursor", stuck_cursor)):
    fleet.orca = fake
    try:
        fleet.recent_runs("a1")
        raise AssertionError(f"{name}: expected a SourceError")
    except fleet.SourceError:
        pass
    finally:
        fleet.orca = real_orca
assert autos[0]["next_run_at"] == "2026-10-09T11:40:00Z" and "xoxb" not in json.dumps(autos), autos
assert autos[0]["recent"][0]["summary"] == "posted with [masked]", autos[0]["recent"][0]
# A long first line reaches the page whole: the table wraps it, nothing here cuts it.
secret_out = "round done: " + "item seen " * 30 + "END\nsecond line"
fleet.orca = fake_orca
try:
    autos = fleet.fetch_automations()
finally:
    fleet.orca = real_orca
assert autos[0]["recent"][0]["summary"] == "round done: " + "item seen " * 30 + "END", autos[0]["recent"][0]

# Monitors: the demo world's watches reach state.json on the session whose worktree they run in.
st = f.tick(force=True)
assert [(m["id"], m["session"]) for m in st["monitors"]][:2] == [("b3wait", "wt-lead"), ("b5poll", "wt-export")], st["monitors"]
assert st["sources"]["monitors"]["ok"], st["sources"]

# fetch_monitors on a fake ~/.claude: a session's transcript says what it started and how it ended, ps says what runs.
home = root / "claude-home"
os.environ["CLAUDE_CONFIG_DIR"] = str(home)
t0 = now - dt.timedelta(hours=1)
at = lambda m: fleet.iso(t0 + dt.timedelta(minutes=m))
lstart = lambda m: (t0 + dt.timedelta(minutes=m)).strftime("%a %b %d %H:%M:%S %Y")
for pid, sid in ((100, "s-live"), (101, "s-live"), (200, "s-gone")):
    fleet.write_atomic(home / "sessions" / f"{pid}.json", json.dumps({"pid": pid, "sessionId": sid, "cwd": "/work/wt-lead",
                                                                      "procStart": lstart(0), "name": "lead:billing"}))
lines = []


def started(tid, cmd, m, name="Bash", desc=None, **result):
    use = f"toolu_{tid}"
    inp = {"command": cmd, "description": desc or tid, **({"run_in_background": True} if name == "Bash" else {})}
    lines.append({"type": "assistant", "timestamp": at(m), "message": {"content": [{"type": "tool_use", "id": use, "name": name, "input": inp}]}})
    lines.append({"type": "user", "timestamp": at(m), "toolUseResult": {("taskId" if name == "Monitor" else "backgroundTaskId"): tid, **result},
                  "message": {"content": [{"type": "tool_result", "tool_use_id": use, "content": f"Command running in background with ID: {tid}."}]}})


def notice(tid, m, status=None, summary="", event=None):
    body = f"<task-notification>\n<task-id>{tid}</task-id>\n" + (f"<status>{status}</status>\n" if status else "") + \
           f"<summary>{summary}</summary>\n" + (f"<event>{event}</event>\n" if event else "") + "</task-notification>"
    lines.append({"type": "user", "timestamp": at(m), "message": {"content": body}})


started("old", "orch wait billing", 1)
notice("old", 2, "failed", 'Background command "x" failed with exit code 1')  # re-armed below: not shown
started("wait", "orch wait billing", 3)  # runs under the second terminal that resumed the session
started("other", f"orch wait tax GH_TOKEN={secret}", 4, desc="Background inbox wait for tax")
notice("other", 5, "completed", 'Background command "x" completed (exit code 1)')
started("ok", "make build", 6)
notice("ok", 7, "completed", 'Background command "x" completed (exit code 0)')
started("ci", "gh pr checks 42", 8, name="Monitor", timeoutMs=300000, persistent=False)  # expired 50 min ago
notice("ci", 9, event="build pass")
started("tail", "tail -f build.log", 10)  # nothing runs it and nothing ended it
started("loop", "while true; do orca orchestration check --wait; done", 11, name="Monitor", persistent=True)
notice("loop", 12, event="mail from wt-tax")
lines.append({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_ag", "name": "Agent", "input": {"prompt": "x"}}]}})
transcript = home / "projects" / "-work-wt-lead" / "s-live.jsonl"
fleet.write_atomic(transcript, "".join(json.dumps(l) + "\n" for l in lines))
shell = "/bin/zsh -c source snap.sh && eval '{}' < /dev/null"
procs = {100: (1, lstart(0), "claude"), 101: (1, lstart(0), "claude --resume s-live"),
         300: (101, lstart(3), shell.format("orch wait billing")),
         301: (300, lstart(3), shell.format("orch wait billing")),  # a $(…) subshell: not a second watch
         302: (100, lstart(11), shell.format("while true; do orca orchestration check --wait; done"))}
mons = fleet.fetch_monitors(procs, now=now)
got = {m["id"]: (m["status"], m["exit"]) for m in mons}
assert got == {"other": ("dead", 1), "tail": ("lost", None), "loop": ("running", None), "wait": ("running", None)}, got
assert next(m for m in mons if m["id"] == "other")["seen"], "its notification was a user turn: the session has it"
assert [m["id"] for m in mons][:2] == ["tail", "other"], "troubled first, newest first"
assert secret not in json.dumps(mons) and all(m["session_name"] == "lead:billing" for m in mons), mons
assert next(m for m in mons if m["id"] == "loop")["event"] == "mail from wt-tax"
# Read on from where the last call stopped: the loop's end arrives, and it is dead.
with open(transcript, "a") as fh:
    fh.write(json.dumps({"type": "user", "timestamp": at(20), "message": {"content":
             '<task-notification>\n<task-id>loop</task-id>\n<status>failed</status>\n<summary>Monitor "x" script failed (exit 127)</summary>\n</task-notification>'}}) + "\n")
got = {m["id"]: (m["status"], m["exit"]) for m in fleet.fetch_monitors(procs, now=now)}
assert got["loop"] == ("dead", 127), got
# A notification that arrives mid-turn is written only to the queue and as an attachment, never as a user turn. A clean
# exit or a deliberate stop read from either is not trouble, a failure still is, and the first record dates the end.
lines.clear()


def queued(tid, m, status, summary, via=("enqueue", "remove", "attachment")):
    body = f"<task-notification>\n<task-id>{tid}</task-id>\n<status>{status}</status>\n<summary>{summary}</summary>\n</task-notification>"
    for i, v in enumerate(via):
        lines.append({"type": "attachment", "timestamp": at(m + i), "attachment": {"type": "queued_command", "prompt": body}} if v == "attachment"
                     else {"type": "queue-operation", "operation": v, "timestamp": at(m + i), "content": body})


started("img", "make image", 21)
queued("img", 22, "completed", 'Background command "retry on exit 3" completed (exit code 0)')
started("stop", "orch wait tax", 21)
queued("stop", 22, "killed", 'Background command "x" was stopped', via=("enqueue",))
started("docs", "make docs", 21)
queued("docs", 22, "failed", 'Background command "x" failed with exit code 2', via=("attachment",))
started("held", "make lint", 21)
queued("held", 22, "failed", 'Background command "x" failed with exit code 1', via=("enqueue",))
with open(transcript, "a") as fh:
    fh.write("".join(json.dumps(l) + "\n" for l in lines))
mons = {m["id"]: m for m in fleet.fetch_monitors(procs, now=now)}
assert "img" not in mons and "stop" not in mons and mons["tail"]["status"] == "lost", mons
assert (mons["docs"]["status"], mons["docs"]["exit"], mons["docs"]["ended_at"]) == ("dead", 2, at(22)), mons["docs"]
assert mons["docs"]["seen"] and not mons["held"]["seen"], "only an end still waiting in the queue is unseen"
# TaskStop writes no notification, only its tool result: the task ended on purpose. One that failed stopped nothing.
lines.clear()


def task_stop(tid, m, ok=True):
    use = f"toolu_stop_{tid}"
    lines.append({"type": "assistant", "timestamp": at(m), "message": {"content": [
        {"type": "tool_use", "id": use, "name": "TaskStop", "input": {"task_id": tid}}]}})
    lines.append({"type": "user", "timestamp": at(m),
                  "toolUseResult": {"message": f"Successfully stopped task: {tid}", "task_id": tid} if ok else "Error",
                  "message": {"content": [{"type": "tool_result", "tool_use_id": use, "is_error": not ok,
                                           "content": f"Successfully stopped task: {tid}" if ok else f"No task found with ID: {tid}"}]}})


started("watch", "python3 watch.py --interval 120", 23)
task_stop("watch", 24)
started("poll", "for i in $(seq 1 96); do gh pr view 1; sleep 300; done", 23)
task_stop("poll", 24, ok=False)
with open(transcript, "a") as fh:
    fh.write("".join(json.dumps(l) + "\n" for l in lines))
mons = {m["id"]: m for m in fleet.fetch_monitors(procs, now=now)}
assert "watch" not in mons and mons["poll"]["status"] == "lost", mons
# A session continued in a new transcript leaves the old one ending in continued-in. The new one repeats what the old one
# started, so the old one shows nothing, and it stays scanned so the next tick does not read it from the start again.
old_transcript = home / "projects" / "-work-wt-lead" / "s-old.jsonl"
fleet.write_atomic(home / "sessions" / "102.json", json.dumps({"pid": 102, "sessionId": "s-old", "cwd": "/work/wt-lead",
                                                                "procStart": lstart(0), "name": "lead:old"}))
lines.clear()
started("gone", "orch wait old", 25)
lines.append({"type": "continued-in", "timestamp": at(26), "sessionId": "s-old", "continuedInSessionId": "s-live"})
fleet.write_atomic(old_transcript, "".join(json.dumps(l) + "\n" for l in lines))
mons = fleet.fetch_monitors({**procs, 102: (1, lstart(0), "claude")}, now=now)
assert "gone" not in {m["id"] for m in mons} and fleet.MONITOR_SCAN[str(old_transcript)]["offset"] == old_transcript.stat().st_size, mons
(home / "sessions" / "102.json").unlink()
# Hours later the failures and the lost one drop off; with no live Claude process there is nothing to show.
assert [m["id"] for m in fleet.fetch_monitors(procs, now=now + dt.timedelta(hours=7))] == ["wait"]
assert fleet.fetch_monitors({}, now=now) == []
assert fleet.MONITOR_SCAN == {}, "a transcript of an ended session is not kept"
# A shell is this task's only when it runs exactly its command and started with it: a longer command that contains
# it, a foreground run of it, or a copy started long after is not.
procs = {100: (1, lstart(0), "claude"), 101: (1, lstart(0), "claude --resume s-live"),
         300: (101, lstart(3), shell.format("orch wait billing --verbose")),
         303: (101, lstart(30), shell.format("tail -f build.log")),
         302: (100, lstart(11), shell.format("while true; do orca orchestration check --wait; done"))}
got = {m["id"]: m["status"] for m in fleet.fetch_monitors(procs, now=now)}
assert got["wait"] == "lost" and got["tail"] == "lost", got
# A broken timestamp, or content that is not a block, does not break the source or drop the task: it is lost, unaged.
with open(transcript, "a") as fh:
    fh.write(json.dumps({"type": "assistant", "timestamp": "yesterday", "message": {"content": ["text", None, {
        "type": "tool_use", "id": "toolu_odd", "name": "Bash", "input": {"command": "sleep 99", "run_in_background": True}}]}}) + "\n")
    fh.write(json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00", "toolUseResult": {"backgroundTaskId": "odd"},
                         "message": {"content": [7, {"type": "tool_result", "tool_use_id": "toolu_odd", "content": None}]}}) + "\n")
    fh.write(json.dumps({"type": "user", "message": "not a dict"}) + "\n" + json.dumps([1, 2]) + "\n")
    fh.write('{"type": "user", "message": {"content": "<task-notifi')  # still being written
mons = fleet.fetch_monitors(procs, now=now + dt.timedelta(hours=7))
assert {m["id"]: m["status"] for m in mons}.get("odd") == "lost", mons
assert fleet.MONITOR_SCAN[str(transcript)]["offset"] == transcript.stat().st_size - len('{"type": "user", "message": {"content": "<task-notifi'), \
    "the partial line is read next time"
# Content no guard foresaw fails the monitors source alone, as a SourceError.
real, fleet.scan_transcript = fleet.scan_transcript, lambda path: 1 / 0
try:
    fleet.fetch_monitors(procs, now=now)
    raise AssertionError("expected SourceError")
except fleet.SourceError as e:
    assert "ZeroDivisionError" in str(e), e
fleet.scan_transcript = real
# A session whose transcript is not where its cwd says is looked up by id, and warned about only when it is nowhere.
long_dir = home / "projects" / "-work-shortened-1a2b3c"
long_dir.mkdir(parents=True)
transcript.rename(long_dir / "s-live.jsonl")
warn = []
assert "odd" in {m["id"] for m in fleet.fetch_monitors(procs, now=now, warn=warn)} and warn == [], warn
(long_dir / "s-live.jsonl").unlink()
assert fleet.fetch_monitors(procs, now=now, warn=warn) == [] and warn == ["lead:billing: transcript not found"], warn
# ps prints its start column in the C locale, the one Claude Code writes procStart in, whatever the user's locale.
seen = {}
real_run = fleet.subprocess.run
fleet.subprocess.run = lambda cmd, **kw: seen.update(kw["env"]) or subprocess.CompletedProcess(cmd, 0, stdout=f"  42 1 {lstart(0)} claude\n")
try:
    assert fleet.ps_table() == {42: (1, lstart(0), "claude")}
finally:
    fleet.subprocess.run = real_run
assert seen["LC_ALL"] == "C" and seen["TZ"] == "UTC", seen
del os.environ["CLAUDE_CONFIG_DIR"]

# Commands are shown, so the passwords they carry are masked: in a URL, after a password flag, under a header name.
pw = "hunter2pw"
for cmd in (f"psql postgres://app:{pw}@db.example.test/x", f"mysql -uroot -p{pw} db", f"mysql --password {pw}",
            f"x --password={pw}", f'curl -H "X-Auth-Token-Header: {pw}" https://example.test'):
    assert pw not in fleet.mask(cmd) and "[masked]" in fleet.mask(cmd), fleet.mask(cmd)
for cmd in ("mkdir -p a", "mkdir -pv a", "git log -p", "docker run -p 8080:80 x", "https://example.test/a@b", "git@example.test:a/b"):
    assert fleet.mask(cmd) == cmd, cmd

# Sends in a Bash command: each orca send with its receiver and text, however it is quoted or chained.
cmd = ("orca terminal send --terminal term_a --text 'run your check' --enter && "
       "orca worktree create --name card-x --repo name:app --prompt \"$(cat /tmp/brief.md)\" --json; "
       "orca orchestration send --to dispatch:ctx_1 --subject 'review on #4' --body \"$(cat <<'EOF'\nfix the two threads\nEOF\n)\" --json")
got = fleet.orca_sends(cmd)
assert [(k, to, ch) for k, to, _, _, ch in got] == [("terminal", "term_a", "orca terminal send"), ("card", "card-x", "orca worktree create"),
                                                    ("inbox", "dispatch:ctx_1", "orca orchestration send")], got
assert got[0][3] == "run your check" and got[1][3] == "$(cat /tmp/brief.md)" and "fix the two threads" in got[2][3] and got[2][2] == "review on #4", got
assert fleet.orca_sends("orca orchestration send --to run:r1 --subject 'done: PR open' --type status")[0][3] == "done: PR open"
for cmd in ("orca terminal send --help", "orca orchestration send --type heartbeat --subject alive --body x",
            'grep -rn "orca terminal send --text" skills', "orch sent add --to x --text y", "orca terminal send --text 'unclosed",
            "orca worktree create --name x --prompt=", "orca terminal read --terminal t"):
    assert fleet.orca_sends(cmd) == [], cmd
# A transcript line: a subagent's prompt, a message to an agent, an orca send; anything else sends nothing.
use = lambda name, inp: json.dumps({"type": "assistant", "timestamp": fleet.iso(now), "message": {"content": [
    {"type": "tool_use", "id": "t", "name": name, "input": inp}]}}).encode() + b"\n"
assert [(r["kind"], r["to"], r["subject"], r["channel"]) for r in fleet.sent_line(use("Agent", {"prompt": "review it", "description": "Review", "subagent_type": "Explore"}))] == [
    ("subagent", "Explore", "Review", "Agent")]
assert fleet.sent_line(use("SendMessage", {"to": "a1", "message": "fix it", "summary": "fix"}))[0]["to"] == "a1"
assert fleet.sent_line(use("Bash", {"command": "orca terminal send --terminal t --text hi"}))[0]["text"] == "hi"
assert fleet.sent_line(use("Bash", {"command": "ls orca"})) == [] and fleet.sent_line(use("Read", {"file_path": "orca"})) == []
assert fleet.sent_line(b'{"type": "assistant", "message": {"content": "orca"}}\n') == [] and fleet.sent_line(b"not json orca\n") == []

# fetch_sent reads the live sessions' transcripts, only what was appended since, and forgets an ended session.
home = root / "claude-home-sent"
os.environ["CLAUDE_CONFIG_DIR"] = str(home)
fleet.write_atomic(home / "sessions" / "500.json", json.dumps({"pid": 500, "sessionId": "s-coord", "cwd": "/work/wt-coord",
                                                               "procStart": "Mon Jan  5 10:00:00 2026", "name": "orchestrator"}))
transcript = home / "projects" / "-work-wt-coord" / "s-coord.jsonl"
fleet.write_atomic(transcript, use("Agent", {"prompt": "deploy with GH_TOKEN=" + secret, "subagent_type": "general-purpose"}).decode())
procs = {500: (1, "Mon Jan 5 10:00:00 2026", "claude")}
rows = fleet.fetch_sent(procs)
assert [(r["from"], r["to"], r["source"], r["claude_session"]) for r in rows] == [("orchestrator", "general-purpose", "transcript", "s-coord")], rows
with open(transcript, "ab") as fh:
    fh.write(use("Bash", {"command": "orca terminal send --terminal term_b --text 'go on' --enter"}))
assert [r["to"] for r in fleet.fetch_sent(procs)] == ["general-purpose", "term_b"]
assert fleet.fetch_sent({}) == [] and fleet.SENT_SCAN == {}
del os.environ["CLAUDE_CONFIG_DIR"]

# The `orch sent add` log: one file a day, masked when shown, merged with its transcript copy and the dashboard's lines.
import sent  # noqa: E402
day = now - dt.timedelta(days=3)
sent.add("card-x", "/goal ship it GH_TOKEN=" + secret, "card", "orca worktree create --prompt", "acme/app", sender="term_coord", now=now)
sent.add("ABC-1", "old comment", "tracker", now=day)
assert sorted(p.name for p in (state_dir / "sent").glob("*.jsonl")) == sorted({f"{day:%Y-%m-%d}.jsonl", f"{now:%Y-%m-%d}.jsonl"})
assert secret in (state_dir / "sent" / f"{now:%Y-%m-%d}.jsonl").read_text(), "the log keeps the text as sent"
for bad in (dict(to="", text="x"), dict(to="x", text=" "), dict(to="x", text="y", kind="fax")):
    try:
        sent.add(**bad)
        raise AssertionError(bad)
    except ValueError:
        pass
twin = {"ts": fleet.iso(now + dt.timedelta(minutes=2)), "from": "orchestrator", "to": "card-x", "kind": "card",
        "text": "/goal ship it GH_TOKEN=" + secret, "source": "transcript", "claude_session": "s-coord"}
far = {**twin, "ts": fleet.iso(now + dt.timedelta(hours=1))}
other = {**twin, "text": "something else"}
p = sent.page([twin, far, other], days=1, now=now + dt.timedelta(hours=2))
srcs = [(r["source"], r.get("also"), r["to"]) for r in p["rows"]]
assert ("log", ["transcript"], "card-x") in srcs and srcs.count(("transcript", None, "card-x")) == 2, srcs
assert ("dashboard", None, "wt-export") in srcs and not any(r["to"] == "ABC-1" for r in p["rows"]), "typed dashboard lines in, a 3-day-old log row out"
assert secret not in json.dumps(p) and p["total"] == len(p["rows"]), p
assert any(r["to"] == "ABC-1" for r in sent.page([], days=7, now=now)["rows"])
assert len(sent.page([twin, far, other], days=1, now=now + dt.timedelta(hours=2), limit=2)["rows"]) == 2
# orch sent add reads the text from stdin and names the sender by its terminal handle.
out = subprocess.run([sys.executable, "prog.py", "sent", "add", "--to", "slack:C0FAKE", "--kind", "slack", "-"], input="리뷰 부탁드립니다",
                     capture_output=True, text=True, cwd=pathlib.Path(__file__).parent, env={**os.environ, "ORCA_TERMINAL_HANDLE": "term_x"})
assert out.returncode == 0, out.stderr
assert any(r.get("from") == "term_x" and r["text"] == "리뷰 부탁드립니다" for r in sent.read_log(now - dt.timedelta(minutes=5))), "stdin text logged"
assert subprocess.run([sys.executable, "prog.py", "sent", "add", "--to", "x"], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                      cwd=pathlib.Path(__file__).parent).returncode != 0
# A slug, an unknown flag or bare text is refused with the usage line, and a refused kind names it too.
for bad in (["demo", "--to", "x", "--text", "hi"], ["--to", "x", "--note", "hi"], ["--to", "x", "hi"], ["--to", "x", "--kind", "brief", "--text", "hi"]):
    out = subprocess.run([sys.executable, "prog.py", "sent", "add", *bad], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                         cwd=pathlib.Path(__file__).parent)
    assert out.returncode != 0 and "usage: orch sent add" in out.stderr, (bad, out.stderr)
# The collector keeps the transcript rows for /api/fleet/sent and says how the read went.
st = f.tick(force=True)
assert f.sent == world.sent + fleet.orca_messages_sent(f.fast["messages"]) + fleet.orca_sent(f.runs.get("tasks")), f.sent
assert any(r["source"] == "orca" and r["kind"] == "inbox" for r in f.sent), "the inbox reaches the Sent rows"
assert st["sources"]["sent"]["ok"] and "sent" not in st, st["sources"]
# One ps a runs cycle feeds both monitors and sent; when ps fails, both say so.
ps_calls, real_ps = [], f.ps_table
f.ps_table = lambda: ps_calls.append(1) or {}
f.tick(force=True)
assert ps_calls == [1], ps_calls
f.ps_table = lambda: (_ for _ in ()).throw(fleet.SourceError("ps: gone"))
st = f.tick(force=True)
assert [st["sources"][k]["error"] for k in ("monitors", "sent")] == ["ps: gone", "ps: gone"], st["sources"]
f.ps_table = real_ps
f.tick(force=True)

# The log keeps raw text, so it is owner-only, even when the file or directory was made before with wider modes.
import stat  # noqa: E402
log_file = state_dir / "sent" / f"{now:%Y-%m-%d}.jsonl"
os.chmod(log_file, 0o644)
os.chmod(state_dir / "sent", 0o755)
sent.add("card-y", "again", now=now)
assert (stat.S_IMODE(log_file.stat().st_mode), stat.S_IMODE((state_dir / "sent").stat().st_mode)) == (0o600, 0o700)
# A line of the wrong shape, in the log or the dashboard's sends, is skipped instead of failing the whole page.
with open(log_file, "a") as fh:
    fh.write('{"ts": 5}\n' + json.dumps({"ts": fleet.iso(now), "to": "x", "text": 7}) + "\n[1]\n")
fleet.append_line(state_dir / "sends.jsonl", {"typed": True, "at": 5, "text": "x"})
fleet.append_line(state_dir / "sends.jsonl", {"typed": True, "at": fleet.iso(now), "session": "wt-x", "text": None})
p = sent.page([], days=1, now=now + dt.timedelta(minutes=1))
assert "card-y" in [r["to"] for r in p["rows"]] and "wt-x" not in [r["to"] for r in p["rows"]], p["rows"]
# Each poll reads only what was appended since the last one; a day file out of the window leaves the cache.
size = log_file.stat().st_size
assert sent.LOG_SCAN[str(log_file)]["offset"] == size
sent.add("card-z", "later", now=now)
assert "card-z" in [r["to"] for r in sent.read_log(now - dt.timedelta(minutes=5))] and sent.LOG_SCAN[str(log_file)]["offset"] > size
sent.read_log(now - dt.timedelta(days=1))
assert not any(k.endswith(f"{day:%Y-%m-%d}.jsonl") for k in sent.LOG_SCAN), sent.LOG_SCAN.keys()
# Channel, repository and cwd are masked when shown, like the text.
p = sent.page([{"ts": fleet.iso(now), "to": "t", "text": "hi", "source": "transcript", "channel": f"x GH_TOKEN={secret}",
                "repo": f"https://u:{secret}@example.test/r", "cwd": f"/w/GH_TOKEN={secret}"}], days=1, now=now)
assert secret not in json.dumps(p), p
# A transcript row older than the page's longest window leaves SENT_SCAN, so a long-lived session does not grow it.
os.environ["CLAUDE_CONFIG_DIR"] = str(home)
assert len(fleet.fetch_sent(procs, now=now)) == 2
assert fleet.fetch_sent(procs, now=now + dt.timedelta(days=31)) == [] and fleet.SENT_SCAN[str(transcript)]["rows"] == []
# orch sent list survives a row with no text to show.
fleet.append_line(state_dir / "sends.jsonl", {"typed": True, "at": fleet.iso(fleet.utcnow()), "session": "wt-empty", "text": ""})
out = subprocess.run([sys.executable, "prog.py", "sent", "list"], capture_output=True, text=True, cwd=pathlib.Path(__file__).parent)
assert out.returncode == 0 and "wt-empty  [dashboard] ?" in out.stdout, out.stderr + out.stdout
del os.environ["CLAUDE_CONFIG_DIR"]

# Shared resources: a hold by a terminal handle sits on that terminal's session.
import holds  # noqa: E402
holds.hold("browser", "term_docs", "staging login")
st = f.tick(force=True)
assert [(h["kind"], h["resource"], h["by"], h["session"]) for h in st["holds"]] == [
    ("resource", "browser", "term_docs", "wt-docs")], st["holds"]

# A CLI that crashes because its cwd was removed is named by its cause, not by Node's closing version line.
fake_cli = root / "fake-orca"
fake_cli.write_text(f"""#!{sys.executable}
import os, sys
try:
    os.getcwd()
except FileNotFoundError:
    sys.stderr.write('''node:internal/bootstrap/switches/does_own_process_state:142
    cachedCwd = rawMethods.cwd();
                           ^

Error: ENOENT: process.cwd failed with error no such file or directory, uv_cwd
    at process.wrappedCwd [as cwd] (node:internal/bootstrap/switches/does_own_process_state:142:28)
    at resolveInvocationCwd (/opt/fake/cli/index.js:50:85) {{
  errno: -2,
  code: 'ENOENT',
  syscall: 'uv_cwd'
}}

Node.js v24.0.0
''')
    sys.exit(1)
print('{{"ok": true}}')
""")
fake_cli.chmod(0o755)
assert fleet.run_json([str(fake_cli)]) == {"ok": True}
gone = pathlib.Path(tempfile.mkdtemp(prefix="test-fleet-gone-"))
live = os.getcwd()
os.chdir(gone)
gone.rmdir()
try:
    fleet.run_json([str(fake_cli)])
    raise AssertionError("expected SourceError")
except fleet.SourceError as e:
    assert "uv_cwd" in str(e) and not str(e).startswith("Node.js"), e
finally:
    os.chdir(live)
# Without an Error line the code:/syscall: fields still beat the version line; other CLIs keep their last line.
for stderr, want in (("boom\n  code: 'EACCES',\nNode.js v24.0.0\n", "code: 'EACCES',"),
                     ("warning: x\nerror: not logged in\n", "error: not logged in")):
    fake_cli.write_text(f"#!{sys.executable}\nimport sys\nsys.stderr.write({stderr!r})\nsys.exit(1)\n")
    try:
        fleet.run_json([str(fake_cli)])
        raise AssertionError("expected SourceError")
    except fleet.SourceError as e:
        assert str(e) == want, (str(e), want)

# An added inbox item nobody resolved leaves after EXTERNAL_KEEP_DAYS; `orch-dash inbox list` (now=None) still has it.
# The server's code version covers every module it imports, so a change to any of them restarts it.
with contextlib.ExitStack() as stack:
    ext_state = pathlib.Path(stack.enter_context(tempfile.TemporaryDirectory()))
    os.environ["ORCH_FLEET_STATE"], was = str(ext_state), os.environ["ORCH_FLEET_STATE"]
    stack.callback(os.environ.__setitem__, "ORCH_FLEET_STATE", was)
    fleet.inbox_add("login", "old", key="old")
    fleet.inbox_add("login", "new", key="new")
    rows = [json.loads(l) for l in (ext_state / "inbox-external.jsonl").read_text().splitlines()]
    rows[0]["at"] = fleet.iso(fleet.utcnow() - dt.timedelta(days=fleet.EXTERNAL_KEEP_DAYS, minutes=1))
    (ext_state / "inbox-external.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert [i["key"] for i in fleet.external_items(fleet.utcnow())] == ["ext:new"]
    assert [i["key"] for i in fleet.external_items()] == ["ext:old", "ext:new"]

# An added inbox item that ages out is on the timeline once, not every tick. A requests.jsonl left by the removed
# owner-request ledger is not read.
with (state_dir / "inbox-external.jsonl").open("a") as fh:
    fh.write(json.dumps({"op": "add", "key": "stale-login", "type": "login", "title": "Log in again",
                         "at": fleet.iso(clock[0] - dt.timedelta(days=fleet.EXTERNAL_KEEP_DAYS + 1))}) + "\n")
(state_dir / "requests.jsonl").write_text(json.dumps({"ev": "open", "id": "r1", "ts": fleet.iso(clock[0]), "text": "x"}) + "\n")
for _ in range(3):
    st = f.tick(force=True)
assert "ext:stale-login" not in {i["key"] for i in st["items"]} and "requests" not in st
assert [e["kind"] for e in f.events].count("inbox_aged_out") == 1
assert not any(e["kind"].startswith("request") for e in f.events)

# PR stats: the charts open on the last six months with the y-axis fitted to them, and every bar carries one tooltip
# for its whole month or week (states, total, line values) in place of an SVG <title> the poll would close.
if shutil.which("node"):
    rep = dash_demo.pr_stats_report(now)
    stat_helpers = "\n".join(take(n) for n in ("$", "niceStep", "yAxis", "kpi", "chips"))
    js = f"""{stat_helpers}
{(assets / "fleet.js").read_text()}
F.stats = {json.dumps(rep)}; F.statsAt = Date.now();
const view = (range) => {{ S.filters.pr_range = range; return fleetStats(); }};
process.stdout.write(JSON.stringify({{dflt: view(undefined), all: view("all"), d90: view("90d")}}));"""
    ctx = ("const vm = require('vm'), sb = {process, S: {filters: {}, section: 'stats'}, getComputedStyle: () => ({paddingLeft: '0', paddingRight: '0'}),"
           " document: {addEventListener() {}, querySelector: () => ({clientWidth: 1000})}};"
           "vm.runInNewContext(require('fs').readFileSync(0, 'utf8'), sb);")
    got = json.loads(subprocess.run(["node", "-e", ctx], input=helpers + "\n" + js, capture_output=True, text=True, check=True).stdout)
    charts = lambda html: [c.split("</svg>")[0] for c in html.split('<div class="chart">')[1:]]  # noqa: E731
    bars = lambda html: [c.count("data-tip=") for c in charts(html)]  # noqa: E731
    months, weeks = len(rep["views"]["all"]["months"]), len(rep["views"]["all"]["weeks"])
    assert bars(got["all"]) == [months, months, weeks], bars(got["all"])
    m6, _, w6 = bars(got["dflt"])
    assert m6 in (6, 7) and 26 <= w6 <= 28 and bars(got["d90"])[0] in (3, 4), (bars(got["dflt"]), bars(got["d90"]))
    assert 'data-val="6m" aria-pressed="true"' in got["dflt"], "six months is the default range"
    monthly, _, weekly = charts(got["dflt"])
    assert "<title>" not in monthly and monthly.count("&lt;b&gt;") == 5 * m6, "Merged, Open, Closed, Total and Trend per month"
    assert "Total&lt;b&gt;" in monthly and "Trend&lt;b&gt;" in monthly and "4-week average&lt;b&gt;" in weekly
    last = rep["views"]["all"]["monthly"]
    assert f'aria-label="{rep["views"]["all"]["months"][-1]}: {last["merged"][-1] + last["open"][-1] + last["closed"][-1]}, Merged {last["merged"][-1]}' in monthly

import dash  # noqa: E402
assert {"board.py", "holds.py", "stages.py"} <={f.name for f in dash.code_files()}
assert not any(f.name.startswith("test_") for f in dash.code_files())

# Orca link fields: linkedItems become refs, linkedPR gets its url (from linkedItems when Orca left it out) and an
# owner/repo#N key from that url, which may name another repository than the session's. An older Orca with none of
# these fields gives empty refs and no PR.
refs, lp = fleet.orca_links({"linkedPR": {"number": 5, "state": "merged"}, "linkedItems": [
    {"provider": "github", "type": "pr", "number": 5, "url": "https://github.com/acme/other/pull/5", "origins": [{"kind": "observed"}]},
    {"provider": "linear", "type": "issue", "url": "https://linear.app/acme/issue/ABC-1", "identifier": "ABC-1"}]})
assert lp == {"number": 5, "state": "merged", "url": "https://github.com/acme/other/pull/5", "key": "acme/other#5"}, lp
assert [(r["type"], r["key"]) for r in refs] == [("pr", "acme/other#5"), ("issue", "ABC-1")], refs
assert fleet.orca_links({}) == ([], None)
# A PR's key comes from its url only: an identifier such as "#7" would reach the PR probe as a repository.
refs, _ = fleet.orca_links({"linkedItems": [{"type": "pr", "identifier": "#7", "url": "https://github.com/acme/app/pull/7"},
                                            {"type": "pr", "identifier": "#8"}]})
assert [r["key"] for r in refs] == ["acme/app#7", None], refs
assert fleet.orca_links({"linkedPR": {"number": 3, "url": "https://github.com/acme/app/pull/3", "checksStatus": "success"}})[1]["key"] == "acme/app#3"
# A worker's brief reaches the Sent page from its Orca Task, with no `orch sent add` line.
rows = fleet.orca_sent([{"id": "task_1", "spec": "ABC-1 do it", "created_at": "2026-01-02 03:04:05", "created_by_terminal_handle": "term_x",
                         "task_title": "ABC-1"}, {"id": "task_2", "spec": None}])
assert rows == [{"ts": "2026-01-02T03:04:05Z", "from": "term_x", "to": "task:task_1", "kind": "worker", "channel": "orca worker-start",
                 "subject": "ABC-1", "text": "ABC-1 do it", "source": "orca"}], rows
# Orca's inbox is a Sent source: every message but heartbeats, so an `orchestration send` or `reply` needs no log line.
# A message up to a coordinator is a report, not an instruction.
msgs = [{"id": "m1", "type": "status", "from_handle": "term_a", "to_handle": "dispatch:ctx_1", "subject": "Re: q",
         "body": "do (c)", "thread_id": "m0", "run_id": "run_1", "created_at": fleet.iso(now)},
        {"id": "m2", "type": "heartbeat", "from_handle": "term_b", "to_handle": "run:run_1", "subject": "alive", "body": "",
         "created_at": fleet.iso(now)},
        {"id": "m3", "type": "worker_done", "from_handle": "term_b", "to_handle": "run:run_1", "subject": "done", "body": "",
         "created_at": fleet.iso(now)},
        {"id": "m4", "type": "status", "subject": "", "body": "", "created_at": fleet.iso(now)}, "junk"]
rows = fleet.orca_messages_sent(msgs)
assert [(r["to"], r["kind"], r["channel"], r["text"], r["message"], r["thread"]) for r in rows] == [
    ("dispatch:ctx_1", "inbox", "orca status", "do (c)", "m1", "m0"), ("run:run_1", "report", "orca worker_done", "done", "m3", None)], rows
later = now + dt.timedelta(minutes=1)
# A transcript's copy of a message folds into it only with the same receiver; one with other text or receiver stays.
copy = {"ts": fleet.iso(now), "to": "dispatch:ctx_1", "text": "do (c)", "channel": "orca orchestration send", "source": "transcript"}
elsewhere = {**copy, "to": "dispatch:ctx_2"}
unread = {**copy, "text": "$(cat f)"}
p = sent.page([copy, elsewhere, unread] + rows, days=1, now=later)
got = [(r["source"], r["to"], r.get("also")) for r in p["rows"] if r["to"] in ("dispatch:ctx_1", "dispatch:ctx_2")]
assert sorted(got, key=str) == sorted([("orca", "dispatch:ctx_1", ["transcript"]), ("transcript", "dispatch:ctx_2", None),
                                       ("transcript", "dispatch:ctx_1", None)], key=str), got
assert p["inbox_since"] is None, p["inbox_since"]
# The rows given are not changed: a second poll of the same rows gives the same page.
assert sent.page([copy, elsewhere, unread] + rows, days=1, now=later)["rows"] == p["rows"]
# Two Orca messages never fold, even with the same text, receiver and minute (two workers reporting "done").
twins = fleet.orca_messages_sent([{"id": i, "type": "worker_done", "from_handle": f, "to_handle": "run:run_1", "subject": "done",
                                   "created_at": fleet.iso(now)} for i, f in (("w1", "term_x"), ("w2", "term_y"))])
assert sorted(r["message"] for r in sent.page(twins, days=1, now=later)["rows"] if r.get("message")) == ["w1", "w2"]
# The inbox read is cut only when it came back full; the cut counts heartbeats too.
full = [{"type": "heartbeat", "created_at": f"2026-01-0{1 + i % 2}T00:00:00Z"} for i in range(fleet.INBOX_LIMIT)]
assert fleet.inbox_cut(full) == "2026-01-01T00:00:00Z" and fleet.inbox_cut(full[:2]) is None and fleet.inbox_cut(None) is None
assert sent.page([], days=1, now=later, inbox_since="2026-01-01T00:00:00Z")["inbox_since"] == "2026-01-01T00:00:00Z"
# Orca files a send to a Run's coordinator terminal under run:<id>, so a term_ copy still folds into it.
up = {**copy, "to": "term_coord", "text": "done"}
p = sent.page([up] + rows, days=1, now=later)
assert [(r["source"], r.get("also")) for r in p["rows"] if r["text"] == "done"] == [("orca", ["transcript"])], p["rows"]
# A send also logged by hand shows once, as the log row naming Orca; the same text to another receiver does not fold.
sent.add("dispatch:ctx_9", "logged twice", now=now)
p = sent.page(fleet.orca_messages_sent([{"id": "m9", "type": "status", "to_handle": "dispatch:ctx_9", "body": "logged twice",
                                          "created_at": fleet.iso(now)},
                                         {"id": "m10", "type": "status", "to_handle": "dispatch:ctx_8", "body": "logged twice",
                                          "created_at": fleet.iso(now)}]), days=1, now=later)
got = [(r["source"], r["to"], r.get("also")) for r in p["rows"] if r["text"] == "logged twice"]
assert sorted(got, key=str) == sorted([("log", "dispatch:ctx_9", ["orca"]), ("orca", "dispatch:ctx_8", None)], key=str), got
assert [r.get("message") for r in p["rows"] if r["source"] == "log" and r["text"] == "logged twice"] == ["m9"], "the log row keeps the message id"

# The ticket link prefix: dashboard.ticket_url, else nothing (and one stderr line, not a guess).
cfg_file = pathlib.Path(tempfile.mkdtemp()) / "agent-skills.json"
def ticket_url_for(cfg):
    if cfg is None:
        cfg_file.unlink(missing_ok=True)
    else:
        cfg_file.write_text(json.dumps(cfg))
    os.environ["AGENT_SKILLS_CONFIG"] = str(cfg_file)
    return fleet.ticket_url()
assert ticket_url_for({"dashboard": {"ticket_url": "https://t.example.test/i/"}}) == "https://t.example.test/i/"
assert ticket_url_for({"dashboard": {"ticket_url": "https://t.example.test/i"}}) == "https://t.example.test/i/", "slash added"
for bad in (None, {}, {"dashboard": {"ticket_url": "javascript:alert(1)/"}}, {"dashboard": {"ticket_url": "https://t.example.test/{key}"}},
            {"dashboard": {"ticket_url": "https://t.example.test/i?x=1"}}, {"dashboard": {"ticket_url": 5}}, {"dashboard": 3}, {"dashboard": {"ticket_url": "https:///"}}):
    assert ticket_url_for(bad) is None, bad
os.environ.pop("AGENT_SKILLS_CONFIG")
shutil.rmtree(cfg_file.parent)

# Reload marks: a session whose SessionStart record holds another hooks.json digest than the checkout's is "stale"; no
# record, an offline row or an unreadable reference is "unknown", never "stale".
plugin = pathlib.Path(tempfile.mkdtemp(prefix="test-reload-"))
(plugin / "hooks").mkdir()
(plugin / "hooks/hooks.json").write_text("{}")
sha = fleet.roles.hooks_sha(plugin)
for h, loaded in (("term_same", sha), ("term_old", "0" * 64)):
    fleet.roles.record_terminal(h, "s-" + h, "/w", 1.0, {"plugin_version": None, "hooks_sha": loaded})
fleet.roles.record_terminal("term_bare", "s-bare", "/w", 1.0, {})
fleet.roles.record_terminal("term_other", "s-gone", "/w", 1.0, {"plugin_version": None, "hooks_sha": "0" * 64})
marks = {k: {"phase": ph, "path": f"/w/{k}", "terminals": [{"handle": h}]} for k, ph, h in (
    ("same", "idle", "term_same"), ("old", "working", "term_old"), ("bare", "idle", "term_bare"),
    ("none", "idle", "term_missing"), ("off", "offline", "term_old"), ("shell", "open", "term_old"),
    ("moved", "idle", "term_other"))}
# Every row but the shell and the offline one has a live Claude session in its worktree; "moved" runs s-live, while
# the terminal's record names s-gone, a session that started there earlier.
live = {"s-term_same": "/w/same", "s-term_old": "/w/old", "s-bare": "/w/bare", "s-x": "/w/none", "s-live": "/w/moved/sub"}
fleet.reload_marks(marks, live, plugin)
assert {k: v["reload"] for k, v in marks.items()} == {"same": "ok", "old": "stale", "bare": "unknown", "none": "unknown", "off": "none",
                                                      "shell": "none", "moved": "unknown"}, marks
fleet.reload_marks(marks, live, plugin / "missing")
assert {v["reload"] for v in marks.values()} == {"none"}, "an unreadable reference marks nothing"
fleet.reload_marks(marks, None, plugin)
assert {v["reload"] for v in marks.values()} == {"none"}, "no live-session list, no mark"
fleet.roles.terminal_file("term_same").chmod(0)
fleet.reload_marks(marks, live, plugin)
assert marks["same"]["reload"] == "none" and marks["old"]["reload"] == "stale", "an unreadable record leaves its own row unmarked"
fleet.roles.terminal_file("term_same").chmod(0o644)
fleet.reload_marks(marks, live, plugin)
assert marks["same"]["reload"] == "ok"
assert all(s["reload"] in ("ok", "stale", "unknown", "none") for s in st["sessions"]), "the collector writes the mark on every session"
shutil.rmtree(plugin)

# P1a: a PR keeps every session that claimed it (`session` is still the first), and a Run says its coordinator's terminal.
st = f.tick(force=True)
assert st["prs"] and all("candidates" in p for p in st["prs"]), st["prs"]
assert all(p["candidates"][:1] == [p["session"]] for p in st["prs"] if p["session"]), [(p["key"], p["session"], p["candidates"]) for p in st["prs"]]
assert all("coordinator" in r for r in st["runs"]) and any(r["coordinator"] for r in st["runs"]), st["runs"]
login = next(w for w in world.worktrees if w["worktreeId"] == "wt-login")
twin = {**login, "worktreeId": "wt-login-twin", "displayName": "twin of login", "path": "/work/wt-login-twin"}
world.worktrees.append(twin)
twin_fleet = world.fleet(clock=lambda: clock[0])
for label in ("branch asked just now", "branch answer cached"):  # the first tick discovers the branch's PR, the second reuses it
    pr41 = next(p for p in twin_fleet.tick(force=True)["prs"] if p["key"] == "acme/launchpad#41")
    assert pr41["session"] == "wt-login" and pr41["candidates"] == ["wt-login", "wt-login-twin"], (label, pr41["session"], pr41["candidates"])
world.worktrees.remove(twin)

# #395 and #396 share compose(): one state carries both the session reload mark and the PR candidates / run coordinator.
both = f.tick(force=True)
assert all(s["reload"] in ("ok", "stale", "unknown", "none") for s in both["sessions"]), "reload on every session"
assert both["prs"] and all("candidates" in p for p in both["prs"]) and all("coordinator" in r for r in both["runs"]), "P1a fields kept"

print("test_fleet: ok")
