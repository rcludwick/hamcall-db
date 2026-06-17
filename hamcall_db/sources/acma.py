"""ACMA RRL amateur importer (Australia).

Parses the ACMA "Register of Radiocommunications Licences" (RRL) bulk *spectra*
extract (``spectra_rrl.zip``), a set of comma-delimited ``.csv`` files — one per core
RRL table — joined on ``LICENCE_NO`` / ``CLIENT_NO``. The RRL covers ALL
radiocommunications services (broadcast, maritime, land mobile, ...), so the amateur
subset must be FILTERED out: an amateur licence is one whose
``licence.LICENCE_TYPE_NAME`` contains "Amateur".

Files / join used (only what we need):

- ``device_details.csv`` — per-device rows; carries the operator ``CALL_SIGN`` and the
  ``LICENCE_NO`` join key. (One licence can have several device rows; we de-dup on
  callsign.)
- ``licence.csv`` — the licence record: ``LICENCE_NO`` -> ``CLIENT_NO``, plus
  ``LICENCE_TYPE_NAME`` (amateur filter) and ``STATUS`` / ``STATUS_TEXT``.
- ``client.csv`` — the licensee identity + postal address. We keep suburb/state/postcode
  (mapped to city/state/postal_code); the street (``POSTAL_STREET``) is read past but
  NEVER stored — redistribution contract.

Column positions are stable in the published spectra format and live in named constants
below so a future format revision is a one-line change (see the ACMA "Summary of Spectra
RRL format" / the create_tables.sql in the well-known kronicd/rrl_import project, which
this mapping was grounded against). The published CSVs may or may not carry a header row;
we auto-detect and skip it. Output leaves county/country/dxcc/grid for downstream stages
(cty.dat enrichment, geocoding from postcode/suburb).

The ACMA client name (``LICENCEE``) is a single free-text field. We best-effort split an
individual's name into first/last; an organisation / club name (no usable split, or a
trailing company token) is placed whole into ``last_name``.
"""

from __future__ import annotations

import csv
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator
from pathlib import Path

from hamcall_db.models import Record

# Daily bulk "spectra" extract of the whole RRL.
DOWNLOAD_URL = "https://web.acma.gov.au/rrl-updates/spectra_rrl.zip"

# CSV file names inside the extract (one per core RRL table).
_CLIENT_CSV = "client.csv"
_LICENCE_CSV = "licence.csv"
_DEVICE_DETAILS_CSV = "device_details.csv"

# 0-based column indices, per the published spectra RRL schema.
# client.csv
_CLIENT_NO = 0
_CLIENT_LICENCEE = 1
# (POSTAL_STREET at index 5 is read past but NEVER stored.)
_CLIENT_POSTAL_SUBURB = 6
_CLIENT_POSTAL_STATE = 7
_CLIENT_POSTAL_POSTCODE = 8

# licence.csv
_LICENCE_NO = 0
_LICENCE_CLIENT_NO = 1
_LICENCE_TYPE_NAME = 4
_LICENCE_STATUS_TEXT = 10

# device_details.csv — wide table; CALL_SIGN and LICENCE_NO are the only fields we read.
_DEVICE_LICENCE_NO = 1
_DEVICE_CALL_SIGN = 32

# Amateur licences are identified by this token in LICENCE_TYPE_NAME
# (e.g. "Amateur", "Amateur Beacon", "Amateur Repeater").
_AMATEUR_TOKEN = "amateur"

# Only currently-granted licences are emitted by default.
_GRANTED_STATUS = "granted"

# Header detection: a first row is treated as a header (and skipped) if its first cell
# matches one of these well-known column names rather than a data value.
_HEADER_TOKENS = {"client_no", "licence_no", "sdd_id"}

# Trailing tokens that mark an organisation / club rather than an individual; such a
# LICENCEE is kept whole in last_name (no first/last split).
_ORG_TOKENS = {
    "club",
    "inc",
    "inc.",
    "incorporated",
    "pty",
    "ltd",
    "limited",
    "group",
    "association",
    "society",
    "radio",
    "company",
    "corporation",
    "university",
    "school",
    "college",
}


def _read_csv(path: Path) -> Iterator[list[str]]:
    """Yield rows of a spectra CSV, skipping a header row if present."""
    if not path.exists():
        return
    with path.open(encoding="latin-1", newline="") as fh:
        reader = csv.reader(fh)
        first = True
        for row in reader:
            if not row:
                continue
            if first:
                first = False
                if row[0].strip().lower() in _HEADER_TOKENS:
                    continue
            yield row


def _field(row: list[str], idx: int) -> str | None:
    """Return column `idx` of `row`, or None if absent/blank."""
    if idx < len(row):
        value = row[idx].strip()
        return value or None
    return None


def _split_name(licencee: str | None) -> tuple[str | None, str | None]:
    """Best-effort split an ACMA LICENCEE field into (first_name, last_name).

    ACMA stores the licensee as a single field. Heuristics:
    - Organisations / clubs (an org token anywhere, or a single token) -> whole name
      in last_name, first_name None.
    - "Surname, Given" -> ("Given", "Surname").
    - "Given ... Surname" -> first token is first_name, remainder is last_name.
    """
    if not licencee:
        return None, None
    name = " ".join(licencee.split())

    tokens = name.replace(",", " ").split()
    if any(tok.strip(".").lower() in _ORG_TOKENS for tok in tokens):
        return None, name

    if "," in name:
        surname, _, given = name.partition(",")
        surname = surname.strip()
        given = given.strip()
        if surname and given:
            return given, surname
        return None, surname or given

    parts = name.split()
    if len(parts) == 1:
        # Single token — treat as an entity/club name, not a personal first name.
        return None, parts[0]
    return parts[0], " ".join(parts[1:])


def parse_dir(
    path: Path,
    *,
    granted_only: bool = True,
    synced_at: str | None = None,
) -> Iterator[Record]:
    """Parse an unzipped ACMA spectra RRL extract directory into `Record`s.

    Joins device_details -> licence -> client and emits only the AMATEUR subset
    (licence.LICENCE_TYPE_NAME contains "Amateur"). By default only currently-granted
    licences are emitted. `synced_at` stamps the upstream file date.
    """
    # licence.csv: keep only amateur licences; map LICENCE_NO -> client_no.
    amateur_client: dict[str, str | None] = {}
    for row in _read_csv(path / _LICENCE_CSV):
        type_name = _field(row, _LICENCE_TYPE_NAME)
        if type_name is None or _AMATEUR_TOKEN not in type_name.lower():
            continue
        if granted_only:
            status = _field(row, _LICENCE_STATUS_TEXT)
            if status is None or status.lower() != _GRANTED_STATUS:
                continue
        licence_no = _field(row, _LICENCE_NO)
        if licence_no is None:
            continue
        amateur_client[licence_no] = _field(row, _LICENCE_CLIENT_NO)

    if not amateur_client:
        return

    # client.csv: identity + location for the amateur clients we care about.
    needed_clients = {c for c in amateur_client.values() if c is not None}
    clients: dict[str, list[str]] = {}
    for row in _read_csv(path / _CLIENT_CSV):
        client_no = _field(row, _CLIENT_NO)
        if client_no is not None and client_no in needed_clients:
            clients[client_no] = row

    # device_details.csv drives emission: it carries the callsign per amateur licence.
    seen: set[str] = set()
    for row in _read_csv(path / _DEVICE_DETAILS_CSV):
        licence_no = _field(row, _DEVICE_LICENCE_NO)
        if licence_no is None or licence_no not in amateur_client:
            continue
        callsign = _field(row, _DEVICE_CALL_SIGN)
        if callsign is None:
            continue
        callsign = callsign.upper()
        if callsign in seen:
            continue
        seen.add(callsign)

        client_no = amateur_client[licence_no]
        client_row = clients.get(client_no) if client_no is not None else None
        if client_row is not None:
            licencee = _field(client_row, _CLIENT_LICENCEE)
            first_name, last_name = _split_name(licencee)
            city = _field(client_row, _CLIENT_POSTAL_SUBURB)
            state = _field(client_row, _CLIENT_POSTAL_STATE)
            postal_code = _field(client_row, _CLIENT_POSTAL_POSTCODE)
        else:
            first_name = last_name = city = state = postal_code = None

        yield Record(
            callsign=callsign,
            first_name=first_name,
            last_name=last_name,
            city=city,
            state=state,
            postal_code=postal_code,
            source="acma",
            synced_at=synced_at,
        )


class AcmaSource:
    """Importer for the ACMA RRL daily spectra bulk extract (amateur subset)."""

    name = "acma"

    def download(self, work_dir: Path) -> Path:
        """Fetch + unzip spectra_rrl.zip into work_dir, returning the extract directory.

        Caches the raw zip; honors If-Modified-Since when a cached copy exists so we
        don't re-pull an unchanged daily extract. Be polite to upstream.
        """
        import zipfile
        from email.utils import formatdate

        raw_zip = work_dir / "spectra_rrl.zip"
        extract_dir = work_dir / "spectra"
        work_dir.mkdir(parents=True, exist_ok=True)

        request = urllib.request.Request(DOWNLOAD_URL)
        if raw_zip.exists():
            request.add_header(
                "If-Modified-Since", formatdate(raw_zip.stat().st_mtime, usegmt=True)
            )
        try:
            with urllib.request.urlopen(request) as response:  # noqa: S310 (https URL)
                raw_zip.write_bytes(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code != 304 or not raw_zip.exists():  # 304 = unchanged, use cache
                raise

        extract_dir.mkdir(exist_ok=True)
        with zipfile.ZipFile(raw_zip) as zf:
            zf.extractall(extract_dir)
        return extract_dir

    def parse(self, path: Path, *, synced_at: str | None = None) -> Iterable[Record]:
        return parse_dir(path, synced_at=synced_at)
