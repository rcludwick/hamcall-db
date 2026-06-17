"""Tests for the ISED Canada amateur importer.

No network: download() is not exercised here; we parse a small checked-in slice of the
semicolon-delimited amateur extract under tests/fixtures/ised/.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from hamcall_db.sources.ised import IsedSource, parse_dir, parse_file

FIXTURES = Path(__file__).parent / "fixtures" / "ised"
AMATEUR_TXT = FIXTURES / "amateur.txt"


def _by_call(records):
    return {r.callsign: r for r in records}


def test_parse_individual_maps_name_and_location() -> None:
    out = _by_call(parse_file(AMATEUR_TXT, synced_at="2026-06-16"))
    va3abc = out["VA3ABC"]
    assert va3abc.first_name == "John"
    assert va3abc.last_name == "Smith"
    assert va3abc.city == "Toronto"
    assert va3abc.state == "ON"  # province maps to state
    assert va3abc.postal_code == "M5V 2T6"
    assert va3abc.source == "ised"
    assert va3abc.synced_at == "2026-06-16"


def test_qualifications_map_to_license_class() -> None:
    out = _by_call(parse_file(AMATEUR_TXT))
    # VA3ABC has Basic + Advanced; VE7XYZ has Basic only.
    assert out["VA3ABC"].license_class == "Basic with Honours, Advanced"
    assert out["VE7XYZ"].license_class == "Basic"


def test_club_entity_lands_in_last_name() -> None:
    out = _by_call(parse_file(AMATEUR_TXT))
    club = out["VE3RAC"]
    # Clubs have no personal name; the entity goes into last_name.
    assert club.last_name == "Radio Amateurs of Canada Inc"
    assert club.first_name is None
    assert club.city == "Ottawa"


def test_no_qualifications_yields_no_license_class() -> None:
    out = _by_call(parse_file(AMATEUR_TXT))
    assert out["VA1NB"].license_class is None


def test_street_address_never_appears_in_output() -> None:
    # The redistribution contract: no street address in any Record field, ever.
    records = list(parse_file(AMATEUR_TXT))
    streets = {
        "123 Maple Ave",
        "456 Pine St",
        "720 Belfast Rd",
        "789 Elm Rd",
    }
    for r in records:
        values = {v for v in asdict(r).values() if isinstance(v, str)}
        assert values.isdisjoint(streets), r


def test_county_country_dxcc_grid_left_for_downstream() -> None:
    va3abc = _by_call(parse_file(AMATEUR_TXT))["VA3ABC"]
    assert va3abc.county is None  # US-only concept
    assert va3abc.country is None  # cty.dat enriches downstream
    assert va3abc.dxcc is None
    assert va3abc.grid is None  # geocoded downstream


def test_parse_dir_finds_amateur_txt() -> None:
    out = _by_call(parse_dir(FIXTURES, synced_at="2026-06-16"))
    assert set(out) == {"VA3ABC", "VE7XYZ", "VE3RAC", "VA1NB"}


def test_source_parse_method_delegates_to_parse_dir() -> None:
    out = _by_call(IsedSource().parse(FIXTURES, synced_at="2026-06-16"))
    assert out["VA3ABC"].callsign == "VA3ABC"
    assert IsedSource().name == "ised"


def test_synced_at_defaults_to_none_before_download() -> None:
    assert IsedSource().synced_at is None


def test_parse_uses_explicit_synced_at_over_self() -> None:
    src = IsedSource()
    src.synced_at = "2026-01-01"
    out = _by_call(src.parse(FIXTURES, synced_at="2026-06-16"))
    assert out["VA3ABC"].synced_at == "2026-06-16"


def test_parse_falls_back_to_self_synced_at() -> None:
    src = IsedSource()
    src.synced_at = "2026-06-10"  # as download() would have set it
    out = _by_call(src.parse(FIXTURES))
    assert out["VA3ABC"].synced_at == "2026-06-10"
