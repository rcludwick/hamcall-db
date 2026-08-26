"""Tests for the pota_park_grids child-table writers (hdb-f53c, PHASE 1).

The grid-set table is ADDITIVE to the MAIN CC BY-NC artifact: its own parquet file
(hamcall-db-pota-park-grids-YYYY-MM-DD.parquet) plus a ``pota_park_grids`` table in the
SQLite .db. It must NEVER touch the callsign current/history schema NOR the pota_parks
schema — it only references parks by ``reference`` (the FK).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import polars as pl

from hamcall_db.sources.padus_grids import ParkGridRecord
from hamcall_db.sqlite_writer import write_pota_park_grids_sqlite
from hamcall_db.writer import write_pota_park_grids_parquet

GRIDS = [
    ParkGridRecord(reference="US-4567", grid="DN13", source="padus", confidence="high"),
    ParkGridRecord(reference="US-4567", grid="DN14", source="padus", confidence="high"),
    ParkGridRecord(reference="US-0001", grid="FN54", source="pota-point", confidence="low"),
]


def test_write_parquet_roundtrip(tmp_path: Path) -> None:
    out = tmp_path / "pota-park-grids.parquet"
    count = write_pota_park_grids_parquet(GRIDS, out)
    assert count == 3
    frame = pl.read_parquet(out)
    assert frame.height == 3
    rows = frame.filter(pl.col("reference") == "US-4567").to_dicts()
    assert {r["grid"] for r in rows} == {"DN13", "DN14"}
    assert all(r["source"] == "padus" for r in rows)


def test_write_parquet_column_order(tmp_path: Path) -> None:
    out = tmp_path / "pota-park-grids.parquet"
    write_pota_park_grids_parquet(GRIDS, out)
    frame = pl.read_parquet(out)
    assert frame.columns == ["reference", "grid", "source", "confidence"]


def test_write_parquet_all_grids_four_char(tmp_path: Path) -> None:
    out = tmp_path / "pota-park-grids.parquet"
    write_pota_park_grids_parquet(GRIDS, out)
    frame = pl.read_parquet(out)
    assert all(len(g) == 4 for g in frame["grid"].to_list())  # mem-e3fd


def test_write_sqlite_creates_table(tmp_path: Path) -> None:
    db = tmp_path / "hamcall.db"
    count = write_pota_park_grids_sqlite(GRIDS, db)
    assert count == 3
    con = sqlite3.connect(db)
    try:
        tables = {
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "pota_park_grids" in tables
        rows = con.execute(
            "SELECT grid, source, confidence FROM pota_park_grids WHERE reference = 'US-0001'"
        ).fetchall()
        assert rows == [("FN54", "pota-point", "low")]
    finally:
        con.close()


def test_write_sqlite_has_reference_index(tmp_path: Path) -> None:
    db = tmp_path / "hamcall.db"
    write_pota_park_grids_sqlite(GRIDS, db)
    con = sqlite3.connect(db)
    try:
        idx = {r[1] for r in con.execute("PRAGMA index_list(pota_park_grids)").fetchall()}
        # An index on reference (the FK join back to pota_parks) exists.
        assert any("reference" in name for name in idx)
    finally:
        con.close()


def test_write_sqlite_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "hamcall.db"
    write_pota_park_grids_sqlite(GRIDS, db)
    write_pota_park_grids_sqlite(GRIDS, db)  # re-run replaces, doesn't append
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM pota_park_grids").fetchone()[0] == 3
    finally:
        con.close()


def test_write_sqlite_preserves_other_tables(tmp_path: Path) -> None:
    db = tmp_path / "hamcall.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE current (id INTEGER PRIMARY KEY, callsign TEXT)")
    con.execute("INSERT INTO current VALUES (1, 'W1AW')")
    con.execute("CREATE TABLE pota_parks (reference TEXT PRIMARY KEY, name TEXT)")
    con.execute("INSERT INTO pota_parks VALUES ('US-4567', 'Boise National Forest')")
    con.commit()
    con.close()

    write_pota_park_grids_sqlite(GRIDS, db)

    con = sqlite3.connect(db)
    try:
        # Both the callsign table AND the parks table are untouched (additive contract).
        assert con.execute("SELECT callsign FROM current").fetchone() == ("W1AW",)
        assert con.execute("SELECT name FROM pota_parks WHERE reference='US-4567'").fetchone() == (
            "Boise National Forest",
        )
        assert con.execute("SELECT COUNT(*) FROM pota_park_grids").fetchone()[0] == 3
    finally:
        con.close()
