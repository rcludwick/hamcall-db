"""Pipeline-level grid geocoding through the build CLI (hdb-gridorder).

Regression: a full build published grid=NULL for every row, even rows whose postal code
is in the seed table. The geocoder ran inside merge(), before cty.dat enrichment had
filled `country`, so every lookup was keyed (None, postal) and missed. The fix keys the
lookup on the country whose postal system the source uses (FCC -> United States), so
these tests drive the real CLI and read the written Parquet back.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
from typer.testing import CliRunner

from hamcall_db import enrich_allstar
from hamcall_db.build import SOURCES, app
from hamcall_db.models import Record

CTY_FIXTURE = Path(__file__).parent / "fixtures" / "cty" / "cty.dat"
ALLSTAR_FIXTURE = Path(__file__).parent / "fixtures" / "allstar" / "allmondb.txt"


class _StubSource:
    def __init__(self, tag: str, records: list[Record]) -> None:
        self.name = tag
        self.synced_at = None
        self._records = records

    def download(self, work_dir):
        return work_dir

    def parse(self, path, *, synced_at=None):
        return list(self._records)


class _NoParks:
    def download(self, work_dir):
        return work_dir

    def parse(self, path):
        return []


def _stub_sources(monkeypatch, by_tag: dict[str, list[Record]]) -> None:
    for tag in list(SOURCES):
        monkeypatch.setitem(SOURCES, tag, _StubSource(tag, by_tag.get(tag, [])))
    monkeypatch.setattr("hamcall_db.build.PotaSource", _NoParks)
    monkeypatch.setattr("hamcall_db.build.SotaSource", _NoParks)
    monkeypatch.setattr("hamcall_db.build._load_padus_features", lambda wd: [])
    monkeypatch.setattr("hamcall_db.build._load_osm_features", lambda wd: [])
    monkeypatch.setattr(enrich_allstar, "download_allstar", lambda *a, **k: ALLSTAR_FIXTURE)


def _grids(out: Path) -> dict[str, str | None]:
    frame = pl.read_parquet(out)
    return dict(zip(frame["callsign"].to_list(), frame["grid"].to_list(), strict=True))


def test_single_source_fcc_build_fills_grid_from_seed_zip(tmp_path, monkeypatch):
    # No --cty and no --all: country is never filled, yet an FCC ZIP is a US ZIP.
    _stub_sources(
        monkeypatch,
        {"fcc": [Record(callsign="W1AW", postal_code="06111", city="NEWINGTON", source="fcc")]},
    )
    out = tmp_path / "out.parquet"
    result = CliRunner().invoke(
        app, ["--source", "fcc", "--out", str(out), "--work-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    grid = _grids(out)["W1AW"]
    assert grid == "FN31"
    assert len(grid) == 4


def test_all_build_fills_grid_for_each_source_and_us_territories(tmp_path, monkeypatch):
    # --all auto-fetches cty.dat (stubbed to the fixture). The KH6 call resolves to the
    # DXCC entity "Hawaii", but the licensee's FCC address is a ZIP in Connecticut: the
    # grid must follow the address (FN31), not the callsign's entity.
    _stub_sources(
        monkeypatch,
        {
            "fcc": [
                Record(callsign="W1AW", postal_code="06111", source="fcc"),
                Record(callsign="KH6ZZZ", postal_code="06111", source="fcc"),
                Record(callsign="KP4ZZZ", postal_code="00901", source="fcc"),
            ],
            "ised": [Record(callsign="VE3ZZZ", postal_code="K1A", source="ised")],
            "acma": [
                Record(callsign="VK2ZZZ", city="Sydney", state="NSW", source="acma"),
            ],
        },
    )
    monkeypatch.setattr("hamcall_db.build.ad1c.download_cty", lambda *a, **k: CTY_FIXTURE)
    out = tmp_path / "out.parquet"
    result = CliRunner().invoke(app, ["--all", "--out", str(out), "--work-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output

    frame = pl.read_parquet(out)
    countries = dict(zip(frame["callsign"].to_list(), frame["country"].to_list(), strict=True))
    assert countries["KH6ZZZ"] == "Hawaii"  # enrichment still ran

    grids = _grids(out)
    assert grids["W1AW"] == "FN31"
    assert grids["KH6ZZZ"] == "FN31"
    assert grids["KP4ZZZ"] == "FK68"  # San Juan, PR: a USPS ZIP in the US table
    assert grids["VE3ZZZ"] == "FN25"  # Ottawa FSA K1A
    assert grids["VK2ZZZ"] == "QF56"  # Sydney city centroid
    assert all(g is not None and len(g) == 4 for g in grids.values())
