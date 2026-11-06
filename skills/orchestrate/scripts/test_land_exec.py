"""The land executor (land_exec.py) and `orch land`'s request path against a fake gh and orca. Run: python3 test_land_exec.py"""
import ast, json, os, pathlib, shutil, subprocess, sys, tempfile, unittest

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
if a[:2] == ["terminal", "list"]:  # the card holding the PR's branch: orch verdict records it as the author
    print(json.dumps({"terminals": [{"handle": "term_author"}]})); sys.exit()
os.execv(sys.executable, [sys.executable, os.environ["FAKE_ORCA_INNER"], *a])
'''
BIN.mkdir(parents=True)
for name, text in (("gh", WRAP_GH), ("orca", WRAP_ORCA), ("gh-inner", FAKES["FAKE_GH"]), ("orca-inner", FAKES["FAKE_ORCA"])):
    (BIN / name).write_text(text)
    (BIN / name).chmod(0o755)
os.environ.update(FAKE_GH_INNER=str(BIN / "gh-inner"), FAKE_ORCA_INNER=str(BIN / "orca-inner"))

sys.path.insert(0, str(HERE))
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


class LandExec(unittest.TestCase):
    seq = 0

    def setUp(self):
        drain()
        LandExec.seq += 1
        self.slug = f"p{self.seq}"
        self.n = self.seq
        self.state = ROOT / f"gh-{self.slug}.json"
        self.write({"prs": {str(self.n): pr(self.n)}, "stacks": []})
        os.environ["FAKE_GH_STATE"] = str(self.state)
        config()
        subprocess.run([sys.executable, HERE / "prog.py", "init", self.slug, "--repo", "o/r", "--run", "run_x"],
                       check=True, capture_output=True)
        self.p = prog.Program(self.slug)
        self.verdict()

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
                LandExec.seq += 1
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
                LandExec.seq += 1
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

    def test_author_sessions_is_empty_when_orca_cannot_vouch_for_the_list(self):
        real = prog.orca_json
        try:
            for answer in ({"terminals": [{"handle": "term_other"}], "truncated": True}, {"terminals": None}, {}):
                prog.orca_json = lambda *a, answer=answer: answer
                self.assertEqual(prog.author_sessions("b"), [], answer)
            prog.orca_json = lambda *a: {"terminals": [{"handle": "term_b"}, {"handle": "term_a"}, {}]}
            self.assertEqual(prog.author_sessions("b"), ["term_a", "term_b"])
            prog.orca_json = lambda *a: (_ for _ in ()).throw(ValueError("not json"))
            self.assertEqual(prog.author_sessions("b"), [])
        finally:
            prog.orca_json = real

    def record(self, *extra, handle="term_orchestrator", authors=("term_author",)):
        """cmd_verdict as a session with `handle` against a PR whose card is `authors`; returns the verdict rows added."""
        before = len([e for e in self.p.events() if e["ev"] == "verdict"])
        os.environ["ORCA_TERMINAL_HANDLE"] = handle
        real = prog.author_sessions
        prog.author_sessions = lambda branch: list(authors)
        try:
            prog.cmd_verdict([self.slug, "--pr", str(self.n), "--sha", f"h{self.n}", *extra])
        finally:
            prog.author_sessions = real
            os.environ.pop("ORCA_TERMINAL_HANDLE")
            self.added = len([e for e in self.p.events() if e["ev"] == "verdict"]) - before

    def test_a_pass_without_reviewer_or_evidence_is_refused_and_not_stored(self):
        for extra, authors in ((["--source", "independent-review", "--evidence", "e"], ("term_author",)),  # no reviewer
                               (["--source", "review:codex"], ("term_author",)),  # no evidence
                               (["--source", "review:codex", "--evidence", "e"], ())):  # no author session found
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
        LandExec.seq += 1
        self.slug, self.n = f"r{LandExec.seq}", LandExec.seq
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
        LandExec.seq += 1
        self.n = LandExec.seq + 100
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
