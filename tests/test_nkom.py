"""Tests for the Nkom (Norway) amateur call sign list importer.

No network: download() is exercised only through injected fetchers. The fixture
``tests/fixtures/nkom/kallesignaler.csv`` keeps the real file's layout exactly: the
14-column header copied byte-for-byte from "Liste over norske kallesignaler 5.10.2026.csv",
``;`` delimiters, CRLF line endings, and the file's own encoding, IBM code page 865
(the DOS Nordic code page, where ``ø`` is byte 0x9B). This repo is public, so every
person, organisation and address in it is synthetic; each row reproduces a shape seen
in the real file (a ``v/`` contact on an organisation, a licence held abroad, a
trailing space on a callsign, a no-break space byte 0xFF after a house number, a town
in the expiry column, a fully blank row).
"""

from __future__ import annotations

import re
import urllib.error
from dataclasses import asdict
from pathlib import Path

import pytest

from hamcall_db.address_guard import echoes_street
from hamcall_db.sources import nkom
from hamcall_db.sources.nkom import (
    NkomSource,
    decode,
    edition_date,
    find_csv_link,
    is_norwegian_callsign,
    parse_file,
)

FIXTURES = Path(__file__).parent / "fixtures" / "nkom"
CSV = FIXTURES / "kallesignaler.csv"

# Every street-address value in the fixture (Adresse and Adresse_2). None of these may
# appear, even as a substring, in any field of any parsed Record.
STREET_ADDRESSES = [
    "Fiktivveien 12",
    "Postboks 999",
    "Klubbveien 7",
    "Postboks 3",
    "Industriveien 45",
    "Skolegata 1",
    "Testvägen 123",
    "Sampleveien 21",
    "Ekkogata 15 B",
    "Rarveien 4",
    "Grenseveien 2",
    "Annenveien 99",
    "Feilveien 1",
    "Postboks 1",
]


def _by_call() -> dict:
    return {r.callsign: r for r in parse_file(CSV, synced_at="2026-10-05")}


# --- encoding -------------------------------------------------------------------------


def test_decodes_norwegian_letters_from_cp865() -> None:
    out = _by_call()
    assert out["LA1XYZ"].first_name == "Kåre Ærlig"
    assert out["LA1XYZ"].last_name == "Bjørnstad"
    assert out["LA1XYZ"].city == "NÆRBØ"
    assert out["JW0QQ"].city == "NY-ÅLESUND"
    assert out["JX9TST"].city == "NESØYA"
    assert out["LA1ZZZ"].last_name == "Testby Radioamatørklubb"


def test_bytes_cp1252_rejects_decode_cleanly() -> None:
    # 0x81, 0x8F and 0x9D are undefined in cp1252 (iconv refuses them); in cp865 they
    # are ü, Å and Ø. 0xFF is a no-break space and must not survive as stray whitespace.
    raw = CSV.read_bytes()
    for byte in (b"\x81", b"\x8f", b"\x9d", b"\xff"):
        assert byte in raw
    with pytest.raises(UnicodeDecodeError):
        raw.decode("cp1252")
    out = _by_call()
    assert out["JW0QQ"].last_name == "Bügeltest"
    for record in out.values():
        for value in asdict(record).values():
            if isinstance(value, str):
                assert "�" not in value
                assert "\xa0" not in value
                assert value == value.strip()


def test_decode_picks_encoding_from_header() -> None:
    header = "Kallesignal;Utløpsdato\r\nLA1A;Kåre\r\n"
    assert decode(header.encode("cp865")) == header
    assert decode(header.encode("utf-8")) == header
    assert decode(header.encode("utf-8-sig")) == header
    # A cp1252/Latin-1 export (ø = 0xF8) is recognised too.
    assert decode(header.encode("cp1252")) == header


# --- the street address ---------------------------------------------------------------


def test_street_address_is_never_stored() -> None:
    for record in parse_file(CSV):
        for value in asdict(record).values():
            if not isinstance(value, str):
                continue
            for street in STREET_ADDRESSES:
                assert street.lower() not in value.lower(), (record.callsign, value)


def test_address_guard_drops_city_that_copies_the_street() -> None:
    out = _by_call()
    assert echoes_street("Ekkogata 15 B", ["Ekkogata 15 B"])
    echo = out["LB4ECH"]
    assert echo.city is None
    # The rest of the row is untouched.
    assert (echo.first_name, echo.last_name, echo.postal_code) == ("Kari", "Ekko", "0560")


# --- field mapping --------------------------------------------------------------------


def test_person_row_mapping() -> None:
    rec = _by_call()["LA1XYZ"]
    assert rec.postal_code == "4365"
    assert rec.applicant_type == "Person"
    assert rec.source == "nkom"
    assert rec.synced_at == "2026-10-05"
    assert rec.expired_date is None
    assert rec.license_class is None  # the list carries no class information
    assert rec.grid is None  # no Norwegian geocoder tables
    assert rec.state is None and rec.county is None


def test_organisation_rows_put_the_name_in_last_name() -> None:
    out = _by_call()
    club = out["LA1ZZZ"]
    # The v/ contact person in Fornavn/Etternavn is not published.
    assert (club.first_name, club.last_name) == (None, "Testby Radioamatørklubb")
    assert club.applicant_type == "Organisation"
    assert (club.city, club.postal_code) == ("TRONDHEIM", "7030")
    company = out["LB1TST"]
    assert (company.first_name, company.last_name) == (None, "Eksempel Radio AS")
    assert company.applicant_type == "Company"
    assert company.expired_date == "2028-12-31"
    school = out["LB100TEST"]
    assert school.applicant_type == "School"
    assert school.expired_date == "2025-12-31"
    assert out["LG5XX"].applicant_type == "Organisation"


def test_callsign_only_rows_are_kept() -> None:
    out = _by_call()
    empty = out["LA2EMP"]
    assert (empty.first_name, empty.last_name, empty.city, empty.postal_code) == (
        None,
        None,
        None,
        None,
    )
    assert empty.applicant_type == "Person"
    assert out["LB3NOT"].applicant_type is None


def test_licence_held_abroad_keeps_the_holder_but_not_the_foreign_location() -> None:
    rec = _by_call()["LA9QQQ"]
    assert (rec.first_name, rec.last_name) == ("Sven", "Utlandsen")
    # Foreign postcodes can be street-precise (a UK postcode, a US ZIP+4) and the town
    # column abroad is free text, so neither is published.
    assert rec.city is None and rec.postal_code is None


def test_country_typo_still_counts_as_norway() -> None:
    rec = _by_call()["LA3QQ"]  # trailing space stripped; Land is "Norger"
    assert (rec.city, rec.postal_code) == ("BERGEN", "5003")


def test_only_four_digit_postcodes_are_published_and_bad_dates_are_dropped() -> None:
    rec = _by_call()["LA4BAD"]
    assert rec.postal_code is None  # "622" is not a Norwegian postcode
    assert rec.city == "KRISTIANSAND S"
    assert rec.expired_date is None  # the expiry column held a town name


# --- rows -----------------------------------------------------------------------------


def test_duplicate_callsigns_keep_the_first_row() -> None:
    records = list(parse_file(CSV))
    calls = [r.callsign for r in records]
    assert calls.count("LA1XYZ") == 1
    assert _by_call()["LA1XYZ"].last_name == "Bjørnstad"


def test_rows_and_callsign_shape_guard() -> None:
    calls = {r.callsign for r in parse_file(CSV)}
    assert "XX1ABC" not in calls  # not a Norwegian callsign
    assert "" not in calls  # the blank row
    assert calls == {
        "LA1XYZ",
        "JW0QQ",
        "LA1ZZZ",
        "LB1TST",
        "LB100TEST",
        "LA2EMP",
        "LB3NOT",
        "LA9QQQ",
        "LA3QQ",
        "LB4ECH",
        "LA4BAD",
        "LG5XX",
        "JX9TST",
    }


@pytest.mark.parametrize(
    "call",
    ["LA1A", "LB2ABC", "LJ2T", "LM100IARU", "LA202BV", "JW0L", "JX3P", "3Y0J", "LC1ABCD"],
)
def test_norwegian_callsign_shapes(call: str) -> None:
    assert is_norwegian_callsign(call)


@pytest.mark.parametrize("call", ["", "LO1ABC", "LA", "LAA1", "XX1ABC", "LA1", "SM5ABC", "la1a"])
def test_non_norwegian_callsign_shapes(call: str) -> None:
    assert not is_norwegian_callsign(call)


def test_missing_column_fails_loudly(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_bytes(b"Kallesignal;Fornavn\r\nLA1A;Ola\r\n")
    with pytest.raises(ValueError, match="column"):
        list(parse_file(bad))


# --- link discovery and download ------------------------------------------------------

_ATTACH = "/frekvenser-og-elektronisk-utstyr/radioamator/_/attachment/download"
_FILE = "Liste%20over%20norske%20kallesignaler%205.10.2026"
PAGE = f"""
<a href="https://nrrl.no/tema/konkurranser/bruk-av-lc-prefiks/">LC</a>
<a href="{_ATTACH}/aa:bb/{_FILE}.xlsx">
  Liste over norske kallesignaler 5.10.2026.xlsx</a>
<a href="{_ATTACH}/cc:dd/{_FILE}.csv">
  <span>Liste over norske kallesignaler 5.10.2026.csv</span></a>
"""

CSV_URL = (
    "https://nkom.no/frekvenser-og-elektronisk-utstyr/radioamator/_/attachment/download/"
    "cc:dd/Liste%20over%20norske%20kallesignaler%205.10.2026.csv"
)


def test_find_csv_link_resolves_the_relative_href() -> None:
    url, text = find_csv_link(PAGE)
    assert url == CSV_URL
    assert text == "Liste over norske kallesignaler 5.10.2026.csv"


def test_find_csv_link_fails_when_the_page_changes() -> None:
    with pytest.raises(ValueError, match="kallesignaler"):
        find_csv_link("<a href='/other.csv'>Something else</a>")


def test_edition_date_from_link_text_or_filename() -> None:
    assert edition_date("Liste over norske kallesignaler 5.10.2026.csv") == "2026-10-05"
    assert edition_date("Liste%20over%20norske%20kallesignaler%2012.1.2027.csv") == "2027-01-12"
    assert edition_date("kallesignaler.csv") is None
    assert edition_date("kallesignaler 31.02.2026.csv") is None


def test_download_caches_under_dated_dir_and_sets_synced_at(tmp_path: Path) -> None:
    calls: list[tuple[str, float | None]] = []

    def fetch(url: str, since: float | None) -> tuple[bytes | None, str | None]:
        calls.append((url, since))
        if url == nkom.PAGE_URL:
            return PAGE.encode("utf-8"), None
        return CSV.read_bytes(), "Mon, 05 Oct 2026 08:00:00 GMT"

    source = NkomSource(fetcher=fetch, today="2026-11-01")
    path = source.download(tmp_path)
    assert path == tmp_path / "data" / "raw" / "nkom" / "2026-11-01" / (
        "Liste over norske kallesignaler 5.10.2026.csv"
    )
    assert path.read_bytes() == CSV.read_bytes()
    assert source.synced_at == "2026-10-05"  # edition date from the link
    assert calls == [(nkom.PAGE_URL, None), (CSV_URL, None)]

    # Second run the same day: If-Modified-Since is sent for the cached CSV; a 304
    # (fetcher returns None) reuses it.
    def fetch_304(url: str, since: float | None) -> tuple[bytes | None, str | None]:
        calls.append((url, since))
        return (PAGE.encode("utf-8"), None) if url == nkom.PAGE_URL else (None, None)

    again = NkomSource(fetcher=fetch_304, today="2026-11-01").download(tmp_path)
    assert again == path
    assert calls[-1][0] == CSV_URL and calls[-1][1] is not None
    records = list(NkomSource().parse(path, synced_at="2026-10-05"))
    assert len(records) == 13


def test_download_without_edition_date_falls_back_to_last_modified(tmp_path: Path) -> None:
    page = '<a href="/x/kallesignaler.csv">Liste over norske kallesignaler</a>'

    def fetch(url: str, since: float | None) -> tuple[bytes | None, str | None]:
        if url == nkom.PAGE_URL:
            return page.encode("utf-8"), None
        return CSV.read_bytes(), "Mon, 05 Oct 2026 08:00:00 GMT"

    source = NkomSource(fetcher=fetch, today="2026-11-01")
    source.download(tmp_path)
    assert source.synced_at == "2026-10-05"


def test_download_errors_propagate_for_the_shared_retry(tmp_path: Path) -> None:
    def fetch(url: str, since: float | None) -> tuple[bytes | None, str | None]:
        raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)  # type: ignore[arg-type]

    with pytest.raises(urllib.error.HTTPError):
        NkomSource(fetcher=fetch, today="2026-11-01").download(tmp_path)


def test_registered_in_build_and_precedence() -> None:
    from hamcall_db.build import SOURCES
    from hamcall_db.merge import SOURCE_PRECEDENCE

    assert isinstance(SOURCES["nkom"], NkomSource)
    assert "nkom" in SOURCE_PRECEDENCE
    assert SOURCE_PRECEDENCE.index("nkom") > SOURCE_PRECEDENCE.index("acma")


def test_fixture_rows_are_synthetic_shapes() -> None:
    # Guard against a real file being dropped in as the fixture.
    text = decode(CSV.read_bytes())
    assert len(text.splitlines()) < 30
    assert re.search(r"Bjørnstad|Utlandsen", text)
