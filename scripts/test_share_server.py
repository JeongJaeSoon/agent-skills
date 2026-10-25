#!/usr/bin/env python3
import http.client, os, pathlib, socket, subprocess, sys, tempfile, time, unittest
from urllib.parse import quote
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import share_server as ss  # noqa: E402


class Launchd(unittest.TestCase):
    def test_spec_runs_serve_on_configured_root_and_port(self):
        with mock.patch.object(ss, "config", return_value={**ss.DEFAULTS, "root": "~/s", "label": "a.b"}):
            s = ss.spec()
        self.assertEqual(s["Label"], "a.b")
        args = s["ProgramArguments"]
        self.assertIn("serve", args)
        self.assertEqual(args[args.index("--root") + 1], os.path.expanduser("~/s"))
        self.assertEqual(args[args.index("--port") + 1], "13201")
        self.assertTrue(s["KeepAlive"])

    def test_caddy_command_runs_caddy_in_the_sandbox(self):
        with tempfile.TemporaryDirectory() as d:
            caddy = pathlib.Path(d) / "caddy"
            caddy.write_text("")
            caddy.chmod(0o755)
            root = pathlib.Path(d) / "sh[are (old)"
            root.mkdir()
            args, env = ss.caddy_command({**ss.DEFAULTS, "caddy": str(caddy)}, str(root), 13201)
            real = os.path.realpath(root)
        self.assertEqual(args[:3], [ss.SANDBOX_EXEC, "-f", str(ss.CADDY_DIR / "sandbox.sb")])
        self.assertIn(f"ROOT={real}", args)
        self.assertIn(f"ROOT_RE={real.replace('[', chr(92) + '[').replace('(', chr(92) + '(').replace(')', chr(92) + ')')}",
                      args)
        self.assertEqual(args[args.index(str(caddy)):][:4], [str(caddy), "run", "--config",
                                                             str(ss.CADDY_DIR / "Caddyfile")])
        self.assertEqual((env["SHARE_ROOT"], env["SHARE_PORT"]), (real, "13201"))

    def test_a_caddy_path_that_does_not_run_counts_as_missing(self):
        self.assertIn("brew install caddy", ss.caddy_missing({**ss.DEFAULTS, "caddy": "/nope/caddy"}))

    def test_a_root_with_glob_characters_is_refused(self):
        for root in ("/x/sh[are]", "/x/s{http.request.uri.query.r}", '/x/a"b'):
            self.assertIn("glob", ss.caddy_missing(ss.DEFAULTS, root), root)

    def test_install_refuses_without_caddy(self):
        with mock.patch.object(ss.sys, "platform", "darwin"), \
                mock.patch.object(ss, "config", return_value={**ss.DEFAULTS, "caddy": "/nope/caddy"}), \
                mock.patch.object(ss, "plist_path", return_value=pathlib.Path("/nonexistent/a.plist")), \
                mock.patch("builtins.print") as out, \
                mock.patch.object(ss.subprocess, "run") as run:
            self.assertEqual(ss.cmd_install(True), 1)
        run.assert_not_called()
        self.assertIn("brew install caddy", str(out.call_args))

    def test_install_backs_up_a_different_plist(self):
        with tempfile.TemporaryDirectory() as d:
            plist = pathlib.Path(d) / "a.b.plist"
            plist.write_bytes(b"old")
            with mock.patch.object(ss, "plist_path", return_value=plist), \
                    mock.patch.object(ss, "config", return_value={**ss.DEFAULTS, "label": "a.b"}), \
                    mock.patch("builtins.print"), \
                    mock.patch.object(ss, "STATE_DIR", pathlib.Path(d)), \
                    mock.patch.object(ss, "CADDY_STATE", pathlib.Path(d) / "caddy"), \
                    mock.patch.object(ss.sys, "platform", "darwin"), \
                    mock.patch.object(ss.subprocess, "run") as run:
                run.return_value.returncode = 0
                self.assertEqual(ss.cmd_install(True), 0)
            backups = list(pathlib.Path(d).glob("a.b.plist.bak-*"))
            self.assertEqual([b.read_bytes() for b in backups], [b"old"])
            self.assertIn(b"serve", plist.read_bytes())

    def test_install_refuses_a_worktree(self):
        with mock.patch.object(ss.sys, "platform", "darwin"), mock.patch.object(ss.pathlib.Path, "is_dir",
                                                                                 return_value=False):
            self.assertEqual(ss.cmd_install(False), 1)


@unittest.skipIf(ss.caddy_missing(ss.DEFAULTS), "needs caddy and sandbox-exec")
class Caddy(unittest.TestCase):
    """The real Caddyfile and sandbox profile, run through `share_server.py serve`."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(cls.tmp.name)
        root, outside = base / "sh are (old)+", base / "outside"
        for d in (root / "gal", root / "docs", root / "site", root / ".private", outside):
            d.mkdir(parents=True)
        (outside / "secret.txt").write_text("secret")
        (root / "gal" / "a.png").write_bytes(b"\x89PNG")
        (root / "gal" / ".hidden.png").write_bytes(b"\x89PNG")
        (root / "gal" / "index.txt").write_text("not an index")
        (root / "gal" / "old.txt").write_text("o")
        os.utime(root / "gal" / "old.txt", (1000, 1000))
        (root / "gal" / "leak.txt").symlink_to(outside / "secret.txt")
        (root / "gal" / "to-hidden.png").symlink_to(root / "gal" / ".hidden.png")
        (root / "gal" / "inside.png").symlink_to(root / "gal" / "a.png")
        (root / "out").symlink_to(outside)
        (root / "hosts.txt").symlink_to("/etc/hosts")
        (root / "usr-share").symlink_to("/usr/share")
        (root / ".private" / "p.md").write_text("# hidden")
        (root / "docs" / "보고서.md").write_text("# 한글 제목\n\n본문 <script>alert(1)</script>\n")
        (root / "docs" / "a<b>.md").write_text("# x")
        (root / "docs" / "note.txt").write_text("한글")
        (root / "site" / "index.html").write_text("<!doctype html><p>own page {{.OriginalReq.URL.Path}}")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        env = {**os.environ, "AGENT_SKILLS_CONFIG": str(base / "none.json"), "SHARE_SERVER_STATE": str(base / "st")}
        cls.proc = subprocess.Popen([sys.executable, str(pathlib.Path(ss.__file__)), "serve", "--root", str(root),
                                     "--port", str(cls.port)], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.addClassCleanup(cls.proc.wait, 10)
        cls.addClassCleanup(cls.proc.terminate)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=0.2).close()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("caddy did not start")

    def req(self, path, method="GET"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            c.request(method, quote(path, safe="/?=%"))
            r = c.getresponse()
            return r.status, r.getheader("Content-Type") or "", r.read().decode(errors="replace")
        finally:
            c.close()

    def test_listing_is_newest_first_and_ignores_index_txt(self):
        code, ctype, body = self.req("/gal/")
        self.assertEqual((code, ctype), (200, "text/html; charset=utf-8"))
        self.assertIn("a.png", body)
        self.assertNotIn("not an index", body)
        self.assertLess(body.index("a.png"), body.index("old.txt"))

    def test_markdown_renders_and_raw_is_utf8_text(self):
        code, ctype, body = self.req("/docs/보고서.md")
        self.assertEqual((code, ctype), (200, "text/html; charset=utf-8"))
        self.assertIn("<h1", body)
        self.assertIn("한글 제목", body)
        for raw in ("?raw=1", "?raw"):
            code, ctype, body = self.req("/docs/보고서.md" + raw)
            self.assertEqual((code, ctype), (200, "text/plain; charset=utf-8"), raw)
            self.assertTrue(body.startswith("# 한글 제목"), raw)
        self.assertEqual(self.req("/docs/none.md")[0], 404)

    def test_rendered_markdown_cannot_run_scripts(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", quote("/docs/보고서.md"))
        csp = c.getresponse().getheader("Content-Security-Policy")
        c.close()
        self.assertIn("default-src 'none'", csp)
        self.assertNotIn("script-src", csp)
        self.assertIn("<title>/docs/a&lt;b&gt;.md</title>", self.req("/docs/a<b>.md")[2])

    def test_text_is_utf8_and_html_is_served_as_is(self):
        self.assertEqual(self.req("/docs/note.txt"), (200, "text/plain; charset=utf-8", "한글"))
        code, _, body = self.req("/site/")
        self.assertEqual(code, 200)
        self.assertIn("own page {{.OriginalReq.URL.Path}}", body)

    def test_hidden_paths_do_not_exist(self):
        for path in ("/gal/.hidden.png", "/.private/p.md", "/.private/", "/%2eprivate/p.md"):
            self.assertEqual(self.req(path)[0], 404, path)
        self.assertNotIn(".hidden.png", self.req("/gal/")[2])

    def test_symlinks_cannot_reach_outside_or_hidden_files(self):
        for path in ("/gal/leak.txt", "/out/secret.txt", "/gal/to-hidden.png", "/hosts.txt", "/usr-share/"):
            code, _, body = self.req(path)
            self.assertNotEqual(code, 200, path)
            self.assertNotIn("secret", body, path)
            self.assertNotIn("localhost", body, path)
        self.assertEqual(self.req("/gal/inside.png")[0], 200)

    def test_write_methods_are_refused(self):
        for method in ("PUT", "POST", "DELETE", "PATCH", "MKCOL"):
            self.assertEqual(self.req("/docs/note.txt", method)[0], 405, method)
        self.assertEqual(self.req("/docs/note.txt")[2], "한글")


if __name__ == "__main__":
    unittest.main()
