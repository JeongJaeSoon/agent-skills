#!/usr/bin/env python3
import http.client, json, os, pathlib, socket, subprocess, sys, tempfile, time, unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import share_server as ss  # noqa: E402


class Args(unittest.TestCase):
    def test_read_only_on_loopback(self):
        args = ss.copyparty_args("/srv/share", 13201)
        self.assertEqual(args[args.index("-i") + 1], "127.0.0.1")
        self.assertEqual(args[args.index("-p") + 1], "13201")
        self.assertEqual([a for a in args if a.startswith("/srv/share")], ["/srv/share::r"])
        for flag in ("--no-dav", "--no-del", "--no-mv", "--no-cp", "--xvol", "--ih"):
            self.assertIn(flag, args)
        self.assertNotIn("-a", args)  # no accounts

    def test_index_and_tag_parser_live_outside_the_share(self):
        args = ss.copyparty_args("/srv/share", 13201)
        self.assertFalse(args[args.index("--hist") + 1].startswith("/srv/share"))
        mtp = args[args.index("-mtp") + 1]
        self.assertTrue(mtp.startswith("body=an,emd,"))
        self.assertTrue(pathlib.Path(mtp.rsplit(",", 1)[1]).is_file())


class TextTag(unittest.TestCase):
    def run_tag(self, name, data):
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / name
            f.write_bytes(data)
            return subprocess.run([sys.executable, str(ss.TEXT_TAG), str(f)], capture_output=True, text=True,
                                  check=True).stdout

    def test_prints_the_head_of_a_text_file_on_one_line(self):
        out = self.run_tag("a.md", "# 제목\n\nzebra  fish\n".encode() + "가".encode() * 30000)
        self.assertTrue(out.startswith("# 제목 zebra fish 가"))
        self.assertEqual(out.count("\n"), 1)
        self.assertLessEqual(len(out.rstrip("\n").encode()), 4096)
        self.assertNotIn("\ufffd", out)  # the cut never splits a character

    def test_html_is_reduced_to_its_text(self):
        out = self.run_tag("p.html", b"<style>.a{}</style><h1>Title</h1><script>var x=1</script><p>body text</p>")
        self.assertEqual(out, "Title body text\n")


class Venv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(ss, "VENVS", pathlib.Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)

    def fake_build(self, cmd, **kw):
        if cmd[1:3] == ["-m", "venv"]:
            (pathlib.Path(cmd[3]) / "bin").mkdir(parents=True)
            (pathlib.Path(cmd[3]) / "bin" / "python").touch()

    def test_builds_once_per_pin_set_beside_older_ones(self):
        old = ss.VENVS / "copyparty-old"
        old.mkdir()
        with mock.patch.object(ss.subprocess, "run", side_effect=self.fake_build) as run:
            py = ss.ensure_venv()
            self.assertEqual(py, ss.venv_dir() / "bin" / "python")
            self.assertTrue(old.is_dir())  # a running server may still use it
            pip = run.call_args_list[-1].args[0]
            self.assertEqual(pip[-len(ss.PINS):], list(ss.PINS))
            self.assertIn(":all:", pip)
            run.reset_mock()
            ss.ensure_venv()
            run.assert_not_called()
        ss.prune_venvs(ss.venv_dir())
        self.assertEqual([d.name for d in ss.VENVS.iterdir()], [ss.venv_dir().name])

    def test_a_venv_key_changes_with_pins(self):
        with mock.patch.object(ss, "PINS", ("copyparty==0.1",)):
            other = ss.venv_dir()
        self.assertNotEqual(other, ss.venv_dir())

    def test_launchd_serve_never_builds(self):
        with mock.patch.object(ss.subprocess, "run") as run, mock.patch.object(ss.os, "execv") as execv, \
                mock.patch("builtins.print"):
            self.assertEqual(ss.serve(self.tmp.name, 13201, str(ss.VENVS / "copyparty-gone")), 1)
        run.assert_not_called()
        execv.assert_not_called()


@unittest.skipUnless(ss.ready(ss.venv_dir()), f"no copyparty venv at {ss.venv_dir()}; run `share_server.py serve` once")
class Live(unittest.TestCase):
    UA = {"User-Agent": "Mozilla/5.0"}  # copyparty answers curl-like clients with plain text

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(cls.tmp.name)
        root, outside = base / "share", base / "outside"
        for d in (root / "gal", root / "site", root / "docs", outside, base / "state"):
            d.mkdir(parents=True)
        (outside / "secret.txt").write_text("outside-content")
        (root / "gal" / "x-390.png").write_bytes(b"\x89PNG")
        (root / "site" / "index.html").write_text("<!doctype html><title>own page</title>")
        (root / "docs" / "note.md").write_text("# Note\n\nthe quick zebrafish\n")
        (root / "docs" / "plain.txt").write_text("한글 본문")
        (root / "docs" / "view.tsx").write_text("export {}")
        (root / ".private").write_text("dot-content")
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text("dot-content")
        (root / ".cpr").mkdir()
        (root / ".cpr" / "own.txt").write_text("dot-content")
        (root / "gitlink").symlink_to(root / ".git")
        (root / "toklink").symlink_to(root / ".private")
        (root / "out").symlink_to(outside)
        cls.root, cls.before = root, cls.snapshot(root)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        with mock.patch.object(ss, "HIST", base / "hist"):
            args = ss.copyparty_args(str(root), cls.port)
        cls.proc = subprocess.Popen([str(ss.venv_dir() / "bin" / "python"), *args],
                                    env={**os.environ, "XDG_CONFIG_HOME": str(base / "state")},
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                if "zebrafish" in cls.search("body like *zebrafish*"):
                    break
            except OSError:
                pass
            time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(10)
        cls.tmp.cleanup()

    @staticmethod
    def snapshot(root):
        return sorted((str(p.relative_to(root)), p.is_file() and p.read_bytes()) for p in root.rglob("*"))

    @classmethod
    def fetch(cls, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=5)
        try:
            c.request(method, path, body, {**cls.UA, **(headers or {})})
            r = c.getresponse()
            return r.status, r.getheader("Content-Type", ""), r.read().decode(errors="replace")
        finally:
            c.close()

    @classmethod
    def request(cls, method, path, body=None, headers=None):
        code, _, text = cls.fetch(method, path, body, headers)
        return code, text

    @classmethod
    def search(cls, q):
        return cls.request("POST", "/?srch", json.dumps({"q": q}), {"Content-Type": "application/json"})[1]

    def test_search_finds_names_and_text_bodies(self):
        self.assertIn("docs/note.md", self.search("body like *zebrafish*"))
        self.assertIn("docs/note.md", self.search("tags like *zebrafish*"))
        self.assertIn("gal/x-390.png", self.search("name like *x-390*"))
        self.assertNotIn(".private", self.search("name like *private*"))

    def test_hidden_paths_are_not_served(self):
        for path in ("/.private", "/.git/config", "/.git/", "/.git/?ls", "/.git/?zip", "/%2egit/config",
                     "/docs/../.git/config", "/.cpr/own.txt", "/toklink", "/gitlink/config", "/gitlink/?ls"):
            code, body = self.request("GET", path)
            self.assertEqual(code, 404, path)
            self.assertNotIn("dot-content", body, path)
        self.assertEqual(self.request("POST", "/.git/?srch", "{}", {"Content-Type": "application/json"})[0], 404)
        form = {"Content-Type": "multipart/form-data; boundary=b"}
        def zip_of(folder, files):
            return self.request("POST", folder + "?zip", '--b\r\nContent-Disposition: form-data; name="act"\r\n\r\nzip\r\n'
                                '--b\r\nContent-Disposition: form-data; name="files"\r\n\r\n'
                                f'{files}\r\n--b--\r\n', form)

        code, body = zip_of("/", ".git\n.private\ndocs")
        self.assertEqual(code, 200)
        self.assertIn("docs/note.md", body)
        self.assertNotIn("dot-content", body)
        self.assertEqual(zip_of("/", ".git\n.private")[0], 404)  # not "the whole folder"
        self.assertEqual(zip_of("/.cpr/", "own.txt")[0], 404)
        self.assertEqual(zip_of("/gitlink/", "config")[0], 404)
        self.assertEqual(self.request("GET", "/.cpr/w/util.js")[0], 200)  # copyparty's own assets

    def test_listing_hides_dotfiles(self):
        code, body = self.request("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn("docs", body)
        self.assertNotIn(".private", body)

    def test_files_and_folder_index_are_served(self):
        self.assertEqual(self.request("GET", "/site/index.html"), (200, "<!doctype html><title>own page</title>"))
        self.assertIn('"idxh": 1', self.request("GET", "/site/")[1])  # the browser opens index.html in its place
        self.assertEqual(self.request("GET", "/docs/note.md")[0], 200)

    def test_a_plain_browser_link_to_markdown_opens_rendered(self):
        html = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
        code, body = self.request("GET", "/docs/note.md", headers=html)
        self.assertEqual(code, 200)
        self.assertIn("md.js", body)
        self.assertIn("view raw", body)
        self.assertEqual(self.request("GET", "/docs/note.md?", headers=html)[1], "# Note\n\nthe quick zebrafish\n")
        self.assertEqual(self.request("GET", "/docs/note.md")[1], "# Note\n\nthe quick zebrafish\n")

    def test_text_responses_declare_utf8(self):
        html = {"Accept": "text/html"}
        for path, headers in (("/docs/note.md", None), ("/docs/note.md", html), ("/docs/note.md?", html),
                              ("/docs/plain.txt", None), ("/docs/view.tsx", None), ("/site/index.html", None), ("/", html)):
            self.assertIn("charset=utf-8", self.fetch("GET", path, headers=headers)[1].lower(), f"{path} {headers}")

    def test_symlink_out_of_the_root_and_traversal_are_refused(self):
        for path in ("/out/secret.txt", "/../outside/secret.txt", "/%2e%2e/outside/secret.txt"):
            code, body = self.request("GET", path)
            self.assertNotEqual(code, 200, path)
            self.assertNotIn("outside-content", body, path)

    def test_writes_are_refused_and_change_nothing(self):
        form = {"Content-Type": "multipart/form-data; boundary=b"}

        def fields(**kw):
            return "".join(f'--b\r\nContent-Disposition: form-data; name="{k}"{v}' for k, v in kw.items()) + "--b--\r\n"

        for method, path, body, headers in (
                ("PUT", "/u.txt", "hi", None),
                ("POST", "/", fields(act="\r\n\r\nmkdir\r\n", name="\r\n\r\nz\r\n"), form),
                ("POST", "/", fields(act="\r\n\r\nbput\r\n", f='; filename="u.txt"\r\n\r\nhi\r\n'), form),
                ("POST", "/?delete", json.dumps(["/docs/note.md"]), {"Content-Type": "application/json"}),
                ("POST", "/docs/note.md?move=/moved.md", "", None),
                ("DELETE", "/docs/note.md", None, None),
                ("MKCOL", "/newdir", None, None),
                ("MOVE", "/docs/note.md", None, {"Destination": "/moved.md"})):
            self.assertIn(self.request(method, path, body, headers)[0], (400, 401, 403, 405), f"{method} {path}")
        self.assertEqual(self.snapshot(self.root), self.before)


class Launchd(unittest.TestCase):
    def test_spec_binds_configured_root_and_port(self):
        with mock.patch.object(ss, "config", return_value={"root": "~/s", "port": 13201, "label": "a.b"}):
            s = ss.spec()
        self.assertEqual(s["Label"], "a.b")
        args = s["ProgramArguments"]
        self.assertEqual(args[args.index("--root") + 1], os.path.expanduser("~/s"))
        self.assertEqual(args[args.index("--port") + 1], "13201")
        self.assertTrue(s["KeepAlive"])

    def test_install_backs_up_a_different_plist(self):
        with tempfile.TemporaryDirectory() as d:
            plist = pathlib.Path(d) / "a.b.plist"
            plist.write_bytes(b"old")
            with mock.patch.object(ss, "plist_path", return_value=plist), \
                    mock.patch.object(ss, "config", return_value={**ss.DEFAULTS, "label": "a.b"}), \
                    mock.patch("builtins.print"), \
                    mock.patch.object(ss, "STATE_DIR", pathlib.Path(d)), \
                    mock.patch.object(ss.sys, "platform", "darwin"), \
                    mock.patch.object(ss, "ensure_venv"), \
                    mock.patch.object(ss.subprocess, "run") as run:
                run.return_value.returncode = 0
                self.assertEqual(ss.cmd_install(True), 0)
            backups = list(pathlib.Path(d).glob("a.b.plist.bak-*"))
            self.assertEqual([b.read_bytes() for b in backups], [b"old"])
            self.assertIn(b"serve", plist.read_bytes())

    def test_failed_venv_build_leaves_the_agent_alone(self):
        with mock.patch.object(ss.sys, "platform", "darwin"), \
                mock.patch.object(ss, "ensure_venv", side_effect=ss.subprocess.CalledProcessError(1, "pip")), \
                mock.patch("builtins.print"), \
                mock.patch.object(ss.subprocess, "run") as run:
            self.assertEqual(ss.cmd_install(True), 1)
        run.assert_not_called()

    def test_install_refuses_a_worktree(self):
        with mock.patch.object(ss.sys, "platform", "darwin"), mock.patch.object(ss.pathlib.Path, "is_dir",
                                                                                 return_value=False):
            self.assertEqual(ss.cmd_install(False), 1)


if __name__ == "__main__":
    unittest.main()
