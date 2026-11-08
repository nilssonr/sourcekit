"""Loading a profile package from a directory. Needs no PDF; profiles are written to temp directories."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from doc_converter.core.profile import load, profile_dir


class LoadTests(unittest.TestCase):
    def package(self, init: str | None) -> str:
        """A profile directory with the given __init__.py (None: no __init__.py). Each gets a unique
        name, because load() caches modules by directory name."""
        d = tempfile.mkdtemp(prefix="profile-")
        self.addCleanup(shutil.rmtree, d)
        if init is not None:
            (Path(d) / "__init__.py").write_text(init, encoding="utf-8")
        return d

    def test_a_package_defining_PROFILE_loads_with_its_data_directory_set(self):
        d = self.package("from doc_converter.core.profile import Profile\nPROFILE = Profile('fake', *[None] * 8)\n")

        prof = load(d)

        self.assertEqual(prof.name, "fake")
        self.assertEqual(prof.data_dir, Path(d).resolve() / "data")

    def test_a_profile_that_sets_its_own_data_directory_keeps_it(self):
        d = self.package(
            "from pathlib import Path\nfrom doc_converter.core.profile import Profile\n"
            "PROFILE = Profile('fake', *[None] * 8, data_dir=Path('/elsewhere'))\n"
        )

        prof = load(d)

        self.assertEqual(prof.data_dir, Path("/elsewhere"))

    def test_a_directory_without_an_init_file_exits_with_a_message(self):
        d = self.package(None)

        with self.assertRaises(SystemExit) as cm:
            load(d)

        self.assertEqual(cm.exception.code, f"profile {Path(d).resolve()} has no __init__.py")

    def test_an_init_file_without_PROFILE_exits_with_a_message(self):
        d = self.package("X = 1\n")

        with self.assertRaises(SystemExit) as cm:
            load(d)

        self.assertEqual(
            cm.exception.code, f"profile {Path(d).resolve()}: __init__.py must define PROFILE = Profile(...)"
        )

    def test_a_name_that_is_neither_a_directory_nor_a_known_profile_exits(self):
        with self.assertRaises(SystemExit) as cm:
            profile_dir("no-such-profile-anywhere")

        self.assertTrue(
            str(cm.exception.code).startswith("profile not found: no-such-profile-anywhere"), cm.exception.code
        )


if __name__ == "__main__":
    unittest.main()
