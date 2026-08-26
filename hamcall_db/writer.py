"""Parquet writer: the single place that serializes `Record`s to the published artifact.

All I/O lives in importers and here. Uses polars so the upstream merge/normalize stages
can stay pure-Python iterables of `Record` and only materialize a frame at the boundary.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path

import polars as pl

from hamcall_db.history import HISTORY_SCHEMA_COLUMNS, HistoryRow
from hamcall_db.models import SCHEMA_COLUMNS, Record
from hamcall_db.reflectors import REFLECTOR_SCHEMA_COLUMNS, ReflectorRecord
from hamcall_db.sources.padus_grids import PARK_GRID_SCHEMA_COLUMNS, ParkGridRecord
from hamcall_db.sources.pota import PARK_SCHEMA_COLUMNS, ParkRecord
from hamcall_db.sources.sota import SUMMIT_SCHEMA_COLUMNS, SummitRecord


# Explicit schema keeps column order and dtypes stable even when a batch is all-null
# for some column (polars would otherwise infer Null dtype). String for everything
# except the integer DXCC entity number and the AllStarLink node list.
def _current_dtype(col: str) -> pl.DataType:
    if col == "dxcc":
        return pl.Int64
    if col == "allstar_nodes":
        return pl.List(pl.Int64)  # one callsign -> MANY nodes (hdb-8803)
    if col == "uses_lotw":
        return pl.Boolean  # LoTW user flag (hdb-fccf); lotw_last_activity stays Utf8 date
    return pl.Utf8


_SCHEMA: dict[str, pl.DataType] = {col: _current_dtype(col) for col in SCHEMA_COLUMNS}

# History artifact schema: same string/Int64 rules; the interval bounds are ISO date
# strings (NULL valid_to = open interval).
_HISTORY_SCHEMA: dict[str, pl.DataType] = {
    col: (pl.Int64 if col == "dxcc" else pl.Utf8) for col in HISTORY_SCHEMA_COLUMNS
}


def write_parquet(records: Iterable[Record], out_path: Path) -> int:
    """Write `records` to `out_path` as Parquet. Returns the row count written."""
    rows = [asdict(r) for r in records]
    frame = pl.DataFrame(rows, schema=_SCHEMA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(out_path)
    return frame.height


def write_history_parquet(rows: Iterable[HistoryRow], out_path: Path) -> int:
    """Write SCD2 history `rows` to `out_path` as Parquet. Returns the row count written.

    Separate from `write_parquet` so the current-state artifact's columns can never drift:
    the two files have different schemas on purpose (mem-4784).
    """
    records = [asdict(r) for r in rows]
    frame = pl.DataFrame(records, schema=_HISTORY_SCHEMA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(out_path)
    return frame.height


# POTA parks artifact schema (hdb-9640): a SEPARATE dataset from the callsign contract.
# ``dxcc`` is Int64, ``active`` is Boolean, ``lat``/``lon`` are Float64 (verbatim,
# indicative-only coords); everything else is string. Grid is stored VERBATIM (no person-
# grid 4-char truncation — parks are public landmarks).
def _park_dtype(col: str) -> pl.DataType:
    if col == "dxcc":
        return pl.Int64
    if col in ("lat", "lon"):
        return pl.Float64
    if col == "active":
        return pl.Boolean
    return pl.Utf8


_PARK_SCHEMA: dict[str, pl.DataType] = {
    col: _park_dtype(col) for col in PARK_SCHEMA_COLUMNS
}


def write_pota_parks_parquet(parks: Iterable[ParkRecord], out_path: Path) -> int:
    """Write POTA `ParkRecord`s to `out_path` as Parquet. Returns the row count written.

    Additive artifact (hamcall-db-pota-parks-YYYY-MM-DD.parquet); separate schema from the
    callsign current/history files so neither can drift (the redistribution contract).
    """
    rows = [asdict(p) for p in parks]
    frame = pl.DataFrame(rows, schema=_PARK_SCHEMA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(out_path)
    return frame.height


# POTA park-grids child table (hdb-f53c): all four columns are strings (reference FK,
# 4-char grid, source tag, confidence flag). Additive to the MAIN CC BY-NC artifact;
# separate file so neither the callsign nor the parks schema can drift.
_PARK_GRID_SCHEMA: dict[str, pl.DataType] = {
    col: pl.Utf8 for col in PARK_GRID_SCHEMA_COLUMNS
}


def write_pota_park_grids_parquet(
    grids: Iterable[ParkGridRecord], out_path: Path
) -> int:
    """Write POTA `ParkGridRecord`s to `out_path` as Parquet. Returns the row count.

    Additive artifact (hamcall-db-pota-park-grids-YYYY-MM-DD.parquet): one row per
    (park reference, 4-char grid). Separate schema from the callsign and parks files so
    none can drift (the redistribution contract).
    """
    rows = [asdict(g) for g in grids]
    frame = pl.DataFrame(rows, schema=_PARK_GRID_SCHEMA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(out_path)
    return frame.height


# OSM/international park-grids artifact (hdb-438b, PHASE 2). SAME four-string schema as the
# PAD-US set, but written to a SEPARATE ODbL-licensed file —
# hamcall-db-pota-park-grids-osm-YYYY-MM-DD.parquet — NEVER mixed into the CC BY-NC artifact.
# OSM-derived grids form an ODbL derivative database; share-alike is incompatible with the
# main artifact's CC BY-NC terms, so they ship segregated (source='osm'/'osm-point').
def write_pota_park_grids_osm_parquet(
    grids: Iterable[ParkGridRecord], out_path: Path
) -> int:
    """Write OSM-derived `ParkGridRecord`s to `out_path` as Parquet. Returns the row count.

    SEPARATE ODbL artifact (hamcall-db-pota-park-grids-osm-YYYY-MM-DD.parquet): one row per
    (non-US park reference, 4-char grid) intersected from OpenStreetMap boundaries. License
    segregation: this is an ODbL derivative database and is NEVER written into the CC BY-NC
    callsign/parks/PAD-US files. (c) OpenStreetMap contributors, ODbL.
    """
    rows = [asdict(g) for g in grids]
    frame = pl.DataFrame(rows, schema=_PARK_GRID_SCHEMA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(out_path)
    return frame.height


# SOTA summits artifact schema (hdb-ca00): a SEPARATE sibling dataset (summits are public
# landmarks, not licensees), mirroring the POTA parks shape. ``alt_m``/``alt_ft``/``points``/
# ``bonus_points`` are Int64, ``lat``/``lon`` are Float64 (verbatim, indicative-only coords),
# ``active`` is Boolean; everything else is string. Grid is stored VERBATIM at 6-char (no
# person-grid 4-char truncation — summits are public landmarks).
def _summit_dtype(col: str) -> pl.DataType:
    if col in ("alt_m", "alt_ft", "points", "bonus_points"):
        return pl.Int64
    if col in ("lat", "lon"):
        return pl.Float64
    if col == "active":
        return pl.Boolean
    return pl.Utf8


_SUMMIT_SCHEMA: dict[str, pl.DataType] = {
    col: _summit_dtype(col) for col in SUMMIT_SCHEMA_COLUMNS
}


def write_sota_summits_parquet(summits: Iterable[SummitRecord], out_path: Path) -> int:
    """Write SOTA `SummitRecord`s to `out_path` as Parquet. Returns the row count written.

    Additive artifact (hamcall-db-sota-summits-YYYY-MM-DD.parquet); separate schema from the
    callsign current/history files (and the POTA parks file) so none can drift (the
    redistribution contract).
    """
    rows = [asdict(s) for s in summits]
    frame = pl.DataFrame(rows, schema=_SUMMIT_SCHEMA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(out_path)
    return frame.height


def read_history_parquet(in_path: Path) -> list[HistoryRow]:
    """Read a prior history artifact back into `HistoryRow`s (the diff's prior state)."""
    frame = pl.read_parquet(in_path)
    return [HistoryRow(**row) for row in frame.to_dicts()]


# Reflector directory artifact (hdb-refl): a SEPARATE reference dataset — reflectors are
# places, not licensees — written to its OWN file under a DIFFERENT licence. DVRef's data
# is CC BY 4.0, and CC BY 4.0 s2(a)(5)(B) forbids adding restrictions the licence grants
# away, so folding these rows into the CC BY-NC artifacts would be a licence violation,
# not merely untidy. Same segregation discipline as the ODbL park grids, opposite
# direction: there the upstream was MORE restrictive, here it is LESS.
# `modules` is a string list ('A'..'Z'); `port` is Int64; everything else is a string.
def _reflector_dtype(col: str) -> pl.DataType:
    if col == "port":
        return pl.Int64
    if col == "modules":
        return pl.List(pl.Utf8)
    return pl.Utf8


_REFLECTOR_SCHEMA: dict[str, pl.DataType] = {
    col: _reflector_dtype(col) for col in REFLECTOR_SCHEMA_COLUMNS
}


def write_reflectors_parquet(reflectors: Iterable[ReflectorRecord], out_path: Path) -> int:
    """Write `ReflectorRecord`s to `out_path` as Parquet. Returns the row count.

    SEPARATE CC BY 4.0 artifact (hamcall-db-reflectors-YYYY-MM-DD.parquet): one row per
    (network, id) reflector across D-Star, M17, YSF, NXDN, P25 and URF. NEVER written into
    the CC BY-NC callsign/parks files — see the module comment above. Attribution travels
    with the artifact in NOTICE and the release body.
    """
    rows = [asdict(r) for r in reflectors]
    frame = pl.DataFrame(rows, schema=_REFLECTOR_SCHEMA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(out_path)
    return frame.height
