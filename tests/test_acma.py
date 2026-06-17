"""Tests for the ACMA RRL amateur importer (parse/join/filter over CSV fixtures).

No network: download() is not exercised here; we parse a small checked-in slice of the
device_details/licence/client comma-delimited spectra extract under
tests/fixtures/acma/. The fixtures are headerless positional rows (matching the
published spectra column order) and include a non-amateur licence to prove filtering.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from hamcall_db.sources.acma import AcmaSource, _split_name, parse_dir

FIXTURES = Path(__file__).parent / "fixtures" / "acma"


def _by_call(records):
    return {r.callsign: r for r in records}


def test_parse_joins_device_licence_client_for_amateur() -> None:
    out = _by_call(parse_dir(FIXTURES, synced_at="2026-06-16"))
    vk4 = out["VK4ABC"]
    assert vk4.first_name == "John Andrew"  # given names after the comma
    assert vk4.last_name == "Smith"  # "Smith, John Andrew" -> surname/given
    assert vk4.city == "Brisbane"
    assert vk4.state == "QLD"
    assert vk4.postal_code == "4000"
    assert vk4.source == "acma"
    assert vk4.synced_at == "2026-06-16"


def test_only_amateur_licences_emitted() -> None:
    out = _by_call(parse_dir(FIXTURES))
    # Apparatus (non-amateur) licence holder must NOT appear.
    assert "VKNONAM" not in out
    # All amateur variants (Amateur / Amateur Beacon / Amateur Repeater) do.
    assert set(out) == {"VK4ABC", "VK6BEA", "VK4RCLUB"}


def test_cancelled_amateur_filtered_by_default() -> None:
    out = _by_call(parse_dir(FIXTURES))
    assert "VK6CAN" not in out  # licence STATUS_TEXT == 'Cancelled'


def test_granted_only_can_be_disabled() -> None:
    out = _by_call(parse_dir(FIXTURES, granted_only=False))
    assert "VK6CAN" in out


def test_duplicate_device_rows_deduped_on_callsign() -> None:
    records = [r for r in parse_dir(FIXTURES) if r.callsign == "VK4ABC"]
    assert len(records) == 1  # two device rows, one Record


def test_callsign_uppercased() -> None:
    out = _by_call(parse_dir(FIXTURES))
    # fixture stores "VK4RClub"; emitted callsign is upper-cased.
    assert "VK4RCLUB" in out


def test_club_name_kept_whole_in_last_name() -> None:
    out = _by_call(parse_dir(FIXTURES))
    club = out["VK4RCLUB"]
    assert club.first_name is None
    assert club.last_name == "Sunshine Coast Amateur Radio Club Inc"


def test_plain_given_surname_split() -> None:
    out = _by_call(parse_dir(FIXTURES))
    vk6 = out["VK6BEA"]
    assert vk6.first_name == "Jane"
    assert vk6.last_name == "Mary Doe"


def test_street_address_never_appears_in_output() -> None:
    # The redistribution contract: no street address in any Record field, ever.
    records = list(parse_dir(FIXTURES, granted_only=False))
    streets = {"42 Wireless Rd", "7 Beacon St", "1 Club Ave", "99 Telco Tce"}
    for r in records:
        values = {v for v in asdict(r).values() if isinstance(v, str)}
        assert values.isdisjoint(streets), r


def test_county_country_dxcc_grid_left_for_downstream() -> None:
    vk4 = _by_call(parse_dir(FIXTURES))["VK4ABC"]
    assert vk4.county is None  # not in RRL
    assert vk4.country is None  # cty.dat enriches downstream
    assert vk4.dxcc is None
    assert vk4.grid is None  # geocoded downstream from postcode/suburb


def test_header_row_is_skipped_if_present(tmp_path: Path) -> None:
    # Some published extracts carry a header row; the parser must skip it.
    import csv

    for name in ("client.csv", "licence.csv", "device_details.csv"):
        rows = list(csv.reader((FIXTURES / name).open(newline="")))
        # Prepend a header whose first cell is a known column name.
        header_first = {
            "client.csv": "CLIENT_NO",
            "licence.csv": "LICENCE_NO",
            "device_details.csv": "SDD_ID",
        }[name]
        width = len(rows[0])
        header = [header_first] + [f"COL{i}" for i in range(1, width)]
        with (tmp_path / name).open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)

    out = _by_call(parse_dir(tmp_path))
    assert set(out) == {"VK4ABC", "VK6BEA", "VK4RCLUB"}
    # No record leaked from a header row.
    assert "LICENCE_NO" not in out
    assert "CALL_SIGN" not in out


def test_source_parse_method_delegates_to_parse_dir() -> None:
    out = _by_call(AcmaSource().parse(FIXTURES, synced_at="2026-06-16"))
    assert out["VK4ABC"].callsign == "VK4ABC"
    assert AcmaSource().name == "acma"


def test_split_name_variants() -> None:
    assert _split_name("Smith, John") == ("John", "Smith")
    assert _split_name("Jane Doe") == ("Jane", "Doe")
    assert _split_name("Madonna") == (None, "Madonna")
    assert _split_name("Acme Radio Club Inc") == (None, "Acme Radio Club Inc")
    assert _split_name(None) == (None, None)
    assert _split_name("") == (None, None)
