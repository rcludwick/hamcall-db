"""ISED Canada amateur importer (CA).

Parses the Innovation, Science and Economic Development Canada (ISED, formerly
Industry Canada) amateur call sign list published at
``apc-cap.ic.gc.ca/datafiles/amateur.zip``. The zip holds three files:

- ``amateur.txt`` — the call sign list itself, a **fixed-width** report (UTF-8, CRLF);
- ``readme_amat.txt`` / ``lisezmoi_amat.txt`` — the column layout, in English / French.

There is no delimited variant. (Until 2026-11 this importer assumed a ``;``-delimited
file that ISED never shipped, so every line landed whole in ``callsign`` — hdb-ised.)

File shape
----------
::

    2026-11-01  Canadian Amateur Call Sign List / List d'indicatif d'appel canadien
    <blank>
    There are 92186 call signs in this report. / Il y a 92186 indicatifs ...
    <blank>
    VA1AA  Bill        McFadden    100 FAKE DRIVE        MIDDLE SACKVILLE  NS B4E 2X8 A C D
    ...one fixed-width row per call sign...
    EOF

Column layout
-------------
Copied from ``readme_amat.txt`` (1-based start column, length). Columns are counted in
*characters* of the decoded text, not bytes. Verified against the whole 2026-11-01 file
(92,186 rows): every column between these fields is a space on every row, and rows are
exactly 210 characters (no club) or 469 (club).

=============================  =====  ======
Field                          Start  Length
=============================  =====  ======
Callsign                           1       6
Given Names                        8      35
Surname                           44      35
Street Address                    80      70   (read past, NEVER stored)
City                             151      35
Province                         187       2
Postal/ZIP Code                  190      10
BASIC Qualification (A)          201       1
5WPM Qualification (B)           203       1
12WPM Qualification (C)          205       1
ADVANCED Qualification (D)       207       1
Basic with Honours (E)           209       1
Club Name (field 1)              211      70
Club Name (field 2)              282      70   (not published; often "c/o <person>")
Club Address                     353      70   (read past, NEVER stored)
Club City                        424      35
Club Province                    460       2
Club Postal/ZIP Code             463       7
=============================  =====  ======

A row with a club name is a club call: the given/surname/address on that row belong to
the trustee, so the Record takes the club name and the club's city/province/postal
code instead. Placeholder values with no letters or digits (``**``, ``-``) become None.
ISED escapes apostrophes as HTML numeric entities (``&#x27;``); those are
decoded.

The street-address columns are never sliced into any Record field — redistribution
contract (no street address in output, see CLAUDE.md / mem-c6e0). Output leaves county
(US-only), country/dxcc (cty.dat enriches downstream), and grid (geocoded downstream)
as None.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator
from pathlib import Path

from hamcall_db.models import Record
from hamcall_db.sources.base import synced_at_from

# Upstream amateur call sign list (zip containing amateur.txt).
# https, not http: the http endpoint 301-redirects to https and its port 80 is
# unreachable from some networks (GitHub Actions runners timed out — hdb-f363).
DOWNLOAD_URL = "https://apc-cap.ic.gc.ca/datafiles/amateur.zip"

# Connect/read timeout (seconds) so a hung upstream fails fast instead of stalling.
DOWNLOAD_TIMEOUT = 60

# File within the zip we parse.
AMATEUR_FILENAME = "amateur.txt"


def _col(start: int, length: int) -> slice:
    """Slice for a field given readme_amat.txt's 1-based start column and length."""
    return slice(start - 1, start - 1 + length)


_CALLSIGN = _col(1, 6)
_GIVEN_NAMES = _col(8, 35)
_SURNAME = _col(44, 35)
# _col(80, 70) is the street address: deliberately not defined, never read.
_CITY = _col(151, 35)
_PROVINCE = _col(187, 2)
_POSTAL_CODE = _col(190, 10)
_CLUB_NAME = _col(211, 70)
# _col(282, 70) is club name field 2 (free text, often "c/o <person>"): not published.
# _col(353, 70) is the club street address: deliberately not defined, never read.
_CLUB_CITY = _col(424, 35)
_CLUB_PROVINCE = _col(460, 2)
_CLUB_POSTAL_CODE = _col(463, 7)

# A data row must reach at least the last qualification column (209).
_MIN_ROW_LENGTH = 209

# Qualification flag columns (a letter when held, blank otherwise). Order here defines
# the order labels appear in the composed license_class string.
_QUALIFICATIONS: tuple[tuple[slice, str], ...] = (
    (_col(201, 1), "Basic"),  # A
    (_col(209, 1), "Basic with Honours"),  # E
    (_col(203, 1), "Morse (5 wpm)"),  # B
    (_col(205, 1), "Morse (12 wpm)"),  # C
    (_col(207, 1), "Advanced"),  # D
)

# A data row's callsign field: letters and digits with at least one digit, left-aligned
# and space-padded to six characters, followed by the space at column 7.
_DATA_ROW = re.compile(r"^(?=[A-Z0-9]*[0-9])[A-Z0-9]{3,6} *(?<=^.{6}) ")

# Non-data lines the report wraps around its rows.
_HEADER = re.compile(r"^\d{4}-\d{2}-\d{2}\s+Canadian Amateur Call Sign List", re.IGNORECASE)
_COUNT = re.compile(r"^There are \d+ call signs in this report", re.IGNORECASE)
_EOF = "EOF"

_HAS_WORD = re.compile(r"\w")
_NUMERIC_ENTITY = re.compile(r"&#(x[0-9a-fA-F]+|[0-9]+);")


def _unescape(match: re.Match[str]) -> str:
    ref = match.group(1)
    return chr(int(ref[1:], 16) if ref[0] == "x" else int(ref))


def _field(line: str, col: slice) -> str | None:
    """Return the fixed-width field at `col`, stripped and entity-decoded, or None.

    Placeholders with no letters or digits (ISED uses ``**``, ``-``, ``--`` for some
    unknown cities) are None too.
    """
    value = _NUMERIC_ENTITY.sub(_unescape, line[col]).strip()
    return value if _HAS_WORD.search(value) else None


def _license_class(line: str) -> str | None:
    """Compose a human-readable license class from the qualification flag columns."""
    held = [label for col, label in _QUALIFICATIONS if line[col].strip()]
    return ", ".join(held) if held else None


def _to_record(line: str, *, synced_at: str | None) -> Record:
    """Map one fixed-width ISED data row to a `Record`."""
    callsign = line[_CALLSIGN].strip()  # non-empty: guaranteed by _DATA_ROW
    club_name = _field(line, _CLUB_NAME)
    if club_name is not None:
        # Club call: the holder is the club, located at the club's mailing address.
        first_name, last_name = None, club_name
        city, state, postal = _CLUB_CITY, _CLUB_PROVINCE, _CLUB_POSTAL_CODE
    else:
        first_name, last_name = _field(line, _GIVEN_NAMES), _field(line, _SURNAME)
        city, state, postal = _CITY, _PROVINCE, _POSTAL_CODE

    return Record(
        callsign=callsign,
        first_name=first_name,
        last_name=last_name,
        city=_field(line, city),
        state=_field(line, state),  # province maps to the state/region slot
        postal_code=_field(line, postal),
        license_class=_license_class(line),
        source="ised",
        synced_at=synced_at,
        # county is US-only; country/dxcc come from cty.dat; grid is geocoded downstream.
    )


def _read_text(path: Path) -> str:
    """Decode the report: UTF-8 (what ISED serves), falling back to Latin-1."""
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def parse_file(path: Path, *, synced_at: str | None = None) -> Iterator[Record]:
    """Parse ISED's fixed-width ``amateur.txt`` report into `Record`s.

    Skips blank lines, the dated report header, the call-sign count line, and the
    trailing ``EOF``. Any other line that isn't a fixed-width data row raises
    ValueError, so a format change fails the build instead of publishing garbage.
    `synced_at` stamps the upstream file date. Street addresses are never read.
    """
    for lineno, line in enumerate(_read_text(path).splitlines(), start=1):
        if not line.strip():
            continue
        if _DATA_ROW.match(line) and len(line) >= _MIN_ROW_LENGTH:
            yield _to_record(line, synced_at=synced_at)
        elif line.strip() == _EOF or _HEADER.match(line) or _COUNT.match(line):
            continue
        else:
            raise ValueError(
                f"{path}: line {lineno} is not an ISED fixed-width call sign row "
                f"(has the amateur.txt layout changed?): {line[:40]!r}"
            )


def parse_dir(path: Path, *, synced_at: str | None = None) -> Iterator[Record]:
    """Parse an unzipped ISED extract directory into `Record`s.

    Locates ``amateur.txt`` within `path` (case-insensitively) and parses it.
    """
    amateur = path / AMATEUR_FILENAME
    if not amateur.exists():
        matches = [p for p in path.iterdir() if p.name.lower() == AMATEUR_FILENAME.lower()]
        if not matches:
            raise FileNotFoundError(f"no {AMATEUR_FILENAME} under {path}")
        amateur = matches[0]
    yield from parse_file(amateur, synced_at=synced_at)


class IsedSource:
    """Importer for the ISED Canada amateur call sign list (fixed-width report)."""

    name = "ised"

    def __init__(self) -> None:
        # ISO date of the upstream extract; set during download(), stamped onto Records.
        self.synced_at: str | None = None

    def download(self, work_dir: Path) -> Path:
        """Fetch + unzip amateur.zip into work_dir, returning the extract directory.

        Caches the raw zip; honors If-Modified-Since when a cached copy exists so we
        don't re-pull an unchanged extract. Be polite to upstream. Records the upstream
        file date (Last-Modified header, else the zip's mtime) in `synced_at`.
        """
        import zipfile
        from email.utils import formatdate

        raw_zip = work_dir / "amateur.zip"
        extract_dir = work_dir / "amateur"
        work_dir.mkdir(parents=True, exist_ok=True)

        request = urllib.request.Request(DOWNLOAD_URL)
        if raw_zip.exists():
            request.add_header(
                "If-Modified-Since", formatdate(raw_zip.stat().st_mtime, usegmt=True)
            )
        last_modified: str | None = None
        try:
            with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:  # noqa: S310
                last_modified = response.headers.get("Last-Modified")
                raw_zip.write_bytes(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code != 304 or not raw_zip.exists():  # 304 = unchanged, use cache
                raise

        self.synced_at = synced_at_from(last_modified, raw_zip)

        extract_dir.mkdir(exist_ok=True)
        with zipfile.ZipFile(raw_zip) as zf:
            zf.extractall(extract_dir)
        return extract_dir

    def parse(self, path: Path, *, synced_at: str | None = None) -> Iterable[Record]:
        return parse_dir(path, synced_at=synced_at if synced_at is not None else self.synced_at)
