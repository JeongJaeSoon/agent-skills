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
program("alpha", {"repo": "acme/app", "run": "run_a", "created_at": ago(48), "predicate": ["A-1", "A-2", "A-3", "A-4"],
                   "stages": ["dev", "prod"]}, [
    {"ts": ago(40), "ev": "spawned", "ticket": "A-1", "note": "ctx_0a"},
    {"ts": ago(30), "ev": "ready", "pr": 11, "ticket": "A-1"},
    {"ts": ago(20), "ev": "landed", "pr": 11, "sha": SHA},
    {"ts": ago(10), "ev": "landed", "pr": 99, "sha": "f" * 40, "onto": 12},  # a fold, not a landing on main
    {"ts": ago(5), "ev": "ready", "pr": 13, "ticket": "A-3"},
])
# beta: finished (predicate_verified after its last landing), no `stages`. gamma: no ledger event in 7 days. _locks:
# not a program.
program("beta", {"repo": "acme/lib", "run": "run_b", "created_at": ago(100), "predicate": ["B-1", "B-2"]}, [
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
                                      "alpha/task_3", "beta", "beta/B-1", "beta/B-2"], stage_auto.read()["order"]
assert by("alpha")["group"] and by("alpha/A-1")["parent"] == "alpha"
assert by("alpha/A-1")["title"] == "A-1: login form — card" and by("alpha/A-2")["title"] == "A-2"

# Merge: the ledger's landed sha, else GitHub's merge commit; an open PR or no PR leaves it empty. dev and prod: empty
# where program.json `stages` names them (alpha), 해당 없음 where it does not (beta).
assert by("alpha/A-1")["pr"] == "acme/app#11" and by("alpha/A-1")["cells"]["merge"]["evidence"] == SHA[:7]
assert by("alpha/A-1")["cells"]["merge"]["from"] == "ledger landed"
assert by("alpha/A-2")["pr"] == "acme/app#12" and by("alpha/A-2")["cells"]["merge"] == {
    "mark": "ok", "evidence": "ccccccc", "at": ago(15), "auto": True, "from": "GitHub merge commit"}
assert by("alpha/A-3")["pr"] == "acme/app#13" and by("alpha/A-3")["cells"] == {}
assert by("alpha/A-4")["pr"] is None and by("alpha/A-4")["cells"] == {}
assert by("alpha/A-1-fix")["pr"] == "acme/app#14" and by("alpha/A-1-fix")["cells"] == {}
assert all(c not in r["cells"] for k, r in rows.items() if k.startswith("alpha") for c in ("dev", "prod"))
na = {"mark": "na", "auto": True, "from": "program.json stages"}
assert by("beta/B-1")["cells"]["dev"] == by("beta/B-1")["cells"]["prod"] == na and by("beta/B-2")["cells"] == {"dev": na, "prod": na}
# A finished program's Close ends the rows nothing else ended (B-2); the summary says which programs are active.
assert by("beta/B-2")["done_at"] == ago(50) and by("beta")["done_at"] == ago(50)
assert by("beta/B-1")["cells"]["merge"]["evidence"] == "bbbbbbb"
progs = stage_auto.read()["programs"]
assert {k: (v["active"], v["rows"]) for k, v in progs.items()} == {"alpha": (True, 6), "beta": (False, 2)}, progs
# Read as the table, a row whose every cell is ✅ or 해당 없음 ends at its latest cell's time, before the Close (B-1's
# merge); alpha deploys, so its merged A-2 waits on dev and prod.
t = {r["id"]: r for r in stages.table({"prs": state["prs"]})["rows"]}
assert t["beta/B-1"]["done_at"] == ago(60) and t["beta/B-2"]["done_at"] == ago(50), (t["beta/B-1"], t["beta/B-2"])
assert t["alpha/A-2"]["cells"]["merge"]["mark"] == "ok" and not t["alpha/A-2"].get("done_at")

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
assert got["omega/O-2"]["pr"] is None and "merge" not in got["omega/O-2"]["cells"], "no evidence, no cell"
# Without the PR in the list the row has no PR to hang the landing on, and stays empty rather than guessed.
got, _, _ = stage_auto.build([omega], {"prs": []}, now)
assert got["omega/O-1"]["pr"] is None and "merge" not in got["omega/O-1"]["cells"]
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

# Two cards of one ticket are two rows: the second is keyed by the ledger's `<ticket>-<word>` (else its task id), and
# each row's PR is the one its own card's session owns, matched by task title, not by the ticket both titles start with.
program("delta", {"repo": "acme/svc", "run": "run_d", "created_at": ago(10),
                  "predicate": ["P1-release", "P2-dev-check", "P3-C2-prod", "P4-K4-ready", "P5-C3-prod"]},
        [{"ts": ago(9), "ev": "spawned", "ticket": "D-1"}, {"ts": ago(8), "ev": "spawned", "ticket": "D-1-K4"}])
two = {"tasks": [{"id": "task_d1", "run": "run_d", "title": "D-1 rollout: release then prod"},
                 {"id": "task_d2", "run": "run_d", "title": "D-1 K4: client plugins for both versions"},
                 {"id": "task_d3", "run": "run_d", "title": "D-1 C3 conflict check"}],
       "sessions": [{"id": "s-d1", "run": "run_d", "task_title": "D-1 rollout: release then prod"},
                    {"id": "s-d2", "run": "run_d", "task_title": "D-1 K4: client plugins for both versions"}],
       "prs": [{"key": "acme/plugins#7", "repo": "acme/plugins", "state": "OPEN", "session": "s-d2", "created": ago(5)}]}
made, _, _ = stage_auto.build(stage_auto.load_programs("delta"), two, now)
assert made["delta/D-1"]["pr"] is None and made["delta/P4-K4-ready"]["pr"] == "acme/plugins#7", made
# The `<ticket>-<word>` card's word names exactly one predicate item (K4 -> P4-K4-ready): it is that item's row.
assert "delta/D-1-K4" not in made and made["delta/P4-K4-ready"]["title"].startswith("D-1 K4"), list(made)
assert made["delta/task_d3"]["title"] == "D-1 C3 conflict check" and made["delta/P1-release"]["item"], made
assert not made["delta/D-1"]["item"], made["delta/D-1"]

# A PR-less row recorded under a program's group folds into the one predicate item its id names past the group's;
# an id that names two items (prod) or none stays its own row.
stage_auto.refresh(two, now)
stages.add("delta", "delta by hand", group=True)  # a lead records its own heading under the program's id
stages.add("delta-release", "release with the two fixes", parent="delta")
stages.set_cell("delta-release", "merge", "ok", "v1.2.0")
stages.add("delta-k4", "K4 client PRs", parent="delta")
stages.add("delta-prod", "prod rollout", parent="delta")
stages.add("delta-docs", "docs page", parent="delta")
got = {r["id"]: r for r in stages.table(two)["rows"]}
assert got["delta/P1-release"]["title"] == "release with the two fixes" and got["delta/P1-release"]["cells"]["merge"]["evidence"] == "v1.2.0"
assert got["delta/P4-K4-ready"]["title"] == "K4 client PRs" and "delta-release" not in got and "delta-k4" not in got, list(got)
assert got["delta/P4-K4-ready"]["title_full"] == "K4 client PRs", "a recorded title replaces the uncut one too"
assert "delta-prod" in got and "delta-docs" in got and "delta/D-1-K4" not in got, list(got)

# A long card title: `title` is cut for the text brief as before, `title_full` carries it whole to the dashboard.
long = "E-1 " + "word " * 40 + "END"
program("eps", {"repo": "acme/eps", "run": "run_e", "created_at": ago(10), "predicate": []},
        [{"ts": ago(9), "ev": "spawned", "ticket": "E-1"}, {"ts": ago(8), "ev": "ready", "ticket": "E-1", "pr": 1}])
first = {"tasks": [{"id": "task_e1", "run": "run_e", "title": long},
                   {"id": "task_e2", "run": "run_e", "title": "E-2 same words"},
                   {"id": "task_e3", "run": "run_e", "title": "E-2 same words"}],
         "sessions": [{"id": "s-e3", "run": "run_e", "task_title": "E-2 same words"},
                      {"id": "s-e2", "run": "run_e", "task": "task_e2", "task_title": "E-2 same words"}],
         "prs": [{"key": "acme/eps#9", "repo": "acme/eps", "state": "OPEN", "session": "s-e3", "created": ago(5)},
                 {"key": "acme/eps#8", "repo": "acme/eps", "state": "OPEN", "session": "s-e2", "created": ago(5)}]}
made, _, held = stage_auto.build(stage_auto.load_programs("eps"), first, now)
assert len(made["eps/E-1"]["title"]) == 120 and made["eps/E-1"]["title_full"] == long, made["eps/E-1"]
# Two cards of one title: a session that names its task id takes its own card's row; one known only by the shared
# title could be either's, so neither row takes its PR, and both rows stay.
assert made["eps/E-2"]["pr"] == "acme/eps#8" and made["eps/task_e3"]["pr"] is None, made

# A card keeps its row across ticks: archived (gone from the Run's tasks), its row, title and ledger PR stay; a later
# card of its ticket keys by its own task id instead of taking that row.
later = {"tasks": [{"id": "task_e4", "run": "run_e", "title": "E-1 follow-up"}], "sessions": [], "prs": []}
made, _, _ = stage_auto.build(stage_auto.load_programs("eps"), later, now, held)
assert made["eps/E-1"]["title_full"] == long and made["eps/E-1"]["pr"] == "acme/eps#1", made["eps/E-1"]
assert made["eps/task_e4"]["title"] == "E-1 follow-up" and made["eps/task_e4"]["pr"] is None, list(made)
# Without what the last tick held, the follow-up takes E-1: the order dependence the stored cards guard against.
made, _, _ = stage_auto.build(stage_auto.load_programs("eps"), later, now)
assert made["eps/E-1"]["title"] == "E-1 follow-up", made["eps/E-1"]
# refresh() stores the cards and reads them back on the next tick.
stage_auto.refresh(first, now)
stage_auto.refresh(later, now)
assert stage_auto.read()["rows"]["eps/task_e4"]["title"] == "E-1 follow-up"

# Each row ends on its own, long before theta's Close. No `stages`: a merge alone ends T-1. A ticket whose last ledger
# word is released (here by the dispatch id its spawned row noted) or parked stops, with nothing merged and no open
# PR; admitted after parked does not. A card row whose card ended with no PR stops at the first tick that saw it and
# keeps that time; one whose PR is open (T-6), or whose card works (T-7), stays open.
program("theta", {"repo": "acme/th", "run": "run_t", "created_at": ago(20), "predicate": ["T-1", "T-2", "T-3", "T-4"]}, [
    {"ts": ago(19), "ev": "spawned", "ticket": "T-1", "note": "ctx_t1"},
    {"ts": ago(18), "ev": "landed", "pr": 31, "sha": "3" * 40, "ticket": "T-1"},
    {"ts": ago(17), "ev": "spawned", "ticket": "T-2", "note": "ctx_t2"},
    {"ts": ago(16), "ev": "released", "note": "ctx_t2"},
    {"ts": ago(15), "ev": "parked", "ticket": "T-3", "note": "waits on a decision"},
    {"ts": ago(14), "ev": "parked", "ticket": "T-4"}, {"ts": ago(13), "ev": "admitted", "ticket": "T-4"}])
th = {"tasks": [{"id": "task_t5", "run": "run_t", "title": "T-5: probe", "status": "completed"},
                {"id": "task_t6", "run": "run_t", "title": "T-6: fix", "status": "completed"},
                {"id": "task_t7", "run": "run_t", "title": "T-7: work", "status": "dispatched"}],
      "sessions": [], "sources": {"runs": {"ok": True}},
      "prs": [{"key": "acme/th#31", "repo": "acme/th", "state": "MERGED", "merged_at": ago(18), "merge_sha": "3" * 40},
              {"key": "acme/th#36", "repo": "acme/th", "state": "OPEN", "branch": "dev/T-6", "created": ago(5)}]}
stage_auto.refresh(th, now)
made = stage_auto.read()["rows"]
assert made["theta/T-1"]["cells"]["dev"] == na and not made["theta/T-1"]["done_at"], made["theta/T-1"]
assert (made["theta/T-2"]["stopped"], made["theta/T-2"]["done_at"]) == ("released", ago(16)), made["theta/T-2"]
assert (made["theta/T-3"]["stopped"], made["theta/T-3"]["done_at"]) == ("parked", ago(15)), made["theta/T-3"]
assert "stopped" not in made["theta/T-4"] and not made["theta/T-4"]["done_at"], made["theta/T-4"]
assert (made["theta/T-5"]["stopped"], made["theta/T-5"]["done_at"]) == ("card ended", fleet.iso(now)), made["theta/T-5"]
assert not made["theta/T-6"]["done_at"] and not made["theta/T-7"]["done_at"]
got = {r["id"]: r for r in stages.table(th)["rows"]}
assert got["theta/T-1"]["done_at"] == ago(18) and got["theta/T-5"]["stopped"] == "card ended"
# A recorded merge ✅ over a stopped row: not stopped, and ended by its cells at that cell's time.
stages.set_cell("theta/T-3", "merge", "ok", "abc1234 merged by hand")
t3 = {r["id"]: r for r in stages.table(th)["rows"]}["theta/T-3"]
assert not t3["stopped"] and t3["done_at"] == t3["cells"]["merge"]["at"], t3

# A stop needs to know the PR is not open: released with an open PR, or one the tick has no state for (a failed
# GitHub read), stays open; a closed one stops.
iota = ({"slug": "iota", "repo": "acme/io", "run": "run_i", "created_at": ago(10), "predicate": ["I-1"]}, [
    {"ts": ago(9), "ev": "spawned", "ticket": "I-1", "note": "ctx_i1"}, {"ts": ago(8), "ev": "ready", "ticket": "I-1", "pr": 5},
    {"ts": ago(7), "ev": "released", "note": "ctx_i1"}])
io5 = lambda s: {"prs": [{"key": "acme/io#5", "repo": "acme/io", "state": s}] if s else []}  # noqa: E731
assert [stage_auto.build([iota], io5(s), now)[0]["iota/I-1"].get("stopped") for s in ("OPEN", None, "CLOSED")] \
    == [None, None, "released"]
# A card's end, once stamped, survives a failed Run read that cannot see the card; a card read working again lifts it.
kappa = ({"slug": "kappa", "repo": "acme/k", "run": "run_k", "created_at": ago(10), "predicate": []}, [{"ts": ago(5), "ev": "config"}])
card = lambda status: [{"id": "task_k1", "run": "run_k", "title": "K-1: probe", "status": status}]  # noqa: E731
made, _, held = stage_auto.build([kappa], {"tasks": card("completed"), "sources": {"runs": {"ok": True}}}, now)
assert made["kappa/K-1"]["done_at"] == fleet.iso(now)
made, _, held = stage_auto.build([kappa], {"tasks": [], "sources": {"runs": {"ok": False}}}, now + dt.timedelta(hours=1), held)
assert (made["kappa/K-1"]["stopped"], made["kappa/K-1"]["done_at"]) == ("card ended", fleet.iso(now)), made["kappa/K-1"]
made, _, _ = stage_auto.build([kappa], {"tasks": card("dispatched"), "sources": {"runs": {"ok": True}}}, now, held)
assert not made["kappa/K-1"]["done_at"] and "stopped" not in made["kappa/K-1"]
# The card's end is kept apart from its PR: reopened while the Run read fails, the row is open; closed again, it
# stops at the card's first end, not a new time.
pr_k = lambda s: [{"key": "acme/k#3", "repo": "acme/k", "state": s, "session": "s-k1", "created": ago(4)}]  # noqa: E731
seen = {"tasks": card("completed"), "sessions": [{"id": "s-k1", "run": "run_k", "task": "task_k1"}], "sources": {"runs": {"ok": True}}}
made, _, held = stage_auto.build([kappa], {**seen, "prs": pr_k("CLOSED")}, now)
assert made["kappa/K-1"]["done_at"] == fleet.iso(now)
blind = {"tasks": [], "sessions": [{"id": "s-k1", "run": "run_k", "task": "task_k1"}], "sources": {"runs": {"ok": False}}}
made, _, held = stage_auto.build([kappa], {**blind, "prs": pr_k("OPEN")}, now + dt.timedelta(hours=1), held)
assert not made["kappa/K-1"]["done_at"] and held["kappa"]["cards"]["task_k1"]["ended"] == fleet.iso(now)
made, _, _ = stage_auto.build([kappa], {**blind, "prs": pr_k("CLOSED")}, now + dt.timedelta(hours=2), held)
assert made["kappa/K-1"]["done_at"] == fleet.iso(now), made["kappa/K-1"]
md = stages.render_md(stages.of_program(stages.table(th), "theta"))
assert "- T-5: probe (중단) — dev 해당 없음 · prod 해당 없음" in md.splitlines() and "- T-1 (끝남) — acme/th#31 · 머지 ✅ 3333333 · dev 해당 없음 · prod 해당 없음" in md.splitlines(), md
# stages-auto.json is written only when its content changes: the same tick five minutes on leaves it (and the ended
# card's time) alone. The collector's stage_auto_at keeps the reorient warning quiet meanwhile.
written, mtime = stage_auto.read()["generated_at"], stage_auto.path().stat().st_mtime_ns
stage_auto.refresh(th, now + dt.timedelta(minutes=5))
assert stage_auto.read()["generated_at"] == written and stage_auto.path().stat().st_mtime_ns == mtime
assert "not refreshed" in stage_auto.warning("theta", now + dt.timedelta(hours=7))
fleet.write_atomic(fleet.state_dir() / "memory.json", json.dumps({"stage_auto_at": fleet.iso(now + dt.timedelta(hours=7))}))
assert stage_auto.warning("theta", now + dt.timedelta(hours=7)) == ""
# A later spawned of T-2 reopens it; the change rewrites the file.
with (tmp / "programs" / "theta" / "ledger.jsonl").open("a") as f:
    f.write(json.dumps({"ts": ago(1), "ev": "spawned", "ticket": "T-2", "note": "ctx_t2b"}) + "\n")
stage_auto.refresh(th, now + dt.timedelta(minutes=10))
assert stage_auto.read()["generated_at"] != written and not stage_auto.read()["rows"]["theta/T-2"]["done_at"]
assert stage_auto.read()["rows"]["theta/T-5"]["done_at"] == fleet.iso(now), "the ended card keeps its first time"
# `orch set <slug> stages` names the deploy steps, a name the table has no column for kept as written: prod stays
# 해당 없음, dev waits, and the merged T-1 is open again until dev has its evidence.
out = subprocess.run([sys.executable, str(HERE / "prog.py"), "set", "theta", "stages", "dev,canary"], capture_output=True, text=True)
assert out.returncode == 0, out.stderr
assert json.loads((tmp / "programs" / "theta" / "program.json").read_text())["stages"] == ["dev", "canary"]
stage_auto.refresh(th, now + dt.timedelta(minutes=10))
t1 = {r["id"]: r for r in stages.table(th)["rows"]}["theta/T-1"]
assert "dev" not in t1["cells"] and t1["cells"]["prod"] == na and not t1.get("done_at"), t1
stages.set_cell("theta/T-1", "dev", "ok", "v3 on staging")
assert {r["id"]: r for r in stages.table(th)["rows"]}["theta/T-1"]["done_at"], "dev's evidence ends it"
subprocess.run([sys.executable, str(HERE / "prog.py"), "set", "theta", "stages", ""], capture_output=True, check=True)
stage_auto.refresh(th, now + dt.timedelta(minutes=10))

# One count for the Stages page and the brief: the page's `open` and `folded` come from stages.counted(), which runs
# the current() the brief's `단계표 N행 (접음 M)` line runs, so the two numbers are the same.
fleet.write_atomic(fleet.state_dir() / "state.json", json.dumps(th))
sys.path.insert(0, str(HERE))
import dash  # noqa: E402
page = dash.stage_report()
assert page["open"] == sum(1 for r in page["rows"] if r["open"] and not r.get("group")) > 0, page["open"]
assert {r["id"] for r in page["rows"] if r["open"]} >= {"theta/T-2", "theta/T-4", "theta/T-6", "theta/T-7"}
assert not any(r["open"] for r in page["rows"] if r["id"] in ("theta/T-1", "theta/T-3", "theta/T-5"))
brief = subprocess.run([sys.executable, str(HERE / "prog.py"), "brief", "render", "--no-refresh"], capture_output=True,
                       text=True, env={**os.environ, "PATH": "/usr/bin:/bin"})
line = next((l for l in brief.stdout.splitlines() if l.startswith("> 단계표 ")), None)
assert line and line.startswith(f"> 단계표 {page['open']}행 (접음 {page['folded']}) · "), (line, page["open"], page["folded"], brief.stderr[-500:])

print("test_stage_auto: ok")
