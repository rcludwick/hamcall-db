"""Tests for the merge/normalize stage (pure functions over Record iterables)."""

from __future__ import annotations

from hamcall_db.merge import merge, normalize
from hamcall_db.models import Record

# --- normalize -----------------------------------------------------------------


def test_normalize_uppercases_and_strips_callsign() -> None:
    out = normalize(Record(callsign="  w1aw "))
    assert out.callsign == "W1AW"


def test_normalize_blanks_become_none() -> None:
    out = normalize(Record(callsign="K1ABC", first_name="  ", city=""))
    assert out.first_name is None
    assert out.city is None


def test_normalize_strips_whitespace_on_string_fields() -> None:
    out = normalize(Record(callsign="K1ABC", last_name="  Smith  ", state=" CT "))
    assert out.last_name == "Smith"
    assert out.state == "CT"


def test_normalize_leaves_non_string_fields_untouched() -> None:
    out = normalize(Record(callsign="K1ABC", dxcc=291))
    assert out.dxcc == 291


def test_normalize_is_idempotent() -> None:
    once = normalize(Record(callsign=" w1aw ", last_name=" Maxim "))
    twice = normalize(once)
    assert once == twice


# --- smart title-casing of all-caps name/place fields (hdb-b991) ----------------

import pytest  # noqa: E402

from hamcall_db.merge import _smart_titlecase  # noqa: E402


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Plain all-caps -> proper case.
        ("SMITH", "Smith"),
        ("KENNETH", "Kenneth"),
        ("BOISE", "Boise"),
        ("NEW YORK", "New York"),
        # Mc / Mac.
        ("MCDONALD", "McDonald"),
        ("MCCALL", "McCall"),
        ("MACEY", "Macey"),  # conservative: not every Mac* is MacX
        ("MACDONALD", "MacDonald"),  # known exception
        # Apostrophe / hyphen boundaries.
        ("O'BRIEN", "O'Brien"),
        ("D'ANGELO", "D'Angelo"),
        ("SMITH-JONES", "Smith-Jones"),
        # Roman-numeral generational suffix kept upper; single-letter initials kept upper.
        ("JOHN SMITH III", "John Smith III"),
        ("J R SMITH", "J R Smith"),
        ("J. R. SMITH", "J. R. Smith"),
        # Surname particles: lower when interior, capitalized when leading.
        ("JOHN VON NEUMANN", "John von Neumann"),
        ("VON NEUMANN", "Von Neumann"),
        ("DE LA CRUZ", "De la Cruz"),
        # Known acronyms (club/business holders) stay upper.
        ("TREASURE VALLEY ARC", "Treasure Valley ARC"),
        # Place edge cases.
        ("ST. LOUIS", "St. Louis"),
        # Already mixed-case values are left UNTOUCHED (never down-cased).
        ("McDonald", "McDonald"),
        ("de la Cruz", "de la Cruz"),
        ("iPhone City", "iPhone City"),
        # Non-letter / empty passthrough.
        ("123", "123"),
        ("", ""),
    ],
)
def test_smart_titlecase(raw: str, expected: str) -> None:
    assert _smart_titlecase(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["SMITH", "MCDONALD", "O'BRIEN", "DE LA CRUZ", "JOHN SMITH III", "J R SMITH", "ARRL"],
)
def test_smart_titlecase_is_idempotent(raw: str) -> None:
    once = _smart_titlecase(raw)
    assert _smart_titlecase(once) == once


def test_normalize_titlecases_name_and_place_fields() -> None:
    out = normalize(
        Record(
            callsign="kf4laj",
            first_name="KENNETH",
            last_name="SMITH",
            city="BOISE",
            county="ADA",
            state="ID",  # 2-letter abbreviation must stay UPPER
        )
    )
    assert (out.first_name, out.last_name) == ("Kenneth", "Smith")
    assert (out.city, out.county) == ("Boise", "Ada")
    assert out.state == "ID"  # not title-cased


def test_normalize_leaves_already_cased_names_untouched() -> None:
    # ISED/ACMA-style proper-case data must pass through unchanged.
    out = normalize(Record(callsign="VE3ABC", first_name="Pierre", last_name="de Villiers"))
    assert out.first_name == "Pierre"
    assert out.last_name == "de Villiers"


# --- merge: dedup + precedence -------------------------------------------------


def test_merge_single_stream_passes_through_normalized() -> None:
    out = list(merge([[Record(callsign="w1aw", source="fcc")]]))
    assert len(out) == 1
    assert out[0].callsign == "W1AW"


def test_merge_dedups_within_a_stream_keeping_first() -> None:
    stream = [
        Record(callsign="K1ABC", city="Boston", source="fcc"),
        Record(callsign="K1ABC", city="Newton", source="fcc"),
    ]
    out = list(merge([stream]))
    assert len(out) == 1
    assert out[0].city == "Boston"


def test_merge_resolves_collision_by_source_precedence() -> None:
    # Same callsign from two sources; fcc outranks ised regardless of stream order.
    ised = [Record(callsign="VA1XYZ", city="Halifax", source="ised")]
    fcc = [Record(callsign="VA1XYZ", city="Boston", source="fcc")]
    out = list(merge([ised, fcc]))
    assert len(out) == 1
    assert out[0].source == "fcc"
    assert out[0].city == "Boston"


def test_merge_keeps_distinct_callsigns_from_all_sources() -> None:
    fcc = [Record(callsign="W1AW", source="fcc")]
    acma = [Record(callsign="VK2ABC", source="acma")]
    out = list(merge([fcc, acma]))
    assert {r.callsign for r in out} == {"W1AW", "VK2ABC"}


def test_merge_output_is_sorted_by_callsign() -> None:
    fcc = [
        Record(callsign="W1AW", source="fcc"),
        Record(callsign="A1AA", source="fcc"),
        Record(callsign="K9XYZ", source="fcc"),
    ]
    out = list(merge([fcc]))
    assert [r.callsign for r in out] == ["A1AA", "K9XYZ", "W1AW"]


# --- merge: geocode hook -------------------------------------------------------


def test_merge_geocode_fills_missing_grid() -> None:
    fcc = [Record(callsign="W1AW", postal_code="06111", source="fcc")]
    out = list(merge([fcc], geocode=lambda r: "FN31"))
    assert out[0].grid == "FN31"


def test_merge_geocode_does_not_override_existing_grid() -> None:
    fcc = [Record(callsign="W1AW", grid="FN31", source="fcc")]
    out = list(merge([fcc], geocode=lambda r: "ZZ99"))
    assert out[0].grid == "FN31"


def test_merge_without_geocode_leaves_grid_none() -> None:
    fcc = [Record(callsign="W1AW", postal_code="06111", source="fcc")]
    out = list(merge([fcc]))
    assert out[0].grid is None
