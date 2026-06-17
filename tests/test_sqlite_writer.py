"""Tests for the optional SQLite output artifact (au-d824).

The SQLite file is an ADDITIONAL convenience artifact (Datasette-serveable) holding
current-state AND forward-only history in ONE file. The Parquet contract is untouched.

CORE CONTRACT under test: the surrogate ``id`` is STABLE and NEVER REUSED across weekly
rebuilds. The same holder keeps its id; a reassigned callsign (new holder) gets a fresh
id and the old id is retired forever (even after the old holder leaves ``current``).

All synthetic data, temp files (tmp_path / :memory:), no network.
"""

from __future__ import annotations

import sqlite3

import pytest

from hamcall_db.history import HistoryRow
from hamcall_db.models import Record
from hamcall_db.sqlite_writer import (
    _SCALAR_COLUMNS,
    assign_ids,
    read_prior,
    write_sqlite,
)

DAY1 = "2026-06-01"
DAY2 = "2026-06-08"


def _open(callsign: str, valid_from: str, **fields) -> HistoryRow:
    return HistoryRow(callsign=callsign, valid_from=valid_from, valid_to=None, **fields)


# --- schema / structure --------------------------------------------------------


def test_tables_and_indexes_exist(tmp_path) -> None:
    db = tmp_path / "out.db"
    write_sqlite([Record(callsign="W1AW", last_name="Maxim", source="fcc")], [], db)
    con = sqlite3.connect(db)
    try:
        tables = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"current", "history"} <= tables
        indexes = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        # Non-unique callsign index on history must exist by name.
        assert any("history" in name and "callsign" in name for name in indexes)
    finally:
        con.close()


def test_current_id_is_integer_primary_key(tmp_path) -> None:
    db = tmp_path / "out.db"
    write_sqlite([Record(callsign="W1AW", source="fcc")], [], db)
    con = sqlite3.connect(db)
    try:
        cols = con.execute("PRAGMA table_info(current)").fetchall()
        # column tuple: (cid, name, type, notnull, dflt_value, pk)
        by_name = {c[1]: c for c in cols}
        assert by_name["id"][5] == 1  # id is the PK
        assert by_name["id"][2] == "INTEGER"
        # every scalar Record column is present on current
        for col in _SCALAR_COLUMNS:
            assert col in by_name
        # allstar_nodes is a list -> child table, NOT a scalar current column (hdb-8803)
        assert "allstar_nodes" not in by_name
    finally:
        con.close()


def test_current_callsign_is_unique(tmp_path) -> None:
    db = tmp_path / "out.db"
    write_sqlite([Record(callsign="W1AW", source="fcc")], [], db)
    con = sqlite3.connect(db)
    try:
        # A second row with the same callsign must be rejected by UNIQUE.
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO current (id, callsign) VALUES (?, ?)", (999, "W1AW")
            )
    finally:
        con.close()


def test_history_carries_id_column(tmp_path) -> None:
    db = tmp_path / "out.db"
    history = [_open("W1AW", DAY1, last_name="Maxim")]
    write_sqlite([Record(callsign="W1AW", last_name="Maxim", source="fcc")], history, db)
    con = sqlite3.connect(db)
    try:
        cols = {c[1] for c in con.execute("PRAGMA table_info(history)").fetchall()}
        assert "id" in cols
        assert {"callsign", "valid_from", "valid_to"} <= cols
        # the open history row gets the same id as the current row for that callsign
        cur_id = con.execute(
            "SELECT id FROM current WHERE callsign='W1AW'"
        ).fetchone()[0]
        hist_id = con.execute(
            "SELECT id FROM history WHERE callsign='W1AW'"
        ).fetchone()[0]
        assert hist_id == cur_id
    finally:
        con.close()


# --- round trip ----------------------------------------------------------------


def test_round_trip_values(tmp_path) -> None:
    db = tmp_path / "out.db"
    rec = Record(
        callsign="K1ABC",
        first_name="Jane",
        last_name="Smith",
        city="Newington",
        county="Hartford",
        state="CT",
        postal_code="06111",
        country="United States",
        dxcc=291,
        grid="FN31",
        license_class="Extra",
        source="fcc",
        synced_at=DAY1,
    )
    write_sqlite([rec], [], db)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT * FROM current WHERE callsign='K1ABC'").fetchone()
        for col in _SCALAR_COLUMNS:
            assert row[col] == getattr(rec, col)
        assert isinstance(row["id"], int)
    finally:
        con.close()


# --- id assignment (pure function) ---------------------------------------------


def test_first_build_allocates_fresh_ids() -> None:
    recs = [
        Record(callsign="W1AW", last_name="Maxim", source="fcc"),
        Record(callsign="K1ABC", last_name="Smith", source="fcc"),
    ]
    assigned = assign_ids(recs, prior_current={}, prior_history=[])
    ids = sorted(i for i, _ in assigned)
    assert ids == [1, 2]


def test_new_callsign_gets_fresh_id_above_high_water() -> None:
    prior_current = {"W1AW": (5, Record(callsign="W1AW", last_name="Maxim"))}
    prior_history = [(5, _open("W1AW", DAY1, last_name="Maxim"))]
    recs = [
        Record(callsign="W1AW", last_name="Maxim"),
        Record(callsign="K9NEW", last_name="Newman"),
    ]
    assigned = dict(
        (rec.callsign, i)
        for i, rec in assign_ids(
            recs, prior_current=prior_current, prior_history=prior_history
        )
    )
    assert assigned["W1AW"] == 5  # unchanged holder keeps id
    assert assigned["K9NEW"] == 6  # fresh id above high-water 5


def test_unchanged_holder_keeps_id_across_two_builds(tmp_path) -> None:
    db = tmp_path / "out.db"
    rec = Record(callsign="W1AW", last_name="Maxim", source="fcc")
    write_sqlite([rec], [_open("W1AW", DAY1, last_name="Maxim")], db)
    con = sqlite3.connect(db)
    first_id = con.execute("SELECT id FROM current WHERE callsign='W1AW'").fetchone()[0]
    con.close()

    # Second build, same holder, reading the prior db.
    db2 = tmp_path / "out2.db"
    write_sqlite(
        [rec], [_open("W1AW", DAY1, last_name="Maxim")], db2, prior_db=db
    )
    con = sqlite3.connect(db2)
    second_id = con.execute("SELECT id FROM current WHERE callsign='W1AW'").fetchone()[0]
    con.close()
    assert second_id == first_id


def test_reassigned_callsign_gets_new_id_and_old_id_retired(tmp_path) -> None:
    # Build 1: W1AW held by Maxim.
    db = tmp_path / "b1.db"
    write_sqlite(
        [Record(callsign="W1AW", last_name="Maxim", source="fcc")],
        [_open("W1AW", DAY1, last_name="Maxim")],
        db,
    )
    con = sqlite3.connect(db)
    old_id = con.execute("SELECT id FROM current WHERE callsign='W1AW'").fetchone()[0]
    con.close()

    # Build 2: W1AW reassigned to a DIFFERENT holder (identity changed).
    # History closes the old interval and opens a new one (SCD2). The new holder must
    # get a NEW id, and the old id must never be reissued.
    db2 = tmp_path / "b2.db"
    new_history = [
        HistoryRow(
            callsign="W1AW",
            valid_from=DAY1,
            valid_to=DAY2,
            last_name="Maxim",
        ),
        _open("W1AW", DAY2, last_name="Newholder"),
    ]
    write_sqlite(
        [Record(callsign="W1AW", last_name="Newholder", source="fcc")],
        new_history,
        db2,
        prior_db=db,
    )
    con = sqlite3.connect(db2)
    new_id = con.execute("SELECT id FROM current WHERE callsign='W1AW'").fetchone()[0]
    con.close()
    assert new_id != old_id
    assert new_id > old_id  # allocated above the high-water mark


def test_high_water_uses_history_after_callsign_leaves_current(tmp_path) -> None:
    # Build 1: two callsigns.
    db = tmp_path / "b1.db"
    write_sqlite(
        [
            Record(callsign="W1AW", last_name="Maxim"),
            Record(callsign="K1OLD", last_name="Gone"),
        ],
        [
            _open("W1AW", DAY1, last_name="Maxim"),
            _open("K1OLD", DAY1, last_name="Gone"),
        ],
        db,
    )
    con = sqlite3.connect(db)
    ids = {
        r[0]: r[1]
        for r in con.execute("SELECT callsign, id FROM current").fetchall()
    }
    con.close()
    high = max(ids.values())  # whichever got id 2

    # Build 2: K1OLD expired -> absent from current, but its closed interval remains in
    # history. A brand-new callsign must still get an id ABOVE the retired high-water.
    db2 = tmp_path / "b2.db"
    history2 = [
        _open("W1AW", DAY1, last_name="Maxim"),
        HistoryRow(callsign="K1OLD", valid_from=DAY1, valid_to=DAY2, last_name="Gone"),
        _open("N1NEW", DAY2, last_name="Fresh"),
    ]
    write_sqlite(
        [
            Record(callsign="W1AW", last_name="Maxim"),
            Record(callsign="N1NEW", last_name="Fresh"),
        ],
        history2,
        db2,
        prior_db=db,
    )
    con = sqlite3.connect(db2)
    new_id = con.execute("SELECT id FROM current WHERE callsign='N1NEW'").fetchone()[0]
    con.close()
    # Even though K1OLD is gone from current, its id survives via history, so N1NEW must
    # not reuse it.
    assert new_id > high


# --- closed-interval id retention (au-6934) ------------------------------------


def test_closed_interval_for_absent_callsign_keeps_original_id(tmp_path) -> None:
    # Build 1: K1OLD is current and held by Gone; its open interval gets an id.
    db = tmp_path / "b1.db"
    write_sqlite(
        [
            Record(callsign="W1AW", last_name="Maxim"),
            Record(callsign="K1OLD", last_name="Gone"),
        ],
        [
            _open("W1AW", DAY1, last_name="Maxim"),
            _open("K1OLD", DAY1, last_name="Gone"),
        ],
        db,
    )
    con = sqlite3.connect(db)
    old_id = con.execute(
        "SELECT id FROM history WHERE callsign='K1OLD' AND valid_from=?", (DAY1,)
    ).fetchone()[0]
    con.close()
    assert isinstance(old_id, int)

    # Build 2: K1OLD expired -> ABSENT from current. Its now-closed interval must retain
    # the original id (not become NULL), preserving the stable-id linkage.
    db2 = tmp_path / "b2.db"
    history2 = [
        _open("W1AW", DAY1, last_name="Maxim"),
        HistoryRow(callsign="K1OLD", valid_from=DAY1, valid_to=DAY2, last_name="Gone"),
    ]
    write_sqlite(
        [Record(callsign="W1AW", last_name="Maxim")],
        history2,
        db2,
        prior_db=db,
    )
    con = sqlite3.connect(db2)
    row = con.execute(
        "SELECT id FROM history WHERE callsign='K1OLD' AND valid_from=?", (DAY1,)
    ).fetchone()
    con.close()
    assert row[0] is not None
    assert row[0] == old_id


def test_open_interval_for_current_callsign_uses_current_id(tmp_path) -> None:
    # Build 1.
    db = tmp_path / "b1.db"
    write_sqlite(
        [Record(callsign="W1AW", last_name="Maxim")],
        [_open("W1AW", DAY1, last_name="Maxim")],
        db,
    )

    # Build 2: W1AW still current and open -> id resolves from the CURRENT build, equal to
    # the current row's id.
    db2 = tmp_path / "b2.db"
    write_sqlite(
        [Record(callsign="W1AW", last_name="Maxim")],
        [_open("W1AW", DAY1, last_name="Maxim")],
        db2,
        prior_db=db,
    )
    con = sqlite3.connect(db2)
    cur_id = con.execute("SELECT id FROM current WHERE callsign='W1AW'").fetchone()[0]
    hist_id = con.execute(
        "SELECT id FROM history WHERE callsign='W1AW' AND valid_to IS NULL"
    ).fetchone()[0]
    con.close()
    assert hist_id == cur_id


def test_reassigned_callsign_old_closed_interval_keeps_old_id(tmp_path) -> None:
    # Build 1: W1AW held by Maxim.
    db = tmp_path / "b1.db"
    write_sqlite(
        [Record(callsign="W1AW", last_name="Maxim")],
        [_open("W1AW", DAY1, last_name="Maxim")],
        db,
    )
    con = sqlite3.connect(db)
    old_id = con.execute("SELECT id FROM current WHERE callsign='W1AW'").fetchone()[0]
    con.close()

    # Build 2: W1AW reassigned to a new holder. The old (now-closed) interval is still
    # keyed by the SAME callsign that is in current, but it must keep the OLD id while the
    # new open interval gets the NEW id.
    db2 = tmp_path / "b2.db"
    history2 = [
        HistoryRow(
            callsign="W1AW", valid_from=DAY1, valid_to=DAY2, last_name="Maxim"
        ),
        _open("W1AW", DAY2, last_name="Newholder"),
    ]
    write_sqlite(
        [Record(callsign="W1AW", last_name="Newholder")],
        history2,
        db2,
        prior_db=db,
    )
    con = sqlite3.connect(db2)
    new_id = con.execute("SELECT id FROM current WHERE callsign='W1AW'").fetchone()[0]
    closed_id = con.execute(
        "SELECT id FROM history WHERE callsign='W1AW' AND valid_from=? AND valid_to=?",
        (DAY1, DAY2),
    ).fetchone()[0]
    open_id = con.execute(
        "SELECT id FROM history WHERE callsign='W1AW' AND valid_from=? AND valid_to IS NULL",
        (DAY2,),
    ).fetchone()[0]
    con.close()
    assert new_id != old_id
    assert closed_id == old_id  # old closed interval keeps its original id
    assert open_id == new_id  # new holding uses the new current id


def test_unknown_closed_interval_id_falls_back_to_null(tmp_path) -> None:
    # A closed interval that is neither in current nor in prior history (e.g. injected
    # history with no provenance) genuinely has no resolvable id -> NULL.
    db = tmp_path / "out.db"
    write_sqlite(
        [Record(callsign="W1AW", last_name="Maxim")],
        [
            _open("W1AW", DAY1, last_name="Maxim"),
            HistoryRow(
                callsign="X9ORPHAN", valid_from=DAY1, valid_to=DAY2, last_name="Ghost"
            ),
        ],
        db,
    )
    con = sqlite3.connect(db)
    orphan_id = con.execute(
        "SELECT id FROM history WHERE callsign='X9ORPHAN'"
    ).fetchone()[0]
    con.close()
    assert orphan_id is None


# --- read_prior ----------------------------------------------------------------


def test_read_prior_missing_file_is_empty(tmp_path) -> None:
    prior_current, prior_history = read_prior(tmp_path / "does-not-exist.db")
    assert prior_current == {}
    assert prior_history == []


def test_read_prior_round_trips_ids(tmp_path) -> None:
    db = tmp_path / "out.db"
    write_sqlite(
        [Record(callsign="W1AW", last_name="Maxim", source="fcc")],
        [_open("W1AW", DAY1, last_name="Maxim")],
        db,
    )
    prior_current, prior_history = read_prior(db)
    assert "W1AW" in prior_current
    pid, prec = prior_current["W1AW"]
    assert isinstance(pid, int)
    assert prec.callsign == "W1AW"
    assert prec.last_name == "Maxim"
    assert any(h.callsign == "W1AW" for _, h in prior_history)
    assert all(isinstance(hid, int) for hid, _ in prior_history)


# --- idempotency ---------------------------------------------------------------


def test_overwrite_safe(tmp_path) -> None:
    db = tmp_path / "out.db"
    write_sqlite([Record(callsign="W1AW", last_name="Maxim")], [], db)
    # Writing again to the same path must not raise (overwrite-safe) and must reflect the
    # new content.
    counts = write_sqlite([Record(callsign="K1ABC", last_name="Smith")], [], db)
    assert counts["current"] == 1
    con = sqlite3.connect(db)
    calls = {r[0] for r in con.execute("SELECT callsign FROM current").fetchall()}
    con.close()
    assert calls == {"K1ABC"}


# --- allstar_nodes child table (hdb-8803) --------------------------------------


def test_allstar_child_table_and_index_exist(tmp_path) -> None:
    db = tmp_path / "out.db"
    write_sqlite([Record(callsign="WB6NIL", allstar_nodes=[2000, 2001])], [], db)
    con = sqlite3.connect(db)
    try:
        tables = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "allstar_nodes" in tables
        cols = {c[1] for c in con.execute("PRAGMA table_info(allstar_nodes)").fetchall()}
        assert cols == {"id", "node"}
        indexes = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "idx_allstar_id" in indexes
    finally:
        con.close()


def test_allstar_child_rows_keyed_by_stable_id(tmp_path) -> None:
    db = tmp_path / "out.db"
    write_sqlite(
        [
            Record(callsign="WB6NIL", allstar_nodes=[2001, 2000]),
            Record(callsign="N0CALL", allstar_nodes=[51234]),
        ],
        [],
        db,
    )
    con = sqlite3.connect(db)
    try:
        wb_id = con.execute(
            "SELECT id FROM current WHERE callsign='WB6NIL'"
        ).fetchone()[0]
        nodes = [
            r[0]
            for r in con.execute(
                "SELECT node FROM allstar_nodes WHERE id=? ORDER BY node", (wb_id,)
            ).fetchall()
        ]
        assert nodes == [2000, 2001]
        # Two records -> three node rows total.
        total = con.execute("SELECT COUNT(*) FROM allstar_nodes").fetchone()[0]
        assert total == 3
    finally:
        con.close()


def test_callsign_with_no_nodes_has_no_child_rows(tmp_path) -> None:
    db = tmp_path / "out.db"
    write_sqlite([Record(callsign="W1AW")], [], db)
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM allstar_nodes").fetchone()[0] == 0
    finally:
        con.close()


def test_write_sqlite_returns_counts(tmp_path) -> None:
    db = tmp_path / "out.db"
    counts = write_sqlite(
        [
            Record(callsign="W1AW", last_name="Maxim"),
            Record(callsign="K1ABC", last_name="Smith"),
        ],
        [_open("W1AW", DAY1), _open("K1ABC", DAY1)],
        db,
    )
    assert counts == {"current": 2, "history": 2}
