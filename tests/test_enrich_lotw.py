"""Tests for LoTW (Logbook of the World) user-activity enrichment (hdb-fccf).

Stamps each callsign with whether it appears in ARRL's public LoTW user-activity
list and that user's last upload date. Mirrors the AllStarLink enrichment shape: a
polite downloader (conditional GET, injectable fetcher) plus a pure loader and a pure
``Record -> Record`` enricher. All offline (checked-in fixture); no network.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from hamcall_db.enrich_lotw import (
    download_lotw,
    enrich,
    load_lotw,
)
from hamcall_db.models import Record

FIXTURE = Path(__file__).parent / "fixtures" / "lotw" / "lotw-user-activity.csv"


# --- loader / parse ------------------------------------------------------------


def test_load_maps_callsign_to_last_activity_date() -> None:
    lookup = load_lotw(FIXTURE)
    # CALLSIGN,YYYY-MM-DD,HH:MM:SS -> just the ISO date (time discarded).
    assert lookup["WB6NIL"] == "2026-06-01"


def test_load_uppercases_and_keeps_most_recent_on_collision() -> None:
    lookup = load_lotw(FIXTURE)
    # 'W1AW' (2026-06-15) and 'w1aw' (2026-05-01) fold to one UPPER key; keep the
    # most recent upload date.
    assert lookup["W1AW"] == "2026-06-15"


def test_load_skips_blank_and_malformed_lines() -> None:
    lookup = load_lotw(FIXTURE)
    # An unparseable date, an empty callsign, and blank lines are all skipped.
    assert "BADDATE" not in lookup
    assert "" not in lookup


def test_load_tolerates_extra_trailing_fields() -> None:
    lookup = load_lotw(FIXTURE)
    # A line with more than three comma fields still parses the first three.
    assert lookup["K7ABC"] == "2026-06-17"


def test_load_no_unexpected_callsigns() -> None:
    lookup = load_lotw(FIXTURE)
    assert set(lookup) == {"WB6NIL", "W1AW", "N0CALL", "K7ABC"}


# --- enrich --------------------------------------------------------------------


def test_enrich_sets_uses_lotw_and_last_activity() -> None:
    lookup = load_lotw(FIXTURE)
    [out] = enrich([Record(callsign="WB6NIL", source="fcc")], lookup)
    assert out.uses_lotw is True
    assert out.lotw_last_activity == "2026-06-01"


def test_enrich_matches_case_insensitively() -> None:
    lookup = load_lotw(FIXTURE)
    [out] = enrich([Record(callsign="w1aw", source="fcc")], lookup)
    assert out.uses_lotw is True
    assert out.lotw_last_activity == "2026-06-15"


def test_enrich_leaves_false_and_none_on_no_match() -> None:
    lookup = load_lotw(FIXTURE)
    [out] = enrich([Record(callsign="K9XYZ", source="fcc")], lookup)
    assert out.uses_lotw is False
    assert out.lotw_last_activity is None


def test_enrich_does_not_mutate_input() -> None:
    lookup = load_lotw(FIXTURE)
    rec = Record(callsign="WB6NIL", source="fcc")
    list(enrich([rec], lookup))
    assert rec.uses_lotw is False  # input untouched
    assert rec.lotw_last_activity is None


def test_enrich_preserves_other_fields() -> None:
    lookup = load_lotw(FIXTURE)
    rec = Record(callsign="WB6NIL", first_name="Jim", state="CA", source="fcc")
    [out] = enrich([rec], lookup)
    assert out.first_name == "Jim"
    assert out.state == "CA"
    assert out.source == "fcc"


# --- downloader (cache + If-Modified-Since, via injected fetcher) ---------------


def _fixture_bytes() -> bytes:
    return FIXTURE.read_bytes()


def test_download_caches_under_dated_dir(tmp_path) -> None:
    def fetcher(url: str, since: float | None) -> bytes | None:
        return _fixture_bytes()

    path = download_lotw(tmp_path, on=dt.date(2026, 6, 18), fetcher=fetcher)
    assert path.exists()
    assert "lotw" in path.parts
    assert "2026-06-18" in path.parts
    lookup = load_lotw(path)
    assert lookup["WB6NIL"] == "2026-06-01"


def test_download_reuses_cache_on_not_modified(tmp_path) -> None:
    calls = {"n": 0}

    def fetcher(url: str, since: float | None) -> bytes | None:
        calls["n"] += 1
        if calls["n"] == 1:
            return _fixture_bytes()
        return None  # 304 Not Modified

    day = dt.date(2026, 6, 18)
    p1 = download_lotw(tmp_path, on=day, fetcher=fetcher)
    p2 = download_lotw(tmp_path, on=day, fetcher=fetcher)
    assert p1 == p2
    # Cached bytes survive the 304.
    assert load_lotw(p2)["N0CALL"] == "2024-12-31"
