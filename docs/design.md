# Design: stages, document model and profile

Status: **draft for review** (Phase 2 of the approved plan). No code has moved yet.

Scope: the Manning *Unit Testing* PDF only. The aim is a clean split between book-independent code and the Manning profile, and a document model between PDF analysis and Markdown. The output stays **byte-identical** to the reference in `~/temp/book-converter-ref/manning/`, except where a section below names a deliberate, reviewed difference.

## 1. Rules for the split

1. **Book-independent code goes in `core/` or `modules/`.** Anything containing a Manning value (font name, colour, size, coordinate, gap, caption regex, page number) is book-specific and goes in the profile. Algorithms stay in `modules/`, and the profile passes their constants in as a config dataclass.
2. **No abstraction without a consumer.** There is one PDF, so a hook exists only where Manning needs it. We don't add "generic" variants that nothing uses.
3. **Runs only write under `--output`.** The profile's `data/` is read-only at run time.
4. **One rendering path.** The verifier checks the real Markdown file, with added material removed by exact character ranges (§6). It never checks a second rendering.

## 2. Pipeline

```
Load ─► Sections ─► per section, per page: Furniture ─► Regions ─► Rows+Roles ─► Assemble ─┐
                                                                                            ▼
                         Write images ◄─ Serialize (Markdown + added-material ranges) ◄─ Document (blocks)
Verify: reads the Markdown, the manifest and the PDF.
```

| Stage | Input → output | Where | Today |
|---|---|---|---|
| Load | PDF → `Book` (spans with chars, drawings, outline, page labels, span ids) | `core/pdf.py` | `pdf.py` |
| Sections | `Book` + profile split data → `list[Section]` (file, title, page range, skip pages) | `modules/split/outline.py` | `sections.py` (hard-coded) |
| Furniture | spans → spans minus running headers and page numbers | profile predicate, applied by `core/pipeline.py` | `classify()` → `header`/`pagenum`, then skipped |
| Regions | page → `list[Region]`, each claiming its spans | `modules/regions/*`, run in the profile's order | the figure/table/listing/code-run blocks in `Converter.convert` |
| Rows + Roles | unclaimed spans → `Row`s with a role | `core/rows.py` + profile `role(row)` | `rows_of` + `classify` |
| Assemble | regions + rows in page order → blocks | `modules/assemble/flow.py`, with a profile `FlowConfig` | the state machine in `Converter.convert` |
| Serialize | blocks → Markdown text + added ranges | `modules/serialize/markdown.py` | `render_runs`, `fence`, `Listing.render`, `Table.markdown`, string assembly |
| Write images | figure/listing image requests → PNGs | `modules/regions/figures.py` `render`, `listings.py` | called inline during conversion |
| Verify | PDF + output → report | `core/verify.py` | `verify.py` |

**Stage 1, "Repair text", is dropped:** Manning's text layer is clean.

**Order matters, and stays exactly as today:**
1. figures, and the leftover figure-font text
2. tables
3. listing bars
4. code runs with their callouts
5. rows

Each region claims spans, as the `used` set does today. The profile lists the detectors in this order.

## 3. Load (`core/pdf.py`)

`Book`, `Span` and `Char` are unchanged. Additions:
- `book.outline() -> list[OutlineEntry(level, title, page)]`, from `doc.get_toc()`.
- `book.page_label(pno) -> str`, which replaces `p - 22`. Measured: PDF page 23 is labelled "1".
- `SpanId = (page, index)`, the index into the cached `book.spans(page)`. It is used by `SourceRef` and in place of `id(span)` in claim sets.
- **Not added:** glyph ids via texttrace (only CLRS needed them).

## 4. Document model (`core/model.py`)

```python
@dataclass
class SourceRef:
    page: int
    bbox: tuple[float, float, float, float]
    spans: list[SpanId]

@dataclass
class Run:                       # today's convert.Run, unchanged
    t: str
    s: str                       # "", "i", "b", "bi", "code", "fn"

Inlines = list[Run]              # already joined: line-end hyphens resolved in Assemble

# Blocks (every block has  src: list[SourceRef])
Heading(level: int, inlines: Inlines, added_prefix: str = "")
                                 # "# Chapter 1: …": prefix added; "## Part 1: …" merges two title rows
Paragraph(inlines: Inlines, dropcap: str = "")
Label(inlines: Inlines)          # "This chapter covers" (bold line before a list)
ListBlock(items: list[ListItem]) # ListItem(number: str | None, inlines, subitems: list[Inlines])
Code(lines: list[str], title: Inlines | None = None, lid: str = "",
     callouts: list[Callout] = [], images: list[ImageRef] = [])
                                 # Callout(lines: list[int], inlines); a listing = Code with title
Figure(id: str, image: ImageRef, alt: str, caption: Inlines | None, description: str | None)
Table(caption: Inlines | None, header: list[Inlines], rows: list[list[Inlines]])
Note(label: str, inlines: Inlines, continued: list[Block] = [])
Sidebar(title: Inlines | None, children: list[Block])   # Paragraph | ListBlock | Code
Footnote(num: str, inlines: Inlines)                    # emitted at section end, as today

ImageRef(file: str, page: int, box: Rect, rotate: int = 0, keep: str = "figure")  # rendered later
Document(sections: list[SectionDoc(section, blocks)])
```

Decisions inside the model:
- **"(continued)" reopens the previous Sidebar or Note.** Today the code pops an already-rendered `> **…` string and re-wraps it. The serializer will emit the same bytes from the reopened block.
- **Images are requests, not side effects.** Assemble decides file names (including today's `listing-6.10-2.png` and `code-p<label>-2.png` counting). A separate step renders them with today's `figures.render` and `_listing_image` code, so the PNG bytes stay the same.
- **Phase 4 only:** `Code` gains per-character emphasis, and the serializer emits `<pre><code>` with `<b>`/`<i>` for blocks where it matters.

## 5. Profile contract

The package is `profiles/manning_unit_testing/`: its own local git repository, git-ignored by the main repo. That is decision D2 in §9.

```python
# profiles/manning_unit_testing/__init__.py
PROFILE = Profile(
    name="manning_unit_testing",
    split=SplitConfig(
        levels={1: "part", 2: "chapter"},       # measured: parts at outline level 1, chapters at level 2
        front=FrontMatter(first_page=2, title="Front matter", file="front-matter.md"),
        stop_at="index",                        # the outline entry that ends the content
        skip_pages={3, 4, 6, *range(7, 16)},
        page_figures={2: ("chapter-map", 90)},
        file_name=slug_rule,                    # "chapter-01-the-goal-of-unit-testing.md"
        title=title_rule,                       # "Chapter 1: …" with the prefix marked as added
    ),
    furniture=is_furniture,                     # header band < 46 pt; bold page numbers
    styles=STYLES,                              # today's styles.py predicates and constants
    detectors=[figures(FIG_CFG), figure_text_sweep, tables(TABLE_CFG), listings(LST_CFG), code_runs(CODE_CFG)],
    role=classify,                              # today's classify(), moved unchanged
    flow=FlowConfig(...),                       # full_right widths 453.3/441.3/465.3, odd-page shift 9,
                                                # gaps 14.7/16/17.3/23.3, top<70, indent 6.7, slack 9.3
    data_dir=Path(__file__).parent / "data",    # descriptions.json (hand-written, never overwritten)
)
```

- **Roles:** the role vocabulary is the set of class names used today: `body`, `epigraph`, `bullet`, `title`, `h2`, `h3`, `smallcaps`, `map-title`, `dropcap`, `covers-title`, `covers-item`, `covers-cont`, `sidebar-title`, `sidebar-text`, `continued`, `note-start`, `footnote`, `footnote-cont`, `caption`, `header`, `pagenum`, `blank`.
- **Where the assembler lives:** it moves to `modules/` and acts on these roles. The profile only maps rows to roles.
- **Smallcaps:** turning the small-size spans of a smallcaps heading to lower case is a profile text hook.
- **`--profile`** takes a name, resolved under `profiles/`, or a path. It is **required**, because no generic profile exists (decision D4).

## 6. Output, manifest and verification

```
<output>/
  *.md, illustrations/*.png          content
  figure-labels.json                  generated each run (no merging across runs)
  hyphen-decisions.json               generated each run
  manifest.json                       per file: page range, skip pages, added-material character ranges
  warnings.txt                        what is printed to stdout today
  run-info.json                       profile name, PyMuPDF version, sha256 of /usr/share/dict/words, input sha256
```

- **Added ranges:** each range is `[start, end, kind]` in the file's text. The kinds are: `title-prefix`, `title`, `figure-image`, `figure-description`, `callout-header`, `callout-lines`, `listing-image`.
- **`verify --input --output --profile`:**
  - Reads `manifest.json`.
  - Checks that the files' page ranges cover the content pages without gaps or overlaps.
  - For each file, deletes the added ranges from the real Markdown, then runs today's comparison (word multiset plus ordered diff, with moves paired).
  - In step 5 it also runs the old `ADDED` regexes once and requires the same result. This proves the manifest strips exactly what the regexes did.
- **Independent count (whole book):** a token multiset from `page.get_text("words")`, minus words inside the profile's furniture and figure-label boxes, compared with all Markdown files minus their added ranges.
  - **Correction to the plan's reasoning:** `verify.page_text` already builds its own rows, so the verifier was never sharing the converter's row or assembly code. What it shares with the converter is the rawdict load (`TEXT_FLAGS`, the `if not text: continue` filter) and the style predicates. The independent count covers the load path only, so its value is modest. It is cheap, so I still propose it.
- **Reference changes:** `compare_ref.sh` changes to "every reference file is identical and none is missing; new files are listed, not failed". That way `manifest.json`, `warnings.txt` and `run-info.json` don't break the comparison. The verifier part of the reference is regenerated once, in step 5, after you review the new report format.

## 7. CLI (`cli.py`)

```
convert --input <pdf> --output <dir> --profile <name|path>
verify  --input <pdf> --output <dir> --profile <name|path>
census  --input <pdf> --pages <a>-<b>
```

## 8. Function mapping (old → new)

| Today | New home | Change |
|---|---|---|
| `pdf.py` `Book`, `Span`, `Char`, `hexcolor` | `core/pdf.py` | + `outline`, `page_label`, span ids |
| `listings.hexrgb` | `core/geometry.py` | moved (used by 3 modules) |
| `figures._clusters` | `core/geometry.py` | moved |
| `hyphens.Resolver` | `core/hyphens.py` | the code-font test (`startswith("Courier")`) becomes a predicate argument |
| `convert.Row`, `rows_of`, `_ink_right` | `core/rows.py` | the role function and the footnote-marker/bullet predicates are passed in |
| `convert.Run`, `span_run`, `row_runs`, `runs_text`, `join_rows` | `core/rows.py` | style predicates passed in; `CTX` global → an explicit `Ctx` argument |
| `convert.classify`, `is_note_label` | profile `roles.py` | unchanged logic |
| `styles.py` (all) | profile `styles.py` | unchanged; exposed as `STYLES` |
| `sections.py` `SECTIONS` | `modules/split/outline.py` + profile `SplitConfig` | built from the outline |
| `sections.py` `PAGE_FIGURES`, `SKIP_PAGES` | profile `SplitConfig` | data |
| `convert.esc`, `render_runs`, `fence` | `modules/serialize/markdown.py` | unchanged |
| `convert.render_item`, `render_list` | `modules/serialize/markdown.py` | take `ListItem`s, not rows |
| `convert.item_text_lefts`, `rel_left` | `modules/assemble/flow.py` | column edges from `STYLES` |
| `convert.Listing`, `ListingPart` | `core/model.py` `Code` + serializer | `render()` → serializer |
| `convert.Converter.convert` region part (lines 457–519) | `core/pipeline.py` + `modules/regions/*` | detectors in profile order |
| `convert.Converter.convert` state machine (lines 385–456, 520–776) | `modules/assemble/flow.py` | emits blocks; constants from `FlowConfig`; `sec.id == "front"` → a `SplitConfig` flag (`headings_shift`) |
| `convert.Converter.footnotes` | `modules/assemble/footnotes.py` | emits `Footnote` blocks |
| `convert.Converter._listing_image` | `modules/regions/listings.py` | rendered from an `ImageRef` |
| `convert.main` | `cli.py` + `core/pipeline.py` | output-only writes |
| `figures.py` | `modules/regions/figures.py` | `IGNORE_FILLS`, `DPI`, caption regex and header band → `FIG_CFG` |
| `tables.py` | `modules/regions/tables.py` | rule/header colours, `ROW_GAP` → `TABLE_CFG` |
| `listings.py` (bars, parts, `code_lines`, `with_blank_lines`, callouts) | `modules/regions/listings.py` | `CODE_CW`, `CODE_PITCH`, `LINE_CENTRE`, bar colour → `LST_CFG` |
| `verify.tokens`, `page_text` | `core/verify.py` | `HEADER_BAND`, the page-number rule and `is_figure_text` come from the profile |
| `verify.ADDED`, `strip_added` | `core/verify.py` | kept for the one-time cross-check in step 5, then removed |
| `census.py` | `cli.py census` | flags only |

## 9. Decisions (agreed 2026-11-07)

- **D1. Hyphen log order: accepted.** Callouts are joined when their listing closes, so one of the 777 entries moves earlier in the log. From step 4 on, `compare_ref.sh` compares `hyphen-decisions.json` as a set of entries. Every other file is compared byte for byte.
- **D2. Profiles get their own history.** `profiles/` is git-ignored by this repo and is itself a local git repository.
- **D3. Unified heading escaping: measured.** No heading contains an escapable character, so no bytes change.
- **D4. `--profile` is required.** There is no default until a generic profile exists.
- **D5. Verifier reports are regenerated once.** In step 5 the user reviews one new report next to the old one. After approval, the verifier part of the reference is regenerated. The converter output reference is not touched.
