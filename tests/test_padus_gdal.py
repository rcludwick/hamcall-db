"""Real-GDAL smoke test for the PAD-US reader seam (hdb-d90d, partial).

These exercise ``padus._read_padus_features`` — the ONE place GDAL/pyogrio is touched — which
the rest of the suite stubs out (no GDAL in CI). They build a SMALL synthetic File Geodatabase
that mimics the real PAD-US national layout (a version-suffixed ``PADUS4_0Combined_*`` layer
plus a decoy ``Fee`` layer, geometry in EPSG:5070, ``Unit_Nm`` / ``Loc_Nm`` columns), zip it,
and read it back through the production code path: ``/vsizip/`` open → ``list_layers`` →
``_select_combined_layer`` → column read → reproject to WGS84.

They SKIP when pyogrio is unavailable, so CI (which installs no GDAL) is unaffected; run them
with ``uv run --group padus pytest tests/test_padus_gdal.py``.

What this does NOT cover (still needs the real ~1.7 GB download, the rest of hdb-d90d): the
actual PAD-US 4.0 file's true layer name, that ``Unit_Nm``/``Loc_Nm`` are populated in the
real data, its real CRS, and that the pinned ``PADUS_DOWNLOAD_URL`` fetches.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

pyogrio = pytest.importorskip("pyogrio")

import numpy as np  # noqa: E402
from pyproj import Transformer  # noqa: E402
from shapely import to_wkb  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402

from hamcall_db.sources import padus  # noqa: E402

# A Boise-area box in WGS84; the real PAD-US ships geometry in EPSG:5070 (Albers), so we store
# it reprojected and expect the reader to bring it back to ~these WGS84 coordinates.
_BOX_WGS = Polygon(
    [(-116.5, 43.5), (-115.0, 43.5), (-115.0, 44.5), (-116.5, 44.5), (-116.5, 43.5)]
)
_COMBINED_LAYER = "PADUS4_0Combined_Proclamation_Marine_Fee_Designation_Easement"


def _synthetic_padus_zip(tmp_path: Path) -> Path:
    """Write a tiny two-layer File Geodatabase (Combined + a Fee decoy) and zip it."""
    gdb = tmp_path / "PADUS4_0.gdb"
    to_5070 = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    box_5070 = Polygon([to_5070.transform(x, y) for x, y in _BOX_WGS.exterior.coords])

    def _write_layer(layer: str, unit: str, loc: str, append: bool) -> None:
        pyogrio.raw.write(
            str(gdb),
            geometry=np.array([to_wkb(box_5070)], dtype=object),
            field_data=[np.array([unit], dtype=object), np.array([loc], dtype=object)],
            fields=["Unit_Nm", "Loc_Nm"],
            layer=layer,
            driver="OpenFileGDB",
            geometry_type="Polygon",
            crs="EPSG:5070",
            append=append,
        )

    _write_layer("Fee", "Decoy Fee Unit", "decoy", append=False)
    _write_layer(_COMBINED_LAYER, "Boise National Forest", "Boise NF", append=True)

    # The REAL PAD-US zip holds the .gdb ALONGSIDE metadata + a stray sibling dataset, so
    # GDAL won't auto-descend into the .gdb from the zip root (it picked the wrong layer in
    # hdb-d90d). Reproduce that layout with a decoy top-level GeoPackage so the reader is
    # forced to target the inner .gdb explicitly.
    decoy = tmp_path / "tl_2022_us_state.gpkg"
    pyogrio.raw.write(
        str(decoy),
        geometry=np.array([to_wkb(_BOX_WGS)], dtype=object),
        field_data=[np.array(["decoy"], dtype=object)],
        fields=["NAME"],
        layer="tl_2022_us_state",
        driver="GPKG",
        geometry_type="Polygon",
        crs="EPSG:4326",
    )
    (tmp_path / "PADUS40_MetadataXML_FGDC.xml").write_text("<metadata/>", encoding="utf-8")

    zip_path = tmp_path / "padus_national.gdb.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in (*gdb.rglob("*"), decoy, tmp_path / "PADUS40_MetadataXML_FGDC.xml"):
            z.write(f, f.relative_to(tmp_path))
    return zip_path


def test_read_padus_features_reads_combined_layer_from_zipped_gdb(tmp_path: Path) -> None:
    features = list(padus._read_padus_features(_synthetic_padus_zip(tmp_path)))

    # Only the Combined layer is read (the Fee decoy lives in another layer and is ignored).
    assert len(features) == 1
    feat = features[0]
    assert feat.unit_nm == "Boise National Forest"
    assert feat.loc_nm == "Boise NF"


def test_read_padus_features_reprojects_to_wgs84(tmp_path: Path) -> None:
    feat = next(iter(padus._read_padus_features(_synthetic_padus_zip(tmp_path))))
    # Stored in EPSG:5070; the reader must bring it back to WGS84 (~the original Boise box).
    cx, cy = feat.geometry.centroid.x, feat.geometry.centroid.y
    assert -116.5 < cx < -115.0
    assert 43.5 < cy < 44.5


def test_synthetic_padus_grids_end_to_end(tmp_path: Path) -> None:
    # Full path: read the zipped GDB, then compute a US park's grid set off the real geometry.
    features = padus.PadusSource().read_features(_synthetic_padus_zip(tmp_path))
    park = padus.ParkRecord(
        reference="US-4567", name="Boise National Forest", country="US", lat=44.0, lon=-115.5
    )
    rows = padus.compute_park_grids([park], features)
    assert rows
    assert {r.source for r in rows} == {"padus"}
    assert "DN13" in {r.grid for r in rows}
