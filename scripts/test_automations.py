#!/usr/bin/env python3
import contextlib, io, json, pathlib, subprocess, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import automations as au  # noqa: E402

# The jobs read their model from agent-skills.json; a developer's own file must not change what these tests expect.
NO_CONFIG = pathlib.Path(tempfile.gettempdir()) / "test-automations-no-such-config.json"
au.CONFIG = au.reap.CONFIG = NO_CONFIG

WANT = au.Janitor().want()


def janitor(enabled=True, **over):
    a = {"id": "J1", "name": au.JANITOR_NAME, "enabled": enabled, "rrule": WANT["trigger"], "prompt": WANT["prompt"],
         "precheck": {"command": WANT["precheck"], "timeoutSeconds": WANT["precheck-timeout"]},
         "workspaceId": f"x::{au.REPO}"}
    a.update(over)
    return a


def retired_load_job(enabled=True, **over):
    """The janitor load job an earlier version registered; nothing defines it any more."""
    return janitor(enabled, **{"id": "J2", "name": au.reap.LOAD_NAME, "rrule": "27,57 * * * *", "prompt": "old", **over})


class FakeOrca:
    """Stands in for `sh`: answers `orca automations list` from a list and records every other call."""
    def __init__(self, autos):
        self.autos, self.calls = autos, []

    def __call__(self, cmd):
        self.calls.append(cmd)
        if cmd[1:3] == ["automations", "list"]:
            out = json.dumps({"ok": True, "result": {"automations": self.autos}}) if self.autos is not None else ""
            return subprocess.CompletedProcess(cmd, 0 if self.autos is not None else 1, out, "boom")
        if self.autos is not None and (str(au.REAP) in cmd or cmd[1:3] == ["automations", "create"]):
            # A created job is listed from then on.
            name = cmd[cmd.index("--name") + 1] if "--name" in cmd else au.JANITOR_NAME
            self.autos.append({"id": f"new{len(self.autos)}", "enabled": True, "name": name})
        return subprocess.CompletedProcess(cmd, 0, "created", "")

    def edits(self):
        return [c for c in self.calls if c[1:3] == ["automations", "edit"]]

    def creates(self):
        return [c for c in self.calls if c[1:3] == ["automations", "create"]]

    def schedules(self):
        return [c for c in self.calls if str(au.REAP) in c]


class Base(unittest.TestCase):
    def orca(self, autos, main=True, configured=False):
        fake = FakeOrca(autos)
        for p in (mock.patch.object(au, "sh", fake), mock.patch.object(au.shutil, "which", return_value="/x/orca"),
                  mock.patch.object(au, "main_checkout", return_value=main),
                  mock.patch.object(au.Watch, "configured", return_value=configured),
                  # A disable drops the job's record: never the real one under the user's state directory.
                  mock.patch.object(au.orca_job, "forget")):
            p.start()
            self.addCleanup(p.stop)
        return fake


class JanitorShape(Base):
    """The janitor is one Orca automation; the load job it absorbed is a retired name, not a second definition."""

    def test_shape(self):
        """The round runs through orca_job (a record setup-pc reads, exit 1 for Orca), like every shell job."""
        self.assertTrue(WANT["precheck"].endswith("precheck --act"), WANT["precheck"])
        self.assertIn("orca_job.py run janitor ", WANT["precheck"])
        self.assertEqual((WANT["trigger"], WANT["precheck-timeout"], WANT["prompt"]), ("*/30 * * * *", 600, au.SHELL_PROMPT))
        # The janitor's own round stops under the job's timeout, which stops under the precheck's.
        self.assertIn(f"--timeout {au.reap.PRECHECK_TIMEOUT - 20} ", WANT["precheck"])
        self.assertLess(au.reap.HARD_STOP_S, au.reap.PRECHECK_TIMEOUT - 20)

    def test_reap_schedule_writes_the_same_precheck(self):
        argv = au.reap.schedule_cmd(au.REAP, au.reap.STATE, "path:x", ledger=au.reap.ledger_path())
        self.assertEqual((argv[argv.index("--precheck") + 1], argv[argv.index("--trigger") + 1]),
                         (WANT["precheck"], WANT["trigger"]))
        self.assertEqual(au.reap.JOB_RUNNER, pathlib.Path(au.orca_job.__file__).resolve())

    def test_no_job_is_defined_for_the_load_name(self):
        self.assertNotIn(au.reap.LOAD_NAME, [getattr(a, "job_name", None) for a in au.AUTOMATIONS])
        self.assertIn(au.reap.LOAD_NAME, au.RETIRED)


class JanitorName(unittest.TestCase):
    def test_matches_what_reap_creates(self):
        argv = au.reap.schedule_cmd(au.REAP, au.reap.STATE, "path:x")
        self.assertEqual(argv[argv.index("--name") + 1], au.JANITOR_NAME)


class Janitor(Base):
    j = au.Janitor()

    def test_status(self):
        self.orca([])
        self.assertEqual(self.j.status()[0], "absent")
        self.orca([janitor()])
        self.assertEqual(self.j.status()[0], "on")
        self.orca([janitor(enabled=False)])
        self.assertEqual(self.j.status()[0], "off")
        self.orca(None)
        self.assertEqual(self.j.status()[0], "unknown")

    def test_status_names_drift(self):
        self.orca([janitor(rrule="17 */3 * * *" if WANT["trigger"] != "17 */3 * * *" else "0 * * * *")])
        self.assertIn("trigger", self.j.status()[1])

    def test_no_orca_cli(self):
        with mock.patch.object(au.shutil, "which", return_value=None):
            self.assertEqual(self.j.status()[0], "unknown")
            code, msg = self.j.enable(False)
        self.assertEqual(code, 1)
        self.assertIn("orca CLI", msg)

    def test_enable_absent_creates_one_agentless_job(self):
        fake = self.orca([])
        self.assertEqual(self.j.enable(False)[0], 0)
        (c,) = fake.creates()
        self.assertEqual((c[c.index("--name") + 1], c[c.index("--precheck") + 1]), (au.JANITOR_NAME, WANT["precheck"]))
        self.assertFalse(any(a.startswith("--extra-agent-args") for a in c), c)

    def test_enable_absent_refuses_a_worktree(self):
        fake = self.orca([], main=False)
        code, msg = self.j.enable(False)
        self.assertEqual(code, 1)
        self.assertIn("worktree", msg)
        self.assertEqual(self.j.enable(True)[0], 1)
        self.assertEqual(fake.schedules() + fake.creates(), [])

    def test_enable_when_on_changes_nothing(self):
        fake = self.orca([janitor()])
        self.assertEqual(self.j.enable(False), (0, "이미 켜져 있음"))
        self.assertEqual(fake.edits(), [])

    def test_enable_turns_on_and_fixes_drift(self):
        fake = self.orca([janitor(enabled=False, prompt="old")])
        self.assertEqual(self.j.enable(False)[0], 0)
        (edit,) = fake.edits()
        self.assertEqual(edit[edit.index("--prompt") + 1], WANT["prompt"])
        self.assertIn("--enabled", edit)
        self.assertNotIn("--trigger", edit)

    def test_timeout_drift_alone_still_passes_the_precheck(self):
        """orca rejects --precheck-timeout without --precheck."""
        fake = self.orca([janitor(precheck={"command": WANT["precheck"], "timeoutSeconds": 60}, prompt="old")])
        self.assertEqual(self.j.enable(False)[0], 0)
        (edit,) = fake.edits()
        self.assertEqual(edit[edit.index("--precheck") + 1], WANT["precheck"])
        self.assertEqual(edit[edit.index("--precheck-timeout") + 1], str(WANT["precheck-timeout"]))

    def test_drift_from_a_worktree_is_not_written(self):
        for here in (False, True):
            fake = self.orca([janitor(prompt="old")], main=False)
            self.assertEqual(self.j.enable(here), (0, "이미 켜져 있음"))
            self.assertEqual(fake.edits(), [])
            self.assertNotIn("다름", self.j.status()[1])

    def test_null_workspace(self):
        self.orca([janitor(workspaceId=None)])
        self.assertEqual(self.j.status()[0], "on")

    def test_disabled_without_drift_turns_on_from_a_worktree(self):
        fake = self.orca([janitor(enabled=False)], main=False)
        self.assertEqual(self.j.enable(False)[0], 0)
        self.assertEqual(fake.edits()[0][-2:], ["--enabled", "--json"])

    def test_disable(self):
        fake = self.orca([janitor()])
        self.assertEqual(self.j.disable()[0], 0)
        self.assertIn("--disabled", fake.edits()[0])
        for autos in ([janitor(enabled=False)], []):
            fake = self.orca(autos)
            self.assertEqual(self.j.disable(), (0, "이미 꺼져 있음"))
            self.assertEqual(fake.edits(), [])

    def test_an_agent_registration_drifts_and_enable_clears_its_model_argument(self):
        """The janitor registered as a model session (`--model sonnet`, an agent prompt, a 3-hour trigger) is the shape
        this checkout no longer defines: every field differs, and the model argument is cleared."""
        old = janitor(prompt="agent prompt", rrule="17 */3 * * *", extraAgentArgs="--model sonnet",
                      precheck={"command": "reap.py precheck", "timeoutSeconds": 600})
        self.assertEqual(sorted(self.j.drift(old)), ["extra-agent-args", "precheck", "prompt", "trigger"])
        fake = self.orca([old])
        self.assertEqual(self.j.enable(False)[0], 0)
        (edit,) = fake.edits()
        # `orca automations edit --help` says "--extra-agent-args=<args> ... empty clears" and its example is the bare
        # `--extra-agent-args=` form; a separate empty argv word would be taken as a missing value
        self.assertIn("--extra-agent-args=", edit)
        self.assertNotIn("--extra-agent-args", edit)
        self.assertEqual(edit[edit.index("--prompt") + 1], WANT["prompt"])
        self.assertEqual(self.j.drift(janitor()), {})


class Drift(Base):
    """`setup drift` reports and `setup reconcile` fixes registrations that no longer match the definitions."""
    reap_hard = next(j for j in au.SHELL_JOBS if j.name == "reap-hard")

    def stored(self, **over):
        return ShellJobs.stored(None, self.reap_hard, **over)

    def test_reports_a_drifted_job_a_retired_one_and_nothing_for_a_clean_list(self):
        self.orca([janitor()])
        self.assertEqual(au.drift_rows(), ([], None))
        self.orca([janitor(), retired_load_job(), self.stored(rrule="*/15 * * * *")])
        rows_, err = au.drift_rows()
        self.assertIsNone(err)
        self.assertEqual([(r["name"], r["kind"]) for r in rows_], [("reap-hard", "drift"), (au.reap.LOAD_NAME, "retired")])
        self.assertEqual(rows_[0]["fields"], ["trigger"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(au.cmd_drift(False), 1)
        self.assertIn("reap-hard: 등록된 내용이 정의와 다름: trigger", out.getvalue())
        self.orca([janitor()])
        self.assertEqual(au.cmd_drift(True), 0)

    def test_an_unreadable_list_is_not_a_clean_one(self):
        self.orca(None)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(au.cmd_drift(False), 2)
            self.assertEqual(au.cmd_reconcile(), 2)

    def test_a_worktree_reports_nothing_and_reconcile_refuses(self):
        fake = self.orca([retired_load_job(), self.stored(rrule="*/15 * * * *")], main=False)
        self.assertEqual(au.drift_rows(), ([], None))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(au.cmd_reconcile(), 1)
        self.assertEqual([c for c in fake.calls if c[1:3] != ["automations", "list"]], [])

    def test_reconcile_edits_in_place_keeps_off_and_removes_the_retired(self):
        fake = self.orca([janitor(), retired_load_job(), self.stored(rrule="*/15 * * * *", enabled=False)])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(au.cmd_reconcile(), 0)
        (edit,) = fake.edits()
        self.assertEqual(edit[edit.index("--trigger") + 1], self.reap_hard.want()["trigger"])
        self.assertTrue({"--enabled", "--disabled", "--prompt"}.isdisjoint(edit), edit)  # on/off stays as the user left it
        (rm,) = [c for c in fake.calls if c[1:3] == ["automations", "remove"]]
        self.assertEqual(rm[rm.index("--id") + 1], "J2")
        self.assertEqual(fake.creates(), [])


class Preview(unittest.TestCase):
    p = au.PreviewWatch()

    def patch(self, loaded, spec, tailscale=True, main=True, owned=None, rc=0):
        self.install, self.uninstall = mock.Mock(return_value=rc), mock.Mock(return_value=rc)
        for p in (mock.patch.object(au, "launchd_loaded", return_value=loaded),
                  mock.patch.object(au, "plist_spec", return_value=spec),
                  mock.patch.object(au, "tailscale_ready", return_value=tailscale),
                  mock.patch.object(au, "main_checkout", return_value=main),
                  mock.patch.object(au.sys, "platform", "darwin"),
                  mock.patch.object(au.pw, "cmd_install", self.install),
                  mock.patch.object(au.pw, "cmd_uninstall", self.uninstall),
                  mock.patch.object(au.pw, "load_state", return_value={"owned": owned or {}})):
            p.start()
            self.addCleanup(p.stop)

    def current(self):
        return au.pw.spec(au.pw.config()["interval"])

    def test_status(self):
        self.patch(False, None)
        self.assertEqual(self.p.status()[0], "absent")
        self.patch(True, self.current())
        self.assertEqual(self.p.status()[0], "on")
        self.patch(True, {"ProgramArguments": ["python3", "/elsewhere/preview_watch.py", "run"]})
        self.assertIn("/elsewhere/preview_watch.py", self.p.status()[1])

    def test_enable_refuses_without_tailscale(self):
        self.patch(False, None, tailscale=False)
        code, msg = self.p.enable(False)
        self.assertEqual(code, 1)
        self.assertIn("tailscale", msg)
        self.install.assert_not_called()

    def test_enable_when_on_changes_nothing(self):
        self.patch(True, self.current())
        self.assertEqual(self.p.enable(False), (0, "이미 켜져 있음"))
        self.install.assert_not_called()

    def test_enable_installs_from_main_only(self):
        self.patch(False, None, main=False)
        self.assertEqual(self.p.enable(False)[0], 1)
        self.install.assert_not_called()
        self.patch(False, None)
        self.assertEqual(self.p.enable(False)[0], 0)
        self.install.assert_called_once()

    def test_disable(self):
        self.patch(False, None)
        self.assertEqual(self.p.disable(), (0, "이미 꺼져 있음"))
        self.uninstall.assert_not_called()
        self.patch(True, self.current())
        self.assertEqual(self.p.disable()[0], 0)
        self.uninstall.assert_called_once()

    def test_failed_uninstall_says_so_and_retries(self):
        self.patch(True, self.current(), rc=1)
        code, msg = self.p.disable()
        self.assertEqual(code, 1)
        self.assertNotIn("닫았다", msg)
        self.patch(False, None, owned={"4590": "http://127.0.0.1:4590"})
        self.assertEqual(self.p.disable()[0], 0)
        self.uninstall.assert_called_once()

    def test_failed_install_says_so(self):
        self.patch(False, None, rc=1)
        self.assertEqual(self.p.enable(False), (1, "실패 (위 출력)"))

    def test_other_os_only_explains(self):
        self.patch(False, None)
        with mock.patch.object(au.sys, "platform", "linux"):
            self.assertEqual(self.p.status()[0], "unsupported")
            self.assertEqual(self.p.enable(False)[0], 1)
        self.install.assert_not_called()


class ShellJobs(Base):
    """sync, pressure-watch, reap-hard, orch-dash and land-executor: Orca automations whose precheck runs the command and skips the agent."""
    jobs = {j.name: j for j in au.SHELL_JOBS}

    def stored(self, job, enabled=True, **over):
        w = job.want()
        a = {"id": f"S-{job.name}", "name": job.job_name, "enabled": enabled, "rrule": w["trigger"],
             "prompt": w["prompt"], "precheck": {"command": w["precheck"], "timeoutSeconds": w["precheck-timeout"]}}
        a.update(over)
        return a

    def creates(self, fake):
        return [c for c in fake.calls if c[1:3] == ["automations", "create"]]

    def test_defaults(self):
        self.assertEqual({n: j.default for n, j in self.jobs.items()},
                         {"pstack-upstream": False, "sync": True, "pressure-watch": True, "reap-hard": True,
                          "orch-dash": True, "patrol": False, "land-executor": False, "sweep": False})

    def test_patrol_round_fits_the_runner(self):
        """The round's two budgets, plus the wait for a cut scan's last command, end before the runner kills the job,
        which ends before the precheck's limit."""
        sys.path.insert(0, str(au.REPO / "skills" / "orchestrate" / "scripts"))
        sys.path.insert(0, str(au.REPO / "skills" / "reap-resources" / "scripts"))
        import candidates
        import patrol
        w = self.jobs["patrol"].want()
        self.assertEqual((w["trigger"], w["precheck-timeout"]), ("*/30 * * * *", 420))
        self.assertTrue(w["precheck"].endswith("prog.py patrol --if-due"), w["precheck"])
        self.assertIn("orca_job.py run patrol --interval 1800 --timeout 400 ", w["precheck"])
        self.assertEqual(au.reap.SPAWN_TIMEOUT_S, 400, "reap-hard's spawned patrol gets the job's runner limit")
        self.assertLess(patrol.BUDGET_S + patrol.CANDIDATE_BUDGET_S + candidates.CUT_WAIT_S, 400)
        import reap_hard
        self.assertLess(patrol.FALLBACK_DUE_S, reap_hard.DEFAULTS["interval"],
                        "reap-hard's fallback is due at every pass of its own period")

    def test_every_job_fits_orcas_precheck_limit(self):
        """`orca automations create` rejects --precheck-timeout above 600 s; sweep registered 2700 and failed to register."""
        for name, j in self.jobs.items():
            self.assertLessEqual(j.want()["precheck-timeout"], 600, name)
        self.assertEqual(au.ORCA_PRECHECK_MAX_S, 600)

    def test_a_timeout_over_the_limit_stops_before_orca_is_asked(self):
        """A definition over the limit must fail here, before create or edit, and leave nothing half made."""
        for secs, refused in ((au.ORCA_PRECHECK_MAX_S, False), (au.ORCA_PRECHECK_MAX_S + 1, True)):
            j = au.ShellJob("probe", "probe", ["true"], 900, True, timeout=secs)
            fake = self.orca([])
            code, msg = j.enable(False)
            self.assertEqual((code != 0), refused, (secs, msg))
            self.assertEqual(len(self.creates(fake)), 0 if refused else 1, secs)
            if refused:
                self.assertIn(str(secs), msg)
            # a registered copy that differs only in its timeout: enable and realign must not call edit
            a = {"id": "P1", "name": j.job_name, "enabled": False, "rrule": j.want()["trigger"],
                 "prompt": j.want()["prompt"], "precheck": {"command": j.want()["precheck"], "timeoutSeconds": 60}}
            fake = self.orca([a])
            self.assertEqual((j.enable(False)[0] != 0), refused, secs)
            self.assertEqual((j.realign()[0] != 0), refused, secs)
            self.assertEqual(len(fake.edits()) > 0, not refused, secs)

    def test_sweep_runs_one_tick_step_per_precheck_and_is_off_by_default(self):
        j = self.jobs["sweep"]
        self.assertIs(au.SWEEP, j)
        self.assertEqual(j.job_name, "agent-skills sweep")
        self.assertEqual(j.argv[1:], [str(au.REAP.parent / "sweep.py"), "tick", "--step"])
        w = j.want()
        self.assertEqual((w["trigger"], w["precheck-timeout"], w["prompt"]), ("*/10 * * * *", 600, au.SHELL_PROMPT))
        self.assertIn("orca_job.py run sweep ", w["precheck"])
        self.assertIn("--timeout 580 ", w["precheck"])
        self.assertIn("--skip 3 ", w["precheck"])  # a tick that found the lock held is a skip, not a success
        import sweep
        self.assertEqual(sweep.SKIP_RC, 3)
        # one step ends before the runner kills the job, which ends before Orca kills the precheck
        self.assertLessEqual(sweep.STEP_BUDGET_S, 550)
        self.assertLess(sweep.STEP_BUDGET_S, au.SWEEP_TIMEOUT_S - 20)
        self.assertEqual(sweep.STEP_PERIOD_S, 600)
        self.orca([])
        self.assertEqual(au.decide(j)[0], "skip")  # apply never turns it on

    def test_turning_sweep_back_on_restarts_its_cycle_but_staying_on_does_not(self):
        """After a pause the old position would hand shadow and real an old round with no fresh patrol first."""
        j = self.jobs["sweep"]
        reset = mock.Mock()
        with mock.patch.object(j, "reset", reset), mock.patch.object(au, "sweep_protect_problem", return_value=None):
            self.orca([self.stored(j, enabled=True)])
            self.assertEqual(j.enable(False)[0], 0)
            reset.assert_not_called()  # already on: nothing was paused
            self.orca([self.stored(j, enabled=False)])
            self.assertEqual(j.enable(False)[0], 0)
            reset.assert_called_once()
            reset.reset_mock()
            self.orca([])
            self.assertEqual(j.enable(False)[0], 0)  # first registration
            reset.assert_called_once()
            reset.reset_mock()
            self.orca([self.stored(j, enabled=True)])
            self.assertEqual(j.disable()[0], 0)
            reset.assert_not_called()  # a step may be running: the file is not touched on disable
        for name, other in self.jobs.items():
            if name != "sweep":
                self.assertIsNone(other.reset, name)

    def test_other_jobs_precheck_strings_are_unchanged_by_the_skip_option(self):
        """A new option that changes every stored string would show every registered job as drifted."""
        for name, j in self.jobs.items():
            if name != "sweep":
                self.assertNotIn("--skip", j.want()["precheck"], name)

    def test_sweep_needs_the_protect_list_like_reap_hard(self):
        j = self.jobs["sweep"]
        fake = self.orca([])
        with mock.patch.object(au, "sweep_protect_problem", return_value="no protect file"):
            self.assertEqual(j.status(), ("unconfigured", "no protect file"))
            code, msg = j.enable(False)
        self.assertEqual(code, 1)
        self.assertIn("no protect file", msg)
        self.assertEqual(self.creates(fake), [])
        with mock.patch.object(au, "sweep_protect_problem", return_value=None):
            self.assertEqual(j.enable(False)[0], 0)
        self.assertEqual(self.creates(fake)[0][self.creates(fake)[0].index("--name") + 1], "agent-skills sweep")

    def test_disable_keeps_the_registration_and_drops_the_record(self):
        j = self.jobs["sweep"]
        fake = self.orca([self.stored(j)])
        self.assertEqual(j.disable()[0], 0)
        self.assertIn("--disabled", fake.edits()[0])
        au.orca_job.forget.assert_called_with("sweep")

    def test_reap_hard_waits_for_the_protect_list_and_orch_dash_does_not(self):
        # With the patrol job off, reap-hard's pass starts patrol, so this gate is also patrol's fallback gate.
        self.orca([])
        j = self.jobs["reap-hard"]
        with tempfile.TemporaryDirectory() as d:
            mine = pathlib.Path(d) / "protect.txt"
            with mock.patch.object(au, "PLANNED_CONFIG", {"reap-hard": {"protect_file": str(mine)}}):
                self.assertEqual(j.status()[0], "unconfigured")
                self.assertIn(str(mine), j.status()[1])
                mine.write_text("port 1\n")
                self.assertEqual(j.status()[0], "absent")
        self.assertEqual(self.jobs["orch-dash"].status()[0], "absent")

    def test_a_registered_job_is_never_gated(self):
        j = self.jobs["reap-hard"]
        self.orca([self.stored(j)])
        # The job holds its own reference to the gate, so patching the module attribute would not reach it.
        with mock.patch.object(j, "gate", return_value="no file") as gate:
            self.assertEqual(j.status()[0], "on")
        gate.assert_not_called()

    def test_stale_only_when_on_with_a_different_registration(self):
        j = self.jobs["orch-dash"]
        self.orca([self.stored(j)])
        self.assertFalse(j.stale())
        self.orca([self.stored(j, rrule="*/7 * * * *")])
        self.assertTrue(j.stale())
        self.assertEqual(au.decide(j)[0], "refresh")
        self.orca([self.stored(j, enabled=False, rrule="*/7 * * * *")])  # the user turned it off: left alone
        self.assertFalse(j.stale())
        self.orca([self.stored(j, rrule="*/7 * * * *")], main=False)  # a worktree's paths differ: not drift
        self.assertFalse(j.stale())

    def test_a_new_interval_in_the_config_apply_writes_is_a_refresh(self):
        j = self.jobs["reap-hard"]
        self.orca([self.stored(j)])
        self.assertFalse(j.stale())
        with mock.patch.object(au, "PLANNED_CONFIG", {"reap-hard": {"interval": 3600}}):
            self.assertTrue(j.stale())
        p = self.jobs["pressure-watch"]
        self.orca([self.stored(p)])
        with mock.patch.object(au, "PLANNED_CONFIG", {"reap": {"pressure": {"interval": 600}}}):
            self.assertTrue(p.stale())

    def test_land_executor_rounds_every_minute_and_enable_sets_up_the_queue(self):
        j = self.jobs["land-executor"]
        self.assertEqual(j.argv[1:], [str(au.LAND_EXEC), "round"])
        self.assertEqual(j.want()["trigger"], "*/1 * * * *")
        self.orca([])
        done = au.subprocess.CompletedProcess([], 0, "created land_request\n", "")
        with mock.patch.object(au.subprocess, "run", return_value=done) as run:
            self.assertEqual(j.enable(False)[0], 0)
        self.assertEqual(run.call_args[0][0][-2:], [str(au.LAND_EXEC), "setup"])

    def test_land_executor_is_not_turned_on_when_the_queue_setup_fails(self):
        j = self.jobs["land-executor"]
        fake = self.orca([])
        failed = au.subprocess.CompletedProcess([], 1, "", "refused: agent-skills.db is not migrated")
        with mock.patch.object(au.subprocess, "run", return_value=failed):
            code, msg = j.enable(False)
        self.assertEqual(code, 1)
        self.assertIn("not migrated", msg)
        self.assertEqual(self.creates(fake), [])

    def test_status_text_ends_with_the_land_scope_drift_lines(self):
        warn = au.subprocess.CompletedProcess([], 0, "경고: o/new는 ... 범위 밖이다\n", "")
        with mock.patch.object(au.subprocess, "run", return_value=warn) as run:
            self.assertEqual(au.land_drift(), ["경고: o/new는 ... 범위 밖이다"])
        self.assertEqual(run.call_args[0][0][-2:], [str(au.LAND_EXEC), "drift"])
        broke = au.subprocess.CompletedProcess([], 1, "", "Traceback: boom")
        with mock.patch.object(au.subprocess, "run", return_value=broke):
            self.assertEqual(au.land_drift(), ["경고: land executor 범위 점검 실패: Traceback: boom"])
        with mock.patch.object(au.subprocess, "run", side_effect=au.subprocess.TimeoutExpired("x", 90)):
            self.assertIn("90초", au.land_drift()[0])
        with mock.patch.object(au, "rows", return_value=[]), mock.patch.object(au, "land_drift", return_value=["경고: x"]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                au.cmd_status(True)
            self.assertEqual(json.loads(out.getvalue()), [], "--json stays the rows alone")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                au.cmd_status(False)
            self.assertIn("경고: x", out.getvalue())

    def test_no_job_is_a_launchagent_or_names_launchctl(self):
        for j in self.jobs.values():
            self.assertNotIn("launchctl", " ".join(j.argv))
            self.assertIn("agent-skills " + j.name, j.job_name)

    def test_periods(self):
        self.assertEqual(au.cron_every(900), "*/15 * * * *")
        self.assertEqual(au.cron_every(180), "*/3 * * * *")
        self.assertEqual(au.cron_every(30), "*/1 * * * *")
        self.assertEqual(au.cron_every(7200), "0 */2 * * *")

    def test_a_period_cron_cannot_run_evenly_becomes_the_nearest_divisor_below(self):
        """*/45 fires at :00 and :45, then :00 again; the job must say the period it really runs at."""
        self.assertEqual(au.cron_every(2700), "*/30 * * * *")
        self.assertEqual(au.cron_every(5400), "0 */1 * * *")
        self.assertEqual(au.cron_every(100000), "0 */24 * * *")
        self.assertEqual(au.cron_period(2700), 1800)
        self.assertIn("--interval 1800 ", self.jobs["pressure-watch"].__class__(
            "x", "w", ["a"], 2700, True).want()["precheck"])

    def test_pressure_watch_turned_off_before_stays_off_on_apply(self):
        self.orca([])
        j = self.jobs["pressure-watch"]
        with mock.patch.object(j, "turned_off", return_value=True):
            self.assertEqual(j.status()[0], "off")
        with mock.patch.object(j, "turned_off", return_value=False):
            self.assertEqual(j.status()[0], "absent")

    def test_enable_clears_the_old_off_marker_only_when_it_worked(self):
        cleared = []
        j = au.ShellJob("x", "w", ["a"], 900, True, clear_off=lambda: cleared.append(1))
        self.orca([])
        self.assertEqual(j.enable(False)[0], 0)
        self.assertEqual(cleared, [1])
        self.orca([], main=False)  # a worktree refuses: the marker stays
        self.assertEqual(j.enable(False)[0], 1)
        self.assertEqual(cleared, [1])

    def test_disable_of_an_already_off_job_still_drops_a_stale_record(self):
        j = self.jobs["sync"]
        self.orca([self.stored(j, enabled=False)])
        with mock.patch.object(au.orca_job, "forget") as forget:
            self.assertEqual(j.disable(), (0, "이미 꺼져 있음"))
        forget.assert_called_once_with("sync")

    def test_disable_drops_the_last_run_record(self):
        j = self.jobs["sync"]
        self.orca([self.stored(j)])
        with mock.patch.object(au.orca_job, "forget") as forget:
            j.disable()
        forget.assert_called_once_with("sync")

    def test_precheck_runs_the_command_through_orca_job(self):
        pre = self.jobs["pressure-watch"].want()["precheck"]
        self.assertIn("orca_job.py run pressure-watch", pre)
        self.assertIn("--ok 0,1,2", pre)  # round exits 1 and 2 for warn and danger: results, not failures
        self.assertTrue(pre.endswith("pressure.py round"), pre)
        self.assertIn("--ok 0 ", self.jobs["sync"].want()["precheck"])

    def test_enable_creates_one_agentless_automation(self):
        fake = self.orca([])
        code, _ = self.jobs["sync"].enable(False)
        self.assertEqual(code, 0)
        (c,) = self.creates(fake)
        arg = dict(zip(c, c[1:]))
        self.assertEqual(arg["--name"], "agent-skills sync")
        self.assertEqual(arg["--trigger"], "*/15 * * * *")
        self.assertEqual(arg["--precheck"], self.jobs["sync"].want()["precheck"])
        self.assertEqual(arg["--workspace-mode"], "existing")

    def test_enable_from_a_worktree_creates_nothing(self):
        fake = self.orca([], main=False)
        self.assertEqual(self.jobs["sync"].enable(False)[0], 1)
        self.assertEqual(self.creates(fake), [])

    def test_status(self):
        j = self.jobs["orch-dash"]
        self.orca([])
        self.assertEqual(j.status()[0], "absent")
        self.orca([self.stored(j)])
        self.assertEqual(j.status()[0], "on")
        self.orca([self.stored(j, enabled=False)])
        self.assertEqual(j.status()[0], "off")
        self.orca([self.stored(j, rrule="*/5 * * * *")])
        self.assertIn("trigger", j.status()[1])

    def test_a_failed_last_run_shows_in_the_status(self):
        j = self.jobs["sync"]
        self.orca([self.stored(j)])
        with mock.patch.object(au.orca_job, "read", return_value={"ok": False, "message": "exit 3: no network"}):
            self.assertIn("마지막 실행 실패: exit 3: no network", j.status()[1])

    def test_enable_fixes_only_what_drifted_and_turns_on(self):
        j = self.jobs["sync"]
        fake = self.orca([self.stored(j, enabled=False, rrule="*/5 * * * *")])
        self.assertEqual(j.enable(False)[0], 0)
        (e,) = fake.edits()
        self.assertIn("--trigger", e)
        self.assertIn("--enabled", e)
        self.assertNotIn("--prompt", e)
        fake = self.orca([self.stored(j)])
        self.assertEqual(j.enable(False), (0, "이미 켜져 있음"))
        self.assertEqual(fake.edits(), [])

    def test_disable_keeps_it_registered(self):
        j = self.jobs["sync"]
        fake = self.orca([self.stored(j)])
        self.assertEqual(j.disable()[0], 0)
        (e,) = fake.edits()
        self.assertIn("--disabled", e)
        fake = self.orca([self.stored(j, enabled=False)])
        self.assertEqual(j.disable(), (0, "이미 꺼져 있음"))
        self.assertEqual(fake.edits(), [])


class Share(unittest.TestCase):
    s = au.ShareServer()

    def patch(self, loaded, spec, main=True, rc=0):
        self.install, self.uninstall = mock.Mock(return_value=rc), mock.Mock(return_value=rc)
        for p in (mock.patch.object(au.ss, "loaded", return_value=loaded),
                  mock.patch.object(au.ss, "plist_spec", return_value=spec),
                  mock.patch.object(au, "main_checkout", return_value=main),
                  mock.patch.object(au.sys, "platform", "darwin"),
                  mock.patch.object(au.ss, "cmd_install", self.install),
                  mock.patch.object(au.ss, "cmd_uninstall", self.uninstall)):
            p.start()
            self.addCleanup(p.stop)

    def test_default_off(self):
        self.assertFalse(self.s.default)

    def test_status(self):
        self.patch(False, None)
        self.assertEqual(self.s.status()[0], "absent")
        self.patch(True, au.ss.spec())
        self.assertEqual(self.s.status()[0], "on")
        self.patch(False, au.ss.spec())
        self.assertEqual(self.s.status()[0], "off")
        self.patch(True, {"ProgramArguments": ["python3", "-m", "http.server"]})
        self.assertIn("다름", self.s.status()[1])

    def test_enable(self):
        self.patch(True, au.ss.spec())
        self.assertEqual(self.s.enable(False), (0, "이미 켜져 있음"))
        self.patch(False, None, main=False)
        self.assertEqual(self.s.enable(False)[0], 1)
        self.install.assert_not_called()
        self.patch(True, {"Label": "old"})
        self.assertEqual(self.s.enable(False), (0, "켬"))
        self.install.assert_called_once()

    def test_disable(self):
        self.patch(False, None)
        self.assertEqual(self.s.disable(), (0, "이미 꺼져 있음"))
        self.patch(True, au.ss.spec())
        self.assertEqual(self.s.disable(), (0, "끔"))
        self.uninstall.assert_called_once()


class Plan(Base):
    """What setup.py's dry run lists is what apply does."""
    real_configured = staticmethod(au.Watch.configured)  # Base.orca patches the method; these tests need the real one

    def test_lists_installs_refreshes_and_the_reason_for_a_skip(self):
        self.orca([janitor()])
        sync_job = next(j for j in au.SHELL_JOBS if j.name == "sync")
        stale = ShellJobs.stored(None, sync_job, rrule="*/7 * * * *")
        self.orca([janitor(), stale])

        def status(job):
            return {"janitor": ("on", ""), "sync": ("on", ""), "pressure-watch": ("absent", ""),
                    "reap-hard": ("unconfigured", "no protect file"), "orch-dash": ("absent", ""),
                    "patrol": ("absent", ""), "land-executor": ("absent", ""),
                    "pstack-upstream": ("absent", ""), "sweep": ("absent", "")}[job.name]
        with mock.patch.object(au.ShellJob, "status", status), \
                mock.patch.object(au.PreviewWatch, "status", return_value=("absent", "")), \
                mock.patch.object(au.ShareServer, "status", return_value=("absent", "")):
            lines = au.plan()
        self.assertEqual([l for l in lines if not l.startswith("automation watch-")],
                         ["automation sync: refresh the registered job",
                          "automation pressure-watch: install",
                          "automation reap-hard: 건너뜀, no protect file",
                          "automation orch-dash: install"])

    def test_a_watch_key_the_run_is_about_to_write_counts_as_configured(self):
        w = au.Watch("watch-prs", "x")
        with mock.patch.object(au, "PLANNED_CONFIG", {}):
            self.assertFalse(self.real_configured(w))
        with mock.patch.object(au, "PLANNED_CONFIG", {"prs": {"inbox_to": "x"}}):
            self.assertTrue(self.real_configured(w))
        with mock.patch.object(au, "PLANNED_CONFIG", {"prs": {"model": "sonnet"}}):
            self.assertFalse(self.real_configured(w))

    def test_plan_clears_the_planned_config_and_apply_makes_the_same_calls(self):
        self.orca([janitor()])
        with mock.patch.object(au.ShellJob, "status", return_value=("absent", "")), \
                mock.patch.object(au.ShellJob, "enable", return_value=(0, "켬")) as sj, \
                mock.patch.object(au.PreviewWatch, "status", return_value=("absent", "")), \
                mock.patch.object(au.ShareServer, "status", return_value=("absent", "")):
            au.plan({"prs": {}})
            self.assertIsNone(au.PLANNED_CONFIG)
            self.assertEqual(au.cmd_apply(False), 0)
        self.assertEqual(sj.call_count, 5)


class Apply(Base):
    def setUp(self):
        for p in (mock.patch.object(au.ShellJob, "status", return_value=("on", "")),
                  mock.patch.object(au.ShareServer, "status", return_value=("absent", "")),
                  mock.patch.object(au.ShareServer, "enable")):
            self.share_enable = p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.share_enable.assert_not_called()

    def test_turns_on_only_default_on_never_set_up(self):
        fake = self.orca([])
        with mock.patch.object(au.PreviewWatch, "status", return_value=("absent", "")), \
                mock.patch.object(au.PreviewWatch, "enable") as pv, \
                mock.patch.object(au.ShellJob, "status", return_value=("absent", "")), \
                mock.patch.object(au.ShellJob, "enable", return_value=(0, "켬")) as sj:
            self.assertEqual(au.cmd_apply(False), 0)
        pv.assert_not_called()
        # janitor, sync, pressure-watch, reap-hard (status is stubbed past its gate), orch-dash: one shell job each, and no
        # second (load) job follows the janitor.
        self.assertEqual(sj.call_count, 5)
        self.assertEqual((fake.creates(), fake.schedules()), ([], []))

    def test_leaves_a_disabled_janitor_off(self):
        fake = self.orca([janitor(enabled=False)])
        with mock.patch.object(au.PreviewWatch, "status", return_value=("on", "")):
            self.assertEqual(au.cmd_apply(False), 0)
        self.assertEqual(fake.edits() + fake.schedules(), [])

    def test_leaves_a_disabled_shell_job_off(self):
        self.orca([janitor()])
        with mock.patch.object(au.PreviewWatch, "status", return_value=("on", "")), \
                mock.patch.object(au.ShellJob, "status", return_value=("off", "")), \
                mock.patch.object(au.ShellJob, "enable") as sj:
            self.assertEqual(au.cmd_apply(False), 0)
        sj.assert_not_called()

    def test_unknown_janitor_fails(self):
        self.orca(None)
        with mock.patch.object(au.PreviewWatch, "status", return_value=("absent", "")):
            self.assertEqual(au.cmd_apply(False), 1)


class Watch(Base):
    w = next(a for a in au.AUTOMATIONS if a.name == "watch-prs")

    def watch(self, enabled=True):
        return {"id": "W1", "name": "agent-skills watch-prs", "enabled": enabled, "rrule": "*/30 * * * *"}

    def test_a_model_alone_does_not_configure_the_watch(self):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "c.json"
            with mock.patch.object(au, "CONFIG", path):
                path.write_text(json.dumps({"prs": {"model": "sonnet"}}))
                self.assertFalse(au.Watch("watch-prs", "x").configured())
                path.write_text(json.dumps({"prs": {"model": "sonnet", "inbox_to": "run:r"}}))
                self.assertTrue(au.Watch("watch-prs", "x").configured())

    def test_status(self):
        self.orca([])
        self.assertEqual(self.w.status()[0], "unconfigured")
        self.orca([], configured=True)
        self.assertEqual(self.w.status()[0], "absent")
        self.orca([self.watch()])
        self.assertEqual(self.w.status()[0], "on")
        self.orca([self.watch(enabled=False)])
        self.assertEqual(self.w.status()[0], "off")

    def test_enable_needs_the_config_key(self):
        fake = self.orca([])
        code, msg = self.w.enable(False)
        self.assertEqual(code, 1)
        self.assertIn('"prs"', msg)
        self.assertEqual([c for c in fake.calls if "schedule" in c], [])

    def test_enable_creates_through_the_skill_schedule(self):
        fake = self.orca([], configured=True)
        self.assertEqual(self.w.enable(False)[0], 0)
        (call,) = [c for c in fake.calls if "schedule" in c]
        self.assertEqual(pathlib.Path(call[1]).name, "prs.py")
        self.assertEqual(call[-2:], ["schedule", "--write"])
        fake = self.orca([], main=False, configured=True)
        self.assertIn("worktree", self.w.enable(False)[1])

    def test_enable_turns_a_disabled_one_on_and_disable_keeps_it(self):
        fake = self.orca([self.watch(enabled=False)])
        self.assertEqual(self.w.enable(False), (0, "켬"))
        self.assertIn("--enabled", fake.edits()[0])
        fake = self.orca([self.watch()])
        self.assertEqual(self.w.disable()[0], 0)
        self.assertIn("--disabled", fake.edits()[0])

    def test_apply_passes_an_unconfigured_one_by(self):
        fake = self.orca([janitor()])
        with mock.patch.object(au.ShellJob, "status", return_value=("on", "")):
            self.assertEqual(au.cmd_apply(False), 0)
        self.assertEqual([c for c in fake.calls if "schedule" in c], [])


class Model(Base):
    """A registered job's model argument follows `<key>.model` of agent-skills.json; no key leaves it alone."""
    ARGS = "--model sonnet"
    FLAG = f"--extra-agent-args={ARGS}"

    def with_models(self, **models):
        """The config keys the jobs read: reap.model for the janitor, <key>.model for the rest."""
        for p in (mock.patch.object(au.reap, "config", return_value={"model": models["reap"]} if "reap" in models else {}),
                  mock.patch.object(au, "config_model", side_effect=lambda key: models.get(key))):
            p.start()
            self.addCleanup(p.stop)

    def watch(self, name, enabled=True, **over):
        return {"id": "W-" + name, "name": f"agent-skills {name}", "enabled": enabled, "rrule": "*/30 * * * *",
                "agentId": "claude", **over}

    # The janitor runs no agent, so it has no model: reap.model reaches no registered job any more.
    def test_the_janitor_has_no_model(self):
        self.with_models(reap="sonnet")
        self.assertIsNone(au.Janitor().model())
        self.assertEqual(au.Janitor().drift(janitor()), {})

    def test_apply_refreshes_the_model_of_jobs_that_are_on_or_off(self):
        self.with_models(reap="sonnet", reflect="sonnet", prs="sonnet", mentions="sonnet")
        autos = [janitor(enabled=False), self.watch("watch-prs", enabled=False),
                 self.watch("watch-mentions", extraAgentArgs="--model haiku"),
                 self.watch("reflect standing", extraAgentArgs=self.ARGS)]
        fake = self.orca(autos)
        with mock.patch.object(au.PreviewWatch, "status", return_value=("on", "")), \
                mock.patch.object(au.ShellJob, "status", return_value=("on", "")):
            self.assertEqual(au.cmd_apply(False), 0)
        self.assertEqual({e[e.index("--id") + 1]: e for e in fake.edits()}.keys(),
                         {"W-watch-prs", "W-watch-mentions"})
        for e in fake.edits():
            self.assertIn(self.FLAG, e)
            self.assertTrue({"--enabled", "--disabled", "--prompt", "--trigger"}.isdisjoint(e), e)

    def test_apply_without_the_keys_edits_nothing(self):
        self.with_models()
        fake = self.orca([janitor(), self.watch("watch-prs"),
                          self.watch("reflect standing", extraAgentArgs="--model haiku")])
        with mock.patch.object(au.PreviewWatch, "status", return_value=("on", "")), \
                mock.patch.object(au.ShellJob, "status", return_value=("on", "")):
            self.assertEqual(au.cmd_apply(False), 0)
        self.assertEqual(fake.edits(), [])

    def test_watch_enable_sends_the_model_with_enabled(self):
        self.with_models(prs="sonnet", mentions="sonnet")
        for name in ("watch-prs", "watch-mentions"):
            w = next(a for a in au.AUTOMATIONS if a.name == name)
            fake = self.orca([self.watch(name, enabled=False)])
            self.assertEqual(w.enable(False), (0, "켬"))
            (edit,) = fake.edits()
            self.assertIn(self.FLAG, edit)
            self.assertIn("--enabled", edit)
            fake = self.orca([self.watch(name)])
            self.assertEqual(w.enable(False)[0], 0)
            self.assertEqual([self.FLAG in e and "--enabled" in e for e in fake.edits()], [False])

    def test_watch_enable_without_the_key_sends_only_enabled(self):
        self.with_models()
        w = next(a for a in au.AUTOMATIONS if a.name == "watch-prs")
        fake = self.orca([self.watch("watch-prs", enabled=False, extraAgentArgs="--model haiku")])
        self.assertEqual(w.enable(False), (0, "켬"))
        self.assertEqual(fake.edits()[0][1:], ["automations", "edit", "--id", "W-watch-prs", "--enabled", "--json"])

    def test_a_job_on_another_agent_keeps_its_arguments(self):
        self.with_models(prs="sonnet")
        fake = self.orca([self.watch("watch-prs", agentId="codex")])
        w = next(a for a in au.AUTOMATIONS if a.name == "watch-prs")
        self.assertEqual(w.refresh_model(), None)
        self.assertEqual(fake.edits(), [])

    def test_an_unreadable_list_is_not_success_when_a_model_is_wanted(self):
        self.with_models(reflect="sonnet", prs="sonnet")
        self.orca(None)
        self.assertEqual(au.REFLECT.refresh_model()[0], 1)
        self.assertEqual(next(a for a in au.AUTOMATIONS if a.name == "watch-prs").refresh_model()[0], 1)
        self.with_models()
        self.assertIsNone(au.REFLECT.refresh_model())

    def test_a_failed_edit_fails_apply(self):
        self.with_models(prs="sonnet")
        fake = self.orca([janitor(), self.watch("watch-prs")])
        with mock.patch.object(au, "sh", side_effect=lambda c: subprocess.CompletedProcess(c, 1 if c[1:3] == ["automations", "edit"] else 0,
                                                                                              fake(c).stdout, "boom")), \
                mock.patch.object(au.PreviewWatch, "status", return_value=("on", "")), \
                mock.patch.object(au.ShellJob, "status", return_value=("on", "")):
            self.assertEqual(au.cmd_apply(False), 1)


class Cli(Base):
    def test_unknown_name(self):
        self.assertEqual(au.main(["enable", "nope"]), 2)


if __name__ == "__main__":
    unittest.main()
