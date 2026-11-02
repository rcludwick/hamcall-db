"""Tests for the Anatel (Brazil) amateur station importer.

The fixture ``tests/fixtures/anatel/Estacoes_Radioamador.csv`` keeps the real file's
layout (UTF-8 with a byte-order mark, ``;`` delimiter, CRLF line endings, the verbatim
18-column header, ``N/I``/``N/A`` placeholders, quoting around values with an
apostrophe), but every person, ID, Fistel number, and town in it is made up. The repo
is public: no real licensee appears here.

download() is tested against an in-memory HTTP server stand-in that honours ``Range``
headers, so no test touches the network.
"""

from __future__ import annotations

import io
import os
import re
import urllib.error
import zipfile
from dataclasses import asdict
from datetime import date
from email.message import Message
from pathlib import Path

import pytest

from hamcall_db.sources.anatel import MEMBER_NAME, AnatelSource, parse_file

FIXTURES = Path(__file__).parent / "fixtures" / "anatel"
CSV = FIXTURES / MEMBER_NAME

# Brazilian amateur call: PP-PY or ZV-ZZ prefix, one digit, 1-3 letter suffix.
CALLSIGN_RE = re.compile(r"^(P[P-Y]|Z[V-Z])[0-9][A-Z]{1,3}$")


def _records():
    return list(parse_file(CSV, synced_at="2026-11-01"))


def _by_call():
    return {r.callsign: r for r in _records()}


def test_one_record_per_callsign_in_file_order() -> None:
    # Multi-station holders collapse to one record; 'N/I' and 'PU2-ZZF' are dropped.
    assert [r.callsign for r in _records()] == [
        "PU5ZZA",
        "PY2ZZB",
        "PT2ZZC",
        "PP1ZZD",
        "PY5ZZE",
        "PU1ZZG",
        "ZZ2ZZH",
    ]


def test_every_callsign_is_callsign_shaped() -> None:
    for r in _records():
        assert CALLSIGN_RE.match(r.callsign), repr(r.callsign)


def test_person_field_mapping() -> None:
    r = _by_call()["PY2ZZB"]
    assert r.first_name is None
    assert r.last_name == "CICRANO FICTICIO DOS SANTOS"  # full name, not split
    assert r.city == "Cidade Inventada"
    assert r.state == "SP"
    assert r.postal_code is None
    assert r.license_class == "Classe B"
    assert r.applicant_type == "Individual"
    assert r.source == "anatel"
    assert r.synced_at == "2026-11-01"


def test_dates_across_stations() -> None:
    # Earliest first licensing, latest renewal, latest station validity.
    r = _by_call()["PY2ZZB"]
    assert (r.grant_date, r.effective_date, r.expired_date) == (
        "2018-02-27",
        "2018-02-27",
        "2055-09-02",
    )
    club = _by_call()["PY5ZZE"]
    assert (club.grant_date, club.effective_date, club.expired_date) == (
        "2008-11-06",
        "2011-03-15",
        "2028-11-06",
    )
    # 'N/I' validity -> None.
    assert _by_call()["PU5ZZA"].expired_date is None


def test_quoted_values_with_apostrophes() -> None:
    r = _by_call()["PT2ZZC"]
    assert r.last_name == "BELTRANO JOSE D'EXEMPLO"
    assert r.city == "Vila d'Amostra"


def test_mobile_only_station_falls_back_to_entity_state() -> None:
    # A mobile station has no municipality ('N/A'): no city, state from the entity.
    r = _by_call()["PP1ZZD"]
    assert r.city is None
    assert r.state == "ES"
    r = _by_call()["ZZ2ZZH"]
    assert r.city is None
    assert r.state == "RS"


def test_company_holder() -> None:
    r = _by_call()["PY5ZZE"]
    assert r.last_name == "LIGA DE RADIOAMADORES FICTICIOS"
    assert r.applicant_type == "Organization"
    assert r.license_class is None  # COER 'N/A'
    assert r.city == "Município Exemplo"
    assert r.state == "PR"


def test_lowercase_callsign_is_uppercased() -> None:
    assert "PU1ZZG" in _by_call()


def test_masked_cpf_and_station_details_never_published() -> None:
    text = CSV.read_text(encoding="utf-8-sig")
    masked_ids = set(re.findall(r"\*[\d*./-]+\*", text))
    station_numbers = {"9000000102", "90000000011", "145,210 / 144,610 MHz", "Estação_000"}
    assert masked_ids
    for r in _records():
        for value in asdict(r).values():
            if isinstance(value, str):
                for secret in masked_ids | station_numbers:
                    assert secret not in value, (r.callsign, value)


def test_missing_header_column_fails_loudly(tmp_path: Path) -> None:
    bad = tmp_path / MEMBER_NAME
    lines = CSV.read_text(encoding="utf-8-sig").splitlines()
    bad.write_text(lines[0].replace("Indicativo", "Indicativo X") + "\n" + lines[1] + "\n")
    with pytest.raises(ValueError, match="Indicativo"):
        list(parse_file(bad))


def test_parse_uses_source_synced_at(tmp_path: Path) -> None:
    src = AnatelSource()
    src.synced_at = "2026-10-31"
    assert {r.synced_at for r in src.parse(CSV)} == {"2026-10-31"}


# --- download: partial fetch of one zip member over HTTP Range ---------------------


LAST_MODIFIED = "Sun, 01 Nov 2026 10:52:35 GMT"


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MEMBER_NAME, CSV.read_bytes())
        # A big incompressible member after it, standing in for the 5 GB station files,
        # so the amateur member sits well outside the zip's fetched tail.
        zf.writestr("Estacoes_SLP.csv", os.urandom(4_000_000))
        zf.writestr("Estacoes_SME.csv", b"x;y\r\n")
    return buf.getvalue()


class _Response:
    def __init__(self, status: int, body: bytes, headers: dict[str, str]):
        self.status = status
        self._body = io.BytesIO(body)
        self.headers = Message()
        for k, v in headers.items():
            self.headers[k] = v

    def read(self, n: int = -1) -> bytes:
        return self._body.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _RangeServer:
    """urlopen stand-in serving one blob, honouring ``Range`` and ``If-Modified-Since``."""

    def __init__(self, blob: bytes, *, honour_range: bool = True, not_modified: bool = False):
        self.blob = blob
        self.honour_range = honour_range
        self.not_modified = not_modified
        self.requests: list[dict[str, str]] = []
        self.bytes_sent = 0
        self.timeouts: list[float | None] = []

    def __call__(self, request, timeout=None):
        headers = dict(request.header_items())
        self.requests.append(headers)
        self.timeouts.append(timeout)
        if self.not_modified and "If-modified-since" in headers:
            raise urllib.error.HTTPError(request.full_url, 304, "Not Modified", Message(), None)
        base = {"Last-Modified": LAST_MODIFIED, "ETag": '"abc"'}
        size = len(self.blob)
        rng = headers.get("Range")
        if not rng or not self.honour_range:
            self.bytes_sent += size
            return _Response(200, self.blob, {**base, "Content-Length": str(size)})
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng)
        assert m, rng
        if m.group(1) == "":
            start, end = max(0, size - int(m.group(2))), size - 1
        else:
            start = int(m.group(1))
            end = min(int(m.group(2)) if m.group(2) else size - 1, size - 1)
        body = self.blob[start : end + 1]
        self.bytes_sent += len(body)
        return _Response(
            206,
            body,
            {
                **base,
                "Content-Range": f"bytes {start}-{end}/{size}",
                "Content-Length": str(len(body)),
            },
        )


def test_download_fetches_only_the_member(tmp_path: Path) -> None:
    blob = _zip_bytes()
    server = _RangeServer(blob)
    src = AnatelSource(opener=server, today=date(2026, 11, 2))
    path = src.download(tmp_path)
    assert path == tmp_path / "data/raw/anatel/2026-11-02" / MEMBER_NAME
    assert path.read_bytes() == CSV.read_bytes()
    assert server.bytes_sent < len(blob) / 2  # never pulled the big member
    assert all(t for t in server.timeouts)
    assert src.synced_at == "2026-11-01"


def test_download_reuses_todays_copy_without_network(tmp_path: Path) -> None:
    server = _RangeServer(_zip_bytes())
    AnatelSource(opener=server, today=date(2026, 11, 2)).download(tmp_path)
    again = _RangeServer(_zip_bytes())
    src = AnatelSource(opener=again, today=date(2026, 11, 2))
    src.download(tmp_path)
    assert again.requests == []
    assert src.synced_at == "2026-11-01"  # from the cached file's mtime


def test_download_304_carries_yesterdays_copy_forward(tmp_path: Path) -> None:
    AnatelSource(opener=_RangeServer(_zip_bytes()), today=date(2026, 11, 2)).download(tmp_path)
    server = _RangeServer(_zip_bytes(), not_modified=True)
    path = AnatelSource(opener=server, today=date(2026, 11, 3)).download(tmp_path)
    assert len(server.requests) == 1
    assert "If-modified-since" in server.requests[0]
    assert path == tmp_path / "data/raw/anatel/2026-11-03" / MEMBER_NAME
    assert path.read_bytes() == CSV.read_bytes()


def test_download_falls_back_to_whole_zip_when_range_ignored(tmp_path: Path) -> None:
    server = _RangeServer(_zip_bytes(), honour_range=False)
    path = AnatelSource(opener=server, today=date(2026, 11, 2)).download(tmp_path)
    assert path.read_bytes() == CSV.read_bytes()
    assert len(server.requests) == 1  # one plain GET, no ranged follow-ups
    assert not list(path.parent.glob("*.zip"))  # the whole zip isn't kept around


def test_download_detects_upstream_change_mid_fetch(tmp_path: Path) -> None:
    server = _RangeServer(_zip_bytes())
    calls = {"n": 0}

    def flaky(request, timeout=None):
        calls["n"] += 1
        resp = server(request, timeout=timeout)
        if calls["n"] > 1:
            del resp.headers["ETag"]
            resp.headers["ETag"] = '"changed"'
        return resp

    with pytest.raises(ConnectionError):
        AnatelSource(opener=flaky, today=date(2026, 11, 2)).download(tmp_path)
