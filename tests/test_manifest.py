"""Added-material ranges and coverage checks: pure functions, no PDF or profile needed."""

from __future__ import annotations

import unittest

from book_converter.core.verify import coverage, without_added
from book_converter.modules.serialize.markdown import added, extract_added


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
