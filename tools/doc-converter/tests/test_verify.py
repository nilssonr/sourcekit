"""The verifier's word stream, per-file report and coverage check over fake spans: no PDF or profile needed."""

from __future__ import annotations

import unittest
from typing import ClassVar

from doc_converter.core.verify import (
    case_differences,
    coverage,
    markup_emphasis,
    page_text,
    report,
    tokens,
    without_added,
)
from doc_converter.modules.serialize.markdown import extract_added, pre
from fixtures import BULLET, CONTINUED, FIGURE_TEXT, PAGENUM, FakeBook, span, verify_config


class TokensTests(unittest.TestCase):
    def test_tokens_keep_non_ascii_letters_split_underscores_and_lower_case(self):
        self.assertEqual(tokens("Café ΑΒΓ snake_case"), ["café", "αβγ", "snake", "case"])

    def test_keep_case_keeps_the_case(self):
        self.assertEqual(tokens("Hello World", keep_case=True), ["Hello", "World"])

    def test_composed_and_decomposed_forms_match(self):
        self.assertEqual(tokens("café"), tokens("café"))

    def test_hyphen_variants_are_removed(self):
        for hyphen in ("-", "‐", "‑"):
            with self.subTest(hyphen=hex(ord(hyphen))):
                out = tokens(f"re{hyphen}sult")

                self.assertEqual(out, ["result"])

    def test_curly_and_straight_apostrophes_tokenize_alike(self):
        for text in ("don’t", "don't"):
            with self.subTest(text=text):
                out = tokens(text)

                self.assertEqual(out, ["don", "t"])

    def test_lower_casing_a_dotted_capital_i_does_not_split_the_word(self):
        self.assertEqual(tokens("İstanbul"), ["i̇stanbul"])

    def test_a_dotted_capital_i_is_one_cased_token(self):
        self.assertEqual(tokens("İstanbul", keep_case=True), ["İstanbul"])


class CaseDifferencesTests(unittest.TestCase):
    def test_a_word_that_matches_only_lower_cased_is_reported_with_both_forms(self):
        self.assertEqual(case_differences(["Hello", "World"], ["HELLO", "World"]), ["Hello->HELLO×1"])

    def test_identical_words_have_no_case_differences(self):
        self.assertEqual(case_differences(["Hello"], ["Hello"]), [])


class PageTextTests(unittest.TestCase):
    cfg = verify_config()

    def test_a_running_header_is_left_out(self):
        spans = [span("Running header", baseline=30), span("Body text", baseline=100)]

        out = page_text(spans, self.cfg)

        self.assertEqual(out, " Body text")

    def test_figure_labels_bullet_glyphs_and_continued_labels_are_left_out(self):
        for font in (FIGURE_TEXT, BULLET, CONTINUED):
            with self.subTest(font=font):
                spans = [span("gone", font=font, baseline=100), span("kept", baseline=120)]

                out = page_text(spans, self.cfg)

                self.assertEqual(out, " kept")

    def test_a_superscript_joins_the_row_just_below_it(self):
        spans = [span("Body", baseline=100), span("3", size=5.0, baseline=97, x0=92), span("next", baseline=115)]

        out = page_text(spans, self.cfg)

        self.assertEqual(out, " Body3 next")

    def test_a_row_of_only_page_numbers_is_dropped(self):
        spans = [span("Body", baseline=100), span("42", font=PAGENUM, baseline=680)]

        out = page_text(spans, self.cfg)

        self.assertEqual(out, " Body")

    def test_a_whitespace_only_row_is_dropped(self):
        spans = [span("Body", baseline=100), span("   ", baseline=110)]

        out = page_text(spans, self.cfg)

        self.assertEqual(out, " Body")

    def test_a_wide_gap_splits_a_row_into_columns_read_row_by_row(self):
        spans = [span("code", baseline=100), span("callout", baseline=100, x0=200),
                 span("line2", baseline=110), span("note", baseline=110, x0=200)]

        out = page_text(spans, self.cfg)

        self.assertEqual(out, " code callout line2 note")

    def test_a_line_end_hyphen_joins_the_line_below(self):
        spans = [span("re-", baseline=100), span("sult", baseline=112)]

        out = page_text(spans, self.cfg)

        self.assertEqual(out, " re-sult")

    def test_a_word_broken_over_several_lines_is_joined_through_them_all(self):
        spans = [span("un-", baseline=100), span("be-", baseline=112), span("lievable", baseline=124)]

        out = page_text(spans, self.cfg)

        self.assertEqual(out, " un-be-lievable")

    def test_a_hyphen_with_nothing_below_joins_the_next_part_without_a_space(self):
        spans = [span("ex-", baseline=100), span("ample", baseline=100, x0=300)]

        out = page_text(spans, self.cfg)

        self.assertEqual(out, " ex-ample")


class ReportTests(unittest.TestCase):
    cfg = verify_config()

    def _compare(self, md: str, *spans) -> list[str]:
        return report(FakeBook(pages={1: list(spans)}), md, 1, 1, set(), self.cfg).splitlines()

    def test_the_first_line_counts_expected_markdown_and_matched_words(self):
        lines = self._compare("Hello caf\n", span("Hello café αβγ"))

        self.assertEqual(lines[0], "expected 3 words, markdown 2 words, matched 1")

    def test_lost_non_ascii_words_are_missing(self):
        lines = self._compare("Hello caf\n", span("Hello café αβγ"))

        self.assertEqual(lines[1], "missing from markdown (2): café×1 αβγ×1")

    def test_words_only_in_the_markdown_are_extra(self):
        lines = self._compare("Hello world\n", span("Hello"))

        self.assertEqual(lines[2], "extra in markdown (1): world×1")

    def test_a_case_difference_is_reported_while_the_words_match(self):
        lines = self._compare("HELLO World\n", span("Hello World"))

        self.assertEqual(lines[0:4:3], ["expected 2 words, markdown 2 words, matched 2", "case differences (1): Hello->HELLO×1"])

    def test_no_case_differences_is_an_empty_list(self):
        lines = self._compare("Hello\n", span("Hello"))

        self.assertEqual(lines[3], "case differences (0): ")

    def test_a_line_end_hyphen_after_a_non_ascii_letter_joins(self):
        lines = self._compare("naïve\n", span("naï-", baseline=100.0), span("ve", baseline=115.0))

        self.assertEqual(lines[1], "missing from markdown (0): ")

    def test_reordered_words_are_counted_as_differing_stretches(self):
        lines = self._compare("gamma delta alpha beta\n", span("alpha beta gamma delta"))

        self.assertEqual(lines[4], "order: 2 differing stretches")

    def test_a_stretch_moved_elsewhere_is_a_move_with_nothing_unexplained(self):
        lines = self._compare("gamma delta alpha beta\n", span("alpha beta gamma delta"))

        self.assertEqual(lines[5], "moves: 1 stretches reappear elsewhere; unexplained: 0 out, 0 in")

    def test_a_stretch_that_reappears_nowhere_is_unpaired(self):
        lines = self._compare("alpha gamma\n", span("alpha beta gamma"))

        self.assertEqual(lines[5:7], ["moves: 0 stretches reappear elsewhere; unexplained: 1 out, 0 in",
                                      "  UNPAIRED delete pdf[1:2]: beta"])

    def test_a_skipped_page_is_left_out(self):
        book = FakeBook(pages={1: [span("one", page=1)], 2: [span("two", page=2)]})

        lines = report(book, "one\n", 1, 2, {2}, self.cfg).splitlines()

        self.assertEqual(lines[0], "expected 1 words, markdown 1 words, matched 1")

    def test_a_hyphen_at_the_end_of_a_page_joins_the_first_word_of_the_next(self):
        book = FakeBook(pages={1: [span("exam-", page=1)], 2: [span("ple", page=2)]})

        lines = report(book, "example\n", 1, 2, set(), self.cfg).splitlines()

        self.assertEqual(lines[0:2], ["expected 1 words, markdown 1 words, matched 1", "missing from markdown (0): "])


class WithoutAddedTests(unittest.TestCase):
    def test_added_ranges_are_removed_exactly(self):
        self.assertEqual(without_added("keep drop keep2", [[5, 10, "k"]]), "keep keep2")

    def test_no_ranges_leaves_the_text_alone(self):
        self.assertEqual(without_added("abc", []), "abc")

    def test_a_code_escape_range_becomes_the_book_character(self):
        self.assertEqual(without_added("a &lt; b", [[2, 6, "code-escape"]]), "a < b")


class MarkupEmphasisTests(unittest.TestCase):
    def test_bold_italic_and_bold_italic_text_land_in_their_strings(self):
        md, ranges = extract_added(pre(["abc"], ["biB"]))

        emphasis = markup_emphasis(md, [list(r) for r in ranges])

        self.assertEqual(emphasis, ("ac", "bc"))


class CoverageTests(unittest.TestCase):
    EXPECTED: ClassVar[dict[str, tuple[int, int]]] = {"a.md": (2, 10), "b.md": (11, 20)}

    def test_matching_contiguous_files_pass(self):
        files = [{"file": "a.md", "pages": [2, 10]}, {"file": "b.md", "pages": [11, 20]}]

        ok, text = coverage(files, self.EXPECTED)

        self.assertEqual((ok, text), (True, "coverage: 2 files, PDF pages 2-20: OK\n"))

    def test_a_gap_an_overlap_and_a_missing_file_each_fail_with_their_message(self):
        cases = (
            ("gap", [{"file": "a.md", "pages": [2, 9]}, {"file": "b.md", "pages": [11, 20]}], "pages 10-10 are in no file"),
            ("overlap", [{"file": "a.md", "pages": [2, 12]}, {"file": "b.md", "pages": [11, 20]}], "overlap"),
            ("missing", [{"file": "a.md", "pages": [2, 10]}], "missing file b.md"),
        )
        for name, files, message in cases:
            with self.subTest(name):
                ok, text = coverage(files, self.EXPECTED)

                self.assertEqual((ok, message in text), (False, True), text)

    def test_a_file_the_outline_does_not_know_is_unexpected(self):
        files = [{"file": "a.md", "pages": [2, 10]}, {"file": "b.md", "pages": [11, 20]}, {"file": "c.md", "pages": [21, 30]}]

        ok, text = coverage(files, self.EXPECTED)

        self.assertEqual((ok, "unexpected file c.md" in text), (False, True), text)

    def test_a_page_range_differing_from_the_outline_is_reported_against_the_manifest(self):
        files = [{"file": "a.md", "pages": [2, 9]}, {"file": "b.md", "pages": [10, 20]}]

        ok, text = coverage(files, self.EXPECTED)

        self.assertEqual((ok, "in the manifest" in text), (False, True), text)

    def test_no_files_and_no_expected_sections_pass_with_no_pages(self):
        ok, text = coverage([], {})

        self.assertEqual((ok, text), (True, "coverage: 0 files, PDF pages none: OK\n"))


if __name__ == "__main__":
    unittest.main()
