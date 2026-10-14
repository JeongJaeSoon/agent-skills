"""Rules of notify.py: filters, once-per-thread keys, personal review requests only, human activity only,
seed, exit codes.
Run: python3 test_notify.py"""
import pathlib, sys, tempfile, unittest
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import notify as n

ME = "owner"
CFG = {**n.DEFAULTS, **n.SCOPE}


def note(i, reason="mention", repo="acme/web", title="Fix login", updated="2026-01-01T00:00:00Z", num=None, latest=None):
    url = f"https://api.github.com/repos/{repo}/pulls/{num or i}"
    return {"id": str(i), "reason": reason, "updated_at": updated, "repository": {"full_name": repo},
            "subject": {"title": title, "url": url, "latest_comment_url": url if latest == "pr" else latest}}


def comment_url(c, repo="acme/web"):
    return f"https://api.github.com/repos/{repo}/issues/comments/{c}"


def person(login, kind="User"):
    return {"login": login, "type": kind}


class Fake:
    """GitHub as a list of notification pages, one per poll, the reviewers requested on each PR,
    comment authors by comment URL and review authors by PR URL."""
    def __init__(self, pages, reviewers=None, fail=0, comments=None, reviews=None):
        self.pages, self.reviewers, self.fail, self.calls = list(pages), reviewers or {}, fail, []
        self.comments, self.reviews = comments or {}, reviews or {}

    def __call__(self, path):
        self.calls.append(path)
        if path == "user":
            return {"login": ME}
        if path.endswith("/requested_reviewers"):
            return {"users": [{"login": u} for u in self.reviewers.get(path, [])], "teams": [{"slug": "core"}]}
        if path.endswith("/reviews?per_page=100"):
            return [{"user": u} for u in self.reviews.get(path.split("/reviews")[0], [])]
        if "/comments/" in path:
            if path not in self.comments:
                raise RuntimeError("HTTP 404")
            return {"user": self.comments[path]}
        if self.fail:
            self.fail -= 1
            raise RuntimeError("HTTP 502")
        return self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]


def rr_url(i, repo="acme/web"):
    return f"https://api.github.com/repos/{repo}/pulls/{i}/requested_reviewers"


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class NotifyTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.seen = pathlib.Path(self.dir.name) / "prs" / "notify-seen.txt"
        self.clock = Clock()

    def tearDown(self):
        self.dir.cleanup()

    def run_watch(self, fake, **cfg):
        return n.watch(fake, {**CFG, **cfg}, self.seen, sleep=self.clock.sleep, clock=self.clock)

    def test_first_run_seeds_the_backlog_then_prints_only_new_items(self):
        fake = Fake([[note(1)], [note(1)], [note(1), note(2, title="New thing")]])
        code, out = self.run_watch(fake)
        self.assertEqual(code, 0)
        self.assertEqual(out, ["mention\tacme/web\tNew thing\thttps://github.com/acme/web/pull/2"])

    def test_filters_reason_title_and_repo_scope(self):
        n.write_seen(self.seen, [], 10)
        page = [note(1, reason="subscribed"), note(2, title="[Auto] sync"), note(3, title="chore(deps): bump x"),
                note(4, title="Bump y from 1 to 2"), note(5, repo="other/lib"), note(6, title="Real one")]
        code, out = self.run_watch(Fake([page]), exclude_orgs=["other"])
        self.assertEqual((code, [o.split("\t")[2] for o in out]), (0, ["Real one"]))

    def test_comment_fires_again_when_the_thread_updates(self):
        n.write_seen(self.seen, [], 10)
        comments = {comment_url(1): person("carol")}
        self.run_watch(Fake([[note(1, reason="comment", latest=comment_url(1))]], comments=comments))
        again = [note(1, reason="comment", updated="2026-01-02T00:00:00Z", latest=comment_url(1))]
        code, out = self.run_watch(Fake([again], comments=comments))
        self.assertEqual((code, len(out)), (0, 1))

    def test_repeated_personal_review_request_fires_once(self):
        n.write_seen(self.seen, [], 10)
        reviewers = {rr_url(7): [ME]}
        code, out = self.run_watch(Fake([[note(7, reason="review_requested")]], reviewers))
        self.assertEqual((code, len(out)), (0, 1))
        again = [note(7, reason="review_requested", updated="2026-01-01T00:01:00Z")]
        self.assertEqual(self.run_watch(Fake([again], reviewers), max_minutes=1), (1, []))

    def test_team_review_request_never_fires_and_is_not_rechecked_until_re_requested(self):
        n.write_seen(self.seen, [], 10)
        team_only = {rr_url(8): ["carol"]}
        fake = Fake([[note(8, reason="review_requested")]], team_only)
        self.assertEqual(self.run_watch(fake, max_minutes=2), (1, []))
        self.assertEqual(sum(c.endswith("/requested_reviewers") for c in fake.calls), 1)
        re_requested = [note(8, reason="review_requested", updated="2026-01-01T00:01:00Z")]
        fake = Fake([re_requested], team_only)
        self.assertEqual(self.run_watch(fake, max_minutes=1), (1, []))
        self.assertEqual(sum(c.endswith("/requested_reviewers") for c in fake.calls), 1)

    def test_team_request_later_made_personal_fires_once(self):
        n.write_seen(self.seen, [], 10)
        self.run_watch(Fake([[note(9, reason="review_requested")]], {rr_url(9): []}), max_minutes=1)
        later = [note(9, reason="review_requested", updated="2026-01-01T00:05:00Z")]
        code, out = self.run_watch(Fake([later], {rr_url(9): [ME]}))
        self.assertEqual((code, len(out)), (0, 1))

    def test_unreadable_review_request_is_skipped_without_blocking_other_items(self):
        n.write_seen(self.seen, [], 10)

        class Gone(Fake):
            def __call__(self, path):
                if path == rr_url(12):
                    raise RuntimeError("HTTP 404")
                return super().__call__(path)

        code, out = self.run_watch(Gone([[note(12, reason="review_requested"), note(13, title="Still here")]]))
        self.assertEqual((code, [o.split("\t")[2] for o in out]), (0, ["Still here"]))
        self.assertFalse([k for k in n.read_seen(self.seen) if k.startswith("12")])

    def test_activity_wakes_only_for_a_human_other_than_the_user(self):
        n.write_seen(self.seen, [], 10)
        page = [note(20, reason="author", title="own push"),
                note(21, reason="author", title="own comment", latest=comment_url(21)),
                note(22, reason="author", title="typed bot", latest=comment_url(22)),
                note(23, reason="comment", title="named bot", latest=comment_url(23)),
                note(24, reason="comment", title="human comment", latest=comment_url(24)),
                note(25, reason="author", title="bot review", latest="pr"),
                note(26, reason="author", title="human review", latest="pr"),
                note(27, reason="author", title="no reviews", latest="pr")]
        comments = {comment_url(21): person(ME), comment_url(22): person("ci-runner", "Bot"),
                    comment_url(23): person("review-helper[bot]"), comment_url(24): person("carol")}
        pr = "https://api.github.com/repos/acme/web/pulls/"
        reviews = {pr + "25": [person("carol"), person("review-helper[bot]", "Bot")], pr + "26": [person("dave")]}
        fake = Fake([page], comments=comments, reviews=reviews)
        code, out = self.run_watch(fake)
        self.assertEqual((code, [o.split("\t")[2] for o in out]), (0, ["human comment", "human review"]))
        self.assertEqual(len(n.read_seen(self.seen)), len(page))

    def test_unreadable_activity_is_skipped_without_blocking_other_items(self):
        n.write_seen(self.seen, [], 10)
        page = [note(30, reason="comment", latest=comment_url(30)), note(31, title="Still here")]
        code, out = self.run_watch(Fake([page]))
        self.assertEqual((code, [o.split("\t")[2] for o in out]), (0, ["Still here"]))
        self.assertFalse([k for k in n.read_seen(self.seen) if k.startswith("30")])

    def test_human_activity_only_off_passes_pushes_without_lookups(self):
        n.write_seen(self.seen, [], 10)
        fake = Fake([[note(32, reason="author"), note(33, reason="comment", latest="pr")]])
        code, out = self.run_watch(fake, human_activity_only=False)
        self.assertEqual((code, len(out)), (0, 2))
        self.assertEqual(fake.calls, ["user", "notifications?per_page=50"])

    def test_personal_review_only_off_passes_team_requests(self):
        n.write_seen(self.seen, [], 10)
        code, out = self.run_watch(Fake([[note(10, reason="review_requested")]]), personal_review_only=False)
        self.assertEqual((code, len(out)), (0, 1))

    def test_transient_failures_back_off_then_recover(self):
        n.write_seen(self.seen, [], 10)
        code, out = self.run_watch(Fake([[note(11)]], fail=3))
        self.assertEqual((code, len(out)), (0, 1))
        self.assertEqual(self.clock.t, 3 * CFG["backoff_s"])

    def test_persistent_failure_exits_2_with_the_error(self):
        n.write_seen(self.seen, [], 10)
        code, out = self.run_watch(Fake([[]], fail=99), max_failures=3)
        self.assertEqual(code, 2)
        self.assertIn("HTTP 502", out[0])

    def test_seed_records_every_notification_and_seen_is_trimmed(self):
        self.assertEqual(n.seed(Fake([[note(i) for i in range(5)]]), self.seen, {**CFG, "keep_seen": 3}), 5)
        self.assertEqual(len(n.read_seen(self.seen)), 3)

    def test_config_needs_the_prs_key_and_merges_notify_over_defaults(self):
        p = pathlib.Path(self.dir.name) / "c.json"
        p.write_text('{"other": {}}')
        self.assertIsNone(n.config(p))
        p.write_text('{"prs": {"exclude_repos": ["acme/x"], "notify": {"interval_s": 60}}}')
        c = n.config(p)
        self.assertEqual((c["interval_s"], c["exclude_repos"], c["personal_review_only"], c["human_activity_only"]),
                         (60, ["acme/x"], True, True))


if __name__ == "__main__":
    unittest.main()
