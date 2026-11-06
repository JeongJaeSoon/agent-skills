"""Offline tests for the priority groups (`orch board set --priority`) and their summary in `orch brief render`, on
invented data.

Run: python3 test_board_priority.py
"""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-board-priority-"))
env = {**os.environ, "ORCH_FLEET_STATE": str(tmp / "dashboard"), "ORCH_BRIEF_LAST": str(tmp / "brief-last"),
       "ORCH_FLEET_CONFIG": str(tmp / "none.json"), "AGENT_SKILLS_STATE": str(tmp / "state"), "TZ": "UTC",
       "AGENT_SKILLS_CONFIG": str(tmp / "none.json"), "PROGRAMS_HOME": str(tmp / "programs")}
os.environ.update(env)
sys.path.insert(0, str(HERE))
import store as db_store  # noqa: E402

db_store.import_apply(checks=False)
NOW = "2026-10-13T14:30:00Z"


def orch(*args):
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), *args], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout.rstrip("\n"), r.stderr.strip()


def ok(*args):
    code, out, err = orch(*args)
    assert code == 0, (args, out, err)
    return out


def render(*extra):
    return ok("brief", "render", "--periodic", "--now", NOW, "--no-refresh", *extra)


# Three leads: alpha (a phase, a done sub-task and an open one), beta (one row), gamma (no group).
ok("board", "lead", "add", "alpha", "--handle", "term_0FAKE1", "--repo", "acme/web")
ok("board", "lead", "add", "beta", "--handle", "term_0FAKE2", "--repo", "acme/api")
ok("board", "lead", "add", "gamma", "--handle", "term_0FAKE3", "--repo", "acme/ops")
(tmp / "dashboard").mkdir(parents=True, exist_ok=True)
(tmp / "dashboard" / "state.json").write_text(json.dumps({"generated_at": NOW, "sessions": [
    {"id": s, "phase": "working", "terminals": [{"handle": h, "agent": "claude", "connected": True, "writable": True}]}
    for s, h in (("s1", "term_0FAKE1"), ("s2", "term_0FAKE2"), ("s3", "term_0FAKE3"))]}))
ok("board", "set", "alpha/t1", "--title", "첫 작업", "--status", "done")
ok("board", "set", "alpha/t2", "--title", "둘째 작업", "--next", "CI 확인")
ok("board", "set", "alpha/t3", "--title", "셋째 작업", "--status", "held", "--next", "t2 머지 뒤")
ok("board", "set", "beta", "--title", "베타 작업", "--status", "owner", "--next", "승인")

# No group recorded: no section, and the report is the one this code printed before the field existed.
plain = {"text": render(), "tg": render("--telegram"), "full": json.loads(render("--json"))["full"]}
assert "우선순위" not in plain["text"] + plain["tg"] + plain["full"], plain

# Refusals say the form.
for bad in ("high", "x:name", "1:", ":name", "1000:name", "1:a — b"):
    code, _, err = orch("board", "set", "alpha", "--priority", bad)
    assert code == 1 and err.startswith("--priority is RANK:NAME"), (bad, err)

# A group on the lead covers its sub-tasks; a row's own group wins over its parent's.
ok("board", "set", "alpha", "--priority", "2:Group B")
ok("board", "set", "beta", "--priority", "1:Group A")
text = render()
lines = text.splitlines()
at = lines.index("## 🧭 우선순위")
summary = lines[at:at + 5]
assert summary[1] == "" and summary[2].startswith("- **Group A** — 진행 0 · 대기 1 · 완료 0 · 다음: ") and "승인" in summary[2], summary
assert summary[3].startswith("- **Group B** — 진행 1 · 대기 1 · 완료 1 · 다음: ") and "CI 확인" in summary[3], summary
assert summary[4] == "" or summary[4].startswith("## "), summary  # gamma is in no group, so no third line
# It is above the owner's list, under the headline and the guide lines; the headline keeps its place and form.
assert lines[0].startswith("**") and at < lines.index("## 🙋 지금 오너가 할 일"), text
assert all(l.startswith(("## ", "**", ">", "- ")) or not l for l in lines if "우선순위" in l or "Group" in l), text
ok("board", "set", "alpha/t3", "--priority", "1:Group A")
assert "- **Group A** — 진행 0 · 대기 2 · 완료 0" in render() and "- **Group B** — 진행 1 · 대기 0 · 완료 1" in render()

# Telegram: plain text, the same order. The dashboard's full copy carries the section too.
tg = render("--telegram")
assert "\n🧭 우선순위\n\nGroup A — 진행 0 · 대기 2 · 완료 0 · 다음: " in tg and "\nGroup B — 진행 1 · 대기 0 · 완료 1" in tg, tg
assert "**" not in tg.split("🧭 우선순위")[1].split("🙋")[0], tg
assert "## 🧭 우선순위" in json.loads(render("--json"))["full"]

# Everything else in the report is what it was without groups.
def without(t, tg=False):
    out, skip = [], False
    for l in t.splitlines():
        if l == ("🧭 우선순위" if tg else "## 🧭 우선순위"):
            skip = True
            continue
        if skip and (l.startswith("## ") or l.startswith("🙋") or l.startswith("📋")):
            skip = False
        if not skip:
            out.append(l)
    return "\n".join(out)
assert without(render()) == plain["text"] and without(render("--telegram"), True) == plain["tg"]

# Rank orders the groups, not the name; a group with only done rows says so; `-` clears.
ok("board", "set", "alpha", "--priority", "0:Zed")
ok("board", "set", "alpha/t3", "--priority", "-")
ok("board", "set", "beta", "--status", "done")
text = render()
assert text.index("**Zed**") < text.index("**Group A**") and "**Group A** — 진행 0 · 대기 0 · 완료 1 · 다음: 없음" in text, text
for rid in ("alpha", "beta"):
    ok("board", "set", rid, "--priority", "-")
assert "우선순위" not in render()

# Counts follow the rows, not the table lines: rows that fold on one reviewer keep their own groups, tasks that wait on
# one reviewer with no lead (bullets) are counted, and a head over rows in another repository is not counted twice.
def open_pr(n, reviewer):
    return f"acme/web#{n}", {"probed_at": NOW, "detail": {
        "key": f"acme/web#{n}", "url": f"https://example.test/{n}", "state": "OPEN", "draft": False, "ci": "success",
        "decision": "REVIEW_REQUIRED", "base": "main", "merge_state": "BLOCKED", "merged_at": None, "merge_sha": None,
        "reviewers": [{"login": reviewer, "requested": True, "state": None, "team": False}]}}


(tmp / "dashboard" / "prs.json").write_text(json.dumps({"me": "octo-owner", "prs": dict([
    open_pr(1, "rev-a"), open_pr(2, "rev-a"), open_pr(3, "rev-b"), open_pr(4, "rev-b")])}))
ok("board", "lead", "add", "fold", "--handle", "term_0FAKE4", "--repo", "acme/web")
ok("board", "set", "fold", "--priority", "1:Parent")
for n in (1, 2):
    ok("board", "set", f"fold/c{n}", "--title", f"자식 {n}", "--pr", f"acme/web#{n}", "--priority", "2:Child")
for n in (3, 4):
    ok("board", "set", f"solo{n}", "--kind", "task", "--repo", "acme/web", "--title", f"단독 {n}", "--pr", f"acme/web#{n}",
       "--priority", "3:Solo")
ok("board", "lead", "add", "cross", "--handle", "term_0FAKE5", "--repo", "acme/web")
ok("board", "set", "cross", "--priority", "4:Cross")
ok("board", "set", "cross/k", "--title", "다른 저장소 작업", "--repo", "acme/other", "--next", "CI 확인")


def totals(text):
    got = {}
    for l in text.splitlines():
        if l.startswith("- **") and " — 진행 " in l:
            name, rest = l[4:].split("** — 진행 ", 1)
            got[name] = sum(int(x.split()[-1]) for x in rest.split(" · 다음:")[0].split(" · "))
    return got


# The next step is the first open row of the group in the order the tables print, not the order rows were added.
code, _, err = orch("board", "set", "cross", "--priority", "5:a\t—\tb")
assert code == 1 and err.startswith("--priority is RANK:NAME"), err
for repo, lead in (("acme/zeta", "oz"), ("acme/alpha", "oa")):
    ok("board", "lead", "add", lead, "--handle", "term_0FAKE1", "--repo", repo)
    ok("board", "set", lead, "--title", f"{lead}작업", "--priority", "5:Order", "--next", f"{lead} 다음")
text = render()
first = "oz" if text.index("## acme/zeta") < text.index("## acme/alpha") else "oa"
assert f"**Order** — 진행 2 · 대기 0 · 완료 0 · 다음: {first}작업 — {first} 다음" in text, text
assert totals(text) == {"Child": 2, "Solo": 2, "Cross": 1, "Order": 2}, (totals(text), text)
assert "Parent" not in totals(text), "a head with rows under it is not a row"
assert "다음: k 다른 저장소 작업 — 재개·정리" in text, "the step is judge()'s, not the stored --next: " + text

# Rules: rows the fleet derives (a lead, its cards, a standalone card) have no board row to carry --priority, so a rule
# gives them a group by worktree, program or repository. Own --priority first, then the nearest worktree rule, then
# program, then repository. A rule is no row: no report, open list or tidy pass sees it.
fenv = {**env, "ORCH_FLEET_STATE": str(tmp / "fleet" / "dashboard"), "ORCH_BRIEF_LAST": str(tmp / "fleet" / "brief-last"),
        "AGENT_SKILLS_STATE": str(tmp / "fleet" / "state")}
(tmp / "fleet" / "dashboard").mkdir(parents=True)


def forch(*args, expect=0):
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), *args], capture_output=True, text=True, env=fenv)
    assert r.returncode == expect, (args, r.stdout, r.stderr)
    return r.stdout.rstrip("\n") if expect == 0 else r.stderr.strip()


def session(sid, kind, parent, **more):
    return {"id": sid, "kind": kind, "parent": parent, "name": sid, "phase": "working", "terminals": [], **more}


(tmp / "fleet" / "dashboard" / "state.json").write_text(json.dumps({"generated_at": NOW, "sessions": [
    session("root", "orchestrator", None),
    session("lead-a", "orchestration", "root", gh_repo="acme/web"),
    session("card-1", "task", "lead-a"), session("card-2", "task", "lead-a"),
    session("card-3", "task", "root", gh_repo="acme/web"), session("card-4", "task", "root", gh_repo="acme/ops"),
    session("card-5", "task", "root", gh_repo="acme/web")]}))
forch("db", "import", "--apply", "--rehearsal")
forch("board", "priority", "set", "program:lead-a", "2:Prog")
forch("board", "priority", "set", "worktree:card-2", "1:Pin")
forch("board", "priority", "set", "repo:acme/web", "3:Repo")
forch("board", "priority", "set", "repo:acme/ops", "3:Repo")
forch("board", "priority", "set", "worktree:card-5", "4:Moved")
forch("board", "priority", "set", "worktree:card-5", "1:Pin")  # the same match again replaces its group
assert forch("board", "priority", "list").splitlines() == [
    "worktree:card-2  1:Pin", "worktree:card-5  1:Pin", "program:lead-a  2:Prog", "repo:acme/ops  3:Repo", "repo:acme/web  3:Repo"]


def fleet_totals():
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), "brief", "render", "--periodic", "--now", NOW, "--no-refresh"],
                       capture_output=True, text=True, env=fenv)
    assert r.returncode == 0, r.stderr
    return totals(r.stdout), r.stdout


got, text = fleet_totals()
# card-1 (program), card-2 and card-5 (worktree), card-3 and card-4 (repository); the lead is their roll-up.
assert got == {"Pin": 2, "Prog": 1, "Repo": 2}, (got, text)
forch("board", "set", "own", "--kind", "task", "--repo", "acme/web", "--title", "직접", "--priority", "9:Own")
got, text = fleet_totals()
assert got == {"Pin": 2, "Prog": 1, "Repo": 2, "Own": 1}, "a row's own group beats the repository's rule: " + text
forch("board", "priority", "drop", "worktree:card-5")
assert fleet_totals()[0] == {"Pin": 1, "Prog": 1, "Repo": 3, "Own": 1}
forch("board", "priority", "drop", "worktree:card-5", expect=1)
for bad in ("lead-a", "team:lead-a", "repo:noslash", "worktree:", "repo:acme/web extra"):
    assert "the match is worktree:ID, program:NAME or repo:OWNER/NAME" in forch("board", "priority", "set", bad, "1:X", expect=1), bad
assert forch("board", "priority", "set", "repo:acme/web", "x", expect=1).startswith("--priority is RANK:NAME")
# The rules are no rows.
shown = json.loads(forch("board", "show"))
assert not [k for k in shown["rows"] if k.startswith("prio:")] and len(shown["groups"]) == 4, shown
assert "prio:" not in forch("board", "open", "--json") and "prio:" not in forch("board", "tidy", "--dry-run", "--json")
assert "prio:" not in forch("brief", "render", "--periodic", "--now", NOW, "--no-refresh", "--json")

# A subagent counts in its host card's group, not in the group of the lead row it hangs under.
before = fleet_totals()[0]
state_file = tmp / "fleet" / "dashboard" / "state.json"
world = json.loads(state_file.read_text())
for s in world["sessions"]:
    if s["id"] == "card-2":
        s["subagents"] = [{"agent": "ag1", "desc": "검토", "type": "general-purpose", "started_at": "2026-10-13T14:29:00Z"}]
state_file.write_text(json.dumps(world))
after = fleet_totals()[0]
assert after["Pin"] == before["Pin"] + 1 and after["Prog"] == before["Prog"], (before, after)

# A board row that names the card's worktree is that card's row: the subagent follows it, over the worktree rule.
# (A new row for a card is refused; this one is written the way a row from before A-ANNOT was.)
assert "would be card card-2's" in forch("board", "set", "man", "--kind", "task", "--repo", "acme/web", "--worktree", "card-2", expect=1)
import board as board_old  # noqa: E402

_saved = {k: os.environ.get(k) for k in ("ORCH_FLEET_STATE", "AGENT_SKILLS_STATE")}
os.environ.update(ORCH_FLEET_STATE=fenv["ORCH_FLEET_STATE"], AGENT_SKILLS_STATE=fenv["AGENT_SKILLS_STATE"])
try:
    board_old.change(lambda s: board_old.put(s, "man", dict(kind="task", repo="acme/web", worktree="card-2", priority="9:Man")))
finally:
    for k, v in _saved.items():
        os.environ[k] = v or ""
assert fleet_totals()[0].get("Man") == 2, fleet_totals()

# Its host card is gone from the report (a completed dispatch with no PR), but the host's worktree rule still holds.
world = json.loads(state_file.read_text())
world["sessions"].append(session("card-6", "task", "lead-a", dispatch_status="completed",
                                 subagents=[{"agent": "ag2", "desc": "끝난 카드의 검토", "type": "general-purpose",
                                             "started_at": "2026-10-13T14:29:00Z"}]))
state_file.write_text(json.dumps(world))
forch("board", "priority", "set", "worktree:card-6", "8:Gone")
assert fleet_totals()[0].get("Gone") == 1, fleet_totals()

# A lead the board records with --worktree takes its worktree's rule, and its sub-rows inherit it.
forch("board", "lead", "add", "bl", "--handle", "term_0FAKE9", "--repo", "acme/web", "--worktree", "wt-b")
forch("board", "set", "bl/t1", "--title", "하위", "--next", "일")
forch("board", "priority", "set", "worktree:wt-b", "6:Lead")
assert fleet_totals()[0].get("Lead") == 1, fleet_totals()

# Rules survive the legacy export and the import that follows it, and re-setting one writes no event.
import store as store_db  # noqa: E402
import board  # noqa: E402

fleet_env = {k: os.environ.get(k) for k in ("ORCH_FLEET_STATE", "AGENT_SKILLS_STATE")}
os.environ.update(ORCH_FLEET_STATE=fenv["ORCH_FLEET_STATE"], AGENT_SKILLS_STATE=fenv["AGENT_SKILLS_STATE"])
try:
    rules = store_db.board()["groups"]
    seq = lambda: store_db.dump("event")["event"][-1]["seq"]  # noqa: E731
    last = seq()
    board.priority_set("repo:acme/web", "3:Repo")
    assert seq() == last, "the same rule again is no change"
    exported = store_db._export_board(tmp / "export-rules")[0]
    saved = json.loads((exported / "board.json").read_text())
    assert saved["groups"] == rules and not [k for k in saved["rows"] if k.startswith("prio:")], saved
    # An import refuses a rule the renderer could not read.
    rule = next(iter(saved["groups"].values()))
    for what, bad in (("team match", {**rule, "id": "prio:team:x", "match": "team:x"}),
                      ("priority form", {**rule, "priority": "bad"}), ("id", {**rule, "id": "prio:other"}),
                      ("repo form", {**rule, "id": "prio:repo:x", "match": "repo:x"}),
                      ("folded priority", {**rule, "priority": "1:a\t—\tb"}), ("leading zero", {**rule, "priority": "01:Group"}),
                      ("folded match", {**rule, "id": "prio:program:a\tb", "match": "program:a\tb"})):
        (exported / "bad.json").write_text(json.dumps({**saved, "groups": {bad["id"]: bad}}))
        assert any("priority rule" in p for p in store_db._legacy_board(exported / "bad.json")[1]), what
    for rid, kind in (("x", "group"), ("prio:program:x", "lead")):
        (exported / "bad.json").write_text(json.dumps({**saved, "rows": {rid: {"id": rid, "kind": kind}}}))
        assert any("priority rules" in p for p in store_db._legacy_board(exported / "bad.json")[1]), rid
    assert "cannot start with prio:" in forch("board", "set", "prio:program:x", "--repo", "acme/web", expect=1)
    store_db.load_board(saved)
    assert store_db.board()["groups"] == rules and "prio:repo:acme/web" not in store_db.board()["rows"]
finally:
    for k, v in fleet_env.items():
        os.environ[k] = v or ""

print("ok")
