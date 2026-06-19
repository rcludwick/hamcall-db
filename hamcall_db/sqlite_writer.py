"""SQLite writer: an OPTIONAL convenience artifact (au-d824).

The published contract is, and stays, the two Parquet files (mem-c6e0, mem-4784). This
module emits an ADDITIONAL single ``.db`` file that bundles current-state AND forward-only
history in ONE file (Parquet cannot) and is directly serveable (e.g. Datasette). It is a
convenience, not the contract — the current-state Parquet is never touched here.

THE STABLE-ID CONTRACT (the core requirement)
---------------------------------------------
``current`` uses a SURROGATE primary key ``id`` that is STABLE and NEVER REUSED across
weekly rebuilds. Consumers may persist this id as a stable "original id" / foreign key, so:

  * the SAME holder of a callsign keeps the SAME id forever;
  * a REASSIGNED callsign (the holder changed) gets a brand-NEW id, and the old id is
    retired forever — it is never reissued to anyone.

"Same holder vs reassigned" reuses the history module's identity rule verbatim
(``hamcall_db.history._identity`` over ``_TRACKED_FIELDS``): a delta in the tracked
identity tuple = a new holding instance = a new id.

NEVER-REUSE MECHANISM: each build carries an id ledger forward. The high-water mark is the
max id ever seen across the prior ``current`` AND prior ``history`` tables. Drawing new ids
strictly above that mark guarantees non-reuse even for ids whose callsign has left
``current`` entirely (the id survives on the retired/closed history rows). First-ever build
starts the high-water at 0.

All I/O is confined to this module (the assignment algorithm itself is a pure function).
Uses stdlib ``sqlite3`` — no third-party dependency.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from hamcall_db.history import HISTORY_SCHEMA_COLUMNS, HistoryRow, _identity
from hamcall_db.models import SCHEMA_COLUMNS, Record
from hamcall_db.sources.padus_grids import PARK_GRID_SCHEMA_COLUMNS, ParkGridRecord
from hamcall_db.sources.pota import PARK_SCHEMA_COLUMNS, ParkRecord
from hamcall_db.sources.sota import SUMMIT_SCHEMA_COLUMNS, SummitRecord

# Columns whose SQLite affinity should be INTEGER rather than TEXT. ``dxcc`` mirrors the
# Parquet writer's Int64 treatment; ``id`` is the surrogate key.
_INT_COLUMNS: frozenset[str] = frozenset({"dxcc"})

# ``allstar_nodes`` is a list[int] (one callsign -> MANY nodes). SQLite has no list type,
# so it is EXCLUDED from the scalar ``current`` table and instead normalized into a child
# table keyed by the stable id (see _ALLSTAR_DDL below). hdb-8803.
_SCALAR_COLUMNS: tuple[str, ...] = tuple(
    c for c in SCHEMA_COLUMNS if c != "allstar_nodes"
)


def _col_def(name: str) -> str:
    return f"{name} INTEGER" if name in _INT_COLUMNS else f"{name} TEXT"


# current: surrogate id PK (NOT autoincrement — ids come from the carried-forward ledger),
# callsign UNIQUE NOT NULL (one current holder; the UNIQUE gives us the callsign index for
# free), plus every Record column.
_CURRENT_DDL = (
    "CREATE TABLE current (\n"
    "  id INTEGER PRIMARY KEY,\n"
    "  callsign TEXT UNIQUE NOT NULL,\n"
    + ",\n".join(
        f"  {_col_def(c)}" for c in _SCALAR_COLUMNS if c != "callsign"
    )
    + "\n)"
)

# allstar_nodes: child table normalizing the one-callsign -> MANY-nodes list out of the
# scalar ``current`` table, keyed by the stable id. Only CURRENT holders carry node
# associations (closed/retired history rows do not). Indexed on id for the join back to
# current. hdb-8803.
_ALLSTAR_DDL = (
    "CREATE TABLE allstar_nodes (\n  id INTEGER,\n  node INTEGER\n)"
)
_ALLSTAR_INDEX_DDL = "CREATE INDEX idx_allstar_id ON allstar_nodes (id)"

# history: the HistoryRow payload columns PLUS the stable id carried from current, so a
# holding instance is traceable across both tables and the id high-water survives even when
# a callsign leaves current. Non-unique callsign index added separately.
_HISTORY_DDL = (
    "CREATE TABLE history (\n"
    "  id INTEGER,\n"
    + ",\n".join(f"  {_col_def(c)}" for c in HISTORY_SCHEMA_COLUMNS)
    + "\n)"
)

_HISTORY_INDEX_DDL = "CREATE INDEX idx_history_callsign ON history (callsign)"

# Insert column orders (id first, then the scalar field order; allstar_nodes is a child
# table, not a current column).
_CURRENT_COLUMNS: tuple[str, ...] = ("id", *_SCALAR_COLUMNS)
_HISTORY_COLUMNS: tuple[str, ...] = ("id", *HISTORY_SCHEMA_COLUMNS)


def assign_ids(
    records: Iterable[Record],
    *,
    prior_current: dict[str, tuple[int, Record]],
    prior_history: Iterable[tuple[int | None, HistoryRow]],
) -> list[tuple[int, Record]]:
    """Assign a stable, never-reused surrogate id to each current record.

    ``prior_current`` maps callsign -> (id, Record) from the previous build's ``current``
    table. ``prior_history`` is the previous build's history rows as ``(id, HistoryRow)``
    pairs (used only to raise the high-water mark so retired ids are never reissued).

    Algorithm:
      * ``high_water`` = max id ever seen across ``prior_current`` AND ``prior_history``
        (0 if none). This is what guarantees non-reuse even for callsigns that have left
        ``current``.
      * For each current record: if its callsign existed in ``prior_current`` with the
        SAME holder identity (history's identity rule), REUSE the prior id. Otherwise
        allocate ``++high_water`` — covering brand-new callsigns AND reassignments.

    Pure function: no I/O, deterministic in input order.
    """
    prior_ids = [pid for pid, _ in prior_current.values()]
    prior_ids += [hid for hid, _ in prior_history if hid is not None]
    high_water = max(prior_ids) if prior_ids else 0

    assigned: list[tuple[int, Record]] = []
    for record in records:
        prior = prior_current.get(record.callsign)
        if prior is not None and _identity(prior[1]) == _identity(record):
            assigned.append((prior[0], record))
        else:
            high_water += 1
            assigned.append((high_water, record))
    return assigned


def read_prior(
    db_path: Path,
) -> tuple[dict[str, tuple[int, Record]], list[tuple[int | None, HistoryRow]]]:
    """Read the id ledger from an existing ``.db``.

    Returns ``(prior_current, prior_history)`` where ``prior_current`` maps callsign ->
    (id, Record) and ``prior_history`` is a list of ``(id, HistoryRow)`` pairs (the id is
    carried alongside, not on the slotted dataclass, so the high-water mark survives even
    after a callsign leaves ``current``). Returns empties when the file or tables are
    absent (the first-ever build path).
    """
    path = Path(db_path)
    if not path.exists():
        return {}, []

    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        tables = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        prior_current: dict[str, tuple[int, Record]] = {}
        if "current" in tables:
            # allstar_nodes is not stored on ``current`` (it's the child table); the
            # reconstructed Record's allstar_nodes stays [], which is irrelevant to the id
            # ledger (it's excluded from holder identity / _TRACKED_FIELDS). hdb-8803.
            for row in con.execute("SELECT * FROM current"):
                rec = Record(**{c: row[c] for c in _SCALAR_COLUMNS})
                prior_current[rec.callsign] = (row["id"], rec)

        prior_history: list[tuple[int | None, HistoryRow]] = []
        if "history" in tables:
            for row in con.execute("SELECT * FROM history"):
                hist = HistoryRow(**{c: row[c] for c in HISTORY_SCHEMA_COLUMNS})
                # Carry the stored id alongside the row (HistoryRow is slotted, so the id
                # can't live on it) so the high-water mark survives.
                prior_history.append((row["id"], hist))

        return prior_current, prior_history
    finally:
        con.close()


def write_sqlite(
    records: Iterable[Record],
    history: Iterable[HistoryRow],
    out_path: Path,
    *,
    prior_db: Path | None = None,
) -> dict[str, int]:
    """Write the ``current`` + ``history`` tables to a single SQLite file.

    Reads the prior id ledger from ``prior_db`` (if given), assigns stable/never-reused
    ids, (re)creates the schema, and writes both tables. Overwrite-safe for ``out_path``
    (an existing file at the path is replaced). Returns row counts keyed by table name.
    """
    records = list(records)
    history = list(history)

    if prior_db is not None:
        prior_current, prior_history = read_prior(prior_db)
    else:
        prior_current, prior_history = {}, []

    assigned = assign_ids(
        records, prior_current=prior_current, prior_history=prior_history
    )
    id_by_callsign = {rec.callsign: rid for rid, rec in assigned}
    # Ids of prior history intervals, keyed by their stable interval key (callsign,
    # valid_from). Lets a CLOSED interval whose callsign has left ``current`` (or been
    # reassigned) keep the id it was written with last build.
    prior_history_ids = {
        (hist.callsign, hist.valid_from): hid
        for hid, hist in prior_history
        if hid is not None
    }

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Overwrite-safe: drop any existing file so a stale schema can't linger.
    out.unlink(missing_ok=True)

    con = sqlite3.connect(out)
    try:
        con.execute(_CURRENT_DDL)
        con.execute(_HISTORY_DDL)
        con.execute(_HISTORY_INDEX_DDL)
        con.execute(_ALLSTAR_DDL)
        con.execute(_ALLSTAR_INDEX_DDL)

        current_placeholders = ", ".join("?" for _ in _CURRENT_COLUMNS)
        con.executemany(
            f"INSERT INTO current ({', '.join(_CURRENT_COLUMNS)}) "
            f"VALUES ({current_placeholders})",
            [(rid, *_current_payload(rec)) for rid, rec in assigned],
        )

        # Child table: one row per (id, node). Only current holders carry nodes.
        con.executemany(
            "INSERT INTO allstar_nodes (id, node) VALUES (?, ?)",
            [
                (rid, node)
                for rid, rec in assigned
                for node in rec.allstar_nodes
            ],
        )

        history_placeholders = ", ".join("?" for _ in _HISTORY_COLUMNS)
        con.executemany(
            f"INSERT INTO history ({', '.join(_HISTORY_COLUMNS)}) "
            f"VALUES ({history_placeholders})",
            [
                (_resolve_history_id(row, id_by_callsign, prior_history_ids),
                 *_history_payload(row))
                for row in history
            ],
        )

        con.commit()
    finally:
        con.close()

    return {"current": len(assigned), "history": len(history)}


def _resolve_history_id(
    row: HistoryRow,
    id_by_callsign: dict[str, int],
    prior_history_ids: dict[tuple[str, str], int],
) -> int | None:
    """Resolve the stable id for one history row being written.

    Resolution order (see au-6934):
      1. CLOSED interval (``valid_to is not None``): prefer the id of the matching prior
         history interval (keyed on callsign AND valid_from, the interval's stable key).
         This preserves the id for a fully-expired holder's closed interval AND for the
         old, now-closed interval of a REASSIGNED callsign (whose callsign is still in
         ``current`` but under a NEW holder/id).
      2. Otherwise fall back to the CURRENT build's id for the callsign — covering the
         OPEN interval of a callsign still in ``current`` (the currently-held holding,
         including a brand-new or reassigned holding) and any prior closed interval that
         was never recorded in history (so its id matches today's holder).
      3. NULL only when genuinely unknown: a closed interval whose callsign is absent
         from ``current`` AND has no matching prior-history interval (e.g. injected
         history with no provenance, or a first-ever build that is handed closed
         intervals with no prior ledger).
    """
    if row.valid_to is not None:
        prior_id = prior_history_ids.get((row.callsign, row.valid_from))
        if prior_id is not None:
            return prior_id
    return id_by_callsign.get(row.callsign)


def _current_payload(rec: Record) -> tuple:
    """The Record values in _SCALAR_COLUMNS order (id added separately; allstar_nodes is
    the child table, not a current column)."""
    return tuple(getattr(rec, name) for name in _SCALAR_COLUMNS)


def _history_payload(row: HistoryRow) -> tuple:
    """The HistoryRow values in HISTORY_SCHEMA_COLUMNS order (id is added separately)."""
    return tuple(getattr(row, name) for name in HISTORY_SCHEMA_COLUMNS)


# --- POTA parks table (hdb-9640) ----------------------------------------------
#
# A SEPARATE reference dataset (parks are places, not licensees). Written ADDITIVELY:
# the table is (re)built without touching the callsign current/history tables, so the
# parks dataset can ship into the SAME .db without breaking the contract. ``reference``
# (e.g. "US-0001") is the natural primary key. ``lat``/``lon`` get REAL affinity;
# ``active`` is stored as 0/1 INTEGER; ``dxcc`` INTEGER; everything else TEXT. Grid is
# stored VERBATIM (no person-grid 4-char truncation — parks are public landmarks).
_PARK_INT_COLUMNS: frozenset[str] = frozenset({"dxcc"})
_PARK_REAL_COLUMNS: frozenset[str] = frozenset({"lat", "lon"})


def _park_col_def(name: str) -> str:
    if name == "reference":
        return "reference TEXT PRIMARY KEY"
    if name == "active":
        return "active INTEGER"
    if name in _PARK_INT_COLUMNS:
        return f"{name} INTEGER"
    if name in _PARK_REAL_COLUMNS:
        return f"{name} REAL"
    return f"{name} TEXT"


_POTA_PARKS_DDL = (
    "CREATE TABLE IF NOT EXISTS pota_parks (\n"
    + ",\n".join(f"  {_park_col_def(c)}" for c in PARK_SCHEMA_COLUMNS)
    + "\n)"
)
_POTA_PARKS_REGION_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_pota_parks_region ON pota_parks (region)"
)


def _park_payload(park: ParkRecord) -> tuple:
    """ParkRecord values in PARK_SCHEMA_COLUMNS order; ``active`` bool -> 0/1 INTEGER."""
    values = []
    for name in PARK_SCHEMA_COLUMNS:
        value = getattr(park, name)
        if name == "active":
            value = int(bool(value))
        values.append(value)
    return tuple(values)


def write_pota_parks_sqlite(parks: Iterable[ParkRecord], out_path: Path) -> int:
    """Write/refresh the ``pota_parks`` table in a SQLite file. Returns the row count.

    ADDITIVE: creates the table if missing and replaces only the parks rows; any existing
    ``current``/``history``/``allstar_nodes`` tables in the same file are untouched (the
    parks dataset rides alongside the callsign artifact without breaking its contract).
    Idempotent — re-running replaces the parks rows.
    """
    parks = list(parks)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(out)
    try:
        con.execute(_POTA_PARKS_DDL)
        con.execute(_POTA_PARKS_REGION_INDEX_DDL)
        con.execute("DELETE FROM pota_parks")  # refresh: idempotent rebuild
        placeholders = ", ".join("?" for _ in PARK_SCHEMA_COLUMNS)
        con.executemany(
            f"INSERT INTO pota_parks ({', '.join(PARK_SCHEMA_COLUMNS)}) "
            f"VALUES ({placeholders})",
            [_park_payload(p) for p in parks],
        )
        con.commit()
    finally:
        con.close()

    return len(parks)


# --- SOTA summits table (hdb-ca00) ---------------------------------------------
#
# A SEPARATE additive sibling of pota_parks (summits are public landmarks, not licensees).
# ``reference`` (SOTA SummitCode) is the primary key; ``alt_m``/``alt_ft``/``points``/
# ``bonus_points`` are INTEGER; ``lat``/``lon`` REAL; ``active`` 0/1 INTEGER; the rest TEXT.
# Grid is stored VERBATIM at 6-char (no person-grid 4-char truncation — summits are public
# landmarks). Never touches the callsign current/history schema (the redistribution contract).
_SUMMIT_INT_COLUMNS: frozenset[str] = frozenset(
    {"alt_m", "alt_ft", "points", "bonus_points"}
)
_SUMMIT_REAL_COLUMNS: frozenset[str] = frozenset({"lat", "lon"})


def _summit_col_def(name: str) -> str:
    if name == "reference":
        return "reference TEXT PRIMARY KEY"
    if name == "active":
        return "active INTEGER"
    if name in _SUMMIT_INT_COLUMNS:
        return f"{name} INTEGER"
    if name in _SUMMIT_REAL_COLUMNS:
        return f"{name} REAL"
    return f"{name} TEXT"


_SOTA_SUMMITS_DDL = (
    "CREATE TABLE IF NOT EXISTS sota_summits (\n"
    + ",\n".join(f"  {_summit_col_def(c)}" for c in SUMMIT_SCHEMA_COLUMNS)
    + "\n)"
)
_SOTA_SUMMITS_ASSOC_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_sota_summits_association "
    "ON sota_summits (association)"
)


def _summit_payload(summit: SummitRecord) -> tuple:
    """SummitRecord values in SUMMIT_SCHEMA_COLUMNS order; ``active`` bool -> 0/1 INTEGER."""
    values = []
    for name in SUMMIT_SCHEMA_COLUMNS:
        value = getattr(summit, name)
        if name == "active":
            value = int(bool(value))
        values.append(value)
    return tuple(values)


def write_sota_summits_sqlite(summits: Iterable[SummitRecord], out_path: Path) -> int:
    """Write/refresh the ``sota_summits`` table in a SQLite file. Returns the row count.

    ADDITIVE: creates the table if missing and replaces only the summit rows; any existing
    ``current``/``history``/``allstar_nodes``/``pota_parks`` tables in the same file are
    untouched (the summits dataset rides alongside the callsign artifact without breaking
    its contract). Idempotent — re-running replaces the summit rows.
    """
    summits = list(summits)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(out)
    try:
        con.execute(_SOTA_SUMMITS_DDL)
        con.execute(_SOTA_SUMMITS_ASSOC_INDEX_DDL)
        con.execute("DELETE FROM sota_summits")  # refresh: idempotent rebuild
        placeholders = ", ".join("?" for _ in SUMMIT_SCHEMA_COLUMNS)
        con.executemany(
            f"INSERT INTO sota_summits ({', '.join(SUMMIT_SCHEMA_COLUMNS)}) "
            f"VALUES ({placeholders})",
            [_summit_payload(s) for s in summits],
        )
        con.commit()
    finally:
        con.close()

    return len(summits)


# --- POTA park-grids child table (hdb-f53c) ------------------------------------
#
# The pota_park_grids child table maps each POTA park (by ``reference``, the FK back to
# pota_parks) to the SET of 4-char Maidenhead grids its boundary intersects. Written
# ADDITIVELY alongside the callsign + parks tables; all four columns are TEXT. Indexed on
# ``reference`` for the join back to pota_parks. NOT a 1:1 table (a big park spans many
# grids), so ``reference`` is NOT a primary key here.
_POTA_PARK_GRIDS_DDL = (
    "CREATE TABLE IF NOT EXISTS pota_park_grids (\n"
    + ",\n".join(f"  {c} TEXT" for c in PARK_GRID_SCHEMA_COLUMNS)
    + "\n)"
)
_POTA_PARK_GRIDS_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_pota_park_grids_reference "
    "ON pota_park_grids (reference)"
)


def _park_grid_payload(grid: ParkGridRecord) -> tuple:
    """ParkGridRecord values in PARK_GRID_SCHEMA_COLUMNS order."""
    return tuple(getattr(grid, name) for name in PARK_GRID_SCHEMA_COLUMNS)


def write_pota_park_grids_sqlite(
    grids: Iterable[ParkGridRecord], out_path: Path
) -> int:
    """Write/refresh the ``pota_park_grids`` table in a SQLite file. Returns the row count.

    ADDITIVE: creates the table if missing and replaces only the grid rows; any existing
    ``current``/``history``/``allstar_nodes``/``pota_parks`` tables in the same file are
    untouched (the redistribution contract). Idempotent — re-running replaces the rows.
    """
    grids = list(grids)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(out)
    try:
        con.execute(_POTA_PARK_GRIDS_DDL)
        con.execute(_POTA_PARK_GRIDS_INDEX_DDL)
        con.execute("DELETE FROM pota_park_grids")  # refresh: idempotent rebuild
        placeholders = ", ".join("?" for _ in PARK_GRID_SCHEMA_COLUMNS)
        con.executemany(
            f"INSERT INTO pota_park_grids ({', '.join(PARK_GRID_SCHEMA_COLUMNS)}) "
            f"VALUES ({placeholders})",
            [_park_grid_payload(g) for g in grids],
        )
        con.commit()
    finally:
        con.close()

    return len(grids)


# --- OSM/international park-grids table (hdb-438b, PHASE 2) ---------------------
#
# LICENSE SEGREGATION: OSM-derived grids form an ODbL DERIVATIVE DATABASE, incompatible with
# the CC BY-NC artifact's share-alike-free terms. They live in their OWN SQLite file
# (hamcall-db-pota-park-grids-osm-YYYY-MM-DD.db) in a DEDICATED table, ``pota_park_grids_osm``
# — DELIBERATELY a different table name from the CC-BY-NC ``pota_park_grids`` so the two can
# never be confused or accidentally co-located. Same four-string schema as PAD-US (reference
# FK, 4-char grid, source ('osm'/'osm-point'), confidence). Indexed on ``reference``.
# (c) OpenStreetMap contributors, ODbL.
_POTA_PARK_GRIDS_OSM_DDL = (
    "CREATE TABLE IF NOT EXISTS pota_park_grids_osm (\n"
    + ",\n".join(f"  {c} TEXT" for c in PARK_GRID_SCHEMA_COLUMNS)
    + "\n)"
)
_POTA_PARK_GRIDS_OSM_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_pota_park_grids_osm_reference "
    "ON pota_park_grids_osm (reference)"
)


def write_pota_park_grids_osm_sqlite(
    grids: Iterable[ParkGridRecord], out_path: Path
) -> int:
    """Write/refresh the ``pota_park_grids_osm`` table in a SEPARATE ODbL SQLite file.

    Returns the row count. This writer targets the OSM artifact's OWN ``.db`` and creates
    ONLY the ``pota_park_grids_osm`` table — it NEVER creates or touches the CC-BY-NC tables
    (current/history/pota_parks/pota_park_grids). The OSM .db must not be the same file as
    the CC-BY-NC .db (the build wires them to different paths) so ODbL share-alike never
    taints the CC BY-NC artifact. Idempotent — re-running replaces the rows.
    """
    grids = list(grids)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(out)
    try:
        con.execute(_POTA_PARK_GRIDS_OSM_DDL)
        con.execute(_POTA_PARK_GRIDS_OSM_INDEX_DDL)
        con.execute("DELETE FROM pota_park_grids_osm")  # refresh: idempotent rebuild
        placeholders = ", ".join("?" for _ in PARK_GRID_SCHEMA_COLUMNS)
        con.executemany(
            f"INSERT INTO pota_park_grids_osm ({', '.join(PARK_GRID_SCHEMA_COLUMNS)}) "
            f"VALUES ({placeholders})",
            [_park_grid_payload(g) for g in grids],
        )
        con.commit()
    finally:
        con.close()

    return len(grids)
