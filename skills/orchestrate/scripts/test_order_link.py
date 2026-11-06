"""Offline tests for how a unit gets tied to an owner order without a hand-typed `orch order link` (SSOT 5b):
`orch board todo|later|doing add --order`, `orch decide add --order`, `orch order reopen|links-complete|show`, and the
subagent hook's `ORDER: oN` line. The card start and the delegate gate have their own tests (scripts/test_card.py,
hooks/test_delegate_gate.py). Temporary state directories, invented data.

Run: python3 test_order_link.py
"""
import datetime as dt, json, os, pathlib, sqlite3, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
HOOKS = HERE.parents[2] / "hooks"
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-order-link-"))
env = {**os.environ, "ORCH_FLEET_STATE": str(tmp / "dashboard"), "ORCH_BRIEF_LAST": str(tmp / "brief-last"),
       "ORCH_FLEET_CONFIG": str(tmp / "none.json"), "AGENT_SKILLS_STATE": str(tmp / "state"), "TZ": "UTC",
       "AGENT_SKILLS_CONFIG": str(tmp / "none.json"), "PROGRAMS_HOME": str(tmp / "programs"),
       "HOME": str(tmp / "home"), "ORCA_TERMINAL_HANDLE": "term_root"}
os.environ.update(env)
sys.path.insert(0, str(HERE))
import fleet, store  # noqa: E402

fleet.state_dir().mkdir(parents=True, exist_ok=True)


def orch(*args):
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), *args], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def ok(*args):
    code, out, err = orch(*args)
    assert code == 0, (args, code, out, err)
    return out


def fails(*args):
    code, out, err = orch(*args)
    assert code != 0, (args, out)
    return err


def facts():
    return json.loads(ok("brief", "render", "--json", "--no-refresh"))["headline_facts"]


def loose():
    """The count the brief's headline gives for orders nothing carries, 0 when it gives no line."""
    line = [f for f in facts() if f.startswith("연결 없는 오너 지시")]
    return int(line[0].split()[4]) if line else 0


def backdate(oid, minutes):
    db = sqlite3.connect(str(store.path()))
    db.execute("UPDATE order_ SET created_at=? WHERE id=?", (fleet.iso(fleet.utcnow() - dt.timedelta(minutes=minutes)), oid))
    db.commit()
    db.close()


def links(oid):
    return next(r for r in json.loads(ok("order", "list", "--json")) if r["id"] == oid)["links"]


# ---- before the tables exist, --order says so and adds nothing
store.import_apply(checks=False)
err = fails("board", "todo", "add", "t", "--who", "x", "--due", "2099-01-01", "--order", "o1")
assert "orders are not available" in err and "order setup" in err, err
assert json.loads(ok("board", "show"))["todo"] == []
ok("order", "setup")

# ---- every board add takes --order: the item is created and linked, and the headline count drops
for n in range(1, 5):
    ok("order", "add", f"지시 {n}")
    backdate(f"o{n}", 30)
assert loose() == 4, facts()
out = ok("board", "todo", "add", "할 일 하나", "--who", "owner", "--due", "2099-01-01", "--order", "o1")
b1 = json.loads(ok("board", "show"))["todo"][0]["id"]
assert out == f"o1 linked board:{b1}", out
assert loose() == 3, facts()
out = ok("board", "later", "add", "나중에 할 일", "--trigger", "다음 분기", "--order", "o2")
b2 = [i for i in json.loads(ok("board", "show"))["later"] if i["title"] == "나중에 할 일"][0]["id"]
assert out == f"o2 linked board:{b2}", out
out = ok("board", "doing", "add", "지금 하는 일", "--owner", "사람", "--next", "회신", "--order", "o3")
b3 = [i for i in json.loads(ok("board", "show"))["doing"] if i["title"] == "지금 하는 일"][0]["id"]
assert out == f"o3 linked board:{b3}", out
assert loose() == 1 and links("o1") == [f"board:{b1}"] and links("o3") == [f"board:{b3}"], facts()

# A to-do reworded in place keeps its id, and the order still ends up linked to that id.
out = ok("board", "todo", "add", "할 일 하나 (고침)", "--who", "owner", "--due", "2099-01-01", "--order", "o4")
assert out == f"o4 linked board:{b1}", out
assert links("o4") == [f"board:{b1}"] and loose() == 0, facts()

# An unknown or closed order adds nothing, and says what to do.
before = json.dumps(json.loads(ok("board", "show")), sort_keys=True)
assert "no order o99" in fails("board", "later", "add", "x", "--trigger", "y", "--order", "o99")
ok("order", "done", "o2")
err = fails("board", "doing", "add", "x", "--owner", "a", "--next", "b", "--order", "o2")
assert "o2 is done" in err and "orch order reopen o2" in err, err
assert json.dumps(json.loads(ok("board", "show")), sort_keys=True) == before, "a refused --order adds no item"

# Without --order nothing changes: no link, no extra output.
assert ok("board", "later", "add", "order 없이", "--trigger", "언젠가") == ""

# ---- decide add --order links the new decision
def decide(*extra):
    return orch("decide", "add", "--title", "고르기", "--option", "가::이유 가", "--option", "나::이유 나", "--recommend", "1",
                "--if-no-answer", "가", "--reason", "owner-asked", *extra)


code, out, err = decide("--order", "o99")
assert code != 0 and "no order o99" in err, (out, err)
assert ok("decide", "list") == "", "an unknown order creates no decision"
code, out, err = decide("--order", "o4")
assert code == 0, (out, err)
did = out.splitlines()[0]
assert out.splitlines()[1] == f"o4 linked decision:{did}", out
assert f"decision:{did}" in links("o4")

# ---- reopen: closed -> open, the links stay, the links-complete mark goes
ok("order", "add", "표시 시험")
assert "no unit linked" in fails("order", "links-complete", "o5")
ok("order", "link", "o5", "--unit", "pr:acme/app#9", "--stand-in")
assert "no unit linked" in fails("order", "links-complete", "o5"), "a stand-in carries nothing"
ok("order", "link", "o5", "--unit", "card:wt-5")
assert ok("order", "links-complete", "o5").startswith("o5 links complete at ")
assert ok("order", "links-complete", "o5").startswith("o5 links complete at "), "a second mark changes nothing"
ok("order", "done", "o5")
assert "o5 is done" in fails("order", "links-complete", "o5")
assert ok("order", "reopen", "o5") == "o5 open"
assert ok("order", "reopen", "o5") == "o5 open", "reopening an open order changes nothing"
row = next(r for r in json.loads(ok("order", "list", "--json")) if r["id"] == "o5")
assert row["status"] == "open" and row["closed_at"] is None and row["links_complete_at"] is None, row
assert sorted(row["links"]) == ["card:wt-5", "pr:acme/app#9"], row
ok("order", "links-complete", "o5")
assert ok("order", "links-complete", "o5", "--undo") == "o5 links not marked complete"
assert "no order o99" in fails("order", "reopen", "o99")

# ---- show gives the card start the order's words
shown = json.loads(ok("order", "show", "o5", "--json"))
assert shown["id"] == "o5" and shown["text"] == "표시 시험" and shown["status"] == "open", shown
assert ok("order", "show", "o5").splitlines()[1] == "표시 시험"
assert "no order o99" in fails("order", "show", "o99")

# ---- every step left its event
db = sqlite3.connect(str(store.path()))
evs = [(r[0], r[1]) for r in db.execute("SELECT id, ev FROM event WHERE entity='order' ORDER BY seq")]
db.close()
for want in (("o5", "links_complete"), ("o5", "done"), ("o5", "reopen"), ("o5", "links_open"), ("o4", "link")):
    assert want in evs, (want, evs)

# ---- a subagent whose first prompt has an ORDER line is linked by the hook, once, for start and stop
ok("order", "add", "서브에이전트 시험")
oid = "o6"


def subagent(prompt, aid, event="SubagentStart", order_state="open"):
    tdir = tmp / "claude" / "sess"
    (tdir / "subagents").mkdir(parents=True, exist_ok=True)
    (tdir / "subagents" / f"agent-{aid}.jsonl").write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}) + "\n")
    ev = {"hook_event_name": event, "agent_id": aid, "session_id": "s1", "transcript_path": str(tmp / "claude" / "sess.jsonl")}
    r = subprocess.run([sys.executable, str(HOOKS / "subagent_log.py")], input=json.dumps(ev), capture_output=True,
                       text=True, env=env)
    assert r.returncode == 0 and r.stdout == "", r


subagent(f"일을 한다.\nORDER: {oid}\n끝.", "a1")
subagent(f"일을 한다.\nORDER: {oid}\n끝.", "a1", "SubagentStop")
assert links(oid) == ["agent:a1"], links(oid)
subagent(f"{oid} 를 참고해서 일을 한다 (ORDER {oid} in prose, ORDER: {oid} extra)", "a2")
assert links(oid) == ["agent:a1"], "a mention is not a line of its own"
ok("order", "add", "펜스 시험")  # o7 below is this one; the fenced example names o6
subagent("예시:\n```\nORDER: o6\n```\nORDER: o7\n일", "a5")
assert links("o7") == ["agent:a5"] and "agent:a5" not in links("o6"), (links("o7"), links("o6"))
subagent("ORDER: o99", "a3")
subagent("ORDER: o2", "a4")  # o2 is done
assert "agent:a4" not in links("o2"), links("o2")
assert not any("agent:a3" in x or "agent:a4" in x for r in json.loads(ok("order", "list", "--json")) for x in r["links"])
# A transcript that does not exist at the start is read at the stop, and an order that closed meanwhile still gets the link.
ok("order", "add", "늦은 transcript")
tdir = tmp / "claude" / "sess" / "subagents"
(tdir / "agent-late.jsonl").unlink(missing_ok=True)
late = {"agent_id": "late", "session_id": "s1", "transcript_path": str(tmp / "claude" / "sess.jsonl")}


def hook(event):
    r = subprocess.run([sys.executable, str(HOOKS / "subagent_log.py")], input=json.dumps({**late, "hook_event_name": event}),
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout == "", r


hook("SubagentStart")
assert links("o8") == [], "no transcript yet"
(tdir / "agent-late.jsonl").write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "ORDER: o8\n일"}}) + "\n")
ok("order", "done", "o8")
hook("SubagentStart")
assert links("o8") == [], "a closed order is not linked at the start"
hook("SubagentStop")
assert links("o8") == ["agent:late"], links("o8")
print("ok")
