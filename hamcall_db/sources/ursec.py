"""URSEC amateur permit importer (Uruguay).

URSEC (Unidad Reguladora de Servicios de Comunicaciones) publishes the "Nómina de
permisos en el servicio de radioaficionados" a few times a year on
https://www.gub.uy/unidad-reguladora-servicios-comunicaciones/datos-y-estadisticas/datos/registro-radioaficionados
as a PDF and an OpenDocument spreadsheet (ODS). Each edition has its own file name
(e.g. ``files/2026-07/Nomina CX Vigentes Julio 2026.ods``), so `UrsecSource.download`
first reads the page and takes the ``.ods`` link from its downloads list, then fetches
that file. Both are cached under ``<work_dir>/data/raw/ursec/<YYYY-MM-DD>/``; the ODS
request carries ``If-Modified-Since`` when an earlier day already holds the same
edition, and the server's ``Last-Modified`` becomes `synced_at`.

Reading the ODS
---------------
An ODS file is a zip whose ``content.xml`` holds the sheet as XML. This module reads it
with the standard library (`zipfile` + `xml.etree.ElementTree`) rather than adding a
spreadsheet dependency: the sheet is a single plain table, and the only ODS features it
uses are repeated cells and rows (``number-columns-repeated`` / ``number-rows-
repeated``), covered (merged) cells, and typed date cells. Repeated runs of empty
cells and rows are skipped without being expanded; the real file ends with one empty
row repeated 1,047,217 times.

Sheet shape: a title row, then the header row, then one row per permit, then a blank
row and an edition footer ("Julio de 2026") in the date column. Columns are found by
header name:

======================  ================================================
Column                  Use
======================  ================================================
Distintivo de Llamada   `callsign`
Permiso                 picks the row to keep (not published)
Apellidos/Razón Social  `last_name` (surnames, or a club's name)
Nombres                 `first_name` (given names; empty for clubs)
Categoria Actual        `license_class` (Inicial / General / Superior)
Fecha Vencimiento       `expired_date`
======================  ================================================

Dates are usually date cells (``office:date-value``), but some rows hold text such as
``30 Abr. 2029`` or ``7 Set. 2026``; `parse_date` reads those Spanish month
abbreviations.

A holder can appear twice: a regular permit and an IARP (International Amateur Radio
Permit) entry, or the same permit typed twice. One record per callsign is kept: a
non-IARP row over an IARP row, then the later expiry, then the first row. A row with no
given names is a club or institution (`applicant_type` "Organization"); the rest are
"Individual".

Visiting foreign amateurs appear as ``<home call>/CX`` (``K0XYZ/CX``, under a
reciprocity permit). Those rows are skipped: the build resolves country and DXCC from
the callsign prefix through cty.dat, which gives the *home* country for these calls,
so they would be published under the wrong entity. They are counted on stderr.

The sheet has no address, town, or postal code, so there is nothing for the address
guard to compare against; city, state, and postal_code stay None.
"""

from __future__ import annotations

import re
import sys
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable, Iterator
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

from hamcall_db.models import Record
from hamcall_db.sources._daily_cache import Opener, fetch_cached, latest_prior, raw_dir
from hamcall_db.sources.base import synced_at_from

PAGE_URL = (
    "https://www.gub.uy/unidad-reguladora-servicios-comunicaciones/"
    "datos-y-estadisticas/datos/registro-radioaficionados"
)
DOWNLOAD_TIMEOUT = 60
PAGE_FILENAME = "registro-radioaficionados.html"

_TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_ROW = f"{{{_TABLE}}}table-row"
_CELLS = (f"{{{_TABLE}}}table-cell", f"{{{_TABLE}}}covered-table-cell")
_COLS_REPEATED = f"{{{_TABLE}}}number-columns-repeated"
_P = f"{{{_TEXT}}}p"
_SPACE = f"{{{_TEXT}}}s"
_TAB = f"{{{_TEXT}}}tab"
_VALUE_TYPE = f"{{{_OFFICE}}}value-type"
_DATE_VALUE = f"{{{_OFFICE}}}date-value"

# Only the first few columns matter; never expand a 16,000-cell repeat beyond this.
_MAX_COLUMNS = 32

# Uruguayan prefixes are CV-CX.
_CALLSIGN = re.compile(r"^C[V-X][0-9][A-Z]{1,3}$")
# A visiting amateur's reciprocal-permit call, <home call>/CX: skipped (see above).
_RECIPROCAL = re.compile(r"^[A-Z0-9]{1,3}[0-9][A-Z]{1,4}/CX$")

_HEADERS = {
    "distintivo de llamada": "callsign",
    "permiso": "permit",
    "apellidos/razon social": "surnames",
    "nombres": "given",
    "categoria actual": "class",
    "fecha vencimiento": "expiry",
}

_MONTHS = {
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "set": 9,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
}
_TEXT_DATE = re.compile(r"^(\d{1,2})\s+([a-z]{3})[a-z]*\.?\s+(\d{4})$")


def _key(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.lower().split())


def parse_date(text: str | None) -> str | None:
    """``30 Abr. 2029`` / ``7 Set. 2026`` / ``31 Oct 2026`` -> ISO date, else None."""
    match = _TEXT_DATE.match(_key(text or ""))
    if not match:
        return None
    month = _MONTHS.get(match.group(2))
    if month is None:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(1))).isoformat()
    except ValueError:
        return None


def _paragraph_text(p: ET.Element) -> str:
    parts = [p.text or ""]
    for child in p:
        if child.tag == _SPACE:
            parts.append(" " * int(child.get(f"{{{_TEXT}}}c", "1")))
        elif child.tag == _TAB:
            parts.append("\t")
        else:
            parts.append(_paragraph_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _cell_value(cell: ET.Element) -> str:
    """The cell's value: ISO date for date cells, else its text, whitespace-collapsed."""
    if cell.get(_VALUE_TYPE) == "date" and cell.get(_DATE_VALUE):
        return cell.get(_DATE_VALUE, "")[:10]
    text = " ".join(_paragraph_text(p) for p in cell.findall(_P))
    return " ".join(text.split())


def _rows(content: bytes) -> Iterator[list[str]]:
    """Yield the first sheet's rows as lists of cell strings (empty rows skipped)."""
    root = ET.fromstring(content)
    for row in root.iter(_ROW):
        cells: list[str] = []
        for cell in row:
            if cell.tag not in _CELLS:
                continue
            repeat = min(int(cell.get(_COLS_REPEATED, "1")), _MAX_COLUMNS - len(cells))
            cells.extend([_cell_value(cell)] * max(repeat, 0))
            if len(cells) >= _MAX_COLUMNS:
                break
        # A repeated row is the same row again; one copy is enough after dedupe.
        if any(cells):
            yield cells


def parse_file(path: Path, *, synced_at: str | None = None) -> Iterator[Record]:
    """Parse the URSEC ``.ods`` permit list into one `Record` per callsign."""
    with zipfile.ZipFile(path) as zf:
        content = zf.read("content.xml")
    rows = _rows(content)
    columns: dict[str, int] | None = None
    for row in rows:
        keys = {_key(v): i for i, v in enumerate(row) if v}
        if "distintivo de llamada" in keys:
            columns = {field: keys[h] for h, field in _HEADERS.items() if h in keys}
            break
    if columns is None or len(columns) != len(_HEADERS):
        raise ValueError(f"{path}: no header row with {sorted(_HEADERS)} (has the layout changed?)")

    best: dict[str, tuple[tuple[bool, str], Record]] = {}
    dropped = visitors = 0
    for row in rows:

        def cell(field: str, row: list[str] = row) -> str | None:
            i = columns[field]
            return (row[i] or None) if i < len(row) else None

        raw_call = cell("callsign")
        if raw_call is None:
            continue  # blank row or the edition footer
        callsign = raw_call.upper()
        if _RECIPROCAL.match(callsign):
            visitors += 1
            continue
        if not _CALLSIGN.match(callsign):
            dropped += 1
            continue
        expiry_cell = cell("expiry")
        expiry = (
            expiry_cell
            if expiry_cell and re.fullmatch(r"\d{4}-\d{2}-\d{2}", expiry_cell)
            else parse_date(expiry_cell)
        )
        given = cell("given")
        license_class = cell("class")
        record = Record(
            callsign=callsign,
            first_name=given,
            last_name=cell("surnames"),
            license_class=license_class.capitalize() if license_class else None,
            expired_date=expiry,
            applicant_type="Individual" if given else "Organization",
            source="ursec",
            synced_at=synced_at,
        )
        rank = ((cell("permit") or "").upper() != "IARP", expiry or "")
        if callsign not in best or rank > best[callsign][0]:
            best[callsign] = (rank, record)
    if dropped:
        print(f"WARNING: ursec: dropped {dropped} row(s) without a valid callsign", file=sys.stderr)
    if visitors:
        print(
            f"ursec: skipped {visitors} visiting-amateur <call>/CX row(s) "
            "(cty.dat would give them their home country)",
            file=sys.stderr,
        )
    for _rank, record in best.values():
        yield record


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        href = dict(attrs).get("href")
        if tag == "a" and href:
            self.links.append(href)


def find_ods_link(html: str, base_url: str) -> str:
    """The absolute URL of the first ``.ods`` download on the publishing page."""
    parser = _LinkParser()
    parser.feed(html)
    for href in parser.links:
        if urllib.parse.urlsplit(href).path.lower().endswith(".ods"):
            return urllib.parse.urljoin(base_url, href)
    raise ValueError("ursec: no .ods link on the registro-radioaficionados page")


class UrsecSource:
    """Importer for URSEC's list of amateur radio permits (Uruguay)."""

    name = "ursec"

    def __init__(self, *, opener: Opener | None = None, today: date | None = None) -> None:
        self.synced_at: str | None = None
        self._opener = opener or urllib.request.urlopen
        self._today = today

    def download(self, work_dir: Path) -> Path:
        """Find the current edition's ``.ods`` on the page and fetch it; return its path."""
        day_dir = raw_dir(work_dir, self.name, self._today)
        page = day_dir / PAGE_FILENAME
        fetch_cached(PAGE_URL, page, prior=None, opener=self._opener, timeout=DOWNLOAD_TIMEOUT)
        url = find_ods_link(page.read_text(encoding="utf-8"), PAGE_URL)
        filename = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])
        if not filename or "/" in filename or filename.startswith("."):
            raise ValueError(f"ursec: unexpected spreadsheet name in {url!r}")
        dest = day_dir / filename
        last_modified = fetch_cached(
            url,
            dest,
            prior=latest_prior(work_dir, self.name, filename, self._today),
            opener=self._opener,
            timeout=DOWNLOAD_TIMEOUT,
        )
        self.synced_at = synced_at_from(last_modified, dest)
        return dest

    def parse(self, path: Path, *, synced_at: str | None = None) -> Iterable[Record]:
        return parse_file(path, synced_at=synced_at if synced_at is not None else self.synced_at)
