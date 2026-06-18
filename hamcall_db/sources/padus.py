"""PAD-US build-time adapter (hdb-f53c, PHASE 1: US/PAD-US, public domain).

This is the THIN seam between the multi-GB PAD-US national boundary file (a GeoPackage/GDB
that needs a GDAL-backed reader) and the PURE-shapely geometry core in ``padus_grids``.
Everything that touches GDAL or the network lives HERE; everything geometric lives in
``padus_grids`` and is unit-tested without GDAL or the download.

Responsibilities
----------------
  * ``download()``   — fetch + cache the PAD-US national file under
                       ``data/raw/padus/<YYYY-MM-DD>/`` with If-Modified-Since (PAD-US
                       updates ~annually, so a weekly build almost always reuses the cache).
  * ``_read_padus_features()`` — the GDAL seam: read features out of the cached file with
                       pyogrio, reproject EPSG:5070 -> WGS84 (EPSG:4326), and yield
                       ``PadusFeature``s. Imports pyogrio LAZILY so the rest of the module
                       (and all tests) run without GDAL installed.
  * ``compute_park_grids()`` — pure orchestration over a parks iterable: gate to US parks,
                       match each against the PAD-US features, intersect with the Maidenhead
                       lattice, and fall back to the point grid. Driven from injected
                       features in tests, so it needs neither GDAL nor the download.

LICENSE: PAD-US (USGS Protected Areas Database of the United States) is PUBLIC DOMAIN — the
only license-clean national boundary set (feasibility hdb-7d0c). OSM (ODbL share-alike) and
WDPA (no redistribution) are excluded; the OSM/international phase is the SEPARATE nugget
hdb-438b and must ship in a SEPARATE ODbL file — never mixed into this CC BY-NC artifact.
NOTICE credits PAD-US as a courtesy (mem-371f).
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from datetime import date
from email.utils import formatdate
from pathlib import Path

from hamcall_db.sources.padus_grids import PadusFeature, ParkGridRecord, park_grids
from hamcall_db.sources.pota import ParkRecord

__all__ = [
    "PADUS_DOWNLOAD_URL",
    "PADUS_ITEM_URL",
    "PADUS_COMBINED_LAYER_PREFIX",
    "DOWNLOAD_TIMEOUT",
    "PadusSource",
    "compute_park_grids",
]

# PAD-US national download (hdb-b6a7). PAD-US ships its NATIONAL inventory as a zipped File
# Geodatabase (~1.7 GB, EPSG:5070) — there is NO national GeoPackage (GeoPackage exists only
# as per-state extracts). The URL is a USGS ScienceBase per-file download link and is
# VERSION-SPECIFIC: it changes with every PAD-US release, so re-pin it on each major version
# bump. Human-readable landing page (where the next version's file link lives):
#   PADUS_ITEM_URL below. Override at build time via PadusSource(url=...).
# Pinned: PAD-US 4.0 Full Inventory Database (PADUS4_0Geodatabase.zip), released 2023.
PADUS_ITEM_URL = "https://www.sciencebase.gov/catalog/item/652ef930d34edd15305a9b03"
PADUS_DOWNLOAD_URL = "https://sciencebase.usgs.gov/manager/download/clvbj5bbs007715ms6e886v58"

# The GDB integrates six feature classes (Fee/Designation/Easement/Proclamation/Marine and
# the Combined layer that unions them all). We read the Combined layer; its exact name is
# version-suffixed (e.g. PADUS4_0Combined_Proclamation_Marine_Fee_Designation_Easement), so
# we select it by this prefix rather than hard-coding the full name.
PADUS_COMBINED_LAYER_PREFIX = "Combined"

DOWNLOAD_TIMEOUT = 600  # the file is large; allow a generous read timeout

_USER_AGENT = "hamcall-db/0 (+https://github.com/rcludwick/hamcall-db)"

# Source coordinate reference system PAD-US ships in (USGS Albers); we reproject to WGS84
# (EPSG:4326) so the geometry core's lon/lat lattice math is valid.
PADUS_SOURCE_CRS = "EPSG:5070"
WGS84_CRS = "EPSG:4326"

# PHASE 1 gates polygon matching to US parks only (POTA references like "US-4567" /
# country "US"). Everything else rides the point-grid fallback.
_US_COUNTRY = "US"


# Injectable fetch seam (matches pota.py / ad1c.py): (url, if_modified_since_or_None) ->
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
    """True for POTA parks in the US program (the PHASE 1 PAD-US slice)."""
    if park.country and park.country.upper() == _US_COUNTRY:
        return True
    return bool(park.reference) and park.reference.upper().startswith("US-")


class PadusSource:
    """Build-time importer for the PAD-US national boundary file.

    Emits no records itself; it caches the source file (``download``) and exposes the GDAL
    read seam (``read_features``) for ``compute_park_grids``. Like POTA, this is wired into
    the build as an ADDITIVE step, not a callsign ``Source``.
    """

    name = "padus"

    def __init__(self, *, url: str | None = None) -> None:
        self.url = url or PADUS_DOWNLOAD_URL
        self.synced_at: str | None = None

    def download(
        self,
        work_dir: Path,
        *,
        on: date | None = None,
        fetcher: Fetcher | None = None,
    ) -> Path:
        """Fetch + cache the PAD-US file under ``work_dir/data/raw/padus/<date>/``.

        Returns the cached FILE path. Honors If-Modified-Since: an unchanged file (HTTP
        304) reuses the cached copy (PAD-US updates ~annually, so weekly builds almost
        always hit the cache). ``fetcher`` is an injectable seam for tests (no network).
        """
        fetcher = fetcher or _urllib_fetch
        day = (on or date.today()).isoformat()
        cache_dir = work_dir / "data" / "raw" / "padus" / day
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Zipped File Geodatabase, not a GeoPackage — GDAL reads the inner .gdb via /vsizip/.
        dest = cache_dir / "padus_national.gdb.zip"

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

    def read_features(self, path: Path) -> list[PadusFeature]:
        """Read PAD-US features from the cached file (delegates to the GDAL seam)."""
        return list(_read_padus_features(path))


def _vsizip_path(path: Path) -> str:
    """GDAL virtual-filesystem path for reading a layer out of a ``.zip`` in place.

    PAD-US ships the national inventory as a zipped File Geodatabase; GDAL opens the inner
    ``.gdb`` directly through the ``/vsizip/`` handler, so we never unzip 1.7 GB to disk.
    """
    return f"/vsizip/{path}"


def _select_combined_layer(layer_names: Iterable[str]) -> str:
    """Pick the integrated 'Combined' feature class from the GDB's layer list.

    The Combined layer unions Fee/Designation/Easement/Proclamation/Marine, so reading it
    alone gives every protected unit once. Its full name is version-suffixed
    (``PADUS4_0Combined_...``), hence prefix matching rather than a hard-coded name.
    """
    for name in layer_names:
        if PADUS_COMBINED_LAYER_PREFIX.lower() in name.lower():
            return name
    raise RuntimeError(
        "No 'Combined' feature class found in the PAD-US geodatabase "
        f"(layers: {sorted(layer_names)}). The national GDB layout may have changed; "
        "re-pin PADUS_COMBINED_LAYER_PREFIX / PADUS_DOWNLOAD_URL (hdb-b6a7)."
    )


def _read_padus_features(path: Path) -> Iterator[PadusFeature]:
    """GDAL seam: read PAD-US features from the zipped GDB at ``path``, reprojected to WGS84.

    Uses pyogrio (GDAL-backed) — imported LAZILY so this module and all of its tests run
    without GDAL installed. Install the reader with ``uv sync --group padus``. Opens the
    Combined feature class inside the ``.gdb.zip`` via ``/vsizip/`` (no unzip), yields
    ``PadusFeature``s carrying the Unit_Nm / Loc_Nm name columns and a WGS84 shapely
    geometry. This is the ONLY place GDAL is touched; the geometry core never sees a file.
    """
    try:
        import pyogrio
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised only in a real build
        raise RuntimeError(
            "Reading the PAD-US file requires the GDAL-backed reader 'pyogrio'. "
            "Install it with `uv sync --group padus`. (The geometry core and its tests "
            "do not need it.)"
        ) from exc

    # pragma: no cover below — exercised only in a real build with GDAL + the 1.7 GB GDB.
    src = _vsizip_path(path)  # /vsizip/.../padus_national.gdb.zip
    layers = [row[0] for row in pyogrio.list_layers(src)]
    layer = _select_combined_layer(layers)

    # Read the Combined layer, reprojecting from PAD-US's source CRS (EPSG:5070 Albers) to
    # WGS84 so the geometry core's lon/lat lattice math is valid. Unit_Nm is the
    # standardized protected-area name; Loc_Nm is the source's local name (fallback).
    geo = pyogrio.read_dataframe(src, layer=layer, columns=["Unit_Nm", "Loc_Nm"])
    geo = geo.to_crs(WGS84_CRS)
    for row in geo.itertuples(index=False):
        geometry = getattr(row, "geometry", None)
        if geometry is None or geometry.is_empty:
            continue
        yield PadusFeature(
            unit_nm=getattr(row, "Unit_Nm", None),
            loc_nm=getattr(row, "Loc_Nm", None),
            geometry=geometry,
        )


def compute_park_grids(
    parks: Iterable[ParkRecord],
    features: list[PadusFeature],
) -> list[ParkGridRecord]:
    """Compute ``pota_park_grids`` rows for every park (the build-time orchestration).

    PHASE 1 gates polygon matching to US parks (the PAD-US slice); every other park — and
    any unmatched US park — degrades to the point-grid fallback inside ``park_grids``. A
    single park's failure NEVER blocks the build: each is guarded so a bad geometry or a
    matching hiccup just falls back to (or omits) that park's rows. Returns the flat list
    of rows for the writers.
    """
    rows: list[ParkGridRecord] = []
    for park in parks:
        park_features = features if _is_us_park(park) else []
        try:
            rows.extend(
                park_grids(
                    reference=park.reference,
                    name=park.name,
                    lat=park.lat,
                    lon=park.lon,
                    features=park_features,
                )
            )
        except Exception:  # never block the build on one park
            # Best-effort point fallback so the park still appears if it has a coord.
            if park.lat is not None and park.lon is not None:
                rows.extend(
                    park_grids(
                        reference=park.reference,
                        name=park.name,
                        lat=park.lat,
                        lon=park.lon,
                        features=[],
                    )
                )
    return rows
