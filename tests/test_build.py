"""Tests for build-time source collection resilience (hdb-6f3b).

A single source's download/parse failure must NOT abort an --all build; it is logged
and skipped, and the build proceeds from whatever succeeded.
"""

from __future__ import annotations

from hamcall_db.build import _collect_streams
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
