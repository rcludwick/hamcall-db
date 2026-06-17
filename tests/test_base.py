"""Tests for the shared source helpers in `hamcall_db.sources.base`.

No network: `synced_at_from` is exercised against literal header strings and a tempfile
mtime, never a live download.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from hamcall_db.sources.base import synced_at_from


def test_last_modified_header_parsed_to_iso_date() -> None:
    # The HTTP Last-Modified format -> calendar date.
    assert synced_at_from("Wed, 10 Jun 2026 12:00:00 GMT", None) == "2026-06-10"


def test_last_modified_preferred_over_file_mtime(tmp_path: Path) -> None:
    f = tmp_path / "extract.zip"
    f.write_bytes(b"x")
    # Set mtime to a clearly different date; the header must win.
    os.utime(f, (0, 0))  # 1970-01-01
    assert synced_at_from("Wed, 10 Jun 2026 12:00:00 GMT", f) == "2026-06-10"


def test_falls_back_to_file_mtime_when_no_header(tmp_path: Path) -> None:
    f = tmp_path / "extract.zip"
    f.write_bytes(b"x")
    ts = datetime(2026, 6, 14, 8, 0, 0, tzinfo=UTC).timestamp()
    os.utime(f, (ts, ts))
    assert synced_at_from(None, f) == "2026-06-14"


def test_unparseable_header_falls_back_to_mtime(tmp_path: Path) -> None:
    f = tmp_path / "extract.zip"
    f.write_bytes(b"x")
    ts = datetime(2026, 6, 14, 8, 0, 0, tzinfo=UTC).timestamp()
    os.utime(f, (ts, ts))
    assert synced_at_from("not a date", f) == "2026-06-14"


def test_none_when_no_header_and_no_file(tmp_path: Path) -> None:
    assert synced_at_from(None, None) is None
    assert synced_at_from(None, tmp_path / "missing.zip") is None
