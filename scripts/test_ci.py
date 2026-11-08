"""ci_tests.py and ci_forbidden_terms.py on throwaway repositories, plus the workflow's pins.
Run: python3 scripts/test_ci.py"""
import contextlib, io, os, pathlib, re, stat, subprocess, sys, tempfile, unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ci_tests

SCANNER = HERE / "ci_forbidden_terms.py"
WORKFLOW = HERE.parent / ".github/workflows/ci.yml"
TERM = "zebra" + "corp"


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.test",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


class Repo(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.repo = pathlib.Path(d.name).resolve()
        git(self.repo, "init", "-q")

    def add(self, name, text, message="x"):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        git(self.repo, "add", name)
        git(self.repo, "commit", "-q", "-m", message)


class CiTests(Repo):
    def run_all(self, excluded=None, timeout=20):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ci_tests.run(self.repo, excluded or {}, timeout)
        return code, out.getvalue()

    def test_a_failing_file_fails_the_run_and_is_named(self):
        self.add("test_ok.py", "print('fine')\n")
        self.add("sub/test_bad.py", "print('why it broke'); raise SystemExit(3)\n")
        code, out = self.run_all()
        self.assertEqual(code, 1)
        self.assertIn("FAILED: sub/test_bad.py", out)
        self.assertIn("why it broke", out)
        self.assertIn("1/2 passed", out)

    def test_all_passing_exits_zero(self):
        self.add("test_ok.py", "\n")
        self.assertEqual(self.run_all()[0], 0)

    def test_a_path_git_would_quote_still_runs(self):
        self.add("sub dir/테스트/test_quoted.py", "raise SystemExit(1)\n")
        self.assertEqual(self.run_all()[0], 1)

    def test_an_excluded_file_does_not_run_and_is_printed_with_its_reason(self):
        self.add("test_ok.py", "\n")
        self.add("test_mac.py", "raise SystemExit(1)\n")
        code, out = self.run_all({"test_mac.py": "macOS only"})
        self.assertEqual(code, 0)
        self.assertIn("skip  test_mac.py: macOS only", out)

    def test_a_timeout_fails(self):
        self.add("test_slow.py", "import time; time.sleep(30)\n")
        code, out = self.run_all(timeout=1)
        self.assertEqual(code, 1)
        self.assertIn("timed out", out)

    def test_a_stale_exclusion_is_refused(self):
        self.add("test_ok.py", "\n")
        with self.assertRaisesRegex(ci_tests.Refused, "gone.py"):
            self.run_all({"gone.py": "x"})

    def test_no_test_files_is_refused(self):
        self.add("README.md", "\n")
        with self.assertRaisesRegex(ci_tests.Refused, "no test files"):
            self.run_all()

    def test_outside_a_repository_is_refused(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaisesRegex(ci_tests.Refused, "failed"):
            ci_tests.run(d, {}, 1)

    def test_a_shallow_clone_is_refused(self):
        self.add("test_ok.py", "\n")
        self.add("test_two.py", "\n")
        with tempfile.TemporaryDirectory() as d:
            clone = pathlib.Path(d) / "c"
            subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{self.repo}", str(clone)], check=True)
            with self.assertRaisesRegex(ci_tests.Refused, "shallow"):
                ci_tests.run(clone, {}, 1)

    def test_the_child_environment_is_clean(self):
        check = ("import os, subprocess, sys\n"
                 "if sys.flags.optimize or 'CI_LEAK' in os.environ: sys.exit(1)\n"
                 "name = subprocess.run(['git', 'config', 'user.name'], capture_output=True, text=True).stdout\n"
                 "sys.exit(name.strip() != 'ci')\n")
        self.add("test_env.py", check)
        os.environ.update(PYTHONOPTIMIZE="1", CI_LEAK="1")
        self.addCleanup(os.environ.pop, "PYTHONOPTIMIZE")
        self.addCleanup(os.environ.pop, "CI_LEAK")
        code, out = self.run_all()
        self.assertEqual(code, 0, out)

    def run_jobs(self, jobs, timeout=20):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ci_tests.run(self.repo, {}, timeout, jobs)
        return code, out.getvalue()

    def test_parallel_runs_overlap_and_run_every_file_once(self):
        # Each file waits until the other two have started: with fewer than 3 workers they would deadlock to the timeout.
        marks = self.repo / "marks"
        marks.mkdir()
        body = ("import pathlib, sys, time\n"
                "d = pathlib.Path({marks!r})\n"
                "with open(d / {name!r}, 'a') as f: f.write('x')\n"
                "end = time.time() + 15\n"
                "while len(list(d.iterdir())) < 3:\n"
                "    if time.time() > end: sys.exit(1)\n"
                "    time.sleep(0.05)\n")
        for n in "abc":
            self.add(f"test_{n}.py", body.format(marks=str(marks), name=n))
        code, out = self.run_jobs(3, timeout=30)
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(marks.iterdir()), [marks / n for n in "abc"])
        self.assertEqual([(marks / n).read_text() for n in "abc"], ["x"] * 3, "a file ran more than once")
        self.assertIn("3/3 passed", out)
        self.assertEqual(out.count("ok  "), 3)

    def test_parallel_failure_is_named_and_counted(self):
        self.add("test_ok.py", "\n")
        self.add("sub/test_bad.py", "print('why it broke'); raise SystemExit(3)\n")
        code, out = self.run_jobs(4)
        self.assertEqual(code, 1)
        self.assertIn("FAILED: sub/test_bad.py", out)
        self.assertIn("why it broke", out)
        self.assertIn("1/2 passed", out)

    def test_jobs_below_one_is_refused(self):
        self.add("test_ok.py", "\n")
        with self.assertRaisesRegex(ci_tests.Refused, "jobs"):
            self.run_jobs(0)

    def test_the_workflow_runs_tests_in_parallel(self):
        text = WORKFLOW.read_text()
        self.assertRegex(text, r"python3 scripts/ci_tests\.py --jobs [2-9]\b")

    def test_every_excluded_path_exists_in_this_repository(self):
        tracked = subprocess.run(["git", "-C", str(HERE.parent), "ls-files"], capture_output=True, text=True).stdout
        self.assertLessEqual(set(ci_tests.EXCLUDED), set(tracked.splitlines()))


class Scanner(Repo):
    def scan(self, terms, **env):
        full = {"PATH": os.environ["PATH"], "HOME": str(self.repo), **env}
        if terms is not None:
            full["FORBIDDEN_TERMS"] = terms
        return subprocess.run([sys.executable, str(SCANNER)], cwd=self.repo, env=full, capture_output=True, text=True)

    def fake_gh(self, title, body):
        stub = self.repo / ".bin"
        stub.mkdir(exist_ok=True)
        gh = stub / "gh"
        gh.write_text(f"#!/bin/sh\nprintf '%s\\n%s\\n' {title!r} {body!r}\n")
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        return f"{stub}{os.pathsep}{os.environ['PATH']}"

    def assert_quiet(self, p):
        self.assertNotIn(TERM, (p.stdout + p.stderr).lower())
        self.assertNotIn("secret.md", p.stdout + p.stderr)

    def test_no_list_warns_once_and_passes(self):
        self.add("a.md", f"{TERM}\n")
        for terms in (None, "", " \n\n"):
            p = self.scan(terms)
            self.assertEqual(p.returncode, 0, p.stdout)
            self.assertEqual(p.stdout.count("::warning::"), 1)

    def test_a_clean_repository_passes(self):
        self.add("a.md", "nothing here\n")
        p = self.scan(f"{TERM}\n")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("tree: 0", p.stdout)

    def test_a_hit_in_the_tree_fails_with_counts_only(self):
        self.add("secret.md", f"see {TERM.upper()} here\n")
        p = self.scan(f"{TERM}\n")
        self.assertEqual(p.returncode, 1)
        self.assertIn("tree: 1", p.stdout)
        self.assert_quiet(p)

    def test_a_blank_line_in_the_list_does_not_match_everything(self):
        self.add("a.md", "nothing here\n")
        self.assertEqual(self.scan(f"\n{TERM}\n\n").returncode, 0)

    def test_a_hit_in_a_commit_message_fails(self):
        self.add("a.md", "x\n")
        base = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        self.add("b.md", "y\n", f"mentions {TERM}")
        p = self.scan(TERM, BASE_SHA=base, HEAD_SHA="HEAD")
        self.assertEqual(p.returncode, 1)
        self.assertIn("commit messages: 1", p.stdout)
        self.assert_quiet(p)
        self.add("c.md", "z\n")
        head = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(self.scan(TERM, BASE_SHA=head, HEAD_SHA=head).returncode, 0)

    def test_a_new_branch_push_reads_its_whole_history(self):
        self.add("a.md", "x\n", f"mentions {TERM}")
        self.add("b.md", "y\n", "clean")
        p = self.scan(TERM, BASE_SHA="0" * 40, HEAD_SHA="HEAD")
        self.assertEqual(p.returncode, 1)
        self.assertIn("commit messages: 1", p.stdout)
        self.assert_quiet(p)

    def test_a_hit_in_the_pr_text_fails(self):
        self.add("a.md", "x\n")
        p = self.scan(TERM, PR_NUMBER="7", GITHUB_REPOSITORY="acme/app", PATH=self.fake_gh("title", f"body {TERM}"))
        self.assertEqual(p.returncode, 1)
        self.assertIn("PR title and body: 1", p.stdout)
        self.assert_quiet(p)

    def test_a_pr_text_read_failure_fails_without_the_response(self):
        self.add("a.md", "x\n")
        stub = self.repo / ".bin"
        stub.mkdir()
        gh = stub / "gh"
        gh.write_text(f"#!/bin/sh\necho 'response {TERM}'; echo 'error {TERM}' >&2; exit 1\n")
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        p = self.scan(TERM, PR_NUMBER="7", GITHUB_REPOSITORY="acme/app", PATH=f"{stub}{os.pathsep}{os.environ['PATH']}")
        self.assertEqual(p.returncode, 1)
        self.assertIn("could not read the PR", p.stdout)
        self.assert_quiet(p)

    def test_an_invalid_pattern_fails_without_quoting_it(self):
        self.add("a.md", "x\n")
        p = self.scan(f"{TERM}(\n")
        self.assertEqual(p.returncode, 1)
        self.assertIn("::error::", p.stdout)
        self.assert_quiet(p)

    def test_children_do_not_get_the_list(self):
        self.add("a.md", "x\n")
        stub = self.repo / ".bin"
        stub.mkdir()
        gh = stub / "gh"
        gh.write_text('#!/bin/sh\n[ -z "$FORBIDDEN_TERMS" ] || exit 9\necho clean\n')
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        p = self.scan(TERM, PR_NUMBER="7", GITHUB_REPOSITORY="acme/app", PATH=f"{stub}{os.pathsep}{os.environ['PATH']}")
        self.assertEqual(p.returncode, 0, p.stdout)


class Workflow(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text()

    def test_actions_are_pinned_to_a_sha(self):
        uses = re.findall(r"uses:\s*(\S+)", self.text)
        self.assertTrue(uses)
        for action in uses:
            self.assertRegex(action, r"@[0-9a-f]{40}$")

    def test_permissions_stay_read_only(self):
        self.assertEqual(re.findall(r"^ *(?:permissions:|[a-z-]+: (?:read|write)$)", self.text, re.M),
                         ["permissions:", "  contents: read"])

    def test_the_job_names_required_checks_match_stay(self):
        names = re.findall(r"^    name: (\S+)$", self.text, re.M)
        self.assertEqual(names, ["plugin-validate", "registry-check", "tests", "forbidden-terms"])
        self.assertNotIn("\n    if:", self.text, "a skipped job counts as success for a required check")


if __name__ == "__main__":
    unittest.main()
