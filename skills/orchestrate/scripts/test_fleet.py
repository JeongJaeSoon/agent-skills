"""Offline tests for fleet.py: hierarchy, inbox, PR events, unread, chat guards and adoption, all on invented data.

Run: python3 test_fleet.py
"""
import contextlib, datetime as dt, json, os, pathlib, shutil, subprocess, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import dash_demo

root = pathlib.Path(tempfile.mkdtemp(prefix="test-fleet-"))
os.environ.update(dash_demo.build(root))
import fleet  # noqa: E402  after the env points the state directory at root

state_dir = pathlib.Path(os.environ["ORCH_FLEET_STATE"])
now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
clock = [now]
world = dash_demo.FakeFleetWorld(now)
secret = "ghp_" + "a1" * 18
world.worktrees[0]["agents"][0]["prompt"] = f"deploy with GH_TOKEN={secret}"
f = world.fleet(clock=lambda: clock[0])


def items(st, t=None):
    return [i for i in st["items"] if t is None or i["type"] == t]


# Hierarchy: the newest Run's coordinator is the root; workers sit under it, sessions you opened sit under it too.
st = f.tick(force=True)
ss = {s["id"]: s for s in st["sessions"]}
assert st["root"] == "wt-coord", st["root"]
assert ss["wt-coord"]["kind"] == "orchestrator" and ss["wt-coord"]["parent"] is None
assert all(ss[w]["kind"] == "task" and ss[w]["parent"] == "wt-coord" for w in ("wt-login", "wt-export", "wt-docs"))
assert ss["wt-scratch"]["kind"] == "standalone" and ss["wt-scratch"]["parent"] == "wt-coord"
assert {w: ss[w]["phase"] for w in ss} == {"wt-coord": "working", "wt-login": "idle", "wt-export": "working",
                                            "wt-docs": "waiting", "wt-scratch": "idle", "wt-old": "offline"}, ss
assert ss["wt-login"]["task_title"] == "ACME-101 login flow" and ss["wt-login"]["terminal"] == "term_login"
assert all(v["ok"] for v in st["sources"].values()), st["sources"]

# Every row names its project: Orca's project for a session, the task's worktree for a gate, the terminal for a decision.
assert {s["id"]: s["project"] for s in st["sessions"]} == {"wt-coord": "platform", "wt-login": "launchpad", "wt-export": "launchpad",
                                                         "wt-docs": "launchpad", "wt-scratch": "tools", "wt-old": "tools"}
assert [(i["project"], i["session"]) for i in items(st, "approval") if i["source"] == "orca-gate"] == [("launchpad", "wt-coord")]
assert [i["project"] for i in items(st, "decision")] == ["platform"] and all(i["project"] for i in items(st))
assert {t["id"]: t["project"] for t in st["tasks"]} == {"task_login": "launchpad", "task_export": "launchpad", "task_docs": "launchpad"}
assert fleet.project_of("/h/orca/workspaces/acme-api/fix-1", None, "o/other") == "acme-api"
assert fleet.project_of("/src/app-wt", None, "o/app") == "app" and fleet.project_of("/src/app-wt") == "app-wt"
assert fleet.project_of(None) is None

# Secrets never reach disk.
for name in ("state.json", "memory.json"):
    assert secret not in (state_dir / name).read_text(), name

# PRs by branch, and what each one asks of you.
prs = {p["number"]: p for p in st["prs"]}
assert {n: prs[n]["session"] for n in prs} == {41: "wt-login", 42: "wt-export", 43: "wt-docs"}, prs
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

# A reload skills-sync could not send sits on its terminal's session (and counts there); an unknown terminal on none.
fleet.write_atomic(state_dir.parent / "reload-pending.json", json.dumps({"since": "t1", "failed": {
    "term_login": {"title": "✳ ACME-101", "reason": "composer holds a draft"}, "term_gone": {"reason": "x"}}}))
st = f.tick(force=True)
assert sorted((i["session"] or "") for i in items(st, "reload_pending")) == ["", "wt-login"], items(st, "reload_pending")
badges_agree(st)
(state_dir.parent / "reload-pending.json").unlink()

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
fleet.fetch_fast, fleet.fetch_runs = world.fetch_fast, world.fetch_runs
fleet.orca = lambda *a, **k: calls.append(a) or {}
world.worktrees[2]["parentWorktreeId"] = "wt-elsewhere"  # a parent the user chose is left alone
assert fleet.adopt() == 0 and calls == []
assert fleet.adopt(apply=True) == 1 and calls == []
pathlib.Path(os.environ["ORCH_FLEET_CONFIG"]).write_text(json.dumps({"adopt": {"write_orca_parent": True}}))
assert fleet.adopt(apply=True) == 0
assert sorted(c[3] for c in calls) == ["id:wt-docs", "id:wt-login", "id:wt-old", "id:wt-scratch"], calls
assert all(c[4:] == ("--parent-worktree", "id:wt-coord") for c in calls)
calls.clear()
assert fleet.adopt(undo="all") == 0
assert sorted(calls) == sorted(("worktree", "set", "--worktree", f"id:{w}", "--no-parent")
                               for w in ("wt-docs", "wt-login", "wt-old", "wt-scratch")), calls
calls.clear()
fleet.adopt(undo="all")
assert calls == []  # the latest row per worktree is now an undo: nothing to restore twice

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
F.open["decision:d1"] = true; F.open["msg:t1"] = true;
const st = {{sessions: [{{id: "wt-coord", name: "coordinator", terminals: []}}]}};
process.stdout.write([{json.dumps(hostile)}, {json.dumps(turn)}, {json.dumps(flat)}].map((it) => itemRow(st, it)).join("\\n-----\\n"));"""
    html, turn_html, flat_html = subprocess.run(["node", "-e", "const vm = require('vm'); vm.runInNewContext(require('fs').readFileSync(0, 'utf8'), "
                                         "{document: {addEventListener() {}}, process});"],
                          input=js, capture_output=True, text=True, check=True).stdout.split("\n-----\n")
    for raw in ("<script", "<img", "<i>", "<b>x", 'href="javascript', "\"quoted\"", "<u>", 'p"q'):
        assert raw not in html, (raw, html)
    assert "&lt;img src=x onerror=alert(1)&gt;" in html and "&lt;i&gt;CSV&lt;/i&gt;" in html
    assert 'href="https://example.com/a?b=1&amp;c=2"' in html, "a URL becomes a link, the trailing dot stays text"
    assert html.count('data-decide="opt:') == 2 and 'data-decide="text"' in html and 'id="decide-d1"' in html
    assert 'data-group="fleet_project" data-val="&lt;u&gt;p&quot;q&lt;/u&gt;"' in html, "the project badge filters, escaped"
    # Every row links to its item page; the chevron alone expands the text, in a full-width band outside the title column.
    assert 'data-href="#/fleet/item/decision%3Ad1"' in html and '<a href="#/fleet/item/decision%3Ad1">' in html
    assert 'data-toggle="decision:d1" aria-expanded="true"' in html and '<ol class="opts">' in html and "Details" not in html
    assert '</span></span>\n    <div class="more"><div class="pre">Done.\n&lt;b&gt;Shall I merge?&lt;/b&gt;</div></div></li>' in turn_html, turn_html
    assert 'data-toggle="msg:t2" aria-expanded="false"' in flat_html and 'class="more"' not in flat_html, "one line still expands to wrap"

    # Per type: every kind of item opens its page, and has the toggle exactly when it has text past the summary.
    # Then the click handler itself on a stub DOM: row -> page, chevron -> expand only, title link and open text -> neither.
    js = f"""{helpers}
{(assets / "fleet.js").read_text()}
const st = {{sessions: [{{id: "wt-coord", name: "coordinator", terminals: []}}]}};
st.items = Object.keys(ITEM).map((type, i) => ({{key: `k:${{type}}/${{i}}#x`, type, session: i % 2 ? "wt-coord" : null, title: "t",
  detail: type.startsWith("ci") ? "" : "line one\\nline two", at: new Date().toISOString()}}));
const first = st.items[0].key, out = {{rows: st.items.map((it) => [it.type, itemRow(st, it)])}};
out.section = fleetSection("item", first);
out.page = fleetItem(st);
F.itemKey = "gone"; out.gone = fleetItem(st);
const matches = (el, sel) => sel.split(",").some((one) => one[0] === "[" ? one.slice(6, -1).replace(/-(\\w)/g, (_, c) => c.toUpperCase()) in el.dataset
  : one[0] === "." ? el.cls.includes(one.slice(1)) : el.tag === one);
const node = (tag, dataset = {{}}, parent = null, cls = []) => ({{tag, dataset, parent, cls,
  closest(sel) {{ for (let n = this; n; n = n.parent) if (matches(n, sel)) return n; return null; }}}});
const li = node("li", {{href: itemHref(first)}}), grow = node("div", {{}}, li), title = node("a", {{}}, node("div", {{}}, grow));
const toggle = node("button", {{toggle: first}}, node("span", {{}}, li)), more = node("div", {{}}, node("div", {{}}, li, ["more"]));
const click = (target) => {{ let stopped = false; location.hash = ""; F.open = {{}}; handlers.click({{target, stopPropagation() {{ stopped = true; }}}});
  return {{hash: location.hash, open: !!F.open[first], stopped}}; }};
out.clicks = {{row: click(grow), toggle: click(toggle), title: click(title), more: click(more)}};
selection = "picked"; out.clicks.selecting = click(grow);
process.stdout.write(JSON.stringify(out));"""
    ctx = ("const vm = require('vm'), handlers = {}, sb = {process, handlers, location: {hash: ''}, selection: '', CSS: {escape: String},"
           " getSelection: () => sb.selection, renderView() {},"
           " document: {addEventListener(t, f) { handlers[t] = f; }, querySelector() { return null; }, activeElement: null}};"
           "vm.runInNewContext(require('fs').readFileSync(0, 'utf8'), sb);")
    got = json.loads(subprocess.run(["node", "-e", ctx], input=js, capture_output=True, text=True, check=True).stdout)
    for kind, row in got["rows"]:
        assert f'data-href="#/fleet/item/k%3A{kind}' in row and f'<a href="#/fleet/item/k%3A{kind}' in row, (kind, row)
        assert (f'data-toggle="k:{kind}/' in row) == (not kind.startswith("ci")), (kind, row)
    assert got["section"] == "item"
    assert 'class="rows items page"' in got["page"] and "data-href" not in got["page"] and "data-toggle" not in got["page"]
    assert '<div class="more"><div class="pre">line one\nline two</div></div>' in got["page"], got["page"]
    assert "no longer waiting on you" in got["gone"]
    c = got["clicks"]
    assert c["row"] == {"hash": "#/fleet/item/k%3Aprompt%2F0%23x", "open": False, "stopped": False}, c
    assert c["toggle"] == {"hash": "", "open": True, "stopped": True}, c
    assert c["title"]["hash"] == c["more"]["hash"] == c["selecting"]["hash"] == "", c
else:
    print("test_fleet: node not found, rendering check skipped")

# PR freshness, counted per probed PR. A quiet PR (cold: no update for days, no CI running, not approved, its session
# not working) waits for its tier with no page open, is probed every PR tick while a page is open, and at once when
# its session's phase changes. A full read that fails is retried at the next probe instead of being forgotten.
probed, real_gql, fail_detail = [], f.graphql, [False]


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
# Few GraphQL points left: the PR tick waits (up to 15 min) instead of spending them.
f.rate, f.last["prs"] = 100, fleet.time.monotonic() - 100
probed.clear()
f.tick(viewed=True)
assert probed == [], probed
f.rate = None
world.prs[43]["state"], world.prs[43]["commits"], world.worktrees[3]["agents"][0]["state"] = "OPEN", ci43, "waiting"
f.graphql = real_gql

# Classification of a finished turn's last lines.
assert fleet.classify("Build done. E2E test failed on the login page.") == "verify_failed"
assert fleet.classify("Login required: run `gh auth login`, then tell me.") == "login"
assert fleet.classify("Shall I merge it?") == "approval"
assert fleet.classify("Refactored the parser.") == "fyi"

print("test_fleet: ok")
