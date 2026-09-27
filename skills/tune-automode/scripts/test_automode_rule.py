#!/usr/bin/env python3
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import automode_rule  # noqa: E402

HERE = pathlib.Path(__file__).parent
SCRIPT = HERE / "automode_rule.py"
FIXTURE = HERE.parent / "references" / "example-settings.json"

RULE = ("Restarting the local dev server the agent started in this session (make dev-restart). "
        "Not covered: shared or remote servers.")
SPEC = {"add": {"allow": [RULE]}}


def run(*args, env=None):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, env=env)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.settings = self.dir / "settings.json"
        self.settings.write_text(FIXTURE.read_text())
        self.spec = self.dir / "spec.json"
        self.spec.write_text(json.dumps(SPEC))

    def backups(self):
        return sorted(p for p in self.dir.iterdir() if ".bak-automode-" in p.name)

    def automode(self):
        return json.loads(self.settings.read_text())["autoMode"]

    def apply(self, spec=None):
        if spec is not None:
            self.spec.write_text(json.dumps(spec))
        spec = json.loads(self.spec.read_text())
        digest = automode_rule.spec_hash(spec) if isinstance(spec, dict) else "x"
        return run(SCRIPT, "--spec", self.spec, "--settings", self.settings, "--apply", digest)


class ApplyTest(Base):
    def test_dry_run_writes_nothing(self):
        before = self.settings.read_bytes()
        r = run(SCRIPT, "--spec", self.spec, "--settings", self.settings)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("+", r.stdout)
        self.assertIn("dev-restart", r.stdout)
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertEqual(self.backups(), [])

    def test_apply_backs_up_original_bytes_and_merges(self):
        before = self.settings.read_bytes()
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        [backup] = self.backups()
        self.assertEqual(backup.read_bytes(), before)
        self.assertIn(str(backup), r.stdout)
        self.assertEqual(self.automode()["allow"][-1], RULE)

    def test_second_apply_is_a_no_op(self):
        self.apply()
        after_first = self.settings.read_bytes()
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("already", r.stdout)
        self.assertEqual(self.settings.read_bytes(), after_first)
        self.assertEqual(len(self.backups()), 1)

    def test_invalid_json_refused_untouched(self):
        self.settings.write_text('{"autoMode": {"allow": ["$defaults",]}')
        before = self.settings.read_bytes()
        r = self.apply()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not valid JSON", r.stderr)
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertEqual(self.backups(), [])

    def test_missing_settings_refused(self):
        self.settings.unlink()
        r = self.apply()
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.settings.exists())

    def test_other_keys_unchanged(self):
        original = json.loads(self.settings.read_text())
        self.apply()
        merged = json.loads(self.settings.read_text())
        for key in original:
            if key != "autoMode":
                self.assertEqual(merged[key], original[key])

    def test_new_section_starts_with_defaults(self):
        data = json.loads(self.settings.read_text())
        del data["autoMode"]["soft_deny"]
        self.settings.write_text(json.dumps(data))
        r = self.apply({"add": {"soft_deny": ["Deleting any cloud storage bucket."]}})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.automode()["soft_deny"], ["$defaults", "Deleting any cloud storage bucket."])

    def test_missing_automode_created_with_defaults(self):
        data = json.loads(self.settings.read_text())
        del data["autoMode"]
        self.settings.write_text(json.dumps(data))
        self.assertEqual(self.apply().returncode, 0)
        self.assertEqual(self.automode(), {"allow": ["$defaults", RULE]})

    def test_symlinked_settings_stay_a_symlink(self):
        real = self.dir / "dotfiles" / "settings.json"
        real.parent.mkdir()
        self.settings.rename(real)
        self.settings.symlink_to(real)
        self.assertEqual(self.apply().returncode, 0)
        self.assertTrue(self.settings.is_symlink())
        self.assertEqual(json.loads(real.read_text())["autoMode"]["allow"][-1], RULE)

    def test_wrong_hash_refused(self):
        before = self.settings.read_bytes()
        r = run(SCRIPT, "--spec", self.spec, "--settings", self.settings, "--apply", "000000000000")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("does not match", r.stderr)
        self.assertEqual(self.settings.read_bytes(), before)

    def test_config_dir_env_picks_default_settings(self):
        env = dict(os.environ, CLAUDE_CONFIG_DIR=str(self.dir))
        r = run(SCRIPT, "--spec", self.spec, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(str(self.dir), r.stdout)

    def test_unencodable_rule_refused(self):
        self.spec.write_text('{"add": {"allow": ["bad \\ud800 rule"]}}')
        r = self.apply()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refused", r.stderr)

    def test_diff_shows_only_automode(self):
        r = run(SCRIPT, "--spec", self.spec, "--settings", self.settings)
        self.assertNotIn("EXAMPLE_TOKEN", r.stdout)
        self.assertNotIn("permissions", r.stdout)


class ReplaceTest(Base):
    OLD = "Running the test suite in local checkouts."
    NEW = "Running the test suite and linters in local checkouts."

    def test_replace_swaps_in_place_and_is_idempotent(self):
        spec = {"replace": [{"section": "allow", "old": self.OLD, "new": self.NEW}]}
        index = self.automode()["allow"].index(self.OLD)
        self.assertEqual(self.apply(spec).returncode, 0)
        self.assertEqual(self.automode()["allow"][index], self.NEW)
        after = self.settings.read_bytes()
        r = self.apply(spec)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.settings.read_bytes(), after)

    def test_replace_when_both_present_leaves_one(self):
        data = json.loads(self.settings.read_text())
        data["autoMode"]["allow"].append(self.NEW)
        self.settings.write_text(json.dumps(data))
        self.assertEqual(self.apply({"replace": [{"section": "allow", "old": self.OLD, "new": self.NEW}]}).returncode, 0)
        allow = self.automode()["allow"]
        self.assertEqual(allow.count(self.NEW), 1)
        self.assertNotIn(self.OLD, allow)

    def test_replace_without_match_refused(self):
        before = self.settings.read_bytes()
        r = self.apply({"replace": [{"section": "allow", "old": "no such rule", "new": self.NEW}]})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not found", r.stderr)
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertEqual(self.backups(), [])


class SpecValidationTest(Base):
    def refused(self, spec):
        before = self.settings.read_bytes()
        r = self.apply(spec)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.settings.read_bytes(), before)
        return r

    def test_defaults_cannot_be_replaced(self):
        self.refused({"replace": [{"section": "allow", "old": "$defaults", "new": "x"}]})

    def test_defaults_cannot_be_added(self):
        self.refused({"add": {"allow": ["$defaults"]}})

    def test_unknown_section_refused(self):
        r = self.refused({"add": {"alow": ["x"]}})
        self.assertIn("alow", r.stderr)

    def test_hard_deny_not_editable_here(self):
        self.refused({"add": {"hard_deny": ["x"]}})

    def test_replace_with_itself_refused(self):
        rule = "Running the test suite in local checkouts."
        self.refused({"replace": [{"section": "allow", "old": rule, "new": rule}]})

    def test_empty_spec_refused(self):
        self.refused({})

    def test_non_string_rule_refused(self):
        self.refused({"add": {"allow": [42]}})


class EmitTest(Base):
    def emit(self, out):
        return run(SCRIPT, "emit", "--spec", self.spec, "--out", out, "--settings", self.settings)

    def test_emitted_script_runs_standalone(self):
        out = self.dir / "elsewhere" / "rule.py"
        r = self.emit(out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("dev-restart", r.stdout)
        digest = automode_rule.spec_hash(SPEC)
        self.assertIn(f"! python3 {out} --settings {self.settings} --apply {digest}", r.stdout)
        self.assertFalse(self.backups())

        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "CLAUDE_SKILL_DIR")}
        dry = run(out, "--settings", self.settings, env=env)
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("dry run", dry.stdout)
        applied = run(out, "--settings", self.settings, "--apply", digest, env=env)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(self.automode()["allow"][-1], RULE)
        self.assertEqual(len(self.backups()), 1)

    def test_emitted_script_carries_rule_text_readably(self):
        out = self.dir / "rule.py"
        run(SCRIPT, "emit", "--spec", self.spec, "--out", out, "--settings", self.settings)
        self.assertIn("make dev-restart", out.read_text())

    def test_edited_emitted_script_refused(self):
        out = self.dir / "rule.py"
        self.emit(out)
        out.write_text(out.read_text().replace("shared or remote servers", "anything"))
        before = self.settings.read_bytes()
        r = run(out, "--settings", self.settings, "--apply", automode_rule.spec_hash(SPEC))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.settings.read_bytes(), before)

    def test_emit_refuses_out_onto_settings(self):
        before = self.settings.read_bytes()
        r = self.emit(self.settings)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.settings.read_bytes(), before)

    def test_emit_refuses_out_inside_config_dir(self):
        env = dict(os.environ, CLAUDE_CONFIG_DIR=str(self.dir))
        out = self.dir / "rule.py"
        r = run(SCRIPT, "emit", "--spec", self.spec, "--out", out, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(out.exists())

    def test_emit_writes_nothing_when_merge_refused(self):
        out = self.dir / "rule.py"
        self.spec.write_text(json.dumps({"replace": [{"section": "allow", "old": "no such rule", "new": "x"}]}))
        r = self.emit(out)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(out.exists())

    def test_emit_refuses_bad_spec_and_writes_nothing(self):
        out = self.dir / "rule.py"
        self.spec.write_text(json.dumps({"add": {"alow": ["x"]}}))
        r = run(SCRIPT, "emit", "--spec", self.spec, "--out", out, "--settings", self.settings)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
