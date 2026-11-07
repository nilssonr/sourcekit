# book-converter

Converts the born-digital PDF of *Unit Testing: Principles, Practices, and Patterns* (Khorikov, Manning 2020)
into verbatim Markdown: one file per chapter, part intro and the front matter, with figures and listings as PNGs.
Uses PyMuPDF for text spans, vector drawings (callout arrows, table rules, figure geometry) and rendering.

No book content lives in this repository; the PDF and the output directory are arguments.

## Usage

```sh
uv run convert <pdf> <outDir> <section>...        # section ids: see src/book_converter/sections.py
uv run verify  <pdf> <outDir>/<file>.md <firstPage> <lastPage>
uv run census  <pdf> <firstPage> <lastPage>       # font/size/color histogram (dev tool)
uv run python -m unittest discover -s tests -v    # needs the PDF (BOOK_PDF env var or ~/temp default)
```

## What the converter adds (marked, and stripped by the verifier)

- `> **Illustration description (added in conversion, not book text):** …` under each figure
  (from `data/descriptions.json`; label strings per figure are written to `data/figure-labels.json`)
- `*Callouts (added in conversion; …):*` lists under listings: callout text with the code line(s) each
  arrow or bracket points to, measured from the vector graphics; plus `![Listing …](…)` images
- `# Chapter N:` prefixes on chapter titles, and `# Front matter`

Dropped on purpose: running headers, page numbers, the "(continued)" sidebar labels, the copyright page,
the table of contents and the index.

## Verification

`verify` rebuilds the word stream from the PDF (rows in reading order, minus headers, page numbers,
figure-label text and bullet glyphs) and compares it with the Markdown minus the added material:
a word multiset comparison and an ordered diff. Expected order differences are intentional moves:
callouts listed under their listing, footnotes at the end of the chapter, table cells row by row.
