#!/usr/bin/env python3
"""scripts/setup.py against a throwaway HOME. Every --apply here writes only under a temp dir."""
import contextlib, importlib.util, io, json, os, pathlib, re, shutil, subprocess, sys, tempfile, types, unittest
from unittest import mock

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
        for k in ("CLAUDE_SETTINGS", "AGENT_SKILLS_CONFIG", "AGENT_SKILLS_CONFIG_DIR"):
            self.env.pop(k, None)

    def run_setup(self, *args, ok=True):
        p = subprocess.run([sys.executable, "-I", str(SETUP), "--here", "--language", "Korean", *args],
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

    def test_marketplace_change_is_shown(self):
        self.settings.write_text(json.dumps({"enabledPlugins": {"agent-skills@jeongjaesoon": True}}))
        self.assertIn("changed enabledPlugins.agent-skills@jeongjaesoon = false", self.run_setup().stdout)

    def test_personal_keys_join_the_plan_without_overwriting(self):
        mine = self.home / "personal"
        mine.mkdir()
        self.env["AGENT_SKILLS_CONFIG_DIR"] = str(self.home)
        (mine / "agent-skills.json").write_text(json.dumps({
            "prs": {"kinds": ["review_request"]}, "reap": {"user_folders": ["~/scratch"]},
            "tracker": {"adapter": "jira", "rules_file": "tracker-rules.md"}, "notes": {"adapter": "markdown"}}))
        (self.cfg / "agent-skills.json").write_text(json.dumps({"notes": {"adapter": "obsidian"}}))
        before = self.snapshot()
        out = self.run_setup().stdout
        self.assertIn('"review_request"', out)
        self.assertIn("kept    notes.adapter = 'obsidian' (personal 'markdown')", out)
        self.assertEqual(self.snapshot(), before)
        self.run_setup("--apply", self.plan_hash())
        skills = json.loads((self.cfg / "agent-skills.json").read_text())
        self.assertEqual(skills["prs"], {"kinds": ["review_request"]})
        self.assertEqual(skills["reap"]["user_folders"], ["~/scratch"])
        self.assertEqual(skills["tracker"], {"adapter": "jira", "rules_file": str(mine / "tracker-rules.md")})
        self.assertEqual(skills["notes"], {"adapter": "obsidian"})
        del skills["prs"]
        (self.cfg / "agent-skills.json").write_text(json.dumps(skills))
        self.assertIn("no change", self.run_setup().stdout)

    def test_agent_skills_json_stays_where_the_skills_read_it_under_another_profile(self):
        profile = self.home / "profile"
        profile.mkdir()
        self.env["CLAUDE_CONFIG_DIR"] = str(profile)
        self.run_setup("--apply", self.plan_hash())
        self.assertIn("env", json.loads((profile / "settings.json").read_text()))
        self.assertIn("reflect", json.loads((self.cfg / "agent-skills.json").read_text()))
        self.assertFalse((profile / "agent-skills.json").exists())

    def test_actions_only_takes_no_hash(self):
        p = self.run_setup("--actions-only", "--apply", "000000000000", ok=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("--actions-only takes none of", p.stderr)

    def test_a_failed_write_prints_rollback_for_the_files_already_written(self):
        self.settings.write_text('{"theme": "dark"}')
        h = self.plan_hash()
        self.state.mkdir()
        self.state.chmod(0o500)  # the record, written last, cannot be created
        self.addCleanup(self.state.chmod, 0o700)
        p = self.run_setup("--apply", h, ok=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("writing", p.stderr)
        self.assertRegex(p.stdout, r"rollback:\n  cp \S+ \S+settings\.json\n  rm \S+agent-skills\.json")

    def drifted(self, *user_rules):
        bundle = self.home / "bundle.json"
        bundle.write_text(json.dumps({"settings": {"autoMode": {"allow": [
            "$defaults", "Test round: run {checkout}/reap.py on {state}/plan.json. Not covered: rm.",
            "Other round: the new words."]}}}))
        self.settings.write_text(json.dumps({"autoMode": {"allow": ["$defaults", *user_rules]}}))
        return "--bundle", str(bundle)

    def allow(self):
        return json.loads(self.settings.read_text())["autoMode"]["allow"]

    def test_a_drifted_rule_is_reported_and_kept_without_the_flag(self):
        old, mine = "Test round: run <checkout>/reap.py. Not covered: rm.", "My round: my own words."
        bundle = self.drifted(old, mine)
        out = self.run_setup(*bundle).stdout
        self.assertIn("drifted autoMode.allow 'Test round'", out)
        self.run_setup(*bundle, "--apply", self.plan_hash(*bundle))
        self.assertEqual(self.allow(), ["$defaults", old, mine, "Other round: the new words."])

    def test_replace_drifted_puts_the_filled_rule_in_its_place(self):
        old, mine = "Test round: run <checkout>/reap.py. Not covered: rm.", "My round: my own words."
        bundle = (*self.drifted(old, mine), "--replace-drifted")
        new = f"Test round: run {HERE.parent}/reap.py on {self.state}/plan.json. Not covered: rm."
        before = self.snapshot()
        out = self.run_setup(*bundle).stdout
        self.assertIn(f'-      "{old}"', out)
        self.assertIn(f'+      "{new}"', out)
        self.assertEqual(self.snapshot(), before)
        out = self.run_setup(*bundle, "--apply", self.plan_hash(*bundle)).stdout
        self.assertEqual(self.allow(), ["$defaults", new, mine])
        self.assertFalse((self.cfg / "agent-skills.json").exists())
        self.assertNotIn("env", json.loads(self.settings.read_text()))
        backups = list(self.cfg.glob("settings.json.bak-setup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text())["autoMode"]["allow"], ["$defaults", old, mine])
        self.assertIn(f"cp {backups[0].resolve()} {self.settings.resolve()}", out)

    def test_named_labels_replace_only_those(self):
        old, other = "Test round: the old words.", "Other round: the old words."
        bundle = self.drifted(old, other)
        args = (*bundle, "--replace-drifted", "Other round:")
        self.run_setup(*args, "--apply", self.plan_hash(*args))
        self.assertEqual(self.allow(), ["$defaults", old, "Other round: the new words."])
        p = self.run_setup(*bundle, "--replace-drifted", "Typo round", ok=False)
        self.assertIn("no drifted rule labelled 'Typo round'", p.stderr)

    def test_two_rules_with_one_label_are_left_alone(self):
        rules = ["Test round: one.", "Test round: two."]
        bundle = (*self.drifted(*rules), "--replace-drifted")
        out = self.run_setup(*bundle).stdout
        self.assertIn("2 rules in autoMode.allow start with 'Test round:'", out)
        self.assertNotIn("--apply", out)
        self.assertEqual(self.allow(), ["$defaults", *rules])

    def test_settings_flag_picks_the_file_and_names_it_first(self):
        other = self.home / "profile" / "settings.json"
        other.parent.mkdir()
        other.write_text("{}")
        out = self.run_setup("--settings", str(other)).stdout
        self.assertEqual(out.splitlines()[0], f"target  {other}")
        self.run_setup("--settings", str(other), "--apply", self.plan_hash("--settings", str(other)))
        self.assertIn("autoMode", json.loads(other.read_text()))
        self.assertFalse(self.settings.exists())
        self.assertEqual(self.run_setup().stdout.splitlines()[0], f"target  {self.settings}")


class Steps(unittest.TestCase):
    """setup.py in-process with subprocess.run and bootstrap stubbed, so no test runs the real sync."""

    def setUp(self):
        self.home = pathlib.Path(tempfile.mkdtemp(prefix="setup-test-"))
        self.addCleanup(shutil.rmtree, self.home)
        self.cfg = self.home / ".claude"
        env = {"HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.cfg), "AGENT_SKILLS_STATE": str(self.home / "state")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in ("CLAUDE_SETTINGS", "AGENT_SKILLS_CONFIG", "AGENT_SKILLS_CONFIG_DIR"):
            os.environ.pop(k, None)
        spec = importlib.util.spec_from_file_location("setup_under_test", SETUP)
        self.setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.setup)
        self.calls, self.fail = [], set()
        self.bootstrap = types.SimpleNamespace(scheduler=lambda write: None)
        for p in (mock.patch.object(self.setup.subprocess, "run", self.fake_run),
                  mock.patch.object(self.setup, "preflight", lambda args: None),
                  mock.patch.dict(sys.modules, {"bootstrap": self.bootstrap})):
            p.start()
            self.addCleanup(p.stop)

    def fake_run(self, cmd, **kw):
        script = pathlib.Path(cmd[1]).name if len(cmd) > 1 else cmd[0]
        self.calls.append((script, (self.cfg / "settings.json").exists()))
        return subprocess.CompletedProcess(cmd, 1 if script in self.fail else 0, "", "")

    def main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = self.setup.main(["--language", "Korean", *args])
        return rc, out.getvalue(), err.getvalue()

    def plan_hash(self, *args):
        rc, out, _ = self.main(*args)
        self.assertEqual(rc, 0, out)
        return re.search(r"--apply ([0-9a-f]{12})$", out.strip())[1]

    def scripts(self):
        return [s for s, _ in self.calls if s.endswith(".py")]

    def test_update_dry_run_does_not_sync(self):
        rc, out, _ = self.main("--update")
        self.assertEqual(rc, 0)
        self.assertIn("first   skills-sync sync", out)
        self.assertNotIn("sync.py", self.scripts())
        self.assertFalse(self.cfg.exists())

    def test_update_apply_syncs_once_before_writing(self):
        h = self.plan_hash("--update")
        rc, out, err = self.main("--update", "--apply", h)
        self.assertEqual(rc, 0, err)
        self.assertEqual([s for s in self.scripts() if s == "sync.py"], ["sync.py"])
        self.assertIn(("sync.py", False), self.calls)
        self.assertTrue((self.cfg / "settings.json").exists())

    def test_update_apply_stops_when_sync_fails(self):
        h = self.plan_hash("--update")
        self.fail.add("sync.py")
        rc, _, err = self.main("--update", "--apply", h)
        self.assertEqual(rc, 1)
        self.assertIn("skills-sync sync failed", err)
        self.assertFalse(self.cfg.exists())

    def test_a_failed_step_is_reported_instead_of_done(self):
        def scheduler(write):
            sys.exit("launchctl bootstrap failed")
        self.bootstrap.scheduler = scheduler
        self.fail.add("automations.py")
        rc, out, err = self.main("--apply", self.plan_hash())
        self.assertEqual(rc, 1)
        self.assertNotIn("done.", out)
        self.assertIn("launchd sync job (launchctl bootstrap failed)", err)
        self.assertIn("skills-sync setup apply", err)
        self.assertIn("setup_check.py", self.scripts())

    def test_actions_only_writes_no_settings(self):
        rc, out, err = self.main("--actions-only")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.scripts(), ["sync.py", "automations.py", "setup_check.py"])
        self.assertFalse(self.cfg.exists())
        self.fail.add("automations.py")
        rc, _, err = self.main("--actions-only")
        self.assertEqual(rc, 1)
        self.assertIn("these steps failed: skills-sync setup apply", err)


class Bootstrap(unittest.TestCase):
    def load(self, env):
        with mock.patch.dict(os.environ, env):
            for k in ("CLAUDE_SETTINGS", "CLAUDE_CONFIG_DIR"):
                if k not in env:
                    os.environ.pop(k, None)
            spec = importlib.util.spec_from_file_location("bootstrap_under_test", HERE / "bootstrap.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod

    def test_settings_follow_the_claude_profile(self):
        profile = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, profile)
        (profile / "settings.json").write_text(json.dumps({"env": {"CLAUDE_CODE_PLUGIN_DIRS": str(HERE.parent)}}))
        b = self.load({"CLAUDE_CONFIG_DIR": str(profile)})
        self.assertEqual(b.SETTINGS, profile / "settings.json")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(b.settings_change())

    def test_claude_settings_wins_and_home_is_the_default(self):
        self.assertEqual(self.load({"CLAUDE_SETTINGS": "/x/s.json", "CLAUDE_CONFIG_DIR": "/y"}).SETTINGS,
                         pathlib.Path("/x/s.json"))
        self.assertEqual(self.load({}).SETTINGS, pathlib.Path("~/.claude/settings.json").expanduser())


class Bundle(unittest.TestCase):
    def test_has_no_internal_names(self):
        names = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG_DIR") or "~/.config/agent-skills").expanduser() / "internal-names.txt"
        if not names.is_file():
            self.skipTest(f"{names} not on this PC")
        patterns = [l.strip() for l in names.read_text().splitlines() if l.strip() and not l.startswith("#")]
        text = BUNDLE.read_text()
        hits = [p for p in patterns if re.search(p, text, re.I)]
        self.assertEqual(hits, [])

    def test_labels_are_unique_per_section(self):
        spec = importlib.util.spec_from_file_location("setup_labels", SETUP)
        setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup)
        for name, rules in json.loads(BUNDLE.read_text())["settings"]["autoMode"].items():
            labels = [setup.label(r) for r in rules if setup.label(r)]
            self.assertEqual(len(labels), len(set(labels)), name)

    def test_rules_name_their_limits(self):
        rules = json.loads(BUNDLE.read_text())["settings"]["autoMode"]["allow"]
        for rule in rules[:-1]:
            self.assertIn("Not covered", rule, rule[:60])
        self.assertIn("only the user's own chat messages count", rules[-1])

    def test_no_review_merge_stays_narrow(self):
        rule = next(r for r in json.loads(BUNDLE.read_text())["settings"]["autoMode"]["allow"]
                    if r.startswith("No-review merge"))
        for limit in ("at least one check that succeeded", "PR with no checks at all", "orchestrate program in any session",
                      "A repository name alone never brings a PR into scope", "require no approving review"):
            self.assertIn(limit, rule)
        # CLEAN also follows a given approval, so it cannot stand for "no approval required".
        self.assertNotIn("which means", rule)

    def test_allow_holds_no_unscoped_writes(self):
        allow = json.loads(BUNDLE.read_text())["settings"]["permissions"]["allow"]
        reads = {"ensure", "serve", "collect", "fleet", "skills", "prstats"}
        for rule in allow:
            self.assertNotRegex(rule, r"^Bash\(gh pr (create|edit)\b", rule)
            if rule.startswith("Bash(orch-dash"):
                self.assertIn(rule.removeprefix("Bash(orch-dash ").split(":")[0], reads, rule)


if __name__ == "__main__":
    unittest.main()
