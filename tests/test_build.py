"""Tests for build-time source collection resilience (hdb-6f3b).

A single source's download/parse failure must NOT abort an --all build; it is logged
and skipped, and the build proceeds from whatever succeeded.
"""

from __future__ import annotations

import polars as pl
from typer.testing import CliRunner

from hamcall_db import enrich_allstar
from hamcall_db.build import SOURCES, _collect_streams, app
from hamcall_db.models import Record


class _GoodSource:
    name = "good"
    synced_at = None

    def download(self, work_dir):
        return work_dir

    def parse(self, path, *, synced_at=None):
        return [Record(callsign="W1AW", source="good")]


class _BadSource:
    name = "bad"
    synced_at = None

    def download(self, work_dir):
        raise OSError("connection timed out")

    def parse(self, path, *, synced_at=None):
        return []


def test_collect_streams_skips_a_failing_source(tmp_path):
    sources = {"good": _GoodSource(), "bad": _BadSource()}
    streams, skipped = _collect_streams(["good", "bad"], tmp_path, sources=sources)
    assert skipped == ["bad"]
    assert len(streams) == 1
    assert streams[0][0].callsign == "W1AW"


def test_collect_streams_all_succeed(tmp_path):
    sources = {"good": _GoodSource()}
    streams, skipped = _collect_streams(["good"], tmp_path, sources=sources)
    assert skipped == []
    assert len(streams) == 1


def test_collect_streams_all_failed_returns_empty(tmp_path):
    sources = {"bad": _BadSource()}
    streams, skipped = _collect_streams(["bad"], tmp_path, sources=sources)
    assert streams == []
    assert skipped == ["bad"]


# --- AllStarLink enrichment wiring on --all (hdb-8803) -------------------------

FIXTURE = (
    __import__("pathlib").Path(__file__).parent
    / "fixtures"
    / "allstar"
    / "allmondb.txt"
)


def _stub_all_sources(monkeypatch):
    """Make every registered source yield one fixed Record, no network."""

    class _Stub:
        def __init__(self, tag):
            self.name = tag
            self.synced_at = None

        def download(self, work_dir):
            return work_dir

        def parse(self, path, *, synced_at=None):
            return [Record(callsign="WB6NIL", source=self.name)]

    monkeypatch.setitem(SOURCES, "fcc", _Stub("fcc"))
    for tag in list(SOURCES):
        monkeypatch.setitem(SOURCES, tag, _Stub(tag))


def test_all_build_enriches_allstar_nodes(tmp_path, monkeypatch):
    _stub_all_sources(monkeypatch)
    # Skip cty download/enrichment (no network): return None so build's cty branch is a
    # no-op.
    monkeypatch.setattr("hamcall_db.build.ad1c.download_cty", lambda *a, **k: None)
    monkeypatch.setattr(enrich_allstar, "download_allstar", lambda *a, **k: FIXTURE)

    out = tmp_path / "out.parquet"
    result = CliRunner().invoke(
        app, ["--all", "--out", str(out), "--work-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    frame = pl.read_parquet(out)
    nodes = frame.filter(pl.col("callsign") == "WB6NIL")["allstar_nodes"].to_list()[0]
    assert nodes == [2000, 2001, 2002]


def test_all_build_resilient_when_allstar_download_fails(tmp_path, monkeypatch):
    _stub_all_sources(monkeypatch)
    monkeypatch.setattr("hamcall_db.build.ad1c.download_cty", lambda *a, **k: None)

    def _boom(*a, **k):
        raise OSError("connection timed out")

    monkeypatch.setattr(enrich_allstar, "download_allstar", _boom)

    out = tmp_path / "out.parquet"
    result = CliRunner().invoke(
        app, ["--all", "--out", str(out), "--work-dir", str(tmp_path)]
    )
    # Build still succeeds; allstar_nodes left empty.
    assert result.exit_code == 0, result.output
    frame = pl.read_parquet(out)
    nodes = frame.filter(pl.col("callsign") == "WB6NIL")["allstar_nodes"].to_list()[0]
    assert nodes == []
