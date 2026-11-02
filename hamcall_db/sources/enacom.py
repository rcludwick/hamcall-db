"""ENACOM amateur licence importer (Argentina).

ENACOM (Ente Nacional de Comunicaciones) links its "Listado de Radioaficionados con
Licencia Vigente" (https://enacom.gob.ar/listado-de-radioaficionados_p316) to a search
form on its Spectrum-E portal, ``ListadoRadioaficionado.php``. The form has two
"show everything" buttons, and each returns one HTML table:

- **Mostrar todos los Radioaficionados**: every licensed person, about 15,900 rows
  (7 MB). Columns: Radioaficionado (full name), Categoria, Señal Distintiva
  (callsign), Vigencia Certificado Antecedentes Penales, Ciudad, Provincia.
- **Instituciones y Radioclubes**: clubs and institutions, about 400 rows, with the
  same columns minus the certificate date and with INSTITUCION for the name. These
  callsigns are not in the first list.

The form is protected by a session cookie and a ``csrf_token`` hidden field, so
`EnacomSource.download` opens one cookie session, GETs the form, reads the token, and
POSTs the button; then does the same for the second button. That's four requests per
day, each with a timeout; the two result pages are cached under
``<work_dir>/data/raw/enacom/<YYYY-MM-DD>/``. The pages are generated per request and
carry no ``Last-Modified``, so ``If-Modified-Since`` doesn't apply; `synced_at` is the
download date.

Field mapping: the full name goes into `last_name` (Spanish names have one or two
given names and one or two surnames, which can't be told apart reliably); Categoria
-> `license_class` (Novicio / General / Superior / Especial); Ciudad -> `city`;
Provincia -> `state`, rewritten to the province's usual name. People get
`applicant_type` "Individual", the clubs list "Organization".

Dropped: "Vigencia Certificado Antecedentes Penales" is the expiry of the holder's
criminal-record certificate, which is not licence data. Since Resolución ENACOM
1186/2024 the licences themselves have no expiry, so no licence dates are published.
The listing has no street address or postal code.

Each page states its row count ("Registros encontrados: N"); parsing fails unless the
table has exactly that many rows, so a truncated page can't publish a partial list.
"""

from __future__ import annotations

import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from datetime import date
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from pathlib import Path

from hamcall_db.models import Record
from hamcall_db.sources._daily_cache import Opener, fetch_cached, raw_dir
from hamcall_db.sources.base import synced_at_from

FORM_URL = "https://hertz.enacom.gob.ar/se/portal/arg/publico/ListadoRadioaficionado.php"
DOWNLOAD_TIMEOUT = 120

PEOPLE_FILENAME = "todos.html"
CLUBS_FILENAME = "instituciones.html"
# (cached file, form button, applicant_type for its rows), in parse order.
LISTINGS: tuple[tuple[str, str, str], ...] = (
    (PEOPLE_FILENAME, "mostrarTodos", "Individual"),
    (CLUBS_FILENAME, "soloCuitEspecial", "Organization"),
)

# Argentine amateur prefixes are LO-LW and AY-AZ.
_CALLSIGN = re.compile(r"^(?:L[O-W]|A[Y-Z])[0-9][A-Z]{1,3}$")
_COUNT = re.compile(r"Registros encontrados:\s*(\d+)")

# Header (accents stripped, lower-cased) -> field. Any other column is ignored, which
# is how the criminal-record certificate date is dropped.
_HEADERS = {
    "radioaficionado": "name",
    "institucion": "name",
    "categoria": "class",
    "senal distintiva": "callsign",
    "ciudad": "city",
    "provincia": "province",
}
_REQUIRED = frozenset(_HEADERS.values())

# Provincia as ENACOM writes it -> the province's usual name. Unknown values pass through.
PROVINCES: dict[str, str] = {
    "CIUDAD DE BUENOS AIRES": "Ciudad Autónoma de Buenos Aires",
    "PROVINCIA DE BUENOS AIRES": "Buenos Aires",
    "CATAMARCA": "Catamarca",
    "CHACO": "Chaco",
    "CHUBUT": "Chubut",
    "CÓRDOBA": "Córdoba",
    "CORRIENTES": "Corrientes",
    "ENTRE RÍOS": "Entre Ríos",
    "FORMOSA": "Formosa",
    "JUJUY": "Jujuy",
    "LA PAMPA": "La Pampa",
    "LA RIOJA": "La Rioja",
    "MENDOZA": "Mendoza",
    "MISIONES": "Misiones",
    "NEUQUÉN": "Neuquén",
    "RÍO NEGRO": "Río Negro",
    "SALTA": "Salta",
    "SAN JUAN": "San Juan",
    "SAN LUIS": "San Luis",
    "SANTA CRUZ": "Santa Cruz",
    "SANTA FE": "Santa Fe",
    "SANTIAGO DEL ESTERO": "Santiago del Estero",
    "TIERRA DEL FUEGO A. E I.A.S.": "Tierra del Fuego",
    "TUCUMÁN": "Tucumán",
}


def _key(header: str) -> str:
    decomposed = unicodedata.normalize("NFKD", header)
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).lower().split())


class _TableParser(HTMLParser):
    """Collects the rows (header cells and data cells) of ``<table class="listado">``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headers: list[str] = []
        self.rows: list[list[str]] = []
        self.found_table = False
        self._in_table = False
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._cell_is_header = False

    def handle_starttag(self, tag, attrs):
        if tag == "table" and "listado" in (dict(attrs).get("class") or "").split():
            self._in_table = self.found_table = True
        elif not self._in_table:
            return
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell, self._cell_is_header = [], tag == "th"

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if not self._in_table:
            return
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            text = " ".join("".join(self._cell).split())
            (self.headers if self._cell_is_header else self._row).append(text)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
        elif tag == "table":
            self._in_table = False


def _read_table(path: Path) -> tuple[list[str], list[list[str]], int | None]:
    text = path.read_text(encoding="utf-8")
    parser = _TableParser()
    parser.feed(text)
    parser.close()
    if not parser.found_table:
        raise ValueError(f"{path}: no listing table (error page, or has the ENACOM page changed?)")
    count = _COUNT.search(text)
    return parser.headers, parser.rows, int(count.group(1)) if count else None


def parse_listing(
    path: Path, *, applicant_type: str, synced_at: str | None = None
) -> Iterator[Record]:
    """Parse one ENACOM result page into `Record`s (duplicates included, file order)."""
    headers, rows, expected = _read_table(path)
    columns = {_HEADERS[_key(h)]: i for i, h in enumerate(headers) if _key(h) in _HEADERS}
    missing = _REQUIRED - columns.keys()
    if missing:
        raise ValueError(f"{path}: table lacks column(s) {sorted(missing)}; headers: {headers}")
    if expected is not None and expected != len(rows):
        raise ValueError(f"{path}: page says {expected} rows but the table has {len(rows)}")

    dropped = 0
    for row in rows:
        if len(row) != len(headers):
            raise ValueError(f"{path}: row has {len(row)} cells, header has {len(headers)}: {row}")

        def cell(name: str, row: list[str] = row) -> str | None:
            return row[columns[name]] or None

        callsign = (cell("callsign") or "").upper()
        if not _CALLSIGN.match(callsign):
            dropped += 1
            continue
        province = cell("province")
        license_class = cell("class")
        yield Record(
            callsign=callsign,
            last_name=cell("name"),
            city=cell("city"),
            state=PROVINCES.get(province, province) if province else None,
            license_class=license_class.capitalize() if license_class else None,
            applicant_type=applicant_type,
            source="enacom",
            synced_at=synced_at,
        )
    if dropped:
        print(
            f"WARNING: enacom: dropped {dropped} row(s) without a valid callsign", file=sys.stderr
        )


def parse_dir(path: Path, *, synced_at: str | None = None) -> Iterator[Record]:
    """Parse both cached listings; the first row seen for a callsign wins."""
    seen: set[str] = set()
    for filename, _button, applicant_type in LISTINGS:
        for record in parse_listing(
            path / filename, applicant_type=applicant_type, synced_at=synced_at
        ):
            if record.callsign not in seen:
                seen.add(record.callsign)
                yield record


class _TokenParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.token: str | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "input" and a.get("name") == "csrf_token":
            self.token = a.get("value")


def _csrf_token(html: str) -> str:
    parser = _TokenParser()
    parser.feed(html)
    if not parser.token:
        raise ValueError("enacom: no csrf_token on the listing form (has the page changed?)")
    return parser.token


def _cookie_session() -> Opener:
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar())).open


class EnacomSource:
    """Importer for ENACOM's list of licensed radio amateurs (Argentina)."""

    name = "enacom"

    def __init__(
        self,
        *,
        opener_factory: Callable[[], Opener] | None = None,
        today: date | None = None,
    ) -> None:
        self.synced_at: str | None = None
        self._opener_factory = opener_factory or _cookie_session
        self._today = today

    def download(self, work_dir: Path) -> Path:
        """Fetch both listings into today's cache directory; return the directory."""
        day_dir = raw_dir(work_dir, self.name, self._today)
        if not all((day_dir / filename).exists() for filename, _, _ in LISTINGS):
            opener = self._opener_factory()  # one cookie session for all four requests
            for filename, button, applicant_type in LISTINGS:
                dest = day_dir / filename
                if dest.exists():
                    continue
                form = day_dir / f"{filename}.form"
                try:  # a fresh token per POST: the form may issue single-use tokens
                    fetch_cached(
                        FORM_URL, form, prior=None, opener=opener, timeout=DOWNLOAD_TIMEOUT
                    )
                    token = _csrf_token(form.read_text(encoding="utf-8"))
                finally:
                    form.unlink(missing_ok=True)
                body = urllib.parse.urlencode({"valor": "", "csrf_token": token, button: "1"})
                fetch_cached(
                    FORM_URL,
                    dest,
                    prior=None,
                    opener=opener,
                    timeout=DOWNLOAD_TIMEOUT,
                    data=body.encode(),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                try:  # don't cache an error page for the rest of the day
                    for _ in parse_listing(dest, applicant_type=applicant_type):
                        pass
                except ValueError:
                    dest.unlink()
                    raise
        self.synced_at = synced_at_from(None, day_dir / PEOPLE_FILENAME)
        return day_dir

    def parse(self, path: Path, *, synced_at: str | None = None) -> Iterable[Record]:
        return parse_dir(path, synced_at=synced_at if synced_at is not None else self.synced_at)
