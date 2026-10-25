#!/usr/bin/env python3
import http.client, http.server, os, pathlib, socket, subprocess, sys, tempfile, threading, time, unittest
from urllib.parse import quote
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import share_server as ss  # noqa: E402


class Groups(unittest.TestCase):
    def test_variants_share_a_group(self):
        names = ["b-dialog-390.png", "a-x.png", "b-dialog-1280-dark.png", "b-dialog-1280.png", "shot@2x.webp"]
        self.assertEqual(ss.groups(names), [("a-x", ["a-x.png"]),
                                            ("b-dialog", ["b-dialog-1280-dark.png", "b-dialog-1280.png",
                                                          "b-dialog-390.png"]),
                                            ("shot", ["shot@2x.webp"])])

    def test_numbered_names_keep_their_number(self):
        self.assertEqual(ss.group_key("01-console-overview.png"), "01-console-overview")
        self.assertEqual(ss.group_key("1280.png"), "1280")


class Markdown(unittest.TestCase):
    def test_blocks(self):
        out = ss.md_html("# 제목\n\n- [x] 끝\n- 둘\n  - 셋\n\n1. **a**\n\n   이어서\n2. b\n\n> 인용\n\n```py\nx < 1\n```\n\n---")
        self.assertIn("<h1>제목</h1>", out)
        self.assertIn('<li><input type="checkbox" disabled checked> 끝</li><li>둘\n<ul><li>셋</li></ul></li>', out)
        self.assertIn("<ol><li><p><strong>a</strong></p>\n<p>이어서</p></li><li><p>b</p></li></ol>", out)
        self.assertIn("<blockquote><p>인용</p></blockquote>", out)
        self.assertIn('<pre><code class="language-py">x &lt; 1</code></pre>', out)
        self.assertTrue(out.endswith("<hr>"))

    def test_table_keeps_pipes_in_code_and_escapes(self):
        out = ss.md_html("| a | b |\n|:--|--:|\n| `x|y` | c \\| d |\n| 1 |")
        self.assertIn('<th style="text-align:left">a</th><th style="text-align:right">b</th>', out)
        self.assertIn("<td style=\"text-align:left\"><code>x|y</code></td><td style=\"text-align:right\">c | d</td>",
                      out)
        self.assertIn("<td style=\"text-align:left\">1</td><td style=\"text-align:right\"></td>", out)

    def test_inline(self):
        self.assertEqual(ss.inline("**굵게** *기울임* ~~취소~~ `a<b` \\`안 코드\\` snake_case_name"),
                         "<strong>굵게</strong> <em>기울임</em> <del>취소</del> <code>a&lt;b</code> `안 코드` "
                         "snake_case_name")
        self.assertEqual(ss.inline("[`x`](https://e.test/a_(b)) https://e.test/q?a=1&b=2."),
                         '<a href="https://e.test/a_(b)"><code>x</code></a> '
                         '<a href="https://e.test/q?a=1&amp;b=2">https://e.test/q?a=1&amp;b=2</a>.')
        self.assertEqual(ss.inline("a  \nb\\\nc"), "a  <br>\nb<br>\nc")

    def test_link_text_is_formatted_but_not_autolinked(self):
        self.assertEqual(ss.inline("[**https://e.test/a** <b>](https://e.test/a)"),
                         '<a href="https://e.test/a"><strong>https://e.test/a</strong> &lt;b&gt;</a>')

    def test_headings_tables_and_rules_need_their_full_shape(self):
        self.assertEqual(ss.md_html("## 제목 ##"), "<h2>제목</h2>")
        self.assertEqual(ss.md_html("# C#"), "<h1>C#</h1>")
        self.assertEqual(ss.md_html("#태그"), "<p>#태그</p>")
        self.assertEqual(ss.md_html("a | b\n---"), "<p>a | b</p>\n<hr>")

    def test_adversarial_input_stays_fast_and_shallow(self):
        cases = [" **a" * 20000, " ~~a" * 20000, " *a" * 20000, "# a" + " " * 20000 + "b",
                 "a|b\n" + " " * 20000 + "x", "![" * 20000, "[" * 20000, "a" + " " * 20000 + "b",
                 ">" * 3000 + " x", "".join("  " * k + "- x\n" for k in range(1500))]
        # About 1 MB of paragraphs each under MAX_INLINE, so every inline pattern actually runs.
        for unit in (" **a", " ~~a", " *a", " _a", "![", "[", "[a](", "<https://a", "`a", "a  "):
            para = unit * (ss.MAX_INLINE // len(unit) - 1)
            cases.append("\n\n".join([para] * (ss.MAX_SOURCE // len(para) - 1)))
        for text in cases:
            started = time.monotonic()
            out = ss.md_html(text)
            self.assertLess(time.monotonic() - started, 1, text[:20])
            self.assertNotIn("<script", out)
        self.assertTrue(ss.md_html("x" * (ss.MAX_SOURCE + 1)).startswith("<pre>"))
        self.assertLessEqual(len(cases[-1]), ss.MAX_SOURCE)

    def test_raw_html_and_unsafe_urls_are_inert(self):
        out = ss.md_html('<script>alert(1)</script> [x](javascript:alert(1)) ![i](JavaScript:x) [r](../a.png)')
        self.assertNotIn("<script", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertIn('<a href="#">x</a>', out)
        self.assertIn('<img src="#" alt="i">', out)
        self.assertIn('<a href="../a.png">r</a>', out)


class Server(unittest.TestCase):
    REPORT = "# 보고서\n\n| 항목 | 결과 |\n|---|---|\n| 설치 | **VERIFIED** |\n\n<script>alert(1)</script>\n"

    @classmethod
    def setUpClass(cls):
        quiet = mock.patch.object(ss.Handler, "log_message")
        quiet.start()
        cls.addClassCleanup(quiet.stop)
        cls.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(cls.tmp.name)
        root, outside = base / "share", base / "outside"
        (root / "gal").mkdir(parents=True)
        (root / "plain").mkdir()
        (root / "site").mkdir()
        outside.mkdir()
        (outside / "secret.txt").write_text("secret")
        (outside / "leak.png").write_bytes(b"\x89PNG")
        for n in ("x-1280.png", "x-390.png", "x-1280-dark.png", "y.jpg"):
            (root / "gal" / n).write_bytes(b"\x89PNG")
        (root / "gal" / '<img src=x onerror=alert(1)>.png').write_bytes(b"\x89PNG")
        (root / "gal" / "notes.txt").write_text("hi")
        (root / "gal" / ".hidden.png").write_bytes(b"\x89PNG")
        (root / "gal" / "leak.png").symlink_to(outside / "leak.png")
        (root / "gal" / "inside.png").symlink_to(root / "gal" / "y.jpg")
        (root / "out").symlink_to(outside)
        (outside / "page.html").write_text("outside page")
        (root / "idx-out").mkdir()
        (root / "idx-out" / "index.html").symlink_to(outside / "page.html")
        (root / ".private").mkdir()
        (root / ".private" / "page.html").write_text("hidden page")
        (root / "idx-hidden").mkdir()
        (root / "idx-hidden" / "index.html").symlink_to(root / ".private" / "page.html")
        (root / "gal" / "to-hidden.png").symlink_to(root / "gal" / ".hidden.png")
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text("x")
        (root / "plain" / "report.md").write_text(cls.REPORT, encoding="utf-8")
        for n in ("a.txt", "a.json", "a.csv", "a.log"):
            (root / "plain" / n).write_text("한글", encoding="utf-8")
        (root / ".private" / "notes.md").write_text("hidden")
        (root / "plain" / "a.tar.gz").write_bytes(b"\x1f\x8b")
        (root / "site" / "index.html").write_text("<!doctype html><title>own page</title>")
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: ss.Handler(*a, root=str(root)))
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def get(self, path):
        return self.fetch(path)[:2]

    def fetch(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", path)
        r = c.getresponse()
        return r.status, r.read().decode(errors="replace"), r.headers

    def raw(self, path):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
            s.sendall(f"GET {path} HTTP/1.0\r\n\r\n".encode())
            return s.recv(65536).split(b"\r\n", 1)[0].decode()

    def test_gallery_groups_and_escapes(self):
        code, body = self.get("/gal/")
        self.assertEqual(code, 200)
        self.assertIn('class="grid"', body)
        self.assertIn("<h2>x <small>3</small></h2>", body)
        self.assertLess(body.index("x-1280-dark.png"), body.index("x-1280.png"))
        self.assertLess(body.index("x-1280.png"), body.index("x-390.png"))
        self.assertNotIn("<img src=x onerror", body)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;.png", body)
        self.assertIn('href="notes.txt"', body)
        self.assertIn('id="lb"', body)

    def test_hidden_and_escaping_symlinks_are_neither_listed_nor_served(self):
        _, body = self.get("/gal/")
        self.assertNotIn(".hidden.png", body)
        self.assertNotIn("leak.png", body)
        self.assertIn("inside.png", body)
        _, top = self.get("/")
        self.assertNotIn(".git", top)
        self.assertNotIn('"out/"', top)
        for path in ("/gal/.hidden.png", "/gal/leak.png", "/out/secret.txt", "/out/", "/.git/config",
                     "/%2egit/config"):
            self.assertEqual(self.get(path)[0], 404, path)
        self.assertEqual(self.get("/gal/inside.png")[0], 200)
        self.assertNotIn("to-hidden.png", body)
        self.assertEqual(self.get("/gal/to-hidden.png")[0], 404)

    def test_folder_index_symlinked_outside_or_hidden_is_refused(self):
        for path in ("/idx-out/", "/idx-hidden/"):
            code, body = self.get(path)
            self.assertEqual(code, 404, path)
            self.assertNotIn(" page", body, path)

    def test_path_traversal_is_refused(self):
        for path in ("/../outside/secret.txt", "/gal/../../outside/secret.txt", "/%2e%2e/outside/secret.txt",
                     "/gal/%2e%2e/%2e%2e/outside/secret.txt", "/..%2foutside/secret.txt"):
            line = self.raw(path)
            self.assertNotIn(" 200 ", line, path)

    def test_folder_without_images_is_a_plain_list(self):
        code, body = self.get("/plain/")
        self.assertEqual(code, 200)
        self.assertIn('href="report.md"', body)
        self.assertNotIn('class="grid"', body)
        self.assertNotIn('id="lb"', body)

    def test_markdown_is_rendered_as_utf8_html(self):
        code, body, headers = self.fetch("/plain/report.md")
        self.assertEqual(code, 200)
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        self.assertNotIn("script-src", headers["Content-Security-Policy"])
        self.assertIn("<h1>보고서</h1>", body)
        self.assertIn("<td>설치</td><td><strong>VERIFIED</strong></td>", body)
        self.assertNotIn("<script>", body)
        self.assertIn('href="?raw"', body)

    def test_raw_markdown_is_the_source(self):
        for query in ("raw", "raw=1", "x&raw"):
            code, body, headers = self.fetch(f"/plain/report.md?{query}")
            self.assertEqual((code, body), (200, self.REPORT), query)
            self.assertEqual(headers["Content-Type"], "text/markdown; charset=utf-8")

    def test_text_files_declare_utf8(self):
        for name, kind in (("a.txt", "text/plain"), ("a.json", "application/json"), ("a.csv", "text/csv"),
                           ("a.log", "text/plain")):
            code, body, headers = self.fetch(f"/plain/{name}")
            self.assertEqual((code, body), (200, "한글"), name)
            self.assertEqual(headers["Content-Type"], f"{kind}; charset=utf-8", name)
        self.assertEqual(self.fetch("/site/")[2]["Content-Type"], "text/html")
        self.assertEqual(self.fetch("/gal/y.jpg")[2]["Content-Type"], "image/jpeg")
        self.assertEqual(self.fetch("/plain/a.tar.gz")[2]["Content-Type"], "application/gzip")

    def test_oversized_markdown_is_sent_as_source(self):
        with mock.patch.object(ss, "MAX_SOURCE", 10):
            code, body, headers = self.fetch("/plain/report.md")
        self.assertEqual((code, body), (200, self.REPORT))
        self.assertEqual(headers["Content-Type"], "text/markdown; charset=utf-8")

    def test_hidden_markdown_is_refused(self):
        self.assertEqual(self.get("/.private/notes.md")[0], 404)
        self.assertEqual(self.get("/%2eprivate/notes.md?raw")[0], 404)

    def test_index_html_wins(self):
        code, body = self.get("/site/")
        self.assertEqual(code, 200)
        self.assertIn("own page", body)

    def test_files_are_served(self):
        self.assertEqual(self.get("/gal/y.jpg"), (200, "�PNG"))


class Launchd(unittest.TestCase):
    def test_spec_binds_configured_root_and_port(self):
        with mock.patch.object(ss, "config", return_value={**ss.DEFAULTS, "root": "~/s", "label": "a.b",
                                                           "backend": "python"}):
            s = ss.spec()
        self.assertEqual(s["Label"], "a.b")
        args = s["ProgramArguments"]
        self.assertEqual(args[args.index("--root") + 1], os.path.expanduser("~/s"))
        self.assertEqual(args[args.index("--port") + 1], "13201")
        self.assertEqual(args[args.index("--backend") + 1], "python")
        self.assertTrue(s["KeepAlive"])

    def test_caddy_spec_resolves_caddy_at_each_start(self):
        with mock.patch.object(ss, "config", return_value={**ss.DEFAULTS, "root": "~/s"}):
            args = ss.spec()["ProgramArguments"]
        self.assertEqual(args[args.index("--backend") + 1], "caddy")
        self.assertIn("serve", args)

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

    def test_unknown_backend_is_refused(self):
        with mock.patch("builtins.print"):
            self.assertEqual(ss.main(["x", "serve", "--backend", "Caddy"]), 2)
            with mock.patch.object(ss.sys, "platform", "darwin"), \
                    mock.patch.object(ss, "config", return_value={**ss.DEFAULTS, "backend": "nginx"}), \
                    mock.patch.object(ss, "plist_path", return_value=pathlib.Path("/nonexistent/a.plist")), \
                    mock.patch.object(ss.subprocess, "run") as run:
                self.assertEqual(ss.cmd_install(True), 1)
        run.assert_not_called()

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
                    mock.patch.object(ss, "config", return_value={**ss.DEFAULTS, "label": "a.b",
                                                                  "backend": "python"}), \
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
    """The real Caddyfile and sandbox profile, run through `share_server.py serve --backend caddy`."""

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
                                     "--port", str(cls.port), "--backend", "caddy"], env=env,
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
