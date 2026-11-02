"""NMHH amateur call sign book importer (Hungary).

The Hungarian regulator, the National Media and Infocommunications Authority (Nemzeti
Média- és Hírközlési Hatóság, NMHH), publishes its amateur call sign book at
``https://nmhh.hu/amator/call_sign_book.xml``, linked from
``https://nmhh.hu/cikk/162176``. It is refreshed daily (the 2026-11-01 copy had 3,246
rows) and the server answers ``If-Modified-Since`` with ``304 Not Modified``.

File shape
----------
The file is an Excel 2003 XML workbook ("SpreadsheetML"), encoded ISO-8859-2 as its
XML declaration says. ``Sheet1`` holds one ``<Row>`` per call sign; ``Sheet2`` and
``Sheet3`` are empty. The first row is a bilingual header::

    Hívójel / Call sign | Név / Name of Licensee | Ország / Country | Város /City |
    utca | Engedély száma / License Number | Érvényes / Validity | Eng.Kat. |
    Morze / Morse | Felelős kezelő | Eng.Státusz

Columns are found by header text, not position, so a reordered file still parses and a
renamed one fails loudly.

Field mapping
-------------
=========================  =======================================================
NMHH column                Record field
=========================  =======================================================
Hívójel / Call sign        ``callsign``
Név / Name of Licensee     ``last_name`` / ``first_name`` (see `split_name`); a club's
                           whole name goes in ``last_name``
Város / City               ``city``
Érvényes / Validity        ``expired_date`` (ISO date)
Eng.Kat. + Morze / Morse   ``license_class``, e.g. ``"General, Morse"``
=========================  =======================================================

Values seen in 2026-11 for ``Eng.Kat.``: General, Contest, Relay, Occasional,
Dir. xmitter, Gate. ``Morze / Morse`` is either empty or ``Morse``.

What is dropped, and why:

- ``utca`` (street name, no house number): never stored. It is read only so the
  address guard can drop a published field that copies it.
- ``Felelős kezelő`` (responsible operator): a second, different person's name. It is
  only used to tell a club station apart from a personal one, never published.
- ``Engedély száma`` (licence number): no column for it in the schema.
- ``Ország / Country`` (country of residence): ``Record.country`` is the callsign's DXCC
  entity, filled from cty.dat downstream, so this does not belong there.
- ``Eng.Státusz``: only ``Kiadott`` ("issued") rows are published. The other value seen,
  ``Ügyintézés alatt`` ("being processed"), is an application, not a licence.

The file has no postal code, so ``postal_code`` stays None, and ``grid`` is left to the
downstream geocoder (which has no Hungarian tables yet, so it stays None too).

Clubs
-----
A row whose responsible operator is filled in and differs from the licensee name is a
club or community station (``Magyar Rádióamatőr Szövetség``, ``... Rádióklub``). Its
name is an organisation, so it goes whole into ``last_name`` and ``applicant_type`` is
``"Club"``. A personal contest call lists its own holder as responsible operator, so it
stays a person.
"""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Iterator
from datetime import date
from email.utils import formatdate
from pathlib import Path

from hamcall_db.address_guard import echoes_street, warn_scrubbed
from hamcall_db.models import Record
from hamcall_db.sources.base import synced_at_from

DOWNLOAD_URL = "https://nmhh.hu/amator/call_sign_book.xml"

# Connect/read timeout (seconds) so a hung upstream fails fast instead of stalling.
DOWNLOAD_TIMEOUT = 60

_USER_AGENT = "hamcall-db/0 (+https://github.com/rcludwick/hamcall-db)"

FILENAME = "call_sign_book.xml"

# Injectable fetch seam: (url, if_modified_since_epoch_or_None) -> (body or None for
# "304 Not Modified", the Last-Modified header or None).
Fetcher = Callable[[str, "float | None"], "tuple[bytes | None, str | None]"]

_SS = "{urn:schemas-microsoft-com:office:spreadsheet}"

# Header cell text (case-insensitive substring) for each column we read.
_HEADERS: dict[str, str] = {
    "callsign": "call sign",
    "name": "name of licensee",
    "city": "city",
    "street": "utca",
    "validity": "validity",
    "class": "eng.kat",
    "morse": "morse",
    "responsible": "felelős kezelő",
    "status": "státusz",
}

# The one status that means a licence is in force.
ACTIVE_STATUS = "Kiadott"

# A Hungarian amateur call: HA or HG, a number, a letter, then letters/digits.
CALLSIGN_RE = re.compile(r"^H[AG][0-9]+[A-Z][A-Z0-9]*$")

# Leading honorifics and generational markers dropped before splitting a name:
# Dr., Prof., id. (senior), ifj. (junior), özv. (widow of).
_PREFIXES: frozenset[str] = frozenset({"dr", "prof", "id", "ifj", "özv"})

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def split_name(name: str | None) -> tuple[str | None, str | None]:
    """Split a Hungarian personal name into ``(first_name, last_name)``.

    Hungarian names are written family name first ("Példa Ödön"), and NMHH writes
    foreign holders the same way most of the time. Leading honorifics (``Dr.``,
    ``ifj.``) are dropped, the first remaining word is the family name, and the rest
    are given names. A single word is a given name: NMHH lists some holders by given
    name only. Western-order foreign names ("Hans Beispiel") come out swapped; that is
    an accepted imperfection, since the file gives no way to tell them apart.
    """
    words = (name or "").split()
    while words and words[0].rstrip(".").casefold() in _PREFIXES:
        words = words[1:]
    if not words:
        return None, None
    if len(words) == 1:
        return words[0], None
    return " ".join(words[1:]), words[0]


def _rows(path: Path) -> Iterator[list[str]]:
    """Yield each worksheet row of ``Sheet1`` as a list of cell strings."""
    raw = path.read_bytes()
    # Refuse DTDs: the file never has one, and entity expansion is an XML attack vector.
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise ValueError(f"{path}: refusing SpreadsheetML with a DOCTYPE/ENTITY declaration")
    root = ET.fromstring(raw)  # noqa: S314 (no DTD allowed, see above)
    sheet = root.find(f"{_SS}Worksheet")
    if sheet is None:
        raise ValueError(f"{path}: no Worksheet (is this still SpreadsheetML?)")
    for row in sheet.iter(f"{_SS}Row"):
        cells: list[str] = []
        for cell in row.findall(f"{_SS}Cell"):
            index = cell.get(f"{_SS}Index")  # sparse rows skip empty cells
            if index is not None:
                cells.extend([""] * (int(index) - 1 - len(cells)))
            data = cell.find(f"{_SS}Data")
            cells.append("".join(data.itertext()).strip() if data is not None else "")
        yield cells


def _columns(header: list[str], path: Path) -> dict[str, int]:
    lowered = [h.casefold() for h in header]
    columns: dict[str, int] = {}
    for key, label in _HEADERS.items():
        matches = [i for i, h in enumerate(lowered) if label in h]
        if not matches:
            raise ValueError(
                f"{path}: header has no {label!r} column (has the NMHH layout changed?): {header!r}"
            )
        columns[key] = matches[0]
    return columns


def _echoes(value: str | None, street: str | None) -> bool:
    """True when ``value`` copies the row's street.

    NMHH's street column has no house number, so most streets ("Fenyves sor") are not
    address-shaped and `echoes_street` ignores them; an exact match (case-insensitive)
    is caught here as well.
    """
    if not value or not street:
        return False
    return echoes_street(value, (street,)) or value.casefold() == street.casefold()


def parse_file(path: Path, *, synced_at: str | None = None) -> Iterator[Record]:
    """Parse NMHH's ``call_sign_book.xml`` into `Record`s, one per issued callsign.

    Duplicate callsigns keep the row with the latest validity date. Rows whose
    callsign isn't Hungarian-shaped are skipped and reported on stderr.
    """
    rows = _rows(path)
    header = next(rows, None)
    if header is None:
        raise ValueError(f"{path}: empty worksheet")
    col = _columns(header, path)

    def cell(row: list[str], key: str) -> str | None:
        i = col[key]
        value = row[i].strip() if i < len(row) else ""
        return value or None

    best: dict[str, Record] = {}
    bad_calls: list[str] = []
    scrubbed = 0
    for row in rows:
        if not any(row):
            continue
        if cell(row, "status") != ACTIVE_STATUS:
            continue
        callsign = (cell(row, "callsign") or "").upper()
        if not CALLSIGN_RE.match(callsign):
            bad_calls.append(callsign)
            continue

        name = cell(row, "name")
        responsible = cell(row, "responsible")
        is_club = bool(name and responsible and responsible.casefold() != name.casefold())
        first, last = (None, name) if is_club else split_name(name)

        # The street is compared against, never stored (hdb-addrguard).
        street = cell(row, "street")
        published = [first, last, cell(row, "city")]
        kept = [None if _echoes(v, street) else v for v in published]
        scrubbed += sum(a is not None and b is None for a, b in zip(published, kept, strict=True))
        first, last, city = kept

        validity = cell(row, "validity")
        expired = validity[:10] if validity and _ISO_DATE.match(validity) else None
        klass = ", ".join(v for v in (cell(row, "class"), cell(row, "morse")) if v) or None

        record = Record(
            callsign=callsign,
            first_name=first,
            last_name=last,
            city=city,
            license_class=klass,
            expired_date=expired,
            applicant_type="Club" if is_club else None,
            source="nmhh",
            synced_at=synced_at,
        )
        existing = best.get(callsign)
        if existing is None or (expired or "") > (existing.expired_date or ""):
            best[callsign] = record

    if bad_calls:
        print(
            f"WARNING: nmhh: skipped {len(bad_calls)} row(s) with a non-Hungarian "
            f"callsign: {', '.join(bad_calls[:10])}",
            file=sys.stderr,
        )
    warn_scrubbed("nmhh", scrubbed)
    yield from best.values()


def _urllib_fetch(url: str, if_modified_since: float | None) -> tuple[bytes | None, str | None]:
    """Default fetcher: conditional GET via urllib. A None body means 304."""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    if if_modified_since is not None:
        request.add_header("If-Modified-Since", formatdate(if_modified_since, usegmt=True))
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:  # noqa: S310
            return response.read(), response.headers.get("Last-Modified")
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return None, None
        raise


class NmhhSource:
    """Importer for the NMHH (Hungary) amateur call sign book."""

    name = "nmhh"

    def __init__(
        self,
        *,
        fetcher: Fetcher | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.synced_at: str | None = None
        self._fetch = fetcher or _urllib_fetch
        self._today = today

    def download(self, work_dir: Path) -> Path:
        """Fetch the call sign book into ``work_dir/data/raw/nmhh/<date>/``.

        Sends If-Modified-Since when today's copy is already cached and reuses it on a
        304. Records the upstream date (Last-Modified, else the file's mtime) in
        `synced_at`. Retries on transient failures are the build's job
        (`base.retry_transient`).
        """
        dest = work_dir / "data" / "raw" / "nmhh" / self._today().isoformat() / FILENAME
        dest.parent.mkdir(parents=True, exist_ok=True)
        since = dest.stat().st_mtime if dest.exists() else None
        body, last_modified = self._fetch(DOWNLOAD_URL, since)
        if body is not None:
            tmp = dest.with_suffix(".part")
            tmp.write_bytes(body)
            tmp.replace(dest)
        elif not dest.exists():
            raise RuntimeError(f"{DOWNLOAD_URL} returned not-modified but nothing is cached")
        self.synced_at = synced_at_from(last_modified, dest)
        return dest

    def parse(self, path: Path, *, synced_at: str | None = None) -> Iterable[Record]:
        return parse_file(path, synced_at=synced_at if synced_at is not None else self.synced_at)
