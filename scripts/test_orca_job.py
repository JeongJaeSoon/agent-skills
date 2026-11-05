#!/usr/bin/env python3
import contextlib, io, json, os, pathlib, sys, tempfile, time, unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import orca_job as oj  # noqa: E402


class RunTest(unittest.TestCase):
    def setUp(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="orca-job-test-"))
        self.addCleanup(__import__("shutil").rmtree, d)
        for p in (mock.patch.object(oj, "STATE", d), mock.patch.object(oj, "JOBS", d / "jobs")):
            p.start()
            self.addCleanup(p.stop)

    def run_job(self, argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return oj.main(argv), err.getvalue()

    def test_always_exits_1_so_orca_skips_the_agent(self):
        for cmd in ([sys.executable, "-c", "pass"], [sys.executable, "-c", "raise SystemExit(7)"], ["/no/such/binary"]):
            code, _ = self.run_job(["run", "j", "--", *cmd])
            self.assertEqual(code, 1, cmd)

    def test_success_is_recorded(self):
        self.run_job(["run", "j", "--interval", "900", "--", sys.executable, "-c", "print('synced 3 files')"])
        rec = oj.read("j")
        self.assertTrue(rec["ok"] and rec["rc"] == 0 and rec["interval"] == 900, rec)
        self.assertIn("synced 3 files", rec["message"])

    def test_failure_is_recorded_with_its_last_stderr_line_and_reported(self):
        code, err = self.run_job(["run", "j", "--", sys.executable, "-c",
                                  "import sys; print('noise', file=sys.stderr); print('no network', file=sys.stderr); sys.exit(3)"])
        rec = oj.read("j")
        self.assertFalse(rec["ok"])
        self.assertEqual((rec["rc"], rec["message"]), (3, "exit 3: no network"))
        self.assertIn("j: exit 3: no network", err)

    def test_a_signal_death_names_the_signal(self):
        self.run_job(["run", "j", "--", sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"])
        rec = oj.read("j")
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["message"], "killed by signal 9")

    def test_a_missing_command_is_a_failure_not_a_crash(self):
        self.run_job(["run", "j", "--", "/no/such/binary"])
        rec = oj.read("j")
        self.assertFalse(rec["ok"] or rec["rc"] != 127)
        self.assertIn("could not start", rec["message"])

    def test_a_timeout_kills_it_and_says_so(self):
        started = time.monotonic()
        code, _ = self.run_job(["run", "j", "--timeout", "1", "--", sys.executable, "-c", "import time; time.sleep(30)"])
        self.assertEqual(code, 1)
        self.assertLess(time.monotonic() - started, 10)
        rec = oj.read("j")
        self.assertFalse(rec["ok"])
        self.assertIn("timed out after 1s", rec["message"])

    def test_ok_codes_are_results_not_failures(self):
        """pressure.py round exits 1 for warn and 2 for danger."""
        cmd = [sys.executable, "-c", "raise SystemExit(2)"]
        self.run_job(["run", "j", "--ok", "0,1,2", "--", *cmd])
        self.assertTrue(oj.read("j")["ok"])
        self.run_job(["run", "k", "--", *cmd])
        self.assertFalse(oj.read("k")["ok"])

    def test_a_timeout_takes_the_jobs_children_down_too(self):
        pidfile = oj.STATE / "child.pid"
        code = ("import subprocess, sys, time; c = subprocess.Popen(['sleep', '60']); "
                f"open({str(pidfile)!r}, 'w').write(str(c.pid)); time.sleep(60)")
        self.run_job(["run", "j", "--timeout", "1", "--", sys.executable, "-c", code])
        pid = int(pidfile.read_text())
        time.sleep(0.3)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_a_failure_with_only_stdout_still_names_its_cause(self):
        self.run_job(["run", "j", "--", sys.executable, "-c", "print('docker is not running'); raise SystemExit(2)"])
        self.assertEqual(oj.read("j")["message"], "exit 2: docker is not running")

    def test_python3_means_the_interpreter_running_the_wrapper(self):
        self.run_job(["run", "j", "--", "python3", "-c", "import sys; print(sys.executable)"])
        self.assertIn(os.path.realpath(sys.executable), os.path.realpath(oj.read("j")["message"].split(": ", 1)[1]))

    def test_a_malformed_record_is_skipped_not_fatal(self):
        oj.JOBS.mkdir(parents=True, exist_ok=True)
        (oj.JOBS / "a.json").write_text("[]")
        (oj.JOBS / "b.json").write_text(json.dumps({"name": "b"}))
        (oj.JOBS / "c.json").write_text("{not json")
        self.run_job(["run", "good", "--", sys.executable, "-c", "pass"])
        self.assertEqual([r["name"] for r in oj.records()], ["good"])
        oj.forget("good")
        self.assertEqual(oj.records(), [])

    def test_the_command_gets_a_usable_path(self):
        with mock.patch.dict(os.environ, {"PATH": "/nowhere"}):
            self.run_job(["run", "j", "--", sys.executable, "-c", "import os; print(os.environ['PATH'])"])
        self.assertIn("/usr/bin", oj.read("j")["message"])

    def test_bad_usage_never_exits_0_and_runs_nothing(self):
        """Exit 0 would let Orca start the agent."""
        for argv in (["run", "--", "true"], ["run", "j"], ["run"], []):
            code, _ = self.run_job(argv)
            self.assertNotEqual(code, 0, argv)
        self.assertEqual(oj.records(), [])

    def test_stale_is_three_intervals_past_the_last_run(self):
        rec = {"at": 1000.0, "interval": 100}
        self.assertIsNone(oj.stale(rec, now=1250.0))
        self.assertEqual(oj.stale(rec, now=1400.0), 100.0)
        self.assertIsNone(oj.stale({"at": 0, "interval": None}, now=10 ** 9))
        self.assertIsNone(oj.stale(None))

    def test_command_round_trips_through_main(self):
        import shlex
        cmd = oj.command("j", ["python3", "/x/a b.py", "run"], 180, 280, ok=(0, 1, 2))
        parts = shlex.split(cmd)
        self.assertEqual(parts[:2], ["python3", "-I"])
        self.assertEqual(parts[parts.index("--") + 1:], ["python3", "/x/a b.py", "run"])
        self.assertEqual(json.loads(json.dumps(parts[parts.index("--ok") + 1])), "0,1,2")


if __name__ == "__main__":
    unittest.main()
