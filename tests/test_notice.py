"""The NOTICE file must surface every upstream source and its required attribution.

The published dataset is distributed under the union of all upstream license
terms (see project memory mem-371f), so each source and each attribution-required
license must be present in NOTICE for redistribution to be lawful.
"""

from __future__ import annotations

from pathlib import Path

NOTICE = (Path(__file__).resolve().parents[1] / "NOTICE").read_text(encoding="utf-8")


def test_notice_exists_and_nonempty() -> None:
    assert NOTICE.strip(), "NOTICE file must not be empty"


def test_notice_names_every_source() -> None:
    for source in ("FCC ULS", "ISED", "ACMA", "cty.dat", "AD1C"):
        assert source in NOTICE, f"NOTICE must mention {source}"


def test_notice_states_each_license() -> None:
    for license_term in (
        "Public domain",
        "Open Government Licence",
        "CC BY 4.0",
        "non-commercial",
    ):
        assert license_term in NOTICE, f"NOTICE must state license: {license_term}"


def test_notice_states_combined_terms() -> None:
    # The combined dataset must be described as the union of upstream terms.
    assert "union" in NOTICE.lower()


def test_notice_separates_build_code_license() -> None:
    # Build code is MIT; dataset is the combined upstream terms.
    assert "MIT" in NOTICE


def test_notice_mentions_pota() -> None:
    # POTA park data is a published source (hdb-9640) — must be attributed.
    assert "POTA" in NOTICE or "Parks On The Air" in NOTICE
    assert "pota.app" in NOTICE


def test_notice_mentions_padus() -> None:
    # PAD-US boundaries source the pota_park_grids child table (hdb-f53c). Public domain,
    # credited as a courtesy; the OSM/WDPA exclusion rationale must be on record.
    assert "PAD-US" in NOTICE
    assert "USGS" in NOTICE
    assert "OpenStreetMap" in NOTICE or "ODbL" in NOTICE  # exclusion rationale


def test_notice_documents_osm_odbl_separate_file() -> None:
    # The international (non-US) OSM park-grids are a SEPARATE ODbL artifact (hdb-438b).
    # NOTICE must attribute OSM, name ODbL, and record the license-segregation + the
    # share-alike inheritance warning for a consumer who joins the two sets.
    assert "OpenStreetMap" in NOTICE
    assert "ODbL" in NOTICE
    assert "Open Database License" in NOTICE
    assert "pota_park_grids_osm" in NOTICE  # the dedicated, segregated table name
    assert "share-alike" in NOTICE.lower()


def test_notice_has_odbl_attribution_statement() -> None:
    # The exact "© OpenStreetMap contributors" attribution must be reproducible from NOTICE.
    assert "OpenStreetMap contributors" in NOTICE
