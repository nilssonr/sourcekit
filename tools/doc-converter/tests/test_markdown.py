"""Markdown rendering of the document model and its added-material ranges: pure functions, no PDF or profile needed."""

from __future__ import annotations

import unittest

import pymupdf
from doc_converter.core.model import (
    Block,
    Callout,
    Code,
    Figure,
    Footnote,
    Heading,
    ImageRef,
    Label,
    ListBlock,
    ListItem,
    Note,
    Paragraph,
    Run,
    SectionDoc,
    Sidebar,
    Table,
)
from doc_converter.core.sections import Section
from doc_converter.core.verify import markup_emphasis, without_added
from doc_converter.modules.serialize.markdown import (
    Markdown,
    added,
    extract_added,
    fmt_lines,
    has_emphasis,
    pre,
    render_runs,
)
from fixtures import markdown_config

CALLOUT_HEADER = "*Callouts (added in conversion; line numbers count every line of the listing, blank lines included):*"
DESCRIPTION = "> **Illustration description (added in conversion, not book text):** "


def plain(text: str) -> list[Run]:
    return [Run(text, "")]


def image(file: str = "f.png") -> ImageRef:
    return ImageRef(file, "figure", 1, pymupdf.Rect(0, 0, 10, 10))


class RenderRunsTests(unittest.TestCase):
    def test_same_style_runs_merge_into_one_span(self):
        runs = [Run("a", "b"), Run("b", "b")]

        out = render_runs(runs)

        self.assertEqual(out, "**ab**")

    def test_a_footnote_run_never_merges_with_its_neighbour(self):
        runs = [Run("1", "fn"), Run("2", "fn")]

        out = render_runs(runs)

        self.assertEqual(out, "[^1][^2]")

    def test_whitespace_between_two_bold_runs_joins_them_into_one_span(self):
        runs = [Run("a", "b"), Run(" ", ""), Run("b", "b")]

        out = render_runs(runs)

        self.assertEqual(out, "**a b**")

    def test_whitespace_between_two_code_runs_keeps_two_spans(self):
        runs = [Run("a", "code"), Run(" ", ""), Run("b", "code")]

        out = render_runs(runs)

        self.assertEqual(out, "`a` `b`")

    def test_emphasis_markers_sit_inside_the_surrounding_whitespace(self):
        runs = [Run("say", ""), Run(" x ", "i"), Run("now", "")]

        out = render_runs(runs)

        self.assertEqual(out, "say *x* now")

    def test_markdown_characters_are_escaped_outside_code(self):
        runs = [Run(r"a\b `c` *d* _e_", "")]

        out = render_runs(runs)

        self.assertEqual(out, r"a\\b \`c\` \*d\* \_e\_")

    def test_markdown_characters_are_not_escaped_inside_code(self):
        runs = [Run(r"a\b*c_d", "code")]

        out = render_runs(runs)

        self.assertEqual(out, r"`a\b*c_d`")

    def test_a_code_run_containing_a_backtick_uses_double_backticks(self):
        runs = [Run("a`b", "code")]

        out = render_runs(runs)

        self.assertEqual(out, "``a`b``")

    def test_a_footnote_run_becomes_a_footnote_reference(self):
        runs = [Run("3", "fn")]

        out = render_runs(runs)

        self.assertEqual(out, "[^3]")

    def test_a_leading_number_with_a_dot_is_escaped(self):
        runs = [Run("1. one", "")]

        out = render_runs(runs)

        self.assertEqual(out, "1\\. one")

    def test_a_leading_list_or_heading_or_quote_character_is_escaped(self):
        for ch in ("-", "+", "#", ">"):
            with self.subTest(ch=ch):
                out = render_runs([Run(f"{ch} x", "")])

                self.assertEqual(out, f"\\{ch} x")

    def test_runs_of_spaces_collapse_to_one(self):
        runs = [Run("a  b", "")]

        out = render_runs(runs)

        self.assertEqual(out, "a b")

    def test_runs_of_spaces_collapse_inside_a_code_run_too(self):
        # pins current behaviour: the final collapse runs over the whole line, code spans included
        runs = [Run("a  b", "code")]

        out = render_runs(runs)

        self.assertEqual(out, "`a b`")


class FormatLinesTests(unittest.TestCase):
    def test_line_lists_are_written_as_ranges(self):
        for lines, expected in (([], "(no line)"), ([3], "line 3"), ([1, 2, 3], "lines 1–3"), ([1, 2, 5], "lines 1–2, 5")):
            with self.subTest(lines=lines):
                out = fmt_lines(lines)

                self.assertEqual(out, expected)


class BlockTests(unittest.TestCase):
    md = Markdown(markdown_config())

    def _clean(self, block: Block) -> str:
        return extract_added(self.md.block(block))[0]

    def test_a_heading_gets_one_hash_per_level(self):
        block = Heading(2, plain("Goal"))

        out = self.md.block(block)

        self.assertEqual(out, "## Goal")

    def test_a_dropcap_rejoins_its_paragraph(self):
        block = Paragraph(plain("he rest"), dropcap="T")

        out = self.md.block(block)

        self.assertEqual(out, "The rest")

    def test_a_label_is_bold(self):
        block = Label(plain("This chapter covers"))

        out = self.md.block(block)

        self.assertEqual(out, "**This chapter covers**")

    def test_list_items_are_numbered_or_bulleted_and_subitems_indented(self):
        block = ListBlock([ListItem("1", plain("one")), ListItem(None, plain("two"), [plain("sub")])])

        out = self.md.block(block)

        self.assertEqual(out, "1. one\n- two\n  - sub")

    def test_code_without_emphasis_is_a_fenced_block(self):
        block = Code(["a", "b"], ["", ""])

        out = self.md.block(block)

        self.assertEqual(out, "```\na\nb\n```")

    def test_code_with_emphasis_is_a_pre_block(self):
        block = Code(["if (a < b) {", "", "  f(x);"], ["....bbbbb...", "", "..bbbbb"])

        out = self._clean(block)

        self.assertEqual(out, "<pre><code>if (<b>a &lt; b</b>) {\n\n  <b>f(x);</b>\n</code></pre>")

    def test_a_listing_title_is_a_bold_line_before_the_code(self):
        block = Code(["a"], [""], title="Listing 1.2 Foo")

        out = self.md.block(block)

        self.assertEqual(out, "**Listing 1.2 Foo**\n\n```\na\n```")

    def test_callouts_follow_the_code_in_line_order(self):
        block = Code(["a", "b", "c", "d", "e"], ["", "", "", "", ""],
                     callouts=[Callout([4, 5], plain("second"), 10.0), Callout([3], plain("first"), 20.0)])

        out = self._clean(block)

        self.assertEqual(out, "```\na\nb\nc\nd\ne\n```\n\n" + CALLOUT_HEADER + "\n- (line 3) first\n- (lines 4–5) second")

    def test_callouts_on_the_same_line_follow_their_position_on_the_page(self):
        block = Code(["a", "b"], ["", ""], callouts=[Callout([2], plain("lower"), 30.0), Callout([2], plain("upper"), 20.0)])

        out = self._clean(block)

        self.assertEqual(out, "```\na\nb\n```\n\n" + CALLOUT_HEADER + "\n- (line 2) upper\n- (line 2) lower")

    def test_a_titled_listing_image_is_named_after_the_listing(self):
        block = Code(["a"], [""], title="Listing 1.2 Foo", lid="1.2", images=[image("l.png")])

        out = self._clean(block)

        self.assertEqual(out, "**Listing 1.2 Foo**\n\n```\na\n```\n\n![Listing 1.2 with callouts](illustrations/l.png)")

    def test_an_untitled_listing_image_is_named_after_its_book_page(self):
        block = Code(["a"], [""], lid="p12", images=[image("l.png")])

        out = self._clean(block)

        self.assertEqual(out, "```\na\n```\n\n![Code on book page 12 with callouts](illustrations/l.png)")

    def test_a_captioned_figure_is_image_caption_and_description(self):
        block = Figure("4.1", image(), "Figure 4.1", plain("Figure 4.1 Caption"), "A box.")

        out = self._clean(block)

        self.assertEqual(out, "![Figure 4.1](illustrations/f.png)\n\n**Figure 4.1** Caption\n\n" + DESCRIPTION + "A box.")

    def test_an_uncaptioned_figure_is_image_and_description(self):
        block = Figure("chapter-map", image(), "Chapter map", None, "A map.")

        out = self._clean(block)

        self.assertEqual(out, "![Chapter map](illustrations/f.png)\n\n" + DESCRIPTION + "A map.")

    def test_a_missing_description_is_a_todo(self):
        block = Figure("chapter-map", image(), "Chapter map", None, None)

        out = self._clean(block)

        self.assertEqual(out, "![Chapter map](illustrations/f.png)\n\n" + DESCRIPTION + "TODO")

    def test_a_table_has_a_caption_header_row_separator_and_escaped_pipes(self):
        block = Table(plain("Table 2.1 Sizes"), [plain("a"), plain("b")], [[plain("x|y"), plain("z")]])

        out = self.md.block(block)

        self.assertEqual(out, "**Table 2.1** Sizes\n\n| a | b |\n|---|---|\n| x\\|y | z |")

    def test_a_table_without_a_caption_starts_with_its_header_row(self):
        block = Table(None, [plain("a")], [])

        out = self.md.block(block)

        self.assertEqual(out, "| a |\n|---|")

    def test_a_note_is_a_quote_with_a_bold_label(self):
        block = Note("TIP", plain("text"))

        out = self.md.block(block)

        self.assertEqual(out, "> **TIP** text")

    def test_a_continued_note_keeps_its_continuation_in_the_quote(self):
        block = Note("TIP", plain("text"), continued=[Paragraph(plain("more"))])

        out = self.md.block(block)

        self.assertEqual(out, "> **TIP** text\n>\n> more")

    def test_a_sidebar_is_a_quote_with_a_bold_title(self):
        block = Sidebar(plain("Title"), [Paragraph(plain("body"))])

        out = self.md.block(block)

        self.assertEqual(out, "> **Title**\n>\n> body")

    def test_a_sidebar_without_a_title_renders_empty_bold_markers(self):
        # pins current behaviour: an empty title still gets its ** markers
        block = Sidebar([], [])

        out = self.md.block(block)

        self.assertEqual(out, "> ****")

    def test_code_lines_starting_with_gt_stay_inside_the_quote(self):
        block = Sidebar(plain("T"), [Paragraph(plain("p")), Code([">foo", "> bar"], ["", ""])])

        out = self.md.block(block)

        self.assertEqual(out, "> **T**\n>\n> p\n>\n> ```\n> >foo\n> > bar\n> ```")

    def test_an_unknown_block_is_rejected(self):
        class Odd(Block):
            pass

        with self.assertRaises(TypeError):
            self.md.block(Odd())


class SectionTests(unittest.TestCase):
    md = Markdown(markdown_config())

    def _section(self, title: str, title_added: str = "") -> Section:
        return Section("ch01", "chapter-01-goal.md", (1, 10), title, title_added=title_added)

    def test_the_added_title_prefix_goes_into_the_first_heading_and_its_range(self):
        doc = SectionDoc(self._section("Chapter 1: Goal", "Chapter 1: "), [Heading(1, plain("Goal"))], [])

        text, ranges = self.md.section(doc)

        self.assertEqual((text, ranges), ("# Chapter 1: Goal\n", [(2, 13, "title")]))

    def test_without_an_added_prefix_the_heading_is_untouched(self):
        doc = SectionDoc(self._section("Chapter 1: Goal"), [Heading(1, plain("Goal"))], [])

        text, ranges = self.md.section(doc)

        self.assertEqual((text, ranges), ("# Goal\n", []))

    def test_footnotes_follow_the_blocks(self):
        doc = SectionDoc(self._section(""), [Paragraph(plain("Body"))], [Footnote("1", plain("text"))])

        text, _ = self.md.section(doc)

        self.assertEqual(text, "Body\n\n[^1]: text\n")

    def test_blocks_are_separated_by_a_blank_line_and_the_text_ends_with_a_newline(self):
        doc = SectionDoc(self._section(""), [Paragraph(plain("One")), Paragraph(plain("Two"))], [])

        text, _ = self.md.section(doc)

        self.assertEqual(text, "One\n\nTwo\n")


class AddedMaterialTests(unittest.TestCase):
    def test_extract_gives_clean_text_and_ranges(self):
        marked = "# " + added("title", "Chapter 1: ") + "Goal\n\n" + added("figure-image", "![x](y.png)") + "\n"

        text, ranges = extract_added(marked)

        self.assertEqual((text, ranges), ("# Chapter 1: Goal\n\n![x](y.png)\n", [(2, 13, "title"), (19, 30, "figure-image")]))

    def test_an_unbalanced_marker_is_rejected(self):
        with self.assertRaises(ValueError):
            extract_added("text \x01 more")

    def test_nested_added_material_is_rejected(self):
        marked = added("a", "x" + added("b", "y"))

        with self.assertRaises(ValueError):
            extract_added(marked)


class PreTests(unittest.TestCase):
    LINES, MARKS = ["if (a < b) {", "", "  f(x);"], ["....bbbbb...", "", "..bbbbb"]

    def test_a_mark_other_than_a_dot_is_emphasis(self):
        self.assertTrue(has_emphasis(self.MARKS))

    def test_dots_and_empty_marks_are_no_emphasis(self):
        self.assertFalse(has_emphasis(["....", ""]))

    def test_pre_escapes_html_and_tags_the_emphasis(self):
        md, _ = extract_added(pre(self.LINES, self.MARKS))

        self.assertEqual(md, "<pre><code>if (<b>a &lt; b</b>) {\n\n  <b>f(x);</b>\n</code></pre>")

    def test_pre_round_trips_to_the_book_text_through_the_verifier(self):
        md, ranges = extract_added(pre(self.LINES, self.MARKS))

        text = without_added(md, [list(r) for r in ranges])

        self.assertEqual(text, "if (a < b) {\n\n  f(x);\n")

    def test_the_verifier_reads_back_the_emphasis_pre_tagged(self):
        md, ranges = extract_added(pre(self.LINES, self.MARKS))

        emphasis = markup_emphasis(md, [list(r) for r in ranges])

        self.assertEqual(emphasis, ("a < bf(x);", ""))

    def test_literal_tags_in_book_text_are_not_emphasis(self):
        md, ranges = extract_added("```\nreturn \"<b>\" + body;\n```\n")

        emphasis = markup_emphasis(md, [list(r) for r in ranges])

        self.assertEqual(emphasis, ("", ""))


if __name__ == "__main__":
    unittest.main()
