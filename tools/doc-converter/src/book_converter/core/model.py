"""The document model: typed blocks between PDF analysis (assemble) and output (serialize).

Inline text is a list of Runs that are already joined across lines (line-end hyphens resolved).
Every block records where it came from in the PDF (src).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf

from .sections import Section

SpanId = tuple[int, int]  # (PDF page, index into Book.spans(page))


@dataclass
class SourceRef:
    page: int
    bbox: tuple[float, float, float, float]
    spans: list[SpanId] = field(default_factory=list)


@dataclass
class Run:
    t: str
    s: str  # "", "i", "b", "bi", "code", "fn"


Inlines = list[Run]


@dataclass
class ImageRef:
    """An image to render after assembly: a figure (label glyphs kept, other text removed) or a
    plain crop of a listing with its callouts."""
    file: str                 # file name under illustrations/
    kind: str                 # "figure" | "crop"
    page: int
    box: pymupdf.Rect
    rotate: int = 0


@dataclass
class Block:
    src: list[SourceRef] = field(default_factory=list, kw_only=True)


@dataclass
class Heading(Block):
    level: int
    inlines: Inlines


@dataclass
class Paragraph(Block):
    inlines: Inlines
    dropcap: str = ""


@dataclass
class Label(Block):
    """A bold line that introduces the next block ("This chapter covers")."""
    inlines: Inlines


@dataclass
class ListItem:
    number: str | None        # None: bulleted
    inlines: Inlines
    subitems: list[Inlines] = field(default_factory=list)


@dataclass
class ListBlock(Block):
    items: list[ListItem]


@dataclass
class Callout:
    lines: list[int]          # 1-based line numbers in the listing text, blank lines included
    inlines: Inlines
    top: float                # y of the callout text on its page (orders callouts on the same line)


@dataclass
class Code(Block):
    """A code block. A listing has a title or callouts; lid names its images."""
    lines: list[str]
    marks: list[str]          # emphasis per character of each line ("." plain, "b", "i", "B" bold italic)
    title: str = ""
    lid: str = ""
    callouts: list[Callout] = field(default_factory=list)
    images: list[ImageRef] = field(default_factory=list)


@dataclass
class Figure(Block):
    id: str                   # "4.9", or the page-figure id ("chapter-map")
    image: ImageRef
    alt: str
    caption: Inlines | None   # None: an uncaptioned page figure
    description: str | None   # added in conversion (profile data); None: missing


@dataclass
class Table(Block):
    caption: Inlines | None
    header: list[Inlines]
    rows: list[list[Inlines]]


@dataclass
class Note(Block):
    label: str                # DEFINITION, TIP, NOTE, ...
    inlines: Inlines
    continued: list[Block] = field(default_factory=list)  # parts after a "(continued)" page break


@dataclass
class Sidebar(Block):
    title: Inlines            # may be empty (sidebar text found without a title)
    children: list[Block] = field(default_factory=list)   # Paragraph | ListBlock | Code


@dataclass
class Footnote(Block):
    num: str
    inlines: Inlines


@dataclass
class SectionDoc:
    section: Section
    blocks: list[Block]
    footnotes: list[Footnote]
