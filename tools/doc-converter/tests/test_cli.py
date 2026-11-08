"""The doc-converter command's argument handling, up to the point where a PDF would be opened.
Needs no PDF or profile."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from doc_converter.cli import main


class CommandTests(unittest.TestCase):
    def run_main(self, argv: list[str]) -> tuple[object, str]:
        """The exit code (SystemExit's code) and what was printed."""
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            main(argv)
        return cm.exception.code, out.getvalue()

    def test_no_arguments_prints_the_usage_and_exits_2(self):
        code, out = self.run_main([])

        self.assertEqual(code, 2)
        self.assertTrue(out.startswith("usage: doc-converter {convert,verify,census}"), out)

    def test_help_prints_the_usage_and_exits_0(self):
        code, out = self.run_main(["--help"])

        self.assertEqual(code, 0)
        self.assertIn("doc-converter <command> --help", out)

    def test_an_unknown_command_exits_with_the_usage_in_the_message(self):
        code, _ = self.run_main(["bogus"])

        self.assertEqual(
            code,
            "unknown command 'bogus'\nusage: doc-converter {convert,verify,census} [options]   "
            "(doc-converter <command> --help for details)",
        )

    def test_census_rejects_a_malformed_page_range_before_opening_the_pdf(self):
        code, _ = self.run_main(["census", "--input", "/nonexistent/book.pdf", "--pages", "16-"])

        self.assertEqual(code, "--pages must look like 16-296, not '16-'")

    def test_convert_requires_its_three_options(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            main(["convert", "--input", "book.pdf"])

        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
