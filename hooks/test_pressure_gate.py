"""pressure_gate.py: heavy starts wait while the machine is loaded. Run: python3 hooks/test_pressure_gate.py"""
import fcntl, importlib.util, json, os, pathlib, subprocess, sys, tempfile, time

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
# A worker in a new worktree is a new agent; one sent to an existing worktree is not.
assert g.heavy("orca orchestration worker-start --spec x --worktree new-top-level --agent claude") == "orca orchestration worker-start"
assert g.heavy("orca orchestration worker-start --spec x --worktree=new-child --agent claude")
assert not g.heavy("orca orchestration worker-start --task t --worktree current --agent claude")
assert not g.heavy("orca orchestration worker-start --help")
assert g.heavy("FOO=1 bash <<EOF\ndocker compose up -d\nEOF") == "docker compose up"
assert not g.heavy("cat > a.md <<'EOF'\ndocker compose up -d\nEOF")
assert g.heavy("cat <<<hello\norca worktree create --repo x") == "orca worktree create"
assert g.heavy("cat <<EOF\n$(docker compose up -d)\nEOF") == "docker compose up"
assert not g.heavy("cat <<'EOF'\n$(docker compose up -d)\nEOF")
assert g.heavy("for d in a b; do orch heavy - -- make; done") == "orch heavy"
assert g.heavy('id="$(orca worktree create --repo x)"') == "orca worktree create"
assert g.heavy("cat <<END-TEXT\nhello\nEND-TEXT\ndocker compose up -d") == "docker compose up"
assert g.heavy("cat <<'END TEXT'\nhi\nEND TEXT\ndocker compose up -d") == "docker compose up"
assert g.heavy("cat <<EOF\nhello\ndocker compose up -d") == "docker compose up"  # no terminator: nothing dropped
assert not g.heavy("cat <<END-TEXT\ndocker compose up -d\nEND-TEXT")
assert g.heavy("bash > out.log <<'EOF'\ndocker compose up -d\nEOF") == "docker compose up"
assert not g.heavy("# $(orca worktree create --repo x)\necho ok")
assert g.heavy("AGENT_SKILLS_PRESSURE_GATE=0 true; bash <<EOF\ndocker compose up -d\nEOF") == "docker compose up"
assert not g.heavy("AGENT_SKILLS_PRESSURE_GATE=0 bash <<EOF\ndocker compose up -d\nEOF")
assert g.new_card("orch heavy - -- bash <<'EOF'\norca worktree create --repo x\nEOF") == "orca worktree create"
assert g.heavy("mask=$((1<<20))\ndocker compose up -d") == "docker compose up"
assert g.heavy("# Example: cat <<EOF\norca worktree create --repo x") == "orca worktree create"
assert g.heavy("(( x = 1 << 2 ))\norca worktree create --repo x") == "orca worktree create"
assert g.heavy('"/bin/bash" <<EOF\ndocker compose up -d\nEOF') == "docker compose up"
assert g.heavy('echo "a<<EOF"\norca worktree create --repo x') == "orca worktree create"

# Whole suites, E2E and headless browsers belong in an `orch heavy` slot; one test file, or one already in a slot, does not.
LOOP = "fail=0\nfor t in " + " ".join(f"skills/x/scripts/test_{i}.py" for i in range(6)) + \
    '; do python3 "$t" || fail=1; done\n(exit $fail)'
for cmd, what in (("bash tests.sh", "test suite"), ("cd /w && ./scripts/run-tests.sh", "test suite"),
                  ("bash /tmp/scratch/tests.sh 2>&1 | tail -20", "test suite"), (LOOP, "test suite"),
                  ("bun test", "test suite"), ("npm test", "test suite"), ("pnpm run test", "test suite"),
                  ("CI=1 yarn test:all", "test suite"), ("pytest", "test suite"), ("go test ./...", "test suite"),
                  ("make test", "test suite"), ("npx playwright test", "E2E"), ("bunx playwright test --headed", "E2E"),
                  ("pnpm test:e2e", "E2E"), ("npm run e2e", "E2E"), ("./scripts/e2e.sh", "E2E"), ("cypress run", "E2E"),
                  ("make e2e", "E2E"), ("bash -c 'cd app && npm run test:e2e'", "E2E"),
                  ("'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' --headless=new --dump-dom x", "browser"),
                  ("chromium --headless --screenshot a.png x", "browser"),
                  # Option values and redirections pick no tests; a wrapped command does not cover its neighbour.
                  ("pytest -n 8", "test suite"), ("bun test --timeout 30000", "test suite"),
                  ("npm test > results.txt", "test suite"), ("npm test 2>&1 | tail -5", "test suite"),
                  ("orch heavy - -- bash tests.sh & npx playwright test", "E2E"),
                  ("pytest --junitxml results.xml", "test suite"), ("bun test --coverage-dir ./coverage", "test suite"),
                  ("pytest --junitxml test-results.xml", "test suite"),
                  ("bun test --coverage-dir ./test-coverage", "test suite"),
                  ("for d in app api; do npm test; done", "test suite"),
                  ("for d in app api; do (cd $d && npx playwright test); done", "E2E"),
                  ("python3 -m pytest", "test suite"), ("uv run pytest -q", "test suite"),
                  ("if true; then pytest; fi", "test suite"),
                  # Only an assignment in front of the command is the user's go-ahead, not text that mentions it.
                  ('echo "AGENT_SKILLS_PRESSURE_GATE=0"; npm test', "test suite"),
                  ("for t in " + " ".join(f"test_{i}.py" for i in range(6)) + '; do "$t"; done', "test suite"),
                  ("npx vitest run", "test suite"), ("npx jest --runInBand", "test suite"), ("vitest", "test suite"),
                  ("jest", "test suite"), ("bash <<'EOF'\nnpm test\nEOF", "test suite"),
                  ("cat > a.md <<'EOF'\nx\nEOF\nnpm test", "test suite"),
                  ("FOO=1 bash <<EOF\nnpm test\nEOF", "test suite"), ("cd x && env bash <<'EOF'\npytest\nEOF", "test suite"),
                  ("pytest -o addopts=", "test suite"), ("uv run python3 -m pytest", "test suite"),
                  ("poetry run python -m pytest -q", "test suite"),
                  ("python3 -c 'print(1<<20)'\nnpm test", "test suite"), ("pnpm exec playwright test", "E2E"),
                  ("npm exec -- playwright test", "E2E"), ("pnpm exec vitest run", "test suite"),
                  ("bun x jest", "test suite"),
                  ('env bash -c "npm test"', "test suite"), ('bash -lc "npm test"', "test suite"),
                  ("nice bash -l -c 'pytest'", "test suite"),
                  ("nice -n 5 npm test", "test suite"), ("nice -n 5 bash -c 'npm test'", "test suite"),
                  ("sudo -u app env -u HOME npm test", "test suite"), ("pytest --color yes", "test suite"),
                  ("pytest --capture sys", "test suite"),
                  ("for d in app api; do bash -c 'npm test'; done", "test suite"),
                  ("if true; then bash -c 'pytest'; fi", "test suite"), ("npm --prefix app test", "test suite"),
                  ("pnpm --dir app test", "test suite"), ("yarn --cwd app test", "test suite"),
                  ("npx --package @playwright/test playwright test", "E2E"), ("npx -y vitest run", "test suite"),
                  ("pnpm dlx --package=vitest vitest run", "test suite"),
                  ("bash -e tests.sh", "test suite"), ("bash -eux scripts/run-tests.sh", "test suite"),
                  ("bash -o pipefail tests.sh", "test suite"), ("bash -o pipefail -c 'npm test'", "test suite"),
                  ("bash +o history -lc 'pytest'", "test suite"),
                  ("npx vitest run " + " ".join(f"{c}.test.ts" for c in "abcde"), "test suite"), ("jest " + " ".join(f"{c}.test.ts" for c in "abcde"), "test suite"),
                  ("FOO=1 bash <<'EOF'\nnpm test\nEOF", "test suite"),
                  ("orch heavy - -- true; bash <<EOF\nnpm test\nEOF", "test suite"),
                  ("cat <<EOF\n$(npm test)\nEOF", "test suite"),
                  ("npm \\\ntest", "test suite"), ("npx vitest run --exclude tests/slow/**", "test suite"),
                  ("npm test -- --testPathIgnorePatterns tests/slow", "test suite"),
                  ('output="$(npm test 2>&1)"', "test suite"), ("printf '%s\\n' \"$(pytest)\"", "test suite"),
                  ('echo "`npm test`"', "test suite"),
                  ("pnpm --dir app exec playwright test", "E2E"), ("npm --prefix app exec -- playwright test", "E2E"),
                  ("npx vitest run --pool forks", "test suite"), ("npx jest --env jsdom", "test suite"),
                  ("uv run --no-sync pytest", "test suite"), ("uv run --project app python -m pytest", "test suite"),
                  ('output="$(printf \')\'; npm test)"', "test suite"),
                  ("cat <<END-TEXT\nx\nEND-TEXT\nnpm test", "test suite"),
                  ("cat <<EOF\ndon't $(npm test)\nEOF", "test suite"), ("echo a # x\nnpm test", "test suite"),
                  ("go test -count 1 ./...", "test suite"), ("go test -p 2 -timeout 5m ./...", "test suite"),
                  ("cat <<'A' <<'B'\nx\nA\ny\nB\nnpm test", "test suite"),
                  ("bash <<A\ncat <<'B'\nx\nB\nnpm test\nA", "test suite"),
                  ("bash -s x <<'EOF'\nnpm test\nEOF", "test suite"), ("bash > output.log <<'EOF'\nnpm test\nEOF", "test suite"),
                  ("bash 2>&1 <<'EOF'\npytest\nEOF", "test suite"), ("FOO=1 env bash -e <<'EOF'\npytest\nEOF", "test suite"), ("pytest --confcutdir tests/", "test suite"),
                  ("pytest --ignore tests/slow", "test suite"), ('for t in hooks/test_*.py; do python3 "$t"; done', "test suite")):
    assert g.unwrapped_tests(cmd) == what, (cmd, g.unwrapped_tests(cmd))
for cmd in ("python3 hooks/test_guard.py", "bun test src/a.test.ts", "npm test -- a.test.ts", "pytest tests/test_a.py",
            "go test ./pkg/a", "npx playwright install chromium", "orch heavy - -- bash tests.sh",
            "orch heavy - -- bash -c " + repr(LOOP), "cat tests.sh", "grep -n e2e package.json",
            "git add " + " ".join(f"hooks/test_{i}.py" for i in range(6)), "AGENT_SKILLS_PRESSURE_GATE=0 bash tests.sh",
            "AGENT_SKILLS_PRESSURE_GATE=0 " + LOOP.split("\n")[1], "AGENT_SKILLS_PRESSURE_GATE=0 bash -c 'npm test'", "chrome --version", "npm run build", "ls tests",
            "pytest -k foo", "pytest tests/test_a.py::test_x", "orch heavy - -- npm test > out.txt",
            "pytest -x tests/", "pytest tests/unit", "bun test --bail src/a.spec.ts",
            "python3 -m pytest tests/test_a.py", "uv run python -m pytest tests/test_a.py", "uv run pytest -k slow", "for f in a b; do echo $f; done",
            "for f in " + " ".join(f"test_{i}.py" for i in range(6)) + '; do cat "$f"; done',
            "AGENT_SKILLS_PRESSURE_GATE=0 bash -c 'cd app && npx playwright test'",
            "cat > README.md <<'EOF'\nnpm test\nEOF", "cat <<-EOF > a.md\n\tpytest\n\tEOF",
            "npx vitest run src/a.test.ts", "jest -t login", "npx jest --testPathPattern auth", "vitest --version", "pnpm exec vitest run src/a.test.ts", "npm exec --help", "bun test src/components/", "bun test Button", "cat <<\\EOF\n$(npm test)\nEOF", "npx vitest run src/components/Button", "jest Button",
            'for t in hooks/test_*.py; do AGENT_SKILLS_PRESSURE_GATE=0 python3 "$t"; done', "bash -c 'cat' <<'EOF'\nnpm test\nEOF", "bash notes.sh <<'EOF'\npytest\nEOF", "echo bash <<'EOF'\nnpm test\nEOF", "go test -run TestX ./...", "go test -count 1 ./pkg/a", "pytest -m smoke", "cat <<'A' <<'B'\nhello\nA\nnpm test\nB", "cat <<EOF\n\\$(npm test)\nEOF", "# $(npm test)\necho ok", "cat <<EOF\ndon't\nEOF", "echo '$(npm test)'", 'echo "$((1 + 2))"', 'v="$(date)"', "cat <<EOF\n$(date)\nnpm test\nEOF", "AGENT_SKILLS_PRESSURE_GATE=0 bash <<'EOF'\ncd app && npm test\nEOF", "npx vitest run a.test.ts b.test.ts", "orch heavy - -- bash <<'EOF'\nnpm test\nEOF", "bash -e scripts/build.sh", "bash -x", "pytest --confcutdir tests/ tests/test_a.py", "npm --prefix app test -- a.test.ts", "pnpm --dir app run build", 'python3 --version; for f in hooks/test_*.py; do cat "$f"; done',
            'for f in hooks/test_*.py; do cat "$f"; done; python3 x.py', "pytest integration/check_login.py", "pytest src/", "nice -n 5 pytest tests/test_a.py", "AGENT_SKILLS_PRESSURE_GATE=0 nice -n 5 npm test", "AGENT_SKILLS_PRESSURE_GATE=0 env bash -lc 'npm test'", 'for f in hooks/test_*.py; do cat "$f"; done'):
    assert g.unwrapped_tests(cmd) is None, cmd
wrapped = g.wrap_reason("cd /w && bash tests.sh", "test suite")
assert wrapped.startswith("[gate:pressure] (agent-skills hooks/pressure_gate.py) ") and "run_in_background" in wrapped
line = wrapped.split("`")[3]
assert line == "orch heavy - -- bash -c 'cd /w && bash tests.sh'" and g.unwrapped_tests(line) is None, line

# A new card waits while Orca shows the cap's turns in progress; other heavy starts and an unknown count do not.
assert pressure.turn_cap(cfg, 18) == 9 and pressure.turn_cap({**cfg, "active_turns_per_cpu": None}, 18) is None
assert pressure.turn_cap({**cfg, "active_turns_per_cpu": 0.01}, 18) == 1
real_turns, real_cap = pressure.active_turns, pressure.turn_cap
pressure.turn_cap = lambda c, cpus=None: 9
for n, card, compose in ((9, "deny", "none"), (8, "none", "none"), (None, "none", "none")):
    pressure.active_turns = lambda n=n: n
    assert gate("orca worktree create --repo x", OK) == card, n
    assert gate("orca orchestration worker-start --spec x --worktree new-top-level", OK) == card, n
    assert gate("docker compose up -d", OK) == compose, n
    # A card started after another heavy start on the line, or inside `orch heavy`, is still a new card.
    assert gate("docker compose up -d && orca worktree create --repo x", OK) == card, n
    assert gate("orch heavy - -- orca worktree create --repo x", OK) == card, n
    assert gate("orch heavy - -- bash -c 'orca worktree create --repo x'", OK) == card, n
pressure.active_turns = lambda: 12
turns = g.gate("orca worktree create --repo x", OK, {}, cfg, now=1.0, p=pressure)[1]
assert "12개" in turns and "상한 9개" in turns and "turns --wait 15" in turns and "Monitor" in turns, turns
pressure.active_turns, pressure.turn_cap = real_turns, real_cap
assert pressure.active_turns(run=lambda *a, **k: json.dumps({"ok": True, "result": {"worktrees": [
    {"agents": [{"state": "working"}, {"state": "done"}]}, {"agents": [{"state": "working"}]}, {"agents": None}]}})) == 2
assert pressure.active_turns(run=lambda *a, **k: None) is None
real_which, pressure.shutil.which = pressure.shutil.which, lambda name: None  # an injected runner needs no orca
assert pressure.active_turns(run=lambda *a, **k: json.dumps({"worktrees": [{"agents": [{"state": "working"}]}]})) == 1
pressure.shutil.which = real_which
assert pressure.active_turns(run=lambda *a, **k: "not json") is None
assert pressure.active_turns(run=lambda *a, **k: json.dumps({"result": {"truncated": True, "worktrees": [
    {"agents": [{"state": "working"}]}]}})) is None  # a card left out could hold a turn
ticks = iter([100.0, 100.0, 200.0, 2000.0])
said = []
assert pressure.turns_wait(cfg, 15, now_fn=lambda: next(ticks), sleep=lambda s: None, count=iter([10**6, 10**6, 0]).__next__,
                           out=said.append) == 0 and "0개" in said[-1]
ticks = iter([0.0, 0.0, 901.0])
assert pressure.turns_wait({**cfg, "active_turns_per_cpu": 0.5}, 15, now_fn=lambda: next(ticks), sleep=lambda s: None,
                           count=lambda: 10**6, out=said.append) == 3 and "1000000개" in said[-1]

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
deny = json.loads(hook("Bash", "bash tests.sh"))["hookSpecificOutput"]
assert deny["permissionDecision"] == "deny" and "orch heavy - -- bash -c 'bash tests.sh'" in deny["permissionDecisionReason"]
assert hook("Bash", "bash tests.sh", AGENT_SKILLS_PRESSURE_GATE="0") == ""
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
    never["active_turns_per_cpu"] = None  # the turn cap reads this machine's Orca; its own tests fake the count
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

# A retry another hook or the permission check held never gets an outcome: one more window, then it fails with that
# reason, so the row does not say "재시도 중" for good. Denied again meanwhile, it waits anew with a fresh deadline.
pressure.enqueue("s7", "/w", CARD, "orca worktree create", ["x"], cfg, now=t0)
assert pressure.queue_mark("s7", CARD, "retrying", now=t0 + 60)["deadline"] == t0 + 60 + 15 * 60
assert pressure.queue_expire(t0 + 10 * 60) == [] and entry(CARD, "s7")["state"] == "retrying"
assert [x["state"] for x in pressure.queue_expire(t0 + 17 * 60)] == ["failed"]
assert entry(CARD, "s7")["error"] == pressure.NO_OUTCOME and "다른 hook" in dash_rows()[-1]["title"]
pressure.enqueue("s8", "/w", CARD, "orca worktree create", ["x"], cfg, now=t0)
pressure.queue_mark("s8", CARD, "retrying", now=t0 + 60)
again = pressure.enqueue("s8", "/w", CARD, "orca worktree create", ["y"], cfg, now=t0 + 120)
assert again["state"] == "waiting" and again["deadline"] == t0 + 120 + 15 * 60 and again["attempts"] == 2, again
# Given up, then the session ran it after all and it went through: the row closes instead of saying "포기" for a week.
assert entry("docker compose up", "s4")["state"] == "gave_up"
assert pressure.queue_mark("s4", "docker compose up", "retrying") and pressure.queue_mark("s4", "docker compose up", "done")
assert dash_rows()[-1] == {**dash_rows()[-1], "op": "resolve", "key": f"pressure:queue:{entry('docker compose up', 's4')['id']}"}

# A queue that cannot be written still holds a danger start, on the old wait; only a failed measurement lets it through.
lock = open(pressure.reap.STATE / "pressure-queue.lock", "w")
fcntl.flock(lock, fcntl.LOCK_EX)
pressure.LOCK_WAIT_S = 0.1
held = g.gate("docker compose up -d", DANGER, {}, cfg, now=1.0, p=pressure, session="s10")
assert held[0] == "deny" and "대기열에 넣지 못함" in held[1] and "pressure.py check" in held[1], held
assert held[1].startswith("[gate:pressure] "), held
lock.close()
pressure.LOCK_WAIT_S = 3

# A broken entry costs itself, not the round: no KeyError, and the whole entries still expire.
q = json.loads(pressure.QUEUE.read_text())
q["q-broken"] = {"state": "waiting"}
q["q-junk"] = "x"
pressure.QUEUE.write_text(json.dumps(q))
assert "q-broken" not in pressure.queue_read() and "q-junk" not in pressure.queue_read()
pressure.queue_expire(time.time() + 10 ** 6)
assert all(e["state"] not in ("waiting", "retrying") for e in pressure.queue_read().values())
pressure.QUEUE.write_text("{not json")
assert pressure.queue_read() == {} and pressure.queue_expire() == []
assert pre(CARD, "s11", "danger").startswith("[gate:pressure] ") and entry(CARD, "s11")["state"] == "waiting"

print("ok")
