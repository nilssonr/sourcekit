"""Added-material ranges and coverage checks: pure functions, no PDF or profile needed."""

from __future__ import annotations

import unittest

from doc_converter.core.verify import coverage, markup_emphasis, without_added
from doc_converter.modules.regions.listings import _fill_space_marks
from doc_converter.modules.serialize.markdown import added, extract_added, has_emphasis, pre


class AddedMaterialTests(unittest.TestCase):
    def test_extract_gives_clean_text_and_ranges(self):
        marked = "# " + added("title", "Chapter 1: ") + "Goal\n\n" + added("figure-image", "![x](y.png)") + "\n"
        text, ranges = extract_added(marked)
        self.assertEqual(text, "# Chapter 1: Goal\n\n![x](y.png)\n")
        self.assertEqual([text[s:e] for s, e, _ in ranges], ["Chapter 1: ", "![x](y.png)"])
        self.assertEqual([k for _, _, k in ranges], ["title", "figure-image"])

    def test_extract_rejects_unbalanced_markers(self):
        with self.assertRaises(ValueError):
            extract_added("text \x01 more")
        with self.assertRaises(ValueError):
            extract_added(added("a", "x" + added("b", "y")))

    def test_without_added_removes_exactly_the_ranges(self):
        text, ranges = extract_added("keep " + added("k", "drop ") + "keep2")
        self.assertEqual(without_added(text, [list(r) for r in ranges]), "keep keep2")
        self.assertEqual(without_added("abc", []), "abc")


class CodeEmphasisTests(unittest.TestCase):
    def test_spaces_join_a_bold_phrase_but_not_its_edges(self):
        text = "x = new Foo(a, b);"
        marks = "....bbb.bbbbbbbbb."
        self.assertEqual(_fill_space_marks(text, [m if c != " " else "." for c, m in zip(text, marks)]),
                         "....bbbbbbbbbbbbb.")

    def test_pre_escapes_and_tags_and_round_trips_through_the_verifier(self):
        lines, marks = ["if (a < b) {", "", "  f(x);"], ["....bbbbb...", "", "..bbbbb"]
        self.assertTrue(has_emphasis(marks))
        self.assertFalse(has_emphasis(["....", ""]))
        md, ranges = extract_added(pre(lines, marks))
        self.assertEqual(md, "<pre><code>if (<b>a &lt; b</b>) {\n\n  <b>f(x);</b>\n</code></pre>")
        rs = [list(r) for r in ranges]
        self.assertEqual(without_added(md, rs), "if (a < b) {\n\n  f(x);\n")
        self.assertEqual(markup_emphasis(md, rs), ("a < bf(x);", ""))

    def test_literal_tags_in_book_text_are_not_emphasis(self):
        md, ranges = extract_added("```\nreturn \"<b>\" + body;\n```\n")
        self.assertEqual(markup_emphasis(md, [list(r) for r in ranges]), ("", ""))


class QuoteTests(unittest.TestCase):
    def test_code_lines_starting_with_gt_stay_inside_the_quote(self):
        from doc_converter.core.model import Code, Paragraph, Run, Sidebar
        from doc_converter.modules.serialize.markdown import Markdown, MarkdownConfig
        md = Markdown(MarkdownConfig(("x", "x"), ("x", "x"), ("x", "x")))
        out = md.block(Sidebar([Run("T", "")], [Paragraph([Run("p", "")]), Code([">foo", "> bar"], ["", ""])]))
        self.assertEqual(out, "> **T**\n>\n> p\n>\n> ```\n> >foo\n> > bar\n> ```")


class CoverageTests(unittest.TestCase):
    EXPECTED = {"a.md": (2, 10), "b.md": (11, 20)}

    def test_matching_contiguous_files_pass(self):
        ok, report = coverage([{"file": "a.md", "pages": [2, 10]}, {"file": "b.md", "pages": [11, 20]}], self.EXPECTED)
        self.assertTrue(ok, report)

    def test_gap_overlap_and_missing_files_fail(self):
        ok, report = coverage([{"file": "a.md", "pages": [2, 9]}, {"file": "b.md", "pages": [11, 20]}], self.EXPECTED)
        self.assertFalse(ok)
        self.assertIn("pages 10-10 are in no file", report)
        ok, report = coverage([{"file": "a.md", "pages": [2, 12]}, {"file": "b.md", "pages": [11, 20]}], self.EXPECTED)
        self.assertFalse(ok)
        self.assertIn("overlap", report)
        ok, report = coverage([{"file": "a.md", "pages": [2, 10]}], self.EXPECTED)
        self.assertFalse(ok)
        self.assertIn("missing file b.md", report)


if __name__ == "__main__":
    unittest.main()
