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
    def orca(self, autos, main=True):
        fake = FakeOrca(autos)
        for p in (mock.patch.object(au, "sh", fake), mock.patch.object(au.shutil, "which", return_value="/x/orca"),
                  mock.patch.object(au, "main_checkout", return_value=main)):
            p.start()
            self.addCleanup(p.stop)
        return fake


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


class Apply(Base):
    def setUp(self):
        p = mock.patch.object(au.PressureWatch, "status", return_value=("on", ""))
        p.start()
        self.addCleanup(p.stop)

    def test_turns_on_only_default_on_never_set_up(self):
        fake = self.orca([])
        with mock.patch.object(au.PreviewWatch, "status", return_value=("absent", "")), \
                mock.patch.object(au.PreviewWatch, "enable") as pv, \
                mock.patch.object(au.PressureWatch, "status", return_value=("absent", "")), \
                mock.patch.object(au.PressureWatch, "enable", return_value=(0, "켬")) as pr:
            self.assertEqual(au.cmd_apply(False), 0)
        pv.assert_not_called()
        pr.assert_called_once()
        self.assertEqual(len(fake.schedules()), 1)

    def test_leaves_a_disabled_janitor_off(self):
        fake = self.orca([janitor(enabled=False)])
        with mock.patch.object(au.PreviewWatch, "status", return_value=("on", "")):
            self.assertEqual(au.cmd_apply(False), 0)
        self.assertEqual(fake.edits() + fake.schedules(), [])

    def test_unknown_janitor_fails(self):
        self.orca(None)
        with mock.patch.object(au.PreviewWatch, "status", return_value=("absent", "")):
            self.assertEqual(au.cmd_apply(False), 1)


class Cli(Base):
    def test_unknown_name(self):
        self.assertEqual(au.main(["enable", "nope"]), 2)


if __name__ == "__main__":
    unittest.main()
