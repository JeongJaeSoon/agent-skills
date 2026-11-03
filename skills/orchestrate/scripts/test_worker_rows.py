"""Every reader of `orca orchestration worker-list` gets the same rows from an Orca that answers with the full record
(older Orca, which has no --full) and from Orca 1.4.246, whose default answer is a concise view without runId,
dispatchStatus, resource or projection, and without page.hasMore. Run: python3 test_worker_rows.py"""
import contextlib, copy, io, json, os, pathlib, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
DATA = {k: json.loads((HERE / "testdata" / f"worker-list-{k}.json").read_text()) for k in ("full", "concise")}
UNKNOWN_FULL = "error: invalid_argument: Unknown flag --full for command: orchestration worker-list"


def answer(version, argv):
    """`orca <argv> --json`'s result as an Orca of that version gives it; raises its error."""
    argv = [a for a in argv if a != "--json"]
    if argv[:2] == ["orchestration", "run-current"]:
        return {"run": None}
    if argv[:2] in (["orchestration", "task-list"], ["orchestration", "gate-list"]):
        return {"tasks": [], "gates": []}
    if argv[:2] == ["orchestration", "run-show"]:
        return {"run": {"id": "run_demo"}}
    assert argv[:2] == ["orchestration", "worker-list"], argv
    full = "--full" in argv
    if full and version == "old":
        raise RuntimeError(UNKNOWN_FULL)
    res = copy.deepcopy(DATA["full" if full or version == "old" else "concise"][1 if "--cursor" in argv else 0])
    if "--terminal-state" in argv:
        res["workers"] = [w for w in res["workers"] if w["terminalState"] == argv[argv.index("--terminal-state") + 1]]
    if "--run" not in argv:  # every Run: Orca names no Run, and the concise rows carry none
        res["scope"] = {"source": "all"}
    return res


if sys.argv[1:2] == ["--orca"]:  # this file as the `orca` binary, for the readers that run a subprocess
    try:
        print(json.dumps({"ok": True, "result": answer(os.environ["WORKER_ROWS_ORCA"], sys.argv[2:])}))
    except RuntimeError as e:
        print(json.dumps({"ok": False, "error": {"code": "invalid_argument", "message": str(e)}}))
        print(e, file=sys.stderr)
        sys.exit(1)
    sys.exit(0)

tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-worker-rows-"))
fake_orca = tmp / "orca"
fake_orca.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{pathlib.Path(__file__).resolve()}" --orca "$@"\n')
fake_orca.chmod(0o755)
os.environ.update(AGENT_SKILLS_STATE=str(tmp), ORCH_FLEET_STATE=str(tmp / "dashboard"), AGENT_SKILLS_PATROL="off",
                  PROGRAMS_HOME=str(tmp / "programs"), SKILLS_SYNC_ORCA=str(fake_orca))
(tmp / "dashboard").mkdir()
sys.path[:0] = [str(HERE), str(HERE.parents[1] / "reap-resources" / "scripts"), str(HERE.parents[2] / "scripts")]
import card, cardguard, dash, fleet, orca_settings, patrol, prog, reap  # noqa: E402


def results(version):
    """What each reader concludes from one Run (a lead, its units a/b/c, another lead's unit x) on that Orca."""
    os.environ["WORKER_ROWS_ORCA"] = version

    def fresh(fn, *a):
        """fn meets the Orca anew, so an older Orca's refusal of --full comes through fn's own error path."""
        if not hasattr(orca_settings, "FULL"):
            return fn(*a)
        orca_settings.FULL[:] = ["--full"]
        got = fn(*a)
        assert orca_settings.FULL == ([] if version == "old" else ["--full"]), (fn, version)
        return got

    def call(*a):
        try:
            return answer(version, list(a))
        except RuntimeError as e:
            sys.exit(f"orca {' '.join(a[:2])}: {e}")  # prog.orca_json's failure
    prog.orca_json = call
    fleet.orca = lambda *a, timeout=30: answer(version, list(a))
    dash.orca_result = lambda *a: answer(version, ["orchestration", *a])
    dash.sh = lambda cmd, timeout=45: {"result": {"worktrees": []}}
    cardguard._orca_every_run = lambda args: answer(version, list(args))
    cardguard._orca_as_seen = lambda *a: answer(version, list(a))

    out = {}
    rows = fresh(prog.all_workers, "run_demo")
    out["all_workers"] = [(w.get("dispatchId"), w.get("runId"), w.get("workerState"), w.get("dispatchStatus"),
                           w.get("agentTerminalHandle"), (w.get("resource") or {}).get("worktreeId")) for w in rows]
    out["own_worker"] = (prog.own_worker(rows, "term_a") or {}).get("dispatchId")
    scoped = prog.lead_scope(rows, "ctx_lead")
    out["lead_scope"] = [w["dispatchId"] for w in scoped]
    out["in_flight"] = [w["dispatchId"] for w in scoped if (w.get("projection") or {}).get("outcome") == "in_progress"]
    out["next_moves"] = prog.next_moves(scoped)
    wts = [{"id": f"repo-1::/work/acme/card-{c}", "path": f"/work/acme/card-{c}"} for c in "abc"]
    out["sweep"] = prog.settle_sweep(scoped, wts, holds=lambda p: None, asked={}, now="T0")
    out["close_out"] = prog.close_out([{"type": "worker_done", "payload": {"dispatchId": "ctx_b", "outcome": "succeeded"}}],
                                      scoped, wts)

    # orch init: the terminal of a listed Dispatch is a dispatched lead, and its program is scoped to that Dispatch.
    os.environ["ORCA_TERMINAL_HANDLE"] = "term_lead"
    prog.skills_version = lambda root: "abc1234"
    prog.dashboard_lines = lambda slug: []
    slug = f"init-{version}"
    with contextlib.redirect_stdout(io.StringIO()):
        fresh(prog.cmd_init, [slug, "--repo", "acme/app", "--run", "run_demo"])
    out["init_lead"] = json.loads((prog.HOME / slug / "program.json").read_text()).get("lead_dispatch")

    # A card started from that lead goes to the Run of its own Dispatch (orca_settings.own_dispatch, a subprocess).
    out["own_dispatch"] = (fresh(orca_settings.own_dispatch, str(fake_orca)) or {}).get("runId")
    try:
        out["card_supervisor"] = fresh(card.supervisor, {"run_demo"})
    except (SystemExit, card.Refuse) as e:
        out["card_supervisor"] = f"refused: {str(e)[:80]}"

    # patrol reads every Run through cardguard; its X rule is Orca's own exited verdict on a running worker.
    every = fresh(patrol.all_workers, None)
    found, _, _ = patrol.judge({"now": 1_800_000_000.0, "terminals": [], "waits": {}, "workers": every, "sessions": [],
                                "prs": {}, "board": {}, "state": {}, "supersedes": [], "level": "ok", "consumers": [],
                                "tails": {}, "screens": {}, "watchers": {}}, {})
    out["patrol"] = sorted((f["code"], f["card"], f["handle"]) for f in found)
    out["reap"] = reap.worker_worktrees(fresh(reap.orca_workers))

    # fleet: dispatch_status and the Run's settledness; dash: the program's workers.
    got = fresh(fleet.fetch_run, {"id": "run_demo"}, True)
    out["fleet"] = ([(w.get("dispatchId"), w.get("runId"), w.get("dispatchStatus")) for w in got["workers"]],
                    fleet.settled_run(got))
    out["dash"] = [w.get("dispatchId") for w in fresh(dash.fetch_orca, {"run": "run_demo", "lead_dispatch": "ctx_lead"})["workers"]]
    return out


old = results("old")
assert old["all_workers"][0] == ("ctx_lead", "run_demo", "ready", "dispatched", "term_lead", "repo-1::/work/acme/lead"), old
assert [r[0] for r in old["all_workers"]] == ["ctx_lead", "ctx_a", "ctx_b", "ctx_c", "ctx_x"], "both pages"
assert old["own_worker"] == "ctx_a" and old["lead_scope"] == ["ctx_a", "ctx_b", "ctx_c"], old
assert old["in_flight"] == ["ctx_a"], old
assert old["next_moves"] == ["  ctx_a no terminal: worker-stop or worker-abandon (recovery-and-cleanup.md)"], old
assert old["sweep"][0] == "SETTLED ctx_b (succeeded): release it; no open PR, nothing unpushed", old
assert old["sweep"][2].startswith("TAKEN OVER ctx_c (succeeded)") and "--terminal term_c" in old["sweep"][3], old
assert old["close_out"][0].startswith("CLOSE OUT ctx_b: release it, close its terminals"), old
assert old["init_lead"] == "ctx_lead" and old["own_dispatch"] == "run_demo", old
assert old["card_supervisor"] == {"id": "run_demo", "via": "own_dispatch"}, old
assert old["patrol"] == [("X", "card-a", "term_a")], old
assert old["reap"][0] == {"/work/acme/card-a": "살아 있는 워커 턴(ctx_a)", "/work/acme/lead": "살아 있는 워커 턴(ctx_lead)",
                          "/work/acme/other": "살아 있는 워커 턴(ctx_x)"} and old["reap"][2] == {"/work/acme/card-c"}, old
assert old["fleet"] == ([("ctx_lead", "run_demo", "dispatched"), ("ctx_a", "run_demo", "dispatched"),
                         ("ctx_b", "run_demo", "completed"), ("ctx_c", "run_demo", "completed"),
                         ("ctx_x", "run_demo", "dispatched")], False), old
assert old["dash"] == ["ctx_a", "ctx_b", "ctx_c"], old

new = results("new")
differ = [k for k in old if new[k] != old[k]]
assert not differ, "\n".join(f"{k}: {new[k]!r}" for k in differ)

# A concise row alone (an Orca that gave no full record) still reads as far as it goes, in the full record's names.
row = orca_settings.worker_row(DATA["concise"][0]["workers"][1], run="run_demo")
assert (row["runId"], row["workerState"], row["agentTerminalHandle"], row["resource"]["worktreeId"],
        row["projection"]["outcome"], row["projection"]["liveness"]["verdict"]) == \
    ("run_demo", "ready", "term_a", "repo-1::/work/acme/card-a", "in_progress", "exited"), row
full_row = DATA["full"][0]["workers"][1]
assert orca_settings.worker_row(full_row) == full_row, "a full record is left as it is"
print("test_worker_rows: ok")
