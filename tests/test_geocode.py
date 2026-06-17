"""Tests for the offline grid geocoder (au-76be).

Covers the deterministic Maidenhead core plus the postal/city lookup geocoder that
fills the merge() `geocode` hook. No network: all lookups come from the small checked-in
seed tables under hamcall_db/data/.
"""

from __future__ import annotations

import pytest

from hamcall_db.geocode import LookupGeocoder, latlon_to_grid
from hamcall_db.models import Record

# --- latlon_to_grid: deterministic Maidenhead core -----------------------------


@pytest.mark.parametrize(
    ("lat", "lon", "expected"),
    [
        (41.7, -72.7, "FN31"),  # Newington, CT (ARRL HQ region)
        (-33.9, 151.2, "QF56"),  # Sydney, AU (southern + eastern hemisphere)
        (0.0, 0.0, "JJ00"),  # origin
        (51.5, -0.1, "IO91"),  # London
        (-34.6, -58.4, "GF05"),  # Buenos Aires (both negative)
    ],
)
def test_latlon_to_grid_known_references(lat: float, lon: float, expected: str) -> None:
    assert latlon_to_grid(lat, lon) == expected


def test_latlon_to_grid_is_always_four_chars() -> None:
    for lat in (-89.0, -33.9, 0.0, 41.7, 89.0):
        for lon in (-179.0, -72.7, 0.0, 151.2, 179.0):
            grid = latlon_to_grid(lat, lon)
            assert len(grid) == 4, (lat, lon, grid)


def test_latlon_to_grid_field_is_uppercase_square_is_digits() -> None:
    grid = latlon_to_grid(41.7, -72.7)
    assert grid[:2].isalpha() and grid[:2].isupper()
    assert grid[2:].isdigit()


def test_latlon_to_grid_clamps_extremes() -> None:
    # Poles / antimeridian must not overflow into a 3rd field letter.
    assert len(latlon_to_grid(90.0, 180.0)) == 4
    assert len(latlon_to_grid(-90.0, -180.0)) == 4


# --- LookupGeocoder: postal -> city -> None priority ---------------------------


def test_geocoder_uses_postal_centroid_first() -> None:
    geo = LookupGeocoder()
    # 06111 (Newington, CT) is in the seed table -> FN31.
    rec = Record(callsign="W1AW", postal_code="06111", country="United States")
    assert geo(rec) == "FN31"


def test_geocoder_falls_back_to_city_centroid() -> None:
    geo = LookupGeocoder()
    rec = Record(
        callsign="VK2ABC",
        city="Sydney",
        state="NSW",
        country="Australia",
        postal_code=None,
    )
    assert geo(rec) == "QF56"


def test_geocoder_returns_none_when_unknown() -> None:
    geo = LookupGeocoder()
    rec = Record(callsign="X0XXX", city="Nowhere", country="Atlantis")
    assert geo(rec) is None


def test_geocoder_returns_four_char_grid() -> None:
    geo = LookupGeocoder()
    rec = Record(callsign="W1AW", postal_code="06111", country="United States")
    grid = geo(rec)
    assert grid is not None and len(grid) == 4


def test_geocoder_postal_lookup_is_country_scoped() -> None:
    # A postal code present only for a different country must not match.
    geo = LookupGeocoder()
    rec = Record(callsign="W1AW", postal_code="06111", country="Australia")
    # No AU postal 06111 in seed; should fall through (no city given) -> None.
    assert geo(rec) is None


# --- integration through merge() -----------------------------------------------


def test_merge_with_lookup_geocoder_fills_grid() -> None:
    from hamcall_db.merge import merge

    fcc = [Record(callsign="W1AW", postal_code="06111", country="United States", source="fcc")]
    out = list(merge([fcc], geocode=LookupGeocoder()))
    assert out[0].grid == "FN31"


def test_merge_with_lookup_geocoder_does_not_override_existing_grid() -> None:
    from hamcall_db.merge import merge

    fcc = [
        Record(
            callsign="W1AW",
            postal_code="06111",
            country="United States",
            grid="AA00",
            source="fcc",
        )
    ]
    out = list(merge([fcc], geocode=LookupGeocoder()))
    assert out[0].grid == "AA00"
