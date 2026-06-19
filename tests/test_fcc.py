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


def test_grant_effective_expired_dates_iso_normalized() -> None:
    # HD.dat carries mm/dd/yyyy dates; we publish ISO YYYY-MM-DD.
    w1aw = _by_call(parse_dir(FIXTURES))["W1AW"]
    assert w1aw.grant_date == "2015-01-01"  # HD grant_date 01/01/2015
    assert w1aw.expired_date == "2025-01-01"  # HD expired_date 01/01/2025
    assert w1aw.effective_date == "2024-06-01"  # HD effective_date 06/01/2024


def test_frn_captured_from_en() -> None:
    out = _by_call(parse_dir(FIXTURES))
    assert out["W1AW"].frn == "0001234560"
    assert out["K1ABC"].frn == "0009876543"


def test_entity_type_raw_code_preserved() -> None:
    # EN entity_type is a small stable code; we keep the raw value.
    assert _by_call(parse_dir(FIXTURES))["W1AW"].entity_type == "L"


def test_applicant_type_mapped_to_readable() -> None:
    out = _by_call(parse_dir(FIXTURES))
    assert out["W1AW"].applicant_type == "Individual"  # EN applicant_type_code 'I'
    assert out["K1ABC"].applicant_type == "Amateur Club"  # 'B'


def test_applicant_type_unknown_code_passes_through() -> None:
    # An unmapped code is kept verbatim rather than dropped.
    out = _by_call(parse_dir(FIXTURES, active_only=False))
    assert out["N0EXP"].applicant_type == "X"  # 'X' is not in the map


def test_previous_callsign_from_am() -> None:
    out = _by_call(parse_dir(FIXTURES))
    assert out["W1AW"].previous_callsign == "W1OLD"
    assert out["K1ABC"].previous_callsign is None  # blank in AM.dat


def test_new_fields_default_none_when_absent() -> None:
    # K1ABC has no effective_date in its HD row -> None, not a crash.
    assert _by_call(parse_dir(FIXTURES))["K1ABC"].effective_date is None


def test_source_parse_method_delegates_to_parse_dir() -> None:
    out = _by_call(FccUlsSource().parse(FIXTURES, synced_at="2026-06-16"))
    assert out["W1AW"].callsign == "W1AW"
    assert FccUlsSource().name == "fcc"


def test_synced_at_defaults_to_none_before_download() -> None:
    assert FccUlsSource().synced_at is None


def test_parse_uses_explicit_synced_at_over_self() -> None:
    src = FccUlsSource()
    src.synced_at = "2026-01-01"
    out = _by_call(src.parse(FIXTURES, synced_at="2026-06-16"))
    assert out["W1AW"].synced_at == "2026-06-16"


def test_parse_falls_back_to_self_synced_at() -> None:
    src = FccUlsSource()
    src.synced_at = "2026-06-10"  # as download() would have set it
    out = _by_call(src.parse(FIXTURES))
    assert out["W1AW"].synced_at == "2026-06-10"
