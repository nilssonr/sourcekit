"""Factories for PDF-free tests: spans with character boxes, a fake Book, and every config with one
place to look for its defaults. Tests state only what they override.

Fonts name the part a span plays (BODY, CODE, BULLET, ...); the config predicates key on them.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace

import pymupdf
from doc_converter.core.hyphens import Resolver
from doc_converter.core.pdf import Char, OutlineEntry, Span, is_ws
from doc_converter.core.rows import Row, RowConfig, Text
from doc_converter.core.verify import VerifyConfig
from doc_converter.modules.assemble.flow import Assembler, FlowConfig
from doc_converter.modules.regions.figures import FigureConfig
from doc_converter.modules.regions.listings import ListingConfig
from doc_converter.modules.regions.page import PageConfig
from doc_converter.modules.regions.tables import TableConfig
from doc_converter.modules.serialize.markdown import MarkdownConfig
from doc_converter.modules.split.outline import FrontMatter, SectionRule, SplitConfig, slug

# ---------------------------------------------------------------- geometry
CW = 5.0  # character advance of span()
COLUMN_LEFT = 72.0  # text column on every page (odd_shift is 0 unless a test sets it)
COLUMN_RIGHT = 372.0  # a line whose ink reaches this is a full line
HEADER_BAND = 40.0  # baselines above this are running headers
PAGE_RECT = pymupdf.Rect(0, 0, 450, 700)
LINE_PITCH = 10.0  # code line spacing
BODY_SIZE = 10.0
CODE_SIZE = 8.0  # code blocks; inline code is INLINE_CODE_SIZE in the same font
INLINE_CODE_SIZE = 9.0

# ---------------------------------------------------------------- colours (drawing fills)
BLUE = "#476b85"
LISTING_BAR = "#70a6cc"
SIDEBAR_BG = "#f7f5e8"
BLACK = "#000000"

# ---------------------------------------------------------------- fonts = roles
BODY, BOLD, ITALIC, BOLD_ITALIC = "Body", "Body-Bold", "Body-Ital", "Body-BoldItal"
EPIGRAPH = "Epigraph"
CODE, CODE_BOLD, CODE_ITALIC, CODE_BOLD_ITALIC = "Code", "Code-Bold", "Code-Ital", "Code-BoldItal"
CALLOUT = "Callout"
LISTING_HEAD = "ListingHead"
BULLET, LIST_NUMBER, COVERS_BULLET = "Bullet", "ListNumber", "CoversBullet"
NOTE_LABEL = "NoteLabel"
FIGURE_TEXT = "FigureText"
CAPTION = "Caption"
TITLE, DROPCAP, H2, H3, SMALLCAPS, MAP_TITLE = "Title", "Dropcap", "Head2", "Head3", "Smallcaps", "MapTitle"
COVERS_TITLE, COVERS_CONT = "CoversTitle", "CoversCont"
SIDEBAR_TITLE, SIDEBAR_TEXT, CONTINUED = "SidebarTitle", "SidebarText", "Continued"
PAGENUM = "PageNum"
FN_MARKER = "FnMarker"  # footnote reference in the text; size <= FN_START_SIZE starts a footnote
FOOTNOTE_TEXT = "FootnoteText"
FN_START_SIZE = 5.0

ROLE_BY_FONT = {
    BULLET: "bullet",
    LIST_NUMBER: "bullet",
    COVERS_BULLET: "covers-item",
    NOTE_LABEL: "note-start",
    TITLE: "title",
    DROPCAP: "dropcap",
    H2: "h2",
    H3: "h3",
    SMALLCAPS: "smallcaps",
    MAP_TITLE: "map-title",
    COVERS_TITLE: "covers-title",
    COVERS_CONT: "covers-cont",
    SIDEBAR_TITLE: "sidebar-title",
    SIDEBAR_TEXT: "sidebar-text",
    CONTINUED: "continued",
    CAPTION: "caption",
    PAGENUM: "pagenum",
    FOOTNOTE_TEXT: "footnote-cont",
    EPIGRAPH: "epigraph",
}


# ---------------------------------------------------------------- predicates
def is_code_font(s: Span) -> bool:
    return s.font.startswith(CODE)


def is_code_block(s: Span) -> bool:
    return is_code_font(s) and s.size == CODE_SIZE


def is_callout(s: Span) -> bool:
    return s.font == CALLOUT


def is_bullet(s: Span) -> bool:
    return s.font in (BULLET, COVERS_BULLET)


def is_list_number(s: Span) -> bool:
    return s.font == LIST_NUMBER


def is_note_label(s: Span) -> bool:
    return s.font == NOTE_LABEL


def is_figure_text(s: Span) -> bool:
    return s.font == FIGURE_TEXT


def is_caption_font(s: Span) -> bool:
    return s.font == CAPTION


def is_caption_start(s: Span) -> bool:
    import re

    return is_caption_font(s) and re.match(r"^\s*(Figure|Table)\s+\d+\.\d+", s.text) is not None


def is_listing_head(s: Span) -> bool:
    return s.font == LISTING_HEAD


def is_fn_marker(s: Span) -> bool:
    return s.font == FN_MARKER


def is_fn_start_marker(s: Span) -> bool:
    return is_fn_marker(s) and s.size <= FN_START_SIZE


def is_body_text(s: Span) -> bool:
    return s.font.startswith(BODY) and s.size == BODY_SIZE


def is_page_number(s: Span) -> bool:
    return s.font == PAGENUM


def is_continuation_label(s: Span) -> bool:
    return s.font == CONTINUED


def column_left(page: int) -> float:
    return COLUMN_LEFT


def column_right(page: int) -> float:
    return COLUMN_RIGHT


def role(r: Row) -> str:
    """The row's role from its first inked span's font, as a profile's role function would."""
    inked = [s for s in r.spans if not is_ws(s)]
    if not inked:
        return "blank"
    if r.top < HEADER_BAND:
        return "header"
    k = inked[0]
    if is_fn_start_marker(k):
        return "footnote"
    if is_code_font(k) and k.size == INLINE_CODE_SIZE:
        # a line that starts with inline code takes its role from the first non-code span
        k = next((s for s in inked if not is_code_font(s) and not is_fn_marker(s)), k)
    if is_bullet(k) or is_list_number(k):
        if k.font == COVERS_BULLET:
            return "covers-item"
        text = next((s for s in inked if not is_bullet(s) and not is_list_number(s)), None)
        return "sidebar-text" if text is not None and text.font == SIDEBAR_TEXT else "bullet"
    if k.font in ROLE_BY_FONT:
        return ROLE_BY_FONT[k.font]
    if k.font.startswith(BODY) or is_code_font(k):
        return "body"
    return "unknown"


# ---------------------------------------------------------------- spans, rows, drawings
def span(
    text: str,
    *,
    page: int = 1,
    x0: float = COLUMN_LEFT,
    x1: float | None = None,
    baseline: float = 100.0,
    font: str = BODY,
    size: float = BODY_SIZE,
    color: str = BLACK,
) -> Span:
    """A span with one Char per character. ``x1`` stretches the characters so the span ends there
    (``x1=COLUMN_RIGHT`` makes a full line); otherwise each character advances CW."""
    cw = (x1 - x0) / len(text) if x1 is not None and text else CW
    chars = [Char(c, x0 + i * cw, x0 + (i + 1) * cw, baseline) for i, c in enumerate(text)]
    end = x0 + cw * len(text)
    return Span(page, x0, baseline - 0.8 * size, end, baseline + 0.2 * size, font, size, color, text, chars, baseline)


def row(*spans: Span, cls: str | None = None) -> Row:
    """A Row straight from spans (bypasses Text.rows_of); cls defaults to role()."""
    r = Row(spans[0].page, spans[0].baseline, list(spans))
    inked = [s for s in spans if not is_ws(s)] or list(spans)
    r.left = min(s.x0 for s in inked)
    r.right = max(s.chars[-1].x1 if s.chars else s.x0 for s in inked)
    r.cls = role(r) if cls is None else cls
    return r


def rgb(hex_colour: str) -> tuple[float, float, float]:
    h = hex_colour.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))


def fill(x0: float, y0: float, x1: float, y1: float, colour: str, items: int = 1) -> dict:
    """A filled vector drawing as PyMuPDF reports it (fill as 0-1 floats)."""
    return {
        "type": "f",
        "rect": pymupdf.Rect(x0, y0, x1, y1),
        "fill": rgb(colour),
        "color": None,
        "items": [("l",)] * items,
    }


def stroke(x0: float, y0: float, x1: float, y1: float, colour: str) -> dict:
    return {"type": "s", "rect": pymupdf.Rect(x0, y0, x1, y1), "fill": None, "color": rgb(colour), "items": [("l",)]}


def leader(x0: float, y0: float, x1: float, y1: float, colour: str = BLACK) -> dict:
    """A callout leader or bracket bar: a filled rectangle thinner than 0.7 pt."""
    return fill(x0, y0, x1, y1, colour)


def arrowhead(x: float, y: float, colour: str = BLACK) -> dict:
    """A callout arrowhead: a small filled outline (at least three path items) centred on (x, y)."""
    return fill(x - 2, y - 2, x + 2, y + 2, colour, items=3)


class FakeBook:
    """What the PDF-free modules read from a Book: spans and drawings per page, the outline, page labels
    and the page rectangle. ``spans(p)`` returns the same list each time, because callers key on ``id(span)``."""

    def __init__(
        self,
        pages: dict[int, list[Span]] | None = None,
        *,
        drawings: dict[int, list[dict]] | None = None,
        outline: list[OutlineEntry] = (),
        labels: dict[int, str] | None = None,
    ):
        self._pages = {p: list(spans) for p, spans in (pages or {}).items()}
        self._drawings = drawings or {}
        self._outline = list(outline)
        self._labels = labels or {}
        self.path = "<fake book>"

    @property
    def page_count(self) -> int:
        return max([*self._pages, *self._drawings], default=0)

    def spans(self, p: int) -> list[Span]:
        return self._pages.setdefault(p, [])

    def all_spans(self, p0: int, p1: int) -> list[Span]:
        return [s for p in range(p0, p1 + 1) for s in self.spans(p)]

    def drawings(self, p: int) -> list[dict]:
        return self._drawings.get(p, [])

    def span_id(self, s: Span) -> tuple[int, int]:
        return (s.page, next(i for i, t in enumerate(self._pages[s.page]) if t is s))

    def page_label(self, p: int) -> str:
        return self._labels.get(p, str(p))

    def outline(self) -> list[OutlineEntry]:
        return list(self._outline)

    def page(self, p: int):
        return SimpleNamespace(rect=pymupdf.Rect(PAGE_RECT), get_image_info=lambda: [])


# ---------------------------------------------------------------- configs
def listing_config(**over) -> ListingConfig:
    return replace(
        ListingConfig(
            bar_fill=LISTING_BAR,
            is_code_block=is_code_block,
            is_callout=is_callout,
            column_left=column_left,
            char_width=CW,
            line_pitch=LINE_PITCH,
            line_centre=2.0,
            bar_min_width=100,
            callout_ink=BLACK,
        ),
        **over,
    )


def table_config(**over) -> TableConfig:
    return replace(
        TableConfig(
            header_fill=BLUE,
            rule_fill=BLACK,
            row_gap=13.0,
            caption_word="Table",
            id_pattern=r"\d+\.\d+",
            is_caption_font=is_caption_font,
            is_caption_start=is_caption_start,
            is_figure_text=is_figure_text,
        ),
        **over,
    )


def figure_config(**over) -> FigureConfig:
    return replace(
        FigureConfig(
            header_band=HEADER_BAND,
            ignore_fills=frozenset({LISTING_BAR, SIDEBAR_BG, BLUE, "#ffffff"}),
            caption_word="Figure",
            id_pattern=r"\d+\.\d+",
            listing_fonts=(CODE, CALLOUT),
            is_caption_font=is_caption_font,
            is_caption_start=is_caption_start,
            is_figure_text=is_figure_text,
            listings=listing_config(),
            tables=table_config(),
        ),
        **over,
    )


def page_config(**over) -> PageConfig:
    return replace(
        PageConfig(
            figures=figure_config(),
            tables=table_config(),
            listings=listing_config(),
            sidebar_fill=SIDEBAR_BG,
            sidebar_min_height=20,
            is_figure_text=is_figure_text,
            is_code_block=is_code_block,
            is_code_font=is_code_font,
            is_callout=is_callout,
            code_run_gap=23.0,
            code_run_break=2.7,
            callout_window=15,
        ),
        **over,
    )


def row_config(**over) -> RowConfig:
    return replace(
        RowConfig(
            role=role,
            is_fn_marker=is_fn_marker,
            is_bullet=is_bullet,
            is_list_number=is_list_number,
            is_inline_label=is_note_label,
            is_code_font=is_code_font,
            row_tol=2.7,
            dropcap_size=40,
            fn_attach=(-1.0, 5.0),
        ),
        **over,
    )


def flow_config(**over) -> FlowConfig:
    return replace(
        FlowConfig(
            page=page_config(),
            column_left=column_left,
            column_right=column_right,
            odd_shift=0.0,
            right_edge={"body": COLUMN_RIGHT, "sidebar": COLUMN_RIGHT - 12, "note": COLUMN_RIGHT - 24},
            right_slack=10.0,
            sidebar_quote_indent=12.0,
            listing_continue_top=70,
            gap_sidebar=15,
            gap_list=16,
            gap_note=15,
            gap_para=17,
            align_tol=4,
            indent_min=7,
            heading_merge_gap=18,
            smallcaps_lower_max=8.5,
            is_body_text=is_body_text,
            is_note_label=is_note_label,
            is_bullet=is_bullet,
            is_list_number=is_list_number,
            is_code_block=is_code_block,
            is_callout=is_callout,
            is_figure_text=is_figure_text,
            is_listing_head=is_listing_head,
            is_fn_start_marker=is_fn_start_marker,
            listing_id_pattern=r"^Listing (\d+\.\d+)",
            skip_pages=frozenset(),
            page_figures={},
        ),
        **over,
    )


def markdown_config(**over) -> MarkdownConfig:
    return replace(
        MarkdownConfig(
            figure_caption=(r"^Figure (\d+\.\d+)\s*", r"**Figure \1** "),
            table_caption=(r"^Table (\d+\.\d+)\s*", r"**Table \1** "),
            listing_title=(r"^(Listing \d+\.\d+)\s*", r"\1 "),
        ),
        **over,
    )


def verify_config(**over) -> VerifyConfig:
    return replace(
        VerifyConfig(
            header_band=HEADER_BAND,
            superscript_max=6.6,
            is_figure_text=is_figure_text,
            is_bullet_glyph=is_bullet,
            is_page_number=is_page_number,
            is_code_block=is_code_block,
            is_continuation_label=is_continuation_label,
            column_gap=12.0,
        ),
        **over,
    )


def split_config(**over) -> SplitConfig:
    """Parts are level-1 bookmarks, chapters level-2, content ends at the level-1 "index" bookmark."""
    return replace(
        SplitConfig(
            rules=[
                SectionRule(1, r"Part (\d+)—(.+)", lambda m: (f"part-{m[1]}", f"part-{m[1]}-{slug(m[2])}.md", "", "")),
                SectionRule(
                    2,
                    r"(\d+) (.+)",
                    lambda m: (
                        f"ch{int(m[1]):02d}",
                        f"chapter-{int(m[1]):02d}-{slug(m[2])}.md",
                        f"Chapter {m[1]}: {m[2]}",
                        f"Chapter {m[1]}: ",
                    ),
                ),
            ],
            end=(1, "index"),
            front=None,
        ),
        **over,
    )


def front_matter(**over) -> FrontMatter:
    return replace(FrontMatter("front", "front-matter.md", "Front matter", first_page=2, heading_shift=1), **over)


# ---------------------------------------------------------------- text and assembly
def dictionary_file(case: unittest.TestCase, words=("the",)) -> str:
    """A word list on disk for the hyphen resolver, removed when the test ends."""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".words", delete=False) as f:
        f.write("".join(w + "\n" for w in words))
    case.addCleanup(os.unlink, f.name)
    return f.name


def resolver(case: unittest.TestCase, corpus=(), dictionary=("the",), code=()) -> Resolver:
    """A Resolver whose book corpus is ``corpus`` (one span per entry; a multi-word entry is one line of
    prose), whose code spans are ``code``, and whose dictionary holds ``dictionary``."""
    spans = [span(t) for t in corpus] + [span(t, font=CODE, size=CODE_SIZE) for t in code]
    return Resolver(spans, is_code_font, dictionary_file(case, dictionary))


def text(case: unittest.TestCase, **resolver_kw) -> Text:
    return Text(row_config(), resolver(case, **resolver_kw))


def assembler(
    case: unittest.TestCase, book: FakeBook, descriptions: dict[str, str] | None = None, corpus=(), **flow_over
) -> tuple[Assembler, list[str]]:
    """An Assembler over ``book`` and the list its warnings go to. ``corpus`` seeds the hyphen resolver."""
    warnings: list[str] = []
    asm = Assembler(book, flow_config(**flow_over), text(case, corpus=corpus), descriptions or {}, warnings.append)
    return asm, warnings
