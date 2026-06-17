"""AD1C cty.dat -> DXCC enrichment (au-9ed1).

Resolves each `Record`'s callsign to a DXCC entity, filling `country` (the entity
name) and `dxcc` (the numeric ADIF entity code). Pure functions over `Record`
iterables — no I/O beyond loading the cty.dat file once via `load_cty`. License:
AD1C cty.dat is free for non-commercial use, attribution required (mem-371f); the
NOTICE file must credit AD1C.

Two pieces:

1. `load_cty(path) -> CtyLookup` parses an AD1C cty.dat into a lookup.
2. `CtyLookup` resolves a callsign and acts as a `Record -> Record` enricher.
   `enrich_record` / `enrich` are thin functional wrappers for the pipeline.

cty.dat format
--------------
Each entity is a ``;``-terminated record. The header is colon-separated::

    Entity Name: CQ Zone: ITU Zone: Continent: Lat: Lon: GMT offset: Primary DXCC Prefix:

followed by one or more (often indented) lines of comma-separated prefixes /
callsigns, the last ending in ``;``. Token rules we implement:

* A leading ``=`` marks an EXACT full-callsign match (highest priority).
* Tokens may carry per-token overrides we IGNORE for matching and strip off:
  ``(CQ)`` CQ-zone, ``[ITU]`` ITU-zone, ``<lat/lon>`` coordinates,
  ``{continent}`` continent. Only the bare prefix/callsign is kept as the key.

Resolution
----------
For a callsign:

1. EXACT full-callsign match (the ``=`` set) wins outright.
2. Otherwise the LONGEST matching prefix wins (e.g. "KH6AA" -> "KH6" over "K").
3. No match -> ``(None, None)``; `country`/`dxcc` are left untouched.

DXCC NUMBER caveat
------------------
Standard cty.dat does NOT carry the numeric ADIF DXCC code — only the entity name
and a textual "Primary DXCC Prefix". We chose option (b): bundle a small
``hamcall_db/data/dxcc_entity_numbers.json`` mapping primary-prefix -> ADIF number.
`country` is therefore always reliable (straight from cty.dat); `dxcc` is
best-effort and is ``None`` for any entity whose primary prefix is absent from the
seed table. Filling that table out (e.g. by generating it from AD1C's ``cty.csv``
variant, which carries the number directly) is a recommended follow-up.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import replace
from functools import lru_cache
from importlib import resources
from pathlib import Path

from hamcall_db.models import Record

__all__ = [
    "CtyLookup",
    "load_cty",
    "enrich_record",
    "enrich",
    "DEFAULT_CTY_FILENAME",
]

# Conventional on-disk name of a downloaded AD1C country file. The importer that
# downloads it (a follow-up) should hand the path to `load_cty`.
DEFAULT_CTY_FILENAME = "cty.dat"


def _strip_token(token: str) -> str:
    """Return the bare prefix/callsign of a cty.dat token.

    Drops a leading ``=`` and any ``(...)`` / ``[...]`` / ``<...>`` / ``{...}``
    override annotations, then uppercases. Returns ``""`` for an empty token.
    """
    token = token.strip()
    if token.startswith("="):
        token = token[1:]
    out: list[str] = []
    depth = 0
    closers = {")": "(", "]": "[", ">": "<", "}": "{"}
    openers = set(closers.values())
    for ch in token:
        if ch in openers:
            depth += 1
        elif ch in closers:
            if depth > 0:
                depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out).strip().upper()


@lru_cache(maxsize=1)
def _dxcc_numbers() -> dict[str, int]:
    """Primary-prefix (UPPER) -> ADIF DXCC entity number, from the bundled seed."""
    raw = json.loads(
        resources.files("hamcall_db.data")
        .joinpath("dxcc_entity_numbers.json")
        .read_text()
    )
    return {
        str(k).strip().upper(): int(v)
        for k, v in raw.items()
        if not k.startswith("_")
    }


class CtyLookup:
    """Prefix/exact-callsign -> DXCC entity lookup built from a cty.dat.

    Instances are immutable after construction and callable as a `Record -> Record`
    enricher, so a built lookup can be passed straight into the pipeline.
    """

    __slots__ = ("_exact", "_prefix", "_max_prefix_len")

    def __init__(
        self,
        exact: dict[str, tuple[str, int | None]],
        prefix: dict[str, tuple[str, int | None]],
    ) -> None:
        self._exact = exact
        self._prefix = prefix
        self._max_prefix_len = max((len(p) for p in prefix), default=0)

    def entity_names(self) -> set[str]:
        """All distinct entity names known to this lookup."""
        names = {name for name, _ in self._exact.values()}
        names |= {name for name, _ in self._prefix.values()}
        return names

    def resolve(self, callsign: str | None) -> tuple[str | None, int | None]:
        """Resolve a callsign to ``(entity_name, dxcc_number)``.

        Exact full-callsign match first, else the longest matching prefix, else
        ``(None, None)``. ``dxcc_number`` may be ``None`` even on a name hit when the
        entity's primary prefix is missing from the bundled number table.
        """
        if not callsign:
            return (None, None)
        call = callsign.strip().upper()
        if not call:
            return (None, None)

        hit = self._exact.get(call)
        if hit is not None:
            return hit

        # Longest matching prefix: probe from the longest possible down to 1 char.
        upper = min(len(call), self._max_prefix_len)
        for length in range(upper, 0, -1):
            hit = self._prefix.get(call[:length])
            if hit is not None:
                return hit
        return (None, None)

    def __call__(self, record: Record) -> Record:
        return enrich_record(record, self)


def load_cty(path: str | Path) -> CtyLookup:
    """Parse an AD1C cty.dat file at ``path`` into a `CtyLookup`.

    The only I/O in this module. Entity records are ``;``-terminated; the first
    colon-separated line is the header (entity name + primary DXCC prefix in field 8)
    and the remainder is the comma-separated prefix/callsign list.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    numbers = _dxcc_numbers()

    exact: dict[str, tuple[str, int | None]] = {}
    prefix: dict[str, tuple[str, int | None]] = {}

    # Records are separated by ';'. Split on it and process each chunk.
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue

        # The header line is everything up to the first newline; the body (prefix
        # list) is the rest. The header has >= 8 colon-separated fields.
        header, _, body = chunk.partition("\n")
        header_fields = header.split(":")
        if len(header_fields) < 8:
            continue
        entity_name = header_fields[0].strip()
        primary_prefix = header_fields[7].strip().upper()
        dxcc_num = numbers.get(primary_prefix)
        value = (entity_name, dxcc_num)

        for raw_token in body.replace("\n", ",").split(","):
            raw_token = raw_token.strip()
            if not raw_token:
                continue
            is_exact = raw_token.lstrip().startswith("=")
            key = _strip_token(raw_token)
            if not key:
                continue
            if is_exact:
                # First definition wins on collision (matches cty.dat precedence).
                exact.setdefault(key, value)
            else:
                prefix.setdefault(key, value)

    return CtyLookup(exact, prefix)


def enrich_record(record: Record, lookup: CtyLookup) -> Record:
    """Return a NEW Record with `country`/`dxcc` resolved from ``lookup``.

    Never mutates the input. On no match the record is returned unchanged (its
    existing `country`/`dxcc`, if any, are preserved).
    """
    entity, number = lookup.resolve(record.callsign)
    if entity is None:
        return record
    return replace(record, country=entity, dxcc=number)


def enrich(records: Iterable[Record], lookup: CtyLookup) -> Iterator[Record]:
    """Apply `enrich_record` lazily across a stream of Records."""
    for record in records:
        yield enrich_record(record, lookup)
