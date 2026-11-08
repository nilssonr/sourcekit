"""Profiles: the per-PDF part of a conversion.

A profile is a Python package, either at a given directory or under <repo>/profiles/<name>/, whose
__init__.py defines PROFILE = Profile(...). Its data/ directory holds hand-written data (figure
descriptions); runs read it and never write to it.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotations only: verify imports this module
    from ..modules.assemble.flow import FlowConfig
    from ..modules.serialize.markdown import MarkdownConfig
    from .rows import RowConfig
    from ..modules.split.outline import SplitConfig
    from ..modules.regions.figures import FigureConfig
    from ..modules.regions.listings import ListingConfig
    from ..modules.regions.tables import TableConfig
    from .verify import VerifyConfig

REPO = Path(__file__).resolve().parents[3]


@dataclass
class Profile:
    name: str
    split: SplitConfig            # how the PDF's outline becomes output files
    rows: RowConfig               # spans -> rows -> runs; includes the row role function
    flow: FlowConfig              # rows and regions -> blocks
    markdown: MarkdownConfig      # caption formats
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
    modname = f"doc_converter_profile_{d.name}"
    mod = sys.modules.get(modname)
    if mod is None:
        init = d / "__init__.py"
        if not init.is_file():
            sys.exit(f"profile {d} has no __init__.py")
        spec = importlib.util.spec_from_file_location(modname, init, submodule_search_locations=[str(d)])
        if spec is None or spec.loader is None:  # pragma: no cover - importlib always builds a spec for a .py file
            sys.exit(f"profile {d}: cannot import __init__.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[modname] = mod
        spec.loader.exec_module(mod)
    prof = getattr(mod, "PROFILE", None)
    if not isinstance(prof, Profile):
        sys.exit(f"profile {d}: __init__.py must define PROFILE = Profile(...)")
    if prof.data_dir is None:
        prof.data_dir = d / "data"
    return prof
