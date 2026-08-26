"""Tests for the PAD-US build-time adapter + park-grids orchestration (hdb-f53c).

The adapter (``hamcall_db.sources.padus``) is the THIN GDAL seam: it downloads/caches the
PAD-US national file and reads its features. Reading the real GeoPackage/GDB needs GDAL
(pyogrio), which these tests DO NOT exercise — the feature-reading seam is injected so the
orchestration (matching + grid intersection over a parks iterable) is driven entirely from
small in-memory GeoJSON fixtures. No GDAL, no multi-GB download in the test path.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from shapely.geometry import shape

from hamcall_db.sources import padus
from hamcall_db.sources.padus_grids import PadusFeature
from hamcall_db.sources.pota import ParkRecord

# A small in-memory PAD-US feature set (WGS84 GeoJSON polygons), as the injected reader
# would yield. Boise NF carries a Bogus-Basin hole (interior ring).
_BOISE_GEOJSON = {
    "type": "Polygon",
    "coordinates": [
        [[-116.5, 43.5], [-115.0, 43.5], [-115.0, 44.5], [-116.5, 44.5], [-116.5, 43.5]],
        [[-116.2, 43.7], [-116.0, 43.7], [-116.0, 43.85], [-116.2, 43.85], [-116.2, 43.7]],
    ],
}
_EAGLE_GEOJSON = {
    "type": "Polygon",
    "coordinates": [
        [[-116.35, 43.65], [-116.25, 43.65], [-116.25, 43.75], [-116.35, 43.75], [-116.35, 43.65]]
    ],
}


def _fake_features() -> list[PadusFeature]:
    return [
        PadusFeature("Boise National Forest", "Boise NF", shape(_BOISE_GEOJSON)),
        PadusFeature("Eagle Island State Park", None, shape(_EAGLE_GEOJSON)),
    ]


_PARKS = [
    ParkRecord(
        reference="US-4567", name="Boise National Forest", country="US", lat=44.0, lon=-115.5
    ),
    ParkRecord(
        reference="US-0001", name="Acadia National Park", country="US", lat=44.31, lon=-68.2034
    ),  # no PAD-US feature -> point fallback
    ParkRecord(
        reference="CA-0001", name="Banff", country="CA", lat=51.0, lon=-115.5
    ),  # non-US -> point fallback (PHASE 1)
    ParkRecord(
        reference="US-9999", name="Inactive", country="US", lat=None, lon=None
    ),  # no point, no polygon -> no rows
]


# --- orchestration: compute_park_grids over a parks iterable -------------------


def test_compute_park_grids_polygon_match_for_us_park() -> None:
    rows = padus.compute_park_grids(_PARKS, _fake_features())
    boise = [r for r in rows if r.reference == "US-4567"]
    assert boise
    assert {r.source for r in boise} == {"padus"}
    assert {r.confidence for r in boise} == {"high"}
    assert "DN13" in {r.grid for r in boise}


def test_compute_park_grids_unmatched_us_park_point_fallback() -> None:
    rows = padus.compute_park_grids(_PARKS, _fake_features())
    acadia = [r for r in rows if r.reference == "US-0001"]
    assert len(acadia) == 1
    assert acadia[0].source == "pota-point"
    assert acadia[0].confidence == "low"


def test_compute_park_grids_non_us_park_point_fallback_phase1() -> None:
    # PHASE 1: only US parks are matched against PAD-US; non-US parks ride the point grid.
    rows = padus.compute_park_grids(_PARKS, _fake_features())
    banff = [r for r in rows if r.reference == "CA-0001"]
    assert len(banff) == 1
    assert banff[0].source == "pota-point"


def test_compute_park_grids_no_point_no_polygon_yields_nothing() -> None:
    rows = padus.compute_park_grids(_PARKS, _fake_features())
    assert not [r for r in rows if r.reference == "US-9999"]


def test_compute_park_grids_only_processes_us_against_padus() -> None:
    # A non-US park whose point happens to fall inside a US PAD-US polygon must still NOT
    # be polygon-matched in PHASE 1 (country gate), to avoid cross-border false matches.
    parks = [
        ParkRecord(
            reference="XX-0001", name="Boise National Forest", country="XX", lat=44.0, lon=-115.5
        )
    ]
    rows = padus.compute_park_grids(parks, _fake_features())
    assert len(rows) == 1
    assert rows[0].source == "pota-point"  # not matched to the US polygon


def test_compute_park_grids_never_raises_on_bad_geometry() -> None:
    # A degenerate feature geometry must not blow up the whole build.
    bad = PadusFeature("Broken", None, shape({"type": "Polygon", "coordinates": [[]]}))
    parks = [
        ParkRecord(
            reference="US-4567", name="Boise National Forest", country="US", lat=44.0, lon=-115.5
        )
    ]
    rows = padus.compute_park_grids(parks, [bad] + _fake_features())
    assert any(r.reference == "US-4567" for r in rows)


# --- download/cache seam (no network; injected fetcher) ------------------------


def test_download_caches_under_dated_dir(tmp_path: Path) -> None:
    def fetcher(url: str, since: float | None) -> bytes | None:
        return b"FAKE-PADUS-GEOPACKAGE-BYTES"

    src = padus.PadusSource()
    path = src.download(tmp_path, on=dt.date(2026, 6, 17), fetcher=fetcher)
    assert path.exists()
    assert "padus" in path.parts
    assert "2026-06-17" in path.parts


def test_download_reuses_cache_on_not_modified(tmp_path: Path) -> None:
    calls = {"n": 0}

    def fetcher(url: str, since: float | None) -> bytes | None:
        calls["n"] += 1
        if calls["n"] == 1:
            return b"FAKE-PADUS-GEOPACKAGE-BYTES"
        return None  # 304 Not Modified — reuse cache

    src = padus.PadusSource()
    day = dt.date(2026, 6, 17)
    p1 = src.download(tmp_path, on=day, fetcher=fetcher)
    p2 = src.download(tmp_path, on=day, fetcher=fetcher)
    assert p1 == p2
    assert p1.read_bytes() == b"FAKE-PADUS-GEOPACKAGE-BYTES"


def test_read_features_seam_is_present_but_gdal_gated() -> None:
    # The real reader lives behind _read_padus_features; without pyogrio installed it
    # should raise a clear, actionable error rather than a bare ImportError. (We do not
    # install GDAL in CI, so we only assert the seam exists and is documented.)
    assert hasattr(padus, "_read_padus_features")


# --- hdb-b6a7: real PAD-US national download pinned (zipped File Geodatabase) ---


def test_padus_download_url_is_pinned_not_placeholder() -> None:
    # The default must be a real USGS ScienceBase download, not the scaffolding
    # placeholder. PAD-US 4.0's national distribution is a File Geodatabase ZIP.
    url = padus.PADUS_DOWNLOAD_URL
    assert url.startswith("https://")
    assert "placeholder" not in url.lower()
    assert "sciencebase" in url
    # The national distribution is a File Geodatabase, not a GeoPackage; the scaffolding
    # placeholder pointed at a fictional "padus_national_geopackage.zip".
    assert "geopackage" not in url.lower()


def test_download_caches_zipped_geodatabase_extension(tmp_path: Path) -> None:
    # PAD-US national ships as a zipped File Geodatabase (.gdb.zip), NOT a .gpkg —
    # GDAL reads the .gdb inside the zip via /vsizip/. The cached file name must
    # reflect that so the reader opens it correctly.
    def fetcher(url: str, since: float | None) -> bytes | None:
        return b"PK\x03\x04FAKE-PADUS-GDB-ZIP"

    path = padus.PadusSource().download(tmp_path, on=dt.date(2026, 6, 17), fetcher=fetcher)
    assert path.name.endswith(".gdb.zip")


def test_select_combined_layer_picks_integrated_feature_class() -> None:
    # The GDB has 6 feature classes; the Combined layer integrates them all, so the
    # reader must select it (by its PADUS*Combined* prefix) regardless of the exact,
    # version-suffixed name.
    names = [
        "Fee",
        "Designation",
        "Easement",
        "Proclamation",
        "Marine",
        "PADUS4_0Combined_Proclamation_Marine_Fee_Designation_Easement",
    ]
    assert (
        padus._select_combined_layer(names)
        == "PADUS4_0Combined_Proclamation_Marine_Fee_Designation_Easement"
    )


def test_select_combined_layer_raises_clearly_when_absent() -> None:
    import pytest

    with pytest.raises(RuntimeError, match="Combined"):
        padus._select_combined_layer(["Fee", "Designation"])


def test_vsizip_path_wraps_zip_for_gdal() -> None:
    p = Path("/data/raw/padus/2026-06-17/padus_national.gdb.zip")
    assert padus._vsizip_path(p) == f"/vsizip/{p}"


def test_vsizip_path_targets_inner_gdb_when_given() -> None:
    p = Path("/data/raw/padus/2026-06-17/padus_national.gdb.zip")
    assert padus._vsizip_path(p, "PADUS4_0_Geodatabase.gdb") == (
        f"/vsizip/{p}/PADUS4_0_Geodatabase.gdb"
    )


def test_inner_gdb_finds_gdb_dir_among_siblings(tmp_path: Path) -> None:
    # The real PAD-US zip holds the .gdb ALONGSIDE metadata XML + .lyrx siblings, so GDAL
    # won't auto-descend into it from the zip root (hdb-d90d: it picked a stray layer). The
    # reader must locate the .gdb directory among the siblings and target it explicitly.
    import zipfile

    zp = tmp_path / "padus_national.gdb.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("PADUS40_MetadataXML_FGDC.xml", "<metadata/>")
        z.writestr("PADUS4_0_GAPStatusCode.lyrx", "{}")
        z.writestr("PADUS4_0_Geodatabase.gdb/a00000001.gdbtable", b"\x00")
        z.writestr("PADUS4_0_Geodatabase.gdb/gdb", b"\x00")
    assert padus._inner_gdb(zp) == "PADUS4_0_Geodatabase.gdb"


def test_inner_gdb_none_when_no_gdb(tmp_path: Path) -> None:
    # A bare zipped single-file dataset (no .gdb dir) -> None; the reader opens the zip root.
    import zipfile

    zp = tmp_path / "x.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("data.gpkg", b"\x00")
    assert padus._inner_gdb(zp) is None
