"""Tests for the FCC ULS amateur importer (parse/join/normalize over .dat fixtures).

No network: download() is not exercised here; we parse a small checked-in slice of the
HD/EN/AM pipe-delimited extract under tests/fixtures/fcc/.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from hamcall_db.sources.fcc import FccUlsSource, parse_dir

FIXTURES = Path(__file__).parent / "fixtures" / "fcc"


def _by_call(records):
    return {r.callsign: r for r in records}


def test_parse_joins_hd_en_am_for_active_license() -> None:
    out = _by_call(parse_dir(FIXTURES, synced_at="2026-06-16"))
    w1aw = out["W1AW"]
    assert w1aw.first_name == "Hiram"
    assert w1aw.last_name == "Maxim"
    assert w1aw.city == "Newington"
    assert w1aw.state == "CT"
    assert w1aw.postal_code == "06111"
    assert w1aw.source == "fcc"
    assert w1aw.synced_at == "2026-06-16"


def test_operator_class_code_is_mapped_to_name() -> None:
    out = _by_call(parse_dir(FIXTURES))
    assert out["W1AW"].license_class == "Amateur Extra"  # E
    assert out["K1ABC"].license_class == "General"  # G


def test_inactive_licenses_filtered_by_default() -> None:
    out = _by_call(parse_dir(FIXTURES))
    assert "N0EXP" not in out  # HD license_status == 'E' (expired)
    assert set(out) == {"W1AW", "K1ABC"}


def test_active_only_can_be_disabled() -> None:
    out = _by_call(parse_dir(FIXTURES, active_only=False))
    assert "N0EXP" in out
    assert out["N0EXP"].license_class == "Technician"  # T


def test_street_address_never_appears_in_output() -> None:
    # The redistribution contract: no street address in any Record field, ever.
    records = list(parse_dir(FIXTURES, active_only=False))
    streets = {"225 Main St", "1 Beacon St", "500 Elm St"}
    for r in records:
        values = {v for v in asdict(r).values() if isinstance(v, str)}
        assert values.isdisjoint(streets), r


def test_county_country_dxcc_grid_left_for_downstream() -> None:
    w1aw = _by_call(parse_dir(FIXTURES))["W1AW"]
    assert w1aw.county is None  # not in FCC bulk
    assert w1aw.country is None  # au-9ed1 cty.dat enriches
    assert w1aw.dxcc is None
    assert w1aw.grid is None  # au-76be geocodes downstream


def test_source_parse_method_delegates_to_parse_dir() -> None:
    out = _by_call(FccUlsSource().parse(FIXTURES, synced_at="2026-06-16"))
    assert out["W1AW"].callsign == "W1AW"
    assert FccUlsSource().name == "fcc"
