"""Tests for the ENACOM (Argentina) amateur licence importer.

The fixtures in ``tests/fixtures/enacom/`` are the real pages from
``hertz.enacom.gob.ar/se/portal/arg/publico/ListadoRadioaficionado.php`` (fetched
2026-11-02: the empty form, the "Mostrar todos" result, and the "Instituciones y
Radioclubes" result), keeping their markup, whitespace, CRLF line endings, and HTML
entities. The table rows are synthetic: every name, callsign, and town is made up,
because the repo is public. The CSRF token is replaced with zeros.
"""

from __future__ import annotations

import io
import re
import urllib.parse
from dataclasses import asdict
from datetime import date
from email.message import Message
from pathlib import Path

import pytest

from hamcall_db.sources.enacom import (
    CLUBS_FILENAME,
    PEOPLE_FILENAME,
    EnacomSource,
    parse_dir,
    parse_listing,
)

FIXTURES = Path(__file__).parent / "fixtures" / "enacom"

# Argentine amateur call: LO-LW or AY-AZ prefix, one digit, 1-3 letter suffix.
CALLSIGN_RE = re.compile(r"^(L[O-W]|A[Y-Z])[0-9][A-Z]{1,3}$")


def _by_call():
    return {r.callsign: r for r in parse_dir(FIXTURES, synced_at="2026-11-02")}


def test_parses_people_then_clubs_one_record_per_callsign() -> None:
    calls = [r.callsign for r in parse_dir(FIXTURES)]
    assert calls == [
        "LU1ZZA",
        "LU2ZZB",
        "LW9ZZC",
        "AY0ZZD",
        "LU5ZZE",
        "LU3ZZF",
        "LO7ZZG",
        "LU4ZZH",
        "LU8ZZJ",
    ]


def test_every_callsign_is_callsign_shaped() -> None:
    for r in parse_dir(FIXTURES):
        assert CALLSIGN_RE.match(r.callsign), repr(r.callsign)


def test_person_field_mapping() -> None:
    r = _by_call()["LU2ZZB"]
    assert r.first_name is None
    assert r.last_name == "MARIA INVENTADA PRUEBA"  # full name, not split
    assert r.license_class == "General"
    assert r.city == "VILLA IMAGINARIA"  # merge() title-cases it
    assert r.state == "Buenos Aires"
    assert r.postal_code is None
    assert r.applicant_type == "Individual"
    assert r.source == "enacom"
    assert r.synced_at == "2026-11-02"
    # ENACOM licences no longer expire (Resolución 1186/2024); no dates are published.
    assert (r.grant_date, r.effective_date, r.expired_date) == (None, None, None)


def test_provinces_get_readable_names() -> None:
    by_call = _by_call()
    assert by_call["LU1ZZA"].state == "Ciudad Autónoma de Buenos Aires"
    assert by_call["LW9ZZC"].state == "Córdoba"
    assert by_call["AY0ZZD"].state == "Tierra del Fuego"
    assert by_call["LU3ZZF"].state == "Entre Ríos"
    # An unknown province passes through as published.
    assert by_call["LO7ZZG"].state == "PROVINCIA INEXISTENTE"


def test_entities_and_blank_city() -> None:
    by_call = _by_call()
    assert by_call["AY0ZZD"].last_name == "ANA D'EJEMPLO"  # &#039; decoded
    assert by_call["LU5ZZE"].city is None


def test_duplicate_callsign_keeps_first_row() -> None:
    assert _by_call()["LU3ZZF"].last_name == "PRIMER TITULAR DUPLICADO"


def test_clubs_are_organizations() -> None:
    r = _by_call()["LU4ZZH"]
    assert r.last_name == "RADIO CLUB IMAGINARIO"
    assert r.applicant_type == "Organization"
    assert r.license_class == "Superior"
    assert r.state == "Santa Fe"


def test_criminal_record_certificate_date_is_dropped() -> None:
    # "Vigencia Certificado Antecedentes Penales" is not licence data.
    for r in parse_dir(FIXTURES):
        for value in asdict(r).values():
            assert not (isinstance(value, str) and re.search(r"\d{4}-\d{2}-\d{2}", value))


def test_row_count_must_match_the_page_total(tmp_path: Path) -> None:
    html = (FIXTURES / PEOPLE_FILENAME).read_text(encoding="utf-8")
    bad = tmp_path / PEOPLE_FILENAME
    bad.write_text(html.replace("Registros encontrados: 8", "Registros encontrados: 9"))
    with pytest.raises(ValueError, match="9"):
        list(parse_listing(bad, applicant_type="Individual"))


def test_page_without_table_fails(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        list(parse_listing(FIXTURES / "form.html", applicant_type="Individual"))


# --- download -----------------------------------------------------------------------


class _Response(io.BytesIO):
    def __init__(self, body: bytes):
        super().__init__(body)
        self.status = 200
        self.headers = Message()


class _FakeSite:
    """Serves the form on GET and the matching listing on a POST with the token."""

    def __init__(self):
        self.requests: list[tuple[str, dict[str, list[str]]]] = []
        self.timeouts: list[float | None] = []
        self.sessions = 0

    def factory(self):
        self.sessions += 1
        return self

    def __call__(self, request, timeout=None):
        self.timeouts.append(timeout)
        if request.data is None:
            self.requests.append(("GET", {}))
            return _Response((FIXTURES / "form.html").read_bytes())
        form = urllib.parse.parse_qs(request.data.decode(), keep_blank_values=True)
        self.requests.append(("POST", form))
        assert form["csrf_token"] == ["0" * 64]
        name = PEOPLE_FILENAME if "mostrarTodos" in form else CLUBS_FILENAME
        return _Response((FIXTURES / name).read_bytes())


def test_download_posts_both_listings_with_the_token(tmp_path: Path) -> None:
    site = _FakeSite()
    src = EnacomSource(opener_factory=site.factory, today=date(2026, 11, 2))
    out = src.download(tmp_path)
    assert out == tmp_path / "data/raw/enacom/2026-11-02"
    assert [m for m, _ in site.requests] == ["GET", "POST", "GET", "POST"]
    assert "mostrarTodos" in site.requests[1][1]
    assert "soloCuitEspecial" in site.requests[3][1]
    assert all(t for t in site.timeouts)
    assert len(list(parse_dir(out))) == 9
    assert src.synced_at is not None


def test_download_reuses_todays_copy(tmp_path: Path) -> None:
    EnacomSource(opener_factory=_FakeSite().factory, today=date(2026, 11, 2)).download(tmp_path)
    site = _FakeSite()
    EnacomSource(opener_factory=site.factory, today=date(2026, 11, 2)).download(tmp_path)
    assert site.requests == []


def test_download_rejects_an_error_page(tmp_path: Path) -> None:
    class _NoTable(_FakeSite):
        def __call__(self, request, timeout=None):
            super().__call__(request, timeout)
            return _Response((FIXTURES / "form.html").read_bytes())

    with pytest.raises(ValueError):
        EnacomSource(opener_factory=_NoTable().factory, today=date(2026, 11, 2)).download(tmp_path)
    # Nothing half-good is cached for the day.
    assert not list((tmp_path / "data/raw/enacom/2026-11-02").glob("*.html"))
