"""Tests for the OSM (international) park -> Maidenhead grid-set core (hdb-438b, PHASE 2).

This is the ODbL/international counterpart to the PAD-US (PHASE 1) core. It REUSES the pure,
license-agnostic geometry CORE ``grid_cells_for_geometry`` from ``padus_grids`` (TRUE
polygon intersection, interior rings / multipolygons honored natively) but has its OWN
park<->boundary matching written for OpenStreetMap features. These tests drive everything
from SMALL hand-built WKT/GeoJSON fixtures — NO network, NO GDAL/OSM reader — including a
holed polygon and a couple of non-US parks.

The OSM grids are an ODbL derivative database. They emit ``source="osm"`` ParkGridRecords
into a SEPARATE ODbL artifact; the matching here NEVER imports or mutates PAD-US matching.
"""

from __future__ import annotations

from shapely.geometry import MultiPolygon, Point, Polygon, shape

from hamcall_db.sources import osm_grids as og
from hamcall_db.sources.osm_grids import OsmFeature
from hamcall_db.sources.padus_grids import ParkGridRecord, grid_cells_for_geometry

# --- the OSM core REUSES the shared geometry core (no fork) --------------------


def test_osm_core_reuses_shared_geometry_core() -> None:
    # The license-agnostic geometry seam is shared verbatim — osm_grids must NOT fork it.
    assert og.grid_cells_for_geometry is grid_cells_for_geometry


# --- OSM matching: tag-aware, name + point-in-polygon --------------------------


def _feature(
    name: str | None,
    poly: Polygon,
    *,
    tags: dict[str, str] | None = None,
    osm_id: str | None = None,
) -> OsmFeature:
    return OsmFeature(
        name=name,
        tags=tags or {"boundary": "protected_area"},
        osm_id=osm_id,
        geometry=poly,
    )


def test_match_point_in_polygon_name_match_high_confidence() -> None:
    poly = Polygon([(11.0, 47.0), (13.0, 47.0), (13.0, 48.0), (11.0, 48.0)])
    feats = [_feature("Bayerischer Wald", poly)]
    geom, conf = og.match_park("Bayerischer Wald", 47.5, 12.0, feats)
    assert geom is not None
    assert conf == "high"


def test_match_point_in_polygon_no_name_match_medium_confidence() -> None:
    poly = Polygon([(11.0, 47.0), (13.0, 47.0), (13.0, 48.0), (11.0, 48.0)])
    feats = [_feature("Totally Different", poly)]
    geom, conf = og.match_park("Bayerischer Wald", 47.5, 12.0, feats)
    assert geom is not None
    assert conf == "medium"


def test_match_no_containing_feature_returns_none() -> None:
    poly = Polygon([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
    feats = [_feature("Far Away", poly)]
    geom, conf = og.match_park("Bayerischer Wald", 47.5, 12.0, feats)
    assert geom is None
    assert conf == "none"


def test_match_unions_split_units_same_name() -> None:
    a = Polygon([(11.0, 47.0), (11.2, 47.0), (11.2, 47.2), (11.0, 47.2)])
    b = Polygon([(15.0, 50.0), (15.2, 50.0), (15.2, 50.2), (15.0, 50.2)])
    feats = [_feature("Split Park", a), _feature("Split Park", b)]
    geom, conf = og.match_park("Split Park", 47.1, 11.1, feats)
    assert geom is not None
    grids = og.grid_cells_for_geometry(geom)
    # both units contribute their grids (the far unit was unioned in)
    assert grids == grid_cells_for_geometry(MultiPolygon([a, b]).buffer(0)) or len(grids) >= 2


def test_match_smallest_containing_when_no_name_match() -> None:
    big = Polygon([(11.0, 47.0), (14.0, 47.0), (14.0, 49.0), (11.0, 49.0)])
    small = Polygon([(11.5, 47.5), (12.5, 47.5), (12.5, 48.5), (11.5, 48.5)])
    feats = [_feature("Region", big), _feature("Other Region", small)]
    geom, conf = og.match_park("Bayerischer Wald", 48.0, 12.0, feats)
    assert geom is not None
    assert geom.equals(small)  # tightest fit
    assert conf == "medium"


def test_match_missing_point_returns_none() -> None:
    poly = Polygon([(11.0, 47.0), (13.0, 47.0), (13.0, 48.0), (11.0, 48.0)])
    feats = [_feature("X", poly)]
    assert og.match_park("X", None, None, feats) == (None, "none")


# --- HOLE / inholding regression (same requirement as PAD-US) ------------------


def _holed_park() -> Polygon:
    """A non-US park boundary with an interior ring (inholding)."""
    outer = [(11.0, 47.0), (13.0, 47.0), (13.0, 48.0), (11.0, 48.0)]
    hole = [(11.4, 47.4), (11.6, 47.4), (11.6, 47.6), (11.4, 47.6)]
    return Polygon(outer, holes=[hole])


def test_hole_is_preserved_not_filled() -> None:
    park = _holed_park()
    assert len(park.interiors) == 1
    assert park.area < Polygon([(11.0, 47.0), (13.0, 47.0), (13.0, 48.0), (11.0, 48.0)]).area


def test_point_inside_hole_is_outside_park() -> None:
    park = _holed_park()
    assert not park.contains(Point(11.5, 47.5))  # inside the inholding


def test_match_tolerates_point_in_hole_via_other_feature() -> None:
    # A representative point that lands in the inholding hole is NOT contained by the holed
    # park, but matching must not crash and may match a smaller overlapping feature.
    park = _holed_park()
    inholding = Polygon([(11.4, 47.4), (11.6, 47.4), (11.6, 47.6), (11.4, 47.6)])
    feats = [
        _feature("Big National Park", park),
        _feature("Inholding Resort", inholding, tags={"leisure": "park"}),
    ]
    geom, conf = og.match_park("Inholding Resort", 47.5, 11.5, feats)
    assert geom is not None
    assert conf == "high"  # named the inholding, point inside it


def test_large_hole_excludes_an_interior_grid_cell_through_core() -> None:
    # Proof the OSM path honors holes through the shared geometry core: a donut whose hole
    # fully contains a 2x1 deg cell must EXCLUDE that cell. No bbox/convex-hull/hole-fill.
    from hamcall_db.geocode import latlon_to_grid

    outer = [(8.0, 44.0), (20.0, 44.0), (20.0, 52.0), (8.0, 52.0)]
    hole = [(11.0, 46.0), (17.0, 46.0), (17.0, 50.0), (11.0, 50.0)]
    donut = Polygon(outer, holes=[hole])
    solid = Polygon(outer)
    interior_cell = latlon_to_grid(47.5, 13.0)  # center of a fully-holed cell
    assert interior_cell in og.grid_cells_for_geometry(solid)
    assert interior_cell not in og.grid_cells_for_geometry(donut)


def test_multipolygon_split_units_unioned_through_core() -> None:
    a = Polygon([(11.0, 47.0), (11.2, 47.0), (11.2, 47.2), (11.0, 47.2)])
    b = Polygon([(15.0, 50.0), (15.2, 50.0), (15.2, 50.2), (15.0, 50.2)])
    mp = MultiPolygon([a, b])
    grids = og.grid_cells_for_geometry(mp)
    assert len(grids) >= 2


# --- osm_park_grids(): polygon match + point fallback, source='osm' ------------


def test_osm_park_grids_polygon_match_emits_osm_rows() -> None:
    poly = Polygon([(11.0, 47.0), (13.0, 47.0), (13.0, 48.0), (11.0, 48.0)])
    feats = [_feature("Bayerischer Wald", poly)]
    rows = og.osm_park_grids(
        reference="DE-0001",
        name="Bayerischer Wald",
        lat=47.5,
        lon=12.0,
        features=feats,
    )
    assert rows
    assert all(isinstance(r, ParkGridRecord) for r in rows)
    assert all(r.reference == "DE-0001" for r in rows)
    assert {r.source for r in rows} == {"osm"}  # the ODbL provenance tag
    assert {r.confidence for r in rows} == {"high"}
    assert all(len(r.grid) == 4 for r in rows)  # 4-char only (mem-e3fd)
    assert {r.grid for r in rows} == og.grid_cells_for_geometry(poly)


def test_osm_park_grids_unmatched_falls_back_to_point_grid() -> None:
    rows = og.osm_park_grids(
        reference="DE-9999",
        name="No Boundary Park",
        lat=51.0,
        lon=10.0,
        features=[],
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.source == "osm-point"  # point fallback still ships in the ODbL file
    assert row.confidence == "low"
    assert len(row.grid) == 4


def test_osm_park_grids_no_point_no_polygon_yields_nothing() -> None:
    rows = og.osm_park_grids(
        reference="DE-0000",
        name="Inactive",
        lat=None,
        lon=None,
        features=[],
    )
    assert rows == []


def test_osm_park_grids_rows_sorted_and_deduped() -> None:
    poly = Polygon([(11.0, 47.0), (13.0, 47.0), (13.0, 48.0), (11.0, 48.0)])
    feats = [_feature("Bayerischer Wald", poly)]
    rows = og.osm_park_grids(
        reference="DE-0001",
        name="Bayerischer Wald",
        lat=47.5,
        lon=12.0,
        features=feats,
    )
    grids = [r.grid for r in rows]
    assert grids == sorted(grids)
    assert len(grids) == len(set(grids))


def test_osm_park_grids_never_raises_on_bad_geometry() -> None:
    bad = _feature("Broken", shape({"type": "Polygon", "coordinates": [[]]}))
    good = Polygon([(11.0, 47.0), (13.0, 47.0), (13.0, 48.0), (11.0, 48.0)])
    rows = og.osm_park_grids(
        reference="DE-0001",
        name="Bayerischer Wald",
        lat=47.5,
        lon=12.0,
        features=[bad, _feature("Bayerischer Wald", good)],
    )
    assert any(r.reference == "DE-0001" for r in rows)
