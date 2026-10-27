"""Offline tests for fleet.py: hierarchy, inbox, PR events, unread, chat guards and adoption, all on invented data.

Run: python3 test_fleet.py
"""
import contextlib, datetime as dt, json, os, pathlib, re, shutil, subprocess, sys, tempfile

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

# Every row names its project: Orca's project for a session, the task's worktree for a gate, the terminal for a decision.
assert {s["id"]: s["project"] for s in st["sessions"]} == {"wt-coord": "platform", "wt-login": "launchpad", "wt-export": "launchpad",
                                                         "wt-docs": "launchpad", "wt-scratch": "tools", "wt-old": "tools",
                                                         "wt-lead": "platform", "wt-invoice": "launchpad", "wt-tax": "launchpad"}
assert [(i["project"], i["session"]) for i in items(st, "approval") if i["source"] == "orca-gate"] == [("launchpad", "wt-coord")]
assert [i["project"] for i in items(st, "decision")] == ["platform"] and all(i["project"] for i in items(st))
assert {t["id"]: t["project"] for t in st["tasks"]} == {"task_login": "launchpad", "task_export": "launchpad", "task_docs": "launchpad",
                                                       "task_lead": "platform", "task_invoice": "launchpad"}
assert fleet.project_of("/h/orca/workspaces/acme-api/fix-1", None, "o/other") == "acme-api"
assert fleet.project_of("/src/app-wt", None, "o/app") == "app" and fleet.project_of("/src/app-wt") == "app-wt"
assert fleet.project_of(None) is None

# Secrets never reach disk.
for name in ("state.json", "memory.json"):
    assert secret not in (state_dir / name).read_text(), name

# PRs by branch, plus mine that no worktree has checked out (found by the search, open or merged within a day), and
# what each one asks of you: only an owned PR raises items.
prs = {p["number"]: p for p in st["prs"]}
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
                      ("review_comment_received", "wt-export"), ("ci_failed", "wt-export")]), got

# A session's badge is the number of Needs you items on it: the badges plus the sessionless items are the inbox.
def badges_agree(st):
    per = {s["id"]: sum(1 for i in st["items"] if i.get("session") == s["id"]) for s in st["sessions"]}
    assert {s["id"]: s["unread"] for s in st["sessions"]} == per, per
    assert sum(per.values()) + sum(1 for i in st["items"] if i.get("session") not in per) == len(st["items"])


badges_agree(st)
assert ss["wt-export"]["unread"] == 4 and ss["wt-docs"]["unread"] == 1 and ss["wt-scratch"]["unread"] == 1, ss
assert not (state_dir / "pr-events.jsonl").exists()  # the first snapshot is a baseline, not a change

# Dismissing an item removes it from the inbox and from its session's badge alike.
fleet.dismiss(f, next(i["key"] for i in items(st, "ci_failed")))
st = f.tick(force=True)
ss = {s["id"]: s for s in st["sessions"]}
assert ss["wt-export"]["unread"] == 3 and not items(st, "ci_failed"), (ss["wt-export"], items(st, "ci_failed"))
badges_agree(st)

# Needs you order: what only the owner settles, then a merge call waiting on them, then the rest; newest first in each.
ranks = [fleet.needs_rank(i) for i in items(st)]
assert ranks == sorted(ranks) and ranks.count(0) == 5 and ranks.count(1) == 1, [(i["type"], r) for i, r in zip(items(st), ranks)]
for r in set(ranks):
    ats = [i.get("at") or "" for i in items(st) if fleet.needs_rank(i) == r]
    assert ats == sorted(ats, reverse=True), (r, ats)
assert [i["type"] for i in items(st)][5] == "ready_to_merge"
# A PR a held board item names goes last, marked with the hold, until its date comes or the item leaves the board.
board_path, ahead = state_dir / "board.json", (now + dt.timedelta(days=2)).astimezone().strftime("%m/%d 09:00")


def hold(trigger):
    fleet.write_atomic(board_path, json.dumps({"doing": [], "owner_todo": [], "rows": {},
                                               "later": [{"title": "acme/launchpad#42 마무리", "trigger": trigger}]}))
    return f.tick(force=True)


st = hold(f"{ahead} 이후")
pr42 = [i for i in items(st) if i.get("pr") == "acme/launchpad#42"]
assert pr42 and all(i["held"] == f"{ahead} 이후" for i in pr42) and items(st)[-len(pr42):] == pr42, [(i["type"], i.get("held")) for i in items(st)]
assert not any(i.get("held") for i in items(st) if i.get("pr") != "acme/launchpad#42")
st = hold((now - dt.timedelta(days=1)).astimezone().strftime("%m/%d") + " 이후")
assert not any(i.get("held") for i in items(st)) and [fleet.needs_rank(i) for i in items(st)] == ranks
st = hold("acme/launchpad#41 머지 뒤")  # no date: held while the PR its trigger names is open
assert all(i.get("held") for i in items(st) if i.get("pr") == "acme/launchpad#42")
board_path.unlink()
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

# Decisions: `orch decide` keeps them in decisions.json; the fleet shows the open ones on the terminal that added them.
dec_state = pathlib.Path(tempfile.mkdtemp(prefix="test-decide-"))
body_file = dec_state / "body.md"
body_file.write_text(f"Pick one.\n<img src=x onerror=alert(1)>\nGH_TOKEN={secret}\n")
env = {**os.environ, "ORCH_FLEET_STATE": str(dec_state), "ORCA_TERMINAL_HANDLE": "term_coord"}


def orch(*args):
    r = subprocess.run([sys.executable, str(pathlib.Path(__file__).parent / "prog.py"), "decide", *args],
                       capture_output=True, text=True, env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


assert orch("add", "--title", "Export format", "--body-file", str(body_file), "--option", "CSV::finance uses sheets",
            "--option", "JSON", "--recommend", "1", "--link", "https://example.com/t/1") == (0, "d1", "")
assert orch("add", "--title", "Second", "--option", "A") == (0, "d2", "")
code, out, _ = orch("list")
assert code == 0 and out.splitlines()[1:3] == ["    1. CSV (recommended) — finance uses sheets", "    2. JSON"], out
saved = json.loads((dec_state / "decisions.json").read_text())["decisions"]["d1"]
assert saved["handle"] == "term_coord" and saved["status"] == "open" and secret not in saved["body"], saved
assert orch("add", "--title", "x", "--option", "A", "--recommend", "2")[0] == 1
assert orch("add", "--option", "A")[0] == 1  # no title
assert orch("done", "d1", "--answer", "CSV") == (0, "d1 done", "")
assert orch("done", "d1")[2] == "d1 is already done" and orch("drop", "d9")[2] == "no decision d9"
assert orch("drop", "d2") == (0, "d2 dropped", "")
assert orch("drop", "d2")[2] == "d2 is already dropped"
assert orch("drop", "d1") == (0, "d1 dropped", "")  # a done decision can be taken back (a misread answer)
saved = json.loads((dec_state / "decisions.json").read_text())["decisions"]["d1"]
assert saved["status"] == "dropped" and saved["answer"] == "CSV", saved
assert orch("list")[1] == ""

fleet.write_atomic(state_dir / "decisions.json", json.dumps({"next": 1}))
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
saved = json.loads((state_dir / "decisions.json").read_text())["decisions"][lost["id"]]
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
    ctx = ("const vm = require('vm'), handlers = {}, sb = {process, handlers, location: {hash: ''}, selection: '', CSS: {escape: String},"
           " getSelection: () => sb.selection, renderView() {}, document: {addEventListener(t, f) { handlers[t] = f; }, querySelector() { return null; }}};"
           "vm.runInNewContext(require('fs').readFileSync(0, 'utf8'), sb);")
    got = json.loads(subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout)
    assert not any("data-go" in got[k] for k in ("gone", "none", "own")), got
    assert got["clicks"] == {"row": "#/fleet/session/wt-coord", "link": "", "button": "", "selecting": ""}, got["clicks"]
    # A decision says how to answer it in the coordinator's terminal too, with the recommended option filled in.
    assert "<span class=\"mono\">d1: &lt;i&gt;CSV&lt;/i&gt;</span>" in html, html

    # Automations and shared resources render escaped; an overdue or failed automation is flagged and counted.
    js = f"""{helpers}
{take("hhmm")}
{(assets / "fleet.js").read_text()}
const old = new Date(Date.now() - 3 * 3600e3).toISOString(), soon = new Date(Date.now() + 600e3).toISOString();
const st = {{sessions: [{{id: "wt-coord", name: "<b>coord</b>", phase: "idle"}}], sources: {{automations: {{ok: true}}}},
  automations: [{{id: "a1", name: "<script>x</script>", rrule: "17 */3 * * *", enabled: true, next_run_at: soon, last_run_at: old,
                  recent: [{{status: "completed", at: old, summary: "<img src=x> " + "lastsum ".repeat(60) + "END"}}, {{status: "skipped_precheck", at: old}}]}},
                {{id: "a2", name: "late", rrule: "0 9 * * *", enabled: true, next_run_at: old, recent: []}},
                {{id: "a3", name: "broken", rrule: "*/20 * * * *", enabled: true, next_run_at: soon, recent: [{{status: "failed", at: old}}]}},
                {{id: "a4", name: "off", rrule: "x", enabled: false, next_run_at: old, recent: [{{status: "failed", at: old}}]}}],
  holds: [{{kind: "resource", resource: "browser", by: "term_coord", session: "wt-coord", note: "<i>login</i>", at: old}},
          {{kind: "lane", resource: "acme/app@main", by: "#7", note: null, at: soon}}]}};
F.jobsAt = Date.now();  // as /api/fleet/jobs answered it a moment ago
F.jobs = {{jobs: [{{key: "tidy", name: "Self-clean", cadence: "after each fleet collect", runs_in: "orch-dash",
                   record: {{at: old, ok: true, n: 0, result: "closed 0", acted_at: old, acted_n: 3, next_at: soon}}}},
                 {{key: "pr-stats", name: "PR stats", cadence: "every 6h", runs_in: "orch-dash",
                   record: {{at: old, ok: false, error: "<b>HTTP 502</b> " + "x".repeat(400), failed_at: old, warned_at: old, next_at: null}}}},
                 {{key: "skill-usage", name: "Skill usage", cadence: "every 15m", runs_in: "orch-dash", record: null}},
                 {{key: "reap-hard", name: "reap-hard", cadence: null, runs_in: "launchd", record: null, error: "<b>ValueError</b>('bad interval')"}}],
          rules: [{{name: "Inbox item expiry", rule: "7 days <i>after</i>", applied: "each fleet collect"}}]}};
process.stdout.write(JSON.stringify({{autos: fleetAutomations(st), holds: holdsCard(st), troubled: autoTroubled(st).map((a) => a.name)}}));"""
    got = json.loads(subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout)
    for raw in ("<script", "<img", "<i>", "<b>coord"):
        assert raw not in got["autos"] + got["holds"], raw
    assert got["troubled"] == ["late", "broken"], got["troubled"]
    assert "every 3h at :17" in got["autos"] and "daily 9:00" in got["autos"] and "every 20m" in got["autos"], got["autos"]
    assert "past its run time" in got["autos"] and "toned tone-bad" in got["autos"], "an overdue run is flagged and its next-run cell toned"
    assert got["autos"].count('class="run tone-good"') == 2 and 'class="run "' in got["autos"], "a completed and a skipped block"
    assert got["holds"].index("browser") < got["holds"].index("acme/app@main") and "landing lane" in got["holds"], got["holds"]
    # A long last result wraps whole in its cell instead of being cut with an ellipsis.
    autos_html = got["autos"][:got["autos"].index("orch-dash jobs")]
    assert "lastsum " * 60 + "END" in autos_html and "ellipsis" not in autos_html, autos_html
    # orch-dash's own jobs under them: a record as written, a failure whole with its link to the timeline, no record said.
    jobs_html = got["autos"][got["autos"].index("orch-dash jobs"):]
    assert "closed 0" in jobs_html and "3 · " in jobs_html, jobs_html
    assert "▲ failed" in jobs_html and "x" * 400 in jobs_html and 'href="#/fleet/timeline"' in jobs_html and "Timeline warning" in jobs_html
    assert "<b>HTTP" not in jobs_html and "<i>after" not in jobs_html, "job text is escaped"
    assert 'data-tl-open="job|pr-stats"' in jobs_html and 'aria-expanded="false"' in jobs_html, "a long error opens in place"
    assert "not read" in jobs_html and "&lt;b&gt;ValueError" in jobs_html, "a row it could not read says why, the rest still show"
    assert jobs_html.count("no record") == 1 and "Rules applied on read" in jobs_html and "Inbox item expiry" in jobs_html
    assert "no change" in jobs_html and "last acted: 3" in jobs_html, "a run that counted 0 says no change, its last change kept"

    # Jobs riding on the fleet tick fold into its row: closed, one summary; opened, a line each with its own result.
    tick = {"at": fleet.iso(now), "ok": True, "n": None, "result": "Needs you 4", "next_at": fleet.iso(now)}
    js = f"""{helpers}
{take("hhmm")}
{(assets / "fleet.js").read_text()}
F.jobsAt = Date.now(); const old = new Date(Date.now() - 3 * 3600e3).toISOString();
F.jobs = {{jobs: [{{key: "fleet-tick", name: "Fleet tick", every: "10s", cadence: "every 10s while a page is open", runs_in: "orch-dash", group: null, record: {json.dumps(tick)}}},
                 {{key: "tidy", name: "Self-clean", every: null, cadence: "after each fleet collect", runs_in: "orch-dash", group: "fleet-tick",
                   record: {{at: old, ok: true, n: 0, result: "closed 0", next_at: null}}}},
                 {{key: "relay", name: "Relay", every: null, cadence: "after each fleet collect", runs_in: "orch-dash", group: "fleet-tick",
                   record: {{at: old, ok: true, n: 2, result: "answers relayed 2", next_at: null}}}},
                 {{key: "reap-hard", name: "reap-hard", every: "10m", cadence: "every 10m", runs_in: "launchd", record: null, why: "agent not installed"}}],
          rules: []}};
const out = {{closed: internalJobs()}};
F.jobOpen.add("fleet-tick"); out.open = internalJobs();
process.stdout.write(JSON.stringify(out));"""
    got = json.loads(subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout)
    assert got["closed"].count("<tr") == 4 and "▸ 2 more" in got["closed"] and "Relay: <b>answers relayed 2</b>" in got["closed"], got["closed"]
    assert ">10s<" in got["closed"] and "Self-clean</b>" not in got["closed"], got["closed"]
    assert got["open"].count("<tr") == 6 and 'class="jsub"' in got["open"] and "Self-clean</b>" in got["open"], got["open"]
    assert "no record · agent not installed" in got["closed"] and 'class="is-done"' in got["closed"], got["closed"]

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
const ok = {{id: "a1", name: "ok", enabled: true, recent: [{{status: "completed", at: old}}]}}, out = {{}};
for (const [k, st] of Object.entries({{calm: {{sessions: [], automations: [ok, ok]}}, none: {{sessions: []}},
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
    # Nothing in an item or a request is cut: the text wraps, and the request card reaches every request on the page.
    assert "ellipsis" not in card and "row0" in card, card
    long = "x" * 400
    reqs = {"open": 12, "items": [{"id": f"r{n}", "text": f"req{n} {long}", "ts": fleet.iso(now), "links": [], "acked": False} for n in range(12)]}
    js = f"""{helpers}
{(assets / "fleet.js").read_text()}
const S = {{filters: {{}}}};
const st = {{requests: {json.dumps(reqs)}}};
const out = {{eight: requestCard(st)}};
F.reqShowAll = true; out.all = requestCard(st);
process.stdout.write(JSON.stringify(out));"""
    got = json.loads(subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout)
    assert got["eight"].count(">req") == 8 and "Show all 12" in got["eight"] and long in got["eight"], got["eight"][:400]
    assert got["all"].count(">req") == 12 and "Show 8" in got["all"] and "ellipsis" not in got["all"]

    # The stage table opens on open rows. Each program's finished rows wait behind its own "▸ N finished", and a program
    # with none open is one dimmed heading line after the open ones; Ack all sits inside an opened program, not on top.
    stage = {"rows": [{"id": "g2", "title": "past group", "group": True, "done_at": fleet.iso(now)},
                      {"id": "g2/a", "parent": "g2", "title": "past row", "cells": {}, "done_at": fleet.iso(now), "ack": "ackable"},
                      {"id": "g2/b", "parent": "g2", "title": "past row two", "cells": {}, "done_at": fleet.iso(now), "ack": "ackable"},
                      {"id": "g1", "title": "live group", "group": True}, {"id": "g1/a", "parent": "g1", "title": "open row", "cells": {}},
                      {"id": "g1/b", "parent": "g1", "title": "finished row", "cells": {}, "done_at": fleet.iso(now)}],
             "refs": {}, "env": {"prod": "v9.9.9", "at": fleet.iso(now - dt.timedelta(days=17))}}
    js = f"""{helpers}
{(assets / "fleet.js").read_text()}
const S = {{filters: {{}}}};
const out = {{open: stageSection({json.dumps(stage)})}};
F.stageFold.add("g1"); F.stageFold.add("g2"); out.all = stageSection({json.dumps(stage)});
process.stdout.write(JSON.stringify(out));"""
    got = json.loads(subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout)
    assert "open row" in got["open"] and "live group" in got["open"] and "past group" in got["open"], got["open"]
    assert got["open"].index("live group") < got["open"].index("past group"), "a program with open rows comes first"
    assert "▸ 1 finished" in got["open"] and "▸ 2 finished" in got["open"] and "Ack all" not in got["open"], got["open"]
    assert not any(t in got["open"] for t in ("finished row", "past row")), got["open"]
    assert all(t in got["all"] for t in ("finished row", "past row", "past row two")) and "Ack all 2" in got["all"], got["all"]
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
    assert 'href="#/fleet/stages"' in got["brief"] and "1 in progress" in got["brief"] and "open row" not in got["brief"], got["brief"]
    assert "open row" in got["stages"] and got["moved"] == "stages", got

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
fleet.write_atomic(state_dir / "board.json", json.dumps({"rows": {"x": {"id": "x", "pr": "acme/tools#8"}},
                                                        "later": [{"title": "t", "trigger": "acme/tools#7 머지"}]}))
assert fleet.board_prs() == {"acme/tools#8", "acme/tools#7"}, fleet.board_prs()
assert next(p for p in pr_tick(viewed=True)["prs"] if p["number"] == 8)["state"] == "CLOSED"
(state_dir / "board.json").unlink()
# Nor while a program's ledger names it: the stage table reads a landed PR's branch from here once its card is gone.
prog_dir = root / "no-programs" / "p1"
prog_dir.mkdir(parents=True)
(prog_dir / "program.json").write_text(json.dumps({"repo": "acme/tools", "created_at": fleet.iso(clock[0])}))
(prog_dir / "ledger.jsonl").write_text(json.dumps({"ts": fleet.iso(clock[0]), "ev": "landed", "pr": 8, "sha": "8" * 40}) + "\n")
assert fleet.program_prs(clock[0]) == {"acme/tools#8"}
assert next(p for p in pr_tick(viewed=True)["prs"] if p["number"] == 8)["state"] == "CLOSED"
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
assert [a["name"] for a in st["automations"]] == ["janitor", "pr-sweeper", "weekly-digest"] and st["sources"]["automations"]["ok"], st["automations"]
secret_out = "## posted with xoxb-" + "1" * 12 + "-" + "a" * 24 + "\nsecond line"


def fake_orca(*args, timeout=30):
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
assert autos[0]["next_run_at"] == "2026-10-09T11:40:00Z" and "xoxb" not in json.dumps(autos), autos
assert autos[0]["recent"][0]["summary"] == "posted with [masked]", autos[0]["recent"][0]

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
assert f.sent == world.sent and st["sources"]["sent"]["ok"] and "sent" not in st, st["sources"]
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

# The tick settles the request ledger: a request whose PR merged closes, one whose PR closed unmerged stays open with
# the link tagged. A ledger it cannot settle warns once on the timeline, not every tick; an added inbox item that
# ages out is on the timeline once too.
import owner_requests  # noqa: E402
world.prs[43].update(state="CLOSED", mergeStateStatus="UNKNOWN")
ids = []
for text, to in (("merge acme/tools#6 once green", "acme/tools#6"), ("finish the setup guide", "acme/launchpad#43")):
    ids.append(owner_requests.add(text, "s-coord", "term_coord", now=clock[0])[0]["id"])
    owner_requests.close(ids[-1], "link", to=to)
merged_id, closed_id = ids
with (state_dir / "inbox-external.jsonl").open("a") as fh:
    fh.write(json.dumps({"op": "add", "key": "stale-login", "type": "login", "title": "Log in again",
                         "at": fleet.iso(clock[0] - dt.timedelta(days=fleet.EXTERNAL_KEEP_DAYS + 1))}) + "\n")
st = f.tick(force=True)
asked = owner_requests.fold(owner_requests.read())
assert asked[merged_id]["status"] == "done" and "acme/tools#6 merged" in asked[merged_id]["note"], asked[merged_id]
assert asked[closed_id]["status"] == "open"
shown = {q["id"]: q for q in st["requests"]["items"]}
assert merged_id not in shown and shown[closed_id]["closed"] == ["acme/launchpad#43"], shown.get(closed_id)
assert any(e["kind"] == "request_done" and e["text"].startswith(merged_id) for e in f.events)
assert "ext:stale-login" not in {i["key"] for i in st["items"]}
# Reopened by the owner, a request whose links are all over stays open: only a link made after the reopen closes it.
owner_requests.reopen(merged_id)
f.tick(force=True)
assert owner_requests.fold(owner_requests.read())[merged_id]["status"] == "open"
owner_requests.close(merged_id, "link", to="acme/tools#6")
f.tick(force=True)
asked = owner_requests.fold(owner_requests.read())
assert asked[merged_id]["status"] == "done" and owner_requests.live_links(asked[merged_id]) == ["acme/tools#6"], asked[merged_id]
import jobs  # noqa: E402
assert jobs.records()["requests-reconcile"]["ok"] and jobs.records()["requests-reconcile"]["acted_n"] == 1
real_reconcile = owner_requests.reconcile
owner_requests.reconcile = lambda *a, **k: 1 / 0
try:
    for _ in range(3):
        st = f.tick(force=True)
finally:
    owner_requests.reconcile = real_reconcile
assert [e["kind"] for e in f.events].count("requests_error") == 1
assert [e["kind"] for e in f.events].count("inbox_aged_out") == 1
rec = jobs.records()["requests-reconcile"]
assert not rec["ok"] and "ZeroDivisionError" in rec["error"], rec
assert rec["warned_at"] == next(e["at"] for e in f.events if e["kind"] == "requests_error")
f.tick(force=True)
assert jobs.records()["requests-reconcile"]["ok"]
# A reconcile error with jobs.json unwritable stays inside the tick: it returns, and the write failure goes to stderr.
import contextlib, io  # noqa: E402
saved = jobs.path().read_text()
jobs.path().unlink()
jobs.path().mkdir()
owner_requests.reconcile, err = (lambda *a, **k: 1 / 0), io.StringIO()
try:
    with contextlib.redirect_stderr(err):
        st = f.tick(force=True)
finally:
    owner_requests.reconcile = real_reconcile
    jobs.path().rmdir()
    jobs.path().write_text(saved)
assert isinstance(st, dict) and "jobs.json" in err.getvalue(), err.getvalue()

import dash  # noqa: E402
assert {"owner_requests.py", "board.py", "holds.py", "stages.py"} <= {f.name for f in dash.code_files()}
assert not any(f.name.startswith("test_") for f in dash.code_files())

print("test_fleet: ok")
