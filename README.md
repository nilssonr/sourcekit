# book-converter

Converts the born-digital PDF of *Unit Testing: Principles, Practices, and Patterns* (Khorikov, Manning 2020)
into verbatim Markdown: one file per chapter, part intro and the front matter, with figures and listings as PNGs.
Uses PyMuPDF for text spans, vector drawings (callout arrows, table rules, figure geometry) and rendering.

No book content lives in this repository. The PDF, the output directory and the profile are arguments.
A run writes only under `--output`. Book-specific data, such as the figure descriptions, lives in a profile
under `profiles/<name>/`. That directory is git-ignored here and is a separate local git repository.
The restructuring plan is in `docs/design.md`.

## Usage

```sh
uv run convert --input <pdf> --output <dir> --profile manning_unit_testing [--sections ID...]
                                                  # section ids: see the profile's SECTIONS
uv run verify  --input <pdf> --output <dir> --profile manning_unit_testing [--report-dir DIR]
uv run census  <pdf> <firstPage> <lastPage>       # font/size/color histogram (dev tool)
uv run python -m unittest discover -s tests -v    # needs the PDF (BOOK_PDF env var or ~/temp default)
scripts/make_run.sh <pdf> <run dir> <profile>     # convert + verify into a run directory
scripts/compare_ref.sh <ref dir> <run dir>        # regression check against a saved reference run
```

## What the converter adds (marked, and stripped by the verifier)

- `> **Illustration description (added in conversion, not book text):** …` under each figure
  (from `profiles/<name>/data/descriptions.json`; label strings per figure are written to
  `<output>/figure-labels.json`)
- `*Callouts (added in conversion; …):*` lists under listings: callout text with the code line(s) each
  arrow or bracket points to, measured from the vector graphics; plus `![Listing …](…)` images
- `# Chapter N:` prefixes on chapter titles, and `# Front matter`

A listing that continues onto another page gets one image per page part that has callouts:
`listing-6.10.png` (first page), `listing-6.10-2.png` (second page). Code blocks with callouts but no
listing title are rendered like listings, as `code-p<book page>.png`.

Line-end hyphens are resolved from the book's own unbroken words (whole book, including figure labels
and the index), then the system dictionary; `<output>/hyphen-decisions.json` logs every decision with its
reason. The verifier ignores hyphens, so review the `compound`, `AMBIGUOUS` and `REVIEW` entries there.

Dropped on purpose: running headers, page numbers, the "(continued)" sidebar labels, the copyright page,
the table of contents and the index.

## Verification

`verify` rebuilds the word stream from the PDF (rows in reading order, minus headers, page numbers,
figure-label text and bullet glyphs) and compares it with the Markdown minus the added material:
a word multiset comparison and an ordered diff. Expected order differences are intentional moves:
callouts listed under their listing, footnotes at the end of the chapter, table cells row by row.
