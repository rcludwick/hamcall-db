"""OpenStreetMap build-time adapter (hdb-438b, PHASE 2: OSM/international, ODbL).

This is the THIN seam between OpenStreetMap regional extracts (Geofabrik ``.osm.pbf`` /
GeoPackage files that need a GIS reader) and the PURE-shapely matching core in
``osm_grids``. Everything that touches the GIS reader or the network lives HERE; everything
geometric lives in ``osm_grids`` (which itself REUSES the shared ``grid_cells_for_geometry``
core from ``padus_grids``) and is unit-tested without the GIS reader or any download.

LICENSE SEGREGATION (non-negotiable)
------------------------------------
OpenStreetMap data is ODbL (share-alike). Grids DERIVED from OSM boundaries form an ODbL
DERIVATIVE DATABASE, INCOMPATIBLE with the main CC BY-NC artifact. This adapter feeds ONLY
the SEPARATE ODbL artifact (``hamcall-db-pota-park-grids-osm-YYYY-MM-DD.parquet`` and the
``pota_park_grids_osm`` table in ``hamcall-db-pota-park-grids-osm-YYYY-MM-DD.db``). It is
NEVER joined into the callsign/.db or the PAD-US ``pota_park_grids`` set. ``(c) OpenStreetMap
contributors``, ODbL (see the ODbL LICENSE shipped with the OSM artifact + NOTICE, mem-371f).

US parks are EXCLUDED here: they are owned by the PAD-US (public-domain / CC BY-NC) set so
the two artifacts never overlap on US references. PHASE 2 covers the non-US world.

Responsibilities
----------------
  * ``download()``  — fetch + cache the configured OSM regional extracts under
                      ``data/raw/osm/<YYYY-MM-DD>/`` with If-Modified-Since (Geofabrik
                      publishes daily extracts; a weekly build refetches at most weekly).
  * ``_read_osm_features()`` — the GIS seam: read boundary features (boundary=protected_area
                      / leisure=park / similar) out of a cached extract with pyogrio, yield
                      ``OsmFeature``s. Imports the reader LAZILY so the rest of the module
                      (and all tests) run without the GIS reader installed. OSM is natively
                      WGS84 (EPSG:4326), so no reprojection is needed.
  * ``compute_osm_park_grids()`` — pure orchestration over a parks iterable: gate OUT US
                      parks, match each non-US park against the OSM features, intersect with
                      the Maidenhead lattice, fall back to the point grid. Driven from
                      injected features in tests, so it needs neither the GIS reader nor a
                      download.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from datetime import date
from email.utils import formatdate
from pathlib import Path

from hamcall_db.sources._gis import require_pyogrio
from hamcall_db.sources.osm_grids import OsmFeature, ParkGridRecord, osm_park_grids
from hamcall_db.sources.pota import ParkRecord

__all__ = [
    "GEOFABRIK_BASE_URL",
    "DEFAULT_OSM_REGIONS",
    "DOWNLOAD_TIMEOUT",
    "OsmSource",
    "compute_osm_park_grids",
]

# Geofabrik regional extract base. Each region's protected-area boundaries ship as a
# ``.osm.pbf`` (and a ``-free.shp.zip``); the reader seam below opens it via GDAL/pyogrio.
# PIN-AT-BUILD-TIME: the exact region list / file flavor is a build-time decision (a global
# planet download is intentionally avoided — it is huge). Override via OsmSource(regions=...,
# base_url=...). Default region list is a conservative non-US starter; extend per coverage
# goals at build time.
GEOFABRIK_BASE_URL = "https://download.geofabrik.de/"

# Conservative default non-US region set (Geofabrik paths, sans extension). This is a
# PLACEHOLDER coverage list to pin/expand at build time — NOT exercised in tests/CI (tests
# inject features). Keeping it small avoids implying a planet-scale fetch.
DEFAULT_OSM_REGIONS: tuple[str, ...] = (
    "europe/germany",
    "europe/great-britain",
    "north-america/canada",
)

DOWNLOAD_TIMEOUT = 1800  # OSM regional extracts are large; allow a generous read timeout

_USER_AGENT = "hamcall-db/0 (+https://github.com/rcludwick/hamcall-db)"

# OSM is natively WGS84 (EPSG:4326) — the lon/lat lattice math is valid as-is; no reproject.
WGS84_CRS = "EPSG:4326"

# US parks are owned by the PAD-US/CC-BY-NC set; they are excluded from the OSM set so the
# two artifacts never overlap on US references.
_US_COUNTRY = "US"

# Geofabrik extract flavor appended to a region path. ``.osm.pbf`` is the canonical raw
# extract the GIS reader opens; pinned here, overridable via OsmSource(extract_suffix=...).
_EXTRACT_SUFFIX = "-latest.osm.pbf"


# Injectable fetch seam (matches padus.py / pota.py): (url, if_modified_since_or_None) ->
# raw bytes, or None for "not modified — keep the cached copy".
Fetcher = Callable[[str, "float | None"], "bytes | None"]


def _urllib_fetch(url: str, if_modified_since: float | None) -> bytes | None:
    """Default fetcher: conditional GET via urllib. None => 304 Not Modified."""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    if if_modified_since is not None:
        request.add_header("If-Modified-Since", formatdate(if_modified_since, usegmt=True))
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:  # noqa: S310
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 304:  # unchanged — caller reuses cache
            return None
        raise


def _is_us_park(park: ParkRecord) -> bool:
    """True for POTA parks in the US program (owned by the PAD-US set, excluded here)."""
    if park.country and park.country.upper() == _US_COUNTRY:
        return True
    return bool(park.reference) and park.reference.upper().startswith("US-")


class OsmSource:
    """Build-time importer for OpenStreetMap regional boundary extracts (ODbL).

    Emits no records itself; it caches the configured regional extracts (``download``) and
    exposes the GIS read seam (``read_features``) for ``compute_osm_park_grids``. Like POTA
    and PAD-US, this is wired into the build as an ADDITIVE step feeding ONLY the separate
    ODbL artifact, not a callsign ``Source``.
    """

    name = "osm"

    def __init__(
        self,
        *,
        regions: Iterable[str] | None = None,
        base_url: str | None = None,
        extract_suffix: str | None = None,
    ) -> None:
        self.regions = tuple(regions) if regions is not None else DEFAULT_OSM_REGIONS
        self.base_url = base_url or GEOFABRIK_BASE_URL
        self.extract_suffix = extract_suffix or _EXTRACT_SUFFIX
        self.synced_at: str | None = None

    def require_reader(self) -> None:
        """Probe for the GIS reader (pyogrio) WITHOUT downloading anything.

        Call this BEFORE :meth:`download`: the configured Geofabrik extracts are multi-GB, so
        a build without the ``osm`` group must skip the download and degrade to point grids
        rather than pull gigabytes only to fail on the read (hdb-66be). Raises the actionable
        ``RuntimeError`` when pyogrio is absent.
        """
        require_pyogrio("osm")

    def _region_url(self, region: str) -> str:
        return f"{self.base_url.rstrip('/')}/{region}{self.extract_suffix}"

    def download(
        self,
        work_dir: Path,
        *,
        on: date | None = None,
        fetcher: Fetcher | None = None,
    ) -> list[Path]:
        """Fetch + cache each configured OSM extract under ``work_dir/data/raw/osm/<date>/``.

        Returns the list of cached FILE paths (one per region). Honors If-Modified-Since: an
        unchanged extract (HTTP 304) reuses the cached copy. ``fetcher`` is an injectable
        seam for tests (no network). A single region's failure is the caller's concern; this
        method raises on a hard fetch error so the guarded build block can fall back.
        """
        fetcher = fetcher or _urllib_fetch
        day = (on or date.today()).isoformat()
        cache_dir = work_dir / "data" / "raw" / "osm" / day
        cache_dir.mkdir(parents=True, exist_ok=True)

        paths: list[Path] = []
        for region in self.regions:
            # Flatten the region path ("europe/germany" -> "europe_germany") for the cache
            # filename so it is a single file, not a nested dir tree.
            stem = region.replace("/", "_")
            dest = cache_dir / f"{stem}{self.extract_suffix}"
            since = dest.stat().st_mtime if dest.exists() else None
            data = fetcher(self._region_url(region), since)
            if data is not None:
                dest.write_bytes(data)
            elif not dest.exists():  # 304 but nothing cached — should never happen
                raise RuntimeError(
                    f"{self._region_url(region)} returned not-modified but no cached file "
                    f"at {dest}"
                )
            paths.append(dest)

        self.synced_at = day
        return paths

    def read_features(self, paths: Iterable[Path]) -> list[OsmFeature]:
        """Read OSM boundary features from cached extracts (delegates to the GIS seam)."""
        features: list[OsmFeature] = []
        for path in paths:
            features.extend(_read_osm_features(path))
        return features


def _read_osm_features(path: Path) -> Iterator[OsmFeature]:
    """GIS seam: read protected-area / park boundary features from an OSM extract.

    Uses pyogrio (GDAL-backed) — imported LAZILY so this module and all of its tests run
    without the GIS reader installed. Install it with ``uv sync --group osm``. Yields
    ``OsmFeature``s carrying the OSM ``name`` tag, the raw tags, the element id, and a WGS84
    shapely geometry (OSM is natively EPSG:4326, so no reprojection). This is the ONLY place
    the GIS reader is touched; the matching core never sees a file.
    """
    require_pyogrio("osm")
    import pyogrio

    # OSM extracts expose a 'multipolygons' layer carrying boundary/leisure relations. Read
    # the boundary-bearing rows; the geometries are already WGS84. We keep features tagged
    # as protected areas or parks (the boundary types POTA parks map to).
    frame = pyogrio.read_dataframe(
        path,
        layer="multipolygons",
        columns=["name", "boundary", "leisure", "osm_id", "osm_way_id"],
    )
    for row in frame.itertuples(index=False):
        geometry = getattr(row, "geometry", None)
        if geometry is None or geometry.is_empty:
            continue
        boundary = getattr(row, "boundary", None)
        leisure = getattr(row, "leisure", None)
        # Keep only protected-area / park boundaries (the POTA-relevant feature types).
        if boundary not in ("protected_area", "national_park") and leisure != "park":
            continue
        tags: dict[str, str] = {}
        if boundary:
            tags["boundary"] = str(boundary)
        if leisure:
            tags["leisure"] = str(leisure)
        osm_id = getattr(row, "osm_id", None) or getattr(row, "osm_way_id", None)
        yield OsmFeature(
            name=getattr(row, "name", None),
            tags=tags,
            osm_id=str(osm_id) if osm_id is not None else None,
            geometry=geometry,
        )


def compute_osm_park_grids(
    parks: Iterable[ParkRecord],
    features: list[OsmFeature],
) -> list[ParkGridRecord]:
    """Compute the OSM ``pota_park_grids_osm`` rows for every NON-US park.

    PHASE 2 gates OUT US parks entirely (they are owned by the PAD-US/CC-BY-NC set, so the
    OSM artifact never overlaps them — not even a point fallback). Every other park is
    matched against the OSM features and intersected with the Maidenhead lattice; an
    unmatched non-US park degrades to the point-grid fallback inside ``osm_park_grids``. A
    single park's failure NEVER blocks the build: each is guarded so a bad geometry or a
    matching hiccup just falls back to (or omits) that park's rows. Returns the flat list of
    rows for the OSM writers — ALL ODbL, written ONLY to the separate ODbL artifact.
    """
    rows: list[ParkGridRecord] = []
    for park in parks:
        if _is_us_park(park):
            continue  # owned by the PAD-US set; excluded from the ODbL artifact
        try:
            rows.extend(
                osm_park_grids(
                    reference=park.reference,
                    name=park.name,
                    lat=park.lat,
                    lon=park.lon,
                    features=features,
                )
            )
        except Exception:  # never block the build on one park
            # Best-effort point fallback so the park still appears if it has a coord.
            if park.lat is not None and park.lon is not None:
                rows.extend(
                    osm_park_grids(
                        reference=park.reference,
                        name=park.name,
                        lat=park.lat,
                        lon=park.lon,
                        features=[],
                    )
                )
    return rows
