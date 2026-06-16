"""Parquet writer: the single place that serializes `Record`s to the published artifact.

All I/O lives in importers and here. Uses polars so the upstream merge/normalize stages
can stay pure-Python iterables of `Record` and only materialize a frame at the boundary.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path

import polars as pl

from hamcall_db.models import SCHEMA_COLUMNS, Record

# Explicit schema keeps column order and dtypes stable even when a batch is all-null
# for some column (polars would otherwise infer Null dtype). String for everything
# except the integer DXCC entity number.
_SCHEMA: dict[str, pl.DataType] = {
    col: (pl.Int64 if col == "dxcc" else pl.Utf8) for col in SCHEMA_COLUMNS
}


def write_parquet(records: Iterable[Record], out_path: Path) -> int:
    """Write `records` to `out_path` as Parquet. Returns the row count written."""
    rows = [asdict(r) for r in records]
    frame = pl.DataFrame(rows, schema=_SCHEMA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(out_path)
    return frame.height
