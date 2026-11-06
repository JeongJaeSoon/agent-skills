"""The land executor (land_exec.py) and `orch land`'s request path against a fake gh and orca. Run: python3 test_land_exec.py"""
import ast, contextlib, io, json, os, pathlib, shutil, subprocess, sys, tempfile, unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="land-exec-test-"))
BIN = ROOT / "bin"
os.environ.pop("CLAUDECODE", None)
os.environ.update(PROGRAMS_HOME=str(ROOT / "programs"), ORCH_FLEET_STATE=str(ROOT / "fleet"),
                  AGENT_SKILLS_STATE=str(ROOT / "state"), AGENT_SKILLS_CONFIG=str(ROOT / "agent-skills.json"),
                  ORCH_DASH="off", AGENT_SKILLS_PATROL="off",AGENT_SKILLS_LEDGER=str(ROOT / "reap-ledger.jsonl"),
                  PATH=f"{BIN}:{os.environ['PATH']}")
os.environ.pop("ORCA_TERMINAL_HANDLE", None)

# test_land.py's fakes cover every call `orch land` makes; this wrapper adds the executor's own.
_land_src = ast.parse((HERE / "test_land.py").read_text())
FAKES = {t.id: ast.literal_eval(n.value) for n in _land_src.body if isinstance(n, ast.Assign)
         for t in n.targets if isinstance(t, ast.Name) and t.id in ("FAKE_GH", "FAKE_ORCA")}
WRAP_GH = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
st = json.load(open(os.environ["FAKE_GH_STATE"]))
if a[:2] == ["api", "user"]:
    if st.get("user_fail"):
        sys.exit("Get \"https://api.github.com/user\": net/http: TLS handshake timeout")
    print(st.get("login", "o")); sys.exit()
if a[:1] == ["api"] and "/commits/" in a[1]:
    print("basesha"); sys.exit()
if a[:1] == ["api"] and a[1].startswith("repos/") and a[1].count("/") == 2:
    if st.get("repo_fail"):
        sys.exit("HTTP 502: Bad Gateway")
    name = a[1][len("repos/"):]
    m = st.get("repos", {}).get(name.lower(), {"nameWithOwner": name})
    print(json.dumps({"full_name": m["nameWithOwner"], "fork": m.get("isFork", False),
                      "archived": m.get("isArchived", False)}))
    sys.exit()
if a[:2] == ["search", "prs"]:
    print(json.dumps([{"repository": {"nameWithOwner": r}} for r in st.get("search", [])])); sys.exit()
if a[:2] == ["repo", "list"]:
    if st.get("list_fail"):
        sys.exit("HTTP 502: Bad Gateway")
    print(json.dumps([{"nameWithOwner": r} for r in st.get("owned", {}).get(a[2], [])])); sys.exit()
if a[:2] == ["pr", "view"] and a[-1] == "comments":
    print(json.dumps({"comments": [{"body": b} for b in st["prs"][a[2]].get("comments", [])]})); sys.exit()
os.execv(sys.executable, [sys.executable, os.environ["FAKE_GH_INNER"], *a])
'''
WRAP_ORCA = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
if a[:2] == ["orchestration", "send"]:
    path = os.environ["FAKE_GH_STATE"]; st = json.load(open(path))
    st["sent"] = st.get("sent", []) + [a[a.index("--to") + 1]]; json.dump(st, open(path, "w")); sys.exit()
if a[:2] == ["worktree", "list"]:  # a card holds each PR's branch (b<n>): orch verdict asks its terminals by id
    st = json.load(open(os.environ["FAKE_GH_STATE"]))
    print(json.dumps({"worktrees": [{"id": "w" + n, "hostId": "local", "branch": f"refs/heads/b{n}", "head": "",
                                     "isMainWorktree": False} for n in st["prs"]],
                      "hostScope": {"hostIds": ["local"], "omittedHostIds": []}, "truncated": False})); sys.exit()
if a[:2] == ["terminal", "list"]:  # the card holding the PR's branch: orch verdict records it as the author
    print(json.dumps({"terminals": [{"handle": "term_author"}], "truncated": False,
                      "hostScope": {"hostIds": ["local"], "omittedHostIds": []}})); sys.exit()
os.execv(sys.executable, [sys.executable, os.environ["FAKE_ORCA_INNER"], *a])
'''
BIN.mkdir(parents=True)
for name, text in (("gh", WRAP_GH), ("orca", WRAP_ORCA), ("gh-inner", FAKES["FAKE_GH"]), ("orca-inner", FAKES["FAKE_ORCA"])):
    (BIN / name).write_text(text)
    (BIN / name).chmod(0o755)
os.environ.update(FAKE_GH_INNER=str(BIN / "gh-inner"), FAKE_ORCA_INNER=str(BIN / "orca-inner"))

sys.path.insert(0, str(HERE))
import autoqueue  # noqa: E402
import prog  # noqa: E402
import store  # noqa: E402
import land_exec  # noqa: E402

RESULTS = "<!-- test-results -->\nsuite 10/10"
assert "created" in store.init_empty()
assert store.land_setup() == "created land_request"


def diff(n):
    return f"diff --git a/f{n} b/f{n}\n--- a/f{n}\n+++ b/f{n}\n@@ -1 +1 @@\n-a\n+b{n}\n"


def pid(n):
    return subprocess.run(["git", "patch-id", "--verbatim"], input=diff(n), capture_output=True, text=True).stdout.split()[0]


def pr(n, mss="CLEAN", conclusion="SUCCESS", comments=(RESULTS,)):
    return {"state": "OPEN", "isDraft": False, "headRefOid": f"h{n}", "mergeStateStatus": mss, "baseRefName": "main",
            "statusCheckRollup": [{"name": "check", "status": "COMPLETED", "conclusion": conclusion,
                                   "detailsUrl": f"https://github.com/o/r/actions/runs/{n}00/job/1"}],
            "diff": diff(n), "files": [f"src/f{n}.py"], "behind": 0, "comments": list(comments)}


def drain():
    """Close what earlier tests left open, so a round sees only this test's request."""
    for r in store.land_rows(store.LAND_OPEN):
        if r["status"] == "queued":
            store.land_claim(r["id"])
        store.land_finish(r["id"], "refused", 1, "test teardown")


def inbox(rid):
    path = pathlib.Path(os.environ["ORCH_FLEET_STATE"]) / "inbox-external.jsonl"
    rows = [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []
    return [r for r in rows if r.get("key") == f"land-exec:{rid}"]


def config(**over):
    cfg = {"enabled": True, "repos": ["o/r"], "verdict_sources": ["review:codex"], **over}
    pathlib.Path(os.environ["AGENT_SKILLS_CONFIG"]).write_text(json.dumps({"orchestrate": {"land_executor": cfg}}))


class Fixture(unittest.TestCase):
    """One program on o/r with one open PR; the verdict is the test's to record."""
    seq = 0

    def setUp(self):
        drain()
        Fixture.seq += 1
        self.slug = f"p{self.seq}"
        self.n = self.seq
        self.state = ROOT / f"gh-{self.slug}.json"
        self.write({"prs": {str(self.n): pr(self.n)}, "stacks": []})
        os.environ["FAKE_GH_STATE"] = str(self.state)
        config()
        subprocess.run([sys.executable, HERE / "prog.py", "init", self.slug, "--repo", "o/r", "--run", "run_x"],
                       check=True, capture_output=True)
        self.p = prog.Program(self.slug)

    def write(self, st):
        self.state.write_text(json.dumps(st))

    def gh(self):
        return json.loads(self.state.read_text())

    def edit_pr(self, **fields):
        st = self.gh()
        st["prs"][str(self.n)].update(fields)
        self.write(st)

    def verdict(self, sha=None, source="review:codex", result="pass", **who):
        self.p.append("ready", pr=self.n, ticket=f"T-{self.n}")
        ident = {"author": ["term_author"], "reviewer": "codex", "evidence": "https://example.test/review/1",
                 "recorder": "term_orchestrator", **who}
        self.p.append("verdict", pr=self.n, sha=sha or f"h{self.n}", patch_id=pid(self.n), source=source, result=result,
                      **ident)

    def request(self, sha=None):
        row, outcome = store.land_put(self.slug, str(prog.HOME), "o/r", self.n, sha or f"h{self.n}", "basesha", "normal",
                                      "term_req")
        return row

    def round(self):
        self.assertEqual(land_exec.cmd_round([]), 0)

    def merges(self):
        return [a for a in self.gh().get("log", []) if a[:2] == ["pr", "merge"]]

    def settled(self, row, status, code=None, why=None):
        got = store.land_get(row["id"])
        self.assertEqual((got["status"], got["code"]), (status, code), got)
        if why:
            self.assertIn(why, got["reason"])
        if status != "merged":
            self.assertEqual(self.merges(), [], "a refused or waiting request must not merge")
        return got


class LandExec(Fixture):
    def setUp(self):
        super().setUp()
        self.verdict()

    # -- merges

    def test_merges_at_the_requested_sha_and_tells_once(self):
        row = self.request()
        self.round()
        got = self.settled(row, "merged", 0)
        self.assertEqual(self.merges()[0][self.merges()[0].index("--match-head-commit") + 1], f"h{self.n}")
        self.assertEqual(got["merge_sha"], f"m{self.n}")
        self.assertEqual(got["audit"]["verdict"]["source"], "review:codex")
        landed = [e for e in self.p.events() if e["ev"] == "landed"]
        self.assertEqual(len(landed), 1)
        self.assertEqual(self.gh().get("sent", []), [], "a precheck has no sender terminal: no Orca mail")
        self.assertEqual(inbox(row["id"]), [], "a merge is in the ledger, not the inbox")
        self.round()  # nothing new is merged
        self.assertEqual(len(self.merges()), 1)

    def test_a_refusal_goes_to_the_inbox_once(self):
        row = self.request()
        self.edit_pr(headRefOid=f"h{self.n}b")
        self.round()
        self.round()
        rows = inbox(row["id"])
        self.assertEqual(len(rows), 1, rows)
        self.assertIn(f"o/r#{self.n} (exit 3)", rows[0]["title"])
        self.assertEqual(rows[0]["url"], f"https://github.com/o/r/pull/{self.n}")
        self.assertIsNotNone(store.land_get(row["id"])["told_at"])

    def test_a_failed_inbox_write_is_retried_next_round(self):
        import fleet
        row = self.request()
        self.edit_pr(headRefOid=f"h{self.n}b")
        real = fleet.inbox_add
        fleet.inbox_add = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
        try:
            self.round()
        finally:
            fleet.inbox_add = real
        self.assertIsNone(store.land_get(row["id"])["told_at"])
        self.round()
        self.assertEqual(len(inbox(row["id"])), 1)

    def test_recovers_a_merge_a_dead_round_left_running(self):
        row = self.request()
        self.assertTrue(store.land_claim(row["id"]))  # the round died after claiming
        self.edit_pr(state="MERGED", merge_sha=f"m{self.n}")
        self.round()
        self.assertEqual(self.settled(row, "merged", 0)["merge_sha"], f"m{self.n}")
        self.assertEqual(len([e for e in self.p.events() if e["ev"] == "landed"]), 1)

    # -- refusals, one condition each

    def test_head_moved_since_request(self):
        row = self.request()
        self.edit_pr(headRefOid=f"h{self.n}b")
        self.round()
        self.settled(row, "refused", 3, "head moved")

    def test_push_between_the_checks_and_the_merge(self):
        row = self.request()
        real = prog.pr_view

        def view(repo, n):
            if sys._getframe(1).f_code.co_name == "merge_unit":  # every check passed; the push lands now
                self.edit_pr(headRefOid=f"h{self.n}b")
            return real(repo, n)
        prog.pr_view = view
        try:
            self.round()
        finally:
            prog.pr_view = real
        self.settled(row, "refused", 3, "head moved")

    def test_verdict_at_another_sha_with_the_same_patch(self):
        self.verdict(sha="hother")
        row = self.request()
        self.round()
        self.settled(row, "refused", 3, "latest verdict")

    def test_verdict_source_not_allowed(self):
        self.verdict(source="review:claude")
        row = self.request()
        self.round()
        self.settled(row, "refused", 3, "review:claude")

    def test_verdict_naming_the_author_as_reviewer_is_refused(self):
        self.verdict(reviewer="term_author")
        row = self.request()
        self.round()
        self.settled(row, "refused", 3, "author session")
        self.assertEqual(self.merges(), [])

    def test_verdict_without_identity_fields_is_refused(self):
        for gap in ({"author": []}, {"reviewer": None}, {"evidence": None}, {"recorder": None}):
            with self.subTest(gap=gap):
                Fixture.seq += 1
                self.n = self.seq  # a fresh PR per gap, so each round sees one request
                self.write({"prs": {str(self.n): pr(self.n)}, "stacks": []})
                self.p = prog.Program(self.slug)
                self.verdict(**gap)
                row = self.request()
                self.round()
                self.settled(row, "refused", 3, "independent review")
                self.assertEqual(self.merges(), [])

    def test_an_orchestrator_may_record_a_review_someone_else_ran(self):
        self.verdict(recorder="term_orchestrator", reviewer="codex")
        row = self.request()
        self.round()
        self.settled(row, "merged", 0)

    def test_cmd_verdict_records_four_roles_and_refuses_self_review(self):
        os.environ["ORCA_TERMINAL_HANDLE"] = "term_orchestrator"
        real = prog.author_sessions
        prog.author_sessions = lambda branch: ["term_author"]
        base = [self.slug, "--pr", str(self.n), "--sha", f"h{self.n}"]
        try:
            for extra in (["--source", "review:term_author", "--evidence", "e"], ["--source", "independent-review",
                          "--reviewer", "self", "--evidence", "e"]):
                with self.assertRaises(SystemExit) as ctx:
                    prog.cmd_verdict([*base, *extra])
                self.assertIn("author session", str(ctx.exception.code))
            prog.cmd_verdict([*base, "--source", "review:codex", "--evidence", "https://example.test/r"])
        finally:
            prog.author_sessions = real
            os.environ.pop("ORCA_TERMINAL_HANDLE")
        v = prog.pr_state(self.p.events())[self.n]["verdict"]
        self.assertEqual((v["author"], v["reviewer"], v["evidence"], v["recorder"]),
                         (["term_author"], "codex", "https://example.test/r", "term_orchestrator"))
        self.assertIsNone(prog.verdict_identity_problem(v))

    def test_malformed_identity_rows_are_refused_not_retried(self):
        for bad in ({"author": 42}, {"author": "term_author"}, {"reviewer": 42}, {"evidence": ["x"]}, {"author": [42]}):
            with self.subTest(bad=bad):
                Fixture.seq += 1
                self.n = self.seq
                self.write({"prs": {str(self.n): pr(self.n)}, "stacks": []})
                self.p = prog.Program(self.slug)
                self.verdict(**bad)
                row = self.request()
                self.round()
                self.settled(row, "refused", 3, "malformed")
                self.assertEqual(self.merges(), [])

    def test_verifier_source_names_the_reviewer(self):
        os.environ["ORCA_TERMINAL_HANDLE"] = "term_orchestrator"
        real = prog.author_sessions
        prog.author_sessions = lambda branch: ["term_other"]
        try:
            prog.cmd_verdict([self.slug, "--pr", str(self.n), "--sha", f"h{self.n}", "--source", "verifier:codex",
                              "--evidence", "e"])
        finally:
            prog.author_sessions = real
            os.environ.pop("ORCA_TERMINAL_HANDLE")
        self.assertEqual(prog.pr_state(self.p.events())[self.n]["verdict"]["reviewer"], "codex")

    def test_a_verdict_recorded_by_cmd_verdict_for_an_orchestrator_merges(self):
        row = self.request()
        os.environ["ORCA_TERMINAL_HANDLE"] = "term_orchestrator"
        real = prog.author_sessions
        prog.author_sessions = lambda branch: ["term_author"]
        try:
            prog.cmd_verdict([self.slug, "--pr", str(self.n), "--sha", f"h{self.n}", "--source", "review:codex",
                              "--evidence", "https://example.test/r"])
            self.round()
        finally:
            prog.author_sessions = real
            os.environ.pop("ORCA_TERMINAL_HANDLE")
        self.settled(row, "merged", 0)

    def test_a_worktree_row_that_cannot_be_matched_is_not_a_missing_card(self):
        """An incomplete row ({}, no head, no id) was skipped as "does not match", so a partial answer read as "Orca
        answered and no card holds the PR" and opened the unverified-author path."""
        real = prog.orca_json
        good = {"id": "w1", "hostId": "local", "branch": "refs/heads/b", "head": "abc", "isMainWorktree": False}
        scope = {"hostIds": ["local"], "omittedHostIds": []}
        terminals = {"terminals": [{"handle": "term_b"}], "hostScope": scope, "truncated": False}

        def orca(rows):
            return lambda *a: ({"worktrees": rows, "hostScope": scope, "truncated": False}
                               if a[:2] == ("worktree", "list") else terminals)

        try:
            for broken in ({}, {"id": "w9"}, {**good, "head": None}, {**good, "branch": None}, {**good, "id": 7},
                           {**good, "hostId": ""}, "w9", None):
                with self.subTest(broken=broken):
                    prog.orca_json = orca([broken])
                    self.assertIsNone(prog.author_sessions("b"))
                    self.assertIsNone(prog.worktree_author_sessions("abc"))
                    # One unreadable row beside a readable match: the match alone is not the whole picture.
                    prog.orca_json = orca([good, broken])
                    self.assertIsNone(prog.author_sessions("b"))
                    self.assertIsNone(prog.worktree_author_sessions("abc"))
            prog.orca_json = orca([good])
            self.assertEqual(prog.author_sessions("b"), ["term_b"])
            self.assertEqual(prog.worktree_author_sessions("abc"), ["term_b"])
            prog.orca_json = orca([{**good, "branch": "refs/heads/other", "head": "def"}])
            self.assertEqual(prog.author_sessions("b"), [])
            self.assertEqual(prog.worktree_author_sessions("abc"), [])
            # Real Orca lists detached worktrees with an empty branch and unborn ones with an empty head: readable
            # rows that simply hold nothing, so they must not turn every lookup into "cannot tell".
            prog.orca_json = orca([good, {**good, "id": "w2", "branch": "", "head": ""}])
            self.assertEqual(prog.author_sessions("b"), ["term_b"])
            self.assertEqual(prog.worktree_author_sessions("abc"), ["term_b"])
        finally:
            prog.orca_json = real

    def test_an_answer_that_does_not_vouch_for_itself_is_not_a_missing_card(self):
        """The envelope fields that say "this answer is complete" count only when present and well typed. A missing or
        mistyped one was read as the empty default, so a partial answer became [] and opened the unverified path."""
        real = prog.orca_json
        scope = {"hostIds": ["local"], "omittedHostIds": []}
        row = {"id": "w1", "hostId": "local", "branch": "refs/heads/b", "head": "abc", "isMainWorktree": False}
        wt = {"worktrees": [row], "hostScope": scope, "truncated": False}
        tm = {"terminals": [{"handle": "term_b"}], "hostScope": scope, "truncated": False}

        def orca(w, t):
            return lambda *a: w if a[:2] == ("worktree", "list") else t

        def without(d, key):
            return {k: v for k, v in d.items() if k != key}

        def check(w, t, label):
            prog.orca_json = orca(w, t)
            self.assertIsNone(prog.author_sessions("b"), label)
            self.assertIsNone(prog.worktree_author_sessions("abc"), label)

        try:
            prog.orca_json = orca(wt, tm)
            self.assertEqual(prog.author_sessions("b"), ["term_b"])
            self.assertEqual(prog.worktree_author_sessions("abc"), ["term_b"])
            for key in ("truncated", "hostScope"):  # a missing field is not a complete answer
                check(without(wt, key), tm, f"worktree list without {key}")
                check(wt, without(tm, key), f"terminal list without {key}")
            for bad in (None, 0, "", [], {}, "false"):
                check({**wt, "truncated": bad}, tm, f"worktree truncated={bad!r}")
                check(wt, {**tm, "truncated": bad}, f"terminal truncated={bad!r}")
            bad_scopes = (None, False, [], {}, {"hostIds": "local", "omittedHostIds": []},
                          {"hostIds": [], "omittedHostIds": []}, {"hostIds": ["other"], "omittedHostIds": []},
                          {"hostIds": ["local", None], "omittedHostIds": []},
                          {"hostIds": ["local", 7], "omittedHostIds": []},
                          {"hostIds": {"local": 1}, "omittedHostIds": []},
                          {"hostIds": ["local"]}, {"hostIds": ["local"], "omittedHostIds": None},
                          {"hostIds": ["local"], "omittedHostIds": False},
                          {"hostIds": ["local"], "omittedHostIds": ""},
                          {"hostIds": ["local"], "omittedHostIds": {}},
                          {"hostIds": ["local"], "omittedHostIds": [None]},
                          {"hostIds": ["local"], "omittedHostIds": [7]})
            for bad in bad_scopes:
                check({**wt, "hostScope": bad}, tm, f"worktree hostScope={bad!r}")
                check(wt, {**tm, "hostScope": bad}, f"terminal hostScope={bad!r}")
            # "local-other" contains "local" as a substring: membership must be of a list, not of a string.
            check(wt, {**tm, "hostScope": {"hostIds": "local-other", "omittedHostIds": []}}, "substring host")
            # The main-worktree flag decides which rows may stand in for the author: it must be a real boolean.
            for bad in ("false", "true", 1, 0, None, [], {}):
                check({**wt, "worktrees": [{**row, "isMainWorktree": bad}]}, tm, f"isMainWorktree={bad!r}")
            check({**wt, "worktrees": [without(row, "isMainWorktree")]}, tm, "isMainWorktree missing")
            # No branch or head to look for is "cannot tell", not "nothing holds it".
            for bad in (None, "", 7, [], {}):
                prog.orca_json = orca(wt, tm)
                self.assertIsNone(prog.author_sessions(bad), bad)
                self.assertIsNone(prog.worktree_author_sessions(bad), bad)
        finally:
            prog.orca_json = real

    def test_an_unverified_row_must_carry_an_explicit_empty_author(self):
        """author_unverified vouches for "Orca answered and found no card", which is the list []; a row with no
        author field at all, or null, says nothing was looked up."""
        row = {"reviewer": "codex", "evidence": "https://example.test/r", "recorder": "term_x", "author_unverified": True}
        self.assertIsNone(prog.verdict_identity_problem({**row, "author": []}))
        for author in (None, "missing"):
            v = {**row} if author == "missing" else {**row, "author": author}
            self.assertTrue(prog.verdict_identity_problem(v), v)
        # The mark and a found author contradict each other; the author branch would skip the mark and the
        # reviewer-is-recorder check, so a row carrying both must be refused, not read as an ordinary reviewed row.
        both = {**row, "author": ["term_author"], "reviewer": "codex", "recorder": "codex", "source": "review:codex"}
        self.assertIn("malformed", prog.verdict_identity_problem(both) or "", both)
        self.assertIsNone(prog.verdict_identity_problem({**both, "author_unverified": None, "recorder": "term_x"}))

    def test_author_sessions_tells_no_card_from_a_lookup_that_cannot_vouch(self):
        real = prog.orca_json

        def fake(worktrees, terminals, **listing):
            """Orca as it answers on one host: worktrees carry hostId "local" and each terminal answer says it
            covered that host, unless the case sets its own."""
            def orca(*a):
                if a[:2] == ("worktree", "list"):
                    rows = [{"hostId": "local", "branch": "", "head": "", "isMainWorktree": False, **w} for w in worktrees]
                    hosts = sorted({r["hostId"] for r in rows if isinstance(r["hostId"], str)})
                    return {"worktrees": rows, "hostScope": {"hostIds": hosts, "omittedHostIds": []}, "truncated": False,
                            **listing}
                answer = terminals[a[-1]]
                if isinstance(answer, BaseException):
                    raise answer
                return {"hostScope": {"hostIds": ["local"], "omittedHostIds": []}, "truncated": False, **answer}
            return orca

        held = [{"id": "w1", "branch": "refs/heads/b"}, {"id": "w2", "branch": "refs/heads/b"},
                {"id": "w3", "branch": "refs/heads/other"}]
        try:
            # None: Orca did not give a complete answer, so the author is unknown, not absent.
            for answer in ({"terminals": [{"handle": "term_other"}], "truncated": True}, {"terminals": None}, {}):
                prog.orca_json = fake(held[:1], {"id:w1": answer})
                self.assertIsNone(prog.author_sessions("b"), answer)
            prog.orca_json = fake(held[:1], {"id:w1": {"terminals": [{"handle": "term_b"}, {"handle": "term_a"}]}})
            self.assertEqual(prog.author_sessions("b"), ["term_a", "term_b"])
            # A terminal row without a usable handle may be the author's own: dropping it would read as "no card".
            for row in ({}, {"handle": ""}, {"handle": 7}, {"handle": None}, "term_a", None):
                prog.orca_json = fake(held[:1], {"id:w1": {"terminals": [{"handle": "term_b"}, row]}})
                self.assertIsNone(prog.author_sessions("b"), row)
            prog.orca_json = fake([{**held[0], "head": "abc"}], {"id:w1": {"terminals": [{}]}})
            self.assertIsNone(prog.worktree_author_sessions("abc"))
            prog.orca_json = fake(held[:1], {"id:w1": ValueError("not json")})
            self.assertIsNone(prog.author_sessions("b"))
            prog.orca_json = fake(held[:1], {"id:w1": SystemExit("orca terminal list: connection refused")})
            self.assertIsNone(prog.author_sessions("b"))
            # Two worktrees hold the branch and only the successor has a terminal: its handle is the author.
            prog.orca_json = fake(held, {"id:w1": {"terminals": []}, "id:w2": {"terminals": [{"handle": "term_new"}]}})
            self.assertEqual(prog.author_sessions("b"), ["term_new"])
            # Both hold terminals: the union, each handle once.
            prog.orca_json = fake(held, {"id:w1": {"terminals": [{"handle": "term_old"}, {"handle": "term_new"}]},
                                         "id:w2": {"terminals": [{"handle": "term_new"}]}})
            self.assertEqual(prog.author_sessions("b"), ["term_new", "term_old"])
            # A later one failing or truncated leaves the author unverified, never the earlier one's partial list.
            first = {"terminals": [{"handle": "term_old"}]}
            for bad in (SystemExit("orca terminal list: selector_ambiguous"),
                        {"terminals": [{"handle": "term_new"}], "truncated": True}):
                prog.orca_json = fake(held, {"id:w1": first, "id:w2": bad})
                self.assertIsNone(prog.author_sessions("b"), bad)
            # A terminal answer is trusted only when it covered the worktree's own host: an empty answer from a host
            # Orca did not reach proves nothing. Another host being left out is an ordinary scope limit.
            old, new = {"terminals": [{"handle": "term_old"}]}, {"terminals": []}
            on = [{**held[0], "hostId": "h1"}, {**held[1], "hostId": "h2"}]
            covered = {"hostIds": ["h1", "h2"], "omittedHostIds": []}
            for scope in ({"hostIds": ["h1", "h2"], "omittedHostIds": ["h2"]},  # listed and omitted
                          {"hostIds": ["h1"], "omittedHostIds": ["h2"]},        # omitted only
                          {"hostIds": ["h1"]},                                  # not covered, nothing omitted
                          {"hostIds": []}, {}):                                 # no host scope at all
                prog.orca_json = fake(on, {"id:w1": {**old, "hostScope": covered}, "id:w2": {**new, "hostScope": scope}})
                self.assertIsNone(prog.author_sessions("b"), scope)
            prog.orca_json = fake(on, {"id:w1": {**old, "hostScope": covered},
                                       "id:w2": {**new, "hostScope": {"hostIds": ["h1", "h2"], "omittedHostIds": ["h9"]}}})
            self.assertEqual(prog.author_sessions("b"), ["term_old"])
            hostless = [{**held[0], "hostId": "h1"}, {**held[1], "hostId": None}]
            for scope in (covered, {"hostIds": ["h1", None], "omittedHostIds": []}):  # the second lists the missing host
                prog.orca_json = fake(hostless, {"id:w1": {**old, "hostScope": covered}, "id:w2": {**new, "hostScope": scope}})
                self.assertIsNone(prog.author_sessions("b"), scope)
            # A truncated worktree list or an unanswered host may hide a holder: not "no card".
            ok = {"id:w1": {"terminals": [{"handle": "term_new"}]}}
            prog.orca_json = fake(held[:1], ok, truncated=True)
            self.assertIsNone(prog.author_sessions("b"))
            prog.orca_json = fake(held[:1], ok, hostScope={"omittedHostIds": ["remote"]})
            self.assertIsNone(prog.author_sessions("b"))
            # []: Orca answered completely and no worktree holds the branch.
            prog.orca_json = fake(held[2:], ok)
            self.assertEqual(prog.author_sessions("b"), [])
        finally:
            prog.orca_json = real

    def record(self, *extra, handle="term_orchestrator", authors=("term_author",)):
        """cmd_verdict as a session with `handle` against a PR whose card is `authors`; returns the verdict rows added."""
        before = len([e for e in self.p.events() if e["ev"] == "verdict"])
        os.environ["ORCA_TERMINAL_HANDLE"] = handle
        real = prog.author_sessions
        prog.author_sessions = lambda branch: authors if authors is None else list(authors)
        try:
            prog.cmd_verdict([self.slug, "--pr", str(self.n), "--sha", f"h{self.n}", *extra])
        finally:
            prog.author_sessions = real
            os.environ.pop("ORCA_TERMINAL_HANDLE")
            self.added = len([e for e in self.p.events() if e["ev"] == "verdict"]) - before

    def test_a_pass_without_reviewer_or_evidence_is_refused_and_not_stored(self):
        for extra, authors in ((["--source", "independent-review", "--evidence", "e"], ("term_author",)),  # no reviewer
                               (["--source", "review:codex"], ("term_author",))):  # no evidence
            with self.subTest(extra=extra, authors=authors), self.assertRaises(SystemExit) as ctx:
                self.record(*extra, authors=authors)
            self.assertIn("nothing recorded", str(ctx.exception.code))
            self.assertEqual(self.added, 0)

    def test_the_source_identity_is_checked_as_well_as_the_reviewer_flag(self):
        for source in ("review:author", "review:term_author", "verifier:self", "review:Implementer"):
            with self.subTest(source=source), self.assertRaises(SystemExit):
                self.record("--source", source, "--reviewer", "codex", "--evidence", "e")
            self.assertEqual(self.added, 0)
        config(verdict_sources=["review:term_author"])  # a row stored that way is refused by the executor too
        self.verdict(source="review:term_author", reviewer="codex")
        row = self.request()
        self.round()
        self.settled(row, "refused", 3, "source reviewer")

    def test_the_pr_author_cannot_record_the_review(self):
        with self.assertRaises(SystemExit) as ctx:
            self.record("--source", "review:codex", "--evidence", "e", handle="term_author")
        self.assertIn("recorder", str(ctx.exception.code))
        self.assertEqual(self.added, 0)
        self.record("--source", "review:codex", "--evidence", "e")
        self.assertEqual(self.added, 1)

    def land_check(self):
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                prog.cmd_land_check([self.slug, "--pr", str(self.n)])
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue()

    def test_land_check_holds_a_verdict_the_executor_would_refuse(self):
        self.verdict(evidence=None)
        code, out = self.land_check()
        self.assertEqual(code, 1, out)
        self.assertIn("no evidence", out)
        self.verdict()
        code, out = self.land_check()
        self.assertNotIn("independent review", out)
        config(enabled=False)  # executor off: the direct path reads no identity, so land-check does not either
        self.verdict(evidence=None)
        self.assertNotIn("independent review", self.land_check()[1])

    def test_land_check_judges_a_renamed_repo_on_the_name_github_serves_now(self):
        """`orch land` asks executor_for (the name GitHub serves now); land-check asked the recorded name. A program
        made before a rename then read the executor as off, and passed a verdict the executor refuses."""
        old_sha = "oldsha"  # a stale pass for an earlier head with the same patch-id: the executor wants this head
        config(repos=["o/renamed"])  # the executor lists the new name only
        self.github_names("o/renamed")
        self.verdict(sha=old_sha)
        code, out = self.land_check()
        self.assertEqual(code, 1, out)
        self.assertIn("is not a pass at", out)
        # The same stale verdict is fine when the executor does not cover the repo: the direct path takes it.
        config(repos=["o/elsewhere"])
        code, out = self.land_check()
        self.assertNotIn("is not a pass at", out)
        # Listed under the recorded name only, GitHub names it differently: orch land refuses, so land-check holds.
        config(repos=["o/r"])
        code, out = self.land_check()
        self.assertEqual(code, 1, out)
        self.assertIn("o/renamed on GitHub now", out)
        # GitHub not answering is not "the executor is off": hold, as orch land yields.
        config(repos=["o/renamed"])
        st = self.gh(); st["repo_fail"] = True; self.write(st)
        code, out = self.land_check()
        self.assertEqual(code, 1, out)
        self.assertIn("502", out)

    def test_land_check_asks_github_only_when_the_executor_is_on(self):
        """executor_for asks GitHub for the repo's name; a repo the executor is off for must not pay (or fail) for it."""
        config(enabled=False)
        st = self.gh(); st["repo_fail"] = True; self.write(st)
        self.verdict()
        code, out = self.land_check()
        self.assertNotIn("502", out)

    def fake_orca(self, worktrees, terminals, truncated=False, terminals_truncated=False):
        """prog.orca_json over one Orca host with `worktrees` rows and `terminals` {worktree id: [handles]}; a terminal
        answer says it covered the host, as Orca's does."""
        def answer(*a):
            if a[:2] == ("worktree", "list"):
                return {"worktrees": worktrees, "truncated": truncated,
                        "hostScope": {"hostIds": ["local"], "omittedHostIds": []}}
            sel = a[a.index("--worktree") + 1]
            return {"terminals": [{"handle": h} for h in terminals.get(sel[3:], [])], "truncated": terminals_truncated,
                    "hostScope": {"hostIds": ["local"], "omittedHostIds": []}}
        real = prog.orca_json
        prog.orca_json = answer
        self.addCleanup(setattr, prog, "orca_json", real)

    CARD = {"id": "wc", "hostId": "local", "path": "/w/card", "branch": "refs/heads/card-local", "head": "abc",
            "isMainWorktree": False, "linkedPR": None}
    MAIN = {"id": "wm", "hostId": "local", "path": "/w/main", "branch": "refs/heads/main", "head": "abc",
            "isMainWorktree": True, "linkedPR": None}

    def test_author_is_found_when_the_card_branch_differs_from_the_pr_branch(self):
        # The card works on `card-local` and pushes it to the PR branch `feature`: no worktree holds `feature`.
        self.fake_orca([self.CARD, self.MAIN], {"wc": ["term_author"], "wm": ["term_main"]})
        self.assertEqual(prog.author_sessions("feature"), [])  # Orca answered completely: no worktree holds the branch
        self.assertEqual(prog.worktree_author_sessions("abc"), ["term_author"])  # by head sha, never the main checkout
        # A PR number is not read: the listing spans repositories, so another repository's card linking #7 would
        # stand in for this PR's author. Neither is the branch name, which the first lookup already used.
        self.fake_orca([{**self.CARD, "head": "zzz", "linkedPR": {"number": 7}, "branch": "refs/heads/feature"}],
                       {"wc": ["term_author"]})
        self.assertEqual(prog.worktree_author_sessions("abc"), [])
        # None is not "no card": a partial or malformed answer cannot show who the author is.
        self.fake_orca([self.CARD], {"wc": ["term_author"]}, truncated=True)
        self.assertIsNone(prog.worktree_author_sessions("abc"))
        self.fake_orca([self.CARD], {"wc": ["term_author"]}, terminals_truncated=True)
        self.assertIsNone(prog.worktree_author_sessions("abc"))
        self.fake_orca([{k: v for k, v in self.CARD.items() if k != "id"}], {})
        self.assertIsNone(prog.worktree_author_sessions("abc"))  # the matching card cannot be read
        self.fake_orca([self.CARD, "not a row"], {"wc": ["term_author"]})
        self.assertIsNone(prog.worktree_author_sessions("abc"))

    def test_a_lookup_that_cannot_vouch_stores_nothing_even_if_the_card_exists(self):
        row_head = f"h{self.n}"
        card = {**self.CARD, "head": row_head}
        os.environ["ORCA_TERMINAL_HANDLE"] = "term_orchestrator"
        try:
            for case in (dict(terminals_truncated=True), dict(truncated=True)):
                self.fake_orca([card], {"wc": ["term_author"]}, **case)
                before = len([e for e in self.p.events() if e["ev"] == "verdict"])
                with self.subTest(case=case), self.assertRaises(SystemExit) as ctx:
                    prog.cmd_verdict([self.slug, "--pr", str(self.n), "--sha", row_head, "--source", "review:codex",
                                      "--evidence", "e"])
                self.assertIn("nothing recorded", str(ctx.exception.code))
                self.assertEqual(len([e for e in self.p.events() if e["ev"] == "verdict"]), before)
        finally:
            os.environ.pop("ORCA_TERMINAL_HANDLE")

    def record_unverified(self, *extra, handle="term_orchestrator"):
        before = len([e for e in self.p.events() if e["ev"] == "verdict"])
        os.environ["ORCA_TERMINAL_HANDLE"] = handle
        real = prog.author_sessions, prog.worktree_author_sessions
        prog.author_sessions = prog.worktree_author_sessions = lambda *a, **kw: []  # Orca answered: no card
        try:
            prog.cmd_verdict([self.slug, "--pr", str(self.n), "--sha", f"h{self.n}", *extra])
        finally:
            prog.author_sessions, prog.worktree_author_sessions = real
            os.environ.pop("ORCA_TERMINAL_HANDLE")
            self.added = len([e for e in self.p.events() if e["ev"] == "verdict"]) - before

    def test_a_pass_is_recorded_as_unverified_when_no_card_is_found_and_the_reviewer_is_not_the_recorder(self):
        row = self.request()
        self.record_unverified("--source", "review:codex", "--evidence", "https://example.test/r")
        v = prog.pr_state(self.p.events())[self.n]["verdict"]
        self.assertEqual((self.added, v["author"], v["author_unverified"]), (1, [], True))
        self.round()
        self.settled(row, "merged", 0)

    def test_an_unverified_author_still_refuses_what_it_can_see(self):
        for extra in (["--source", "independent-review", "--reviewer", "term_orchestrator", "--evidence", "e"],  # reviewer is the recorder
                      ["--source", "independent-review", "--reviewer", "self", "--evidence", "e"],  # a self alias
                      ["--source", "review:codex"]):  # no evidence
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                self.record_unverified(*extra)
            self.assertEqual(self.added, 0)

    def test_an_unreadable_author_lookup_refuses_the_pass(self):
        with self.assertRaises(SystemExit) as ctx:
            self.record("--source", "review:codex", "--evidence", "e", authors=None)
        self.assertIn("nothing recorded", str(ctx.exception.code))
        self.assertEqual(self.added, 0)

    def test_a_stored_row_with_no_author_needs_the_unverified_mark(self):
        for who, ok in (({"author": []}, False), ({"author": [], "author_unverified": True}, True),
                        ({"author": [], "author_unverified": "yes"}, False),
                        ({"author": [], "author_unverified": True, "recorder": "codex"}, False),  # recorder is the reviewer
                        # the recorder named only in the source: its own review
                        ({"author": [], "author_unverified": True, "recorder": "term_x", "source": "review:term_x"}, False)):
            with self.subTest(who=who):
                self.assertEqual(prog.verdict_identity_problem(
                    {"reviewer": "codex", "evidence": "e", "recorder": "term_orchestrator", **who}) is None, ok)

    def test_land_check_and_the_executor_agree_on_a_verdict_for_another_sha(self):
        self.verdict(sha="hother")  # same patch-id as the head, a different sha
        code, out = self.land_check()
        self.assertEqual(code, 1, out)
        self.assertIn(f"not a pass at h{self.n}", out)
        self.assertNotIn("READY", out)
        row = self.request()
        self.round()
        self.settled(row, "refused", 3, f"not a pass at h{self.n}")
        config(enabled=False)  # executor off: the direct path keeps the patch-id rule, a moved head with the same patch holds
        self.assertNotIn("not a pass at", self.land_check()[1])

    def test_a_failing_verdict_needs_no_independence(self):
        os.environ["ORCA_TERMINAL_HANDLE"] = "term_author"
        real = prog.author_sessions
        prog.author_sessions = lambda branch: ["term_author"]
        try:
            prog.cmd_verdict([self.slug, "--pr", str(self.n), "--sha", f"h{self.n}", "--source", "review:term_author",
                              "--result", "fail"])
        finally:
            prog.author_sessions = real
            os.environ.pop("ORCA_TERMINAL_HANDLE")
        self.assertEqual(prog.pr_state(self.p.events())[self.n]["verdict"]["result"], "fail")

    def test_latest_verdict_fail_wins_over_an_older_pass(self):
        self.verdict(result="fail")
        row = self.request()
        self.round()
        self.settled(row, "refused", 3, "fail from review:codex")

    def test_no_test_results_comment(self):
        self.edit_pr(comments=[])
        row = self.request()
        self.round()
        self.settled(row, "refused", 3, "test-results")

    def test_ci_failed(self):
        self.edit_pr(statusCheckRollup=pr(self.n, conclusion="FAILURE")["statusCheckRollup"])
        row = self.request()
        self.round()
        self.settled(row, "refused", 3)

    def test_ci_pending_waits_in_the_queue(self):
        st = self.gh(); st["runs"] = {f"h{self.n}": [{"name": "CI", "status": "in_progress"}]}; self.write(st)
        row = self.request()
        self.round()
        got = self.settled(row, "queued", None)
        self.assertIn("CI", got["reason"])
        st = self.gh(); st["runs"] = {}; self.write(st)
        self.round()
        self.settled(row, "merged", 0)

    def test_unresolved_thread(self):
        st = self.gh(); st["threads"] = {str(self.n): [[False]]}; self.write(st)
        row = self.request()
        self.round()
        self.settled(row, "refused", 3, "unresolved review thread")

    def test_conflict(self):
        self.edit_pr(mergeStateStatus="DIRTY")
        row = self.request()
        self.round()
        self.settled(row, "refused", 3, "conflicts")

    def test_blocked_by_a_required_review_waits_then_lands_once_approved(self):
        self.edit_pr(mergeStateStatus="BLOCKED")
        row = self.request()
        self.round()
        self.assertIn("required review", self.settled(row, "queued", None)["reason"])
        self.assertEqual(inbox(row["id"]), [], "a wait is not a refusal")
        self.edit_pr(mergeStateStatus="CLEAN")  # someone approved
        self.round()
        self.settled(row, "merged", 0)

    def test_a_failure_of_ours_behind_a_github_block_is_still_refused(self):
        """A missing approval must not hide a failed CI run: the worker has something to fix now."""
        st = self.gh()
        st["prs"][str(self.n)] = pr(self.n, mss="BLOCKED", conclusion="FAILURE")
        self.write(st)
        row = self.request()
        self.round()
        self.settled(row, "refused", 3, "CI failed")

    def test_a_thread_behind_a_github_block_is_still_refused(self):
        """BLOCKED can be the thread itself (required thread resolution): waiting would never end."""
        st = self.gh(); st["threads"] = {str(self.n): [[False]]}; self.write(st)
        self.edit_pr(mergeStateStatus="BLOCKED")
        row = self.request()
        self.round()
        self.settled(row, "refused", 3, "unresolved review thread")

    def test_a_github_wait_holds_no_lane(self):
        self.edit_pr(mergeStateStatus="BLOCKED")
        self.request()
        self.round()
        self.assertIsNone(prog.ExclusiveLock(self.p, "main").holder())

    def test_an_unanswered_owner_lookup_waits_instead_of_refusing(self):
        st = self.gh(); st["user_fail"] = True; self.write(st)
        rows = [self.request()]
        self.round()
        self.assertIn("gh api user", self.settled(rows[0], "queued", None)["reason"])
        st = self.gh(); st["user_fail"] = False; self.write(st)
        self.round()
        self.settled(rows[0], "merged", 0)

    def test_an_unread_stack_listing_waits_instead_of_refusing(self):
        st = self.gh(); st["stacks_fail"] = True; self.write(st)
        row = self.request()
        self.round()
        self.settled(row, "queued", None)
        st = self.gh(); st["stacks_fail"] = False; self.write(st)
        self.round()
        self.settled(row, "merged", 0)

    def test_a_verdict_replaced_during_the_checks_does_not_merge(self):
        row = self.request()
        real = prog.pr_view

        def view(repo, n):
            if sys._getframe(1).f_code.co_name == "merge_unit":  # every check passed; another verdict lands now
                self.p.append("verdict", pr=self.n, sha=f"h{self.n}", patch_id=pid(self.n), source="review:claude",
                              result="pass")
            return real(repo, n)
        prog.pr_view = view
        try:
            self.round()
        finally:
            prog.pr_view = real
        got = self.settled(row, "refused", 3, "verdict changed")
        self.assertEqual(got["audit"]["verdict"]["source"], "review:claude")

    def test_a_merge_found_after_the_expiry_is_recorded_not_expired(self):
        config(max_wait_hours=1e-9)  # every request is past it (0 would read as unset, the 24 h default)
        row = self.request()
        self.assertTrue(store.land_claim(row["id"]))  # the round died after the merge
        self.edit_pr(state="MERGED", merge_sha=f"m{self.n}")
        self.round()
        self.settled(row, "merged", 0)

    def test_the_same_slug_and_pr_in_two_homes_are_two_requests(self):
        a, _ = store.land_put(self.slug, "/homes/a", "o/r", self.n, "s1", None, "normal", None)
        b, outcome = store.land_put(self.slug, "/homes/b", "o/r", self.n, "s1", None, "normal", None)
        self.assertEqual(outcome, "new")
        self.assertNotEqual(a["id"], b["id"])
        _, outcome = store.land_put(self.slug, "/homes/b", "o/r", self.n, "s2", None, "normal", None)
        self.assertEqual(store.land_get(a["id"])["status"], "queued", "another home's request is not superseded")

    def test_a_stack_seen_on_the_pr_is_refused_even_when_the_listing_fails(self):
        st = self.gh(); st["stacks_fail"] = True; st["pr_stack"] = {str(self.n): {"number": 100}}; self.write(st)
        row = self.request()
        self.round()
        self.settled(row, "refused", 1, "single PRs only")

    def stack(self, *members):
        """Open PRs `members` (bottom first, one of them the PR under test) as one GitHub stack on main."""
        st = self.gh()
        for below, n in zip((None, *members), members):
            st["prs"][str(n)] = dict(pr(n), baseRefName=f"b{below}" if below else "main")
        st["stacks"] = [{"id": 900, "number": 100, "open": True, "base": {"ref": "main"},
                         "pull_requests": [{"number": n, "state": "open", "draft": False, "merged_at": None,
                                            "head": {"ref": f"b{n}", "sha": f"h{n}"}} for n in members]}]
        self.write(st)

    def test_the_bottom_layer_is_refused_with_the_retarget_that_opens_the_way(self):
        up = self.n + 100
        self.stack(self.n, up)
        row = self.request()
        self.round()
        got = self.settled(row, "refused", 1, "single PRs only")
        self.assertIn(f"gh pr edit {up} --base main --repo o/r", got["reason"])
        self.assertIn(f"orch land {self.slug} --pr {self.n}", got["reason"])
        self.assertEqual(self.gh().get("log", []), [], "the executor only says it: nothing is retargeted")

    def test_the_do_line_uses_the_bottom_layers_own_base(self):
        up = self.n + 100
        self.stack(self.n, up)
        st = self.gh()
        st["prs"][str(self.n)]["baseRefName"] = "release"
        self.write(st)
        row = self.request()
        real = prog.open_rows
        # The page of open PRs the executor reads can be short of the bottom PR (OPEN_LIMIT); pr view still has it.
        prog.open_rows = lambda repo: [r for r in real(repo) if r["number"] != self.n]
        try:
            self.round()
        finally:
            prog.open_rows = real
        self.settled(row, "refused", 1, f"gh pr edit {up} --base release --repo o/r")

    def test_an_unreadable_base_withholds_the_command_instead_of_guessing_main(self):
        up = self.n + 100
        self.stack(self.n, up)
        st = self.gh()
        st["prs"][str(self.n)]["drop"] = ["baseRefName"]
        self.write(st)
        row = self.request()
        self.round()
        got = self.settled(row, "refused", 1, f"#{up}")
        self.assertNotIn("--base", got["reason"])

    def test_the_bottom_of_three_names_only_the_layer_above_it(self):
        mid, top = self.n + 100, self.n + 200
        self.stack(self.n, mid, top)
        row = self.request()
        self.round()
        got = self.settled(row, "refused", 1, f"gh pr edit {mid} --base main --repo o/r")
        self.assertNotIn(f"gh pr edit {top}", got["reason"])

    def test_a_layer_above_the_bottom_is_told_to_wait_for_the_layers_below(self):
        for members in ((self.n + 100, self.n), (self.n + 100, self.n, self.n + 200)):
            with self.subTest(members=members):
                self.stack(*members)
                self.p.append("ready", pr=self.n, ticket=f"T-{self.n}")  # the new `orch land` that makes the request
                row = self.request()
                self.round()
                got = self.settled(row, "refused", 1, f"Land #{members[0]} first")
                self.assertNotIn("gh pr edit", got["reason"])
                drain()

    def test_a_github_wait_releases_a_lane_taken_in_an_earlier_round(self):
        lane = prog.ExclusiveLock(self.p, "main")
        self.assertTrue(lane.take(self.n))
        self.edit_pr(mergeStateStatus="BLOCKED")
        self.request()
        self.round()
        self.assertIsNone(prog.ExclusiveLock(self.p, "main").holder())

    def test_a_verdict_refusal_releases_the_lane_the_request_held(self):
        """A lane taken in an earlier round must not outlive a refusal the requester will not retry on its own."""
        lane = prog.ExclusiveLock(self.p, "main")
        self.addCleanup(lane.release, self.n + 1000)
        self.assertTrue(lane.take(self.n))
        self.verdict(sha="hother")  # the latest verdict is for another head
        row = self.request()
        self.round()
        self.settled(row, "refused", 3, "latest verdict")
        self.assertIsNone(lane.holder())
        self.assertTrue(prog.ExclusiveLock(self.p, "main").take(self.n + 1000), "the next PR proceeds without a yield")

    def test_a_parked_pr_whose_request_is_refused_releases_its_lane(self):
        """Parked, then refused for a verdict reason before attempt: the lane must not wait out the stale window."""
        lane = prog.ExclusiveLock(self.p, "main")
        self.addCleanup(lane.release, self.n + 1000)
        self.assertTrue(lane.take(self.n))
        self.p.append("parked", pr=self.n, note="waits on a person")
        self.verdict(sha="hother")
        row = self.request()
        self.round()
        self.settled(row, "refused", 3, "latest verdict")
        self.assertIsNone(lane.holder())
        self.assertTrue(prog.ExclusiveLock(self.p, "main").take(self.n + 1000), "the next PR proceeds without a yield")

    def test_a_parked_pr_refused_by_attempt_releases_its_lane(self):
        lane = prog.ExclusiveLock(self.p, "main")
        self.assertTrue(lane.take(self.n))
        self.p.append("parked", pr=self.n, note="waits on a person")
        row = self.request()
        self.round()
        self.settled(row, "refused", 1, "on hold")
        self.assertIsNone(lane.holder())

    def test_a_verdict_refusal_leaves_another_prs_lane_alone(self):
        lane = prog.ExclusiveLock(self.p, "main")
        self.addCleanup(lane.release, self.n + 1000)
        self.assertTrue(lane.take(self.n + 1000))
        self.verdict(sha="hother")
        row = self.request()
        self.round()
        self.settled(row, "refused", 3, "latest verdict")
        self.assertEqual(lane.holder()["pr"], self.n + 1000)

    def test_a_github_that_never_answers_expires_the_request(self):
        config(max_wait_hours=1e-9)
        st = self.gh(); st["user_fail"] = True; self.write(st)
        row = self.request()
        self.round()
        self.settled(row, "refused", 1, "expired")

    def test_a_max_wait_that_can_never_end_refuses_instead_of_waiting(self):
        for bad in ("24h", "NaN", 0, True):
            with self.subTest(bad=bad):
                drain()
                config(max_wait_hours=bad)
                row = self.request()
                self.round()
                self.settled(row, "refused", 1, "positive number of hours")

    def test_human_gate_program(self):
        self.p.update(lambda c: c.update(merge_policy="human-gate"))
        row = self.request()
        self.round()
        self.settled(row, "refused", 1, "merge_policy")

    def test_repo_not_in_the_allowlist(self):
        row = self.request()
        config(repos=["o/other"])
        self.round()
        self.settled(row, "refused", 1, "does not cover o/r")

    def test_a_wildcard_of_an_org_or_another_user_is_refused(self):
        config(repos=["o/*"])
        st = self.gh(); st["login"] = "someone-else"; self.write(st)
        row = self.request()
        self.round()
        self.settled(row, "refused", 1, "own repos (someone-else) only")
        self.assertEqual(self.merges(), [])

    def test_an_org_repo_listed_by_name_lands_and_waits_on_its_required_review(self):
        st = self.gh(); st["login"] = "someone-else"; self.write(st)  # config lists o/r by name
        self.edit_pr(mergeStateStatus="BLOCKED")
        row = self.request()
        self.round()
        self.assertIn("required review", self.settled(row, "queued", None)["reason"])
        self.assertEqual(self.merges(), [], "a required approval is never bypassed")
        self.edit_pr(mergeStateStatus="CLEAN")  # a person approved
        self.round()
        self.settled(row, "merged", 0)

    # -- scope by owner, judged on the repo GitHub names now

    def github_names(self, now, **meta):
        st = self.gh(); st["repos"] = {"o/r": {"nameWithOwner": now, "isFork": False, "isArchived": False, **meta}}
        self.write(st)

    def test_owner_wildcard_merges(self):
        config(repos=["o/*"])
        row = self.request()
        self.round()
        self.settled(row, "merged", 0)

    def test_excluded_under_the_wildcard_is_off(self):
        config(repos=["o/*"], exclude=["o/r"])
        row = self.request()
        self.round()
        self.settled(row, "refused", 1, "does not cover o/r")

    def test_a_program_under_the_old_name_still_goes_to_the_executor(self):
        """The program says o/r; GitHub calls it o/renamed now and only that name is listed: never a direct merge."""
        config(repos=["o/renamed"])
        self.github_names("o/renamed")
        land_exec.beat(phase="done")
        r = subprocess.run([sys.executable, HERE / "prog.py", "land", self.slug, "--pr", str(self.n)],
                           capture_output=True, text=True)
        self.assertIn("queued land request", r.stdout + r.stderr)
        self.assertEqual(self.merges(), [], "orch land itself merges nothing")
        self.round()
        self.assertEqual(len(self.merges()), 1)

    def test_a_repo_moved_out_of_the_scope_is_refused_never_merged_directly(self):
        for repos, now in ((["o/*"], "elsewhere/r"), (["o/r"], "o/renamed")):
            with self.subTest(repos=repos, now=now):
                config(repos=repos)
                self.github_names(now)
                r = subprocess.run([sys.executable, HERE / "prog.py", "land", self.slug, "--pr", str(self.n)],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn(f"o/r is {now} on GitHub now", r.stderr)
                self.assertEqual(self.merges(), [])

    def test_an_exclusion_under_the_new_name_merges_directly(self):
        config(repos=["o/*"], exclude=["o/renamed"])
        self.github_names("o/renamed")
        self.assertEqual(prog.executor_for("o/r")[0], None)

    def test_an_unanswered_rename_lookup_yields_instead_of_merging_directly(self):
        st = self.gh(); st["repo_fail"] = True; self.write(st)
        r = subprocess.run([sys.executable, HERE / "prog.py", "land", self.slug, "--pr", str(self.n)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("undecided", r.stdout)
        self.assertEqual(self.merges(), [])

    def test_a_rename_inside_the_wildcard_still_merges(self):
        config(repos=["o/*"])
        self.github_names("o/renamed")
        row = self.request()
        self.round()
        self.settled(row, "merged", 0)

    def test_a_rename_out_of_an_explicit_entry_is_refused(self):
        self.github_names("o/renamed")  # config lists o/r by name only
        row = self.request()
        self.round()
        self.settled(row, "refused", 1, "o/renamed on GitHub now")

    def test_an_exclusion_on_the_new_name_holds_for_the_old_one(self):
        config(repos=["o/*"], exclude=["o/renamed"])
        self.github_names("o/renamed")
        row = self.request()
        self.round()
        self.settled(row, "refused", 1, "does not cover")

    def test_a_transfer_to_another_owner_is_refused(self):
        for repos in (["o/r"], ["o/r", "elsewhere/*"]):
            with self.subTest(repos=repos):
                drain()
                config(repos=repos)
                self.github_names("elsewhere/r")
                row = self.request()
                self.round()
                self.settled(row, "refused", 1, "elsewhere/r on GitHub now")

    def test_a_fork_or_archive_the_wildcard_alone_covers_is_refused_never_merged_directly(self):
        for meta, why in (({"isFork": True}, "a fork"), ({"isArchived": True}, "archived")):
            with self.subTest(why=why):
                drain()
                config(repos=["o/*"])
                self.github_names("o/r", **meta)
                row = self.request()
                self.round()
                self.settled(row, "refused", 1, why)

    def test_a_fork_listed_by_name_merges(self):
        config(repos=["o/*", "o/r"])
        self.github_names("o/r", isFork=True)
        row = self.request()
        self.round()
        self.settled(row, "merged", 0)

    def test_an_unanswered_repo_lookup_waits(self):
        st = self.gh(); st["repo_fail"] = True; self.write(st)
        row = self.request()
        self.round()
        self.settled(row, "queued", None, "gh api repos/o/r")

    def test_a_malformed_scope_refuses(self):
        row = self.request()
        config(repos=["*/*"])
        self.round()
        self.settled(row, "refused", 1, "settings cannot be read")

    def test_a_string_verdict_sources_stops_land(self):
        config(verdict_sources="review:codex")
        with self.assertRaises(SystemExit) as e:
            prog.land_executor("o/r")
        self.assertIn("verdict_sources must be a list", str(e.exception.code))

    def test_dependency_not_landed_waits(self):
        self.p.append("dep", ticket=f"T-{self.n}", after=["T-zz"])
        st = self.gh(); st["prs"]["900"] = dict(pr(900), headRefOid="h900"); self.write(st)
        self.p.append("ready", pr=900, ticket="T-zz")
        row = self.request()
        self.round()
        self.settled(row, "queued", None)

    def test_expired(self):
        config(max_wait_hours=0.0001)
        row = self.request()
        import time; time.sleep(1)
        self.round()
        self.settled(row, "refused", 1, "expired")

    def test_agent_session_cannot_run_a_round(self):
        row = self.request()
        os.environ["CLAUDECODE"] = "1"
        try:
            self.assertEqual(land_exec.cmd_round([]), 1)
        finally:
            os.environ.pop("CLAUDECODE")
        self.assertEqual(store.land_get(row["id"])["status"], "queued")

    # -- the queue itself

    def test_same_sha_is_one_request_and_a_new_sha_supersedes(self):
        a = self.request()
        self.assertEqual(self.request()["id"], a["id"])
        b = self.request(sha="hnew")
        self.assertNotEqual(b["id"], a["id"])
        self.assertEqual(store.land_get(a["id"])["status"], "superseded")
        self.assertTrue(store.land_claim(b["id"]))
        row, outcome = store.land_put(self.slug, str(prog.HOME), "o/r", self.n, "hthird", None, "normal", None)
        self.assertEqual((outcome, row["id"]), ("busy", b["id"]))
        self.assertFalse(store.land_claim(a["id"]), "a superseded request is never claimed")
        store.land_finish(b["id"], "refused", 3, "test")


class Request(unittest.TestCase):
    """`orch land` as an agent runs it: with the executor on it only records; off, it merges as before."""

    def setUp(self):
        drain()
        Fixture.seq += 1
        self.slug, self.n = f"r{Fixture.seq}", Fixture.seq
        self.state = ROOT / f"gh-{self.slug}.json"
        self.state.write_text(json.dumps({"prs": {str(self.n): pr(self.n)}, "stacks": []}))
        os.environ["FAKE_GH_STATE"] = str(self.state)
        subprocess.run([sys.executable, HERE / "prog.py", "init", self.slug, "--repo", "o/r", "--run", "run_x"],
                       check=True, capture_output=True)
        p = prog.Program(self.slug)
        p.append("verdict", pr=self.n, sha=f"h{self.n}", patch_id=pid(self.n), source="review:codex", result="pass")

    def land(self):
        r = subprocess.run([sys.executable, HERE / "prog.py", "land", self.slug, "--pr", str(self.n)],
                           capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def merged(self):
        return [a for a in json.loads(self.state.read_text()).get("log", []) if a[:2] == ["pr", "merge"]]

    def test_on_it_records_and_does_not_merge(self):
        config()
        land_exec.beat(phase="done")
        code, out = self.land()
        self.assertEqual(code, 2, out)
        self.assertIn("queued land request", out)
        self.assertEqual(self.merged(), [])
        self.assertEqual(store.land_rows(("queued",))[-1]["pr"], self.n)

    def test_says_when_the_executor_is_not_running(self):
        config()
        prog.LAND_HEARTBEAT.unlink(missing_ok=True)
        code, out = self.land()
        self.assertEqual(code, 2, out)
        self.assertIn("no land executor round", out)

    def test_off_it_merges_directly(self):
        config(enabled=False)
        code, out = self.land()
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.merged()), 1)

    def test_a_broken_settings_file_stops_instead_of_merging(self):
        pathlib.Path(os.environ["AGENT_SKILLS_CONFIG"]).write_text('{"orchestrate": {"land_executor": {"enabled": tr')
        code, out = self.land()
        self.assertNotEqual(code, 0, out)
        self.assertIn("land_executor", out)
        self.assertEqual(self.merged(), [])
        config()


class Standalone(unittest.TestCase):
    """A standalone card's PR in a repo with no program: `orch verdict` / `orch land --repo` create `<name>-standalone`."""
    SLUG = "r-standalone"

    def setUp(self):
        drain()
        Fixture.seq += 1
        self.n = Fixture.seq + 100
        self.state = ROOT / f"gh-standalone-{self.n}.json"
        self.state.write_text(json.dumps({"prs": {str(self.n): pr(self.n)}, "stacks": []}))
        os.environ["FAKE_GH_STATE"] = str(self.state)
        shutil.rmtree(prog.HOME / self.SLUG, ignore_errors=True)
        config()

    def orch(self, *args):
        env = {**os.environ, "ORCA_TERMINAL_HANDLE": "term_orchestrator"}
        r = subprocess.run([sys.executable, HERE / "prog.py", *args], capture_output=True, text=True, env=env)
        return r.returncode, r.stdout + r.stderr

    def test_land_creates_the_program_and_the_next_round_merges(self):
        land_exec.beat(phase="done")
        self.assertFalse((prog.HOME / self.SLUG).exists())
        code, out = self.orch("land", self.SLUG, "--repo", "o/r", "--pr", str(self.n))  # no program, no verdict yet
        self.assertEqual(code, 2, out)
        self.assertIn("queued land request", out)
        cfg = json.loads((prog.HOME / self.SLUG / "program.json").read_text())
        self.assertEqual((cfg["repo"], cfg["merge_policy"]), ("o/r", "autonomous"))
        self.assertEqual(store.land_rows(("queued",))[-1]["pr"], self.n)
        code, out = self.orch("verdict", self.SLUG, "--repo", "o/r", "--pr", str(self.n), "--sha", f"h{self.n}",
                              "--source", "review:codex", "--evidence", "https://example.test/review/1")
        self.assertEqual(code, 0, out)
        self.assertEqual(land_exec.cmd_round([]), 0)
        merged = [a for a in json.loads(self.state.read_text()).get("log", []) if a[:2] == ["pr", "merge"]]
        self.assertEqual(len(merged), 1)
        self.assertEqual(store.land_rows(("merged",))[-1]["pr"], self.n)

    def test_an_existing_program_is_left_alone(self):
        self.orch("verdict", self.SLUG, "--repo", "o/r", "--pr", str(self.n), "--sha", f"h{self.n}",
                  "--source", "review:codex", "--evidence", "https://example.test/review/1")
        path = prog.HOME / self.SLUG / "program.json"
        before = path.read_text()
        self.orch("land", self.SLUG, "--repo", "o/r", "--pr", str(self.n))
        self.assertEqual(path.read_text(), before)

    def test_an_existing_program_of_another_repo_is_refused(self):
        subprocess.run([sys.executable, HERE / "prog.py", "init", self.SLUG, "--repo", "other/r", "--run", "run_x"],
                       check=True, capture_output=True)
        for cmd in ("land", "verdict"):
            code, out = self.orch(cmd, self.SLUG, "--repo", "o/r", "--pr", str(self.n), "--sha", f"h{self.n}",
                                  "--source", "review:codex", "--evidence", "https://example.test/review/1")
            self.assertNotEqual(code, 0, out)
            self.assertIn("is for other/r, not o/r", out)
        self.assertNotIn(self.n, [r["pr"] for r in store.land_rows(("queued",))])

    def test_a_second_creation_loses_without_touching_the_first(self):
        args = [self.SLUG, "--repo", "o/r"]
        prog.standalone_program(args)
        path = prog.HOME / self.SLUG / "program.json"
        first = path.read_text()
        prog.standalone_program(args)
        self.assertEqual(path.read_text(), first)
        self.assertEqual(list(path.parent.glob("program.json.*")), [], "no temporary file is left")

    def test_init_that_loses_the_race_does_not_overwrite(self):
        prog.standalone_program([self.SLUG, "--repo", "o/r"])
        path = prog.HOME / self.SLUG / "program.json"
        first = path.read_text()
        with self.assertRaises(FileExistsError):  # init passed its exists() check before the card created it
            prog.write_program(path.parent, {"repo": "other/r"}, "run_x", "human-gate")
        self.assertEqual(path.read_text(), first)

    def test_executor_off_creates_nothing(self):
        config(enabled=False)
        code, out = self.orch("land", self.SLUG, "--repo", "o/r", "--pr", str(self.n))
        self.assertNotEqual(code, 0, out)
        self.assertIn("land_executor is off", out)
        self.assertFalse((prog.HOME / self.SLUG).exists())

    def test_a_slug_that_is_not_the_repos_standalone_program_creates_nothing(self):
        code, out = self.orch("land", "other", "--repo", "o/r", "--pr", str(self.n))
        self.assertNotEqual(code, 0, out)
        self.assertFalse((prog.HOME / "other").exists())

    # -- OWNER/*, renames, forks and the Run

    def github(self, **repos):
        st = json.loads(self.state.read_text())
        st["repos"] = {k.replace("__", "/"): {"isFork": False, "isArchived": False, **v} for k, v in repos.items()}
        self.state.write_text(json.dumps(st))

    def cfg(self, slug=None):
        return json.loads((prog.HOME / (slug or self.SLUG) / "program.json").read_text())

    def test_owner_wildcard_creates_the_program(self):
        config(repos=["o/*"])
        code, out = self.orch("verdict", self.SLUG, "--repo", "o/r", "--pr", str(self.n), "--sha", f"h{self.n}",
                              "--source", "review:codex", "--evidence", "https://example.test/review/1")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.cfg()["repo"], "o/r")

    def test_an_org_wildcard_creates_nothing(self):
        config(repos=["o/*"])
        st = json.loads(self.state.read_text()); st["login"] = "someone-else"; self.state.write_text(json.dumps(st))
        code, out = self.orch("land", self.SLUG, "--repo", "o/r", "--pr", str(self.n))
        self.assertNotEqual(code, 0, out)
        self.assertIn("own repos (someone-else) only", out)
        self.assertFalse((prog.HOME / self.SLUG).exists())

    def test_an_excluded_repo_creates_nothing(self):
        config(repos=["o/*"], exclude=["o/r"])
        code, out = self.orch("land", self.SLUG, "--repo", "o/r", "--pr", str(self.n))
        self.assertNotEqual(code, 0, out)
        self.assertIn("land_executor is off for o/r", out)
        self.assertFalse((prog.HOME / self.SLUG).exists())

    def test_a_renamed_repo_gets_its_current_name(self):
        config(repos=["o/*"])
        self.github(o__old={"nameWithOwner": "o/r"})
        code, out = self.orch("land", "old-standalone", "--repo", "o/old", "--pr", str(self.n))
        self.assertNotEqual(code, 0, out)
        self.assertIn("o/old is o/r on GitHub now", out)
        self.assertIn(f"orch land {self.SLUG} --repo o/r", out)
        self.assertFalse((prog.HOME / "old-standalone").exists())
        code, out = self.orch("verdict", self.SLUG, "--repo", "o/old", "--pr", str(self.n), "--sha", f"h{self.n}",
                              "--source", "review:codex", "--evidence", "https://example.test/review/1")
        self.assertEqual(self.cfg()["repo"], "o/r", out)
        self.assertEqual(code, 0, "the old name reaches the program made under the new one: " + out)
        code, out = self.orch("land", self.SLUG, "--repo", "o/old", "--pr", str(self.n))
        self.assertIn("queued land request", out)
        self.github(o__old={"nameWithOwner": "o/elsewhere"})
        code, out = self.orch("land", self.SLUG, "--repo", "o/old", "--pr", str(self.n))
        self.assertIn("is for o/r, not o/old", out, "an old name that now leads elsewhere is still refused")

    def test_an_explicit_entry_under_the_old_name_does_not_cover_the_new_one(self):
        config(repos=["o/old"])
        self.github(o__old={"nameWithOwner": "o/r"})
        code, out = self.orch("land", self.SLUG, "--repo", "o/old", "--pr", str(self.n))
        self.assertNotEqual(code, 0, out)
        self.assertIn("o/r on GitHub now", out)
        self.assertFalse((prog.HOME / self.SLUG).exists())

    def test_a_case_variant_of_an_existing_program_is_the_same_repo(self):
        self.orch("land", self.SLUG, "--repo", "o/r", "--pr", str(self.n))
        code, out = self.orch("verdict", self.SLUG, "--repo", "O/R", "--pr", str(self.n), "--sha", f"h{self.n}",
                              "--source", "review:codex", "--evidence", "https://example.test/review/1")
        self.assertEqual(code, 0, out)

    def test_a_fork_the_wildcard_alone_covers_creates_nothing(self):
        for meta in ({"isFork": True}, {"isArchived": True}):
            config(repos=["o/*"])
            self.github(o__r={"nameWithOwner": "o/r", **meta})
            code, out = self.orch("land", self.SLUG, "--repo", "o/r", "--pr", str(self.n))
            self.assertNotEqual(code, 0, out)
            self.assertIn("list it by name", out)
            self.assertFalse((prog.HOME / self.SLUG).exists())
            self.assertNotIn(self.n, [r["pr"] for r in store.land_rows(("queued",))])

    def standing(self, slug, run):
        (prog.HOME / slug).mkdir(parents=True, exist_ok=True)
        (prog.HOME / slug / "program.json").write_text(json.dumps({"repo": f"o/{slug}", "run": run}))
        self.addCleanup(shutil.rmtree, prog.HOME / slug, True)

    def test_the_run_the_other_standalone_programs_share_is_used(self):
        self.standing("a-standalone", "run_orch")
        self.standing("b-standalone", "standalone")
        self.orch("land", self.SLUG, "--repo", "o/r", "--pr", str(self.n))
        self.assertEqual(self.cfg()["run"], "run_orch")
        self.assertNotIn("lead_dispatch", self.cfg(), "a standalone program belongs to no lead")

    def test_disagreeing_runs_give_the_placeholder(self):
        self.standing("a-standalone", "run_orch")
        self.standing("b-standalone", "run_other")
        self.orch("land", self.SLUG, "--repo", "o/r", "--pr", str(self.n))
        self.assertEqual(self.cfg()["run"], prog.STANDALONE_RUN)

    def test_an_explicit_run_wins(self):
        self.standing("a-standalone", "run_orch")
        self.orch("land", self.SLUG, "--repo", "o/r", "--pr", str(self.n), "--run", "run_given")
        self.assertEqual(self.cfg()["run"], "run_given")


class Drift(unittest.TestCase):
    def setUp(self):
        self.state = ROOT / "gh-drift.json"
        os.environ["FAKE_GH_STATE"] = str(self.state)
        self.write()

    def write(self, **st):
        self.state.write_text(json.dumps({"prs": {}, "stacks": [], "login": "o", **st}))

    def program(self, slug, repo):
        (prog.HOME / slug).mkdir(parents=True, exist_ok=True)
        (prog.HOME / slug / "program.json").write_text(json.dumps({"repo": repo}))
        self.addCleanup(shutil.rmtree, prog.HOME / slug, True)

    def test_executor_off_prints_nothing(self):
        config(enabled=False)
        self.assertEqual(land_exec.drift_lines(), [])

    def test_an_owned_repo_with_recent_prs_outside_the_scope_is_warned_once(self):
        config(repos=["o/r"], exclude=["o/kept-out"])
        self.program("r-standalone", "o/r")
        self.write(search=["o/new", "o/new", "O/New", "o/kept-out", "someone/else", "o/r"])
        lines = land_exec.drift_lines()
        self.assertEqual(len([l for l in lines if "범위 밖" in l]), 1, lines)
        self.assertIn("o/new", lines[0])
        self.assertFalse(any("kept-out" in l or "someone/else" in l for l in lines), lines)

    def test_covered_repos_without_their_standalone_program_are_one_line(self):
        config(repos=["o/*", "o/listed"], exclude=["o/kept-out"])
        self.program("w-other", "o/w")  # a program on the repo that is not its standalone one
        self.program("has-standalone", "o/has")
        # a covered repo with no recent PR counts too: OWNER/* covers the owner's whole listing
        self.write(search=["o/w"], owned={"o": ["o/w", "o/quiet", "o/kept-out", "o/has"]})
        lines = land_exec.drift_lines()
        self.assertEqual(lines, ["경고: 범위 안인데 <name>-standalone program이 없는 repo 3개: o/listed, o/quiet, o/w"
                                 " (`orch verdict|land <name>-standalone --repo OWNER/NAME`가 처음 부를 때 만든다)"])

    def test_an_excluded_named_entry_is_not_warned(self):
        config(repos=["o/*", "o/r"], exclude=["o/r"])
        self.write(owned={"o": ["o/r"]})
        self.assertEqual(land_exec.drift_lines(), [])

    def test_a_standalone_program_on_its_repo_silences_the_warning(self):
        config(repos=["o/*"])
        self.program("quiet-standalone", "o/quiet")
        self.write(owned={"o": ["o/quiet"]})
        self.assertEqual(land_exec.drift_lines(), [])

    def test_an_unanswered_owner_listing_is_one_line_and_the_rest_still_runs(self):
        config(repos=["o/*"])
        self.write(search=["p/x"], list_fail=True, login="o")
        lines = land_exec.drift_lines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("o/*의 repo 목록", lines[0])

    def test_a_renamed_entry_is_warned(self):
        config(repos=["o/old"])
        self.write(repos={"o/old": {"nameWithOwner": "o/renamed"}})
        lines = land_exec.drift_lines()
        self.assertTrue(any("o/old는 GitHub에서 o/renamed로 이름이 바뀌어" in l for l in lines), lines)

    def test_a_rename_the_wildcard_still_covers_is_not_warned(self):
        config(repos=["o/*", "o/old"])
        self.program("renamed-standalone", "o/renamed")
        self.write(repos={"o/old": {"nameWithOwner": "o/renamed"}}, owned={"o": ["o/renamed"]})
        self.assertEqual(land_exec.drift_lines(), [])

    def test_an_org_wildcard_is_warned_and_not_listed(self):
        config(repos=["acme/*", "acme/app"])
        self.write(list_fail=True, repos={"acme/app": {"nameWithOwner": "acme/app"}})
        lines = land_exec.drift_lines()
        self.assertTrue(any("acme/*는 gh 계정(o)이 아니어서" in l for l in lines), lines)
        self.assertFalse(any("repo 목록" in l for l in lines), "an org's listing is never asked for")
        self.assertTrue(any("acme/app" in l and "program이 없는" in l for l in lines), "a named org repo stays in")

    def test_an_org_wildcard_never_covers_a_renamed_entry(self):
        config(repos=["acme/old", "acme/*"])
        self.write(list_fail=True, repos={"acme/old": {"nameWithOwner": "acme/new"}})
        lines = land_exec.drift_lines()
        self.assertTrue(any("acme/old는 GitHub에서 acme/new로 이름이 바뀌어" in l for l in lines), lines)
        self.assertFalse(any("program이 없는" in l for l in lines), lines)

    def test_github_not_answering_is_one_line(self):
        config(repos=["o/*"])
        self.write(user_fail=True)
        lines = land_exec.drift_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("점검하지 못함", lines[0])

    def test_a_malformed_scope_is_one_line(self):
        config(repos=["*/*"])
        lines = land_exec.drift_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("설정을 읽지 못해", lines[0])


class AutoQueue(Fixture):
    """orchestrate.land_executor.auto_queue: ready PRs get a request without anyone calling `orch land`."""

    def setUp(self):
        super().setUp()
        self.head = f"head{self.n:04d}x"
        self.sticky = f"{RESULTS}\nhead {self.head}"
        self.edit_pr(headRefOid=self.head, comments=[self.sticky])
        self.mode("on")
        self.vrd()

    def mode(self, mode, **more):
        config(auto_queue={"mode": mode, **more} if mode else None)

    def vrd(self, pr_=None, sha=None, **over):
        """A verdict without `ready`: nobody has called `orch land` for this PR."""
        n = pr_ or self.n
        row = {"pr": n, "sha": sha or (self.head if n == self.n else f"head{n:04d}x"), "patch_id": pid(n),
               "source": "review:codex", "result": "pass", "author": ["term_author"], "reviewer": "codex",
               "evidence": "https://example.test/review/1", "recorder": "term_orchestrator", **over}
        self.p.append("verdict", **row)

    def add_pr(self, m):
        st = self.gh()
        st["prs"][str(m)] = dict(pr(m), headRefOid=f"head{m:04d}x", comments=[f"{RESULTS}\nhead head{m:04d}x"])
        self.write(st)
        self.vrd(m)

    def auto(self):
        land_exec.auto_round()

    def mine(self):
        return [r for r in store.land_rows() if r["program"] == self.slug]

    def events(self, ev):
        return [e for e in self.p.events() if e["ev"] == ev and e.get("pr") == self.n]

    def log(self):
        return land_exec.LOG.read_text() if land_exec.LOG.exists() else ""

    def report(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(land_exec.cmd_auto([]), 0)
        return buf.getvalue()

    def skipped(self, why):
        self.auto()
        self.assertEqual(self.mine(), [], "a PR that is not ready must not be requested")
        self.assertIn(why, self.report())

    # -- the setting

    def test_the_setting_is_off_unless_it_says_otherwise(self):
        for raw in (None, {}, {"mode": "yes"}, {"mode": "on", "max_new_per_round": 0},
                    {"mode": "on", "max_new_per_round": "2"}, {"mode": "on", "max_new_per_round": True}, "on", 7):
            self.assertEqual(autoqueue.config(raw)["mode"], "off", raw)
        self.assertEqual(autoqueue.config({"mode": "on"}), {"mode": "on", "max_new_per_round": 1})
        self.assertEqual(autoqueue.config({"mode": "shadow", "max_new_per_round": 3}),
                         {"mode": "shadow", "max_new_per_round": 3})
        self.mode(None)
        self.auto()
        self.mode("off")
        self.auto()
        self.assertEqual((self.mine(), self.events("ready")), ([], []))

    def test_shadow_logs_what_on_would_do_and_writes_nothing(self):
        self.mode("shadow")
        self.auto()
        self.assertEqual((self.mine(), self.events("ready")), ([], []))
        self.assertIn(f"would queue o/r#{self.n} {self.head[:8]}", self.log())

    def test_the_report_never_writes_even_in_mode_on(self):
        out = self.report()
        self.assertIn("auto_queue is on", out)
        self.assertIn(f"would queue o/r#{self.n}", out)
        self.assertEqual((self.mine(), self.events("ready")), ([], []))

    def test_a_repo_outside_the_executor_list_is_left_alone(self):
        config(repos=["o/other"], auto_queue={"mode": "on"})
        self.auto()
        self.assertEqual(self.mine(), [])

    # -- queueing

    def test_on_requests_the_head_and_the_round_lands_it(self):
        self.auto()
        [row] = self.mine()
        self.assertEqual((row["sha"], row["status"], row["klass"]), (self.head, "queued", "normal"))
        self.assertEqual(len(self.events("ready")), 1, "the same `ready` event `orch land` writes")
        self.assertIn(f"queued o/r#{self.n}", self.log())
        self.assertEqual(self.merges(), [], "queueing merges nothing")
        self.round()
        self.assertEqual(store.land_get(row["id"])["status"], "merged")
        self.assertEqual(len(self.merges()), 1)

    def test_a_live_land_call_keeps_its_ready_event(self):
        self.p.append("ready", pr=self.n, ticket="T-1", klass="urgent")
        self.auto()
        [row] = self.mine()
        self.assertEqual(len(self.events("ready")), 1)
        self.assertEqual(row["klass"], "urgent", "the class the agent gave stays")

    def test_each_head_is_requested_once(self):
        self.auto()
        [row] = self.mine()
        self.assertTrue(store.land_claim(row["id"]))
        store.land_finish(row["id"], "refused", 3, "the executor refused it")
        self.auto()
        self.assertEqual(len(self.mine()), 1, "a refused request is not asked for again at the same head")
        self.assertIn("a land request for this head exists already", self.report())

    def test_a_pr_the_executor_stopped_waits_for_a_new_verdict(self):
        self.auto()
        [row] = self.mine()
        self.assertTrue(store.land_claim(row["id"]))
        store.land_finish(row["id"], "refused", 1, "refused")
        prog.stop_land(self.p, self.n, "refused")
        self.auto()
        self.assertEqual(len(self.mine()), 1)
        self.assertTrue(autoqueue.withdrawn(self.p.events(), self.n))
        self.vrd()  # a review at the same head, recorded again
        self.assertFalse(autoqueue.withdrawn(self.p.events(), self.n))
        self.auto()
        self.assertEqual(len(self.mine()), 1, "the earlier request for this head still counts")
        new = f"{self.head}2"  # a new head with its own verdict
        self.edit_pr(headRefOid=new, comments=[f"{RESULTS}\nhead {new}"])
        self.vrd(sha=new)
        self.auto()
        self.assertEqual([r["sha"] for r in self.mine()][-1], new)

    def test_a_stop_without_a_request_is_a_withdrawal_too(self):
        self.p.append("land_check", pr=self.n, outcome="stopped", note="a person stopped it")
        self.skipped("its land was stopped")
        self.vrd()
        self.auto()
        self.assertEqual(len(self.mine()), 1)

    def test_at_most_max_new_per_round_new_requests(self):
        self.add_pr(self.n + 1000)
        self.mode("on", max_new_per_round=1)
        self.auto()
        self.assertEqual(len(self.mine()), 1)
        self.assertIn("over max_new_per_round 1", self.log())
        self.auto()
        self.assertEqual(sorted(r["pr"] for r in self.mine()), [self.n, self.n + 1000])
        self.mode("on", max_new_per_round=5)
        self.auto()
        self.assertEqual(len(self.mine()), 2, "nothing is requested twice")

    # -- what keeps a PR out

    def test_no_verdict(self):
        st = self.gh()
        st["prs"][str(self.n + 2000)] = dict(pr(self.n + 2000), headRefOid="head-no-verdict", comments=[])
        self.write(st)
        self.p.append("ready", pr=self.n + 2000, ticket="T-x")
        self.auto()
        self.assertEqual([r for r in self.mine() if r["pr"] == self.n + 2000], [])
        self.assertIn("is not a pass", self.report())

    def test_a_verdict_for_an_older_head(self):
        self.edit_pr(headRefOid=f"{self.head}2", comments=[f"{RESULTS}\nhead {self.head}2"])
        self.skipped("is not a pass")

    def test_a_failed_verdict_a_foreign_source_and_a_self_review(self):
        for over in ({"result": "fail"}, {"source": "review:human"}, {"reviewer": "author"}):
            self.vrd(**over)
            self.skipped("verdict")

    def test_failed_and_pending_ci(self):
        self.edit_pr(statusCheckRollup=[{"name": "check", "status": "COMPLETED", "conclusion": "FAILURE"}])
        self.skipped("ci_failed")
        self.edit_pr(statusCheckRollup=[{"name": "check", "status": "IN_PROGRESS", "conclusion": ""}])
        self.skipped("ci_pending")
        self.edit_pr(statusCheckRollup=[])
        self.skipped("ci_pending")

    def test_the_test_results_comment_must_name_this_head(self):
        self.edit_pr(comments=[RESULTS])
        self.skipped("no test-results comment names this head")
        self.edit_pr(comments=[f"{RESULTS}\nhead head9999x"])
        self.skipped("no test-results comment names this head")
        self.edit_pr(comments=[f"no marker, but head {self.head}"])
        self.skipped("no test-results comment names this head")

    def test_a_draft_a_conflict_and_a_blocked_merge_state(self):
        self.edit_pr(isDraft=True)
        self.skipped("not_open")
        self.edit_pr(isDraft=False, mergeStateStatus="DIRTY")
        self.skipped("dirty")
        self.edit_pr(mergeStateStatus="BLOCKED")
        self.skipped("merge state BLOCKED")

    def test_a_stop_line(self):
        self.p.append("stop", why="test")
        self.skipped("stopped")
        self.p.append("resume")
        self.auto()
        self.assertEqual(len(self.mine()), 1)

    def test_a_program_that_is_not_autonomous(self):
        self.p.update(lambda c: c.update(merge_policy="human-gate"))
        self.skipped("is not an autonomous program")

    def test_two_programs_naming_the_pr_leave_it_to_a_person(self):
        subprocess.run([sys.executable, HERE / "prog.py", "init", f"{self.slug}b", "--repo", "o/r", "--run", "run_x"],
                       check=True, capture_output=True)
        prog.Program(f"{self.slug}b").append("verdict", pr=self.n, sha=self.head, result="pass", source="review:codex")
        self.skipped("2 programs name it")

    def test_a_program_that_cannot_be_read_hides_its_owner(self):
        broken = prog.HOME / f"{self.slug}z"
        broken.mkdir()
        (broken / "program.json").write_text("{not json")
        try:
            self.skipped("a program ledger could not be read")
        finally:
            shutil.rmtree(broken)

    def test_github_not_answering_for_one_pr_skips_only_that_pr(self):
        self.add_pr(self.n + 1000)
        real = prog.gh_json

        def flaky(*args, **kw):
            if args[:3] == ("pr", "view", str(self.n)) and "comments" in args:
                sys.exit("gh: HTTP 502")
            return real(*args, **kw)

        prog.gh_json = flaky
        try:
            self.auto()
            out = self.report()
        finally:
            prog.gh_json = real
        self.assertEqual([r["pr"] for r in self.mine()], [self.n + 1000])
        self.assertIn("GitHub did not answer", out)

    # -- races, unreadable inputs, limits

    def test_a_directory_with_a_ledger_but_no_config_hides_its_owner(self):
        stray = prog.HOME / f"{self.slug}y"
        stray.mkdir()
        (stray / "ledger.jsonl").write_text("")
        try:
            self.skipped("a program ledger could not be read")
        finally:
            shutil.rmtree(stray)
        empty = prog.HOME / f"{self.slug}e"
        empty.mkdir()  # nothing in it: not a program
        try:
            self.auto()
            self.assertEqual(len(self.mine()), 1)
        finally:
            shutil.rmtree(empty)

    def test_a_stop_that_lands_after_the_decision_wins(self):
        self.p.append("land_check", pr=self.n, outcome="stopped", note="a person stopped it")
        row = {"number": self.n, "headRefOid": self.head, "baseRefName": "main"}
        self.assertIsNone(land_exec.queue_pr(self.slug, self.p, "o/r", row))
        self.assertEqual((self.mine(), self.events("ready")), ([], []))

    def test_a_stop_that_lands_during_the_decision_is_not_written_over(self):
        row = {"number": self.n, "headRefOid": self.head, "baseRefName": "main"}
        real = store.land_seen

        def seen_then_stopped(*a):
            got = real(*a)
            self.p.append("land_check", pr=self.n, outcome="stopped", note="a person stopped it")  # after the look
            return got

        store.land_seen = seen_then_stopped
        try:
            self.assertIsNone(land_exec.queue_pr(self.slug, self.p, "o/r", row))
        finally:
            store.land_seen = real
        self.assertEqual((self.mine(), self.events("ready")), ([], []))
        self.assertTrue(autoqueue.withdrawn(self.p.events(), self.n), "the stop still stands")

    def test_a_stop_after_the_request_keeps_the_executor_from_merging(self):
        """ready, then the request, then a stop before the round: the old call must not merge."""
        self.auto()
        [row] = self.mine()
        prog.stop_land(self.p, self.n, "a person withdrew the call")
        self.round()
        got = store.land_get(row["id"])
        self.assertEqual((got["status"], got["code"]), ("refused", 3), got)
        self.assertIn("was stopped after this request", got["reason"])
        self.assertEqual(self.merges(), [])
        self.vrd()  # a review at the head again brings the PR back, with a request of its own
        self.assertFalse(autoqueue.withdrawn(self.p.events(), self.n))

    def test_a_stop_during_the_checks_keeps_the_executor_from_merging(self):
        self.auto()
        [row] = self.mine()
        real = prog.gh_json

        def stop_then_answer(*args, **kw):
            if "comments" in args:  # a check made after the executor's own look at the withdrawal
                prog.stop_land(self.p, self.n, "a person withdrew the call")
            return real(*args, **kw)

        prog.gh_json = stop_then_answer
        try:
            self.round()
        finally:
            prog.gh_json = real
        got = store.land_get(row["id"])
        self.assertEqual((got["status"], got["code"]), ("refused", 3), got)
        self.assertIn("stopped during the checks", got["reason"])
        self.assertEqual(self.merges(), [])

    def test_a_request_for_the_head_is_not_inserted_again_after_the_queue_moved_on(self):
        home = str(prog.HOME)
        put = lambda sha, **kw: store.land_put(self.slug, home, "o/r", self.n, sha, "b", "normal", None, **kw)
        put("A")
        put("B")  # an `orch land` for a newer head superseded A
        before = len(self.mine())
        row, outcome = put("A", only_new=True)
        self.assertEqual((outcome, len(self.mine())), ("seen", before))
        self.assertEqual(put("C", only_new=True)[1], "new")

    def test_the_report_writes_no_log_line_when_a_listing_fails(self):
        st = self.gh()
        st["hard_fail"] = "pr list"
        self.write(st)
        before = self.log()
        out = self.report()
        self.assertIn("the open PR listing failed", out)
        self.assertEqual(self.log(), before)

    def test_a_full_listing_is_said_out_loud(self):
        real, prog.OPEN_LIMIT = prog.OPEN_LIMIT, 1
        try:
            out = self.report()
        finally:
            prog.OPEN_LIMIT = real
        self.assertIn("is the listing's limit", out)

    def test_a_bad_settings_value_or_a_bad_cache_never_stops_the_queue(self):
        row, _ = store.land_put(self.slug, str(self.p.dir.parent.resolve()), "o/r", self.n, self.head, "b", "normal", None)
        config(repos=7, auto_queue={"mode": "on"})
        self.round()  # the settings are unusable: the auto-queue says so and the queue is still walked
        self.assertIn("auto-queue", self.log())
        # A malformed scope is refused by name (land_scope.ScopeError), not crashed on: the queue is still walked.
        self.assertIn("settings cannot be read", store.land_get(row["id"])["reason"])
        self.assertEqual(store.land_get(row["id"])["status"], "refused")
        self.p.append("ready", pr=self.n, ticket=f"T-{self.n}")  # the new `orch land` that makes the request
        row, _ = store.land_put(self.slug, str(self.p.dir.parent.resolve()), "o/r", self.n, self.head, "b", "normal", None)
        config(auto_queue={"mode": "on"})
        for junk in ("null", "1", '[["x"]]', "{not json"):
            land_exec.AUTO_LAST.write_text(junk)
            self.auto()
        self.assertEqual(len(self.mine()), 2)  # the refused row and the new one; the junk cache queued nothing
        land_exec.AUTO_LAST.write_text("[1, null]")
        self.round()  # a damaged cache and a good setting: the queued request is settled
        self.assertEqual(store.land_get(row["id"])["status"], "merged")

    def test_owner_wildcard_covers_the_repo_a_program_records(self):
        config(repos=["o/*"], auto_queue={"mode": "on"})
        self.auto()
        self.assertEqual(len(self.mine()), 1)

    def test_a_renamed_repo_is_still_found_under_the_name_its_program_holds(self):
        config(repos=["o/*"], auto_queue={"mode": "on"})
        st = self.gh()
        st["repos"] = {"o/r": {"nameWithOwner": "o/new", "isFork": False, "isArchived": False}}
        self.write(st)
        self.auto()
        self.assertEqual(len(self.mine()), 1)

    def test_a_repo_renamed_into_scope_is_found_under_the_name_its_program_holds(self):
        config(repos=["o/new"], auto_queue={"mode": "on"})
        st = self.gh()
        st["repos"] = {"o/r": {"nameWithOwner": "o/new", "isFork": False, "isArchived": False}}
        self.write(st)
        self.auto()
        self.assertEqual(len(self.mine()), 1)

    def test_programs_recording_the_same_repo_in_another_case_are_both_found(self):
        other = f"{self.slug}-case"
        subprocess.run([sys.executable, HERE / "prog.py", "init", other, "--repo", "O/R", "--run", "run_y"],
                       check=True, capture_output=True)
        self.addCleanup(shutil.rmtree, prog.HOME / other, True)
        found = {s for s, _, _ in land_exec.programs_on("o/r")[0]}
        self.assertLessEqual({self.slug, other}, found)

    def test_the_request_carries_the_owning_programs_spelling(self):
        # The program that sorts first records O/R and owns no PR; the owner of the PR records o/r.
        other = f"0-{self.slug}"
        subprocess.run([sys.executable, HERE / "prog.py", "init", other, "--repo", "O/R", "--run", "run_y"],
                       check=True, capture_output=True)
        self.addCleanup(shutil.rmtree, prog.HOME / other, True)
        self.auto()
        self.assertEqual([r["repo"] for r in self.mine()], ["o/r"])

    def test_the_programs_spelling_wins_over_a_differently_cased_entry(self):
        config(repos=["O/R", "o/*"], auto_queue={"mode": "on"})
        self.auto()
        self.assertEqual(len(self.mine()), 1)

    def test_a_wildcard_repo_is_left_out_while_its_owner_cannot_be_checked(self):
        config(repos=["o/*"], auto_queue={"mode": "on"})
        st = self.gh()
        st["user_fail"] = True
        self.write(st)
        self.auto()
        self.assertEqual((self.mine(), self.events("ready")), ([], []))
        self.assertIn("gh api user", self.report())

    def test_an_excluded_repo_is_neither_requested_nor_touched(self):
        config(repos=["o/*"], exclude=["o/r"], auto_queue={"mode": "on"})
        self.auto()
        self.assertEqual((self.mine(), self.events("ready")), ([], []))
        self.assertIn("exclude", self.report())
        config(repos=["o/r"], exclude=["o/r"], auto_queue={"mode": "on"})  # named and excluded: exclude wins
        self.auto()
        self.assertEqual((self.mine(), self.events("ready")), ([], []))
        self.assertIn("o/r", self.report() + self.log())  # said, not silent

    def test_a_wildcard_for_another_owner_is_not_expanded(self):
        config(repos=["acme/*"], auto_queue={"mode": "on"})
        st = self.gh()
        st["owned"] = {"acme": ["acme/r"]}
        self.write(st)
        self.auto()
        self.assertEqual(self.mine(), [])

    def test_the_report_survives_a_listing_it_cannot_parse(self):
        real = prog.open_rows
        prog.open_rows = lambda repo: (_ for _ in ()).throw(ValueError("not json"))
        try:
            buf, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                code = land_exec.cmd_auto([])
        finally:
            prog.open_rows = real
        self.assertEqual(code, 1)
        self.assertIn("refused: not json", err.getvalue())
        self.assertEqual(self.mine(), [])


class Setup(unittest.TestCase):
    def test_the_step_is_recorded_once_and_others_do_not_need_it(self):
        self.assertEqual(store.land_setup(), "already set up")
        self.assertIn(store.LAND_STEP, store.migrated_steps())
        self.assertNotIn(store.LAND_STEP, store.NEEDS)

    def test_without_the_step_requests_stop(self):
        with store.tx() as db:
            db.execute("DELETE FROM migration WHERE step=?", (store.LAND_STEP,))
        try:
            with self.assertRaises(store.NotMigrated):
                store.land_put("x", "/h", "o/r", 1, "s", None, "normal", None)
            self.assertEqual(land_exec.cmd_round([]), 1)
        finally:
            with store.tx() as db:
                db.execute("INSERT INTO migration (step, ts, backup, sources, stage) VALUES (?,?,?,?, 'imported')",
                           (store.LAND_STEP, store.now(), "", "[]"))


if __name__ == "__main__":
    unittest.main()
