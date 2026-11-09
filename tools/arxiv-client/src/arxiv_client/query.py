"""A small builder for arXiv ``search_query`` strings (docs/arxiv-api.md section 2.2).

    q = Q.title("attention is all you need") & Q.category("cs.LG")
    q = (Q.author("Hinton") | Q.author("LeCun")) - Q.category("q-bio.NC")
    q = Q.all("transformer") & Q.submitted_between("20230101", "20230131")

Field text with several words becomes a quoted phrase. Operators are always the
uppercase ``AND``/``OR``/``ANDNOT``. Diacritics are never altered. Free text
(``Q.from_text``, or a plain string given to the client) has no operators: its
stopwords are dropped and the remaining words are ANDed. There is
deliberately no ``lastUpdatedDate`` filter: the server silently rewrites it to
``submittedDate``. Use ``ArxivClient.scan_revisions`` to find revised papers.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import NamedTuple, Optional, Tuple, Union
from urllib.parse import quote, urlencode

from .errors import ValidationError

FIELDS = {
    "title": "ti",
    "author": "au",
    "abstract": "abs",
    "comment": "co",
    "journal_ref": "jr",
    "category": "cat",
    "report_number": "rn",
    "id": "id",
    "all": "all",
}

_OPERATOR_WORDS = {"AND", "OR", "NOT", "ANDNOT", "TO"}
# Words arXiv does not index in titles and abstracts (docs/arxiv-api.md section 2.2). Each one
# was measured: ``ti:<word>`` matches 0 papers, so ANDing it into a query returns nothing.
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "if",
        "in",
        "into",
        "is",
        "it",
        "no",
        "not",
        "of",
        "on",
        "or",
        "such",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "was",
        "will",
        "with",
    }
)
_FREE_TEXT_OPERATORS = ("AND", "OR", "NOT", "ANDNOT")
# Stripped only to recognise a stopword ("is," or "(the"); a kept word is sent as typed.
_EDGE_PUNCTUATION = ".,;:!?()[]{}'\""
_NEEDS_QUOTES = re.compile(r"[\s():\[\]]")
_DATE_RE = re.compile(r"^\d{8}(?:\d{4}(?:\d{2})?)?$")

DateLike = Union[str, _dt.date, _dt.datetime]


class FreeText(NamedTuple):
    """Plain user text, split: ``terms`` are ANDed, ``dropped`` are the stopwords left out."""

    terms: Tuple[str, ...]
    dropped: Tuple[str, ...]


def parse_free_text(text: str) -> FreeText:
    """Split plain user text into the terms to search for and the stopwords to leave out.

    Words are separated by whitespace; a segment in double quotes is one term and is kept
    whole, stopwords and all (arXiv drops them from a phrase itself). An unquoted stopword is
    dropped, in any case and with punctuation around it. An unquoted uppercase ``AND``,
    ``OR``, ``NOT`` or ``ANDNOT`` is rejected: free text has no operators, and sent as a
    word it would silently change the query.
    """
    terms = []
    dropped = []
    for m in re.finditer(r'"([^"]*)"|(\S+)', text or ""):
        if m.group(1) is not None:
            phrase = m.group(1).strip()
            if phrase:
                terms.append(phrase)
            continue
        word = m.group(2)
        if word in _FREE_TEXT_OPERATORS:
            raise ValidationError(
                "%s is not an operator in free text. For a boolean query use --raw (Q.raw in the library); "
                'to search for the word itself, put it in double quotes: "%s"' % (word, word)
            )
        if word.strip(_EDGE_PUNCTUATION).lower() in STOPWORDS:
            dropped.append(word)
        else:
            terms.append(word)
    if not terms:
        if dropped:
            raise ValidationError("search text has only stopwords, which arXiv does not index: %s" % " ".join(dropped))
        raise ValidationError("empty search text")
    return FreeText(tuple(terms), tuple(dropped))


class Q:
    """An immutable search_query expression. Combine with ``&`` (AND), ``|`` (OR), ``-`` (ANDNOT)."""

    __slots__ = ("_op", "expr")

    def __init__(self, expr: str, _op: Optional[str] = None) -> None:
        self.expr = expr
        self._op = _op  # None for a single term; 'AND'/'OR'/'ANDNOT'/'RAW' for compound

    # ----- leaf constructors -------------------------------------------------
    @classmethod
    def field(cls, name: str, text: str) -> "Q":
        if name not in FIELDS:
            raise ValidationError("unknown field %r; use one of %s" % (name, ", ".join(FIELDS)))
        return cls(_term(FIELDS[name], text))

    @classmethod
    def phrase(cls, name: str, text: str) -> "Q":
        """Always quote ``text`` as a phrase, even a single word."""
        if name not in FIELDS:
            raise ValidationError("unknown field %r" % name)
        return cls('%s:"%s"' % (FIELDS[name], _clean(text)))

    @classmethod
    def title(cls, text: str) -> "Q":
        return cls.field("title", text)

    @classmethod
    def author(cls, text: str) -> "Q":
        return cls.field("author", text)

    @classmethod
    def abstract(cls, text: str) -> "Q":
        return cls.field("abstract", text)

    @classmethod
    def comment(cls, text: str) -> "Q":
        return cls.field("comment", text)

    @classmethod
    def journal_ref(cls, text: str) -> "Q":
        return cls.field("journal_ref", text)

    @classmethod
    def category(cls, text: str) -> "Q":
        return cls.field("category", text)

    @classmethod
    def report_number(cls, text: str) -> "Q":
        return cls.field("report_number", text)

    @classmethod
    def id(cls, text: str) -> "Q":
        return cls.field("id", text)

    @classmethod
    def all(cls, text: str) -> "Q":
        return cls.field("all", text)

    @classmethod
    def submitted_between(cls, start: DateLike, end: DateLike) -> "Q":
        """``submittedDate:[A TO B]``. Dates are GMT; a date-only value covers the whole day."""
        a, b = _format_date(start), _format_date(end)
        if a.ljust(14, "0") > b.ljust(14, "9"):
            raise ValidationError("date range start %s is after end %s" % (a, b))
        return cls("submittedDate:[%s TO %s]" % (a, b))

    @classmethod
    def raw(cls, expr: str) -> "Q":
        """Escape hatch: use ``expr`` verbatim. It is parenthesised when combined."""
        if not expr or not expr.strip():
            raise ValidationError("empty raw query")
        return cls(expr.strip(), "RAW")

    @classmethod
    def from_text(cls, text: str) -> "Q":
        """Plain user text: stopwords dropped, the other words joined with AND; segments in
        double quotes stay phrases. See ``parse_free_text`` for the rules.

        ``transformers for protein folding`` -> ``all:transformers AND all:protein AND all:folding``.
        """
        parts = parse_free_text(text).terms
        result = cls.all(parts[0])
        for p in parts[1:]:
            result = result & cls.all(p)
        return result

    # ----- operators -----------------------------------------------------------
    def _combine(self, other: "Q", op: str) -> "Q":
        if not isinstance(other, Q):
            return NotImplemented  # type: ignore[return-value]
        associative = op in ("AND", "OR")
        left = self.expr if (self._op is None or (associative and self._op == op)) else "(%s)" % self.expr
        right = other.expr if (other._op is None or (associative and other._op == op)) else "(%s)" % other.expr
        return Q("%s %s %s" % (left, op, right), op)

    def __and__(self, other: "Q") -> "Q":
        return self._combine(other, "AND")

    def __or__(self, other: "Q") -> "Q":
        return self._combine(other, "OR")

    def __sub__(self, other: "Q") -> "Q":
        return self._combine(other, "ANDNOT")

    def __str__(self) -> str:
        return self.expr

    def __repr__(self) -> str:
        return "Q(%r)" % self.expr

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Q) and other.expr == self.expr

    def __hash__(self) -> int:
        return hash(self.expr)


def _clean(text: str) -> str:
    if not isinstance(text, str):
        raise ValidationError("query text must be a string")
    # The syntax has no escaping, so embedded double quotes are dropped.
    # Whitespace is normalised; diacritics and case are left alone.
    cleaned = " ".join(text.replace('"', " ").split())
    if not cleaned:
        raise ValidationError("empty query text")
    return cleaned


def _term(prefix: str, text: str) -> str:
    t = _clean(text)
    if " " in t or _NEEDS_QUOTES.search(t) or t in _OPERATOR_WORDS:
        if "*" in t:
            raise ValidationError("wildcard * is only allowed at the end of a single word: %r" % text)
        return '%s:"%s"' % (prefix, t)
    if "*" in t[:-1]:
        raise ValidationError("wildcard * is only allowed at the end of a word: %r" % text)
    if t == "*":
        raise ValidationError("a bare * is not a valid term")
    return "%s:%s" % (prefix, t)


def _format_date(value: DateLike) -> str:
    if isinstance(value, _dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(_dt.timezone.utc)
        return value.strftime("%Y%m%d%H%M%S")
    if isinstance(value, _dt.date):
        return value.strftime("%Y%m%d")
    if isinstance(value, str) and _DATE_RE.match(value.strip()):
        text = value.strip()
        # arXiv answers a date that is not on the calendar with HTTP 500 (docs/arxiv-api.md section 2.2).
        parts = [int(text[:4]), int(text[4:6]), int(text[6:8])] + [int(text[i : i + 2]) for i in range(8, len(text), 2)]
        try:
            _dt.datetime(*parts)
        except ValueError as e:
            raise ValidationError("%s is not a calendar date and time: %s" % (text, e)) from None
        return text
    raise ValidationError("dates must be date/datetime objects or YYYYMMDD[HHMM[SS]] strings, got %r" % (value,))


def to_search_query(query: Union[str, Q]) -> str:
    """A ``Q`` renders as-is; a plain string goes through ``Q.from_text``."""
    if isinstance(query, Q):
        return query.expr
    if isinstance(query, str):
        return Q.from_text(query).expr
    raise ValidationError("query must be a str or Q, got %r" % type(query).__name__)


def encode_params(params: dict) -> str:
    """URL-encode with %20 for spaces (never ``+``), so ``[A TO B]`` never becomes ``%2B``."""
    return urlencode(params, quote_via=quote, safe=":,")
