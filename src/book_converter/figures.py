"""Figures: locate each captioned figure from drawing geometry, collect its label text,
and render it without the caption or surrounding prose (glyphs removed, no whiteout)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf

from . import tables as tabmod
from .listings import hexrgb, listing_bars
from .pdf import Book, Span
from .styles import (BLUE, HEADER_BAND, LISTING_BAR, SIDEBAR_BG, is_caption_font, is_caption_start,
                     is_figure_text)

DPI = 216
IGNORE_FILLS = {LISTING_BAR, SIDEBAR_BG, BLUE, "#ffffff", "#e6e6e6"}


@dataclass(eq=False)
class Figure:
    id: str
    page: int
    box: pymupdf.Rect
    caption: list[Span]
    labels: list[Span] = field(default_factory=list)
    rotate: int = 0

    @property
    def label_texts(self) -> list[str]:
        return [s.text.strip() for s in self.labels if s.text.strip()]


def caption_block(spans: list[Span], start: Span) -> list[Span]:
    """The caption's spans: the start span's row and the following caption-font rows."""
    caps = sorted((s for s in spans if is_caption_font(s) and s.y0 >= start.y0 - 1 and s.x1 > start.x0 - 2),
                  key=lambda s: (s.baseline, s.x0))
    out = [s for s in caps if abs(s.baseline - start.baseline) < 1.5]
    last = start.baseline
    for s in caps:
        if s in out:
            continue
        if 0 < s.baseline - last < 13.5:
            row = [t for t in caps if abs(t.baseline - s.baseline) < 1.5]
            out.extend(t for t in row if t not in out)
            last = s.baseline
    # stop at the next caption start
    return sorted(out, key=lambda s: (s.baseline, s.x0))


def _ink(book: Book, pno: int, exclude: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    rects: list[pymupdf.Rect] = []
    for d in book.drawings(pno):
        r = d["rect"]
        if r.y1 < HEADER_BAND:
            continue
        fill, stroke = hexrgb(d.get("fill")), hexrgb(d.get("color"))
        if d["type"] == "f" and fill in IGNORE_FILLS:
            continue
        if d["type"] == "s" and stroke in IGNORE_FILLS:
            continue
        if r.width > 300 and r.height < 1.0:
            continue  # table rule
        if any(r.intersects(x) for x in exclude):
            continue
        if r.is_empty and r.width == 0 and r.height == 0:
            continue
        rects.append(pymupdf.Rect(r))
    for s in book.spans(pno):
        if is_figure_text(s) and s.text.strip() and s.y0 > HEADER_BAND:
            rects.append(pymupdf.Rect(s.x0, s.y0, s.x1, s.y1))
    for info in book.page(pno).get_image_info():
        rects.append(pymupdf.Rect(info["bbox"]))
    return rects


def _clusters(rects: list[pymupdf.Rect], gap: float = 6.0) -> list[pymupdf.Rect]:
    parent = list(range(len(rects)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    grown = [r + (-gap, -gap, gap, gap) for r in rects]
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            if grown[i].intersects(grown[j]):
                parent[find(i)] = find(j)
    boxes: dict[int, pymupdf.Rect] = {}
    for i, r in enumerate(rects):
        k = find(i)
        boxes[k] = boxes[k] | r if k in boxes else pymupdf.Rect(r)
    return list(boxes.values())


def listing_areas(book: Book, pno: int) -> list[pymupdf.Rect]:
    """Rough listing regions (bar to the last code/callout line below it) for excluding callout art."""
    areas = []
    spans = book.spans(pno)
    for bar in listing_bars(book, pno):
        below = [s for s in spans if s.y0 > bar.y1 - 1 and (s.font.startswith(("Courier", "Humanist521")))]
        y1 = bar.y1
        for s in sorted(below, key=lambda s: s.y0):
            if s.y0 - y1 > 30:
                break
            y1 = max(y1, s.y1)
        areas.append(pymupdf.Rect(0, bar.y0, book.page(pno).rect.x1, y1 + 4))
    return areas


def detect(book: Book, pno: int, warn) -> list[Figure]:
    spans = book.spans(pno)
    starts = [s for s in spans if is_caption_start(s) and s.text.strip().startswith("Figure")]
    if not starts:
        return []
    excl = listing_areas(book, pno) + [r for r, _, _ in tabmod.regions(book, pno)]
    caps = {id(st): caption_block(spans, st) for st in starts}
    cap_rects = []
    for st in starts:
        cb = caps[id(st)]
        r = pymupdf.Rect(cb[0].x0, cb[0].y0, cb[0].x1, cb[0].y1)
        for s in cb[1:]:
            r |= (s.x0, s.y0, s.x1, s.y1)
        cap_rects.append(r)
    clusters = [c for c in _clusters(_ink(book, pno, excl)) if c.width * c.height > 60]
    cap_ids = {id(s) for cb in caps.values() for s in cb}
    prose = [s for s in spans if s.text.strip() and s.y0 > HEADER_BAND and not is_figure_text(s)
             and id(s) not in cap_ids]
    used: set[int] = set()
    figs = []
    for st, cr in zip(starts, cap_rects):
        def dist(c: pymupdf.Rect) -> float:
            dx = max(c.x0 - cr.x1, cr.x0 - c.x1, 0)
            if c.y1 <= cr.y0 + 2:
                dy = cr.y0 - c.y1
            elif c.y0 >= cr.y1 - 2:
                dy = (c.y0 - cr.y1) * 1.5
            else:
                dy = 0
            return (dx * dx + dy * dy) ** 0.5
        cand = sorted((i for i in range(len(clusters)) if i not in used), key=lambda i: dist(clusters[i]))
        if not cand:
            warn(f"p{pno}: no figure art near caption {st.text.strip()!r}")
            continue
        i = cand[0]
        used.add(i)
        box = pymupdf.Rect(clusters[i])
        # grow: merge other clusters while the union contains no prose and no other caption
        other_caps = [r for r in cap_rects if r != cr]
        grown = True
        while grown:
            grown = False
            for j in range(len(clusters)):
                if j in used:
                    continue
                u = box | clusters[j]
                if any(u.contains(pymupdf.Point(s.cx, s.cy)) for s in prose):
                    continue
                if any(u.intersects(r) for r in other_caps):
                    continue
                box = u
                used.add(j)
                grown = True
        box = box + (-3, -3, 3, 3)
        box &= book.page(pno).rect
        fid = re.search(r"\d+\.\d+", st.text).group(0)
        labels = [s for s in spans if is_figure_text(s) and box.contains(pymupdf.Point(s.cx, s.cy))]
        figs.append(Figure(fid, pno, box, caps[id(st)], labels))
    return figs


def page_figure(book: Book, pno: int, fid: str, rotate: int = 0) -> Figure:
    """A figure without a caption that fills its page (the inside-cover chapter map)."""
    box = None
    for c in _clusters(_ink(book, pno, [])):
        if c.width * c.height > 60:
            box = c if box is None else box | c
    box = (box + (-3, -3, 3, 3)) & book.page(pno).rect
    labels = [s for s in book.spans(pno) if is_figure_text(s) and box.contains(pymupdf.Point(s.cx, s.cy))]
    return Figure(fid, pno, box, [], labels, rotate)


def render(book: Book, pno: int, box: pymupdf.Rect, keep, dest: str, rotate: int = 0) -> None:
    """Render `box` at 216 dpi after removing the glyphs of every span inside it that `keep` rejects."""
    tmp = pymupdf.open()
    tmp.insert_pdf(book.doc, from_page=pno - 1, to_page=pno - 1)
    page = tmp[0]
    n = 0
    for s in book.spans(pno):
        if keep(s) or not s.text.strip():
            continue
        r = pymupdf.Rect(s.x0, s.y0, s.x1, s.y1)
        if not r.intersects(box):
            continue
        page.add_redact_annot(r + (0.3, 0.6, -0.3, -0.6), fill=False)
        n += 1
    if n:
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                              text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    m = pymupdf.Matrix(DPI / 72, DPI / 72).prerotate(rotate)
    page.get_pixmap(matrix=m, clip=box).save(dest)
    tmp.close()
