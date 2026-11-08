"""pipeline.convert end to end on a fake Book: the section files, the manifest and the run sidecars.
Needs no PDF or profile package; the profile is built from the test configs."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from doc_converter.core.pdf import OutlineEntry
from doc_converter.core.pipeline import convert
from doc_converter.core.profile import Profile
from fixtures import (
    COLUMN_RIGHT,
    TITLE,
    FakeBook,
    dictionary_file,
    figure_config,
    flow_config,
    listing_config,
    markdown_config,
    row_config,
    span,
    split_config,
    table_config,
    verify_config,
)


class ConvertTests(unittest.TestCase):
    def profile(self) -> Profile:
        data_dir = Path(tempfile.mkdtemp(prefix="profile-data-"))
        self.addCleanup(shutil.rmtree, data_dir)
        return Profile(
            name="fake",
            split=split_config(),
            rows=row_config(),
            flow=flow_config(),
            markdown=markdown_config(),
            listings=listing_config(),
            figures=figure_config(),
            tables=table_config(),
            verify=verify_config(),
            data_dir=data_dir,
        )

    def out_dir(self) -> Path:
        out = Path(tempfile.mkdtemp(prefix="convert-out-")) / "out"
        self.addCleanup(shutil.rmtree, out.parent)
        return out

    def book(self) -> FakeBook:
        """Chapter 1 on pages 1-2, chapter 2 on page 3, the index on page 4."""
        book = FakeBook(
            {
                1: [
                    span("The goal", baseline=100, font=TITLE),
                    span("A full first line of the only paragraph", baseline=150, x1=COLUMN_RIGHT),
                    span("and its short second line.", baseline=164),
                ],
                2: [span("Page two.", baseline=100, page=2)],
                3: [span("Chapter two text.", baseline=100, page=3)],
            },
            outline=[
                OutlineEntry(2, "1 The goal", 1),
                OutlineEntry(2, "2 What is a test", 3),
                OutlineEntry(1, "index", 4),
            ],
        )
        book.path = dictionary_file(self)  # run-info hashes the input file
        return book

    def test_each_section_becomes_a_markdown_file_with_the_outline_title(self):
        out = self.out_dir()

        convert(self.book(), self.profile(), out, dict_path=dictionary_file(self))

        self.assertEqual(
            (out / "chapter-01-the-goal.md").read_text(encoding="utf-8"),
            "# Chapter 1: The goal\n\nA full first line of the only paragraph and its short second line.\n\n"
            "Page two.\n",
        )
        self.assertEqual(
            (out / "chapter-02-what-is-a-test.md").read_text(encoding="utf-8"),
            "# Chapter 2: What is a test\n\nChapter two text.\n",
        )

    def test_the_manifest_lists_each_file_with_its_pages_and_added_ranges(self):
        out = self.out_dir()

        convert(self.book(), self.profile(), out, dict_path=dictionary_file(self))

        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            manifest,
            {
                "profile": "fake",
                "files": [
                    {"file": "chapter-01-the-goal.md", "pages": [1, 2], "skip_pages": [], "added": [[2, 13, "title"]]},
                    {
                        "file": "chapter-02-what-is-a-test.md",
                        "pages": [3, 3],
                        "skip_pages": [],
                        "added": [[2, 13, "title"]],
                    },
                ],
            },
        )

    def test_the_result_counts_the_blocks_written_per_file(self):
        out = self.out_dir()

        res = convert(self.book(), self.profile(), out, dict_path=dictionary_file(self))

        self.assertEqual(res.written, [("chapter-01-the-goal.md", 3), ("chapter-02-what-is-a-test.md", 2)])
        self.assertEqual(res.warnings, [])

    def test_only_the_wanted_sections_are_written(self):
        out = self.out_dir()

        convert(self.book(), self.profile(), out, wanted=["ch02"], dict_path=dictionary_file(self))

        self.assertEqual(sorted(p.name for p in out.glob("*.md")), ["chapter-02-what-is-a-test.md"])
        files = json.loads((out / "manifest.json").read_text(encoding="utf-8"))["files"]
        self.assertEqual([f["file"] for f in files], ["chapter-02-what-is-a-test.md"])

    def test_the_run_sidecars_are_written_and_run_info_names_the_dictionary(self):
        out = self.out_dir()
        words = dictionary_file(self)

        convert(self.book(), self.profile(), out, dict_path=words)

        self.assertEqual(
            sorted(p.name for p in out.iterdir() if p.suffix != ".md"),
            [
                "figure-labels.json",
                "hyphen-decisions.json",
                "illustrations",
                "manifest.json",
                "run-info.json",
                "warnings.txt",
            ],
        )
        info = json.loads((out / "run-info.json").read_text(encoding="utf-8"))
        self.assertEqual(info["dictionary"], words)
        self.assertEqual(info["profile"], "fake")
        self.assertEqual((out / "warnings.txt").read_text(encoding="utf-8"), "")
        self.assertEqual(json.loads((out / "hyphen-decisions.json").read_text(encoding="utf-8")), [])


if __name__ == "__main__":
    unittest.main()
