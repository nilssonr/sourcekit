"""Verify that the generated Markdown contains exactly the book's words.

Per file: the word stream is rebuilt from raw spans in reading order (its own rows, top to bottom,
left to right), minus running headers, page numbers, figure-label text and bullet glyphs, and is
compared with the Markdown minus the added material listed in the run's manifest.json.

Also: the files' page ranges must match the profile's outline split and be contiguous (coverage),
and an independent whole-book count compares PyMuPDF's separate word extraction with all files.

Usage: uv run verify --input <pdf> --output <dir> --profile <name|dir> [--report-dir DIR]

Reports go to stdout, or to --report-dir: <file>.md.txt per file, coverage.txt, independent.txt.
Exits 1 when coverage fails.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pymupdf

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


def without_added(md: str, ranges: list[list]) -> str:
    """The Markdown with the manifest's added-material ranges [start, end, kind] removed."""
    out, pos = [], 0
    for start, end, _ in sorted(ranges):
        out.append(md[pos:start])
        pos = end
    out.append(md[pos:])
    return "".join(out)


def report(book: Book, book_text: str, p0: int, p1: int, skip_pages: set[int], cfg: VerifyConfig) -> str:
    """Compare one file's book text (Markdown minus added material) with PDF pages p0-p1."""
    lines: list[str] = []
    text = ""
    for p in range(p0, p1 + 1):
        if p not in skip_pages:
            t = page_text(book.spans(p), cfg)
            # a hyphen at the end of a page joins the first word of the next page
            text += t.lstrip() if re.search(r"[A-Za-z]-$", text) else " " + t
    expected = tokens(text)
    actual = tokens(book_text)

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


def _excluded_boxes(spans: list[Span], cfg: VerifyConfig) -> list[pymupdf.Rect]:
    """Boxes of the spans the per-file comparison leaves out: headers, figure labels, bullets, page numbers."""
    boxes = [pymupdf.Rect(s.x0, s.y0, s.x1, s.y1) for s in spans
             if s.baseline < cfg.header_band or cfg.is_figure_text(s) or cfg.is_bullet_glyph(s)]
    rows: list[tuple[float, list[Span]]] = []
    for s in sorted(spans, key=lambda s: s.baseline):
        if rows and abs(rows[-1][0] - s.baseline) <= 2.7:
            rows[-1][1].append(s)
        else:
            rows.append((s.baseline, [s]))
    for _, fs in rows:
        ink = [s for s in fs if s.text.strip()]
        if ink and all(cfg.is_page_number(s) for s in ink):
            boxes += [pymupdf.Rect(s.x0, s.y0, s.x1, s.y1) for s in ink]
    return boxes


def independent(book: Book, pages: list[int], book_text: str, cfg: VerifyConfig) -> str:
    """Whole-book word multiset from PyMuPDF's own word extraction (not the converter's spans, rows
    or regions) against all files' book text. Shares only the exclusion predicates."""
    words: list[tuple[str, bool]] = []   # (word, last on its line)
    for p in pages:
        page = book.page(p)
        boxes = _excluded_boxes(book.spans(p), cfg)
        ws = page.get_text("words", flags=pymupdf.TEXT_MEDIABOX_CLIP)
        for k, w in enumerate(ws):
            c = pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)
            if any(b.contains(c) for b in boxes):
                continue
            last = k + 1 == len(ws) or ws[k + 1][5:7] != w[5:7]
            words.append((w[4], last))
    # A line-end hyphen joins the next word in PyMuPDF's order; tokens() drops the hyphen itself.
    # Where a listing or figure interrupts a paragraph, that order is not reading order and the join
    # pairs the wrong words (known: PDF page 118, "sub-" + "Listing"). Tried and rejected: waiting for
    # a lower-case word, or joining within PyMuPDF's text blocks; both paired more words wrongly.
    text: list[str] = []
    for i, (w, _) in enumerate(words):
        if text and words[i - 1][1] and re.search(r"[A-Za-z]-$", text[-1]):
            text[-1] += w
        else:
            text.append(w)
    expected = Counter(tokens(" ".join(text)))
    actual = Counter(tokens(book_text))
    missing = sorted(((v - actual[k], k) for k, v in expected.items() if actual[k] < v), reverse=True)
    extra = sorted(((v - expected[k], k) for k, v in actual.items() if expected[k] < v), reverse=True)
    matched = sum(min(v, actual[k]) for k, v in expected.items())
    return (f"independent count: PyMuPDF words on {len(pages)} pages vs all Markdown files minus added material\n"
            f"(a coarse check of the loading path: line-end hyphens are joined in PyMuPDF's word order, which is\n"
            f"not reading order where a listing or figure interrupts a paragraph)\n"
            f"pdf {sum(expected.values())} words, markdown {sum(actual.values())} words, matched {matched}\n"
            f"missing from markdown ({len(missing)}): {' '.join(f'{k}×{n}' for n, k in missing)}\n"
            f"extra in markdown ({len(extra)}): {' '.join(f'{k}×{n}' for n, k in extra)}\n")


def coverage(files: list[dict], expected: dict[str, tuple[int, int]]) -> tuple[bool, str]:
    """The manifest's files against the profile's outline split; page ranges contiguous, no overlap."""
    problems = []
    got = {f["file"]: tuple(f["pages"]) for f in files}
    for file, pages in expected.items():
        if file not in got:
            problems.append(f"missing file {file} (pages {pages[0]}-{pages[1]})")
        elif got[file] != pages:
            problems.append(f"{file}: pages {got[file]} in the manifest, {pages} from the outline")
    problems += [f"unexpected file {f}" for f in got if f not in expected]
    ranges = sorted(got.values())
    for (a0, a1), (b0, b1) in zip(ranges, ranges[1:]):
        if b0 <= a1:
            problems.append(f"pages {a0}-{a1} and {b0}-{b1} overlap")
        elif b0 != a1 + 1:
            problems.append(f"pages {a1 + 1}-{b0 - 1} are in no file")
    span = f"{ranges[0][0]}-{ranges[-1][1]}" if ranges else "none"
    head = f"coverage: {len(got)} files, PDF pages {span}: " + ("OK" if not problems else f"{len(problems)} problems")
    return not problems, "\n".join([head] + ["  " + p for p in problems]) + "\n"


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
    mpath = out / "manifest.json"
    if not mpath.exists():
        sys.exit(f"no manifest.json in {out}")
    files = json.loads(mpath.read_text())["files"]

    def emit(name: str, title: str, text: str) -> None:
        if rdir:
            (rdir / name).write_text(text)
        else:
            sys.stdout.write(f"--- {title}\n{text}\n")

    expected = {sec.file: sec.pages for sec in outline.sections(book, prof.split).values()}
    ok, cov = coverage(files, expected)
    emit("coverage.txt", "coverage", cov)
    all_text, pages = [], []
    for f in files:
        p0, p1 = f["pages"]
        skip = set(f["skip_pages"])
        text = without_added((out / f["file"]).read_text(encoding="utf-8"), f["added"])
        all_text.append(text)
        pages += [p for p in range(p0, p1 + 1) if p not in skip]
        emit(f"{f['file']}.txt", f"{f['file']} (PDF pages {p0}-{p1})", report(book, text, p0, p1, skip, prof.verify))
    emit("independent.txt", "independent count", independent(book, sorted(pages), "\n".join(all_text), prof.verify))
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
