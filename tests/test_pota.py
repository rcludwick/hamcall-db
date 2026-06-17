"""Tests for the POTA (Parks On The Air) parks reference dataset (hdb-9640).

A SEPARATE dataset from the callsign Record schema — parks are places, not licensees.
All offline: parse/merge tests drive off the checked-in fixture under tests/fixtures/pota/
(small real-park slice). The downloader uses an injectable fetcher seam so no test needs
the network.

POTA coords/grid are stored VERBATIM (INDICATIVE only) — the person-grid 4-char truncation
rule (mem-e3fd) does NOT apply to public landmarks. See the nugget spec.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from hamcall_db.sources import pota
from hamcall_db.sources.pota import PotaSource

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "pota"
FIXTURE_US = FIXTURE_DIR / "parks_US.json"
FIXTURE_CA = FIXTURE_DIR / "parks_CA.json"


# --- ParkRecord schema ---------------------------------------------------------


def test_park_record_columns() -> None:
    # The published parks schema (additive; separate from callsign Record).
    assert pota.PARK_SCHEMA_COLUMNS == (
        "reference",
        "name",
        "location_desc",
        "region",
        "country",
        "dxcc",
        "grid",
        "lat",
        "lon",
        "active",
        "source",
        "synced_at",
    )


# --- parse ---------------------------------------------------------------------


def test_parse_maps_core_fields() -> None:
    parks = {p.reference: p for p in pota.parse_file(FIXTURE_US, country="US")}
    acadia = parks["US-0001"]
    assert acadia.name == "Acadia National Park"
    assert acadia.location_desc == "US-ME"
    assert acadia.region == "ME"
    assert acadia.country == "US"
    assert acadia.source == "pota"


def test_parse_stores_grid_verbatim_no_truncation() -> None:
    # Person-grid 4-char truncation (mem-e3fd) must NOT apply: store 6-char as-given.
    parks = {p.reference: p for p in pota.parse_file(FIXTURE_US, country="US")}
    assert parks["US-0001"].grid == "FN54vh"  # 6-char preserved verbatim
    assert parks["US-4567"].grid == "DN14"  # already 4-char, unchanged


def test_parse_stores_coords_verbatim() -> None:
    parks = {p.reference: p for p in pota.parse_file(FIXTURE_US, country="US")}
    assert parks["US-0001"].lat == 44.31
    assert parks["US-0001"].lon == -68.2034


def test_parse_nullable_coords_and_grid() -> None:
    parks = {p.reference: p for p in pota.parse_file(FIXTURE_US, country="US")}
    inactive = parks["US-9999"]
    assert inactive.lat is None
    assert inactive.lon is None
    assert inactive.grid is None


def test_parse_active_flag_defaults_true() -> None:
    parks = {p.reference: p for p in pota.parse_file(FIXTURE_US, country="US")}
    # POTA parks are active unless explicitly marked inactive.
    assert parks["US-0001"].active is True
    assert parks["US-9999"].active is False


def test_parse_stamps_synced_at() -> None:
    parks = list(pota.parse_file(FIXTURE_US, country="US", synced_at="2026-06-17"))
    assert all(p.synced_at == "2026-06-17" for p in parks)


def test_parse_reference_is_primary_key_unique() -> None:
    refs = [p.reference for p in pota.parse_file(FIXTURE_US, country="US")]
    assert len(refs) == len(set(refs))


# --- downloader (cache + If-Modified-Since via injected fetcher) ----------------


def _us_bytes() -> bytes:
    return FIXTURE_US.read_bytes()


def test_download_caches_under_dated_dir(tmp_path) -> None:
    def fetcher(url: str, since: float | None) -> bytes | None:
        return _us_bytes()

    src = PotaSource(programs=["US"])
    path = src.download(tmp_path, on=dt.date(2026, 6, 17), fetcher=fetcher)
    assert path.exists()
    assert "pota" in path.parts
    assert "2026-06-17" in path.parts


def test_download_reuses_cache_on_not_modified(tmp_path) -> None:
    calls = {"n": 0}

    def fetcher(url: str, since: float | None) -> bytes | None:
        calls["n"] += 1
        if calls["n"] == 1:
            return _us_bytes()
        return None  # 304 Not Modified

    src = PotaSource(programs=["US"])
    day = dt.date(2026, 6, 17)
    p1 = src.download(tmp_path, on=day, fetcher=fetcher)
    p2 = src.download(tmp_path, on=day, fetcher=fetcher)
    assert p1 == p2
    parks = list(src.parse(p2))  # download() returns the cache directory
    assert any(p.reference == "US-0001" for p in parks)


def test_download_multiple_programs(tmp_path) -> None:
    def fetcher(url: str, since: float | None) -> bytes | None:
        if url.endswith("/CA"):
            return FIXTURE_CA.read_bytes()
        return _us_bytes()

    src = PotaSource(programs=["US", "CA"])
    day = dt.date(2026, 6, 17)
    cache_dir = src.download(tmp_path, on=day, fetcher=fetcher)
    parks = {p.reference: p for p in src.parse(cache_dir)}
    assert "US-0001" in parks
    assert "CA-0001" in parks
    assert parks["CA-0001"].country == "CA"
    assert parks["CA-0001"].region == "AB"


# --- source-dir parse ----------------------------------------------------------


def test_parse_dir_infers_country_from_filename() -> None:
    parks = {p.reference: p for p in pota.parse_dir(FIXTURE_DIR)}
    assert parks["US-0001"].country == "US"
    assert parks["CA-0001"].country == "CA"


def test_parse_synced_at_falls_back_to_self(tmp_path) -> None:
    def fetcher(url: str, since: float | None) -> bytes | None:
        return _us_bytes()

    src = PotaSource(programs=["US"])
    src.synced_at = "2026-06-17"
    day = dt.date(2026, 6, 17)
    path = src.download(tmp_path, on=day, fetcher=fetcher)
    parks = list(src.parse(path))  # download() returns the cache directory
    assert all(p.synced_at == "2026-06-17" for p in parks)


# --- fixture sanity ------------------------------------------------------------


def test_fixture_is_valid_json_array() -> None:
    data = json.loads(FIXTURE_US.read_text())
    assert isinstance(data, list)
    assert data[0]["reference"] == "US-0001"
