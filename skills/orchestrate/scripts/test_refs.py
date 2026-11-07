"""Offline tests for step 8-refs: the collector keeping PR references, reviewers and worktrees in the DB, the review asks
closing themselves from it, the brief rows that follow, retention, the reap view and the generated columns. The acceptance
test at the top drives the real collector tick against a fixture GitHub and renders the brief after each change, with no
edit between a world change and its render. All data invented.

Run: python3 test_refs.py
"""
import json, os, pathlib, re, sqlite3, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-refs-"))
env = {**os.environ, "ORCH_FLEET_STATE": str(tmp / "fleet"), "ORCH_BRIEF_LAST": str(tmp / "brief-last"),
       "ORCH_FLEET_CONFIG": str(tmp / "none.json"), "AGENT_SKILLS_STATE": str(tmp / "shared"), "TZ": "UTC",
       "AGENT_SKILLS_CONFIG": str(tmp / "none.json"), "PROGRAMS_HOME": str(tmp / "programs")}
os.environ.update(env)
sys.path.insert(0, str(HERE))
import acks, board, fleet, store  # noqa: E402


def _row(rid, **fields):  # a row that names its PR but no card, as test_brief_rows writes them
    return board.change(lambda s: board.put(s, rid, fields))


board.set_row = _row
(tmp / "fleet").mkdir()
(tmp / "shared" / "prs").mkdir(parents=True)
store.import_apply(checks=False)
store.state_setup()
store.sendlog_setup()
assert store.refs_setup().startswith("created") and store.refs_ready() and store.refs_setup() == "already created"

# ---- a fixture GitHub: what the collector's GraphQL calls get back, changed only by the test's world edits
ME = "owner-me"
WORLD = {}  # "owner/repo#N" -> {"state", "reviews": [(login, state, at)], "mine": bool}


def pr_node(key):
    w = WORLD[key]
    repo, n = key.split("#")
    reviews = [{"state": s, "submittedAt": at, "author": {"__typename": "User", "login": who}, "commit": {"oid": "h1"},
                "id": f"r-{who}"} for who, s, at in w["reviews"]]
    return {"number": int(n), "state": w["state"], "isDraft": False, "updatedAt": w.get("updated", "2026-11-07T00:00:00Z"),
            "createdAt": "2026-11-06T00:00:00Z", "mergedAt": w.get("merged_at"), "headRefOid": "h1", "url": f"https://example.test/{n}",
            "title": f"pr {n}", "mergeStateStatus": "CLEAN", "reviewDecision": w.get("decision"), "baseRefName": "main",
            "headRefName": f"b{n}", "mergeCommit": None, "author": {"login": ME}, "assignees": {"nodes": [{"login": ME}]},
            "reviewRequests": {"nodes": []}, "latestOpinionatedReviews": {"nodes": reviews}, "reviews": {"nodes": reviews},
            "reviewThreads": {"nodes": []}, "commits": {"nodes": []}}


def graphql(query, *a, **k):
    if "search(" in query:
        want = "MERGED" if "is:merged" in query else "OPEN"
        nodes = [{"repository": {"nameWithOwner": k_.split("#")[0]}, "number": int(k_.split("#")[1]), "updatedAt": "2026-11-07T00:00:00Z"}
                 for k_, w in WORLD.items() if w["mine"] and w["state"] == want]
        return {"s": {"nodes": nodes, "pageInfo": {"hasNextPage": False}}}, []
    data = {}
    for m in re.finditer(r'p(\d+): repository\(owner:"([^"]+)",name:"([^"]+)"\)\{pullRequest\(number:(\d+)\)', query):
        key = f"{m.group(2)}/{m.group(3)}#{m.group(4)}"
        data[f"p{m.group(1)}"] = {"pullRequest": pr_node(key) if key in WORLD else None}
    return data, []


collector = fleet.Fleet(fetch_fast=lambda: {"worktrees": [], "terminals": [], "messages": []},
                        fetch_runs=lambda live=None: {"runs": [], "workers": [], "tasks": [], "gates": []},
                        graphql=graphql, ps_table=lambda: {}, fetch_automations=lambda: [], fetch_monitors=lambda **k: [],
                        read_screen=lambda h: [])
collector.me = ME


def tick():
    """One collector tick, as if the PR tiers' wait had passed: a PR no session owns is probed on its tier's clock."""
    for entry in collector.prs.values():
        entry.pop("probed_at", None)
    collector.tick(force=True)


def brief():
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), "brief", "render", "--scope", "top-level", "--json", "--no-refresh"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, (r.stdout, r.stderr)
    return json.loads(r.stdout)


def counts():
    """What `orch brief render --scope top-level --json` shows: open rows, and the lines under ⏳ waiting on others."""
    b, theirs, on = brief(), [], False
    for line in b["text"].splitlines():
        if line.startswith("## "):
            on = line.startswith("## ⏳")
        elif on and line.startswith("- "):
            theirs.append(line)
    return {"rows": len(b["open"]), "theirs": len(theirs)}, theirs


def one(sql, *args):
    c = sqlite3.connect(store.path())
    try:
        return c.execute(sql, args).fetchone()
    finally:
        c.close()


# ---- acceptance: a fixture PR flips open -> merged, and asks are queued, flushed and reviewed, with nothing edited between
WORLD["acme/widgets#7"] = {"state": "OPEN", "mine": True, "reviews": []}
WORLD["acme/widgets#9"] = {"state": "OPEN", "mine": False, "reviews": []}      # nobody's session or search names it
WORLD["acme/widgets#11"] = {"state": "OPEN", "mine": False, "reviews": []}
board.set_row("w7", title="w7", repo="acme/widgets", status="waiting", pr="acme/widgets#7", next="reviewer's answer")
tick()
base, _ = counts()
assert one("SELECT state, assignee, head_sha FROM pr_ref WHERE repo='acme/widgets' AND number=7") == ("open", ME, "h1"), \
    "the collector wrote the PR it found by search"

aid, _ = store.ask_add("acme/widgets#9", "rev-a")
assert store.open_ask_prs() == ["acme/widgets#9"]
tick()
assert one("SELECT state FROM pr_ref WHERE repo='acme/widgets' AND number=9") == ("open",), \
    "a PR only an open ask names is read, so the ask can close when it does"
c1, theirs = counts()
assert c1["theirs"] == base["theirs"] + 1 and any("acme/widgets#9" in t and "대기열" in t for t in theirs), (base, c1, theirs)

store.send_log_add({"pr": "acme/widgets#9", "reviewer": "rev-a", "head": "h1", "action": "request", "source": "sent",
                    "at": 1000.0, "post_at": None, "thread": None, "scheduled": None, "channel": None})
c2, theirs = counts()
assert c2["theirs"] == c1["theirs"] and any("acme/widgets#9" in t and "의뢰함" in t for t in theirs), \
    "flushed: the same ask, now sent, with no edit to it"

WORLD["acme/widgets#9"].update(reviews=[("rev-a", "APPROVED", "2026-11-07T01:00:00Z")], updated="2026-11-07T01:00:00Z")  # a review bumps updatedAt
tick()
c3, _ = counts()
assert c3["theirs"] == c2["theirs"] - 1 and one("SELECT closed_why FROM review_ask WHERE id=?", aid) == ("reviewed",), (c2, c3)
assert one("SELECT state, stale FROM pr_reviewer WHERE pr_repo='acme/widgets' AND pr_number=9 AND login='rev-a'") == ("APPROVED", 0)

store.ask_add("acme/widgets#11", "rev-b")
tick()
c4, _ = counts()
assert c4["theirs"] == c3["theirs"] + 1
WORLD["acme/widgets#11"].update(state="MERGED", merged_at="2026-11-07T02:00:00Z")
tick()
c5, _ = counts()
assert c5["theirs"] == c4["theirs"] - 1 and one("SELECT closed_why FROM review_ask WHERE reviewer='rev-b'") == ("merged",), (c4, c5)

WORLD["acme/widgets#7"].update(state="MERGED", merged_at="2026-11-07T03:00:00Z", updated="2026-11-07T03:00:00Z")
tick()
c6, _ = counts()
assert one("SELECT state, merged_at FROM pr_ref WHERE repo='acme/widgets' AND number=7") == ("merged", "2026-11-07T03:00:00Z")
assert c6["rows"] != c5["rows"] or c6["theirs"] != c5["theirs"], ("the merged PR changed the brief by itself", c5, c6)

# ---- "Ack verified": a finished row whose PR is merged is offered, nothing acks it by itself, an unmerged one is not offered
board.set_row("w9", title="w9", repo="acme/widgets", status="done", pr="acme/widgets#9", next="")  # #9 is still open
board.set_row("w7", title="w7", repo="acme/widgets", status="done", pr="acme/widgets#7", next="")
ids, _ = acks.verified()
assert ids == ["board:w7"], ids
assert acks.acked() == {}, "offering acks nothing"
assert acks.write(ids, can=_)[0] == ["board:w7"] and acks.verified()[0] == [], "acked on request, then not offered again"
assert acks.write(["board:w7"], undo=True)[0] == ["board:w7"]

# ---- readers that compare prs.json's words keep working, and the table maps both ways
saved = json.loads((tmp / "fleet" / "prs.json").read_text())["prs"]
assert saved["acme/widgets#7"]["detail"]["state"] == "MERGED" and "acme/widgets#9" not in saved, \
    "prs.json keeps live detail only: #9's ask closed, nothing names it now; its state stays in pr_ref"
assert store.pr_words() == {"acme/widgets#7": "MERGED", "acme/widgets#9": "OPEN", "acme/widgets#11": "MERGED"}, store.pr_words()
assert [store.pr_word(s) for s in ("open", "merged", "closed", "unknown")] == ["OPEN", "MERGED", "CLOSED", None]
assert all(store.PR_STATES[store.pr_word(s)] == s for s in ("open", "merged", "closed"))

# ---- a withdrawn ask: the PR closed without merging
WORLD["acme/widgets#13"] = {"state": "OPEN", "mine": False, "reviews": []}
store.ask_add("acme/widgets#13", "rev-c")
tick()
WORLD["acme/widgets#13"]["state"] = "CLOSED"
tick()
assert one("SELECT closed_why FROM review_ask WHERE reviewer='rev-c'") == ("withdrawn",)
assert one("SELECT closed_at IS NOT NULL FROM pr_ref WHERE repo='acme/widgets' AND number=13") == (1,)

# ---- worktrees: seen, tombstoned when they leave, and the reap view reads them
store.card_refresh([{"id": "wt-a", "repo": "acme/widgets", "branch": "b7", "pr": "acme/widgets#7"},
                    {"id": "wt-b", "repo": "acme/widgets", "branch": "b9", "pr": "acme/widgets#9"}])
store.card_refresh([{"id": "wt-b", "repo": "acme/widgets", "branch": "b9", "pr": None}])
assert one("SELECT gone_at IS NOT NULL FROM card_ref WHERE orca_worktree_id='wt-a'") == (1,), "left the list: tombstoned, not deleted"
assert one("SELECT pr_number FROM card_ref WHERE orca_worktree_id='wt-b'") == (9,), "a tick that lost the PR keeps the last one"
reap = {r["id"]: r["reason"] for r in store.reap_view()}
assert reap == {"wt-a": "worktree_gone"}, reap
store.card_refresh([{"id": "wt-a", "repo": "acme/widgets", "branch": "b7", "pr": "acme/widgets#7"}])
assert {r["id"]: r["reason"] for r in store.reap_view()} == {"wt-a": "pr_merged", "wt-b": "worktree_gone"}, \
    "wt-a is back, so its merged PR is the reason; wt-b was not in this list, so it is gone"
assert not (tmp / "fleet" / "patrol-verdicts.json").exists(), "the reap view derives; it writes no verdict file"

# ---- retention: a gone card and a PR nothing points at go; a PR a card, an ask or a send-log row points at stays
store.pr_refresh([{"repo": "acme/widgets", "number": 15, "state": "CLOSED"}])  # nothing points at it
store.card_refresh([])  # every worktree gone
old = 4_000_000_000.0  # far past every window
dry = store.retention(old, dry_run=True)
assert dry == {"card_ref": 2, "pr_ref": 2} and one("SELECT count(*) FROM card_ref") == (2,), dry
assert store.retention(old) == dry and one("SELECT count(*) FROM card_ref") == (0,), "a dry run counts what a real one removes"
assert one("SELECT count(*) FROM pr_ref WHERE number IN (7, 15)") == (0,), "#15 had nothing pointing at it, #7 only a card that went"
for n in (9, 11, 13):
    assert one("SELECT count(*) FROM pr_ref WHERE number=?", n) == (1,), f"an ask points at #{n}, closed or not"
assert store.retention(old) == {"card_ref": 0, "pr_ref": 0}, "running it again changes nothing"
assert store.retention(1_000.0) == {"card_ref": 0, "pr_ref": 0}, "nothing is old yet"

# ---- generated columns: pr, program and kind read the JSON the old readers still parse
with store.tx() as db:
    db.execute("INSERT INTO board_row (id, parent, pos, data) VALUES ('g1', NULL, 99, ?)",
               (json.dumps({"pr": "acme/widgets#7", "program": "prog-x", "kind": "card", "title": "t"}),))
    db.execute("INSERT INTO decision (id, status, title, options, link, created_at) VALUES "
               "('dg1', 'open', 'q', '[]', 'https://github.com/acme/widgets/pull/21/files', '2026-11-07T00:00:00Z')")
assert one("SELECT pr, program, kind FROM board_row WHERE id='g1'") == ("acme/widgets#7", "prog-x", "card")
assert one("SELECT pr, program, kind FROM decision WHERE id='dg1'") == ("acme/widgets#21", None, "decision")
assert one("SELECT count(*) FROM board_row WHERE pr='acme/widgets#7'")[0] >= 2, "the generated column filters: w7 and g1"
with store.tx() as db:
    db.execute("DELETE FROM board_row WHERE id='g1'")
    db.execute("DELETE FROM decision WHERE id='dg1'")

# ---- review fixes: each held a way for the tables to say something untrue
# a dismissed review is not a verdict
store.pr_refresh([{"repo": "acme/widgets", "number": 31, "state": "OPEN"}])
store.ask_add("acme/widgets#31", "rev-d")
store.send_log_add({"pr": "acme/widgets#31", "reviewer": "rev-d", "head": "h1", "action": "request", "source": "sent", "at": 1000.0,
                    "post_at": None, "thread": None, "scheduled": None, "channel": None})
dismissed = {"repo": "acme/widgets", "number": 31, "state": "OPEN", "reviewers": [{"login": "rev-d", "state": "DISMISSED", "at": "2026-11-07T05:00:00Z"}]}
assert store.pr_refresh([dismissed])["closed"] == {} and one("SELECT closed_at FROM review_ask WHERE reviewer='rev-d'") == (None,)
dismissed["reviewers"][0]["state"] = "COMMENTED"
assert store.pr_refresh([dismissed])["closed"] == {"reviewed": 1}
# a worktree that changed branch does not keep the old branch's PR; the same branch keeps it through a tick that lost it
store.card_refresh([{"id": "wt-c", "repo": "acme/widgets", "branch": "old", "pr": "acme/widgets#7"}])
store.card_refresh([{"id": "wt-c", "repo": "acme/widgets", "branch": "old", "pr": None}])
assert one("SELECT pr_number FROM card_ref WHERE orca_worktree_id='wt-c'") == (7,)
store.card_refresh([{"id": "wt-c", "repo": "acme/widgets", "branch": "new", "pr": None}])
assert one("SELECT pr_number FROM card_ref WHERE orca_worktree_id='wt-c'") == (None,), "live work is not listed for the merged PR of its old branch"
assert "wt-c" not in {r["id"] for r in store.reap_view()}
# the generated pr is lower-cased, so it joins pr_ref whatever case the source wrote
with store.tx() as db:
    db.execute("INSERT INTO decision (id, status, title, options, link, created_at) VALUES "
               "('dg2', 'open', 'q', '[]', 'https://github.com/Acme/Widgets/pull/31/files', '2026-11-07T00:00:00Z')")
assert one("SELECT count(*) FROM decision d JOIN pr_ref p ON p.repo || '#' || p.number = d.pr WHERE d.id='dg2'") == (1,)
with store.tx() as db:
    db.execute("DELETE FROM decision WHERE id='dg2'")
# a collector tick that saw no worktree does not tombstone them all, and one whose GitHub read failed does not stamp the cache as seen
store.card_refresh([{"id": "wt-d", "repo": "acme/widgets", "branch": "x", "pr": None}])
before = one("SELECT last_seen_at FROM pr_ref WHERE repo='acme/widgets' AND number=31")
import time as _t
_t.sleep(1.1)
real_graphql, collector.graphql = collector.graphql, lambda *a, **k: (_ for _ in ()).throw(fleet.SourceError("rate limited"))
try:
    tick()
finally:
    collector.graphql = real_graphql
assert one("SELECT last_seen_at FROM pr_ref WHERE repo='acme/widgets' AND number=31") == before, "a failed read stamps nothing"
assert one("SELECT gone_at IS NULL FROM card_ref WHERE orca_worktree_id='wt-d'") == (1,), "no worktrees in a tick: nobody tombstoned"
# a reload of the board tables keeps the step's columns, and the step still rolls back
store.load_board({"rows": {}, "order": [], "leads": {}})
cols = {r[1] for r in sqlite3.connect(store.path()).execute("PRAGMA table_xinfo(board_row)")}
assert {"pr", "program", "kind"} <= cols, cols
# ack --verified with a stage id named too does not refuse the id
code = subprocess.run([sys.executable, str(HERE / "dash.py"), "ack", "stage:nope", "--verified"], capture_output=True, text=True, env=env)
assert "no such row" in code.stderr and "board:" not in code.stderr, (code.stderr, code.stdout)

# ---- the step is its own: rollback refuses state until it is out, takes the columns and tables with it, and runs again
try:
    store.state_rollback(tmp / "none")
    raise AssertionError("pr_reviewer and card_ref point at pr_ref")
except store.Refused as e:
    assert "orch db rollback refs" in str(e), e
assert store.refs_rollback().startswith(store.REFS_STEP) and not store.refs_ready()
cols = {r[1] for r in sqlite3.connect(store.path()).execute("PRAGMA table_xinfo(board_row)")}
assert not {"pr", "program", "kind"} & cols and not one("SELECT name FROM sqlite_master WHERE name IN ('card_ref','reap_card','pr_reviewer')")
assert store.refs_setup().startswith("created") and one("SELECT count(*) FROM pr_ref")[0] >= 1, "the references survive a rollback of the step"

# ---- a collector with no refs step writes nothing and costs nothing; a broken DB costs the tables a tick, never the page
store.refs_rollback()
tick()
assert not collector.mem.get("db_refs_error") and (tmp / "fleet" / "prs.json").is_file()
store.refs_setup()
real_refresh, store.pr_refresh = store.pr_refresh, lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("locked"))
try:
    tick()
    assert "locked" in collector.mem["db_refs_error"] and (tmp / "fleet" / "state.json").is_file()
finally:
    store.pr_refresh = real_refresh
tick()
assert "db_refs_error" not in collector.mem

print("test_refs: ok")
