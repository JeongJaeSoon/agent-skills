#!/usr/bin/env python3
import http.server, json, os, pathlib, socket, stat, sys, tempfile, threading, time, unittest
from unittest import mock

TMP = pathlib.Path(tempfile.mkdtemp())
os.environ["PREVIEW_WATCH_STATE"] = str(TMP / "state")
os.environ["AGENT_SKILLS_CONFIG"] = str(TMP / "absent.json")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import preview_watch as pw  # noqa: E402

HOST = "box.example.test"
# Stands in for the tailscale CLI: keeps the serve config in a JSON file and logs every call.
FAKE_CLI = f'''#!{sys.executable}
import json, os, pathlib, sys
if not os.environ.get("TERM"):  # what the Tailscale.app binary does under launchd
    print("The Tailscale GUI failed to start"); sys.exit(0)
cfg, calls = pathlib.Path(sys.argv[0] + ".json"), pathlib.Path(sys.argv[0] + ".calls")
mode = pathlib.Path(sys.argv[0] + ".mode")
mode = mode.read_text().strip() if mode.exists() else ""
d = json.loads(cfg.read_text())
a = sys.argv[1:]
with calls.open("a") as f: f.write(" ".join(a) + "\\n")
if a[:3] == ["serve", "status", "--json"]:
    print({{"empty": "", "garbage": "[]"}}.get(mode, json.dumps(d))); sys.exit(0)
port = next(x.split("=")[1] for x in a if x.startswith("--https="))
if a[-1] == "off" and mode == "fail-off":
    sys.exit(1)
if a[-1] == "off":
    d["TCP"].pop(port, None); d["Web"].pop("{HOST}:" + port, None)
else:
    d["TCP"][port] = {{"HTTPS": True}}
    d["Web"]["{HOST}:" + port] = {{"Handlers": {{"/": {{"Proxy": a[-1]}}}}}}
cfg.write_text(json.dumps(d))
'''


def status_of(entries):
    return {"TCP": {str(p): {"HTTPS": True} for p in entries},
            "Web": {f"{HOST}:{p}": {"Handlers": {"/": {"Proxy": t}}} for p, t in entries.items()}}


class Base(unittest.TestCase):
    def setUp(self):
        for f in (pw.STATE, *pw.STATE_DIR.glob("preview-watch.json.corrupt-*")):
            f.unlink(missing_ok=True)


class Ranges(Base):
    R = pw.DEFAULTS["ranges"]

    def test_hand_started_range_any_command(self):
        self.assertTrue(pw.allowed(4590, "Python", self.R))
        self.assertTrue(pw.allowed(4500, "node", self.R))
        self.assertFalse(pw.allowed(4600, "Python", self.R))
        self.assertFalse(pw.allowed(4499, "Python", self.R))

    def test_docker_range_only_for_the_daemon(self):
        self.assertTrue(pw.allowed(33898, "OrbStack Helper", self.R))
        self.assertTrue(pw.allowed(50000, "com.docker.backend", self.R))
        self.assertFalse(pw.allowed(39127, "1Password", self.R))
        self.assertFalse(pw.allowed(50913, "Google Chrome", self.R))

    def test_user_ports_outside_every_range(self):
        for p in (443, 3000, 13004, 13005):
            self.assertFalse(pw.allowed(p, "ssh", self.R), p)

    def test_parse_lsof_loopback_only(self):
        out = "p1\ncPython\nf3\nn127.0.0.1:4590\np2\ncnode\nf4\nn[::1]:4591\nf5\nn*:4592\nn10.0.0.1:4593\n"
        self.assertEqual(pw.parse_lsof(out), {4590: ("127.0.0.1", "Python", 1), 4591: ("[::1]", "node", 2)})
        self.assertEqual(pw.target(4591, "[::1]"), "http://[::1]:4591")

    def test_ipv4_wins_for_a_dual_bound_port(self):
        out = "p1\ncvite\nf3\nn[::1]:4590\nf4\nn127.0.0.1:4590\n"
        self.assertEqual(pw.parse_lsof(out), {4590: ("127.0.0.1", "vite", 1)})


    def test_reads_this_machines_listeners(self):
        if sys.platform != "darwin":
            self.skipTest("macOS lsof")
        got = pw.listeners()
        self.assertTrue(got)  # any Mac has some loopback listener; empty means the scan itself broke
        self.assertTrue(all(h in ("127.0.0.1", "[::1]") and c and pid > 0 for h, c, pid in got.values()), got)


ROUTES = {
    "/": (200, "text/html; charset=utf-8", b"<!doctype html><html><body>app</body></html>"),
    "/api": (200, "application/json", b'{"status": "ok"}'),
    "/plain": (200, "text/plain", b""),
    "/fake-html": (200, "text/html", b'{"not": "a page"}'),
    "/missing": (404, "text/html", b"<html><body>404</body></html>"),
    "/page": (200, "text/html", b"<html lang=en><body>app</body></html>"),
    "/to-page": (302, "", b""),
    "/elsewhere": (302, "", b""),
    "/loop": (302, "", b""),
    "/bad-location": (302, "", b""),
    "/ui": (302, "", b""),
    "/ui/": (302, "", b""),
    "/ui/index.html": (200, "text/html", b"<html><body>ui</body></html>"),
    "/q": (302, "", b""),
    "/q?x=1": (200, "text/html", b"<html><body>q</body></html>"),
}
LOCATIONS = {"/to-page": "/page", "/elsewhere": "https://example.test/", "/loop": "/loop",
             "/bad-location": "http://127.0.0.1:invalid/", "/ui": "/ui/", "/ui/": "index.html", "/q": "?x=1"}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        # The server stands in for an app whose "/" is root: "/?x=1" is root + "?x=1".
        path = self.server.root + self.path[1:] if self.path.startswith(("/?", "/#")) or self.path == "/" else self.path
        code, ctype, body = ROUTES[path]
        self.send_response(code)
        if ctype:
            self.send_header("Content-Type", ctype)
        if path in LOCATIONS:
            self.send_header("Location", LOCATIONS[path])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class Probe(unittest.TestCase):
    """probe_page against real loopback servers."""

    def serve(self, root):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        srv.root = root
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv.server_address[1]

    def verdict(self, root):
        return pw.probe_page(self.serve(root), "127.0.0.1")

    def test_html_page(self):
        self.assertIs(self.verdict("/"), True)

    def test_same_origin_redirect_to_a_page(self):
        for root in ("/to-page", "/ui", "/q"):  # absolute path, nested relative path, query only
            self.assertIs(self.verdict(root), True, root)

    def test_not_pages(self):
        for root in ("/api", "/plain", "/fake-html", "/missing", "/elsewhere", "/loop", "/bad-location"):
            self.assertIs(self.verdict(root), False, root)

    def test_non_http_listener_is_not_a_page(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        self.addCleanup(sock.close)
        def greet():  # a database-style server: speaks first, never HTTP
            c, _ = sock.accept()
            c.sendall(b"\x00\x00\x00\x08garbage\r\n")
            c.close()
        threading.Thread(target=greet, daemon=True).start()
        self.assertIs(pw.probe_page(sock.getsockname()[1], "127.0.0.1"), False)

    def raw_server(self, talk):
        """A loopback listener that runs talk(conn) for its first connection; returns the port."""
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        self.addCleanup(sock.close)
        stop = threading.Event()
        self.addCleanup(stop.set)
        def serve():
            c, _ = sock.accept()
            c.recv(4096)
            try:
                talk(c, stop)
            except OSError:
                pass
            finally:
                c.close()
        threading.Thread(target=serve, daemon=True).start()
        return sock.getsockname()[1]

    def test_streaming_page_is_seen_before_the_stream_ends(self):
        def talk(c, stop):
            c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nTransfer-Encoding: chunked\r\n\r\n"
                      b"17\r\n<!doctype html><html>hi\r\n")
            stop.wait(30)  # keeps the response open, like a server-sent stream
        t = time.monotonic()
        self.assertIs(pw.probe_page(self.raw_server(talk), "127.0.0.1"), True)
        self.assertLess(time.monotonic() - t, 1)

    def test_trickling_server_cannot_hold_the_pass(self):
        def talk(c, stop):
            c.sendall(b"HTTP/1.1 200 OK\r\n")
            while not stop.wait(0.2):
                c.sendall(b"X-Slow: 1\r\n")  # each read returns within the socket timeout
        t = time.monotonic()
        self.assertIs(pw.probe_page(self.raw_server(talk), "127.0.0.1"), False)
        self.assertLess(time.monotonic() - t, pw.PROBE_BUDGET_S + 1)

    def test_refused_connection_is_unknown(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        self.assertIsNone(pw.probe_page(port, "127.0.0.1"))


class Decide(Base):
    def test_existing_entry_on_a_new_port_is_foreign(self):
        cand = {4571: ("127.0.0.1", "Python", 1), 4590: ("127.0.0.1", "Python", 1)}
        opens, closes, forget, foreign = pw.decide(cand, [4571, 4590], [], {}, {4571: "http://127.0.0.1:4571"})
        self.assertEqual(opens, [(4590, "http://127.0.0.1:4590")])
        self.assertEqual(foreign, [4571])

    def test_replaced_entry_is_forgotten_not_turned_off(self):
        owned = {4590: "http://127.0.0.1:4590", 4591: "http://127.0.0.1:4591"}
        live = {4590: "http://127.0.0.1:4590", 4591: "http://127.0.0.1:9999"}
        _, closes, forget, _ = pw.decide({}, [], [4590, 4591], owned, live)
        self.assertEqual((closes, forget), ([4590], [4591]))


class EndToEnd(Base):
    USER = {13004: "http://127.0.0.1:13004", 13005: "http://127.0.0.1:13005", 443: "http://127.0.0.1:3000",
            4571: "http://127.0.0.1:4571"}

    def setUp(self):
        super().setUp()
        self.cli = TMP / "tailscale"
        self.cli.write_text(FAKE_CLI)
        self.cli.chmod(self.cli.stat().st_mode | stat.S_IXUSR)
        pathlib.Path(f"{self.cli}.json").write_text(json.dumps(status_of(self.USER)))
        pathlib.Path(f"{self.cli}.calls").write_text("")
        pathlib.Path(f"{self.cli}.mode").unlink(missing_ok=True)
        self.cfg = {**pw.DEFAULTS, "tailscale": str(self.cli)}
        self.listening = {13004: ("127.0.0.1", "ssh", 1), 13005: ("127.0.0.1", "ssh", 1), 3000: ("127.0.0.1", "OrbStack", 1),
                          4571: ("127.0.0.1", "Python", 1)}

    def run_pass(self, http=lambda p, h: True):
        with mock.patch.object(pw, "listeners", lambda: dict(self.listening)), \
             mock.patch.object(pw, "probe_page", http), mock.patch.dict(os.environ):
            os.environ.pop("TERM", None)  # launchd starts the job without one
            pw.run_once(self.cfg)

    def live(self):
        return pw.entries(json.loads(pathlib.Path(f"{self.cli}.json").read_text()))

    def calls(self):
        return pathlib.Path(f"{self.cli}.calls").read_text().splitlines()

    def test_open_then_close_only_its_own(self):
        self.run_pass()
        self.assertEqual(self.calls(), ["serve status --json"])  # 4571 is someone else's
        self.listening[4590] = ("127.0.0.1", "Python", 1)
        self.run_pass()
        self.assertEqual(self.live()[4590], "http://127.0.0.1:4590")
        del self.listening[4590], self.listening[4571]
        self.run_pass()
        live = self.live()
        self.assertNotIn(4590, live)
        for p, t in self.USER.items():
            self.assertEqual(live[p], t, p)  # 4571 went quiet but was never this watcher's
        self.assertEqual([c for c in self.calls() if c.endswith("off")], ["serve --https=4590 off"])

    def test_quiet_pass_calls_no_tailscale(self):
        self.run_pass()
        n = len(self.calls())
        self.run_pass()
        self.run_pass()
        self.assertEqual(len(self.calls()), n)

    def test_refused_probe_is_retried_later(self):
        self.listening[33900] = ("127.0.0.1", "OrbStack Helper", 1)
        self.run_pass(http=lambda p, h: None)
        self.assertNotIn(33900, self.live())
        self.assertGreater(pw.load_state()["skip"][33900], 0)
        self.run_pass()  # inside the retry window: no probe, no serve
        self.assertNotIn(33900, self.live())
        with mock.patch.object(pw.time, "time", lambda: 10 ** 12):
            self.run_pass()
        self.assertIn(33900, self.live())

    def counting(self, verdicts):
        probes = []
        def probe(p, h):
            if p not in self.USER:  # the fixture's foreign 4571 is probed once on first sight
                probes.append(p)
            return verdicts.get(p, True)
        return probes, probe

    def test_api_is_not_served_and_not_probed_every_pass(self):
        self.run_pass()
        pathlib.Path(f"{self.cli}.calls").write_text("")
        self.listening[33896] = ("127.0.0.1", "OrbStack Helper", 7)
        probes, probe = self.counting({33896: False})
        self.run_pass(http=probe)
        self.run_pass(http=probe)
        self.run_pass(http=probe)
        self.assertNotIn(33896, self.live())
        self.assertEqual(probes, [33896])
        self.assertEqual(self.calls(), [])  # no page appeared: tailscale is never asked
        with mock.patch.object(pw.time, "time", lambda: 10 ** 12):  # past the recheck window
            self.run_pass(http=probe)
        self.assertEqual(probes, [33896, 33896])

    def test_new_process_on_the_port_is_probed_again(self):
        self.listening[4590] = ("127.0.0.1", "Python", 7)
        probes, probe = self.counting({4590: False})
        self.run_pass(http=probe)
        self.listening[4590] = ("127.0.0.1", "vite", 8)
        probes2, probe2 = self.counting({})
        self.run_pass(http=probe2)
        self.assertEqual((probes, probes2), ([4590], [4590]))
        self.assertEqual(self.live()[4590], "http://127.0.0.1:4590")

    def owned_before_upgrade(self, *ports):
        """Entries a pre-page-check watcher opened: owned, with no verdict in the state."""
        for p in ports:
            self.listening[p] = ("127.0.0.1", "OrbStack Helper", 7)
        self.edit_live(lambda d: [(d["TCP"].update({str(p): {"HTTPS": True}}),
                                   d["Web"].update({f"{HOST}:{p}": {"Handlers": {"/": {"Proxy": pw.target(p, "127.0.0.1")}}}}))
                                  for p in ports])
        pw.save_state({"owned": {p: pw.target(p, "127.0.0.1") for p in ports}, "skip": {}, "pages": {}})

    def test_owned_api_from_before_is_closed_and_pages_stay(self):
        self.owned_before_upgrade(33896, 33897)
        probes, probe = self.counting({33896: False, 33897: True})
        self.run_pass(http=probe)
        live = self.live()
        self.assertNotIn(33896, live)
        self.assertEqual(live[33897], "http://127.0.0.1:33897")
        for p, t in self.USER.items():
            self.assertEqual(live[p], t, p)
        self.assertEqual([c for c in self.calls() if c.endswith("off")], ["serve --https=33896 off"])
        self.assertEqual(sorted(probes), [33896, 33897])
        self.run_pass(http=probe)
        self.assertEqual(sorted(probes), [33896, 33897])  # verdicts cached

    def test_excluded_port_is_closed_and_never_probed(self):
        self.listening[4570] = ("127.0.0.1", "Python", 7)
        probes, probe = self.counting({})
        self.run_pass(http=probe)
        self.assertNotIn(4570, self.live())
        self.owned_before_upgrade(4570)
        self.run_pass(http=probe)
        self.assertNotIn(4570, self.live())
        self.assertEqual(probes, [])
        self.assertIn("close https :4570 (out of scope)", pw.LOG.read_text())

    def test_included_port_is_served_without_a_probe(self):
        self.cfg["include"] = [8080]
        self.listening[8080] = ("127.0.0.1", "java", 7)
        probes, probe = self.counting({8080: False})
        self.run_pass(http=probe)
        self.assertEqual(self.live()[8080], "http://127.0.0.1:8080")
        self.assertEqual(probes, [])

    def test_include_overrides_a_cached_not_page_verdict(self):
        self.listening[4590] = ("127.0.0.1", "java", 7)
        probes, probe = self.counting({4590: False})
        self.run_pass(http=probe)
        self.assertNotIn(4590, self.live())
        self.cfg["include"] = [4590]
        self.run_pass(http=probe)
        self.assertEqual(self.live()[4590], "http://127.0.0.1:4590")
        st = pw.load_state()
        st["pages"][4590] = [7, False, pw.time.time()]
        pw.save_state(st)
        self.run_pass(http=probe)
        self.assertIn(4590, self.live())  # an owned include is not taken down by a stale verdict either

    def test_page_verdict_is_rechecked_for_a_reused_port(self):
        self.listening[33897] = ("127.0.0.1", "OrbStack Helper", 7)
        probes, probe = self.counting({})
        self.run_pass(http=probe)
        self.assertIn(33897, self.live())
        probes2, probe2 = self.counting({33897: False})  # another container took the port under the same pid
        self.run_pass(http=probe2)
        self.assertIn(33897, self.live())
        self.assertEqual(probes2, [])  # cached inside the window
        with mock.patch.object(pw.time, "time", lambda: 10 ** 12):
            self.run_pass(http=probe2)
        self.assertNotIn(33897, self.live())
        self.assertIn("close https :33897 (not a web page)", pw.LOG.read_text())

    def test_foreign_entries_untouched_even_when_excluded(self):
        self.cfg["exclude"] = [13004, 13005, 3000, 4571]
        self.run_pass()
        for p, t in self.USER.items():
            self.assertEqual(self.live()[p], t, p)
        self.assertFalse([c for c in self.calls() if not c.startswith("serve status")])

    def test_corrupt_state_turns_nothing_off(self):
        self.listening[4590] = ("127.0.0.1", "Python", 1)
        self.run_pass()
        pw.STATE.write_text("{not json")
        del self.listening[4590]
        self.run_pass()
        self.assertEqual(self.live()[4590], "http://127.0.0.1:4590")  # not provably ours any more: left on
        self.assertTrue(list(pw.STATE_DIR.glob("preview-watch.json.corrupt-*")))
        self.assertEqual(pw.load_state()["owned"], {})

    def test_wrong_shape_state_is_corrupt_too(self):
        pw.STATE_DIR.mkdir(parents=True, exist_ok=True)
        pw.STATE.write_text(json.dumps({"owned": ["4590"]}))
        self.assertEqual(pw.load_state()["owned"], {})
        self.assertTrue(list(pw.STATE_DIR.glob("preview-watch.json.corrupt-*")))

    def test_entry_replaced_by_someone_is_left_alone(self):
        self.listening[4590] = ("127.0.0.1", "Python", 1)
        self.run_pass()
        d = json.loads(pathlib.Path(f"{self.cli}.json").read_text())
        d["Web"][f"{HOST}:4590"]["Handlers"]["/"]["Proxy"] = "http://127.0.0.1:8000"
        pathlib.Path(f"{self.cli}.json").write_text(json.dumps(d))
        del self.listening[4590]
        self.run_pass()
        self.assertEqual(self.live()[4590], "http://127.0.0.1:8000")
        self.assertEqual(pw.load_state()["owned"], {})

    def test_empty_scan_turns_nothing_off(self):
        self.listening[4590] = ("127.0.0.1", "Python", 1)
        self.run_pass()
        self.listening = {}
        with self.assertRaises(RuntimeError):
            self.run_pass()
        self.assertEqual(self.live()[4590], "http://127.0.0.1:4590")
        self.assertIn(4590, pw.load_state()["owned"])

    def mode(self, m):
        pathlib.Path(f"{self.cli}.mode").write_text(m)

    def edit_live(self, fn):
        p = pathlib.Path(f"{self.cli}.json")
        d = json.loads(p.read_text())
        fn(d)
        p.write_text(json.dumps(d))

    def test_empty_or_garbage_status_takes_nothing(self):
        self.listening[4590] = ("127.0.0.1", "Python", 1)
        self.run_pass()
        for m in ("empty", "garbage"):
            self.mode(m)
            self.listening[4591] = ("127.0.0.1", "Python", 1)
            del self.listening[4590]
            with self.assertRaises(Exception):
                self.run_pass()
            self.listening[4590] = ("127.0.0.1", "Python", 1)
        self.mode("")
        self.assertEqual(self.calls().count("serve --bg --https=4591 http://127.0.0.1:4591"), 0)
        self.assertIn(4590, pw.load_state()["owned"])  # not forgotten while status was unreadable

    def test_port_taken_during_probe_is_not_overwritten(self):
        self.listening[4590] = ("127.0.0.1", "Python", 1)
        def probe(p, h):
            self.edit_live(lambda d: (d["TCP"].update({"4590": {"HTTPS": True}}),
                                      d["Web"].update({f"{HOST}:4590": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8000"}}}})))
            return True
        self.run_pass(http=probe)
        self.assertEqual(self.live()[4590], "http://127.0.0.1:8000")
        self.assertNotIn(4590, pw.load_state()["owned"])

    def test_off_failure_is_kept_for_retry(self):
        self.listening[4590] = ("127.0.0.1", "Python", 1)
        self.run_pass()
        self.mode("fail-off")
        with mock.patch.dict(os.environ):
            os.environ.pop("TERM", None)
            self.assertEqual(pw.close_owned(str(self.cli)), [4590])
        self.assertIn(4590, pw.load_state()["owned"])
        self.mode("")
        self.assertEqual(pw.close_owned(str(self.cli)), [])
        self.assertNotIn(4590, self.live())
        self.assertIn(13004, self.live())

    def test_repeated_failure_logs_once(self):
        pw.LOG.unlink(missing_ok=True)
        for _ in range(3):
            pw.log("pass failed: X", once=True)
        pw.log("open  https :4590", once=True)
        pw.log("pass failed: X", once=True)
        self.assertEqual(len(pw.LOG.read_text().splitlines()), 3)

    def test_log_tail_survives_a_split_multibyte_character(self):
        pw.LOG.unlink(missing_ok=True)
        # "<timestamp> " is 20 bytes, so 1024 bytes back from the end lands on the second byte of "가".
        pw.log("가" + "x" * 1021)
        pw.log("pass failed: Y", once=True)
        pw.log("pass failed: Y", once=True)
        self.assertEqual(len(pw.LOG.read_text().splitlines()), 2)

    def test_timed_out_open_that_applied_is_still_owned(self):
        self.listening[4590] = ("127.0.0.1", "Python", 1)
        real = pw.ts
        def slow(cli, *args):
            r = real(cli, *args)
            if "--bg" in args:
                raise pw.subprocess.TimeoutExpired(args, 20)
            return r
        with mock.patch.object(pw, "ts", slow), self.assertRaises(pw.subprocess.TimeoutExpired):
            self.run_pass()
        self.assertEqual(pw.load_state()["owned"].get(4590), "http://127.0.0.1:4590")

    def test_never_calls_funnel(self):
        self.listening[4590] = ("127.0.0.1", "Python", 1)
        self.run_pass()
        self.assertFalse([c for c in self.calls() if "funnel" in c])


if __name__ == "__main__":
    unittest.main()
