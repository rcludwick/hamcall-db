"""Tests for the POTA park -> Maidenhead grid-set geometry CORE (hdb-f53c, PHASE 1).

These tests drive the PURE-shapely core: lattice generation, TRUE polygon intersection
(interior rings / multipolygons handled natively), park<->PAD-US matching, confidence
flags, and the point-grid fallback. They use SMALL hand-built WKT/GeoJSON fixtures so they
need NEITHER GDAL (pyogrio/fiona) NOR the multi-GB PAD-US national download — the only
geometry deps exercised here are shapely + the project's existing Maidenhead math.

The flagship regression is the HOLE test (spec DECISION, user 2026-06-17): a Boise National
Forest polygon with an interior ring around the Bogus Basin ski-area inholding. It asserts
the hole is PRESERVED (not bbox/convex-hull/hole-filled) and that a point inside the hole is
treated as OUTSIDE the park.
"""

from __future__ import annotations

from shapely.geometry import MultiPolygon, Point, Polygon, shape

from hamcall_db.sources import padus_grids as pg
from hamcall_db.sources.padus_grids import PadusFeature, ParkGridRecord

# --- 4-char Maidenhead lattice / single-cell intersection ----------------------


def test_eagle_island_small_park_one_grid() -> None:
    # Spec litmus test: a small park (Eagle Island State Park, near Boise ID, ~ -116.3,
    # 43.7) intersects exactly ONE 4-char grid. -116.3,43.7 -> DN13 (lon field D=3rd 20deg
    # band starting -180 -> [-120,-100); square (-116.3 - -120)//2 = 1; lat field N=14th
    # 10deg band from -90 -> [40,50); square 43.7-40 = 3 -> DN13).
    park = Polygon([(-116.35, 43.65), (-116.25, 43.65), (-116.25, 43.75), (-116.35, 43.75)])
    grids = pg.grid_cells_for_geometry(park)
    assert grids == {"DN13"}


def test_grid_is_always_four_char() -> None:
    park = Polygon([(-116.35, 43.65), (-116.25, 43.65), (-116.25, 43.75), (-116.35, 43.75)])
    for grid in pg.grid_cells_for_geometry(park):
        assert len(grid) == 4  # never publish 6-char subsquare (mem-e3fd)


def test_large_blob_spans_several_grids() -> None:
    # A big rectangle spanning ~5 deg lon x ~3 deg lat must hit MANY 4-char cells (2x1 deg).
    park = Polygon([(-117.0, 43.0), (-112.0, 43.0), (-112.0, 46.0), (-117.0, 46.0)])
    grids = pg.grid_cells_for_geometry(park)
    # 5 deg lon / 2 = ~3 columns (snapped, expect 3-4); 3 deg lat / 1 = ~3-4 rows.
    assert len(grids) >= 9
    # Spot-check a couple of known cells are present.
    assert "DN13" in grids


def test_grid_set_matches_brute_force_point_sampling() -> None:
    # Independent oracle: a cell is in the set iff the park intersects that 2x1 deg cell.
    # Verify by sampling the cell interiors and confirming membership agrees.
    from hamcall_db.geocode import latlon_to_grid

    park = Polygon([(-117.0, 43.0), (-112.5, 43.0), (-112.5, 45.5), (-117.0, 45.5)])
    grids = pg.grid_cells_for_geometry(park)
    # Every sampled interior point of the park must fall in a cell we reported.
    for lon in [-116.9, -115.0, -113.0, -112.6]:
        for lat in [43.1, 44.0, 45.0, 45.4]:
            if park.contains(Point(lon, lat)):
                assert latlon_to_grid(lat, lon) in grids


# --- HOLE / inholding regression (the flagship spec requirement) ---------------


def _boise_with_bogus_basin_hole() -> Polygon:
    """Boise NF outer boundary with an interior ring (hole) around the Bogus Basin
    inholding. Coordinates are a small stand-in, not the real boundary."""
    outer = [(-116.5, 43.5), (-115.0, 43.5), (-115.0, 44.5), (-116.5, 44.5)]
    # A hole near Bogus Basin (~ -116.1, 43.76) — entirely inside the outer ring.
    hole = [(-116.2, 43.7), (-116.0, 43.7), (-116.0, 43.85), (-116.2, 43.85)]
    return Polygon(outer, holes=[hole])


def test_hole_is_preserved_not_filled() -> None:
    park = _boise_with_bogus_basin_hole()
    # The geometry must carry exactly one interior ring (the inholding) — no hole-filling.
    assert len(park.interiors) == 1
    assert (
        park.area < Polygon([(-116.5, 43.5), (-115.0, 43.5), (-115.0, 44.5), (-116.5, 44.5)]).area
    )


def test_point_inside_hole_is_outside_park() -> None:
    park = _boise_with_bogus_basin_hole()
    bogus_basin = Point(-116.1, 43.78)  # inside the inholding ring
    assert not park.contains(bogus_basin)
    assert not park.intersects(bogus_basin.buffer(0.0))


def test_point_in_park_but_not_in_hole_is_inside() -> None:
    park = _boise_with_bogus_basin_hole()
    elsewhere = Point(-115.5, 44.0)  # in the forest, away from the inholding
    assert park.contains(elsewhere)


def test_grid_set_uses_true_intersection_through_core() -> None:
    # Driving the production core (not raw shapely) on the holed polygon still yields a
    # plausible grid set, and the core never crashes / fills the hole behind our back.
    park = _boise_with_bogus_basin_hole()
    grids = pg.grid_cells_for_geometry(park)
    assert "DN13" in grids
    assert all(len(g) == 4 for g in grids)


def test_large_hole_excludes_an_interior_grid_cell() -> None:
    # Proof the grid math honors holes, not just point containment: a donut whose hole is
    # big enough to fully contain a 2x1 deg cell must EXCLUDE that cell from the grid set,
    # while a solid polygon of the same outline includes it. No bbox/convex-hull/hole-fill
    # shortcut could produce this difference.
    outer = [(-120.0, 40.0), (-108.0, 40.0), (-108.0, 48.0), (-120.0, 48.0)]
    # Hole spanning lon [-117,-111] x lat [42,46]. The cell at lon[-114,-112] x lat[43,44]
    # sits STRICTLY inside the hole (no shared edge with the hole boundary), so true
    # intersection must drop it.
    hole = [(-117.0, 42.0), (-111.0, 42.0), (-111.0, 46.0), (-117.0, 46.0)]
    donut = Polygon(outer, holes=[hole])
    solid = Polygon(outer)
    from hamcall_db.geocode import latlon_to_grid

    interior_cell = latlon_to_grid(43.5, -113.0)  # center of a fully-holed cell
    donut_grids = pg.grid_cells_for_geometry(donut)
    solid_grids = pg.grid_cells_for_geometry(solid)
    assert interior_cell in solid_grids
    assert interior_cell not in donut_grids  # the hole removed it (true intersection)


def test_multipolygon_split_units_unioned() -> None:
    # A park split into two disjoint units (a MultiPolygon) must contribute the grids of
    # BOTH parts.
    a = Polygon([(-116.35, 43.65), (-116.25, 43.65), (-116.25, 43.75), (-116.35, 43.75)])
    b = Polygon([(-100.1, 40.1), (-100.0, 40.1), (-100.0, 40.2), (-100.1, 40.2)])
    mp = MultiPolygon([a, b])
    grids = pg.grid_cells_for_geometry(mp)
    assert "DN13" in grids  # from unit a
    assert "DN90" in grids  # from unit b (lon -100.1..-100.0 lands in the -102..-100 cell)


# --- park <-> PAD-US matching + confidence -------------------------------------


def _feature(name: str, poly: Polygon, *, loc_nm: str | None = None) -> PadusFeature:
    return PadusFeature(unit_nm=name, loc_nm=loc_nm, geometry=poly)


def test_match_point_in_polygon_single_candidate_high_confidence() -> None:
    poly = Polygon([(-116.5, 43.5), (-115.0, 43.5), (-115.0, 44.5), (-116.5, 44.5)])
    feats = [_feature("Boise National Forest", poly)]
    geom, conf = pg.match_park("Boise National Forest", 44.0, -115.5, feats)
    assert geom is not None
    assert conf == "high"


def test_match_fuzzy_name_disambiguates_overlapping_units() -> None:
    # Two PAD-US units cover the point (PAD-US nests Fee/Designation layers); fuzzy name
    # disambiguates to the one whose Unit_Nm matches the POTA name.
    big = Polygon([(-117.0, 43.0), (-114.0, 43.0), (-114.0, 45.0), (-117.0, 45.0)])
    small = Polygon([(-116.0, 43.5), (-115.0, 43.5), (-115.0, 44.5), (-116.0, 44.5)])
    feats = [
        _feature("Some Wilderness Study Area", big),
        _feature("Boise National Forest", small),
    ]
    geom, conf = pg.match_park("Boise National Forest", 44.0, -115.5, feats)
    assert geom is not None
    # The matched geometry is the name-matching (smaller) unit, not the big overlapping one.
    assert geom.equals(small)
    assert conf == "high"


def test_match_union_of_split_units_with_same_name() -> None:
    # POTA park split across two PAD-US units with the SAME name -> union them.
    a = Polygon([(-116.35, 43.65), (-116.25, 43.65), (-116.25, 43.75), (-116.35, 43.75)])
    b = Polygon([(-100.1, 40.1), (-100.0, 40.1), (-100.0, 40.2), (-100.1, 40.2)])
    feats = [_feature("Split Park", a), _feature("Split Park", b)]
    geom, conf = pg.match_park("Split Park", 43.70, -116.30, feats)
    assert geom is not None
    grids = pg.grid_cells_for_geometry(geom)
    assert "DN13" in grids
    assert "DN90" in grids  # the far unit was unioned in


def test_match_point_in_polygon_no_name_match_is_medium_confidence() -> None:
    # Point falls inside a single unit but the name doesn't match -> still a match, lower
    # confidence.
    poly = Polygon([(-116.5, 43.5), (-115.0, 43.5), (-115.0, 44.5), (-116.5, 44.5)])
    feats = [_feature("Totally Different Name", poly)]
    geom, conf = pg.match_park("Boise National Forest", 44.0, -115.5, feats)
    assert geom is not None
    assert conf == "medium"


def test_no_match_returns_none() -> None:
    poly = Polygon([(-100.0, 40.0), (-99.0, 40.0), (-99.0, 41.0), (-100.0, 41.0)])
    feats = [_feature("Far Away Park", poly)]
    geom, conf = pg.match_park("Boise National Forest", 44.0, -115.5, feats)
    assert geom is None
    assert conf == "none"


# --- end-to-end: park_grids() with polygon match + point fallback --------------


def test_park_grids_polygon_match_emits_rows() -> None:
    poly = Polygon([(-116.5, 43.5), (-115.0, 43.5), (-115.0, 44.5), (-116.5, 44.5)])
    feats = [_feature("Boise National Forest", poly)]
    rows = pg.park_grids(
        reference="US-4567",
        name="Boise National Forest",
        lat=44.0,
        lon=-115.5,
        features=feats,
    )
    assert rows
    assert all(isinstance(r, ParkGridRecord) for r in rows)
    assert all(r.reference == "US-4567" for r in rows)
    assert {r.source for r in rows} == {"padus"}
    assert {r.confidence for r in rows} == {"high"}
    assert all(len(r.grid) == 4 for r in rows)
    # The set of grids matches the geometry core.
    assert {r.grid for r in rows} == pg.grid_cells_for_geometry(poly)


def test_park_grids_unmatched_falls_back_to_point_grid() -> None:
    # No PAD-US feature matches -> single point-grid row, source='pota-point', low conf.
    feats: list[PadusFeature] = []
    rows = pg.park_grids(
        reference="US-0001",
        name="Acadia National Park",
        lat=44.31,
        lon=-68.2034,
        features=feats,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.reference == "US-0001"
    assert row.source == "pota-point"
    assert row.confidence == "low"
    assert len(row.grid) == 4


def test_park_grids_no_geometry_and_no_point_yields_nothing() -> None:
    # A park with neither a matched polygon nor a representative point yields no rows
    # (never block the build; just nothing to say).
    rows = pg.park_grids(
        reference="US-9999",
        name="Inactive Test Park",
        lat=None,
        lon=None,
        features=[],
    )
    assert rows == []


def test_park_grids_dedups_grid_rows() -> None:
    poly = Polygon([(-116.5, 43.5), (-115.0, 43.5), (-115.0, 44.5), (-116.5, 44.5)])
    feats = [_feature("Boise National Forest", poly)]
    rows = pg.park_grids(
        reference="US-4567",
        name="Boise National Forest",
        lat=44.0,
        lon=-115.5,
        features=feats,
    )
    grids = [r.grid for r in rows]
    assert len(grids) == len(set(grids))  # one row per (reference, grid)


def test_park_grids_rows_are_sorted_deterministic() -> None:
    poly = Polygon([(-116.5, 43.5), (-115.0, 43.5), (-115.0, 44.5), (-116.5, 44.5)])
    feats = [_feature("Boise National Forest", poly)]
    rows = pg.park_grids(
        reference="US-4567",
        name="Boise National Forest",
        lat=44.0,
        lon=-115.5,
        features=feats,
    )
    grids = [r.grid for r in rows]
    assert grids == sorted(grids)


# --- geojson fixture loads through shape() (the adapter's output contract) ------


def test_geojson_holed_polygon_roundtrips() -> None:
    # The build-time adapter hands the core shapely geoms via shape(geojson). Confirm a
    # holed GeoJSON polygon keeps its interior ring through that path.
    geojson = {
        "type": "Polygon",
        "coordinates": [
            [[-116.5, 43.5], [-115.0, 43.5], [-115.0, 44.5], [-116.5, 44.5], [-116.5, 43.5]],
            [[-116.2, 43.7], [-116.0, 43.7], [-116.0, 43.85], [-116.2, 43.85], [-116.2, 43.7]],
        ],
    }
    geom = shape(geojson)
    assert len(geom.interiors) == 1
    assert not geom.contains(Point(-116.1, 43.78))  # the hole reads as outside
