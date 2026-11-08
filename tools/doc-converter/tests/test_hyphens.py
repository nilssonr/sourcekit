"""Line-end hyphen decisions: the book's own words, the dictionary and its stems, code; no PDF or profile needed."""

from __future__ import annotations

import unittest

from doc_converter.core.hyphens import Resolver

from fixtures import resolver


class BookCorpusDecisionTests(unittest.TestCase):
    def test_a_word_the_book_prints_joined_is_joined(self):
        r = resolver(self, corpus=("hello",))

        keep = r.resolve("hel", "lo", "p1")

        self.assertFalse(keep)
        self.assertEqual(r.log[0].why, "book-joined")

    def test_a_word_the_book_prints_hyphenated_keeps_the_hyphen(self):
        r = resolver(self, corpus=("well-known",))

        keep = r.resolve("well", "known", "p1")

        self.assertTrue(keep)
        self.assertEqual(r.log[0].why, "book-hyphenated")

    def test_a_tie_between_joined_and_hyphenated_keeps_the_hyphen(self):
        r = resolver(self, corpus=("wellknown", "well-known"))

        keep = r.resolve("well", "known", "p1")

        self.assertTrue(keep)
        self.assertTrue(r.log[0].why.startswith("AMBIGUOUS"))

    def test_a_joined_majority_over_a_hyphenated_minority_joins(self):
        r = resolver(self, corpus=("wellknown", "wellknown", "well-known"))

        keep = r.resolve("well", "known", "p1")

        self.assertFalse(keep)
        self.assertTrue(r.log[0].why.startswith("AMBIGUOUS"))


class DictionaryDecisionTests(unittest.TestCase):
    def test_a_joined_word_the_dictionary_knows_is_joined(self):
        r = resolver(self, dictionary=("hello",))

        keep = r.resolve("hel", "lo", "p1")

        self.assertFalse(keep)
        self.assertEqual(r.log[0].why, "dictionary-joined")

    def test_an_inflected_word_whose_stem_the_dictionary_knows_is_joined(self):
        cases = [
            ("ies becomes y", "par", "ties", "party"),
            ("es is dropped", "bo", "xes", "box"),
            ("s is dropped", "ca", "ts", "cat"),
            ("ed is dropped", "wal", "ked", "walk"),
            ("ed becomes e", "ba", "ked", "bake"),
            ("ing is dropped", "wal", "king", "walk"),
            ("ing becomes e", "ba", "king", "bake"),
            ("ly is dropped", "qui", "ckly", "quick"),
            ("est is dropped", "fas", "test", "fast"),
        ]
        for rule, a, b, stem in cases:
            with self.subTest(rule=rule, word=a + b):
                r = resolver(self, dictionary=(stem,))

                keep = r.resolve(a, b, "p1")

                self.assertFalse(keep)
                self.assertEqual(r.log[0].why, "dictionary-joined")

    def test_a_long_book_word_lends_its_stem_to_another_inflection(self):
        r = resolver(self, corpus=("overcomplicated",))

        keep = r.resolve("overcompli", "cating", "p1")

        self.assertFalse(keep)
        self.assertEqual(r.log[0].why, "dictionary-joined")


class CompoundDecisionTests(unittest.TestCase):
    def test_two_dictionary_words_with_a_long_first_half_keep_the_hyphen(self):
        r = resolver(self, dictionary=("well", "known"))

        keep = r.resolve("well", "known", "p1")

        self.assertTrue(keep)
        self.assertEqual(r.log[0].why, "compound")

    def test_two_dictionary_words_with_a_two_letter_first_half_are_joined_for_review(self):
        r = resolver(self, dictionary=("in", "put"))

        keep = r.resolve("in", "put", "p1")

        self.assertFalse(keep)
        self.assertTrue(r.log[0].why.startswith("REVIEW"))

    def test_the_tail_of_a_hyphen_chain_keeps_its_hyphen_when_the_book_never_joins_it(self):
        r = resolver(self)

        keep = r.resolve("to", "production", "p1", chain=True)

        self.assertTrue(keep)
        self.assertEqual(r.log[0].why, "compound-chain")

    def test_the_tail_of_a_hyphen_chain_is_joined_when_the_book_prints_it_joined(self):
        r = resolver(self, corpus=("toproduction",))

        keep = r.resolve("to", "production", "p1", chain=True)

        self.assertFalse(keep)
        self.assertEqual(r.log[0].why, "book-joined")

    def test_halves_nobody_knows_are_joined_for_review(self):
        r = resolver(self)

        keep = r.resolve("zxq", "vbn", "p1")

        self.assertFalse(keep)
        self.assertTrue(r.log[0].why.startswith("REVIEW"))


class CorpusContentTests(unittest.TestCase):
    def test_a_joined_form_seen_only_in_code_does_not_count_as_book_text(self):
        r = resolver(self, code=("hello",))

        keep = r.resolve("hel", "lo", "p1")

        self.assertFalse(keep)
        self.assertTrue(r.log[0].why.startswith("REVIEW"))

    def test_a_word_broken_at_a_line_end_is_not_a_book_word(self):
        r = resolver(self, corpus=("the hel-",), dictionary=("lo",))

        keep = r.resolve("hel", "lo", "p1")

        self.assertFalse(keep)
        self.assertTrue(r.log[0].why.startswith("REVIEW"))

    def test_a_whole_word_at_a_line_end_is_a_book_word(self):
        r = resolver(self, corpus=("the hel",), dictionary=("lo",))

        keep = r.resolve("hel", "lo", "p1")

        self.assertTrue(keep)
        self.assertEqual(r.log[0].why, "compound")


class CodeDecisionTests(unittest.TestCase):
    def test_a_hyphenated_form_in_the_books_code_keeps_the_hyphen(self):
        r = resolver(self, code=("foo-bar",))

        keep = r.resolve_code("foo", "bar", "p1")

        self.assertTrue(keep)
        self.assertEqual(r.log[0].why, "code-hyphenated")

    def test_code_without_the_hyphenated_form_is_joined(self):
        r = resolver(self)

        keep = r.resolve_code("foo", "bar", "p1")

        self.assertFalse(keep)
        self.assertEqual(r.log[0].why, "code-joined")

    def test_a_hyphenated_form_in_the_prose_does_not_decide_code(self):
        r = resolver(self, corpus=("foo-bar",))

        keep = r.resolve_code("foo", "bar", "p1")

        self.assertFalse(keep)
        self.assertEqual(r.log[0].why, "code-joined")


class DecisionLogTests(unittest.TestCase):
    def test_the_log_holds_one_dict_per_decision_with_its_location(self):
        r = resolver(self, corpus=("hello",))
        r.resolve("hel", "lo", "p7")
        r.resolve_code("foo", "bar", "p9")

        log = r.log_dicts()

        self.assertEqual(log, [
            {"a": "hel", "b": "lo", "keep": False, "why": "book-joined", "where": "p7"},
            {"a": "foo", "b": "bar", "keep": False, "why": "code-joined", "where": "p9"},
        ])


class MissingDictionaryTests(unittest.TestCase):
    def test_a_missing_dictionary_exits_naming_the_path_and_the_package(self):
        with self.assertRaises(SystemExit) as cm:
            Resolver([], lambda s: False, "/nonexistent/words")

        self.assertIn("/nonexistent/words", str(cm.exception.code))
        self.assertIn("wamerican", str(cm.exception.code))


if __name__ == "__main__":
    unittest.main()
