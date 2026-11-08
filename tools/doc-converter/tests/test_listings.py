"""Listing code lines, blank-line spacing and callout mapping from synthetic spans and drawings; no PDF or profile is needed."""

from __future__ import annotations

import unittest
from typing import ClassVar

import pymupdf
from doc_converter.modules.regions.listings import (
    Callout,
    CodeLine,
    code_lines,
    hexrgb,
    listing_bars,
    listing_parts,
    map_callouts,
    with_blank_lines,
)
from fixtures import (
    BLUE,
    BODY,
    CALLOUT,
    CODE,
    CODE_BOLD,
    CODE_BOLD_ITALIC,
    CODE_ITALIC,
    CODE_SIZE,
    COLUMN_LEFT,
    CW,
    LISTING_BAR,
    LISTING_HEAD,
    FakeBook,
    arrowhead,
    fill,
    leader,
    listing_config,
    span,
)


def code(text: str, baseline: float, *, x0: float = COLUMN_LEFT, font: str = CODE, page: int = 1):
    return span(text, page=page, x0=x0, baseline=baseline, font=font, size=CODE_SIZE)


class CodeLinesTests(unittest.TestCase):
    def test_characters_land_in_their_grid_columns(self):
        spans = [code("foo", 100, x0=COLUMN_LEFT + 4 * CW)]

        lines = code_lines(spans, [].append, listing_config())

        self.assertEqual([line.text for line in lines], ["    foo"])

    def test_two_spans_on_one_baseline_merge_into_one_line_in_x_order(self):
        spans = [code("b", 100, x0=COLUMN_LEFT + 2 * CW), code("a", 100)]

        lines = code_lines(spans, [].append, listing_config())

        self.assertEqual([line.text for line in lines], ["a b"])

    def test_lines_come_back_in_page_then_baseline_order(self):
        spans = [code("c", 100, page=2), code("b", 110), code("a", 100)]

        lines = code_lines(spans, [].append, listing_config())

        self.assertEqual(
            [(line.page, line.baseline, line.text) for line in lines],
            [(1, 100.0, "a"), (1, 110.0, "b"), (2, 100.0, "c")],
        )

    def test_a_line_starts_at_its_first_inked_character(self):
        spans = [code("  foo", 100)]

        lines = code_lines(spans, [].append, listing_config())

        self.assertEqual([(line.text, line.x0) for line in lines], [("  foo", 82.0)])

    def test_a_block_sitting_between_grid_cells_uses_its_own_left_edge_as_origin(self):
        warnings: list[str] = []
        spans = [code("foo", 100, x0=COLUMN_LEFT + 2.0)]

        lines = code_lines(spans, warnings.append, listing_config())

        self.assertEqual([(line.text, line.x0) for line in lines], [("foo", 74.0)])
        self.assertEqual(warnings, [])

    def test_one_character_off_the_grid_is_reported(self):
        warnings: list[str] = []
        spans = [code("a", 100), code("b", 100, x0=COLUMN_LEFT + 2 * CW + 2.0)]

        code_lines(spans, warnings.append, listing_config())

        self.assertEqual(warnings, ["code off-grid p1 col 2.40 in 'b'"])

    def test_two_different_characters_in_one_cell_are_reported(self):
        warnings: list[str] = []
        spans = [code("a", 100), code("b", 100)]

        code_lines(spans, warnings.append, listing_config())

        self.assertEqual(warnings, ["code column clash p1 col 0 'a'/'b' in 'b'"])

    def test_each_code_font_has_its_emphasis_mark(self):
        for font, mark in ((CODE, "."), (CODE_BOLD, "b"), (CODE_ITALIC, "i"), (CODE_BOLD_ITALIC, "B")):
            with self.subTest(font=font):
                spans = [code("x", 100, font=font)]

                lines = code_lines(spans, [].append, listing_config())

                self.assertEqual(lines[0].marks, mark)

    def test_a_space_between_two_bold_words_is_bold(self):
        spans = [
            code("new", 100, font=CODE_BOLD),
            code(" ", 100, x0=COLUMN_LEFT + 3 * CW),
            code("Foo", 100, x0=COLUMN_LEFT + 4 * CW, font=CODE_BOLD),
        ]

        lines = code_lines(spans, [].append, listing_config())

        self.assertEqual([(line.text, line.marks) for line in lines], [("new Foo", "bbbbbbb")])

    def test_a_space_between_a_bold_and_a_plain_word_is_plain(self):
        spans = [code("new", 100, font=CODE_BOLD), code("foo", 100, x0=COLUMN_LEFT + 4 * CW)]

        lines = code_lines(spans, [].append, listing_config())

        self.assertEqual([(line.text, line.marks) for line in lines], [("new foo", "bbb....")])

    def test_leading_spaces_stay_plain_and_trailing_spaces_are_dropped(self):
        spans = [code("  x  ", 100, font=CODE_BOLD)]

        lines = code_lines(spans, [].append, listing_config())

        self.assertEqual([(line.text, line.marks) for line in lines], [("  x", "..b")])


class WithBlankLinesTests(unittest.TestCase):
    def test_lines_one_pitch_apart_get_no_blank_line(self):
        lines = [CodeLine("a", 100.0, 1), CodeLine("b", 110.0, 1)]

        out = with_blank_lines(lines, listing_config())

        self.assertEqual(out, (["a", "b"], [0, 1]))

    def test_a_three_pitch_step_inserts_two_blank_lines(self):
        lines = [CodeLine("a", 100.0, 1), CodeLine("b", 130.0, 1)]

        out = with_blank_lines(lines, listing_config())

        self.assertEqual(out, (["a", "", "", "b"], [0, 3]))

    def test_positions_index_each_code_line_in_the_output(self):
        lines = [CodeLine("a", 100.0, 1), CodeLine("b", 120.0, 1)]

        out, pos = with_blank_lines(lines, listing_config())

        self.assertEqual([out[i] for i in pos], ["a", "b"])

    def test_no_blank_lines_are_inserted_across_a_page_change(self):
        lines = [CodeLine("a", 600.0, 1), CodeLine("b", 100.0, 2)]

        out = with_blank_lines(lines, listing_config())

        self.assertEqual(out, (["a", "b"], [0, 1]))

    def test_the_marks_attribute_gives_the_emphasis_of_the_same_lines(self):
        lines = [CodeLine("ab", 100.0, 1, marks="b."), CodeLine("cd", 120.0, 1, marks=".i")]

        out = with_blank_lines(lines, listing_config(), attr="marks")

        self.assertEqual(out, (["b.", "", ".i"], [0, 2]))


class ListingPartsTests(unittest.TestCase):
    BAR = pymupdf.Rect(72, 50, 372, 62)

    def head(self):
        return span("Listing 1.1 Foo", baseline=58, font=LISTING_HEAD)

    def body(self):
        """Two code lines with a callout beside the first, sorted by (baseline, x0)."""
        return [
            code("int a;", 80),
            span("Sets a", x0=300, baseline=80, font=CALLOUT, size=CODE_SIZE),
            code("int b;", 90),
        ]

    def test_spans_centred_in_the_bar_are_the_head(self):
        head = self.head()
        spans = [head, *self.body()]

        parts = listing_parts(spans, self.BAR, set(), listing_config())

        self.assertEqual(parts[0], [head])

    def test_code_and_callout_spans_below_the_bar_are_the_body_in_order(self):
        body = self.body()
        spans = [self.head(), *body]

        parts = listing_parts(spans, self.BAR, set(), listing_config())

        self.assertEqual(parts[1], body)

    def test_the_body_stops_at_the_first_prose_span(self):
        body = self.body()
        spans = [self.head(), *body, span("Prose follows.", baseline=110, font=BODY), code("int c;", 120)]

        parts = listing_parts(spans, self.BAR, set(), listing_config())

        self.assertEqual(parts[1], body)

    def test_consumed_spans_are_added_to_used(self):
        head, body = self.head(), self.body()
        used: set[int] = set()

        listing_parts([head, *body], self.BAR, used, listing_config())

        self.assertEqual(used, {id(head), id(body[0]), id(body[1]), id(body[2])})

    def test_spans_already_used_are_skipped(self):
        body = self.body()
        used = {id(body[0])}

        parts = listing_parts([self.head(), *body], self.BAR, used, listing_config())

        self.assertEqual(parts[1], [body[1], body[2]])


class ListingBarsTests(unittest.TestCase):
    def test_wide_fills_in_the_bar_colour_are_bars_sorted_by_top(self):
        book = FakeBook(drawings={1: [fill(72, 300, 372, 312, LISTING_BAR), fill(72, 50, 372, 62, LISTING_BAR)]})

        bars = listing_bars(book, 1, listing_config())

        self.assertEqual(bars, [pymupdf.Rect(72, 50, 372, 62), pymupdf.Rect(72, 300, 372, 312)])

    def test_a_narrow_fill_in_the_bar_colour_is_not_a_bar(self):
        book = FakeBook(drawings={1: [fill(72, 50, 150, 62, LISTING_BAR)]})

        bars = listing_bars(book, 1, listing_config())

        self.assertEqual(bars, [])

    def test_a_wide_fill_in_another_colour_is_not_a_bar(self):
        book = FakeBook(drawings={1: [fill(72, 50, 372, 62, BLUE)]})

        bars = listing_bars(book, 1, listing_config())

        self.assertEqual(bars, [])


class HexrgbTests(unittest.TestCase):
    def test_unit_floats_become_a_hex_colour(self):
        self.assertEqual(hexrgb((1.0, 0.0, 0.0)), "#ff0000")

    def test_no_colour_stays_none(self):
        self.assertIsNone(hexrgb(None))


class CalloutTests(unittest.TestCase):
    def test_top_is_the_smallest_y0_of_its_spans(self):
        callout = Callout([span("a", baseline=120, font=CALLOUT), span("b", baseline=100, font=CALLOUT)])

        self.assertEqual(callout.top, 92.0)


class MapCalloutsTests(unittest.TestCase):
    """Four code lines with baselines 100-130 (centres 98-128), callout text to the right of the code."""

    AREA = pymupdf.Rect(0, 90, 450, 140)
    LINES: ClassVar[list[CodeLine]] = [
        CodeLine("int a;", 100.0, 1, COLUMN_LEFT),
        CodeLine("int b;", 110.0, 1, COLUMN_LEFT),
        CodeLine("int c;", 120.0, 1, COLUMN_LEFT),
        CodeLine("int d;", 130.0, 1, COLUMN_LEFT),
    ]
    INDEX: ClassVar[list[int]] = [0, 1, 2, 3]

    def callout(self, baseline: float, x0: float = 300):
        return span("Sets a", x0=x0, baseline=baseline, font=CALLOUT, size=CODE_SIZE)

    def arrow(self, y: float, x1: float, colour: str = "#000000"):
        """A leader from x=150 to x1 at height y with its arrowhead at the left end."""
        return [leader(150, y - 0.2, x1, y + 0.2, colour), arrowhead(148, y, colour)]

    def map(self, book: FakeBook, callouts, warnings: list[str]):
        return map_callouts(book, 1, self.AREA, self.LINES, self.INDEX, callouts, warnings.append, listing_config())

    def test_an_arrow_tip_names_the_code_line_it_touches(self):
        text = self.callout(100)
        book = FakeBook(drawings={1: self.arrow(98, 290)})

        callouts = self.map(book, [text], [])

        self.assertEqual([(c.kind, c.lines, c.spans) for c in callouts], [("arrow", [1], [text])])

    def test_two_arrowheads_on_one_leader_name_both_lines(self):
        text = self.callout(100)
        drawings = [
            leader(150, 97.8, 290, 98.2),
            leader(199.8, 98, 200.2, 118),
            leader(150, 117.8, 200, 118.2),
            arrowhead(148, 98),
            arrowhead(148, 118),
        ]
        book = FakeBook(drawings={1: drawings})

        callouts = self.map(book, [text], [])

        self.assertEqual([(c.kind, c.lines) for c in callouts], [("arrow", [1, 3])])

    def test_a_bracket_bar_names_every_line_it_spans(self):
        text = self.callout(115, x0=210)
        book = FakeBook(drawings={1: [leader(200, 106.5, 200.4, 119.5)]})

        callouts = self.map(book, [text], [])

        self.assertEqual([(c.kind, c.lines) for c in callouts], [("bracket", [2, 3])])

    def test_a_callout_without_a_graphic_maps_to_no_lines(self):
        book = FakeBook()

        callouts = self.map(book, [self.callout(100)], [])

        self.assertEqual([(c.kind, c.lines) for c in callouts], [("none", [])])

    def test_a_callout_without_a_graphic_is_reported(self):
        warnings: list[str] = []
        book = FakeBook()

        self.map(book, [self.callout(100)], warnings)

        self.assertEqual(warnings, ["p1: callout 'Sets a' has no graphic"])

    def test_a_graphic_farther_than_twelve_points_from_the_text_is_not_attached(self):
        book = FakeBook(drawings={1: self.arrow(98, 270)})

        callouts = self.map(book, [self.callout(100)], [])

        self.assertEqual([(c.kind, c.lines) for c in callouts], [("none", [])])

    def test_graphics_not_in_the_callout_ink_are_ignored(self):
        for colour in (LISTING_BAR, BLUE):
            with self.subTest(colour=colour):
                book = FakeBook(drawings={1: self.arrow(98, 290, colour)})

                callouts = self.map(book, [self.callout(100)], [])

                self.assertEqual([(c.kind, c.lines) for c in callouts], [("none", [])])


if __name__ == "__main__":
    unittest.main()
