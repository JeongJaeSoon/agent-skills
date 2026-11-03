"""`orch status`: the `inbox:` wait line comes first and stands alone with --inbox, and the lines `orch wait` used to
print (TAKEN OVER, LAND, WAITING ON REVIEW, MERGED OUTSIDE LAND, WATCH-CARDS DOWN, PATROL) come out here too. A worker
row missing a field a judgment needs is printed as it came with one WARN line, and nothing is decided from it.
Run: python3 test_status_lines.py"""
import contextlib, io, json, os, pathlib, shutil, sys, tempfile, atexit

tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-status-lines-"))
atexit.register(shutil.rmtree, tmp, True)
os.environ.update(PROGRAMS_HOME=str(tmp / "programs"), AGENT_SKILLS_STATE=str(tmp / "state"), ORCH_DASH="off",
                  ORCH_FLEET_STATE=str(tmp / "dashboard"))
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import card_watch, patrol, prog  # noqa: E402


def program(slug, **extra):
    d = prog.HOME / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "program.json").write_text(json.dumps({"slug": slug, "repo": "acme/app", "run": "run_x", "predicate": [],
                                                "ceiling": 4, "merge_policy": "autonomous",
                                                "created_at": "2026-11-01T00:00:00+00:00", **extra}))
    (d / "ledger.jsonl").write_text("".join(json.dumps(e) + "\n" for e in LEDGER))
    return d


LEDGER = [{"ts": "2026-11-01T01:00:00+00:00", "ev": "spawned", "ticket": "ABC-1", "note": "ctx_a1"},
          {"ts": "2026-11-01T01:00:00+00:00", "ev": "spawned", "ticket": "ABC-2", "note": "ctx_b2"},
          {"ts": "2026-11-01T02:00:00+00:00", "ev": "ready", "pr": 11, "ticket": "ABC-1"},
          {"ts": "2026-11-01T02:00:00+00:00", "ev": "verdict", "pr": 11, "result": "pass"},
          {"ts": "2026-11-01T02:00:00+00:00", "ev": "ready", "pr": 12, "ticket": "ABC-2"},
          {"ts": "2026-11-01T02:00:00+00:00", "ev": "verdict", "pr": 12, "result": "pass"},
          {"ts": "2026-11-01T02:00:00+00:00", "ev": "ready", "pr": 13, "ticket": "ABC-3"},
          {"ts": "2026-11-01T02:00:00+00:00", "ev": "verdict", "pr": 13, "result": "pass"}]
green = [{"name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"}]
OK = {"state": "OPEN", "reviewDecision": "APPROVED", "mergeStateStatus": "CLEAN", "isDraft": False, "labels": [],
      "statusCheckRollup": green, "headRefOid": "abcdef1234"}
VIEWS = {11: OK, 12: OK, 13: dict(OK, state="MERGED")}
taken = {"dispatchId": "ctx_t", "taskId": "tt", "terminalState": "retained", "workerState": "succeeded",
         "agentTerminalHandle": "term_t", "resource": {"retainedReason": "user_takeover", "worktreeId": "wt"}}
done_a = {"dispatchId": "ctx_a1", "taskId": "ta", "terminalState": "released", "workerState": "succeeded",
          "resource": {"worktreeId": "wa"}}
done_b = {"dispatchId": "ctx_b2", "taskId": "tb", "terminalState": "released", "workerState": "succeeded",
          "resource": {"worktreeId": "wb"}}
WORKERS = [taken, done_a, done_b]

prog.run_tasks = lambda run: []
prog.tracker_issues = lambda cfg, ids=(): None
prog.open_rows = lambda repo: []
prog.repo_stacks = lambda repo: {}
prog.program_files = lambda *a: ({}, {})
prog.cross_inputs = lambda slug, repo, events, rows, prs, files: (prs, files, {}, set())
prog.unresolved_threads = lambda repo, n: 0
prog.orca_json = lambda *a: {"worktrees": []} if a[:2] == ("worktree", "list") else {}
prog.gh_json = lambda *a: VIEWS[int(a[2])]
card_watch.down_line = lambda me: "WATCH-CARDS DOWN: its last round ended 20 min ago; start it again"
patrol.pending_lines = lambda me, unowned: ["  PATROL S card-z: stuck on a prompt", "  after dealing with each: orch patrol ack p1"]


def status(slug, *flags, workers=None, me="term_lead"):
    prog.run_workers = workers or (lambda run: [dict(w) for w in WORKERS])
    if me is None:
        os.environ.pop("ORCA_TERMINAL_HANDLE", None)
    else:
        os.environ["ORCA_TERMINAL_HANDLE"] = me
    out, code = io.StringIO(), 0
    with contextlib.redirect_stdout(out):
        try:
            prog.cmd_status([slug, *flags])
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
            print(e.code if not isinstance(e.code, int) else "", end="")
    return out.getvalue().splitlines(), code


d = program("run-lead")
wait = (f"inbox: orca orchestration check --run run_x --wait --types escalation,question,worker_done --timeout-ms 1800000"
        f" --full --json 2>>{d / 'wait.err'}")
lines, code = status("run-lead")
assert code == 0 and lines[0] == wait, lines
assert lines[1].startswith("WATCH-CARDS DOWN") and lines[2] == "  PATROL S card-z: stuck on a prompt", lines
assert "  TAKEN OVER ctx_t (succeeded): worker-release refuses it; ask it to check for work left and close itself" in lines, lines
assert any(l.startswith("    orca terminal send --terminal term_t --text ") for l in lines), lines
assert "  LAND #11 (ABC-1): approved, clean and green at abcdef12, and no worker holds it" in lines, lines
assert "  LAND #12 (ABC-2): approved, clean and green at abcdef12, and no worker holds it" in lines, lines
assert "  MERGED OUTSIDE LAND #13 (ABC-3): record it and finish its ticket as for a LAND" in lines, lines
assert not any("WARN" in l or l.startswith("  ROW ") for l in lines), lines
# The self-check is asked once, in status's own state file, not orch wait's.
lines, _ = status("run-lead")
assert any(l.startswith("  TAKEN OVER ctx_t (succeeded): self-check asked at ") for l in lines), lines
assert not any("orca terminal send" in l for l in lines), lines
assert (d / "status-self-check.json").exists() and not (d / "self-check.json").exists()
# A program with no taken-over worker gets no state file.
d3 = program("quiet")
status("quiet", workers=lambda run: [dict(done_a), dict(done_b)])
assert not (d3 / "status-self-check.json").exists()

# --inbox prints the wait line alone and calls nothing else.
prog.run_workers = lambda run: (_ for _ in ()).throw(AssertionError("--inbox must not list workers"))
out = io.StringIO()
with contextlib.redirect_stdout(out):
    try:
        prog.cmd_status(["run-lead", "--inbox"])
    except SystemExit as e:
        assert e.code == 0, e.code
assert out.getvalue().splitlines() == [wait], out.getvalue()

# A failed worker lookup still leaves the wait line first.
def down(run):
    raise SystemExit("orca orchestration worker-list … failed (1): no runtime")
lines, code = status("run-lead", workers=down)
assert lines[0] == wait and code == 1 and lines[-1].startswith("orca orchestration worker-list"), (lines, code)

# A lead that is a worker of the Run waits on its own terminal, never on the Run; with no handle there is no command.
d2 = program("sub-lead", lead_dispatch="ctx_lead")
lines, code = status("sub-lead")
assert lines[0].startswith("inbox: orca orchestration check --terminal term_lead --wait ") and "--run" not in lines[0], lines
assert lines[0].endswith(f"2>>{d2 / 'wait.err'}") and code == 0, lines
lines, code = status("sub-lead", me=None)
assert lines[0] == "inbox: cannot build the wait: lead_dispatch is set but ORCA_TERMINAL_HANDLE is empty" and code == 1, (lines, code)
assert any(l.startswith("  LAND #11") for l in lines), "the rest of status still prints"
lines, code = status("sub-lead", "--inbox", me="")
assert lines == ["inbox: cannot build the wait: lead_dispatch is set but ORCA_TERMINAL_HANDLE is empty"] and code == 1, lines

# A row without terminalState: its ticket's PR is not offered to land, the row is printed as it came with one WARN,
# and the other PR still lands.
blind_a = {k: v for k, v in done_a.items() if k != "terminalState"}
lines, _ = status("run-lead", workers=lambda run: [dict(taken), blind_a, dict(done_b)])
assert not any(l.startswith("  LAND #11") for l in lines), lines
assert "  WAITING ON REVIEW #11 (ABC-1): worker state unknown" in lines, lines
assert "  LAND #12 (ABC-2): approved, clean and green at abcdef12, and no worker holds it" in lines, lines
assert f"  ROW {json.dumps(blind_a)}" in lines, lines
assert "  WARN status: worker row not judged (missing terminalState)" in lines, lines
# A row tied to no ticket may hold any PR: nothing lands.
loose = {"dispatchId": "ctx_99", "terminalState": "active", "workerState": "running"}
lines, _ = status("run-lead", workers=lambda run: [dict(done_a), dict(done_b), loose])
assert not any(l.startswith("  LAND #") for l in lines), lines
assert "  WAITING ON REVIEW #11 (ABC-1): worker state unknown; #12 (ABC-2): worker state unknown" in lines, lines
assert "  WARN status: worker row not judged (missing taskId)" in lines, lines
# An active worker whose Task status did not read cannot be tied to a ticket: nothing lands.
unlisted = {"dispatchId": "ctx_c3", "taskId": "unlisted_task", "terminalState": "active", "workerState": "running"}
lines, _ = status("run-lead", workers=lambda run: [dict(done_a), dict(done_b), unlisted])
assert not any(l.startswith("  LAND #") for l in lines), lines
assert "  WARN status: worker row not judged (missing taskId in task-list)" in lines, lines
# An active worker whose Task is listed but names no ticket holds no program PR.
prog.run_tasks = lambda run: [{"id": "unlisted_task", "display_name": "QA lead"}]
lines, _ = status("run-lead", workers=lambda run: [dict(done_a), dict(done_b), unlisted])
assert any(l.startswith("  LAND #11") for l in lines) and not any("WARN" in l for l in lines), lines
prog.run_tasks = lambda run: []
# A row without dispatchId is printed with its WARN, not a crash.
nameless = {"terminalState": "active", "taskId": "ta", "projection": {"outcome": "in_progress"}}
lines, code = status("run-lead", workers=lambda run: [dict(done_a), dict(done_b), nameless])
assert code == 0 and any(l.startswith("  WARN status: worker row not judged (missing dispatchId") for l in lines), (code, lines)
assert not any(l.startswith("  LAND #") for l in lines), lines
# A self-check asked before survives a status whose row for it lacked a field: it is not asked again after.
d4 = program("flaky")
full_t = lambda run: [dict(taken), dict(done_a), dict(done_b)]
status("flaky", workers=full_t)
status("flaky", workers=lambda run: [{k: v for k, v in taken.items() if k != "workerState"}, dict(done_a), dict(done_b)])
lines, _ = status("flaky", workers=full_t)
assert any("self-check asked at" in l for l in lines) and not any("orca terminal send" in l for l in lines), lines
# A retained row without its reason is not called taken over; it holds no PR, so both PRs still land.
vague = {k: v for k, v in taken.items() if k != "resource"}
lines, _ = status("run-lead", workers=lambda run: [vague, dict(done_a), dict(done_b)])
assert not any("TAKEN OVER" in l for l in lines), lines
assert "  WARN status: worker row not judged (missing resource.retainedReason)" in lines, lines
assert any(l.startswith("  LAND #11") for l in lines) and any(l.startswith("  LAND #12") for l in lines), lines
# "resource": null is Orca's answer that the worker holds none: judged, not taken over, no WARN.
bare = dict(taken, resource=None)
lines, _ = status("run-lead", workers=lambda run: [bare, dict(done_a), dict(done_b)])
assert not any("TAKEN OVER" in l or "WARN" in l for l in lines), lines

# LANDED-BUT-OPEN: a card whose worker row has no terminalState is not called released, and no rm is offered.
LEDGER.append({"ts": "2026-11-01T03:00:00+00:00", "ev": "landed", "pr": 11, "ticket": "ABC-1", "sha": "m11"})
program("run-lead")
prog.run_tasks = lambda run: [{"id": "ta", "display_name": "ABC-1 a"}, {"id": "tb", "display_name": "ABC-2 b"}]
prog.orca_json = lambda *a: ({"worktrees": [{"id": "wa", "path": "/work/card-a"}]} if a[:2] == ("worktree", "list") else {})
lines, _ = status("run-lead", workers=lambda run: [dict(done_a), dict(done_b)])
assert "  LANDED-BUT-OPEN ABC-1 (dispatch ctx_a1): released, but its card is still there: `orca worktree rm --worktree path:/work/card-a`" in lines, lines
lines, _ = status("run-lead", workers=lambda run: [blind_a, dict(done_b)])
assert "  LANDED-BUT-OPEN ABC-1 (dispatch ctx_a1): worker state unknown (row below); release and remove nothing on this line" in lines, lines
assert not any("worktree rm" in l for l in lines), lines
assert "  WARN status: worker row not judged (missing terminalState)" in lines, lines
print("test_status_lines: ok")
