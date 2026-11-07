"""Standard-library client for arXiv's query API and announcement feeds.

Thank you to arXiv for use of its open access interoperability.
"""

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
from .ids import ArxivId, is_valid_id, parse_id
from .models import Author, Feed, FeedItem, FetchResult, Paper, PdfResult, SearchPage
from .pacing import BlockStore, MemoryBlockStore, Pacer
from .query import Q
from .transport import DEFAULT_USER_AGENT

__version__ = "0.1.0"
