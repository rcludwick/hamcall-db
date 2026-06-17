"""Tests for the POTA parks Parquet + SQLite writers (hdb-9640).

The parks dataset is ADDITIVE: it writes its own parquet file
(hamcall-db-pota-parks-YYYY-MM-DD.parquet) and a `pota_parks` table in the SQLite
artifact. It must NEVER touch the callsign current/history schema.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import polars as pl

from hamcall_db.sources.pota import ParkRecord
from hamcall_db.sqlite_writer import write_pota_parks_sqlite
from hamcall_db.writer import write_pota_parks_parquet

PARKS = [
    ParkRecord(
        reference="US-0001",
        name="Acadia National Park",
        location_desc="US-ME",
        region="ME",
        country="US",
        dxcc=None,
        grid="FN54vh",
        lat=44.31,
        lon=-68.2034,
        active=True,
        source="pota",
        synced_at="2026-06-17",
    ),
    ParkRecord(
        reference="US-9999",
        name="Inactive Test Park",
        location_desc="US-CA",
        region="CA",
        country="US",
        dxcc=None,
        grid=None,
        lat=None,
        lon=None,
        active=False,
        source="pota",
        synced_at="2026-06-17",
    ),
]


def test_write_parquet_roundtrip(tmp_path: Path) -> None:
    out = tmp_path / "pota-parks.parquet"
    count = write_pota_parks_parquet(PARKS, out)
    assert count == 2
    frame = pl.read_parquet(out)
    assert frame.height == 2
    row = frame.filter(pl.col("reference") == "US-0001").to_dicts()[0]
    assert row["name"] == "Acadia National Park"
    assert row["grid"] == "FN54vh"  # verbatim, not truncated
    assert row["lat"] == 44.31


def test_write_parquet_column_order(tmp_path: Path) -> None:
    out = tmp_path / "pota-parks.parquet"
    write_pota_parks_parquet(PARKS, out)
    frame = pl.read_parquet(out)
    assert frame.columns == [
        "reference",
        "name",
        "location_desc",
        "region",
        "country",
        "dxcc",
        "grid",
        "lat",
        "lon",
        "active",
        "source",
        "synced_at",
    ]


def test_write_parquet_nullable_coords(tmp_path: Path) -> None:
    out = tmp_path / "pota-parks.parquet"
    write_pota_parks_parquet(PARKS, out)
    frame = pl.read_parquet(out)
    row = frame.filter(pl.col("reference") == "US-9999").to_dicts()[0]
    assert row["lat"] is None
    assert row["lon"] is None
    assert row["grid"] is None


def test_write_sqlite_creates_pota_parks_table(tmp_path: Path) -> None:
    db = tmp_path / "hamcall.db"
    count = write_pota_parks_sqlite(PARKS, db)
    assert count == 2
    con = sqlite3.connect(db)
    try:
        tables = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "pota_parks" in tables
        row = con.execute(
            "SELECT reference, name, grid, lat, active FROM pota_parks "
            "WHERE reference = 'US-0001'"
        ).fetchone()
        assert row == ("US-0001", "Acadia National Park", "FN54vh", 44.31, 1)
    finally:
        con.close()


def test_write_sqlite_reference_is_primary_key(tmp_path: Path) -> None:
    db = tmp_path / "hamcall.db"
    write_pota_parks_sqlite(PARKS, db)
    con = sqlite3.connect(db)
    try:
        cols = con.execute("PRAGMA table_info(pota_parks)").fetchall()
        pk_cols = [c[1] for c in cols if c[5] == 1]
        assert pk_cols == ["reference"]
    finally:
        con.close()


def test_write_sqlite_into_existing_db_preserves_other_tables(tmp_path: Path) -> None:
    db = tmp_path / "hamcall.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE current (id INTEGER PRIMARY KEY, callsign TEXT)")
    con.execute("INSERT INTO current VALUES (1, 'W1AW')")
    con.commit()
    con.close()

    write_pota_parks_sqlite(PARKS, db)

    con = sqlite3.connect(db)
    try:
        # The existing callsign table is untouched (additive contract).
        assert con.execute("SELECT callsign FROM current").fetchone() == ("W1AW",)
        assert con.execute("SELECT COUNT(*) FROM pota_parks").fetchone()[0] == 2
    finally:
        con.close()
