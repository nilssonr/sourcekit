"""Rows from spans, inline runs and joining rows into running text; no PDF or profile needed."""

from __future__ import annotations

import unittest

from doc_converter.core.model import Run
from doc_converter.core.rows import runs_text

from fixtures import (BODY, BOLD, BOLD_ITALIC, BULLET, CODE, DROPCAP, FN_MARKER, INLINE_CODE_SIZE, ITALIC,
                      LIST_NUMBER, NOTE_LABEL, row, span, text)

FN_SIZE = 6.0


def fn_marker(label: str, *, x0: float, baseline: float):
    return span(label, font=FN_MARKER, size=FN_SIZE, x0=x0, baseline=baseline)


def texts(rows) -> list[list[str]]:
    return [[s.text for s in r.spans] for r in rows]


class RowGroupingTests(unittest.TestCase):
    def test_spans_whose_baselines_are_within_the_tolerance_share_a_row(self):
        spans = [span("a", baseline=100.0), span("b", baseline=102.0, x0=80.0)]

        rows = text(self).rows_of(spans)

        self.assertEqual(texts(rows), [["a", "b"]])

    def test_spans_whose_baselines_are_beyond_the_tolerance_get_separate_rows(self):
        spans = [span("a", baseline=100.0), span("b", baseline=103.0, x0=80.0)]

        rows = text(self).rows_of(spans)

        self.assertEqual(texts(rows), [["a"], ["b"]])

    def test_rows_come_back_in_page_and_top_order_whatever_the_span_order(self):
        spans = [span("d", page=2, baseline=50.0), span("c", baseline=130.0), span("a", baseline=100.0),
                 span("b", baseline=115.0)]

        rows = text(self).rows_of(spans)

        self.assertEqual([(r.page, r.top) for r in rows], [(1, 100.0), (1, 115.0), (1, 130.0), (2, 50.0)])

    def test_spans_on_different_pages_never_share_a_row(self):
        spans = [span("a", page=1, baseline=100.0), span("b", page=2, baseline=100.0)]

        rows = text(self).rows_of(spans)

        self.assertEqual([(r.page, [s.text for s in r.spans]) for r in rows], [(1, ["a"]), (2, ["b"])])

    def test_a_drop_cap_gets_its_own_row_topped_at_its_glyph_top(self):
        spans = [span("T", font=DROPCAP, size=40.0, baseline=100.0), span("he rest", baseline=100.0, x0=100.0)]

        rows = text(self).rows_of(spans)

        self.assertEqual([(r.top, [s.text for s in r.spans]) for r in rows], [(68.0, ["T"]), (100.0, ["he rest"])])


class FootnoteMarkerTests(unittest.TestCase):
    def test_a_marker_joins_the_row_whose_baseline_sits_just_below_it(self):
        spans = [span("word", baseline=100.0), fn_marker("1", x0=92.0, baseline=97.0)]

        rows = text(self).rows_of(spans)

        self.assertEqual(texts(rows), [["word", "1"]])

    def test_a_marker_between_two_rows_joins_the_nearer_one(self):
        spans = [span("first", baseline=100.0), span("second", baseline=104.0),
                 fn_marker("1", x0=97.0, baseline=99.5)]

        rows = text(self).rows_of(spans)

        self.assertEqual(texts(rows), [["first", "1"], ["second"]])

    def test_a_marker_with_no_row_in_reach_gets_its_own_row(self):
        spans = [span("word", baseline=100.0), fn_marker("1", x0=92.0, baseline=90.0)]

        rows = text(self).rows_of(spans)

        self.assertEqual([(r.top, [s.text for s in r.spans]) for r in rows], [(90.0, ["1"]), (100.0, ["word"])])


class RowEdgeTests(unittest.TestCase):
    def test_the_left_edge_skips_bullets_list_numbers_and_whitespace(self):
        cases = [("bullet", span("•", font=BULLET, x0=60.0)), ("list number", span("1.", font=LIST_NUMBER, x0=60.0)),
                 ("whitespace", span(" ", x0=60.0))]
        for kind, lead in cases:
            with self.subTest(kind=kind):
                rows = text(self).rows_of([lead, span("item", x0=72.0)])

                self.assertEqual(rows[0].left, 72.0)

    def test_the_right_edge_stops_at_the_last_inked_character(self):
        spans = [span("item  ", x0=72.0)]

        rows = text(self).rows_of(spans)

        self.assertEqual(rows[0].right, 92.0)

    def test_the_right_edge_skips_a_trailing_whitespace_span(self):
        spans = [span("item", x0=72.0), span("   ", x0=92.0)]

        rows = text(self).rows_of(spans)

        self.assertEqual(rows[0].right, 92.0)


class SpanRunTests(unittest.TestCase):
    def test_a_code_font_span_is_a_code_run(self):
        s = span("x", font=CODE, size=INLINE_CODE_SIZE)

        run = text(self).span_run(s)

        self.assertEqual(run, Run("x", "code"))

    def test_a_footnote_marker_is_a_stripped_fn_run(self):
        s = span(" 1 ", font=FN_MARKER, size=FN_SIZE)

        run = text(self).span_run(s)

        self.assertEqual(run, Run("1", "fn"))

    def test_emphasis_follows_the_font(self):
        for font, style in [(BOLD, "b"), (ITALIC, "i"), (BOLD_ITALIC, "bi"), (BODY, "")]:
            with self.subTest(font=font):
                run = text(self).span_run(span("word", font=font))

                self.assertEqual(run, Run("word", style))


class RowRunsTests(unittest.TestCase):
    def test_bullets_list_numbers_and_note_labels_leave_no_run(self):
        cases = [("bullet", span("•", font=BULLET, x0=60.0)), ("list number", span("1.", font=LIST_NUMBER, x0=60.0)),
                 ("note label", span("NOTE", font=NOTE_LABEL, x0=40.0))]
        for kind, lead in cases:
            with self.subTest(kind=kind):
                runs = text(self).row_runs(row(lead, span("item", x0=72.0)))

                self.assertEqual(runs, [Run("item", "")])

    def test_a_gap_wider_than_a_point_between_spans_becomes_a_space(self):
        r = row(span("one", x0=72.0), span("two", x0=90.0))

        runs = text(self).row_runs(r)

        self.assertEqual(runs, [Run("one", ""), Run(" ", ""), Run("two", "")])

    def test_a_gap_within_a_point_adds_nothing(self):
        r = row(span("one", x0=72.0), span("two", x0=87.5))

        runs = text(self).row_runs(r)

        self.assertEqual(runs, [Run("one", ""), Run("two", "")])

    def test_a_gap_beside_a_span_that_already_has_the_space_adds_nothing(self):
        cases = [
            ("previous ends with a space", row(span("one ", x0=72.0), span("two", x0=100.0)),
             [Run("one ", ""), Run("two", "")]),
            ("next starts with a space", row(span("one", x0=72.0), span(" two", x0=100.0)),
             [Run("one", ""), Run(" two", "")]),
        ]
        for side, r, expected in cases:
            with self.subTest(side=side):
                runs = text(self).row_runs(r)

                self.assertEqual(runs, expected)

    def test_plain_drops_emphasis_but_keeps_code_and_footnote_markers(self):
        r = row(span("bold", font=BOLD, x0=72.0), span("ital", font=ITALIC, x0=92.0),
                span("x", font=CODE, size=INLINE_CODE_SIZE, x0=112.0), fn_marker("1", x0=117.0, baseline=100.0))

        runs = text(self).row_runs(r, plain=True)

        self.assertEqual(runs, [Run("bold", ""), Run("ital", ""), Run("x", "code"), Run("1", "fn")])


class JoinRowsTests(unittest.TestCase):
    def test_two_rows_become_one_run_list_with_a_space_between(self):
        t = text(self)
        rows = t.rows_of([span("one", baseline=100.0), span("two", baseline=115.0)])

        runs = t.join_rows(rows)

        self.assertEqual(runs, [Run("one", ""), Run(" ", ""), Run("two", "")])

    def test_a_trailing_whitespace_run_is_dropped_before_the_joining_space(self):
        t = text(self)
        rows = t.rows_of([span("one", baseline=100.0), span("  ", baseline=100.0, x0=90.0),
                          span("two", baseline=115.0)])

        runs = t.join_rows(rows)

        self.assertEqual(runs, [Run("one", ""), Run(" ", ""), Run("two", "")])

    def test_a_trailing_footnote_marker_stays_before_the_joining_space(self):
        t = text(self)
        rows = t.rows_of([span("one", baseline=100.0), fn_marker("1", x0=87.0, baseline=100.0),
                          span("two", baseline=115.0)])

        runs = t.join_rows(rows)

        self.assertEqual(runs, [Run("one", ""), Run("1", "fn"), Run(" ", ""), Run("two", "")])

    def test_a_line_end_hyphen_the_book_prints_joined_is_joined(self):
        for first, second, corpus_word, joined in [("hel-", "lo", "hello", "hello"), ("naï-", "ve", "naïve", "naïve")]:
            with self.subTest(word=joined):
                t = text(self, corpus=(corpus_word,))
                rows = t.rows_of([span(first, baseline=100.0), span(second, baseline=115.0)])

                runs = t.join_rows(rows)

                self.assertEqual(runs_text(runs), joined)

    def test_a_line_end_hyphen_the_book_prints_hyphenated_is_kept(self):
        t = text(self, corpus=("well-known",))
        rows = t.rows_of([span("well-", baseline=100.0), span("known", baseline=115.0)])

        runs = t.join_rows(rows)

        self.assertEqual(runs_text(runs), "well-known")

    def test_a_hyphen_chain_broken_at_a_line_end_stays_hyphenated(self):
        t = text(self)
        rows = t.rows_of([span("close-to-", baseline=100.0), span("production", baseline=115.0)])

        runs = t.join_rows(rows)

        self.assertEqual(runs_text(runs), "close-to-production")

    def test_a_code_line_end_hyphen_the_books_code_prints_is_kept(self):
        t = text(self, code=("foo-bar",))
        rows = t.rows_of([span("foo-", font=CODE, size=INLINE_CODE_SIZE, baseline=100.0),
                          span("bar", font=CODE, size=INLINE_CODE_SIZE, baseline=115.0)])

        runs = t.join_rows(rows)

        self.assertEqual(runs, [Run("foo-", "code"), Run("bar", "code")])

    def test_a_code_line_end_hyphen_unknown_to_the_books_code_is_joined(self):
        t = text(self)
        rows = t.rows_of([span("foo-", font=CODE, size=INLINE_CODE_SIZE, baseline=100.0),
                          span("bar", font=CODE, size=INLINE_CODE_SIZE, baseline=115.0)])

        runs = t.join_rows(rows)

        self.assertEqual(runs, [Run("foo", "code"), Run("bar", "code")])

    def test_a_line_ending_in_a_dash_or_slash_joins_the_next_line_without_a_space(self):
        for first, second, joined in [("foo—", "bar", "foo—bar"), ("foo/", "bar", "foo/bar")]:
            with self.subTest(first=first):
                t = text(self)
                rows = t.rows_of([span(first, baseline=100.0), span(second, baseline=115.0)])

                runs = t.join_rows(rows)

                self.assertEqual(runs_text(runs), joined)

    def test_a_hyphen_before_a_line_starting_with_a_non_letter_is_kept_with_a_space(self):
        t = text(self)
        rows = t.rows_of([span("hel-", baseline=100.0), span("(lo", baseline=115.0)])

        runs = t.join_rows(rows)

        self.assertEqual(runs_text(runs), "hel- (lo")


class RunsTextTests(unittest.TestCase):
    def test_the_text_of_runs_is_their_texts_in_order(self):
        runs = [Run("a ", ""), Run("b", "code"), Run("1", "fn")]

        joined = runs_text(runs)

        self.assertEqual(joined, "a b1")


if __name__ == "__main__":
    unittest.main()
