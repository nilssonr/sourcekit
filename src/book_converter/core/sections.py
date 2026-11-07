"""A section: one output file, its PDF page range (inclusive, 1-based) and heading.

The section table itself belongs to the profile.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    id: str
    file: str
    pages: tuple[int, int]
    title: str = ""  # empty: the title is taken from the page itself
