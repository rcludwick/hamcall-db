"""Tests for the NMHH (Hungary) amateur call sign book importer.

No network: download() is exercised through an injected fetcher. The fixture under
tests/fixtures/nmhh/ keeps the real file's layout byte for byte where it matters: the
Excel 2003 XML (SpreadsheetML) prologue, styles and empty Sheet2/Sheet3 copied from
``call_sign_book.xml`` as served on 2026-11-01, the real bilingual header row, and the
file's own ISO-8859-2 encoding. The data rows are synthetic: this repo is public, so
every name, town and street is made up.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pytest

from hamcall_db.sources.base import Source
from hamcall_db.sources.nmhh import NmhhSource, parse_file, split_name

FIXTURE = Path(__file__).parent / "fixtures" / "nmhh" / "call_sign_book.xml"

# Hungarian amateur calls: HA or HG, a number, then letters (HA1956XY, HG5XRK).
CALLSIGN_RE = re.compile(r"^H[AG][0-9]+[A-Z][A-Z0-9]*$")

# Every street value in the fixture. None may appear in any published field.
STREETS = [
    "Kitalált utca",
    "Próba út",
    "Gyakorló tér",
    "Nincs köz",
    "Álom sor",
    "Kőris utca",
    "Beispielweg",
    "Május 1. utca",
    "Fenyves sor",
]


def _records(**kw):
    return list(parse_file(FIXTURE, **kw))


def _by_call(records=None):
    return {r.callsign: r for r in (records if records is not None else _records())}


def test_source_protocol() -> None:
    src = NmhhSource()
    assert isinstance(src, Source)
    assert src.name == "nmhh"
    assert src.synced_at is None


def test_only_issued_rows_with_valid_callsigns_are_emitted() -> None:
    calls = [r.callsign for r in _records()]
    # HA5XRK is "Ügyintézés alatt" (being processed): not an issued licence, dropped.
    assert "HA5XRK" not in calls
    # XX1XBAD fails the callsign-shape guard.
    assert "XX1XBAD" not in calls
    assert sorted(calls) == sorted(
        [
            "HA0XAA",
            "HA1956XY",
            "HG5XRK",
            "HA3XBB",
            "HA7XCC",
            "HA8XDD",
            "HA9XEE",
            "HA2XFF",
            "HA4XGG",
            "HA1XZZ",
            "HA6XHH",
            "HG0XGW",
        ]
    )


def test_every_callsign_is_hungarian_shaped() -> None:
    for r in _records():
        assert CALLSIGN_RE.match(r.callsign), r.callsign


def test_bad_callsign_is_reported(capsys) -> None:
    _records()
    assert "XX1XBAD" in capsys.readouterr().err


def test_dedupes_by_callsign_keeping_latest_validity() -> None:
    records = _records()
    calls = [r.callsign for r in records]
    assert calls.count("HA6XHH") == 1
    dup = _by_call(records)["HA6XHH"]
    assert dup.expired_date == "2031-12-31"
    assert dup.license_class == "General, Morse"


def test_iso_8859_2_diacritics_decode() -> None:
    # ő and ű exist in ISO-8859-2 but not Latin-1: a wrong decode would give õ / û.
    by = _by_call()
    assert by["HA0XAA"].city == "Őrhegyfalu"
    assert by["HA0XAA"].first_name == "Ödön"
    assert by["HA1956XY"].city == "Tűzköves"
    assert by["HA1956XY"].first_name == "Klára Éva"


def test_field_mapping_for_a_person() -> None:
    r = _by_call()["HA0XAA"]
    assert r.last_name == "Példa"  # Hungarian order: family name first
    assert r.first_name == "Ödön"  # "Dr." dropped
    assert r.city == "Őrhegyfalu"
    assert r.state is None
    assert r.postal_code is None  # NMHH publishes no postal code
    assert r.license_class == "General, Morse"
    assert r.expired_date == "2027-11-30"
    assert r.source == "nmhh"
    assert r.grid is None
    assert r.applicant_type is None


def test_generational_prefix_dropped() -> None:
    r = _by_call()["HA8XDD"]
    assert (r.last_name, r.first_name) == ("Próbás", "Bendegúz")


def test_single_word_name_is_a_given_name() -> None:
    r = _by_call()["HA3XBB"]
    assert (r.first_name, r.last_name) == ("Gyula", None)


def test_empty_name() -> None:
    r = _by_call()["HA7XCC"]
    assert (r.first_name, r.last_name) == (None, None)


def test_club_takes_whole_name_and_hides_responsible_operator() -> None:
    by = _by_call()
    club = by["HG5XRK"]
    assert club.last_name == "Képzelt Rádióklub"
    assert club.first_name is None
    assert club.applicant_type == "Club"
    # The responsible operator is a different person: never published.
    for r in by.values():
        if r.callsign != "HA1XZZ":
            assert "Teszt Elek" not in str(asdict(r))


def test_own_call_with_self_as_responsible_operator_is_a_person() -> None:
    r = _by_call()["HA1XZZ"]
    assert (r.last_name, r.first_name) == ("Teszt", "Elek")
    assert r.applicant_type is None


def test_class_without_morse() -> None:
    assert _by_call()["HA1956XY"].license_class == "Occasional"
    assert _by_call()["HG0XGW"].license_class == "Gate"


def test_city_echoing_street_is_dropped() -> None:
    by = _by_call()
    assert by["HA2XFF"].city is None  # "Május 1. utca" copies the street (has a digit)
    assert by["HA4XGG"].city is None  # "Fenyves sor" equals the street exactly
    assert by["HA4XGG"].last_name == "Fenyves"  # a surname that starts the street survives


def test_no_street_value_reaches_any_field() -> None:
    for r in _records():
        blob = " | ".join(str(v) for v in asdict(r).values())
        for street in STREETS:
            assert street not in blob, (r.callsign, street)


def test_licence_number_and_country_not_published() -> None:
    for r in _records():
        blob = str(asdict(r))
        assert "99901" not in blob
        assert "Hungary" not in blob
        assert r.country is None


def test_synced_at_stamped() -> None:
    assert {r.synced_at for r in _records(synced_at="2026-11-01")} == {"2026-11-01"}


def test_header_change_fails_loudly(tmp_path) -> None:
    raw = FIXTURE.read_bytes().replace("Hívójel / Call sign".encode("iso-8859-2"), b"Foo")
    bad = tmp_path / "bad.xml"
    bad.write_bytes(raw)
    with pytest.raises(ValueError, match="header"):
        _ = list(parse_file(bad))


def test_rejects_doctype(tmp_path) -> None:
    bad = tmp_path / "bad.xml"
    bad.write_bytes(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>')
    with pytest.raises(ValueError, match="DOCTYPE"):
        _ = list(parse_file(bad))


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Példa Ödön", ("Ödön", "Példa")),
        ("Dr. Példa Ödön", ("Ödön", "Példa")),
        ("dr. id. Példa Ödön Béla", ("Ödön Béla", "Példa")),
        ("Kiss-Nagy Anna", ("Anna", "Kiss-Nagy")),
        ("Kovács Jánosné", ("Jánosné", "Kovács")),
        ("Ödön", ("Ödön", None)),
        ("", (None, None)),
        ("Dr.", (None, None)),
    ],
)
def test_split_name(name, expected) -> None:
    assert split_name(name) == expected


# --- download (cache + If-Modified-Since, via injected fetcher) ----------------------


def test_download_caches_under_dated_dir(tmp_path) -> None:
    calls = []

    def fetcher(url, since):
        calls.append((url, since))
        return FIXTURE.read_bytes(), "Sun, 01 Nov 2026 20:00:09 GMT"

    src = NmhhSource(fetcher=fetcher, today=lambda: date(2026, 11, 2))
    path = src.download(tmp_path)
    assert path == tmp_path / "data" / "raw" / "nmhh" / "2026-11-02" / "call_sign_book.xml"
    assert path.read_bytes() == FIXTURE.read_bytes()
    assert calls[0][1] is None  # nothing cached: unconditional GET
    assert src.synced_at == "2026-11-01"  # from Last-Modified


def test_download_reuses_cache_on_not_modified(tmp_path) -> None:
    responses = [(FIXTURE.read_bytes(), None), (None, None)]
    seen = []

    def fetcher(url, since):
        seen.append(since)
        return responses.pop(0)

    src = NmhhSource(fetcher=fetcher, today=lambda: date(2026, 11, 2))
    first = src.download(tmp_path)
    second = src.download(tmp_path)
    assert first == second
    assert seen[1] is not None  # If-Modified-Since sent once a copy is cached
    assert second.read_bytes() == FIXTURE.read_bytes()
    assert src.synced_at is not None  # falls back to the cached file's mtime
