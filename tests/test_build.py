"""Tests for build-time source collection resilience (hdb-6f3b).

A single source's download/parse failure must NOT abort an --all build; it is logged
and skipped, and the build proceeds from whatever succeeded.
"""

from __future__ import annotations

import sqlite3

import polars as pl
from shapely.geometry import Polygon
from typer.testing import CliRunner

from hamcall_db import enrich_allstar
from hamcall_db.build import SOURCES, _collect_streams, app
from hamcall_db.models import Record
from hamcall_db.sources.osm_grids import OsmFeature
from hamcall_db.sources.padus_grids import PadusFeature
from hamcall_db.sources.pota import ParkRecord


class _GoodSource:
    name = "good"
    synced_at = None

    def download(self, work_dir):
        return work_dir

    def parse(self, path, *, synced_at=None):
        return [Record(callsign="W1AW", source="good")]


class _BadSource:
    name = "bad"
    synced_at = None

    def download(self, work_dir):
        raise OSError("connection timed out")

    def parse(self, path, *, synced_at=None):
        return []


def test_collect_streams_skips_a_failing_source(tmp_path):
    sources = {"good": _GoodSource(), "bad": _BadSource()}
    streams, skipped = _collect_streams(["good", "bad"], tmp_path, sources=sources)
    assert skipped == ["bad"]
    assert len(streams) == 1
    assert streams[0][0].callsign == "W1AW"


def test_collect_streams_all_succeed(tmp_path):
    sources = {"good": _GoodSource()}
    streams, skipped = _collect_streams(["good"], tmp_path, sources=sources)
    assert skipped == []
    assert len(streams) == 1


def test_collect_streams_all_failed_returns_empty(tmp_path):
    sources = {"bad": _BadSource()}
    streams, skipped = _collect_streams(["bad"], tmp_path, sources=sources)
    assert streams == []
    assert skipped == ["bad"]


# --- AllStarLink enrichment wiring on --all (hdb-8803) -------------------------

FIXTURE = (
    __import__("pathlib").Path(__file__).parent
    / "fixtures"
    / "allstar"
    / "allmondb.txt"
)


def _stub_all_sources(monkeypatch):
    """Make every registered source yield one fixed Record, no network."""

    class _Stub:
        def __init__(self, tag):
            self.name = tag
            self.synced_at = None

        def download(self, work_dir):
            return work_dir

        def parse(self, path, *, synced_at=None):
            return [Record(callsign="WB6NIL", source=self.name)]

    monkeypatch.setitem(SOURCES, "fcc", _Stub("fcc"))
    for tag in list(SOURCES):
        monkeypatch.setitem(SOURCES, tag, _Stub(tag))


def test_all_build_enriches_allstar_nodes(tmp_path, monkeypatch):
    _stub_all_sources(monkeypatch)
    # Skip cty download/enrichment (no network): return None so build's cty branch is a
    # no-op.
    monkeypatch.setattr("hamcall_db.build.ad1c.download_cty", lambda *a, **k: None)
    monkeypatch.setattr(enrich_allstar, "download_allstar", lambda *a, **k: FIXTURE)

    out = tmp_path / "out.parquet"
    result = CliRunner().invoke(
        app, ["--all", "--out", str(out), "--work-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    frame = pl.read_parquet(out)
    nodes = frame.filter(pl.col("callsign") == "WB6NIL")["allstar_nodes"].to_list()[0]
    assert nodes == [2000, 2001, 2002]


def test_all_build_treats_suffixless_out_as_directory(tmp_path, monkeypatch):
    """`--out dist` (no suffix, not yet existing) means a directory: create it and
    write all three dated artifacts inside it (regression for the CI release where
    `out.is_dir()` was False for a non-existent dir, so the parquet landed in a file
    literally named `dist` and history/sqlite scattered into the cwd)."""
    _stub_all_sources(monkeypatch)
    monkeypatch.setattr("hamcall_db.build.ad1c.download_cty", lambda *a, **k: None)
    monkeypatch.setattr(enrich_allstar, "download_allstar", lambda *a, **k: FIXTURE)

    out = tmp_path / "dist"  # does NOT exist yet, no file suffix
    result = CliRunner().invoke(
        app, ["--all", "--out", str(out), "--work-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert out.is_dir(), "out should have been created as a directory"
    dated = sorted(p.name for p in out.glob("hamcall-db-*"))
    # current parquet + history parquet + sqlite .db, all inside dist/
    assert any(n.endswith(".parquet") and "history" not in n for n in dated)
    assert any("history" in n and n.endswith(".parquet") for n in dated)
    assert any(n.endswith(".db") for n in dated)
    # nothing scattered into the working dir
    assert not list(tmp_path.glob("hamcall-db-*"))


def test_all_build_resilient_when_allstar_download_fails(tmp_path, monkeypatch):
    _stub_all_sources(monkeypatch)
    monkeypatch.setattr("hamcall_db.build.ad1c.download_cty", lambda *a, **k: None)

    def _boom(*a, **k):
        raise OSError("connection timed out")

    monkeypatch.setattr(enrich_allstar, "download_allstar", _boom)

    out = tmp_path / "out.parquet"
    result = CliRunner().invoke(
        app, ["--all", "--out", str(out), "--work-dir", str(tmp_path)]
    )
    # Build still succeeds; allstar_nodes left empty.
    assert result.exit_code == 0, result.output
    frame = pl.read_parquet(out)
    nodes = frame.filter(pl.col("callsign") == "WB6NIL")["allstar_nodes"].to_list()[0]
    assert nodes == []


# --- OSM / PAD-US license segregation end-to-end at the CLI (hdb-438b) ---------


def _stub_parks_and_boundaries(tmp_path, monkeypatch):
    """Stub POTA + PAD-US + OSM loaders so the --all build's park block runs offline.

    One US park (PAD-US/CC-BY-NC set) and one non-US park (OSM/ODbL set), each matched to a
    boundary, so both grid artifacts get real rows without any network or GIS reader.
    """
    _stub_all_sources(monkeypatch)
    monkeypatch.setattr("hamcall_db.build.ad1c.download_cty", lambda *a, **k: None)
    monkeypatch.setattr(enrich_allstar, "download_allstar", lambda *a, **k: FIXTURE)

    parks = [
        ParkRecord(reference="US-4567", name="Boise National Forest", country="US",
                   lat=44.0, lon=-115.5),
        ParkRecord(reference="DE-0001", name="Bayerischer Wald", country="DE",
                   lat=47.5, lon=12.0),
    ]

    class _StubPota:
        def download(self, work_dir):
            return work_dir

        def parse(self, path):
            return parks

    monkeypatch.setattr("hamcall_db.build.PotaSource", _StubPota)

    boise = Polygon([(-116.5, 43.5), (-115.0, 43.5), (-115.0, 44.5), (-116.5, 44.5)])
    baywald = Polygon([(11.0, 47.0), (13.0, 47.0), (13.0, 48.0), (11.0, 48.0)])
    monkeypatch.setattr(
        "hamcall_db.build._load_padus_features",
        lambda wd: [PadusFeature("Boise National Forest", "Boise NF", boise)],
    )
    monkeypatch.setattr(
        "hamcall_db.build._load_osm_features",
        lambda wd: [OsmFeature("Bayerischer Wald", {"boundary": "protected_area"}, "r1",
                               baywald)],
    )
    return parks


def test_all_build_writes_segregated_osm_and_ccbync_grid_artifacts(tmp_path, monkeypatch):
    _stub_parks_and_boundaries(tmp_path, monkeypatch)

    out = tmp_path / "dist"
    result = CliRunner().invoke(
        app, ["--all", "--out", str(out), "--work-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output

    # The OSM (ODbL) grid set ships in its OWN parquet AND its OWN .db, separate from the
    # CC-BY-NC files.
    osm_parquets = list(out.glob("hamcall-db-pota-park-grids-osm-*.parquet"))
    osm_dbs = list(out.glob("hamcall-db-pota-park-grids-osm-*.db"))
    cc_grid_parquets = [
        p for p in out.glob("hamcall-db-pota-park-grids-*.parquet")
        if "osm" not in p.name
    ]
    assert len(osm_parquets) == 1
    assert len(osm_dbs) == 1
    assert len(cc_grid_parquets) == 1

    # OSM parquet: only the non-US park, OSM provenance, no US rows.
    osm_frame = pl.read_parquet(osm_parquets[0])
    assert set(osm_frame["reference"].to_list()) == {"DE-0001"}
    assert set(osm_frame["source"].to_list()) == {"osm"}
    assert not [r for r in osm_frame["reference"].to_list() if r.startswith("US-")]

    # CC-BY-NC park-grid parquet (PAD-US PHASE 1): the US park is polygon-matched ('padus');
    # the non-US park rides PHASE 1's PUBLIC-DOMAIN point fallback ('pota-point') here. The
    # license-critical invariant is that NO OSM-DERIVED provenance ('osm'/'osm-point') ever
    # leaks into this CC-BY-NC file.
    cc_frame = pl.read_parquet(cc_grid_parquets[0])
    cc_sources = set(cc_frame["source"].to_list())
    assert cc_sources <= {"padus", "pota-point"}
    assert "osm" not in cc_sources
    assert "osm-point" not in cc_sources
    us_rows = cc_frame.filter(pl.col("reference") == "US-4567")
    assert set(us_rows["source"].to_list()) == {"padus"}

    # The CC-BY-NC SQLite .db (callsign + parks + pota_park_grids) must NOT carry the OSM
    # table; the OSM .db must NOT carry any CC-BY-NC table.
    cc_db = next(
        p for p in out.glob("hamcall-db-*.db")
        if "pota-park-grids-osm" not in p.name
    )
    con = sqlite3.connect(cc_db)
    try:
        cc_tables = {
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        con.close()
    assert "pota_park_grids" in cc_tables
    assert "pota_park_grids_osm" not in cc_tables  # no ODbL taint in the CC-BY-NC file

    con = sqlite3.connect(osm_dbs[0])
    try:
        osm_tables = {
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        con.close()
    assert osm_tables == {"pota_park_grids_osm"}


def test_all_build_resilient_when_osm_reader_missing(tmp_path, monkeypatch):
    # OSM boundaries unavailable (no GIS reader / download fails) must NOT block the build:
    # non-US parks degrade to point grids in the SEPARATE ODbL file; US parks unaffected.
    _stub_parks_and_boundaries(tmp_path, monkeypatch)

    def _boom(wd):
        raise RuntimeError("pyogrio not installed")

    monkeypatch.setattr("hamcall_db.build._load_osm_features", _boom)

    out = tmp_path / "dist"
    result = CliRunner().invoke(
        app, ["--all", "--out", str(out), "--work-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    osm_parquets = list(out.glob("hamcall-db-pota-park-grids-osm-*.parquet"))
    assert len(osm_parquets) == 1
    osm_frame = pl.read_parquet(osm_parquets[0])
    # The non-US park still appears, via the point fallback in the ODbL file.
    assert set(osm_frame["reference"].to_list()) == {"DE-0001"}
    assert set(osm_frame["source"].to_list()) == {"osm-point"}
