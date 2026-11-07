"""Code lines and callout arrows/brackets for listings.

Callout graphics are vector paths (FrameMaker): leaders are 0.24 pt filled rectangles,
arrowheads are small filled outlines, brackets are leaders without arrowheads.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pymupdf

from ...core.pdf import Book, Span, is_ws


@dataclass(frozen=True)
class ListingConfig:
    """The book's listing and code-block style (set by the profile)."""
    bar_fill: str                            # fill colour of the listing header bar
    is_code_block: Callable[[Span], bool]    # code block text (not inline code)
    is_callout: Callable[[Span], bool]       # callout text beside a listing
    column_left: Callable[[int], float]      # left edge of the text column on a PDF page
    char_width: float                        # advance of the fixed-pitch code font
    line_pitch: float                        # code line spacing
    line_centre: float                       # code line centre = baseline - line_centre
    bar_min_width: float                     # narrower fills of the bar colour are not listing bars
    callout_ink: str                         # colour of callout arrows, leaders and brackets


@dataclass
class CodeLine:
    text: str
    baseline: float
    page: int
    x0: float = 0.0  # x of the first non-space character


@dataclass
class Callout:
    spans: list[Span]           # the callout text spans (one block)
    lines: list[int] = field(default_factory=list)  # 1-based line numbers in the listing
    kind: str = ""              # "arrow" | "bracket" | "none"

    @property
    def top(self) -> float:
        return min(s.y0 for s in self.spans)


def hexrgb(c) -> str | None:
    return None if c is None else "#%02x%02x%02x" % tuple(round(v * 255) for v in c[:3])


def listing_bars(book: Book, pno: int, cfg: ListingConfig) -> list[pymupdf.Rect]:
    return sorted((d["rect"] for d in book.drawings(pno)
                   if d["type"] == "f" and hexrgb(d.get("fill")) == cfg.bar_fill
                   and d["rect"].width > cfg.bar_min_width),
                  key=lambda r: r.y0)


def listing_parts(spans: list[Span], bar: pymupdf.Rect, used: set[int],
                  cfg: ListingConfig) -> tuple[list[Span], list[Span]]:
    """Header spans inside the listing bar, and the code/callout spans that follow it.

    `spans` must be sorted by (baseline, x0); consumed spans are added to `used`.
    """
    head = [s for s in spans if bar.contains(pymupdf.Point(s.cx, s.cy)) and id(s) not in used]
    used.update(id(s) for s in head)
    body: list[Span] = []
    for s in spans:
        if s.baseline <= bar.y1 + 1 or id(s) in used:
            continue
        if cfg.is_code_block(s) or cfg.is_callout(s) or is_ws(s):
            body.append(s)
            used.add(id(s))
            continue
        break
    return head, body


def code_lines(spans: list[Span], warn, cfg: ListingConfig) -> list[CodeLine]:
    """Rebuild code lines from character positions on the fixed-pitch grid (cfg.char_width)."""
    rows: dict[tuple[int, float], dict[int, str]] = {}
    keys: list[tuple[int, float]] = []
    # grid origin: the text column, unless the block sits off that grid (e.g. padded inside a sidebar)
    bases: dict[int, float] = {}
    for pg in {s.page for s in spans}:
        xs = [c.x0 for s in spans if s.page == pg for c in s.chars if c.c.strip()]
        base = cfg.column_left(pg)
        if xs:
            off = (min(xs) - base) / cfg.char_width
            if abs(off - round(off)) > 0.3:
                base = min(xs)
        bases[pg] = base
    for s in spans:
        base = bases[s.page]
        for c in s.chars:
            if not c.c.strip():
                continue
            k = next((k for k in keys if k[0] == s.page and abs(k[1] - c.y) < 1.0), None)
            if k is None:
                k = (s.page, c.y)
                keys.append(k)
                rows[k] = {}
            pos = (c.x0 - base) / cfg.char_width
            col = max(0, round(pos))
            if abs(pos - col) > 0.3:
                warn(f"code off-grid p{s.page} col {pos:.2f} in {s.text.strip()[:30]!r}")
            if col in rows[k] and rows[k][col] != c.c:
                warn(f"code column clash p{s.page} col {col} {rows[k][col]!r}/{c.c!r} in {s.text.strip()[:30]!r}")
            rows[k][col] = c.c
    out = []
    for k in sorted(keys):
        row = rows[k]
        out.append(CodeLine("".join(row.get(i, " ") for i in range(max(row) + 1)).rstrip(), k[1], k[0],
                            bases[k[0]] + min(row) * cfg.char_width))
    return out


def with_blank_lines(lines: list[CodeLine], cfg: ListingConfig) -> tuple[list[str], list[int]]:
    """Insert blank lines from the line pitch. Returns (text lines, index of each CodeLine in them)."""
    out: list[str] = []
    pos: list[int] = []
    for i, l in enumerate(lines):
        if i > 0 and lines[i - 1].page == l.page:
            for _ in range(round((l.baseline - lines[i - 1].baseline) / cfg.line_pitch) - 1):
                out.append("")
        pos.append(len(out))
        out.append(l.text)
    return out, pos


def _blocks(callouts: list[Span]) -> list[list[Span]]:
    """Group callout spans into text blocks: vertically adjacent and left- or right-aligned."""
    blocks: list[list[Span]] = []
    for s in sorted((s for s in callouts if s.text.strip()), key=lambda s: (round(s.baseline, 1), s.x0)):
        for b in blocks:
            last = b[-1]
            if 0 < s.baseline - last.baseline < 12 and (abs(s.x0 - last.x0) < 2.5 or abs(s.x1 - last.x1) < 2.5
                                                        or (s.x0 < last.x1 and s.x1 > last.x0)):
                b.append(s)
                break
            if abs(s.baseline - last.baseline) < 1 and s.x0 - last.x1 < 6 and s.x0 > last.x0:
                b.append(s)  # same line, split span
                break
        else:
            blocks.append([s])
    return blocks


def _rect(spans: list[Span]) -> pymupdf.Rect:
    r = pymupdf.Rect(spans[0].x0, spans[0].y0, spans[0].x1, spans[0].y1)
    for s in spans[1:]:
        r |= (s.x0, s.y0, s.x1, s.y1)
    return r


def map_callouts(book: Book, pno: int, area: pymupdf.Rect, lines: list[CodeLine], line_index: list[int],
                 callout_spans: list[Span], warn, cfg: ListingConfig) -> list[Callout]:
    """Map each callout text block on one page part of a listing to code line numbers.

    lines/line_index: the code lines of this page part and their 0-based positions in the
    whole listing text (blank lines included).
    """
    blocks = _blocks(callout_spans)
    if not blocks:
        return []
    centres = [(l.baseline - cfg.line_centre, line_index[i] + 1) for i, l in enumerate(lines)]
    segs: list[pymupdf.Rect] = []
    heads: list[pymupdf.Rect] = []
    slants: list[pymupdf.Rect] = []
    for d in book.drawings(pno):
        r = d["rect"]
        if not r.intersects(area) or hexrgb(d.get("fill")) == cfg.bar_fill:
            continue
        col = hexrgb(d.get("fill")) or hexrgb(d.get("color"))
        if col != cfg.callout_ink:
            continue
        if d["type"] == "f" and 2.5 < r.width < 7 and 2.5 < r.height < 7 and len(d["items"]) >= 3:
            heads.append(r)
        elif r.width < 0.7 or r.height < 0.7:
            segs.append(r)
        elif r.width < 40 and r.height < 40:
            slants.append(r)  # slanted connector (jog)
    objs = [("s", r) for r in segs] + [("h", r) for r in heads] + [("j", r) for r in slants]
    parent = list(range(len(objs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(objs)):
        a = objs[i][1] + (-0.7, -0.7, 0.7, 0.7)
        for j in range(i + 1, len(objs)):
            if a.intersects(objs[j][1]):
                parent[find(i)] = find(j)
    comps: dict[int, list] = {}
    for i, o in enumerate(objs):
        comps.setdefault(find(i), []).append(o)

    results: dict[int, Callout] = {i: Callout(b) for i, b in enumerate(blocks)}
    brects = [_rect(b) for b in blocks]

    def nearest_line(y: float) -> tuple[int, float]:
        c, n = min(centres, key=lambda c: abs(c[0] - y))
        return n, abs(c - y)

    for comp in comps.values():
        cb = pymupdf.Rect(comp[0][1])
        for _, r in comp[1:]:
            cb |= r
        if cb.width < 2 and cb.height < 2:
            continue

        def dist(tb: pymupdf.Rect) -> float:
            dx = max(tb.x0 - cb.x1, cb.x0 - tb.x1, 0)
            dy = max(tb.y0 - cb.y1, cb.y0 - tb.y1, 0)
            return (dx * dx + dy * dy) ** 0.5

        cands = [i for i in range(len(blocks)) if dist(brects[i]) <= 12]
        bi = min(range(len(blocks)), key=lambda i: dist(brects[i]))
        hs0 = [r for k, r in comp if k == "h"]
        # which side of the graphic is the code on? compare with the first character of the code lines beside it
        beside = [l.x0 for l in lines if cb.y0 - 6 <= l.baseline - cfg.line_centre <= cb.y1 + 6] or [l.x0 for l in lines]
        code_on_left = cb.x1 > min(beside) + 1
        if not hs0 and len(cands) > 1:
            # brackets: the text sits beside the bar on the side away from the code
            vs0 = [r for k, r in comp if k == "s" and r.width < 0.7 and r.height > 3]
            if vs0:
                edge = max(r.x0 for r in vs0) if code_on_left else min(r.x0 for r in vs0)
                tr = pymupdf.Rect()
                for r in vs0:
                    if abs(r.x0 - edge) < 1.0:
                        tr |= r
                saved = cb
                cb = tr
                bi = min(cands, key=lambda i: dist(brects[i]))
                cb = saved
        if hs0 and len(cands) > 1:
            # several text blocks are close: the text belongs to the tail, the leader end farthest from the heads
            ends = []
            for k, r in comp:
                if k == "h":
                    continue
                if r.width >= r.height:
                    ends += [pymupdf.Point(r.x0, (r.y0 + r.y1) / 2), pymupdf.Point(r.x1, (r.y0 + r.y1) / 2)]
                else:
                    ends += [pymupdf.Point((r.x0 + r.x1) / 2, r.y0), pymupdf.Point((r.x0 + r.x1) / 2, r.y1)]
            hc = [pymupdf.Point((h.x0 + h.x1) / 2, (h.y0 + h.y1) / 2) for h in hs0]
            if ends:
                tail = max(ends, key=lambda pt: min(abs(pt - c) for c in hc))
                tr = pymupdf.Rect(tail, tail)
                saved = cb
                cb = tr
                bi = min(cands, key=lambda i: dist(brects[i]))
                cb = saved
        if dist(brects[bi]) > 12:
            warn(f"p{pno}: callout graphic at {tuple(round(v) for v in cb)} has no text block near it")
            continue
        tb = brects[bi]
        text_right = code_on_left
        co = results[bi]
        hs = [r for k, r in comp if k == "h"]
        if hs:
            co.kind = "arrow"
            for h in hs:
                tipy = (h.y0 + h.y1) / 2
                n, d = nearest_line(tipy)
                if d > 2.5:
                    warn(f"p{pno}: arrow tip y={tipy:.1f} is {d:.1f} pt from the nearest code line ({n})")
                co.lines.append(n)
        else:
            vs = [r for k, r in comp if k == "s" and r.width < 0.7 and r.height > 3]
            if not vs:
                warn(f"p{pno}: callout graphic without arrowhead or vertical bar near {co.spans[0].text.strip()[:30]!r}")
                continue
            co.kind = "bracket"
            xs = [r.x0 for r in vs]
            edge = min(xs) if text_right else max(xs)
            side = [r for r in vs if abs(r.x0 - edge) < 1.0]
            for r in side:
                got = [n for c, n in centres if r.y0 - 1.5 <= c <= r.y1 + 1.5]
                if not got:
                    warn(f"p{pno}: bracket bar y {r.y0:.1f}-{r.y1:.1f} covers no code line")
                co.lines.extend(got)
    out = []
    for co in results.values():
        co.lines = sorted(set(co.lines))
        if not co.lines:
            co.kind = "none"
            warn(f"p{pno}: callout {' '.join(s.text.strip() for s in co.spans)[:40]!r} has no graphic")
        out.append(co)
    return out
