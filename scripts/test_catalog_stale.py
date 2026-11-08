"""build.py judges a translation stale from git history alone (no hand-written header)."""
import pathlib, shutil, subprocess, sys, tempfile, unittest

REPO = pathlib.Path(__file__).resolve().parents[1]


def run(*args, cwd, check=True):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=check,
                          env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(cwd),
                               "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"})


class Stale(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        shutil.copytree(REPO / "scripts/catalog", self.repo / "scripts/catalog")
        for rel in ("vendor/pstack/manifest.json", "hooks/hooks.json", "docs/skills.md", "docs/skills.en.md",
                    "docs/skills.ja.md"):
            (self.repo / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / rel, self.repo / rel)
        for p in (REPO / "skills").glob("*/SKILL.md"):
            (self.repo / "skills" / p.parent.name).mkdir(parents=True, exist_ok=True)
            (self.repo / "skills" / p.parent.name / "SKILL.md").write_text("x\n")
        run("git", "init", "-q", cwd=self.repo)
        self.commit("base")

    def commit(self, msg):
        run("git", "add", "-A", cwd=self.repo)
        run("git", "-c", "user.name=t", "-c", "user.email=t@example.test", "-c", "commit.gpgsign=false",
            "commit", "-q", "-m", msg, cwd=self.repo)

    def touch(self, name):
        with open(self.repo / "docs" / name, "a") as f:
            f.write("\n")

    def build(self, cwd=None):
        return run(sys.executable, "scripts/catalog/build.py", str(self.tmp / "out.html"), cwd=cwd or self.repo,
                   check=False)

    def built(self):
        # A failed build prints no warning either, so every "not stale" case must also see the build succeed.
        r = self.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stderr

    def test_current_translation_is_not_stale(self):
        self.assertNotIn("is stale", self.built())

    def test_source_only_commit_is_stale(self):
        self.touch("skills.md")
        self.commit("source only")
        err = self.built()
        self.assertIn("docs/skills.en.md is stale", err)
        self.assertIn("docs/skills.ja.md is stale", err)

    def test_catch_up_commit_clears_the_warning(self):
        self.touch("skills.md")
        self.commit("source only")
        self.touch("skills.en.md")
        self.touch("skills.ja.md")
        self.commit("catch up")
        self.assertNotIn("is stale", self.built())

    def test_uncommitted_source_change_is_stale_even_if_translation_is_dirty(self):
        self.touch("skills.md")
        self.touch("skills.en.md")
        err = self.built()
        self.assertIn("docs/skills.en.md is stale", err)
        self.assertIn("docs/skills.ja.md is stale", err)

    def test_shallow_clone_is_refused(self):
        self.touch("skills.md")
        self.commit("second")
        shallow = self.tmp / "shallow"
        run("git", "clone", "-q", "--depth", "1", f"file://{self.repo}", str(shallow), cwd=self.tmp)
        r = self.build(cwd=shallow)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("shallow clone", r.stderr)


if __name__ == "__main__":
    unittest.main()
