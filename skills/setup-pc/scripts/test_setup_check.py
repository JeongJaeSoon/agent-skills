#!/usr/bin/env python3
import datetime, json, os, pathlib, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import setup_check as sc  # noqa: E402

REMOTE = "a" * 40


def fake_run(answers):
    """Answer a command by the first key that is a prefix of it; unknown commands fail."""
    def run(cmd, timeout=20):
        for k, v in answers.items():
            if tuple(cmd[:len(k)]) == k:
                return v
        return 1, ""
    return run


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.cfg = self.tmp / "claude"
        self.cfg.mkdir()
        env = {"CLAUDE_CONFIG_DIR": str(self.cfg), "AGENT_SKILLS_STATE": str(self.tmp / "state"),
               "AGENT_SKILLS_CONFIG_DIR": str(self.tmp / "local"), "CLAUDE_SETTINGS": ""}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def settings(self, d):
        (self.cfg / "settings.json").write_text(json.dumps(d))

    def checkout(self, git_dir=True):
        co = self.tmp / "co"
        (co / "skills").mkdir(parents=True)
        (co / ".claude-plugin").mkdir()
        (co / ".git").mkdir() if git_dir else (co / ".git").write_text("gitdir: elsewhere")
        return co

    def by_item(self, rows):
        return {r["item"]: r for r in rows}


class Install(Base):
    def test_none_points_to_both_install_paths(self):
        rows = sc.check_install({}, {}, sc.paths(), fake_run({}), darwin=False)
        self.assertEqual(rows[0]["status"], "missing")
        self.assertIn("bootstrap.py --write", rows[0]["next"])
        self.assertIn("claude plugin install agent-skills@jeongjaesoon", rows[0]["next"])

    def test_both_warns_double_load(self):
        co = self.checkout()
        s = {"env": {sc.ENV_KEY: str(co)}, "enabledPlugins": {sc.MARKET_ID: True}}
        rows = sc.check_install(s, {}, sc.paths(), fake_run({}), darwin=False)
        self.assertEqual(rows[0]["status"], "warn")

    def test_marketplace_behind_gives_update_lines(self):
        s = {"enabledPlugins": {sc.MARKET_ID: True}}
        inst = {"plugins": {sc.MARKET_ID: [{"gitCommitSha": "b" * 40}]}}
        run = fake_run({("git", "ls-remote"): (0, f"{REMOTE}\trefs/heads/main")})
        rows = self.by_item(sc.check_install(s, inst, sc.paths(), run, darwin=False))
        self.assertEqual(rows["최신 여부"]["status"], "differs")
        for line in sc.UPDATE_LINES:
            self.assertIn(line, rows["최신 여부"]["next"])

    def test_marketplace_current(self):
        s = {"enabledPlugins": {sc.MARKET_ID: True}}
        inst = {"plugins": {sc.MARKET_ID: [{"gitCommitSha": REMOTE}]}}
        run = fake_run({("git", "ls-remote"): (0, f"{REMOTE}\trefs/heads/main")})
        self.assertEqual(self.by_item(sc.check_install(s, inst, sc.paths(), run, darwin=False))["최신 여부"]["status"], "ok")

    def inline(self, sync, head=REMOTE, branch="main", git_dir=True, darwin=False, launchd_rc=0):
        co = self.checkout(git_dir)
        if sync is not None:
            (self.tmp / "state").mkdir()
            (self.tmp / "state" / "sync.json").write_text(json.dumps(sync))
        run = fake_run({("git", "ls-remote"): (0, f"{REMOTE}\trefs/heads/main"),
                        ("git", "-C", str(co), "rev-parse", "--abbrev-ref"): (0, branch),
                        ("git", "-C", str(co), "rev-parse", "HEAD"): (0, head),
                        ("launchctl",): (launchd_rc, "")})
        now = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)
        return self.by_item(sc.check_install({"env": {sc.ENV_KEY: str(co)}}, {}, sc.paths(), run, darwin, now))

    def test_inline_fresh_sync_is_ok(self):
        rows = self.inline({"ok": True, "message": "up to date", "at": "2026-01-01T11:50:00+0000", "interval": 900})
        self.assertEqual(rows["최신 여부"]["status"], "ok")
        self.assertEqual(rows["자동 동기화"]["status"], "ok")
        self.assertNotIn("체크아웃", rows)

    def test_inline_stalled_sync_and_behind(self):
        rows = self.inline({"ok": True, "message": "up to date", "at": "2026-01-01T10:00:00+0000", "interval": 900},
                           head="c" * 40)
        self.assertEqual(rows["자동 동기화"]["status"], "warn")
        self.assertEqual(rows["최신 여부"]["status"], "differs")
        self.assertIn("skills-sync sync", rows["최신 여부"]["next"])

    def test_inline_failed_sync_warns(self):
        rows = self.inline({"ok": False, "message": "checkout has uncommitted changes",
                            "at": "2026-01-01T11:59:00+0000", "interval": 900})
        self.assertEqual(rows["자동 동기화"]["status"], "warn")

    def test_inline_worktree_and_no_sync(self):
        rows = self.inline(None, branch="feature", git_dir=False)
        self.assertEqual(rows["체크아웃"]["status"], "warn")
        self.assertEqual(rows["자동 동기화"]["status"], "missing")

    def test_inline_offline_still_has_freshness_row(self):
        co = self.checkout()
        rows = self.by_item(sc.check_install({"env": {sc.ENV_KEY: str(co)}}, {}, sc.paths(), fake_run({}), darwin=False))
        self.assertEqual(rows["최신 여부"]["status"], "warn")

    def test_launchd_missing_on_darwin(self):
        rows = self.inline({"ok": True, "message": "ok", "at": "2026-01-01T11:59:00+0000"}, darwin=True, launchd_rc=113)
        self.assertEqual(rows["launchd 동기화 작업"]["status"], "missing")


class Tools(Base):
    def test_unsigned_git_is_missing(self):
        run = fake_run({("gh", "auth"): (0, ""), ("git", "config", "--global", "--get", "commit.gpgsign"): (0, "")})
        rows = self.by_item(sc.check_tools(run))
        self.assertEqual(rows["git 서명"]["status"], "missing")

    def test_signed_git_is_ok(self):
        run = fake_run({("git", "config", "--global", "--get", "commit.gpgsign"): (0, "true"),
                        ("git", "config", "--global", "--get", "user.signingkey"): (0, "~/.ssh/id.pub")})
        self.assertEqual(self.by_item(sc.check_tools(run))["git 서명"]["status"], "ok")


class Automations(Base):
    def test_default_on_but_absent(self):
        out = json.dumps([{"name": "janitor", "default": "on", "state": "on"},
                          {"name": "pressure-watch", "default": "on", "state": "absent"},
                          {"name": "preview-watch", "default": "off", "state": "absent"}])
        rows = sc.check_automations(lambda cmd, timeout=0: (0, out))
        self.assertEqual(rows[0]["status"], "missing")
        self.assertIn("pressure-watch", rows[0]["have"])
        self.assertNotIn("preview-watch", rows[0]["have"])

    def test_unreadable(self):
        self.assertEqual(sc.check_automations(lambda cmd, timeout=0: (1, "boom"))[0]["status"], "warn")


SETTINGS = {
    "language": "English", "outputStyle": "Explanatory",
    "permissions": {"defaultMode": "auto", "allow": ["Bash(gh pr view:*)", "Bash(curl -H x ghp_FAKEFAKEFAKE)"]},
    "autoMode": {"allow": ["$defaults", "Merging PRs in acme/app once checks are green."], "soft_deny": ["$defaults"]},
    "env": {"SOME_TOKEN": "do-not-export"},
}


class Baseline(Base):
    def setUp(self):
        super().setUp()
        self.settings(SETTINGS)
        (self.cfg / "agent-skills.json").write_text(json.dumps({"tracker": {"adapter": "linear"}, "notes": {}}))
        (self.cfg / "skills" / "my-skill").mkdir(parents=True)
        (self.cfg / "CLAUDE.md").write_text("x")

    def test_export_drops_env_and_credentials(self):
        b = sc.export()
        text = json.dumps(b)
        self.assertNotIn("do-not-export", text)
        self.assertNotIn("ghp_", text)
        self.assertEqual(b["skipped_secretish"], 1)
        self.assertEqual(b["permissions_allow"], ["Bash(gh pr view:*)"])
        self.assertEqual(b["autoMode"]["allow"], ["Merging PRs in acme/app once checks are green."])
        self.assertEqual(b["agent_skills_keys"], ["tracker.adapter", "notes"])
        self.assertEqual(b["personal"], {"CLAUDE.md": True, "skills": ["my-skill"], "agents": []})

    def test_new_pc_against_baseline(self):
        base = sc.export()
        self.settings({"language": "English"})
        (self.cfg / "agent-skills.json").unlink()
        (self.cfg / "skills" / "my-skill").rmdir()
        rows = self.by_item(sc.check_settings(json.loads((self.cfg / "settings.json").read_text()), base)
                            + sc.check_config(sc.paths(), base))
        self.assertEqual(rows["language"]["status"], "ok")
        self.assertEqual(rows["outputStyle"]["status"], "differs")
        self.assertEqual(rows["permissions.defaultMode"]["status"], "differs")
        self.assertEqual(rows["autoMode"]["status"], "differs")
        self.assertIn("1개 없음", rows["autoMode"]["have"])
        self.assertEqual(rows["permissions.allow"]["status"], "differs")
        self.assertIn("tracker.adapter", rows["agent-skills.json 키"]["have"])
        self.assertIn("skills/my-skill", rows["개인 지침·스킬"]["have"])
        self.assertEqual(sc.automode_spec(base), {"add": {"allow": ["Merging PRs in acme/app once checks are green."]}})

    def test_same_pc_matches_its_own_baseline(self):
        base = sc.export()
        rows = sc.check_settings(SETTINGS, base) + sc.check_config(sc.paths(), base)
        self.assertFalse([r for r in rows if r["status"] in ("missing", "differs", "warn")], rows)
        self.assertEqual(sc.automode_spec(base), {"add": {}})

    def test_no_baseline_never_says_differs(self):
        rows = sc.check_settings({}, None) + sc.check_config(sc.paths(), None)
        self.assertFalse([r for r in rows if r["status"] == "differs"])


class Cli(Base):
    def test_main_writes_nothing(self):
        self.settings(SETTINGS)
        before = sorted(p for p in self.tmp.rglob("*"))
        with mock.patch.object(sc, "run", fake_run({})), mock.patch("sys.stdout"):
            for argv in (["export"], ["check"], ["check", "--json"]):
                self.assertEqual(sc.main(argv), 0)
        self.assertEqual(before, sorted(p for p in self.tmp.rglob("*")))

    def test_bad_args(self):
        with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            self.assertEqual(sc.main([]), 2)
            self.assertEqual(sc.main(["automode-spec"]), 2)
            self.assertEqual(sc.main(["check", "--baseline", str(self.tmp / "nope.json")]), 1)

    def test_table_escapes_pipes_and_counts(self):
        out = sc.table([sc.row("ok", "a|b", "x"), sc.row("missing", "c", "y", "z")])
        self.assertIn("a\\|b", out)
        self.assertIn("남은 것 1개 / 전체 2개", out)


if __name__ == "__main__":
    unittest.main()
