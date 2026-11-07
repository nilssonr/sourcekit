"""Section table: output file, PDF page range (inclusive, 1-based) and heading.

Dropped: PDF 1 (cover), 3 (title), 4 (copyright), 6-15 (blank, contents), 297-305 (index).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    id: str
    file: str
    pages: tuple[int, int]
    title: str = ""  # empty: the title is taken from the page itself


SECTIONS: dict[str, Section] = {s.id: s for s in [
    Section("front", "front-matter.md", (2, 22), "Front matter"),
    Section("part-1", "part-1-the-bigger-picture.md", (23, 24)),
    Section("ch01", "chapter-01-the-goal-of-unit-testing.md", (25, 41), "Chapter 1: The goal of unit testing"),
    Section("ch02", "chapter-02-what-is-a-unit-test.md", (42, 62), "Chapter 2: What is a unit test?"),
    Section("ch03", "chapter-03-the-anatomy-of-a-unit-test.md", (63, 86), "Chapter 3: The anatomy of a unit test"),
    Section("part-2", "part-2-making-your-tests-work-for-you.md", (87, 88)),
    Section("ch04", "chapter-04-the-four-pillars-of-a-good-unit-test.md", (89, 113), "Chapter 4: The four pillars of a good unit test"),
    Section("ch05", "chapter-05-mocks-and-test-fragility.md", (114, 140), "Chapter 5: Mocks and test fragility"),
    Section("ch06", "chapter-06-styles-of-unit-testing.md", (141, 172), "Chapter 6: Styles of unit testing"),
    Section("ch07", "chapter-07-refactoring-toward-valuable-unit-tests.md", (173, 204), "Chapter 7: Refactoring toward valuable unit tests"),
    Section("part-3", "part-3-integration-testing.md", (205, 206)),
    Section("ch08", "chapter-08-why-integration-testing.md", (207, 237), "Chapter 8: Why integration testing?"),
    Section("ch09", "chapter-09-mocking-best-practices.md", (238, 250), "Chapter 9: Mocking best practices"),
    Section("ch10", "chapter-10-testing-the-database.md", (251, 278), "Chapter 10: Testing the database"),
    Section("part-4", "part-4-unit-testing-anti-patterns.md", (279, 280)),
    Section("ch11", "chapter-11-unit-testing-anti-patterns.md", (281, 296), "Chapter 11: Unit testing anti-patterns"),
]}

# Pages inside a section's range that are not converted.
SKIP_PAGES = {3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15}
