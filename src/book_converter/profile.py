"""Profiles: the per-PDF part of a conversion.

A profile is a Python package, either at a given directory or under <repo>/profiles/<name>/, whose
__init__.py defines PROFILE = Profile(...). Its data/ directory holds hand-written data (figure
descriptions); runs read it and never write to it.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from .sections import Section

if TYPE_CHECKING:  # annotations only: verify imports this module
    from .convert import Row
    from .figures import FigureConfig
    from .listings import ListingConfig
    from .tables import TableConfig
    from .verify import VerifyConfig

REPO = Path(__file__).resolve().parents[2]


@dataclass
class Profile:
    name: str
    sections: dict[str, Section]
    styles: ModuleType            # style predicates and constants used by convert.py (until step 4 of the plan)
    role: Callable[[Row], str]    # row -> role name (see the role vocabulary in docs/design.md)
    listings: ListingConfig
    figures: FigureConfig
    tables: TableConfig
    verify: VerifyConfig
    skip_pages: set[int] = field(default_factory=set)          # pages inside a section range that are not converted
    page_figures: dict[int, tuple[str, int]] = field(default_factory=dict)  # page -> (figure id, rotation)
    data_dir: Path | None = None


def profile_dir(name: str) -> Path:
    """A profile given by path, or by name under <repo>/profiles/."""
    p = Path(name).expanduser()
    if not p.is_dir():
        p = REPO / "profiles" / name
    if not p.is_dir():
        sys.exit(f"profile not found: {name} (looked for a directory, then {p})")
    return p


def load(name: str) -> Profile:
    d = profile_dir(name).resolve()
    modname = f"book_converter_profile_{d.name}"
    mod = sys.modules.get(modname)
    if mod is None:
        spec = importlib.util.spec_from_file_location(modname, d / "__init__.py", submodule_search_locations=[str(d)])
        if spec is None or spec.loader is None:
            sys.exit(f"profile {d} has no __init__.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[modname] = mod
        spec.loader.exec_module(mod)
    prof = getattr(mod, "PROFILE", None)
    if not isinstance(prof, Profile):
        sys.exit(f"profile {d}: __init__.py must define PROFILE = Profile(...)")
    if prof.data_dir is None:
        prof.data_dir = d / "data"
    return prof
