"""Regression tests for read_prior schema-drift tolerance (hdb-relfix).

BUG: the weekly Release workflow downloads the PREVIOUS release's ``.db`` as
``prior_db`` and feeds it to ``read_prior``. Once the ``current`` table gained
columns after the 2026-06-18 release (grant_date/effective_date/expired_date/frn/
entity_type/applicant_type/previous_callsign from hdb-f865, uses_lotw/
lotw_last_activity from hdb-fccf), ``read_prior`` crashed with
``IndexError: No item with that key`` on every subsequent scheduled run, because it
did ``row[c]`` for every column ``sqlite_writer`` currently tracks, including ones
absent from the older file on disk.

These tests pin the fix in both directions:
  * a prior file MISSING a column this module tracks reads fine, with that field
    defaulted to its dataclass "unset" value (not a spurious value), and the id
    ledger's identity check does not treat the missing column as a change;
  * a prior file with an EXTRA column this module no longer tracks reads fine too.
"""

from __future__ import annotations

import sqlite3

from hamcall_db.history import HistoryRow, _identity
from hamcall_db.models import Record
from hamcall_db.sqlite_writer import assign_ids, read_prior, write_sqlite

DAY1 = "2026-06-01"


def test_read_prior_tolerates_missing_newer_column(tmp_path) -> None:
    """A prior .db written before a scalar column existed must not crash read_prior,
    and the missing column must default to the value a fresh Record carries for
    "unset" (None here) rather than some other value that would look like a change.
    """
    db = tmp_path / "prior.db"
    write_sqlite(
        [
            Record(
                callsign="W1AW",
                last_name="Maxim",
                source="fcc",
                frn="0001234567",
                applicant_type="Individual",
            )
        ],
        [],
        db,
    )

    # Simulate the older schema by dropping a column that was added after the last
    # good prior (hdb-f865's frn), the way the real 2026-06-18 prior lacked it.
    con = sqlite3.connect(db)
    con.execute("ALTER TABLE current DROP COLUMN frn")
    con.commit()
    con.close()

    prior_current, prior_history = read_prior(db)

    assert "W1AW" in prior_current
    pid, prec = prior_current["W1AW"]
    assert isinstance(pid, int)
    assert prec.callsign == "W1AW"
    assert prec.last_name == "Maxim"
    # The dropped column reads as the Record default for "unset", not an error.
    assert prec.frn is None
    # A column untouched by the drop still round-trips normally.
    assert prec.applicant_type == "Individual"


def test_read_prior_missing_column_is_not_a_spurious_identity_change(tmp_path) -> None:
    """A column absent from the prior file (because it didn't exist yet) must not
    make assign_ids treat an unchanged holder as reassigned: _identity only tracks
    holder/location fields, and reading a schema-newer column as its default must
    not perturb that tuple for an otherwise-unchanged record.
    """
    db = tmp_path / "prior.db"
    write_sqlite(
        [Record(callsign="W1AW", last_name="Maxim", city="Newington", source="fcc")],
        [],
        db,
    )
    con = sqlite3.connect(db)
    con.execute("ALTER TABLE current DROP COLUMN frn")
    con.commit()
    con.close()

    prior_current, prior_history = read_prior(db)
    pid, prior_rec = prior_current["W1AW"]

    # Same holder/location fields as the prior; only the field that couldn't have
    # existed in the prior file (frn) differs, because it is populated on this build.
    current_rec = Record(
        callsign="W1AW", last_name="Maxim", city="Newington", source="fcc", frn="0001234567"
    )
    assert _identity(prior_rec) == _identity(current_rec)

    assigned = assign_ids([current_rec], prior_current=prior_current, prior_history=prior_history)
    # Same holder identity -> the prior id is reused, not a fresh (reassigned) id.
    assert assigned == [(pid, current_rec)]


def test_read_prior_tolerates_missing_bool_column(tmp_path) -> None:
    """uses_lotw (a 0/1 INTEGER column, added by hdb-fccf) missing from an older
    prior must default to False -- the Record default for "unset" -- not None and
    not a crash.
    """
    db = tmp_path / "prior.db"
    write_sqlite([Record(callsign="W1AW", last_name="Maxim", uses_lotw=True)], [], db)

    con = sqlite3.connect(db)
    con.execute("ALTER TABLE current DROP COLUMN uses_lotw")
    con.commit()
    con.close()

    prior_current, _ = read_prior(db)
    _, prec = prior_current["W1AW"]
    assert prec.uses_lotw is False


def test_read_prior_tolerates_extra_unknown_column(tmp_path) -> None:
    """The reverse direction: a prior .db with a column this module no longer
    tracks must be read fine, ignoring the unknown column rather than failing.
    """
    db = tmp_path / "prior.db"
    write_sqlite([Record(callsign="W1AW", last_name="Maxim", source="fcc")], [], db)

    con = sqlite3.connect(db)
    con.execute("ALTER TABLE current ADD COLUMN retired_future_column TEXT")
    con.execute("UPDATE current SET retired_future_column = 'whatever' WHERE callsign = 'W1AW'")
    con.commit()
    con.close()

    prior_current, prior_history = read_prior(db)
    assert "W1AW" in prior_current
    pid, prec = prior_current["W1AW"]
    assert isinstance(pid, int)
    assert prec.callsign == "W1AW"
    assert prec.last_name == "Maxim"


def test_read_prior_tolerates_missing_and_extra_history_columns(tmp_path) -> None:
    """The history table gets the same schema-drift tolerance as current."""
    db = tmp_path / "prior.db"
    write_sqlite(
        [Record(callsign="W1AW", last_name="Maxim")],
        [HistoryRow(callsign="W1AW", valid_from=DAY1, valid_to=None, last_name="Maxim")],
        db,
    )

    con = sqlite3.connect(db)
    con.execute("ALTER TABLE history DROP COLUMN license_class")
    con.execute("ALTER TABLE history ADD COLUMN retired_future_column TEXT")
    con.commit()
    con.close()

    _, prior_history = read_prior(db)
    assert len(prior_history) == 1
    hid, hist = prior_history[0]
    assert isinstance(hid, int)
    assert hist.callsign == "W1AW"
    assert hist.last_name == "Maxim"
    assert hist.license_class is None
