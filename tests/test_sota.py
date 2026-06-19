"""Tests for the SOTA (Summits on the Air) summit reference dataset (hdb-ca00).

A SEPARATE dataset from the callsign Record schema — summits are PLACES, not licensees.
All offline: parse tests drive off the checked-in fixture under tests/fixtures/sota/
(a small real-summit slice, NEVER the full ~170k-summit list). The downloader uses an
injectable fetcher seam so no test needs the network.

SOTA summit coords/grid are stored VERBATIM (INDICATIVE only) — the person-grid 4-char
truncation rule (mem-e3fd) does NOT apply to public landmarks (mountain summits).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from hamcall_db.sources import sota
from hamcall_db.sources.sota import SotaSource

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sota"
FIXTURE_CSV = FIXTURE_DIR / "summitslist.csv"


# --- SummitRecord schema -------------------------------------------------------


def test_summit_record_columns() -> None:
    # The published summits schema (additive; separate from callsign Record).
    assert sota.SUMMIT_SCHEMA_COLUMNS == (
        "reference",
        "name",
        "association",
        "region",
        "alt_m",
        "alt_ft",
        "grid",
        "lat",
        "lon",
        "points",
        "bonus_points",
        "valid_from",
        "valid_to",
        "active",
        "source",
        "synced_at",
    )


# --- parse ---------------------------------------------------------------------


def test_parse_maps_core_fields() -> None:
    summits = {s.reference: s for s in sota.parse_file(FIXTURE_CSV)}
    scafell = summits["G/LD-001"]
    assert scafell.name == "Scafell Pike"
    assert scafell.association == "England (Lake District)"
    assert scafell.region == "Lake District"
    assert scafell.alt_m == 978
    assert scafell.alt_ft == 3209
    assert scafell.points == 8
    assert scafell.bonus_points == 3
    assert scafell.source == "sota"


def test_parse_reference_is_primary_key_unique() -> None:
    refs = [s.reference for s in sota.parse_file(FIXTURE_CSV)]
    assert len(refs) == len(set(refs))


def test_parse_stores_coords_verbatim() -> None:
    summits = {s.reference: s for s in sota.parse_file(FIXTURE_CSV)}
    browns = summits["W7A/MN-001"]
    # Stored verbatim from the Longitude/Latitude columns; NOT truncated.
    assert browns.lat == 33.7042
    assert browns.lon == -111.387


def test_parse_grid_verbatim_no_truncation() -> None:
    # A 6-char Maidenhead locator must be kept VERBATIM (mem-e3fd does NOT apply to
    # public landmarks). The fixture has no grid column, so grid is derived from coords
    # at FULL (6-char) precision and stored as-is.
    summits = {s.reference: s for s in sota.parse_file(FIXTURE_CSV)}
    grid = summits["G/LD-001"].grid
    assert grid is not None
    assert len(grid) == 6  # 6-char subsquare preserved, never truncated to 4


def test_parse_dates_normalized_to_iso() -> None:
    summits = {s.reference: s for s in sota.parse_file(FIXTURE_CSV)}
    bogong = summits["VK3/VC-001"]
    # DD/MM/YYYY upstream -> ISO YYYY-MM-DD.
    assert bogong.valid_from == "2012-02-01"
    assert bogong.valid_to == "2099-12-31"


def test_parse_active_flag_from_valid_to() -> None:
    summits = {s.reference: s for s in sota.parse_file(FIXTURE_CSV)}
    # A summit whose valid_to is in the far future is active.
    assert summits["G/LD-001"].active is True
    # A summit whose valid_to is in the past (retired) is inactive.
    assert summits["W0C/SP-001"].active is False


def test_parse_stamps_synced_at() -> None:
    summits = list(sota.parse_file(FIXTURE_CSV, synced_at="2026-06-18"))
    assert summits
    assert all(s.synced_at == "2026-06-18" for s in summits)


def test_parse_handles_quoted_commas_in_names() -> None:
    # AssociationName/RegionName may be quoted and contain commas — the CSV reader must
    # honor quoting, not naive splitting.
    summits = {s.reference: s for s in sota.parse_file(FIXTURE_CSV)}
    assert "Bouvet" in summits["3Y/BV-001"].association


# --- downloader (cache + If-Modified-Since via injected fetcher) ----------------


def _csv_bytes() -> bytes:
    return FIXTURE_CSV.read_bytes()


def test_download_caches_under_dated_dir(tmp_path) -> None:
    def fetcher(url: str, since: float | None) -> bytes | None:
        return _csv_bytes()

    src = SotaSource()
    path = src.download(tmp_path, on=dt.date(2026, 6, 18), fetcher=fetcher)
    assert path.exists()
    assert "sota" in path.parts
    assert "2026-06-18" in path.parts


def test_download_reuses_cache_on_not_modified(tmp_path) -> None:
    calls = {"n": 0}

    def fetcher(url: str, since: float | None) -> bytes | None:
        calls["n"] += 1
        if calls["n"] == 1:
            return _csv_bytes()
        return None  # 304 Not Modified

    src = SotaSource()
    day = dt.date(2026, 6, 18)
    p1 = src.download(tmp_path, on=day, fetcher=fetcher)
    p2 = src.download(tmp_path, on=day, fetcher=fetcher)
    assert p1 == p2
    summits = list(src.parse(p2))
    assert any(s.reference == "G/LD-001" for s in summits)


def test_download_then_parse_stamps_synced_at(tmp_path) -> None:
    def fetcher(url: str, since: float | None) -> bytes | None:
        return _csv_bytes()

    src = SotaSource()
    day = dt.date(2026, 6, 18)
    path = src.download(tmp_path, on=day, fetcher=fetcher)
    summits = list(src.parse(path))
    assert summits
    assert all(s.synced_at == "2026-06-18" for s in summits)


# --- fixture sanity ------------------------------------------------------------


def test_fixture_has_header_and_rows() -> None:
    text = FIXTURE_CSV.read_text(encoding="utf-8")
    assert text.startswith("SOTA Summits List")
    assert "SummitCode" in text
