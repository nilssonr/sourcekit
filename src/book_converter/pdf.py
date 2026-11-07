"""Load positioned text spans and vector drawings from the book PDF with PyMuPDF.

All coordinates are PDF points with a top-left origin (PyMuPDF's page space).
Page numbers are 1-based PDF page numbers.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field

import pymupdf

# Ligatures are expanded ("ﬁ" -> "fi"); line-end hyphens are left alone (hyphens.py decides).
TEXT_FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP


@dataclass(eq=False)
class Char:
    c: str
    x0: float
    x1: float
    y: float  # baseline


@dataclass(eq=False)
class Span:
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    font: str
    size: float
    color: str  # "#rrggbb"
    text: str
    chars: list[Char] = field(default_factory=list)
    baseline: float = 0.0

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def italic(self) -> bool:
        return "Ital" in self.font or "Oblique" in self.font

    @property
    def bold(self) -> bool:
        return any(k in self.font for k in ("Bold", "Demi", "Black", "Heavy"))

    def __repr__(self) -> str:
        return f"Span(p{self.page} {self.x0:.1f},{self.y0:.1f} {self.font}/{self.size:.2f}/{self.color} {self.text!r})"


def hexcolor(c: int) -> str:
    return f"#{c:06x}"


class Book:
    def __init__(self, path: str):
        self.path = path
        self.doc = pymupdf.open(path)

    @property
    def page_count(self) -> int:
        return self.doc.page_count

    def page(self, pno: int) -> pymupdf.Page:
        return self.doc[pno - 1]

    @functools.lru_cache(maxsize=None)
    def spans(self, pno: int) -> list[Span]:
        out: list[Span] = []
        raw = self.page(pno).get_text("rawdict", flags=TEXT_FLAGS)
        for b in raw["blocks"]:
            for l in b.get("lines", []):
                for s in l["spans"]:
                    chars = [Char(c["c"], c["bbox"][0], c["bbox"][2], c["origin"][1]) for c in s["chars"]]
                    text = "".join(c.c for c in chars)
                    if not text:
                        continue
                    x0, y0, x1, y1 = s["bbox"]
                    out.append(Span(pno, x0, y0, x1, y1, s["font"], round(s["size"], 2), hexcolor(s["color"]),
                                    text, chars, s["origin"][1]))
        return out

    @functools.lru_cache(maxsize=None)
    def drawings(self, pno: int) -> list[dict]:
        return self.page(pno).get_drawings()

    def all_spans(self, p0: int, p1: int) -> list[Span]:
        return [s for p in range(p0, p1 + 1) for s in self.spans(p)]
