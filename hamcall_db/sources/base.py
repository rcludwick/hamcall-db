"""The common interface every source importer implements.

Keep I/O confined to importers (download) and the writer. The merge/normalize/geocode
stages downstream are pure functions over iterables of `Record`.
"""

from __future__ import annotations

import http.client
import socket
import time
import urllib.error
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from hamcall_db.models import Record

# Seconds to wait before each retry of a transient download failure: 3 attempts in total
# (the first try, then one retry after each delay). Read at call time, so tests can
# monkeypatch this to zeros instead of sleeping.
RETRY_DELAYS: tuple[float, ...] = (30.0, 90.0)


def is_transient(exc: BaseException) -> bool:
    """Say whether a download failure is worth retrying.

    Transient means the same request might well succeed a minute later: an HTTP 5xx
    (server trouble, e.g. ISED's ``503 Service Unavailable``), HTTP 429 (rate limited),
    a timeout, a dropped or reset connection, or a response cut short mid-body.

    Everything else is permanent for the purposes of tonight's build: other 4xx codes
    (a moved URL, a 404, a 403) and DNS/name-resolution failures will fail the same way
    on every attempt, so retrying them only delays the build.
    """
    if isinstance(exc, urllib.error.HTTPError):  # check before URLError: it's a subclass
        return exc.code >= 500 or exc.code == 429
    if isinstance(exc, urllib.error.URLError):
        # urlopen wraps socket-level failures in URLError; classify the wrapped reason.
        reason = exc.reason
        return isinstance(reason, BaseException) and is_transient(reason)
    if isinstance(exc, socket.gaierror):  # DNS lookup failed: not going to fix itself
        return False
    return isinstance(exc, TimeoutError | ConnectionError | http.client.IncompleteRead)


def retry_transient[T](
    fn: Callable[[], T],
    *,
    delays: Sequence[float] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
) -> T:
    """Call ``fn``, retrying it after each delay in ``delays`` while it fails transiently.

    ``delays`` defaults to ``RETRY_DELAYS``. A non-transient failure (see
    ``is_transient``) is raised immediately; a transient one is raised once the delays
    run out. ``on_retry(attempt, exc, delay)`` is called before each wait so the caller
    can log it.
    """
    waits = RETRY_DELAYS if delays is None else tuple(delays)
    attempt = 1
    while True:
        try:
            return fn()
        except Exception as exc:
            if attempt > len(waits) or not is_transient(exc):
                raise
            delay = waits[attempt - 1]
            if on_retry is not None:
                on_retry(attempt, exc, delay)
            sleep(delay)
            attempt += 1


def synced_at_from(last_modified: str | None, fallback_path: Path | None) -> str | None:
    """Derive an ISO date (``YYYY-MM-DD``) for the upstream file.

    Prefers the HTTP ``Last-Modified`` response header (e.g.
    ``"Wed, 10 Jun 2026 12:00:00 GMT"``); when that's absent or unparseable, falls back
    to the mtime of ``fallback_path``. Returns None when neither yields a date.

    The result is a UTC calendar date — the day the upstream extract was published —
    suitable for stamping onto ``Record.synced_at``.
    """
    if last_modified:
        try:
            dt = parsedate_to_datetime(last_modified)
        except TypeError, ValueError:
            dt = None
        if dt is not None:
            if dt.tzinfo is not None:
                dt = dt.astimezone(UTC)
            return dt.date().isoformat()

    if fallback_path is not None and fallback_path.exists():
        mtime = fallback_path.stat().st_mtime
        return datetime.fromtimestamp(mtime, tz=UTC).date().isoformat()

    return None


@runtime_checkable
class Source(Protocol):
    """A national regulator or reference-file importer.

    `name` is the short source tag written into `Record.source` (e.g. "fcc").

    `synced_at` is the ISO date (``YYYY-MM-DD``) of the upstream source file, derived
    during `download()` from the HTTP ``Last-Modified`` header (preferred) or the
    downloaded file's mtime (fallback). It is None until `download()` runs. The build
    path passes it into `parse()` so every emitted `Record.synced_at` is stamped with
    the upstream file date. See `synced_at_from`.
    """

    name: str
    synced_at: str | None

    def download(self, work_dir: Path) -> Path:
        """Fetch the upstream artifact into `work_dir`, returning the local path.

        Implementations should cache under data/raw/<source>/<YYYY-MM-DD>/ and honor
        If-Modified-Since where the server supports it. Be polite to upstream. As a
        side effect, set `self.synced_at` to the upstream file's ISO date.
        """
        ...

    def parse(self, path: Path, *, synced_at: str | None = None) -> Iterable[Record]:
        """Parse the downloaded artifact into normalized `Record`s.

        When `synced_at` is None, implementations fall back to `self.synced_at` (set by
        `download()`); the resolved value stamps every `Record.synced_at`.
        """
        ...
