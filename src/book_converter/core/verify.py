"""Verify that a generated Markdown file contains exactly the book's words for its page range.

Independent of the converter: it rebuilds the word stream from raw spans in reading order
(rows top to bottom, left to right), minus running headers, page numbers, figure-label text
and bullet glyphs, and compares it with the Markdown minus the material the converter adds.

Usage: uv run verify --input <pdf> --output <dir> --profile <name|dir> [--report-dir DIR]

Checks every section file of the profile in <dir>. Reports go to stdout, or one file per section
(<file>.md.txt) in --report-dir.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .pdf import Book, Span
from ..modules.split import outline
from .profile import load as load_profile


@dataclass(frozen=True)
class VerifyConfig:
    """What the verifier leaves out of the PDF's word stream (set by the profile)."""
    header_band: float                        # baselines above this are running headers
    superscript_max: float                    # smaller text is a superscript (footnote marker)
    is_figure_text: Callable[[Span], bool]    # figure-label text
    is_bullet_glyph: Callable[[Span], bool]   # bullet glyphs
    is_page_number: Callable[[Span], bool]    # a row made only of these is a page number


def tokens(s: str) -> list[str]:
    s = s.lower().replace("’", "'")
    s = re.sub(r"[-‐‑]", "", s)
    return [t for t in re.split(r"[^a-z0-9]+", s) if t]


def page_text(spans: list[Span], cfg: VerifyConfig) -> str:
    keep = [s for s in spans if s.baseline >= cfg.header_band and not cfg.is_figure_text(s)
            and not cfg.is_bullet_glyph(s)]
    rows: list[tuple[float, list[Span]]] = []
    # full-size text first, then small superscripts (footnote markers) join the row just below them
    for s in sorted(keep, key=lambda s: (s.size < cfg.superscript_max, s.baseline, s.x0)):
        small = s.size < cfg.superscript_max
        for base, fs in rows:
            if (-1.0 <= base - s.baseline <= 5.0) if small else abs(base - s.baseline) <= 2.7:
                fs.append(s)
                break
        else:
            rows.append((s.baseline, [s]))
    text = ""
    for _, fs in sorted(rows, key=lambda r: r[0]):
        fs.sort(key=lambda s: s.x0)
        if all(cfg.is_page_number(s) for s in fs if s.text.strip()):
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


def report(book: Book, md_path: Path, p0: int, p1: int, skip_pages: set[int], cfg: VerifyConfig) -> str:
    lines: list[str] = []
    text = ""
    for p in range(p0, p1 + 1):
        if p not in skip_pages:
            t = page_text(book.spans(p), cfg)
            # a hyphen at the end of a page joins the first word of the next page
            text += t.lstrip() if re.search(r"[A-Za-z]-$", text) else " " + t
    expected = tokens(text)
    actual = tokens(strip_added(md_path.read_text(encoding="utf-8")))

    ce, ca = Counter(expected), Counter(actual)
    missing = [f"{k}×{v - ca[k]}" for k, v in ce.items() if ca[k] < v]
    extra = [f"{k}×{v - ce[k]}" for k, v in ca.items() if ce[k] < v]
    matched = sum(min(v, ca[k]) for k, v in ce.items())
    lines.append(f"expected {len(expected)} words, markdown {len(actual)} words, matched {matched}")
    lines.append(f"missing from markdown ({len(missing)}): {' '.join(missing)}")
    lines.append(f"extra in markdown ({len(extra)}): {' '.join(extra)}")

    sm = difflib.SequenceMatcher(None, expected, actual, autojunk=False)
    hunks = [op for op in sm.get_opcodes() if op[0] != "equal"]
    lines.append(f"order: {len(hunks)} differing stretches")

    # Pair moves: every stretch taken out of one place must reappear as part of a stretch put in elsewhere.
    outs = [Counter(expected[i1:i2]) for t, i1, i2, j1, j2 in hunks if i2 > i1]
    ins = [Counter(actual[j1:j2]) for t, i1, i2, j1, j2 in hunks if j2 > j1]
    out_ops = [h for h in hunks if h[2] > h[1]]
    unpaired = []
    for k in sorted(range(len(outs)), key=lambda k: -sum(outs[k].values())):
        o = outs[k]
        home = next((c for c in ins if all(c[w] >= n for w, n in o.items())), None)
        if home is None:
            unpaired.append(out_ops[k])
        else:
            home.subtract(o)
    leftover_in = [c for c in ins if +c]
    moved = len(out_ops) - len(unpaired)
    lines.append(f"moves: {moved} stretches reappear elsewhere; unexplained: {len(unpaired)} out, {len(leftover_in)} in")
    for tag, i1, i2, j1, j2 in unpaired:
        lines.append(f"  UNPAIRED {tag} pdf[{i1}:{i2}]: {' '.join(expected[i1:i2])[:140]}")
    for c in leftover_in:
        lines.append(f"  UNPAIRED in md: {' '.join(sorted((+c).elements()))[:140]}")
    for tag, i1, i2, j1, j2 in hunks[:60]:
        lines.append(f"  {tag} pdf[{i1}:{i2}] md[{j1}:{j2}]")
        lines.append(f"    pdf: {' '.join(expected[i1:i2])[:140] or '(none)'}")
        lines.append(f"    md : {' '.join(actual[j1:j2])[:140] or '(none)'}")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(prog="verify", description="Compare converted Markdown with the PDF's words.")
    ap.add_argument("--input", required=True, help="the PDF that was converted")
    ap.add_argument("--output", required=True, help="the conversion's output directory")
    ap.add_argument("--profile", required=True, help="profile name (under profiles/) or directory")
    ap.add_argument("--report-dir", help="write one report per section file here instead of to stdout")
    args = ap.parse_args()
    prof = load_profile(args.profile)
    book = Book(args.input)
    out = Path(args.output)
    rdir = Path(args.report_dir) if args.report_dir else None
    if rdir:
        rdir.mkdir(parents=True, exist_ok=True)
    sections = outline.sections(book, prof.split)
    missing = [sec.file for sec in sections.values() if not (out / sec.file).exists()]
    if missing:
        sys.exit(f"missing in {out}: {' '.join(missing)}")
    for sec in sections.values():
        rep = report(book, out / sec.file, sec.pages[0], sec.pages[1], prof.skip_pages, prof.verify)
        if rdir:
            (rdir / f"{sec.file}.txt").write_text(rep)
        else:
            sys.stdout.write(f"--- {sec.file} (PDF pages {sec.pages[0]}-{sec.pages[1]})\n{rep}\n")


if __name__ == "__main__":
    main()
