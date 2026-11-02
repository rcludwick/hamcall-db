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


# --- retry_transient / is_transient (hdb-skipalert) ------------------------------

import socket  # noqa: E402
import urllib.error  # noqa: E402

import pytest  # noqa: E402

from hamcall_db.sources import base  # noqa: E402
from hamcall_db.sources.base import is_transient, retry_transient  # noqa: E402


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.invalid/x.zip", code, "err", None, None)  # type: ignore[arg-type]


class _Flaky:
    """Raise each queued exception in turn, then return "ok"."""

    def __init__(self, *errors: BaseException) -> None:
        self.errors = list(errors)
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "ok"


@pytest.mark.parametrize(
    "exc",
    [
        _http_error(503),
        _http_error(500),
        _http_error(429),
        TimeoutError("timed out"),
        ConnectionResetError("reset by peer"),
        urllib.error.URLError(TimeoutError("timed out")),
        urllib.error.URLError(ConnectionResetError("reset")),
    ],
)
def test_transient_failures_are_classified_transient(exc: BaseException) -> None:
    assert is_transient(exc)


@pytest.mark.parametrize(
    "exc",
    [
        _http_error(404),
        _http_error(403),
        _http_error(400),
        socket.gaierror(8, "nodename nor servname provided"),
        urllib.error.URLError(socket.gaierror(-2, "Name or service not known")),
        urllib.error.URLError("unknown url type"),
        ValueError("bad zip"),
    ],
)
def test_permanent_failures_are_not_transient(exc: BaseException) -> None:
    assert not is_transient(exc)


def test_retry_then_succeed_after_transient_errors() -> None:
    fn = _Flaky(_http_error(503), TimeoutError("timed out"))
    slept: list[float] = []
    retries: list[int] = []
    result = retry_transient(
        fn,
        delays=(30, 90),
        sleep=slept.append,
        on_retry=lambda attempt, exc, delay: retries.append(attempt),
    )
    assert result == "ok"
    assert fn.calls == 3
    assert slept == [30, 90]
    assert retries == [1, 2]


def test_transient_error_raised_once_attempts_run_out() -> None:
    fn = _Flaky(_http_error(503), _http_error(503), _http_error(502))
    slept: list[float] = []
    with pytest.raises(urllib.error.HTTPError) as info:
        retry_transient(fn, delays=(30, 90), sleep=slept.append)
    assert info.value.code == 502
    assert fn.calls == 3
    assert slept == [30, 90]


def test_non_transient_error_is_not_retried() -> None:
    fn = _Flaky(_http_error(404))
    slept: list[float] = []
    with pytest.raises(urllib.error.HTTPError):
        retry_transient(fn, delays=(30, 90), sleep=slept.append)
    assert fn.calls == 1
    assert slept == []


def test_dns_failure_is_not_retried() -> None:
    fn = _Flaky(urllib.error.URLError(socket.gaierror(-2, "Name or service not known")))
    slept: list[float] = []
    with pytest.raises(urllib.error.URLError):
        retry_transient(fn, delays=(30, 90), sleep=slept.append)
    assert fn.calls == 1
    assert slept == []


def test_default_delays_come_from_module_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    # RETRY_DELAYS is read at call time so a build-level test can zero it.
    monkeypatch.setattr(base, "RETRY_DELAYS", (0.0,))
    slept: list[float] = []
    assert retry_transient(_Flaky(_http_error(503)), sleep=slept.append) == "ok"
    assert slept == [0.0]
