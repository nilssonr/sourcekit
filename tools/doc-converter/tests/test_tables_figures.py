"""Table regions and cells, figure captions, art boxes and listing areas from synthetic spans and drawings; no PDF or profile is needed."""

from __future__ import annotations

import unittest

import pymupdf
from doc_converter.modules.regions import figures, tables
from fixtures import (
    BLACK,
    BLUE,
    BODY,
    CAPTION,
    CODE,
    CODE_SIZE,
    FIGURE_TEXT,
    LISTING_BAR,
    FakeBook,
    figure_config,
    fill,
    span,
    stroke,
    table_config,
)

RED = "#ff0000"


class TableRegionsTests(unittest.TestCase):
    """A table between a top rule at y=200 and a bottom rule at y=300, header band down to 215."""

    def rules(self) -> list[dict]:
        return [fill(72, 200, 372, 200.5, BLACK), fill(72, 200.5, 372, 215, BLUE), fill(72, 299.5, 372, 300, BLACK)]

    def vertical_rules(self) -> list[dict]:
        return [fill(172, 200, 172.4, 300, BLACK), fill(272, 200, 272.4, 300, BLACK)]

    def test_a_rule_with_a_header_band_below_it_starts_a_region_down_to_the_vertical_rules(self):
        book = FakeBook(drawings={1: self.rules() + self.vertical_rules()})

        regions = tables.regions(book, 1, table_config())

        self.assertEqual(regions, [(pymupdf.Rect(72, 200, 372, 300), 215.0, [172.0, 272.0])])

    def test_a_header_band_without_a_rule_at_its_top_is_no_region(self):
        book = FakeBook(drawings={1: [fill(72, 200.5, 372, 215, BLUE), fill(72, 299.5, 372, 300, BLACK)]})

        regions = tables.regions(book, 1, table_config())

        self.assertEqual(regions, [])

    def test_without_vertical_rules_the_region_ends_at_the_next_horizontal_rule(self):
        book = FakeBook(drawings={1: self.rules()})

        regions = tables.regions(book, 1, table_config())

        self.assertEqual(regions, [(pymupdf.Rect(72, 200, 372, 300), 215.0, [])])


class TableDetectTests(unittest.TestCase):
    """Three columns split at x=172 and x=272; header text at baseline 210, body rows at 230/242 and 260."""

    def rules(self) -> list[dict]:
        return [fill(72, 200, 372, 200.5, BLACK), fill(72, 200.5, 372, 215, BLUE), fill(72, 299.5, 372, 300, BLACK)]

    def vertical_rules(self) -> list[dict]:
        return [fill(172, 200, 172.4, 300, BLACK), fill(272, 200, 272.4, 300, BLACK)]

    def caption(self):
        return span("Table 1.1 Things", font=CAPTION, baseline=190)

    def header_cells(self):
        return [span("Name", x0=80, baseline=210), span("Size", x0=180, baseline=210), span("Note", x0=280, baseline=210)]

    def body_cells(self):
        return [span("a", x0=80, baseline=230), span("b", x0=180, baseline=230), span("c", x0=280, baseline=230),
                span("a2", x0=80, baseline=242), span("d", x0=80, baseline=260)]

    def test_header_spans_are_assigned_to_columns_by_the_vertical_rules(self):
        name, size, note = self.header_cells()
        book = FakeBook(pages={1: [self.caption(), name, size, note, *self.body_cells()]},
                        drawings={1: self.rules() + self.vertical_rules()})

        found = tables.detect(book, 1, [].append, table_config())

        self.assertEqual(found[0].header, [[name], [size], [note]])

    def test_body_rows_split_where_the_baseline_step_exceeds_the_row_gap(self):
        a, b, c, a2, d = self.body_cells()
        book = FakeBook(pages={1: [self.caption(), *self.header_cells(), a, b, c, a2, d]},
                        drawings={1: self.rules() + self.vertical_rules()})

        found = tables.detect(book, 1, [].append, table_config())

        self.assertEqual(found[0].rows, [[[a, a2], [b], [c]], [[d], [], []]])

    def test_a_caption_just_above_the_region_gives_the_id_and_caption_spans(self):
        cap = self.caption()
        book = FakeBook(pages={1: [cap, *self.header_cells(), *self.body_cells()]},
                        drawings={1: self.rules() + self.vertical_rules()})

        found = tables.detect(book, 1, [].append, table_config())

        self.assertEqual([(t.id, t.caption) for t in found], [("1.1", [cap])])

    def test_a_table_without_a_caption_gets_a_page_based_id(self):
        book = FakeBook(pages={1: [*self.header_cells(), *self.body_cells()]},
                        drawings={1: self.rules() + self.vertical_rules()})

        found = tables.detect(book, 1, [].append, table_config())

        self.assertEqual([(t.id, t.caption) for t in found], [("uncaptioned-p1", [])])

    def test_a_table_without_a_caption_is_reported(self):
        warnings: list[str] = []
        book = FakeBook(pages={1: [*self.header_cells(), *self.body_cells()]},
                        drawings={1: self.rules() + self.vertical_rules()})

        tables.detect(book, 1, warnings.append, table_config())

        self.assertEqual(warnings, ["p1: table without a caption at y=200"])

    def test_without_vertical_rules_columns_come_from_the_gaps_in_the_header_text(self):
        name, size, note = self.header_cells()
        book = FakeBook(pages={1: [self.caption(), name, size, note, *self.body_cells()]}, drawings={1: self.rules()})

        found = tables.detect(book, 1, [].append, table_config())

        self.assertEqual(found[0].header, [[name], [size], [note]])

    def test_missing_vertical_rules_are_reported(self):
        warnings: list[str] = []
        book = FakeBook(pages={1: [self.caption(), *self.header_cells(), *self.body_cells()]}, drawings={1: self.rules()})

        tables.detect(book, 1, warnings.append, table_config())

        self.assertEqual(warnings, ["p1: table 1.1: no vertical rules; columns from header text"])

    def test_a_body_span_crossing_a_column_rule_is_reported(self):
        warnings: list[str] = []
        crossing = span("crosses it", x0=150, baseline=230)
        book = FakeBook(pages={1: [self.caption(), *self.header_cells(), crossing]},
                        drawings={1: self.rules() + self.vertical_rules()})

        tables.detect(book, 1, warnings.append, table_config())

        self.assertEqual(warnings, ["p1: table 1.1: cell text crosses a column rule: 'crosses it'"])

    def test_a_caption_start_without_a_table_is_reported(self):
        warnings: list[str] = []
        book = FakeBook(pages={1: [self.caption()]})

        tables.detect(book, 1, warnings.append, table_config())

        self.assertEqual(warnings, ["p1: table caption without a table: 'Table 1.1 Things'"])

    def test_a_table_holds_its_caption_and_cell_spans_and_starts_at_the_caption(self):
        cap, header, body = self.caption(), self.header_cells(), self.body_cells()
        book = FakeBook(pages={1: [cap, *header, *body]}, drawings={1: self.rules() + self.vertical_rules()})

        found = tables.detect(book, 1, [].append, table_config())

        self.assertEqual([(t.spans, t.top) for t in found], [([cap, *header, *body], 182.0)])


class CaptionBlockTests(unittest.TestCase):
    def start(self):
        return span("Figure 2.1 Flow", font=CAPTION, baseline=400)

    def test_the_start_row_and_the_following_caption_rows_form_the_block(self):
        start = self.start()
        same_row = span("of data", font=CAPTION, x0=160, baseline=400)
        next_row = span("continues here", font=CAPTION, baseline=412)

        block = figures.caption_block([next_row, same_row, start], start, figure_config())

        self.assertEqual(block, [start, same_row, next_row])

    def test_a_caption_row_farther_than_a_line_pitch_away_is_not_part_of_the_block(self):
        start = self.start()
        far_row = span("another caption line", font=CAPTION, baseline=430)

        block = figures.caption_block([start, far_row], start, figure_config())

        self.assertEqual(block, [start])

    def test_a_span_left_of_the_start_is_not_part_of_the_block(self):
        start = self.start()
        left = span("1", font=CAPTION, x0=60, baseline=400)

        block = figures.caption_block([left, start], start, figure_config())

        self.assertEqual(block, [start])


class FigureLabelTextsTests(unittest.TestCase):
    def test_one_span_splits_into_labels_on_runs_of_two_or_more_spaces(self):
        figure = figures.Figure("2.1", 1, pymupdf.Rect(0, 0, 10, 10), [], [span("  A   B ", font=FIGURE_TEXT)])

        self.assertEqual(figure.label_texts, ["A", "B"])


class FigureDetectTests(unittest.TestCase):
    """Art is a cluster of two red strokes at y 300-340, captioned 12 pt below it."""

    def caption(self):
        return span("Figure 2.1 Flow", font=CAPTION, baseline=360)

    def art(self) -> list[dict]:
        return [stroke(100, 300, 200, 340, RED), stroke(205, 300, 300, 340, RED)]

    def upper_art(self) -> list[dict]:
        return [stroke(100, 200, 300, 240, RED)]

    def test_a_page_without_a_caption_start_has_no_figures(self):
        book = FakeBook(pages={1: [span("hello")]}, drawings={1: self.art()})

        found = figures.detect(book, 1, [].append, figure_config())

        self.assertEqual(found, [])

    def test_a_caption_below_art_yields_a_figure_with_the_caption_id(self):
        cap = self.caption()
        book = FakeBook(pages={1: [cap]}, drawings={1: self.art()})

        found = figures.detect(book, 1, [].append, figure_config())

        self.assertEqual([(f.id, f.page, f.caption) for f in found], [("2.1", 1, [cap])])

    def test_the_figure_box_is_the_art_grown_by_three_points(self):
        book = FakeBook(pages={1: [self.caption()]}, drawings={1: self.art()})

        found = figures.detect(book, 1, [].append, figure_config())

        self.assertEqual([f.box for f in found], [pymupdf.Rect(97, 297, 303, 343)])

    def test_figure_text_inside_the_box_becomes_the_labels(self):
        label = span("Start", font=FIGURE_TEXT, x0=110, baseline=320)
        prose = span("Prose below the figure.", x0=100, baseline=420, font=BODY)
        far_label = span("Elsewhere", font=FIGURE_TEXT, x0=110, baseline=500)
        book = FakeBook(pages={1: [self.caption(), label, prose, far_label]}, drawings={1: self.art()})

        found = figures.detect(book, 1, [].append, figure_config())

        self.assertEqual([f.labels for f in found], [[label]])

    def test_drawings_above_the_header_band_are_not_art(self):
        book = FakeBook(pages={1: [self.caption()]}, drawings={1: [stroke(100, 10, 200, 35, RED)]})

        found = figures.detect(book, 1, [].append, figure_config())

        self.assertEqual(found, [])

    def test_a_fill_in_an_ignored_colour_is_not_art(self):
        book = FakeBook(pages={1: [self.caption()]}, drawings={1: [fill(100, 300, 300, 340, BLUE)]})

        found = figures.detect(book, 1, [].append, figure_config())

        self.assertEqual(found, [])

    def test_a_caption_with_no_art_near_it_is_reported_and_yields_no_figure(self):
        warnings: list[str] = []
        book = FakeBook(pages={1: [self.caption()]})

        found = figures.detect(book, 1, warnings.append, figure_config())

        self.assertEqual(found, [])
        self.assertEqual(warnings, ["p1: no figure art near caption 'Figure 2.1 Flow'"])

    def test_a_second_cluster_is_merged_into_the_box_when_no_prose_lies_between(self):
        book = FakeBook(pages={1: [self.caption()]}, drawings={1: self.art() + self.upper_art()})

        found = figures.detect(book, 1, [].append, figure_config())

        self.assertEqual([f.box for f in found], [pymupdf.Rect(97, 197, 303, 343)])

    def test_a_second_cluster_is_not_merged_across_prose(self):
        prose = span("Some prose", x0=100, baseline=275, font=BODY)
        book = FakeBook(pages={1: [self.caption(), prose]}, drawings={1: self.art() + self.upper_art()})

        found = figures.detect(book, 1, [].append, figure_config())

        self.assertEqual([f.box for f in found], [pymupdf.Rect(97, 297, 303, 343)])


class ListingAreasTests(unittest.TestCase):
    BAR = fill(72, 50, 372, 62, LISTING_BAR)

    def code(self, baseline: float):
        return span("int a;", baseline=baseline, font=CODE, size=CODE_SIZE)

    def test_a_bar_with_code_below_it_spans_to_four_points_under_the_last_code_line(self):
        book = FakeBook(pages={1: [self.code(80), self.code(90)]}, drawings={1: [self.BAR]})

        areas = figures.listing_areas(book, 1, figure_config())

        self.assertEqual(areas, [pymupdf.Rect(0, 50, 450, 95.6)])

    def test_a_code_span_more_than_thirty_points_below_the_previous_one_ends_the_area(self):
        book = FakeBook(pages={1: [self.code(80), self.code(130)]}, drawings={1: [self.BAR]})

        areas = figures.listing_areas(book, 1, figure_config())

        self.assertEqual(areas, [pymupdf.Rect(0, 50, 450, 85.6)])


if __name__ == "__main__":
    unittest.main()
