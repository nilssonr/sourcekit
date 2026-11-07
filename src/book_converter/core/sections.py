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
    heading_shift: int = 0  # added to every heading level found on the pages (front matter: 1)
    title_added: str = ""   # leading part of title that is not book text ("Chapter 1: "; all of "Front matter")
