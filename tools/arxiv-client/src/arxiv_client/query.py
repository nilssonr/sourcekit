"""A small builder for arXiv ``search_query`` strings (docs/arxiv-api.md section 2.2).

    q = Q.title("attention is all you need") & Q.category("cs.LG")
    q = (Q.author("Hinton") | Q.author("LeCun")) - Q.category("q-bio.NC")
    q = Q.all("transformer") & Q.submitted_between("20230101", "20230131")

Field text with several words becomes a quoted phrase. Operators are always the
uppercase ``AND``/``OR``/``ANDNOT``. Diacritics are never altered. Free text
(``Q.from_text``, or a plain string given to the client) has no operators: its
stopwords are dropped, a trailing ``?`` or possessive ``'s`` is removed from a
word, and the remaining words are ANDed. There is
deliberately no ``lastUpdatedDate`` filter: the server silently rewrites it to
``submittedDate``. Use ``ArxivClient.scan_revisions`` to find revised papers.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import NamedTuple, Optional, Tuple, Union
from urllib.parse import quote, urlencode

from .errors import ValidationError
from .ids import parse_id

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

# Archives divided into subcategories: ``cat:cs`` matches nothing, ``cat:cs.*`` the whole archive.
ARCHIVES_WITH_SUBCATEGORIES = frozenset(
    {"astro-ph", "cond-mat", "cs", "econ", "eess", "math", "nlin", "physics", "q-bio", "q-fin", "stat"}
)
# The archives that also have papers filed under the bare name, which ``X.*`` does not match
# (docs/arxiv-api.md section 2.2: cat:astro-ph 105,380, cat:cond-mat 14,215, cat:q-bio 1,356).
_ARCHIVES_WITH_BARE_PAPERS = frozenset({"astro-ph", "cond-mat", "q-bio"})
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
# Stripped to recognise a stopword ("is," or "(the"). A kept word is sent with its
# punctuation, except what ``_clean_word`` removes.
_EDGE_PUNCTUATION = ".,;:!?()[]{}'\""
# Measured (docs/arxiv-api.md section 2.2): all:Maldacena's and all:Maldacena’s each match
# 982 papers, all:Maldacena 1,171.
_POSSESSIVES = ("'s", "\u2019s")
_NEEDS_QUOTES = re.compile(r"[\s():\[\]]")
_DATE_RE = re.compile(r"^\d{8}(?:\d{4}(?:\d{2})?)?$")

DateLike = Union[str, _dt.date, _dt.datetime]


class FreeText(NamedTuple):
    """Plain user text, split. ``terms`` are ANDed. ``dropped`` are the words left out, as
    typed. ``changed`` pairs each word that was altered, as typed, with the term sent."""

    terms: Tuple[str, ...]
    dropped: Tuple[str, ...]
    changed: Tuple[Tuple[str, str], ...] = ()


def _clean_word(word: str) -> str:
    """Remove what arXiv misreads at the end of an unquoted free-text word.

    A ``?`` among the trailing punctuation is a one-character wildcard (``ti:attention?``
    matches 2 papers, ``ti:attention`` 14,795), and a possessive narrows the match.
    """
    core = word.rstrip(_EDGE_PUNCTUATION)
    tail = word[len(core) :].replace("?", "")
    if core.lower().endswith(_POSSESSIVES):
        core = core[:-2]
    return core + tail


def parse_free_text(text: str) -> FreeText:
    """Split plain user text into the terms to search for and the stopwords to leave out.

    Words are separated by whitespace; a segment in double quotes is one term and is kept
    whole and as typed, stopwords and all (arXiv drops them from a phrase itself, and ignores
    a ``?`` inside one). An unquoted word loses a trailing ``?`` and a possessive ``'s``,
    and is reported in ``changed``. An unquoted stopword is dropped, in any case and with
    punctuation around it; so is a word with nothing left, such as a lone ``?``. An unquoted
    uppercase ``AND``, ``OR``, ``NOT`` or ``ANDNOT`` is rejected: free text has no operators,
    and sent as a word it would silently change the query.
    """
    terms = []
    dropped = []
    changed = []
    only_stopwords = True
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
        sent = _clean_word(word)
        bare = sent.strip(_EDGE_PUNCTUATION)
        if bare.lower() in STOPWORDS:
            dropped.append(word)
        elif sent != word and not bare:
            dropped.append(word)
            only_stopwords = False
        else:
            terms.append(sent)
            if sent != word:
                changed.append((word, sent))
    if not terms:
        if dropped and only_stopwords:
            raise ValidationError("search text has only stopwords, which arXiv does not index: %s" % " ".join(dropped))
        if dropped:
            raise ValidationError("search text has no word to search for: %s" % " ".join(dropped))
        raise ValidationError("empty search text")
    return FreeText(tuple(terms), tuple(dropped), tuple(changed))


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
        if name == "id":
            return cls.id(text)
        if name == "category":
            archive = _clean(text).lower()
            if archive in _ARCHIVES_WITH_BARE_PAPERS:
                return cls("cat:%s" % archive) | cls("cat:%s.*" % archive)
            if archive in ARCHIVES_WITH_SUBCATEGORIES:
                return cls("cat:%s.*" % archive)
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
        """``id:`` search. It matches the unversioned new-style ID only, and takes a trailing
        ``*`` (``1706.037*``). A versioned or old-style ID would find nothing, so it is rejected:
        fetch those by ID instead."""
        return cls(_term("id", _id_text(text)))

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
        """Plain user text: stopwords dropped, a trailing ``?`` or ``'s`` removed, the other
        words joined with AND; segments in double quotes stay phrases. See ``parse_free_text``
        for the rules.

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


def _id_text(text: str) -> str:
    try:
        aid = parse_id(text)
    except ValidationError:
        return text  # a prefix such as 1706.037*, or not an ID at all: sent as typed
    if aid.version is not None or aid.old_style:
        kind = "a versioned ID" if aid.version is not None else "an old-style ID"
        raise ValidationError(
            "an id: search matches only unversioned new-style IDs, so %s (%s) would find nothing. "
            "Fetch it by ID instead: arxiv fetch %s (client.fetch in the library)" % (kind, aid, aid)
        )
    return aid.base


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
