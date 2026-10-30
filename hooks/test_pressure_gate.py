"""pressure_gate.py: heavy starts wait while the machine is loaded. Run: python3 hooks/test_pressure_gate.py"""
import importlib.util, json, os, pathlib, subprocess, sys, tempfile, time

HERE = pathlib.Path(__file__).resolve().parent
os.environ["AGENT_SKILLS_STATE"] = tempfile.mkdtemp()
spec = importlib.util.spec_from_file_location("pressure_gate", HERE / "pressure_gate.py")
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
sys.path.insert(0, str(g.SCRIPT.parent))
import pressure

cfg = dict(pressure.DEFAULTS)
OK = {"cpus": 18, "load5": 9.0, "load_per_cpu": 0.5, "mem_free_pct": 60, "vm_pressure": 1, "swap_gb": 1.0,
      "disk_free_gb": 200.0, "containers": 10}
WARN, DANGER = {**OK, "load_per_cpu": 1.4}, {**OK, "mem_free_pct": 5}


def gate(cmd, m, scan_t=None, now=10000.0):
    out = g.gate(cmd, m, {"scan_t": scan_t} if scan_t else {}, cfg, now=now, p=pressure)
    return out[0] if out else "none"


# What counts as a heavy start: each simple command's own words, quotes respected.
for cmd in ("orch heavy slug -- docker compose up -d", "docker compose -f a.yml up -d", "docker-compose up",
            "cd /w && docker compose up --wait", "orca worktree create --repo x --prompt 'CLOSE'",
            "FOO=1 docker compose up", "/opt/bin/orch heavy - -- make e2e", "sudo docker compose up",
            "env X=1 docker compose up", "bash -c 'docker compose up -d'", "docker compose --profile up up",
            "make build; docker compose -p x --env-file vars up"):
    assert g.heavy(cmd), cmd
for cmd in ("docker compose ps", "docker compose down", "grep 'docker compose up' notes.md", "echo orch heavy",
            "orca worktree list", 'echo "a; docker compose up"', 'git commit -m "x; orca worktree create later"',
            "docker compose exec web make up", "docker compose --profile up ps", "orca worktree create --help",
            "cd x && AGENT_SKILLS_PRESSURE_GATE=0 docker compose up", "echo 'unterminated"):
    assert not g.heavy(cmd), cmd
assert g.heavy("docker compose up -d && foo --help") == "docker compose up"

# ok: nothing. warn: denied until a sweep ran in gate_fresh_minutes. danger: denied either way.
assert gate("docker compose up -d", OK) == "none"
assert gate("docker compose up -d", WARN) == "deny"
assert gate("docker compose up -d", WARN, scan_t=10000.0 - 14 * 60) == "none"
assert gate("docker compose up -d", WARN, scan_t=10000.0 - 16 * 60) == "deny"
assert gate("docker compose up -d", DANGER, scan_t=10000.0 - 60) == "deny"
assert gate("docker compose ps", DANGER) == "none"
assert gate("AGENT_SKILLS_PRESSURE_GATE=0 docker compose up -d", DANGER) == "none"
# The hook and `check` share one judge: every danger `check` reports holds a start, and a level `check` reports as
# ok or warn never does once a sweep is fresh. Waiting for `check` to leave danger therefore ends the wait.
LEAK = {**OK, "swap_gb": 56.0, "swapout_pps": 0.0, "vm_pressure": 2, "containers": 45}
for m in (LEAK, {**OK, "load_per_cpu": 2.4, "cpu_idle_pct": 30}, {**OK, "swapout_pps": 2000},
          {**OK, "load_per_cpu": 2.4, "cpu_idle_pct": 0}, {**OK, "vm_pressure": 4}, {**OK, "docker_mem_pct": 95},
          {**OK, "disk_free_gb": 5}, WARN, DANGER):
    level = pressure.judge(m, cfg)[0]
    assert gate("docker compose up", m, scan_t=10000.0 - 60) == ("deny" if level == "danger" else "none"), (m, level)
# Disk does not free itself: held, but the deny asks for space to be freed instead of a wait.
disk = g.gate("docker compose up", {**OK, "disk_free_gb": 5}, {}, cfg, now=1.0, p=pressure)[1]
assert "Monitor" not in disk and "디스크" in disk, disk
reason = g.gate("orch heavy x -- make", DANGER, {}, cfg, now=1.0, p=pressure)[1]
assert "pressure.py round" in reason and "Monitor" in reason and "종료하지 않는다" in reason, reason
assert "15분" in reason, "the wait has a cap"
assert reason.startswith("[gate:pressure] (agent-skills hooks/pressure_gate.py) "), reason
assert g.gate("docker compose up -d", WARN, {}, cfg, now=1.0, p=pressure)[1].startswith(
    "[gate:pressure] (agent-skills hooks/pressure_gate.py) ")
assert "round" not in g.gate("orch heavy x -- make", DANGER, {"scan_t": 1.0}, cfg, now=2.0, p=pressure)[1] \
    .split("Monitor")[0], "a fresh sweep does not ask for another round"

# The hook process: off by environment, silent for other tools and plain commands.
def hook(tool, cmd, **env):
    e = {**os.environ, **env}
    out = subprocess.run([sys.executable, HERE / "pressure_gate.py"], input=json.dumps(
        {"tool_name": tool, "tool_input": {"command": cmd}}), capture_output=True, text=True, env=e)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()

assert hook("Bash", "ls") == "" and hook("Read", "docker compose up") == ""
assert hook("Bash", "docker compose up", AGENT_SKILLS_PRESSURE_GATE="0") == ""
real = hook("Bash", "docker compose up")  # this machine's real level: either no decision or a well-formed deny
assert real == "" or json.loads(real)["hookSpecificOutput"]["permissionDecision"] == "deny", real

# The queue: a danger deny records the command for the session that asked, and only that session's own rerun of the
# same text counts as its retry. A HOME whose config puts every metric at danger, or none, fixes the level.
dash = pathlib.Path(tempfile.mkdtemp())
os.environ["ORCH_FLEET_STATE"] = str(dash)
homes = {}
for name, worst in (("danger", {"mem_free_pct": [101, 101]}), ("ok", {})):
    home = pathlib.Path(tempfile.mkdtemp())
    (home / ".claude").mkdir()
    never = {k: ([1e9, 1e9] if higher else [-1, -1]) for k, higher in pressure.CHECKS}
    (home / ".claude" / "agent-skills.json").write_text(json.dumps({"reap": {"pressure": {**never, **worst}}}))
    homes[name] = str(home)


def event(kind, cmd, session, home="ok", **extra):
    out = subprocess.run([sys.executable, HERE / "pressure_gate.py"], input=json.dumps(
        {"hook_event_name": kind, "tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": session,
         "cwd": "/w", **extra}), capture_output=True, text=True, env={**os.environ, "HOME": homes[home]})
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def pre(cmd, session, home):
    out = event("PreToolUse", cmd, session, home)
    return out and json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]


def entry(cmd, session):
    return pressure.queue_read().get(pressure.queue_id(session, cmd))


def dash_rows():
    return [json.loads(line) for line in (dash / "inbox-external.jsonl").read_text().splitlines()]


def wait(qid, home):
    return subprocess.run([sys.executable, g.SCRIPT, "queue", "wait", qid], capture_output=True, text=True,
                          env={**os.environ, "HOME": homes[home]})


CARD = "orca worktree create --repo acme/app --prompt '/goal x. CLOSE: y'"
held = pre(CARD, "s1", "danger")
e = entry(CARD, "s1")
assert e and e["state"] == "waiting" and e["cwd"] == "/w" and e["attempts"] == 1, e
assert f"queue wait {e['id']}" in held and "Monitor" in held and "같은 명령을 그대로 다시 실행" in held, held
assert held.startswith("[gate:pressure] "), held
row = dash_rows()[-1]
assert row["op"] == "add" and row["key"] == f"pressure:queue:{e['id']}" and e["id"] in row["title"], row
assert row["command"].endswith("pressure.py queue") and CARD not in json.dumps(row), "the copy button lists, never reruns"
# Denied again before it got through: the same entry, counted, with its first deadline. Another session: its own entry.
rows = len(dash_rows())
pre(CARD, "s1", "danger")
assert entry(CARD, "s1")["attempts"] == 2 and entry(CARD, "s1")["deadline"] == e["deadline"]
assert len(dash_rows()) == rows, "a repeat deny keeps the one row it has"
pre(CARD, "s2", "danger")
assert entry(CARD, "s2")["id"] != e["id"] and len(pressure.queue_read()) >= 2
# Load down: wait says go, and only the rerun by the session that asked is its retry (danger below, under the deadline).
out = wait(e["id"], "ok")
assert out.returncode == 0 and CARD in out.stdout, out
assert pre(CARD, "s9", "ok") == "" and entry(CARD, "s2")["state"] == "waiting", "another session's run is not s2's retry"
assert pre(CARD, "s1", "ok") == "" and entry(CARD, "s1")["state"] == "retrying"
assert wait(e["id"], "ok").returncode == 3, "a retried entry is never run a second time"
assert event("PostToolUseFailure", CARD, "s1", error="Exit code 1") == ""
e = entry(CARD, "s1")
assert e["state"] == "failed" and e["error"] == "Exit code 1" and "재시도 실패" in dash_rows()[-1]["title"], e
assert event("PostToolUse", CARD, "s1") == "" and entry(CARD, "s1")["state"] == "failed", "an outcome only of a retry"
# A retry that went through takes its row off the dashboard.
pre(CARD, "s3", "danger")
pre(CARD, "s3", "ok")
event("PostToolUse", CARD, "s3")
assert entry(CARD, "s3")["state"] == "done" and dash_rows()[-1] == {**dash_rows()[-1], "op": "resolve",
                                                                     "key": f"pressure:queue:{entry(CARD, 's3')['id']}"}
# A plain command never touches the queue, and a failure event of anything else is not a retry's.
assert event("PostToolUseFailure", "ls", "s2", error="Exit code 1") == "" and entry(CARD, "s2")["state"] == "waiting"

# The deadline: wait gives up and records it, and a round gives up on entries nobody waits for any more.
t0, ticks = time.time(), iter(range(10 ** 6))  # a finished entry older than a week is pruned
q = pressure.enqueue("s4", "/w", "docker compose up", "docker compose up", ["mem_free_pct 5 ≤ 10"], cfg, now=t0)
lines = []
code = pressure.queue_wait(q["id"], {**cfg, "mem_free_pct": [101, 101]}, now_fn=lambda: t0 + 60 * next(ticks),
                           sleep=lambda s: None, measure_fn=lambda: dict(OK), out=lines.append)
assert code == 3 and "포기" in lines[-1] and entry("docker compose up", "s4")["state"] == "gave_up", lines
assert "포기" in dash_rows()[-1]["title"]
pressure.enqueue("s5", "/w", "docker compose up", "docker compose up", ["x"], cfg, now=t0)
assert [x["session"] for x in pressure.queue_expire(t0 + 16 * 60)] == ["s5"]
assert pressure.queue_expire(t0 + 16 * 60) == [], "given up once"
# Disk: nothing to wait for, so nothing is queued.
before = dict(pressure.queue_read())
g.gate("docker compose up --wait", {**OK, "disk_free_gb": 5}, {}, cfg, now=1.0, p=pressure, session="s6")
assert pressure.queue_read().keys() == before.keys()

print("ok")
