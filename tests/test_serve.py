"""Tests for the Datasette serve launcher (hdb-7cc6) — db discovery logic."""

from __future__ import annotations

from hamcall_db.serve import latest_db


def test_latest_db_none_when_empty(tmp_path):
    assert latest_db(tmp_path) is None


def test_latest_db_picks_newest_by_dated_name(tmp_path):
    (tmp_path / "hamcall-db-2026-06-10.db").touch()
    (tmp_path / "hamcall-db-2026-06-17.db").touch()
    (tmp_path / "hamcall-db-2026-06-03.db").touch()
    assert latest_db(tmp_path).name == "hamcall-db-2026-06-17.db"


def test_latest_db_ignores_non_matching_files(tmp_path):
    (tmp_path / "hamcall-db-2026-06-17.db").touch()
    (tmp_path / "notes.txt").touch()
    (tmp_path / "hamcall-db-2026-06-17.parquet").touch()
    assert latest_db(tmp_path).name == "hamcall-db-2026-06-17.db"
