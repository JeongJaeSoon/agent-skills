#!/usr/bin/env python3
"""review_pick.py, run as a subprocess against a fake codex and a throwaway git checkout."""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

PICK = pathlib.Path(__file__).resolve().parent / "review_pick.py"
GIT = shutil.which("git")


class ReviewPickTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        os.symlink(GIT, self.bin / "git")
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        subprocess.run([GIT, "init", "-q"], cwd=self.repo, check=True)
        subprocess.run([GIT, "remote", "add", "origin", "git@example.test:acme/app.git"], cwd=self.repo, check=True)
        self.config = self.tmp / "agent-skills.json"

    def codex(self, body):
        """A fake codex; PATH holds only it and git, so its body names other tools by absolute path."""
        path = self.bin / "codex"
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def run_pick(self, config=None, *args):
        if config is not None:
            self.config.write_text(config if isinstance(config, str) else json.dumps(config))
        env = {"PATH": str(self.bin), "HOME": str(self.tmp), "AGENT_SKILLS_CONFIG": str(self.config)}
        r = subprocess.run([sys.executable, "-I", str(PICK), "--repo", str(self.repo), *args],
                           capture_output=True, text=True, env=env)
        return r.returncode, r.stdout.strip()

    def test_auto_is_the_default_and_picks_codex_when_it_answers(self):
        self.codex('[ "$1 $2" = "login status" ] && echo "Logged in" && exit 0; exit 9')
        code, out = self.run_pick()
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("codex "), out)

    def test_auto_falls_back_to_claude_when_codex_is_missing(self):
        code, out = self.run_pick({"review": {"reviewer": "auto"}})
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("claude "), out)
        self.assertIn("not on PATH", out)

    def test_auto_falls_back_when_codex_is_logged_out(self):
        self.codex('echo "Not logged in" >&2; exit 1')
        code, out = self.run_pick()
        self.assertEqual((code, out.split()[0]), (0, "claude"))
        self.assertIn("exited 1: Not logged in", out)

    def test_auto_falls_back_when_codex_hangs(self):
        self.codex(f"exec {shutil.which('sleep')} 30")
        start = time.monotonic()
        code, out = self.run_pick(None, "--timeout", "1")
        self.assertLess(time.monotonic() - start, 10)
        self.assertEqual((code, out.split()[0]), (0, "claude"))
        self.assertIn("no answer in 1 s", out)

    def test_codex_setting_waits_instead_of_falling_back(self):
        self.codex("exit 1")
        code, out = self.run_pick({"review": {"reviewer": "codex"}})
        self.assertEqual((code, out.split()[0]), (3, "none"))

    def test_codex_setting_picks_codex_when_it_answers(self):
        self.codex("exit 0")
        self.assertEqual(self.run_pick({"review": {"reviewer": "codex"}})[1].split()[0], "codex")

    def test_off_never_calls_codex_and_keeps_the_review(self):
        marker = self.tmp / "called"
        self.codex(f"echo > {marker}; exit 0")
        code, out = self.run_pick({"review": {"reviewer": "off"}})
        self.assertEqual((code, out.split()[0]), (0, "claude"))
        self.assertFalse(marker.exists())

    def test_external_llm_false_wins_over_every_setting(self):
        marker = self.tmp / "called"
        self.codex(f"echo > {marker}; exit 0")
        for setting in ("auto", "codex", "off"):
            code, out = self.run_pick({"review": {"reviewer": setting}, "repos": {"acme/app": {"external_llm": False}}})
            self.assertEqual((code, out.split()[0]), (0, "claude"), setting)
            self.assertIn("repos.acme/app.external_llm is false", out)
        self.assertFalse(marker.exists())

    def test_external_llm_on_another_repo_does_not_apply(self):
        self.codex("exit 0")
        out = self.run_pick({"repos": {"acme/other": {"external_llm": False}}})[1]
        self.assertTrue(out.startswith("codex "), out)

    def test_unknown_value_exits_2(self):
        code, out = self.run_pick({"review": {"reviewer": "gpt"}})
        self.assertEqual((code, out.split()[0]), (2, "none"))
        self.assertIn("auto | codex | off", out)

    def test_bad_json_exits_2(self):
        code, out = self.run_pick("{")
        self.assertEqual((code, out.split()[0]), (2, "none"))
        self.assertIn("cannot be read as JSON", out)


if __name__ == "__main__":
    unittest.main()
