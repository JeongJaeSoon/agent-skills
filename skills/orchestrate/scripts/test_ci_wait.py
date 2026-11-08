#!/usr/bin/env python3
"""ci_wait.py with a fake gh on PATH: one poller per target, subscribers, takeover, deadline, interval floor.
Run: python3 skills/orchestrate/scripts/test_ci_wait.py"""
import hashlib, json, os, pathlib, signal, stat, subprocess, sys, tempfile, time, unittest

HERE = pathlib.Path(__file__).resolve().parent
GUARD = HERE.parent.parent / "reap-resources" / "scripts"
TMP = pathlib.Path(os.path.realpath(tempfile.mkdtemp()))
BIN, STATE, LOG = TMP / "bin", TMP / "state", TMP / "gh.log"
os.environ["AGENT_SKILLS_STATE"] = str(STATE)  # reap.STATE reads it once, at import

# The fake answers `gh pr view … --json headRefOid,statusCheckRollup` and `gh run view … --json …`. It logs the pid of
# its parent (the ci-wait process that called it) and reports "completed" from the FAKE_GH_DONE_AFTER-th call on.
FAKE_GH = f"""#!{sys.executable}
import json, os, sys
log = {str(LOG)!r}
open(log, "a").write(f"{{os.getppid()}} {{' '.join(sys.argv[1:])}}\\n")
n = len(open(log).read().splitlines())
if os.environ.get("FAKE_GH_FAIL"):
    sys.stderr.write("HTTP 401\\n"); sys.exit(1)
done = n >= int(os.environ.get("FAKE_GH_DONE_AFTER", "2"))
concl = os.environ.get("FAKE_GH_CONCLUSION", "SUCCESS")
head = os.environ.get("FAKE_GH_HEAD", "a" * 40)
if sys.argv[1:3] == ["pr", "view"]:
    print(json.dumps({{"headRefOid": head, "statusCheckRollup": [
        {{"__typename": "CheckRun", "name": "tests", "status": "COMPLETED" if done else "IN_PROGRESS",
          "conclusion": concl if done else "", "startedAt": "2026-01-01T00:00:00Z"}}]}}))
elif sys.argv[1:3] == ["run", "view"]:
    print(json.dumps({{"headSha": head, "attempt": 1, "status": "completed" if done else "in_progress",
                      "conclusion": concl.lower() if done else ""}}))
else:
    sys.exit(2)
"""

LAUNCH = ("import sys; sys.path.insert(0, %r); import ci_wait; ci_wait.MIN_INTERVAL_S = 1; ci_wait.SUB_POLL_S = 0.1; "
          "sys.exit(ci_wait.main(sys.argv[1:]))") % str(HERE)
TARGET = "acme/app#7"


def env(**extra):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("AGENT_SKILLS_", "FAKE_GH"))}
    e.update(PATH=f"{BIN}:{e['PATH']}", AGENT_SKILLS_STATE=str(STATE), HOME=str(TMP))
    e.update(extra)
    return e


def start(*args, **extra):
    return subprocess.Popen([sys.executable, "-I", "-c", LAUNCH, *args], env=env(**extra), text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def calls():
    return LOG.read_text().splitlines() if LOG.exists() else []


class CiWait(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BIN.mkdir(parents=True)
        gh = BIN / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)

    def setUp(self):
        LOG.unlink(missing_ok=True)
        subprocess.run(["rm", "-rf", str(STATE)])
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.communicate()

    def spawn(self, *args, **extra):
        p = start(*args, **extra)
        self.procs.append(p)
        return p

    def test_two_waiters_make_one_poller(self):
        a = self.spawn(TARGET, "--deadline-min", "1", FAKE_GH_DONE_AFTER="3")
        b = self.spawn(TARGET, "--deadline-min", "1", FAKE_GH_DONE_AFTER="3")
        out_a, out_b = a.communicate(timeout=30)[0], b.communicate(timeout=30)[0]
        self.assertEqual((a.returncode, b.returncode), (0, 0), (out_a, out_b))
        callers = {line.split()[0] for line in calls()}
        self.assertEqual(len(callers), 1, calls())  # every gh call came from one ci-wait process
        self.assertIn("success", out_b.lower())  # the subscriber got the holder's result

    def test_failed_ci_exits_1(self):
        p = self.spawn(TARGET, "--deadline-min", "1", FAKE_GH_CONCLUSION="FAILURE")
        out = p.communicate(timeout=30)[0]
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("failure", out.lower())

    def test_run_id_target(self):
        p = self.spawn("123456", "-R", "acme/app", "--deadline-min", "1")
        out = p.communicate(timeout=30)[0]
        self.assertEqual(p.returncode, 0, out)
        self.assertTrue(any(" run view 123456" in c for c in calls()), calls())

    def test_holder_death_subscriber_takes_over(self):
        a = self.spawn(TARGET, "--deadline-min", "1", FAKE_GH_DONE_AFTER="5")
        for _ in range(100):
            if calls():
                break
            time.sleep(0.1)
        self.assertTrue(calls(), "holder never polled")
        b = self.spawn(TARGET, "--deadline-min", "1", FAKE_GH_DONE_AFTER="5")
        time.sleep(0.5)
        a.send_signal(signal.SIGKILL)  # no cleanup runs: only the OS releases the flock
        out_b = b.communicate(timeout=60)[0]
        self.assertEqual(b.returncode, 0, out_b)
        callers = {line.split()[0] for line in calls()}
        self.assertIn(str(b.pid), callers, "the subscriber never polled after the holder died")

    def test_subscriber_deadline_exits_3(self):
        a = self.spawn(TARGET, "--deadline-min", "1", FAKE_GH_DONE_AFTER="999")
        for _ in range(100):
            if calls():
                break
            time.sleep(0.1)
        b = self.spawn(TARGET, "--deadline-min", "0.03")
        b.communicate(timeout=30)
        self.assertEqual(b.returncode, 3)
        self.assertEqual(a.poll(), None, "the holder keeps going")

    def test_deadline_is_required(self):
        p = self.spawn(TARGET)
        p.communicate(timeout=30)
        self.assertEqual(p.returncode, 2)
        self.assertEqual(calls(), [])

    def test_gh_error_exits_4(self):
        p = self.spawn(TARGET, "--deadline-min", "1", FAKE_GH_FAIL="1")
        out, err = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 4, (out, err))

    def test_head_mismatch_is_not_accepted(self):
        p = self.spawn(TARGET, "--deadline-min", "0.05", "--head", "b" * 40)
        p.communicate(timeout=30)
        self.assertEqual(p.returncode, 3)

    def test_old_done_result_is_not_reused(self):
        sys.path.insert(0, str(HERE))
        import ci_wait
        path = ci_wait.state_path("pr:" + TARGET)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"gen": "old", "state": "done", "result": "failure", "head": "c" * 40,
                                    "polled_at": time.time() - 3600}))
        p = self.spawn(TARGET, "--deadline-min", "1")
        out = p.communicate(timeout=30)[0]
        self.assertEqual(p.returncode, 0, out)
        self.assertTrue(calls(), "an hour-old result answered without asking GitHub")


class Units(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(HERE))
        sys.path.insert(0, str(GUARD))

    def test_interval_floor_is_sixty(self):
        import ci_wait
        self.assertEqual(ci_wait.MIN_INTERVAL_S, 60)
        for asked, got in ((1, 60), (5, 60), (59, 60), (60, 60), (90, 90)):
            self.assertEqual(ci_wait.effective_interval(asked), got)

    def test_lock_file_is_guards(self):
        import ci_wait, guard
        self.assertEqual(ci_wait.lock_path("run:99"), guard.lock_file("other", "ci-wait:run:99"))
        self.assertEqual(hashlib.sha1(b"ci-wait:run:99").hexdigest()[:16] + ".lock", ci_wait.lock_path("run:99").name[len("other-"):])


if __name__ == "__main__":
    unittest.main()
