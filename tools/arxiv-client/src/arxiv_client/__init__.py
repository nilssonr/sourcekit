# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Robin Nilsson. Full text: LICENSE in this package folder.
"""Standard-library client for arXiv's query API and announcement feeds.

Thank you to arXiv for use of its open access interoperability.
"""

from .client import ArxivClient, SearchResults
from .errors import (
    ArxivError,
    ArxivTimeoutError,
    LocalFileError,
    MalformedResponseError,
    NetworkBlockedError,
    NotFoundError,
    ServerError,
    ThrottledError,
    ValidationError,
)
from .ids import ArxivId, is_valid_id, parse_id
from .models import Author, Feed, FeedItem, FetchResult, Paper, PdfResult, SearchPage
from .pacing import BlockStore, FileGate, MemoryBlockStore, MemoryGate, Pacer
from .query import Q
from .transport import DEFAULT_USER_AGENT

__version__ = "0.1.0"

__all__ = [
    "ArxivClient",
    "SearchResults",
    "ArxivError",
    "ArxivTimeoutError",
    "LocalFileError",
    "MalformedResponseError",
    "NetworkBlockedError",
    "NotFoundError",
    "ServerError",
    "ThrottledError",
    "ValidationError",
    "ArxivId",
    "is_valid_id",
    "parse_id",
    "Author",
    "Feed",
    "FeedItem",
    "FetchResult",
    "Paper",
    "PdfResult",
    "SearchPage",
    "BlockStore",
    "MemoryBlockStore",
    "FileGate",
    "MemoryGate",
    "Pacer",
    "Q",
    "DEFAULT_USER_AGENT",
    "__version__",
]
