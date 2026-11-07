"""Verify that a generated Markdown file contains exactly the book's words for its page range.

Independent of the converter: it rebuilds the word stream from raw spans in reading order
(rows top to bottom, left to right), minus running headers, page numbers, figure-label text
and bullet glyphs, and compares it with the Markdown minus the material the converter adds.

Usage: uv run verify <pdf> <markdown> <firstPage> <lastPage>
"""

from __future__ import annotations

import difflib
import re
import sys
from collections import Counter

from .pdf import Book, Span
from .sections import SKIP_PAGES
from .styles import HEADER_BAND, is_figure_text


def tokens(s: str) -> list[str]:
    s = s.lower().replace("’", "'")
    s = re.sub(r"[-‐‑]", "", s)
    return [t for t in re.split(r"[^a-z0-9]+", s) if t]


def page_text(spans: list[Span]) -> str:
    keep = [s for s in spans if s.baseline >= HEADER_BAND and not is_figure_text(s)
            and not s.font.startswith(("Wingdings", "ZapfDingbats"))]
    rows: list[tuple[float, list[Span]]] = []
    # full-size text first, then small superscripts (footnote markers) join the row just below them
    for s in sorted(keep, key=lambda s: (s.size < 6.6, s.baseline, s.x0)):
        small = s.size < 6.6
        for base, fs in rows:
            if (-1.0 <= base - s.baseline <= 5.0) if small else abs(base - s.baseline) <= 2.7:
                fs.append(s)
                break
        else:
            rows.append((s.baseline, [s]))
    text = ""
    for _, fs in sorted(rows, key=lambda r: r[0]):
        fs.sort(key=lambda s: s.x0)
        if all(s.font.startswith("NewBaskerville-Bold") and s.color == "#000000"
               and re.fullmatch(r"\s*([0-9]+|[ivxlc]+)\s*", s.text) for s in fs if s.text.strip()):
            continue  # page number (foot of an opener page)
        line, prev = "", None
        for s in fs:
            if prev and s.x0 - prev.x1 > 1.0 and not prev.text[-1:].isspace() and not s.text[:1].isspace():
                line += " "
            line += s.text
            prev = s
        # a line-end hyphen joins the halves; tokens() drops the hyphen itself
        text += line.lstrip() if re.search(r"[A-Za-z]-\s*$", text) else " " + line
        text = re.sub(r"([A-Za-z])-\s*$", r"\1-", text)
    return text


ADDED = [
    (r"^!\[.*$", ""),
    (r"^> \*\*Illustration description \(added in conversion, not book text\):\*\*.*$", ""),
    (r"^\*Callouts \(added in conversion.*$", ""),
    (r"^- \(lines? [\d–, ]+\) ", ""),
    (r"^# Chapter \d+: ", "# "),
    (r"^# Front matter$", ""),
]


def strip_added(md: str) -> str:
    for pat, rep in ADDED:
        md = re.sub(pat, rep, md, flags=re.M)
    return md


def main() -> None:
    pdf, md_path, a, b = sys.argv[1:5]
    p0, p1 = int(a), int(b)
    book = Book(pdf)
    expected: list[str] = []
    for p in range(p0, p1 + 1):
        if p not in SKIP_PAGES:
            expected += tokens(page_text(book.spans(p)))
    actual = tokens(strip_added(open(md_path, encoding="utf-8").read()))

    ce, ca = Counter(expected), Counter(actual)
    missing = [f"{k}×{v - ca[k]}" for k, v in ce.items() if ca[k] < v]
    extra = [f"{k}×{v - ce[k]}" for k, v in ca.items() if ce[k] < v]
    matched = sum(min(v, ca[k]) for k, v in ce.items())
    print(f"expected {len(expected)} words, markdown {len(actual)} words, matched {matched}")
    print(f"missing from markdown ({len(missing)}): {' '.join(missing)}")
    print(f"extra in markdown ({len(extra)}): {' '.join(extra)}")

    sm = difflib.SequenceMatcher(None, expected, actual, autojunk=False)
    hunks = [op for op in sm.get_opcodes() if op[0] != "equal"]
    print(f"order: {len(hunks)} differing stretches")
    for tag, i1, i2, j1, j2 in hunks[:40]:
        print(f"  {tag} pdf[{i1}:{i2}] md[{j1}:{j2}]")
        print(f"    pdf: {' '.join(expected[i1:i2])[:140] or '(none)'}")
        print(f"    md : {' '.join(actual[j1:j2])[:140] or '(none)'}")


if __name__ == "__main__":
    main()
