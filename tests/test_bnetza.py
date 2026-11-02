"""BNetzA Rufzeichenliste importer (DE).

The fixture PDF is synthetic (invented people; see tests/fixtures/bnetza/make_fixture.py)
but copies the real layout: title page with the edition date, table of contents, page
numbers, list headings, three columns, entries wrapping across lines and pages.
"""

from __future__ import annotations

import importlib.util
import os
from datetime import date
from pathlib import Path

import pytest

from hamcall_db.models import Record
from hamcall_db.sources import bnetza
from hamcall_db.sources.bnetza import (
    CALLSIGN,
    BnetzaSource,
    ParseStats,
    edition_date,
    parse_file,
    parse_pages,
)

FIXTURE = Path(__file__).parent / "fixtures" / "bnetza" / "rufzeichenliste_sample.pdf"
TITLE = "Verzeichnis der zugeteilten\nund ihrer Inhaber (Rufzeichenliste)\nvom 23. Oktober 2026\n"
LIST1 = "1 Liste der personengebundenen Rufzeichen gemäß § 3 Abs. 1 \nAmateurfunkgesetzes \n \n"


@pytest.fixture(scope="module")
def records() -> dict[str, Record]:
    return {r.callsign: r for r in parse_file(FIXTURE)}


def _parse(*pages: str, stats: ParseStats | None = None) -> dict[str, Record]:
    return {r.callsign: r for r in parse_pages([TITLE, *pages], stats=stats)}


def test_fixture_pdf_is_small_and_matches_its_generator() -> None:
    spec = importlib.util.spec_from_file_location(
        "make_fixture", FIXTURE.with_name("make_fixture.py")
    )
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)

    assert FIXTURE.stat().st_size < 20_000
    assert FIXTURE.read_bytes() == generator.build_pdf(generator.PAGES)


def test_every_list_is_kept_and_labelled(records: dict[str, Record]) -> None:
    assert records["DA1AA"].applicant_type == "Individual"
    assert records["DN1AA"].applicant_type == "Training station"
    assert records["DL0AA"].applicant_type == "Club station"
    assert records["DA100XYZ"].applicant_type == "Special event station"
    assert records["DB0XYZ"].applicant_type == "Relay/beacon"
    assert records["DA4ZZZ"].applicant_type == "Experimental station"
    assert all(r.source == "bnetza" for r in records.values())


def test_wrapped_entry_maps_to_record(records: dict[str, Record]) -> None:
    r = records["DA1AA"]
    assert (r.first_name, r.last_name, r.city, r.postal_code, r.license_class) == (
        "Erika",
        "Mustermann",
        "Braunschweig",
        "38100",
        "A",
    )


def test_hyphen_at_line_end_joins_without_space(records: dict[str, Record]) -> None:
    assert records["DB1XY"].city == "Barchfeld-Immelborn"


def test_entry_wrapping_across_a_page_break(records: dict[str, Record]) -> None:
    r = records["DL1ZZ"]
    assert (r.last_name, r.city, r.postal_code) == ("Seitenwechsel", "Leipzig", "04109")


def test_umlauts_and_accents_survive(records: dict[str, Record]) -> None:
    assert (records["DA1AC"].last_name, records["DA1AC"].city) == ("Größer", "München")
    assert records["DC1AB"].first_name == "Ács"
    assert records["DA100XYZ"].last_name == "Jubiläum"


def test_cp850_u_umlaut_leak_is_repaired(records: dict[str, Record]) -> None:
    assert records["DF1ZZ"].city == "München"


def test_stray_control_character_is_deleted() -> None:
    stats = ParseStats()
    r = _parse(LIST1 + "DA1AA, E, Eva Probe; \x7fKuhweg 5, \n41460 Neuss \n", stats=stats)["DA1AA"]
    assert (r.city, r.postal_code) == ("Neuss", "41460")
    assert stats.repaired_characters == 1 and stats.rejected == []


def test_titles_stay_in_first_name_and_the_name_round_trips(records: dict[str, Record]) -> None:
    assert (records["DA1AB"].first_name, records["DA1AB"].last_name) == ("Dr. Max", "Beispiel")
    r = records["DK1AA"]
    assert (r.first_name, r.last_name) == ("Prof. Dr. Karl Theodor", "zu Testberg-Müller")


def test_surname_particles_join_the_surname(records: dict[str, Record]) -> None:
    assert (records["DB1XY"].first_name, records["DB1XY"].last_name) == (
        "Hans-Peter",
        "von der Heide",
    )


@pytest.mark.parametrize(
    ("name", "first", "last"),
    [
        ("Erika Mustermann", "Erika", "Mustermann"),
        ("Mustermann", None, "Mustermann"),
        ("Dr. Mustermann", "Dr.", "Mustermann"),
        ("Anna Müller geb. Schmidt", "Anna", "Müller geb. Schmidt"),
        ("Hans Meier jun.", "Hans", "Meier jun."),
        ("Jan van den Berg", "Jan", "van den Berg"),
        ("Dr. med. Eva Probe", "Dr. med. Eva", "Probe"),
    ],
)
def test_split_name(name: str, first: str | None, last: str) -> None:
    assert bnetza._split_name(name) == (first, last)
    assert " ".join(p for p in (first, last) if p) == name


def test_opted_out_entry_has_callsign_and_name_only(records: dict[str, Record]) -> None:
    r = records["DO1AA"]
    assert (r.first_name, r.last_name, r.license_class) == ("Lisa", "Klein", "E")
    assert (r.city, r.postal_code) == (None, None)


def test_comma_instead_of_semicolon_after_the_name(records: dict[str, Record]) -> None:
    r = records["DD1AA"]
    assert (r.first_name, r.last_name, r.city) == ("Klaus", "Komma", "Berlin")
    assert records["DO1AB"].last_name == "Eins"  # "Dr. Hans Eins,; Zweiweg 2, ..."


def test_city_is_the_holders_town_not_the_station_location(records: dict[str, Record]) -> None:
    assert (records["DL0AA"].city, records["DL0AA"].postal_code) == ("Oelde", "59302")
    assert records["DJ1AA"].city == "Erfurt"


def test_relay_site_label_is_not_part_of_the_name(records: dict[str, Record]) -> None:
    r = records["DB0XYZ"]
    assert (r.first_name, r.last_name) == ("Rolf", "Relais")
    # A 4-digit postcode (leading zero lost, or a foreign holder) gives the town only.
    assert (r.city, r.postal_code) == ("Starkenberg", None)
    sat = records["DP0XYZ"]
    assert (sat.last_name, sat.city, sat.postal_code) == ("Satellit", None, None)


def test_foreign_postcodes(records: dict[str, Record]) -> None:
    assert (records["DG1AB"].city, records["DG1AB"].postal_code) == ("Wien", None)
    assert (records["DG1AA"].city, records["DG1AA"].postal_code) == (None, None)  # Dutch


def test_foreign_holder_city_is_not_taken_from_the_german_station() -> None:
    entry = "DF2AA, A, Max Ausland; Testweg 1, 224-0036 Yokohama, Bergstr. 2, 67346 Speyer \n"
    r = _parse(LIST1 + entry)["DF2AA"]
    assert (r.city, r.postal_code) == (None, None)


def test_street_is_never_stored(records: dict[str, Record]) -> None:
    streets = ("Musterstr", "Hauptstraße", "Weiher", "Kommaweg", "Lindenallee", "Birkenweg")
    for r in records.values():
        for value in (r.first_name, r.last_name, r.city, r.postal_code):
            assert not any(s in (value or "") for s in streets), (r.callsign, value)


def test_city_copying_the_street_is_dropped(records: dict[str, Record]) -> None:
    assert records["DH1AB"].city is None
    assert records["DH1AB"].postal_code == "01067"


def test_duplicate_callsign_keeps_first_entry() -> None:
    stats = ParseStats()
    calls = [r.callsign for r in parse_file(FIXTURE)]
    assert calls.count("DJ1AA") == 1
    list(parse_pages(bnetza.extract_pages(FIXTURE), stats=stats))
    assert stats.duplicates == 1


def test_stats_from_fixture() -> None:
    stats = ParseStats()
    out = list(parse_pages(bnetza.extract_pages(FIXTURE), stats=stats))
    assert stats.edition == "2026-10-23"
    assert stats.rejected == []
    assert stats.repaired_characters == 1
    assert sum(stats.records_by_list.values()) == len(out) == 22
    assert set(stats.records_by_list) == {1, 2, 3, 4, 5, 6}


def test_synced_at_is_the_edition_date(records: dict[str, Record]) -> None:
    assert {r.synced_at for r in records.values()} == {"2026-10-23"}
    assert edition_date(["", "vom 2. März 2027"]) == "2027-03-02"
    assert edition_date(["no date here"]) is None


def test_explicit_synced_at_wins() -> None:
    assert {r.synced_at for r in parse_file(FIXTURE, synced_at="2026-01-01")} == {"2026-01-01"}


@pytest.mark.parametrize(
    "call",
    ["DA1AA", "DL1ABC", "DO9ZZZ", "DN1AAA", "DB0XYZ", "DA100LENZ", "DB1976AD", "DK50K32MR"],
)
def test_callsign_guard_accepts_german_calls(call: str) -> None:
    assert CALLSIGN.match(call)


@pytest.mark.parametrize("call", ["W1AW", "OE1ABC", "DS1ABC", "DL1", "DLABC", "DL1AB1", "D1AB"])
def test_callsign_guard_rejects_others(call: str) -> None:
    assert not CALLSIGN.match(call)


def test_malformed_entry_is_rejected_with_its_continuation_lines() -> None:
    good = "".join(
        f"DA1A{c}, A, Name {c}; Weg 1, 12345 Ort \n" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    )
    page = LIST1 + good * 1 + "OE1ABC, A, Fremder Mensch; Gasse 1, \n1010 Wien \n"
    # Pad with many good entries so one rejection stays under the threshold.
    more = "".join(f"DL{n}AA, A, Name {n}; Weg 1, 12345 Ort \n" for n in range(1, 10))
    more += "".join(f"DO{n}AA, A, Name {n} \n" for n in range(1, 10))
    padding = "".join(
        f"DP{n}{c}{d}, A, Name \n" for n in range(1, 10) for c in "AB" for d in "ABCDEFGHIJ"
    )
    stats = ParseStats()
    out = _parse(page + more + padding, stats=stats)
    assert "OE1ABC" not in out
    assert len(stats.rejected) == 1
    assert all(r.city != "Wien" for r in out.values())


def test_bad_licence_class_is_rejected() -> None:
    stats = ParseStats()
    body = "".join(
        f"DL{n}A{c}, A, Name \n" for n in range(1, 10) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    )
    out = _parse(LIST1 + "DA1AA, B, Name Falsch \n" + body, stats=stats)
    assert "DA1AA" not in out
    assert stats.rejected == ["DA1AA, B, Name Falsch"]


def test_many_malformed_entries_raise() -> None:
    body = "DA1AA, A, Gut \n" + "".join(f"XX{n}AA, A, Schlecht \n" for n in range(1, 10))
    with pytest.raises(ValueError, match="malformed"):
        _parse(LIST1 + body)


def test_continuation_line_without_an_entry_raises() -> None:
    with pytest.raises(ValueError, match="not a Rufzeichenliste entry"):
        _parse(LIST1 + "Weg 1, 12345 Ort \n")


def test_out_of_order_callsigns_raise() -> None:
    with pytest.raises(ValueError, match="mixed up the columns"):
        _parse(LIST1 + "DL1AB, A, Zwei \nDA1AA, A, Eins \n")


def test_lists_out_of_order_raise() -> None:
    list3 = "3 Liste der Rufzeichen ... als Klubstation \n \n"
    with pytest.raises(ValueError, match="list 3 heading after list 1"):
        _parse(LIST1 + "DA1AA, A, Eins \n", list3.replace("...", "für den Betrieb"))


def test_no_list_heading_raises() -> None:
    with pytest.raises(ValueError, match="no Rufzeichenliste list heading"):
        list(parse_pages([TITLE, "DA1AA, A, Eins \n"]))


def test_page_number_only_skipped_as_first_line() -> None:
    page = "Seite 9 \nDA1AA, A, Eva Seite; \nSeite 12, 77815 Bühl \n"
    r = _parse(LIST1, page)["DA1AA"]
    assert (r.city, r.postal_code) == ("Bühl", "77815")


# --- download ---------------------------------------------------------------------


class _FakeFetch:
    def __init__(self, body: bytes | None, last_modified: str | None) -> None:
        self.body, self.last_modified = body, last_modified
        self.calls: list[tuple[str, float | None]] = []

    def __call__(self, url: str, since: float | None) -> tuple[bytes | None, str | None]:
        self.calls.append((url, since))
        return self.body, self.last_modified


def test_download_caches_by_date_and_reads_edition(tmp_path: Path) -> None:
    fetch = _FakeFetch(FIXTURE.read_bytes(), "Fri, 23 Oct 2026 06:44:00 GMT")
    source = BnetzaSource(fetcher=fetch)
    path = source.download(tmp_path, on=date(2026, 11, 1))
    assert path == tmp_path / "data/raw/bnetza/2026-11-01/rufzeichenliste_afu.pdf"
    assert fetch.calls[0][1] is None
    assert source.synced_at == "2026-10-23"
    # The saved file carries the server's Last-Modified, so If-Modified-Since asks about it.
    assert int(path.stat().st_mtime) == 1792737840


def test_download_304_reuses_newest_earlier_copy(tmp_path: Path) -> None:
    old = tmp_path / "data/raw/bnetza/2026-10-25/rufzeichenliste_afu.pdf"
    old.parent.mkdir(parents=True)
    old.write_bytes(FIXTURE.read_bytes())
    os.utime(old, (1792737840, 1792737840))
    fetch = _FakeFetch(None, None)
    source = BnetzaSource(fetcher=fetch)
    path = source.download(tmp_path, on=date(2026, 11, 1))
    assert fetch.calls[0][1] == 1792737840
    assert path.read_bytes() == FIXTURE.read_bytes()
    assert source.synced_at == "2026-10-23"
    assert {r.callsign for r in source.parse(path)} >= {"DA1AA"}


def test_download_304_without_cache_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="304"):
        BnetzaSource(fetcher=_FakeFetch(None, None)).download(tmp_path)
