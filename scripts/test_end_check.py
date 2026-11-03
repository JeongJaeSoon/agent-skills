"""`skills-sync end-check`: end-session §4/§7/§8's table, one test per row, and --apply against a fake orca.
Run: python3 scripts/test_end_check.py"""
import contextlib, io, json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-end-check-")).resolve()
os.environ.update(AGENT_SKILLS_STATE=str(tmp / "state"), ORCA_TERMINAL_HANDLE="term_me")
sys.path.insert(0, str(HERE))
import end_check  # noqa: E402

CLEAN = {"tracked": [], "caches": ["x/__pycache__/"], "untracked": [], "branch": "b", "open_prs": [], "on_main": True,
         "main": False, "path": "/wt/card", "tickets": [], "other_agents": [], "questions": []}


def judge(mode="worker", outcome="succeeded", keep=False, ticket_done=False, **facts):
    return end_check.judge({**CLEAN, **facts}, mode, outcome, keep, ticket_done)


# Remove: every criterion holds (build caches do not count as dirt).
assert judge() == ("remove", ["the Remove row holds"]), judge()
assert judge(mode="card")[0] == "remove" and judge(mode="user", keep=None)[0] == "remove"
# The keep rows, each alone.
rows = {
    "failed outcome": dict(outcome="failed"),
    "open PR": dict(open_prs=[{"number": 1, "url": "https://example.test/acme/app/pull/1"}]),
    "question waiting": dict(questions=["msg_1"]),
    "dirty tree": dict(tracked=[" M a.py"]),
    "unexpected untracked file": dict(untracked=["notes.txt"]),
    "work not on main": dict(on_main=False),
    "KEEP row": dict(keep=True),
}
for name, case in rows.items():
    verdict, reasons = judge(**case)
    assert verdict == "keep" and reasons, (name, verdict, reasons)
assert "outcome failed" in judge(outcome="failed")[1][0]
assert "pull/1" in judge(open_prs=[{"number": 1, "url": "https://example.test/acme/app/pull/1"}])[1][0]
# Unreadable is keeping, whichever fact it is: no rm on a guess.
for fact in ("tracked", "untracked", "on_main", "open_prs", "main", "other_agents", "questions"):
    verdict, reasons = judge(**{fact: None})
    assert verdict == "keep" and any(r.startswith("cannot read") and fact in r for r in reasons), (fact, reasons)
assert judge(outcome=None) == ("keep", ["cannot read: outcome (pass --outcome)"])
assert judge(keep=None)[0] == "keep" and "KEEP row" in judge(keep=None)[1][0]
# close-terminal: the Remove row holds but the card stays.
assert judge(main=True) == ("close-terminal", ["isMainWorktree"])
assert judge(other_agents=["term_x"])[0] == "close-terminal"
assert judge(main=True, open_prs=[{"number": 2}])[0] == "keep", "a hold wins over close-terminal"
# --card: a linked ticket needs --ticket-done (its state is not read here).
assert judge(mode="card", tickets=[{"id": "ABC-1"}])[0] == "keep"
assert judge(mode="card", tickets=[{"id": "ABC-1"}], ticket_done=True)[0] == "remove"
assert judge(mode="card", tickets=None)[0] == "keep"
# No mode (the user asked): a dirty tree alone asks the human; with a hold it keeps.
assert judge(mode="user", keep=None, tracked=[" M a.py"])[0] == "ask"
assert judge(mode="user", keep=None, tracked=[" M a.py"], open_prs=[{"number": 3}])[0] == "keep"

# What counts as removable leftovers.
assert end_check.removable("pkg/__pycache__/", set()) and end_check.removable("web/node_modules/.cache/", set())
assert end_check.removable(".agents/skills/foo/", {"foo"}) and not end_check.removable(".agents/skills/foo/", set())
assert not end_check.removable("notes.txt", set()) and not end_check.removable(".env", set())

# KEEP row from the brief file.
brief = tmp / "brief.md"
brief.write_text("/goal x\nKEEP        the next review round\n")
assert end_check.keep_row({"--brief": str(brief)}) is True
brief.write_text("/goal x\nCLOSE: done\n```\nKEEP        quoted template\n```\n")
assert end_check.keep_row({"--brief": str(brief)}) is False
assert end_check.keep_row({}) is None and end_check.keep_row({"--no-keep": True}) is False
assert end_check.keep_row({"--brief": str(tmp / "missing.md")}) is None

# Questions: this terminal's `question` mail with no reply from someone else; rows with no sender are unknown.
FAKE = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
with open(os.environ["FAKE_CALLS"], "a") as f:
    f.write(json.dumps(a) + "\n")
mode = os.environ.get("FAKE_MODE", "")
if a[:2] == ["orchestration", "inbox"]:
    rows = json.loads(os.environ["FAKE_INBOX"])
    print(json.dumps({"ok": True, "result": {"messages": rows}}))
elif a[:2] == ["orchestration", "worker-show"]:
    if mode == "orca-down":
        sys.exit(1)
    print(json.dumps({"ok": True, "result": {"dispatch": {"status": os.environ.get("FAKE_STATUS", "dispatched")}}}))
elif a[:2] == ["worktree", "current"]:
    print(json.dumps({"ok": True, "result": {"worktree": json.loads(os.environ["FAKE_WT"])}}))
elif a[:2] == ["terminal", "list"]:
    print(json.dumps({"ok": True, "result": {"terminals": [{"handle": "term_me", "agentIdentity": "claude"}]}}))
elif a[:2] in (["worktree", "rm"], ["terminal", "close"]):
    print(json.dumps({"ok": True, "result": {}}))
else:
    sys.exit(1)
'''
(tmp / "orca").write_text(FAKE)
(tmp / "orca").chmod(0o755)
CALLS = tmp / "calls.jsonl"
os.environ.update(FAKE_CALLS=str(CALLS))
end_check.ORCA = str(tmp / "orca")
Q = {"id": "m1", "type": "question", "from_handle": "term_me", "to_handle": "run:r", "created_at": "2026-01-01 10:00"}
ME = {"term_me"}
os.environ["FAKE_INBOX"] = json.dumps([Q])
assert end_check.waiting_questions(ME) == ["m1"]
os.environ["FAKE_INBOX"] = json.dumps([Q, {"id": "m2", "type": "status", "thread_id": "m1", "from_handle": "term_lead",
                                           "created_at": "2026-01-01 10:05"}])
assert end_check.waiting_questions(ME) == []
with contextlib.redirect_stderr(io.StringIO()):
    os.environ["FAKE_INBOX"] = json.dumps([{"id": "m3", "type": "question"}])
    assert end_check.waiting_questions(ME) is None
    # One row without a sender is enough to make the rest unknown; so is no messages list, or a full page.
    os.environ["FAKE_INBOX"] = json.dumps([dict(Q, id="m4", from_handle="term_x"), {"id": "m5", "type": "question"}])
    assert end_check.waiting_questions(ME) is None
    os.environ["FAKE_INBOX"] = "null"
    assert end_check.waiting_questions(ME) is None
    limit, end_check.INBOX_LIMIT = end_check.INBOX_LIMIT, "1"
    os.environ["FAKE_INBOX"] = json.dumps([dict(Q, from_handle="term_x")])
    assert end_check.waiting_questions(ME) is None
    end_check.INBOX_LIMIT = limit
# A worker's question can come from its Dispatch.
os.environ["FAKE_INBOX"] = json.dumps([dict(Q, from_handle="dispatch:ctx_1")])
assert end_check.waiting_questions({"term_me", "dispatch:ctx_1"}) == ["m1"]
assert end_check.waiting_questions({"term_me", "dispatch:ctx_10"}) == []

# card_facts: a worktree with no linkedItems list leaves its ticket unknown; a worker with no Dispatch id leaves its
# questions unknown.
os.environ["FAKE_INBOX"] = "[]"
os.environ["FAKE_WT"] = json.dumps({"isMainWorktree": False, "path": "/w"})
with contextlib.redirect_stderr(io.StringIO()):
    f = end_check.card_facts("term_me")
assert f["tickets"] is None and f["questions"] == [], f
os.environ["FAKE_WT"] = json.dumps({"isMainWorktree": False, "path": "/w", "linkedItems": [], "linkedIssue": None})
f = end_check.card_facts("term_me")
assert f["tickets"] == [] and f["other_agents"] == [], f
assert end_check.card_facts("term_me", "worker", None)["questions"] is None
assert end_check.card_facts("term_me", "worker", "ctx_1")["questions"] == []

# --apply: a worker's remove waits for its Dispatch to settle (worker_done arrived); unread is refused too.
CLEAN_FACTS = dict(CLEAN, caches=[])


def apply(verdict, mode="worker", status=None, fmode=""):
    os.environ.update(FAKE_STATUS=status or "dispatched", FAKE_MODE=fmode)
    CALLS.write_text("")
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        rc = end_check.apply(verdict, CLEAN_FACTS, mode, {"--dispatch": "ctx_1"}, "term_me")
    return rc, err.getvalue(), [json.loads(l) for l in CALLS.read_text().splitlines()]


rc, err, calls = apply("remove")
assert rc == 3 and "dispatched" in err and not any(c[:2] == ["worktree", "rm"] for c in calls), (rc, err, calls)
rc, err, calls = apply("remove", fmode="orca-down")
assert rc == 3 and not any(c[:2] == ["worktree", "rm"] for c in calls), (rc, calls)
rc, err, calls = apply("remove", status="completed")
assert rc == 0 and ["worktree", "rm", "--worktree", "current", "--json"] in calls, calls
rc, err, calls = apply("close-terminal", status="completed")
assert rc == 0 and ["terminal", "close", "--terminal", "term_me", "--json"] in calls, calls
# A card has no Dispatch to wait for; keep applies nothing for it.
rc, err, calls = apply("remove", mode="card")
assert rc == 0 and calls == [["worktree", "rm", "--worktree", "current", "--json"]], calls
rc, err, calls = apply("keep", mode="card")
assert rc == 0 and calls == []

# End to end on a throwaway repo: a dirty card keeps, and the verdict is the printed JSON.
repo = tmp / "repo"
subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
(repo / "a.txt").write_text("x\n")
out = io.StringIO()
cwd = os.getcwd()
os.chdir(repo)
try:
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        rc = end_check.main(["--worker", "--outcome", "succeeded", "--no-keep"])
finally:
    os.chdir(cwd)
res = json.loads(out.getvalue())
assert rc == 0 and res["verdict"] == "keep" and any("a.txt" in r for r in res["reasons"]), res
with contextlib.redirect_stderr(io.StringIO()):
    assert end_check.main(["--worker", "--card"]) == 2

# on_main: a merged PR counts only when it merged this HEAD; a commit after it, or a failed fetch, does not land.
origin, clone = tmp / "origin.git", tmp / "clone"
git = lambda *a, cwd=clone: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test",  # noqa: E731
                                            "-c", "commit.gpgsign=false", *a], cwd=cwd, check=True,
                                           capture_output=True, text=True).stdout.strip()
subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)
git("commit", "-q", "--allow-empty", "-m", "base")
git("push", "-q", "origin", "main")
git("switch", "-q", "-c", "feat")
(clone / "b.txt").write_text("b\n")
git("add", "b.txt")
git("commit", "-q", "-m", "work")
merged_at = git("rev-parse", "HEAD")
(tmp / "gh").write_text("#!/bin/sh\n[ \"$6\" = merged ] && printf '%s' \"$FAKE_MERGED\" || echo '[]'\n")
(tmp / "gh").chmod(0o755)
end_check.GH = str(tmp / "gh")
os.environ["FAKE_MERGED"] = json.dumps([{"number": 1, "url": "u", "headRefOid": merged_at}])
assert end_check.git_facts(str(clone))["on_main"] is True
(clone / "c.txt").write_text("c\n")
git("add", "c.txt")
git("commit", "-q", "-m", "after the merge")
assert end_check.git_facts(str(clone))["on_main"] is False
git("remote", "set-url", "origin", str(tmp / "nowhere.git"))
assert end_check.git_facts(str(clone))["on_main"] is None

print("ok")
