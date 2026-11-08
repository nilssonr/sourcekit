"""Markdown output for the document model.

Material the converter adds (figure descriptions, callout headers and line references, image links,
section title prefixes) is reported as character ranges, so the verifier can tell it from book text.
"""

from __future__ import annotations

import html
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


# Added material is wrapped as ADD + kind + KIND_END + text + ADD_END while rendering, then turned
# into ranges. These control characters never occur in book text (checked).
ADD, KIND_END, ADD_END = "\x00", "\x02", "\x01"


def added(kind: str, text: str) -> str:
    return f"{ADD}{kind}{KIND_END}{text}{ADD_END}"


def extract_added(marked: str) -> tuple[str, list[tuple[int, int, str]]]:
    """Remove the markers: the clean text and the (start, end, kind) character ranges of added material."""
    out: list[str] = []
    ranges: list[tuple[int, int, str]] = []
    pos = n = 0
    while True:
        i = marked.find(ADD, pos)
        if i < 0:
            rest = marked[pos:]
            break
        out.append(marked[pos:i])
        n += i - pos
        k = marked.index(KIND_END, i)
        e = marked.index(ADD_END, k)
        text = marked[k + 1:e]
        if ADD in text or KIND_END in text:
            raise ValueError("nested added-material markers")
        out.append(text)
        ranges.append((n, n + len(text), marked[i + 1:k]))
        n += len(text)
        pos = e + 1
    if KIND_END in rest or ADD_END in rest:
        raise ValueError("unbalanced added-material markers")
    out.append(rest)
    return "".join(out), ranges


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


TAGS = {"b": ("<b>", "</b>"), "i": ("<i>", "</i>"), "B": ("<b><i>", "</i></b>")}


def has_emphasis(marks: list[str]) -> bool:
    return any(m.strip(".") for m in marks)


def pre(lines: list[str], marks: list[str]) -> str:
    """Code with emphasis as an HTML block: <pre><code> with <b>/<i>, text HTML-escaped. Tags are
    marked "code-markup" (added); entities are marked "code-escape" (book characters, written escaped)."""
    def esc_html(t: str) -> str:
        return re.sub(r"&(?:lt|gt|amp);", lambda m: added("code-escape", m[0]), html.escape(t, quote=False))

    out = []
    for t, m in zip(lines, marks):
        m = m.ljust(len(t), ".")
        line, i = "", 0
        while i < len(t):
            j = i
            while j < len(t) and m[j] == m[i]:
                j += 1
            seg = esc_html(t[i:j])
            tag = TAGS.get(m[i])
            line += added("code-markup", tag[0]) + seg + added("code-markup", tag[1]) if tag else seg
            i = j
        out.append(line)
    return added("code-markup", "<pre><code>") + "\n".join(out) + "\n" + added("code-markup", "</code></pre>")


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

    def section(self, doc: SectionDoc) -> tuple[str, list[tuple[int, int, str]]]:
        """The section's Markdown and the character ranges of added material in it."""
        sec = doc.section
        out = [self.block(b) for b in doc.blocks] + [self.footnote(f) for f in doc.footnotes]
        if sec.title and sec.title_added and doc.blocks and isinstance(doc.blocks[0], Heading) and not doc.blocks[0].src:
            h = doc.blocks[0]
            rest = sec.title[len(sec.title_added):]
            tail = render_runs([Run(rest, "")] + h.inlines[1:]) if rest or len(h.inlines) > 1 else ""
            out[0] = "#" * h.level + " " + added("title", sec.title_added.rstrip() if not tail else sec.title_added) + tail
        marked = "\n\n".join(out) + "\n"
        return extract_added(marked)

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
        md = pre(b.lines, b.marks) if has_emphasis(b.marks) else fence(b.lines)
        if b.title:
            pat, rep = self.cfg.listing_title
            md = f"**{re.sub(pat, rep, b.title)}**\n\n" + md
        if b.callouts:
            items = [added("callout-lines", f"- ({fmt_lines(c.lines)}) ") + render_runs(c.inlines)
                     for c in sorted(b.callouts, key=lambda c: (min(c.lines) if c.lines else 0, c.top))]
            md += ("\n\n" + added("callout-header", "*Callouts (added in conversion; line numbers count every line "
                                     "of the listing, blank lines included):*") + "\n" + "\n".join(items))
        for img in b.images:
            alt = f"Listing {b.lid}" if b.title else f"Code on book page {b.lid[1:]}"
            md += "\n\n" + added("listing-image", f"![{alt} with callouts](illustrations/{img.file})")
        return md

    def figure(self, b: Figure) -> str:
        note = added("figure-description",
                     f"> **Illustration description (added in conversion, not book text):** {b.description or 'TODO'}")
        img = added("figure-image", f"![{b.alt}](illustrations/{b.image.file})")
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
