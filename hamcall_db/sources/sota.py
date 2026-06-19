"""SOTA (Summits on the Air) summit-directory importer (hdb-ca00).

A SEPARATE reference dataset from the callsign `Record` schema — SOTA summits are PLACES
(mountain landmarks), not licensees, so this module defines its own `SummitRecord`
dataclass and does NOT touch the callsign contract (mem-c6e0). It is a sibling of the POTA
parks dataset (hamcall_db/sources/pota.py) and mirrors its structure: own Parquet file
(``hamcall-db-sota-summits-YYYY-MM-DD.parquet``) + a ``sota_summits`` table in the SQLite
``.db``.

Source / endpoint
-----------------
SOTA publishes a single bulk ``summitslist.csv`` as a STATIC file at
``https://storage.sota.org.uk/summitslist.csv`` (NOT the gated ``api2.sota.org.uk`` JSON
API — see the License note below for why we deliberately avoid the API). The file is a
CSV with a one-line title header (``SOTA Summits List (Date=DD/MM/YYYY)``) followed by a
column-header row::

    SummitCode,AssociationName,RegionName,SummitName,AltM,AltFt,GridRef1,GridRef2,
    Longitude,Latitude,Points,BonusPoints,ValidFrom,ValidTo,ActivationCount,
    ActivationDate,ActivationCall

``SummitCode`` (e.g. ``G/LD-001``) is the primary key. ``GridRef1``/``GridRef2`` are
upstream OSGB-style grid columns we do NOT use; the published Maidenhead ``grid`` is
derived from ``Longitude``/``Latitude`` at full 6-char precision (see below).

Coordinates / grid policy
-------------------------
SOTA's ``Longitude``/``Latitude`` is the summit's indicative point — a PUBLIC LANDMARK,
not a person's location. Therefore, exactly like POTA parks:

  * The person-grid 4-char truncation/privacy rule (mem-e3fd) does NOT apply. The
    published ``grid`` is the FULL 6-char Maidenhead subsquare, stored VERBATIM.
  * ``grid``/``lat``/``lon`` are NULLABLE, display-only, never a join key or PK.

License (ASSUMED NON-COMMERCIAL — flagged for human sign-off, see NOTICE source 9)
---------------------------------------------------------------------------------
SOTA's *API* terms of service are explicit: NON-COMMERCIAL only, registration required,
and "no AI-generated software may connect to the SOTA API without prior approval". To stay
clear of the API ToS we consume the STATIC ``summitslist.csv`` bulk file, NOT the API.

The static bulk file carries no explicit machine-readable license. No SHARE-ALIKE term
(CC BY-SA / ODbL) was found for it, so — unlike OpenStreetMap (osm.py) — it does NOT need
OSM-style separate-file segregation. PROJECT POSTURE (2026-06-18): treat SOTA summit data
as ASSUMED NON-COMMERCIAL + attributed + droppable, the SAME posture as POTA (pota.py) and
AllStarLink, which fits the dataset's CC BY-NC 4.0 umbrella. Given SOTA's explicit
non-commercial stance this is conservative, but it is NOT a confirmed license: FLAGGED FOR
HUMAN SIGN-OFF before the first public release. The NOTICE file credits SOTA (source 9).
"""

from __future__ import annotations

import csv
import io
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, fields
from datetime import date, datetime
from email.utils import formatdate
from pathlib import Path

__all__ = [
    "SUMMITS_URL",
    "SUMMIT_SCHEMA_COLUMNS",
    "DOWNLOAD_TIMEOUT",
    "SummitRecord",
    "SotaSource",
    "parse_file",
]

# Static bulk summit list (a single CSV; NOT the gated api2.sota.org.uk JSON API).
SUMMITS_URL = "https://storage.sota.org.uk/summitslist.csv"

# Connect/read timeout (seconds) so a hung upstream fails fast instead of stalling the
# build (mirrors the POTA / ISED importers).
DOWNLOAD_TIMEOUT = 120

# Polite identifier so upstream can see who is pulling.
_USER_AGENT = "hamcall-db/0 (+https://github.com/rcludwick/hamcall-db)"

# Maidenhead field alphabet (A-R), used for the 6-char locator derivation.
_FIELD = "ABCDEFGHIJKLMNOPQR"


@dataclass(slots=True)
class SummitRecord:
    """One published SOTA summit row.

    SEPARATE from the callsign `Record` schema. ``reference`` is the primary key
    (SOTA ``SummitCode``, e.g. "G/LD-001"). ``grid``/``lat``/``lon`` are INDICATIVE ONLY,
    nullable, display-only, and never a join key — the 6-char locator is stored VERBATIM
    (no person-grid 4-char truncation; summits are public landmarks).
    """

    reference: str  # primary key, SOTA SummitCode, e.g. "G/LD-001"
    name: str | None = None
    association: str | None = None  # SOTA AssociationName, e.g. "England (Lake District)"
    region: str | None = None  # SOTA RegionName, e.g. "Lake District"
    alt_m: int | None = None  # altitude, metres
    alt_ft: int | None = None  # altitude, feet
    grid: str | None = None  # 6-char Maidenhead, VERBATIM; indicative only
    lat: float | None = None  # summit latitude; indicative only
    lon: float | None = None  # summit longitude; indicative only
    points: int | None = None  # SOTA points value
    bonus_points: int | None = None  # SOTA seasonal bonus points
    valid_from: str | None = None  # ISO date the summit became valid
    valid_to: str | None = None  # ISO date the summit's validity ends
    active: bool = True  # False when valid_to is in the past (retired summit)
    source: str | None = "sota"
    synced_at: str | None = None  # ISO date of the upstream pull


SUMMIT_SCHEMA_COLUMNS: tuple[str, ...] = tuple(f.name for f in fields(SummitRecord))
"""Ordered column names of the published summits artifact. The writer relies on this order."""


def latlon_to_grid6(lat: float, lon: float) -> str:
    """Return the 6-char Maidenhead locator (field + square + subsquare) for ``lat``/``lon``.

    Correct in both hemispheres. UNLIKE the privacy-truncated person-grid helper in
    ``hamcall_db.geocode`` (4-char only, mem-e3fd), this yields the FULL 6-char subsquare:
    SOTA summits are public landmarks, so the truncation rule does not apply.
    """
    adj_lon = min(max(lon + 180.0, 0.0), 359.999999)
    adj_lat = min(max(lat + 90.0, 0.0), 179.999999)

    lon_field = int(adj_lon // 20)
    lat_field = int(adj_lat // 10)
    lon_square = int((adj_lon % 20) // 2)
    lat_square = int(adj_lat % 10)
    lon_sub = int(((adj_lon % 2) / 2) * 24)
    lat_sub = int(((adj_lat % 1) / 1) * 24)

    return (
        f"{_FIELD[lon_field]}{_FIELD[lat_field]}"
        f"{lon_square}{lat_square}"
        f"{chr(ord('a') + lon_sub)}{chr(ord('a') + lat_sub)}"
    )


def _int(value: str | None) -> int | None:
    """Coerce a SOTA integer column to int, or None when missing/blank/unparseable."""
    if value is None or value.strip() == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float(value: str | None) -> float | None:
    """Coerce a SOTA coordinate to float, or None when missing/blank/unparseable."""
    if value is None or value.strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso_date(value: str | None) -> str | None:
    """Normalize a SOTA ``DD/MM/YYYY`` date to ISO ``YYYY-MM-DD``; None if unparseable."""
    if value is None or value.strip() == "":
        return None
    raw = value.strip()
    try:
        return datetime.strptime(raw, "%d/%m/%Y").date().isoformat()
    except ValueError:
        # Already ISO, or some other format — keep verbatim rather than drop it.
        return raw


def _active_from_valid_to(valid_to_iso: str | None, *, on: date | None = None) -> bool:
    """A summit is active unless its (ISO) ``valid_to`` is strictly in the past."""
    if not valid_to_iso:
        return True
    try:
        end = date.fromisoformat(valid_to_iso)
    except ValueError:
        return True  # unparseable bound -> assume active rather than silently retire it
    return end >= (on or date.today())


def parse_row(row: dict[str, str], *, synced_at: str | None = None) -> SummitRecord | None:
    """Map one ``summitslist.csv`` row (as a dict) into a `SummitRecord`. Pure; no I/O.

    Returns None for a row with no ``SummitCode`` (blank/trailing line).
    """
    reference = (row.get("SummitCode") or "").strip()
    if not reference:
        return None

    lat = _float(row.get("Latitude"))
    lon = _float(row.get("Longitude"))
    grid = latlon_to_grid6(lat, lon) if lat is not None and lon is not None else None
    valid_to = _iso_date(row.get("ValidTo"))

    return SummitRecord(
        reference=reference,
        name=((row.get("SummitName") or "").strip() or None),
        association=((row.get("AssociationName") or "").strip() or None),
        region=((row.get("RegionName") or "").strip() or None),
        alt_m=_int(row.get("AltM")),
        alt_ft=_int(row.get("AltFt")),
        grid=grid,
        lat=lat,
        lon=lon,
        points=_int(row.get("Points")),
        bonus_points=_int(row.get("BonusPoints")),
        valid_from=_iso_date(row.get("ValidFrom")),
        valid_to=valid_to,
        active=_active_from_valid_to(valid_to),
        source="sota",
        synced_at=synced_at,
    )


def _reader(text: str) -> csv.DictReader:
    """Build a DictReader over the CSV body, skipping the one-line title header.

    The file begins with a ``SOTA Summits List (Date=...)`` title line, then the real
    column-header row. Drop the title line before handing the rest to ``csv``.
    """
    lines = text.splitlines(keepends=True)
    if lines and lines[0].lstrip().startswith("SOTA Summits List"):
        lines = lines[1:]
    return csv.DictReader(io.StringIO("".join(lines)))


def parse_file(path: Path, *, synced_at: str | None = None) -> Iterator[SummitRecord]:
    """Parse a ``summitslist.csv`` file into `SummitRecord`s.

    De-dups on ``reference`` (the primary key), keeping the first seen. ``synced_at``
    stamps the upstream pull date onto every record.
    """
    text = Path(path).read_text(encoding="utf-8")
    seen: set[str] = set()
    for row in _reader(text):
        summit = parse_row(row, synced_at=synced_at)
        if summit is None or summit.reference in seen:
            continue
        seen.add(summit.reference)
        yield summit


# Injectable fetch seam (matches pota.py / osm.py): a fetcher takes
# (url, if_modified_since_epoch_or_None) and returns the raw bytes, or None for
# "not modified — keep the cached copy".
Fetcher = Callable[[str, "float | None"], "bytes | None"]


def _urllib_fetch(url: str, if_modified_since: float | None) -> bytes | None:
    """Default fetcher: conditional GET via urllib. None => 304 Not Modified."""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    if if_modified_since is not None:
        request.add_header("If-Modified-Since", formatdate(if_modified_since, usegmt=True))
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:  # noqa: S310 (https)
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 304:  # unchanged — caller reuses cache
            return None
        raise


class SotaSource:
    """Importer for the SOTA summit directory.

    Unlike the callsign sources, this emits `SummitRecord`s (a separate dataset), so it does
    NOT implement the callsign `Source` protocol — it is wired into the build CLI as an
    additive summits step. Pulls the single static ``summitslist.csv``, caching politely
    under ``data/raw/sota/<YYYY-MM-DD>/`` with If-Modified-Since.
    """

    name = "sota"

    def __init__(self, *, url: str | None = None) -> None:
        self.url = url or SUMMITS_URL
        # ISO date of the upstream pull; set during download(), stamped onto SummitRecords.
        self.synced_at: str | None = None

    def download(
        self,
        work_dir: Path,
        *,
        on: date | None = None,
        fetcher: Fetcher | None = None,
    ) -> Path:
        """Fetch ``summitslist.csv``, caching under ``work_dir/data/raw/sota/<date>/``.

        Returns the cached FILE path. Honors If-Modified-Since: an unchanged extract
        (HTTP 304) reuses the cached copy. ``fetcher`` is an injectable seam for tests
        (avoids the network); production leaves it None to use urllib. Sets
        ``self.synced_at`` to the pull date.
        """
        fetcher = fetcher or _urllib_fetch
        day = (on or date.today()).isoformat()
        cache_dir = work_dir / "data" / "raw" / "sota" / day
        cache_dir.mkdir(parents=True, exist_ok=True)

        dest = cache_dir / "summitslist.csv"
        since = dest.stat().st_mtime if dest.exists() else None
        data = fetcher(self.url, since)
        if data is not None:
            dest.write_bytes(data)
        elif not dest.exists():  # 304 but nothing cached — should never happen
            raise RuntimeError(
                f"{self.url} returned not-modified but no cached file at {dest}"
            )

        self.synced_at = day
        return dest

    def parse(
        self, path: Path, *, synced_at: str | None = None
    ) -> Iterable[SummitRecord]:
        """Parse a cached ``summitslist.csv`` into `SummitRecord`s.

        When ``synced_at`` is None, falls back to ``self.synced_at`` (set by download()).
        """
        return parse_file(
            path, synced_at=synced_at if synced_at is not None else self.synced_at
        )
