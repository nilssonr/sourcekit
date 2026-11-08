"""Assembler.section: how roled rows, code runs and figures become blocks. Needs no PDF or profile:
pages are lists of synthetic spans on a fake Book."""

from __future__ import annotations

import unittest

from fixtures import (
    BULLET,
    CALLOUT,
    CAPTION,
    CODE,
    CODE_SIZE,
    COLUMN_RIGHT,
    CONTINUED,
    COVERS_BULLET,
    COVERS_CONT,
    COVERS_TITLE,
    DROPCAP,
    FN_MARKER,
    FN_START_SIZE,
    FOOTNOTE_TEXT,
    H2,
    LIST_NUMBER,
    LISTING_BAR,
    LISTING_HEAD,
    MAP_TITLE,
    NOTE_LABEL,
    PAGENUM,
    SIDEBAR_BG,
    SIDEBAR_TEXT,
    SIDEBAR_TITLE,
    SMALLCAPS,
    TITLE,
    FakeBook,
    assembler,
    fill,
    span,
    stroke,
)

from doc_converter.core.model import Code, Figure, Heading, Label, ListBlock, Note, Paragraph, Sidebar
from doc_converter.core.rows import runs_text
from doc_converter.core.sections import Section

FULL = COLUMN_RIGHT          # a line whose ink reaches this is a full line
TEXT_X = 84.0                # where list and note text starts, after a 12 pt marker


def shape(block) -> tuple:
    """A block as plain data: its kind and the text that matters, so expectations are literals."""
    match block:
        case Paragraph():
            return ("para", block.dropcap + runs_text(block.inlines))
        case Heading():
            return (f"h{block.level}", runs_text(block.inlines))
        case Label():
            return ("label", runs_text(block.inlines))
        case ListBlock():
            return ("list", [(it.number, runs_text(it.inlines), [runs_text(s) for s in it.subitems]) for it in block.items])
        case Note():
            return ("note", block.label, runs_text(block.inlines), [shape(c) for c in block.continued])
        case Sidebar():
            return ("sidebar", runs_text(block.title), [shape(c) for c in block.children])
        case Code():
            return ("code", block.lines)
        case Figure():
            return ("figure", block.id)
    raise TypeError(type(block).__name__)


def full(text: str, baseline: float, page: int = 1, **kw):
    return span(text, baseline=baseline, page=page, x1=FULL, **kw)


def short(text: str, baseline: float, page: int = 1, **kw):
    return span(text, baseline=baseline, page=page, **kw)


class FlowCase(unittest.TestCase):
    def assemble(self, book: FakeBook, pages=(1, 1), *, title: str = "", heading_shift: int = 0, **kw):
        """Blocks and warnings for one section over ``pages``."""
        asm, warnings = assembler(self, book, **kw)
        doc = asm.section(Section("sec", "sec.md", pages, title, heading_shift))
        return [shape(b) for b in doc.blocks], warnings


class ParagraphTests(FlowCase):
    def test_full_lines_and_a_short_last_line_form_one_paragraph(self):
        book = FakeBook({1: [full("First line of the paragraph that", 100), full("fills the measure and goes on", 114),
                             short("and ends here.", 128)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("para", "First line of the paragraph that fills the measure and goes on and ends here.")])

    def test_an_indented_line_starts_a_new_paragraph(self):
        book = FakeBook({1: [full("The first paragraph is one full line.", 100),
                             full("The second starts indented and", 114, x0=82.0), short("ends short.", 128)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("para", "The first paragraph is one full line."),
                                  ("para", "The second starts indented and ends short.")])

    def test_a_short_line_ends_its_paragraph(self):
        book = FakeBook({1: [short("A short line.", 100), full("The next full line is a new paragraph.", 114)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("para", "A short line."), ("para", "The next full line is a new paragraph.")])

    def test_a_step_wider_than_the_paragraph_gap_starts_a_new_paragraph(self):
        book = FakeBook({1: [full("A full line that is followed by a gap.", 100),
                             full("A full line after an eighteen point gap.", 118)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("para", "A full line that is followed by a gap."),
                                  ("para", "A full line after an eighteen point gap.")])

    def test_a_paragraph_continues_across_a_page_break(self):
        book = FakeBook({1: [full("The last line of page one is full and", 650)],
                         2: [short("continues on page two.", 60, page=2)]})

        blocks, _ = self.assemble(book, pages=(1, 2))

        self.assertEqual(blocks, [("para", "The last line of page one is full and continues on page two.")])

    def test_the_most_common_body_left_edge_is_the_column(self):
        # every body line starts at 82, so 82 is the column and none of them is indented
        book = FakeBook({1: [full("Lines that all start ten points right", 100, x0=82.0),
                             full("of the configured column are not", 114, x0=82.0), short("indented.", 128, x0=82.0)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("para", "Lines that all start ten points right of the configured column are not indented.")])

    def test_on_an_odd_page_the_right_edge_moves_by_odd_shift(self):
        # a line ending at 365 is full on an even page (edge 372 - slack 10) but not on an odd page (edge 381 - 10)
        even = FakeBook({2: [span("A line ending at 365", baseline=100, page=2, x1=365.0),
                             short("and the next line.", 114, page=2)]})
        odd = FakeBook({3: [span("A line ending at 365", baseline=100, page=3, x1=365.0),
                            short("and the next line.", 114, page=3)]})

        on_even, _ = self.assemble(even, pages=(2, 2), odd_shift=9.0)
        on_odd, _ = self.assemble(odd, pages=(3, 3), odd_shift=9.0)

        self.assertEqual(on_even, [("para", "A line ending at 365 and the next line.")])
        self.assertEqual(on_odd, [("para", "A line ending at 365"), ("para", "and the next line.")])

    def test_a_line_end_hyphen_is_resolved_when_lines_join(self):
        book = FakeBook({1: [full("The converter joins words at a line-end hy-", 100), short("phen.", 114)]})

        blocks, _ = self.assemble(book, corpus=["hyphen"])

        self.assertEqual(blocks, [("para", "The converter joins words at a line-end hyphen.")])


class ListTests(FlowCase):
    def item(self, text: str, baseline: float, marker: str = "•", font: str = BULLET, page: int = 1):
        return [span(marker, baseline=baseline, page=page, font=font), span(text, baseline=baseline, page=page, x0=TEXT_X)]

    def test_bullet_rows_become_one_list_with_an_item_each(self):
        book = FakeBook({1: self.item("first item", 100) + self.item("second item", 116)})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("list", [(None, "first item", []), (None, "second item", [])])])

    def test_a_body_line_aligned_with_the_item_text_continues_the_item(self):
        book = FakeBook({1: self.item("an item whose text", 100) + [span("wraps to a second line", baseline=114, x0=TEXT_X)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("list", [(None, "an item whose text wraps to a second line", [])])])

    def test_a_body_line_at_the_column_ends_the_list(self):
        book = FakeBook({1: self.item("an item", 100) + [short("A paragraph after the list.", 114)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("list", [(None, "an item", [])]), ("para", "A paragraph after the list.")])

    def test_a_dash_line_under_an_item_is_a_sub_item_without_its_dash(self):
        book = FakeBook({1: self.item("an item", 100) + [span("– a sub-item", baseline=114, x0=TEXT_X)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("list", [(None, "an item", ["a sub-item"])])])

    def test_a_numbered_item_keeps_its_number(self):
        book = FakeBook({1: self.item("first step", 100, marker="1", font=LIST_NUMBER)
                         + self.item("second step", 116, marker="2", font=LIST_NUMBER)})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("list", [("1", "first step", []), ("2", "second step", [])])])

    def test_a_bullet_closes_an_open_paragraph(self):
        book = FakeBook({1: [full("An introductory sentence that ends in a colon:", 100)] + self.item("an item", 114)})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("para", "An introductory sentence that ends in a colon:"), ("list", [(None, "an item", [])])])


class NoteTests(FlowCase):
    def note(self, label: str, text: str, baseline: float):
        return [span(label, baseline=baseline, font=NOTE_LABEL), span(text, baseline=baseline, x0=120.0)]

    def test_a_label_row_starts_a_note_with_that_label(self):
        book = FakeBook({1: self.note("TIP", "Keep tests short.", 100)})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("note", "TIP", "Keep tests short.", [])])

    def test_a_body_line_aligned_with_the_label_continues_the_note(self):
        book = FakeBook({1: self.note("NOTE", "A note that wraps", 100) + [short("onto a second line.", 114)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("note", "NOTE", "A note that wraps onto a second line.", [])])

    def test_a_body_line_not_aligned_with_the_label_closes_the_note(self):
        book = FakeBook({1: self.note("NOTE", "A one-line note.", 100) + [short("A paragraph.", 114, x0=90.0)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("note", "NOTE", "A one-line note.", []), ("para", "A paragraph.")])

    def test_a_bullet_closes_the_note(self):
        book = FakeBook({1: self.note("NOTE", "A note before a list.", 100)
                         + [span("•", baseline=114, font=BULLET), span("an item", baseline=114, x0=TEXT_X)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("note", "NOTE", "A note before a list.", []), ("list", [(None, "an item", [])])])


class SidebarTests(FlowCase):
    SIDEBAR_FULL = COLUMN_RIGHT - 12  # the sidebar measure is narrower than the body

    def sidebar_line(self, text: str, baseline: float, page: int = 1, *, is_full: bool = True, x0: float = 72.0):
        kw = {"x1": self.SIDEBAR_FULL} if is_full else {}
        return span(text, baseline=baseline, page=page, font=SIDEBAR_TEXT, x0=x0, **kw)

    def test_a_title_row_and_its_text_rows_form_a_sidebar_with_one_paragraph(self):
        book = FakeBook({1: [span("A sidebar", baseline=100, font=SIDEBAR_TITLE),
                             self.sidebar_line("Sidebar text that is a full line", 114),
                             self.sidebar_line("and a short last line.", 128, is_full=False)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("sidebar", "A sidebar", [("para", "Sidebar text that is a full line and a short last line.")])])

    def test_a_short_line_splits_sidebar_text_into_paragraphs(self):
        book = FakeBook({1: [span("A sidebar", baseline=100, font=SIDEBAR_TITLE),
                             self.sidebar_line("First paragraph.", 114, is_full=False),
                             self.sidebar_line("Second paragraph.", 128, is_full=False)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("sidebar", "A sidebar", [("para", "First paragraph."), ("para", "Second paragraph.")])])

    def test_bullets_inside_a_sidebar_form_a_list_in_it(self):
        book = FakeBook({1: [span("A sidebar", baseline=100, font=SIDEBAR_TITLE),
                             span("•", baseline=114, font=BULLET), self.sidebar_line("one", 114, is_full=False, x0=TEXT_X),
                             span("•", baseline=130, font=BULLET), self.sidebar_line("two", 130, is_full=False, x0=TEXT_X)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("sidebar", "A sidebar", [("list", [(None, "one", []), (None, "two", [])])])])

    def test_a_body_line_closes_the_sidebar(self):
        book = FakeBook({1: [span("A sidebar", baseline=100, font=SIDEBAR_TITLE),
                             self.sidebar_line("Sidebar text.", 114, is_full=False), short("Body text.", 128)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("sidebar", "A sidebar", [("para", "Sidebar text.")]), ("para", "Body text.")])

    def test_a_continued_label_reopens_the_sidebar_from_the_previous_page(self):
        book = FakeBook({1: [span("A sidebar", baseline=100, font=SIDEBAR_TITLE),
                             self.sidebar_line("Text on page one.", 114, is_full=False)],
                         2: [span("(continued)", baseline=60, page=2, font=CONTINUED),
                             self.sidebar_line("Text on page two.", 74, page=2, is_full=False)]})

        blocks, _ = self.assemble(book, pages=(1, 2))

        self.assertEqual(blocks, [("sidebar", "A sidebar", [("para", "Text on page one."), ("para", "Text on page two.")])])

    def test_a_continued_label_with_nothing_to_continue_warns(self):
        book = FakeBook({1: [span("(continued)", baseline=100, font=CONTINUED)]})

        blocks, warnings = self.assemble(book)

        self.assertEqual(blocks, [])
        self.assertEqual(warnings, ["(continued) without a sidebar p1"])

    def test_sidebar_text_without_a_title_warns_and_opens_an_untitled_sidebar(self):
        book = FakeBook({1: [self.sidebar_line("Orphan sidebar text.", 100, is_full=False)]})

        blocks, warnings = self.assemble(book)

        self.assertEqual(blocks, [("sidebar", "", [("para", "Orphan sidebar text.")])])
        self.assertEqual(warnings, ["sidebar text without sidebar p1: 'Orphan sidebar text.'"])


class FigureTests(FlowCase):
    """Figures through the page-figure path (an uncaptioned figure named in page_figures)."""

    ART = {1: [stroke(100, 300, 200, 400, "#ff0000")]}

    def test_a_figure_between_paragraphs_is_emitted_in_place(self):
        book = FakeBook({1: [short("Before the figure.", 100), short("After the figure.", 500)]}, drawings=self.ART)

        blocks, _ = self.assemble(book, page_figures={1: ("map", 0)}, descriptions={"map": "A map."})

        self.assertEqual(blocks, [("para", "Before the figure."), ("figure", "map"), ("para", "After the figure.")])

    def test_a_figure_inside_an_open_paragraph_is_emitted_after_it(self):
        # the figure sits between two full lines of one paragraph; the paragraph stays whole
        book = FakeBook({1: [full("A paragraph whose lines surround", 380), full("the figure art and", 394),
                             short("carry on past it.", 408)]}, drawings=self.ART)

        blocks, _ = self.assemble(book, page_figures={1: ("map", 0)}, descriptions={"map": "A map."})

        self.assertEqual(blocks, [("para", "A paragraph whose lines surround the figure art and carry on past it."),
                                  ("figure", "map")])

    def test_a_figure_without_a_description_warns(self):
        book = FakeBook({1: []}, drawings=self.ART)

        _, warnings = self.assemble(book, page_figures={1: ("map", 0)})

        self.assertEqual(warnings, ["no description for map"])


class HeadingTests(FlowCase):
    def test_a_heading_row_becomes_a_heading_of_its_level(self):
        book = FakeBook({1: [span("A second-level heading", baseline=100, font=H2)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("h2", "A second-level heading")])

    def test_heading_shift_raises_every_heading_level(self):
        book = FakeBook({1: [span("A heading", baseline=100, font=H2)]})

        blocks, _ = self.assemble(book, heading_shift=1)

        self.assertEqual(blocks, [("h3", "A heading")])

    def test_a_second_heading_row_close_below_continues_the_heading(self):
        book = FakeBook({1: [span("Refactoring toward", baseline=100, font=H2), span("valuable tests", baseline=114, font=H2)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("h2", "Refactoring toward valuable tests")])

    def test_a_wrapped_heading_resolves_its_line_end_hyphen(self):
        book = FakeBook({1: [span("Why inte-", baseline=100, font=H2), span("gration testing", baseline=114, font=H2)]})

        blocks, _ = self.assemble(book, corpus=["integration"])

        self.assertEqual(blocks, [("h2", "Why integration testing")])

    def test_a_heading_row_starting_with_a_digit_is_a_new_heading(self):
        book = FakeBook({1: [span("Summary", baseline=100, font=H2), span("2 Next steps", baseline=114, font=H2)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("h2", "Summary"), ("h2", "2 Next steps")])

    def test_a_heading_row_far_below_is_a_new_heading(self):
        book = FakeBook({1: [span("First", baseline=100, font=H2), span("Second", baseline=130, font=H2)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("h2", "First"), ("h2", "Second")])

    def test_two_title_rows_of_an_untitled_section_become_one_heading_joined_by_a_colon(self):
        book = FakeBook({1: [span("Part 1", baseline=100, font=TITLE), span("The bigger picture", baseline=140, font=TITLE)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("h1", "Part 1: The bigger picture")])

    def test_the_section_title_from_the_outline_replaces_the_title_row(self):
        book = FakeBook({1: [span("The goal of unit testing", baseline=100, font=TITLE), short("Body.", 140)]})

        blocks, _ = self.assemble(book, title="Chapter 1: The goal of unit testing")

        self.assertEqual(blocks, [("h1", "Chapter 1: The goal of unit testing"), ("para", "Body.")])

    def test_a_title_row_in_a_shifted_section_is_a_shifted_heading(self):
        book = FakeBook({1: [span("Preface", baseline=100, font=TITLE)]})

        blocks, _ = self.assemble(book, heading_shift=1)

        self.assertEqual(blocks, [("h2", "Preface")])

    def test_small_capitals_below_the_size_limit_are_lower_cased(self):
        book = FakeBook({1: [span("T", baseline=100, font=SMALLCAPS, size=9.0),
                             span("HIS CHAPTER", baseline=100, font=SMALLCAPS, size=7.0, x0=77.0)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("h4", "This chapter")])

    def test_a_drop_cap_is_attached_to_the_paragraph_that_follows(self):
        book = FakeBook({1: [span("T", baseline=100, font=DROPCAP, size=44.0), short("he rest of the sentence.", 100, x0=100.0)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("para", "The rest of the sentence.")])

    def test_a_map_title_row_is_a_second_level_heading(self):
        book = FakeBook({1: [span("Part 1 The bigger picture", baseline=100, font=MAP_TITLE)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("h2", "Part 1 The bigger picture")])


class CoversTests(FlowCase):
    def test_a_covers_title_and_its_items_become_a_label_and_a_list(self):
        book = FakeBook({1: [span("This chapter covers", baseline=100, font=COVERS_TITLE),
                             span("•", baseline=116, font=COVERS_BULLET), span("The goal", baseline=116, x0=TEXT_X),
                             span("•", baseline=132, font=COVERS_BULLET), span("The cost", baseline=132, x0=TEXT_X)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("label", "This chapter covers"), ("list", [(None, "The goal", []), (None, "The cost", [])])])

    def test_a_covers_continuation_row_extends_the_last_item(self):
        book = FakeBook({1: [span("This chapter covers", baseline=100, font=COVERS_TITLE),
                             span("•", baseline=116, font=COVERS_BULLET), span("An item that", baseline=116, x0=TEXT_X),
                             span("wraps", baseline=130, font=COVERS_CONT, x0=TEXT_X)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("label", "This chapter covers"), ("list", [(None, "An item that wraps", [])])])

    def test_a_covers_continuation_without_an_item_warns(self):
        book = FakeBook({1: [span("stray", baseline=100, font=COVERS_CONT)]})

        blocks, warnings = self.assemble(book)

        self.assertEqual(blocks, [])
        self.assertEqual(warnings, ["covers text without item p1"])


class SkippedRowTests(FlowCase):
    def test_running_headers_page_numbers_and_footnote_text_produce_no_block(self):
        book = FakeBook({1: [span("Running header", baseline=30), span("12", baseline=680, font=PAGENUM),
                             span("Footnote text", baseline=640, font=FOOTNOTE_TEXT), short("Body.", 100)]})

        blocks, warnings = self.assemble(book)

        self.assertEqual(blocks, [("para", "Body.")])
        self.assertEqual(warnings, [])

    def test_a_caption_row_that_no_figure_or_table_claimed_warns(self):
        # a caption-font row that is not a caption start, so no figure detection runs for it
        book = FakeBook({1: [span("continues the caption of nothing", baseline=100, font=CAPTION)]})

        blocks, warnings = self.assemble(book)

        self.assertEqual(blocks, [])
        self.assertEqual(warnings, ["stray caption row p1: 'continues the caption of nothing'"])

    def test_a_row_of_an_unknown_style_warns_with_its_font_size_and_colour(self):
        book = FakeBook({1: [span("Mystery", baseline=100, font="Weird", size=11.0, color="#123456")]})

        blocks, warnings = self.assemble(book)

        self.assertEqual(blocks, [])
        self.assertEqual(warnings, ["unclassified row p1 [Weird/11.0/#123456]: 'Mystery'"])

    def test_a_page_listed_in_skip_pages_contributes_nothing(self):
        book = FakeBook({1: [short("Page one.", 100)], 2: [short("Page two.", 100, page=2)]})

        blocks, _ = self.assemble(book, pages=(1, 2), skip_pages=frozenset({2}))

        self.assertEqual(blocks, [("para", "Page one.")])


class CodeTests(FlowCase):
    def code(self, text: str, baseline: float, page: int = 1, x0: float = 72.0):
        return span(text, baseline=baseline, page=page, font=CODE, size=CODE_SIZE, x0=x0)

    def listing_page(self, page: int, title: str, lines: list[str]):
        """A listing: a bar with its head inside, then code lines 10 pt apart."""
        spans = [span(title, baseline=70, page=page, font=LISTING_HEAD, color="#ffffff")]
        spans += [self.code(t, 90 + 10 * i, page) for i, t in enumerate(lines)]
        return spans, [fill(60, 60, 380, 76, LISTING_BAR)]

    def test_a_run_of_code_lines_becomes_one_code_block(self):
        book = FakeBook({1: [self.code("int a = 1;", 100), self.code("int b = 2;", 110), short("Prose after.", 140)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("code", ["int a = 1;", "int b = 2;"]), ("para", "Prose after.")])

    def test_code_inside_a_sidebar_box_belongs_to_the_sidebar(self):
        book = FakeBook({1: [span("A sidebar", baseline=100, font=SIDEBAR_TITLE),
                             span("Some text.", baseline=114, font=SIDEBAR_TEXT),
                             self.code("x = 1", 130, x0=80.0)]},
                        drawings={1: [fill(60, 90, 390, 150, SIDEBAR_BG)]})

        blocks, _ = self.assemble(book)

        self.assertEqual(blocks, [("sidebar", "A sidebar", [("para", "Some text."), ("code", ["x = 1"])])])

    def test_code_at_the_top_of_the_next_page_continues_the_listing(self):
        spans1, bars1 = self.listing_page(1, "Listing 1.1 A listing", ["void f() {", "}"])
        book = FakeBook({1: spans1, 2: [self.code("int g;", 60, page=2)]}, drawings={1: bars1})

        blocks, warnings = self.assemble(book, pages=(1, 2))

        self.assertEqual(blocks, [("code", ["void f() {", "}", "", "int g;"])])
        self.assertEqual(warnings, ["listing 1.1 continued across pages 1-2"])

    def test_code_with_a_callout_is_named_after_its_page_label_and_numbered_from_the_second_block(self):
        # the callouts have no arrow graphics, so each block also warns that its callout has none
        book = FakeBook({1: [self.code("a = 1", 100), span("First", baseline=100, font=CALLOUT, x0=200.0),
                             short("Prose between.", 130),
                             self.code("b = 2", 160), span("Second", baseline=160, font=CALLOUT, x0=200.0)]},
                        labels={1: "12"})
        asm, _ = assembler(self, book)

        doc = asm.section(Section("sec", "sec.md", (1, 1)))

        codes = [b for b in doc.blocks if isinstance(b, Code)]
        self.assertEqual([c.lid for c in codes], ["p12", "p12-2"])
        self.assertEqual([img.file for c in codes for img in c.images], ["code-p12.png", "code-p12-2.png"])


class FootnoteTests(FlowCase):
    def footnote(self, num: str, text: str, baseline: float, page: int = 1):
        return [span(num, baseline=baseline, page=page, font=FN_MARKER, size=FN_START_SIZE),
                span(text, baseline=baseline, page=page, font=FOOTNOTE_TEXT, x0=78.0)]

    def footnotes(self, book: FakeBook, pages=(1, 1)):
        asm, _ = assembler(self, book)
        doc = asm.section(Section("sec", "sec.md", pages))
        return [(f.num, runs_text(f.inlines)) for f in doc.footnotes]

    def test_a_start_marker_row_is_a_footnote_without_the_marker_in_its_text(self):
        book = FakeBook({1: self.footnote("1", "A footnote.", 640)})

        self.assertEqual(self.footnotes(book), [("1", "A footnote.")])

    def test_a_footnote_text_row_on_the_same_page_continues_the_footnote(self):
        book = FakeBook({1: self.footnote("1", "A footnote that", 640) + [span("wraps.", baseline=652, font=FOOTNOTE_TEXT)]})

        self.assertEqual(self.footnotes(book), [("1", "A footnote that wraps.")])

    def test_footnote_text_on_another_page_does_not_continue_it(self):
        book = FakeBook({1: self.footnote("1", "Ends on page one.", 640),
                         2: [span("Unrelated small text.", baseline=60, page=2, font=FOOTNOTE_TEXT)]})

        self.assertEqual(self.footnotes(book, pages=(1, 2)), [("1", "Ends on page one.")])

    def test_two_footnotes_on_one_page_are_separate(self):
        book = FakeBook({1: self.footnote("1", "First.", 630) + self.footnote("2", "Second.", 645)})

        self.assertEqual(self.footnotes(book), [("1", "First."), ("2", "Second.")])


if __name__ == "__main__":
    unittest.main()
