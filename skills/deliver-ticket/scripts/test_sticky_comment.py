#!/usr/bin/env python3
"""sticky-comment.sh, run as a subprocess against a fake gh that serves one PR's comments from a JSON file."""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parent / "sticky-comment.sh"
HEAD = "0123456789abcdef0123456789abcdef01234567"
MARKER = "<!-- test-results -->"

FAKE_GH = r'''#!{python}
import json, os, re, sys
state_path = os.environ["FAKE_GH_STATE"]
state = json.load(open(state_path))
args = sys.argv[1:]
with open(state_path + ".calls", "a") as f:
    f.write(json.dumps(args) + "\n")
if args[:2] == ["repo", "view"]:
    print("acme/app")
elif args[:2] == ["pr", "view"]:
    print(state["head"])
elif args[0] == "api" and "-X" in args:
    print("https://example.test/acme/app/pull/7#c")
elif args[0] == "api" and args[1].endswith("/comments"):
    marker = re.search(r'contains\("(.*)"\)', args[args.index("--jq") + 1]).group(1)
    for c in state["comments"]:
        if marker in c["body"]:
            print(c["id"])
elif args[0] == "api" and "/issues/comments/" in args[1]:
    cid = int(args[1].rsplit("/", 1)[1])
    print(next(c["body"] for c in state["comments"] if c["id"] == cid))
else:
    sys.exit("fake gh: unexpected " + " ".join(args))
'''


class StickyCommentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(FAKE_GH.replace("{python}", sys.executable))
        gh.chmod(0o755)
        self.state = self.tmp / "state.json"
        self.env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(self.tmp), "FAKE_GH_STATE": str(self.state)}

    def run_script(self, comments, *args):
        self.state.write_text(json.dumps({"head": HEAD, "comments": comments}))
        return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=self.env)

    def calls(self):
        path = pathlib.Path(str(self.state) + ".calls")
        return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []

    def test_check_passes_on_current_head(self):
        p = self.run_script([{"id": 1, "body": "other"}, {"id": 2, "body": f"{MARKER}\n\nhead {HEAD[:8]}: ok"}],
                            "--check", "7")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("ok:", p.stdout)
        self.assertFalse(any("-X" in c for c in self.calls()), "--check must not write")

    def test_check_fails_on_stale_head(self):
        p = self.run_script([{"id": 2, "body": f"{MARKER}\n\nhead fedcba98: ok"}], "--check", "7")
        self.assertEqual(p.returncode, 1)
        self.assertIn("stale:", p.stderr)

    def test_check_fails_on_another_sha_with_the_same_prefix(self):
        other = HEAD[:7] + "f" * 33
        p = self.run_script([{"id": 2, "body": f"{MARKER}\n\nhead {other}: ok"}], "--check", "7")
        self.assertEqual(p.returncode, 1, p.stdout)

    def test_check_passes_on_full_sha(self):
        p = self.run_script([{"id": 2, "body": f"{MARKER}\n\nhead `{HEAD}`: ok"}], "--check", "7")
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_check_fails_without_comment(self):
        p = self.run_script([{"id": 1, "body": "unrelated"}], "--check", "7")
        self.assertEqual(p.returncode, 1)
        self.assertIn("missing:", p.stderr)

    def test_check_reads_the_first_marker_comment(self):
        p = self.run_script([{"id": 2, "body": f"{MARKER} old head fedcba98"}, {"id": 3, "body": f"{MARKER} {HEAD}"}],
                            "--check", "7")
        self.assertEqual(p.returncode, 1, "land reads the first marker comment, so --check does too")

    def test_upsert_patches_existing(self):
        body = self.tmp / "body.md"
        body.write_text(f"head {HEAD[:8]}")
        p = self.run_script([{"id": 2, "body": f"{MARKER} old"}], "7", str(body))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(p.stdout.startswith("updated: "))
        self.assertTrue(any("PATCH" in c and "repos/acme/app/issues/comments/2" in c for c in self.calls()))

    def test_upsert_refuses_empty_body(self):
        body = self.tmp / "empty.md"
        body.write_text("")
        p = self.run_script([], "7", str(body))
        self.assertEqual(p.returncode, 1)
        self.assertFalse(any("-X" in c for c in self.calls()))


if __name__ == "__main__":
    unittest.main()
