"""Full conversion compared with a saved reference run (scripts/compare_ref.sh).

Runs only when BOOK_REF points at a reference directory (e.g. ~/temp/book-converter-ref/manning),
the PDF is present (BOOK_PDF) and the profile exists. Takes about 15 s.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from book_converter.core.profile import REPO

PDF = os.environ.get("BOOK_PDF", str(Path.home() / "temp/Manning.Unit.Testing.Principles.Practices.and.Patterns.2020.1.pdf"))
REF = os.environ.get("BOOK_REF", "")
PROFILE = "manning_unit_testing"


@unittest.skipUnless(REF and Path(REF).expanduser().is_dir(), "BOOK_REF not set to a reference directory")
@unittest.skipUnless(Path(PDF).exists(), "book PDF not available")
@unittest.skipUnless((REPO / "profiles" / PROFILE).is_dir(), f"profile {PROFILE} not available")
class RegressionTest(unittest.TestCase):
    def test_output_matches_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            subprocess.run([str(REPO / "scripts/make_run.sh"), PDF, str(run), PROFILE], check=True,
                           capture_output=True, text=True)
            res = subprocess.run([str(REPO / "scripts/compare_ref.sh"), str(Path(REF).expanduser()), str(run)],
                                 capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)


if __name__ == "__main__":
    unittest.main()
