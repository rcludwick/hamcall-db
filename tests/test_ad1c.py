"""Tests for the AD1C cty.dat downloader (cache + If-Modified-Since path logic).

No network: every test injects a local ``fetcher`` seam, so we exercise the cache-hit
(304), fresh-download, dated-directory, and cty.csv branches without hitting upstream.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from hamcall_db.sources import ad1c

# Minimal cty.dat-shaped payload; load_cty (tested elsewhere) parses the real format. The
# downloader treats the body as opaque bytes, so a tiny stand-in is enough here.
_DAT_BYTES = b"United States:    05:  08:  NA:   37.53:  -91.66:    5.0:  K:\n    K;\n"
_CSV_BYTES = b"United States,5,08,NA,37.53,-91.66,5.0,K,K;\n"


def _const_fetcher(payload: bytes):
    """A fetcher that always returns ``payload`` (i.e. always 'modified')."""
    calls: list[tuple[str, float | None]] = []

    def fetch(url: str, since: float | None) -> bytes | None:
        calls.append((url, since))
        return payload

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def test_downloads_and_caches_cty_dat(tmp_path: Path) -> None:
    fetch = _const_fetcher(_DAT_BYTES)
    out = ad1c.download_cty(tmp_path, on=date(2026, 6, 16), fetcher=fetch)

    assert out.read_bytes() == _DAT_BYTES
    assert out.name == "cty.dat"
    # On the first pull nothing is cached, so no If-Modified-Since is sent.
    assert fetch.calls == [(ad1c.CTY_DAT_URL, None)]


def test_caches_under_dated_ad1c_directory(tmp_path: Path) -> None:
    out = ad1c.download_cty(tmp_path, on=date(2026, 6, 16), fetcher=_const_fetcher(_DAT_BYTES))
    expected = tmp_path / "data" / "raw" / "ad1c" / "2026-06-16" / "cty.dat"
    assert out == expected
    assert out.exists()


def test_conditional_get_sends_if_modified_since_when_cached(tmp_path: Path) -> None:
    # Pre-seed the cache so the second pull is conditional.
    ad1c.download_cty(tmp_path, on=date(2026, 6, 16), fetcher=_const_fetcher(_DAT_BYTES))

    fetch = _const_fetcher(_DAT_BYTES)
    ad1c.download_cty(tmp_path, on=date(2026, 6, 16), fetcher=fetch)

    ((url, since),) = fetch.calls
    assert url == ad1c.CTY_DAT_URL
    assert since is not None  # mtime of the cached file was passed through


def test_not_modified_reuses_cached_bytes(tmp_path: Path) -> None:
    # First pull writes a known body...
    ad1c.download_cty(tmp_path, on=date(2026, 6, 16), fetcher=_const_fetcher(_DAT_BYTES))

    # ...then a 304 (fetcher returns None) must leave the cached bytes untouched.
    def not_modified(url: str, since: float | None) -> bytes | None:
        return None

    out = ad1c.download_cty(tmp_path, on=date(2026, 6, 16), fetcher=not_modified)
    assert out.read_bytes() == _DAT_BYTES


def test_not_modified_without_cache_is_an_error(tmp_path: Path) -> None:
    def not_modified(url: str, since: float | None) -> bytes | None:
        return None

    with pytest.raises(RuntimeError):
        ad1c.download_cty(tmp_path, on=date(2026, 6, 16), fetcher=not_modified)


def test_with_csv_also_caches_cty_csv_but_returns_dat(tmp_path: Path) -> None:
    seen: list[str] = []

    def fetch(url: str, since: float | None) -> bytes | None:
        seen.append(url)
        return _CSV_BYTES if url == ad1c.CTY_CSV_URL else _DAT_BYTES

    out = ad1c.download_cty(tmp_path, on=date(2026, 6, 16), with_csv=True, fetcher=fetch)

    assert out.name == "cty.dat"
    csv_path = out.parent / "cty.csv"
    assert csv_path.read_bytes() == _CSV_BYTES
    assert ad1c.CTY_DAT_URL in seen and ad1c.CTY_CSV_URL in seen


def test_returned_path_feeds_enrich_load_cty(tmp_path: Path) -> None:
    # The contract with enrich.py: download_cty returns a path load_cty can read. Use a
    # real (small) cty.dat fixture body so load_cty parses it without raising.
    fixture = Path(__file__).parent / "fixtures" / "cty" / "cty.dat"
    payload = fixture.read_bytes()

    out = ad1c.download_cty(tmp_path, on=date(2026, 6, 16), fetcher=_const_fetcher(payload))

    from hamcall_db.enrich import load_cty

    lookup = load_cty(out)  # must not raise
    assert lookup is not None
