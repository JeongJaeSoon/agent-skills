"""handoff.py (`orch handoff`) on an empty agent-skills.db in a temporary state directory; Orca is never called.

Run: python3 test_handoff.py
"""
import contextlib, io, os, pathlib, sys, tempfile

tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-handoff-"))
os.environ.update({"ORCH_FLEET_STATE": str(tmp / "dash"), "ORCH_FLEET_CONFIG": str(tmp / "none.json"),
                   "AGENT_SKILLS_CONFIG": str(tmp / "agent-skills.json"), "AGENT_SKILLS_STATE": str(tmp / "state"),
                   "ORCA_TERMINAL_HANDLE": "term_me", "TZ": "UTC"})
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import handoff, inbox_stream, roles, store  # noqa: E402

handoff.bound_run = lambda: "run_1"


def run(*argv):
    out = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out):
        try:
            handoff.main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue()


code, _ = run("save", "--next", "x")
assert "handoff not stored" in str(code), code
assert handoff.text("orchestrator") == ""
store.init_empty()

assert run("show") == (0, "no handoff recorded for orchestrator\n")
code, _ = run("save")
assert "needs --next" in str(code), code
assert run("save", "--next", "land acme/app#1 after CI", "--waiting", "dispatch d1", "--reply", "chat") == (0, "handoff saved for orchestrator\n")
code, out = run("show")
assert code == 0 and "  next: land acme/app#1 after CI" in out and "  waiting: dispatch d1" in out and "run: run_1" in out, out
text =handoff.text("orchestrator")
assert text.startswith("The previous session left this handoff") and "land acme/app#1" in text, text
assert handoff.text("lead:other") == ""

before = store.last_session_event("orchestrator", "handoff")
with inbox_stream.only_stream("term_me"):
    code, _ = run("save", "--next", "z", "--restart")
assert "restart off" in str(code) and store.last_session_event("orchestrator", "handoff") == before, code
(tmp / "agent-skills.json").write_text('{"orchestrate": {"auto_clear": true, "restart_at": 100000}}')

code, _ = run("save", "--next", "y", "--restart")
assert "no `orch inbox --stream` runs" in str(code) and store.last_session_event("orchestrator", "handoff") == before, code
roles.record_terminal("term_me", "s1", "/w", 1.0)
with inbox_stream.only_stream("term_me"):
    code, out = run("save", "--next", "y", "--restart")
assert code == 0 and "restart requested" in out, (code, out)
ts, req = store.last_session_event("orchestrator", "restart")
assert req == {"stage": "requested", "handle": "term_me", "session": "s1"}, req
assert "restart " in run("show")[1]

assert store.last_session_event("orchestrator", "handoff")[1]["session"] == "s1"

S = "00000000-0000-4000-8000-000000000001"
empty = lambda key, handle: []  # noqa: E731
assert handoff.ready(S, 150_000, False, peek=empty)["show"] is False
(roles.roles_dir()).mkdir(parents=True, exist_ok=True)
(roles.roles_dir() / f"{S}.json").write_text('{"role": "orchestrator"}')
assert handoff.ready(S, 90_000, False, peek=empty)["show"] is False
got = handoff.ready(S, 150_000, True, peek=empty)
assert got["show"] and not got["ready"] and "question" in got["reason"], got
got = handoff.ready(S, 150_000, False, peek=lambda k, h: [{"id": "m", "type": "status"}])
assert not got["ready"] and "1 message" in got["reason"], got
# Mail the stream took but holds in its debounce window is not in the inbox any more, and still not safe to drop.
hold_handle = os.environ.get("ORCA_TERMINAL_HANDLE") or handoff.terminal_of(S)
inbox_stream.hold(hold_handle, 60)
got = handoff.ready(S, 150_000, False, peek=empty)
assert not got["ready"] and "holds mail" in got["reason"], got
inbox_stream.hold(hold_handle, 0)
# A session with no ROLE record but a role SessionStart resolved (an --agent lead) still gets a key.
L = "00000000-0000-4000-8000-000000000002"
roles.record_resolved(L, {"role": "lead", "slug": "app", "agent_type": "agent-skills:lead"})
assert handoff.role_key(L) == "lead:app"
# resolve()'s order: an --agent role outranks a ROLE line declared later; `ROLE: none` outranks both.
(roles.roles_dir() / f"{L}.json").write_text('{"role": "orchestrator"}')
assert handoff.role_key(L) == "lead:app"
(roles.roles_dir() / f"{L}.json").write_text('{"role": "none"}')
assert handoff.role_key(L) is None
# A title role (no agent_type) gives way to a declared orchestrator or lead.
roles.record_resolved(L, {"role": "lead", "slug": "app", "agent_type": None})
(roles.roles_dir() / f"{L}.json").write_text('{"role": "orchestrator"}')
assert handoff.role_key(L) == "orchestrator"
roles.record_resolved(L, None)
assert not roles.resolved_file(L).exists()
before =store.last_session_event("orchestrator", "handoff")
got = handoff.ready(S, 150_000, False, peek=empty)
ts, saved = store.last_session_event("orchestrator", "handoff")
assert got["ready"] and saved["auto"] and saved["session"] == S and saved["context"] == 150_000, (got, saved)
assert saved["next"] == "y", saved
assert not handoff.ready(S, 150_000, False, peek=empty, agents=1)["ready"]
assert "saved by the clear band" in run("show")[1]
rows = lambda: __import__("sqlite3").connect(store.path()).execute("select count(*) from event where entity='session'").fetchone()[0]  # noqa: E731
n = rows()
assert handoff.ready(S, 152_000, False, peek=empty)["ready"] and rows() == n
assert handoff.ready(S, 152_000, False, peek=empty, now=4_000_000_000)["ready"] and rows() == n + 1
store.session_event("orchestrator", "handoff", {"next": "land acme/app#2", "reply": "chat", "session": S})
handoff.ready(S, 160_000, False, peek=empty)
saved = store.last_session_event("orchestrator", "handoff")[1]
assert saved["next"] == "land acme/app#2" and saved["reply"] == "chat" and saved["auto"], saved
print("test_handoff: ok")
