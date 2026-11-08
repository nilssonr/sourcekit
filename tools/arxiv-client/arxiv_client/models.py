"""Data models returned by the client."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


def _iso(value: Optional[_dt.datetime]) -> Optional[str]:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


@dataclass(frozen=True)
class Author:
    name: str
    affiliations: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "affiliations": list(self.affiliations)}


@dataclass(frozen=True)
class Paper:
    """One query-API entry.

    ``id`` is the base ID without version. ``published`` is the v1 date; ``updated`` is
    the date of the returned version. ``requested_version`` is set only when arXiv's
    old-style-ID bug forced an unversioned retry: you asked for v``requested_version``
    and got v``version``.
    """

    id: str
    version: Optional[int]
    title: Optional[str]
    summary: Optional[str]
    authors: Tuple[Author, ...]
    categories: Tuple[str, ...]
    primary_category: Optional[str]
    published: Optional[_dt.datetime]
    updated: Optional[_dt.datetime]
    comment: Optional[str]
    journal_ref: Optional[str]
    dois: Tuple[str, ...]
    abs_url: Optional[str]
    pdf_url: Optional[str]
    requested_version: Optional[int] = None

    @property
    def versioned_id(self) -> str:
        return self.id if self.version is None else "%sv%d" % (self.id, self.version)

    @property
    def version_mismatch(self) -> bool:
        return self.requested_version is not None and self.requested_version != self.version

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "version": self.version,
            "versioned_id": self.versioned_id,
            "title": self.title,
            "summary": self.summary,
            "authors": [a.to_dict() for a in self.authors],
            "categories": list(self.categories),
            "primary_category": self.primary_category,
            "published": _iso(self.published),
            "updated": _iso(self.updated),
            "comment": self.comment,
            "journal_ref": self.journal_ref,
            "dois": list(self.dois),
            "abs_url": self.abs_url,
            "pdf_url": self.pdf_url,
        }
        if self.requested_version is not None:
            d["requested_version"] = self.requested_version
            d["note"] = "requested v%d, served v%s" % (self.requested_version, self.version)
        return d


@dataclass
class SearchPage:
    """One page of query-API results.

    ``raw_entry_count`` counts every ``<entry>`` element, including ones that failed to
    parse (``skipped``); page by it, not by ``len(papers)``.
    """

    papers: List[Paper]
    raw_entry_count: int
    total_results: Optional[int]
    start: Optional[int]
    skipped: int = 0


@dataclass
class FetchResult:
    """Result of fetching by ID. ``papers`` is keyed by the ID string you passed in."""

    papers: Dict[str, Paper]
    not_found: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "papers": {k: v.to_dict() for k, v in self.papers.items()},
            "not_found": list(self.not_found),
        }


@dataclass(frozen=True)
class FeedItem:
    id: str
    version: Optional[int]
    title: Optional[str]
    abstract: Optional[str]
    authors: Tuple[str, ...]
    categories: Tuple[str, ...]
    announce_type: Optional[str]
    dois: Tuple[str, ...]
    journal_ref: Optional[str]
    rights: Optional[str]
    link: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "title": self.title,
            "abstract": self.abstract,
            "authors": list(self.authors),
            "categories": list(self.categories),
            "announce_type": self.announce_type,
            "dois": list(self.dois),
            "journal_ref": self.journal_ref,
            "rights": self.rights,
            "link": self.link,
        }


@dataclass
class Feed:
    """Today's announcements. Empty (not an error) on weekends and holidays."""

    items: List[FeedItem]
    message: Optional[str] = None

    @property
    def empty(self) -> bool:
        return not self.items

    def to_dict(self) -> Dict[str, Any]:
        return {
            "empty": self.empty,
            "count": len(self.items),
            "message": self.message,
            "items": [i.to_dict() for i in self.items],
        }


@dataclass(frozen=True)
class PdfResult:
    path: str
    id: str
    version: Optional[int]
    bytes: int
    skipped: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "id": self.id,
            "version": self.version,
            "bytes": self.bytes,
            "skipped": self.skipped,
        }
