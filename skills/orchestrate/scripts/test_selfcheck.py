"""Offline tests for selfcheck.py: each mismatch class on invented data, the two-read rule, and one item per class.

Run: python3 test_selfcheck.py
"""
import copy, datetime as dt, os, pathlib, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import dash_demo

root = pathlib.Path(tempfile.mkdtemp(prefix="test-selfcheck-"))
os.environ.update(dash_demo.build(root))
import fleet  # noqa: E402  after the env points the state directory at root
import selfcheck  # noqa: E402

now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
clock = [now]
world = dash_demo.FakeFleetWorld(now)
f = world.fleet(clock=lambda: clock[0])
base = f.tick(force=True)
ms = lambda t: int(t.timestamp() * 1000)


def check(state=None, mutate_world=None):
    """findings() for a copy of the base state against a copy of the world, each changed as a test needs."""
    w = copy.deepcopy(world)
    if mutate_world:
        mutate_world(w)
    st = copy.deepcopy(state or base)
    live = {**w.fetch_fast(), "prs": {k: w.prs[int(k.split("#")[1])] for k in (p["key"] for p in st["prs"])}}
    probed = {k: fleet.parse(v.get("probed_at")) for k, v in f.prs.items()}
    return {c: [s for s, _ in rows] for c, rows in selfcheck.findings(st, live, probed, {}, clock[0]).items() if rows}


def with_state(change):
    st = copy.deepcopy(base)
    change(st)
    return st


def sess(st, sid):
    return next(s for s in st["sessions"] if s["id"] == sid)


# The demo world as collected: nothing to report.
assert check() == {}, check()

# Sessions: a worktree Orca lists that the page lacks, and one the page keeps after Orca archived or dropped it.
assert check(with_state(lambda st: st["sessions"].remove(sess(st, "wt-old")))) == {"session_missing": ["wt-old"]}
assert check(mutate_world=lambda w: w.worktrees[-1].update(isArchived=True)) == {"session_ghost": ["wt-old"]}
assert check(mutate_world=lambda w: w.worktrees.pop()) == {"session_ghost": ["wt-old"]}
assert check(mutate_world=lambda w: w.worktrees.clear()) == {}  # an empty read is a bad read, not every session gone
# A main worktree with no agent is not a session unless the config asks for it.
main_wt = {"worktreeId": "wt-main", "displayName": "main", "path": "/work/main", "isMainWorktree": True, "agents": []}
assert check(mutate_world=lambda w: w.worktrees.append(main_wt)) == {}

# The tree: a parent missing from the list, a root with a parent, and a loop that never reaches the root.
assert check(with_state(lambda st: sess(st, "wt-login").update(parent="wt-nowhere"))) == {"tree_parent": ["wt-login"]}
assert check(with_state(lambda st: sess(st, "wt-coord").update(parent="wt-login"))) == {"tree_parent": ["wt-coord"]}
loop = with_state(lambda st: (sess(st, "wt-login").update(parent="wt-export"), sess(st, "wt-export").update(parent="wt-login")))
assert check(loop) == {"tree_parent": ["wt-export", "wt-login"]}, check(loop)

# PRs: merged on GitHub but open on the page counts once the collector probed after the merge, or the merge is
# older than any probe tier; a merge the collector has not had its turn at yet does not.
merged = lambda at: lambda w: w.prs[42].update(state="MERGED", mergedAt=fleet.iso(at))
assert check(mutate_world=merged(now - dt.timedelta(hours=1))) == {"pr_state": ["acme/launchpad#42"]}
f.prs["acme/launchpad#42"]["probed_at"] = fleet.iso(now - dt.timedelta(seconds=60))
assert check(mutate_world=merged(now - dt.timedelta(seconds=30))) == {}
f.prs["acme/launchpad#42"]["probed_at"] = fleet.iso(now)
assert check(mutate_world=merged(now - dt.timedelta(seconds=30))) == {}  # GitHub may still have said OPEN then
assert check(mutate_world=merged(now - dt.timedelta(seconds=120))) == {"pr_state": ["acme/launchpad#42"]}

# Needs you items already dealt with: a turn answered in its own pane (a new turn began, or a later one finished),
# a question with a reply in its thread, a permission dialog that closed, an item on a session that is gone.
turn = next(i for i in base["items"] if i["source"] == "turn")
started = lambda w: w.worktrees[4]["agents"][0].update(state="working", stateStartedAt=ms(now + dt.timedelta(minutes=1)))
assert check(mutate_world=started) == {"item_answered": [turn["key"]]}, check(mutate_world=started)
finished = lambda w: w.worktrees[4]["agents"][0].update(lastAssistantMessage="Released v2; nothing left to do.")
assert check(mutate_world=finished) == {"item_answered": [turn["key"]]}
# A prompt to another pane of the session is not an answer: that is what "missed" shows.
assert check(with_state(lambda st: sess(st, "wt-scratch").update(last_prompt_at=fleet.iso(now)))) == {}
reply = {"id": "m2", "thread_id": "m1", "type": "reply", "from_handle": "term_coord", "to_handle": "term_export",
         "subject": "CSV", "created_at": fleet.iso(now)}
assert check(mutate_world=lambda w: w.messages.append(reply)) == {"mail_answered": ["mail:m1"]}
wait = next(i for i in base["items"] if i["key"].startswith("wait:"))
assert check(mutate_world=lambda w: w.worktrees[3]["agents"][0].update(state="working")) == {"prompt_gone": [wait["key"]]}
orphan = {"key": "ext:x", "type": "run_command", "session": "wt-nowhere", "title": "run it", "at": fleet.iso(now), "source": "skill"}
assert check(with_state(lambda st: st["items"].append(orphan))) == {"item_session_gone": ["ext:x"]}

# Text the page would print as "None".
bad = {"key": "reload:t:1", "type": "reload_pending", "session": None, "title": "reload 못 보냄: None", "source": "skills-sync"}
assert check(with_state(lambda st: st["items"].append(bad))) == {"item_text": ["reload:t:1"]}

# A session whose shown last activity is the worktree's time while its agent kept working.
stale = with_state(lambda st: sess(st, "wt-export").update(last_activity=fleet.iso(now - dt.timedelta(hours=2))))
assert check(stale) == {"activity_stale": ["wt-export"]}, check(stale)

# The loop: a difference is re-read GRACE_S later and only then becomes an item, one per class, keyed by what it names.
mono = [0.0]
sc = selfcheck.SelfCheck(now=lambda: clock[0], clock=lambda: mono[0])
assert sc.due() and sc.run(f) is False and sc.items == []  # the collected world agrees with itself
world.worktrees[-1]["isArchived"] = True
world.messages.append(reply)
mono[0] += selfcheck.GRACE_S
assert not sc.due()  # all was well at the last read: the next one is a full interval away
mono[0] += selfcheck.EVERY
assert sc.due() and sc.run(f) is False and sc.items == []  # seen once: not yet an item
assert not sc.due()
mono[0] += selfcheck.GRACE_S
assert sc.due() and sc.run(f) is True
assert sorted(i["check"] for i in sc.items) == ["mail_answered", "session_ghost"], sc.items
ghost = next(i for i in sc.items if i["check"] == "session_ghost")
assert ghost["type"] == "selfcheck" and ghost["title"].endswith("(1건)") and "old-spike" in ghost["detail"], ghost
keys = [i["key"] for i in sc.items]
mono[0] += selfcheck.EVERY
assert sc.run(f) is False and [i["key"] for i in sc.items] == keys  # still there: the same items, not new ones

# The items reach the Needs you list through the next tick, and leave it when the state catches up.
f.checks = sc.items
st = f.tick()
assert {i["check"] for i in st["items"] if i["type"] == "selfcheck"} == {"mail_answered", "session_ghost"}
mono[0] += selfcheck.EVERY
assert sc.run(f) is True and sc.items == [], sc.items
f.checks = sc.items
assert not [i for i in f.tick()["items"] if i["type"] == "selfcheck"]

print("test_selfcheck: ok")
