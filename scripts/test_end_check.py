"""`skills-sync end-check`: end-session §4/§7/§8's table, one test per row, and --apply against a fake orca.
Run: python3 scripts/test_end_check.py"""
import contextlib, io, json, os, pathlib, subprocess, sys, tempfile, time

HERE = pathlib.Path(__file__).resolve().parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-end-check-")).resolve()
os.environ.update(AGENT_SKILLS_STATE=str(tmp / "state"), ORCA_TERMINAL_HANDLE="term_me")
sys.path.insert(0, str(HERE))
import end_check  # noqa: E402

CLEAN = {"tracked": [], "caches": ["x/__pycache__/"], "untracked": [], "branch": "b", "open_prs": [], "on_main": True,
         "main": False, "path": "/wt/card", "tickets": [], "other_agents": [], "questions": []}


def judge(mode="worker", outcome="succeeded", keep=False, ticket_done=False, **facts):
    return end_check.judge({**CLEAN, **facts}, mode, outcome, keep, ticket_done)


# Every criterion holds (build caches do not count as dirt): only a person's ending removes; a worker and a dispatched
# card close their terminal and the cleaners take the card.
assert judge(mode="user", keep=None) == ("remove", ["the Remove row holds"]), judge(mode="user", keep=None)
assert judge()[0] == judge(mode="card")[0] == "close-terminal" and "janitor" in judge()[1][0], judge()
# An orca.yaml that exists but cannot be read keeps the card before any rm: a direct rm would skip its archive hook.
assert judge(mode="card", archive_hook=None) == ("keep", ["cannot read: orca.yaml (its archive hook)"])
assert judge(mode="user", keep=None, archive_hook=True)[0] == "remove"
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
assert judge(mode="card", tickets=[{"id": "ABC-1"}], ticket_done=True)[0] == "close-terminal"
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
    print(json.dumps({"ok": os.environ.get("FAKE_OK") != "false",
                      "result": {"messages": rows, "count": len(rows) if isinstance(rows, list) else 0,
                                 "page": json.loads(os.environ.get("FAKE_PAGE", "{}")),
                                             **json.loads(os.environ.get("FAKE_TOP", "{}"))}}))
elif a[:2] == ["orchestration", "worker-show"]:
    if mode == "orca-down":
        sys.exit(1)
    print(json.dumps({"ok": True, "result": {"dispatch": {"status": os.environ.get("FAKE_STATUS", "dispatched")}}}))
elif a[:2] == ["worktree", "current"]:
    print(json.dumps({"ok": True, "result": {"worktree": json.loads(os.environ["FAKE_WT"])}}))
elif a[:2] == ["terminal", "list"]:
    rows = os.environ.get("FAKE_TERMS") or '[{"handle": "term_me", "agentIdentity": "claude"}]'
    print(json.dumps({"ok": True, "result": {"terminals": json.loads(rows)}}))
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
Q = {"id": "m1", "type": "question", "from_handle": "term_me", "to_handle": "run:r",
     "created_at": "2026-01-01 10:00:00"}
ME = {"term_me"}
REPLY = {"id": "m2", "type": "status", "thread_id": "m1", "from_handle": "term_lead", "to_handle": "term_me",
         "created_at": "2026-01-01 10:05:00"}
os.environ["FAKE_INBOX"] = json.dumps([Q])
assert end_check.waiting_questions(ME) == ["m1"]
os.environ["FAKE_INBOX"] = json.dumps([Q, REPLY])
assert end_check.waiting_questions(ME) == []
# Within one second the newest-first order decides: listed above the question, the reply came after it.
os.environ["FAKE_INBOX"] = json.dumps([dict(REPLY, created_at="2026-01-01 10:00:00"), Q])
assert end_check.waiting_questions(ME) == []
# Not an answer: addressed to someone else (a heartbeat to the run), from this card's own other handle, at the same
# second but listed below the question, or an ISO time in another zone that is really earlier.
for r in (dict(REPLY, to_handle="run:r"), dict(REPLY, type="question"), dict(REPLY, from_handle="dispatch:ctx_1", to_handle="term_me"),
          dict(REPLY, created_at="2026-01-01 10:00:00"), dict(REPLY, created_at="2026-01-01T18:30:00+09:00")):
    os.environ["FAKE_INBOX"] = json.dumps([Q, r])
    assert end_check.waiting_questions({"term_me", "dispatch:ctx_1"}, None, "term_me") == ["m1"], r
# Another terminal of the same card answering this one's question does answer it; this session's own Dispatch
# answering its own terminal, or a reply addressed to some other card terminal, does not.
CARD = {"term_me", "term_b", "dispatch:ctx_1"}
os.environ["FAKE_INBOX"] = json.dumps([dict(REPLY, from_handle="term_b"), Q])
assert end_check.waiting_questions(CARD, None, "term_me") == []
os.environ["FAKE_INBOX"] = json.dumps([dict(REPLY, to_handle="term_b"), Q])
assert end_check.waiting_questions(CARD, None, "term_me") == ["m1"]
os.environ["FAKE_INBOX"] = json.dumps([dict(REPLY, from_handle="dispatch:ctx_1", to_handle="term_me"),
                                       dict(Q, from_handle="dispatch:ctx_1")])
assert end_check.waiting_questions(CARD, None, "term_me") == ["m1"]
with contextlib.redirect_stderr(io.StringIO()):
    # Duplicate ids, a thread_id that is not a string, minute-only times, malformed paging, or ok:false: unknown.
    for rows in ([Q, dict(REPLY, id="m1")], [Q, dict(REPLY, thread_id=1)], [dict(Q, created_at="2026-01-01 10:00")]):
        os.environ["FAKE_INBOX"] = json.dumps(rows)
        assert end_check.waiting_questions(ME) is None, rows
    os.environ["FAKE_INBOX"] = "[]"
    os.environ["FAKE_PAGE"] = '["nextCursor", "c2"]'
    assert end_check.waiting_questions(ME) is None
    os.environ["FAKE_PAGE"] = "{}"
    for top in ('{"count": 1}', '{"count": {}}', '{"count": null}'):  # wrong, not a number, or missing
        os.environ["FAKE_TOP"] = top
        assert end_check.waiting_questions(ME) is None, top
    os.environ["FAKE_TOP"] = "{}"
    os.environ["FAKE_OK"] = "false"
    assert end_check.waiting_questions(ME) is None
    os.environ.pop("FAKE_OK")
    # A card creation time in the future does not prove a full page covers it.
    limit, end_check.INBOX_LIMIT = end_check.INBOX_LIMIT, "1"
    os.environ["FAKE_INBOX"] = json.dumps([dict(Q, from_handle="term_x", created_at="2026-01-01T09:00:00Z")])
    assert end_check.waiting_questions(ME, (time.time() + 3600) * 1000) is None
    end_check.INBOX_LIMIT = limit
    os.environ["FAKE_INBOX"] = json.dumps([{"id": "m3", "type": "question"}])
    assert end_check.waiting_questions(ME) is None
    # One row without a sender is enough to make the rest unknown; so is no messages list, or a full page.
    os.environ["FAKE_INBOX"] = json.dumps([dict(Q, id="m4", from_handle="term_x"), {"id": "m5", "type": "question"}])
    assert end_check.waiting_questions(ME) is None
    os.environ["FAKE_INBOX"] = json.dumps([{"id": "q1", "from_handle": "term_me"}])  # no type: maybe a question
    assert end_check.waiting_questions(ME) is None
    # A short page still needs every row whole: a null sender, or a question with no time beside its thread.
    os.environ["FAKE_INBOX"] = json.dumps([dict(Q, from_handle=None)])
    assert end_check.waiting_questions(ME) is None
    no_time = {k: v for k, v in Q.items() if k != "created_at"}
    os.environ["FAKE_INBOX"] = json.dumps([no_time, dict(REPLY, created_at="2025-12-31 10:00:00")])
    assert end_check.waiting_questions(ME) is None
    os.environ["FAKE_INBOX"] = "null"
    assert end_check.waiting_questions(ME) is None
    limit, end_check.INBOX_LIMIT = end_check.INBOX_LIMIT, "1"
    os.environ["FAKE_INBOX"] = json.dumps([dict(Q, from_handle="term_x")])
    assert end_check.waiting_questions(ME) is None
    # A full page whose oldest row is older than the card covers every question the card sent; a newer one does not.
    card_ms = 1767261600000  # 2026-01-01T10:00:00Z
    os.environ["FAKE_INBOX"] = json.dumps([dict(Q, from_handle="term_x", created_at="2026-01-01T09:00:00Z")])
    assert end_check.waiting_questions(ME, card_ms) == []
    os.environ["FAKE_INBOX"] = json.dumps([dict(Q, created_at="2026-01-01T11:00:00Z")])
    assert end_check.waiting_questions(ME, card_ms) is None
    os.environ["FAKE_INBOX"] = json.dumps([dict(Q, created_at="not a time")])
    assert end_check.waiting_questions(ME, card_ms) is None
    # At the boundary (the card's own second) the next page may hold the same instant: not covered.
    for at in ("2026-01-01T10:00:00Z", "2026-01-01T09:59:59.500Z"):
        os.environ["FAKE_INBOX"] = json.dumps([dict(Q, from_handle="term_x", created_at=at)])
        assert end_check.waiting_questions(ME, card_ms) is None, at
    # Times of the wrong kind are unread: a number for created_at, seconds or infinity for the card's creation.
    os.environ["FAKE_INBOX"] = json.dumps([dict(Q, from_handle="term_x", created_at=1767250000)])
    assert end_check.waiting_questions(ME, card_ms) is None
    os.environ["FAKE_INBOX"] = json.dumps([dict(Q, from_handle="term_x", created_at="2026-01-01T09:00:00Z")])
    assert end_check.waiting_questions(ME, 1767261600) is None
    assert end_check.waiting_questions(ME, float("inf")) is None
    end_check.INBOX_LIMIT = limit
    os.environ["FAKE_PAGE"] = '{"hasMore": true, "nextCursor": "c2"}'
    assert end_check.waiting_questions(ME) is None
    os.environ["FAKE_PAGE"] = "{}"
    os.environ["FAKE_TOP"] = '{"nextCursor": "c2"}'
    assert end_check.waiting_questions(ME) is None
    os.environ["FAKE_TOP"] = "{}"
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
# A terminal row without agentIdentity: other agents on the card are unknown.
os.environ["FAKE_TERMS"] = '[{"handle": "term_me", "agentIdentity": "claude"}, {"handle": "term_other"}]'
with contextlib.redirect_stderr(io.StringIO()):
    assert end_check.card_facts("term_me")["other_agents"] is None
os.environ["FAKE_TERMS"] = '[{"handle": "term_me", "agentIdentity": "claude"}, {"handle": "x", "agentIdentity": {}}]'
with contextlib.redirect_stderr(io.StringIO()):
    assert end_check.card_facts("term_me")["other_agents"] is None, "an agentIdentity that is no name nor null"
os.environ["FAKE_TERMS"] = '[{"handle": "term_me", "agentIdentity": "claude"}, {"handle": "x", "agentIdentity": ""}]'
with contextlib.redirect_stderr(io.StringIO()):
    assert end_check.card_facts("term_me")["other_agents"] is None, "an empty name is neither an agent nor a shell"
os.environ["FAKE_TERMS"] = '[{"handle": "term_me", "agentIdentity": "claude"}, {"handle": "s", "agentIdentity": null}]'
assert end_check.card_facts("term_me")["other_agents"] == []
# A question another terminal on the same card left waiting holds the card too.
os.environ["FAKE_INBOX"] = json.dumps([dict(Q, from_handle="s")])
assert end_check.card_facts("term_me")["questions"] == ["m1"]
# A terminal row without a handle: whose questions they are is unknown.
os.environ["FAKE_TERMS"] = '[{"handle": "term_me", "agentIdentity": "claude"}, {"agentIdentity": null}]'
assert end_check.card_facts("term_me")["questions"] is None
os.environ["FAKE_INBOX"] = "[]"
os.environ.pop("FAKE_TERMS")

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
# A done card's untracked cache goes first (the cleaners read it as dirty); a card that stays for another agent keeps its own.
sys.path.insert(0, str(HERE.parent / "skills" / "reap-resources" / "scripts"))
import reap  # noqa: E402
saved_trash, trashed = reap.trash, []
reap.trash = lambda *p: trashed.extend(p)
with contextlib.redirect_stdout(io.StringIO()):
    assert end_check.apply("close-terminal", dict(CLEAN_FACTS, caches=["x/__pycache__/"]), "card", {}, "term_me") == 0
    assert trashed == ["/wt/card/x/__pycache__"], trashed
    trashed.clear()
    assert end_check.apply("close-terminal", dict(CLEAN_FACTS, caches=["x/__pycache__/"], other_agents=["term_x"]), "card", {}, "term_me") == 0
    assert trashed == [], trashed
reap.trash = saved_trash
# A card has no Dispatch to wait for; keep applies nothing for it.
rc, err, calls = apply("remove", mode="card")
assert rc == 0 and calls == [["worktree", "rm", "--worktree", "current", "--json"]], calls
rc, err, calls = apply("keep", mode="card")
assert rc == 0 and calls == []

# An orca.yaml that exists but cannot be read leaves the archive hook unknown, and nothing is removed.
yml = tmp / "card-yml"
(yml / "orca.yaml").mkdir(parents=True)  # read_text on a directory: an OSError other than not-found
os.environ.update(FAKE_STATUS="completed", FAKE_MODE="")
CALLS.write_text("")
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    rc = end_check.apply("remove", dict(CLEAN_FACTS, path=str(yml)), "card", {}, "term_me")
assert rc == 3 and CALLS.read_text() == "", rc
assert end_check.archive_hook(str(tmp / "nowhere")) is False
dangling = tmp / "card-dangling"
dangling.mkdir()
(dangling / "orca.yaml").symlink_to(tmp / "no-such-target.yaml")
assert end_check.archive_hook(str(dangling)) is None, "a dangling orca.yaml link is there but unread"
# The YAML is not parsed: any readable orca.yaml, however its key is spelled or broken, runs the hooks.
for text in ("scripts: {archive: make down}\n", 'scripts:\n  "\\x61rchive": x\n', "scripts: [unterminated\n", ""):
    (dangling / "orca.yaml").unlink()
    (dangling / "orca.yaml").write_text(text)
    assert end_check.archive_hook(str(dangling)) is True, text
# With an archive hook, --run-hooks goes after the --worktree value.
hooked = tmp / "card-hooked"
hooked.mkdir()
(hooked / "orca.yaml").write_text("scripts:\n  archive: make down\n")
CALLS.write_text("")
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    rc = end_check.apply("remove", dict(CLEAN_FACTS, path=str(hooked)), "card", {}, "term_me")
calls = [json.loads(l) for l in CALLS.read_text().splitlines()]
assert rc == 0 and calls == [["worktree", "rm", "--worktree", "current", "--run-hooks", "--json"]], calls

# End to end on a throwaway repo: a dirty card keeps, and the verdict is the printed JSON. The git facts come from
# the card's path Orca names, not from the directory it ran in.
repo = tmp / "repo"
subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
(repo / "a.txt").write_text("x\n")
os.environ["FAKE_WT"] = json.dumps({"isMainWorktree": False, "path": str(repo), "linkedItems": []})
out = io.StringIO()
with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
    rc = end_check.main(["--worker", "--outcome", "succeeded", "--no-keep", "--dispatch", "ctx_1"])
res = json.loads(out.getvalue())
assert rc == 0 and res["verdict"] == "keep" and any("a.txt" in r for r in res["reasons"]), res
assert res["facts"]["archive_hook"] is False, res["facts"]
# No card path from Orca: every git fact is unread, so it keeps.
os.environ["FAKE_WT"] = json.dumps({"isMainWorktree": False, "linkedItems": []})
out = io.StringIO()
with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
    end_check.main(["--card", "--no-keep"])
res = json.loads(out.getvalue())
assert res["verdict"] == "keep" and "cannot read: tracked" in res["reasons"], res
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
os.environ["FAKE_MERGED"] = json.dumps([{"number": 1, "url": "u", "headRefOid": merged_at, "baseRefName": "release"}])
assert end_check.git_facts(str(clone))["on_main"] is False, "merged into another branch is not on main"
os.environ["FAKE_MERGED"] = json.dumps([{"number": 1, "url": "u", "headRefOid": merged_at, "baseRefName": "main"}])
assert end_check.git_facts(str(clone))["on_main"] is True
(clone / "c.txt").write_text("c\n")
git("add", "c.txt")
git("commit", "-q", "-m", "after the merge")
assert end_check.git_facts(str(clone))["on_main"] is False
# A gh answer that is not a list of PR rows is unread, not "no PRs".
for bad in ("{}", '""'):
    os.environ["FAKE_MERGED"] = bad
    f = end_check.git_facts(str(clone))
    assert f["on_main"] is None, bad
git("remote", "set-url", "origin", str(tmp / "nowhere.git"))
assert end_check.git_facts(str(clone))["on_main"] is None
# A status row that is not porcelain v1 leaves the untracked files unread, not "none".
real_run = end_check.run
for row in ("unread status row", "?! unread-path", "   blank-status"):
    end_check.run = lambda argv, cwd=None, row=row, **kw: \
        (0, row + "\n", "") if "--ignored" in argv else real_run(argv, cwd, **kw)
    try:
        assert end_check.git_facts(str(clone))["untracked"] is None, row
    finally:
        end_check.run = real_run

print("ok")
