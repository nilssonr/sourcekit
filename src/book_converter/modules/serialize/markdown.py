"""Markdown output for the document model.

Material the converter adds (figure descriptions, callout headers and line references, image links,
section title prefixes) is marked so the verifier can tell it from book text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ...core.model import (Block, Code, Figure, Footnote, Heading, Label, ListBlock, Note, Paragraph, Run, SectionDoc,
                           Sidebar, Table)


@dataclass(frozen=True)
class MarkdownConfig:
    """Book-specific caption formats: (pattern, replacement) applied to the rendered caption text."""
    figure_caption: tuple[str, str]     # e.g. (r"^Figure (\d+\.\d+)\s*", r"**Figure \1** ")
    table_caption: tuple[str, str]
    listing_title: tuple[str, str]      # applied to the plain listing title, which is set in bold


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


def fmt_lines(ns: list[int]) -> str:
    if not ns:
        return "(no line)"
    parts: list[str] = []
    start = prev = ns[0]
    for n in ns[1:] + [None]:
        if n is not None and n == prev + 1:
            prev = n
            continue
        parts.append(f"{start}" if start == prev else f"{start}–{prev}")
        if n is not None:
            start = prev = n
    word = "line" if len(ns) == 1 else "lines"
    return f"{word} {', '.join(parts)}"


def _quote(parts: list[str]) -> str:
    """Parts as one blockquote; lines that already start with ">" are kept as they are."""
    return "\n>\n".join("\n".join(("> " + l) if not l.startswith(">") else l for l in p.split("\n")) for p in parts)


class Markdown:
    def __init__(self, cfg: MarkdownConfig):
        self.cfg = cfg

    def section(self, doc: SectionDoc) -> str:
        out = [self.block(b) for b in doc.blocks] + [self.footnote(f) for f in doc.footnotes]
        return "\n\n".join(out) + "\n"

    def block(self, b: Block) -> str:
        match b:
            case Heading():
                return "#" * b.level + " " + render_runs(b.inlines)
            case Paragraph():
                return b.dropcap + render_runs(b.inlines)
            case Label():
                return f"**{render_runs(b.inlines)}**"
            case ListBlock():
                return self.list_block(b)
            case Code():
                return self.code(b)
            case Figure():
                return self.figure(b)
            case Table():
                return self.table(b)
            case Note():
                first = f"> **{b.label}** {render_runs(b.inlines)}"
                return _quote([first] + [self.block(c) for c in b.continued]) if b.continued else first
            case Sidebar():
                return _quote([f"**{render_runs(b.title)}**"] + [self.block(c) for c in b.children])
        raise TypeError(f"no Markdown for {type(b).__name__}")

    def list_block(self, b: ListBlock) -> str:
        lines = []
        for it in b.items:
            item = [(f"{it.number}. " if it.number else "- ") + render_runs(it.inlines)]
            item += ["  - " + render_runs(s) for s in it.subitems]
            lines.append("\n".join(item))
        return "\n".join(lines)

    def code(self, b: Code) -> str:
        md = fence(b.lines)
        if b.title:
            pat, rep = self.cfg.listing_title
            md = f"**{re.sub(pat, rep, b.title)}**\n\n" + md
        if b.callouts:
            items = [f"- ({fmt_lines(c.lines)}) {render_runs(c.inlines)}"
                     for c in sorted(b.callouts, key=lambda c: (min(c.lines) if c.lines else 0, c.top))]
            md += ("\n\n*Callouts (added in conversion; line numbers count every line of the listing, "
                   "blank lines included):*\n" + "\n".join(items))
        for img in b.images:
            alt = f"Listing {b.lid}" if b.title else f"Code on book page {b.lid[1:]}"
            md += f"\n\n![{alt} with callouts](illustrations/{img.file})"
        return md

    def figure(self, b: Figure) -> str:
        note = f"> **Illustration description (added in conversion, not book text):** {b.description or 'TODO'}"
        img = f"![{b.alt}](illustrations/{b.image.file})"
        if b.caption is None:
            return f"{img}\n\n{note}"
        pat, rep = self.cfg.figure_caption
        cap = re.sub(pat, rep, render_runs(b.caption))
        return f"{img}\n\n{cap}\n\n{note}"

    def table(self, b: Table) -> str:
        def cell(rs: list[Run]) -> str:
            return render_runs(rs).replace("|", "\\|")
        pat, rep = self.cfg.table_caption
        cap = re.sub(pat, rep, render_runs(b.caption)) if b.caption else ""
        lines = ["| " + " | ".join(cell(c) for c in b.header) + " |",
                 "|" + "|".join("---" for _ in b.header) + "|"]
        for r in b.rows:
            lines.append("| " + " | ".join(cell(c) for c in r) + " |")
        return (cap + "\n\n" if cap else "") + "\n".join(lines)

    def footnote(self, f: Footnote) -> str:
        return f"[^{f.num}]: {render_runs(f.inlines)}"
