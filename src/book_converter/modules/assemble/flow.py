"""Assemble a section's page content (regions and roled rows) into document blocks.

A single pass over each page's items in reading order, with open paragraph, list, note and sidebar
state: rows join the open block or close it and start the next one. Layout values come from the
profile's FlowConfig.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

import pymupdf

from ...core.model import (Block, Callout, Code, Figure, Footnote, Heading, ImageRef, Label, ListBlock, ListItem,
                           Note, Paragraph, Run, SectionDoc, Sidebar, SourceRef, Table)
from ...core.pdf import Book, Span, is_ws, near
from ...core.rows import Row, Text, runs_text
from ...core.sections import Section
from ..regions import figures as figmod
from ..regions.listings import code_lines, map_callouts, with_blank_lines
from ..regions.page import PageConfig, page_content
from ..serialize.markdown import render_runs


@dataclass(frozen=True)
class FlowConfig:
    """Layout values for joining rows into blocks (set by the profile). Right edges and positions are
    for even pages; odd pages sit odd_shift further right."""
    page: PageConfig
    column_left: Callable[[int], float]
    column_right: Callable[[int], float]
    odd_shift: float
    right_edge: dict[str, float]               # full-line right edge per context: "body", "sidebar", "note"
    right_slack: float                         # a line is full when it ends within this of the right edge
    sidebar_quote_indent: float                # deeper indents in a sidebar are quotes (narrower on the right too)
    listing_continue_top: float                # code above this on the next page continues a listing
    gap_sidebar: float                         # max baseline step inside a sidebar paragraph or item
    gap_list: float                            # max baseline step inside a body list item
    gap_note: float                            # max baseline step inside a note
    gap_para: float                            # max baseline step inside a body paragraph
    align_tol: float                           # left edges this close are aligned
    indent_min: float                          # a larger indent starts a new paragraph
    heading_merge_gap: float                   # a second heading row this close continues the heading
    smallcaps_lower_max: float                 # small-capital spans this size or smaller are lower case
    is_body_text: Callable[[Span], bool]       # body text (its most common left edge is the column)
    is_note_label: Callable[[Span], bool]
    is_bullet: Callable[[Span], bool]
    is_list_number: Callable[[Span], bool]
    is_code_block: Callable[[Span], bool]
    is_callout: Callable[[Span], bool]
    is_figure_text: Callable[[Span], bool]
    is_listing_head: Callable[[Span], bool]
    is_fn_start_marker: Callable[[Span], bool]
    listing_id_pattern: str                    # listing number in its title
    skip_pages: frozenset[int]
    page_figures: dict[int, tuple[str, int]]


class Assembler:
    def __init__(self, book: Book, cfg: FlowConfig, text: Text, descriptions: dict[str, str], warn):
        self.book = book
        self.cfg = cfg
        self.text = text
        self.desc = descriptions
        self.warn = warn
        self.figure_labels: dict[str, list[str]] = {}

    # ------------------------------------------------------------ helpers

    def src(self, spans: list[Span]) -> list[SourceRef]:
        """Where these spans are: one SourceRef per page, with the union of their boxes."""
        out: dict[int, SourceRef] = {}
        for s in spans:
            ref = out.get(s.page)
            if ref is None:
                ref = out[s.page] = SourceRef(s.page, (s.x0, s.y0, s.x1, s.y1))
            else:
                x0, y0, x1, y1 = ref.bbox
                ref.bbox = (min(x0, s.x0), min(y0, s.y0), max(x1, s.x1), max(y1, s.y1))
            ref.spans.append(self.book.span_id(s))
        return list(out.values())

    def rows_src(self, rows: list[Row]) -> list[SourceRef]:
        return self.src([s for r in rows for s in r.spans])

    def rel_left(self, page: int, x: float) -> float:
        return x - self.cfg.column_left(page)

    def item_text_lefts(self, item: list[Row]) -> list[float]:
        """Left edges a continuation line of this list item may align with: the item's text,
        and the text after the dash of any "–" sub-item row."""
        cfg = self.cfg
        tl = next(s for s in item[0].spans if not cfg.is_bullet(s) and not cfg.is_list_number(s) and not is_ws(s))
        lefts = [self.rel_left(tl.page, tl.x0)]
        for r in item:
            for s in r.spans:
                if s.text.lstrip().startswith("–"):
                    after = [c for c in s.chars if c.c.strip()][1:2]
                    if after:
                        lefts.append(self.rel_left(r.page, after[0].x0))
                    else:
                        nxt = [t for t in r.spans if t.x0 > s.x1 - 0.5 and not is_ws(t)]
                        if nxt:
                            lefts.append(self.rel_left(r.page, nxt[0].x0))
                    break
        return lefts

    def list_block(self, items: list[list[Row]]) -> ListBlock:
        out = []
        for rows in items:
            segs: list[list[Row]] = [[]]
            for r in rows:
                if re.match(r"^\s*–\s", runs_text(self.text.row_runs(r))):
                    segs.append([r])
                else:
                    segs[-1].append(r)
            main, *subs = segs
            num = next((s.text.strip() for s in main[0].spans if self.cfg.is_list_number(s)), None) if main else None
            item = ListItem(num, self.text.join_rows(main))
            for seg in subs:
                rs = self.text.join_rows(seg)
                first = next((x for x in rs if x.t.strip()), None)
                if first:
                    first.t = re.sub(r"^\s*–\s*", "", first.t)  # the dash is the sub-item marker
                item.subitems.append(rs)
            out.append(item)
        return ListBlock(out, src=self.rows_src([r for rows in items for r in rows]))

    def listing_image(self, pno: int, spans: list[Span], area: pymupdf.Rect, name: str) -> ImageRef:
        ink = [s for s in spans if not is_ws(s)]
        r = pymupdf.Rect(min([self.cfg.column_left(pno)] + [s.x0 for s in ink]), min(s.y0 for s in ink),
                         max([self.cfg.column_right(pno)] + [s.x1 for s in ink]), max(s.y1 for s in ink))
        for d in self.book.drawings(pno):
            if d["rect"].intersects(area):
                r |= d["rect"]
        r = (r + (-6, -4, 6, 5)) & self.book.page(pno).rect
        return ImageRef(name, "crop", pno, r)

    def callouts(self, pno: int, area: pymupdf.Rect, lines, pos: list[int], calls: list[Span]) -> list[Callout]:
        got = map_callouts(self.book, pno, area, lines, pos, calls, self.warn, self.cfg.page.listings)
        return [Callout(c.lines, self.text.join_rows(self.text.rows_of(c.spans), plain=True), c.top) for c in got]

    # ------------------------------------------------------------ section

    def section(self, sec: Section) -> SectionDoc:
        cfg, text = self.cfg, self.text
        lst_cfg = cfg.page.listings
        p0, p1 = sec.pages
        blocks: list[Block] = []
        listing_state: dict[int, list[int]] = {}   # id(Code) -> [last page, page parts so far]
        if sec.title:
            blocks.append(Heading(1, [Run(sec.title, "")]))
        st: dict = {"para": None, "pending": [], "list": None, "sidebar": None, "note": None, "dropcap": "",
                    "last_head_top": None}

        def full_right(r: Row, ctx: str) -> bool:
            off = cfg.odd_shift if r.page % 2 == 1 else 0.0
            right = cfg.right_edge[ctx]
            if ctx == "sidebar":
                # quote blocks inside sidebars are indented on both sides
                right -= max(0.0, self.rel_left(r.page, r.left) - cfg.sidebar_quote_indent)
            return r.right >= right + off - cfg.right_slack

        def push(b: Block) -> None:
            blocks.append(b)

        def flush_para() -> None:
            if st["para"]:
                push(Paragraph(text.join_rows(st["para"]), st["dropcap"], src=self.rows_src(st["para"])))
                st["dropcap"] = ""
                st["para"] = None
            for b in st["pending"]:
                push(b)
            st["pending"] = []

        def flush_list() -> None:
            if st["list"]:
                push(self.list_block(st["list"]["items"]))
                st["list"] = None

        def sidebar_flush_inner() -> None:
            sb = st["sidebar"]
            if not sb:
                return
            if sb["para"]:
                sb["parts"].append(Paragraph(text.join_rows(sb["para"]), src=self.rows_src(sb["para"])))
                sb["para"] = None
            if sb["list"]:
                sb["parts"].append(self.list_block(sb["list"]))
                sb["list"] = None

        def open_sidebar(block: Sidebar | Note) -> dict:
            parts = block.children if isinstance(block, Sidebar) else block.continued
            return {"block": block, "parts": parts, "para": None, "list": None}

        def close_sidebar() -> None:
            sb = st["sidebar"]
            if not sb:
                return
            sidebar_flush_inner()
            st["sidebar"] = None
            (st["pending"] if st["para"] else blocks).append(sb["block"])

        def close_note() -> None:
            n = st["note"]
            if not n:
                return
            st["note"] = None
            (st["pending"] if st["para"] else blocks).append(
                Note(n["label"], text.join_rows(n["rows"]), src=self.rows_src(n["rows"])))

        def close_all() -> None:
            close_sidebar()
            close_note()
            flush_list()
            flush_para()

        for p in range(p0, p1 + 1):
            if p in cfg.skip_pages:
                continue
            content = page_content(self.book, p, cfg.page, text, cfg.page_figures.get(p), self.warn)
            pf, sidebar_boxes = content.spans, content.sidebar_boxes

            for top, kind, obj in content.items:
                if kind == "fig":
                    fig: figmod.Figure = obj
                    key = fig.id if not fig.caption else f"figure-{fig.id}"
                    image = ImageRef(f"{key}.png", "figure", p, fig.box, fig.rotate)
                    fsrc = [SourceRef(p, tuple(fig.box), [self.book.span_id(s) for s in fig.labels + fig.caption])]
                    self.figure_labels[fig.id] = fig.label_texts
                    desc = self.desc.get(key)
                    if not desc:
                        self.warn(f"no description for {key}")
                    if fig.caption:
                        b = Figure(fig.id, image, f"Figure {fig.id}",
                                   text.join_rows(text.rows_of(fig.caption), plain=True), desc or None, src=fsrc)
                    else:
                        b = Figure(fig.id, image, fig.id.replace('-', ' ').capitalize(), None, desc or None,
                                   src=fsrc)
                    if st["para"] or st["sidebar"]:
                        st["pending"].append(b)
                    else:
                        close_all()
                        push(b)
                    continue
                if kind == "table":
                    close_all()
                    tab = obj
                    cell = lambda spans: text.join_rows(text.rows_of(spans)) if spans else []  # noqa: E731
                    caption = cell(tab.caption) if tab.caption else None
                    header = [cell(c) for c in tab.header]
                    push(Table(caption, header, [[cell(c) for c in r] for r in tab.rows], src=self.src(tab.spans)))
                    continue
                if kind == "listing":
                    close_all()
                    bar, head, body = obj
                    title = re.sub(r"\s+", " ", " ".join(runs_text(text.row_runs(r, plain=True))
                                                         for r in text.rows_of(head))).strip()
                    m = re.match(cfg.listing_id_pattern, title)
                    lid = m.group(1) if m else "?"
                    code = [s for s in body if cfg.is_code_block(s)]
                    lines = code_lines(code, self.warn, lst_cfg)
                    ltext, pos = with_blank_lines(lines, lst_cfg)
                    lst = Code(ltext, title, lid, src=self.src(head + body))
                    listing_state[id(lst)] = [p, 1]
                    calls = [s for s in body if cfg.is_callout(s)]
                    if calls:
                        area = pymupdf.Rect(0, bar.y0, self.book.page(p).rect.x1,
                                            max(s.y1 for s in body if not is_ws(s)) + 3)
                        lst.callouts = self.callouts(p, area, lines, pos, calls)
                        lst.images.append(self.listing_image(p, head + body, area, f"listing-{lid}.png"))
                    push(lst)
                    continue
                if kind == "code":
                    ink, calls = obj
                    lines = code_lines(ink, self.warn, lst_cfg)
                    prev = blocks[-1] if blocks else None
                    state = listing_state.get(id(prev)) if isinstance(prev, Code) else None
                    if (not st["para"] and not st["list"] and not st["sidebar"] and not st["note"]
                            and top < cfg.listing_continue_top and state and state[0] == p - 1):
                        sep = 1 if prev.lines and prev.lines[-1].strip() == "}" and lines[0].text.strip() != "}" else 0
                        base = len(prev.lines) + sep
                        ctext, pos = with_blank_lines(lines, lst_cfg)
                        prev.lines.extend([""] * sep + ctext)
                        prev.src += self.src(ink + calls)
                        state[0] = p
                        state[1] += 1
                        self.warn(f"listing {prev.lid} continued across pages {p - 1}-{p}")
                        if calls:
                            area = pymupdf.Rect(0, min(s.y0 for s in ink) - 6, self.book.page(p).rect.x1,
                                                max(s.y1 for s in ink + calls) + 3)
                            prev.callouts += self.callouts(p, area, lines, [base + i for i in pos], calls)
                            prev.images.append(self.listing_image(p, ink + calls, area,
                                                                  f"listing-{prev.lid}-{state[1]}.png"))
                    elif st["sidebar"] and not calls and any(b.contains(pymupdf.Point(ink[0].x0 + 1, ink[0].cy))
                                                             for b in sidebar_boxes):
                        sidebar_flush_inner()
                        st["sidebar"]["parts"].append(Code(with_blank_lines(lines, lst_cfg)[0], src=self.src(ink)))
                    else:
                        close_all()
                        if calls:
                            ctext, pos = with_blank_lines(lines, lst_cfg)
                            label = self.book.page_label(p)
                            lst = Code(ctext, "", f"p{label}", src=self.src(ink + calls))
                            listing_state[id(lst)] = [p, 1]
                            area = pymupdf.Rect(0, min(s.y0 for s in ink + calls) - 3, self.book.page(p).rect.x1,
                                                max(s.y1 for s in ink + calls) + 3)
                            lst.callouts = self.callouts(p, area, lines, pos, calls)
                            n = sum(1 for b in blocks if isinstance(b, Code) and b.lid.startswith(f"p{label}"))
                            if n:
                                lst.lid = f"p{label}-{n + 1}"
                            lst.images.append(self.listing_image(p, ink + calls, area, f"code-{lst.lid}.png"))
                            push(lst)
                        else:
                            push(Code(with_blank_lines(lines, lst_cfg)[0], src=self.src(ink)))
                    continue

                r: Row = obj
                cls = r.cls
                if cls in ("header", "pagenum", "blank", "caption", "footnote", "footnote-cont"):
                    if cls == "caption":
                        self.warn(f"stray caption row p{p}: {runs_text(text.row_runs(r))[:60]!r}")
                    continue
                if cls == "map-title":
                    close_all()
                    push(Heading(2, [Run(runs_text(text.row_runs(r, plain=True)).strip(), "")], src=self.rows_src([r])))
                    continue
                if cls == "title":
                    t = runs_text(text.row_runs(r, plain=True)).strip()
                    if sec.heading_shift:
                        close_all()
                        push(Heading(1 + sec.heading_shift, [Run(t, "")], src=self.rows_src([r])))
                    elif not sec.title:
                        if len(blocks) == 1 and isinstance(blocks[0], Heading) and blocks[0].level == 1:
                            blocks[0].inlines.append(Run(f": {t}", ""))
                            blocks[0].src += self.rows_src([r])
                        else:
                            push(Heading(1, [Run(t, "")], src=self.rows_src([r])))
                    continue
                if cls == "dropcap":
                    close_all()
                    st["dropcap"] = runs_text(text.row_runs(r, plain=True)).strip()
                    continue
                if cls in ("h2", "h3"):
                    close_all()
                    t = text.row_runs(r, plain=True)
                    lvl = (2 if cls == "h2" else 3) + sec.heading_shift
                    prev = blocks[-1] if blocks else None
                    t_md = render_runs(t)
                    if (isinstance(prev, Heading) and prev.level == lvl and not re.match(r"^\d", t_md)
                            and st["last_head_top"] is not None
                            and 0 < r.top - st["last_head_top"] < cfg.heading_merge_gap):
                        hm = re.search(r"([A-Za-z]+)-$", render_runs(prev.inlines))
                        tm = re.match(r"([A-Za-z]+)", t_md)
                        if hm and tm:
                            keep = text.resolver.resolve(hm.group(1), tm.group(1), f"p{r.page}")
                            if not keep:
                                last = next(x for x in reversed(prev.inlines) if x.t.strip())
                                last.t = last.t.rstrip()[:-1]
                            prev.inlines.extend(t)
                        else:
                            prev.inlines.extend([Run(" ", "")] + t)
                        prev.src += self.rows_src([r])
                    else:
                        push(Heading(lvl, t, src=self.rows_src([r])))
                    st["last_head_top"] = r.top
                    continue
                if cls == "smallcaps":
                    close_all()
                    s_ = ""
                    prev_s = None
                    for s in r.spans:
                        if prev_s and s.x0 - prev_s.x1 > 1.0 and not prev_s.text[-1:].isspace() \
                                and not s.text[:1].isspace():
                            s_ += " "
                        s_ += s.text.lower() if s.size <= cfg.smallcaps_lower_max else s.text
                        prev_s = s
                    push(Heading(4, [Run(re.sub(r"\s+", " ", s_).strip(), "")], src=self.rows_src([r])))
                    continue
                if cls == "covers-title":
                    close_all()
                    push(Label([Run(runs_text(text.row_runs(r, plain=True)).strip(), "")], src=self.rows_src([r])))
                    st["list"] = {"items": [], "ctx": "covers"}
                    continue
                if cls == "covers-item":
                    if not st["list"]:
                        st["list"] = {"items": [], "ctx": "covers"}
                    st["list"]["items"].append([r])
                    continue
                if cls == "covers-cont":
                    if st["list"] and st["list"]["items"]:
                        st["list"]["items"][-1].append(r)
                    else:
                        self.warn(f"covers text without item p{p}")
                    continue
                if cls == "sidebar-title":
                    close_note()
                    close_sidebar()
                    st["sidebar"] = open_sidebar(Sidebar(text.row_runs(r, plain=True), src=self.rows_src([r])))
                    continue
                if cls == "continued":
                    if st["sidebar"]:
                        continue
                    src = st["pending"] if st["pending"] and isinstance(st["pending"][-1], (Sidebar, Note)) \
                        else blocks
                    prev_b = src[-1] if src else None
                    if isinstance(prev_b, (Sidebar, Note)):
                        src.pop()
                        st["sidebar"] = open_sidebar(prev_b)
                    else:
                        self.warn(f"(continued) without a sidebar p{p}")
                    continue
                if cls == "sidebar-text":
                    sb = st["sidebar"]
                    if not sb:
                        self.warn(f"sidebar text without sidebar p{p}: {runs_text(text.row_runs(r))[:40]!r}")
                        sb = st["sidebar"] = open_sidebar(Sidebar([]))
                    if any(cfg.is_bullet(s) or cfg.is_list_number(s) for s in r.spans):
                        if sb["para"]:
                            sidebar_flush_inner()
                        sb["list"] = sb["list"] or []
                        sb["list"].append([r])
                        continue
                    if sb["list"]:
                        last = sb["list"][-1]
                        lr = last[-1]
                        if any(abs(self.rel_left(r.page, r.left) - x) < cfg.align_tol
                               for x in self.item_text_lefts(last)) and \
                                (r.page != lr.page or r.top - lr.top < cfg.gap_sidebar):
                            last.append(r)
                            continue
                        sidebar_flush_inner()
                    pr = sb["para"][-1] if sb["para"] else None
                    if pr and ((not full_right(pr, "sidebar")) if pr.page != r.page
                               else (r.top - pr.top > cfg.gap_sidebar or not full_right(pr, "sidebar")
                                     or abs(self.rel_left(r.page, r.left) - self.rel_left(pr.page, pr.left))
                                     > cfg.align_tol)):
                        sidebar_flush_inner()
                    sb["para"] = (sb["para"] or []) + [r]
                    continue
                if cls == "note-start":
                    close_note()
                    lab = next(s for s in r.spans if cfg.is_note_label(s))
                    st["note"] = {"label": lab.text.strip(), "rows": [r], "left": lab.x0}
                    continue
                if cls == "bullet":
                    if st["note"]:
                        close_note()
                    if st["para"]:
                        flush_para()
                    if not st["list"] or st["list"]["ctx"] != "body":
                        flush_list()
                        st["list"] = {"items": [], "ctx": "body"}
                    st["list"]["items"].append([r])
                    continue
                if cls in ("body", "epigraph"):
                    if st["note"]:
                        n = st["note"]
                        lr = n["rows"][-1]
                        if r.page == lr.page and r.top - lr.top < cfg.gap_note and abs(r.left - n["left"]) < cfg.align_tol:
                            n["rows"].append(r)
                            continue
                        close_note()
                    if st["list"] and st["list"]["ctx"] == "body":
                        last = st["list"]["items"][-1]
                        lr = last[-1]
                        if any(abs(self.rel_left(r.page, r.left) - x) < cfg.align_tol
                               for x in self.item_text_lefts(last)) and \
                                (r.page != lr.page or r.top - lr.top < cfg.gap_list):
                            last.append(r)
                            continue
                        flush_list()
                    if st["list"]:
                        flush_list()
                    if st["sidebar"]:
                        close_sidebar()
                    lefts = Counter(round(s.x0, 1) for s in pf if cfg.is_body_text(s))
                    col = lefts.most_common(1)[0][0] if lefts else cfg.column_left(p)
                    indented = r.left - col > cfg.indent_min
                    if st["para"]:
                        pr = st["para"][-1]
                        cont = (not indented and full_right(pr, "body")
                                and (pr.page != r.page or r.top - pr.top < cfg.gap_para or st["pending"]))
                        if cont:
                            st["para"].append(r)
                            continue
                        flush_para()
                    st["para"] = [r]
                    continue
                self.warn(f"unclassified row p{p} [{r.spans[0].font}/{r.spans[0].size}/{r.spans[0].color}]: "
                          f"{runs_text(text.row_runs(r))[:60]!r}")
        close_all()
        return SectionDoc(sec, blocks, self.footnotes(sec))

    def footnotes(self, sec: Section) -> list[Footnote]:
        cfg, text = self.cfg, self.text
        p0, p1 = sec.pages
        spans = [s for p in range(p0, p1 + 1) if p not in cfg.skip_pages for s in self.book.spans(p)
                 if not cfg.is_figure_text(s) and not cfg.is_code_block(s) and not cfg.is_listing_head(s)
                 and not cfg.is_callout(s)]
        out: list[Footnote] = []
        cur: dict | None = None

        def emit() -> None:
            nonlocal cur
            if cur:
                rows = [Row(r.page, r.top, [s for s in r.spans if not cfg.is_fn_start_marker(s)], r.left, r.right,
                            r.cls) for r in cur["rows"]]
                out.append(Footnote(cur["num"], text.join_rows(rows), src=self.rows_src(cur["rows"])))
            cur = None

        for r in text.rows_of(spans):
            if r.cls == "footnote":
                emit()
                cur = {"num": next(s for s in r.spans if cfg.is_fn_start_marker(s)).text.strip(), "rows": [r]}
            elif r.cls == "footnote-cont" and cur and cur["rows"][0].page == r.page:
                cur["rows"].append(r)
            elif cur and r.page != cur["rows"][0].page:
                emit()
        emit()
        return out
