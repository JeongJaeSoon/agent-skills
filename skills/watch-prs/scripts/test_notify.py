"""Rules of notify.py: filters, once-per-thread keys, personal review requests only, human activity only,
seed, backoff and exit codes.
Run: python3 test_notify.py"""
import pathlib, sys, tempfile, unittest
from unittest import mock
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
    def __init__(self, pages, reviewers=None, fail=0, comments=None, reviews=None, error="HTTP 502", login=ME):
        self.pages, self.reviewers, self.fail, self.calls = list(pages), reviewers or {}, fail, []
        self.error, self.login = error, login
        self.comments, self.reviews = comments or {}, reviews or {}

    def __call__(self, path):
        self.calls.append(path)
        if path == "user":
            if self.fail:
                self.fail -= 1
                raise RuntimeError(self.error)
            return {"login": self.login}
        if path.endswith("/requested_reviewers"):
            users = self.reviewers.get(path, [])
            return {"users": None if users is None else [u and {"login": u} for u in users], "teams": [{"slug": "core"}]}
        if "/reviews?per_page=100" in path:
            page = int(path.split("&page=")[1]) if "&page=" in path else 1
            return [{"user": u} for u in self.reviews.get(path.split("/reviews")[0], [])[(page - 1) * 100:page * 100]]
        if "/comments/" in path:
            if path not in self.comments:
                raise RuntimeError("HTTP 404")
            return {"user": self.comments[path]}
        if self.fail:
            self.fail -= 1
            raise RuntimeError(self.error)
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
        for error, recorded in (("gh: Bad Gateway (HTTP 502)", []), ("gh: Not Found (HTTP 404)", ["12@2026-01-01T00:00:00Z:team"])):
            n.write_seen(self.seen, [], 10)

            class Unreadable(Fake):
                def __call__(self, path):
                    if path == rr_url(12):
                        raise RuntimeError(error)
                    return super().__call__(path)

            code, out = self.run_watch(Unreadable([[note(12, reason="review_requested"), note(13, title="Still here")]]))
            self.assertEqual((code, [o.split("\t")[2] for o in out]), (0, ["Still here"]))
            self.assertEqual([k for k in n.read_seen(self.seen) if k.startswith("12")], recorded)

    def test_reviewer_list_with_null_entries_does_not_crash(self):
        n.write_seen(self.seen, [], 10)
        for i, users in ((14, [None, ME]), (15, None)):
            code, out = self.run_watch(Fake([[note(i, reason="review_requested")]], {rr_url(i): users}), max_minutes=1)
            self.assertEqual(code, 0 if users else 1)

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

    def test_bot_repeating_a_mention_stays_silent_while_a_human_mention_fires(self):
        n.write_seen(self.seen, [], 10)
        comments = {comment_url(50): person("approval-check[bot]", "Bot"), comment_url(51): person("carol")}
        for day in ("01", "02", "03"):
            page = [note(50, title="bot mention", updated=f"2026-01-{day}T00:00:00Z", latest=comment_url(50)),
                    note(51, title="human mention", updated=f"2026-01-{day}T00:00:00Z", latest=comment_url(51))]
            code, out = self.run_watch(Fake([page], comments=comments))
            self.assertEqual((code, [o.split("\t")[2] for o in out]), (0, ["human mention"]))
        self.assertEqual(len([k for k in n.read_seen(self.seen) if k.startswith("50@")]), 3)

    def test_mention_in_a_pr_body_fires_without_a_reviews_call(self):
        n.write_seen(self.seen, [], 10)
        fake = Fake([[note(52, title="body mention", latest="pr")]], reviews={note(52)["subject"]["url"]: [person("ci-runner", "Bot")]})
        code, out = self.run_watch(fake)
        self.assertEqual((code, [o.split("\t")[2] for o in out]), (0, ["body mention"]))
        self.assertFalse([c for c in fake.calls if "/reviews" in c])

    def test_unreadable_activity_is_retried_and_a_deleted_comment_is_recorded(self):
        n.write_seen(self.seen, [], 10)

        class Flaky(Fake):
            def __call__(self, path):
                if path == comment_url(30):
                    raise RuntimeError("HTTP 503")
                return super().__call__(path)

        page = [note(30, reason="comment", latest=comment_url(30)), note(31, title="Still here"),
                note(34, reason="comment", latest=comment_url(34))]
        code, out = self.run_watch(Flaky([page]))
        self.assertEqual((code, [o.split("\t")[2] for o in out]), (0, ["Still here"]))
        seen = n.read_seen(self.seen)
        self.assertFalse([k for k in seen if k.startswith("30")])
        self.assertTrue([k for k in seen if k.startswith("34@")])

    def test_latest_review_is_read_from_the_last_page(self):
        n.write_seen(self.seen, [], 10)
        pr = note(35)["subject"]["url"]
        reviews = {pr: [person("review-helper[bot]", "Bot")] * 99 + [person("carol")] + [person("ci-runner", "Bot")] * 50}
        fake = Fake([[note(35, reason="author", latest="pr")]], reviews=reviews)
        self.assertEqual(self.run_watch(fake, max_minutes=1), (1, []))
        self.assertEqual([c for c in fake.calls if "/reviews" in c], [pr + "/reviews?per_page=100", pr + "/reviews?per_page=100&page=2"])
        reviews[pr] = reviews[pr][:100]
        code, out = self.run_watch(Fake([[note(35, reason="author", latest="pr", updated="2026-01-02T00:00:00Z")]], reviews=reviews))
        self.assertEqual((code, len(out)), (0, 1))

    def test_issue_thread_pointing_at_itself_is_silent_without_a_reviews_call(self):
        n.write_seen(self.seen, [], 10)
        issue = note(40, reason="comment")
        issue["subject"]["url"] = issue["subject"]["latest_comment_url"] = "https://api.github.com/repos/acme/web/issues/40"
        fake = Fake([[issue]])
        self.assertEqual(self.run_watch(fake, max_minutes=1), (1, []))
        self.assertFalse([c for c in fake.calls if "/reviews" in c])
        self.assertTrue([k for k in n.read_seen(self.seen) if k.startswith("40@")])

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

    def test_repeated_state_change_fires_once(self):
        n.write_seen(self.seen, [], 10)
        code, out = self.run_watch(Fake([[note(16, reason="state_change")]]))
        self.assertEqual((code, len(out)), (0, 1))
        again = [note(16, reason="state_change", updated="2026-01-03T00:00:00Z")]
        self.assertEqual(self.run_watch(Fake([again]), max_minutes=1), (1, []))

    def test_transient_failures_back_off_exponentially_then_recover(self):
        n.write_seen(self.seen, [], 10)
        code, out = self.run_watch(Fake([[note(11)]], fail=3))
        self.assertEqual((code, len(out)), (0, 1))
        self.assertEqual(self.clock.t, 60 + 120 + 240)

    def test_outage_and_rate_limit_never_exit_2(self):
        for error in ("gh: Bad Gateway (HTTP 502)", "gh: API rate limit exceeded for user ID 1. (HTTP 403)",
                      'Get "https://api.github.com/user": dial tcp: lookup api.github.com: no such host'):
            self.clock.t = 0.0
            code, out = self.run_watch(Fake([[]], fail=999, error=error), max_failures=3, max_minutes=120)
            self.assertEqual((code, out), (1, []), error)

    def test_backoff_is_capped(self):
        n.write_seen(self.seen, [], 10)
        sleeps = []
        n.watch(Fake([[note(17)]], fail=8), CFG, self.seen, sleep=sleeps.append, clock=self.clock)
        self.assertEqual(sleeps, [60, 120, 240, 480, 900, 900, 900, 900])

    def test_persistent_config_failure_exits_2_with_the_error(self):
        n.write_seen(self.seen, [], 10)
        code, out = self.run_watch(Fake([[]], fail=99, error="gh: Bad credentials (HTTP 401)"), max_failures=3)
        self.assertEqual(code, 2)
        self.assertIn("HTTP 401", out[0])

    def test_startup_waits_out_an_offline_user_lookup_and_an_empty_login_counts(self):
        code, out = self.run_watch(Fake([[note(18)], [note(18), note(19, title="After")]], fail=2, error="EOF"))
        self.assertEqual((code, [o.split("\t")[2] for o in out]), (0, ["After"]))
        code, out = self.run_watch(Fake([[]], login=""), max_failures=2)
        self.assertEqual(code, 2)
        self.assertIn("no login", out[0])

    def test_a_crash_mid_write_keeps_the_old_seen_file(self):
        n.write_seen(self.seen, ["a", "b"], 10)

        def torn(path, text):
            open(path, "w").write(text[:1])
            raise OSError("disk full")

        with mock.patch.object(pathlib.Path, "write_text", torn), self.assertRaises(OSError):
            n.write_seen(self.seen, ["c", "d"], 10)
        self.assertEqual(n.read_seen(self.seen), ["a", "b"])

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
