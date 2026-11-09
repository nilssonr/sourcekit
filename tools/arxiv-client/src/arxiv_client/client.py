"""The arXiv client: search, fetch by ID, revision scan, feeds, PDF download."""

from __future__ import annotations

import dataclasses
import os
import re
import time
from typing import Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple, Union

from .atom import parse_query_feed
from .errors import ArxivError, LocalFileError, NotFoundError, ServerError, ValidationError
from .feeds import parse_atom_feed, parse_rss, validate_categories
from .ids import ArxivId, parse_id
from .models import Feed, FetchResult, Paper, PdfResult, SearchPage
from .pacing import BlockStore, MemoryGate, Pacer
from .query import Q, encode_params, to_search_query
from .transport import (
    DEFAULT_BUDGET,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_USER_AGENT,
    MIN_INTERVAL,
    PDF_INTERVAL,
    Opener,
    Transport,
    create_part_file,
)

QUERY_URL = "https://export.arxiv.org/api/query"
FEED_BASE = "https://rss.arxiv.org"
PDF_BASE = "https://arxiv.org/pdf"

SORT_BY = ("relevance", "lastUpdatedDate", "submittedDate")
SORT_ORDER = ("ascending", "descending")

START_LIMIT = 10000  # start >= 10000 returns a deterministic 500 (doc section 2.1)
MAX_PAGE_REQUEST = 2000  # largest max_results we send in one request
ID_CHUNK_SIZE = 100  # GET URLs with ~650+ IDs return 414
EMPTY_PAGE_RETRIES = 3  # an empty page partway through paging is retried this many times
MAX_PDFS_PER_CALL = 10
DEFAULT_MAX_PDF_BYTES = 50 * 1024 * 1024

QueryLike = Union[str, Q]


def _check_int(name: str, value: object, minimum: int, maximum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("%s must be an integer, got %r" % (name, value))
    if value < minimum or (maximum is not None and value > maximum):
        bound = ">= %d" % minimum if maximum is None else "between %d and %d" % (minimum, maximum)
        raise ValidationError("%s must be %s, got %d" % (name, bound, value))
    return value


def _check_sort(sort_by: str, sort_order: str) -> None:
    if sort_by not in SORT_BY:
        raise ValidationError("sort_by must be one of %s" % ", ".join(SORT_BY))
    if sort_order not in SORT_ORDER:
        raise ValidationError("sort_order must be one of %s" % ", ".join(SORT_ORDER))
    if sort_by == "relevance" and sort_order == "ascending":
        raise ValidationError(
            "ascending order needs a date sort: with relevance it returns the least relevant papers first. "
            "Use --sort submitted or --sort updated (sort_by submittedDate or lastUpdatedDate in the library)"
        )


class SearchResults:
    """Lazy iterator over search results, paging as you consume it.

    After iteration starts, ``total_results`` holds arXiv's (untrustworthy) total and
    ``truncated`` is True if arXiv's 10000-result paging limit cut off results you asked
    for. With ``strict=True`` that case raises ValidationError before anything is yielded.
    """

    def __init__(
        self,
        client: "ArxivClient",
        search_query: str,
        max_results: Optional[int],
        sort_by: str,
        sort_order: str,
        page_size: int,
        strict: bool,
    ) -> None:
        self._client = client
        self._query = search_query
        self._max = max_results
        self._sort_by = sort_by
        self._sort_order = sort_order
        self._page_size = page_size
        self._strict = strict
        self.total_results: Optional[int] = None
        self.truncated = False
        self.server_query: Optional[str] = None
        self.request_urls: List[str] = []
        self._iterator: Optional[Iterator[Paper]] = None

    def __iter__(self) -> Iterator[Paper]:
        if self._iterator is None:
            self._iterator = self._generate()
        return self._iterator

    def __next__(self) -> Paper:
        return next(iter(self))

    def _generate(self) -> Iterator[Paper]:
        yielded = 0
        first = True
        for page in self._client._iter_pages(self._query, self._sort_by, self._sort_order, self._page_size, self._max):
            if page.request_url:
                self.request_urls.append(page.request_url)
            if first:
                first = False
                self.total_results = page.total_results
                self.server_query = page.server_query
                wanted = page.total_results or 0
                if self._max is not None:
                    wanted = min(wanted, self._max)
                if wanted > START_LIMIT:
                    if self._strict:
                        raise ValidationError(
                            "query matches %s results but arXiv only pages through the first %d"
                            % (page.total_results, START_LIMIT)
                        )
                    self.truncated = True
            for paper in page.papers:
                if self._max is not None and yielded >= self._max:
                    return
                yielded += 1
                yield paper


class ArxivClient:
    """Client for the arXiv query API and announcement feeds.

    Pacing: arXiv's terms allow at most 1 request every 3 s and one connection at a
    time, across all processes. Every request goes through ``gate`` (by default a
    ``FileGate`` in the cache dir shared by all processes on this machine), which holds
    the connection exclusively and spaces requests 3 s apart (15 s between PDFs), across
    separate calls and processes too. Waiting for the gate counts against the budget.

    Time budget: every HTTP fetch (one page, one ID chunk, one PDF) gets ``budget``
    seconds in total, including pacing waits and retries.

    Not thread-safe: don't share one client between concurrent calls.
    """

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        *,
        budget: float = DEFAULT_BUDGET,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
        page_size: int = 100,
        opener: Optional[Opener] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        block_store: Optional[BlockStore] = None,
        gate: Optional[MemoryGate] = None,
        min_interval: float = MIN_INTERVAL,
        pdf_interval: float = PDF_INTERVAL,
        create_part_file: Callable[[str], Tuple[int, str]] = create_part_file,
    ) -> None:
        if not (0 < budget <= 30):
            raise ValidationError("budget must be between 0 and 30 seconds")
        if not (0 < request_timeout <= budget):
            raise ValidationError("request_timeout must be positive and no larger than budget")
        self.page_size = _check_int("page_size", page_size, 100, 200)
        self.budget = float(budget)
        self._clock = clock
        self._sleep = sleep
        self._min_interval = min_interval
        self._pdf_interval = pdf_interval
        self.transport = Transport(
            user_agent,
            opener=opener,
            clock=clock,
            sleep=sleep,
            block_store=block_store,
            gate=gate,
            min_interval=min_interval,
            pdf_interval=pdf_interval,
            request_timeout=request_timeout,
            create_part_file=create_part_file,
        )

    def _pacer(self, interval: Optional[float] = None) -> Pacer:
        return Pacer(self._min_interval if interval is None else interval, self._clock, self._sleep)

    # ----- query API ---------------------------------------------------------
    def search_page(
        self,
        query: Optional[QueryLike] = None,
        start: int = 0,
        max_results: int = 10,
        sort_by: str = "relevance",
        sort_order: str = "descending",
        *,
        id_list: Optional[Sequence[str]] = None,
        _pacer: Optional[Pacer] = None,
    ) -> SearchPage:
        """Fetch one page. ``start`` must be below 10000; ``max_results`` is clamped so
        that start + max_results <= 10000."""
        url = self.query_url(query, start, max_results, sort_by, sort_order, id_list=id_list)
        response = self.transport.get(url, _pacer or self._pacer(), self.budget)
        page = parse_query_feed(response.body)
        page.request_url = url
        return page

    def query_url(
        self,
        query: Optional[QueryLike] = None,
        start: int = 0,
        max_results: int = 10,
        sort_by: str = "relevance",
        sort_order: str = "descending",
        *,
        id_list: Optional[Sequence[str]] = None,
    ) -> str:
        """The URL ``search_page`` would request, validated but not sent."""
        if query is None and not id_list:
            raise ValidationError("either a query or an id_list is required")
        _check_int("start", start, 0)
        _check_int("max_results", max_results, 1, MAX_PAGE_REQUEST)
        _check_sort(sort_by, sort_order)
        if start >= START_LIMIT:
            raise ValidationError(
                "start=%d: arXiv fails at start >= %d; narrow the query instead" % (start, START_LIMIT)
            )
        max_results = min(max_results, START_LIMIT - start)
        params: Dict[str, object] = {}
        if query is not None:
            params["search_query"] = to_search_query(query)
        if id_list:
            params["id_list"] = ",".join(id_list)
        params.update(start=start, max_results=max_results, sortBy=sort_by, sortOrder=sort_order)
        return "%s?%s" % (QUERY_URL, encode_params(params))

    def _iter_pages(
        self,
        search_query: str,
        sort_by: str,
        sort_order: str,
        page_size: int,
        max_results: Optional[int],
    ) -> Iterator[SearchPage]:
        """Yield pages, advancing by the raw <entry> count. Stops at totalResults, at
        max_results, or at the 10000 limit. Retries an empty page that appears before
        totalResults; raises ServerError if it stays empty."""
        pacer = self._pacer()
        start = 0
        consumed = 0
        while True:
            remaining = None if max_results is None else max_results - consumed
            if remaining is not None and remaining <= 0:
                return
            if start >= START_LIMIT:
                return
            want = page_size if remaining is None else min(page_size, remaining)
            page = self.search_page(
                Q.raw(search_query) if search_query else None,
                start,
                want,
                sort_by,
                sort_order,
                _pacer=pacer,
            )
            total = page.total_results
            retries = 0
            while page.raw_entry_count == 0 and total is not None and start < total:
                if retries >= EMPTY_PAGE_RETRIES:
                    raise ServerError(
                        "arXiv returned an empty page at start=%d although totalResults=%d "
                        "(%d retries)" % (start, total, retries)
                    )
                retries += 1
                page = self.search_page(Q.raw(search_query), start, want, sort_by, sort_order, _pacer=pacer)
                if page.total_results is not None:
                    total = page.total_results
            yield page
            if page.raw_entry_count == 0:
                return
            start += page.raw_entry_count
            consumed += len(page.papers)
            if total is not None and start >= total:
                return

    def search(
        self,
        query: QueryLike,
        *,
        max_results: Optional[int] = None,
        sort_by: str = "relevance",
        sort_order: str = "descending",
        page_size: Optional[int] = None,
        strict: bool = False,
    ) -> SearchResults:
        """Search lazily. A plain string is free text: its stopwords are dropped, a trailing
        ``?`` or ``'s`` is removed from a word, and the other words are joined with AND
        (``Q.from_text``); use ``Q`` for more.

        ``max_results=None`` pages through everything (up to arXiv's 10000 limit).
        """
        if max_results is not None:
            _check_int("max_results", max_results, 1)
        _check_sort(sort_by, sort_order)
        size = self.page_size if page_size is None else _check_int("page_size", page_size, 100, 200)
        return SearchResults(self, to_search_query(query), max_results, sort_by, sort_order, size, strict)

    def fetch(self, ids: Sequence[str]) -> FetchResult:
        """Fetch papers by ID. Results are keyed by the ID strings you passed in.

        IDs that don't exist are listed in ``not_found``. Any invalid ID raises
        ValidationError before a request is sent. ``request_urls`` lists every URL
        requested, including one that failed and was retried.
        """
        if isinstance(ids, str):
            ids = [ids]
        if not ids:
            raise ValidationError("no IDs given")
        parsed: List[Tuple[str, ArxivId]] = []
        bad: List[str] = []
        for raw in ids:
            try:
                parsed.append((raw, parse_id(raw)))
            except ValidationError:
                bad.append(str(raw))
        if bad:
            raise ValidationError("invalid arXiv IDs: %s" % ", ".join(bad))

        unique: List[ArxivId] = []
        for _, aid in parsed:
            if aid not in unique:
                unique.append(aid)

        pacer = self._pacer()
        matched: Dict[ArxivId, Paper] = {}
        urls: List[str] = []
        for i in range(0, len(unique), ID_CHUNK_SIZE):
            matched.update(self._fetch_chunk(unique[i : i + ID_CHUNK_SIZE], pacer, urls))

        result = FetchResult(papers={}, not_found=[], request_urls=urls)
        for raw, aid in parsed:
            paper = matched.get(aid)
            if paper is None:
                if raw not in result.not_found:
                    result.not_found.append(raw)
            else:
                result.papers[raw] = paper
        return result

    def _fetch_chunk(self, chunk: List[ArxivId], pacer: Pacer, urls: List[str]) -> Dict[ArxivId, Paper]:
        """Fetch one chunk. Each URL is added to ``urls`` before it is requested, so a
        request that fails is listed too."""
        ids = [str(a) for a in chunk]
        urls.append(self.query_url(id_list=ids, max_results=len(ids)))
        try:
            page = self.search_page(id_list=ids, max_results=len(ids), _pacer=pacer)
            return _match_ids(chunk, page.papers, fallback=frozenset())
        except ServerError as e:
            affected = frozenset(a for a in chunk if a.old_style and a.version is not None)
            if e.status != 500 or not affected:
                raise
        # arXiv bug: some old-style IDs with a non-latest version return 500 for the whole
        # request. Retry once with those IDs unversioned (doc section 2.3).
        retry_ids: List[str] = []
        for a in chunk:
            s = str(a.unversioned() if a in affected else a)
            if s not in retry_ids:
                retry_ids.append(s)
        urls.append(self.query_url(id_list=retry_ids, max_results=len(retry_ids)))
        page = self.search_page(id_list=retry_ids, max_results=len(retry_ids), _pacer=pacer)
        return _match_ids(chunk, page.papers, fallback=affected)

    def get(self, arxiv_id: str) -> Paper:
        """Fetch one paper. Raises NotFoundError if it doesn't exist."""
        result = self.fetch([arxiv_id])
        if arxiv_id not in result.papers:
            raise NotFoundError("arXiv ID %s not found" % arxiv_id)
        return result.papers[arxiv_id]

    def scan_revisions(
        self,
        query: QueryLike,
        known: Mapping[str, int],
        *,
        stop_after_empty_pages: int = 3,
        page_size: Optional[int] = None,
    ) -> Iterator[Paper]:
        """Find new or revised papers the arxiv-sanity way.

        Pages through ``sortBy=lastUpdatedDate`` descending and yields every paper whose
        version is newer than ``known[base_id]`` (missing means new). Stops after
        ``stop_after_empty_pages`` consecutive pages with nothing new. Upsert by ID,
        keeping the higher version.
        """
        _check_int("stop_after_empty_pages", stop_after_empty_pages, 1)
        size = self.page_size if page_size is None else _check_int("page_size", page_size, 100, 200)
        empty_streak = 0
        for page in self._iter_pages(to_search_query(query), "lastUpdatedDate", "descending", size, None):
            new = [p for p in page.papers if (p.version or 0) > known.get(p.id, 0)]
            for p in new:
                yield p
            empty_streak = 0 if new else empty_streak + 1
            if empty_streak >= stop_after_empty_pages:
                return

    # ----- feeds -------------------------------------------------------------
    def feed(self, categories: Union[str, Sequence[str]], fmt: str = "atom") -> Feed:
        """Today's announcements for one or more archives/categories.

        Empty on weekends and holidays. An invalid category raises ValidationError
        (arXiv answers HTTP 400 with an empty feed, which is never treated as success).
        """
        if fmt not in ("atom", "rss"):
            raise ValidationError("fmt must be 'atom' or 'rss'")
        cats = validate_categories(categories)
        url = "%s/%s/%s" % (FEED_BASE, fmt, "+".join(cats))
        response = self.transport.get(url, self._pacer(), self.budget)
        return parse_atom_feed(response.body) if fmt == "atom" else parse_rss(response.body)

    # ----- PDFs --------------------------------------------------------------
    def download_pdf(
        self,
        arxiv_id: str,
        dest_dir: str,
        *,
        overwrite: bool = False,
        max_bytes: int = DEFAULT_MAX_PDF_BYTES,
        _pacer: Optional[Pacer] = None,
    ) -> PdfResult:
        """Download one PDF to ``dest_dir/<id>v<N>.pdf`` (old-style ``/`` becomes ``_``).

        For personal and research use only: arXiv PDFs are copyrighted, so don't re-host
        them. The file appears only once complete.
        """
        aid = parse_id(arxiv_id)
        _check_int("max_bytes", max_bytes, 1)
        if not os.path.isdir(dest_dir):
            raise ValidationError("destination directory does not exist: %s" % dest_dir)
        if aid.version is not None:
            target = os.path.join(dest_dir, _pdf_filename(aid.base, aid.version))
            if os.path.exists(target) and not overwrite:
                return PdfResult(target, aid.base, aid.version, os.path.getsize(target), skipped=True)
        url = "%s/%s" % (PDF_BASE, aid)
        # The transport maps network failures to ArxivError, so an OSError here is a local file problem.
        try:
            tmp, headers, size = self.transport.download(
                url,
                _pacer or self._pacer(self._pdf_interval),
                dest_dir,
                max_bytes=max_bytes,
                budget=self.budget,
            )
            try:
                version = aid.version or _version_from_disposition(headers.get("content-disposition", ""))
                target = os.path.join(dest_dir, _pdf_filename(aid.base, version))
                if os.path.exists(target) and not overwrite:
                    os.unlink(tmp)
                    return PdfResult(target, aid.base, version, os.path.getsize(target), skipped=True)
                os.replace(tmp, target)
            except BaseException:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
        except OSError as e:
            raise LocalFileError("could not save the PDF in %s: %s" % (dest_dir, e)) from e
        return PdfResult(target, aid.base, version, size)

    def download_pdfs(
        self,
        ids: Sequence[str],
        dest_dir: str,
        *,
        overwrite: bool = False,
        max_bytes: int = DEFAULT_MAX_PDF_BYTES,
    ) -> List[PdfResult]:
        """Download up to 10 PDFs, one after another, at least 15 s apart.

        On failure the exception has a ``completed`` attribute listing the PDFs already
        written; the call as a whole is not reported as a success.
        """
        if isinstance(ids, str):
            ids = [ids]
        if not ids:
            raise ValidationError("no IDs given")
        if len(ids) > MAX_PDFS_PER_CALL:
            raise ValidationError("at most %d PDFs per call, got %d" % (MAX_PDFS_PER_CALL, len(ids)))
        for i in ids:
            parse_id(i)
        pacer = self._pacer(self._pdf_interval)
        done: List[PdfResult] = []
        for i in ids:
            try:
                done.append(self.download_pdf(i, dest_dir, overwrite=overwrite, max_bytes=max_bytes, _pacer=pacer))
            except ArxivError as e:
                e.completed = list(done)  # type: ignore[attr-defined]
                raise
        return done


def _match_ids(requested: Sequence[ArxivId], papers: Sequence[Paper], fallback: frozenset) -> Dict[ArxivId, Paper]:
    """Match results to requests by ID; arXiv doesn't preserve order."""
    by_base: Dict[str, List[Paper]] = {}
    for p in papers:
        by_base.setdefault(p.id, []).append(p)
    out: Dict[ArxivId, Paper] = {}
    for aid in requested:
        candidates = by_base.get(aid.base, [])
        if not candidates:
            continue
        if aid in fallback:
            served = max(candidates, key=lambda p: p.version or 0)
            if served.version is not None and aid.version > served.version:
                continue  # newer than the latest version, so it doesn't exist
            out[aid] = dataclasses.replace(served, requested_version=aid.version)
        elif aid.version is None:
            out[aid] = max(candidates, key=lambda p: p.version or 0)
        else:
            exact = [p for p in candidates if p.version == aid.version]
            if exact:
                out[aid] = exact[0]
    return out


def _pdf_filename(base: str, version: Optional[int]) -> str:
    safe = base.replace("/", "_")
    return "%sv%d.pdf" % (safe, version) if version else "%s.pdf" % safe


def _version_from_disposition(value: str) -> Optional[int]:
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', value or "")
    if not m:
        return None
    v = re.search(r"v(\d+)\.pdf$", m.group(1).strip())
    return int(v.group(1)) if v else None
