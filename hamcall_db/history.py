"""SCD2 history stage: track callsign holder/location changes over successive builds.

The published history artifact is a SECOND, separate Parquet file
(``hamcall-db-history-YYYY-MM-DD.parquet``) — the current-state contract (mem-c6e0) is
NEVER touched. Consumers opt in by downloading it; downstream autocomplete is unaffected.

HARD CONSTRAINT — forward-only (mem-4784): upstream extracts are current-snapshot only,
so history cannot be reconstructed backward. It accrues by diffing each weekly current
set against the prior build's history. ``diff_history`` is a pure function over iterables
(no I/O); the writer materializes the frame at the boundary.

WHAT COUNTS AS A CHANGE: any delta in the tracked identity tuple (``_TRACKED_FIELDS`` —
holder name + location + class). An address/grid-only move by the same person therefore
opens a NEW interval, because QSO-time attribution cares about where AND who a station was
at a given time. ``source`` / ``synced_at`` are provenance, not identity, so they alone do
not trigger a new interval. Same no-street-address / 4-char-grid rules as the main
artifact (inherited from `Record`).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, fields, replace

from hamcall_db.models import Record

# Holder/location identity fields. A delta in ANY of these is a "change" that closes the
# open interval and opens a new one. Deliberately excludes callsign (the key), source and
# synced_at (provenance), and the interval bounds.
_TRACKED_FIELDS: tuple[str, ...] = (
    "first_name",
    "last_name",
    "city",
    "county",
    "state",
    "postal_code",
    "country",
    "dxcc",
    "grid",
    "license_class",
)

# Record fields NOT carried into a history row. ``allstar_nodes`` is excluded because the
# node list is not holder identity and keeps list support out of the history writers
# (hdb-8803). The FCC-specific date/identity fields added in hdb-f865 (grant/effective/
# expired dates, frn, entity_type, applicant_type, previous_callsign) are likewise NOT
# holder-location identity and are deliberately kept OFF HistoryRow: history tracks where
# AND who a station was, and the expired/status logic is a separate concern (hdb-6e95).
# The LoTW activity columns (uses_lotw, lotw_last_activity, hdb-fccf) are activity metadata,
# not holder identity, so a LoTW upload never opens a new SCD2 interval. Excluding all of
# these keeps the history artifact schema unchanged (additive bump touches current-state only).
_NON_PAYLOAD_FIELDS: frozenset[str] = frozenset(
    {
        "allstar_nodes",
        "grant_date",
        "effective_date",
        "expired_date",
        "frn",
        "entity_type",
        "applicant_type",
        "previous_callsign",
        "uses_lotw",
        "lotw_last_activity",
    }
)

# Record payload fields carried into a history row (everything except the interval
# bounds, which history adds, and the non-payload fields above).
_PAYLOAD_FIELDS: tuple[str, ...] = tuple(
    f.name for f in fields(Record) if f.name not in _NON_PAYLOAD_FIELDS
)


@dataclass(slots=True)
class HistoryRow:
    """One SCD2 interval: a callsign's holder/location state over [valid_from, valid_to).

    ``valid_to is None`` means the interval is open (the current/most-recent state).
    Carries the same payload columns as `Record` (minus nothing) plus the interval bounds.
    """

    callsign: str
    valid_from: str  # ISO date the state became effective in our records
    valid_to: str | None = None  # ISO date it was superseded; None = open/current
    first_name: str | None = None
    last_name: str | None = None
    city: str | None = None
    county: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    dxcc: int | None = None
    grid: str | None = None
    license_class: str | None = None
    source: str | None = None
    synced_at: str | None = None


HISTORY_SCHEMA_COLUMNS: tuple[str, ...] = tuple(f.name for f in fields(HistoryRow))
"""Ordered column names of the history artifact. The history writer relies on this order."""


def _identity(row: HistoryRow | Record) -> tuple:
    """The tracked-field tuple used to decide whether holder/location changed."""
    return tuple(getattr(row, name) for name in _TRACKED_FIELDS)


def _row_from_record(record: Record, *, valid_from: str) -> HistoryRow:
    """Build an OPEN history interval from a current-state Record."""
    payload = {name: getattr(record, name) for name in _PAYLOAD_FIELDS}
    return HistoryRow(valid_from=valid_from, valid_to=None, **payload)


def diff_history(
    previous_history: Iterable[HistoryRow],
    current: Iterable[Record],
    *,
    as_of: str,
) -> list[HistoryRow]:
    """Return the new cumulative history set after folding ``current`` into prior history.

    ``as_of`` is the effective date of this build (e.g. the source ``synced_at`` or today).
    Output is the FULL set (closed + open intervals), deterministically sorted by
    (callsign, valid_from, valid_to). See the module docstring for the change semantics.
    """
    prior = list(previous_history)
    # Closed intervals are immutable history: carry them through untouched.
    result: list[HistoryRow] = [row for row in prior if row.valid_to is not None]
    # Index the at-most-one open interval per callsign (the "previous holder" state).
    open_by_call: dict[str, HistoryRow] = {
        row.callsign: row for row in prior if row.valid_to is None
    }

    current_by_call: dict[str, Record] = {rec.callsign: rec for rec in current}

    for callsign, record in current_by_call.items():
        open_iv = open_by_call.pop(callsign, None)
        if open_iv is None:
            # Brand-new callsign -> open a fresh interval.
            result.append(_row_from_record(record, valid_from=as_of))
        elif _identity(open_iv) == _identity(record):
            # Unchanged holder/location -> carry the open interval forward as-is.
            result.append(open_iv)
        else:
            # Changed -> close the old interval and open a new one.
            result.append(replace(open_iv, valid_to=as_of))
            result.append(_row_from_record(record, valid_from=as_of))

    # Anything still open but absent from current disappeared (expired/cancelled) -> close.
    for open_iv in open_by_call.values():
        result.append(replace(open_iv, valid_to=as_of))

    result.sort(key=lambda r: (r.callsign, r.valid_from, r.valid_to or "~"))
    return result
