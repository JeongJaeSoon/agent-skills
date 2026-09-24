#!/usr/bin/env python3
"""Offline tests for tracker.py: python3 test_tracker.py

Linear runs against a fake `orca` placed first on PATH; Jira runs against a local http.server.
Nothing here reaches a real tracker.
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
TRACKER = os.path.join(HERE, "tracker.py")
LINEAR_SAMPLE = os.path.join(HERE, "testdata", "linear_list_issues.json")
sys.path.insert(0, HERE)
import tracker  # noqa: E402

# Canned `orca linear` responses. The list-issues page split exercises cursor paging.
FAKE_ORCA = r'''#!/usr/bin/env python3
import json, os, re, sys
args = sys.argv[1:]
with open(os.environ["FAKE_ORCA_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\n")
sample = json.load(open(os.environ["FAKE_ORCA_SAMPLE"]))["result"]["issues"]
def ok(result):
    print(json.dumps({"id": "x", "ok": True, "result": result})); sys.exit(0)
def fail(code, msg):
    print(json.dumps({"id": "x", "ok": False, "error": {"code": code, "message": msg}})); sys.exit(1)
def val(flag):
    return args[args.index(flag) + 1] if flag in args else None
cmd = args[1] if args[0] == "linear" else None
rest = args[2:]
if cmd == "list-issues" and "--updated-at" in args:
    ok({"issues": [dict(sample[1], identifier="9X-4"), dict(sample[0], identifier="9X-5"),
                   dict(sample[0], identifier="94S-1"), dict(sample[0], identifier="ENG-7")],
        "meta": {"hasMore": False}})
if cmd == "list-issues":
    newest = sorted(sample, key=lambda i: i["createdAt"], reverse=True)
    if val("--cursor") == "c1":
        ok({"issues": newest[2:], "truncated": False, "meta": {"hasMore": False}})
    ok({"issues": newest[:2], "truncated": True, "meta": {"hasMore": True, "nextCursor": "c1"}})
if cmd == "issue":
    ident = rest[0]
    if not re.match(r"^[A-Za-z][A-Za-z0-9_]*-\d+$", ident) or ident == "ENG-7":
        fail("linear_issue_required", "Pass a Linear issue identifier or issue URL.")
    issue = dict(sample[0], identifier=ident)
    ok({"issue": issue})
if cmd == "search":
    if rest[0] == "94S-1":
        ok({"issues": [{"identifier": "94S-1", "title": "t", "team": {"key": "94S"},
                        "updatedAt": "2026-09-21T00:00:00.000Z", "state": sample[0]["state"]}]})
    if rest[0] == "ENG-7":
        ok({"issues": [{"identifier": "ENG-7", "title": "t", "team": {"key": "ENG"},
                        "updatedAt": "2026-09-21T00:00:00.000Z", "state": sample[0]["state"]}]})
    ok({"issues": [{"identifier": "9X-5", "title": "t", "team": {"key": "9X"},
                    "updatedAt": "2026-09-21T00:00:00.000Z", "state": sample[0]["state"]}]})
if cmd == "project":
    ok({"projects": [{"id": "p1", "name": "demo", "teams": [{"key": "ENG", "id": "t1"}]}]})
if cmd == "create":
    ok({"issue": {"identifier": os.environ.get("FAKE_ORCA_NEW_ID", "ENG-99"), "title": val("--title"),
                  "url": "https://linear.app/acme/issue/ENG-99", "state": sample[1]["state"]}})
if cmd == "team":
    ok({"states": [
        {"name": "Backlog", "type": "backlog", "position": 0},
        {"name": "Todo", "type": "unstarted", "position": 1},
        {"name": "In Review", "type": "started", "position": 1002},
        {"name": "In Progress", "type": "started", "position": 2},
        {"name": "Done", "type": "completed", "position": 3},
        {"name": "Canceled", "type": "canceled", "position": 4},
        {"name": "Duplicate", "type": "duplicate", "position": 5}]})
if cmd in ("label", "comment", "status", "relation"):
    ok({"issue": {"identifier": rest[1]}})
fail("unknown", "fake orca: unhandled " + " ".join(args))
'''


class Env:
    """A throwaway HOME with a fake orca first on PATH."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        os.makedirs(os.path.join(self.home, ".claude"))
        bindir = os.path.join(self.tmp.name, "bin")
        os.makedirs(bindir)
        orca = os.path.join(bindir, "orca")
        with open(orca, "w") as fh:
            fh.write(FAKE_ORCA.replace("#!/usr/bin/env python3", "#!" + sys.executable, 1))
        os.chmod(orca, 0o755)
        self.log = os.path.join(self.tmp.name, "orca.log")
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("TRACKER_FIXTURES", "JIRA_EMAIL", "JIRA_API_TOKEN")}
        self.env.update(HOME=self.home, PATH=bindir + os.pathsep + os.environ.get("PATH", ""),
                        FAKE_ORCA_LOG=self.log, FAKE_ORCA_SAMPLE=LINEAR_SAMPLE)

    def file(self, name, content):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content if isinstance(content, str) else json.dumps(content))
        return path

    def config(self, data):
        with open(os.path.join(self.home, ".claude", "agent-skills.json"), "w") as fh:
            json.dump(data, fh)

    def run(self, *argv, **extra_env):
        env = dict(self.env, **extra_env)
        proc = subprocess.run([sys.executable, TRACKER, *argv], capture_output=True, text=True, env=env)
        out = json.loads(proc.stdout) if proc.returncode == 0 else None
        return proc, out

    def orca_calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as fh:
            return [json.loads(line) for line in fh]

    def close(self):
        self.tmp.cleanup()


# ---------------------------------------------------------------- fake Jira

JIRA_EMAIL, JIRA_TOKEN = "bot@example.com", "s3cr3t-token-value"


def jira_issue(key, created, status, category, resolution=None, resolved=None, **extra):
    fields = {"summary": f"Summary {key}", "created": created, "updated": created,
              "status": {"name": status, "statusCategory": {"key": category}},
              "resolution": {"name": resolution} if resolution else None,
              "resolutiondate": resolved, "labels": extra.get("labels", []),
              "parent": {"key": extra["parent"]} if extra.get("parent") else None,
              "assignee": {"displayName": "Bob"}, "priority": {"name": "High"},
              "description": {"type": "doc", "version": 1, "content": [
                  {"type": "paragraph", "content": [{"type": "text", "text": "Line one"},
                                                    {"type": "hardBreak"},
                                                    {"type": "text", "text": "line two"}]},
                  {"type": "heading", "content": [{"type": "text", "text": "Heading"}]}]}}
    return {"key": key, "fields": fields}


JIRA_ISSUES = [
    jira_issue("ABC-1", "2026-09-01T10:00:00.000+0900", "Backlog", "new"),
    jira_issue("ABC-2", "2026-09-02T10:00:00.000+0000", "To Do", "new"),
    jira_issue("ABC-3", "2026-09-03T10:00:00.000+0000", "In Progress", "indeterminate", parent="ABC-1"),
    jira_issue("ABC-4", "2026-09-04T10:00:00.000+0000", "Done", "done", "Done",
               "2026-09-05T12:30:00.000+0000", labels=["follow-up"]),
    jira_issue("ABC-5", "2026-09-05T10:00:00.000+0000", "Done", "done", "Won't Do",
               "2026-09-06T08:00:00.000+0000"),
]


class FakeJira:
    def __init__(self, legacy_only=False):
        self.requests = []
        self.legacy_only = legacy_only
        self.transitions = [
            {"id": "11", "name": "Start", "to": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}}},
            {"id": "21", "name": "Won't do", "to": {"name": "Closed", "statusCategory": {"key": "done"}}},
            {"id": "31", "name": "Resolve", "to": {"name": "Done", "statusCategory": {"key": "done"}}},
        ]
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _reply(self, code, body=None):
                data = json.dumps(body).encode() if body is not None else b""
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                fake.requests.append((self.command, self.path, body))
                want = "Basic " + base64.b64encode(f"{JIRA_EMAIL}:{JIRA_TOKEN}".encode()).decode()
                if self.headers.get("Authorization") != want:
                    return self._reply(401, {"errorMessages": ["unauthorized"]})
                path = self.path.split("?")[0]
                m, p = self.command, path
                if m == "POST" and p == "/rest/api/3/search/jql":
                    if fake.legacy_only:
                        return self._reply(404, {"errorMessages": ["not found"]})
                    ordered = JIRA_ISSUES[::-1] if "DESC" in body["jql"] else JIRA_ISSUES
                    start = int(body.get("nextPageToken") or 0)
                    size = min(2, body["maxResults"])
                    page = ordered[start:start + size]
                    nxt = start + size
                    resp = {"issues": page, "isLast": nxt >= len(ordered)}
                    if nxt < len(ordered):
                        resp["nextPageToken"] = str(nxt)
                    return self._reply(200, resp)
                if m == "POST" and p == "/rest/api/3/search":
                    start = body["startAt"]
                    return self._reply(200, {"issues": JIRA_ISSUES[start:start + 2], "total": len(JIRA_ISSUES)})
                if m == "POST" and p == "/rest/api/3/issue":
                    return self._reply(201, {"id": "1009", "key": "ABC-9"})
                if m == "POST" and p == "/rest/api/3/issueLink":
                    return self._reply(201)
                if p.endswith("/transitions"):
                    if m == "GET":
                        return self._reply(200, {"transitions": fake.transitions})
                    return self._reply(204)
                if m == "POST" and p.endswith("/comment"):
                    return self._reply(201, {"id": "c1"})
                if p.startswith("/rest/api/3/issue/"):
                    key = p.rsplit("/", 1)[1]
                    if m == "PUT":
                        return self._reply(204)
                    for issue in JIRA_ISSUES + [jira_issue("ABC-9", "2026-09-09T00:00:00.000+0000", "To Do", "new")]:
                        if issue["key"] == key:
                            return self._reply(200, issue)
                    return self._reply(404, {"errorMessages": ["Issue does not exist"]})
                return self._reply(404, {"errorMessages": [f"no route {m} {p}"]})

            do_GET = do_POST = do_PUT = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


# ---------------------------------------------------------------- tests

class NormalizeLinear(unittest.TestCase):
    def test_sample_from_real_orca_output(self):
        with open(LINEAR_SAMPLE) as fh:
            raw = json.load(fh)["result"]["issues"]
        issues = {i["id"]: i for i in map(tracker.normalize_linear, raw)}
        done = issues["ENG-10"]
        self.assertEqual(set(done), set(tracker.issue_template()))
        self.assertEqual(done["state_type"], "completed")
        self.assertEqual(done["created_at"], "2026-09-21T08:18:45Z")
        self.assertEqual(done["completed_at"], done["updated_at"])
        self.assertTrue(done["closed_approx"])
        self.assertEqual(done["labels"], ["Spike"])
        self.assertEqual(done["assignee"], "alice")
        self.assertIsNone(done["parent"])
        backlog = issues["ENG-11"]
        self.assertEqual(backlog["state_type"], "backlog")
        self.assertIsNone(backlog["completed_at"])
        self.assertIsNone(backlog["canceled_at"])

    def test_duplicate_and_explicit_fields(self):
        issue = tracker.normalize_linear({
            "identifier": "ENG-1", "state": {"name": "Duplicate", "type": "duplicate"},
            "updatedAt": "2026-01-02T00:00:00.000Z", "canceledAt": "2026-01-01T00:00:00Z",
            "parent": {"identifier": "ENG-0"}, "labels": {"nodes": [{"name": "follow-up"}]}})
        self.assertEqual(issue["state_type"], "canceled")
        self.assertEqual(issue["canceled_at"], "2026-01-01T00:00:00Z")
        self.assertEqual(issue["parent"], "ENG-0")
        self.assertEqual(issue["labels"], ["follow-up"])

    def test_iso_z(self):
        self.assertEqual(tracker.iso_z("2026-09-01T10:00:00.123+0900"), "2026-09-01T01:00:00Z")
        self.assertEqual(tracker.iso_z("2026-09-01T10:00:00Z"), "2026-09-01T10:00:00Z")
        self.assertIsNone(tracker.iso_z(None))


class LinearCli(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.close()

    def test_list_pages_with_filters_repeated(self):
        proc, out = self.env.run("list", "--project", "demo")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(out), 3)
        created = [i["created_at"] for i in out]
        self.assertEqual(created, sorted(created))
        calls = [c for c in self.env.orca_calls() if c[1] == "list-issues"]
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertIn("--include-archived", call)
            self.assertEqual(call[call.index("--project") + 1], "demo")
        self.assertIn("--cursor", calls[1])

    def test_children(self):
        proc, out = self.env.run("children", "94S-110")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        call = self.env.orca_calls()[0]
        self.assertEqual(call[call.index("--parent-id") + 1], "94S-110")  # identifiers `issue` rejects work here
        self.assertNotIn("--project", call)
        self.env.run("children", "--project", "demo")
        call = self.env.orca_calls()[-1]
        self.assertEqual((call[call.index("--parent-id") + 1], call[call.index("--project") + 1]), ("null", "demo"))
        proc, _ = self.env.run("children")
        self.assertEqual(proc.returncode, 1)

    def test_list_since_and_limit(self):
        proc, out = self.env.run("list", "--project", "demo", "--since", "2026-01-01T00:00:00Z", "--limit", "2")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(out), 2)
        call = self.env.orca_calls()[0]
        self.assertEqual(call[call.index("--created-at") + 1], "2026-01-01T00:00:00Z")
        self.assertEqual(call[call.index("--limit") + 1], "2")

    def test_get_and_digit_team_fallback(self):
        proc, out = self.env.run("get", "ENG-10")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out["id"], "ENG-10")
        proc, out = self.env.run("get", "9X-5")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((out["id"], out["labels"], out["state_type"]), ("9X-5", ["Spike"], "completed"))
        calls = self.env.orca_calls()
        self.assertEqual([c[1] for c in calls], ["issue", "search", "list-issues"])
        self.assertEqual(calls[2][calls[2].index("--team") + 1], "9X")
        proc, out = self.env.run("get", "94S-1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((out["id"], out["created_at"]), ("94S-1", "2026-09-21T08:18:45Z"))
        # A letter-led id that Orca still answers with linear_issue_required also falls back.
        proc, out = self.env.run("get", "ENG-7")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out["id"], "ENG-7")
        self.assertEqual([c[1] for c in self.env.orca_calls()[-3:]], ["issue", "search", "list-issues"])
        proc, _ = self.env.run("get", "9X-6")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not found", proc.stderr)

    def test_create_label_comment_transition(self):
        body = self.env.file("body.md", "파생: ENG-10 · 원인: 누락\n\ndetails")
        proc, out = self.env.run("create", "--project", "demo", "--title", "Follow-up", "--body-file", body,
                                 "--label", "follow-up", "--parent", "ENG-10", "--related", "ENG-10")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out["id"], "ENG-99")
        calls = self.env.orca_calls()
        create = next(c for c in calls if c[1] == "create")
        for flag, value in (("--team", "ENG"), ("--label", "follow-up"), ("--parent", "ENG-10"),
                            ("--body-file", body), ("--project", "demo")):
            self.assertEqual(create[create.index(flag) + 1], value)
        relation = next(c for c in calls if c[1] == "relation")
        self.assertEqual(relation[2:8], ["add", "ENG-99", "--related", "ENG-10", "--type", "related"])

        proc, out = self.env.run("label", "ENG-10", "--add", "follow-up", "--add", "bug")
        self.assertEqual(out, {"ok": True, "op": "label", "id": "ENG-10", "added": ["follow-up", "bug"]})
        self.assertEqual(self.env.orca_calls()[-1][:8],
                         ["linear", "label", "add", "ENG-10", "--label", "follow-up", "--label", "bug"])

        proc, out = self.env.run("comment", "ENG-10", "--body-file", body)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.env.orca_calls()[-1][:5], ["linear", "comment", "add", "ENG-10", "--body-file"])

        for target, name in (("started", "In Progress"), ("completed", "Done"), ("canceled", "Canceled")):
            proc, out = self.env.run("transition", "ENG-10", "--to", target)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(out["state"], name)
            self.assertEqual(self.env.orca_calls()[-1][:6], ["linear", "status", "set", "ENG-10", "--to", name])

    def test_writes_refuse_ids_orca_cannot_parse(self):
        proc, _ = self.env.run("label", "9X-5", "--add", "bug")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("MCP", proc.stderr)
        self.assertEqual(self.env.orca_calls(), [])

    def test_create_relation_failure_still_reports_new_id(self):
        body = self.env.file("body.md", "x")
        proc, _ = self.env.run("create", "--project", "demo", "--title", "t", "--body-file", body,
                               "--related", "ENG-1", FAKE_ORCA_NEW_ID="9X-7")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["id"], "9X-7")
        self.assertIn("created 9X-7", proc.stderr)

    def test_missing_body_file(self):
        proc, _ = self.env.run("comment", "ENG-1", "--body-file", "/nonexistent/body.md")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("cannot read body file", proc.stderr)
        self.assertEqual(self.env.orca_calls(), [])


class JiraCli(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.jira = FakeJira()
        self.env.config({"tracker": {"adapter": "jira", "jira": {"base_url": self.jira.url}}})
        self.env.env.update(JIRA_EMAIL=JIRA_EMAIL, JIRA_API_TOKEN=JIRA_TOKEN)

    def tearDown(self):
        self.jira.close()
        self.env.close()

    def test_list_maps_states_and_pages(self):
        proc, out = self.env.run("list", "--project", "ABC")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        by = {i["id"]: i for i in out}
        self.assertEqual([i["id"] for i in out], ["ABC-1", "ABC-2", "ABC-3", "ABC-4", "ABC-5"])
        self.assertEqual(by["ABC-1"]["state_type"], "backlog")
        self.assertEqual(by["ABC-1"]["created_at"], "2026-09-01T01:00:00Z")
        self.assertEqual(by["ABC-2"]["state_type"], "unstarted")
        self.assertEqual(by["ABC-3"]["state_type"], "started")
        self.assertEqual(by["ABC-3"]["parent"], "ABC-1")
        self.assertEqual(by["ABC-4"]["state_type"], "completed")
        self.assertEqual(by["ABC-4"]["completed_at"], "2026-09-05T12:30:00Z")
        self.assertEqual(by["ABC-4"]["labels"], ["follow-up"])
        self.assertEqual(by["ABC-5"]["state_type"], "canceled")
        self.assertEqual(by["ABC-5"]["canceled_at"], "2026-09-06T08:00:00Z")
        self.assertIsNone(by["ABC-5"]["completed_at"])
        self.assertEqual(by["ABC-3"]["description"], "Line one\nline two\nHeading")
        self.assertEqual(by["ABC-3"]["url"], f"{self.jira.url}/browse/ABC-3")
        self.assertEqual(by["ABC-3"]["priority"], 2)
        searches = [r for r in self.jira.requests if r[1] == "/rest/api/3/search/jql"]
        self.assertEqual(len(searches), 3)
        self.assertEqual(searches[0][2]["jql"], 'project = "ABC" ORDER BY created ASC')
        self.assertEqual(searches[1][2]["nextPageToken"], "2")

    def test_children_jql(self):
        self.env.run("children", "ABC-1")
        self.env.run("children", "--project", "ABC")
        jqls = [r[2]["jql"] for r in self.jira.requests if r[1] == "/rest/api/3/search/jql"]
        self.assertEqual(jqls[0], 'parent = "ABC-1" ORDER BY created ASC')
        self.assertIn('project = "ABC" AND parent is EMPTY', jqls[-1])

    def test_list_since_limit_and_legacy_fallback(self):
        proc, out = self.env.run("list", "--project", "ABC", "--since", "2026-09-02T00:00:00Z", "--limit", "2")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual([i["id"] for i in out], ["ABC-4", "ABC-5"])
        jql = self.jira.requests[0][2]["jql"]
        self.assertIn('created >= "2026-09-02 00:00"', jql)
        self.jira.legacy_only = True
        proc, out = self.env.run("list", "--project", "ABC")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(out), 5)
        self.assertTrue(any(r[1] == "/rest/api/3/search" for r in self.jira.requests))

    def test_writes(self):
        body = self.env.file("body.md", "파생: ABC-1 · 원인: 누락\n\nsecond para")
        proc, out = self.env.run("create", "--project", "ABC", "--title", "Follow-up", "--body-file", body,
                                 "--label", "follow-up", "--parent", "ABC-1", "--related", "ABC-1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out["id"], "ABC-9")
        create = next(r for r in self.jira.requests if r[:2] == ("POST", "/rest/api/3/issue"))[2]["fields"]
        self.assertEqual(create["project"], {"key": "ABC"})
        self.assertEqual(create["labels"], ["follow-up"])
        self.assertEqual(create["parent"], {"key": "ABC-1"})
        self.assertEqual(create["issuetype"], {"name": "Task"})
        self.assertEqual(len(create["description"]["content"]), 2)
        link = next(r for r in self.jira.requests if r[1] == "/rest/api/3/issueLink")[2]
        self.assertEqual(link, {"type": {"name": "Relates"}, "inwardIssue": {"key": "ABC-9"},
                                "outwardIssue": {"key": "ABC-1"}})

        proc, out = self.env.run("label", "ABC-2", "--add", "follow-up")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.jira.requests[-1], ("PUT", "/rest/api/3/issue/ABC-2",
                                                  {"update": {"labels": [{"add": "follow-up"}]}}))

        proc, out = self.env.run("comment", "ABC-2", "--body-file", body)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.jira.requests[-1][2]["body"]["type"], "doc")

        for target, tid in (("started", "11"), ("completed", "31"), ("canceled", "21")):
            proc, out = self.env.run("transition", "ABC-2", "--to", target)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(self.jira.requests[-1][2], {"transition": {"id": tid}})

        self.jira.transitions = self.jira.transitions[:1]
        proc, _ = self.env.run("transition", "ABC-2", "--to", "completed")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no transition leads to 'completed'", proc.stderr)

    def test_bad_token_error_never_leaks_secret(self):
        proc, _ = self.env.run("get", "ABC-1", JIRA_API_TOKEN="wrong-token-zzz")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("HTTP 401", proc.stderr)
        for secret in ("wrong-token-zzz", JIRA_TOKEN):
            self.assertNotIn(secret, proc.stderr + proc.stdout)

    def test_missing_env_and_base_url(self):
        env = {k: v for k, v in self.env.env.items() if k != "JIRA_API_TOKEN"}
        proc = subprocess.run([sys.executable, TRACKER, "get", "ABC-1"], capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("JIRA_API_TOKEN", proc.stderr)
        self.env.config({"tracker": {"adapter": "jira"}})
        proc, _ = self.env.run("get", "ABC-1")
        self.assertIn("base_url is not set", proc.stderr)


class ConfigAndFixtures(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.close()

    def test_defaults_without_config_file_use_linear(self):
        # Env's HOME has no agent-skills.json, so this exercises the built-in defaults.
        proc, out = self.env.run("list", "--project", "demo")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(self.env.orca_calls())

    def test_precedence(self):
        self.env.config({"tracker": {"adapter": "jira", "jira": {"base_url": "http://127.0.0.1:9"}}})
        proc, _ = self.env.run("list", "--project", "demo")
        self.assertIn("JIRA_EMAIL", proc.stderr)
        proc, out = self.env.run("--adapter", "linear", "list", "--project", "demo")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        explicit = self.env.file("explicit.json", {"tracker": {"adapter": "linear"}})
        proc, out = self.env.run("--config", explicit, "list", "--project", "demo")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc, _ = self.env.run("--config", "/nonexistent/agent-skills.json", "list", "--project", "demo")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("config file not found", proc.stderr)

    def test_merge_keeps_defaults(self):
        path = self.env.file("partial.json", {"tracker": {"jira": {"base_url": "https://x.atlassian.net"}}})
        cfg = tracker.load_config(path)
        self.assertEqual(cfg["tracker"]["adapter"], "linear")
        self.assertEqual(cfg["tracker"]["jira"]["token_env"], "JIRA_API_TOKEN")
        self.assertEqual(cfg["tracker"]["jira"]["base_url"], "https://x.atlassian.net")

    def test_fixture_mode(self):
        fixtures = os.path.join(self.env.tmp.name, "fx")
        os.makedirs(fixtures)
        issues = [dict(tracker.issue_template(), id=f"F-{n}", created_at=f"2026-09-0{n}T00:00:00Z")
                  for n in (3, 1, 2)]
        with open(os.path.join(fixtures, "issues.json"), "w") as fh:
            json.dump(issues, fh)
        proc, out = self.env.run("list", "--project", "any", TRACKER_FIXTURES=fixtures)
        self.assertEqual([i["id"] for i in out], ["F-1", "F-2", "F-3"])
        proc, out = self.env.run("list", "--project", "any", "--since", "2026-09-02T00:00:00Z", "--limit", "1",
                                 TRACKER_FIXTURES=fixtures)
        self.assertEqual([i["id"] for i in out], ["F-3"])
        proc, out = self.env.run("get", "F-2", TRACKER_FIXTURES=fixtures)
        self.assertEqual(out["id"], "F-2")
        proc, out = self.env.run("children", "--project", "any", TRACKER_FIXTURES=fixtures)
        self.assertEqual([i["id"] for i in out], ["F-1", "F-2", "F-3"])
        proc, _ = self.env.run("label", "F-2", "--add", "x", TRACKER_FIXTURES=fixtures)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("write operations are disabled", proc.stderr)
        self.assertEqual(self.env.orca_calls(), [])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=0, stream=sys.stderr).run(suite)
    if not result.wasSuccessful():
        sys.exit(1)
    print(f"test_tracker: all {result.testsRun} tests passed")
