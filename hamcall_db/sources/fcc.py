"""FCC ULS amateur importer (US).

Parses the FCC Universal Licensing System amateur bulk extract (``l_amat.zip``), a set
of pipe-delimited ``.dat`` files joined on the unique-system-identifier (USI):

- HD.dat — license header: call sign, license status (filter to active), and the
  grant / effective / expired dates (normalized to ISO).
- EN.dat — entity: licensee name + mailing address (we keep city/state/zip; the street
  address is read past but NEVER stored — redistribution contract), plus the FRN,
  entity-type code, and applicant-type code (mapped to a readable value).
- AM.dat — amateur: operator class (mapped to a human-readable license class) and the
  previous call sign held.

The expired date is surfaced as a plain field; active/expired STATUS derivation is a
separate concern and is intentionally NOT done here.

Column positions are stable in the ULS public format; they live in named constants
below so a future format revision is a one-line change. See FCC "Public Access Database
Definitions". Output leaves county/country/dxcc/grid for downstream stages (cty.dat
enrichment in au-9ed1, geocoding in au-76be).
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator
from pathlib import Path

from hamcall_db.models import Record
from hamcall_db.sources.base import synced_at_from

# Upstream amateur bulk extract.
DOWNLOAD_URL = "https://data.fcc.gov/download/pub/uls/complete/l_amat.zip"

# 0-based column indices within each pipe-delimited record type. Positions are 1 less
# than the (1-based) FCC "Public Access Database Definitions" field numbers.
_HD_CALLSIGN = 4
_HD_LICENSE_STATUS = 5
_HD_GRANT_DATE = 7  # FCC HD position 8
_HD_EXPIRED_DATE = 8  # FCC HD position 9
_HD_EFFECTIVE_DATE = 42  # FCC HD position 43
_EN_CALLSIGN = 4
_EN_ENTITY_TYPE = 5  # FCC EN position 6
_EN_FIRST_NAME = 8
_EN_LAST_NAME = 10
_EN_CITY = 16
_EN_STATE = 17
_EN_ZIP = 18
_EN_FRN = 22  # FCC EN position 23 (FCC Registration Number)
_EN_APPLICANT_TYPE = 23  # FCC EN position 24 (applicant_type_code)
_AM_CALLSIGN = 4
_AM_OPERATOR_CLASS = 5
# previous_callsign lives on the amateur-specific AM record (FCC AM position 16), NOT HD.
_AM_PREVIOUS_CALLSIGN = 15

_HD_USI = _EN_USI = _AM_USI = 1  # unique system identifier (join key)

# 'A' = active. Other values (E=expired, C=cancelled, T=terminated, ...) are non-active.
_ACTIVE_STATUS = "A"

# FCC operator class single-letter codes -> human-readable license class.
OPERATOR_CLASS = {
    "E": "Amateur Extra",
    "A": "Advanced",
    "G": "General",
    "T": "Technician",
    "P": "Technician Plus",
    "N": "Novice",
}

# FCC EN applicant_type_code -> readable value. Small/stable set per the FCC code tables.
# Unmapped codes pass through verbatim (forward-compatible with new FCC additions).
APPLICANT_TYPE = {
    "I": "Individual",
    "B": "Amateur Club",
    "M": "Military Recreation",
    "R": "RACES",
    "C": "Corporation",
    "G": "Governmental Entity",
    "E": "Limited Liability Company",
    "J": "Joint Venture",
    "L": "Limited Liability Corporation",
    "O": "Consortium",
    "P": "Partnership",
    "T": "Trust",
    "U": "Unincorporated Association",
}


def _iso_date(value: str | None) -> str | None:
    """Normalize a ULS ``mm/dd/yyyy`` date to ISO ``YYYY-MM-DD``.

    Returns None for a blank/absent value, and passes an unrecognized format through
    unchanged rather than dropping data.
    """
    if value is None:
        return None
    parts = value.split("/")
    if len(parts) == 3 and all(parts):
        month, day, year = parts
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    return value


def _read_dat(path: Path) -> Iterator[list[str]]:
    """Yield pipe-split fields for each non-empty line of a ULS .dat file."""
    with path.open(encoding="latin-1") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line:
                yield line.split("|")


def _field(row: list[str], idx: int) -> str | None:
    """Return column `idx` of `row`, or None if absent/blank."""
    if idx < len(row):
        value = row[idx].strip()
        return value or None
    return None


def parse_dir(
    path: Path,
    *,
    active_only: bool = True,
    synced_at: str | None = None,
) -> Iterator[Record]:
    """Parse an unzipped FCC ULS amateur extract directory into `Record`s.

    Joins HD/EN/AM on the unique system identifier. By default only active licenses
    (HD license_status == 'A') are emitted. `synced_at` stamps the upstream file date.
    """
    # HD gives the authoritative set of licenses + their status and grant/effective/
    # expired dates (normalized to ISO at emit time).
    statuses: dict[str, str | None] = {}
    hd_dates: dict[str, tuple[str | None, str | None, str | None]] = {}
    for row in _read_dat(path / "HD.dat"):
        usi = _field(row, _HD_USI)
        if usi is not None:
            statuses[usi] = _field(row, _HD_LICENSE_STATUS)
            hd_dates[usi] = (
                _field(row, _HD_GRANT_DATE),
                _field(row, _HD_EFFECTIVE_DATE),
                _field(row, _HD_EXPIRED_DATE),
            )

    # AM gives operator class + previous callsign per USI.
    operator_classes: dict[str, str | None] = {}
    previous_callsigns: dict[str, str | None] = {}
    for row in _read_dat(path / "AM.dat"):
        usi = _field(row, _AM_USI)
        if usi is not None:
            operator_classes[usi] = _field(row, _AM_OPERATOR_CLASS)
            previous_callsigns[usi] = _field(row, _AM_PREVIOUS_CALLSIGN)

    # EN carries the licensee identity + location; it drives record emission.
    for row in _read_dat(path / "EN.dat"):
        usi = _field(row, _EN_USI)
        if usi is None or usi not in statuses:
            continue
        if active_only and statuses[usi] != _ACTIVE_STATUS:
            continue
        callsign = _field(row, _EN_CALLSIGN)
        if callsign is None:
            continue
        op_class = operator_classes.get(usi)
        grant_date, effective_date, expired_date = hd_dates.get(usi, (None, None, None))
        applicant_code = _field(row, _EN_APPLICANT_TYPE)
        yield Record(
            callsign=callsign,
            first_name=_field(row, _EN_FIRST_NAME),
            last_name=_field(row, _EN_LAST_NAME),
            city=_field(row, _EN_CITY),
            state=_field(row, _EN_STATE),
            postal_code=_field(row, _EN_ZIP),
            license_class=OPERATOR_CLASS.get(op_class, op_class) if op_class else None,
            source="fcc",
            synced_at=synced_at,
            grant_date=_iso_date(grant_date),
            effective_date=_iso_date(effective_date),
            expired_date=_iso_date(expired_date),
            frn=_field(row, _EN_FRN),
            entity_type=_field(row, _EN_ENTITY_TYPE),
            applicant_type=(
                APPLICANT_TYPE.get(applicant_code, applicant_code)
                if applicant_code
                else None
            ),
            previous_callsign=previous_callsigns.get(usi),
        )


class FccUlsSource:
    """Importer for the FCC Universal Licensing System amateur bulk extract."""

    name = "fcc"

    def __init__(self) -> None:
        # ISO date of the upstream extract; set during download(), stamped onto Records.
        self.synced_at: str | None = None

    def download(self, work_dir: Path) -> Path:
        """Fetch + unzip l_amat.zip into work_dir, returning the extract directory.

        Caches the raw zip; honors If-Modified-Since when a cached copy exists so we
        don't re-pull an unchanged weekly extract. Be polite to upstream. Records the
        upstream file date (Last-Modified header, else the zip's mtime) in `synced_at`.
        """
        import zipfile
        from email.utils import formatdate

        raw_zip = work_dir / "l_amat.zip"
        extract_dir = work_dir / "l_amat"
        work_dir.mkdir(parents=True, exist_ok=True)

        request = urllib.request.Request(DOWNLOAD_URL)
        if raw_zip.exists():
            request.add_header(
                "If-Modified-Since", formatdate(raw_zip.stat().st_mtime, usegmt=True)
            )
        last_modified: str | None = None
        try:
            with urllib.request.urlopen(request) as response:  # noqa: S310 (https URL)
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
