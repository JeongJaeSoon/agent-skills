"""orca_settings against a throwaway Orca profile store. Run: python3 test_orca_settings.py"""
import contextlib, io, json, os, pathlib, sqlite3, subprocess, sys, tempfile, types
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import orca_settings as s  # noqa: E402

base = pathlib.Path(tempfile.mkdtemp(prefix="test-orca-settings-"))
real_binding = s.binding


def store(settings, profile="local-default", index=True):
    db = base / "profiles" / profile / "profile-state.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
    index_file = base / "orca-profile-index.json"
    if index:
        index_file.write_text(json.dumps({"activeProfileId": profile}))
    elif index_file.exists():
        index_file.unlink()  # no index: the profile is local-default
    con = sqlite3.connect(db)
    con.execute("create table profile_state_documents (domain text primary key, payload text)")
    con.execute("insert into profile_state_documents values ('ui', '{}')")
    if settings is not None:
        con.execute("insert into profile_state_documents values ('settings', ?)", (json.dumps(settings),))
    con.commit()
    con.close()
    return db


# The owner's store: depth 3 fits orchestrator → lead → worker → helper with the lead as a dispatched worker.
db = store({"nestedWorkerMaxDepth": 3, "orchestrationHeartbeatsEnabled": True, "orchestrationHeartbeatIntervalMinutes": 5,
            "orchestrationChatErrorEscalationEnabled": True, "theme": "dark"}, profile="p1")
before = db.read_bytes()
got, why = s.read(base)
assert why is None and got == {"nestedWorkerMaxDepth": 3, "orchestrationHeartbeatsEnabled": True,
                               "orchestrationHeartbeatIntervalMinutes": 5, "orchestrationChatErrorEscalationEnabled": True}, got
assert s.lead_as_worker(got) and s.suggestions(got) == []
assert db.read_bytes() == before  # opened read-only

# Keys the store lacks are Orca's defaults: depth 1, which also keeps a card lead's workers from starting helpers.
store({}, index=False)
got, why = s.read(base)
assert why is None and got["nestedWorkerMaxDepth"] == 1 and not s.lead_as_worker(got)
(line,) = s.suggestions(got)
assert "Nested worker depth" in line and "3으로" in line and "지금 1" in line and "보조 워커를 띄우지 못한다" in line, line
# Depth 2: the lead stays a card; the line says what 3 would change.
(line,) = s.suggestions({**got, "nestedWorkerMaxDepth": 2})
assert "지금 2: 리드를 /goal 카드로 띄운다." in line and "dispatched worker" in line, line
# Heartbeats and chat error escalation turned off each get their own line; a stored string depth still counts.
lines = s.suggestions({"nestedWorkerMaxDepth": "4", "orchestrationHeartbeatsEnabled": False,
                       "orchestrationChatErrorEscalationEnabled": False})
assert len(lines) == 2 and "하트비트" in lines[0] and "채팅 오류" in lines[1], lines

# No store, no settings document, a payload that is not an object: never a guess that the lead fits.
assert s.read(base / "absent")[0] is None and "no Orca profile store" in s.read(base / "absent")[1]
store(None)
assert s.read(base) == ({k: d for k, d in s.DEFAULTS.items()}, None)
db = base / "profiles" / "local-default" / "profile-state.db"
con = sqlite3.connect(db)
con.execute("insert into profile_state_documents values ('settings', '[1]')")
con.commit()
con.close()
got, why = s.read(base)
assert got is None and "not an object" in why and not s.lead_as_worker(got)
assert s.report(None, why, None)["suggestions"] == []

# orch doctor's text: the unbound terminal line names the restart failure and the fix.
store({"nestedWorkerMaxDepth": 2})
s.data_dir, s.binding, s.own_dispatch = (lambda: base), (lambda: None), (lambda: None)
out = io.StringIO()
with contextlib.redirect_stdout(out):
    s.main([])
text = out.getvalue()
assert "리드: /goal 카드" in text and "제안: Orca 설정 > Orchestration > Nested worker depth" in text, text
assert "Run에 묶여 있지 않다" in text and "terminal_not_found" in text and "run-use" in text, text
out = io.StringIO()
with contextlib.redirect_stdout(out):
    s.main(["--json"])
r = json.loads(out.getvalue())
assert r["lead_as_worker"] is False and r["run"] is None and len(r["suggestions"]) == 1, r
# A lead started with worker-start has no Run of its own: doctor names the Run it works for and its Dispatch.
s.own_dispatch = lambda: {"runId": "run_top", "dispatchId": "ctx_lead"}
out = io.StringIO()
with contextlib.redirect_stdout(out):
    s.main([])
assert "Run run_top 의 워커" in out.getvalue() and "dispatch:ctx_lead" in out.getvalue(), out.getvalue()
assert "묶여 있지 않다" not in out.getvalue()
# A worker lead owns only the tasks its units run; a coordinating lead owns the whole Run.
tasks = [{"id": "t1"}, {"id": "tx"}]
assert s.scope_tasks(tasks, [{"taskId": "t1"}], "ctx_L") == [{"id": "t1"}] and s.scope_tasks(tasks, [], None) == tasks
# A malformed profile index falls back to the default profile instead of crashing.
with tempfile.TemporaryDirectory() as tmp:
    (pathlib.Path(tmp) / "orca-profile-index.json").write_text("[]")
    assert s.read(pathlib.Path(tmp))[0] is None
# A worker row names its agent's terminal as `terminal` (newer Orca) or `agentTerminalHandle` (older Orca).
assert s.worker_terminal({"dispatchId": "d1", "terminal": "term_new"}) == "term_new"
assert s.worker_terminal({"dispatchId": "d1", "agentTerminalHandle": "term_old"}) == "term_old"
assert s.worker_terminal({"agentTerminalHandle": "term_old", "terminal": "term_new"}) == "term_old"
assert s.worker_terminal({"agentTerminalHandle": None, "terminal": "term_new"}) == "term_new"
assert s.worker_terminal({"dispatchId": "d1"}) is None and s.worker_terminal({}) is None
assert s.worker_terminal({"terminal": {"worktreeId": "x"}}) is None  # `terminal show`'s object is no handle


def answer(result):
    return 0, json.dumps({"ok": True, "result": result}), ""


def bound(full, plain=None):
    """binding() against an Orca giving `full` to run-current --full and `plain` without it: (binding, calls, stderr)."""
    calls = []

    def fake(cmd, **_):
        calls.append(cmd)
        rc, out, err = full if "--full" in cmd else plain
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)
    s.FULL[:] = ["--full"]
    err = io.StringIO()
    with mock.patch.object(s.subprocess, "run", fake), contextlib.redirect_stderr(err), \
            mock.patch.dict(os.environ, ORCA_TERMINAL_HANDLE="term_me"):
        return real_binding(), calls, err.getvalue()


FULL_RUN, CONCISE_RUN = {"run": {"id": "run_x", "objective": "ship"}}, {"runId": "run_x"}
REFUSED = "error: invalid_argument: Unknown flag --full for command: orchestration run-current"
# Both views count, and the full record comes back whole.
got, calls, err = bound(answer(FULL_RUN))
assert got == FULL_RUN["run"] and len(calls) == 1 and calls[0][1:] == ["orchestration", "run-current", "--full", "--json"], calls
assert not err, err
got, calls, err = bound(answer(CONCISE_RUN))
assert got == {"id": "run_x"} and not err, (got, err)
# An Orca from before --full is asked once more without it, whichever stream says why.
for refusal in ((1, "", REFUSED), (1, json.dumps({"ok": False, "error": {"message": REFUSED}}), "")):
    got, calls, err = bound(refusal, answer(FULL_RUN))
    assert got == FULL_RUN["run"] and ["--full" in c for c in calls] == [True, False] and not err, (got, calls, err)
    assert s.FULL == []
# Null is Orca saying unbound. A result naming neither key is unbound too, with one line saying the shape was not read.
for result in ({"run": None}, {"runId": None}):
    got, calls, err = bound(answer(result))
    assert got is None and len(calls) == 1 and not err, (result, got, err)
got, calls, err = bound(answer({"bindings": []}))
assert got is None and len(err.splitlines()) == 1 and "neither" in err and "bindings" in err, (got, err)
# Only the refusal of --full is asked again: any other failure is "unknown", never "unbound".
for broken in ((1, "", "connect ECONNREFUSED"), (0, "not json", ""), (0, "[]", "")):
    got, calls, err = bound(broken)
    assert got == "unknown" and len(calls) == 1, (broken, got, calls)
got, calls, _ = bound((1, "", REFUSED), (1, "", "connect ECONNREFUSED"))
assert got == "unknown" and len(calls) == 2, (got, calls)
with mock.patch.object(s.subprocess, "run", side_effect=subprocess.TimeoutExpired("orca", 30)), \
        mock.patch.dict(os.environ, ORCA_TERMINAL_HANDLE="term_me"):
    assert real_binding() == "unknown"
with mock.patch.dict(os.environ):
    os.environ.pop("ORCA_TERMINAL_HANDLE", None)
    assert real_binding() == "unknown"
assert s.run_of({"run": "run_x"}) == {"id": "run_x"}  # an id in `run`, which reboot has always read
# run_current passes its arguments on and reads the answer the same way, through the caller's own Orca adapter.
s.FULL[:] = ["--full"]
seen = []
assert s.run_current(lambda *a: seen.append(a) or CONCISE_RUN, "--from", "term_me") == {"id": "run_x"}
assert seen == [("orchestration", "run-current", "--from", "term_me", "--full")], seen
print("test_orca_settings: ok")
