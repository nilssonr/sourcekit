import datetime as dt
import unittest

from arxiv_client import MalformedResponseError, ServerError
from arxiv_client.atom import clean_abstract, error_message, parse_query_feed

from tests.helpers import fixture, make_entry, make_feed


class FullFeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = parse_query_feed(fixture("query_full.xml"))
        cls.paper = cls.page.papers[0]

    def test_counts_raw_entries_including_unparseable(self):
        self.assertEqual(self.page.raw_entry_count, 3)
        self.assertEqual(len(self.page.papers), 2)
        self.assertEqual(self.page.skipped, 1)
        self.assertEqual(self.page.total_results, 3)
        self.assertEqual(self.page.start, 0)

    def test_id_split_into_base_and_version(self):
        self.assertEqual(self.paper.id, "2301.00001")
        self.assertEqual(self.paper.version, 2)
        self.assertEqual(self.paper.versioned_id, "2301.00001v2")

    def test_title_whitespace_normalised_and_diacritics_kept(self):
        self.assertEqual(self.paper.title, "A Study of Fréchet Means in Curved Spaces")

    def test_duplicate_dois_deduplicated(self):
        self.assertEqual(self.paper.dois, ("10.1234/example.5678",))

    def test_primary_category_without_scheme(self):
        self.assertEqual(self.paper.primary_category, "math.ST")
        self.assertEqual(self.paper.categories, ("math.ST", "stat.TH"))

    def test_author_names_stripped_affiliations_kept(self):
        names = [a.name for a in self.paper.authors]
        self.assertEqual(names, ["Ada Lovelace", "The ATLAS Collaboration", "J. Kova{č}i{ć}"])
        self.assertEqual(self.paper.authors[0].affiliations, ("Analytical Engine Co.",))

    def test_published_and_updated_are_utc_datetimes(self):
        self.assertEqual(self.paper.published, dt.datetime(2023, 1, 1, 9, tzinfo=dt.timezone.utc))
        self.assertEqual(self.paper.updated, dt.datetime(2023, 2, 1, 10, tzinfo=dt.timezone.utc))

    def test_comment_journal_ref_and_urls_are_read(self):
        self.assertEqual(self.paper.comment, "12 pages, 3 figures")
        self.assertEqual(self.paper.journal_ref, "J. Stat. 1 (2023) 1-12")
        self.assertEqual(self.paper.abs_url, "https://arxiv.org/abs/2301.00001v2")
        self.assertEqual(self.paper.pdf_url, "https://arxiv.org/pdf/2301.00001v2")

    def test_abstract_paragraphs(self):
        self.assertEqual(
            self.paper.summary,
            "We study Fréchet means on manifolds. The method extends earlier work on hard-wrapped lines.\n"
            "A second paragraph starts after a full stop and an indented line.\n"
            "Third paragraph after a blank line.",
        )

    def test_missing_title_and_optional_elements_tolerated(self):
        old = self.page.papers[1]
        self.assertEqual(old.id, "hep-th/9901001")
        self.assertEqual(old.version, 3)
        self.assertIsNone(old.title)
        self.assertIsNone(old.comment)
        self.assertIsNone(old.journal_ref)
        self.assertEqual(old.dois, ())

    def test_to_dict_is_json_ready(self):
        import json
        d = self.paper.to_dict()
        json.dumps(d)
        self.assertEqual(d["published"], "2023-01-01T09:00:00Z")
        self.assertNotIn("requested_version", d)


class AbstractTests(unittest.TestCase):
    def test_mid_sentence_hard_wrap_joined(self):
        self.assertEqual(clean_abstract("We show that\n  the result holds."), "We show that the result holds.")

    def test_indented_line_after_sentence_is_paragraph(self):
        self.assertEqual(clean_abstract("First.\n  Second."), "First.\nSecond.")

    def test_unindented_line_after_sentence_is_joined(self):
        self.assertEqual(clean_abstract("First.\nSecond."), "First. Second.")

    def test_leading_two_spaces_removed(self):
        self.assertEqual(clean_abstract("  Pre-2010 abstract.\n"), "Pre-2010 abstract.")

    def test_spaces_within_line_collapsed(self):
        self.assertEqual(clean_abstract("a   b\tc"), "a b c")

    def test_a_missing_or_blank_abstract_is_none(self):
        self.assertIsNone(clean_abstract(None))
        self.assertIsNone(clean_abstract("   \n  "))


class FeedLevelTests(unittest.TestCase):
    def test_items_per_page_ignored(self):
        page = parse_query_feed(make_feed([make_entry("2401.00001")], total=50, items_per_page=100))
        self.assertEqual(page.raw_entry_count, 1)
        self.assertEqual(page.total_results, 50)

    def test_empty_feed(self):
        page = parse_query_feed(make_feed([], total=0))
        self.assertEqual((page.papers, page.raw_entry_count), ([], 0))

    def test_error_feeds_detected(self):
        for name in ("query_error_400.xml", "query_error_sort.xml", "query_error_500.xml"):
            with self.subTest(fixture=name), self.assertRaises(ServerError):
                parse_query_feed(fixture(name))

    def test_error_message_extraction(self):
        self.assertEqual(error_message(fixture("query_error_400.xml")), "incorrect id format for foo")
        self.assertEqual(error_message(fixture("rss_400.xml")), "Bad archive/category: foo.BAR")
        self.assertEqual(error_message(fixture("atom_400.xml")), "Bad archive/category: foo.BAR")

    def test_unparseable_xml(self):
        with self.assertRaises(MalformedResponseError):
            parse_query_feed(b"<feed><unclosed>")

    def test_not_a_feed(self):
        with self.assertRaises(MalformedResponseError):
            parse_query_feed(b"<html></html>")

    def test_feed_names_for_doi_and_journal_ref_accepted(self):
        entry = make_entry("2401.00001").replace(
            "</entry>",
            "<arxiv:DOI>10.1/a</arxiv:DOI><arxiv:journal_reference>JR 1</arxiv:journal_reference></entry>",
        )
        paper = parse_query_feed(make_feed([entry])).papers[0]
        self.assertEqual(paper.dois, ("10.1/a",))
        self.assertEqual(paper.journal_ref, "JR 1")


if __name__ == "__main__":
    unittest.main()
