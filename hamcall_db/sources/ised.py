"""ISED Canada amateur importer (CA).

Parses the Innovation, Science and Economic Development Canada (ISED, formerly
Industry Canada) amateur call sign list — the "delimited TXT" download published at
``apc-cap.ic.gc.ca/datafiles/amateur.zip``. The zip contains a single semicolon
(``;``) delimited ``amateur.txt`` with one row per assigned call sign (individuals and
sponsored clubs), carrying qualification flags, name, and a mailing address.

Column layout
-------------
The exact field order is not documented on the public ISED downloads page; it ships in a
README *inside the zip*. The constants below encode the long-standing community-documented
layout of the delimited extract:

    callsign ; qual_a ; qual_b ; qual_c ; qual_d ; qual_e ;
    first_name ; surname ; club_name_or_attn ; address ; city ; province ;
    postal_code ; country ; region

If a future download reorders columns, this is a one-line fix per constant — the parser
reads strictly by named index. The qualification columns are flag columns; a non-empty
value means the operator holds that qualification (Basic / Basic-with-Honours / Advanced
/ Morse, etc.). The ``address`` column is read *past* but NEVER stored — redistribution
contract (no street address in output, see CLAUDE.md / mem-c6e0).

Output leaves county (US-only), country/dxcc (cty.dat enriches downstream), and grid
(geocoded downstream) as None.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator
from pathlib import Path

from hamcall_db.models import Record
from hamcall_db.sources.base import synced_at_from

# Upstream delimited amateur extract (zip containing amateur.txt).
# https, not http: the http endpoint 301-redirects to https and its port 80 is
# unreachable from some networks (GitHub Actions runners timed out — hdb-f363).
DOWNLOAD_URL = "https://apc-cap.ic.gc.ca/datafiles/amateur.zip"

# Connect/read timeout (seconds) so a hung upstream fails fast instead of stalling.
DOWNLOAD_TIMEOUT = 60

# Field within the zip we parse.
AMATEUR_FILENAME = "amateur.txt"

# Record delimiter.
_DELIMITER = ";"

# 0-based column indices in the semicolon-delimited amateur record.
# ASSUMPTION: documented in the in-zip README; encoded here so a reorder is a 1-line fix.
_CALLSIGN = 0
# Qualification flag columns. ISED publishes one column per qualification; a non-empty
# value indicates the operator holds it. Order: Basic, Basic-w/-Honours, 5 wpm Morse,
# 12 wpm Morse, Advanced. Exact set/order varies; mapped by name below.
_QUAL_BASIC = 1
_QUAL_BASIC_HONOURS = 2
_QUAL_MORSE_5 = 3
_QUAL_MORSE_12 = 4
_QUAL_ADVANCED = 5
_FIRST_NAME = 6
_SURNAME = 7
_CLUB_NAME = 8  # club / care-of name; populated for sponsored-club call signs
_ADDRESS = 9  # street address — read past, NEVER stored
_CITY = 10
_PROVINCE = 11
_POSTAL_CODE = 12
_COUNTRY = 13
_REGION = 14

# Ordered (column-index, human-readable label) for qualification flags. Order here
# defines the order they appear in the composed license_class string.
_QUALIFICATIONS: tuple[tuple[int, str], ...] = (
    (_QUAL_BASIC, "Basic"),
    (_QUAL_BASIC_HONOURS, "Basic with Honours"),
    (_QUAL_MORSE_5, "Morse (5 wpm)"),
    (_QUAL_MORSE_12, "Morse (12 wpm)"),
    (_QUAL_ADVANCED, "Advanced"),
)


def _field(row: list[str], idx: int) -> str | None:
    """Return column `idx` of `row` stripped, or None if absent/blank."""
    if idx < len(row):
        value = row[idx].strip()
        return value or None
    return None


def _license_class(row: list[str]) -> str | None:
    """Compose a human-readable license class from the qualification flag columns.

    Each non-empty qualification column contributes its label. ``None`` when the
    operator holds no recorded qualifications (e.g. some club records).
    """
    held = [label for idx, label in _QUALIFICATIONS if _field(row, idx) is not None]
    return ", ".join(held) if held else None


def _to_record(row: list[str], *, synced_at: str | None) -> Record | None:
    """Map one delimited ISED row to a `Record`, or None if it has no callsign."""
    callsign = _field(row, _CALLSIGN)
    if callsign is None:
        return None

    first_name = _field(row, _FIRST_NAME)
    surname = _field(row, _SURNAME)
    # Sponsored clubs have no personal name; the entity lives in the club/attn column.
    # Fold it into last_name so consumers see a single display name.
    last_name = surname or _field(row, _CLUB_NAME)

    return Record(
        callsign=callsign,
        first_name=first_name,
        last_name=last_name,
        city=_field(row, _CITY),
        state=_field(row, _PROVINCE),  # province maps to the state/region slot
        postal_code=_field(row, _POSTAL_CODE),
        license_class=_license_class(row),
        source="ised",
        synced_at=synced_at,
        # county is US-only; country/dxcc come from cty.dat; grid is geocoded downstream.
    )


def parse_file(path: Path, *, synced_at: str | None = None) -> Iterator[Record]:
    """Parse a single semicolon-delimited ISED amateur file into `Record`s.

    Skips blank lines and rows lacking a callsign. `synced_at` stamps the upstream file
    date. The street-address column is read past and never emitted.
    """
    with path.open(encoding="latin-1") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            row = line.split(_DELIMITER)
            record = _to_record(row, synced_at=synced_at)
            if record is not None:
                yield record


def parse_dir(path: Path, *, synced_at: str | None = None) -> Iterator[Record]:
    """Parse an unzipped ISED extract directory into `Record`s.

    Locates ``amateur.txt`` within `path` (case-insensitively) and parses it.
    """
    amateur = path / AMATEUR_FILENAME
    if not amateur.exists():
        matches = [
            p for p in path.iterdir() if p.name.lower() == AMATEUR_FILENAME.lower()
        ]
        if not matches:
            raise FileNotFoundError(f"no {AMATEUR_FILENAME} under {path}")
        amateur = matches[0]
    yield from parse_file(amateur, synced_at=synced_at)


class IsedSource:
    """Importer for the ISED Canada amateur delimited call sign list."""

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
