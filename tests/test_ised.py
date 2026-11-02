"""Tests for the ISED Canada amateur importer.

No network: download() is not exercised here. We parse a small checked-in slice of the
real ``amateur.txt`` that ISED serves inside ``amateur.zip`` (downloaded 2026-11-01),
under tests/fixtures/ised/. Every line comes from that file, including the report
header, the call-sign count line, and the trailing ``EOF`` line, with the file's own
UTF-8 encoding and CRLF line endings. The one exception: this repo is public, so every
street-address field (personal, columns 80-149, and club, columns 353-422) has been
overwritten with a synthetic address of the same shape, padded to the same width.
``readme_amat.txt`` is the layout description shipped in the same zip, verbatim.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import asdict
from pathlib import Path

import pytest

from hamcall_db.history import HistoryRow, diff_history
from hamcall_db.sources.ised import IsedSource, fsa, parse_dir, parse_file, scrub_prior_history

FIXTURES = Path(__file__).parent / "fixtures" / "ised"
AMATEUR_TXT = FIXTURES / "amateur.txt"

# The 12 data rows in the fixture, in file order.
FIXTURE_CALLS = [
    "VA1AA",
    "VA1AAB",
    "VA1AN",
    "VA1LEN",
    "VA2AA",
    "VA1HRH",
    "VA2RAQ",
    "VE3RAC",
    "VA1RAC",
    "VE2JPV",
    "VY9NF",
    "VA2ZX",
]

# Canadian amateur call: 1-2 letter prefix (VA, VE, VO, VY, CY, ...), one digit, 1-3
# letter suffix. Anything else in the callsign column means the parser misread a line.
CALLSIGN_RE = re.compile(r"^[A-Z]{1,2}[0-9][A-Z]{1,3}$")

# Every street-address value (personal and club) present in the fixture. These are
# synthetic (see the module docstring) but keep the real file's shapes. None of these
# may appear, even as a substring, in any field of any parsed Record.
STREET_ADDRESSES = [
    "100 FAKE DRIVE",
    "*C/0 JANE SAMPLE 1234 HWY 999 RR#3",
    "200 SAMPLE STREET #101",
    "123 FAKE ST / 6",
    "300 TEST DRIVE",
    "400 EXAMPLE WAY",
    "500 PLACEHOLDER RD SUITE 100",
    "600 1er Rue Fictive",
    "700 Nowhere Hill, Sample Building, 10th Floor",
]


def _records(**kw):
    return list(parse_file(AMATEUR_TXT, **kw))


def _by_call(records):
    return {r.callsign: r for r in records}


def test_parses_every_data_row_and_nothing_else() -> None:
    # Header, count line, blank lines and EOF are not rows.
    assert [r.callsign for r in _records()] == FIXTURE_CALLS


def test_every_callsign_is_callsign_shaped() -> None:
    # Guard: a parser that puts a whole fixed-width line (or a report header) into
    # `callsign` must fail here instead of passing silently.
    for r in _records():
        assert CALLSIGN_RE.match(r.callsign), repr(r.callsign)


def test_individual_maps_name_and_location() -> None:
    va1aa = _by_call(_records(synced_at="2026-11-01"))["VA1AA"]
    assert va1aa.first_name == "Bill"
    assert va1aa.last_name == "McFadden"
    assert va1aa.city == "MIDDLE SACKVILLE"
    assert va1aa.state == "NS"  # province maps to state
    assert va1aa.postal_code == "B4E"  # FSA only (hdb-isedfsa)
    assert va1aa.source == "ised"
    assert va1aa.synced_at == "2026-11-01"


def test_accented_names_decode_as_utf8() -> None:
    out = _by_call(_records())
    assert out["VA1LEN"].first_name == "Mylène"
    assert out["VA2AA"].last_name == "Sauvé"


def test_html_numeric_entities_are_decoded() -> None:
    # ISED escapes apostrophes as &#x27; in some fields.
    out = _by_call(_records())
    assert out["VE2JPV"].city == "SAINT-PAUL-DE-L'ILE-AUX-NOIX"
    assert out["VY9NF"].city == "ST. JOHN'S"


def test_unpublished_address_leaves_city_and_postal_empty() -> None:
    # Many holders have no published mailing address: only the province is filled.
    va1aab = _by_call(_records())["VA1AAB"]
    assert va1aab.first_name == "DAVID"
    assert va1aab.last_name == "LEWIS"
    assert va1aab.city is None
    assert va1aab.postal_code is None
    assert va1aab.state == "NS"


def test_placeholder_city_is_null() -> None:
    # Some rows carry "**" or "-" in place of a city (and street address).
    va2zx = _by_call(_records())["VA2ZX"]
    assert va2zx.city is None
    assert va2zx.postal_code is None
    assert va2zx.state == "QC"
    assert va2zx.last_name == "Bergeron"


def test_qualifications_map_to_license_class() -> None:
    out = _by_call(_records())
    # A = Basic, B = 5 wpm, C = 12 wpm, D = Advanced, E = Basic with Honours.
    assert out["VA1AA"].license_class == "Basic, Morse (12 wpm), Advanced"
    assert out["VA1LEN"].license_class == "Basic"
    assert out["VA1AN"].license_class == "Basic with Honours"
    assert out["VA1AAB"].license_class == "Basic with Honours, Advanced"


def test_club_call_uses_club_name_and_club_location() -> None:
    # A club call's holder is the club; the trustee's name and home location are not
    # the call's identity. Club name field 2 (often "c/o <person>") is not published.
    out = _by_call(_records())
    ve3rac = out["VE3RAC"]
    assert ve3rac.first_name is None
    assert ve3rac.last_name == "RADIO AMATEUR DU / OF CANADA INC."
    assert ve3rac.city == "OTTAWA"
    assert ve3rac.state == "ON"
    assert ve3rac.postal_code == "K1G"  # FSA only (hdb-isedfsa)
    assert ve3rac.license_class == "Basic, Morse (12 wpm), Advanced"

    va1hrh = out["VA1HRH"]
    assert va1hrh.last_name == "Handiradioham Amateur Radio Club of Canada"
    assert va1hrh.first_name is None
    assert va1hrh.city == "NEW MINAS"


def test_club_without_trustee_address_still_gets_club_location() -> None:
    va1rac = _by_call(_records())["VA1RAC"]
    assert va1rac.last_name == "RADIO AMATEUR DU / OF CANADA INC."
    assert va1rac.city == "OTTAWA"
    assert va1rac.state == "ON"


def test_street_address_never_appears_in_output() -> None:
    # The redistribution contract: no street address in any Record field, ever —
    # not as a whole value and not embedded inside a longer value.
    records = _records()
    assert records
    for r in records:
        for value in asdict(r).values():
            if isinstance(value, str):
                for street in STREET_ADDRESSES:
                    assert street not in value, (r.callsign, value)


def test_no_field_holds_a_whole_raw_line() -> None:
    # Every published string field fits inside the widest single fixed-width column.
    for r in _records():
        for name, value in asdict(r).items():
            if isinstance(value, str):
                assert len(value) <= 70, (r.callsign, name, value)
                assert "  " not in value, (r.callsign, name, value)


def test_county_country_dxcc_grid_left_for_downstream() -> None:
    va1aa = _by_call(_records())["VA1AA"]
    assert va1aa.county is None  # US-only concept
    assert va1aa.country is None  # cty.dat enriches downstream
    assert va1aa.dxcc is None
    assert va1aa.grid is None  # geocoded downstream


def test_lf_line_endings_parse_the_same(tmp_path: Path) -> None:
    lf = tmp_path / "amateur.txt"
    lf.write_bytes(AMATEUR_TXT.read_bytes().replace(b"\r\n", b"\n"))
    assert [asdict(r) for r in parse_file(lf)] == [asdict(r) for r in _records()]


def test_unrecognised_line_raises(tmp_path: Path) -> None:
    # A format change must fail the build loudly, not publish garbage rows.
    bad = tmp_path / "amateur.txt"
    bad.write_text("VA3ABC;;A;;;A;John;Smith;;123 Maple Ave;Toronto;ON;M5V 2T6;Canada;\n")
    with pytest.raises(ValueError, match="line 1"):
        list(parse_file(bad))


def test_parse_dir_finds_amateur_txt(tmp_path: Path) -> None:
    shutil.copy(AMATEUR_TXT, tmp_path / "AMATEUR.TXT")
    assert [r.callsign for r in parse_dir(tmp_path)] == FIXTURE_CALLS
    assert [r.callsign for r in parse_dir(FIXTURES)] == FIXTURE_CALLS


def test_source_parse_method_delegates_to_parse_dir() -> None:
    out = _by_call(IsedSource().parse(FIXTURES, synced_at="2026-11-01"))
    assert out["VA1AA"].callsign == "VA1AA"
    assert IsedSource().name == "ised"


def test_synced_at_defaults_to_none_before_download() -> None:
    assert IsedSource().synced_at is None


def test_parse_uses_explicit_synced_at_over_self() -> None:
    src = IsedSource()
    src.synced_at = "2026-01-01"
    out = _by_call(src.parse(FIXTURES, synced_at="2026-11-01"))
    assert out["VA1AA"].synced_at == "2026-11-01"


def test_parse_falls_back_to_self_synced_at() -> None:
    src = IsedSource()
    src.synced_at = "2026-06-10"  # as download() would have set it
    out = _by_call(src.parse(FIXTURES))
    assert out["VA1AA"].synced_at == "2026-06-10"


def test_download_url_is_https_hdb_f363():
    # Root cause of the 2026-06-16 CI failure: http:// port 80 timed out from the
    # GitHub runner; https works. Guard against regressing to plaintext http.
    from hamcall_db.sources.ised import DOWNLOAD_URL

    assert DOWNLOAD_URL.startswith("https://")


# --- street address typed into a published field (hdb-addrguard) ----------------
# Synthetic addresses only: the repo is public.


def _ised_row(
    call: str,
    *,
    given: str = "",
    surname: str = "",
    street: str = "",
    city: str = "",
    province: str = "ON",
    postal: str = "",
    club: str = "",
    club_street: str = "",
    club_city: str = "",
    club_postal: str = "",
) -> str:
    """Build one fixed-width amateur.txt row at readme_amat.txt's 1-based columns."""
    cols = [
        (1, 6, call),
        (8, 35, given),
        (44, 35, surname),
        (80, 70, street),
        (151, 35, city),
        (187, 2, province),
        (190, 10, postal),
        (201, 1, "A"),
    ]
    width = 210
    if club:
        cols += [
            (211, 70, club),
            (353, 70, club_street),
            (424, 35, club_city),
            (460, 2, province),
            (463, 7, club_postal),
        ]
        width = 469
    line = [" "] * width
    for start, length, value in cols:
        line[start - 1 : start - 1 + length] = value.ljust(length)[:length]
    return "".join(line)


def _parse_rows(tmp_path: Path, *rows: str):
    path = tmp_path / "amateur.txt"
    path.write_text("\r\n".join([*rows, "EOF"]) + "\r\n", encoding="utf-8")
    return _by_call(parse_file(path))


def test_city_holding_the_rows_street_address_is_nulled(tmp_path: Path) -> None:
    out = _parse_rows(
        tmp_path,
        _ised_row(
            "VA3ZZA", given="Jane", surname="Sample", street="100 FAKE RD", city="100 FAKE RD"
        ),
    )
    assert out["VA3ZZA"].city is None
    assert out["VA3ZZA"].last_name == "Sample"
    assert out["VA3ZZA"].state == "ON"


def test_club_name_holding_a_street_address_is_nulled(tmp_path: Path) -> None:
    # The trustee typed the address into the club-name field.
    out = _parse_rows(
        tmp_path,
        _ised_row(
            "VE3ZZB",
            given="John",
            surname="Example",
            street="1305 PLACEHOLDER GREEN",
            club="1305 PLACEHOLDER GREEN",
            club_city="OTTAWA",
        ),
    )
    assert out["VE3ZZB"].last_name is None
    assert out["VE3ZZB"].first_name is None
    assert out["VE3ZZB"].city == "OTTAWA"


def test_club_city_matching_the_club_street_is_nulled(tmp_path: Path) -> None:
    out = _parse_rows(
        tmp_path,
        _ised_row(
            "VE3ZZC",
            club="SAMPLE AMATEUR RADIO CLUB",
            club_street="9 EXAMPLE CRES",
            club_city="9 EXAMPLE CRES",
        ),
    )
    assert out["VE3ZZC"].last_name == "SAMPLE AMATEUR RADIO CLUB"
    assert out["VE3ZZC"].city is None


def test_town_typed_as_street_keeps_city(tmp_path: Path) -> None:
    # Rural filers often put the town in the street column; that's not an address.
    out = _parse_rows(
        tmp_path,
        _ised_row("VA1ZZD", street="INGONISH BEACH", city="INGONISH BEACH", province="NS"),
        _ised_row("VA7ZZE", street="PO BOX 1", city="100 MILE HOUSE", province="BC"),
    )
    assert out["VA1ZZD"].city == "INGONISH BEACH"
    assert out["VA7ZZE"].city == "100 MILE HOUSE"


# --- postal code published as the 3-character FSA only (hdb-isedfsa) ---------------
# Synthetic codes and names only: the repo is public.


def test_personal_postal_code_is_truncated_to_fsa(tmp_path: Path) -> None:
    out = _parse_rows(
        tmp_path,
        _ised_row("VA3ZZF", given="Jane", surname="Sample", city="OTTAWA", postal="X1X 1X1"),
    )
    assert out["VA3ZZF"].postal_code == "X1X"


def test_club_postal_code_is_truncated_to_fsa(tmp_path: Path) -> None:
    out = _parse_rows(
        tmp_path,
        _ised_row(
            "VE3ZZG",
            given="John",
            surname="Example",
            postal="X2X 2X2",
            club="SAMPLE AMATEUR RADIO CLUB",
            club_city="OTTAWA",
            club_postal="X3X 3X3",
        ),
    )
    # The club's code, not the trustee's, and only its FSA.
    assert out["VE3ZZG"].postal_code == "X3X"


def test_malformed_postal_code_is_null(tmp_path: Path) -> None:
    out = _parse_rows(
        tmp_path,
        _ised_row("VA3ZZH", surname="Sample", postal="12345"),
        _ised_row("VA3ZZI", surname="Sample", postal="USA 1X1"),
    )
    assert out["VA3ZZH"].postal_code is None
    assert out["VA3ZZI"].postal_code is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("X1X 1X1", "X1X"),
        ("x1x1x1", "X1X"),
        ("X1X-1X1", "X1X"),
        (" X1X ", "X1X"),
        ("X1X", "X1X"),
        ("X1X 1X", "X1X"),  # typo in the unpublished tail: the FSA is still real
        ("X1X 1XX", "X1X"),
        ("1X1 X1X", None),
        ("XX1 1X1", None),
        ("06111", None),
        ("", None),
        (None, None),
    ],
)
def test_fsa(raw: str | None, expected: str | None) -> None:
    assert fsa(raw) == expected


def test_address_guard_still_works_with_postal_code(tmp_path: Path) -> None:
    out = _parse_rows(
        tmp_path,
        _ised_row(
            "VA3ZZJ", surname="Sample", street="100 FAKE RD", city="100 FAKE RD", postal="X1X 1X1"
        ),
    )
    assert out["VA3ZZJ"].city is None
    assert out["VA3ZZJ"].postal_code == "X1X"


def _hist(call: str, postal: str | None, *, valid_to: str | None = None, source="ised"):
    return HistoryRow(
        callsign=call,
        valid_from="2026-01-01",
        valid_to=valid_to,
        last_name="Sample",
        postal_code=postal,
        grid="FN25",
        source=source,
    )


def test_scrub_prior_history_truncates_every_ised_row() -> None:
    prior = [
        _hist("VA3ZZK", "X1X 1X1"),  # open
        _hist("VA3ZZL", "X2X 2X2", valid_to="2026-06-01"),  # closed
        _hist("VA3ZZM", "not a code"),
        _hist("VA3ZZN", "X3X"),  # already truncated
        _hist("W1ZZZ", "06111-1234", source="fcc"),  # other source: untouched
    ]
    out = {r.callsign: r for r in scrub_prior_history(prior)}
    assert out["VA3ZZK"].postal_code == "X1X"
    assert out["VA3ZZL"].postal_code == "X2X"
    assert out["VA3ZZL"].valid_to == "2026-06-01"
    assert out["VA3ZZM"].postal_code is None
    assert out["VA3ZZN"].postal_code == "X3X"
    assert out["W1ZZZ"].postal_code == "06111-1234"


def test_scrubbed_prior_history_does_not_churn_unchanged_rows(tmp_path: Path) -> None:
    from hamcall_db.models import Record

    current = [
        Record(callsign="VA3ZZK", last_name="Sample", postal_code="X1X", grid="FN25", source="ised")
    ]
    prior = [_hist("VA3ZZK", "X1X 1X1")]  # an older release's full code
    night1 = diff_history(scrub_prior_history(prior), current, as_of="2026-11-02")
    night2 = diff_history(scrub_prior_history(night1), current, as_of="2026-11-03")
    for history in (night1, night2):
        assert len(history) == 1
        assert history[0].valid_from == "2026-01-01"
        assert history[0].valid_to is None
        assert history[0].postal_code == "X1X"
