# book-converter

Converts a born-digital PDF into verbatim Markdown, with a verifier. Book-independent code lives in this
repository. Everything specific to one PDF lives in a **profile**: fonts, colours, layout values, how
the outline splits into files, and hand-written data such as figure descriptions.

The one profile so far is for *Unit Testing: Principles, Practices, and Patterns* (Khorikov, Manning
2020). It produces one file per chapter, part intro and the front matter, with figures and listings as
PNGs. PyMuPDF supplies text spans, vector drawings (callout arrows, table rules, figure geometry) and
rendering.

No book content lives in this repository. The PDF, the output directory and the profile are arguments,
and a run writes only under `--output`. Profiles live under `profiles/<name>/`. That directory is
git-ignored here and is a separate local git repository. The design is in `docs/design.md`.

## Usage

```sh
uv run convert --input <pdf> --output <dir> --profile manning_unit_testing [--sections ID...]
uv run verify  --input <pdf> --output <dir> --profile manning_unit_testing [--report-dir DIR]
uv run census  --input <pdf> --pages 16-296       # font x size x colour histogram (profile authoring)

scripts/make_run.sh <pdf> <run dir> <profile>     # convert + verify into a run directory
scripts/compare_ref.sh <ref dir> <run dir>        # regression check against a saved reference run

uv run python -m unittest discover -s tests -v    # book tests need the PDF (BOOK_PDF, default under ~/temp)
                                                  # and the profile; BOOK_REF=<ref dir> adds the full regression run
```

Section ids (`front`, `part-1`, `ch01`, …) come from the PDF outline, using the profile's split rules.

## Layout

```
src/book_converter/
  cli.py                 convert, census
  core/                  pdf (Book, spans, outline, page labels), model (document blocks), rows (rows, runs,
                         line joining), hyphens, sections, profile (loader), pipeline, verify
  modules/regions/       page (one page's regions in reading order), figures, tables, listings (code, callouts)
  modules/assemble/      flow (rows and regions -> blocks)
  modules/serialize/     markdown (blocks -> Markdown + added-material ranges)
  modules/split/         outline (bookmarks -> output files)
profiles/<name>/         git-ignored: __init__.py (PROFILE), styles.py, roles.py, data/descriptions.json
```

## Output

| File | What it is |
|---|---|
| `*.md`, `illustrations/*.png` | the content |
| `manifest.json` | per file: PDF page range, skipped pages, character ranges of added material |
| `figure-labels.json` | label strings found in each figure |
| `hyphen-decisions.json` | every line-end hyphen decision, with its reason |
| `warnings.txt` | conversion warnings (also printed) |
| `run-info.json` | input hash, PyMuPDF/MuPDF versions, dictionary path and hash |

All of these are generated fresh on every run.

## What the converter adds (listed in manifest.json, ignored by the verifier)

- `> **Illustration description (added in conversion, not book text):** …` under each figure, from
  `profiles/<name>/data/descriptions.json`.
- `*Callouts (added in conversion; …):*` lists under listings. Each lists the callout text with the code
  line(s) its arrow or bracket points to, measured from the vector graphics. Each listing also gets a
  `![Listing …](…)` image.
- `Chapter N: ` prefixes on chapter titles, and the `# Front matter` title.
- In code blocks with bold or italic: the `<pre><code>` block and its `<b>`/`<i>` tags. These blocks are
  written as HTML so the emphasis survives (GitHub and CommonMark render them). Code is HTML-escaped there,
  and every other code block is a plain fenced block.

A listing that continues onto another page gets one image per page part that has callouts:
`listing-6.10.png` (first page), `listing-6.10-2.png` (second page). Code blocks with callouts but no
listing title are rendered like listings, as `code-p<page label>.png`.

Line-end hyphens are resolved from the book's own unbroken words first. That corpus is the whole book,
including figure labels and the index. The system dictionary is the fallback. The verifier ignores
hyphens, so review the `compound`, `AMBIGUOUS` and `REVIEW` entries in `hyphen-decisions.json`.

Dropped on purpose: running headers, page numbers, the "(continued)" sidebar labels, the copyright page,
the table of contents and the index.

## Verification

`verify` writes three kinds of report:

- **Per file** (`<file>.md.txt`): it rebuilds the word stream from the PDF, using its own rows in reading
  order, minus headers, page numbers, figure-label text and bullet glyphs. That stream is compared with
  the Markdown minus the added ranges in `manifest.json`, as a word multiset and as an ordered diff.
  Expected order differences are intentional moves: callouts listed under their listing, footnotes at the
  end of the chapter, table cells row by row.
- **Coverage** (`coverage.txt`): the files' page ranges must match the outline split, with no gaps or
  overlaps. `verify` exits with status 1 if they don't.
- **Emphasis** (`emphasis.txt`): the bold and italic characters of the PDF's code blocks must equal the
  text inside the converter's `<b>`/`<i>` tags (literal tags in book code don't count).
- **Independent count** (`independent.txt`): PyMuPDF's separate word extraction for the whole book,
  compared with all files. This is a coarse check of the loading path. Its hyphen joining follows
  PyMuPDF's word order, which isn't reading order where a listing or figure interrupts a paragraph.
