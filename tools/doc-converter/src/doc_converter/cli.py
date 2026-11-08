"""Command line.

  doc-converter convert --input <pdf> --output <dir> --profile <name|dir> [--sections ID...]
  doc-converter verify  --input <pdf> --output <dir> --profile <name|dir> [--report-dir DIR]
  doc-converter census  --input <pdf> --pages <first>-<last>     font x size x colour histogram (profile authoring)

verify lives in core/verify.py.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

from .core import pipeline
from .core.pdf import Book
from .core.profile import load as load_profile
from .core.verify import main as verify
from .core.verify import tolerant_stdout


def convert(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="doc-converter convert", description="Convert a PDF into Markdown using a profile.")
    ap.add_argument("--input", required=True, help="the PDF to convert")
    ap.add_argument("--output", required=True, help="directory for everything the run generates")
    ap.add_argument("--profile", required=True, help="profile name (under profiles/) or directory")
    ap.add_argument("--sections", nargs="+", metavar="ID", help="section ids to convert (default: all)")
    args = ap.parse_args(argv)
    prof = load_profile(args.profile)
    book = Book(args.input)
    known = pipeline.section_ids(book, prof)
    unknown = [s for s in args.sections or [] if s not in known]
    if unknown:
        sys.exit(f"unknown section ids: {' '.join(unknown)} (known: {' '.join(known)})")
    res = pipeline.convert(book, prof, Path(args.output), args.sections)
    for file, n in res.written:
        print(f"wrote {file} ({n} blocks)")
    log = res.resolver.log
    review = [d for d in log if re.search("REVIEW|AMBIGUOUS", d.why)]
    print(f"hyphens: {len(log)} resolved, {len(review)} flagged")
    for d in review:
        print(f"  {d.where} {d.a}-|{d.b} -> {'kept' if d.keep else 'joined'} ({d.why})")
    print(f"warnings: {len(res.warnings)}")
    for w in res.warnings:
        print("  " + w)


def census(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="doc-converter census", description="Histogram of font x size x colour, with sample text.")
    ap.add_argument("--input", required=True, help="the PDF")
    ap.add_argument("--pages", required=True, help="PDF page range, e.g. 16-296")
    args = ap.parse_args(argv)
    m = re.fullmatch(r"(\d+)-(\d+)", args.pages)
    if not m:
        sys.exit(f"--pages must look like 16-296, not {args.pages!r}")
    book = Book(args.input)
    stats: dict[tuple, list] = defaultdict(lambda: [0, 0, ""])
    for s in book.all_spans(int(m[1]), int(m[2])):
        if not s.text.strip():
            continue
        st = stats[(s.font, s.size, s.color)]
        st[0] += 1
        st[1] += len(s.text)
        if len(st[2]) < 50:
            st[2] = (st[2] + " | " + s.text.strip())[:60] if st[2] else s.text.strip()[:60]
    for (font, size, color), (n, chars, sample) in sorted(stats.items(), key=lambda kv: -kv[1][1]):
        print(f"{n:5} {chars:6}  {font:28} {size:6.2f} {color}  {sample}")


COMMANDS = {"convert": convert, "verify": verify, "census": census}
USAGE = f"usage: doc-converter {{{','.join(COMMANDS)}}} [options]   (doc-converter <command> --help for details)"


def main(argv: list[str] | None = None) -> None:
    tolerant_stdout()
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        sys.exit(0 if argv else 2)
    if argv[0] not in COMMANDS:
        sys.exit(f"unknown command {argv[0]!r}\n{USAGE}")
    COMMANDS[argv[0]](argv[1:])
