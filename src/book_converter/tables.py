"""Tables: caption "Table N.N", horizontal rules (full width), vertical rules between columns,
a blue header band, and no rules between body rows (rows split on vertical gaps)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf

from .listings import hexrgb
from .pdf import Book, Span
from .styles import is_caption_font, is_caption_start, is_figure_text

ROW_GAP = 13.0


@dataclass(eq=False)
class Table:
    id: str
    page: int
    top: float
    caption: list[Span]
    header: list[list[Span]]           # one span list per column
    rows: list[list[list[Span]]]       # rows -> columns -> spans
    spans: list[Span] = field(default_factory=list)  # every span consumed (caption + cells)

    def markdown(self, render) -> str:
        def cell(spans: list[Span]) -> str:
            return render(spans).replace("|", "\\|") if spans else ""
        cap = render(self.caption)
        cap = re.sub(r"^Table (\d+\.\d+)\s*", r"**Table \1** ", cap)
        lines = ["| " + " | ".join(cell(c) for c in self.header) + " |",
                 "|" + "|".join("---" for _ in self.header) + "|"]
        for r in self.rows:
            lines.append("| " + " | ".join(cell(c) for c in r) + " |")
        return cap + "\n\n" + "\n".join(lines)


def _caption(spans: list[Span], start: Span) -> list[Span]:
    caps = sorted((s for s in spans if is_caption_font(s) and s.y0 >= start.y0 - 1), key=lambda s: (s.baseline, s.x0))
    out, last = [], start.baseline
    for s in caps:
        if abs(s.baseline - start.baseline) < 1.5 or 0 < s.baseline - last < 13.5:
            out.append(s)
            last = max(last, s.baseline)
    return out


def detect(book: Book, pno: int, warn) -> list[Table]:
    spans = book.spans(pno)
    starts = [s for s in spans if is_caption_start(s) and s.text.strip().startswith("Table")]
    out = []
    draws = book.drawings(pno)
    hrules = sorted((d["rect"] for d in draws if d["type"] == "f" and hexrgb(d.get("fill")) == "#000000"
                     and d["rect"].width > 300 and d["rect"].height < 1.0), key=lambda r: r.y0)
    vrules = [d["rect"] for d in draws if d["type"] == "f" and hexrgb(d.get("fill")) == "#000000"
              and d["rect"].width < 0.6 and d["rect"].height > 10]
    for st in starts:
        tid = re.search(r"\d+\.\d+", st.text).group(0)
        cap = _caption(spans, st)
        cap_bottom = max(s.y1 for s in cap)
        hs = [r for r in hrules if r.y0 > cap_bottom - 1]
        if len(hs) < 2:
            warn(f"p{pno}: table {tid}: fewer than two rules below the caption")
            continue
        top = hs[0]
        # bottom: last rule before a big gap
        chain = [top]
        for r in hs[1:]:
            if r.y0 - chain[-1].y1 > 200:
                break
            chain.append(r)
        vs = sorted({round(r.x0, 1) for r in vrules if r.y0 < top.y1 + 2 and r.y1 > top.y1 + 5})
        if vs:
            bottom_y = max(r.y1 for r in vrules if round(r.x0, 1) in vs)
            chain = [r for r in chain if r.y0 <= bottom_y + 1]
        if len(chain) < 2:
            warn(f"p{pno}: table {tid}: cannot find its bottom rule")
            continue
        region = pymupdf.Rect(top.x0, top.y0, top.x1, chain[-1].y1)
        head_bottom = chain[1].y0
        if not vs:
            warn(f"p{pno}: table {tid}: no vertical rules; columns from header text")
            head_spans = [s for s in spans if region.contains(pymupdf.Point(s.cx, s.cy)) and s.cy < head_bottom
                          and s.text.strip()]
            vs = sorted({round((a.x1 + b.x0) / 2, 1) for a, b in
                         zip(sorted(head_spans, key=lambda s: s.x0), sorted(head_spans, key=lambda s: s.x0)[1:])})
        edges = [region.x0] + vs + [region.x1]
        ncol = len(edges) - 1

        def col_of(s: Span) -> int:
            for i in range(ncol):
                if edges[i] - 1 <= s.cx < edges[i + 1] + 1:
                    return i
            return ncol - 1

        cells = [s for s in spans if region.contains(pymupdf.Point(s.cx, s.cy)) and not is_figure_text(s)]
        header = [[] for _ in range(ncol)]
        body = []
        for s in cells:
            if not s.text.strip():
                continue
            (header[col_of(s)].append(s) if s.cy < head_bottom else body.append(s))
        rows: list[list[list[Span]]] = []
        last_base = None
        for s in sorted(body, key=lambda s: (s.baseline, s.x0)):
            if last_base is None or s.baseline - last_base > ROW_GAP:
                rows.append([[] for _ in range(ncol)])
            if last_base is None or s.baseline > last_base:
                last_base = s.baseline
            rows[-1][col_of(s)].append(s)
        # sanity: a span must not straddle a column edge
        for s in body:
            for e in vs:
                if s.x0 < e - 1 and s.x1 > e + 1:
                    warn(f"p{pno}: table {tid}: cell text crosses a column rule: {s.text.strip()[:40]!r}")
        out.append(Table(tid, pno, st.y0, cap, header, rows, cap + cells))
    return out
