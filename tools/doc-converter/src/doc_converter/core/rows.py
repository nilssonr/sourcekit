"""Rows (spans on one baseline), inline runs, and joining rows into running text.

Joining resolves line-end hyphens with the Resolver; every decision is logged there.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from .hyphens import LETTER, Resolver
from .model import Run
from .pdf import Span, is_ws


@dataclass(eq=False)
class Row:
    page: int
    top: float  # baseline (drop-cap rows: glyph top)
    spans: list[Span]
    left: float = 0.0
    right: float = 0.0
    cls: str = ""  # role, from the profile's role function


@dataclass(frozen=True)
class RowConfig:
    """How spans become rows and runs (set by the profile)."""

    role: Callable[[Row], str]  # row -> role name
    is_fn_marker: Callable[[Span], bool]  # superscript footnote reference
    is_bullet: Callable[[Span], bool]  # bullet glyph (not text)
    is_list_number: Callable[[Span], bool]  # list item number (not text)
    is_inline_label: Callable[[Span], bool]  # label spans left out of the text (note labels)
    is_code_font: Callable[[Span], bool]  # code text -> "code" runs
    row_tol: float  # baselines this close form one row
    dropcap_size: float  # spans this size or larger are drop caps (own row, top = glyph top)
    fn_attach: tuple[float, float]  # a footnote marker joins the row whose baseline is this far below it


def _ink_right(s: Span) -> float:
    for c in reversed(s.chars):
        if c.c.strip():
            return c.x1
    return s.x0


def runs_text(rs: list[Run]) -> str:
    return "".join(r.t for r in rs)


class Text:
    """Row building and joining for one conversion (one RowConfig, one hyphen Resolver)."""

    def __init__(self, cfg: RowConfig, resolver: Resolver):
        self.cfg = cfg
        self.resolver = resolver

    def rows_of(self, spans: list[Span]) -> list[Row]:
        cfg = self.cfg
        rows: list[Row] = []
        normal = [s for s in spans if not cfg.is_fn_marker(s) and s.size < cfg.dropcap_size]
        for s in sorted(normal, key=lambda s: (s.page, s.baseline, s.x0)):
            r = next((r for r in rows if r.page == s.page and abs(r.top - s.baseline) <= cfg.row_tol), None)
            if r:
                r.spans.append(s)
            else:
                rows.append(Row(s.page, s.baseline, [s]))
        lo, hi = cfg.fn_attach
        for s in spans:
            if s.size >= cfg.dropcap_size:
                rows.append(Row(s.page, s.y0, [s]))
            elif cfg.is_fn_marker(s):
                cand = [r for r in rows if r.page == s.page and lo <= r.top - s.baseline <= hi]
                if cand:
                    min(cand, key=lambda r: abs(r.top - s.baseline)).spans.append(s)
                else:
                    rows.append(Row(s.page, s.baseline, [s]))
        for r in rows:
            r.spans.sort(key=lambda s: s.x0)
            ink = [s for s in r.spans if not is_ws(s) and not cfg.is_bullet(s) and not cfg.is_list_number(s)] or r.spans
            r.left = min(s.x0 for s in ink)
            r.right = max(_ink_right(s) for s in ink)
            r.cls = cfg.role(r)
        return sorted(rows, key=lambda r: (r.page, r.top))

    def span_run(self, s: Span) -> Run:
        if self.cfg.is_code_font(s):
            return Run(s.text, "code")
        if self.cfg.is_fn_marker(s):
            return Run(s.text.strip(), "fn")
        i = s.italic
        b = "Bold" in s.font
        return Run(s.text, "bi" if i and b else "i" if i else "b" if b else "")

    def row_runs(self, r: Row, plain: bool = False) -> list[Run]:
        """The row's text as runs; plain drops emphasis (code and footnote markers stay)."""
        cfg = self.cfg
        out: list[Run] = []
        prev: Span | None = None
        for s in r.spans:
            if cfg.is_bullet(s) or cfg.is_list_number(s) or cfg.is_inline_label(s):
                continue
            if prev and s.x0 - prev.x1 > 1.0 and not prev.text[-1:].isspace() and not s.text[:1].isspace():
                out.append(Run(" ", ""))
            run = self.span_run(s)
            out.append(Run(run.t, "") if plain and run.s not in ("code", "fn") else run)
            prev = s
        return out

    def join_rows(self, rows: list[Row], plain: bool = False) -> list[Run]:
        """Rows as one run of text: line-end hyphens resolved, a space between lines otherwise."""
        out: list[Run] = []
        for idx, r in enumerate(rows):
            rs = self.row_runs(r, plain)
            if idx > 0:
                while out and not out[-1].t.strip() and out[-1].s != "fn":
                    out.pop()
                last = out[-1] if out else None
                first_text = runs_text(rs).lstrip()
                m = re.search(rf"({LETTER}+)-\s*$", last.t) if last and last.s != "code" else None
                n = re.match(rf"({LETTER}+)", first_text)
                fc = next((x for x in rs if x.t.strip()), None)
                cm = re.search(rf"({LETTER}+)-\s*$", last.t) if last and last.s == "code" else None
                if cm and fc and fc.s == "code" and re.match(rf"{LETTER}", fc.t.lstrip()):
                    if self.resolver.resolve_code(
                        cm.group(1), re.match(rf"{LETTER}+", fc.t.lstrip()).group(0), f"p{r.page}"
                    ):
                        last.t = last.t.rstrip()
                    else:
                        last.t = last.t.rstrip()[:-1]
                    fc.t = fc.t.lstrip()
                elif m and n:
                    last.t = last.t.rstrip()
                    chain = re.search(rf"{LETTER}-{LETTER}+-\s*$", last.t) is not None
                    if not self.resolver.resolve(m.group(1), n.group(1), f"p{r.page}", chain):
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
