"""Tests for the ACMA RRL amateur importer (parse/join/filter over CSV fixtures).

No network: download() is not exercised here; we parse a small checked-in slice of the
device_details/licence/client comma-delimited spectra extract under
tests/fixtures/acma/. The top-level fixtures are headerless positional rows (matching the
published spectra column order) and include a non-amateur licence to prove filtering;
headered/ holds rows under the real 2026 header rows.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from hamcall_db.history import HistoryRow
from hamcall_db.models import Record
from hamcall_db.sources.acma import AcmaSource, parse_dir, withhold_prior_history

FIXTURES = Path(__file__).parent / "fixtures" / "acma"


def _by_call(records):
    return {r.callsign: r for r in records}


def test_parse_joins_device_licence_client_for_amateur() -> None:
    out = _by_call(parse_dir(FIXTURES, synced_at="2026-06-16"))
    org = out["VK4RCLUB"]  # CLIENT_TYPE_ID 5 (Company): published in full
    assert org.last_name == "Example Coast Repeater Company Pty Ltd"
    assert (org.city, org.state, org.postal_code) == ("Caloundra", "QLD", "4551")
    assert org.source == "acma"
    assert org.synced_at == "2026-06-16"


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


def test_synced_at_defaults_to_none_before_download() -> None:
    assert AcmaSource().synced_at is None


def test_parse_uses_explicit_synced_at_over_self() -> None:
    src = AcmaSource()
    src.synced_at = "2026-01-01"
    out = _by_call(src.parse(FIXTURES, synced_at="2026-06-16"))
    assert out["VK4ABC"].synced_at == "2026-06-16"


def test_parse_falls_back_to_self_synced_at() -> None:
    src = AcmaSource()
    src.synced_at = "2026-06-10"  # as download() would have set it
    out = _by_call(src.parse(FIXTURES))
    assert out["VK4ABC"].synced_at == "2026-06-10"


# --- Headered extract in the 2026 published layout (tests/fixtures/acma/headered/) ---
# Synthetic rows under the real header rows of the 2026-11-01 spectra extract. The
# device rows carry a populated AREA_AREA_ID (index 32) right before CALL_SIGN (33).

HEADERED = FIXTURES / "headered"


def test_headered_extract_reads_call_sign_not_area_id() -> None:
    out = _by_call(parse_dir(HEADERED, synced_at="2026-11-01"))
    # Land-mobile VKTELCO is filtered; the area ids (17737...) are never read as calls.
    assert set(out) == {"VK2RXX", "VK4RZZ", "VK3ZZA", "VK6ZZB", "VK5ZZC"}
    for rec in out.values():
        values = {v for v in asdict(rec).values() if isinstance(v, str)}
        assert not any("PO Box" in v or "Sample St" in v for v in values)  # no street


def test_header_names_win_over_positions(tmp_path: Path) -> None:
    # Upstream inserts a new column before CALL_SIGN: name lookup must follow it.
    import csv
    import shutil

    for name in ("client.csv", "licence.csv"):
        shutil.copy(HEADERED / name, tmp_path / name)
    rows = list(csv.reader((HEADERED / "device_details.csv").open(newline="")))
    for row in rows:
        row.insert(1, "NEW_COLUMN" if row is rows[0] else "x")
    with (tmp_path / "device_details.csv").open("w", newline="") as fh:
        csv.writer(fh).writerows(rows)

    shutil.copy(HEADERED / "client_type.csv", tmp_path / "client_type.csv")
    assert "VK2RXX" in _by_call(parse_dir(tmp_path))


# --- ACMA licence clause 8: natural persons' Client Information is withheld ---------
# "Where the Licensee is a natural person, this Licence does not permit you to reproduce
# or distribute the Licensee's Client Information contained in the Register in any
# Derivative work." Natural-person status comes from client.CLIENT_TYPE_ID; anything
# that isn't clearly an organisation is treated as a natural person (fail closed).
# All names and addresses below are synthetic.

IDENTIFYING = ("first_name", "last_name", "city", "county", "state", "postal_code", "grid")


def _assert_withheld(rec: Record) -> None:
    for name in IDENTIFYING:
        assert getattr(rec, name) is None, (rec.callsign, name)


def test_person_keeps_callsign_and_licence_fields_but_no_identity() -> None:
    rec = _by_call(parse_dir(HEADERED))["VK3ZZA"]  # CLIENT_TYPE_ID 7 = Person
    _assert_withheld(rec)
    assert rec.license_class == "Advanced"
    assert (rec.grant_date, rec.effective_date, rec.expired_date) == (
        "2022-03-04",
        "2022-03-05",
        "2027-03-03",
    )
    assert rec.applicant_type == "Person"
    assert rec.source == "acma"


def test_company_record_is_fully_populated() -> None:
    rec = _by_call(parse_dir(HEADERED))["VK4RZZ"]  # CLIENT_TYPE_ID 5 = Company
    assert rec.first_name is None
    assert rec.last_name == "Example Repeater Company Pty Ltd"
    assert (rec.city, rec.state, rec.postal_code) == ("EXAMPLETOWN", "QLD", "4000")
    assert rec.license_class == "Amateur Repeater"
    assert (rec.grant_date, rec.expired_date) == ("2021-02-03", "2027-02-02")
    assert rec.applicant_type == "Company"


def test_community_group_type_is_ambiguous_so_withheld() -> None:
    # Type 6 "Community or Volunteer Group" is used for clubs AND, in the real extract,
    # for plenty of individuals, so it can't prove the holder isn't a natural person.
    rec = _by_call(parse_dir(HEADERED))["VK2RXX"]
    _assert_withheld(rec)
    assert rec.license_class == "Amateur Repeater"
    assert rec.applicant_type == "Community or Volunteer Group"


def test_blank_client_type_treated_as_natural_person() -> None:
    _assert_withheld(_by_call(parse_dir(HEADERED))["VK6ZZB"])


def test_unknown_client_type_treated_as_natural_person() -> None:
    _assert_withheld(_by_call(parse_dir(HEADERED))["VK5ZZC"])


def test_headerless_individuals_withheld() -> None:
    out = _by_call(parse_dir(FIXTURES))
    _assert_withheld(out["VK4ABC"])
    _assert_withheld(out["VK6BEA"])


def test_no_person_name_or_place_reaches_any_field() -> None:
    synthetic = {"Alex Example", "Sam Placeholder", "Robin Mockperson", "Alex", "Example"}
    places = {"SAMPLEVILLE", "TESTVILLE", "MOCKTON", "3000", "6000", "5000", "VIC", "WA", "SA"}
    for rec in parse_dir(HEADERED):
        if rec.callsign not in {"VK3ZZA", "VK6ZZB", "VK5ZZC"}:
            continue
        values = {v for v in asdict(rec).values() if isinstance(v, str)}
        assert values.isdisjoint(synthetic | places), rec


def test_withheld_person_gets_no_grid_after_merge_and_geocode() -> None:
    from hamcall_db.geocode import LookupGeocoder
    from hamcall_db.merge import merge

    merged = _by_call(merge([parse_dir(HEADERED)], geocode=LookupGeocoder()))
    for call in ("VK3ZZA", "VK6ZZB", "VK5ZZC", "VK2RXX"):
        _assert_withheld(merged[call])


def _hist(callsign: str, valid_to: str | None, **kw) -> HistoryRow:
    base = dict(
        first_name="Old",
        last_name="Holder",
        city="OLDTOWN",
        state="NSW",
        postal_code="2000",
        grid="QF56",
        source="acma",
    )
    base.update(kw)
    return HistoryRow(callsign=callsign, valid_from="2026-01-01", valid_to=valid_to, **base)


def test_prior_history_scrubbed_for_withheld_and_vanished_acma_calls() -> None:
    current = list(parse_dir(HEADERED))
    prior = [
        _hist("VK3ZZA", None),  # now a withheld person
        _hist("VK9GONE", "2026-06-01"),  # no longer in the extract
        _hist("17737", None),  # junk row from the old AREA_AREA_ID bug
        _hist("W1AW", None, source="fcc"),  # other sources untouched
    ]
    out = {r.callsign: r for r in withhold_prior_history(prior, current)}
    for call in ("VK3ZZA", "VK9GONE", "17737"):
        for name in IDENTIFYING:
            assert getattr(out[call], name) is None, (call, name)
    assert out["W1AW"].last_name == "Holder"


def test_prior_history_kept_for_unchanged_open_org_interval() -> None:
    current = list(parse_dir(HEADERED))
    prior = [
        _hist("VK4RZZ", None, last_name="Example Repeater Company Pty Ltd", first_name=None),
        _hist("VK4RZZ", "2025-01-01", last_name="Example Repeater Company Pty Ltd"),
    ]
    out = withhold_prior_history(prior, current)
    open_iv = next(r for r in out if r.valid_to is None)
    closed = next(r for r in out if r.valid_to is not None)
    assert open_iv.last_name == "Example Repeater Company Pty Ltd"
    assert open_iv.city == "OLDTOWN"
    assert closed.last_name is None  # closed ACMA intervals can't be vetted: withheld


def test_prior_open_interval_of_a_different_holder_is_scrubbed() -> None:
    # Callsign now held by an organisation, but the open interval names someone else.
    current = list(parse_dir(HEADERED))
    out = withhold_prior_history([_hist("VK4RZZ", None)], current)
    assert out[0].last_name is None and out[0].city is None
