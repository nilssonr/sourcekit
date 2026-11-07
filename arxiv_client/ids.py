"""arXiv identifier validation and splitting (docs/arxiv-api.md section 5)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

from .errors import ValidationError

# Regexes copied from docs/arxiv-api.md section 5.
NEW_RE = re.compile(
    r"^(?:arXiv:)?(?P<id>(?P<yymm>\d\d(?:0[1-9]|1[0-2]))\.(?P<num>\d{4,5}))"
    r"(?:v(?P<version>[1-9]\d*))?$"
)
OLD_RE = re.compile(
    r"^(?:arXiv:)?(?P<id>(?P<archive>[a-z]+(?:-[a-z]+)*)(?:\.(?P<subj>[A-Z]{2}))?"
    r"/(?P<yymm>\d\d(?:0[1-9]|1[0-2]))(?P<num>\d{3}))(?:v(?P<version>[1-9]\d*))?$"
)

_ENTRY_ID_RE = re.compile(r"^https?://arxiv\.org/abs/(?P<id>.+?)(?:v(?P<version>\d+))?$")
_OAI_ID_RE = re.compile(r"^oai:arXiv\.org:(?P<id>.+?)(?:v(?P<version>\d+))?$")


@dataclass(frozen=True)
class ArxivId:
    """A validated, canonical arXiv ID. ``base`` has no version and no old-style subject class."""

    base: str
    version: Optional[int]
    old_style: bool

    def __str__(self) -> str:
        return self.base if self.version is None else "%sv%d" % (self.base, self.version)

    def unversioned(self) -> "ArxivId":
        return ArxivId(self.base, None, self.old_style)


def _new_style_year_month(yymm: str) -> int:
    yy, mm = int(yymm[:2]), int(yymm[2:])
    # Doc section 5: YY 07-99 means 20YY, and 00-06 means 21YY.
    year = 2000 + yy if yy >= 7 else 2100 + yy
    return year * 100 + mm


def _old_style_year_month(yymm: str) -> int:
    yy, mm = int(yymm[:2]), int(yymm[2:])
    year = 1900 + yy if yy >= 91 else 2000 + yy
    return year * 100 + mm


def parse_id(text: str) -> ArxivId:
    """Validate and canonicalise an arXiv ID, raising ValidationError if it is malformed.

    Accepts an optional ``arXiv:`` prefix and an optional ``vN`` suffix. Drops the
    old-style ``.SC`` subject class (``math.CA/0611800`` becomes ``math/0611800``).
    """
    if not isinstance(text, str):
        raise ValidationError("arXiv ID must be a string, got %r" % (text,))
    s = text.strip()
    m = NEW_RE.match(s)
    if m:
        ym = _new_style_year_month(m.group("yymm"))
        num = m.group("num")
        if ym < 200704:
            raise ValidationError("invalid arXiv ID %r: new-style IDs start at 0704" % text)
        expected_digits = 4 if ym <= 201412 else 5
        if len(num) != expected_digits:
            raise ValidationError(
                "invalid arXiv ID %r: %s IDs have %d-digit numbers"
                % (text, m.group("yymm"), expected_digits)
            )
        if int(num) == 0:
            raise ValidationError("invalid arXiv ID %r: sequence number is zero" % text)
        version = m.group("version")
        return ArxivId(m.group("id"), int(version) if version else None, False)
    m = OLD_RE.match(s)
    if m:
        ym = _old_style_year_month(m.group("yymm"))
        if not (199107 <= ym <= 200703):
            raise ValidationError("invalid arXiv ID %r: old-style IDs run from 9107 to 0703" % text)
        if int(m.group("num")) == 0:
            raise ValidationError("invalid arXiv ID %r: sequence number is zero" % text)
        base = "%s/%s%s" % (m.group("archive"), m.group("yymm"), m.group("num"))
        version = m.group("version")
        return ArxivId(base, int(version) if version else None, True)
    raise ValidationError("invalid arXiv ID %r" % text)


def is_valid_id(text: str) -> bool:
    try:
        parse_id(text)
    except ValidationError:
        return False
    return True


def split_entry_id(url: str) -> Tuple[str, Optional[int]]:
    """Split a query-API entry ID ``http://arxiv.org/abs/<id>v<N>`` into (base, version)."""
    m = _ENTRY_ID_RE.match(url.strip())
    if not m:
        raise ValueError("not an arXiv abs URL: %r" % url)
    v = m.group("version")
    return m.group("id"), int(v) if v else None


def split_oai_id(text: str) -> Tuple[str, Optional[int]]:
    """Split a feed ID ``oai:arXiv.org:<id>v<N>`` into (base, version)."""
    m = _OAI_ID_RE.match(text.strip())
    if not m:
        raise ValueError("not an arXiv OAI identifier: %r" % text)
    v = m.group("version")
    return m.group("id"), int(v) if v else None
