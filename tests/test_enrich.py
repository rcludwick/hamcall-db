"""Tests for AD1C cty.dat -> DXCC enrichment (au-9ed1).

Pure, offline. Everything resolves from the small checked-in cty.dat fixture under
tests/fixtures/cty/ plus the bundled primary-prefix -> ADIF-number seed table. No
network.

The fixture is hand-built to exercise the resolution rules:
  * United States carries an exact full-call override ``=W1AW`` (and ``=K7RA``).
  * "K" (United States) vs "KH6"/"KL" (Hawaii/Alaska) and "VE7" overlap so a
    callsign like "KH6AA" must pick the LONGEST matching prefix, not "K".
  * Tokens carry overrides — ``=W1AW(05)[08]``, ``KH6<21.12/157.48>``,
    ``CY{NA}`` — which the loader strips for matching.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hamcall_db.enrich import (
    CtyLookup,
    enrich,
    enrich_record,
    load_cty,
)
from hamcall_db.models import Record

FIXTURE = Path(__file__).parent / "fixtures" / "cty" / "cty.dat"


@pytest.fixture(scope="module")
def lookup() -> CtyLookup:
    return load_cty(FIXTURE)


# --- loader --------------------------------------------------------------------


def test_load_cty_returns_lookup(lookup: CtyLookup) -> None:
    assert isinstance(lookup, CtyLookup)


def test_loader_collects_all_entities(lookup: CtyLookup) -> None:
    names = lookup.entity_names()
    assert {
        "United States",
        "Hawaii",
        "Alaska",
        "Canada",
        "Argentina",
    } <= names


def test_loader_strips_override_annotations(lookup: CtyLookup) -> None:
    # KH6<21.12/157.48> must be stored as the bare prefix "KH6".
    entity, _num = lookup.resolve("KH6")
    assert entity == "Hawaii"


# --- exact full-call override --------------------------------------------------


def test_exact_callsign_override_wins_over_prefix(lookup: CtyLookup) -> None:
    # W1AW is a "=" exact override under United States. By prefix alone "W" -> US
    # too here, but the exact path must be taken regardless.
    entity, num = lookup.resolve("W1AW")
    assert entity == "United States"
    assert num == 291


def test_exact_override_is_full_call_not_prefix(lookup: CtyLookup) -> None:
    # "=K7RA" is exact: K7RA matches exactly, but K7RAX should fall to prefix "K".
    assert lookup.resolve("K7RA")[0] == "United States"
    assert lookup.resolve("K7RAX")[0] == "United States"  # via prefix "K"


# --- longest-prefix matching ---------------------------------------------------


def test_longest_prefix_wins(lookup: CtyLookup) -> None:
    # "KH6AA": prefixes "K" (US) and "KH6" (Hawaii) both match; longest -> Hawaii.
    entity, num = lookup.resolve("KH6AA")
    assert entity == "Hawaii"
    assert num == 110


def test_shorter_prefix_still_matches_when_no_longer(lookup: CtyLookup) -> None:
    # "K1ABC": only "K" matches -> United States.
    assert lookup.resolve("K1ABC")[0] == "United States"


def test_alaska_longest_prefix(lookup: CtyLookup) -> None:
    entity, num = lookup.resolve("KL7XYZ")
    assert entity == "Alaska"
    assert num == 6


def test_unknown_callsign_resolves_to_none(lookup: CtyLookup) -> None:
    assert lookup.resolve("3Y0XX") == (None, None)


def test_resolve_is_case_insensitive(lookup: CtyLookup) -> None:
    assert lookup.resolve("kh6aa")[0] == "Hawaii"


# --- DXCC number ---------------------------------------------------------------


def test_dxcc_number_from_bundled_table(lookup: CtyLookup) -> None:
    assert lookup.resolve("VE3ABC") == ("Canada", 1)
    assert lookup.resolve("LU1AA") == ("Argentina", 100)


# --- enrich_record (pure Record -> Record) -------------------------------------


def test_enrich_record_sets_country_and_dxcc(lookup: CtyLookup) -> None:
    rec = Record(callsign="KH6AA", source="fcc")
    out = enrich_record(rec, lookup)
    assert out.country == "Hawaii"
    assert out.dxcc == 110


def test_enrich_record_does_not_mutate_input(lookup: CtyLookup) -> None:
    rec = Record(callsign="KH6AA", source="fcc")
    enrich_record(rec, lookup)
    assert rec.country is None
    assert rec.dxcc is None


def test_enrich_record_returns_new_object(lookup: CtyLookup) -> None:
    rec = Record(callsign="KH6AA")
    out = enrich_record(rec, lookup)
    assert out is not rec


def test_enrich_record_unknown_leaves_fields_none(lookup: CtyLookup) -> None:
    rec = Record(callsign="3Y0XX")
    out = enrich_record(rec, lookup)
    assert out.country is None
    assert out.dxcc is None


def test_enrich_record_does_not_clobber_other_fields(lookup: CtyLookup) -> None:
    rec = Record(callsign="KH6AA", city="Hilo", source="fcc", license_class="E")
    out = enrich_record(rec, lookup)
    assert out.city == "Hilo"
    assert out.source == "fcc"
    assert out.license_class == "E"


def test_enrich_record_uppercases_callsign_for_match(lookup: CtyLookup) -> None:
    rec = Record(callsign="kh6aa")
    out = enrich_record(rec, lookup)
    assert out.country == "Hawaii"


# --- enrich (iterable -> iterator) ---------------------------------------------


def test_enrich_stream(lookup: CtyLookup) -> None:
    recs = [
        Record(callsign="W1AW"),
        Record(callsign="VE3ABC"),
        Record(callsign="3Y0XX"),
    ]
    out = list(enrich(recs, lookup))
    assert [(r.country, r.dxcc) for r in out] == [
        ("United States", 291),
        ("Canada", 1),
        (None, None),
    ]


# --- public callable shape: CtyLookup is itself a Record -> Record enricher -----


def test_lookup_is_callable_as_enricher(lookup: CtyLookup) -> None:
    out = lookup(Record(callsign="LU1AA"))
    assert (out.country, out.dxcc) == ("Argentina", 100)
