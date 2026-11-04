"""Every reader of `orca orchestration worker-list` and `run-list` gets the same rows from an Orca that answers with the
full record (older Orca, which has no --full) and from Orca 1.4.246, whose default answer is a concise view: worker
rows without runId, dispatchStatus, resource or projection, Runs without coordinator_handle or legacy, and no
page.hasMore. Each reader runs its own `orca` adapter against this file standing in as the `orca` binary on PATH.
Run: python3 test_worker_rows.py"""
import atexit, contextlib, copy, io, json, os, pathlib, shutil, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
DATA = {k: json.loads((HERE / "testdata" / f"worker-list-{k}.json").read_text()) for k in ("full", "concise")}
RUNS = {k: json.loads((HERE / "testdata" / f"run-list-{k}.json").read_text()) for k in ("full", "concise")}
UNKNOWN_FULL = "error: invalid_argument: Unknown flag --full for command: orchestration worker-list"


def answer(version, argv):
    """`orca <argv> --json`'s result as an Orca of that version gives it; raises its error."""
    argv = [a for a in argv if a != "--json"]
    if argv[:2] == ["worktree", "list"]:
        return {"worktrees": []}
    if argv[:2] == ["terminal", "list"]:
        return {"terminals": [{"handle": "term_coord"}]}
    if argv[:2] == ["orchestration", "run-list"]:
        full = "--full" in argv
        if full and version.startswith("old"):
            raise RuntimeError(UNKNOWN_FULL.replace("worker-list", "run-list"))
        return copy.deepcopy(RUNS["full" if full or version.startswith("old") else "concise"][1 if "--cursor" in argv else 0])
    if argv[:2] == ["orchestration", "run-current"]:
        return {"run": None}
    if argv[:2] in (["orchestration", "task-list"], ["orchestration", "gate-list"]):
        return {"tasks": [], "gates": []}
    if argv[:2] == ["orchestration", "run-show"]:
        return {"run": {"id": "run_demo"}}
    assert argv[:2] == ["orchestration", "worker-list"], argv
    full = "--full" in argv
    if full and version.startswith("old"):
        raise RuntimeError(UNKNOWN_FULL)
    res = copy.deepcopy(DATA["full" if full or version.startswith("old") else "concise"][1 if "--cursor" in argv else 0])
    if "--terminal-state" in argv:
        res["workers"] = [w for w in res["workers"] if w["terminalState"] == argv[argv.index("--terminal-state") + 1]]
    if "--run" not in argv:  # every Run: Orca names no Run, and the concise rows carry none
        res["scope"] = {"source": "all"}
    return res


if sys.argv[1:2] == ["--orca"]:  # this file as the `orca` binary
    version = os.environ["WORKER_ROWS_ORCA"]
    try:
        print(json.dumps({"ok": True, "result": answer(version, sys.argv[2:])}))
    except RuntimeError as e:
        if version != "old-stderr":  # an Orca that says why on stderr alone
            print(json.dumps({"ok": False, "error": {"code": "invalid_argument", "message": str(e)}}))
        if version != "old-stdout":  # Orca 1.4.240 says why on stdout alone, stderr empty
            print(e, file=sys.stderr)
        sys.exit(1)
    sys.exit(0)

tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-worker-rows-"))
atexit.register(shutil.rmtree, tmp, True)
fake_orca = tmp / "orca"
fake_orca.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{pathlib.Path(__file__).resolve()}" --orca "$@"\n')
fake_orca.chmod(0o755)
os.environ.update(AGENT_SKILLS_STATE=str(tmp), ORCH_FLEET_STATE=str(tmp / "dashboard"), AGENT_SKILLS_PATROL="off",
                  PROGRAMS_HOME=str(tmp / "programs"), SKILLS_SYNC_ORCA=str(fake_orca),
                  PATH=f"{tmp}{os.pathsep}{os.environ['PATH']}")
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
        assert orca_settings.FULL == ([] if version.startswith("old") else ["--full"]), (fn, version)
        return got

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
    fleet.RUN_CACHE.clear()
    runs = fresh(fleet.fetch_runs)
    out["fleet_runs"] = ([(r["id"], r.get("coordinator_handle")) for r in runs["runs"]],
                         sorted({w.get("runId") for w in runs["workers"]}))
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
assert old["close_out"][0].startswith("CLOSE OUT ctx_b: release it and close its agent terminal"), old
assert old["init_lead"] == "ctx_lead" and old["own_dispatch"] == "run_demo", old
assert old["card_supervisor"] == {"id": "run_demo", "via": "own_dispatch"}, old
assert old["patrol"] == [("X", "card-a", "term_a")], old
assert old["reap"][0] == {"/work/acme/card-a": "살아 있는 워커 턴(ctx_a)", "/work/acme/lead": "살아 있는 워커 턴(ctx_lead)",
                          "/work/acme/other": "살아 있는 워커 턴(ctx_x)"} and old["reap"][2] == {"/work/acme/card-c"}, old
assert old["fleet"] == ([("ctx_lead", "run_demo", "dispatched"), ("ctx_a", "run_demo", "dispatched"),
                         ("ctx_b", "run_demo", "completed"), ("ctx_c", "run_demo", "completed"),
                         ("ctx_x", "run_demo", "dispatched")], False), old
assert old["dash"] == ["ctx_a", "ctx_b", "ctx_c"], old
# run-list's first page only, as before; the legacy Run is left out and run_demo keeps its coordinator.
assert old["fleet_runs"] == ([("run_demo", "term_coord")], ["run_demo"]), old

for version in ("old-stderr", "old-stdout", "new"):
    got = results(version)
    differ = [k for k in old if got[k] != old[k]]
    assert not differ, version + "\n" + "\n".join(f"{k}: {got[k]!r}" for k in differ)

# The concise view's own paging: a top-level nextCursor, no page object, none on the last page.
orca_settings.FULL.clear()
assert [(w["dispatchId"], w["runId"]) for w in orca_settings.all_workers(lambda *a: answer("new", list(a)), "--run", "r")] \
    == [(d, "run_demo") for d in ("ctx_lead", "ctx_a", "ctx_b", "ctx_c", "ctx_x")]
# Another thread emptied FULL while this call was out: its own refusal of --full is still asked again without it.
orca_settings.FULL[:] = ["--full"]


def racing(*a):
    if "--full" in a:
        orca_settings.FULL.clear()
        raise RuntimeError(UNKNOWN_FULL)
    return {"workers": [{"dispatchId": "d"}]}


assert orca_settings.all_workers(racing) == [{"dispatchId": "d"}]
# More pages and no cursor fail with the caller's own error type, so its handler keeps the source as failed.
for err in (RuntimeError, fleet.SourceError, dash.SourceError, SystemExit):
    try:
        orca_settings.all_workers(lambda *a: {"workers": [], "page": {"hasMore": True}}, error=err)
        raise AssertionError("a list cut short is not a smaller list")
    except err:
        pass

# A concise row alone (an Orca that gave no full record) still reads as far as it goes, in the full record's names.
row = orca_settings.worker_row(DATA["concise"][0]["workers"][1], run="run_demo")
assert (row["runId"], row["workerState"], row["agentTerminalHandle"], row["resource"]["worktreeId"],
        row["projection"]["outcome"], row["projection"]["liveness"]["verdict"]) == \
    ("run_demo", "ready", "term_a", "repo-1::/work/acme/card-a", "in_progress", "exited"), row
full_row = DATA["full"][0]["workers"][1]
assert orca_settings.worker_row(full_row) == full_row, "a full record is left as it is"


# orch wait on a lead's mailbox. A consuming check hands its batch over once. Orca 1.4.246's concise answer
# carries no deliveryId or payload; with --full it has both. An older Orca refuses --full and answers in full.
def mailbox(version):
    done = {"id": "msg_done", "type": "worker_done", "from_handle": "term_b", "subject": "READY", "body": "done",
            "payload": json.dumps({"dispatchId": "ctx_b", "outcome": "succeeded"})}
    queue, acks = [[done]], []

    def orca_json(*a):
        a = list(a)
        if a[:2] == ["orchestration", "check"]:
            full = "--full" in a
            if full and version == "old":
                sys.exit(f"orca orchestration check: {UNKNOWN_FULL.replace('worker-list', 'check')}")
            if "--ack" in a:
                acks.append(a[a.index("--ack") + 1])
                return {}
            msgs = queue.pop(0) if queue else []
            if full or version == "old":
                return {"deliveryId": "delivery_1" if msgs else None, "messages": msgs, "count": len(msgs)}
            return {"messages": [{"id": m["id"], "type": m["type"], "from": m["from_handle"], "subject": m["subject"],
                                  "body": m["body"]} for m in msgs]}
        if a[:2] == ["worktree", "list"]:
            return {"worktrees": [{"id": "repo-1::/work/acme/card-b", "path": "/work/acme/card-b"}]}
        if a[:2] == ["terminal", "show"]:
            return {"terminal": {"worktreeId": "repo-1::/work/acme/lead"}}
        return answer("old" if version == "old" else "new", a)
    return orca_json, acks


prog.ensure_dash = lambda: None
prog.watch_lines = lambda *a: []
prog.print_sweep = lambda *a, **k: None
prog.print_stuck = lambda *a, **k: False
prog.print_reviews = lambda p: None
os.environ["ORCA_TERMINAL_HANDLE"] = "term_lead"
for version in ("old", "new"):
    orca_settings.FULL[:] = ["--full"]
    prog.orca_json, acks = mailbox(version)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        prog.cmd_wait([f"init-{version}", "--rounds", "1", "--timeout-ms", "10"])
    text = out.getvalue()
    assert "INBOX id=msg_done type=worker_done from=term_b" in text and '"dispatchId": "ctx_b"' in text, (version, text)
    assert "CLOSE OUT ctx_b: release it" in text and "ACTIONABLE delivery=delivery_1 (1 of 1)" in text, (version, text)
    assert "nothing actionable" not in text, (version, text)
print("test_worker_rows: ok")
