#!/usr/bin/env python3
import json, os, pathlib, stat, sys, tempfile, unittest
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
        self.assertEqual(pw.parse_lsof(out), {4590: ("127.0.0.1", "Python"), 4591: ("[::1]", "node")})
        self.assertEqual(pw.target(4591, "[::1]"), "http://[::1]:4591")

    def test_ipv4_wins_for_a_dual_bound_port(self):
        out = "p1\ncvite\nf3\nn[::1]:4590\nf4\nn127.0.0.1:4590\n"
        self.assertEqual(pw.parse_lsof(out), {4590: ("127.0.0.1", "vite")})


    def test_reads_this_machines_listeners(self):
        if sys.platform != "darwin":
            self.skipTest("macOS lsof")
        got = pw.listeners()
        self.assertTrue(got)  # any Mac has some loopback listener; empty means the scan itself broke
        self.assertTrue(all(h in ("127.0.0.1", "[::1]") and c for h, c in got.values()), got)


class Decide(Base):
    def test_existing_entry_on_a_new_port_is_foreign(self):
        cand = {4571: ("127.0.0.1", "Python"), 4590: ("127.0.0.1", "Python")}
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
        self.listening = {13004: ("127.0.0.1", "ssh"), 13005: ("127.0.0.1", "ssh"), 3000: ("127.0.0.1", "OrbStack"),
                          4571: ("127.0.0.1", "Python")}

    def run_pass(self, http=lambda p, h: True):
        with mock.patch.object(pw, "listeners", lambda: dict(self.listening)), \
             mock.patch.object(pw, "speaks_http", http), mock.patch.dict(os.environ):
            os.environ.pop("TERM", None)  # launchd starts the job without one
            pw.run_once(self.cfg)

    def live(self):
        return pw.entries(json.loads(pathlib.Path(f"{self.cli}.json").read_text()))

    def calls(self):
        return pathlib.Path(f"{self.cli}.calls").read_text().splitlines()

    def test_open_then_close_only_its_own(self):
        self.run_pass()
        self.assertEqual(self.calls(), ["serve status --json"])  # 4571 is someone else's
        self.listening[4590] = ("127.0.0.1", "Python")
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

    def test_non_http_listener_is_not_served_and_retried_later(self):
        self.listening[33900] = ("127.0.0.1", "OrbStack Helper")
        self.run_pass(http=lambda p, h: False)
        self.assertNotIn(33900, self.live())
        self.assertGreater(pw.load_state()["skip"][33900], 0)
        self.run_pass()  # inside the retry window: no probe, no serve
        self.assertNotIn(33900, self.live())
        with mock.patch.object(pw.time, "time", lambda: 10 ** 12):
            self.run_pass()
        self.assertIn(33900, self.live())

    def test_corrupt_state_turns_nothing_off(self):
        self.listening[4590] = ("127.0.0.1", "Python")
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
        self.listening[4590] = ("127.0.0.1", "Python")
        self.run_pass()
        d = json.loads(pathlib.Path(f"{self.cli}.json").read_text())
        d["Web"][f"{HOST}:4590"]["Handlers"]["/"]["Proxy"] = "http://127.0.0.1:8000"
        pathlib.Path(f"{self.cli}.json").write_text(json.dumps(d))
        del self.listening[4590]
        self.run_pass()
        self.assertEqual(self.live()[4590], "http://127.0.0.1:8000")
        self.assertEqual(pw.load_state()["owned"], {})

    def test_empty_scan_turns_nothing_off(self):
        self.listening[4590] = ("127.0.0.1", "Python")
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
        self.listening[4590] = ("127.0.0.1", "Python")
        self.run_pass()
        for m in ("empty", "garbage"):
            self.mode(m)
            self.listening[4591] = ("127.0.0.1", "Python")
            del self.listening[4590]
            with self.assertRaises(Exception):
                self.run_pass()
            self.listening[4590] = ("127.0.0.1", "Python")
        self.mode("")
        self.assertEqual(self.calls().count("serve --bg --https=4591 http://127.0.0.1:4591"), 0)
        self.assertIn(4590, pw.load_state()["owned"])  # not forgotten while status was unreadable

    def test_port_taken_during_probe_is_not_overwritten(self):
        self.listening[4590] = ("127.0.0.1", "Python")
        def probe(p, h):
            self.edit_live(lambda d: (d["TCP"].update({"4590": {"HTTPS": True}}),
                                      d["Web"].update({f"{HOST}:4590": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8000"}}}})))
            return True
        self.run_pass(http=probe)
        self.assertEqual(self.live()[4590], "http://127.0.0.1:8000")
        self.assertNotIn(4590, pw.load_state()["owned"])

    def test_off_failure_is_kept_for_retry(self):
        self.listening[4590] = ("127.0.0.1", "Python")
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

    def test_never_calls_funnel(self):
        self.listening[4590] = ("127.0.0.1", "Python")
        self.run_pass()
        self.assertFalse([c for c in self.calls() if "funnel" in c])


if __name__ == "__main__":
    unittest.main()
