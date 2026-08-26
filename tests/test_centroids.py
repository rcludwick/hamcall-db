"""Tests for the expanded production centroid tables (au-63fb).

au-76be shipped a tiny seed; au-63fb populates well-distributed US/CA/AU centroids.
These tests assert that the JSON tables (a) parse into the schema the geocode loader
expects, (b) contain no duplicate lookup keys, and (c) resolve a representative spread
of new entries to the *correct* 4-char Maidenhead grid. The expected grids below were
verified independently from the centroid coordinates (the well-known grid for each city).
"""

from __future__ import annotations

import json
from importlib import resources

import pytest

from hamcall_db.geocode import LookupGeocoder, latlon_to_grid
from hamcall_db.models import Record


def _load(name: str) -> list[dict]:
    return json.loads(resources.files("hamcall_db.data").joinpath(name).read_text())


# --- schema / integrity --------------------------------------------------------


def test_postal_table_matches_loader_schema() -> None:
    rows = _load("postal_centroids.json")
    assert len(rows) >= 50
    for r in rows:
        assert isinstance(r["country"], str) and r["country"]
        assert isinstance(r["postal_code"], str) and r["postal_code"]
        assert isinstance(float(r["lat"]), float)
        assert isinstance(float(r["lon"]), float)
        assert -90.0 <= r["lat"] <= 90.0
        assert -180.0 <= r["lon"] <= 180.0


def test_city_table_matches_loader_schema() -> None:
    rows = _load("city_centroids.json")
    assert len(rows) >= 100
    for r in rows:
        assert isinstance(r["country"], str) and r["country"]
        assert isinstance(r["city"], str) and r["city"]
        assert "state" in r  # may be "" for province-less countries
        assert -90.0 <= r["lat"] <= 90.0
        assert -180.0 <= r["lon"] <= 180.0


def test_no_duplicate_postal_keys() -> None:
    rows = _load("postal_centroids.json")
    keys = [(r["country"].upper(), r["postal_code"].upper()) for r in rows]
    assert len(keys) == len(set(keys))


def test_no_duplicate_city_keys() -> None:
    rows = _load("city_centroids.json")
    keys = [(r["country"].upper(), r.get("state", "").upper(), r["city"].upper()) for r in rows]
    assert len(keys) == len(set(keys))


def test_countries_cover_fcc_ised_acma_sources() -> None:
    cities = _load("city_centroids.json")
    countries = {r["country"] for r in cities}
    assert {"United States", "Canada", "Australia"} <= countries


def test_us_state_coverage_is_broad() -> None:
    # Every US state capital region should be reachable: expect a wide state spread.
    cities = _load("city_centroids.json")
    us_states = {r["state"] for r in cities if r["country"] == "United States"}
    assert len(us_states) >= 50  # 50 states + DC/PR


def test_all_canadian_provinces_and_territories_present() -> None:
    cities = _load("city_centroids.json")
    ca_states = {r["state"] for r in cities if r["country"] == "Canada"}
    expected = {
        "ON",
        "QC",
        "BC",
        "AB",
        "MB",
        "SK",
        "NS",
        "NB",
        "NL",
        "PE",
        "NT",
        "YT",
        "NU",
    }
    assert expected <= ca_states


def test_all_australian_states_present() -> None:
    cities = _load("city_centroids.json")
    au_states = {r["state"] for r in cities if r["country"] == "Australia"}
    assert {"NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"} <= au_states


# --- end-to-end grid resolution for new entries --------------------------------

# (country, postal_code, expected 4-char grid). Grids independently verified.
_POSTAL_CASES = [
    ("United States", "90001", "DM04"),  # Los Angeles, CA
    ("United States", "60601", "EN61"),  # Chicago, IL
    ("United States", "33101", "EL95"),  # Miami, FL
    ("United States", "98101", "CN87"),  # Seattle, WA
    ("Canada", "M5H", "FN03"),  # Toronto, ON
    ("Canada", "T2P", "DO21"),  # Calgary, AB
    ("Australia", "3000", "QF22"),  # Melbourne, VIC
    ("Australia", "6000", "OF78"),  # Perth, WA
]


@pytest.mark.parametrize(("country", "postal", "expected"), _POSTAL_CASES)
def test_postal_entries_resolve_to_expected_grid(country: str, postal: str, expected: str) -> None:
    geo = LookupGeocoder()
    rec = Record(callsign="TEST", postal_code=postal, country=country)
    assert geo(rec) == expected


# (country, state, city, expected grid). Grids independently verified.
_CITY_CASES = [
    ("United States", "TX", "Houston", "EL29"),
    ("United States", "CO", "Denver", "DM79"),
    ("United States", "GA", "Atlanta", "EM73"),
    ("Canada", "BC", "Vancouver", "CN89"),
    ("Canada", "MB", "Winnipeg", "EN19"),
    ("Australia", "QLD", "Brisbane", "QG62"),
    ("Australia", "SA", "Adelaide", "PF95"),
    ("Australia", "NT", "Darwin", "PH57"),
]


@pytest.mark.parametrize(("country", "state", "city", "expected"), _CITY_CASES)
def test_city_entries_resolve_to_expected_grid(
    country: str, state: str, city: str, expected: str
) -> None:
    geo = LookupGeocoder()
    rec = Record(callsign="TEST", city=city, state=state, country=country)
    assert geo(rec) == expected


def test_every_centroid_yields_a_four_char_grid() -> None:
    for name in ("postal_centroids.json", "city_centroids.json"):
        for r in _load(name):
            grid = latlon_to_grid(r["lat"], r["lon"])
            assert len(grid) == 4, (name, r)
            assert grid[:2].isalpha() and grid[2:].isdigit()
