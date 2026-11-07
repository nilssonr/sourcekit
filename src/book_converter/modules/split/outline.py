"""Sections from the PDF outline: each matching bookmark starts a section that runs to the page
before the next one. Pages before the first section can form a front-matter section."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from ...core.pdf import Book
from ...core.sections import Section


@dataclass(frozen=True)
class SectionRule:
    """Bookmarks at `level` whose title matches `pattern` start a section; `make` builds its
    (id, file, title) from the match. A title of "" means the heading is taken from the page."""
    level: int
    pattern: str
    make: Callable[[re.Match[str]], tuple[str, str, str]]


@dataclass(frozen=True)
class FrontMatter:
    id: str
    file: str
    title: str
    first_page: int    # the front matter runs from here to the page before the first section


@dataclass(frozen=True)
class SplitConfig:
    rules: list[SectionRule]
    end: tuple[int, str]          # (level, exact title) of the bookmark where content ends, e.g. the index
    front: FrontMatter | None = None


def slug(text: str) -> str:
    """"What is a unit test?" -> "what-is-a-unit-test"."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def sections(book: Book, cfg: SplitConfig) -> dict[str, Section]:
    outline = book.outline()
    end = next((e.page for e in outline if (e.level, e.title) == cfg.end), None)
    if end is None:
        raise ValueError(f"outline has no level-{cfg.end[0]} bookmark titled {cfg.end[1]!r}")
    starts: list[tuple[int, str, str, str]] = []
    for e in outline:
        if e.page >= end:
            continue
        for rule in cfg.rules:
            m = re.fullmatch(rule.pattern, e.title) if e.level == rule.level else None
            if m:
                starts.append((e.page, *rule.make(m)))
                break
    if not starts:
        raise ValueError("no outline bookmark matches the section rules")
    pages = [p for p, *_ in starts]
    if pages != sorted(pages) or len(set(pages)) != len(pages):
        raise ValueError(f"section start pages are not strictly increasing: {pages}")
    out: dict[str, Section] = {}
    if cfg.front:
        f = cfg.front
        out[f.id] = Section(f.id, f.file, (f.first_page, starts[0][0] - 1), f.title)
    for i, (page, sid, file, title) in enumerate(starts):
        last = starts[i + 1][0] - 1 if i + 1 < len(starts) else end - 1
        if sid in out:
            raise ValueError(f"duplicate section id {sid!r}")
        out[sid] = Section(sid, file, (page, last), title)
    return out
