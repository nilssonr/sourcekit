"""Request pacing inside one call, and the shared 406 block marker.

There is deliberately no global or cross-process throttle: pacing between separate
calls is the caller's (the skill's) job. A ``Pacer`` lives for one public call and
spaces the requests *that call* makes. The one piece of state shared between calls
and processes is the 406 block marker, because retrying during a 406 block extends it.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from typing import Callable, Optional, TextIO

from .errors import ArxivTimeoutError

BLOCK_SECONDS = 600.0


class Pacer:
    """Spaces requests at least ``min_interval`` seconds apart, measured from request start."""

    def __init__(
        self,
        min_interval: float = 3.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval = float(min_interval)
        self._clock = clock
        self._sleep = sleep
        self._last_start: Optional[float] = None

    def wait_needed(self) -> float:
        if self._last_start is None:
            return 0.0
        return max(0.0, self._last_start + self.min_interval - self._clock())

    def before_request(self, deadline: float) -> None:
        """Wait until the next request may start, then record its start time.

        Raises ArxivTimeoutError if waiting would pass ``deadline`` (a clock() value).
        """
        wait = self.wait_needed()
        if wait > 0:
            if self._clock() + wait >= deadline:
                raise ArxivTimeoutError(
                    "time budget exhausted: next request would have to wait %.1fs for pacing" % wait
                )
            self._sleep(wait)
        self._last_start = self._clock()


def default_cache_dir() -> str:
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches")
    elif sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "arxiv_client")


class BlockStore:
    """Remembers a 406 block in a small marker file so every call and process honours it.

    The file holds one number: the wall-clock time the block ends. Writes are atomic
    (temp file + os.replace). If the file can't be written, the block is kept in memory
    for this process and a single warning goes to stderr.
    """

    def __init__(
        self,
        path: Optional[str] = None,
        wall: Callable[[], float] = time.time,
        warn_stream: Optional[TextIO] = None,
    ) -> None:
        self.path = path if path is not None else os.path.join(default_cache_dir(), "blocked_until")
        self._wall = wall
        self._memory_until = 0.0
        self._warned = False
        self._warn_stream = warn_stream

    def remaining(self) -> float:
        """Seconds left on the current block, or 0."""
        now = self._wall()
        until = max(self._memory_until, self._read_file())
        # A marker far in the future (clock change, corruption) never blocks longer than one block.
        until = min(until, now + BLOCK_SECONDS)
        return max(0.0, until - now)

    def block(self, seconds: float = BLOCK_SECONDS) -> None:
        until = self._wall() + seconds
        self._memory_until = max(self._memory_until, until)
        try:
            directory = os.path.dirname(self.path) or "."
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".blocked_until.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    f.write("%.3f\n" % until)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as e:
            if not self._warned:
                self._warned = True
                stream = self._warn_stream if self._warn_stream is not None else sys.stderr
                stream.write(
                    "arxiv_client: could not write 406 block marker %s (%s); "
                    "remembering the block in memory only\n" % (self.path, e)
                )

    def _read_file(self) -> float:
        try:
            with open(self.path) as f:
                return float(f.read().strip() or 0)
        except (OSError, ValueError):
            return 0.0


class MemoryBlockStore(BlockStore):
    """A BlockStore that never touches disk (for tests and embedding)."""

    def __init__(self, wall: Callable[[], float] = time.time) -> None:
        super().__init__(path="", wall=wall)

    def block(self, seconds: float = BLOCK_SECONDS) -> None:
        self._memory_until = max(self._memory_until, self._wall() + seconds)

    def _read_file(self) -> float:
        return 0.0
