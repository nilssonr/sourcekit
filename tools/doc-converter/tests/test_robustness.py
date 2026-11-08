"""File encodings and make_run.sh: no PDF or profile needed."""

from __future__ import annotations

import ast
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


TOOL = Path(__file__).resolve().parents[1]


class EncodingTests(unittest.TestCase):
    def test_every_text_file_call_names_its_encoding(self):
        for path in (TOOL / "src").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("read_text", "write_text")):
                    self.assertIn("encoding", [k.arg for k in node.keywords], f"{path}:{node.lineno}")


class MakeRunTests(unittest.TestCase):
    """A copy of the script in a sandbox, with a uv stub that records nothing and succeeds."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.tool = self.root / "tools" / "doc-converter"
        (self.tool / "scripts").mkdir(parents=True)
        (self.tool / "src").mkdir()
        (self.tool / "src" / "code.py").touch()
        shutil.copy(TOOL / "scripts" / "make_run.sh", self.tool / "scripts")
        (self.root / "book.pdf").touch()
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        uv = bin_dir / "uv"
        uv.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
        self.env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    def run_script(self, pdf: str, run: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        script = self.tool / "scripts" / "make_run.sh"
        return subprocess.run(["bash", str(script), pdf, run, "prof"], cwd=cwd or self.root, env=self.env,
                              capture_output=True, text=True, check=False)

    def test_relative_paths_use_the_callers_directory(self):
        r = self.run_script("book.pdf", "runs/r1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.root / "runs" / "r1" / "convert-stdout.txt").exists())
        self.assertFalse((self.tool / "runs").exists())

    def test_earlier_run_is_replaced(self):
        old = self.root / "r1"
        old.mkdir()
        (old / "convert-stdout.txt").touch()
        (old / "stale").touch()
        self.assertEqual(self.run_script("book.pdf", "r1").returncode, 0)
        self.assertFalse((old / "stale").exists())

    def test_directory_that_is_not_a_run_is_refused(self):
        r = self.run_script(str(self.root / "book.pdf"), "src", cwd=self.tool)
        self.assertEqual(r.returncode, 2)
        self.assertIn("refusing", r.stderr)
        self.assertTrue((self.tool / "src" / "code.py").exists())

    def test_missing_pdf_deletes_nothing(self):
        old = self.root / "r1"
        old.mkdir()
        (old / "convert-stdout.txt").touch()
        r = self.run_script("nope.pdf", "r1")
        self.assertEqual(r.returncode, 2)
        self.assertTrue((old / "convert-stdout.txt").exists())

    def test_pdf_inside_the_run_directory_is_refused(self):
        old = self.root / "r1"
        old.mkdir()
        (old / "convert-stdout.txt").touch()
        (old / "book.pdf").touch()
        r = self.run_script("r1/book.pdf", "r1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("inside the run directory", r.stderr)
        self.assertTrue((old / "book.pdf").exists())
        self.assertTrue((old / "convert-stdout.txt").exists())
