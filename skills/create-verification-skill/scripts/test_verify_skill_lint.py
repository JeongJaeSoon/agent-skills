#!/usr/bin/env python3
"""verify_skill_lint.py against throwaway skill directories; no app is launched."""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

LINT = pathlib.Path(__file__).resolve().parent / "verify_skill_lint.py"
EXAMPLE = LINT.parent.parent / "references" / "feature-map-example"

SKILL = """---
name: verify-demo
description: "Drive the demo CLI. Use when verifying demo changes."
---

# Verify demo

## Launch
Run `scripts/launch.sh`; it prints the state file path.

## Doctor
Run `scripts/doctor.sh <state-file>`.

## Drive
```bash
scripts/drive.sh "$STATE"
```

## Evidence
Screenshots go to `evidence/`.

## Cleanup
```bash
scripts/cleanup.sh "$STATE"
```
"""

FEATURE = "# Feat\n\nText.\n\n## Sub-features\n## How to get to it (user POV)\n## Driving it with demo\n## Gotchas\n"


class LintTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = self.tmp / "verify-demo"
        (self.root / "scripts").mkdir(parents=True)
        (self.root / "features").mkdir()
        self.put("SKILL.md", SKILL)
        for name in ("launch.sh", "doctor.sh", "drive.sh"):
            self.put(f"scripts/{name}", "#!/usr/bin/env bash\ntrue\n", 0o755)
        self.put("scripts/cleanup.sh", '#!/usr/bin/env bash\nstate="${1:?state file required}"\ntrue\n', 0o755)
        self.put("features/README.md", "# Map\n\n- [Feat](./feat.md) does it.\n")
        self.put("features/feat.md", FEATURE)

    def put(self, rel, text, mode=0o644):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        p.chmod(mode)

    def run_lint(self):
        r = subprocess.run([sys.executable, str(LINT), str(self.root)], capture_output=True, text=True)
        return r.returncode, r.stdout

    def rules(self):
        code, out = self.run_lint()
        return code, sorted({ln.split(": ")[1] for ln in out.splitlines() if ": " in ln})

    def test_clean_skill_passes(self):
        self.assertEqual(self.run_lint(), (0, "clean\n"))

    def test_name_must_match_directory(self):
        self.put("SKILL.md", SKILL.replace("name: verify-demo", "name: verify-other"))
        self.assertEqual(self.rules(), (1, ["frontmatter"]))

    def test_missing_frontmatter(self):
        self.put("SKILL.md", SKILL.split("---\n", 2)[2])
        self.assertEqual(self.rules(), (1, ["frontmatter"]))

    def test_missing_section(self):
        self.put("SKILL.md", SKILL.replace("## Doctor", "## Health"))
        code, out = self.run_lint()
        self.assertEqual(code, 1)
        self.assertIn("no Doctor heading", out)

    def test_state_file_with_secret_name(self):
        self.put("SKILL.md", SKILL + "\nThe state file is written to `$RUN_DIR/.env.state`.\n")
        self.assertEqual(self.rules(), (1, ["state-secret"]))

    def test_state_file_named_by_run_id_passes(self):
        self.put("SKILL.md", SKILL + "\nThe state file is `$RUN_DIR/run-$RUN_ID.state`.\n")
        self.assertEqual(self.run_lint()[0], 0)

    def test_shared_latest_link(self):
        self.put("scripts/launch.sh", "#!/usr/bin/env bash\nln -sf \"$STATE\" /tmp/verify/latest\n", 0o755)
        self.assertEqual(self.rules(), (1, ["shared-latest"]))

    def test_releases_latest_url_is_not_a_state_link(self):
        self.put("SKILL.md", SKILL + "\nDownload from github.com/acme/app/releases/latest.\n")
        self.assertEqual(self.run_lint()[0], 0)

    def test_cleanup_without_required_argument(self):
        self.put("scripts/cleanup.sh", "#!/usr/bin/env bash\nstate=\"${1:-$(ls -t /tmp/verify | head -1)}\"\n", 0o755)
        self.assertEqual(self.rules(), (1, ["cleanup-arg"]))

    def test_cleanup_python_with_positional_argument_passes(self):
        self.put("scripts/cleanup.py", "#!/usr/bin/env python3\nimport argparse\np = argparse.ArgumentParser()\np.add_argument('run')\n", 0o755)
        self.put("SKILL.md", SKILL + "\nAlso `cleanup.py <run>`.\n")
        self.assertEqual(self.run_lint()[0], 0)

    def test_pattern_kill_in_script_and_in_fence(self):
        self.put("scripts/cleanup.sh", '#!/usr/bin/env bash\n: "${1:?}"\npkill -f demo\n', 0o755)
        self.put("SKILL.md", SKILL.replace("scripts/cleanup.sh \"$STATE\"", "kill $(pgrep demo)"))
        code, out = self.run_lint()
        self.assertEqual(code, 1)
        self.assertEqual(out.count("pattern-kill"), 2)

    def test_prose_may_forbid_pkill(self):
        self.put("SKILL.md", SKILL + "\nNever use pkill; kill the recorded PID.\n")
        self.assertEqual(self.run_lint()[0], 0)

    def test_commented_pkill_in_script_is_ignored(self):
        self.put("scripts/doctor.sh", "#!/usr/bin/env bash\n# not pkill -f demo\ntrue\n", 0o755)
        self.assertEqual(self.run_lint()[0], 0)

    def test_helper_not_executable_or_not_documented(self):
        self.put("scripts/extra.sh", "#!/usr/bin/env bash\ntrue\n", 0o644)
        code, out = self.run_lint()
        self.assertEqual(code, 1)
        self.assertIn("not executable", out)
        self.assertIn("never shows how to invoke it", out)

    def test_feature_index_gaps(self):
        self.put("features/README.md", "# Map\n\n- [Gone](./gone.md)\n")
        self.put("features/orphan.md", FEATURE)
        self.put("features/feat.md", "# Feat\n\n## Sub-features\n")
        code, out = self.run_lint()
        self.assertEqual(code, 1)
        for want in ("links gone.md", "does not list feat.md", "does not list orphan.md", "no 'gotchas' H2"):
            self.assertIn(want, out)

    def test_missing_feature_index(self):
        (self.root / "features" / "README.md").unlink()
        self.assertEqual(self.rules(), (1, ["feature-index"]))

    def test_shipped_feature_map_example_passes_the_feature_checks(self):
        self.root = self.tmp / "verify-example"
        shutil.copytree(EXAMPLE, self.root / "features")
        self.put("SKILL.md", SKILL.replace("verify-demo", "verify-example"))
        shutil.rmtree(self.root / "scripts", ignore_errors=True)
        self.assertEqual(self.run_lint(), (0, "clean\n"))

    def test_not_a_skill_directory(self):
        self.assertEqual(subprocess.run([sys.executable, str(LINT), str(self.tmp)], capture_output=True).returncode, 2)


if __name__ == "__main__":
    unittest.main()
