"""dispatch.py: one process per event runs the matching hook modules and merges their output.
Run: python3 hooks/test_dispatch.py"""
import json, os, pathlib, subprocess, sys, tempfile, textwrap

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(tempfile.mkdtemp())
FAKES = TMP / "fakes"
FAKES.mkdir()
for var in [v for v in os.environ if v.startswith(("ORCA_", "CLAUDE_", "AGENT_SKILLS_"))]:
    del os.environ[var]  # the subprocesses below get a clean environment; so does this process
os.environ.update(HOME=str(TMP), AGENT_SKILLS_STATE=str(TMP / "state"), AGENT_SKILLS_CONFIG=str(TMP / "config.json"))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(FAKES))
import dispatch  # noqa: E402


def fake(name, body):
    (FAKES / f"{name}.py").write_text(textwrap.dedent(body))


def pre(decision=None, reason=None, system=None, context=None):
    spec = {"hookEventName": "PreToolUse"}
    if decision:
        spec.update(permissionDecision=decision, permissionDecisionReason=reason)
    if context:
        spec["additionalContext"] = context
    out = {"hookSpecificOutput": spec}
    if system:
        out["systemMessage"] = system
    return out


# ---- merge: Pre is deny > ask > allow, every deny/ask reason kept, messages joined without repeats
m = dispatch.merge("PreToolUse", [("a", pre("ask", "ask-a")), ("b", pre("deny", "deny-b")), ("c", pre("allow", "allow-c"))])
spec = m["hookSpecificOutput"]
assert spec["permissionDecision"] == "deny" and spec["permissionDecisionReason"] == "deny-b\nask-a", m
assert spec["hookEventName"] == "PreToolUse"
m = dispatch.merge("PreToolUse", [("a", pre("allow", "allow-a")), ("b", pre("ask", "ask-b")), ("c", pre("ask", "ask-c"))])
assert m["hookSpecificOutput"]["permissionDecision"] == "ask", m
assert m["hookSpecificOutput"]["permissionDecisionReason"] == "ask-b\nask-c", m
m = dispatch.merge("PreToolUse", [("a", pre("allow", "allow-a")), ("b", pre(system="s", context="s"))])
assert m["hookSpecificOutput"]["permissionDecision"] == "allow" and m["hookSpecificOutput"]["permissionDecisionReason"] == "allow-a"
m = dispatch.merge("PreToolUse", [("a", pre(system="one", context="ctx-1")), ("b", pre(system="two\nthree", context="ctx-2")),
                                  ("c", pre(system="one", context="ctx-1"))])
assert m["systemMessage"] == "one\ntwo\nthree" and m["hookSpecificOutput"]["additionalContext"] == "ctx-1\nctx-2", m
assert "permissionDecision" not in m["hookSpecificOutput"], m
assert dispatch.merge("PreToolUse", [("a", None), ("b", None)]) is None
assert dispatch.merge("PreToolUse", []) is None

# ---- merge: Stop and SubagentStop block when any module blocks, and keep every reason
for ev in ("Stop", "SubagentStop"):
    m = dispatch.merge(ev, [("a", {"systemMessage": "note"}), ("b", {"decision": "block", "reason": "why-b"}),
                            ("c", {"decision": "block", "reason": "why-c"}), ("d", {"systemMessage": "note2"})])
    assert m == {"decision": "block", "reason": "why-b\nwhy-c", "systemMessage": "note\nnote2"}, m
    assert dispatch.merge(ev, [("a", {"systemMessage": "only"})]) == {"systemMessage": "only"}

# ---- merge: Post carries additionalContext under its own event name
m = dispatch.merge("PostToolUse", [("a", {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "x"}}),
                                   ("b", {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "y"}})])
assert m == {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "x\ny"}}, m
m = dispatch.merge("PostToolUseFailure", [("a", {"hookSpecificOutput": {"hookEventName": "PostToolUseFailure", "additionalContext": "x"}})])
assert m["hookSpecificOutput"]["hookEventName"] == "PostToolUseFailure"

# ---- merge: one module's malformed output (wrong types, not an object) loses only that module's part
m = dispatch.merge("PreToolUse", [("a", pre("deny", "keep")), ("b", {"hookSpecificOutput": {"permissionDecision": []}}),
                                  ("c", ["not", "an", "object"]), ("d", {"systemMessage": 5, "hookSpecificOutput": "x"}),
                                  ("e", pre(system="fine"))])
assert m["hookSpecificOutput"]["permissionDecision"] == "deny" and m["systemMessage"] == "fine", m
m = dispatch.merge("Stop", [("a", {"decision": "block", "reason": "stay"}), ("b", {"decision": ["block"], "reason": 3})])
assert m == {"decision": "block", "reason": "stay"}, m

# ---- merge: a key the table does not know is dropped and logged, never passed on
m = dispatch.merge("Stop", [("odd", {"decision": "block", "reason": "r", "continue": False})])
assert m == {"decision": "block", "reason": "r"}, m
assert "odd" in (TMP / "state" / "guard-errors.jsonl").read_text()

# ---- merge: a context over the native 10,000-character limit loses whole trailing entries, never a deny reason
big = "x" * 6000
m = dispatch.merge("PreToolUse", [("a", pre("deny", "keep-me", context=big)), ("b", pre(context="y" * 6000))])
assert m["hookSpecificOutput"]["permissionDecisionReason"] == "keep-me"
ctx = m["hookSpecificOutput"]["additionalContext"]
assert big in ctx and "y" * 6000 not in ctx and len(ctx) < 10000 and "1" in ctx.splitlines()[-1], ctx[-80:]

# one entry longer than the cap is cut too, with the cut counted
for parts in (["x" * 12000], ["x" * 12000, "y"]):
    fitted = dispatch.fit(parts)
    assert len(fitted) < 10000 and ("cut" in fitted or "omitted" in fitted), (len(fitted), fitted[-60:])
assert dispatch.fit(["short", "text"]) == "short\ntext"

# ---- matchers: a plain name list is exact, anything else is a regex search (Claude Code's matcher rules)
assert dispatch.matches(None, "Anything")
assert dispatch.matches("Bash|Monitor", "Bash") and not dispatch.matches("Bash|Monitor", "BashOutput")
assert not dispatch.matches("Edit|Write", "MultiEdit")
assert dispatch.matches("Bash|mcp__.*[Tt]elegram.*", "BashOutput")  # a regex searches
assert dispatch.matches("mcp__.*[Tt]elegram.*", "mcp__plugin_telegram_telegram__reply")

# The hooks.json matchers before this change, kept as a literal: for every tool name the dispatcher must pick
# the modules those entries picked.
OLD = {
    "PreToolUse": [
        (None, ["delegate_gate"]),
        ("Bash|Monitor|Skill|Edit|Write|MultiEdit|NotebookEdit|AskUserQuestion", ["guard"]),
        ("Bash", ["self_land_gate"]), ("Bash", ["land_route_gate"]),
        ("Bash|Agent|Task", ["lifecycle"]),
        ("Bash|mcp__.*([Ll]inear|[Jj]ira|atlassian).*", ["prose_check"]),
        ("mcp__.*([Ll]inear|[Jj]ira|[Aa]tlassian).*", ["tracker_gate"]),
        ("Bash", ["stack_gate"]), ("Bash", ["record_check"]), ("Bash", ["decision_relay"]),
        ("Bash|mcp__.*[Tt]elegram.*", ["artifact_send_check"]),
        ("mcp__.*[Tt]elegram.*", ["turn_check"]),
    ],
    "PostToolUse": [("Bash", ["pressure_gate"]), ("Bash", ["record_check"])],
    "PostToolUseFailure": [("Bash", ["pressure_gate"]), ("Bash", ["record_check"])],
    "Stop": [(None, ["record_check"]), (None, ["claim_check"]), (None, ["owner_check"]),
             (None, ["artifact_links_stop"]), (None, ["turn_check"])],
    "SubagentStop": [(None, ["record_check"]), (None, ["claim_check", "owner_check", "subagent_log"]), (None, ["turn_check"])],
}
TOOLS = ["Bash", "BashOutput", "XBash", "Monitor", "Skill", "Edit", "Write", "MultiEdit", "NotebookEdit", "Edits",
         "AskUserQuestion", "Agent", "Task", "Read", "Grep", "Artifact", "mcp__linear__save_issue",
         "mcp__claude_ai_Linear__get_issue", "mcp__jira__x", "mcp__atlassian__y", "mcp__Atlassian__z",
         "mcp__plugin_telegram_telegram__reply", "mcp__telegram__x", "mcp__other__tool", "mcp__linear", ""]
for event, entries in OLD.items():
    for tool in TOOLS:
        want = [mod for matcher, mods in entries if dispatch.matches(matcher, tool) for mod in mods]
        got = [mod for mod, _ in dispatch.selected(event, tool)]
        assert got == want, (event, tool, got, want)

# ---- hooks.json: one dispatch entry per event, pressure_gate and artifact_archive stay apart, timeouts add up
hooks = json.loads((HERE / "hooks.json").read_text())["hooks"]


def commands(event):
    return [(g.get("matcher"), h["command"].rsplit("/", 1)[-1].rstrip('"'), h["timeout"])
            for g in hooks[event] for h in g["hooks"]]


for event in OLD:
    cmds = commands(event)
    dispatchers = [c for c in cmds if c[1] == "dispatch.py"]
    assert len(dispatchers) == 1, (event, cmds)
    matcher, _, timeout = dispatchers[0]
    assert matcher == dispatch.HOOK_MATCHER[event], (event, matcher)
    assert timeout == dispatch.budget(event), (event, timeout, dispatch.budget(event))
    others = {c[1] for c in cmds} - {"dispatch.py"}
    assert others <= {"pressure_gate.py", "artifact_archive.py"}, (event, others)
# Before a call pressure_gate keeps its own process (it can hold a start for its queue); after it, dispatch runs it.
assert [c for c in commands("PreToolUse") if c[1] == "pressure_gate.py"] == [("Bash", "pressure_gate.py", 20)]
assert not any(c[1] == "pressure_gate.py" for e in OLD if e != "PreToolUse" for c in commands(e))
assert [c for c in commands("PostToolUse") if c[1] == "artifact_archive.py"] == [("Artifact", "artifact_archive.py", 30)]
bash_pre = [c for c in commands("PreToolUse") if c[0] in (None, "Bash") and c[1] in ("dispatch.py", "pressure_gate.py")]
assert len(bash_pre) == 2, bash_pre

# ---- every module the registry names has run(event), and the module files never exit with code 2
for event, entries in dispatch.ENTRIES.items():
    for mod, _, timeout in entries:
        src = (HERE / f"{mod}.py").read_text()
        assert "def run(" in src, f"{mod} has no run()"
        assert "sys.exit(2)" not in src and "SystemExit(2)" not in src, mod
        assert timeout > 0

# ---- one module cannot take the others down
fake("fake_ok", """
    def run(event):
        return {"systemMessage": "ok:" + event["tool_name"]}
""")
fake("fake_deny", """
    import pathlib, os
    def run(event):
        pathlib.Path(os.environ["AGENT_SKILLS_STATE"], "deny-ran").write_text("1")
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "no"}}
""")
fake("fake_raise", """
    def run(event):
        raise ValueError("boom")
""")
fake("fake_exit", """
    import sys
    def run(event):
        sys.exit(0)
""")
fake("fake_print", """
    def run(event):
        print("stray text")
        return {"systemMessage": "after-print"}
""")
fake("fake_slow", """
    import time
    def run(event):
        while True:
            try:
                time.sleep(0.02)
            except Exception:  # a module that swallows ordinary errors must still be stopped
                pass
""")
fake("fake_broken", "def run(event:\n    pass\n")
fake("fake_norun", "X = 1\n")
EV = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "x"}, "session_id": "s"}


def entries(*names, timeout=2):
    return [(n, None, timeout) for n in names]


fake("fake_importexit", "import sys\nsys.exit(0)\ndef run(event):\n    return {'systemMessage': 'never'}\n")
fake("fake_importprint", "print('import noise')\ndef run(event):\n    return {'systemMessage': 'never'}\n")
out = dispatch.dispatch(EV, entries("fake_raise", "fake_exit", "fake_print", "fake_ok", "fake_norun", "fake_broken",
                                    "fake_importexit", "fake_importprint"))
assert out["systemMessage"].splitlines()[0] == "ok:Bash", out
for gone in ("fake_broken", "fake_norun", "fake_importexit"):  # a module that cannot load is named
    assert gone in out["systemMessage"], (gone, out)
assert "stray text" not in json.dumps(out) and "after-print" not in json.dumps(out), out  # a module that printed loses its output
assert "never" not in json.dumps(out) and "import noise" not in json.dumps(out), out
log = (TMP / "state" / "guard-errors.jsonl").read_text()
assert "fake_raise" in log and "fake_print" in log, log

import time  # noqa: E402
t0 = time.monotonic()
out = dispatch.dispatch(EV, [("fake_slow", None, 0.3), ("fake_ok", None, 2)])
spent = time.monotonic() - t0
assert spent < 1.5, spent
assert "fake_slow" in out["systemMessage"] and "ok:Bash" in out["systemMessage"], out

# a deny is kept and the modules after it still run (they left state files before this change too)
(TMP / "state").mkdir(exist_ok=True)
out = dispatch.dispatch(EV, entries("fake_deny", "fake_ok"))
assert out["hookSpecificOutput"]["permissionDecision"] == "deny" and "ok:Bash" in out["systemMessage"], out
assert (TMP / "state" / "deny-ran").exists()

# ---- legacy mode feeds every child its input at once: a child with a short limit of its own must not wait behind a slow one
fake("fake_slowchild", """
    import json, sys, time
    sys.stdin.read()
    time.sleep(0.6)
    print(json.dumps({"systemMessage": "slow"}))
""")
fake("fake_alarmchild", """
    import json, signal, sys
    signal.setitimer(signal.ITIMER_REAL, 0.3)  # like subagent_log: a limit of its own, counted from its start
    sys.stdin.read()
    print(json.dumps({"decision": "block", "reason": "quick"}))
""")
real_here = dispatch.HERE
dispatch.HERE = FAKES
try:
    got = dict(dispatch.legacy({"x": 1}, [("fake_slowchild", 5), ("fake_alarmchild", 5)]))
finally:
    dispatch.HERE = real_here
assert got["fake_slowchild"] == {"systemMessage": "slow"} and got["fake_alarmchild"] == {"decision": "block", "reason": "quick"}, got

# a big event and a first child that does not read stdin for a while must not hold the second child back
fake("fake_lateread", """
    import json, sys, time
    time.sleep(1.5)
    sys.stdin.read()
    print(json.dumps({"systemMessage": "late"}))
""")
dispatch.HERE = FAKES
try:
    t0 = time.monotonic()
    got = list(dispatch.legacy({"pad": "a" * 1_000_000}, [("fake_lateread", 0.3), ("fake_alarmchild", 5)]))
    spent = time.monotonic() - t0
finally:
    dispatch.HERE = real_here
assert isinstance(got[0][1], dispatch.Skipped) and got[1][1] == {"decision": "block", "reason": "quick"}, got
assert spent < 1.2, spent

# each legacy child is held to its own deadline from its start, and one child's bad bytes cost only that child
fake("fake_sleepy", """
    import json, time
    time.sleep(0.6)
    print(json.dumps({"systemMessage": "sleepy"}))
""")
fake("fake_late", """
    import json, time
    time.sleep(0.4)
    print(json.dumps({"systemMessage": "late"}))
""")
fake("fake_badbytes", "import sys\nsys.stdout.buffer.write(b'\\xff\\xfe')\n")
dispatch.HERE = FAKES
try:
    got = dict(dispatch.legacy({"x": 1}, [("fake_sleepy", 5), ("fake_late", 0.15), ("fake_alarmchild", 5), ("fake_badbytes", 5)]))
finally:
    dispatch.HERE = real_here
assert got["fake_sleepy"] == {"systemMessage": "sleepy"}, got
assert isinstance(got["fake_late"], dispatch.Skipped), got  # finished after its deadline, though before it was waited on
assert got["fake_alarmchild"] == {"decision": "block", "reason": "quick"} and got["fake_badbytes"] is None, got

# ---- a module that writes to file descriptor 1 directly loses its output and spoils nothing
driver = TMP / "driver.py"
fake("fake_fdnoise", """
    import os
    def run(event):
        os.write(1, b"raw noise\\n")
        return {"systemMessage": "from the noisy one"}
""")
driver.write_text(textwrap.dedent(f"""
    import sys
    sys.path[:0] = [{str(HERE)!r}, {str(FAKES)!r}]
    import dispatch
    dispatch.run_event({EV!r}, [("fake_deny", None, 1), ("fake_fdnoise", None, 1)], grace=1)
"""))
r = subprocess.run([sys.executable, str(driver)], capture_output=True, text=True, timeout=30)
assert r.returncode == 0 and r.stdout.count("\n") == 1 and "noise" not in r.stdout, r
assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny" and "noisy" not in r.stdout, r

# text left in the process's own stdout buffer (no newline, no flush) is caught too, and the deny stays valid JSON
fake("fake_bufnoise", """
    import sys
    def run(event):
        sys.__stdout__.write("BUFFERED NOISE")
        return {"systemMessage": "from the buffered one"}
""")
driver.write_text(textwrap.dedent(f"""
    import sys
    sys.path[:0] = [{str(HERE)!r}, {str(FAKES)!r}]
    import dispatch
    dispatch.run_event({EV!r}, [("fake_deny", None, 1), ("fake_bufnoise", None, 1)], grace=1)
"""))
r = subprocess.run([sys.executable, str(driver)], capture_output=True, text=True, timeout=30)
assert r.returncode == 0 and "BUFFERED" not in r.stdout and "buffered one" not in r.stdout, r
assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny", r

# ---- a legacy child whose descendant keeps the pipe open neither holds the wait nor hides the next child's deny
fake("fake_heir", """
    import subprocess, sys, time
    sys.stdin.read()
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(6)"])  # inherits stdout
    time.sleep(30)
""")
dispatch.HERE = FAKES
try:
    t0 = time.monotonic()
    got = list(dispatch.legacy({"x": 1}, [("fake_heir", 0.3), ("fake_alarmchild", 5)]))
    spent = time.monotonic() - t0
finally:
    dispatch.HERE = real_here
assert isinstance(got[0][1], dispatch.Skipped) and got[1][1] == {"decision": "block", "reason": "quick"}, got
assert spent < 4, spent

# ---- a hung module (signals blocked) still lets the finished deny out before the process ends
fake("fake_hang", """
    import signal, time
    def run(event):
        signal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGALRM])
        time.sleep(60)
""")
driver = TMP / "driver.py"
driver.write_text(textwrap.dedent(f"""
    import json, sys
    sys.path[:0] = [{str(HERE)!r}, {str(FAKES)!r}]
    import dispatch
    dispatch.run_event({EV!r}, [("fake_deny", None, 1), ("fake_hang", None, 1)], grace=1)
"""))
t0 = time.monotonic()
r = subprocess.run([sys.executable, str(driver)], capture_output=True, text=True, timeout=30)
assert time.monotonic() - t0 < 10 and r.returncode == 0, r
assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny", r

# ---- the real modules through the real entry point; legacy mode (hooks.dispatch=false) must print the same
CONFIG = TMP / "config.json"
STATE = TMP / "state"


def event(name, tool="Bash", **kw):
    return {"hook_event_name": name, "session_id": "s1", "cwd": str(TMP), "tool_name": tool, "tool_use_id": "tu1",
            "tool_input": kw.pop("tool_input", {}), **kw}


def cli(ev, cfg):
    CONFIG.write_text(json.dumps(cfg))
    r = subprocess.run([sys.executable, str(HERE / "dispatch.py")], input=json.dumps(ev), capture_output=True, text=True,
                       env={"PATH": os.environ["PATH"], "HOME": str(TMP), "AGENT_SKILLS_CONFIG": str(CONFIG),
                            "AGENT_SKILLS_STATE": str(STATE)})
    assert r.returncode == 0 and r.stderr == "", r
    return json.loads(r.stdout) if r.stdout.strip() else None


RECORDING = {"tracker": {"provider": "linear"}}
SAMPLES = [
    (event("PreToolUse", tool_input={"command": "echo hi"}), {}),
    (event("PreToolUse", tool_input={"command": "until false; do sleep 5; done"}), {}),
    (event("PreToolUse", tool_input={"command": "orca orchestration send --type worker_done --body 'done'"}), RECORDING),
    (event("PostToolUse", tool_input={"command": "gh pr create --title t"}), RECORDING),
    (event("PostToolUseFailure", tool_input={"command": "false; gh pr merge 1"}), RECORDING),
    (event("Stop", tool="", last_assistant_message="done"), RECORDING),
    (event("Stop", tool="", last_assistant_message="done", stop_hook_active=True), RECORDING),
    (event("SubagentStop", tool="", last_assistant_message="done", agent_id="a1"), RECORDING),
]
seen = 0
for ev, cfg in SAMPLES:
    on = cli(ev, cfg)
    off = cli(ev, {**cfg, "hooks": {"dispatch": False}})
    assert on == off, (ev["hook_event_name"], ev["tool_input"] if "tool_input" in ev else "", on, off)
    seen += bool(on)
assert seen >= 3, seen
sleep = cli(SAMPLES[1][0], {})
assert sleep["hookSpecificOutput"]["permissionDecision"] == "deny" and "[gate:sleep-loop]" in json.dumps(sleep), sleep

# record_check keeps its Pre state when another module's deny is merged in (it also did when hooks ran in parallel)
STATE.mkdir(exist_ok=True)
rc = STATE / "record-check.jsonl"
rc.unlink(missing_ok=True)
CONFIG.write_text(json.dumps(RECORDING))
ev = event("PreToolUse", tool_input={"command": "orca orchestration send --type worker_done --body '기록: 없음 (t)'"})
out = dispatch.dispatch(ev, [("fake_deny", None, 2), ("record_check", "Bash", 5)])
assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
assert '"event": "report"' in rc.read_text(), rc.read_text()

# ---- each module's main() prints exactly what its run() returns
for ev, cfg in SAMPLES:
    CONFIG.write_text(json.dumps(cfg))
    for mod, matcher, _ in dispatch.ENTRIES[ev["hook_event_name"]]:
        if not dispatch.matches(matcher, ev["tool_name"]):
            continue
        proc = subprocess.run([sys.executable, str(HERE / f"{mod}.py")], input=json.dumps(ev), capture_output=True, text=True,
                              env={"PATH": os.environ["PATH"], "HOME": str(TMP), "AGENT_SKILLS_CONFIG": str(CONFIG),
                                   "AGENT_SKILLS_STATE": str(STATE)})
        assert proc.returncode == 0, (mod, proc)
        printed = json.loads(proc.stdout) if proc.stdout.strip() else None
        assert printed == dispatch.call(mod, ev, 10), (mod, ev["hook_event_name"], printed)

print("ok")
