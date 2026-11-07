"""Regression tests against the book PDF (skipped when it is not present).

Set BOOK_PDF to the PDF path; defaults to ~/temp/Manning.Unit.Testing.Principles.Practices.and.Patterns.2020.1.pdf.
Needs the git-ignored profile profiles/manning_unit_testing/ (skipped when it is absent).
Run: uv run python -m unittest -v
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

import pymupdf

from book_converter import figures, tables
from book_converter.listings import code_lines, listing_bars, listing_parts, map_callouts, with_blank_lines
from book_converter.pdf import Book
from book_converter.profile import REPO, load as load_profile

PDF = os.environ.get("BOOK_PDF", str(Path.home() / "temp/Manning.Unit.Testing.Principles.Practices.and.Patterns.2020.1.pdf"))
PROFILE_DIR = REPO / "profiles" / "manning_unit_testing"


@unittest.skipUnless(Path(PDF).exists(), "book PDF not available")
@unittest.skipUnless(PROFILE_DIR.is_dir(), "profile manning_unit_testing not available")
class BookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = Book(PDF)
        cls.prof = load_profile(str(PROFILE_DIR))
        cls.lc = cls.prof.listings

    def callouts(self, pno: int, index: int = 0) -> dict[str, list[int]]:
        warns: list[str] = []
        spans = sorted(self.book.spans(pno), key=lambda s: (s.baseline, s.x0))
        used: set[int] = set()
        lc = self.lc
        bars = listing_bars(self.book, pno, lc)
        for bar in bars[:index]:
            listing_parts(spans, bar, used, lc)
        bar = bars[index]
        head, body = listing_parts(spans, bar, used, lc)
        lines = code_lines([s for s in body if lc.is_code_block(s)], warns.append, lc)
        _, pos = with_blank_lines(lines, lc)
        area = pymupdf.Rect(0, bar.y0, 1000, max(s.y1 for s in body if s.text.strip()) + 3)
        got = map_callouts(self.book, pno, area, lines, pos, [s for s in body if lc.is_callout(s)], warns.append, lc)
        self.assertEqual([w for w in warns if "off-grid" in w or "no graphic" in w], [])
        return {" ".join(s.text.strip() for s in c.spans): c.lines for c in got}

    def test_listing_1_1(self):
        c = self.callouts(31)
        self.assertEqual(c["Covered by the test"], [2, 3, 6, 7])
        self.assertEqual(c["Not covered by the test"], [4])

    def test_listing_1_2(self):
        c = self.callouts(34)
        self.assertEqual(c["First outcome"], [6])
        self.assertEqual(c["Second outcome"], [7])
        self.assertEqual(c["The test verifies only the second outcome."], [13])

    def test_listing_1_3(self):
        c = self.callouts(35)
        self.assertEqual(c["Returns true"], [3])
        self.assertEqual(c["Returns false"], [4])

    def test_listing_3_1(self):
        c = self.callouts(64)
        self.assertEqual(sorted(c.values()), [[1], [3], [4], [7, 8, 9], [12], [15]])

    def test_listing_7_14_jog_bracket(self):
        c = self.callouts(200)
        hit = [v for k, v in c.items() if k.startswith("Simultaneously asserts")]
        self.assertEqual(hit, [[12, 13]])

    def test_listing_6_10_left_bracket_inside_code_column(self):
        c = self.callouts(160, index=1)
        self.assertEqual(c["The new interface in action"], [3, 4, 12, 13])

    def test_listing_10_3_continuation_bracket(self):
        # p260 holds the middle part of listing 10.3 (no title bar); check its bracket on the page's own lines
        warns: list[str] = []
        lc = self.lc
        code = [s for s in self.book.spans(260) if lc.is_code_block(s)]
        lines = code_lines(code, warns.append, lc)
        _, pos = with_blank_lines(lines, lc)
        text, _ = with_blank_lines(lines, lc)
        calls = [s for s in self.book.spans(260) if lc.is_callout(s)]
        got = map_callouts(self.book, 260, pymupdf.Rect(0, 40, 1000, 640), lines, pos, calls, warns.append, lc)
        hit = [c for c in got if " ".join(s.text.strip() for s in c.spans).startswith("Uses the repositories")]
        self.assertEqual(len(hit), 1)
        covered = [text[n - 1].strip() for n in hit[0].lines]
        self.assertEqual(covered, ["object[] userData = _userRepository", ".GetUserById(userId);",
                                   "object[] companyData = _companyRepository", ".GetCompany();",
                                   "_companyRepository.SaveCompany(company);", "_userRepository.SaveUser(user);"])

    def test_table_6_1(self):
        t = tables.detect(self.book, 150, lambda w: None, self.prof.tables)
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0].id, "6.1")
        self.assertEqual(len(t[0].header), 4)
        self.assertEqual(len(t[0].rows), 2)
        self.assertEqual([len([c for c in r if c]) for r in t[0].rows], [4, 4])

    def test_figure_4_9_caption_removed(self):
        figs = [f for f in figures.detect(self.book, 107, lambda w: None, self.prof.figures) if f.id == "4.9"]
        self.assertEqual(len(figs), 1)
        f = figs[0]
        tmp = pymupdf.open()
        tmp.insert_pdf(self.book.doc, from_page=106, to_page=106)
        page = tmp[0]
        for s in self.book.spans(107):
            if not self.prof.figures.is_figure_text(s) and s.text.strip() and pymupdf.Rect(s.x0, s.y0, s.x1, s.y1).intersects(f.box):
                page.add_redact_annot(pymupdf.Rect(s.x0, s.y0, s.x1, s.y1) + (0.3, 0.6, -0.3, -0.6), fill=False)
        page.apply_redactions(images=0, graphics=0, text=0)
        left = page.get_text("text", clip=f.box)
        self.assertNotIn("Figure 4.9", left)
        for label in f.label_texts:
            self.assertIn(label.split()[0], left)


if __name__ == "__main__":
    unittest.main()
