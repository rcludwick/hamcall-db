"""Merge/normalize stage: combine per-source `Record` streams into the final set.

Pure functions over iterables — no I/O. The ONLY place where source precedence
matters. Callsign collisions across countries are theoretically possible; precedence
is codified here once (FCC wins for US prefixes, etc.) rather than sprinkled through
importers.

Grid derivation is a pluggable hook (`geocode`) so this stage stays decoupled from the
postal/city lookup tables that land in au-76be.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import fields, replace

from hamcall_db.models import Record

# Source precedence, highest first. Used to resolve cross-source callsign collisions.
SOURCE_PRECEDENCE: tuple[str, ...] = ("fcc", "ised", "acma")

# Fields that hold human names / place names and should be smart-title-cased when a source
# (notably FCC ULS) delivers them ALL CAPS. Deliberately EXCLUDES `state` (often a 2-letter
# abbreviation like "CT"/"ID" that must stay upper), `postal_code`, `country` (DXCC entity
# name, already proper from cty.dat), and machine fields.
_NAME_FIELDS: frozenset[str] = frozenset({"first_name", "last_name", "city", "county"})

# Surname particles rendered lowercase when INTERIOR (capitalized when leading). Conservative
# European set; cross-cultural overlaps (e.g. Vietnamese "Le"/"Van") are accepted imperfections.
_PARTICLES: frozenset[str] = frozenset({
    "de", "del", "della", "van", "von", "der", "den", "da", "das", "dos", "di", "du",
    "la", "le", "of", "the",
})
# Known acronyms (club/business holders often live in last_name) kept fully uppercase.
_ACRONYMS: frozenset[str] = frozenset({
    "ARRL", "ARC", "ARES", "RACES", "VFW", "EOC", "USA", "VHF", "UHF", "DX",
})
# "Mac" is only prefixed-capitalized for known names; every other Mac* stays plain-Title
# (MACEY -> Macey, MACDONALD -> MacDonald).
_MAC_EXCEPTIONS: frozenset[str] = frozenset({
    "MACDONALD", "MACARTHUR", "MACKENZIE", "MACLEOD", "MACMILLAN", "MACGREGOR",
    "MACINTOSH", "MACINTYRE", "MACNEIL", "MACPHERSON", "MACKAY", "MACLEAN", "MACLACHLAN",
})
# Generational roman-numeral suffixes kept upper ("JOHN SMITH III" -> "John Smith III").
_ROMAN_SUFFIXES: frozenset[str] = frozenset({"II", "III", "IV", "V", "VI", "VII", "VIII", "IX"})
# A single letter, optionally with a trailing period: a personal initial -> kept upper.
_INITIAL_RE = re.compile(r"^[A-Za-z]\.?$")


def _cap_chunk(chunk: str) -> str:
    """Capitalize one separator-free alphabetic chunk, honoring Mc*/Mac* conventions."""
    if not chunk:
        return chunk
    upper = chunk.upper()
    if upper.startswith("MC") and len(chunk) > 2:
        return "Mc" + _cap_chunk(chunk[2:])
    if upper in _MAC_EXCEPTIONS:
        return "Mac" + _cap_chunk(chunk[3:])
    return chunk[:1].upper() + chunk[1:].lower()


def _cap_word(word: str) -> str:
    """Capitalize a whitespace-free word, treating ``-`` and ``'`` as internal boundaries."""
    for sep in ("-", "'"):
        if sep in word:
            return sep.join(_cap_word(part) for part in word.split(sep))
    return _cap_chunk(word)


def _smart_titlecase(value: str) -> str:
    """Title-case an ALL-CAPS name/place value; leave already-cased values untouched.

    Only re-cases values with NO lowercase letters (so it never down-cases deliberately-cased
    ISED/ACMA data, and is idempotent — a re-cased value has lowercase letters and is returned
    unchanged on a second pass). Handles Mc/Mac, O'/D', hyphens, roman-numeral suffixes,
    single-letter initials, interior surname particles, and known acronyms. Internal runs of
    whitespace are collapsed to single spaces.
    """
    if any(c.islower() for c in value) or not any(c.isalpha() for c in value):
        return value
    words = value.split()
    out: list[str] = []
    for i, word in enumerate(words):
        upper = word.upper()
        if _INITIAL_RE.match(word):  # personal initial: J, R, J.
            out.append(upper)
        elif upper in _ROMAN_SUFFIXES:  # generational suffix: III
            out.append(upper)
        elif upper in _ACRONYMS:  # club/business acronym: ARRL, ARC
            out.append(upper)
        elif i != 0 and upper.lower() in _PARTICLES:  # interior particle: von, de, la
            out.append(upper.lower())
        else:
            out.append(_cap_word(word))
    return " ".join(out)

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
    """Clean a single record: trim strings, blank -> None, uppercase the callsign, and
    smart-title-case ALL-CAPS name/place fields (FCC ULS ships these uppercase; hdb-b991).

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
        elif cleaned and name in _NAME_FIELDS:
            cleaned = _smart_titlecase(cleaned)
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
