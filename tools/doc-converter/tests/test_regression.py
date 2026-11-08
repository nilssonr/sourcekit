"""Full conversion compared with a saved reference run (scripts/compare_ref.sh).

Runs only when DOC_CONVERTER_REF points at a reference run directory (made by scripts/make_run.sh),
DOC_CONVERTER_PDF points at the PDF and the profile exists. Takes about 15 s.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from doc_converter.core.profile import REPO

PDF = os.environ.get("DOC_CONVERTER_PDF", "")
REF = os.environ.get("DOC_CONVERTER_REF", "")
PROFILE = "manning_unit_testing"


@unittest.skipUnless(REF and Path(REF).expanduser().is_dir(), "DOC_CONVERTER_REF not set to a reference directory")
@unittest.skipUnless(PDF and Path(PDF).is_file(), "DOC_CONVERTER_PDF not set to the PDF")
@unittest.skipUnless((REPO / "profiles" / PROFILE).is_dir(), f"profile {PROFILE} not available")
class RegressionTest(unittest.TestCase):
    def test_output_matches_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            subprocess.run([str(REPO / "scripts/make_run.sh"), PDF, str(run), PROFILE], check=True,
                           capture_output=True, text=True)
            res = subprocess.run([str(REPO / "scripts/compare_ref.sh"), str(Path(REF).expanduser()), str(run)],
                                 capture_output=True, text=True, check=False)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)


if __name__ == "__main__":
    unittest.main()
