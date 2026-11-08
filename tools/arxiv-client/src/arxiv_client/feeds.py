"""Parsing of the RSS 2.0 / Atom announcement feeds (docs/arxiv-api.md section 4).

The item structure here follows arXiv's docs and the arxiv-feed source; it was not
observed live (the doc's probe ran on a Saturday, when every feed is empty).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import List, Optional, Sequence, Tuple

from .atom import ARXIV, ATOM, clean_abstract, dedupe, normalize_space, parse_xml, text_of
from .errors import MalformedResponseError, ValidationError
from .ids import split_oai_id
from .models import Feed, FeedItem

DC = "http://purl.org/dc/elements/1.1/"
_A = "{%s}" % ATOM
_AX = "{%s}" % ARXIV
_DC = "{%s}" % DC

# Spec feedQuery pattern, one part at a time.
_CATEGORY_RE = re.compile(r"^[A-Za-z-]+(\.[A-Za-z-]+)?$")
_DESCRIPTION_PREFIX = re.compile(r"^\s*arXiv:\S+\s+Announce Type:\s*\S+\s*(?:Abstract:\s*)?", re.IGNORECASE)


def validate_categories(categories: Sequence[str]) -> List[str]:
    if isinstance(categories, str):
        categories = [c for c in categories.split("+")]
    cats = [c.strip() for c in categories if c and c.strip()]
    if not cats:
        raise ValidationError("at least one feed category is required")
    bad = [c for c in cats if not _CATEGORY_RE.match(c)]
    if bad:
        raise ValidationError("invalid feed categories: %s" % ", ".join(bad))
    return cats


def split_creators(creator: Optional[str]) -> Tuple[str, ...]:
    """Split the single comma-joined ``dc:creator`` string; drop parenthesised affiliations."""
    if not creator:
        return ()
    names: List[str] = []
    depth = 0
    current = []
    for ch in creator:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            names.append("".join(current))
            current = []
        else:
            current.append(ch)
    names.append("".join(current))
    result = []
    for raw in names:
        name = re.sub(r"\([^()]*\)", " ", raw)
        name = re.sub(r"\([^()]*\)", " ", name)  # one level of nesting
        name = " ".join(name.split())
        if name:
            result.append(name)
    return tuple(result)


def _strip_description(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    return clean_abstract(_DESCRIPTION_PREFIX.sub("", text, count=1))


def _ids_from(*candidates: Optional[str]) -> Tuple[str, Optional[int]]:
    for c in candidates:
        if c:
            try:
                return split_oai_id(c)
            except ValueError:
                continue
    raise ValueError("item has no oai:arXiv.org identifier")


def _common(el: ET.Element) -> dict:
    return {
        "announce_type": text_of(el.find(_AX + "announce_type")),
        "authors": split_creators(text_of(el.find(_DC + "creator"))),
        "rights": text_of(el.find(_DC + "rights")),
        # Feeds use arxiv:DOI / arxiv:journal_reference; accept the query-API names too.
        "dois": dedupe(text_of(d) or "" for d in el.findall(_AX + "DOI") + el.findall(_AX + "doi")),
        "journal_ref": normalize_space(
            text_of(el.find(_AX + "journal_reference")) or text_of(el.find(_AX + "journal_ref"))
        ),
    }


def parse_rss(body: bytes) -> Feed:
    root = parse_xml(body)
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise MalformedResponseError("expected an RSS 2.0 feed, got <%s>" % root.tag)
    items: List[FeedItem] = []
    for item in channel.findall("item"):
        try:
            base, version = _ids_from(text_of(item.find("guid")))
        except ValueError:
            continue
        items.append(
            FeedItem(
                id=base,
                version=version,
                title=normalize_space(text_of(item.find("title"))),
                abstract=_strip_description(item.findtext("description")),
                categories=dedupe(text_of(c) or "" for c in item.findall("category")),
                link=text_of(item.find("link")),
                **_common(item),
            )
        )
    return Feed(items=items, message=normalize_space(text_of(channel.find("description"))))


def parse_atom_feed(body: bytes) -> Feed:
    root = parse_xml(body)
    if root.tag != _A + "feed":
        raise MalformedResponseError("expected an Atom feed, got <%s>" % root.tag)
    items: List[FeedItem] = []
    for entry in root.findall(_A + "entry"):
        try:
            base, version = _ids_from(text_of(entry.find(_A + "id")))
        except ValueError:
            continue
        link = None
        for lnk in entry.findall(_A + "link"):
            if lnk.get("rel") in (None, "alternate"):
                link = lnk.get("href")
                break
        items.append(
            FeedItem(
                id=base,
                version=version,
                title=normalize_space(text_of(entry.find(_A + "title"))),
                abstract=_strip_description(entry.findtext(_A + "summary")),
                categories=dedupe((c.get("term") or "").strip() for c in entry.findall(_A + "category")),
                link=link,
                **_common(entry),
            )
        )
    return Feed(items=items, message=normalize_space(text_of(root.find(_A + "subtitle"))))
