"""AllStarLink node-number enrichment (hdb-8803).

Resolves each `Record`'s callsign to the AllStarLink node numbers registered under it,
filling `allstar_nodes`. A callsign can hold MANY nodes (a hub op may run dozens), so
this is a one-to-many join — unlike the cty.dat enrichment's one entity per callsign.
Mirrors that module's shape otherwise: a small polite downloader + a pure loader and a
pure `Record -> Record` enricher.

Three pieces:

1. `download_allstar(work_dir) -> Path` fetches the AllMon node database and caches it
   under ``data/raw/allstar/<YYYY-MM-DD>/`` with a conditional GET (If-Modified-Since).
2. `load_allstar(path) -> dict[str, list[int]]` parses it into callsign (UPPER) ->
   sorted, unique node numbers.
3. `enrich(records, lookup)` sets `allstar_nodes` via `dataclasses.replace` (never
   mutates input); leaves ``[]`` when a callsign has no nodes.

Data format
-----------
``http://allmondb.allstarlink.org/`` serves one pipe-delimited line per node::

    NodeNumber|Callsign|Description|Location

Description and Location may be empty. Blank lines and lines without a numeric node
number or a callsign are skipped.

License: AllStarLink node data is used under an ASSUMED non-commercial basis (project
decision 2026-06-16), consistent with the dataset's CC BY-NC 4.0 umbrella (mem-ffc0).
The NOTICE file credits AllStarLink.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from dataclasses import replace
from datetime import date
from email.utils import formatdate
from pathlib import Path

from hamcall_db.models import Record

__all__ = [
    "ALLSTAR_DB_URL",
    "DOWNLOAD_TIMEOUT",
    "download_allstar",
    "load_allstar",
    "enrich",
]

# AllMon node database: pipe-delimited NodeNumber|Callsign|Description|Location.
# Served as plain HTTP (no https endpoint as of 2026-06-16, confirmed reachable); a
# conditional GET (If-Modified-Since) lets us skip an unchanged daily extract. If
# AllStarLink ever publishes an https mirror, switch this one constant.
ALLSTAR_DB_URL = "http://allmondb.allstarlink.org/"

# Connect/read timeout (seconds) so a hung upstream fails fast instead of stalling the
# build (mirrors the ISED importer's DOWNLOAD_TIMEOUT).
DOWNLOAD_TIMEOUT = 60

# Polite identifier so upstream can see who is pulling.
_USER_AGENT = "hamcall-db/0 (+https://github.com/rcludwick/hamcall-db)"

# Pipe-delimited column indices.
_DELIMITER = "|"
_NODE = 0
_CALLSIGN = 1

# Injectable fetch seam (matches ad1c.py): a fetcher takes (url, if_modified_since_epoch
# _or_None) and returns the raw bytes, or None for "not modified — keep the cached copy".
Fetcher = Callable[[str, "float | None"], "bytes | None"]


def _urllib_fetch(url: str, if_modified_since: float | None) -> bytes | None:
    """Default fetcher: conditional GET via urllib. None => 304 Not Modified."""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    if if_modified_since is not None:
        request.add_header("If-Modified-Since", formatdate(if_modified_since, usegmt=True))
    try:
        # Plain http (no https mirror upstream); timeout guards against a hung server.
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:  # noqa: S310
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 304:  # unchanged — caller reuses cache
            return None
        raise


def download_allstar(
    work_dir: Path,
    *,
    on: date | None = None,
    fetcher: Fetcher | None = None,
) -> Path:
    """Fetch the AllStarLink node DB, caching under ``work_dir/data/raw/allstar/<date>/``.

    Returns the local path to the cached pipe-delimited file (ready for `load_allstar`).
    Honors If-Modified-Since: an unchanged extract (HTTP 304) reuses the cached copy
    without re-downloading. ``fetcher`` is an injectable seam used by tests to avoid the
    network; production code leaves it None to use urllib.
    """
    fetcher = fetcher or _urllib_fetch
    day = (on or date.today()).isoformat()
    dest = work_dir / "data" / "raw" / "allstar" / day / "allmondb.txt"
    dest.parent.mkdir(parents=True, exist_ok=True)

    since = dest.stat().st_mtime if dest.exists() else None
    data = fetcher(ALLSTAR_DB_URL, since)
    if data is not None:
        dest.write_bytes(data)
    elif not dest.exists():  # 304 but nothing cached — should never happen
        raise RuntimeError(f"{ALLSTAR_DB_URL} returned not-modified but no cached file at {dest}")
    return dest


def load_allstar(path: str | Path) -> dict[str, list[int]]:
    """Parse the AllMon node DB into ``callsign (UPPER) -> sorted unique node numbers``.

    The only I/O in this module beyond the downloader. Skips blank lines and rows without
    a numeric node number or a callsign. Node lists are sorted ascending and deduped for
    reproducible diffs.
    """
    nodes_by_call: dict[str, set[int]] = {}
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        fields = line.split(_DELIMITER)
        if len(fields) <= _CALLSIGN:
            continue
        callsign = fields[_CALLSIGN].strip().upper()
        if not callsign:
            continue
        try:
            node = int(fields[_NODE].strip())
        except ValueError:
            continue
        nodes_by_call.setdefault(callsign, set()).add(node)
    return {call: sorted(nodes) for call, nodes in nodes_by_call.items()}


def enrich_record(record: Record, lookup: dict[str, list[int]]) -> Record:
    """Return a NEW Record with `allstar_nodes` set from ``lookup``.

    Never mutates the input. Leaves ``allstar_nodes`` as ``[]`` (a fresh list copy) when
    the callsign has no registered nodes.
    """
    nodes = lookup.get(record.callsign.upper()) if record.callsign else None
    return replace(record, allstar_nodes=list(nodes) if nodes else [])


def enrich(records: Iterable[Record], lookup: dict[str, list[int]]) -> Iterator[Record]:
    """Apply `enrich_record` lazily across a stream of Records."""
    for record in records:
        yield enrich_record(record, lookup)
