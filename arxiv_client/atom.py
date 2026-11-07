"""Parsing of query-API Atom feeds (docs/arxiv-api.md sections 2.4 and 2.5)."""

from __future__ import annotations

import datetime as _dt
import re
import xml.etree.ElementTree as ET
from typing import List, Optional, Tuple

from .errors import MalformedResponseError, ServerError
from .ids import split_entry_id
from .models import Author, Paper, SearchPage

ATOM = "http://www.w3.org/2005/Atom"
OPENSEARCH = "http://a9.com/-/spec/opensearch/1.1/"
ARXIV = "http://arxiv.org/schemas/atom"

_A = "{%s}" % ATOM
_OS = "{%s}" % OPENSEARCH
_AX = "{%s}" % ARXIV

ERROR_ID_PREFIXES = ("https://arxiv.org/api/errors", "https://arxiv.org/help/api/user-manual")

_SENTENCE_END = re.compile(r"""[.!?:;]["')\]]?$""")


def parse_xml(body: bytes) -> ET.Element:
    try:
        return ET.fromstring(body)
    except ET.ParseError as e:
        raise MalformedResponseError("could not parse XML response: %s" % e) from None


def text_of(el: Optional[ET.Element]) -> Optional[str]:
    if el is None or el.text is None:
        return None
    t = el.text.strip()
    return t or None


def normalize_space(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    t = " ".join(text.split())
    return t or None


def clean_abstract(text: Optional[str]) -> Optional[str]:
    """Join hard-wrapped lines, keep paragraph breaks.

    A new paragraph starts at a blank line, or at an indented line whose previous line
    ends a sentence. Every other line break is a hard wrap and becomes one space. Spaces
    inside a line are collapsed; the pre-2010 two-space lead is removed.

    The doc's rule ("newline followed by indentation is a paragraph") collides with the
    ``\\n  `` hard-wrap quirk, which looks identical; the sentence-end check is our
    heuristic for telling them apart. It will misread a hard wrap that happens to fall
    right after a full stop.
    """
    if text is None:
        return None
    paragraphs: List[List[str]] = []
    current: List[str] = []
    prev_ends_sentence = False
    for raw in text.replace("\r\n", "\n").split("\n"):
        stripped = " ".join(raw.split())
        if not stripped:
            if current:
                paragraphs.append(current)
                current = []
            prev_ends_sentence = False
            continue
        indented = raw[:1] in (" ", "\t")
        if current and indented and prev_ends_sentence:
            paragraphs.append(current)
            current = []
        current.append(stripped)
        prev_ends_sentence = bool(_SENTENCE_END.search(stripped))
    if current:
        paragraphs.append(current)
    result = "\n".join(" ".join(p) for p in paragraphs)
    return result or None


def parse_datetime(text: Optional[str]) -> Optional[_dt.datetime]:
    if not text:
        return None
    t = text.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        value = _dt.datetime.fromisoformat(t)
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=_dt.timezone.utc)
    return value.astimezone(_dt.timezone.utc)


def _int_or_none(text: Optional[str]) -> Optional[int]:
    try:
        return int(text) if text is not None else None
    except ValueError:
        return None


def dedupe(values) -> Tuple[str, ...]:
    seen = []
    for v in values:
        if v and v not in seen:
            seen.append(v)
    return tuple(seen)


def find_error_entry(root: ET.Element) -> Optional[Tuple[str, str]]:
    """Return (entry id, summary) if this is an Atom error feed, else None."""
    for entry in root.findall(_A + "entry"):
        entry_id = text_of(entry.find(_A + "id")) or ""
        if entry_id.startswith(ERROR_ID_PREFIXES):
            summary = normalize_space(text_of(entry.find(_A + "summary"))) or "unknown arXiv error"
            return entry_id, summary
    return None


def error_message(body: bytes) -> Optional[str]:
    """Best-effort human message from an error body: an Atom error entry, an RSS channel
    description, or an Atom feed subtitle (the feeds' 400 format)."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        text = body.decode("utf-8", "replace").strip()
        return text[:300] or None
    found = find_error_entry(root)
    if found:
        return found[1]
    desc = root.find("channel/description")
    if desc is not None and text_of(desc):
        return normalize_space(text_of(desc))
    subtitle = root.find(_A + "subtitle")
    if subtitle is not None and text_of(subtitle):
        return normalize_space(text_of(subtitle))
    return None


def parse_entry(entry: ET.Element) -> Paper:
    entry_id = text_of(entry.find(_A + "id"))
    if not entry_id:
        raise ValueError("entry has no id")
    base, version = split_entry_id(entry_id)

    abs_url = pdf_url = None
    for link in entry.findall(_A + "link"):
        rel, title, href = link.get("rel"), link.get("title"), link.get("href")
        if rel == "alternate" and abs_url is None:
            abs_url = href
        elif title == "pdf" and pdf_url is None:
            pdf_url = href

    authors = []
    for a in entry.findall(_A + "author"):
        name = text_of(a.find(_A + "name"))
        if not name:
            continue
        affs = tuple(t for t in (normalize_space(text_of(x)) for x in a.findall(_AX + "affiliation")) if t)
        authors.append(Author(name.strip(), affs))

    categories = dedupe(c.get("term", "").strip() for c in entry.findall(_A + "category"))
    primary = entry.find(_AX + "primary_category")
    primary_term = None
    if primary is not None:
        # arxiv:primary_category has only @term; no @scheme (doc section 2.4).
        primary_term = (primary.get("term") or "").strip() or None

    dois = dedupe(
        (text_of(d) or "")
        for d in entry.findall(_AX + "doi") + entry.findall(_AX + "DOI")
    )
    journal_ref = normalize_space(
        text_of(entry.find(_AX + "journal_ref")) or text_of(entry.find(_AX + "journal_reference"))
    )

    return Paper(
        id=base,
        version=version,
        title=normalize_space(text_of(entry.find(_A + "title"))),
        summary=clean_abstract(entry.findtext(_A + "summary")),
        authors=tuple(authors),
        categories=categories,
        primary_category=primary_term,
        published=parse_datetime(text_of(entry.find(_A + "published"))),
        updated=parse_datetime(text_of(entry.find(_A + "updated"))),
        comment=normalize_space(text_of(entry.find(_AX + "comment"))),
        journal_ref=journal_ref,
        dois=dois,
        abs_url=abs_url or "https://arxiv.org/abs/%s" % (base if version is None else "%sv%d" % (base, version)),
        pdf_url=pdf_url,
    )


def parse_query_feed(body: bytes) -> SearchPage:
    """Parse a 200 query-API response. Raises ServerError if it is an error feed."""
    root = parse_xml(body)
    if root.tag != _A + "feed":
        raise MalformedResponseError("expected an Atom feed, got <%s>" % root.tag)
    err = find_error_entry(root)
    if err:
        raise ServerError("arXiv returned an error feed: %s (%s)" % (err[1], err[0]))
    entries = root.findall(_A + "entry")
    papers: List[Paper] = []
    skipped = 0
    for entry in entries:
        try:
            papers.append(parse_entry(entry))
        except ValueError:
            skipped += 1
    # opensearch:itemsPerPage is deliberately ignored: it echoes the requested max_results.
    return SearchPage(
        papers=papers,
        raw_entry_count=len(entries),
        total_results=_int_or_none(text_of(root.find(_OS + "totalResults"))),
        start=_int_or_none(text_of(root.find(_OS + "startIndex"))),
        skipped=skipped,
    )
