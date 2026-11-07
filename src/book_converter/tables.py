"""Tables: caption "Table N.N", horizontal rules (full width), vertical rules between columns,
a blue header band, and no rules between body rows (rows split on vertical gaps)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

import pymupdf

from .listings import hexrgb
from .pdf import Book, Span


@dataclass(frozen=True)
class TableConfig:
    """The book's table style (set by the profile)."""
    header_fill: str                          # fill colour of the header band
    rule_fill: str                            # fill colour of the horizontal and vertical rules
    row_gap: float                            # a baseline step larger than this starts a new body row
    caption_word: str                         # caption starts with this word ("Table")
    id_pattern: str                           # the table id inside the caption start
    is_caption_font: Callable[[Span], bool]
    is_caption_start: Callable[[Span], bool]
    is_figure_text: Callable[[Span], bool]


@dataclass(eq=False)
class Table:
    id: str
    page: int
    top: float
    caption: list[Span]
    header: list[list[Span]]           # one span list per column
    rows: list[list[list[Span]]]       # rows -> columns -> spans
    spans: list[Span] = field(default_factory=list)  # every span consumed (caption + cells)
    region: pymupdf.Rect | None = None

    def markdown(self, render) -> str:
        def cell(spans: list[Span]) -> str:
            return render(spans).replace("|", "\\|") if spans else ""
        cap = re.sub(r"^Table (\d+\.\d+)\s*", r"**Table \1** ", render(self.caption)) if self.caption else ""
        lines = ["| " + " | ".join(cell(c) for c in self.header) + " |",
                 "|" + "|".join("---" for _ in self.header) + "|"]
        for r in self.rows:
            lines.append("| " + " | ".join(cell(c) for c in r) + " |")
        return (cap + "\n\n" if cap else "") + "\n".join(lines)


def _caption(spans: list[Span], start: Span, cfg: TableConfig) -> list[Span]:
    caps = sorted((s for s in spans if cfg.is_caption_font(s) and s.y0 >= start.y0 - 1), key=lambda s: (s.baseline, s.x0))
    out, last = [], start.baseline
    for s in caps:
        if abs(s.baseline - start.baseline) < 1.5 or 0 < s.baseline - last < 13.5:
            out.append(s)
            last = max(last, s.baseline)
    return out


def _rules(book: Book, pno: int, cfg: TableConfig):
    draws = book.drawings(pno)
    black = [d["rect"] for d in draws if d["type"] == "f" and hexrgb(d.get("fill")) == cfg.rule_fill]
    hrules = sorted((r for r in black if r.width > 100 and r.height < 1.0), key=lambda r: r.y0)
    vrules = [r for r in black if r.width < 0.6 and r.height > 10]
    heads = sorted((d["rect"] for d in draws if d["type"] == "f" and hexrgb(d.get("fill")) == cfg.header_fill
                    and d["rect"].height > 10), key=lambda r: (r.y0, r.x0))
    return hrules, vrules, heads


def regions(book: Book, pno: int, cfg: TableConfig) -> list[tuple[pymupdf.Rect, float, list[float]]]:
    """Table regions: (rect, header bottom y, inner column rule xs). A table starts at a rule with
    a blue header band directly below it."""
    hrules, vrules, heads = _rules(book, pno, cfg)
    out = []
    seen: list[float] = []
    for h in heads:
        if any(abs(h.y0 - y) < 1 for y in seen):
            continue
        seen.append(h.y0)
        band = [x for x in heads if abs(x.y0 - h.y0) < 1]
        x0, x1 = min(x.x0 for x in band), max(x.x1 for x in band)
        top = next((r for r in hrules if abs(r.y0 - h.y0) < 1.5 and abs(r.x0 - x0) < 2), None)
        if top is None:
            continue
        chain = [r for r in hrules if r.y0 >= top.y0 and abs(r.x0 - top.x0) < 2 and abs(r.x1 - top.x1) < 2]
        vs = sorted({round(r.x0, 1) for r in vrules if abs(r.y0 - top.y0) < 2 and top.x0 + 2 < r.x0 < top.x1 - 2})
        if vs:
            bottom = max(r.y1 for r in vrules if abs(r.y0 - top.y0) < 2 and round(r.x0, 1) in vs)
        else:
            bottom = chain[1].y1 if len(chain) > 1 else top.y1
            for r in chain[1:]:
                if r.y0 - bottom > 60:
                    break
                bottom = r.y1
        out.append((pymupdf.Rect(top.x0, top.y0, top.x1, bottom), max(x.y1 for x in band), vs))
    return out


def detect(book: Book, pno: int, warn, cfg: TableConfig) -> list[Table]:
    spans = book.spans(pno)
    starts = [s for s in spans if cfg.is_caption_start(s) and s.text.strip().startswith(cfg.caption_word)]
    out = []
    used_caps: set[int] = set()
    for region, head_bottom, vs in regions(book, pno, cfg):
        st = next((s for s in starts if region.y0 - 40 < s.y0 < region.y0), None)
        if st is not None:
            used_caps.add(id(st))
            cap = _caption(spans, st, cfg)
            tid = re.search(cfg.id_pattern, st.text).group(0)
        else:
            cap, tid = [], f"uncaptioned-p{pno}"
            warn(f"p{pno}: table without a caption at y={region.y0:.0f}")
        if not vs:
            warn(f"p{pno}: table {tid}: no vertical rules; columns from header text")
            hsp = sorted((s for s in spans if region.contains(pymupdf.Point(s.cx, s.cy)) and s.cy < head_bottom
                          and s.text.strip()), key=lambda s: s.x0)
            vs = [round((a.x1 + b.x0) / 2, 1) for a, b in zip(hsp, hsp[1:]) if b.x0 - a.x1 > 8]
        edges = [region.x0] + vs + [region.x1]
        ncol = len(edges) - 1

        def col_of(s: Span) -> int:
            for i in range(ncol):
                if edges[i] - 1 <= s.cx < edges[i + 1] + 1:
                    return i
            return ncol - 1

        cells = [s for s in spans if region.contains(pymupdf.Point(s.cx, s.cy)) and not cfg.is_figure_text(s)]
        header: list[list[Span]] = [[] for _ in range(ncol)]
        body = []
        for s in cells:
            if not s.text.strip():
                continue
            (header[col_of(s)].append(s) if s.cy < head_bottom else body.append(s))
        rows: list[list[list[Span]]] = []
        last_base = None
        for s in sorted(body, key=lambda s: (s.baseline, s.x0)):
            if last_base is None or s.baseline - last_base > cfg.row_gap:
                rows.append([[] for _ in range(ncol)])
            if last_base is None or s.baseline > last_base:
                last_base = s.baseline
            rows[-1][col_of(s)].append(s)
        for s in body:
            for e in vs:
                if s.x0 < e - 1 and s.x1 > e + 1:
                    warn(f"p{pno}: table {tid}: cell text crosses a column rule: {s.text.strip()[:40]!r}")
        out.append(Table(tid, pno, (cap[0].y0 if cap else region.y0), cap, header, rows, cap + cells, region))
    for s in starts:
        if id(s) not in used_caps:
            warn(f"p{pno}: table caption without a table: {s.text.strip()!r}")
    return out
