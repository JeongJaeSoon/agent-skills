"""land_scope.py: OWNER/* and OWNER/NAME entries, exclude, and what is refused rather than widened.
Run: python3 test_land_scope.py"""
import pathlib, sys, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import land_scope as ls  # noqa: E402


class Scope(unittest.TestCase):
    def test_a_plain_list_matches_as_before(self):
        s = ls.parse({"repos": ["acme/app", "acme/web"]})
        self.assertEqual(ls.how(s, "acme/app"), "name")
        self.assertIsNone(ls.how(s, "acme/other"))
        self.assertIsNone(ls.how(s, "other/app"))

    def test_owner_wildcard_covers_every_repo_of_that_owner_only(self):
        s = ls.parse({"repos": ["acme/*"]})
        self.assertEqual(ls.how(s, "acme/new-repo"), "owner")
        self.assertIsNone(ls.how(s, "acmex/app"), "an owner is matched whole, never by prefix")
        self.assertIsNone(ls.how(s, "other/acme"))

    def test_names_are_case_insensitive(self):
        s = ls.parse({"repos": ["Acme/*", "Other/App"]})
        self.assertEqual(ls.how(s, "acme/x"), "owner")
        self.assertEqual(ls.how(s, "other/app"), "name")

    def test_exclude_wins_over_the_wildcard_and_a_name(self):
        s = ls.parse({"repos": ["acme/*", "acme/app"], "exclude": ["acme/app", "ACME/legacy"]})
        self.assertIsNone(ls.how(s, "acme/app"))
        self.assertIsNone(ls.how(s, "acme/Legacy"))
        self.assertTrue(ls.excluded(s, "acme/legacy"))
        self.assertEqual(ls.how(s, "acme/web"), "owner")

    def test_a_name_beside_the_wildcard_says_name(self):
        s = ls.parse({"repos": ["acme/*", "acme/fork"]})
        self.assertEqual(ls.how(s, "acme/fork"), "name")

    def test_other_wildcards_and_shapes_are_errors_not_wider_matches(self):
        for bad in ("*", "*/*", "*/app", "acme/app-*", "acme", "acme/app/x", "/app", "acme/", "ac me/app", ""):
            with self.subTest(bad=bad), self.assertRaises(ls.ScopeError):
                ls.parse({"repos": [bad]})
        for bad in ("acme/*", "acme", "*/*"):
            with self.subTest(exclude=bad), self.assertRaises(ls.ScopeError):
                ls.parse({"repos": ["acme/*"], "exclude": [bad]})
        for container in ("acme/*", {"acme/*": 1}, 0):
            with self.subTest(container=container), self.assertRaises(ls.ScopeError):
                ls.parse({"repos": container})
            with self.assertRaises(ls.ScopeError):
                ls.parse({"repos": [], "exclude": container})
        with self.assertRaises(ls.ScopeError):
            ls.parse({"repos": [], "exclude": [7]})

    def test_a_non_string_repos_entry_is_skipped_as_before(self):
        self.assertEqual(ls.how(ls.parse({"repos": ["acme/app", 7]}), "acme/app"), "name")

    def test_owners_lists_every_owner_named(self):
        self.assertEqual(ls.owners(ls.parse({"repos": ["acme/*", "Other/app"]})), {"acme", "other"})

    def test_a_fork_or_archive_is_out_only_when_the_wildcard_alone_covers_it(self):
        s = ls.parse({"repos": ["acme/*", "acme/named-fork"]})
        self.assertIn("a fork", ls.wildcard_only(s, "acme/f", {"isFork": True}))
        self.assertIn("archived", ls.wildcard_only(s, "acme/old", {"isArchived": True}))
        self.assertIsNone(ls.wildcard_only(s, "acme/named-fork", {"isFork": True}), "listed by name: in")
        self.assertIsNone(ls.wildcard_only(s, "acme/app", {"isFork": False, "isArchived": False}))


if __name__ == "__main__":
    unittest.main()
