#!/usr/bin/env python3
"""artifact_hooks_cutover.py: dry run writes nothing; --apply drops only the old Artifact hook entries, keeps a backup
of each file, and carries the old category rules over."""
import json, pathlib, subprocess, sys, tempfile, unittest

SCRIPT = pathlib.Path(__file__).with_name("artifact_hooks_cutover.py")
SETTINGS = {
    "model": "x",
    "hooks": {
        "PostToolUse": [
            {"matcher": "Artifact", "hooks": [{"type": "command", "command": 'python3 "$HOME/.claude/hooks/artifact-archive.py"'}]},
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "other.sh"}]},
        ],
        "Stop": [
            {"hooks": [{"type": "command", "command": 'python3 "$HOME/.claude/hooks/artifact-links-stop.py"'},
                       {"type": "command", "command": "keep.sh"}]},
        ],
    },
}
OLD_HOOK = """CATEGORY_RULES = [
    ('Infra', re.compile(r'grafana|loki', re.I)),
    ('Docs', re.compile(r'\\bplan\\b|guide', re.I)),
]
OTHER = 'x'
"""


class Cutover(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="cutover-test-"))
        self.settings, self.config, self.old = (self.dir / n for n in ("settings.json", "agent-skills.json", "old.py"))
        self.settings.write_text(json.dumps(SETTINGS))
        self.config.write_text(json.dumps({"guard": {"x": "warn"}}))
        self.old.write_text(OLD_HOOK)

    def run_script(self, *extra):
        return subprocess.run([sys.executable, str(SCRIPT), "--base-url", "https://files.example.test/a/", "--settings",
                               str(self.settings), "--config", str(self.config), *extra],
                              capture_output=True, text=True, timeout=30)

    def test_dry_run_writes_nothing(self):
        r = self.run_script("--old-hook", str(self.old))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("remove 2 hook entries", r.stdout)
        self.assertEqual(json.loads(self.settings.read_text()), SETTINGS)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["agent-skills.json", "old.py", "settings.json"])

    def test_apply_removes_only_old_entries(self):
        r = self.run_script("--old-hook", str(self.old), "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        hooks = json.loads(self.settings.read_text())["hooks"]
        self.assertEqual(hooks["PostToolUse"], [SETTINGS["hooks"]["PostToolUse"][1]])
        self.assertEqual(hooks["Stop"], [{"hooks": [{"type": "command", "command": "keep.sh"}]}])
        self.assertEqual(json.loads(self.settings.read_text())["model"], "x")
        self.assertEqual(len(list(self.dir.glob("settings.json.bak-*"))), 1)
        self.assertEqual(len(list(self.dir.glob("agent-skills.json.bak-*"))), 1)

    def test_apply_sets_config_keys(self):
        self.run_script("--old-hook", str(self.old), "--apply")
        cfg = json.loads(self.config.read_text())
        self.assertEqual(cfg["guard"], {"x": "warn"})
        self.assertEqual(cfg["artifact-backup"]["base_url"], "https://files.example.test/a/")
        self.assertEqual(cfg["artifact-backup"]["categories"], [["Infra", "grafana|loki"], ["Docs", "\\bplan\\b|guide"]])

    def test_second_apply_keeps_the_original_backup(self):
        self.run_script("--apply")
        first = next(self.dir.glob("settings.json.bak-*"))
        self.run_script("--apply")
        self.assertEqual(json.loads(first.read_text()), SETTINGS)

    def test_rerun_is_a_no_op_on_hooks(self):
        self.run_script("--apply")
        r = self.run_script()
        self.assertIn("remove 0 hook entries", r.stdout)


if __name__ == "__main__":
    unittest.main()
