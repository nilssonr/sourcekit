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

    def test_an_id_search_takes_an_unversioned_new_style_id_or_a_prefix(self):
        self.assertEqual(str(Q.id("1706.037*")), "id:1706.037*")
        self.assertEqual(str(Q.id("17*")), "id:17*")
        self.assertEqual(str(Q.id("arXiv:1706.03762")), "id:1706.03762")
        self.assertEqual(str(Q.field("id", "1706.03762")), "id:1706.03762")

    def test_an_id_search_rejects_the_ids_it_cannot_match(self):
        # Measured: id:1706.03762v5 and id:hep-th/9901001 both match 0 papers.
        for bad in ("1706.03762v5", "hep-th/9901001", "hep-th/9901001v1", "math.CA/0611800"):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValidationError, "arxiv fetch"):
                Q.id(bad)
            with self.subTest(bad=bad, via="field"), self.assertRaises(ValidationError):
                Q.field("id", bad)

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

    def test_a_date_or_time_that_is_not_on_the_calendar_is_rejected(self):
        # arXiv answers these with HTTP 500, not 400.
        for bad in ("20231399", "20230229", "20230001", "202401152460", "20240115235960"):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValidationError, "^%s is not a calendar date" % bad):
                Q.submitted_between(bad, "20991231")
            with self.subTest(bad=bad, position="end"), self.assertRaises(ValidationError):
                Q.submitted_between("19910101", bad)

    def test_a_leap_day_and_the_last_second_of_a_day_are_accepted(self):
        self.assertEqual(
            str(Q.submitted_between("20240229", "20240229235959")), "submittedDate:[20240229 TO 20240229235959]"
        )

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
        # A kept word keeps its punctuation, except a trailing question mark.
        self.assertEqual(parsed.terms, ("paper,", "one"))

    def test_a_trailing_question_mark_is_removed_and_reported(self):
        # arXiv reads ? as "any one character": ti:attention? matched 2 papers, ti:attention 14,795.
        parsed = parse_free_text("what is attention?")
        self.assertEqual(parsed.terms, ("what", "attention"))
        self.assertEqual(parsed.dropped, ("is",))
        self.assertEqual(parsed.changed, (("attention?", "attention"),))
        self.assertEqual(to_search_query("what is attention?"), "all:what AND all:attention")

    def test_a_question_mark_among_other_trailing_punctuation_is_removed(self):
        for typed, sent in (("need??", "need"), ("need?!", "need!"), ("need?,", "need,"), ("(need?)", "(need)")):
            with self.subTest(typed=typed):
                self.assertEqual(parse_free_text(typed), ((sent,), (), ((typed, sent),)))

    def test_a_question_mark_inside_a_word_is_kept(self):
        self.assertEqual(parse_free_text("wom?n"), (("wom?n",), (), ()))

    def test_a_possessive_is_removed_and_reported(self):
        # all:Maldacena's and all:Maldacena’s each matched 982 papers, all:Maldacena 1,171.
        for typed, sent in (
            ("Maldacena's", "Maldacena"),
            ("Maldacena’s", "Maldacena"),
            ("MALDACENA'S", "MALDACENA"),
            ("Maldacena's,", "Maldacena,"),
            ("Maldacena's?", "Maldacena"),
        ):
            with self.subTest(typed=typed):
                self.assertEqual(parse_free_text(typed), ((sent,), (), ((typed, sent),)))

    def test_an_apostrophe_that_is_not_a_possessive_is_kept(self):
        self.assertEqual(parse_free_text("O'Brien authors' rock'n'roll").terms, ("O'Brien", "authors'", "rock'n'roll"))
        self.assertEqual(parse_free_text("O'Brien authors' rock'n'roll").changed, ())

    def test_a_word_that_becomes_a_stopword_is_dropped_not_changed(self):
        parsed = parse_free_text("it's that's attention")
        self.assertEqual(parsed, (("attention",), ("it's", "that's"), ()))

    def test_a_word_with_nothing_left_is_dropped(self):
        self.assertEqual(parse_free_text("attention ? 's"), (("attention",), ("?", "'s"), ()))

    def test_text_with_no_word_left_is_rejected(self):
        with self.assertRaisesRegex(ValidationError, r"no word to search for: \? the"):
            to_search_query("? the")

    def test_a_quoted_phrase_keeps_its_question_mark_and_possessive(self):
        # Measured: arXiv ignores a ? inside a phrase.
        parsed = parse_free_text('"is attention all you need?" "Maldacena\'s conjecture"')
        self.assertEqual(parsed, (("is attention all you need?", "Maldacena's conjecture"), (), ()))

    def test_a_field_search_keeps_a_trailing_question_mark_as_a_wildcard(self):
        self.assertEqual(str(Q.title("attention?")), "ti:attention?")
        self.assertEqual(str(Q.title("is attention all you need?")), 'ti:"is attention all you need?"')

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
