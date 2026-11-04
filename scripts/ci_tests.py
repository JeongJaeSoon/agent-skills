#!/usr/bin/env python3
"""Run every test file CI can run, each with its own empty HOME and an allow-listed environment.

Usage: python3 scripts/ci_tests.py

Discovery, not a list: a new test_*.py runs here without touching CI. A file that cannot run on a hosted Linux runner
goes into EXCLUDED with its reason. CONTRIBUTING.md's loop stays the full suite, and it still runs these on a Mac.
"""
import os, pathlib, subprocess, sys, tempfile, time

REPO = pathlib.Path(__file__).resolve().parents[1]
TIMEOUT_S = 300
TAIL_LINES = 40
PASS_ENV = ("PATH", "LANG", "LC_ALL", "TMPDIR", "USER")
EXCLUDED = {
    "hooks/test_pressure_gate.py": "macOS only: the memory reading comes from kern.memorystatus_level",
    "scripts/pstack-sync-test.sh": "macOS only: BSD `sed -i ''`",
    "scripts/test_share_server.py": "macOS only: sandbox-exec and launchd",
    "skills/reap-resources/scripts/test_reap.py": "hosted runner: a process of this user has an unreadable /proc cwd",
}


class Refused(Exception):
    """CI cannot trust this run; nothing was tested."""


def git(repo, *args):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if p.returncode:
        raise Refused(f"git {' '.join(args)} failed: {p.stderr.strip()[:300]}")
    return p.stdout


def discover(repo, excluded):
    tracked = git(repo, "ls-files").splitlines()
    stale = sorted(set(excluded) - set(tracked))
    if stale:
        raise Refused(f"EXCLUDED names files that do not exist: {', '.join(stale)}")
    found = sorted(p for p in tracked if p not in excluded and pathlib.PurePath(p).name.startswith("test_")
                   and p.endswith(".py"))
    if not found:
        raise Refused("found no test files")
    return found


def child_env(home):
    """No inherited PYTHONOPTIMIZE (it strips asserts) or GIT_CONFIG_*. The dummy token lets gh reach its own
    "no git remotes" answer, the one a signed-in machine gets, instead of stopping at login."""
    signers = home / "allowed_signers.tmp"
    signers.touch()
    config = home / "gitconfig.tmp"
    config.write_text("[user]\n\tname = ci\n\temail = ci@example.test\n"
                      f"[gpg \"ssh\"]\n\tallowedSignersFile = {signers}\n")
    env = {k: os.environ[k] for k in PASS_ENV if k in os.environ}
    return dict(env, HOME=str(home), GIT_CONFIG_GLOBAL=str(config), GH_TOKEN="ci-dummy", PYTHONDONTWRITEBYTECODE="1")


def run_one(repo, path, timeout):
    with tempfile.TemporaryDirectory() as home:
        try:
            p = subprocess.run([sys.executable, path], cwd=repo, env=child_env(pathlib.Path(home).resolve()),
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, f"timed out after {timeout}s"
    return p.returncode == 0, "\n".join(p.stdout.splitlines()[-TAIL_LINES:])


def run(repo, excluded, timeout=TIMEOUT_S):
    if git(repo, "rev-parse", "--is-shallow-repository").strip() == "true":
        raise Refused("a shallow clone: hooks/test_retired.py would pass with less to check; use fetch-depth 0")
    for path, why in sorted(excluded.items()):
        print(f"skip  {path}: {why}")
    failed = []
    start = time.monotonic()
    tests = discover(repo, excluded)
    for path in tests:
        began = time.monotonic()
        ok, tail = run_one(repo, path, timeout)
        print(f"{'ok  ' if ok else 'FAIL'}  {time.monotonic() - began:6.1f}s  {path}", flush=True)
        if not ok:
            failed.append(path)
            print(tail, flush=True)
    print(f"{len(tests) - len(failed)}/{len(tests)} passed in {time.monotonic() - start:.0f}s")
    for path in failed:
        print(f"FAILED: {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(run(REPO, EXCLUDED))
    except Refused as e:
        sys.exit(f"refused: {e}")
