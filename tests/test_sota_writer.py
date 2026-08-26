"""Tests for the SOTA summits Parquet + SQLite writers (hdb-ca00).

The summits dataset is ADDITIVE: it writes its own parquet file
(hamcall-db-sota-summits-YYYY-MM-DD.parquet) and a `sota_summits` table in the SQLite
artifact. It must NEVER touch the callsign current/history schema.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import polars as pl

from hamcall_db.sources.sota import SummitRecord
from hamcall_db.sqlite_writer import write_sota_summits_sqlite
from hamcall_db.writer import write_sota_summits_parquet

SUMMITS = [
    SummitRecord(
        reference="G/LD-001",
        name="Scafell Pike",
        association="England (Lake District)",
        region="Lake District",
        alt_m=978,
        alt_ft=3209,
        grid="IO84mn",
        lat=54.4542,
        lon=-3.2117,
        points=8,
        bonus_points=3,
        valid_from="2002-07-01",
        valid_to="2099-12-31",
        active=True,
        source="sota",
        synced_at="2026-06-18",
    ),
    SummitRecord(
        reference="W0C/SP-099",
        name="Retired Test Summit",
        association="W0C - Colorado",
        region="Sangre de Cristo",
        alt_m=None,
        alt_ft=None,
        grid=None,
        lat=None,
        lon=None,
        points=None,
        bonus_points=None,
        valid_from="2010-05-01",
        valid_to="2010-12-31",
        active=False,
        source="sota",
        synced_at="2026-06-18",
    ),
]


def test_write_parquet_roundtrip(tmp_path: Path) -> None:
    out = tmp_path / "sota-summits.parquet"
    count = write_sota_summits_parquet(SUMMITS, out)
    assert count == 2
    frame = pl.read_parquet(out)
    assert frame.height == 2
    row = frame.filter(pl.col("reference") == "G/LD-001").to_dicts()[0]
    assert row["name"] == "Scafell Pike"
    assert row["grid"] == "IO84mn"  # verbatim 6-char, not truncated
    assert row["lat"] == 54.4542
    assert row["alt_m"] == 978
    assert row["points"] == 8


def test_write_parquet_column_order(tmp_path: Path) -> None:
    out = tmp_path / "sota-summits.parquet"
    write_sota_summits_parquet(SUMMITS, out)
    frame = pl.read_parquet(out)
    assert frame.columns == [
        "reference",
        "name",
        "association",
        "region",
        "alt_m",
        "alt_ft",
        "grid",
        "lat",
        "lon",
        "points",
        "bonus_points",
        "valid_from",
        "valid_to",
        "active",
        "source",
        "synced_at",
    ]


def test_write_parquet_nullable_fields(tmp_path: Path) -> None:
    out = tmp_path / "sota-summits.parquet"
    write_sota_summits_parquet(SUMMITS, out)
    frame = pl.read_parquet(out)
    row = frame.filter(pl.col("reference") == "W0C/SP-099").to_dicts()[0]
    assert row["lat"] is None
    assert row["lon"] is None
    assert row["grid"] is None
    assert row["alt_m"] is None
    assert row["points"] is None


def test_write_sqlite_creates_sota_summits_table(tmp_path: Path) -> None:
    db = tmp_path / "hamcall.db"
    count = write_sota_summits_sqlite(SUMMITS, db)
    assert count == 2
    con = sqlite3.connect(db)
    try:
        tables = {
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "sota_summits" in tables
        row = con.execute(
            "SELECT reference, name, grid, lat, alt_m, active FROM sota_summits "
            "WHERE reference = 'G/LD-001'"
        ).fetchone()
        assert row == ("G/LD-001", "Scafell Pike", "IO84mn", 54.4542, 978, 1)
    finally:
        con.close()


def test_write_sqlite_reference_is_primary_key(tmp_path: Path) -> None:
    db = tmp_path / "hamcall.db"
    write_sota_summits_sqlite(SUMMITS, db)
    con = sqlite3.connect(db)
    try:
        cols = con.execute("PRAGMA table_info(sota_summits)").fetchall()
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

    write_sota_summits_sqlite(SUMMITS, db)

    con = sqlite3.connect(db)
    try:
        # The existing callsign table is untouched (additive contract).
        assert con.execute("SELECT callsign FROM current").fetchone() == ("W1AW",)
        assert con.execute("SELECT COUNT(*) FROM sota_summits").fetchone()[0] == 2
    finally:
        con.close()
