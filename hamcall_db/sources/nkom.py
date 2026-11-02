"""Nkom (Norway) amateur call sign list importer (NO, plus Svalbard JW and Jan Mayen JX).

Nasjonal kommunikasjonsmyndighet (Nkom) publishes the list of Norwegian amateur call
signs on its radio amateur page, ``nkom.no/frekvenser-og-elektronisk-utstyr/radioamator``,
as two attachments: "Liste over norske kallesignaler <d.m.yyyy>.csv" and the same as
``.xlsx``. The attachment URL and filename change with every edition, so `download`
reads the page and follows the current CSV link (`find_csv_link`); the edition date in
the link text becomes ``synced_at`` (`edition_date`).

File shape
----------
One header row, then one ``;``-separated row per call sign, CRLF line endings, no
quoting. Checked against the 5.10.2026 edition (7,335 rows, 14 columns on every row)::

    Kallesignal;Organisasjon;Fornavn;Etternavn;Adresse;Adresse_2;Postnr;Poststed;
    Land;Type;Gyldigdato;Utløpsdato;Oppdatertdato;Kommentar

=================  ===================================================================
Column             Use
=================  ===================================================================
Kallesignal        callsign (stripped; some carry a trailing space)
Organisasjon       organisation name -> last_name, when filled
Fornavn/Etternavn  person first/last name; on an organisation row they name a contact
                   ("v/Ola", i.e. "attn. Ola") and are NOT published
Adresse(_2)        street address: compared against (address guard), NEVER stored
Postnr             4-digit Norwegian postcode -> postal_code (area-level)
Poststed           postal town -> city
Land               country of the postal address; used only to decide whether Postnr
                   and Poststed are Norwegian (see below)
Type               Personlig/Organisasjon/Skole/Bedrift/Myndighet/Forsvar ->
                   applicant_type, translated
Utløpsdato         expiry date (dd.mm.yyyy, 115 rows) -> expired_date
Gyldigdato,        empty on every row
Kommentar
Oppdatertdato      last-updated date; not used. It is a per-row edit stamp (2016 for
                   most rows, from the register migration) and says nothing about the
                   edition, so it never feeds ``synced_at``.
=================  ===================================================================

The list has no licence class, so ``license_class`` stays None; county/state are
None; country/dxcc come from cty.dat downstream; grid stays None (there are no
Norwegian geocoder tables).

Encoding
--------
The file is IBM code page 865 (DOS Nordic), not Windows-1252: ``ø`` is 0x9B, ``Ø``
0x9D, ``å`` 0x86, ``Å`` 0x8F, ``æ`` 0x91, ``Æ`` 0x92 (the header's "Utløpsdato" is
``Utl\\x9bpsdato``). 0x81, 0x8D, 0x8F, 0x90 and 0x9D are undefined in cp1252, which is
why iconv rejects the file as cp1252. cp865 defines all 256 byte values, so decoding
can't fail and no byte is ever replaced; 0xFF decodes to a no-break space, which
`_clean` turns into ordinary whitespace. `decode` picks the encoding from the header
bytes, so a future UTF-8 or cp1252 export decodes correctly too.

Rows
----
- Fully blank rows are skipped.
- A callsign that isn't Norwegian-shaped (`is_norwegian_callsign`) is dropped and
  counted on stderr. Every non-blank row in the 5.10.2026 edition passes.
- Duplicate callsigns keep the first row (none in the 5.10.2026 edition).
- About 70 rows have a foreign ``Land`` (Sweden, USA, Germany, ...). They are all
  LA/LB calls, Norwegian licences held by people living abroad, so they are kept. Only
  their location is withheld: abroad, ``Poststed`` is free text that often carries a
  full foreign postcode ("E1 3JU LONDON", "Utah 84105-3710, USA"), precise enough to
  sit next to a street address, so city and postal_code are None for them.
"""

from __future__ import annotations

import csv
import html
import io
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from datetime import date
from email.utils import formatdate
from pathlib import Path

from hamcall_db.address_guard import echoes_street, warn_scrubbed
from hamcall_db.models import Record
from hamcall_db.sources.base import synced_at_from

PAGE_URL = "https://nkom.no/frekvenser-og-elektronisk-utstyr/radioamator"

# Connect/read timeout (seconds) so a hung upstream fails fast instead of stalling.
DOWNLOAD_TIMEOUT = 60

_USER_AGENT = "hamcall-db (+https://github.com/rcludwick/hamcall-db)"

# Columns resolved by name from the header row; a missing one fails the parse.
_CALLSIGN = "Kallesignal"
_ORGANISATION = "Organisasjon"
_FIRST_NAME = "Fornavn"
_LAST_NAME = "Etternavn"
_STREET = "Adresse"  # compared against (address guard), NEVER stored
_STREET_2 = "Adresse_2"  # compared against (address guard), NEVER stored
_POSTCODE = "Postnr"
_TOWN = "Poststed"
_COUNTRY = "Land"
_TYPE = "Type"
_EXPIRY = "Utløpsdato"
_REQUIRED = (
    _CALLSIGN,
    _ORGANISATION,
    _FIRST_NAME,
    _LAST_NAME,
    _STREET,
    _STREET_2,
    _POSTCODE,
    _TOWN,
    _COUNTRY,
    _TYPE,
    _EXPIRY,
)

# Nkom's holder types, translated for applicant_type (ACMA uses "Person"/"Company" too).
# An unknown type passes through as written.
_TYPE_NAMES = {
    "personlig": "Person",
    "organisasjon": "Organisation",
    "skole": "School",
    "bedrift": "Company",
    "myndighet": "Government",
    "forsvar": "Military",
}

# Values of the Land column that mean Norway: blank (106 rows, all with Norwegian
# postcodes where any), "Norge" (with or without a trailing space), and the typo
# "Norger". Svalbard addresses also say "Norge".
_NORWAY = frozenset({"", "norge", "norger", "noreg", "norway"})

# A Norwegian postcode is exactly four digits. Anything else ("622", "24 180",
# "SE-670 10") is not published.
_POSTCODE_RE = re.compile(r"^\d{4}$")

# Norwegian amateur prefixes: LA-LN (ITU block; the list uses LA, LB, LG, LI, LJ, LM,
# and NRRL hands out LC contest calls), JW (Svalbard), JX (Jan Mayen) and 3Y (Bouvet,
# Peter I; none in the 5.10.2026 edition). Then 1-3 digits (special-event calls such as
# LA100IARU and LA202BV) and 1-4 letters (club calls such as LA1EGON).
_CALLSIGN_RE = re.compile(r"^(?:L[A-N]|JW|JX|3Y)[0-9]{1,3}[A-Z]{1,4}$")

_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")

# The edition date as written in the link text and filename: "5.10.2026".
_EDITION_RE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)")

_LINK_RE = re.compile(r"<a\b[^>]*?\bhref\s*=\s*([\"'])(.*?)\1[^>]*>(.*?)</a>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")

# fetch(url, if_modified_since_mtime) -> (body, Last-Modified header). body is None
# when the server answers 304 Not Modified. Injected by tests to avoid the network.
Fetcher = Callable[[str, float | None], tuple[bytes | None, str | None]]


def is_norwegian_callsign(callsign: str) -> bool:
    """True when `callsign` has the shape of a Norwegian-issued amateur call."""
    return bool(_CALLSIGN_RE.match(callsign))


def decode(raw: bytes) -> str:
    """Decode the CSV, choosing the encoding from how the header spells "Utløpsdato".

    Nkom exports cp865 (``ø`` = 0x9B). A UTF-8 file (with or without a BOM) and a
    cp1252/Latin-1 one (``ø`` = 0xF8) are recognised as well. cp865 is the default
    because it maps every byte, so decoding never fails or substitutes characters.
    """
    header = raw.split(b"\n", 1)[0]
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8")
    if "ø".encode() in header:
        return raw.decode("utf-8")
    if b"\xf8" in header:
        return raw.decode("cp1252", errors="replace")
    return raw.decode("cp865")


def _clean(value: str | None) -> str | None:
    """Strip a field (no-break spaces included); empty becomes None."""
    if value is None:
        return None
    value = " ".join(value.replace("\xa0", " ").split())
    return value or None


def _iso_date(value: str | None) -> str | None:
    """``dd.mm.yyyy`` -> ``yyyy-mm-dd``; anything else (a town, a typo) -> None."""
    match = _DATE_RE.match(value or "")
    if not match:
        return None
    day, month, year = (int(g) for g in match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def edition_date(text: str) -> str | None:
    """ISO date of the edition named in a link text or filename ("... 5.10.2026.csv")."""
    for match in _EDITION_RE.finditer(urllib.parse.unquote(text)):
        day, month, year = (int(g) for g in match.groups())
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            continue
    return None


def find_csv_link(page: str, base_url: str = PAGE_URL) -> tuple[str, str]:
    """Return (absolute URL, link text) of the call sign list CSV on Nkom's page.

    The link is the ``.csv`` anchor whose URL or text mentions "kallesignaler". Raises
    ValueError when there is none, so a page redesign fails the source loudly.
    """
    for match in _LINK_RE.finditer(page):
        href = html.unescape(match.group(2)).strip()
        text = " ".join(html.unescape(_TAG_RE.sub(" ", match.group(3))).split())
        path = urllib.parse.unquote(urllib.parse.urlsplit(href).path)
        if path.lower().endswith(".csv") and "kallesignaler" in (path + " " + text).lower():
            return urllib.parse.urljoin(base_url, href), text
    raise ValueError(f"{base_url}: no 'Liste over norske kallesignaler' .csv link found")


def _to_record(row: dict[str, str], *, synced_at: str | None) -> tuple[Record, int]:
    """Map one CSV row to a `Record`; also return how many values the guard dropped."""
    organisation = _clean(row[_ORGANISATION])
    if organisation is not None:
        # The holder is the organisation; Fornavn/Etternavn name its contact person.
        first_name, last_name = None, organisation
    else:
        first_name, last_name = _clean(row[_FIRST_NAME]), _clean(row[_LAST_NAME])

    in_norway = (_clean(row[_COUNTRY]) or "").lower() in _NORWAY
    town = _clean(row[_TOWN]) if in_norway else None
    postcode = _clean(row[_POSTCODE]) if in_norway else None
    if postcode is not None and not _POSTCODE_RE.match(postcode):
        postcode = None

    # The street columns are compared against, never stored (hdb-addrguard).
    streets = (_clean(row[_STREET]), _clean(row[_STREET_2]))
    published = [first_name, last_name, town]
    kept = [None if echoes_street(value, streets) else value for value in published]
    scrubbed = sum(a is not None and b is None for a, b in zip(published, kept, strict=True))
    first_name, last_name, town = kept

    holder_type = _clean(row[_TYPE])
    record = Record(
        callsign=row[_CALLSIGN].strip().upper(),
        first_name=first_name,
        last_name=last_name,
        city=town,
        postal_code=postcode,
        source="nkom",
        synced_at=synced_at,
        expired_date=_iso_date(_clean(row[_EXPIRY])),
        applicant_type=_TYPE_NAMES.get(holder_type.lower(), holder_type) if holder_type else None,
    )
    return record, scrubbed


def parse_file(path: Path, *, synced_at: str | None = None) -> Iterator[Record]:
    """Parse Nkom's call sign list CSV into `Record`s (see the module docstring)."""
    reader = csv.DictReader(
        io.StringIO(decode(path.read_bytes()), newline=""), delimiter=";", quoting=csv.QUOTE_NONE
    )
    missing = [c for c in _REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(
            f"{path}: missing column(s) {', '.join(missing)} "
            "(has the Nkom call sign list layout changed?)"
        )
    seen: set[str] = set()
    scrubbed = bad_shape = duplicates = 0
    for row in reader:
        row = {k: v or "" for k, v in row.items() if k is not None}
        callsign = row[_CALLSIGN].strip().upper()
        if not callsign:
            continue  # the blank row(s)
        if not is_norwegian_callsign(callsign):
            bad_shape += 1
            continue
        if callsign in seen:
            duplicates += 1
            continue
        seen.add(callsign)
        record, dropped = _to_record(row, synced_at=synced_at)
        scrubbed += dropped
        yield record
    if bad_shape:
        print(
            f"WARNING: nkom: dropped {bad_shape} row(s) whose callsign isn't Norwegian-shaped",
            file=sys.stderr,
        )
    if duplicates:
        print(f"WARNING: nkom: dropped {duplicates} duplicate callsign row(s)", file=sys.stderr)
    warn_scrubbed("nkom", scrubbed)


def _urllib_fetch(url: str, since: float | None) -> tuple[bytes | None, str | None]:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    if since is not None:
        request.add_header("If-Modified-Since", formatdate(since, usegmt=True))
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:  # noqa: S310
            return response.read(), response.headers.get("Last-Modified")
    except urllib.error.HTTPError as exc:
        if exc.code == 304:  # unchanged: caller reuses the cache
            return None, None
        raise


class NkomSource:
    """Importer for Nkom's list of Norwegian amateur call signs (CSV)."""

    name = "nkom"

    def __init__(self, *, fetcher: Fetcher | None = None, today: str | None = None) -> None:
        # ISO date of the edition; set during download(), stamped onto Records.
        self.synced_at: str | None = None
        self._fetch = fetcher or _urllib_fetch
        self._today = today

    def download(self, work_dir: Path) -> Path:
        """Find the current CSV on Nkom's page and cache it, returning its path.

        Caches under ``work_dir/data/raw/nkom/<YYYY-MM-DD>/<edition filename>`` and
        sends If-Modified-Since when that file is already cached (a 304 reuses it).
        HTTP errors propagate so the build's shared retry (`retry_transient`) can retry
        transient ones. ``synced_at`` is the edition date from the link text or
        filename, else the Last-Modified header, else the cached file's mtime.
        """
        page, _ = self._fetch(PAGE_URL, None)
        if page is None:
            raise RuntimeError(f"{PAGE_URL} returned not-modified to an unconditional GET")
        url, text = find_csv_link(page.decode("utf-8", errors="replace"))

        filename = Path(urllib.parse.unquote(urllib.parse.urlsplit(url).path)).name
        day = self._today or date.today().isoformat()
        dest = work_dir / "data" / "raw" / "nkom" / day / filename
        dest.parent.mkdir(parents=True, exist_ok=True)

        since = dest.stat().st_mtime if dest.exists() else None
        body, last_modified = self._fetch(url, since)
        if body is not None:
            dest.write_bytes(body)
        elif not dest.exists():
            raise RuntimeError(f"{url} returned not-modified but nothing is cached at {dest}")

        self.synced_at = (
            edition_date(text) or edition_date(filename) or synced_at_from(last_modified, dest)
        )
        return dest

    def parse(self, path: Path, *, synced_at: str | None = None) -> Iterable[Record]:
        return parse_file(path, synced_at=synced_at if synced_at is not None else self.synced_at)
