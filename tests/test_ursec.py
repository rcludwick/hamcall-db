"""Tests for the URSEC (Uruguay) amateur permit importer.

``tests/fixtures/ursec/Nomina CX Vigentes Julio 2026.ods`` was made from the real July
2026 edition: its ``styles.xml``, ``mimetype`` and ``content.xml`` framing (title row,
header row, cell styles, the blank row, the "Julio de 2026" footer in the date column,
and the 1,047,217-row repeated empty tail) are kept verbatim. The data rows are
synthetic, as are the author names in ``meta.xml``; the embedded logo was removed.
Dates appear both as real date cells and as text such as ``30 Abr. 2029``, as in the
real file.

``registro-radioaficionados.html`` is the publishing page trimmed to its downloads list.
"""

from __future__ import annotations

import io
import re
import urllib.error
from dataclasses import asdict
from datetime import date
from email.message import Message
from pathlib import Path

import pytest

from hamcall_db.sources.ursec import (
    PAGE_URL,
    UrsecSource,
    find_ods_link,
    parse_date,
    parse_file,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ursec"
ODS = FIXTURES / "Nomina CX Vigentes Julio 2026.ods"
PAGE = FIXTURES / "registro-radioaficionados.html"
ODS_URL = (
    "https://www.gub.uy/unidad-reguladora-servicios-comunicaciones/sites/"
    "unidad-reguladora-servicios-comunicaciones/files/2026-07/"
    "Nomina%20CX%20Vigentes%20Julio%202026.ods"
)

# Uruguayan amateur call: CV-CX prefix, one digit, 1-3 letter suffix.
CALLSIGN_RE = re.compile(r"^C[V-X][0-9][A-Z]{1,3}$")


def _by_call():
    return {r.callsign: r for r in parse_file(ODS, synced_at="2026-07-27")}


def test_one_record_per_callsign_in_file_order() -> None:
    assert [r.callsign for r in parse_file(ODS)] == [
        "CX1ZA",
        "CX2ZB",
        "CX3ZC",
        "CX4ZXX",
        "CX5ZD",
        "CW9Z",
        "CX6ZE",
    ]


def test_every_callsign_is_callsign_shaped() -> None:
    for r in parse_file(ODS):
        assert CALLSIGN_RE.match(r.callsign), repr(r.callsign)


def test_person_field_mapping() -> None:
    r = _by_call()["CX1ZA"]
    assert (r.first_name, r.last_name) == ("JUAN PRUEBA", "EJEMPLO FICTICIO")
    assert r.license_class == "General"
    assert r.expired_date == "2027-10-31"
    assert r.applicant_type == "Individual"
    assert (r.city, r.state, r.postal_code) == (None, None, None)
    assert r.source == "ursec"
    assert r.synced_at == "2026-07-27"


def test_text_dates_are_parsed() -> None:
    assert _by_call()["CX2ZB"].expired_date == "2029-04-30"
    assert _by_call()["CX5ZD"].expired_date == "2026-09-07"


@pytest.mark.parametrize(
    ("text", "iso"),
    [
        ("30 Abr. 2029", "2029-04-30"),
        ("7 Set. 2026", "2026-09-07"),
        ("31 Oct 2026", "2026-10-31"),
        ("01 Dic 2026", "2026-12-01"),
        ("15 Sep 2027", "2027-09-15"),
        ("Julio de 2026", None),
        ("", None),
    ],
)
def test_parse_date(text: str, iso: str | None) -> None:
    assert parse_date(text) == iso


def test_visiting_reciprocal_calls_are_skipped(capsys) -> None:
    # K0XYZ/CX would resolve to its home country through cty.dat: not published.
    assert "K0XYZ/CX" not in _by_call()
    assert "1 visiting-amateur" in capsys.readouterr().err


def test_malformed_callsign_is_dropped_and_counted(capsys) -> None:
    assert "CX-7ZF" not in _by_call()
    assert "dropped 1 row" in capsys.readouterr().err


def test_regular_permit_wins_over_iarp() -> None:
    # CX3ZC holds an IARP (international) permit and a regular one: keep the regular.
    assert _by_call()["CX3ZC"].expired_date == "2030-06-30"


def test_clubs_are_organizations() -> None:
    for call in ("CX4ZXX", "CX5ZD"):
        r = _by_call()[call]
        assert r.first_name is None
        assert r.applicant_type == "Organization"
    assert _by_call()["CX5ZD"].last_name == "RADIO CLUB IMAGINARIO"


def test_permit_type_is_not_published() -> None:
    for r in parse_file(ODS):
        for value in asdict(r).values():
            assert value not in ("Comun", "IARP", "Repetidora", "Reciprocidad")


def test_find_ods_link() -> None:
    assert find_ods_link(PAGE.read_text(encoding="utf-8"), PAGE_URL) == ODS_URL


def test_find_ods_link_missing() -> None:
    with pytest.raises(ValueError):
        find_ods_link("<html><a href='x.pdf'>pdf</a></html>", PAGE_URL)


def test_header_change_fails_loudly(tmp_path: Path) -> None:
    import zipfile

    bad = tmp_path / "bad.ods"
    with zipfile.ZipFile(ODS) as src, zipfile.ZipFile(bad, "w") as dst:
        for item in src.infolist():
            data = src.read(item)
            if item.filename == "content.xml":
                data = data.replace(b"Distintivo de Llamada", b"Indicativo")
            dst.writestr(item, data)
    with pytest.raises(ValueError):
        list(parse_file(bad))


# --- download -----------------------------------------------------------------------

LAST_MODIFIED = "Mon, 27 Jul 2026 19:55:05 GMT"


class _Response(io.BytesIO):
    def __init__(self, body: bytes, headers: dict[str, str] | None = None):
        super().__init__(body)
        self.status = 200
        self.headers = Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v


class _FakeSite:
    def __init__(self, not_modified: bool = False):
        self.urls: list[str] = []
        self.headers: list[dict[str, str]] = []
        self.timeouts: list[float | None] = []
        self.not_modified = not_modified

    def __call__(self, request, timeout=None):
        self.urls.append(request.full_url)
        self.headers.append(dict(request.header_items()))
        self.timeouts.append(timeout)
        if request.full_url == PAGE_URL:
            return _Response(PAGE.read_bytes())
        assert request.full_url == ODS_URL
        if self.not_modified and "If-modified-since" in self.headers[-1]:
            raise urllib.error.HTTPError(request.full_url, 304, "Not Modified", Message(), None)
        return _Response(ODS.read_bytes(), {"Last-Modified": LAST_MODIFIED})


def test_download_discovers_and_fetches_the_current_edition(tmp_path: Path) -> None:
    site = _FakeSite()
    src = UrsecSource(opener=site, today=date(2026, 11, 2))
    path = src.download(tmp_path)
    assert path == tmp_path / "data/raw/ursec/2026-11-02/Nomina CX Vigentes Julio 2026.ods"
    assert site.urls == [PAGE_URL, ODS_URL]
    assert all(t for t in site.timeouts)
    assert src.synced_at == "2026-07-27"
    assert path.read_bytes() == ODS.read_bytes()


def test_download_reuses_todays_copy(tmp_path: Path) -> None:
    UrsecSource(opener=_FakeSite(), today=date(2026, 11, 2)).download(tmp_path)
    site = _FakeSite()
    src = UrsecSource(opener=site, today=date(2026, 11, 2))
    src.download(tmp_path)
    assert site.urls == []
    assert src.synced_at == "2026-07-27"


def test_download_sends_if_modified_since_for_the_same_edition(tmp_path: Path) -> None:
    UrsecSource(opener=_FakeSite(), today=date(2026, 11, 2)).download(tmp_path)
    site = _FakeSite(not_modified=True)
    src = UrsecSource(opener=site, today=date(2026, 11, 3))
    path = src.download(tmp_path)
    assert "If-modified-since" in site.headers[1]
    assert path.read_bytes() == ODS.read_bytes()
    assert src.synced_at == "2026-07-27"
