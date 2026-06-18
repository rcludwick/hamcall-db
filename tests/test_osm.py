"""Tests for the OSM build-time adapter + orchestration (hdb-438b, PHASE 2: international).

The adapter (``hamcall_db.sources.osm``) is the THIN reader seam: it downloads/caches OSM
regional extracts and reads boundary features out of them. Reading a real OSM extract needs
a GIS reader (pyogrio/GDAL), which these tests DO NOT exercise — the feature-reading seam is
injected so the orchestration (match non-US parks against OSM features, intersect with the
Maidenhead lattice, point fallback) is driven entirely from small in-memory GeoJSON
fixtures. No network, no GDAL in the test path.

LICENSE: every row produced here is an ODbL derivative — it is written ONLY to the separate
ODbL artifact (asserted in test_osm_writers / test_build), never the CC BY-NC files.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from shapely.geometry import shape

from hamcall_db.sources import osm
from hamcall_db.sources.osm_grids import OsmFeature
from hamcall_db.sources.pota import ParkRecord

# A small in-memory OSM feature set (WGS84 GeoJSON polygons), as the injected reader would
# yield. The German park carries an inholding hole (interior ring).
_BAYWALD_GEOJSON = {
    "type": "Polygon",
    "coordinates": [
        [[11.0, 47.0], [13.0, 47.0], [13.0, 48.0], [11.0, 48.0], [11.0, 47.0]],
        [[11.4, 47.4], [11.6, 47.4], [11.6, 47.6], [11.4, 47.6], [11.4, 47.4]],
    ],
}
_BANFF_GEOJSON = {
    "type": "Polygon",
    "coordinates": [
        [[-116.5, 50.5], [-115.0, 50.5], [-115.0, 52.0], [-116.5, 52.0], [-116.5, 50.5]]
    ],
}


def _fake_features() -> list[OsmFeature]:
    return [
        OsmFeature("Bayerischer Wald", {"boundary": "protected_area"}, "r1",
                   shape(_BAYWALD_GEOJSON)),
        OsmFeature("Banff National Park", {"boundary": "protected_area"}, "r2",
                   shape(_BANFF_GEOJSON)),
    ]


_PARKS = [
    ParkRecord(reference="DE-0001", name="Bayerischer Wald", country="DE",
               lat=47.5, lon=12.0),  # OSM polygon match
    ParkRecord(reference="CA-0001", name="Banff National Park", country="CA",
               lat=51.0, lon=-115.5),  # OSM polygon match
    ParkRecord(reference="DE-9999", name="Unmapped Park", country="DE",
               lat=51.0, lon=10.0),  # no OSM feature -> point fallback
    ParkRecord(reference="US-4567", name="Boise National Forest", country="US",
               lat=44.0, lon=-115.5),  # US -> EXCLUDED from the OSM set (PAD-US owns it)
    ParkRecord(reference="DE-0000", name="Inactive", country="DE",
               lat=None, lon=None),  # no point, no polygon -> no rows
]


# --- orchestration: compute_osm_park_grids over a parks iterable ---------------


def test_compute_polygon_match_for_non_us_park() -> None:
    rows = osm.compute_osm_park_grids(_PARKS, _fake_features())
    de = [r for r in rows if r.reference == "DE-0001"]
    assert de
    assert {r.source for r in de} == {"osm"}
    assert {r.confidence for r in de} == {"high"}
    assert all(len(r.grid) == 4 for r in de)


def test_compute_excludes_us_parks_entirely() -> None:
    # US parks are owned by the PAD-US/CC-BY-NC set; they must NOT appear in the OSM set at
    # all (not even a point fallback) so the two artifacts never overlap on US references.
    rows = osm.compute_osm_park_grids(_PARKS, _fake_features())
    assert not [r for r in rows if r.reference == "US-4567"]
    assert not [r for r in rows if (r.reference or "").upper().startswith("US-")]


def test_compute_unmatched_non_us_park_point_fallback() -> None:
    rows = osm.compute_osm_park_grids(_PARKS, _fake_features())
    de9999 = [r for r in rows if r.reference == "DE-9999"]
    assert len(de9999) == 1
    assert de9999[0].source == "osm-point"
    assert de9999[0].confidence == "low"


def test_compute_no_point_no_polygon_yields_nothing() -> None:
    rows = osm.compute_osm_park_grids(_PARKS, _fake_features())
    assert not [r for r in rows if r.reference == "DE-0000"]


def test_compute_never_raises_on_bad_geometry() -> None:
    bad = OsmFeature("Broken", {}, "r9", shape({"type": "Polygon", "coordinates": [[]]}))
    parks = [ParkRecord(reference="DE-0001", name="Bayerischer Wald", country="DE",
                        lat=47.5, lon=12.0)]
    rows = osm.compute_osm_park_grids(parks, [bad] + _fake_features())
    assert any(r.reference == "DE-0001" for r in rows)


def test_compute_all_rows_are_odbl_provenance() -> None:
    # Every row in the OSM set must carry an OSM provenance tag (osm / osm-point) — never a
    # padus/pota-point tag that belongs to the CC-BY-NC set.
    rows = osm.compute_osm_park_grids(_PARKS, _fake_features())
    assert rows
    assert {r.source for r in rows} <= {"osm", "osm-point"}


# --- download/cache seam (no network; injected fetcher) ------------------------


def test_download_caches_under_dated_osm_dir(tmp_path: Path) -> None:
    def fetcher(url: str, since: float | None) -> bytes | None:
        return b"FAKE-OSM-EXTRACT-BYTES"

    src = osm.OsmSource(regions=["europe/germany"])
    paths = src.download(tmp_path, on=dt.date(2026, 6, 17), fetcher=fetcher)
    assert paths
    for p in paths:
        assert p.exists()
        assert "osm" in p.parts
        assert "2026-06-17" in p.parts


def test_download_reuses_cache_on_not_modified(tmp_path: Path) -> None:
    calls = {"n": 0}

    def fetcher(url: str, since: float | None) -> bytes | None:
        calls["n"] += 1
        if calls["n"] == 1:
            return b"FAKE-OSM-EXTRACT-BYTES"
        return None  # 304 Not Modified — reuse cache

    src = osm.OsmSource(regions=["europe/germany"])
    day = dt.date(2026, 6, 17)
    p1 = src.download(tmp_path, on=day, fetcher=fetcher)
    p2 = src.download(tmp_path, on=day, fetcher=fetcher)
    assert p1 == p2
    assert p1[0].read_bytes() == b"FAKE-OSM-EXTRACT-BYTES"


def test_read_features_seam_present_but_gis_gated() -> None:
    # The real reader lives behind _read_osm_features; without the optional GIS reader it
    # should raise a clear, actionable error rather than a bare ImportError. We do not
    # install the reader in CI, so we only assert the seam exists.
    assert hasattr(osm, "_read_osm_features")
