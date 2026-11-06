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

    def lint_rules_with(self, rel, text, mode=0o755, skill_extra=""):
        self.put(rel, text, mode)
        if skill_extra:
            self.put("SKILL.md", SKILL + skill_extra)
        return self.rules()

    def test_cleanup_default_to_shared_state_is_not_a_required_argument(self):
        text = '#!/usr/bin/env bash\nstate="${1:-}"\nsource "${state:-/tmp/verify/current.state}"\n'
        self.assertEqual(self.lint_rules_with("scripts/cleanup.sh", text), (1, ["cleanup-arg"]))

    def test_cleanup_guard_in_a_comment_does_not_count(self):
        text = '#!/usr/bin/env bash\n# state="${1:?needed}"\ntrue\n'
        self.assertEqual(self.lint_rules_with("scripts/cleanup.sh", text), (1, ["cleanup-arg"]))

    def test_cleanup_optional_python_argument(self):
        text = "#!/usr/bin/env python3\nimport argparse\np = argparse.ArgumentParser()\np.add_argument('run', nargs='?', default='current')\n"
        self.assertEqual(self.lint_rules_with("scripts/cleanup.py", text, skill_extra="\n`cleanup.py <run>`\n"), (1, ["cleanup-arg"]))

    def test_cleanup_set_u_with_positional_passes(self):
        text = "#!/usr/bin/env bash\nset -eu\nstate=$1\ntrue\n"
        self.assertEqual(self.lint_rules_with("scripts/cleanup.sh", text)[0], 0)

    def test_kill_of_pids_found_on_an_earlier_line(self):
        text = '#!/usr/bin/env bash\npids=$(pgrep -f demo)\nkill "$pids"\n'
        self.assertEqual(self.lint_rules_with("scripts/doctor.sh", text), (1, ["pattern-kill"]))
        text = "#!/usr/bin/env bash\nlsof -ti :3000 | xargs -r kill\n"
        self.assertEqual(self.lint_rules_with("scripts/doctor.sh", text), (1, ["pattern-kill"]))

    def test_kill_inside_a_conditional_is_flagged(self):
        for text in ('#!/usr/bin/env bash\nif [ -n "$1" ]; then pkill -f demo; fi\n',
                     '#!/usr/bin/env bash\npids=$(pgrep -f demo)\nif [ -n "$pids" ]; then kill "$pids"; fi\n'):
            self.assertEqual(self.lint_rules_with("scripts/doctor.sh", text), (1, ["pattern-kill"]))

    def test_image_tag_latest_is_not_a_shared_state_link(self):
        self.assertEqual(self.lint_rules_with("scripts/launch.sh", "#!/usr/bin/env bash\nIMAGE=postgres:latest\n")[0], 0)

    def test_negation_in_a_trailing_comment_does_not_hide_a_shared_link(self):
        text = '#!/usr/bin/env bash\nln -sfn "$RUN_DIR" /tmp/verify/latest # no overwrite\n'
        self.assertEqual(self.lint_rules_with("scripts/launch.sh", text), (1, ["shared-latest"]))

    def test_folded_description_with_content_passes(self):
        self.put("SKILL.md", SKILL.replace('description: "Drive the demo CLI. Use when verifying demo changes."',
                                           "description: >\n  Drive the demo CLI.\n  Use when verifying demo changes."))
        self.assertEqual(self.run_lint()[0], 0)

    def test_python_cleanup_arguments_are_judged_one_by_one(self):
        head = "#!/usr/bin/env python3\nimport argparse\np = argparse.ArgumentParser()\n"
        extra = "\n`cleanup.py <run>`\n"
        cases = [("p.add_argument('run', nargs='*')\n", 1),
                 ("p.add_argument('state')\np.add_argument('--mode', nargs='?')\n", 0),
                 ("p.add_argument('--run', required=True)\n", 0)]
        for body, want in cases:
            self.put("SKILL.md", SKILL + extra)
            self.assertEqual(self.lint_rules_with("scripts/cleanup.py", head + body)[0], want, body)

    def test_javascript_helper_without_shebang_is_checked(self):
        self.put("scripts/drive.mjs", 'import { chromium } from "playwright";\n', 0o644)
        self.assertEqual(self.rules(), (1, ["helper-mode"]))
        self.put("SKILL.md", SKILL + "\nRun `node scripts/drive.mjs`.\n")
        self.assertEqual(self.run_lint()[0], 0)

    def test_feature_h2_inside_a_code_fence_does_not_count(self):
        self.put("features/feat.md", "# Feat\n\n```markdown\n" + FEATURE.split("\n\n", 1)[1] + "```\n")
        self.assertEqual(self.rules(), (1, ["feature-index"]))

    def test_kill_of_a_recorded_pid_passes_after_a_read_only_pgrep(self):
        text = '#!/usr/bin/env bash\npgrep -f demo >/dev/null\nkill "$(cat "$1/pid")"\n'
        self.assertEqual(self.lint_rules_with("scripts/doctor.sh", text)[0], 0)
        text = '#!/usr/bin/env bash\nkill "$(cat "$1/pid")"\necho "Do not use pkill"\n'
        self.assertEqual(self.lint_rules_with("scripts/doctor.sh", text)[0], 0)

    def test_extensionless_helper_is_scanned(self):
        text = "#!/usr/bin/env bash\npkill -f demo\n"
        self.assertEqual(self.lint_rules_with("scripts/stop", text, skill_extra="\nRun `scripts/stop`.\n"), (1, ["pattern-kill"]))

    def test_secret_state_name_without_the_word_state_or_with_backticks(self):
        self.assertEqual(self.lint_rules_with("scripts/launch.sh", '#!/usr/bin/env bash\nRUN_FILE="$RUN_DIR/run-$RUN_ID.env"\n'), (1, ["state-secret"]))
        self.put("scripts/launch.sh", "#!/usr/bin/env bash\ntrue\n", 0o755)
        self.assertEqual(self.lint_rules_with("scripts/launch.sh", "#!/usr/bin/env bash\ntrue\n", skill_extra="\nThe state file is `$RUN_DIR/run-$RUN_ID.env`.\n"), (1, ["state-secret"]))

    def test_latest_directory_is_flagged_and_a_prohibition_or_url_is_not(self):
        self.assertEqual(self.lint_rules_with("scripts/launch.sh", "#!/usr/bin/env bash\nRUN_DIR=/tmp/verify/latest\n"), (1, ["shared-latest"]))
        self.put("scripts/launch.sh", "#!/usr/bin/env bash\ntrue\n", 0o755)
        extra = "\nNever use a shared `latest.state` file.\nRun `curl -L https://github.com/acme/app/releases/latest/download/app -o app`.\n"
        self.assertEqual(self.lint_rules_with("scripts/launch.sh", "#!/usr/bin/env bash\ntrue\n", skill_extra=extra)[0], 0)

    def test_helper_outside_scripts_directory_is_checked(self):
        self.assertEqual(self.lint_rules_with("helpers/launch2.sh", "#!/usr/bin/env bash\ntrue\n", mode=0o644)[1], ["helper-mode"])

    def test_helper_mentioned_only_in_a_sentence_is_not_documented(self):
        self.put("scripts/extra.sh", "#!/usr/bin/env bash\ntrue\n", 0o755)
        self.put("SKILL.md", SKILL + "\nThe extra.sh file is an implementation detail.\n")
        self.assertEqual(self.rules(), (1, ["helper-mode"]))

    def test_frontmatter_comment_and_empty_description_forms(self):
        self.put("SKILL.md", SKILL.replace("name: verify-demo", "name: verify-demo # app"))
        self.assertEqual(self.run_lint()[0], 0)
        for empty in ("null", ">"):
            self.put("SKILL.md", SKILL.replace('description: "Drive the demo CLI. Use when verifying demo changes."', f"description: {empty}"))
            self.assertEqual(self.rules(), (1, ["frontmatter"]), empty)

    def test_headings_inside_a_code_fence_are_not_sections(self):
        fenced = "---\nname: verify-demo\ndescription: x\n---\n\n```\n## Launch\n## Doctor\n## Drive\n## Evidence\n## Cleanup\n```\n"
        self.put("SKILL.md", fenced)
        code, out = self.run_lint()
        self.assertEqual(code, 1)
        self.assertEqual(out.count("sections"), 5)

    def test_reference_style_links_in_the_feature_index(self):
        self.put("features/README.md", "# Map\n\n- [Feat][feat]\n\n[feat]: feat.md\n")
        self.assertEqual(self.run_lint()[0], 0)
        self.put("features/README.md", "# Map\n\n- [Feat](./feat.md)\n- [Gone][gone]\n\n[gone]: gone.md\n")
        self.assertIn("links gone.md", self.run_lint()[1])

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
