"""Style classes for this book, from font + size + color (PyMuPDF units: points, sRGB hex).

Constants come from `uv run census` over PDF pages 16-296.
"""

from __future__ import annotations

import re

from .pdf import Span

BLUE = "#476b85"        # headings, sidebar titles, note labels
CALLOUT_GRAY = "#656565"  # listing callouts, captions, running headers
GOLD = "#cca659"        # bullets and list numbers
LISTING_BAR = "#70a6cc"  # listing header bar fill
SIDEBAR_BG = "#f7f5e8"

# Colors only used by text drawn inside figures (Illustrator art), never by book text.
FIGURE_COLORS = {"#231f20", "#363a3c", "#e6e7e8", "#808285", "#58595b", "#181c20", "#e8e8e8"}

HEADER_BAND = 46.0   # baseline above this = running header / page number
CODE_SIZE = 7.98     # code block Courier
INLINE_CODE_SIZE = 9.03
CODE_CW = 4.788      # Courier 7.98 pt advance (0.6 em)
CODE_PITCH = 10.0
BODY_SIZE = 9.96
ODD_SHIFT = 9.0      # odd pages sit 9 pt right of even pages
COL_LEFT_EVEN = 93.1
COL_RIGHT_EVEN = 465.1


def near(a: float, b: float, tol: float = 0.06) -> bool:
    return abs(a - b) <= tol


def fam(s: Span) -> str:
    return s.font


def is_ws(s: Span) -> bool:
    return not s.text.strip()


def is_figure_text(s: Span) -> bool:
    """Text that belongs to figure art (labels), never book prose."""
    if s.font.startswith(("Arial", "Helvetica")):
        return True
    if s.color in FIGURE_COLORS:
        return True
    if s.font.startswith("HumanistMann"):
        return True
    return False


def is_code_block(s: Span) -> bool:
    return s.font.startswith("Courier") and near(s.size, CODE_SIZE) and s.color in ("#262626", "#000000")


def is_inline_code(s: Span) -> bool:
    return s.font.startswith("Courier") and near(s.size, INLINE_CODE_SIZE)


def is_callout(s: Span) -> bool:
    return s.font.startswith("Humanist521") and s.color == CALLOUT_GRAY


def is_listing_head(s: Span) -> bool:
    return s.color == "#ffffff" and s.size >= 8.9 and s.font.startswith(("FranklinGothic", "Courier"))


def is_caption_font(s: Span) -> bool:
    return s.color == CALLOUT_GRAY and (
        (s.font.startswith("FranklinGothic-Demi") and near(s.size, 7.98))
        or (s.font.startswith("Courier") and near(s.size, 8.28)))


def is_caption_start(s: Span) -> bool:
    return is_caption_font(s) and re.match(r"^\s*(Figure|Table)\s+\d+\.\d+", s.text) is not None


def is_bullet(s: Span) -> bool:
    return s.font.startswith(("Wingdings2", "ZapfDingbats"))


def is_list_number(s: Span) -> bool:
    return s.color == GOLD and s.font.startswith("FranklinGothic-Demi") and re.fullmatch(r"\s*\d+\s*", s.text) is not None


def is_fn_marker(s: Span) -> bool:
    return s.font.startswith("NewBaskerville") and s.size <= 6.6 and re.fullmatch(r"\s*\d+\s*", s.text) is not None


def is_fn_start_marker(s: Span) -> bool:
    return is_fn_marker(s) and s.size <= 5.6


def is_table_text(s: Span) -> bool:
    return s.font.startswith("FranklinGothic") and near(s.size, 7.98) and s.color in ("#262626", "#000000", "#ffffff")


def column_left(page: int) -> float:
    return COL_LEFT_EVEN + (ODD_SHIFT if page % 2 == 1 else 0.0)


def column_right(page: int) -> float:
    return COL_RIGHT_EVEN + (ODD_SHIFT if page % 2 == 1 else 0.0)
