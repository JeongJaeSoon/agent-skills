#!/usr/bin/env python3
import json, pathlib, subprocess, sys, unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import automations as au  # noqa: E402

WANT = au.janitor_want(f"path:{au.REPO}")


def janitor(enabled=True, **over):
    a = {"id": "J1", "name": au.JANITOR_NAME, "enabled": enabled, "rrule": WANT["trigger"], "prompt": WANT["prompt"],
         "precheck": {"command": WANT["precheck"], "timeoutSeconds": WANT["precheck-timeout"]},
         "workspaceId": f"x::{au.REPO}"}
    a.update(over)
    return a


LOAD = au.janitor_want(f"path:{au.REPO}", load=True)


def load_job(enabled=True, **over):
    return janitor(enabled, **{"id": "J2", "name": au.reap.LOAD_NAME, "rrule": LOAD["trigger"],
                               "precheck": {"command": LOAD["precheck"], "timeoutSeconds": LOAD["precheck-timeout"]}, **over})


class FakeOrca:
    """Stands in for `sh`: answers `orca automations list` from a list and records every other call."""
    def __init__(self, autos):
        self.autos, self.calls = autos, []

    def __call__(self, cmd):
        self.calls.append(cmd)
        if cmd[1:3] == ["automations", "list"]:
            out = json.dumps({"ok": True, "result": {"automations": self.autos}}) if self.autos is not None else ""
            return subprocess.CompletedProcess(cmd, 0 if self.autos is not None else 1, out, "boom")
        return subprocess.CompletedProcess(cmd, 0, "created", "")

    def edits(self):
        return [c for c in self.calls if c[1:3] == ["automations", "edit"]]

    def schedules(self):
        return [c for c in self.calls if str(au.REAP) in c]


class Base(unittest.TestCase):
    def orca(self, autos, main=True, configured=False):
        fake = FakeOrca(autos)
        for p in (mock.patch.object(au, "sh", fake), mock.patch.object(au.shutil, "which", return_value="/x/orca"),
                  mock.patch.object(au, "main_checkout", return_value=main),
                  mock.patch.object(au.Watch, "configured", return_value=configured)):
            p.start()
            self.addCleanup(p.stop)
        return fake


class JanitorLoad(Base):
    """The load job is a second Orca automation: its own name, trigger and `precheck --load`, found and fixed apart."""
    j = au.JanitorLoad()

    def test_shape(self):
        self.assertTrue(LOAD["precheck"].endswith("precheck --load"), LOAD["precheck"])
        self.assertNotEqual(LOAD["trigger"], WANT["trigger"])
        self.assertEqual(WANT["trigger"], "17 */3 * * *")

    def test_status_finds_only_its_own_job(self):
        self.orca([janitor()])
        self.assertEqual(self.j.status()[0], "absent")
        self.orca([janitor(), load_job()])
        self.assertEqual(self.j.status(), ("on", f"{LOAD['trigger']} · {au.REPO}"))

    def test_enable_fixes_its_own_drift(self):
        fake = self.orca([janitor(), load_job(precheck={"command": WANT["precheck"], "timeoutSeconds": 300})])
        self.assertEqual(self.j.enable(False)[0], 0)
        edit = fake.edits()[0]
        self.assertEqual((edit[edit.index("--id") + 1], edit[edit.index("--precheck") + 1]), ("J2", LOAD["precheck"]))

    def test_enable_absent_creates_with_load(self):
        fake = self.orca([janitor()])
        self.assertEqual(self.j.enable(False)[0], 0)
        self.assertIn("--load", fake.schedules()[0])

    def test_absent_follows_the_janitor(self):
        """apply must not turn reaping back on for a user who turned the janitor off, and the load job runs where
        the janitor was moved to."""
        fake = self.orca([janitor(enabled=False)])
        code, msg = self.j.enable(False)
        self.assertEqual((code, fake.schedules()), (0, []), msg)
        fake = self.orca([janitor(workspaceId="x::/w/runner")])
        self.assertEqual(self.j.enable(False)[0], 0)
        argv = fake.schedules()[0]
        self.assertEqual(argv[argv.index("--workspace") + 1], "path:/w/runner")


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

    def test_enable_absent_creates_through_reap_schedule(self):
        fake = self.orca([])
        self.assertEqual(self.j.enable(False)[0], 0)
        self.assertEqual([c[-2:] for c in fake.schedules()], [["schedule", "--write"]])

    def test_enable_absent_refuses_a_worktree(self):
        fake = self.orca([], main=False)
        code, msg = self.j.enable(False)
        self.assertEqual(code, 1)
        self.assertIn("worktree", msg)
        self.assertEqual(self.j.enable(True)[0], 1)
        self.assertEqual(fake.schedules(), [])

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


class Pressure(unittest.TestCase):
    p = au.PressureWatch()

    def patch(self, loaded, spec, main=True, rc=0):
        self.install, self.uninstall = mock.Mock(return_value=rc), mock.Mock(return_value=rc)
        for p in (mock.patch.object(au.pressure, "loaded", return_value=loaded),
                  mock.patch.object(au.pressure, "plist_spec", return_value=spec),
                  mock.patch.object(au, "main_checkout", return_value=main),
                  mock.patch.object(au.sys, "platform", "darwin"),
                  mock.patch.object(au.pressure, "cmd_install", self.install),
                  mock.patch.object(au.pressure, "cmd_uninstall", self.uninstall)):
            p.start()
            self.addCleanup(p.stop)

    def current(self):
        return au.pressure.spec(au.pressure.config()["interval"])

    def test_default_on(self):
        self.assertTrue(self.p.default)

    def test_status(self):
        self.patch(False, None)
        self.assertEqual(self.p.status()[0], "absent")
        self.patch(True, self.current())
        self.assertEqual(self.p.status()[0], "on")
        self.patch(False, self.current())
        self.assertEqual(self.p.status()[0], "off")
        self.patch(True, {"ProgramArguments": ["python3", "/elsewhere/pressure.py", "round"]})
        self.assertIn("/elsewhere/pressure.py", self.p.status()[1])

    def test_enable(self):
        self.patch(True, self.current())
        self.assertEqual(self.p.enable(False), (0, "이미 켜져 있음"))
        self.patch(False, None, main=False)
        self.assertEqual(self.p.enable(False)[0], 1)
        self.install.assert_not_called()
        self.patch(False, None)
        self.assertEqual(self.p.enable(False), (0, "켬"))
        self.install.assert_called_once()
        self.patch(False, None, rc=1)
        self.assertEqual(self.p.enable(False), (1, "실패 (위 출력)"))

    def test_disable(self):
        self.patch(False, None)
        self.assertEqual(self.p.disable(), (0, "이미 꺼져 있음"))
        self.patch(True, self.current())
        self.assertEqual(self.p.disable(), (0, "끔"))
        self.uninstall.assert_called_once()


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


class Apply(Base):
    def setUp(self):
        for p in (mock.patch.object(au.PressureWatch, "status", return_value=("on", "")),
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
                mock.patch.object(au.PressureWatch, "status", return_value=("absent", "")), \
                mock.patch.object(au.PressureWatch, "enable", return_value=(0, "켬")) as pr:
            self.assertEqual(au.cmd_apply(False), 0)
        pv.assert_not_called()
        pr.assert_called_once()
        self.assertEqual(["--load" in c for c in fake.schedules()], [False, True])

    def test_leaves_a_disabled_janitor_off(self):
        fake = self.orca([janitor(enabled=False), load_job(enabled=False)])
        with mock.patch.object(au.PreviewWatch, "status", return_value=("on", "")):
            self.assertEqual(au.cmd_apply(False), 0)
        self.assertEqual(fake.edits() + fake.schedules(), [])

    def test_unknown_janitor_fails(self):
        self.orca(None)
        with mock.patch.object(au.PreviewWatch, "status", return_value=("absent", "")):
            self.assertEqual(au.cmd_apply(False), 1)


class Watch(Base):
    w = next(a for a in au.AUTOMATIONS if a.name == "watch-prs")

    def watch(self, enabled=True):
        return {"id": "W1", "name": "agent-skills watch-prs", "enabled": enabled, "rrule": "*/30 * * * *"}

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
        fake = self.orca([janitor(), load_job()])
        with mock.patch.object(au.PressureWatch, "status", return_value=("on", "")):
            self.assertEqual(au.cmd_apply(False), 0)
        self.assertEqual([c for c in fake.calls if "schedule" in c], [])


class Cli(Base):
    def test_unknown_name(self):
        self.assertEqual(au.main(["enable", "nope"]), 2)


if __name__ == "__main__":
    unittest.main()
