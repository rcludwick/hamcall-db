"""POTA park -> Maidenhead 4-char grid-set geometry CORE (hdb-f53c, PHASE 1: US/PAD-US).

This module is the PURE-shapely heart of the park-grids feature: lattice generation, TRUE
polygon intersection, park<->boundary matching, and the point-grid fallback. It has NO
GDAL dependency and never touches the network — it takes shapely geometries that the thin
build-time adapter (``hamcall_db.sources.padus``) reads out of the PAD-US GeoPackage/GDB.
Keeping the geometry here means the whole computation is unit-tested with small hand-built
WKT/GeoJSON fixtures (including a holed polygon), with neither GDAL nor the multi-GB PAD-US
download in the test path.

WHY US-ONLY (feasibility hdb-7d0c): PAD-US (USGS Protected Areas DB) is the only
license-clean (public-domain) national boundary set. OSM is ODbL share-alike (incompatible
with the artifact's CC BY-NC) and WDPA prohibits redistribution. The OSM/international phase
is a SEPARATE nugget (hdb-438b); see the seam note at the bottom of this file.

Holes / inholdings (spec DECISION, user 2026-06-17)
---------------------------------------------------
Park boundaries have interior rings — canonical example: Boise National Forest has a hole
around the Bogus Basin ski-area inholding. We use shapely's TRUE intersection, which honors
interior rings and multipolygons natively. There is NO bbox / convex-hull / hole-fill
shortcut anywhere in this module: a candidate grid cell is in the set iff the (possibly
holed, possibly multi-part) park geometry genuinely intersects that cell. A park's
representative POINT may legitimately fall inside a hole; matching tolerates that.

Output rows
-----------
``ParkGridRecord(reference, grid, source, confidence)`` — the child table
``pota_park_grids`` keyed back to the parks dataset (hdb-9640) by ``reference``.

  * ``grid``       4-char Maidenhead field+square (never 6-char; mem-e3fd).
  * ``source``     ``"padus"`` (intersected a matched PAD-US polygon) or ``"pota-point"``
                   (point-grid fallback for unmatched/non-matching parks).
  * ``confidence`` ``"high"`` (point-in-polygon AND name match), ``"medium"`` (point in a
                   single polygon, name didn't match), ``"low"`` (point-grid fallback).
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from difflib import SequenceMatcher

from shapely.geometry import Point, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.prepared import prep

from hamcall_db.geocode import latlon_to_grid

__all__ = [
    "GRID_LON_DEG",
    "GRID_LAT_DEG",
    "PARK_GRID_SCHEMA_COLUMNS",
    "PadusFeature",
    "ParkGridRecord",
    "grid_cells_for_geometry",
    "match_park",
    "park_grids",
]

# A 4-char Maidenhead grid is a 2deg-lon x 1deg-lat rectangle on a lattice with origin
# (-180, -90). These are the cell dimensions; the lattice is snapped to them below.
GRID_LON_DEG = 2.0
GRID_LAT_DEG = 1.0
_LON_ORIGIN = -180.0
_LAT_ORIGIN = -90.0

# Fuzzy name-match acceptance threshold (SequenceMatcher ratio over normalized names).
# Above this counts as a name match (=> "high" confidence). Tuned permissively because
# POTA and PAD-US name the same place differently ("Boise NF" vs "Boise National Forest").
_NAME_MATCH_THRESHOLD = 0.6


@dataclass(slots=True)
class PadusFeature:
    """One PAD-US boundary feature handed to the core by the build-time adapter.

    ``geometry`` is a shapely (Multi)Polygon already reprojected to WGS84 (EPSG:4326).
    ``unit_nm`` / ``loc_nm`` are PAD-US's Unit_Nm / Loc_Nm name columns used for fuzzy
    disambiguation. The core never reads files — it only sees these.
    """

    unit_nm: str | None
    loc_nm: str | None
    geometry: BaseGeometry


@dataclass(slots=True)
class ParkGridRecord:
    """One published ``pota_park_grids`` row: (reference, grid, source, confidence)."""

    reference: str  # POTA park reference, FK to the parks dataset (e.g. "US-4567")
    grid: str  # 4-char Maidenhead field+square (mem-e3fd)
    source: str  # "padus" (polygon intersection) or "pota-point" (fallback)
    confidence: str  # "high" | "medium" | "low"


PARK_GRID_SCHEMA_COLUMNS: tuple[str, ...] = tuple(f.name for f in fields(ParkGridRecord))
"""Ordered column names of the pota_park_grids child table. The writers rely on this."""


def _snap_down(value: float, origin: float, step: float) -> float:
    """Largest lattice line <= ``value`` on the (origin, step) lattice."""
    n = (value - origin) // step
    return origin + n * step


def grid_cells_for_geometry(geom: BaseGeometry) -> set[str]:
    """Return the SET of 4-char Maidenhead grids the geometry truly intersects.

    Generates candidate 2x1 deg cells over the geometry's bbox, snaps them to the
    Maidenhead lattice (origin -180,-90), and keeps a cell iff a PREPARED copy of the
    (possibly holed / multi-part) geometry genuinely intersects that cell. No bbox /
    convex-hull / hole-fill shortcut: an inholding hole that a cell falls entirely inside
    excludes that cell, exactly as shapely's true intersection dictates. Grids are 4-char
    only (mem-e3fd).
    """
    if geom.is_empty:
        return set()

    min_lon, min_lat, max_lon, max_lat = geom.bounds
    prepared = prep(geom)
    grids: set[str] = set()

    lon = _snap_down(min_lon, _LON_ORIGIN, GRID_LON_DEG)
    while lon < max_lon:
        lat = _snap_down(min_lat, _LAT_ORIGIN, GRID_LAT_DEG)
        while lat < max_lat:
            cell = box(lon, lat, lon + GRID_LON_DEG, lat + GRID_LAT_DEG)
            # TRUE intersection (handles interior rings / multipolygons natively): a cell
            # sitting wholly inside an inholding hole will NOT intersect and is dropped.
            if prepared.intersects(cell):
                # Label the cell by its center so the Maidenhead math lands squarely in it
                # rather than on a boundary line (where rounding could pick a neighbor).
                center_lon = lon + GRID_LON_DEG / 2.0
                center_lat = lat + GRID_LAT_DEG / 2.0
                grids.add(latlon_to_grid(center_lat, center_lon))
            lat += GRID_LAT_DEG
        lon += GRID_LON_DEG

    return grids


def _normalize_name(name: str | None) -> str:
    """Lowercase + strip common boundary-name noise for fuzzy comparison."""
    if not name:
        return ""
    text = name.lower().strip()
    # Collapse a few synonym/abbreviation tails so "Boise NF" ~ "Boise National Forest".
    replacements = {
        " nf": " national forest",
        " np": " national park",
        " sp": " state park",
        " sf": " state forest",
        " wma": " wildlife management area",
    }
    for short, full in replacements.items():
        if text.endswith(short):
            text = text[: -len(short)] + full
    return " ".join(text.split())


def _name_score(park_name: str | None, feature: PadusFeature) -> float:
    """Best fuzzy ratio of the POTA name against the feature's Unit_Nm / Loc_Nm."""
    target = _normalize_name(park_name)
    if not target:
        return 0.0
    best = 0.0
    for candidate in (feature.unit_nm, feature.loc_nm):
        norm = _normalize_name(candidate)
        if not norm:
            continue
        best = max(best, SequenceMatcher(None, target, norm).ratio())
    return best


def match_park(
    name: str | None,
    lat: float | None,
    lon: float | None,
    features: list[PadusFeature],
) -> tuple[BaseGeometry | None, str]:
    """Match a POTA park to PAD-US boundary geometry, returning (geometry, confidence).

    Strategy (feasibility hdb-7d0c): the POTA representative point is tested for
    containment against every candidate feature (PAD-US nests Fee/Designation/Easement
    layers, so several may contain the point). Among the containing features:

      * If one or more share the park's name (fuzzy ratio >= threshold), UNION those
        name-matching units (covers split units published as separate PAD-US rows) and
        return ("high"). This both disambiguates overlapping layers AND merges split units.
      * Otherwise fall back to the single smallest containing feature (tightest fit) and
        return ("medium") — a positional match with no name corroboration.

    Returns (None, "none") when the point is missing or no feature contains it. Never
    raises on a single bad geometry — the build must not block (it is caller-guarded too).
    """
    if lat is None or lon is None or not features:
        return None, "none"

    point = Point(lon, lat)
    containing = [f for f in features if _safe_contains(f.geometry, point)]
    if not containing:
        return None, "none"

    named = [f for f in containing if _name_score(name, f) >= _NAME_MATCH_THRESHOLD]
    if named:
        # Union name-matching units (split-unit merge), then attach any other PAD-US rows
        # carrying that same name even if their geometry didn't contain the point (a split
        # unit's other half won't contain the representative point).
        match_names = {_normalize_name(f.unit_nm) for f in named}
        match_names |= {_normalize_name(f.loc_nm) for f in named}
        match_names.discard("")
        same_name = [
            f
            for f in features
            if _normalize_name(f.unit_nm) in match_names
            or _normalize_name(f.loc_nm) in match_names
        ]
        geom = unary_union([f.geometry for f in same_name])
        return geom, "high"

    # No name corroboration: take the tightest (smallest-area) containing unit.
    smallest = min(containing, key=lambda f: f.geometry.area)
    return smallest.geometry, "medium"


def _safe_contains(geom: BaseGeometry, point: Point) -> bool:
    """Point-in-polygon that tolerates a representative point on a boundary or in a hole.

    ``contains`` is strict (interior only); a representative point dropped exactly on the
    outer ring would be missed, so we also accept ``intersects``. A point genuinely inside
    an inholding hole still returns False (shapely's intersection honors interior rings) —
    that is the desired behavior, and ``match_park`` then has no containing feature for the
    point but other heuristics / the fallback still apply.
    """
    try:
        return geom.contains(point) or geom.intersects(point)
    except Exception:  # a malformed geometry must never block the build
        return False


def park_grids(
    *,
    reference: str,
    name: str | None,
    lat: float | None,
    lon: float | None,
    features: list[PadusFeature],
) -> list[ParkGridRecord]:
    """Compute the ``pota_park_grids`` rows for one POTA park.

    Matched PAD-US polygon -> the SET of 4-char grids it intersects (source ``"padus"``,
    confidence from ``match_park``). Unmatched/non-matching parks degrade to a single
    point-grid row from the representative coordinate (source ``"pota-point"``, ``"low"``).
    A park with neither a matched polygon nor a usable point yields no rows (nothing to
    say; never blocks the build). Rows are de-duped on grid and sorted for determinism.
    """
    geom, confidence = match_park(name, lat, lon, features)

    if geom is not None and not geom.is_empty:
        grids = grid_cells_for_geometry(geom)
        if grids:
            return [
                ParkGridRecord(reference=reference, grid=g, source="padus",
                               confidence=confidence)
                for g in sorted(grids)
            ]

    # Point-grid fallback (covers unmatched US parks; the whole non-US world rides this in
    # PHASE 1 — see the OSM seam note below).
    if lat is not None and lon is not None:
        grid = latlon_to_grid(lat, lon)
        return [
            ParkGridRecord(reference=reference, grid=grid, source="pota-point",
                           confidence="low")
        ]

    return []


# --- SEAM for the OSM / international phase (hdb-438b) --------------------------
#
# PHASE 1 here is US/PAD-US (public-domain) only. The OSM/international boundaries are ODbL
# share-alike and CANNOT be mixed into the CC BY-NC artifact (feasibility hdb-7d0c), so they
# must ship in a SEPARATE ODbL file built by a separate path. The clean seam: that phase
# reuses ``grid_cells_for_geometry`` (pure, license-agnostic geometry) and emits its own
# ``ParkGridRecord``s with ``source="osm"`` into the separate ODbL artifact — it does NOT
# import or extend this module's PAD-US matching. Do NOT add OSM ingestion here.
