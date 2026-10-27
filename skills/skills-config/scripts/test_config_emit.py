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
                "permissions": {"add": [self.rule("profiles/p1/cfg/projects/*/memory/**"),
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
        r, _ = self.emit({"settings": ["profile", "home"], "permissions": {"add": ["mcp__srv__tool"]}})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("== settings:"), 1)

    def test_symlinked_memory_dir_refused(self):
        (self.claude / "hooks").mkdir()
        (self.claude / "projects" / "evil").mkdir(parents=True)
        (self.claude / "projects" / "evil" / "memory").symlink_to(self.claude / "hooks")
        (self.claude / "projects" / "evil2").symlink_to(self.claude / "hooks")
        for rule in ("Edit(~/.claude/projects/evil/memory/**)", "Edit(~/.claude/projects/evil/memory/x.sh)",
                     "Edit(~/.claude/projects/*/memory/**)", self.rule(".claude/projects/evil/memory/**"),
                     self.rule(".claude/projects/evil2/memory/**")):
            r, out = self.emit({"settings": ["home"], "permissions": {"add": [rule]}})
            self.assertNotEqual(r.returncode, 0, rule)
            self.assertFalse(out.exists(), rule)
        r, _ = self.emit({"settings": ["home"], "permissions": {"add": ["Edit(~/.claude/projects/real/memory/**)"]}})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_hidden_symlink_and_loop_refused_on_every_python(self):
        """glob skips hidden names before 3.11; macOS's /usr/bin/python3 is 3.9, and the run line may pick it."""
        (self.claude / "hooks").mkdir()
        memory = self.claude / "projects" / "app" / "memory"
        memory.mkdir(parents=True)
        (self.claude / "projects" / ".h").symlink_to(self.claude / "hooks")
        (memory / ".h").symlink_to(self.claude / "hooks")
        (memory / "loop").symlink_to(memory)
        pythons = [sys.executable] + [p for p in ("/usr/bin/python3",) if os.path.exists(p)]
        for python in pythons:
            for rule in ("Edit(~/.claude/projects/*/memory/**)", "Edit(~/.claude/projects/app/memory/**)"):
                self.write_spec({"settings": ["home"], "permissions": {"add": [rule]}})
                out = self.home / "out" / "change.py"
                r = subprocess.run([python, "-I", str(SCRIPT), "emit", "--spec", str(self.spec), "--out", str(out)],
                                   capture_output=True, text=True, env=self.env)
                self.assertIn("resolves to", r.stderr, (python, rule))
                self.assertFalse(out.exists(), (python, rule))
            (memory / ".h").unlink()
            (memory / "loop").unlink()
            r = subprocess.run([python, "-I", str(SCRIPT), "emit", "--spec", str(self.spec), "--out", str(out)],
                               capture_output=True, text=True, env=self.env)
            self.assertEqual(r.returncode, 0, (python, r.stderr))
            out.unlink()
            (memory / ".h").symlink_to(self.claude / "hooks")
            (memory / "loop").symlink_to(memory)

    def test_shared_link_targets_walked_once(self):
        """Each level holds s/ and links a, b to it: walked path by path that is 3^16 entries, hours."""
        level = self.claude / "projects" / "app" / "memory"
        level.mkdir(parents=True)
        for _ in range(16):
            (level / "s").mkdir()
            for k in "ab":
                (level / k).symlink_to(level / "s")
            level = level / "s"
        self.write_spec({"settings": ["home"], "permissions": {"add": ["Edit(~/.claude/projects/*/memory/**)"]}})
        out = self.home / "out" / "change.py"
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), "emit", "--spec", str(self.spec), "--out", str(out)],
                           capture_output=True, text=True, env=self.env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_unreadable_memory_subdir_refused_with_its_path(self):
        locked = self.claude / "projects" / "app" / "memory" / "locked"
        locked.mkdir(parents=True)
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o700)
        r, out = self.emit({"settings": ["home"], "permissions": {"add": ["Edit(~/.claude/projects/*/memory/**)"]}})
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn(f"refused: cannot list {locked} (Permission denied)", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertFalse(out.exists())

    def test_symlinked_profile_home_is_not_a_config_dir(self):
        (self.claude / "hooks" / "projects" / "x" / "memory").mkdir(parents=True)
        (self.claude / "projects").mkdir()
        (self.claude / "projects" / "x").symlink_to(self.claude / "hooks" / "projects" / "x")
        profiles = self.home / "Library" / "Application Support" / "orca" / "claude-profiles"
        profiles.mkdir(parents=True)
        (profiles / "fake2").mkdir()
        (profiles / "fake2" / "home").symlink_to(self.claude / "hooks")
        r, out = self.emit({"settings": ["home"], "permissions": {"add": ["Edit(~/.claude/projects/x/memory/**)"]}})
        self.assertIn("resolves to", r.stderr)
        self.assertFalse(out.exists())
        # A linked profile directory is the same hole one level up: any directory named home becomes a root.
        (profiles / "fake2" / "home").unlink()
        (self.claude / "v" / "home" / "projects" / "x" / "memory").mkdir(parents=True)
        (self.claude / "projects" / "x").unlink()
        (self.claude / "projects" / "x").symlink_to(self.claude / "v" / "home" / "projects" / "x")
        (profiles / "fake3").symlink_to(self.claude / "v")
        r, _ = self.emit({"settings": ["home"], "permissions": {"add": ["Edit(~/.claude/projects/x/memory/**)"]}})
        self.assertIn("resolves to", r.stderr)

    def test_symlink_planted_after_emit_refused_at_apply(self):
        r, out = self.emit({"settings": ["home"], "permissions": {"add": ["Edit(~/.claude/projects/*/memory/**)"]}})
        self.assertEqual(r.returncode, 0, r.stderr)
        (self.claude / "hooks").mkdir()
        (self.claude / "projects" / "evil").mkdir(parents=True)
        (self.claude / "projects" / "evil" / "memory").symlink_to(self.claude / "hooks")
        before = self.snapshot()
        r = self.run_copy(out, "--apply", self.apply_line(r))
        self.assertIn("resolves to", r.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_profile_projects_linked_to_another_config_dir_passes(self):
        """Orca links every profile's projects/ to ~/.claude/projects; a real path on the list is fine."""
        (self.claude / "projects" / "app" / "memory").mkdir(parents=True)
        (self.claude / "projects" / "app" / "memory" / "MEMORY.md").write_text("x")
        (self.claude / "projects" / ".claude").mkdir()
        orca = self.home / "Library" / "Application Support" / "orca" / "claude-profiles" / "p9" / "home"
        orca.mkdir(parents=True)
        for cfg in (self.profile, orca):
            (cfg / "projects").symlink_to(self.claude / "projects")
        rules = [self.rule("profiles/p1/cfg/projects/*/memory/**"),
                 self.rule("Library/Application Support/orca/claude-profiles/*/" + "home/projects/*/memory/**")]
        r, out = self.emit({"permissions": {"add": rules}})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.run_copy(out, "--apply", self.apply_line(r)).returncode, 0)
        (self.claude / "hooks").mkdir()
        (self.claude / "projects" / "app" / "memory" / "x.sh").symlink_to(self.claude / "hooks" / "x.sh")
        r, _ = self.emit({"permissions": {"add": rules[1:]}})
        self.assertIn("resolves to", r.stderr)

    def test_remove_missing_refused_before_any_write(self):
        before = self.snapshot()
        r, out = self.emit({"settings": ["profile", "home"], "permissions": {"remove": ["Bash(npm run x:*)"]}})
        self.assertIn("already removed?", r.stderr)
        self.assertFalse(out.exists())
        self.assertEqual(self.snapshot(), before)

    def test_rules_outside_the_allow_list_refused(self):
        for rule in ("Bash(*)", "Bash", "WebFetch(domain:*)", "Bash(python3:*)", "mcp__server", "Edit",
                     f"Edit(/{self.home}/**)", "Edit(//**)", "Edit(~/.claude/**)",
                     f"Write(/{self.profile}/settings.json)",
                     "Edit(src/**)", "Edit(//tmp/é/x)", f"Read(/{self.home}/src/app/**)",
                     "Read(//tmp/x-" + "ghp_" + "a" * 36 + ")"):
            r, _ = self.emit({"permissions": {"add": [rule]}})
            self.assertNotEqual(r.returncode, 0, rule)
            self.assertIn("outside this skill", r.stderr, rule)
        r, _ = self.emit({"permissions": {"add": ["mcp__server__tool", "Read(~/.claude/projects/*/memory/**)"]}})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_remove_takes_a_broad_rule(self):
        (self.profile / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash(*)", "Bash(x:*)"]}}))
        r, out = self.emit({"permissions": {"remove": ["Bash(*)"]}})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("null", r.stdout)
        self.assertNotIn('"autoMode"', r.stdout)


class RuleProbeTest(unittest.TestCase):
    """Every rule earlier reviews found passing a deny list, now outside the allow list, and the rules it takes."""

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

    BASH = [
        "Bash(*)", "Bash", "Bash(python3 -c:*)", "Bash(bash -c:*)", "Bash(sh -c *)", "Bash(node -e:*)",
        "Bash(env X=1:*)", "Bash(sudo -u root:*)", "Bash(/bin/bash:*)", "Bash(/usr/bin/env:*)", "Bash(python3.12:*)",
        "Bash(py*)", "Bash(p*:*)", "Bash(a*)", "Bash(b*)", "Bash(uv run:*)", "Bash(fish:*)", "Bash(nohup:*)",
        "Bash(timeout:*)", "Bash(command:*)", "Bash(find:*)", "Bash(awk:*)", "Bash(FOO=1 bash:*)", "Bash(npx:*)",
        "Bash(PYTHON3:*)", "Bash(Bash -c:*)", "Bash(uv tool run:*)",
        "Bash(python3 -c import*)", "Bash(bash -c e*)", "Bash(perl -e x*)", "Bash(uv run python:*)",
        "Bash(uv run bash:*)", "Bash(poetry run python:*)", "Bash(bundle exec ruby:*)", "Bash(npm exec bash:*)",
        "Bash(npx node:*)", "Bash(uv run --with x python:*)", "Bash(uv run FOO=1 python:*)", "Bash(npm:*)",
        "Bash(uv:*)", "Bash(uv tool:*)", "Bash(poetry:*)", "Bash(git:*)", "Bash(git -c:*)", "Bash(make:*)",
        "Bash(npm run:*)", "Bash(launchctl:*)", "Bash(crontab:*)", "Bash(open -a:*)", "Bash(p\\ython3:*)",
        "Bash($SHELL:*)", "Bash(python3 /dev/stdin:*)", "Bash(bash /dev/fd/0:*)", "Bash(python3 /proc/self/fd/0:*)",
        "Bash(python3 -:*)", "Bash(python3 -m:*)", "Bash(\"python3\" -c:*)", "Bash(Python3.12 -c:*)",
        "Bash(/opt/homebrew/bin/python3:*)", "Bash(FOO=1 python3:*)", "Bash(osascript -e:*)", "Bash(git * main)",
        "Bash(PATH=/tmp/x gh pr view:*)", "Bash(GIT_PAGER=sh git log:*)", "Bash(NODE_OPTIONS=-r/tmp/x npm test:*)",
        "Bash(DYLD_INSERT_LIBRARIES=/tmp/x.dylib ls:*)", "Bash(python3 -m timeit:*)", "Bash(python3 -m pdb:*)",
        "Bash(python3 -m cProfile:*)", "Bash(python3 -m trace:*)", "Bash(python3 -m code:*)", "Bash(xcrun python3:*)",
        "Bash(arch -arm64 bash:*)", "Bash(script -q /dev/null:*)", "Bash(sandbox-exec -p:*)",
        "Bash(sqlite3 :memory:*)", "Bash(tclsh:*)", "Bash(expect -c:*)", "Bash(irb:*)", "Bash(swift:*)",
        "Bash(jshell:*)", "Bash(vim -c:*)", "Bash(python3-intel64 -c:*)", "Bash(pythonw -c:*)", "Bash(python3t -c:*)",
        "Bash(gh pr view:*)", "Bash(npm test:*)", "Bash(python3 scripts/check.py:*)", "Bash(git status:*)",
    ]
    ORCA = "/{h}/Library/Application Support/orca"
    PROFILE = ORCA + "/claude-profiles/p1/home"
    REFUSED = [
        "Edit(~/.claude/hooks/*.sh)", "Edit(~/.claude/hooks/sub/**)", "Edit(/{h}/prof/hooks/x.sh)",
        "Edit(~/.claude/skills/*/scripts/**)", "Edit(~/.claude/skills/*)", "Edit(~/.claude/plugins/marketplaces/**)",
        "Edit(~/.claude/agents/x/**)", "Edit(~/.claude/commands/**)", "Edit(~/.claude/output-styles/**)",
        "Edit(~/.claude/*.json)", "Edit(~/.claude/settings.local.json)", "Edit(~/.CLAUDE/Settings.json)",
        "Edit(/{h}/../**)", "Edit(~/../**)", "Edit(/{h}/./**)", "Edit(/{h}/x/../.claude/settings.json)",
        "Edit(/{h}*/**)", "Edit(~/.claude/{{hooks,x}}/a)",
        "Edit(~/.zshrc)", "Edit(~/.ssh/**)", "Edit(~/.config/**)", "Edit(~/.gitconfig)", "Edit(~/*rc)",
        "Edit(~/Library/LaunchAgents/x.plist)", "Write(~/.claude.json)",
        "Edit(~/.local/bin/**)", "Edit(~/bin/**)", "Edit(~/.oh-my-zsh/**)",
        "Edit(~/.claude/projects/**)", "Edit(~/.claude/keybindings.json)",
        "Edit(~/.bash_login)", "Edit(~/.zsh/x.zsh)", "Edit(~/.zshrc.d/**)", "Edit(~/.tmux.conf)", "Edit(~/.vimrc)",
        "Edit(~/.cargo/bin/x)", "Edit(~/go/bin/**)", "Edit(~/.pyenv/shims/python3)", "Edit(~/.rbenv/shims/**)",
        "Edit(~/.volta/bin/**)", "Edit(~/.bun/bin/**)", "Edit(~/.deno/bin/**)", "Edit(~/.nvm/nvm.sh)",
        "Edit(//opt/homebrew/bin/**)", "Edit(//usr/local/bin/x)",
        "Edit(~/.cargo/config.toml)", "Edit(~/.pip/pip.conf)", "Edit(~/.gemrc)", "Edit(~/.condarc)",
        "Edit(~/.git-templates/hooks/**)", "Read(~/.git-credentials)", "Read(~/.py" + "pirc)",
        "Edit(" + ORCA + "/**)", "Edit(" + ORCA + "/claude-profiles/*/{p}/settings.json)",
        "Edit(" + ORCA + "/claude-profiles/*/{p}/hooks/x.sh)", "Edit(" + ORCA + "/**/memory/**)",
        "Edit(" + ORCA + "/*/memory/**)", "Read(" + ORCA + "/orca-runtime.json)",
        "Edit(~/Library/Application Support/Code/User/settings.json)", "Edit(~/Library/LaunchDaemons/x.plist)",
        "Edit(//Library/LaunchAgents/x.plist)", "Edit(//Library/LaunchDaemons/**)", "Read(~/Library/Keychains/**)",
        "Edit(/{h}/src/app/.git/hooks/pre-commit)", "Edit(/{h}/src/app/.git/config)", "Edit(/{h}/src/app/.claude/x)",
        "Edit(/{h}/src/app/.mcp.json)", "Edit(/{h}/src/app/**)", "Write(/{h}/src/app/*/hooks/x)",
        "Read(/{h}/src/app/.git/**)", "Read(/{h}/src/app/.claude/settings.local.json)", "Read(/{h}/src/app/.mcp.json)",
        "Read(/{h}/src/app/*.json)", "Read(/{h}/src/app/.cl?ude/x)", "Read(/{h}/src/*/hooks/x)",
        "Edit(/{h}/src/аpp/x.md)", "Edit(~/．claude/settings.json)", "mcp__ѕrv__tool",
        "mcp__server", "mcp__server__*", "mcp__srv__tool(x)", "WebFetch(domain:example.test)", "Glob(/{h}/src/**)",
        "Read(/{h}/src/app/**)", "Read(/{h}/src/app/.git/HEAD)", "Edit(/{h}/src/app/docs/notes.md)",
        "Edit(/{h}/src/app/src/*.py)",
        "Edit(~/.claude/projects/*/memory/../../hooks/x)", "Edit(~/.claude/projects/**/memory/**)",
        "Edit(~/.claude/**/memory/**)", "Edit(~/.claude/projects/*/*/**)", "Edit(~/.claude/projects/*/memory*/**)",
        "Edit(~/.claude/projects/*/MEMORY/**)", "Edit(~/.claude/Projects/*/memory/**)",
        "Edit(~/.claude/projects/*/memory/../x)",
        "Edit(~/.claude/projects/*/memory/.claude/settings.json)", "Edit(~/.claude/projects/*/memory/.git/hooks/x)",
        "Edit(~/.claude/projects/*/memory/x/../../../../hooks/a)", "Edit(" + ORCA + "/*/projects/x/memory/**)",
        "Edit(" + ORCA + "/claude-profiles/*/*/projects/*/memory/**)",
        "Edit(" + ORCA + "/claude-profiles/*/{p}/projects/*/memory/**/settings.json)",
        "Edit(" + ORCA + "/**/projects/*/memory/**)", "Edit(~/projects/x/memory/**)",
        "Edit(//tmp/projects/x/memory/**)",
        "Edit(/{h}/src/app/projects/x/memory/.git/hooks/a)", "Edit(~/.claude/projects/*/memory)",
        "Edit(~/.claude/projects/*/memory/*)", "Edit(~/.claude/projects/?/memory/**)",
        "Edit(~/.claude/projects/*?/memory/**)",
        "Edit(~/.claude/projects/*/memory/**/hooks/**)", "Edit(~/.claude/projects/.git/memory/**)",
        "Edit(~/.claude/projects/*/memory/.hidden)", "Edit(~/.claude/projects/*/memory/x/y.md)",
        "Edit(" + PROFILE + "/settings.json)", "Edit(" + PROFILE + "/hooks/x.sh)", "Edit(" + ORCA + "/*)",
        "Edit(" + ORCA + "/claude-profiles/*/{p}/*.json)", "Read(" + ORCA + "/claude-profiles/p2/{p}/settings.json)",
        "Edit(/{h}/src/app/.GIT/Hooks/x)", "Edit(/{h}/src/app/.git/modules/sub/hooks/pre-commit)",
        "Edit(/{h}/src/app/.git/modules/sub/config)", "Edit(/{h}/src/app/.git/worktrees/w/config.worktree)",
        "Edit(/{h}/bare.git/hooks/post-receive)", "Edit(/{h}/bare.git/config)", "Edit(/{h}/src/app/.husky/pre-commit)",
        "Edit(/{h}/src/app/.vscode/tasks.json)", "Edit(/{h}/src/app/.envrc)", "Edit(/{h}/src/app/CLAUDE.md)",
        "Edit(/{h}/src/app/.pre-commit-config.yaml)", "Edit(/{h}/src/app/.git/info/attributes)",
        "Edit(/{h}/src/app/.gitattributes)", "Edit(/{h}/src/app/.github/workflows/x.yml)",
        "Edit(~/.ZSHRC)", "Edit(~/.zsh*)", "Edit(~/.*rc)", "Edit(~/.*)", "Edit(~/Library/*/x.plist)",
        "Edit(//opt/homebrew/*/x)", "Edit(//opt/homebrew/opt/python/bin/x)", "Edit(//opt/homebrew/Cellar/**)",
        "Write(//opt/homebrew/opt/gh/bin/gh)", "Edit(//usr/local/*/x)", "Edit(//private/etc/zshrc)",
        "Edit(//etc/paths.d/x)",
        "Edit(~/.hammerspoon/init.lua)", "Edit(~/.local/share/x)", "Edit(~/.claude/projects/x/y.json)",
        "Edit(~/.claude.json.backup)", "Edit(~/.claude-x/settings.json)",
        "Edit(~/Library/Application Support/Claude/claude_desktop_config.json)", "Edit(~/.cursor/mcp.json)",
        "Edit(~/.codex/config.toml)", "Edit(~/.gemini/settings.json)",
        "mcp__srv", "mcp__plugin__x__y", "mcp__a__b__c", "mcp__srv__", "mcp____tool", "mcp__srv__tool ",
        " mcp__srv__tool", "MCP__srv__tool", "mcp__srv__tool\n", "mcp__srv___tool", "mcp___srv__tool",
        "Agent(x)", "Skill(x)", "Task(x)", 7, None, "",
    ]
    # The two rules a live setup uses, in both spellings, and the other path tools on them.
    ALLOWED = [
        "Edit(/{h}/prof/projects/*/memory/**)", "Edit(~/.claude/projects/*/memory/**)",
        "Edit(/{h}/.claude/projects/*/memory/**)", "Edit(" + ORCA + "/claude-profiles/*/{p}/projects/*/memory/**)",
        "Write(~/.claude/projects/*/memory/**)", "MultiEdit(~/.claude/projects/*/memory/**)",
        "NotebookEdit(~/.claude/projects/*/memory/**)", "Read(~/.claude/projects/*/memory/**)",
        "Write(" + ORCA + "/claude-profiles/*/{p}/projects/*/memory/**)",
        "MultiEdit(" + ORCA + "/claude-profiles/p1/{p}/projects/-Users-x-src-app/memory/*.md)",
        "Edit(~/.claude/projects/x/memory/MEMORY.md)", "mcp__srv__tool", "mcp__plugin_x_y-slack__send_message",
        "mcp__-__-",
    ]

    def test_refused(self):
        for rule in self.BASH + self.REFUSED:
            rule = rule.format(h=self.home, p="home") if isinstance(rule, str) and rule in self.REFUSED else rule
            with self.subTest(rule=rule), self.assertRaisesRegex(config_emit.Refused, "outside this skill|string"):
                config_emit.check_permission(rule)

    def test_allowed(self):
        for rule in self.ALLOWED:
            with self.subTest(rule=rule):
                config_emit.check_permission(rule.format(h=self.home, p="home"))


class HashAndOutTest(Base):
    SPEC = {"permissions": {"add": ["mcp__srv__tool"]}}

    def test_wrong_hash_and_edited_copy_refused(self):
        r, out = self.emit(self.SPEC)
        digest = self.apply_line(r)
        self.assertNotEqual(self.run_copy(out, "--apply", "0" * 12).returncode, 0)
        out.write_text(out.read_text().replace("mcp__srv__tool", "mcp__srv__any"))
        r = self.run_copy(out, "--apply", digest)
        self.assertIn("does not match", r.stderr)
        self.assertNotIn("mcp__srv__any", (self.profile / "settings.json").read_text())

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
        self.assertIn("mcp__srv__tool", (self.profile / "settings.json").read_text())

    def test_run_line_ignores_a_module_planted_beside_the_copy(self):
        r, out = self.emit(self.SPEC)
        line = r.stdout.strip().splitlines()[-1].strip().removeprefix("! ")
        self.assertIn(" python3 -I ", line)
        (out.parent / "difflib.py").write_text("import sys\nsys.stderr.write('PLANTED')\nsys.exit(99)\n")
        r = subprocess.run(["bash", "-c", line], capture_output=True, text=True, env=self.env, cwd=out.parent)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("PLANTED", r.stderr)
        self.assertIn("mcp__srv__tool", (self.profile / "settings.json").read_text())

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
