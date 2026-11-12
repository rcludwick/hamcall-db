"""Directory parsing of the AllMon node database (strict, per-row drop report)."""

from pathlib import Path

import pytest

from hamcall_db.enrich_allstar import AllStarNode, parse_directory, parse_directory_report

FIXTURE = Path(__file__).parent / "fixtures" / "allstar" / "allmondb-slice.txt"


@pytest.fixture
def text() -> str:
    return FIXTURE.read_bytes().decode("utf-8", errors="replace")


def by_node(nodes: list[AllStarNode]) -> dict[str, AllStarNode]:
    return {n.node: n for n in nodes}


def test_parses_well_formed_rows(text):
    assert by_node(parse_directory(text))["2002"] == AllStarNode(
        "2002", "WB6NIL", "AllStarLink Parrot", "AWS US-EAST-1"
    )


def test_empty_fields_become_none(text):
    assert by_node(parse_directory(text))["46588"].description is None


def test_drops_lines_without_four_fields(text):
    _, report = parse_directory_report(text)
    assert report["field_count"] == 2


def test_drops_non_numeric_node(text):
    nodes, report = parse_directory_report(text)
    assert report["node_number"] == 1
    assert "abc" not in by_node(nodes)


def test_first_duplicate_wins_and_number_normalised(text):
    nodes = parse_directory(text)
    assert [n.node for n in nodes].count("2002") == 1
    assert by_node(nodes)["2002"].description == "AllStarLink Parrot"
    assert parse_directory_report(text)[1]["duplicate"] == 1


def test_street_address_location_blanked(text):
    nodes, report = parse_directory_report(text)
    for node in ("2007", "2010", "2011", "2012", "2013", "2016"):
        assert by_node(nodes)[node].location is None, node
    assert report["address"] == 6


def test_club_location_with_route_word_kept(text):
    location = by_node(parse_directory(text))["2014"].location
    assert location == "Route 66 Repeater Club, Tulsa, OK"


def test_town_with_postcode_kept(text):
    assert by_node(parse_directory(text))["2015"].location == "Anytown, IA, 50000"


def test_email_redacted(text):
    description = by_node(parse_directory(text))["2008"].description
    assert "[email removed]" in description
    assert "@" not in description


def test_non_utf8_kept(text):
    assert "�" in by_node(parse_directory(text))["2009"].description


def test_sorted_numerically(text):
    numbers = [int(n.node) for n in parse_directory(text)]
    assert numbers == sorted(numbers)
    assert numbers[-1] == 46588
