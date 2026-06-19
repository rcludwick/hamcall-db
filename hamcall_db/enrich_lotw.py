"""LoTW (ARRL Logbook of the World) user-activity enrichment (hdb-fccf).

Stamps each `Record`'s callsign with whether it appears in ARRL's public LoTW
user-activity list and, if so, that user's last upload date (`uses_lotw` +
`lotw_last_activity`). This is activity metadata, not holder identity — like the
AllStarLink node enrichment it is a supplementary column that can be dropped without
affecting the rest of the dataset. Mirrors `enrich_allstar.py` exactly: a small polite
downloader plus a pure loader and a pure `Record -> Record` enricher.

Three pieces:

1. `download_lotw(work_dir) -> Path` fetches the user-activity CSV and caches it under
   ``data/raw/lotw/<YYYY-MM-DD>/`` with a conditional GET (If-Modified-Since). ARRL
   regenerates the file every ~7 days, so the conditional GET avoids re-pulling an
   unchanged extract during local iteration.
2. `load_lotw(path) -> dict[str, str]` parses it into callsign (UPPER) -> last upload
   date (ISO ``YYYY-MM-DD``). On a case-fold collision the MOST RECENT date wins.
3. `enrich(records, lookup)` sets `uses_lotw` / `lotw_last_activity` via
   `dataclasses.replace` (never mutates input); leaves ``(False, None)`` for callsigns
   absent from the list.

Data format
-----------
``https://lotw.arrl.org/lotw-user-activity.csv`` serves one comma-delimited line per
LoTW user::

    CALLSIGN,YYYY-MM-DD,HH:MM:SS

The date/time are UTC. Only the date is kept; the time-of-day is discarded (a daily
granularity is all consumers need and keeps the value a clean ISO date). Blank lines,
rows without a callsign, and rows whose date doesn't parse as ``YYYY-MM-DD`` are
skipped. Extra trailing comma fields (if ARRL ever adds columns) are tolerated.

License: ARRL publishes this list as a public developer web service but states NO
explicit redistribution license — only a generic "© American Radio Relay League, Inc.
All Rights Reserved" copyright notice. PROJECT POSTURE (2026-06-18): this column is
used on an ASSUMED NON-COMMERCIAL basis, consistent with the dataset's CC BY-NC 4.0
umbrella, the SAME unverified posture as AllStarLink/POTA — but the explicit "All
Rights Reserved" makes redistribution permission GENUINELY UNCLEAR and this needs
HUMAN SIGN-OFF (see NOTICE source 9 and the nugget report). The column is supplementary
and can be dropped without affecting the rest of the dataset.
"""

from __future__ import annotations

import datetime as dt
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from dataclasses import replace
from datetime import date
from email.utils import formatdate
from pathlib import Path

from hamcall_db.models import Record

__all__ = [
    "LOTW_ACTIVITY_URL",
    "DOWNLOAD_TIMEOUT",
    "download_lotw",
    "load_lotw",
    "enrich",
]

# ARRL's public LoTW user-activity list: one CSV line per user,
# ``CALLSIGN,YYYY-MM-DD,HH:MM:SS`` (UTC). Served over https; a conditional GET
# (If-Modified-Since) lets us skip an unchanged extract (ARRL regenerates it ~weekly).
LOTW_ACTIVITY_URL = "https://lotw.arrl.org/lotw-user-activity.csv"

# Connect/read timeout (seconds) so a hung upstream fails fast instead of stalling the
# build (mirrors the AllStarLink/ISED importers' DOWNLOAD_TIMEOUT).
DOWNLOAD_TIMEOUT = 60

# Polite identifier so upstream can see who is pulling.
_USER_AGENT = "hamcall-db/0 (+https://github.com/rcludwick/hamcall-db)"

# Comma-delimited column indices.
_DELIMITER = ","
_CALLSIGN = 0
_DATE = 1

# Injectable fetch seam (matches enrich_allstar.py): a fetcher takes (url,
# if_modified_since_epoch_or_None) and returns the raw bytes, or None for "not modified
# — keep the cached copy".
Fetcher = Callable[[str, "float | None"], "bytes | None"]


def _urllib_fetch(url: str, if_modified_since: float | None) -> bytes | None:
    """Default fetcher: conditional GET via urllib. None => 304 Not Modified."""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    if if_modified_since is not None:
        request.add_header(
            "If-Modified-Since", formatdate(if_modified_since, usegmt=True)
        )
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 304:  # unchanged — caller reuses cache
            return None
        raise


def download_lotw(
    work_dir: Path,
    *,
    on: date | None = None,
    fetcher: Fetcher | None = None,
) -> Path:
    """Fetch the LoTW user-activity CSV, caching under ``work_dir/data/raw/lotw/<date>/``.

    Returns the local path to the cached CSV (ready for `load_lotw`). Honors
    If-Modified-Since: an unchanged extract (HTTP 304) reuses the cached copy without
    re-downloading. ``fetcher`` is an injectable seam used by tests to avoid the network;
    production code leaves it None to use urllib.
    """
    fetcher = fetcher or _urllib_fetch
    day = (on or date.today()).isoformat()
    dest = work_dir / "data" / "raw" / "lotw" / day / "lotw-user-activity.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)

    since = dest.stat().st_mtime if dest.exists() else None
    data = fetcher(LOTW_ACTIVITY_URL, since)
    if data is not None:
        dest.write_bytes(data)
    elif not dest.exists():  # 304 but nothing cached — should never happen
        raise RuntimeError(
            f"{LOTW_ACTIVITY_URL} returned not-modified but no cached file at {dest}"
        )
    return dest


def load_lotw(path: str | Path) -> dict[str, str]:
    """Parse the user-activity CSV into ``callsign (UPPER) -> last upload date (ISO)``.

    The only I/O in this module beyond the downloader. Skips blank lines, rows without a
    callsign, and rows whose date field doesn't parse as ``YYYY-MM-DD``. The time-of-day
    is discarded. On a case-fold collision the MOST RECENT date wins, so the value is
    deterministic regardless of line order.
    """
    date_by_call: dict[str, str] = {}
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        fields = line.split(_DELIMITER)
        if len(fields) <= _DATE:
            continue
        callsign = fields[_CALLSIGN].strip().upper()
        if not callsign:
            continue
        raw_date = fields[_DATE].strip()
        try:
            iso_date = dt.date.fromisoformat(raw_date).isoformat()
        except ValueError:
            continue  # malformed date — skip the row
        existing = date_by_call.get(callsign)
        if existing is None or iso_date > existing:
            date_by_call[callsign] = iso_date
    return date_by_call


def enrich_record(record: Record, lookup: dict[str, str]) -> Record:
    """Return a NEW Record with `uses_lotw` / `lotw_last_activity` set from ``lookup``.

    Never mutates the input. Leaves ``uses_lotw=False`` / ``lotw_last_activity=None`` when
    the callsign is absent from the LoTW list.
    """
    last = lookup.get(record.callsign.upper()) if record.callsign else None
    if last is None:
        return replace(record, uses_lotw=False, lotw_last_activity=None)
    return replace(record, uses_lotw=True, lotw_last_activity=last)


def enrich(records: Iterable[Record], lookup: dict[str, str]) -> Iterator[Record]:
    """Apply `enrich_record` lazily across a stream of Records."""
    for record in records:
        yield enrich_record(record, lookup)
