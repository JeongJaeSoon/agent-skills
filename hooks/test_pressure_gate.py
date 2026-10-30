"""pressure_gate.py: heavy starts wait while the machine is loaded. Run: python3 hooks/test_pressure_gate.py"""
import importlib.util, json, os, pathlib, subprocess, sys, tempfile

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

print("ok")
