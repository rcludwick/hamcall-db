"""Tests for the street-address guard (hamcall_db.address_guard).

Every address here is synthetic: this repo is public, so no real person's address may
be committed. The shapes mirror what filers actually type into the wrong field.
"""

from __future__ import annotations

import pytest

from hamcall_db.address_guard import echoes_street, looks_like_street_address

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
