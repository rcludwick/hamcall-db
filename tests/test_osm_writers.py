"""Tests for the SEPARATE ODbL OSM park-grids writers (hdb-438b, PHASE 2).

The OSM-derived grids are an ODbL derivative database and MUST ship in their OWN files —
NEVER mixed into the CC BY-NC artifacts. These tests assert:

  * a separate ``hamcall-db-pota-park-grids-osm-*.parquet`` with the (reference, grid,
    source, confidence) schema mirroring PAD-US;
  * a separate ``.db`` whose table is named ``pota_park_grids_osm`` (NOT ``pota_park_grids``);
  * the OSM writers NEVER create or touch the CC-BY-NC tables (current/history/pota_parks/
    pota_park_grids) — license segregation.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import polars as pl

from hamcall_db.sources.padus_grids import ParkGridRecord
from hamcall_db.sqlite_writer import (
    write_pota_park_grids_osm_sqlite,
    write_pota_park_grids_sqlite,
)
from hamcall_db.writer import write_pota_park_grids_osm_parquet

_OSM_ROWS = [
    ParkGridRecord(reference="DE-0001", grid="JN57", source="osm", confidence="high"),
    ParkGridRecord(reference="DE-0001", grid="JN58", source="osm", confidence="high"),
    ParkGridRecord(reference="DE-9999", grid="JO50", source="osm-point", confidence="low"),
]


def test_osm_parquet_writes_separate_file_with_mirrored_schema(tmp_path: Path) -> None:
    out = tmp_path / "hamcall-db-pota-park-grids-osm-2026-06-17.parquet"
    count = write_pota_park_grids_osm_parquet(_OSM_ROWS, out)
    assert count == 3
    frame = pl.read_parquet(out)
    assert frame.columns == ["reference", "grid", "source", "confidence"]
    assert set(frame["source"].to_list()) == {"osm", "osm-point"}


def test_osm_sqlite_uses_dedicated_table_name(tmp_path: Path) -> None:
    db = tmp_path / "hamcall-db-pota-park-grids-osm-2026-06-17.db"
    count = write_pota_park_grids_osm_sqlite(_OSM_ROWS, db)
    assert count == 3
    con = sqlite3.connect(db)
    try:
        tables = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        # The OSM table is dedicated and segregated; the CC-BY-NC table name is NOT present.
        assert "pota_park_grids_osm" in tables
        assert "pota_park_grids" not in tables
        rows = con.execute(
            "SELECT reference, grid, source, confidence FROM pota_park_grids_osm "
            "ORDER BY grid"
        ).fetchall()
        assert rows[0] == ("DE-0001", "JN57", "osm", "high")
        assert ("DE-9999", "JO50", "osm-point", "low") in rows
    finally:
        con.close()


def test_osm_sqlite_never_touches_cc_by_nc_tables(tmp_path: Path) -> None:
    # The OSM .db must be its OWN file with ONLY the OSM table — no CC-BY-NC tables created.
    db = tmp_path / "osm.db"
    write_pota_park_grids_osm_sqlite(_OSM_ROWS, db)
    con = sqlite3.connect(db)
    try:
        tables = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert tables == {"pota_park_grids_osm"}
        for forbidden in ("current", "history", "pota_parks", "pota_park_grids"):
            assert forbidden not in tables
    finally:
        con.close()


def test_osm_and_padus_grids_go_to_different_db_files(tmp_path: Path) -> None:
    # Concrete license-segregation proof: writing the PAD-US set to one .db and the OSM set
    # to another leaves the OSM table out of the CC-BY-NC file and vice versa.
    cc_db = tmp_path / "hamcall-db-2026-06-17.db"
    odbl_db = tmp_path / "hamcall-db-pota-park-grids-osm-2026-06-17.db"
    padus_rows = [
        ParkGridRecord(reference="US-4567", grid="DN13", source="padus",
                       confidence="high"),
    ]
    write_pota_park_grids_sqlite(padus_rows, cc_db)
    write_pota_park_grids_osm_sqlite(_OSM_ROWS, odbl_db)

    cc = sqlite3.connect(cc_db)
    odbl = sqlite3.connect(odbl_db)
    try:
        cc_tables = {
            r[0] for r in cc.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        odbl_tables = {
            r[0] for r in odbl.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "pota_park_grids" in cc_tables
        assert "pota_park_grids_osm" not in cc_tables  # no ODbL taint in the CC-BY-NC file
        assert "pota_park_grids_osm" in odbl_tables
        assert "pota_park_grids" not in odbl_tables
    finally:
        cc.close()
        odbl.close()


def test_osm_sqlite_idempotent_refresh(tmp_path: Path) -> None:
    db = tmp_path / "osm.db"
    write_pota_park_grids_osm_sqlite(_OSM_ROWS, db)
    count = write_pota_park_grids_osm_sqlite(_OSM_ROWS, db)  # re-run replaces rows
    assert count == 3
    con = sqlite3.connect(db)
    try:
        n = con.execute("SELECT COUNT(*) FROM pota_park_grids_osm").fetchone()[0]
        assert n == 3  # not doubled
    finally:
        con.close()
