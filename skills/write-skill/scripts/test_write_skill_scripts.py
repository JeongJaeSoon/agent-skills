"""check.sh and eval_desc.sh on a fake checkout with stub commands. Run: python3 test_write_skill_scripts.py"""
import atexit, os, pathlib, shutil, stat, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
CHECK, EVAL = HERE / "check.sh", HERE / "eval_desc.sh"

tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-write-skill-")).resolve()
atexit.register(shutil.rmtree, tmp, True)
bindir = tmp / "bin"
bindir.mkdir()
(bindir / "python3").symlink_to(sys.executable)
LOG = tmp / "calls.log"


def stub(name, body):
    p = bindir / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)


def fake_root(name, tests_rc=0, pstack_rc=0, local_rc=0):
    root = tmp / name
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "ci_tests.py").write_text(
        "import sys\nEXCLUDED = {'scripts/test_local.py': 'macOS only', 'scripts/pstack-sync-test.sh': 'bash'}\n"
        f"if __name__ == '__main__':\n    print('ci_tests ran')\n    sys.exit({tests_rc})\n")
    (root / "scripts" / "test_local.py").write_text(f"import sys\nprint('local-only ran')\nsys.exit({local_rc})\n")
    (root / "scripts" / "pstack-sync-test.sh").write_text(f"echo pstack ran\nexit {pstack_rc}\n")
    return root


def run(script, *args, path=None, **env):
    e = {**os.environ, "PATH": path or f"{bindir}:/bin:/usr/bin", "TMPDIR": str(tmp), **env}
    return subprocess.run(["bash", str(script), *args], capture_output=True, text=True, env=e)


# check.sh: all three steps run and a clean pass says so.
stub("claude", f'echo "claude $*" >> {LOG}\nexit 0\n')
r = run(CHECK, CHECK_ROOT=str(fake_root("ok")))
assert r.returncode == 0 and "all checks passed" in r.stdout, (r.stdout, r.stderr)
for line in ("ci_tests ran", "local-only ran", "-- local-only scripts/test_local.py: ok", "pstack ran", "-- plugin-validate: ok"):
    assert line in r.stdout, (line, r.stdout)
assert "claude plugin validate ." in LOG.read_text(), LOG.read_text()

# One failing step does not stop the others, and the failure is named.
r = run(CHECK, CHECK_ROOT=str(fake_root("bad", tests_rc=1)))
assert r.returncode == 1 and "-- tests: FAILED" in r.stdout and "FAILED: tests" in r.stdout, r.stdout
assert "pstack ran" in r.stdout and "-- plugin-validate: ok" in r.stdout, "later steps must still run"
r = run(CHECK, CHECK_ROOT=str(fake_root("bad2", tests_rc=1, pstack_rc=3)))
assert "FAILED: tests pstack-sync" in r.stdout, r.stdout
# A test file CI skips still fails the run here, and the steps after it still run.
r = run(CHECK, CHECK_ROOT=str(fake_root("badlocal", local_rc=1)))
assert r.returncode == 1 and "FAILED: local-only scripts/test_local.py" in r.stdout and "pstack ran" in r.stdout, r.stdout
# PYTHONOPTIMIZE strips asserts; a local-only test whose only check is an assert must still fail.
asserting = fake_root("optimized")
(asserting / "scripts" / "test_local.py").write_text("assert False, 'must fail'\n")
r = run(CHECK, CHECK_ROOT=str(asserting), PYTHONOPTIMIZE="1")
assert r.returncode == 1 and "FAILED: local-only scripts/test_local.py" in r.stdout, r.stdout
# An unreadable EXCLUDED is a failure of its own, not an empty list.
broken = fake_root("brokenexcluded")
(broken / "scripts" / "ci_tests.py").write_text("raise SystemExit('no EXCLUDED here')\n")
r = run(CHECK, CHECK_ROOT=str(broken))
assert r.returncode == 1 and "cannot read ci_tests.EXCLUDED" in r.stdout and "FAILED: tests local-only" in r.stdout, r.stdout

# A failing validate fails the run; a missing claude is a failure too, never a skipped step.
stub("claude", "exit 4\n")
r = run(CHECK, CHECK_ROOT=str(fake_root("badvalidate")))
assert r.returncode == 1 and "FAILED: plugin-validate" in r.stdout, r.stdout
(bindir / "claude").unlink()
system_path = "/bin:/usr/bin"
assert not any((pathlib.Path(d) / "claude").exists() for d in system_path.split(":")), "claude is on the system PATH"
r = run(CHECK, path=f"{bindir}:{system_path}", CHECK_ROOT=str(fake_root("noclaude")))
assert r.returncode == 1 and "NOT VERIFIED (claude is not on PATH)" in r.stdout and "FAILED: plugin-validate" in r.stdout, r.stdout

# Without CHECK_ROOT it works from the checkout this script sits in.
assert (HERE.parents[2] / "scripts" / "ci_tests.py").is_file()

# eval_desc.sh: usage, then the case must exist in both checkouts before anything costs money.
r = run(EVAL, "only-one")
assert r.returncode == 2 and "usage:" in r.stderr, r.stderr
old, new = tmp / "old", tmp / "new"
(old / "evals" / "demo").mkdir(parents=True)
new.mkdir()
stub("claude", f'echo "claude $*" >> {LOG}\nexit 0\n')
LOG.write_text("")
r = run(EVAL, str(old), str(new), "demo")
assert r.returncode == 2 and f"no evals/demo in {new}" in r.stderr and LOG.read_text() == "", (r.stderr, LOG.read_text())

# Both arms run with the same flags, labelled; the exit status is the new arm's.
(new / "evals" / "demo").mkdir(parents=True)
r = run(EVAL, str(old), str(new), "demo")
assert r.returncode == 0 and "old exit 0, new exit 0" in r.stdout, (r.stdout, r.stderr)
calls = LOG.read_text().splitlines()
assert len(calls) == 2 and calls[0].startswith(f"claude plugin eval {old} ") and calls[1].startswith(f"claude plugin eval {new} "), calls
for call in calls:
    assert "--ablation none --no-publish --trust-plugin --case demo" in call and "--output-dir" in call, call
assert "== old:" in r.stdout and "== new:" in r.stdout

# A regression (new fails) fails the comparison; an old arm that fails alone does not.
# $1 is "plugin", $2 is "eval", $3 the checkout.
stub("claude", f'case "$3" in {new}) exit 1;; esac\nexit 0\n')
r = run(EVAL, str(old), str(new), "demo")
assert r.returncode == 1 and "old exit 0, new exit 1" in r.stdout, (r.returncode, r.stdout)
stub("claude", f'case "$3" in {old}) exit 1;; esac\nexit 0\n')
r = run(EVAL, str(old), str(new), "demo")
assert r.returncode == 0 and "old exit 1, new exit 0" in r.stdout, (r.returncode, r.stdout)

# The new arm's own status is kept (2 is a cost ceiling), not folded into 1.
for code in (2, 127):
    stub("claude", f'case "$3" in {new}) exit {code};; esac\nexit 0\n')
    r = run(EVAL, str(old), str(new), "demo")
    assert r.returncode == code and f"new exit {code}" in r.stdout, (code, r.returncode, r.stdout)

# No temporary directory, no model calls.
LOG.write_text("")
stub("claude", f'echo "claude $*" >> {LOG}\nexit 0\n')
r = run(EVAL, str(old), str(new), "demo", TMPDIR=str(tmp / "missing" / "dir"))
assert r.returncode == 2 and LOG.read_text() == "", (r.returncode, r.stderr, LOG.read_text())

print("test_write_skill_scripts: ok")
