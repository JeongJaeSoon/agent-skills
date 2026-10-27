#!/usr/bin/env python3
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import config_emit  # noqa: E402

SCRIPT = pathlib.Path(__file__).parent / "config_emit.py"
AM_LINE = ("Writing auto-memory Markdown files under a project's memory directory. "
           "Not covered: settings.json, skills, agents, hooks or CLAUDE.md.")


class Base(unittest.TestCase):
    """A fake HOME with ~/.claude and an Orca-style profile dir, so the user's real files are never read."""

    def setUp(self):
        self.home = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.home)
        self.claude = self.home / ".claude"
        self.profile = self.home / "profiles" / "p1" / "cfg"
        for d in (self.claude, self.profile):
            d.mkdir(parents=True)
            (d / "settings.json").write_text(json.dumps(
                {"env": {"X": "1"}, "permissions": {"allow": ["Bash(npm test:*)"]}}, indent=4))
        self.skills_json = self.claude / "agent-skills.json"
        self.skills_json.write_text(json.dumps({"prs": {"orgs": ["acme"]}, "reap": {"user_folders": []}}))
        self.env = {**os.environ, "HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.profile)}
        self.env.pop("AGENT_SKILLS_CONFIG", None)
        self.spec = self.home / "spec.json"

    def run_script(self, *args):
        return self.run_copy(SCRIPT, *args)

    def run_copy(self, script, *args, cwd=None):
        return subprocess.run([sys.executable, "-I", str(script), *map(str, args)], capture_output=True, text=True,
                              env=self.env, cwd=cwd)

    def write_spec(self, spec):
        self.spec.write_text(json.dumps(spec))

    def emit(self, spec, *extra):
        self.write_spec(spec)
        out = self.home / "out" / "change.py"
        r = self.run_script("emit", "--spec", self.spec, "--out", out, *extra)
        return r, out

    def apply_line(self, r):
        return r.stdout.strip().splitlines()[-1].split("--apply ")[1]

    def snapshot(self):
        return {p: p.read_bytes() for p in self.home.rglob("*.json*") if p != self.spec}


class AgentSkillsKeysTest(Base):
    SPEC = {"agent_skills": {"set": {"prs.assign_reviewers": True, "prs.after_hours": "schedule-certain-only"}}}

    def test_emit_writes_nothing_and_prints_diff_and_run_line(self):
        before = self.snapshot()
        r, out = self.emit(self.SPEC)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('+    "assign_reviewers": true', r.stdout)
        self.assertRegex(r.stdout, r"! echo .* \| shasum -a 256 -c - && python3 ")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(oct(out.stat().st_mode & 0o777), "0o600")

    def test_emitted_copy_applies_standalone_and_is_idempotent(self):
        r, out = self.emit(self.SPEC)
        moved = self.home / "elsewhere" / "change.py"
        moved.parent.mkdir()
        shutil.copy(out, moved)
        digest = self.apply_line(r)
        r = self.run_copy(moved, "--apply", digest, cwd=moved.parent)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(self.skills_json.read_text())
        self.assertEqual(data["prs"], {"orgs": ["acme"], "assign_reviewers": True,
                                       "after_hours": "schedule-certain-only"})
        self.assertEqual(data["reap"], {"user_folders": []})
        self.assertIn("rollback: cp", r.stdout)
        self.assertEqual(len(list(self.claude.glob("agent-skills.json.bak-skills-config-*"))), 1)
        r = self.run_copy(moved, "--apply", digest)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no change", r.stdout)
        self.assertEqual(len(list(self.claude.glob("agent-skills.json.bak-skills-config-*"))), 1)

    def test_emitted_copy_runs_from_a_shallow_path(self):
        r, out = self.emit(self.SPEC)
        shallow = pathlib.Path("/tmp") / f"sc-{self.home.name}.py"
        shutil.copy(out, shallow)
        self.addCleanup(shallow.unlink)
        r = self.run_copy(shallow, "--apply", self.apply_line(r))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(json.loads(self.skills_json.read_text())["prs"]["assign_reviewers"])

    def test_undocumented_key_refused_unless_listed(self):
        r, _ = self.emit({"agent_skills": {"set": {"prs.assign_reviewer": True}}})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not a documented key", r.stderr)
        r, _ = self.emit({"agent_skills": {"set": {"prs.assign_reviewer": True},
                                           "allow_unknown": ["prs.assign_reviewer"]}})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_file_extension_is_not_a_documented_key(self):
        for key in ("prs.py", "prs.json", "brief.md"):
            r, _ = self.emit({"agent_skills": {"set": {key: "x"}}})
            self.assertIn("not a documented key", r.stderr, key)

    def test_unknown_section_refused(self):
        r, _ = self.emit({"agent_skills": {"set": {"nosuch.key": 1}}})
        self.assertIn("no skill documents", r.stderr)

    def test_enum_and_type_checked(self):
        r, _ = self.emit({"agent_skills": {"set": {"prs.after_hours": "later"}}})
        self.assertIn("is not one of", r.stderr)
        r, _ = self.emit({"agent_skills": {"set": {"prs.assign_reviewers": "yes"}}})
        self.assertIn("expected bool", r.stderr)
        r, _ = self.emit({"agent_skills": {"set": {"prs.max_items": True}}})
        self.assertIn("expected int", r.stderr)

    def test_free_form_map_and_nested_defaults(self):
        r, _ = self.emit({"agent_skills": {"set": {"prs.reviewers.rev-a.slack": "U0FAKE1",
                                                   "reap.pressure.swap_gb": [4, 8]}}})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_secret_value_and_key_refused(self):
        r, _ = self.emit({"agent_skills": {"set": {"prs.channel": "xox" + "b-123-456"}}})
        self.assertIn("looks like a secret", r.stderr)
        r, _ = self.emit({"agent_skills": {"set": {"tracker.api_token": "x"}, "allow_unknown": ["tracker.api_token"]}})
        self.assertIn("secrets do not go", r.stderr)

    def test_unset_missing_refused_and_set_through_list_refused(self):
        r, _ = self.emit({"agent_skills": {"unset": ["prs.cron"]}})
        self.assertIn("already removed?", r.stderr)
        self.skills_json.write_text(json.dumps({"prs": {"profiles": []}}))
        r, _ = self.emit({"agent_skills": {"set": {"prs.profiles.after_hours": "send-now"}},
                          })
        self.assertNotEqual(r.returncode, 0)


class PermissionsTest(Base):
    def rule(self, rel):
        return f"Edit(/{self.home}/{rel})"

    def test_both_settings_files_get_rules_and_automode(self):
        spec = {"settings": ["profile", "home"],
                "permissions": {"add": [self.rule("profiles/*/cfg/projects/*/memory/**"),
                                        self.rule(".claude/projects/*/memory/**")]},
                "autoMode": {"add": {"allow": [AM_LINE]}}}
        r, out = self.emit(spec)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("== settings:"), 2)
        r = self.run_copy(out, "--apply", self.apply_line(r))
        self.assertEqual(r.returncode, 0, r.stderr)
        for d in (self.claude, self.profile):
            data = json.loads((d / "settings.json").read_text())
            self.assertEqual(data["permissions"]["allow"][1:], spec["permissions"]["add"])
            self.assertEqual(data["autoMode"]["allow"], ["$defaults", AM_LINE])
            self.assertEqual(data["env"], {"X": "1"})

    def test_same_real_file_written_once(self):
        shutil.rmtree(self.profile)
        self.profile.symlink_to(self.claude)
        r, _ = self.emit({"settings": ["profile", "home"], "permissions": {"add": ["Bash(npm run lint:*)"]}})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("== settings:"), 1)

    def test_symlink_twin_added_to_spec(self):
        real = self.home / "real-claude"
        real.mkdir()
        (self.home / "linked").symlink_to(real)
        r, out = self.emit({"permissions": {"add": [self.rule("linked/projects/*/memory/**")]}})
        self.assertEqual(r.returncode, 0, r.stderr)
        twin = f"Edit(/{real}/projects/*/memory/**)"
        self.assertIn(f"symlink twin added: {twin}", r.stdout)
        r = self.run_copy(out, "--apply", self.apply_line(r))
        allow = json.loads((self.profile / "settings.json").read_text())["permissions"]["allow"]
        self.assertIn(twin, allow)

    def test_remove_missing_refused_before_any_write(self):
        before = self.snapshot()
        r, out = self.emit({"settings": ["profile", "home"], "permissions": {"remove": ["Bash(npm run x:*)"]}})
        self.assertIn("already removed?", r.stderr)
        self.assertFalse(out.exists())
        self.assertEqual(self.snapshot(), before)

    def test_broad_rules_refused(self):
        cases = {"Bash(*)": "matches everything", "Bash": "bare tool name", "WebFetch(domain:*)": "matches everything",
                 "Bash(python3:*)": "runs any command", "mcp__server": "bare tool name",
                 f"Edit(/{self.home}/**)": "whole home", "Edit(//**)": "matches everything",
                 "Edit(~/.claude/**)": "user's to edit", f"Write(/{self.profile}/settings.json)": "user's to edit",
                 "Edit(src/**)": "absolute path", "Bash(curl -H 'Authorization: " + "ghp_" + "a" * 36 + "')": "secret"}
        for rule, why in cases.items():
            r, _ = self.emit({"permissions": {"add": [rule]}})
            self.assertNotEqual(r.returncode, 0, rule)
            self.assertIn(why, r.stderr, rule)
        r, _ = self.emit({"permissions": {"add": ["mcp__server__tool", "Bash(python3 scripts/check.py:*)"]}})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_remove_takes_a_broad_rule(self):
        (self.profile / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash(*)", "Bash(x:*)"]}}))
        r, out = self.emit({"permissions": {"remove": ["Bash(*)"]}})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("null", r.stdout)
        self.assertNotIn('"autoMode"', r.stdout)


class RuleProbeTest(unittest.TestCase):
    """Rules an independent review found passing, and the narrow rules that must keep passing."""

    def setUp(self):
        self.home = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.home)
        h, profile = self.home, self.home / "prof"
        for patch in (mock.patch.object(pathlib.Path, "home", return_value=h),
                      mock.patch.object(config_emit, "HOME_DIR", h / ".claude"),
                      mock.patch.object(config_emit, "CONFIG_DIR", profile),
                      mock.patch.object(config_emit, "SKILLS_JSON", h / ".claude" / "agent-skills.json")):
            patch.start()
            self.addCleanup(patch.stop)

    REFUSED = [
        "Bash(python3 -c:*)", "Bash(bash -c:*)", "Bash(sh -c *)", "Bash(node -e:*)", "Bash(env X=1:*)",
        "Bash(sudo -u root:*)", "Bash(/bin/bash:*)", "Bash(/usr/bin/env:*)", "Bash(python3.12:*)", "Bash(py*)",
        "Bash(p*:*)", "Bash(a*)", "Bash(b*)", "Bash(uv run:*)", "Bash(fish:*)", "Bash(nohup:*)", "Bash(timeout:*)",
        "Bash(command:*)", "Bash(find:*)", "Bash(awk:*)", "Bash(FOO=1 bash:*)", "Bash(npx:*)",
        "Bash(PYTHON3:*)", "Bash(Bash -c:*)", "Bash(uv tool run:*)",
        "Edit(~/.claude/hooks/*.sh)", "Edit(~/.claude/hooks/sub/**)", "Edit(/{h}/prof/hooks/x.sh)",
        "Edit(~/.claude/skills/*/scripts/**)", "Edit(~/.claude/skills/*)", "Edit(~/.claude/plugins/marketplaces/**)",
        "Edit(~/.claude/agents/x/**)", "Edit(~/.claude/commands/**)", "Edit(~/.claude/output-styles/**)",
        "Edit(~/.claude/*.json)", "Edit(~/.claude/settings.local.json)", "Edit(~/.CLAUDE/Settings.json)",
        "Edit(/{h}/../**)", "Edit(~/../**)", "Edit(/{h}/./**)", "Edit(/{h}/x/../.claude/settings.json)",
        "Edit(/{h}*/**)", "Edit(~/.claude/{{hooks,x}}/a)",
        "Edit(~/.zshrc)", "Edit(~/.ssh/**)", "Edit(~/.config/**)", "Edit(~/.gitconfig)", "Edit(~/*rc)",
        "Edit(~/Library/LaunchAgents/x.plist)", "Write(~/.claude.json)",
    ]
    ALLOWED = [
        "Edit(/{h}/prof/projects/*/memory/**)", "Edit(~/.claude/projects/*/memory/**)", "Edit(~/.claude/projects/**)",
        "Edit(~/.claude/keybindings.json)", "Edit(/{h}/src/app/**)", "Bash(gh pr view:*)", "Bash(npm test:*)",
        "Bash(npm run lint:*)", "Bash(python3 scripts/check.py:*)", "Bash(python3 -m pytest:*)",
        "Bash(uv run pytest:*)", "mcp__srv__tool", "WebFetch(domain:example.test)", "WebFetch(domain:*.com)",
    ]

    def test_refused(self):
        for rule in self.REFUSED:
            with self.subTest(rule=rule), self.assertRaises(config_emit.Refused):
                config_emit.check_permission(rule.format(h=self.home))

    def test_allowed(self):
        for rule in self.ALLOWED:
            with self.subTest(rule=rule):
                config_emit.check_permission(rule.format(h=self.home))


class HashAndOutTest(Base):
    SPEC = {"permissions": {"add": ["Bash(npm run lint:*)"]}}

    def test_wrong_hash_and_edited_copy_refused(self):
        r, out = self.emit(self.SPEC)
        digest = self.apply_line(r)
        self.assertNotEqual(self.run_copy(out, "--apply", "0" * 12).returncode, 0)
        out.write_text(out.read_text().replace("npm run lint", "npm run any"))
        r = self.run_copy(out, "--apply", digest)
        self.assertIn("does not match", r.stderr)
        self.assertNotIn("npm run any", (self.profile / "settings.json").read_text())

    def test_code_edited_after_emit_refused(self):
        r, out = self.emit(self.SPEC)
        line = r.stdout.strip().splitlines()[-1].strip().removeprefix("! ")
        digest = self.apply_line(r)
        tampered = out.read_text().replace("    for path, after in changes:\n",
                                           "    changes[0][1]['permissions']['allow'].append('Bash(*)')\n"
                                           "    for path, after in changes:\n", 1)
        self.assertNotEqual(tampered, out.read_text())
        out.write_text(tampered)
        before = self.snapshot()
        self.assertIn("does not match", self.run_copy(out, "--apply", digest).stderr)
        r = subprocess.run(["bash", "-c", line], capture_output=True, text=True, env=self.env)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_run_line_applies_untouched_copy(self):
        r, out = self.emit(self.SPEC)
        line = r.stdout.strip().splitlines()[-1].strip().removeprefix("! ")
        r = subprocess.run(["bash", "-c", line], capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Bash(npm run lint:*)", (self.profile / "settings.json").read_text())

    def test_out_inside_config_dirs_refused(self):
        self.write_spec(self.SPEC)
        for target in (self.claude / "x.py", self.profile / "x.py"):
            r = self.run_script("emit", "--spec", self.spec, "--out", target)
            self.assertIn("--out must be outside", r.stderr)
            self.assertFalse(target.exists())

    def test_dry_run_of_emitted_copy_writes_nothing(self):
        r, out = self.emit(self.SPEC)
        before = self.snapshot()
        r = self.run_copy(out)
        self.assertIn("dry run", r.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_settings_json_refused(self):
        (self.profile / "settings.json").write_text("{")
        r, _ = self.emit(self.SPEC)
        self.assertIn("not valid JSON", r.stderr)

    def test_automode_spec_checked_by_tune_automode(self):
        r, _ = self.emit({"autoMode": {"add": {"allow": ["$defaults"]}}})
        self.assertIn("autoMode: $defaults is kept", r.stderr)


class KeyDocsTest(unittest.TestCase):
    def test_every_section_source_exists(self):
        for docs, defaults in config_emit.SECTIONS.values():
            for path in [*docs, *defaults.values()]:
                self.assertTrue((config_emit.REPO / path).is_file(), path)
            for path in defaults.values():
                self.assertIsInstance(config_emit.defaults(config_emit.REPO / path), dict, path)


if __name__ == "__main__":
    unittest.main()
