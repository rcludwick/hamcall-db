"""POTA park -> Maidenhead 4-char grid-set matching CORE (hdb-438b, PHASE 2: OSM/international).

This is the ODbL/international counterpart to ``padus_grids`` (PHASE 1, US/PAD-US). It is the
PURE-shapely heart of the NON-US park-grids feature: park<->OpenStreetMap-boundary matching
and the point-grid fallback. It has NO GDAL/OSM-reader dependency and never touches the
network — the thin build-time adapter (``hamcall_db.sources.osm``) hands it shapely
geometries read out of an OSM extract.

LICENSE SEGREGATION (non-negotiable)
------------------------------------
OpenStreetMap is an ODbL share-alike database; grids DERIVED from OSM boundaries form an
ODbL DERIVATIVE DATABASE. They are therefore INCOMPATIBLE with the main CC BY-NC artifact
and MUST ship in their OWN ODbL-licensed files (Parquet + SQLite), NEVER mixed into the
callsign/.db or the PAD-US ``pota_park_grids`` set. The rows this module emits carry
``source="osm"`` (polygon match) or ``source="osm-point"`` (point fallback) and are written
ONLY into the separate ODbL artifact by the OSM writers. ``(c) OpenStreetMap contributors``,
ODbL — see the ODbL LICENSE file shipped with the OSM artifact and NOTICE (mem-371f).

REUSE, not fork
---------------
The pure, license-agnostic geometry CORE ``grid_cells_for_geometry`` (TRUE polygon
intersection; interior rings / multipolygons honored natively; NO bbox / convex-hull /
hole-fill shortcut; 4-char Maidenhead only, mem-e3fd) is REUSED VERBATIM from
``padus_grids`` via the seam documented at the bottom of that file. We do NOT import or
extend the PAD-US *matching* — OSM features have different shape (OSM tags + a single
``name`` rather than PAD-US Unit_Nm/Loc_Nm), so matching is written here.

Holes / inholdings (same requirement as PAD-US)
-----------------------------------------------
International park boundaries also have interior rings (inholdings). We rely on the shared
core's TRUE intersection, which honors holes natively: a candidate grid cell is in the set
iff the (possibly holed, possibly multi-part) park geometry genuinely intersects it. A
park's representative POINT may legitimately fall inside a hole; matching tolerates that.

Output rows
-----------
``ParkGridRecord(reference, grid, source, confidence)`` — REUSED from ``padus_grids`` so the
schema is IDENTICAL to the PAD-US set (the files stay separate; only the schema is shared).

  * ``grid``       4-char Maidenhead field+square (never 6-char; mem-e3fd).
  * ``source``     ``"osm"`` (intersected a matched OSM boundary) or ``"osm-point"``
                   (point-grid fallback for unmatched/non-matching parks).
  * ``confidence`` ``"high"`` (point-in-polygon AND name match), ``"medium"`` (point in a
                   single polygon, name didn't match), ``"low"`` (point-grid fallback).
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

# REUSE the shared, license-agnostic geometry core (the seam in padus_grids.py). We import
# the helpers we need so callers can drive the OSM path without touching the PAD-US adapter.
from hamcall_db.geocode import latlon_to_grid
from hamcall_db.sources.padus_grids import (
    ParkGridRecord,
    grid_cells_for_geometry,
)

__all__ = [
    "OsmFeature",
    "ParkGridRecord",
    "grid_cells_for_geometry",
    "match_park",
    "osm_park_grids",
]

# OSM provenance tags. Rows from a matched OSM boundary; the point fallback keeps a distinct
# tag so a consumer can tell a true boundary intersection from a single indicative point.
SOURCE_OSM = "osm"
SOURCE_OSM_POINT = "osm-point"

# Fuzzy name-match acceptance threshold (SequenceMatcher ratio over normalized names). Tuned
# the same as PAD-US — OSM ``name`` and the POTA park name often differ in punctuation /
# language / suffix.
_NAME_MATCH_THRESHOLD = 0.6


@dataclass(slots=True)
class OsmFeature:
    """One OpenStreetMap boundary feature handed to the core by the build-time adapter.

    ``geometry`` is a shapely (Multi)Polygon in WGS84 (EPSG:4326) — OSM is natively WGS84,
    so no reprojection is needed. ``name`` is the OSM ``name`` tag (the human name).
    ``tags`` are the raw OSM tags (e.g. ``boundary=protected_area`` / ``leisure=park``),
    carried so matching can prefer protected-area features and a consumer-facing tag could
    later be surfaced. ``osm_id`` is the OSM element id (provenance only). The core never
    reads files — it only sees these.
    """

    name: str | None
    tags: dict[str, str]
    osm_id: str | None
    geometry: BaseGeometry


def _normalize_name(name: str | None) -> str:
    """Lowercase + collapse whitespace for fuzzy comparison (language-neutral)."""
    if not name:
        return ""
    return " ".join(name.lower().strip().split())


def _name_score(park_name: str | None, feature: OsmFeature) -> float:
    """Fuzzy ratio of the POTA park name against the OSM feature's ``name`` tag."""
    target = _normalize_name(park_name)
    candidate = _normalize_name(feature.name)
    if not target or not candidate:
        return 0.0
    return SequenceMatcher(None, target, candidate).ratio()


def _safe_contains(geom: BaseGeometry, point: Point) -> bool:
    """Point-in-polygon that tolerates a representative point on a boundary edge.

    ``contains`` is interior-only; a representative point dropped exactly on the outer ring
    would be missed, so we also accept ``intersects``. A point genuinely inside an inholding
    HOLE still returns False (shapely honors interior rings) — that is desired; the park
    then has no containing feature for that point and other features / the fallback apply.
    Never raises on a malformed geometry (the build must not block).
    """
    try:
        return geom.contains(point) or geom.intersects(point)
    except Exception:  # a malformed geometry must never block the build
        return False


def match_park(
    name: str | None,
    lat: float | None,
    lon: float | None,
    features: list[OsmFeature],
) -> tuple[BaseGeometry | None, str]:
    """Match a POTA park to OpenStreetMap boundary geometry, returning (geometry, confidence).

    Strategy (mirrors the PAD-US heuristic but for OSM features): the POTA representative
    point is tested for containment against every candidate OSM feature (overlapping
    boundaries are common — a national park inside a larger protected landscape). Among the
    containing features:

      * If one or more share the park's name (fuzzy ratio >= threshold), UNION those
        name-matching features (covers a park mapped as several OSM relations / split units)
        and return ``"high"``. Other same-named features whose geometry did NOT contain the
        point (a split unit's other half) are unioned in too.
      * Otherwise fall back to the single smallest containing feature (tightest fit) and
        return ``"medium"`` — a positional match with no name corroboration.

    Returns ``(None, "none")`` when the point is missing or no feature contains it. Never
    raises on one bad geometry — the build must not block (the caller guards it too).
    """
    if lat is None or lon is None or not features:
        return None, "none"

    point = Point(lon, lat)
    containing = [f for f in features if _safe_contains(f.geometry, point)]
    if not containing:
        return None, "none"

    named = [f for f in containing if _name_score(name, f) >= _NAME_MATCH_THRESHOLD]
    if named:
        match_names = {_normalize_name(f.name) for f in named}
        match_names.discard("")
        same_name = [f for f in features if _normalize_name(f.name) in match_names]
        geom = unary_union([f.geometry for f in same_name])
        return geom, "high"

    # No name corroboration: take the tightest (smallest-area) containing feature.
    smallest = min(containing, key=lambda f: f.geometry.area)
    return smallest.geometry, "medium"


def osm_park_grids(
    *,
    reference: str,
    name: str | None,
    lat: float | None,
    lon: float | None,
    features: list[OsmFeature],
) -> list[ParkGridRecord]:
    """Compute the OSM ``pota_park_grids_osm`` rows for one POTA park.

    Matched OSM boundary -> the SET of 4-char grids it intersects (via the SHARED geometry
    core, source ``"osm"``, confidence from ``match_park``). Unmatched/non-matching parks
    degrade to a single point-grid row from the representative coordinate (source
    ``"osm-point"``, ``"low"``). A park with neither a matched polygon nor a usable point
    yields no rows. Rows are de-duped on grid and sorted for determinism.

    EVERY row produced here is an ODbL derivative and is written ONLY into the separate ODbL
    artifact — never into the CC BY-NC files.
    """
    geom, confidence = match_park(name, lat, lon, features)

    if geom is not None and not geom.is_empty:
        grids = grid_cells_for_geometry(geom)
        if grids:
            return [
                ParkGridRecord(
                    reference=reference, grid=g, source=SOURCE_OSM, confidence=confidence
                )
                for g in sorted(grids)
            ]

    # Point-grid fallback (unmatched parks). Stays in the ODbL file: it is the OSM-side
    # answer for a non-US park, kept segregated from the PAD-US/CC-BY-NC set on purpose.
    if lat is not None and lon is not None:
        grid = latlon_to_grid(lat, lon)
        return [
            ParkGridRecord(
                reference=reference, grid=grid, source=SOURCE_OSM_POINT, confidence="low"
            )
        ]

    return []
