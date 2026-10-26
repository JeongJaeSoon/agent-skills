#!/usr/bin/env python3
"""scripts/setup.py against a throwaway HOME. Every --apply here writes only under a temp dir."""
import json, os, pathlib, re, shutil, subprocess, sys, tempfile, unittest

HERE = pathlib.Path(__file__).resolve().parent
SETUP = HERE / "setup.py"
BUNDLE = HERE / "recommended-settings.json"


class Setup(unittest.TestCase):
    def setUp(self):
        self.home = pathlib.Path(tempfile.mkdtemp(prefix="setup-test-"))
        self.addCleanup(shutil.rmtree, self.home)
        self.cfg = self.home / ".claude"
        self.cfg.mkdir()
        self.settings = self.cfg / "settings.json"
        self.state = self.home / "state"
        self.env = {**os.environ, "HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.cfg),
                    "AGENT_SKILLS_STATE": str(self.state), "GH_CONFIG_DIR": str(self.home / "gh")}
        self.env.pop("CLAUDE_SETTINGS", None)

    def run_setup(self, *args, ok=True):
        p = subprocess.run([sys.executable, str(SETUP), "--here", "--language", "Korean", *args],
                           capture_output=True, text=True, env=self.env)
        if ok:
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        return p

    def plan_hash(self, *args):
        out = self.run_setup(*args).stdout
        m = re.search(r"--apply ([0-9a-f]{12})$", out.strip())
        self.assertTrue(m, out)
        return m[1]

    def snapshot(self):
        # gh keeps its own state under HOME even for a read; only this script's writes count.
        return {str(p.relative_to(self.home)): p.read_bytes() for p in self.home.rglob("*")
                if p.is_file() and "gh" not in p.relative_to(self.home).parts}

    def test_dry_run_writes_nothing(self):
        self.settings.write_text('{"theme": "dark"}\n')
        before = self.snapshot()
        out = self.run_setup().stdout
        self.assertIn("+  \"outputStyle\": \"Explanatory\"", out)
        self.assertIn("(new file)", out)
        self.assertEqual(self.snapshot(), before)

    def test_apply_then_rerun_is_a_no_op(self):
        self.run_setup("--apply", self.plan_hash())
        s = json.loads(self.settings.read_text())
        self.assertEqual(s["language"], "Korean")
        self.assertEqual(s["permissions"]["defaultMode"], "auto")
        self.assertEqual(s["autoMode"]["allow"][0], "$defaults")
        self.assertEqual(s["env"]["CLAUDE_CODE_PLUGIN_DIRS"], str(HERE.parent))
        skills = json.loads((self.cfg / "agent-skills.json").read_text())
        self.assertEqual(skills["reflect"]["skill_repository"], str(HERE.parent))
        before = self.snapshot()
        out = self.run_setup().stdout
        self.assertIn("no change", out)
        self.assertNotIn("--apply", out)
        self.assertEqual(self.snapshot(), before)

    def test_existing_values_are_kept(self):
        mine = {"language": "Japanese", "outputStyle": "default", "permissions": {"allow": ["Bash(make test)"]},
                "autoMode": {"allow": ["$defaults", "my own rule"]}, "theme": "dark"}
        self.settings.write_text(json.dumps(mine))
        (self.cfg / "agent-skills.json").write_text('{"reap": {"user_folders": ["~/x"]}}')
        out = self.run_setup().stdout
        self.assertIn("kept    language = 'Japanese'", out)
        self.run_setup("--apply", self.plan_hash())
        s = json.loads(self.settings.read_text())
        self.assertEqual((s["language"], s["outputStyle"], s["theme"]), ("Japanese", "default", "dark"))
        self.assertEqual(s["permissions"]["allow"][0], "Bash(make test)")
        self.assertEqual(s["autoMode"]["allow"][:2], ["$defaults", "my own rule"])
        self.assertEqual(json.loads((self.cfg / "agent-skills.json").read_text())["reap"]["user_folders"], ["~/x"])

    def test_wrong_or_stale_hash_is_refused(self):
        h = self.plan_hash()
        p = self.run_setup("--apply", "000000000000", ok=False)
        self.assertEqual(p.returncode, 1)
        self.settings.write_text('{"theme": "light"}')
        p = self.run_setup("--apply", h, ok=False)
        self.assertIn("does not match", p.stderr)
        self.assertEqual(self.settings.read_text(), '{"theme": "light"}')

    def test_rollback_restores_the_original_bytes(self):
        original = '{"theme":"dark",  "env": {"X": "1"}}'
        self.settings.write_text(original)
        out = self.run_setup("--apply", self.plan_hash()).stdout
        undo = re.search(r"cp (\S+) (\S+settings\.json)", out)
        subprocess.run(["cp", undo[1], undo[2]], check=True)
        self.assertEqual(self.settings.read_text(), original)

    def test_a_removed_item_is_not_offered_again_but_a_new_one_is(self):
        self.run_setup("--apply", self.plan_hash())
        s = json.loads(self.settings.read_text())
        dropped = s["autoMode"]["soft_deny"].pop()
        del s["outputStyle"]
        self.settings.write_text(json.dumps(s))
        self.assertIn("no change", self.run_setup().stdout)
        bundle = json.loads(BUNDLE.read_text())
        bundle["settings"]["autoMode"]["soft_deny"].append("a rule added in a later release")
        newer = self.home / "newer.json"
        newer.write_text(json.dumps(bundle))
        out = self.run_setup("--bundle", str(newer)).stdout
        self.assertIn("a rule added in a later release", out)
        self.run_setup("--bundle", str(newer), "--apply", self.plan_hash("--bundle", str(newer)))
        s = json.loads(self.settings.read_text())
        self.assertIn("a rule added in a later release", s["autoMode"]["soft_deny"])
        self.assertNotIn(dropped, s["autoMode"]["soft_deny"])
        self.assertNotIn("outputStyle", s)

    def test_repository_rules_wait_for_a_gh_login(self):
        out = self.run_setup().stdout
        self.assertIn("gh: not logged in", out)
        self.run_setup("--apply", self.plan_hash())
        rules = json.loads(self.settings.read_text())["autoMode"]
        self.assertFalse([r for r in rules["allow"] + rules["environment"] if "user's repositories" in r])
        fake = self.home / "bin"
        fake.mkdir()
        (fake / "gh").write_text("#!/bin/sh\necho octocat\n")
        (fake / "gh").chmod(0o755)
        self.env["PATH"] = f"{fake}{os.pathsep}{self.env['PATH']}"
        self.assertIn("github.com/octocat/*", self.run_setup().stdout)

    def test_plugin_dirs_pointing_elsewhere_is_refused(self):
        self.settings.write_text(json.dumps({"env": {"CLAUDE_CODE_PLUGIN_DIRS": "/somewhere/else"}}))
        p = self.run_setup(ok=False)
        self.assertIn("/somewhere/else", p.stderr)

    def test_marketplace_copy_is_turned_off(self):
        self.settings.write_text(json.dumps({"enabledPlugins": {"agent-skills@jeongjaesoon": True}}))
        self.run_setup("--apply", self.plan_hash())
        self.assertIs(json.loads(self.settings.read_text())["enabledPlugins"]["agent-skills@jeongjaesoon"], False)


class Bundle(unittest.TestCase):
    def test_has_no_internal_names(self):
        names = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG_DIR") or "~/.config/agent-skills").expanduser() / "internal-names.txt"
        if not names.is_file():
            self.skipTest(f"{names} not on this PC")
        patterns = [l.strip() for l in names.read_text().splitlines() if l.strip() and not l.startswith("#")]
        text = BUNDLE.read_text()
        hits = [p for p in patterns if re.search(p, text, re.I)]
        self.assertEqual(hits, [])

    def test_rules_name_their_limits(self):
        rules = json.loads(BUNDLE.read_text())["settings"]["autoMode"]["allow"]
        for rule in rules[:-1]:
            self.assertIn("Not covered", rule, rule[:60])
        self.assertIn("only the user's own chat messages count", rules[-1])


if __name__ == "__main__":
    unittest.main()
