"""Tests for the SCD2 history artifact (au-80c0).

History is FORWARD-ONLY: it accrues by diffing each weekly current-state build against
the prior build's history. These tests use synthetic before/after snapshots — no real
importer, no network. The current-state contract (Record / write_parquet) is unaffected.
"""

from __future__ import annotations

import polars as pl

from hamcall_db.history import (
    HISTORY_SCHEMA_COLUMNS,
    HistoryRow,
    diff_history,
)
from hamcall_db.models import Record

DAY1 = "2026-06-01"
DAY2 = "2026-06-08"
DAY3 = "2026-06-15"


def _open(callsign: str, valid_from: str, **fields) -> HistoryRow:
    return HistoryRow(callsign=callsign, valid_from=valid_from, valid_to=None, **fields)


# --- bootstrap: no prior history -----------------------------------------------


def test_no_prior_history_makes_every_row_an_open_interval() -> None:
    current = [
        Record(callsign="W1AW", last_name="Maxim", source="fcc"),
        Record(callsign="K1ABC", last_name="Smith", source="fcc"),
    ]
    out = diff_history([], current, as_of=DAY1)
    assert len(out) == 2
    assert all(r.valid_to is None for r in out)
    assert all(r.valid_from == DAY1 for r in out)
    assert {r.callsign for r in out} == {"W1AW", "K1ABC"}


# --- unchanged -----------------------------------------------------------------


def test_unchanged_callsign_carries_open_interval_forward() -> None:
    prior = [_open("W1AW", DAY1, last_name="Maxim", source="fcc")]
    current = [Record(callsign="W1AW", last_name="Maxim", source="fcc")]
    out = diff_history(prior, current, as_of=DAY2)
    assert len(out) == 1
    assert out[0].valid_from == DAY1  # interval NOT reopened
    assert out[0].valid_to is None


def test_source_only_difference_is_not_a_change() -> None:
    prior = [_open("W1AW", DAY1, last_name="Maxim", source="fcc")]
    # Same holder identity, different source tag only -> no new interval.
    current = [Record(callsign="W1AW", last_name="Maxim", source="ised")]
    out = diff_history(prior, current, as_of=DAY2)
    assert len(out) == 1
    assert out[0].valid_from == DAY1
    assert out[0].valid_to is None


# --- changed -------------------------------------------------------------------


def test_name_change_closes_old_and_opens_new() -> None:
    prior = [_open("W1AW", DAY1, first_name="Bob", state="TX", source="fcc")]
    current = [Record(callsign="W1AW", first_name="Alan", state="WA", source="fcc")]
    out = diff_history(prior, current, as_of=DAY2)
    out = sorted(out, key=lambda r: (r.valid_from, r.valid_to or "~"))
    assert len(out) == 2
    closed, opened = out[0], out[1]
    assert closed.first_name == "Bob"
    assert closed.valid_from == DAY1 and closed.valid_to == DAY2
    assert opened.first_name == "Alan"
    assert opened.valid_from == DAY2 and opened.valid_to is None


def test_address_only_move_opens_new_interval() -> None:
    # Decision: same person, new location => new interval (QSO-time attribution cares
    # about where the station was, not only who).
    prior = [_open("W1AW", DAY1, last_name="Maxim", city="Hartford", grid="FN31", source="fcc")]
    current = [
        Record(callsign="W1AW", last_name="Maxim", city="Seattle", grid="CN87", source="fcc")
    ]
    out = diff_history(prior, current, as_of=DAY2)
    assert len(out) == 2
    closed = next(r for r in out if r.valid_to is not None)
    opened = next(r for r in out if r.valid_to is None)
    assert closed.city == "Hartford" and closed.valid_to == DAY2
    assert opened.city == "Seattle" and opened.valid_from == DAY2


# --- new / disappeared ----------------------------------------------------------


def test_brand_new_callsign_gets_open_interval() -> None:
    prior = [_open("W1AW", DAY1, last_name="Maxim", source="fcc")]
    current = [
        Record(callsign="W1AW", last_name="Maxim", source="fcc"),
        Record(callsign="N0NEW", last_name="Fresh", source="fcc"),
    ]
    out = diff_history(prior, current, as_of=DAY2)
    new = next(r for r in out if r.callsign == "N0NEW")
    assert new.valid_from == DAY2 and new.valid_to is None


def test_disappeared_callsign_is_closed() -> None:
    prior = [_open("W1AW", DAY1, last_name="Maxim", source="fcc")]
    current: list[Record] = []  # callsign expired / cancelled
    out = diff_history(prior, current, as_of=DAY2)
    assert len(out) == 1
    assert out[0].valid_to == DAY2  # closed, not deleted


def test_already_closed_intervals_carry_forward_untouched() -> None:
    closed = HistoryRow(
        callsign="W1AW", valid_from=DAY1, valid_to=DAY2, first_name="Bob", source="fcc"
    )
    open_iv = _open("W1AW", DAY2, first_name="Alan", source="fcc")
    prior = [closed, open_iv]
    current = [Record(callsign="W1AW", first_name="Alan", source="fcc")]
    out = diff_history(prior, current, as_of=DAY3)
    # The historical closed interval is preserved exactly; open interval stays open.
    assert closed in out
    still_open = next(r for r in out if r.valid_to is None)
    assert still_open.valid_from == DAY2


# --- determinism ---------------------------------------------------------------


def test_output_sorted_by_callsign_then_valid_from() -> None:
    prior = [
        _open("W1AW", DAY1, first_name="Bob", source="fcc"),
        _open("A1AA", DAY1, first_name="Ann", source="fcc"),
    ]
    current = [
        Record(callsign="W1AW", first_name="Bob2", source="fcc"),
        Record(callsign="A1AA", first_name="Ann", source="fcc"),
    ]
    out = diff_history(prior, current, as_of=DAY2)
    keys = [(r.callsign, r.valid_from) for r in out]
    assert keys == sorted(keys)


# --- writer --------------------------------------------------------------------


def test_history_writer_roundtrip(tmp_path) -> None:
    from hamcall_db.writer import write_history_parquet

    rows = [
        HistoryRow(callsign="A1AA", valid_from=DAY1, valid_to=DAY2, first_name="Ann", source="fcc"),
        HistoryRow(callsign="W1AW", valid_from=DAY1, valid_to=None, first_name="Bob", dxcc=291),
    ]
    out = tmp_path / "history.parquet"
    count = write_history_parquet(rows, out)
    assert count == 2
    frame = pl.read_parquet(out)
    assert tuple(frame.columns) == HISTORY_SCHEMA_COLUMNS
    assert frame["callsign"].to_list() == ["A1AA", "W1AW"]
    assert frame["dxcc"].dtype == pl.Int64
    # Open interval serializes valid_to as null.
    assert frame.filter(pl.col("callsign") == "W1AW")["valid_to"].to_list() == [None]


def test_history_parquet_read_roundtrip_feeds_next_diff(tmp_path) -> None:
    # A history file written this week must read back and serve as prior state next week.
    from hamcall_db.writer import read_history_parquet, write_history_parquet

    week1 = diff_history([], [Record(callsign="W1AW", first_name="Bob", source="fcc")], as_of=DAY1)
    path = tmp_path / "h.parquet"
    write_history_parquet(week1, path)
    prior = read_history_parquet(path)
    assert prior == week1  # exact roundtrip

    week2 = diff_history(
        prior, [Record(callsign="W1AW", first_name="Alan", source="fcc")], as_of=DAY2
    )
    assert len(week2) == 2  # Bob closed, Alan open
    assert {r.valid_to for r in week2} == {DAY2, None}


# --- allstar_nodes is NOT holder identity (hdb-8803) ---------------------------


def test_allstar_node_change_does_not_open_new_interval() -> None:
    # Same holder/location, only the AllStarLink node list changed. Node churn is NOT
    # identity, so the open interval must carry forward unchanged (no new interval).
    prior = diff_history(
        [],
        [Record(callsign="WB6NIL", last_name="Smith", allstar_nodes=[2000], source="fcc")],
        as_of=DAY1,
    )
    out = diff_history(
        prior,
        [
            Record(
                callsign="WB6NIL",
                last_name="Smith",
                allstar_nodes=[2000, 2001, 2002],
                source="fcc",
            )
        ],
        as_of=DAY2,
    )
    assert len(out) == 1  # no new interval spawned
    assert out[0].valid_from == DAY1
    assert out[0].valid_to is None
    # HistoryRow carries no allstar_nodes attribute (kept off the history schema).
    assert not hasattr(out[0], "allstar_nodes")
    assert "allstar_nodes" not in HISTORY_SCHEMA_COLUMNS
