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


# term_me runs an orchestrator session: its handoff key comes from that role, not from a default.
roles.roles_dir().mkdir(parents=True, exist_ok=True)
(roles.roles_dir() / "s1.json").write_text('{"role": "orchestrator"}')
roles.record_terminal("term_me", "s1", "/w", 1.0)

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
roles.record_resolved(L, {"role": "lead", "slug": "app", "agent_type": "agent-skills:lead", "title_slug": "app"})
assert handoff.role_key(L) == "lead:app"
# resolve()'s order: an --agent role outranks a ROLE line declared later; `ROLE: none` outranks both.
(roles.roles_dir() / f"{L}.json").write_text('{"role": "orchestrator"}')
assert handoff.role_key(L) == "lead:app"
(roles.roles_dir() / f"{L}.json").write_text('{"role": "none"}')
assert handoff.role_key(L) is None
# An --agent lead started with no slug in its title takes the slug its ROLE line declares later, as resolve() does.
roles.record_resolved(L, {"role": "lead", "slug": None, "agent_type": "agent-skills:lead", "title_slug": None})
(roles.roles_dir() / f"{L}.json").write_text('{"role": "lead", "slug": "web"}')
assert handoff.role_key(L) == "lead:web"
assert roles.role_of(L) == roles.resolve({"agent_type": "agent-skills:lead"}, roles.declared(L))
roles.record_resolved(L, {"role": "lead", "slug": "app", "agent_type": "agent-skills:lead", "title_slug": "app"})
assert handoff.role_key(L) == "lead:app", "the title's slug wins, as in resolve()"
(roles.roles_dir() / f"{L}.json").unlink()
roles.record_resolved(L, {"role": "lead", "slug": "app", "agent_type": "agent-skills:lead"})
assert handoff.role_key(L) == "lead:app", "a record written before title_slug keeps its slug"
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
# Within the window the own auto row still counts although a manual save came after it: it stays the newest record.
assert handoff.ready(S, 160_000, False, peek=empty)["ready"]
assert store.last_session_event("orchestrator", "handoff")[1]["next"] == "land acme/app#2"
handoff.ready(S, 160_000, False, peek=empty, now=4_000_000_100)
saved = store.last_session_event("orchestrator", "handoff")[1]
assert saved["next"] == "land acme/app#2" and saved["reply"] == "chat" and saved["auto"], saved

# A manual save committed while the band was checking (inside peek) is what the auto row carries.
def peek_then_save(key, handle):
    store.session_event(key, "handoff", {"next": "raced manual", "waiting": "w1", "session": S})
    return []
handoff.ready(S, 161_000, False, peek=peek_then_save, now=4_000_001_000)
saved = store.last_session_event("orchestrator", "handoff")[1]
assert saved["next"] == "raced manual" and saved["waiting"] == "w1" and saved["auto"], saved

# Two terminals alternate: the second is refused (conflict) and writes nothing, however its session differs.
n = rows()
meta = lambda sess, h: {"auto": True, "session": sess, "handle": h, "context": 1}  # noqa: E731
K = "lead:iso"  # a key of its own, so the rows above (term_me, real clock) are not in the way
args = lambda h, sess: (K, meta(sess, h), h, sess, handoff.AUTO_EVERY_S, handoff.FIELDS, "nx")  # noqa: E731
assert store.save_auto_handoff(*args("term_a", "sa")) == ("saved", None)
assert store.save_auto_handoff(*args("term_b", "sb")) == ("conflict", "term_a") and rows() == n + 1
assert store.save_auto_handoff(*args("term_a", "sa")) == ("skipped", None) and rows() == n + 1
# A manual save from the other terminal in between does not hide the auto row.
store.session_event(K, "handoff", {"next": "m", "handle": "term_b", "session": "sb"})
assert store.save_auto_handoff(*args("term_b", "sb"))[0] == "conflict"
# The same terminal after a /clear (new session, same handle) passes; past the window the other terminal does too.
assert store.save_auto_handoff(*args("term_a", "sa2")) == ("saved", None)
# A caller with no handle is another terminal, not an exemption: it cannot hide term_a's save, nor be hidden by it.
assert store.save_auto_handoff(K, meta("sn", None), None, "sn", handoff.AUTO_EVERY_S, handoff.FIELDS, "nx") == ("conflict", "term_a")
assert store.save_auto_handoff(*args("term_a", "sa2")) == ("skipped", None)
assert store.save_auto_handoff(*args("term_b", "sb"), at=4_100_000_000) == ("saved", None)
# Through ready(): a handoff another terminal just saved for the orchestrator key stops the band's save.
assert store.save_auto_handoff("orchestrator", meta("sz", "term_z"), "term_z", "sz", handoff.AUTO_EVERY_S, handoff.FIELDS,
                               "nx", at=4_300_000_000) == ("saved", None)  # the earlier rows are old by this clock
n = rows()
got = handoff.ready(S, 150_000, False, peek=empty)
assert not got["ready"] and got["show"] and "term_z" in got["reason"] and rows() == n, got

# Two processes that read the same previous state: exactly one saves.
import multiprocessing as mp
def contend(q, bar, h):
    bar.wait()
    q.put(store.save_auto_handoff("lead:race", meta("s" + h, h), h, "s" + h, 600, handoff.FIELDS, "nx")[0])
ctx = mp.get_context("fork")
q, bar = ctx.Queue(), ctx.Barrier(2)
ps = [ctx.Process(target=contend, args=(q, bar, h)) for h in ("t1", "t2")]
[p.start() for p in ps]
[p.join(30) for p in ps]
assert sorted([q.get(timeout=5), q.get(timeout=5)]) == ["conflict", "saved"]

# ready: only an orchestrator session is shown; a lead or a role-less one is not, whatever its context.
for sid, rec in (("sid-lead", {"role": "lead", "slug": "app", "agent_type": "agent-skills:lead", "title_slug": "app"}),
                 ("sid-slugless", {"role": "lead", "slug": None, "agent_type": None})):
    roles.record_resolved(sid, rec)
    got = handoff.ready(sid, 400_000, False, peek=empty)
    assert got["show"] is False and got["ready"] is False, (sid, got)
assert handoff.ready("sid-unknown", 400_000, False, peek=empty)["show"] is False

# The verdict decides, not the context size: due at 40k shows (no floor), not due at 400k does not, and the reason is
# reported. The saved handoff is the same as when the ceiling made it due.
import compactions  # noqa: E402
seen = []
def verdict(session, context, policy):
    seen.append((session, context))
    return "3 auto-compactions"
got = handoff.ready(S, 40_000, False, peek=empty, verdict=verdict, now=4_400_000_000)
assert got["show"] and got["ready"] and got["due"] == "3 auto-compactions" and got["key"] == "orchestrator", got
assert seen == [(S, 40_000)] and "threshold" not in got
n = rows()
got = handoff.ready(S, 400_000, False, peek=empty, verdict=lambda *a: "", now=4_500_000_000)
assert got["show"] is False and got["due"] == "" and rows() == n, got
assert handoff.ready("sid-lead", 40_000, False, peek=empty, verdict=verdict)["show"] is False and len(seen) == 1
# The default verdict is compactions.due: with this config's ceiling (100k) and no transcript for the session.
assert handoff.ready(S, 99_000, False, peek=empty)["show"] is False
assert "ceiling 100k" in handoff.ready(S, 101_000, True, peek=empty)["due"]
# auto_clear_on: the ceiling's switch, or the compaction rules' own.
cfg = tmp / "agent-skills.json"
for text, want in (('{"orchestrate": {"auto_clear": true}}', True), ('{"orchestrate": {}}', False),
                   ('{"orchestrate": {"compact_restart": {"auto_clear": true}}}', True),
                   ('{"orchestrate": {"compact_restart": false}}', False)):
    cfg.write_text(text)
    assert handoff.auto_clear_on() is want, text
cfg.write_text('{"orchestrate": {"auto_clear": true, "restart_at": 100000}}')

# key_for and save: a lead session saves under lead:<slug>; the orchestrator key and others' keys are refused.
roles.record_terminal("term_lead", "sl", "/w", 1.0)
roles.record_resolved("sl", {"role": "lead", "slug": "app", "agent_type": "agent-skills:lead", "title_slug": "app"})
assert handoff.key_for("sl", None) == ("lead:app", "")
assert handoff.key_for("sl", "lead:app") == ("lead:app", "")
for bad in ("orchestrator", "lead:other", "lead:"):
    assert handoff.key_for("sl", bad)[1], bad
assert handoff.key_for("s1", "lead:app")[1] and handoff.key_for("s1", "orchestrator") == ("orchestrator", "")
assert handoff.key_for("nobody", None)[1], "a session with no role has no key"
assert handoff.key_for(None, None)[1] and handoff.key_for(None, "orchestrator")[1]
assert handoff.key_for(None, "lead:app") == ("lead:app", "") and handoff.key_for(None, "lead:a b")[1]

def count():
    return __import__("sqlite3").connect(store.path()).execute(
        "select count(*) from event where entity='session' and ev in ('handoff','restart')").fetchone()[0]
os.environ["ORCA_TERMINAL_HANDLE"] = "term_lead"
n = count()
assert run("save", "--next", "lead work") == (0, "handoff saved for lead:app\n")
assert store.last_session_event("lead:app", "handoff")[1]["next"] == "lead work" and count() == n + 1
assert "next: lead work" in run("show")[1] and "next: lead work" in run("show", "--key", "lead:app")[1]
assert "lead:app" in handoff.text("lead:app")
for argv in (("--key", "orchestrator"), ("--key", "lead:other")):
    code, _ = run("save", "--next", "bad", *argv)
    assert "refused" in str(code) and count() == n + 1, (argv, code)
(tmp / "agent-skills.json").write_text('{"orchestrate": {"auto_clear": true, "restart_at": 100000}}')
with inbox_stream.only_stream("term_lead"):
    code, _ = run("save", "--next", "bad", "--restart")
assert "--restart is for the orchestrator" in str(code) and count() == n + 1, code
# No known session: only a well-formed lead key passes, and nothing for the orchestrator.
os.environ["ORCA_TERMINAL_HANDLE"] = "term_unknown"
for argv in ((), ("--key", "orchestrator"), ("--key", "lead:")):
    code, _ = run("save", "--next", "bad", *argv)
    assert "Nothing saved" in str(code) and count() == n + 1, (argv, code)
assert run("save", "--next", "from outside", "--key", "lead:app")[0] == 0
# A role-less session (ROLE: none) saves nothing without --key, and nothing with one.
roles.record_terminal("term_none", "sn", "/w", 1.0)
os.environ["ORCA_TERMINAL_HANDLE"] = "term_none"
n = count()
for argv in ((), ("--key", "orchestrator")):
    assert "Nothing saved" in str(run("save", "--next", "bad", *argv)[0]) and count() == n
os.environ["ORCA_TERMINAL_HANDLE"] = "term_me"
print("test_handoff: ok")
