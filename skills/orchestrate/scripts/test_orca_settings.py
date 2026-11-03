"""orca_settings against a throwaway Orca profile store. Run: python3 test_orca_settings.py"""
import contextlib, io, json, pathlib, sqlite3, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import orca_settings as s  # noqa: E402

base = pathlib.Path(tempfile.mkdtemp(prefix="test-orca-settings-"))


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
print("test_orca_settings: ok")
