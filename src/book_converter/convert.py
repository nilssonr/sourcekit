"""Convert page ranges of the book PDF into verbatim Markdown.

Usage: uv run convert <pdf> <outDir> <section-id>...   (section ids in sections.py)
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from . import figures as figmod
from . import tables as tabmod
from .hyphens import Resolver
from .listings import (Callout, CodeLine, code_lines, fmt_lines, listing_bars, listing_parts, map_callouts,
                       with_blank_lines)
from .pdf import Book, Span
from .sections import SECTIONS, SKIP_PAGES, Section
from .styles import (BLUE, HEADER_BAND, column_left, column_right, is_bullet, is_callout, is_caption_font,
                     is_code_block, is_figure_text, is_fn_marker, is_fn_start_marker, is_list_number,
                     is_listing_head, is_ws, near)

REPO = Path(__file__).resolve().parents[2]

ROW_TOL = 2.7


# ---------------------------------------------------------------- rows


@dataclass(eq=False)
class Row:
    page: int
    top: float           # baseline (dropcap rows: glyph top)
    spans: list[Span]
    left: float = 0.0
    right: float = 0.0
    cls: str = ""


def _ink_right(s: Span) -> float:
    for c in reversed(s.chars):
        if c.c.strip():
            return c.x1
    return s.x0


def rows_of(spans: list[Span]) -> list[Row]:
    rows: list[Row] = []
    normal = [s for s in spans if not is_fn_marker(s) and s.size < 40]
    for s in sorted(normal, key=lambda s: (s.page, s.baseline, s.x0)):
        r = next((r for r in rows if r.page == s.page and abs(r.top - s.baseline) <= ROW_TOL), None)
        if r:
            r.spans.append(s)
        else:
            rows.append(Row(s.page, s.baseline, [s]))
    for s in spans:
        if s.size >= 40:
            rows.append(Row(s.page, s.y0, [s]))
        elif is_fn_marker(s):
            cand = [r for r in rows if r.page == s.page and -1.0 <= r.top - s.baseline <= 5.0]
            if cand:
                min(cand, key=lambda r: abs(r.top - s.baseline)).spans.append(s)
            else:
                rows.append(Row(s.page, s.baseline, [s]))
    for r in rows:
        r.spans.sort(key=lambda s: s.x0)
        ink = [s for s in r.spans if not is_ws(s) and not is_bullet(s) and not is_list_number(s)] or r.spans
        r.left = min(s.x0 for s in ink)
        r.right = max(_ink_right(s) for s in ink)
        r.cls = classify(r)
    return sorted(rows, key=lambda r: (r.page, r.top))


def classify(r: Row) -> str:
    al = [s for s in r.spans if not is_ws(s)]
    if not al:
        return "blank"
    k = al[0]
    f = k.font
    if r.top < HEADER_BAND:
        return "header"
    if all(s.font.startswith("NewBaskerville-Bold") and s.color == "#000000"
           and re.fullmatch(r"\s*([0-9]+|[ivxlc]+)\s*", s.text) for s in al):
        return "pagenum"
    if any(s.font == "FranklinGothic-Demi" and near(s.size, 8.46) and s.color == BLUE
           and s.text.strip() in ("DEFINITION", "TIP", "NOTE", "IMPORTANT", "WARNING") for s in al):
        return "note-start"
    if is_bullet(k) or is_list_number(k):
        if k.color == BLUE:
            return "covers-item"
        txt = next((s for s in al if not is_bullet(s) and not is_list_number(s)), None)
        return "sidebar-text" if txt and txt.font.startswith("FranklinGothic-Book") else "bullet"
    if is_fn_start_marker(k):
        return "footnote"
    if f.startswith("NewBaskerville") and k.size >= 40 and k.color == BLUE:
        return "dropcap"
    if f.startswith("NewBaskerville") and near(k.size, 30) and k.color == BLUE:
        return "title"
    if f == "FranklinGothic-DemiItal" and k.color == BLUE:
        if near(k.size, 12.48):
            return "h2"
        if near(k.size, 10.5):
            return "h3"
        if near(k.size, 12.0):
            return "covers-title"
        if k.text.strip() == "(continued)":
            return "continued"
    if f.startswith("FranklinGothic-Book") and k.color == BLUE:
        return "covers-cont"
    if f == "FranklinGothic-Demi" and k.color == BLUE and (near(k.size, 7.56) or near(k.size, 9.48)) \
            and all(re.fullmatch(r"[A-Z’'\- ]+", s.text) for s in al):
        return "smallcaps"
    if f == "FranklinGothic-Demi" and k.color == BLUE and near(k.size, 10.5):
        return "sidebar-title"
    if f.startswith("FranklinGothic-Book"):
        return "sidebar-text"
    if is_caption_font(k):
        return "caption"
    if f.startswith("NewBaskerville") and near(k.size, 8.46):
        return "footnote-cont"
    if f.startswith("NewBaskerville") and near(k.size, 9.48):
        return "epigraph"
    if f.startswith("NewBaskerville") or (f.startswith("Courier") and near(k.size, 9.03)):
        return "body"
    return "unknown"


# ---------------------------------------------------------------- runs


@dataclass
class Run:
    t: str
    s: str  # "", "i", "b", "bi", "code", "fn"


def span_run(s: Span) -> Run:
    if s.font.startswith("Courier"):
        return Run(s.text, "code")
    if is_fn_marker(s):
        return Run(s.text.strip(), "fn")
    i = s.italic
    b = "Bold" in s.font
    return Run(s.text, "bi" if i and b else "i" if i else "b" if b else "")


def is_note_label(s: Span) -> bool:
    return s.font == "FranklinGothic-Demi" and near(s.size, 8.46) and s.color == BLUE


def row_runs(r: Row, plain: bool = False) -> list[Run]:
    out: list[Run] = []
    prev: Span | None = None
    for s in r.spans:
        if is_bullet(s) or is_list_number(s) or is_note_label(s):
            continue
        if prev and s.x0 - prev.x1 > 1.0 and not prev.text[-1:].isspace() and not s.text[:1].isspace():
            out.append(Run(" ", ""))
        run = span_run(s)
        out.append(Run(run.t, "") if plain and run.s not in ("code", "fn") else run)
        prev = s
    return out


def runs_text(rs: list[Run]) -> str:
    return "".join(r.t for r in rs)


class Ctx:
    def __init__(self, resolver: Resolver):
        self.resolver = resolver
        self.warnings: list[str] = []

    def warn(self, s: str) -> None:
        self.warnings.append(s)


CTX: Ctx  # set in main


def join_rows(rows: list[Row], plain: bool = False) -> list[Run]:
    out: list[Run] = []
    for idx, r in enumerate(rows):
        rs = row_runs(r, plain)
        if idx > 0:
            while out and not out[-1].t.strip() and out[-1].s != "fn":
                out.pop()
            last = out[-1] if out else None
            first_text = runs_text(rs).lstrip()
            m = re.search(r"([A-Za-z]+)-\s*$", last.t) if last and last.s != "code" else None
            n = re.match(r"([A-Za-z]+)", first_text)
            if m and n:
                last.t = last.t.rstrip()
                if not CTX.resolver.resolve(m.group(1), n.group(1), f"p{r.page}"):
                    last.t = last.t[:-1]
                fr = next((x for x in rs if x.t.strip()), None)
                if fr:
                    fr.t = fr.t.lstrip()
            elif last and re.search(r"[—/]\s*$", last.t):
                last.t = last.t.rstrip()
                fr = next((x for x in rs if x.t.strip()), None)
                if fr:
                    fr.t = fr.t.lstrip()
            else:
                out.append(Run(" ", ""))
        out.extend(rs)
    return out


def esc(s: str) -> str:
    return re.sub(r"([\\`*_])", r"\\\1", s)


def render_runs(rs: list[Run]) -> str:
    merged: list[Run] = []
    for r in rs:
        if merged and merged[-1].s == r.s and r.s != "fn":
            merged[-1].t += r.t
        else:
            merged.append(Run(r.t, r.s))
    i = 1
    while i < len(merged) - 1:
        a, w, b = merged[i - 1], merged[i], merged[i + 1]
        if w.s == "" and not w.t.strip() and a.s == b.s and a.s and a.s not in ("code", "fn"):
            a.t += w.t + b.t
            del merged[i:i + 2]
            continue
        i += 1
    out = ""
    for r in merged:
        t = r.t if r.s == "code" else re.sub(r"\s+", " ", r.t)
        if r.s == "fn":
            out += f"[^{t}]"
            continue
        if not t.strip():
            out += t
            continue
        lead = re.match(r"^\s*", t).group(0)
        trail = re.search(r"\s*$", t).group(0)
        core = t.strip()
        if r.s == "code":
            tick = "``" if "`" in core else "`"
            out += f"{lead}{tick}{core}{tick}{trail}"
            continue
        mark = {"i": "*", "b": "**", "bi": "***"}.get(r.s, "")
        out += lead + mark + esc(core) + mark + trail
    out = re.sub(r" {2,}", " ", out).strip()
    out = re.sub(r"^(\d+)\. ", r"\1\\. ", out)
    return re.sub(r"^([-+#>])", r"\\\1", out)


def fence(lines: list[str]) -> str:
    return "```\n" + "\n".join(lines) + "\n```"


# ---------------------------------------------------------------- lists


def render_item(rows: list[Row]) -> str:
    segs: list[list[Row]] = [[]]
    for r in rows:
        if re.match(r"^\s*–\s", runs_text(row_runs(r))):
            segs.append([r])
        else:
            segs[-1].append(r)
    main, *subs = segs
    num = next((s.text.strip() for s in main[0].spans if is_list_number(s)), None) if main else None
    out = [(f"{num}. " if num else "- ") + render_runs(join_rows(main))]
    for s in subs:
        out.append("  - " + re.sub(r"^–\s*", "", render_runs(join_rows(s))))
    return "\n".join(out)


def render_list(items: list[list[Row]]) -> str:
    return "\n".join(render_item(i) for i in items)


def rel_left(page: int, x: float) -> float:
    return x - column_left(page)


# ---------------------------------------------------------------- listings


@dataclass
class ListingPart:
    page: int
    lines: list[CodeLine]
    callouts: list[Callout]
    image: str | None = None


@dataclass
class Listing:
    title: str
    lid: str
    text: list[str] = field(default_factory=list)
    callouts: list[Callout] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    last_page: int = 0

    def render(self) -> str:
        md = f"**{re.sub(r'^(Listing \d+\.\d+)\s*', r'\1 ', self.title)}**\n\n{fence(self.text)}"
        if self.callouts:
            items = []
            for c in sorted(self.callouts, key=lambda c: (min(c.lines) if c.lines else 0, c.top)):
                text = render_runs(join_rows(rows_of(c.spans), plain=True))
                items.append(f"- ({fmt_lines(c.lines)}) {text}")
            md += ("\n\n*Callouts (added in conversion; line numbers count every line of the listing, "
                   "blank lines included):*\n" + "\n".join(items))
        for img in self.images:
            md += f"\n\n![Listing {self.lid} with callouts](illustrations/{img})"
        return md


# ---------------------------------------------------------------- conversion


class Converter:
    def __init__(self, book: Book, out_dir: Path, descriptions: dict[str, str]):
        self.book = book
        self.out = out_dir
        self.desc = descriptions
        self.figure_labels: dict[str, list[str]] = {}

    def warn(self, s: str) -> None:
        CTX.warn(s)

    def _listing_image(self, pno: int, spans: list[Span], area: pymupdf.Rect, name: str) -> str:
        ink = [s for s in spans if not is_ws(s)]
        r = pymupdf.Rect(min([column_left(pno)] + [s.x0 for s in ink]), min(s.y0 for s in ink),
                         max([column_right(pno)] + [s.x1 for s in ink]), max(s.y1 for s in ink))
        for d in self.book.drawings(pno):
            if d["rect"].intersects(area):
                r |= d["rect"]
        r = (r + (-6, -4, 6, 5)) & self.book.page(pno).rect
        self.book.page(pno).get_pixmap(clip=r, dpi=figmod.DPI).save(self.out / "illustrations" / name)
        return name

    def convert(self, sec: Section) -> list[str]:
        p0, p1 = sec.pages
        blocks: list = []
        if sec.title:
            blocks.append(f"# {sec.title}")
        st = {"para": None, "pending": [], "list": None, "sidebar": None, "note": None, "dropcap": "",
              "last_head_top": None}

        def full_right(r: Row, ctx: str) -> bool:
            off = 9.0 if r.page % 2 == 1 else 0.0
            right = {"sidebar": 453.3, "note": 441.3}.get(ctx, 465.3)
            return r.right >= right + off - 9.3

        def push(md) -> None:
            blocks.append(md)

        def flush_para() -> None:
            if st["para"]:
                md = render_runs(join_rows(st["para"]))
                if st["dropcap"]:
                    md = st["dropcap"] + md
                    st["dropcap"] = ""
                push(md)
                st["para"] = None
            for p in st["pending"]:
                push(p)
            st["pending"] = []

        def flush_list() -> None:
            if st["list"]:
                push(render_list(st["list"]["items"]))
                st["list"] = None

        def sidebar_flush_inner() -> None:
            sb = st["sidebar"]
            if not sb:
                return
            if sb["para"]:
                sb["parts"].append(render_runs(join_rows(sb["para"])))
                sb["para"] = None
            if sb["list"]:
                sb["parts"].append(render_list(sb["list"]))
                sb["list"] = None

        def close_sidebar() -> None:
            sb = st["sidebar"]
            if not sb:
                return
            sidebar_flush_inner()
            parts = ([f"**{sb['title']}**"] if not sb.get("raw") else []) + sb["parts"]
            md = "\n>\n".join("\n".join(("> " + l) if not l.startswith(">") else l for l in p.split("\n"))
                              for p in parts)
            st["sidebar"] = None
            (st["pending"] if st["para"] else blocks).append(md)

        def close_note() -> None:
            n = st["note"]
            if not n:
                return
            md = f"> **{n['label']}** {render_runs(join_rows(n['rows']))}"
            st["note"] = None
            (st["pending"] if st["para"] else blocks).append(md)

        def close_all() -> None:
            close_sidebar()
            close_note()
            flush_list()
            flush_para()

        for p in range(p0, p1 + 1):
            if p in SKIP_PAGES:
                continue
            pf = self.book.spans(p)
            used: set[int] = set()
            items: list[tuple[float, str, object]] = []

            # figures
            for fig in figmod.detect(self.book, p, self.warn):
                for s in fig.labels + fig.caption:
                    used.add(id(s))
                items.append((fig.box.y0, "fig", fig))
            for s in pf:
                if is_figure_text(s) and id(s) not in used and s.text.strip():
                    self.warn(f"figure-font text outside any figure p{p}: {s.text.strip()!r}")
                if is_figure_text(s):
                    used.add(id(s))

            # tables
            for tab in tabmod.detect(self.book, p, self.warn):
                for s in tab.spans:
                    used.add(id(s))
                items.append((tab.top, "table", tab))

            # listings: header bar + following code/callout spans
            srt = sorted((s for s in pf if id(s) not in used), key=lambda s: (s.baseline, s.x0))
            for bar in listing_bars(self.book, p):
                head, body = listing_parts(srt, bar, used)
                items.append((bar.y0, "listing", (bar, head, body)))

            # code runs (snippets and listing continuations), with nearby callouts
            rest = [s for s in srt if id(s) not in used]
            runs: list[list[Span]] = []
            cur: list[Span] = []
            for s in rest:
                if is_code_block(s) or (cur and is_ws(s) and s.font.startswith("Courier")):
                    if cur and s.baseline - cur[-1].baseline > 23.3:
                        runs.append(cur)
                        cur = []
                    cur.append(s)
                    used.add(id(s))
                elif cur and s.baseline > cur[-1].baseline + 2.7 and not is_callout(s):
                    runs.append(cur)
                    cur = []
            if cur:
                runs.append(cur)
            for run in runs:
                ink = [s for s in run if not is_ws(s)]
                if not ink:
                    continue
                y0, y1 = min(s.y0 for s in ink) - 6, max(s.y1 for s in ink) + 6
                calls = [s for s in rest if is_callout(s) and id(s) not in used and y0 <= s.cy <= y1]
                for s in calls:
                    used.add(id(s))
                items.append((min(s.baseline for s in ink), "code", (ink, calls)))

            for r in rows_of([s for s in pf if id(s) not in used]):
                items.append((r.top, "row", r))
            items.sort(key=lambda it: it[0])

            for top, kind, obj in items:
                if kind == "fig":
                    fig: figmod.Figure = obj
                    name = f"figure-{fig.id}.png"
                    figmod.render(self.book, p, fig.box, is_figure_text, str(self.out / "illustrations" / name))
                    self.figure_labels[fig.id] = fig.label_texts
                    cap = render_runs(join_rows(rows_of(fig.caption), plain=True))
                    cap = re.sub(r"^Figure (\d+\.\d+)\s*", r"**Figure \1** ", cap)
                    desc = self.desc.get(f"figure-{fig.id}")
                    if not desc:
                        self.warn(f"no description for figure {fig.id}")
                    md = (f"![Figure {fig.id}](illustrations/{name})\n\n{cap}\n\n"
                          f"> **Illustration description (added in conversion, not book text):** {desc or 'TODO'}")
                    if st["para"] or st["sidebar"]:
                        st["pending"].append(md)
                    else:
                        close_all()
                        push(md)
                    continue
                if kind == "table":
                    close_all()
                    push(obj.markdown(lambda spans: render_runs(join_rows(rows_of(spans)))))
                    continue
                if kind == "listing":
                    close_all()
                    bar, head, body = obj
                    title = re.sub(r"\s+", " ", " ".join(runs_text(row_runs(r, plain=True))
                                                         for r in rows_of(head))).strip()
                    m = re.match(r"^Listing (\d+\.\d+)", title)
                    lid = m.group(1) if m else "?"
                    code = [s for s in body if is_code_block(s)]
                    lines = code_lines(code, self.warn)
                    text, pos = with_blank_lines(lines)
                    lst = Listing(title, lid, text, last_page=p)
                    calls = [s for s in body if is_callout(s)]
                    if calls:
                        area = pymupdf.Rect(0, bar.y0, self.book.page(p).rect.x1,
                                            max(s.y1 for s in body if not is_ws(s)) + 3)
                        lst.callouts = map_callouts(self.book, p, area, lines, pos, calls, self.warn)
                        lst.images.append(self._listing_image(p, head + body, area, f"listing-{lid}.png"))
                    push(lst)
                    continue
                if kind == "code":
                    ink, calls = obj
                    lines = code_lines(ink, self.warn)
                    prev = blocks[-1] if blocks else None
                    if (not st["para"] and not st["list"] and not st["sidebar"] and not st["note"]
                            and top < 70 and isinstance(prev, Listing) and prev.last_page == p - 1):
                        sep = 1 if prev.text and prev.text[-1].strip() == "}" and lines[0].text.strip() != "}" else 0
                        base = len(prev.text) + sep
                        text, pos = with_blank_lines(lines)
                        prev.text.extend([""] * sep + text)
                        prev.last_page = p
                        self.warn(f"listing {prev.lid} continued across pages {p - 1}-{p}")
                        if calls:
                            area = pymupdf.Rect(0, min(s.y0 for s in ink) - 6, self.book.page(p).rect.x1,
                                                max(s.y1 for s in ink + calls) + 3)
                            prev.callouts += map_callouts(self.book, p, area, lines, [base + i for i in pos],
                                                          calls, self.warn)
                            k = len(prev.images) + 1
                            prev.images.append(self._listing_image(p, ink + calls, area,
                                                                   f"listing-{prev.lid}-{k}.png"))
                    else:
                        if calls:
                            self.warn(f"p{p}: callouts next to a code block that is not a listing: "
                                      f"{' '.join(s.text.strip() for s in calls)[:50]!r}")
                        close_all()
                        push(fence(with_blank_lines(lines)[0]))
                    continue

                r: Row = obj
                cls = r.cls
                if cls in ("header", "pagenum", "blank", "caption", "footnote", "footnote-cont"):
                    if cls == "caption":
                        self.warn(f"stray caption row p{p}: {runs_text(row_runs(r))[:60]!r}")
                    continue
                if cls == "title":
                    t = runs_text(row_runs(r, plain=True)).strip()
                    if not sec.title:
                        if len(blocks) == 1 and isinstance(blocks[0], str) and blocks[0].startswith("# "):
                            blocks[0] += f": {t}"
                        else:
                            push(f"# {t}")
                    continue
                if cls == "dropcap":
                    close_all()
                    st["dropcap"] = runs_text(row_runs(r, plain=True)).strip()
                    continue
                if cls in ("h2", "h3"):
                    close_all()
                    t = render_runs(row_runs(r, plain=True))
                    lvl = "##" if cls == "h2" else "###"
                    prev = blocks[-1] if blocks else None
                    if (isinstance(prev, str) and prev.startswith(lvl + " ") and not re.match(r"^\d", t)
                            and st["last_head_top"] is not None and 0 < r.top - st["last_head_top"] < 18):
                        blocks[-1] += " " + t
                    else:
                        push(f"{lvl} {t}")
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
                        s_ += s.text.lower() if s.size <= 8.5 else s.text
                        prev_s = s
                    push("#### " + re.sub(r"\s+", " ", s_).strip())
                    continue
                if cls == "covers-title":
                    close_all()
                    push(f"**{runs_text(row_runs(r, plain=True)).strip()}**")
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
                    st["sidebar"] = {"title": render_runs(row_runs(r, plain=True)), "parts": [], "para": None,
                                     "list": None}
                    continue
                if cls == "continued":
                    if st["sidebar"]:
                        continue
                    src = st["pending"] if st["pending"] and str(st["pending"][-1]).startswith("> **") else blocks
                    prev_md = src[-1] if src else None
                    if isinstance(prev_md, str) and prev_md.startswith("> **"):
                        src.pop()
                        st["sidebar"] = {"title": "", "parts": [prev_md], "para": None, "list": None, "raw": True}
                    else:
                        self.warn(f"(continued) without a sidebar p{p}")
                    continue
                if cls == "sidebar-text":
                    sb = st["sidebar"]
                    if not sb:
                        self.warn(f"sidebar text without sidebar p{p}: {runs_text(row_runs(r))[:40]!r}")
                        sb = st["sidebar"] = {"title": "", "parts": [], "para": None, "list": None}
                    if any(is_bullet(s) or is_list_number(s) for s in r.spans):
                        if sb["para"]:
                            sidebar_flush_inner()
                        sb["list"] = sb["list"] or []
                        sb["list"].append([r])
                        continue
                    if sb["list"]:
                        last = sb["list"][-1]
                        lr = last[-1]
                        tl = next(s for s in last[0].spans if not is_bullet(s) and not is_list_number(s)
                                  and not is_ws(s))
                        if abs(rel_left(r.page, r.left) - rel_left(tl.page, tl.x0)) < 4 and \
                                (r.page != lr.page or r.top - lr.top < 14.7):
                            last.append(r)
                            continue
                        sidebar_flush_inner()
                    pr = sb["para"][-1] if sb["para"] else None
                    if pr and ((not full_right(pr, "sidebar")) if pr.page != r.page
                               else (r.top - pr.top > 14.7 or not full_right(pr, "sidebar"))):
                        sidebar_flush_inner()
                    sb["para"] = (sb["para"] or []) + [r]
                    continue
                if cls == "note-start":
                    close_note()
                    label = next(s for s in r.spans if is_note_label(s)).text.strip()
                    st["note"] = {"label": label, "rows": [r], "left": next(s for s in r.spans if is_note_label(s)).x0}
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
                        if r.page == lr.page and r.top - lr.top < 14.7 and abs(r.left - n["left"]) < 4:
                            n["rows"].append(r)
                            continue
                        close_note()
                    if st["list"] and st["list"]["ctx"] == "body":
                        last = st["list"]["items"][-1]
                        lr = last[-1]
                        tl = next(s for s in last[0].spans if not is_bullet(s) and not is_list_number(s)
                                  and not is_ws(s))
                        if abs(rel_left(r.page, r.left) - rel_left(tl.page, tl.x0)) < 4 and \
                                (r.page != lr.page or r.top - lr.top < 16):
                            last.append(r)
                            continue
                        flush_list()
                    if st["list"]:
                        flush_list()
                    if st["sidebar"]:
                        close_sidebar()
                    lefts = Counter(round(s.x0, 1) for s in pf
                                    if s.font.startswith("NewBaskerville") and near(s.size, 9.96))
                    col = lefts.most_common(1)[0][0] if lefts else column_left(p)
                    indented = r.left - col > 6.7
                    if st["para"]:
                        pr = st["para"][-1]
                        cont = (not indented and full_right(pr, "body")
                                and (pr.page != r.page or r.top - pr.top < 17.3 or st["pending"]))
                        if cont:
                            st["para"].append(r)
                            continue
                        flush_para()
                    st["para"] = [r]
                    continue
                self.warn(f"unclassified row p{p} [{r.spans[0].font}/{r.spans[0].size}/{r.spans[0].color}]: "
                          f"{runs_text(row_runs(r))[:60]!r}")
        close_all()
        out = [b.render() if isinstance(b, Listing) else b for b in blocks]
        return out + self.footnotes(sec)

    def footnotes(self, sec: Section) -> list[str]:
        p0, p1 = sec.pages
        spans = [s for p in range(p0, p1 + 1) if p not in SKIP_PAGES for s in self.book.spans(p)
                 if not is_figure_text(s) and not is_code_block(s) and not is_listing_head(s) and not is_callout(s)]
        out: list[str] = []
        cur: dict | None = None

        def emit() -> None:
            nonlocal cur
            if cur:
                rows = [Row(r.page, r.top, [s for s in r.spans if not is_fn_start_marker(s)], r.left, r.right, r.cls)
                        for r in cur["rows"]]
                out.append(f"[^{cur['num']}]: {render_runs(join_rows(rows))}")
            cur = None

        for r in rows_of(spans):
            if r.cls == "footnote":
                emit()
                cur = {"num": next(s for s in r.spans if is_fn_start_marker(s)).text.strip(), "rows": [r]}
            elif r.cls == "footnote-cont" and cur and cur["rows"][0].page == r.page:
                cur["rows"].append(r)
            elif cur and r.page != cur["rows"][0].page:
                emit()
        emit()
        return out


def main() -> None:
    global CTX
    pdf, out_dir, *wanted = sys.argv[1:]
    out = Path(out_dir)
    (out / "illustrations").mkdir(parents=True, exist_ok=True)
    book = Book(pdf)
    CTX = Ctx(Resolver(book.all_spans(16, 296)))
    dpath = REPO / "data" / "descriptions.json"
    desc = json.loads(dpath.read_text()) if dpath.exists() else {}
    conv = Converter(book, out, desc)
    for sid in wanted:
        sec = SECTIONS[sid]
        blocks = conv.convert(sec)
        (out / sec.file).write_text("\n\n".join(blocks) + "\n")
        print(f"wrote {sec.file} ({len(blocks)} blocks)")
    lpath = REPO / "data" / "figure-labels.json"
    labels = json.loads(lpath.read_text()) if lpath.exists() else {}
    labels.update(conv.figure_labels)
    lpath.write_text(json.dumps(dict(sorted(labels.items(), key=lambda kv: [int(x) for x in kv[0].split(".")])),
                                indent=1, ensure_ascii=False))
    (REPO / "data" / "hyphen-decisions.json").write_text(json.dumps(CTX.resolver.log_dicts(), indent=1))
    review = [d for d in CTX.resolver.log if re.search("REVIEW|AMBIGUOUS", d.why)]
    print(f"hyphens: {len(CTX.resolver.log)} resolved, {len(review)} flagged")
    for d in review:
        print(f"  {d.where} {d.a}-|{d.b} -> {'kept' if d.keep else 'joined'} ({d.why})")
    print(f"warnings: {len(CTX.warnings)}")
    for w in CTX.warnings:
        print("  " + w)


if __name__ == "__main__":
    main()
