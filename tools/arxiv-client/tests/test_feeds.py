import unittest

from arxiv_client import ValidationError
from arxiv_client.feeds import parse_atom_feed, parse_rss, split_creators, validate_categories
from tests.helpers import FakeClock, FakeOpener, fixture, make_client, xml_response

RSS_CT = "application/rss+xml; charset=utf-8"


class RssTests(unittest.TestCase):
    def setUp(self):
        self.feed = parse_rss(fixture("rss_weekday.xml"))

    def test_a_weekday_feed_has_its_items(self):
        self.assertEqual(len(self.feed.items), 2)
        self.assertFalse(self.feed.empty)

    def test_id_version_and_announce_type_from_guid(self):
        new, rep = self.feed.items
        self.assertEqual((new.id, new.version, new.announce_type), ("2611.01234", 1, "new"))
        self.assertEqual((rep.id, rep.version, rep.announce_type), ("hep-th/9901001", 4, "replace-cross"))

    def test_abstract_prefix_stripped(self):
        self.assertEqual(self.feed.items[0].abstract, "We present a planner. It works well on benchmarks.")

    def test_authors_split_from_single_creator_string(self):
        self.assertEqual(self.feed.items[0].authors, ("Alice Smith", "Bob Jones", "Carol Wu"))

    def test_feed_names_doi_and_journal_reference(self):
        rep = self.feed.items[1]
        self.assertEqual(rep.dois, ("10.1000/xyz123",))
        self.assertEqual(rep.journal_ref, "Phys. Rev. D 1 (1999) 1")

    def test_categories_rights_link(self):
        item = self.feed.items[0]
        self.assertEqual(item.categories, ("cs.AI", "cs.CL"))
        self.assertEqual(item.rights, "http://creativecommons.org/licenses/by/4.0/")
        self.assertEqual(item.link, "https://arxiv.org/abs/2611.01234")

    def test_weekend_feed_is_empty_not_error(self):
        feed = parse_rss(fixture("rss_weekend.xml"))
        self.assertTrue(feed.empty)
        self.assertEqual(feed.items, [])


class AtomFeedTests(unittest.TestCase):
    def test_an_atom_entry_is_read_in_full(self):
        feed = parse_atom_feed(fixture("atom_weekday.xml"))
        item = feed.items[0]
        self.assertEqual((item.id, item.version, item.announce_type), ("2611.01234", 1, "new"))
        self.assertEqual(item.abstract, "We present a planner.")
        self.assertEqual(item.authors, ("Alice Smith", "Carol Wu"))
        self.assertEqual(item.categories, ("cs.AI", "cs.CL"))
        self.assertEqual(item.dois, ("10.1000/abc",))
        self.assertEqual(item.journal_ref, "Conf. Proc. 2026")
        self.assertEqual(item.link, "https://arxiv.org/abs/2611.01234")


class CreatorSplitTests(unittest.TestCase):
    def test_commas_inside_parentheses_kept_together(self):
        self.assertEqual(
            split_creators("A One (Univ. X, Dept. Y), B Two, C Three (Lab (Z))"),
            ("A One", "B Two", "C Three"),
        )

    def test_no_creator_text_gives_no_authors(self):
        self.assertEqual(split_creators(None), ())
        self.assertEqual(split_creators(" "), ())


class CategoryValidationTests(unittest.TestCase):
    def test_known_category_forms_are_accepted(self):
        self.assertEqual(validate_categories(["cs.AI", "q-bio.NC", "hep-th"]), ["cs.AI", "q-bio.NC", "hep-th"])
        self.assertEqual(validate_categories("cs.AI+math.NA"), ["cs.AI", "math.NA"])

    def test_malformed_categories_are_rejected(self):
        for bad in (["cs.AI/x"], [], ["cs AI"]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    validate_categories(bad)


class FeedClientTests(unittest.TestCase):
    def test_400_with_valid_empty_feed_is_an_error(self):
        for fmt, name, ct in (("rss", "rss_400.xml", RSS_CT), ("atom", "atom_400.xml", "application/atom+xml")):
            with self.subTest(fmt=fmt):
                clock = FakeClock()
                opener = FakeOpener(clock, [xml_response(fixture(name), status=400, ct=ct)])
                with self.assertRaises(ValidationError) as cm:
                    make_client(clock, opener).feed(["foo.BAR"], fmt=fmt)
                self.assertIn("Bad archive/category: foo.BAR", str(cm.exception))

    def test_url_and_parse(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [xml_response(fixture("rss_weekday.xml"), ct=RSS_CT)])
        feed = make_client(clock, opener).feed(["cs.AI", "cs.CL"], fmt="rss")
        self.assertEqual(opener.requests[0].url, "https://rss.arxiv.org/rss/cs.AI+cs.CL")
        self.assertEqual(len(feed.items), 2)

    def test_atom_default(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [xml_response(fixture("atom_weekday.xml"))])
        make_client(clock, opener).feed("cs.AI")
        self.assertEqual(opener.requests[0].url, "https://rss.arxiv.org/atom/cs.AI")

    def test_weekend_200_with_zero_items(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [xml_response(fixture("rss_weekend.xml"), ct=RSS_CT)])
        feed = make_client(clock, opener).feed("cs.AI", fmt="rss")
        self.assertTrue(feed.empty)


if __name__ == "__main__":
    unittest.main()
