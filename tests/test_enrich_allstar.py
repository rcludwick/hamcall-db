"""Tests for AllStarLink node-number enrichment (hdb-8803).

Joins each callsign to its AllStarLink node numbers (one callsign -> MANY nodes).
Mirrors the cty.dat enrichment shape: a pure loader + a pure `Record -> Record`
enricher. All offline (checked-in fixture); no network.
"""

from __future__ import annotations

from pathlib import Path

from hamcall_db.enrich_allstar import (
    download_allstar,
    enrich,
    load_allstar,
)
from hamcall_db.models import Record

FIXTURE = Path(__file__).parent / "fixtures" / "allstar" / "allmondb.txt"


# --- loader / parse ------------------------------------------------------------


def test_load_maps_callsign_to_sorted_unique_nodes() -> None:
    lookup = load_allstar(FIXTURE)
    # One callsign -> MANY nodes, sorted ascending, deduped.
    assert lookup["WB6NIL"] == [2000, 2001, 2002]


def test_load_uppercases_callsign_and_merges_case() -> None:
    lookup = load_allstar(FIXTURE)
    # 'W1AW' and 'w1aw' fold to one UPPER key with both nodes, sorted.
    assert lookup["W1AW"] == [40000, 40001]


def test_load_handles_empty_description_and_blank_lines() -> None:
    lookup = load_allstar(FIXTURE)
    # Empty Description field must not break parsing; blank lines are skipped.
    assert lookup["N0CALL"] == [51234]


def test_load_no_unexpected_callsigns() -> None:
    lookup = load_allstar(FIXTURE)
    assert set(lookup) == {"WB6NIL", "W1AW", "N0CALL"}


# --- enrich --------------------------------------------------------------------


def test_enrich_sets_sorted_node_list() -> None:
    lookup = load_allstar(FIXTURE)
    [out] = enrich([Record(callsign="WB6NIL", source="fcc")], lookup)
    assert out.allstar_nodes == [2000, 2001, 2002]


def test_enrich_matches_case_insensitively() -> None:
    lookup = load_allstar(FIXTURE)
    [out] = enrich([Record(callsign="w1aw", source="fcc")], lookup)
    assert out.allstar_nodes == [40000, 40001]


def test_enrich_leaves_empty_on_no_match() -> None:
    lookup = load_allstar(FIXTURE)
    [out] = enrich([Record(callsign="K9XYZ", source="fcc")], lookup)
    assert out.allstar_nodes == []


def test_enrich_does_not_mutate_input() -> None:
    lookup = load_allstar(FIXTURE)
    rec = Record(callsign="WB6NIL", source="fcc")
    list(enrich([rec], lookup))
    assert rec.allstar_nodes == []  # input untouched


def test_enrich_preserves_other_fields() -> None:
    lookup = load_allstar(FIXTURE)
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

    import datetime as dt

    path = download_allstar(tmp_path, on=dt.date(2026, 6, 16), fetcher=fetcher)
    assert path.exists()
    assert "allstar" in path.parts
    assert "2026-06-16" in path.parts
    lookup = load_allstar(path)
    assert lookup["WB6NIL"] == [2000, 2001, 2002]


def test_download_reuses_cache_on_not_modified(tmp_path) -> None:
    import datetime as dt

    calls = {"n": 0}

    def fetcher(url: str, since: float | None) -> bytes | None:
        calls["n"] += 1
        if calls["n"] == 1:
            return _fixture_bytes()
        return None  # 304 Not Modified

    day = dt.date(2026, 6, 16)
    p1 = download_allstar(tmp_path, on=day, fetcher=fetcher)
    p2 = download_allstar(tmp_path, on=day, fetcher=fetcher)
    assert p1 == p2
    # Cached bytes survive the 304.
    assert load_allstar(p2)["N0CALL"] == [51234]
