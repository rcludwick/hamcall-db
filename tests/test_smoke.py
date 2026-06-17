"""Smoke tests for the bootstrap skeleton: imports resolve, CLI runs, writer roundtrips."""

from __future__ import annotations

import polars as pl
from typer.testing import CliRunner

from hamcall_db.build import SOURCES, app
from hamcall_db.models import SCHEMA_COLUMNS, Record
from hamcall_db.writer import write_parquet

runner = CliRunner()


def test_cli_help_runs() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


def test_cli_requires_source_selection() -> None:
    result = runner.invoke(app, ["--out", "x.parquet"])
    assert result.exit_code != 0


def test_fcc_source_registered() -> None:
    assert "fcc" in SOURCES
    assert SOURCES["fcc"].name == "fcc"


def test_writer_roundtrip(tmp_path) -> None:
    out = tmp_path / "out.parquet"
    records = [
        Record(callsign="N0CALL", last_name="Test", state="WA", source="fcc"),
        Record(callsign="W1AW", city="Newington", state="CT", dxcc=291, source="fcc"),
    ]
    count = write_parquet(records, out)

    assert count == 2
    frame = pl.read_parquet(out)
    assert frame.height == 2
    # Column order and full set match the schema contract exactly.
    assert tuple(frame.columns) == SCHEMA_COLUMNS
    assert frame["callsign"].to_list() == ["N0CALL", "W1AW"]
    assert frame["dxcc"].dtype == pl.Int64


def test_writer_roundtrips_allstar_node_list(tmp_path) -> None:
    # allstar_nodes is a list[int] column (one callsign -> MANY nodes); it must serialize
    # as pl.List(pl.Int64) and round-trip, including the empty-list default (hdb-8803).
    out = tmp_path / "out.parquet"
    records = [
        Record(callsign="WB6NIL", allstar_nodes=[2000, 2001, 2002], source="fcc"),
        Record(callsign="W1AW", source="fcc"),  # no nodes -> []
    ]
    write_parquet(records, out)
    frame = pl.read_parquet(out)
    assert tuple(frame.columns) == SCHEMA_COLUMNS  # contract intact
    assert frame["allstar_nodes"].dtype == pl.List(pl.Int64)
    assert frame["allstar_nodes"].to_list() == [[2000, 2001, 2002], []]
