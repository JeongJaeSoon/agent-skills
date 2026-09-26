#!/usr/bin/env python3
import io
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import secure_fill as sf  # noqa: E402

SECRET = 'tok-"quoted"\\back\nline-s3cr3t'
ORIGIN = "https://dev.example.test"
ALLOW = {"items": {"dev-token": {"origins": [ORIGIN, "http://127.0.0.1:8765"], "env": "SF_TEST_TOKEN"}}}


class FakeRun:
    """Records every child process; answers like the real tools would."""

    def __init__(self, url=ORIGIN + "/login", js_result="ok", helper_rc=0, keychain=SECRET):
        self.calls, self.url, self.js_result, self.helper_rc, self.keychain = [], url, js_result, helper_rc, keychain

    def __call__(self, argv, stdin=None, timeout=120):
        self.calls.append((list(argv), stdin))
        out, rc = "", 0
        if argv[0] == "osascript":
            if "execute active tab" in stdin:
                if self.js_result is None:
                    rc = 1
                else:
                    out = self.js_result
            else:
                out = self.url + "\n"
        elif argv[0] == "security":
            out, rc = (self.keychain + "\n", 0) if self.keychain else ("", 44)
        elif argv[0] == "orca":
            if "url" in argv:
                out = json.dumps({"ok": True, "result": {"value": self.url}})
            else:
                out = json.dumps({"ok": True, "result": {"value": True}})
        elif argv[0] == "xcrun":
            pathlib.Path(argv[argv.index("-o") + 1]).write_text("")
        elif argv[1:2] == ["paste"]:
            rc = self.helper_rc
        return subprocess.CompletedProcess(argv, rc, out, "")

    def argv_text(self):
        return "\n".join(" ".join(a) for a, _ in self.calls)


class Origins(unittest.TestCase):
    def test_normalize(self):
        cases = {
            "https://Dev.Example.Test/login?x=1#f": "https://dev.example.test",
            "https://dev.example.test:443/": "https://dev.example.test",
            "http://127.0.0.1:8765/index.html": "http://127.0.0.1:8765",
            "http://localhost:80": "http://localhost",
            "https://dev.example.test:8443": "https://dev.example.test:8443",
            "https://[::1]:9000/": "https://[::1]:9000",
        }
        for url, want in cases.items():
            self.assertEqual(sf.normalize_origin(url), want, url)

    def test_rejects(self):
        for url in ["https://dev.example.test@evil.example/", "https://u:p@dev.example.test",
                    "file:///etc/passwd", "javascript:alert(1)", "chrome://settings", "", "https://",
                    "https://dev.example.test:99999"]:
            self.assertIsNone(sf.normalize_origin(url), url)

    def test_scheme_and_port_matter(self):
        self.assertNotEqual(sf.normalize_origin("http://dev.example.test"), ORIGIN)
        self.assertNotEqual(sf.normalize_origin("https://dev.example.test:8443"), ORIGIN)
        self.assertNotEqual(sf.normalize_origin("https://dev.example.test.evil.example"), ORIGIN)

    def test_origin_arg_has_no_path(self):
        self.assertEqual(sf.parse_origin_arg("https://dev.example.test/"), ORIGIN)
        self.assertIsNone(sf.parse_origin_arg("https://dev.example.test/login"))
        self.assertIsNone(sf.parse_origin_arg("https://dev.example.test?x"))


class Allowlist(unittest.TestCase):
    def test_parse(self):
        items = sf.load_allowlist(json.dumps(ALLOW))
        entry = items["dev-token"]
        self.assertEqual(entry["origins"], {ORIGIN, "http://127.0.0.1:8765"})
        self.assertEqual(entry["keychain"], {"service": "secure-fill", "account": "dev-token", "path": None})
        self.assertEqual(entry["env"], "SF_TEST_TOKEN")

    def test_errors(self):
        bad = ["not json", "[]", '{"items": []}', '{"items": {"a b": {"origins": ["https://x.test"]}}}',
               '{"items": {"a": {"origins": []}}}', '{"items": {"a": {"origins": ["https://x.test/path"]}}}',
               '{"items": {"a": {"origins": ["https://x.test"], "env": 1}}}',
               '{"items": {"a": {"origins": ["https://x.test"], "keychain": {"service": 2}}}}']
        for text in bad:
            with self.assertRaises(sf.Outcome, msg=text) as cm:
                sf.load_allowlist(text)
            self.assertEqual(cm.exception.result, "config-error")


class Flow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = pathlib.Path(self.tmp.name)
        self.config, self.log = d / "allow.json", d / "state" / "log.jsonl"
        self.config.write_text(json.dumps(ALLOW))
        sf.CACHE = d / "cache"
        self.gate_calls, self.source_calls = [], []

    def tearDown(self):
        self.tmp.cleanup()

    def gate(self, answer):
        def approve(reason):
            self.gate_calls.append(reason)
            return answer
        return approve

    def source(self, entry):
        self.source_calls.append(entry)
        return SECRET

    def go(self, run, approve=True, argv=None, sources=None):
        out = io.StringIO()
        argv = argv or ["--item", "dev-token", "--origin", ORIGIN, "--target", "chrome"]
        rc = sf.main(argv, run=run, approve=self.gate(approve), sources=sources or [self.source],
                     config=self.config, log=self.log, out=out)
        return rc, out.getvalue(), self.log.read_text()

    def assert_no_secret(self, run, *texts):
        for piece in [SECRET, "s3cr3t"]:
            for t in texts:
                self.assertNotIn(piece, t)
            self.assertNotIn(piece, run.argv_text(), "secret reached argv")

    def test_dom_fill(self):
        run = FakeRun()
        rc, out, log = self.go(run)
        self.assertEqual(rc, 0)
        self.assertIn("filled", out)
        self.assertEqual(json.loads(log)["method"], "dom")
        self.assertIn('"dev-token"', self.gate_calls[0])
        self.assertIn(ORIGIN, self.gate_calls[0])
        self.assert_no_secret(run, out, log)
        script = [s for a, s in run.calls if s and "execute" in s][0]
        self.assertIn(ORIGIN, script)

    def test_paste_fallback_keeps_secret_on_stdin(self):
        run = FakeRun(js_result=None)
        rc, out, log = self.go(run)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(log)["method"], "paste")
        paste = [(a, s) for a, s in run.calls if a[1:2] == ["paste"]]
        self.assertEqual(paste[0][1], SECRET)
        self.assert_no_secret(run, out, log)

    def test_denied_never_reads_secret(self):
        run = FakeRun()
        rc, out, log = self.go(run, approve=False)
        self.assertEqual(rc, sf.EXIT["denied"])
        self.assertEqual(self.source_calls, [])
        self.assertEqual(json.loads(log)["result"], "denied")

    def test_origin_mismatch_skips_gate(self):
        run = FakeRun(url="https://dev.example.test.evil.example/login")
        rc, out, _ = self.go(run)
        self.assertEqual(rc, sf.EXIT["origin-mismatch"])
        self.assertEqual(self.gate_calls, [])
        self.assertEqual(self.source_calls, [])

    def test_origin_changed_inside_page(self):
        rc, out, log = self.go(FakeRun(js_result="origin-mismatch"))
        self.assertEqual(rc, sf.EXIT["origin-mismatch"])

    def test_not_in_allowlist(self):
        run = FakeRun(url="https://other.example.test/")
        rc, _, _ = self.go(run, argv=["--item", "dev-token", "--origin", "https://other.example.test",
                                      "--target", "chrome"])
        self.assertEqual(rc, sf.EXIT["not-allowed"])
        self.assertEqual(run.calls, [])
        rc, _, _ = self.go(run, argv=["--item", "nope", "--origin", ORIGIN, "--target", "chrome"])
        self.assertEqual(rc, sf.EXIT["not-allowed"])
        self.assertEqual(self.gate_calls, [])

    def test_not_found(self):
        rc, out, _ = self.go(FakeRun(), sources=[lambda e: None])
        self.assertEqual(rc, sf.EXIT["not-found"])

    def test_keychain_then_env(self):
        run = FakeRun(keychain="")
        entry = sf.load_allowlist(json.dumps(ALLOW))["dev-token"]
        self.assertIsNone(sf.keychain_source(run)(entry))
        self.assertIn(["security", "find-generic-password", "-s", "secure-fill", "-a", "dev-token", "-w"],
                      [a for a, _ in run.calls])
        with unittest.mock.patch.dict("os.environ", {"SF_TEST_TOKEN": SECRET}):
            self.assertEqual(sf.env_source(entry), SECRET)
        self.assertEqual(sf.keychain_source(FakeRun())(entry), SECRET)

    def test_orca_paste_needs_focused_field(self):
        run = FakeRun(url=ORIGIN + "/login")
        rc, out, log = self.go(run, argv=["--item", "dev-token", "--origin", ORIGIN, "--target", "orca"])
        self.assertEqual(rc, 0)
        self.assert_no_secret(run, out, log)
        orca_cmds = [a for a, _ in run.calls if a[0] == "orca"]
        self.assertTrue(any("eval" in a for a in orca_cmds))

    def test_paste_permission_failure_is_safe(self):
        run = FakeRun(js_result=None, helper_rc=3)
        rc, out, log = self.go(run)
        self.assertEqual(rc, sf.EXIT["fill-failed"])
        self.assertIn("Accessibility", out)
        self.assert_no_secret(run, out, log)

    def test_writable_allowlist_warns(self):
        rc, out, log = self.go(FakeRun())
        self.assertIn("allowlist-writable", out)
        self.assertIn("allowlist-writable", log)

    def test_bad_origin_arg(self):
        rc, out, _ = self.go(FakeRun(), argv=["--item", "dev-token", "--origin", "https://x.test/p",
                                              "--target", "chrome"])
        self.assertEqual(rc, sf.EXIT["usage"])

    def test_log_permissions(self):
        self.go(FakeRun())
        self.assertEqual(self.log.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.log.parent.stat().st_mode & 0o777, 0o700)


class Escaping(unittest.TestCase):
    def test_js_literal_survives_applescript(self):
        js = sf.fill_js(ORIGIN, None, SECRET, False)
        self.assertIn(json.dumps(SECRET), js)
        lit = sf.applescript_string(js)
        self.assertTrue(lit.startswith('"') and lit.endswith('"'))
        decoded = re.sub(r'\\(.)', r'\1', lit[1:-1])
        self.assertEqual(decoded, js)
        self.assertNotRegex(re.sub(r'\\.', '', lit[1:-1]), '"')


if __name__ == "__main__":
    unittest.main()
