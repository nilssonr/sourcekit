"""One page's content in reading order: figures, tables, listings and code runs claim their spans
first (in that order); the remaining spans become rows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pymupdf

from ...core.pdf import Book, Span, is_ws
from ...core.rows import Text
from . import figures as figmod
from . import tables as tabmod
from .listings import ListingConfig, hexrgb, listing_bars, listing_parts


@dataclass(frozen=True)
class PageConfig:
    """Region detection on a page (set by the profile)."""

    figures: figmod.FigureConfig
    tables: tabmod.TableConfig
    listings: ListingConfig
    sidebar_fill: str  # background fill of sidebar boxes
    sidebar_min_height: float  # lower fills of that colour are not sidebar boxes
    is_figure_text: Callable[[Span], bool]
    is_code_block: Callable[[Span], bool]
    is_code_font: Callable[[Span], bool]  # whitespace in this font continues a code run
    is_callout: Callable[[Span], bool]
    code_run_gap: float  # a larger baseline step starts a new code run
    code_run_break: float  # non-code text this far below the run ends it
    callout_window: float  # callouts this close above/below a code run belong to it


@dataclass
class PageContent:
    spans: list[Span]  # every span on the page
    sidebar_boxes: list[pymupdf.Rect]
    items: list[tuple[float, str, object]]  # (top, kind, object) sorted by top; kinds:
    # fig, table, listing (bar, head, body), code (ink, callouts), row


def page_content(
    book: Book, p: int, cfg: PageConfig, text: Text, page_figure: tuple[str, int] | None, warn
) -> PageContent:
    pf = book.spans(p)
    sidebar_boxes = [
        d["rect"]
        for d in book.drawings(p)
        if d["type"] == "f" and hexrgb(d.get("fill")) == cfg.sidebar_fill and d["rect"].height > cfg.sidebar_min_height
    ]
    used: set[int] = set()
    items: list[tuple[float, str, object]] = []

    # figures
    page_figs = [figmod.page_figure(book, p, cfg.figures, *page_figure)] if page_figure else []
    for fig in page_figs + figmod.detect(book, p, warn, cfg.figures):
        for s in fig.labels + fig.caption:
            used.add(id(s))
        # a page figure has no caption; it follows its heading
        items.append((fig.box.y0 if fig.caption else fig.box.y1, "fig", fig))
    for s in pf:
        if cfg.is_figure_text(s) and id(s) not in used and s.text.strip():
            warn(f"figure-font text outside any figure p{p}: {s.text.strip()!r}")
        if cfg.is_figure_text(s):
            used.add(id(s))

    # tables
    for tab in tabmod.detect(book, p, warn, cfg.tables):
        for s in tab.spans:
            used.add(id(s))
        items.append((tab.top, "table", tab))

    # listings: header bar + following code/callout spans
    srt = sorted((s for s in pf if id(s) not in used), key=lambda s: (s.baseline, s.x0))
    for bar in listing_bars(book, p, cfg.listings):
        head, body = listing_parts(srt, bar, used, cfg.listings)
        items.append((bar.y0, "listing", (bar, head, body)))

    # code runs (snippets and listing continuations), with nearby callouts
    rest = [s for s in srt if id(s) not in used]
    runs: list[list[Span]] = []
    cur: list[Span] = []
    for s in rest:
        if cfg.is_code_block(s) or (cur and is_ws(s) and cfg.is_code_font(s)):
            if cur and s.baseline - cur[-1].baseline > cfg.code_run_gap:
                runs.append(cur)
                cur = []
            cur.append(s)
            used.add(id(s))
        elif cur and s.baseline > cur[-1].baseline + cfg.code_run_break and not cfg.is_callout(s):
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    for run in runs:
        ink = [s for s in run if not is_ws(s)]
        if not ink:
            continue
        y0, y1 = min(s.y0 for s in ink) - cfg.callout_window, max(s.y1 for s in ink) + cfg.callout_window
        calls = [s for s in rest if cfg.is_callout(s) and id(s) not in used and y0 <= s.cy <= y1]
        for s in calls:
            used.add(id(s))
        items.append((min(s.baseline for s in ink), "code", (ink, calls)))

    for r in text.rows_of([s for s in pf if id(s) not in used]):
        items.append((r.top, "row", r))
    items.sort(key=lambda it: it[0])
    return PageContent(pf, sidebar_boxes, items)
