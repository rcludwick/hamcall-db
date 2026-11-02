"""Tests for the UKE (Poland) amateur permit importer.

No network: download() is exercised through an injected fetcher. The fixtures under
tests/fixtures/uke/ copy the real layout of UKE's four CSV exports as served on
2026-11-02 (exact header rows, ``;`` delimiter, LF line endings, cp1250 encoding, ``""``
for empty quoted fields, doubled quotes inside values). Every row is synthetic: this
repo is public, so the clubs, towns and streets are made up.
"""

from __future__ import annotations

import http.client
import re
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pytest

from hamcall_db.sources.base import Source, is_transient
from hamcall_db.sources.uke import LISTS, UkeSource, parse_dir, town_only

FIXTURES = Path(__file__).parent / "fixtures" / "uke"

# Poland's ITU blocks: SN-SR, 3Z, HF. SR is used for unattended stations (repeaters).
CALLSIGN_RE = re.compile(r"^(S[N-R]|3Z|HF)[0-9]+[A-Z][A-Z0-9]*$")

# Street values in the fixtures (with their house numbers). None may be published.
STREETS = [
    "Fikcyjna",
    "Ogrodowa Próbna",
    "Wymyślona",
    "Nibylandzka",
    "Zmyślona 16",
    "Wilków Wymyślonych",
    "OSINOWA TESTOWA",
    "Przemysłowa Próbna",
    "Górska Próbna",
]


def _records(**kw):
    return list(parse_dir(FIXTURES, **kw))


def _by_call(records=None):
    return {r.callsign: r for r in (records if records is not None else _records())}


def test_source_protocol() -> None:
    src = UkeSource()
    assert isinstance(src, Source)
    assert src.name == "uke"
    assert src.synced_at is None


def test_parses_all_four_lists() -> None:
    assert sorted(r.callsign for r in _records()) == sorted(
        [
            # individuals
            "SP4XAA",
            "SQ9XBB",
            "SO5XCC",
            "SN0XDD",
            "HF0XEE",
            "SP7XFF",
            "3Z6XGG",
            "SQ2XHH",
            "SP0XDG",
            # clubs
            "SP2XKA",
            "SP2XKB",
            "SP5XKC",
            "SN0XKD",
            "SP3XKE",
            "SP9XKF",
            # unattended stations
            "SR9XAA",
            "SR8XAB",
            "SR6XBB",
            "SR7XBC",
        ]
    )


def test_every_callsign_is_polish_shaped(capsys) -> None:
    for r in _records():
        assert CALLSIGN_RE.match(r.callsign), r.callsign
    assert "XX1XBAD" in capsys.readouterr().err


def test_dedupe_keeps_latest_validity() -> None:
    records = _records()
    calls = [r.callsign for r in records]
    assert len(calls) == len(set(calls))
    by = _by_call(records)
    assert by["SP4XAA"].expired_date == "2036-12-20"  # renewal beats the old permit
    # SP0XDG is in both the individual (2027) and club (2029) list: latest wins.
    assert by["SP0XDG"].expired_date == "2029-01-01"
    assert by["SP0XDG"].last_name == "Klub Podwójny"


def test_cp1250_diacritics_decode() -> None:
    by = _by_call()
    # ś, ą, Ś and ź have different bytes in cp1250 and ISO-8859-2.
    assert by["SQ9XBB"].city == "Ząbkowo Śląskie"
    assert by["SO5XCC"].city == "Źrebiec"
    assert by["SP2XKA"].last_name == "Stowarzyszenie Przykładowych Krótkofalowców"
    assert by["SP2XKA"].city == "ŚWIĘTOWO DOLNE"  # ALL CAPS is re-cased in merge


def test_individual_mapping() -> None:
    r = _by_call()["SQ9XBB"]
    assert r.first_name is None and r.last_name is None  # UKE publishes no names
    assert r.license_class == "Category 3"
    assert r.expired_date == "2030-05-24"
    assert r.applicant_type == "Individual"
    assert r.postal_code is None
    assert r.state is None
    assert r.grid is None
    assert r.source == "uke"
    assert _by_call()["SO5XCC"].license_class == "Additional"
    assert _by_call()["SP4XAA"].license_class == "Category 1"


def test_expired_permits_are_kept_with_their_date() -> None:
    assert _by_call()["SQ2XHH"].expired_date == "2025-01-01"


def test_empty_and_quoted_towns() -> None:
    by = _by_call()
    assert by["SN0XDD"].city is None
    assert by["3Z6XGG"].city == 'Huta Zmyślona "B"'


def test_house_number_in_town_is_cut() -> None:
    by = _by_call()
    assert by["SP7XFF"].city == "Wólka Próbna"  # "Wólka Próbna 30"
    assert by["SN0XKD"].city == "Pogórze"  # "Pogórze ul. Zmyślona 16 B"
    assert by["SR7XBC"].city == "Gliczarówek Górny"  # "Gliczarówek Górny 158"


def test_coordinates_in_town_field_are_dropped() -> None:
    assert _by_call()["SP5XKC"].city is None  # 52°06'05,4" 21°15'47,4"


def test_club_mapping() -> None:
    by = _by_call()
    kb = by["SP2XKB"]
    assert kb.last_name == "Ogólnopolski Związek Testowy"  # applicant, no end user
    assert kb.first_name is None
    assert kb.city == "Gdańsk"  # the station's town
    assert kb.applicant_type == "Club"
    # The end user (the local club) wins over the applicant when present.
    assert by["SP3XKE"].last_name == 'HARCERSKI KLUB ŁĄCZNOŚCI "PRÓBA"  SP3XKE'


def test_village_address_keeps_town() -> None:
    # Polish village addresses repeat the town as the street ("Długa Wieś Zmyślona 12").
    assert _by_call()["SP3XKE"].city == "Długa Wieś Zmyślona"


def test_name_echoing_street_is_dropped() -> None:
    assert _by_call()["SP9XKF"].last_name is None  # end user name "ul. Wymyślona 3"


def test_coordinates_become_a_four_char_grid_only() -> None:
    by = _by_call()
    assert by["SP2XKB"].grid == "KO02"  # 52.2297, 21.0122
    assert by["SP5XKC"].grid == "JO82"  # 52,4064 / 16,9252 (decimal comma)
    for r in _records():
        assert r.grid is None or re.fullmatch(r"[A-R]{2}[0-9]{2}", r.grid)
        blob = str(asdict(r))
        for coord in ("52.2297", "21.0122", "52,4064", "16,9252", "52°"):
            assert coord not in blob


def test_unattended_stations() -> None:
    by = _by_call()
    rep = by["SR9XAA"]
    assert rep.license_class == "Category 5"
    assert rep.applicant_type == "Unattended station"
    assert rep.city == "Przykładowo"
    assert rep.last_name is None
    club_rep = by["SR6XBB"]
    assert club_rep.last_name == "Klub Testowy Przemienników"
    assert club_rep.city == "Górka Testowa"
    assert by["SR8XAB"].city == 'Górka - Obiekt RTCN "Próbny"'


def test_no_street_value_reaches_any_field() -> None:
    for r in _records():
        blob = " | ".join(str(v) for v in asdict(r).values())
        for street in STREETS:
            assert street not in blob, (r.callsign, street)
        assert "ul." not in blob


def test_permit_numbers_and_power_not_published() -> None:
    for r in _records():
        blob = str(asdict(r))
        assert "DC.WML" not in blob and "/K/X/" not in blob


def test_synced_at_stamped() -> None:
    assert {r.synced_at for r in _records(synced_at="2026-11-02")} == {"2026-11-02"}


def test_header_change_fails_loudly(tmp_path) -> None:
    for name in LISTS:
        (tmp_path / name).write_bytes((FIXTURES / name).read_bytes())
    raw = (tmp_path / "individuals.csv").read_bytes().replace(b"call_sign", b"znak")
    (tmp_path / "individuals.csv").write_bytes(raw)
    with pytest.raises(ValueError, match="call_sign"):
        _ = list(parse_dir(tmp_path))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Białystok", "Białystok"),
        ("Wólka Próbna 30", "Wólka Próbna"),
        ("Pogórze ul. Zmyślona 16 B", "Pogórze"),
        ("Szczyrk al. Testowa", "Szczyrk"),
        ("52°06’05,4”  21°15’47,4”", None),
        ("ul. Fikcyjna 7", None),
        ("", None),
        (None, None),
        ("Samoklęski-Kolonia Pierwsza", "Samoklęski-Kolonia Pierwsza"),
    ],
)
def test_town_only(value, expected) -> None:
    assert town_only(value) == expected


# --- download (dated cache + completeness check, via injected fetcher) ---------------


def _fixture_fetcher(totals: dict[str, int] | None = None, calls: list | None = None):
    """Serve the fixture CSVs and a datatables-style row count for each list."""

    def fetch(url: str) -> bytes:
        if calls is not None:
            calls.append(url)
        for name in LISTS:
            stem = name.removesuffix(".csv")
            if url.endswith(f"/{stem}/export.csv"):
                return (FIXTURES / name).read_bytes()
            if f"/{stem}.json" in url:
                lines = (FIXTURES / name).read_bytes().count(b"\n") - 1
                total = (totals or {}).get(stem, lines)
                return f'{{"recordsTotal":{total},"data":[]}}'.encode()
        raise AssertionError(url)

    return fetch


def test_download_caches_all_lists_under_dated_dir(tmp_path) -> None:
    src = UkeSource(fetcher=_fixture_fetcher(), today=lambda: date(2026, 11, 2))
    path = src.download(tmp_path)
    assert path == tmp_path / "data" / "raw" / "uke" / "2026-11-02"
    for name in LISTS:
        assert (path / name).read_bytes() == (FIXTURES / name).read_bytes()
    assert src.synced_at is not None
    assert len(list(parse_dir(path))) == len(_records())


def test_download_reuses_todays_cache(tmp_path) -> None:
    calls: list[str] = []
    src = UkeSource(fetcher=_fixture_fetcher(calls=calls), today=lambda: date(2026, 11, 2))
    src.download(tmp_path)
    first = len(calls)
    src.download(tmp_path)
    assert len(calls) == first  # UKE has no Last-Modified: today's copy is reused


def test_truncated_export_is_transient_and_not_cached(tmp_path) -> None:
    # UKE's export sometimes ends early on a clean line boundary (seen 2026-11-02): the
    # CSV has fewer rows than the site's own row count.
    src = UkeSource(
        fetcher=_fixture_fetcher(totals={"individuals": 5000}),
        today=lambda: date(2026, 11, 2),
    )
    with pytest.raises(http.client.IncompleteRead) as info:
        src.download(tmp_path)
    assert is_transient(info.value)  # so the build's shared retry tries again
    assert not (tmp_path / "data" / "raw" / "uke" / "2026-11-02" / "individuals.csv").exists()
