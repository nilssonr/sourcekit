"""Command line: convert a PDF into Markdown with a profile.

Usage: uv run convert --input <pdf> --output <dir> --profile <name|dir> [--sections ID...]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .core import pipeline
from .core.pdf import Book
from .core.profile import load as load_profile


def convert() -> None:
    ap = argparse.ArgumentParser(prog="convert", description="Convert a PDF into Markdown using a profile.")
    ap.add_argument("--input", required=True, help="the PDF to convert")
    ap.add_argument("--output", required=True, help="directory for everything the run generates")
    ap.add_argument("--profile", required=True, help="profile name (under profiles/) or directory")
    ap.add_argument("--sections", nargs="+", metavar="ID", help="section ids to convert (default: all)")
    args = ap.parse_args()
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
