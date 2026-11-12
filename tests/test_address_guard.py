"""Tests for the street-address guard (hamcall_db.address_guard).

Every address here is synthetic: this repo is public, so no real person's address may
be committed. The shapes mirror what filers actually type into the wrong field.
"""

from __future__ import annotations

import pytest

from hamcall_db.address_guard import (
    echoes_street,
    location_has_street_address,
    looks_like_street_address,
)

# --- echoes_street: the importer-layer comparison against the row's own address ---


@pytest.mark.parametrize(
    ("value", "street"),
    [
        ("100 Fake Rd", "100 FAKE RD"),  # exact copy, different case
        ("200 Sample Ave", "200 Sample Ave"),
        ("300 Test Ct. Somewhere", "300 Test Ct."),  # street plus a city fragment
        ("400 Example Water Cov", "400 EXAMPLE WATER COVE"),  # truncated (FCC city=20)
        ("12345 Nowhere PL NW", "12345 Nowhere Pl NW"),
        ("1305 Placeholder Green", "1305 PLACEHOLDER GREEN"),  # club name = address
    ],
)
def test_echoes_street_matches_copies_of_the_row_address(value: str, street: str) -> None:
    assert echoes_street(value, [street])


@pytest.mark.parametrize(
    ("value", "street"),
    [
        ("Pawleys Island", "PAWLEYS ISLAND"),  # town typed as street: not an address
        ("29 Palms", "29 PALMS HWY"),  # short prefix: a place name can start a street
        ("Newington", "225 Fake St"),
        ("Smith", "SMITH"),
        ("Simon Fraser University School", "SIMON FRASER UNIVERSITY"),
        ("1100 Fake Rd", "100 FAKE RD"),  # different house number, not a copy
        (None, "100 FAKE RD"),
        ("100 Fake Rd", None),
    ],
)
def test_echoes_street_ignores_non_address_overlaps(value, street) -> None:
    assert not echoes_street(value, [street])


def test_echoes_street_checks_every_candidate() -> None:
    assert echoes_street("9 Sample Ln", [None, "PO BOX 1", "9 SAMPLE LN"])


# --- looks_like_street_address: the normalize-layer backstop ---------------------


@pytest.mark.parametrize(
    "value",
    [
        "100 Fake Rd",
        "200 SAMPLE STREET",
        "233B Example Street",
        "12345 Nowhere Pl NW",
        "300 Test Ct. Somewhere",
        "400 Example Water Cov",
        "500 Placeholder Ave #101",
        "600 Fictive Blvd",
        "700 Sample Drive",
        "800 Example Lane",
        "900 Fake Way",
        "1000 Sample Cres",
        "1100 Test Hwy 7",
        "1200 rue Fictive",
        "1300 Chemin du Faux",
        "1400 Boul. Exemple",
        "1500 Boulevard Exemple",
        "292nd St S",
        "12-34 Fake Road",
    ],
)
def test_backstop_flags_house_number_plus_street_type(value: str) -> None:
    assert looks_like_street_address(value)


@pytest.mark.parametrize(
    "value",
    [
        "29 Palms",
        "1000 Palms",
        "100 Mile House",
        "108 Mile Ranch",
        "150 Mile House",
        "70 Mile House",
        "5 Mile",
        "Six Mile",
        "St. Louis",
        "Saint-Jean-sur-Richelieu",
        "Port St Lucie",
        "306 Forster City",
        "10407 Berlin, DE",
        "Fort Drive-In",  # no leading house number
        # Norwegian "av" means "of", not "avenue" (a real Scout group's name shape).
        "1. Kolbotn gruppe av Norges Speiderforbund",
        "2 Eksempel gruppe av Norsk Radio Relæ Liga",
        "Smith",
        "",
    ],
)
def test_backstop_spares_legit_place_and_person_names(value: str) -> None:
    assert not looks_like_street_address(value)


@pytest.mark.parametrize(
    "value",
    [
        "Waldstr. 35 A",  # a German address typed into a Norwegian town field
        "Musterstraße 12",
        "Beispielgasse 4",
        "Eksempelveien 3",
        "Storgata 10B",
        "ul. Przykładowa 5",
        "Calle Falsa 123",
        "Rua Exemplo 45",
        "Avenida Ficticia 1200",
        "Barrio San Ramon Lote 245",
        "Manzana 4 Casa 12",
        "1435",
        "92",
    ],
)
def test_backstop_flags_number_after_street_and_plot_numbers(value: str) -> None:
    assert looks_like_street_address(value)


@pytest.mark.parametrize(
    "value",
    [
        "9 de Julio",
        "25 de Mayo",
        "Villa 25 de Mayo",
        "Comuna 1",
        "Barrio 76 Viviendas",
        "5ta Seccion",
        "Neustadt an der Weinstraße",  # street word in a town name, no number
        "Salzweg",
        "Sankt Augustin",
        "Gdańsk",
        "Tromsø",
        "39 Signal Regiment",
        "1. Kolbotn gruppe av Norges Speiderforbund",
    ],
)
def test_backstop_leaves_numbered_place_and_club_names(value: str) -> None:
    assert not looks_like_street_address(value)


@pytest.mark.parametrize(
    "value",
    ["745 Sample Loop", "12 Example Run", "500 Example Pike", "88 Sample Trail", "9 Test Trl"],
)
def test_backstop_flags_newer_street_types(value: str) -> None:
    assert looks_like_street_address(value)


@pytest.mark.parametrize(
    "value",
    [
        "Anytown, IA, 1431 Example St",  # house number after town and state
        "745 Sample Loop",
        "12 Example Run",
        "500 Example Pike",
        "Sampleville, 3 Example Route 9",
        "239 Sample Av",  # "AV" counts in a location, though not in the shared list
        "77 N Example Av Sampleton",
    ],
)
def test_location_flags_street_address_in_any_part(value: str) -> None:
    assert location_has_street_address(value)


@pytest.mark.parametrize(
    "value",
    [
        "Anytown, IA",
        "Des Moines, Iowa",
        "AWS US-EAST-1",
        "Grid EN31",
        "Mt. Hood, OR",
        "Route 66 Repeater Club, Tulsa, OK",  # no house number before Route
        "Trail, BC",
        "Bull Run, VA",
        "Anytown, IA, 50000",  # a postcode part is not an address
        "Sampleton, Example State, 12345, US",
        "With 10,000 mah Battery",  # thousands comma leaves a digit-only part
        "",
        None,
    ],
)
def test_location_spares_ordinary_locations(value: str | None) -> None:
    assert not location_has_street_address(value)


@pytest.mark.parametrize(
    "value",
    [
        "12 Example Strete",  # misspelled street type
        "345 Sample Lane Rd",
        "7 Example",  # house number and street name, no type word
        "Sampleton, 12B Example",
        "Sampleton, OK, 12-14 Example",
        "1234 Sample Bolevard Sampleton",
        "99/3 Example2, Sampleland",  # house number with a sub-number
        "29 Palms, CA",  # accepted over-block: a numbered place name is blanked
    ],
)
def test_location_flags_part_led_by_house_number(value: str) -> None:
    assert location_has_street_address(value)


@pytest.mark.parametrize(
    "value",
    [
        "75001 Paris",  # five-digit postcode
        "Sampleton, 12345",  # digit-only part
        "443.825 Repeater",  # frequency
        "Sampleton, 146.520 MHz",
        "220 MHz Repeater",
        "Sampleton, 100 kHz",
        "Sampleton, PL 88.5",
        "50 Hz hum",
        "With 10,000 mah Battery",
        "5,000mah Battery",
        "2 m Sampleton",
        "2m Sampleton, IA",  # band prefix
        "6M FM Hub",
        "10 Miles N. of Sampleton",  # distance
        "108 Mile Ranch, BC",
        "146.520MHz Sampleton",
        "7SW Sampleton",  # direction glued to a distance
        "Grid EN31",
        "Sampleton 12 Example",  # number not at the start of the part
    ],
)
def test_location_spares_postcodes_frequencies_and_units(value: str) -> None:
    assert not location_has_street_address(value)
