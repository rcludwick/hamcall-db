"""AD1C cty.dat downloader (DXCC prefix reference file).

cty.dat is a *reference* file, not licensee data: it maps callsign prefixes to DXCC
entities and is consumed by :func:`hamcall_db.enrich.load_cty`, NOT by the merge stage.
It is therefore intentionally NOT a record-producing ``Source`` and is not registered in
the build's SOURCES table. This module only fetches and caches the file; parsing lives in
``enrich.py``.

Upstream is the "Big CTY" plain-text variant maintained by Jim Reisert, AD1C, at
country-files.com. The big variant carries the full prefix/callsign list (better coverage
for everyday logging than the contest-trimmed file). It is served as a plain file (no zip
extraction needed) with a ``Last-Modified`` header, so a conditional GET (If-Modified-Since)
lets us skip re-downloading an unchanged weekly file.

License: AD1C cty.dat is free for non-commercial use, attribution required (see NOTICE /
mem-371f). Be polite to upstream — cache and use conditional GET.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import date
from email.utils import formatdate
from pathlib import Path

# Plain-text Big CTY files. Both are served directly (Content-Type
# application/octet-stream); cty.csv additionally carries the numeric DXCC/ADIF entity
# code. Confirmed reachable + If-Modified-Since 304-capable on 2026-06-16. If AD1C ever
# moves these, updating these two constants is the only change required.
CTY_DAT_URL = "https://www.country-files.com/bigcty/cty.dat"
CTY_CSV_URL = "https://www.country-files.com/bigcty/cty.csv"

# Polite identifier so upstream can see who is pulling.
_USER_AGENT = "hamcall-db/0 (+https://github.com/rcludwick/hamcall-db)"

# Injectable fetch seam. Real downloads go through urllib; tests pass a local-file fetcher
# so the cache/path logic can be exercised without touching the network. A fetcher takes
# (url, if_modified_since_epoch_or_None) and returns the raw bytes, or None for "not
# modified — keep the cached copy".
Fetcher = Callable[[str, "float | None"], "bytes | None"]


def _urllib_fetch(url: str, if_modified_since: float | None) -> bytes | None:
    """Default fetcher: conditional GET via urllib. None => 304 Not Modified."""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    if if_modified_since is not None:
        request.add_header("If-Modified-Since", formatdate(if_modified_since, usegmt=True))
    try:
        with urllib.request.urlopen(request) as response:  # noqa: S310 (https URL)
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 304:  # unchanged — caller reuses cache
            return None
        raise


def _fetch_cached(
    url: str,
    dest: Path,
    *,
    fetcher: Fetcher,
) -> Path:
    """Download ``url`` to ``dest``, reusing the cached file on a 304.

    If ``dest`` already exists, sends If-Modified-Since with its mtime; a None return
    (304) leaves the cached bytes in place. Returns ``dest``.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    since = dest.stat().st_mtime if dest.exists() else None
    data = fetcher(url, since)
    if data is not None:
        dest.write_bytes(data)
    elif not dest.exists():  # 304 but nothing cached — should never happen
        raise RuntimeError(f"{url} returned not-modified but no cached file at {dest}")
    return dest


def download_cty(
    work_dir: Path,
    *,
    with_csv: bool = False,
    on: date | None = None,
    fetcher: Fetcher | None = None,
) -> Path:
    """Fetch AD1C cty.dat, caching under ``work_dir/data/raw/ad1c/<YYYY-MM-DD>/``.

    Returns the local path to the plain-text ``cty.dat`` (ready for
    :func:`hamcall_db.enrich.load_cty`). Honors If-Modified-Since: an unchanged weekly
    file (HTTP 304) reuses the cached copy without re-downloading.

    ``work_dir`` is the build root; the dated cache subdirectory is created beneath it,
    mirroring the other importers' ``data/raw/<source>/<date>/`` convention.

    ``with_csv=True`` also fetches ``cty.csv`` (carries the numeric DXCC/ADIF entity code)
    into the same directory; it is cached but the returned path is still cty.dat.

    ``fetcher`` is an injectable seam (see :data:`Fetcher`) used by tests to avoid the
    network; production code leaves it None to use urllib.
    """
    fetcher = fetcher or _urllib_fetch
    day = (on or date.today()).isoformat()
    cache_dir = work_dir / "data" / "raw" / "ad1c" / day

    dat_path = _fetch_cached(CTY_DAT_URL, cache_dir / "cty.dat", fetcher=fetcher)
    if with_csv:
        _fetch_cached(CTY_CSV_URL, cache_dir / "cty.csv", fetcher=fetcher)
    return dat_path
