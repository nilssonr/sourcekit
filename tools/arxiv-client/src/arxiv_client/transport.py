"""HTTP transport: https GET, timeouts, throttle-signal detection, retries within a budget.

Every request is a fresh connection (``Connection: close``). urllib's ``timeout`` applies
per socket operation, so the body is read in chunks and checked against the call's
deadline; where possible the socket timeout is also tightened to the time remaining.
"""

from __future__ import annotations

import http.client
import os
import socket
import ssl
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlsplit

from .atom import error_message
from .errors import (
    ArxivError,
    ArxivTimeoutError,
    MalformedResponseError,
    NetworkBlockedError,
    NotFoundError,
    ServerError,
    ThrottledError,
    ValidationError,
)
from .pacing import BLOCK_SECONDS, BlockStore, FileGate, MemoryGate, Pacer

DEFAULT_USER_AGENT = "arxiv-client/0.1"
DEFAULT_BUDGET = 30.0
DEFAULT_REQUEST_TIMEOUT = 10.0
MIN_INTERVAL = 3.0              # arXiv: at most 1 request every 3 s, across all hosts and processes
PDF_INTERVAL = 15.0             # robots.txt Crawl-delay for User-agent: *

BACKOFFS = (5.0, 10.0)          # sleeps before the 2nd and 3rd attempt on 429/503/rate-exceeded
MIN_REQUEST_WINDOW = 5.0        # budget that must remain after a backoff sleep to bother retrying
SUGGESTED_THROTTLE_WAIT = 60.0  # floor for the retry_after we suggest to callers
CHUNK_SIZE = 64 * 1024
MAX_XML_BYTES = 64 * 1024 * 1024
RATE_EXCEEDED_BODY = b"Rate exceeded."

RETRYABLE_SIGNALS = ("429", "503", "rate-exceeded-200", "empty-200")

Opener = Callable[[urllib.request.Request, float], Any]


@dataclass
class Response:
    status: int
    headers: Dict[str, str]
    body: bytes
    url: str


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        if not newurl.lower().startswith("https://"):
            raise ServerError("refusing redirect to non-https URL %s" % newurl, status=code)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# build_opener includes ProxyHandler, which honours HTTPS_PROXY / https_proxy from the
# environment (used by sandbox egress proxies).
def build_urllib_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_HttpsOnlyRedirectHandler)


_URLLIB_OPENER = build_urllib_opener()


class _UrllibResponse:
    def __init__(self, resp: Any) -> None:
        self._resp = resp
        self.status = getattr(resp, "status", None) or resp.getcode()
        self.headers = resp.headers

    def read(self, n: int) -> bytes:
        return self._resp.read(n)

    def close(self) -> None:
        try:
            self._resp.close()
        except Exception:
            pass

    def set_timeout(self, seconds: float) -> None:
        """Best effort: find the underlying socket and tighten its timeout."""
        obj = self._resp
        for _ in range(6):
            sock = getattr(obj, "_sock", None)
            if isinstance(sock, socket.socket):
                try:
                    sock.settimeout(max(0.01, seconds))
                except OSError:
                    pass
                return
            nxt = getattr(obj, "fp", None) or getattr(obj, "raw", None)
            if nxt is None or nxt is obj:
                return
            obj = nxt


def default_opener(request: urllib.request.Request, timeout: float) -> _UrllibResponse:
    try:
        resp = _URLLIB_OPENER.open(request, timeout=timeout)
    except urllib.error.HTTPError as e:
        resp = e  # HTTPError is a readable response with status and headers
    return _UrllibResponse(resp)


def _lower_headers(headers: Any) -> Dict[str, str]:
    if headers is None:
        return {}
    items = headers.items() if hasattr(headers, "items") else headers
    return {str(k).lower(): str(v) for k, v in items}


def _suggested_wait(headers: Dict[str, str]) -> float:
    try:
        value = float(headers.get("retry-after", ""))
    except ValueError:
        value = 0.0
    # Retry-After: 0 is treated as unknown (doc section 3.0 pitfalls).
    return max(SUGGESTED_THROTTLE_WAIT, value)


def _is_timeout(exc: BaseException) -> bool:
    return isinstance(exc, (socket.timeout, TimeoutError))


class Transport:
    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        *,
        opener: Optional[Opener] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        block_store: Optional[BlockStore] = None,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
        gate: Optional[MemoryGate] = None,
        min_interval: float = MIN_INTERVAL,
        pdf_interval: float = PDF_INTERVAL,
    ) -> None:
        if not user_agent or not user_agent.strip():
            raise ValidationError("user_agent must not be empty")
        self.user_agent = user_agent.strip()
        self._opener = opener or default_opener
        self._clock = clock
        self._sleep = sleep
        self.block_store = block_store if block_store is not None else BlockStore()
        self.request_timeout = float(request_timeout)
        self.gate = gate if gate is not None else FileGate(clock=clock, sleep=sleep)
        self._api_lanes = {"any": float(min_interval)}
        self._pdf_lanes = {"any": float(min_interval), "pdf": float(pdf_interval)}

    # ----- public ------------------------------------------------------------
    def get(self, url: str, pacer: Pacer, budget: float = DEFAULT_BUDGET) -> Response:
        """GET an XML resource. Retries throttling signals while the budget allows."""
        deadline = self._clock() + budget
        attempt = 0
        while True:
            self._raise_if_blocked()  # before waiting for the gate: fail fast during a 406 block
            # The gate is held for the whole connection and released before any backoff sleep.
            with self.gate.hold(self._api_lanes, deadline):
                raw = self._open(url, pacer, deadline)
                try:
                    status = int(raw.status)
                    headers = _lower_headers(raw.headers)
                    body = self._read(raw, deadline, MAX_XML_BYTES)
                finally:
                    raw.close()
            if status == 200:
                signal = self._classify_200(headers, body, expect="xml")
                if signal is None:
                    return Response(status, headers, body, url)
            else:
                signal = self._status_signal(status)
            if signal is None:
                self._raise_for_status(status, headers, body, url)
            attempt = self._backoff_or_raise(signal, attempt, deadline, headers, url)

    def download(
        self,
        url: str,
        pacer: Pacer,
        dest_dir: str,
        *,
        max_bytes: int,
        budget: float = DEFAULT_BUDGET,
    ) -> Tuple[str, Dict[str, str], int]:
        """GET a PDF into a temporary ``.part`` file in ``dest_dir``.

        Returns (temp path, headers, byte count). The caller renames the file into place.
        On any error the partial file is removed.
        """
        deadline = self._clock() + budget
        attempt = 0
        while True:
            self._raise_if_blocked()
            with self.gate.hold(self._pdf_lanes, deadline):
                raw = self._open(url, pacer, deadline)
                try:
                    status = int(raw.status)
                    headers = _lower_headers(raw.headers)
                    ctype = headers.get("content-type", "").lower()
                    if status == 200 and "application/pdf" in ctype:
                        path, size = self._stream_pdf(raw, deadline, dest_dir, max_bytes)
                        return path, headers, size
                    body = self._read(raw, deadline, 1024 * 1024)
                finally:
                    raw.close()
            if status == 200:
                signal = self._classify_200(headers, body, expect="pdf")
            else:
                signal = self._status_signal(status)
            if signal is None:
                self._raise_for_status(status, headers, body, url)
            attempt = self._backoff_or_raise(signal, attempt, deadline, headers, url)

    # ----- internals ---------------------------------------------------------
    def _raise_if_blocked(self) -> None:
        blocked = self.block_store.remaining()
        if blocked > 0:
            raise ThrottledError(
                "arXiv blocked this IP (HTTP 406) recently; %.0fs left. Retrying during the "
                "block extends it, so no request was sent." % blocked,
                retry_after=blocked,
                kind="406",
            )

    def _open(self, url: str, pacer: Pacer, deadline: float) -> Any:
        if not url.lower().startswith("https://"):
            raise ValidationError("only https URLs are allowed: %s" % url)
        host = urlsplit(url).hostname or url
        self._raise_if_blocked()  # again: a block may have started while waiting for the gate
        pacer.before_request(deadline)
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise ArxivTimeoutError("time budget exhausted before the request to %s" % host)
        request = urllib.request.Request(
            url,
            method="GET",
            headers={
                "User-Agent": self.user_agent,
                "Connection": "close",
                "Accept-Encoding": "identity",
            },
        )
        try:
            return self._opener(request, min(self.request_timeout, remaining))
        except ArxivError:
            raise
        except urllib.error.URLError as e:
            raise self._map_network_error(e.reason, host) from None
        except Exception as e:  # socket errors, http.client errors
            raise self._map_network_error(e, host) from None

    def _map_network_error(self, reason: Any, host: str) -> ArxivError:
        if _is_timeout(reason) or "timed out" in str(reason):
            return ArxivTimeoutError("request to %s timed out" % host)
        text = str(reason)
        if (
            isinstance(reason, (socket.gaierror, ConnectionRefusedError))
            or "Tunnel connection failed" in text
        ):
            return NetworkBlockedError(
                "could not reach %s (%s). If this runs in a sandbox, allow this domain "
                "in your network settings." % (host, text),
                host=host,
            )
        if isinstance(reason, ssl.SSLError):
            return ServerError("TLS error talking to %s: %s" % (host, text))
        return ServerError("connection error talking to %s: %s" % (host, text))

    def _read_chunks(self, raw: Any, deadline: float, max_bytes: int, sink: Callable[[bytes], None]) -> int:
        total = 0
        set_timeout = getattr(raw, "set_timeout", None)
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise ArxivTimeoutError("time budget exhausted while reading the response")
            if set_timeout is not None:
                set_timeout(min(self.request_timeout, remaining))
            try:
                chunk = raw.read(CHUNK_SIZE)
            except Exception as e:
                if _is_timeout(e):
                    raise ArxivTimeoutError("timed out reading the response") from None
                raise ServerError("connection error while reading the response: %s" % e) from None
            if not chunk:
                return total
            total += len(chunk)
            if total > max_bytes:
                raise _TooLarge(max_bytes)
            sink(chunk)

    def _read(self, raw: Any, deadline: float, max_bytes: int) -> bytes:
        parts = []
        try:
            self._read_chunks(raw, deadline, max_bytes, parts.append)
        except _TooLarge:
            raise MalformedResponseError("response larger than %d bytes" % max_bytes) from None
        return b"".join(parts)

    def _stream_pdf(self, raw: Any, deadline: float, dest_dir: str, max_bytes: int) -> Tuple[str, int]:
        fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".arxiv-", suffix=".pdf.part")
        head = bytearray()
        try:
            with os.fdopen(fd, "wb") as f:
                def sink(chunk: bytes) -> None:
                    if len(head) < 5:
                        head.extend(chunk[: 5 - len(head)])
                        if len(head) >= 5 and not bytes(head).startswith(b"%PDF-"):
                            raise MalformedResponseError("response is labelled PDF but lacks the %PDF- header")
                    f.write(chunk)

                try:
                    size = self._read_chunks(raw, deadline, max_bytes, sink)
                except _TooLarge:
                    raise ValidationError("PDF is larger than max_bytes=%d" % max_bytes) from None
            if not bytes(head).startswith(b"%PDF-"):
                raise MalformedResponseError("response is labelled PDF but lacks the %PDF- header")
            return tmp, size
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def _classify_200(headers: Dict[str, str], body: bytes, expect: str) -> Optional[str]:
        stripped = body.strip()
        # Throttling disguised as success: check before parsing (doc section 2.5).
        if stripped == RATE_EXCEEDED_BODY:
            return "rate-exceeded-200"
        ctype = headers.get("content-type", "")
        if not stripped and not ctype:
            return "empty-200"
        if expect == "xml" and stripped.startswith(b"<"):
            return None
        raise MalformedResponseError(
            "unexpected 200 response (content-type %r, %d bytes)%s"
            % (ctype or None, len(body), "; expected a PDF" if expect == "pdf" else "")
        )

    @staticmethod
    def _status_signal(status: int) -> Optional[str]:
        if status == 429:
            return "429"
        if status == 503:
            return "503"
        return None

    def _backoff_or_raise(
        self, signal: str, attempt: int, deadline: float, headers: Dict[str, str], url: str
    ) -> int:
        if attempt < len(BACKOFFS):
            wait = BACKOFFS[attempt]
            if self._clock() + wait + MIN_REQUEST_WINDOW <= deadline:
                self._sleep(wait)  # the gate is released while backing off (doc section 6.2)
                return attempt + 1
        host = urlsplit(url).hostname or url
        if signal == "empty-200":
            raise ServerError("%s kept returning empty 200 responses" % host, status=200)
        raise ThrottledError(
            "arXiv is throttling requests (%s from %s); wait before retrying" % (signal, host),
            retry_after=_suggested_wait(headers),
            kind=signal,
        )

    def _raise_for_status(self, status: int, headers: Dict[str, str], body: bytes, url: str) -> None:
        host = urlsplit(url).hostname or url
        message = error_message(body) if body.strip() else None
        detail = (": %s" % message) if message else ""
        if status == 406:
            self.block_store.block(BLOCK_SECONDS)
            raise ThrottledError(
                "HTTP 406 from %s: arXiv blocked this IP for about 10 minutes. Retrying extends "
                "the block; wait at least 10 minutes." % host,
                retry_after=BLOCK_SECONDS,
                kind="406",
            )
        if status == 400:
            raise ValidationError("arXiv rejected the request (HTTP 400)%s" % detail)
        if status == 404:
            raise NotFoundError("not found (HTTP 404) at %s" % url)
        if status == 414:
            raise ValidationError("request URL too long (HTTP 414); send fewer IDs per request")
        if status == 403:
            raise ServerError(
                "HTTP 403 from %s: arXiv refused the request, or a proxy blocked it%s" % (host, detail),
                status=403,
            )
        raise ServerError("HTTP %d from %s%s" % (status, host, detail), status=status)


class _TooLarge(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(limit)
        self.limit = limit
