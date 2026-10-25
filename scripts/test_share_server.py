#!/usr/bin/env python3
import http.client, http.server, os, pathlib, socket, sys, tempfile, threading, unittest
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


class Server(unittest.TestCase):
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
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text("x")
        (root / "plain" / "report.md").write_text("r")
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
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", path)
        r = c.getresponse()
        return r.status, r.read().decode(errors="replace")

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

    def test_index_html_wins(self):
        code, body = self.get("/site/")
        self.assertEqual(code, 200)
        self.assertIn("own page", body)

    def test_files_are_served(self):
        self.assertEqual(self.get("/gal/y.jpg"), (200, "�PNG"))


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


if __name__ == "__main__":
    unittest.main()
