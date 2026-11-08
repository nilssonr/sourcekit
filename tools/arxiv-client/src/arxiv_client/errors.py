"""Typed exceptions raised by the arXiv client."""

from __future__ import annotations

from typing import Optional


class ArxivError(Exception):
    """Base class for every error raised by this package."""


class ValidationError(ArxivError):
    """The request was invalid, either caught locally or reported by arXiv (HTTP 400)."""


class NotFoundError(ArxivError):
    """The requested paper (or PDF) does not exist."""


class ThrottledError(ArxivError):
    """arXiv signalled throttling. Wait at least ``retry_after`` seconds before trying again.

    ``kind`` is one of ``'429'``, ``'503'``, ``'406'`` or ``'rate-exceeded-200'``.
    A ``'406'`` is a ~10 minute block on your IP; retrying during it extends the block.
    """

    def __init__(self, message: str, *, retry_after: float, kind: str) -> None:
        super().__init__(message)
        self.retry_after = float(retry_after)
        self.kind = kind


class ServerError(ArxivError):
    """arXiv returned an error status, or a response that could not be used."""

    def __init__(self, message: str, *, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class MalformedResponseError(ServerError):
    """The response body was not what was expected (unparseable XML, wrong content type)."""


class ArxivTimeoutError(ArxivError):
    """A request timed out, or the call's total time budget ran out."""


class NetworkBlockedError(ArxivError):
    """The host could not be reached at all: DNS failure, refused connection, or a proxy
    refusing the tunnel. In a sandbox this usually means the domain is not allowlisted."""

    def __init__(self, message: str, *, host: str) -> None:
        super().__init__(message)
        self.host = host
