import datetime as dt
import unittest

from arxiv_client import Q, ValidationError
from arxiv_client.query import STOPWORDS, encode_params, parse_free_text, to_search_query


class QueryBuilderTests(unittest.TestCase):
    def test_field_prefixes(self):
        self.assertEqual(str(Q.title("transformer")), "ti:transformer")
        self.assertEqual(str(Q.author("Hinton")), "au:Hinton")
        self.assertEqual(str(Q.abstract("x")), "abs:x")
        self.assertEqual(str(Q.comment("x")), "co:x")
        self.assertEqual(str(Q.journal_ref("x")), "jr:x")
        self.assertEqual(str(Q.category("cs.LG")), "cat:cs.LG")
        self.assertEqual(str(Q.report_number("x")), "rn:x")
        self.assertEqual(str(Q.id("2301.00001")), "id:2301.00001")
        self.assertEqual(str(Q.all("x")), "all:x")

    def test_multi_word_field_text_is_a_phrase(self):
        self.assertEqual(str(Q.title("attention is all you need")), 'ti:"attention is all you need"')

    def test_embedded_quotes_dropped_and_whitespace_normalised(self):
        self.assertEqual(str(Q.title('  say "hello"   world ')), 'ti:"say hello world"')

    def test_operator_words_are_quoted_not_operators(self):
        self.assertEqual(str(Q.all("AND")), 'all:"AND"')
        self.assertEqual(str(Q.title("NOT")), 'ti:"NOT"')

    def test_operators_are_uppercase(self):
        q = Q.title("a") & Q.author("b") | Q.category("c")
        self.assertEqual(str(q), "(ti:a AND au:b) OR cat:c")
        self.assertEqual(str(Q.all("a") - Q.all("b")), "all:a ANDNOT all:b")

    def test_associative_chains_are_flat(self):
        q = Q.all("a") & Q.all("b") & Q.all("c")
        self.assertEqual(str(q), "all:a AND all:b AND all:c")

    def test_andnot_parenthesises_compound_operands(self):
        q = (Q.author("a") | Q.author("b")) - (Q.category("x") | Q.category("y"))
        self.assertEqual(str(q), "(au:a OR au:b) ANDNOT (cat:x OR cat:y)")

    def test_diacritics_preserved(self):
        self.assertEqual(str(Q.author("Fréchet")), "au:Fréchet")
        self.assertIn("Fr%C3%A9chet", encode_params({"search_query": str(Q.author("Fréchet"))}))

    def test_a_trailing_wildcard_on_a_single_word_is_allowed(self):
        self.assertEqual(str(Q.title("transform*")), "ti:transform*")

    def test_wildcards_anywhere_else_are_rejected(self):
        for bad in ("tr*ansform", "*", "two words*"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                Q.title(bad)

    def test_blank_field_text_is_rejected(self):
        with self.assertRaises(ValidationError):
            Q.title("   ")

    def test_phrase_always_quotes(self):
        self.assertEqual(str(Q.phrase("title", "single")), 'ti:"single"')

    def test_unknown_field(self):
        with self.assertRaises(ValidationError):
            Q.field("foo", "bar")

    def test_raw_is_parenthesised_when_combined(self):
        q = Q.raw("au:a OR au:b") & Q.category("cs.LG")
        self.assertEqual(str(q), "(au:a OR au:b) AND cat:cs.LG")


class DateRangeTests(unittest.TestCase):
    def test_strings_and_dates(self):
        self.assertEqual(str(Q.submitted_between("20230101", "20230131")), "submittedDate:[20230101 TO 20230131]")
        self.assertEqual(
            str(Q.submitted_between(dt.date(2023, 1, 1), dt.date(2023, 1, 31))),
            "submittedDate:[20230101 TO 20230131]",
        )

    def test_aware_datetimes_converted_to_gmt(self):
        tz = dt.timezone(dt.timedelta(hours=2))
        q = Q.submitted_between(
            dt.datetime(2023, 1, 1, 2, 0, tzinfo=tz), dt.datetime(2023, 1, 2, tzinfo=dt.timezone.utc)
        )
        self.assertEqual(str(q), "submittedDate:[20230101000000 TO 20230102000000]")

    def test_malformed_or_reversed_date_ranges_are_rejected(self):
        for start, end in (("2023-01-01", "20230131"), ("20230201", "20230101")):
            with self.subTest(start=start, end=end), self.assertRaises(ValidationError):
                Q.submitted_between(start, end)

    def test_spaces_encode_as_percent20_never_percent2B(self):
        encoded = encode_params(
            {"search_query": str(Q.category("cs.LG") & Q.submitted_between("20230101", "20230131"))}
        )
        self.assertIn("%20TO%20", encoded)
        self.assertNotIn("%2B", encoded)
        self.assertNotIn("+", encoded)


class PlainTextTests(unittest.TestCase):
    def test_words_joined_with_and(self):
        self.assertEqual(
            to_search_query("transformer protein folding"),
            "all:transformer AND all:protein AND all:folding",
        )

    def test_quoted_segments_stay_phrases(self):
        self.assertEqual(
            to_search_query('"attention is all you need" vaswani'),
            'all:"attention is all you need" AND all:vaswani',
        )

    def test_single_word(self):
        self.assertEqual(to_search_query("transformer"), "all:transformer")

    def test_stopwords_are_dropped_before_the_words_are_joined(self):
        # arXiv does not index stopwords: with "is" ANDed in, this query matched 0 papers.
        self.assertEqual(
            to_search_query("attention is all you need"),
            "all:attention AND all:all AND all:you AND all:need",
        )

    def test_a_stopword_is_recognised_in_any_case_and_with_punctuation_around_it(self):
        parsed = parse_free_text("The paper, is it (the) one?")
        self.assertEqual(parsed.dropped, ("The", "is", "it", "(the)"))
        # A kept word is sent as typed.
        self.assertEqual(parsed.terms, ("paper,", "one?"))

    def test_every_stopword_is_dropped(self):
        self.assertEqual(len(STOPWORDS), 33)
        parsed = parse_free_text("transformer " + " ".join(sorted(STOPWORDS)))
        self.assertEqual(parsed.terms, ("transformer",))
        self.assertEqual(len(parsed.dropped), 33)

    def test_a_quoted_phrase_keeps_its_stopwords(self):
        self.assertEqual(
            to_search_query('"attention is all you need" by vaswani'),
            'all:"attention is all you need" AND all:vaswani',
        )

    def test_text_of_only_stopwords_is_rejected(self):
        with self.assertRaisesRegex(ValidationError, "only stopwords.*of the"):
            to_search_query("of the")

    def test_an_uppercase_operator_word_is_rejected(self):
        for word in ("AND", "OR", "NOT", "ANDNOT"):
            with self.subTest(word=word), self.assertRaisesRegex(ValidationError, "^%s is not an operator" % word):
                to_search_query("attention %s transformer" % word)

    def test_a_quoted_operator_word_is_searched_for(self):
        self.assertEqual(to_search_query('"OR" gate'), 'all:"OR" AND all:gate')
        self.assertEqual(to_search_query('"AND gate" circuits'), 'all:"AND gate" AND all:circuits')

    def test_lowercase_operator_words_are_stopwords_and_are_dropped(self):
        self.assertEqual(
            to_search_query("attention and transformer or vision not text"),
            "all:attention AND all:transformer AND all:vision AND all:text",
        )

    def test_a_hyphenated_word_with_a_stopword_inside_is_kept_whole(self):
        # Measured: ti:state-of-the-art matches about as many papers as the phrase does.
        self.assertEqual(to_search_query("state-of-the-art models"), "all:state-of-the-art AND all:models")

    def test_q_passes_through(self):
        self.assertEqual(to_search_query(Q.title("x")), "ti:x")

    def test_blank_free_text_is_rejected(self):
        with self.assertRaises(ValidationError):
            to_search_query("   ")


if __name__ == "__main__":
    unittest.main()
