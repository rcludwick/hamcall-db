"""A dated download cache for importers that fetch one or two files per build.

The convention (see `Source.download`) is to keep each upstream file under
``<work_dir>/data/raw/<source>/<YYYY-MM-DD>/``. This module holds the three pieces the
Anatel, ENACOM, and URSEC importers share:

- `raw_dir` names today's cache directory for a source.
- `latest_prior` finds the newest earlier day's copy of a file, so a conditional
  request can be sent against it.
- `fetch_cached` downloads a URL into today's directory at most once. When an earlier
  copy exists it sends ``If-Modified-Since``; a ``304 Not Modified`` reply copies the
  earlier file forward instead of downloading it again.

Downloaded files get their mtime set to the server's ``Last-Modified`` time, so
`hamcall_db.sources.base.synced_at_from` gives the upstream date even when the file
is reused from the cache, and later ``If-Modified-Since`` headers carry the server's
own clock rather than ours.

Every request carries a timeout. Retrying a transient failure is the build's job
(`hamcall_db.build._run_source` wraps each ``download()`` in `retry_transient`).
"""

from __future__ import annotations

import os
import shutil
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import date
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path
from typing import Any

# A urlopen-compatible callable: opener(request, timeout=seconds) -> response.
Opener = Callable[..., Any]

USER_AGENT = "hamcall-db (+https://github.com/rcludwick/hamcall-db)"


def raw_dir(work_dir: Path, tag: str, day: date | None = None) -> Path:
    """``<work_dir>/data/raw/<tag>/<YYYY-MM-DD>/`` for `day` (default: today)."""
    return work_dir / "data" / "raw" / tag / (day or date.today()).isoformat()


def latest_prior(work_dir: Path, tag: str, filename: str, day: date | None = None) -> Path | None:
    """The newest copy of `filename` in a dated cache directory before `day`, or None."""
    today = (day or date.today()).isoformat()
    root = work_dir / "data" / "raw" / tag
    if not root.is_dir():
        return None
    for sub in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
        if sub.name < today and (sub / filename).is_file():
            return sub / filename
    return None


def set_mtime_from_header(path: Path, last_modified: str | None) -> None:
    """Stamp `path` with the HTTP ``Last-Modified`` time, when the header parses."""
    if not last_modified:
        return
    try:
        stamp = parsedate_to_datetime(last_modified).timestamp()
    except TypeError, ValueError:
        return
    os.utime(path, (stamp, stamp))


def if_modified_since(prior: Path | None) -> dict[str, str]:
    """The ``If-Modified-Since`` header for a cached copy, or no header."""
    if prior is None or not prior.exists():
        return {}
    return {"If-Modified-Since": formatdate(prior.stat().st_mtime, usegmt=True)}


def fetch_cached(
    url: str,
    dest: Path,
    *,
    prior: Path | None,
    opener: Opener = urllib.request.urlopen,
    timeout: float,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> str | None:
    """Download `url` to `dest` unless `dest` is already there.

    Returns the response's ``Last-Modified`` header (None when the file came from the
    cache or the server sent none). `prior` is an earlier day's copy: it drives the
    ``If-Modified-Since`` header, and a 304 reply copies it to `dest`. `data` makes the
    request a POST (form-encoded by the caller); conditional headers are only sent
    for GETs.
    """
    if dest.exists():
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    request_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    if data is None:
        request_headers.update(if_modified_since(prior))
    request = urllib.request.Request(url, data=data, headers=request_headers)
    part = dest.with_name(dest.name + ".part")
    try:
        with opener(request, timeout=timeout) as response:
            last_modified = response.headers.get("Last-Modified")
            part.write_bytes(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 304 and prior is not None and prior.exists():
            shutil.copy2(prior, dest)  # unchanged upstream: carry the old copy forward
            return None
        raise
    part.replace(dest)
    set_mtime_from_header(dest, last_modified)
    return last_modified
