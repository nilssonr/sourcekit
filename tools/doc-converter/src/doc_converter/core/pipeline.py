"""Run a conversion: sections from the outline, assemble each into blocks, write Markdown and images.

Everything is written under the output directory; the profile's data directory is only read.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from ..modules.assemble.flow import Assembler
from ..modules.regions import figures as figmod
from ..modules.serialize.markdown import Markdown
from ..modules.split import outline
from .hyphens import Resolver
from .model import Code, Figure, ImageRef, Note, SectionDoc, Sidebar
from .pdf import Book
from .profile import Profile
from .rows import Text


@dataclass
class Result:
    written: list[tuple[str, int]] = field(default_factory=list)   # (file, number of blocks)
    warnings: list[str] = field(default_factory=list)
    resolver: Resolver | None = None


def _images(doc: SectionDoc) -> list[ImageRef]:
    out: list[ImageRef] = []

    def walk(blocks) -> None:
        for b in blocks:
            if isinstance(b, Figure):
                out.append(b.image)
            elif isinstance(b, Code):
                out.extend(b.images)
            elif isinstance(b, Sidebar):
                walk(b.children)
            elif isinstance(b, Note):
                walk(b.continued)

    walk(doc.blocks)
    return out


def _render(book: Book, prof: Profile, img: ImageRef, dest: Path) -> None:
    if img.kind == "figure":
        figmod.render(book, img.page, img.box, prof.figures.is_figure_text, str(dest), img.rotate)
    else:
        book.page(img.page).get_pixmap(clip=img.box, dpi=figmod.DPI).save(dest)


def section_ids(book: Book, prof: Profile) -> list[str]:
    return list(outline.sections(book, prof.split))


def convert(book: Book, prof: Profile, out: Path, wanted: list[str] | None = None,
            dict_path: str = Resolver.DICT_PATH) -> Result:
    """Convert ``wanted`` sections (default: all) into ``out``. ``dict_path`` is the word list the
    hyphen resolver falls back on when the book itself has no answer."""
    res = Result()
    sections = outline.sections(book, prof.split)
    wanted = wanted or list(sections)
    (out / "illustrations").mkdir(parents=True, exist_ok=True)
    # whole book, incl. figure labels and index
    res.resolver = Resolver(book.all_spans(1, book.page_count), prof.rows.is_code_font, dict_path)
    text = Text(prof.rows, res.resolver)
    dpath = prof.data_dir / "descriptions.json"
    desc = json.loads(dpath.read_text(encoding="utf-8")) if dpath.exists() else {}
    asm = Assembler(book, prof.flow, text, desc, res.warnings.append)
    md = Markdown(prof.markdown)
    files = []
    for sid in wanted:
        sec = sections[sid]
        doc = asm.section(sec)
        text_md, ranges = md.section(doc)
        (out / sec.file).write_text(text_md, encoding="utf-8")
        p0, p1 = sec.pages
        files.append({"file": sec.file, "pages": [p0, p1],
                      "skip_pages": sorted(p for p in prof.skip_pages if p0 <= p <= p1),
                      "added": [list(r) for r in ranges]})
        for img in _images(doc):
            _render(book, prof, img, out / "illustrations" / img.file)
        res.written.append((sec.file, len(doc.blocks) + len(doc.footnotes)))
    # generated fresh on every run, never merged with an earlier run
    labels = asm.figure_labels
    (out / "figure-labels.json").write_text(
        json.dumps(dict(sorted(labels.items(), key=lambda kv: [int(x) if x.isdigit() else -1
                                                                for x in re.split(r"[.-]", kv[0])])),
                   indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "hyphen-decisions.json").write_text(json.dumps(res.resolver.log_dicts(), indent=1), encoding="utf-8")
    # added ranges are [start, end, kind] in characters (not bytes) of the file's text
    (out / "manifest.json").write_text(json.dumps({"profile": prof.name, "files": files}, indent=1), encoding="utf-8")
    (out / "warnings.txt").write_text("".join(w + "\n" for w in res.warnings), encoding="utf-8")
    (out / "run-info.json").write_text(json.dumps(run_info(book, prof, dict_path), indent=1), encoding="utf-8")
    return res


def _sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_info(book: Book, prof: Profile, dict_path: str = Resolver.DICT_PATH) -> dict:
    """Inputs besides the code that decide the output."""
    return {"profile": prof.name, "input_sha256": _sha256(book.path), "pymupdf": pymupdf.__version__,
            "mupdf": pymupdf.VersionFitz, "dictionary": dict_path, "dictionary_sha256": _sha256(dict_path)}
