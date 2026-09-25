"""Offline tests for fleet.py: hierarchy, inbox, PR events, unread, chat guards and adoption, all on invented data.

Run: python3 test_fleet.py
"""
import contextlib, datetime as dt, json, os, pathlib, sys, tempfile

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
                      ("ready_to_merge", "wt-login"), ("changes_requested", "wt-export"),
                      ("review_comment_received", "wt-export"), ("ci_failed", "wt-export")]), got

# Fresh install: nothing has been looked at, so every session with an open item carries a badge.
assert ss["wt-export"]["unread"] == 4 and ss["wt-docs"]["unread"] == 1 and ss["wt-scratch"]["unread"] == 1, ss
assert not (state_dir / "pr-events.jsonl").exists()  # the first snapshot is a baseline, not a change

# Opening a session clears its badge; dismissing an item removes it.
fleet.mark_seen("wt-export")
fleet.dismiss(f, next(i["key"] for i in items(st, "ci_failed")))
st = f.tick(force=True)
ss = {s["id"]: s for s in st["sessions"]}
assert ss["wt-export"]["unread"] == 0 and not items(st, "ci_failed"), (ss["wt-export"], items(st, "ci_failed"))

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

# A prompt typed into a session is an event, and marks earlier items as seen by the person who typed it.
world.worktrees[3]["agents"][0]["prompt"] = "Approved, go ahead."
clock[0] = now + dt.timedelta(minutes=11)
st = f.tick(force=True)
ss = {s["id"]: s for s in st["sessions"]}
assert ss["wt-docs"]["unread"] == 0 and any(e["kind"] == "prompt" and e["session"] == "wt-docs" for e in st["timeline"])

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

# Classification of a finished turn's last lines.
assert fleet.classify("Build done. E2E test failed on the login page.") == "verify_failed"
assert fleet.classify("Login required: run `gh auth login`, then tell me.") == "login"
assert fleet.classify("Shall I merge it?") == "approval"
assert fleet.classify("Refactored the parser.") == "fyi"

print("test_fleet: ok")
