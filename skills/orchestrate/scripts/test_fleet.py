"""Offline tests for fleet.py: hierarchy, inbox, PR events, unread, chat guards and adoption, all on invented data.

Run: python3 test_fleet.py
"""
import datetime as dt, json, os, pathlib, sys, tempfile

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
assert got == sorted([("permission", "wt-docs"), ("question", "wt-export"), ("approval", "wt-coord"), ("login", "wt-scratch"),
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
