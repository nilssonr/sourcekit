"""Request pacing, the cross-process request gate, and the shared 406 block marker.

arXiv allows at most 1 request every 3 s and one connection at a time, across all your
processes and all its legacy APIs together (doc section 6.1). Three pieces enforce that:

- ``Pacer`` lives for one public call and spaces the requests *that call* makes.
- ``FileGate`` is shared by every call in every process on this machine. It holds an OS
  file lock for the whole of each request (one connection at a time, but not while
  waiting for pacing) and keeps the
  start time of the last request in that file, so separate calls and parallel
  processes are spaced too. The OS drops the lock when a process exits, so a crash
  can't leave it stuck.
- ``BlockStore`` remembers a 406, because retrying during a 406 block extends it.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import time
from typing import Callable, Dict, Iterator, Optional, TextIO

from .errors import ArxivTimeoutError

BLOCK_SECONDS = 600.0
GATE_MARGIN = 0.001  # added to every gate wait: wall-clock floats and stored timestamps are not exact


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


try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - not POSIX: the gate falls back to this process only
    _fcntl = None  # type: ignore[assignment]


class _Contended(Exception):
    """Another process holds the gate."""


class MemoryGate:
    """Spaces requests made through it, within this process only (tests and embedding).

    ``hold(intervals, deadline)`` waits until every lane in ``intervals`` (lane name to
    seconds) is clear, records the request start in those lanes, and holds the gate
    while the request runs (in FileGate, the file lock; it is not held while waiting).
    MemoryGate itself has no lock: the client is not thread-safe. ``deadline`` is a ``clock()`` value: waiting past it raises
    ArxivTimeoutError instead of sending the request.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._wall = wall
        self._lanes: Dict[str, float] = {}

    @contextlib.contextmanager
    def hold(self, intervals: Dict[str, float], deadline: float) -> Iterator[None]:
        # The lock is released while waiting for pacing, so a long wait (a PDF's 15 s lane)
        # doesn't block other requests whose lanes are already clear. After each wait the
        # lanes are read again, because another request may have started meanwhile.
        waited: Dict[str, float] = {}  # lane timestamps this call has already waited out
        while True:
            self._lock(deadline)
            try:
                lanes = self._merge(self._read())
                wait = self._wait_needed(lanes, intervals, waited)
            except BaseException:
                self._unlock()
                raise
            if wait <= 0:
                break
            self._unlock()
            wait += GATE_MARGIN
            if self._clock() + wait >= deadline:
                raise ArxivTimeoutError(
                    "time budget exhausted: next request would have to wait %.1fs for pacing" % wait
                )
            self._sleep(wait)
            waited = {lane: lanes[lane] for lane in intervals if lane in lanes}
        try:
            start = self._wall()
            for lane in intervals:
                lanes[lane] = start
            self._lanes = dict(lanes)
            self._write(lanes)
            yield
        finally:
            self._unlock()

    def _wait_needed(
        self, lanes: Dict[str, float], intervals: Dict[str, float], waited: Dict[str, float]
    ) -> float:
        now = self._wall()
        wait = 0.0
        for lane, interval in intervals.items():
            last = lanes.get(lane)
            # Skip a timestamp already waited out: one still in the future after a full
            # interval would otherwise be waited on again and again.
            if last is None or waited.get(lane) == last:
                continue
            # A timestamp in the future (clock change, corruption) never waits longer than one interval.
            wait = max(wait, min(interval, last + interval - now))
        return wait

    def _merge(self, stored: Dict[str, float]) -> Dict[str, float]:
        lanes = dict(self._lanes)
        for lane, t in stored.items():
            lanes[lane] = max(lanes.get(lane, t), t)
        return lanes

    # Storage hooks: in memory here, a locked file in FileGate.
    def _lock(self, deadline: float) -> None:
        pass

    def _unlock(self) -> None:
        pass

    def _read(self) -> Dict[str, float]:
        return {}

    def _write(self, lanes: Dict[str, float]) -> None:
        pass


class FileGate(MemoryGate):
    """A gate shared by every process on this machine, through a locked file.

    The lock is taken without blocking and retried every ``poll`` seconds until the
    deadline, so a busy gate ends in ArxivTimeoutError rather than a hang. OS locks are
    not first-come-first-served: with many processes queued, the later ones may time
    out. If the file can't be opened, the gate warns once on stderr and spaces only
    this process's requests.
    """

    def __init__(
        self,
        path: Optional[str] = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wall: Callable[[], float] = time.time,
        poll: float = 0.1,
        warn_stream: Optional[TextIO] = None,
    ) -> None:
        super().__init__(clock=clock, sleep=sleep, wall=wall)
        self.path = path if path is not None else os.path.join(default_cache_dir(), "request.lock")
        self._poll = poll
        self._fd: Optional[int] = None
        self._warned = False
        self._warn_stream = warn_stream

    def _lock(self, deadline: float) -> None:
        if _fcntl is None:  # pragma: no cover
            self._warn(OSError("file locking needs a POSIX system"))
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as e:
            self._warn(e)
            return
        try:
            while True:
                try:
                    _try_lock(fd)
                    break
                except _Contended:
                    if self._clock() + self._poll >= deadline:
                        raise ArxivTimeoutError(
                            "time budget exhausted waiting for another arxiv_client process to "
                            "finish its request (gate %s)" % self.path
                        ) from None
                    self._sleep(self._poll)
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd

    def _unlock(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            _unlock(fd)
        finally:
            os.close(fd)

    def _read(self) -> Dict[str, float]:
        if self._fd is None:
            return {}
        try:
            os.lseek(self._fd, 0, os.SEEK_SET)
            data = json.loads(os.read(self._fd, 4096).decode("utf-8") or "{}")
            return {str(k): float(v) for k, v in data.items()}
        except (OSError, ValueError, AttributeError, TypeError):
            return {}  # unreadable or corrupt: the in-memory lanes still apply

    def _write(self, lanes: Dict[str, float]) -> None:
        if self._fd is None:
            return
        data = json.dumps({k: round(v, 6) for k, v in sorted(lanes.items())}).encode("utf-8")
        try:
            os.lseek(self._fd, 0, os.SEEK_SET)
            os.write(self._fd, data)
            os.ftruncate(self._fd, len(data))
        except OSError as e:
            self._warn(e)

    def _warn(self, error: OSError) -> None:
        if not self._warned:
            self._warned = True
            stream = self._warn_stream if self._warn_stream is not None else sys.stderr
            stream.write(
                "arxiv_client: could not use request gate %s (%s); spacing this process's "
                "requests only\n" % (self.path, error)
            )


def _try_lock(fd: int) -> None:
    try:
        _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
    except BlockingIOError:
        raise _Contended() from None


def _unlock(fd: int) -> None:
    _fcntl.flock(fd, _fcntl.LOCK_UN)
