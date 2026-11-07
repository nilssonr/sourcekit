"""Test doubles: a fake clock, a fake opener with canned responses, and feed builders.

Nothing here touches the network or really sleeps.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Union
from urllib.parse import parse_qs, urlsplit

from arxiv_client import ArxivClient, MemoryBlockStore

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "arxiv")
ATOM_CT = "application/atom+xml; charset=utf-8"


def fixture(name: str) -> bytes:
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleeps: List[float] = []

    def monotonic(self) -> float:
        return self.now

    def wall(self) -> float:
        return 1_700_000_000.0 + self.now

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0, seconds
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeResponse:
    """A canned response. ``read_delay`` advances the fake clock per chunk read."""

    def __init__(
        self,
        status: int = 200,
        body: Union[bytes, str] = b"",
        headers: Optional[Dict[str, str]] = None,
        clock: Optional[FakeClock] = None,
        read_delay: float = 0.0,
        chunk: int = 1024,
        read_error: Optional[BaseException] = None,
    ) -> None:
        self.status = status
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.headers = headers if headers is not None else {}
        self.clock = clock
        self.read_delay = read_delay
        self.chunk = chunk
        self.read_error = read_error
        self._pos = 0
        self.closed = False

    def read(self, n: int) -> bytes:
        if self.read_error is not None and self._pos > 0:
            raise self.read_error
        if self.clock is not None and self.read_delay:
            self.clock.advance(self.read_delay)
        size = min(n, self.chunk)
        data = self.body[self._pos : self._pos + size]
        self._pos += len(data)
        if self.read_error is not None and not data:
            raise self.read_error
        return data

    def close(self) -> None:
        self.closed = True


class Recorded:
    def __init__(self, request: Any, timeout: float, at: float) -> None:
        self.request = request
        self.url = request.full_url
        self.method = request.get_method()
        self.headers = {k.lower(): v for k, v in request.header_items()}
        self.timeout = timeout
        self.at = at

    @property
    def params(self) -> Dict[str, List[str]]:
        return parse_qs(urlsplit(self.url).query)

    @property
    def raw_query(self) -> str:
        return urlsplit(self.url).query


Step = Union[FakeResponse, BaseException, Callable[["Recorded"], FakeResponse]]


class FakeOpener:
    """Returns (or raises) queued steps in order and records every request."""

    def __init__(self, clock: FakeClock, steps: Optional[List[Step]] = None, request_duration: float = 0.1) -> None:
        self.clock = clock
        self.steps: List[Step] = list(steps or [])
        self.requests: List[Recorded] = []
        self.request_duration = request_duration

    def add(self, *steps: Step) -> "FakeOpener":
        self.steps.extend(steps)
        return self

    def __call__(self, request: Any, timeout: float) -> FakeResponse:
        rec = Recorded(request, timeout, self.clock.now)
        self.requests.append(rec)
        if not self.steps:
            raise AssertionError("unexpected request: %s" % rec.url)
        step = self.steps.pop(0)
        self.clock.advance(self.request_duration)
        if isinstance(step, BaseException):
            raise step
        if callable(step) and not isinstance(step, FakeResponse):
            step = step(rec)
        return step


def xml_response(body: Union[bytes, str], status: int = 200, ct: str = ATOM_CT, **kw: Any) -> FakeResponse:
    return FakeResponse(status, body, {"content-type": ct}, **kw)


def make_entry(
    arxiv_id: str,
    version: int = 1,
    title: Optional[str] = "A title",
    summary: str = "An abstract.",
    category: str = "cs.LG",
    dois: Optional[List[str]] = None,
) -> str:
    vid = "%sv%d" % (arxiv_id, version)
    parts = [
        "<entry>",
        "<id>http://arxiv.org/abs/%s</id>" % vid,
    ]
    if title is not None:
        parts.append("<title>%s</title>" % title)
    parts += [
        "<updated>2024-01-02T00:00:00Z</updated>",
        '<link href="https://arxiv.org/abs/%s" rel="alternate" type="text/html"/>' % vid,
        '<link title="pdf" href="https://arxiv.org/pdf/%s" rel="related" type="application/pdf"/>' % vid,
        "<summary>%s</summary>" % summary,
        '<category term="%s" scheme="http://arxiv.org/schemas/atom"/>' % category,
        "<published>2024-01-01T00:00:00Z</published>",
        '<arxiv:primary_category term="%s"/>' % category,
        "<author><name>Someone</name></author>",
    ]
    for d in dois or []:
        parts.append("<arxiv:doi>%s</arxiv:doi>" % d)
    parts.append("</entry>")
    return "".join(parts)


def make_feed(entries: List[str], total: Optional[int] = None, start: int = 0, items_per_page: int = 10) -> bytes:
    total = len(entries) if total is None else total
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" '
        'xmlns:arxiv="http://arxiv.org/schemas/atom">'
        "<id>https://arxiv.org/api/xyz</id><title>arXiv Query</title><updated>2026-11-07T00:00:00Z</updated>"
        '<link href="https://arxiv.org/api/query" type="application/atom+xml"/>'
        "<opensearch:itemsPerPage>%d</opensearch:itemsPerPage>"
        "<opensearch:totalResults>%d</opensearch:totalResults>"
        "<opensearch:startIndex>%d</opensearch:startIndex>%s</feed>"
        % (items_per_page, total, start, "".join(entries))
    ).encode("utf-8")


def make_client(clock: FakeClock, opener: FakeOpener, **kw: Any) -> ArxivClient:
    kw.setdefault("block_store", MemoryBlockStore(wall=clock.wall))
    return ArxivClient(opener=opener, clock=clock.monotonic, sleep=clock.sleep, **kw)
