"""POTA (Parks On The Air) park-directory importer (hdb-9640).

A SEPARATE reference dataset from the callsign `Record` schema — POTA parks are PLACES,
not licensees, so this module defines its own `ParkRecord` dataclass and does NOT touch
the callsign contract (mem-c6e0). Downstream apps (the adif park picker) do OFFLINE fuzzy
park search ("Boise" -> "Boise National Forest") and tag QSOs without live-hitting
pota.app.

Source / endpoint
-----------------
pota.app's public API (``api.pota.app``) is UNOFFICIAL and undocumented
(``docs.pota.app/api`` is "under construction"), "subject to change", and asks callers
not to hammer it. There is NO single global all-parks endpoint; the bulk listing is
served PER PROGRAM (per country/entity) at::

    https://api.pota.app/program/parks/<PREFIX>        e.g. /US, /CA, /G

Each returns a JSON array of park objects::

    {
      "reference": "US-0001",            # park reference, the primary key
      "name": "Acadia National Park",
      "latitude": 44.31, "longitude": -68.2034,
      "grid": "FN54vh",                  # 6-char Maidenhead — stored VERBATIM
      "locationDesc": "US-ME",           # ISO-ish region/subdivision code
      "active": 1,                       # present only when a park is retired (0)
      "attempts": 602, "activations": 536, "qsos": 18109   # stats (ignored)
    }

The full list of program prefixes is available at ``https://api.pota.app/programs``.

Coordinates / grid policy (RESOLVED in spec, 2026-06-17)
--------------------------------------------------------
POTA's single representative ``latitude``/``longitude``/``grid`` are INDICATIVE ONLY:
a large park (Boise National Forest) spans many grid squares, so one point is NOT
authoritative coverage. Therefore:

  * The person-grid 4-char truncation/privacy rule (mem-e3fd) does NOT apply — parks are
    public landmarks, so the grid is stored VERBATIM (6-char kept as-given).
  * ``grid``/``lat``/``lon`` are NULLABLE, display-only, NEVER a join key or PK.

The picker's fuzzy match keys on ``name`` + ``location_desc``/``region`` text + ``reference``,
never on grid, so coverage ambiguity for big parks doesn't affect search quality.

License
-------
POTA publishes no explicit data license. PROJECT DECISION (2026-06-17): treat POTA park
data as ASSUMED-NON-COMMERCIAL + attributed + droppable, the same posture as AllStarLink
(NOTICE source 5), fitting the dataset's CC BY-NC 4.0 umbrella. The NOTICE file credits
POTA (source 6).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, fields
from datetime import date
from email.utils import formatdate
from pathlib import Path

__all__ = [
    "PARKS_URL_TEMPLATE",
    "PARK_SCHEMA_COLUMNS",
    "DEFAULT_PROGRAMS",
    "DOWNLOAD_TIMEOUT",
    "ParkRecord",
    "PotaSource",
    "parse_file",
    "parse_dir",
]

# Per-program bulk listing (no global all-parks endpoint exists upstream).
PARKS_URL_TEMPLATE = "https://api.pota.app/program/parks/{program}"

# Connect/read timeout (seconds) so a hung upstream fails fast instead of stalling the
# build (mirrors the ISED / AllStarLink importers).
DOWNLOAD_TIMEOUT = 60

# Polite identifier so upstream can see who is pulling (the API asks callers not to
# hammer it).
_USER_AGENT = "hamcall-db/0 (+https://github.com/rcludwick/hamcall-db)"

# Default set of program prefixes to pull. Kept conservative (the largest amateur
# programs); the full list is at https://api.pota.app/programs. Override via
# PotaSource(programs=[...]). Adding a program here is additive — it only grows the
# park directory.
DEFAULT_PROGRAMS: tuple[str, ...] = ("US", "CA", "GB", "AU", "DE")


@dataclass(slots=True)
class ParkRecord:
    """One published POTA park row.

    SEPARATE from the callsign `Record` schema. ``reference`` is the primary key
    (e.g. "US-0001"). ``grid``/``lat``/``lon`` are INDICATIVE ONLY, nullable, display-only,
    and never a join key — POTA's verbatim values (no 4-char person-grid truncation).
    """

    reference: str  # primary key, e.g. "US-0001"
    name: str | None = None
    location_desc: str | None = None  # POTA locationDesc, e.g. "US-ME"
    region: str | None = None  # subdivision parsed from location_desc, e.g. "ME"
    country: str | None = None  # POTA program prefix / country, e.g. "US"
    dxcc: int | None = None  # DXCC entity number (nullable; not resolved here)
    grid: str | None = None  # Maidenhead, VERBATIM (may be 6-char); indicative only
    lat: float | None = None  # representative latitude; indicative only
    lon: float | None = None  # representative longitude; indicative only
    active: bool = True  # POTA active flag (False = retired park)
    source: str | None = "pota"
    synced_at: str | None = None  # ISO date of the upstream pull


PARK_SCHEMA_COLUMNS: tuple[str, ...] = tuple(f.name for f in fields(ParkRecord))
"""Ordered column names of the published parks artifact. The writer relies on this order."""


def _region_from_location_desc(location_desc: str | None) -> str | None:
    """Parse the subdivision out of a POTA ``locationDesc`` like ``"US-ME"`` -> ``"ME"``.

    Some parks span multiple subdivisions (``"US-ME,US-NH"``); we keep the FIRST
    subdivision as a coarse region hint (the full value stays in ``location_desc``).
    Returns None when there is no parseable subdivision.
    """
    if not location_desc:
        return None
    first = location_desc.split(",")[0].strip()
    if "-" in first:
        return first.split("-", 1)[1].strip() or None
    return first or None


def _coord(value: object) -> float | None:
    """Coerce a POTA coordinate to float, or None when missing/blank/unparseable."""
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _active_flag(obj: dict) -> bool:
    """POTA parks are active unless explicitly marked inactive (``active``/``isActive`` 0)."""
    raw = obj.get("active", obj.get("isActive"))
    if raw is None:
        return True
    if isinstance(raw, bool):
        return raw
    try:
        return int(raw) != 0
    except (TypeError, ValueError):
        return bool(raw)


def parse_obj(obj: dict, *, country: str | None, synced_at: str | None = None) -> ParkRecord:
    """Map one POTA park JSON object into a `ParkRecord`. Pure; no I/O."""
    location_desc = obj.get("locationDesc")
    grid = obj.get("grid")
    return ParkRecord(
        reference=str(obj["reference"]).strip(),
        name=(obj.get("name") or None),
        location_desc=(location_desc or None),
        region=_region_from_location_desc(location_desc),
        country=country,
        dxcc=None,
        grid=(grid.strip() if isinstance(grid, str) and grid.strip() else None),
        lat=_coord(obj.get("latitude")),
        lon=_coord(obj.get("longitude")),
        active=_active_flag(obj),
        source="pota",
        synced_at=synced_at,
    )


def parse_file(
    path: Path, *, country: str | None, synced_at: str | None = None
) -> Iterator[ParkRecord]:
    """Parse a single per-program POTA parks JSON file into `ParkRecord`s.

    The file is the JSON array returned by ``/program/parks/<PREFIX>``. ``country`` is
    the program prefix; ``synced_at`` stamps the upstream pull date.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for obj in data:
        if not isinstance(obj, dict) or not obj.get("reference"):
            continue
        yield parse_obj(obj, country=country, synced_at=synced_at)


def _country_from_filename(path: Path) -> str | None:
    """Infer the program prefix from a cached filename like ``parks_US.json`` -> ``"US"``."""
    stem = path.stem  # e.g. "parks_US"
    if "_" in stem:
        return stem.rsplit("_", 1)[1] or None
    return None


def parse_dir(directory: Path, *, synced_at: str | None = None) -> Iterator[ParkRecord]:
    """Parse every ``parks_<PREFIX>.json`` in a cache directory, inferring country.

    De-dups on ``reference`` (the primary key) across files, keeping the first seen.
    """
    seen: set[str] = set()
    for path in sorted(Path(directory).glob("parks_*.json")):
        country = _country_from_filename(path)
        for park in parse_file(path, country=country, synced_at=synced_at):
            if park.reference in seen:
                continue
            seen.add(park.reference)
            yield park


# Injectable fetch seam (matches ad1c.py / enrich_allstar.py): a fetcher takes
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


class PotaSource:
    """Importer for the POTA park directory.

    Unlike the callsign sources, this emits `ParkRecord`s (a separate dataset), so it does
    NOT implement the callsign `Source` protocol — it is wired into the build CLI as an
    additive parks step. Pulls one JSON file per program prefix, caching politely under
    ``data/raw/pota/<YYYY-MM-DD>/`` with If-Modified-Since.
    """

    name = "pota"

    def __init__(self, *, programs: Iterable[str] | None = None) -> None:
        self.programs: tuple[str, ...] = tuple(programs) if programs else DEFAULT_PROGRAMS
        # ISO date of the upstream pull; set during download(), stamped onto ParkRecords.
        self.synced_at: str | None = None

    def download(
        self,
        work_dir: Path,
        *,
        on: date | None = None,
        fetcher: Fetcher | None = None,
    ) -> Path:
        """Fetch each program's parks JSON, caching under ``work_dir/data/raw/pota/<date>/``.

        Returns the cache DIRECTORY (it holds one ``parks_<PREFIX>.json`` per program).
        Honors If-Modified-Since per file: an unchanged extract (HTTP 304) reuses the
        cached copy. ``fetcher`` is an injectable seam for tests (avoids the network);
        production leaves it None to use urllib. Sets ``self.synced_at`` to the pull date.
        """
        fetcher = fetcher or _urllib_fetch
        day = (on or date.today()).isoformat()
        cache_dir = work_dir / "data" / "raw" / "pota" / day
        cache_dir.mkdir(parents=True, exist_ok=True)

        for program in self.programs:
            dest = cache_dir / f"parks_{program}.json"
            url = PARKS_URL_TEMPLATE.format(program=program)
            since = dest.stat().st_mtime if dest.exists() else None
            data = fetcher(url, since)
            if data is not None:
                dest.write_bytes(data)
            elif not dest.exists():  # 304 but nothing cached — should never happen
                raise RuntimeError(
                    f"{url} returned not-modified but no cached file at {dest}"
                )

        self.synced_at = day
        return cache_dir

    def parse(
        self, path: Path, *, synced_at: str | None = None
    ) -> Iterable[ParkRecord]:
        """Parse a cache directory of per-program JSON files into `ParkRecord`s.

        When ``synced_at`` is None, falls back to ``self.synced_at`` (set by download()).
        """
        return parse_dir(path, synced_at=synced_at if synced_at is not None else self.synced_at)
