"""Offline tests for stage_auto.py (the stage table's generated rows), its merge with `orch stage` cells in stages.py,
the issue/PR links of the table and the reorient hook's stage warning, on invented data.

Run: python3 test_stage_auto.py
"""
import datetime as dt, json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-stage-auto-"))
env = {"ORCH_FLEET_STATE": str(tmp / "dashboard"), "PROGRAMS_HOME": str(tmp / "programs"),
       "ORCH_FLEET_CONFIG": str(tmp / "config.json"), "AGENT_SKILLS_STATE": str(tmp / "state")}
os.environ.update(env)
sys.path.insert(0, str(HERE))
import fleet, stage_auto, stages  # noqa: E402

now = fleet.utcnow().replace(microsecond=0)
ago = lambda h: fleet.iso(now - dt.timedelta(hours=h))  # noqa: E731
SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


def program(slug, cfg, events):
    d = tmp / "programs" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "program.json").write_text(json.dumps({"slug": slug, **cfg}))
    (d / "ledger.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


# alpha: live. A landed (ledger sha), B merged on GitHub (found by its branch), C open, D nothing yet; a follow-up
# card outside the predicate gets a row of its own.
program("alpha", {"repo": "acme/app", "run": "run_a", "created_at": ago(48), "predicate": ["A-1", "A-2", "A-3", "A-4"]}, [
    {"ts": ago(40), "ev": "spawned", "ticket": "A-1", "note": "ctx_0a"},
    {"ts": ago(30), "ev": "ready", "pr": 11, "ticket": "A-1"},
    {"ts": ago(20), "ev": "landed", "pr": 11, "sha": SHA},
    {"ts": ago(10), "ev": "landed", "pr": 99, "sha": "f" * 40, "onto": 12},  # a fold, not a landing on main
    {"ts": ago(5), "ev": "ready", "pr": 13, "ticket": "A-3"},
])
# beta: finished (predicate_verified after its last landing). gamma: no ledger event in 7 days. _locks: not a program.
program("beta", {"repo": "acme/lib", "run": "run_b", "created_at": ago(100), "predicate": ["B-1"]}, [
    {"ts": ago(60), "ev": "landed", "pr": 5, "sha": "b" * 40, "ticket": "B-1"},
    {"ts": ago(50), "ev": "predicate_verified", "note": "final check on main"}])
program("gamma", {"repo": "acme/old", "run": "run_c", "created_at": ago(400), "predicate": ["G-1"]},
        [{"ts": ago(300), "ev": "spawned", "ticket": "G-1"}])
program("_locks", {"predicate": ["X-1"]}, [{"ts": ago(1), "ev": "lane"}])

state = {
    "tasks": [{"id": "task_1", "run": "run_a", "title": "A-1: login form — card", "status": "completed"},
              {"id": "task_2", "run": "run_a", "title": "A-1-fix: address review on A-1", "status": "dispatched"},
              {"id": "task_3", "run": "run_a", "title": "untitled scratch", "status": "dispatched"},
              {"id": "task_9", "run": "run_z", "title": "Z-1: someone else's run", "status": "dispatched"}],
    "sessions": [{"id": "s-fix", "run": "run_a", "task_title": "A-1-fix: address review on A-1"}],
    "prs": [{"key": "acme/app#11", "repo": "acme/app", "state": "MERGED", "merged_at": ago(20), "merge_sha": SHA},
            {"key": "acme/app#12", "repo": "acme/app", "state": "MERGED", "merged_at": ago(15), "merge_sha": "c" * 40,
             "branch": "dev/A-2", "created": ago(25)},
            {"key": "acme/app#13", "repo": "acme/app", "state": "OPEN", "branch": "dev/A-3", "created": ago(6)},
            {"key": "acme/app#14", "repo": "acme/app", "state": "OPEN", "session": "s-fix", "created": ago(3)},
            {"key": "acme/app#3", "repo": "acme/app", "state": "MERGED", "merge_sha": "d" * 40, "branch": "dev/A-4",
             "created": ago(500)},  # a branch of the same name from before the program: not its PR
            {"key": "acme/web#4", "repo": "acme/web", "state": "OPEN", "created": ago(2)}],
}
rows = stage_auto.refresh(state, now)
by = lambda rid: rows[rid]  # noqa: E731

# Rows: a group per program with work in 7 days, one row per predicate item, then one per other card of its Run.
assert stage_auto.read()["order"] == ["alpha", "alpha/A-1", "alpha/A-2", "alpha/A-3", "alpha/A-4", "alpha/A-1-fix",
                                      "alpha/task_3", "beta", "beta/B-1"], stage_auto.read()["order"]
assert by("alpha")["group"] and by("alpha/A-1")["parent"] == "alpha"
assert by("alpha/A-1")["title"] == "A-1: login form — card" and by("alpha/A-2")["title"] == "A-2"

# Merge: the ledger's landed sha, else GitHub's merge commit; an open PR or no PR leaves it empty. dev and prod: empty.
assert by("alpha/A-1")["pr"] == "acme/app#11" and by("alpha/A-1")["cells"]["merge"]["evidence"] == SHA[:7]
assert by("alpha/A-1")["cells"]["merge"]["from"] == "ledger landed"
assert by("alpha/A-2")["pr"] == "acme/app#12" and by("alpha/A-2")["cells"]["merge"] == {
    "mark": "ok", "evidence": "ccccccc", "at": ago(15), "auto": True, "from": "GitHub merge commit"}
assert by("alpha/A-3")["pr"] == "acme/app#13" and by("alpha/A-3")["cells"] == {}
assert by("alpha/A-4")["pr"] is None and by("alpha/A-4")["cells"] == {}
assert by("alpha/A-1-fix")["pr"] == "acme/app#14" and by("alpha/A-1-fix")["cells"] == {}
assert all(c not in r["cells"] for r in rows.values() for c in ("dev", "prod"))
# A finished program's rows carry done_at; the summary says which programs are active.
assert by("beta/B-1")["done_at"] == ago(50) and by("beta")["done_at"] == ago(50)
assert by("beta/B-1")["cells"]["merge"]["evidence"] == "bbbbbbb"
progs = stage_auto.read()["programs"]
assert {k: (v["active"], v["rows"]) for k, v in progs.items()} == {"alpha": (True, 6), "beta": (False, 1)}, progs

# The table: generated rows, then recorded ones. A recorded cell on a generated row's id wins over the generated one;
# a generated row's merge is evidence or nothing, never the PR's state (A-3's open PR stays empty).
stages.add("solo", "outside any program", pr="acme/web#4")
stages.set_cell("alpha/A-1", "merge", "partial", "reverted on main, re-landing", by="lead")
stages.set_cell("alpha/A-1", "dev", "ok", "v1.4.0 on staging")
try:
    stages.set_cell("alpha", "dev", "ok", "v1")
    raise AssertionError("a group row took a cell")
except ValueError:
    pass
t = stages.table({"prs": state["prs"]})
got = {r["id"]: r for r in t["rows"]}
assert [r["id"] for r in t["rows"]][-1] == "solo"
assert got["alpha/A-1"]["cells"]["merge"]["mark"] == "partial" and got["alpha/A-1"]["cells"]["dev"]["evidence"] == "v1.4.0 on staging"
assert got["alpha/A-1"]["title"] == "A-1: login form — card" and got["alpha/A-1"]["depth"] == 1
assert got["alpha/A-3"]["cells"] == {} and got["solo"]["cells"]["merge"]["evidence"] == "리뷰 대기"
assert "alpha/A-1" not in stages.read()["order"], "an overlay is cells only, placed by the generated row"
# An overlay whose generated row left (its program went quiet) draws nothing.
stage_auto.fleet.write_atomic(stage_auto.path(), json.dumps({"rows": {}, "order": [], "programs": {}}))
assert [r["id"] for r in stages.table({"prs": state["prs"]})["rows"]] == ["solo"]
rows = stage_auto.refresh(state, now)

# Links: a PR the table knows goes to /pull/, any other owner/repo#N to /issues/ (GitHub forwards a PR's).
stages.set_cell("alpha/A-4", "dev", "fail", "blocked on acme/infra#7, see acme/app#12")
refs = stages.table({"prs": state["prs"]})["refs"]
assert refs["acme/app#12"] == "https://github.com/acme/app/pull/12" and refs["acme/app#13"] == "https://github.com/acme/app/pull/13"
assert refs["acme/infra#7"] == "https://github.com/acme/infra/issues/7", refs

# PROGRAMS_HOME is read per call: `dash.py demo` sets it after prog is imported, and must not show this PC's programs.
os.environ["PROGRAMS_HOME"] = str(tmp / "elsewhere")
assert stage_auto.load_programs() == [] and stage_auto.load_programs("alpha") == []
os.environ["PROGRAMS_HOME"] = env["PROGRAMS_HOME"]

# Acks: a finished generated row can be acked like a recorded one.
import acks  # noqa: E402
assert "stage:beta" in acks.ackable(now, {"stage"}) and "stage:alpha" not in acks.ackable(now, {"stage"})

# The hook's warning: an active program with no work row, or rows the tick has not rewritten in stage_warn_h hours.
assert stage_auto.warning("alpha", now) == "" and stage_auto.warning(None, now) == ""
assert stage_auto.warning("beta", now) == "", "a finished program is not warned about"
program("delta", {"repo": "acme/new", "run": "run_d", "created_at": ago(1), "predicate": []}, [{"ts": ago(1), "ev": "config"}])
stage_auto.refresh(state, now)
assert stage_auto.warning(None, now) == "Stage table: no stage row for active program delta.", stage_auto.warning(None, now)
assert stage_auto.warning("delta", now) == "Stage table: no stage row for active program delta."
later = now + dt.timedelta(hours=7)
assert "not refreshed in 6 h" in stage_auto.warning("alpha", later) and "orch-dash ensure" in stage_auto.warning("alpha", later)
(tmp / "config.json").write_text(json.dumps({"board": {"stage_warn_h": 8}}))
assert stage_auto.warning("alpha", later) == "", "stage_warn_h moves the line"
(tmp / "config.json").write_text("{}")
assert stage_auto.warning("nope", now) == "", "no such program"
# A program the tick skips is active by the same rule: quiet for 7 days or stopped and aged out is not warned about,
# one created after the last tick is.
assert stage_auto.warning("gamma", now) == "", "no ledger event in 7 days"
program("zeta", {"repo": "acme/z", "created_at": ago(400), "predicate": ["Z-1"]},
        [{"ts": ago(200), "ev": "spawned", "ticket": "Z-1"}, {"ts": ago(190), "ev": "stop"}])
assert stage_auto.warning("zeta", now) == "", "stopped, then aged out"
program("eta", {"repo": "acme/e", "created_at": ago(0), "predicate": ["E-1"]}, [{"ts": ago(0), "ev": "config"}])
assert stage_auto.warning("eta", now) == "Stage table: no stage row for active program eta.", "newer than the tick"
# A tick that never ran.
stage_auto.path().unlink()
assert "not refreshed" in stage_auto.warning("alpha", now)

# Through the hook: a lead's prompt line carries the warning; ROLE: none silences it with the rest.
hook = HERE.parents[2] / "hooks" / "reorient.py"


def prompt(text, session="s1"):
    out = subprocess.run([sys.executable, str(hook)], input=json.dumps({"hook_event_name": "UserPromptSubmit",
                         "session_id": session, "prompt": text}), capture_output=True, text=True,
                         env={**os.environ, "PATH": "/usr/bin:/bin"})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"] if out.stdout.strip() else ""


stage_auto.refresh(state, now)
prompt("ROLE: lead delta")
assert prompt("next").endswith("Stage table: no stage row for active program delta."), prompt("next")
prompt("ROLE: lead alpha", "s2")
assert "Stage table" not in prompt("next", "s2")
assert prompt("ROLE: none") == "ROLE cleared: this session no longer gets the orchestrate reminder."

# A torn last line or a row without a string ev is skipped, not the whole program.
assert [stage_auto.ledger_row(l) for l in ['{"ev": "landed", "pr', '[1]', '{"ev": 3}', '', '{"ev": "stop"}']] \
    == [None, None, None, None, {"ev": "stop"}]

# Evidence outlives the cards: once a program's cards are archived (no task, no session), a ledger `landed` with no
# ticket still fills merge, the PR found by its branch in the collector's list (which keeps every PR a program's
# ledger names, fleet.program_prs).
omega = ({"slug": "omega", "repo": "acme/app", "run": "run_o", "created_at": ago(30), "predicate": ["O-1", "O-2"]}, [
    {"ts": ago(29), "ev": "spawned", "ticket": "O-1", "note": "ctx_0o"},
    {"ts": ago(20), "ev": "verdict", "pr": 21, "sha": "e" * 40, "result": "pass"},
    {"ts": ago(19), "ev": "landed", "pr": 21, "sha": "9" * 40}])
archived = {"tasks": [], "sessions": [], "prs": [
    {"key": "acme/app#21", "repo": "acme/app", "state": "MERGED", "merge_sha": "9" * 40, "branch": "dev/O-1", "created": ago(25)}]}
got, order, _ = stage_auto.build([omega], archived, now)
assert got["omega/O-1"]["pr"] == "acme/app#21" and got["omega/O-1"]["cells"]["merge"] == {
    "mark": "ok", "evidence": "9999999", "at": ago(19), "auto": True, "from": "ledger landed"}, got["omega/O-1"]
assert got["omega/O-2"]["pr"] is None and got["omega/O-2"]["cells"] == {}, "no evidence, no cell"
# Without the PR in the list the row has no PR to hang the landing on, and stays empty rather than guessed.
got, _, _ = stage_auto.build([omega], {"prs": []}, now)
assert got["omega/O-1"]["pr"] is None and got["omega/O-1"]["cells"] == {}
program("omega", omega[0], omega[1])
assert {"acme/app#21"} <= fleet.program_prs(now) and "acme/old#" not in " ".join(fleet.program_prs(now)), "gamma is quiet"

# One row per piece of work: a recorded row with a generated row's PR folds into it. Its cells win where it has them,
# the generated ones fill the rest; a recorded group whose every row folded goes, one with a row left stays.
stage_auto.refresh(archived | {"tasks": state["tasks"], "sessions": state["sessions"], "prs": state["prs"] + archived["prs"]}, now)
stages.add("hand", "omega by hand", group=True)
stages.add("hand-o1", "O-1 written up", pr="acme/app#21", parent="hand")
stages.add("hand-note", "a note under O-1", parent="hand-o1")
stages.set_cell("hand-o1", "dev", "ok", "v2 on staging")
stages.add("hand2", "mixed", group=True)
stages.add("hand2-o1", "O-1 again", pr="acme/app#21", parent="hand2")
stages.add("hand2-x", "outside any program", parent="hand2")
t = stages.table({"prs": state["prs"] + archived["prs"]})
got = {r["id"]: r for r in t["rows"]}
assert "hand" not in got and "hand-o1" not in got and "hand2-o1" not in got, list(got)
assert "hand2" in got and "hand2-x" in got
o1 = got["omega/O-1"]
assert o1["cells"]["merge"]["evidence"] == "9999999" and o1["cells"]["dev"]["evidence"] == "v2 on staging", o1["cells"]
assert o1["parent"] == "omega" and got["hand-note"]["parent"] == "omega/O-1" and got["hand-note"]["depth"] == 2
ids = [r["id"] for r in t["rows"]]
assert ids.index("hand-note") == ids.index("omega/O-1") + 1, ids
stages.set_cell("hand-o1", "merge", "partial", "reverted")
assert {r["id"]: r for r in stages.table({"prs": []})["rows"]}["omega/O-1"]["cells"]["merge"]["mark"] == "partial"

print("test_stage_auto: ok")
