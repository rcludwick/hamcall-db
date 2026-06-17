"""Merge/normalize stage: combine per-source `Record` streams into the final set.

Pure functions over iterables — no I/O. The ONLY place where source precedence
matters. Callsign collisions across countries are theoretically possible; precedence
is codified here once (FCC wins for US prefixes, etc.) rather than sprinkled through
importers.

Grid derivation is a pluggable hook (`geocode`) so this stage stays decoupled from the
postal/city lookup tables that land in au-76be.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import fields, replace

from hamcall_db.models import Record

# Source precedence, highest first. Used to resolve cross-source callsign collisions.
SOURCE_PRECEDENCE: tuple[str, ...] = ("fcc", "ised", "acma")

# A geocoder maps a (normalized) record to a 4-char Maidenhead grid, or None.
Geocoder = Callable[[Record], "str | None"]

# String-valued fields get trimmed/blanked by normalize(). With `from __future__ import
# annotations` the field types are their annotation strings; every string field's
# annotation starts with "str" ("str" for callsign, "str | None" for the rest), while
# dxcc is "int | None".
_STR_FIELDS: tuple[str, ...] = tuple(
    f.name for f in fields(Record) if str(f.type).startswith("str")
)


def _precedence_rank(source: str | None) -> int:
    """Lower rank wins. Unknown/None sources sort after all known ones."""
    try:
        return SOURCE_PRECEDENCE.index(source)  # type: ignore[arg-type]
    except ValueError:
        return len(SOURCE_PRECEDENCE)


def normalize(record: Record) -> Record:
    """Clean a single record: trim strings, blank -> None, uppercase the callsign.

    Idempotent.
    """
    changes: dict[str, str | None] = {}
    for name in _STR_FIELDS:
        value = getattr(record, name)
        if value is None:
            continue
        cleaned = value.strip()
        if name == "callsign":
            cleaned = cleaned.upper()
        changes[name] = cleaned or None
    return replace(record, **changes)


def merge(
    streams: Iterable[Iterable[Record]],
    *,
    geocode: Geocoder | None = None,
) -> Iterator[Record]:
    """Merge per-source record streams into one normalized, deduplicated set.

    On a callsign collision the higher-precedence source wins (see SOURCE_PRECEDENCE);
    within a single source the first occurrence wins. When `geocode` is supplied it
    fills `grid` only where it is missing. Output is sorted by callsign for
    reproducible builds.
    """
    winners: dict[str, Record] = {}
    for stream in streams:
        for raw in stream:
            record = normalize(raw)
            existing = winners.get(record.callsign)
            if existing is None:
                winners[record.callsign] = record
            elif _precedence_rank(record.source) < _precedence_rank(existing.source):
                winners[record.callsign] = record

    for callsign in sorted(winners):
        record = winners[callsign]
        if geocode is not None and record.grid is None:
            record = replace(record, grid=geocode(record))
        yield record
