"""Splitting a book into sections from its outline bookmarks: a fake outline, no PDF or profile needed."""

from __future__ import annotations

import unittest

from doc_converter.core.pdf import OutlineEntry
from doc_converter.core.sections import Section
from doc_converter.modules.split.outline import SectionRule, sections, slug
from fixtures import FakeBook, front_matter, split_config

PART = OutlineEntry(1, "Part 1—The bigger picture", 5)
CHAPTER_1 = OutlineEntry(2, "1 The goal of unit testing", 7)
CHAPTER_2 = OutlineEntry(2, "2 What is a unit test?", 20)
INDEX = OutlineEntry(1, "index", 40)
OUTLINE = [PART, CHAPTER_1, CHAPTER_2, INDEX]


class SlugTests(unittest.TestCase):
    def test_a_title_becomes_lower_case_words_joined_by_hyphens(self):
        self.assertEqual(slug("What is a unit test?"), "what-is-a-unit-test")


class SectionsTests(unittest.TestCase):
    def test_a_chapter_runs_to_the_page_before_the_next_bookmark(self):
        book = FakeBook(outline=OUTLINE)

        out = sections(book, split_config())

        self.assertEqual(out["ch01"].pages, (7, 19))

    def test_the_last_section_ends_on_the_page_before_the_end_bookmark(self):
        book = FakeBook(outline=OUTLINE)

        out = sections(book, split_config())

        self.assertEqual(out["ch02"].pages, (20, 39))

    def test_a_part_bookmark_becomes_a_part_section_whose_title_comes_from_the_page(self):
        book = FakeBook(outline=OUTLINE)

        out = sections(book, split_config())

        self.assertEqual(out["part-1"], Section("part-1", "part-1-the-bigger-picture.md", (5, 6), "", 0, ""))

    def test_a_chapter_bookmark_becomes_a_chapter_section_with_an_added_title_prefix(self):
        book = FakeBook(outline=OUTLINE)

        out = sections(book, split_config())

        self.assertEqual(out["ch02"], Section("ch02", "chapter-02-what-is-a-unit-test.md", (20, 39),
                                              "Chapter 2: What is a unit test?", 0, "Chapter 2: "))

    def test_bookmarks_at_or_after_the_end_page_are_ignored(self):
        book = FakeBook(outline=OUTLINE + [OutlineEntry(2, "3 At the end", 40), OutlineEntry(2, "4 After the end", 45)])

        out = sections(book, split_config())

        self.assertEqual(list(out), ["part-1", "ch01", "ch02"])

    def test_a_matching_title_at_another_level_is_ignored(self):
        book = FakeBook(outline=[PART, CHAPTER_1, CHAPTER_2, OutlineEntry(1, "3 Not a chapter", 30), INDEX])

        out = sections(book, split_config())

        self.assertEqual(list(out), ["part-1", "ch01", "ch02"])

    def test_a_title_that_only_starts_like_a_rule_is_ignored(self):
        cfg = split_config(rules=[SectionRule(2, r"Appendix ([A-Z])", lambda m: (f"app-{m[1]}", f"appendix-{m[1]}.md", "", ""))])
        book = FakeBook(outline=[OutlineEntry(2, "Appendix A", 5), OutlineEntry(2, "Appendix B: Notes", 8), INDEX])

        out = sections(book, cfg)

        self.assertEqual(list(out), ["app-A"])

    def test_the_first_matching_rule_wins(self):
        cfg = split_config(rules=[SectionRule(2, r"(\d+) (.+)", lambda m: ("first", "first.md", "", "")),
                                  SectionRule(2, r"\d+ .+", lambda m: ("second", "second.md", "", ""))])
        book = FakeBook(outline=[CHAPTER_1, INDEX])

        out = sections(book, cfg)

        self.assertEqual(list(out), ["first"])

    def test_front_matter_runs_from_its_first_page_to_before_the_first_section(self):
        book = FakeBook(outline=OUTLINE)

        out = sections(book, split_config(front=front_matter(first_page=2)))

        self.assertEqual(out["front"], Section("front", "front-matter.md", (2, 4), "Front matter", 1, "Front matter"))

    def test_sections_come_in_page_order_after_the_front_matter(self):
        book = FakeBook(outline=OUTLINE)

        out = sections(book, split_config(front=front_matter(first_page=2)))

        self.assertEqual(list(out), ["front", "part-1", "ch01", "ch02"])

    def test_an_outline_that_cannot_be_split_is_rejected(self):
        cases = (
            ("no end bookmark", [CHAPTER_1], "no level-1 bookmark"),
            ("no matching bookmark", [OutlineEntry(1, "Preface", 3), INDEX], "no outline bookmark matches"),
            ("two sections on one page", [CHAPTER_1, OutlineEntry(2, "2 Same page", 7), INDEX], "not strictly increasing"),
            ("duplicate section id", [OutlineEntry(2, "3 A", 5), OutlineEntry(2, "3 B", 7), INDEX], "duplicate section id"),
        )
        for name, outline, message in cases:
            with self.subTest(name):
                with self.assertRaisesRegex(ValueError, message):
                    sections(FakeBook(outline=outline), split_config())


if __name__ == "__main__":
    unittest.main()
