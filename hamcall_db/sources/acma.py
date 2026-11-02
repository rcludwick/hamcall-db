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
- ``client.csv`` — the licensee identity + postal address + ``CLIENT_TYPE_ID``. For an
  organisation we keep the name and suburb/state/postcode (mapped to city/state/
  postal_code); the street (``POSTAL_STREET``) is only compared against (address guard),
  NEVER stored — redistribution contract.
- ``client_type.csv`` — ``TYPE_ID`` -> readable name ("Company", "Person", ...).

ACMA LICENCE, CLAUSE 8 (see `is_natural_person` below): where the licensee is a natural
person, their Client Information (name, address, contact details) may not be reproduced
or distributed in a derivative work. For those licences we publish the callsign and
licence-level fields only; every identifying field stays None.

The published CSVs carry a header row (checked 2026-11-01); when present, columns are
resolved BY NAME from it, so a column added or moved upstream can't silently shift a
field. Headerless input falls back to the positional constants below, which follow the
ACMA's own ``DOC/cr_tables_oracle.sql`` shipped inside the extract (device_details has
``AREA_AREA_ID`` at 32 and ``CALL_SIGN`` at 33). Output leaves county/country/dxcc/grid
for downstream stages (cty.dat enrichment, geocoding from postcode/suburb).

The ACMA client name (``LICENCEE``) is a single free-text field. Only organisations'
names are published, so it goes whole into ``last_name`` (first_name stays None).
"""

from __future__ import annotations

import csv
import re
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator
from dataclasses import replace
from pathlib import Path

from hamcall_db.address_guard import echoes_street, warn_scrubbed
from hamcall_db.history import HistoryRow
from hamcall_db.models import Record
from hamcall_db.sources.base import synced_at_from

# Daily bulk "spectra" extract of the whole RRL, served from the ACMA CDN. The old
# web.acma.gov.au/rrl-updates/ host stopped resolving around 2026-10-21.
DOWNLOAD_URL = "https://cdn.acma.gov.au/rrl/spectra_rrl.zip"

# CSV file names inside the extract (one per core RRL table).
_CLIENT_CSV = "client.csv"
_LICENCE_CSV = "licence.csv"
_DEVICE_DETAILS_CSV = "device_details.csv"
_CLIENT_TYPE_CSV = "client_type.csv"

# 0-based column indices, per the published spectra RRL schema.
# client.csv
_CLIENT_NO = 0
_CLIENT_LICENCEE = 1
_CLIENT_POSTAL_STREET = 5  # compared against (address guard), NEVER stored
_CLIENT_POSTAL_SUBURB = 6
_CLIENT_POSTAL_STATE = 7
_CLIENT_POSTAL_POSTCODE = 8
_CLIENT_TYPE_ID = 10

# client_type.csv
_CLIENT_TYPE_TYPE_ID = 0
_CLIENT_TYPE_NAME = 1

# licence.csv
_LICENCE_NO = 0
_LICENCE_CLIENT_NO = 1
_LICENCE_TYPE_NAME = 4
_LICENCE_CATEGORY_NAME = 5
_LICENCE_DATE_ISSUED = 6
_LICENCE_DATE_OF_EFFECT = 7
_LICENCE_DATE_OF_EXPIRY = 8
_LICENCE_STATUS_TEXT = 10

# device_details.csv — wide table; CALL_SIGN and LICENCE_NO are the only fields we read.
_DEVICE_LICENCE_NO = 1
_DEVICE_CALL_SIGN = 33

# Amateur licences are identified by this token in LICENCE_TYPE_NAME
# (e.g. "Amateur", "Amateur Beacon", "Amateur Repeater").
_AMATEUR_TOKEN = "amateur"

# Only currently-granted licences are emitted by default.
_GRANTED_STATUS = "granted"

# Header detection: a first row is treated as a header (and skipped) if its first cell
# matches one of these well-known column names rather than a data value.
_HEADER_TOKENS = {"client_no", "licence_no", "sdd_id", "type_id"}

# client_type.csv as shipped in the 2026-11-01 extract. Used for the readable
# applicant_type when the extract lacks client_type.csv; the publish/withhold decision
# keys on the numeric id, never on these names.
_CLIENT_TYPE_NAMES = {
    "1": "Commonwealth Department",
    "2": "Other Commonwealth Agency",
    "3": "State Government",
    "4": "Local Government",
    "5": "Company",
    "6": "Community or Volunteer Group",
    "7": "Person",
}

# ---------------------------------------------------------------------------------
# ACMA LICENCE CLAUSE 8 — THE ONE PLACE THE NATURAL-PERSON RULE LIVES.
#
# ACMA's "Licence to use the Register of Radiocommunications Licences" (LICENCE.TXT in
# the extract), clause 8: "Where the Licensee is a natural person, this Licence does not
# permit you to reproduce or distribute the Licensee's Client Information contained in
# the Register in any Derivative work." Clause 6 defines Client Information as personal
# information that may readily identify the licensee, "including, but not limited to,
# the Licensee's name, address and contact details".
#
# So whether a record is published in full turns on ACMA's own client.CLIENT_TYPE_ID:
#   1-5 government / Company        -> organisation, published in full.
#   6 Community or Volunteer Group  -> organisation WHEN the licensee name contains an
#                                      organisation word (club, society, group, inc,
#                                      WICEN, scouts...). This is how ACMA files clubs,
#                                      and a club is a collective, not a natural person
#                                      (owner's decision, 2026-11-02). ACMA also files
#                                      some individuals under type 6 ("AP Bartel",
#                                      "Nominee <person> <callsign>"); a type-6 name with
#                                      no organisation word, or a nominee/attention
#                                      line, is treated as a natural person.
#   7 Person                        -> natural person.
#   blank / any other value         -> natural person (fail closed).
# The name test only ever RESCUES type 6; it can't turn a Person into an organisation.
# For a natural person the callsign and licence-level fields (class, dates, the readable
# client type) are published; name, city, state, postcode and grid are not, and the grid
# can't be derived downstream because its inputs (postcode, suburb) are withheld.
# ---------------------------------------------------------------------------------
_ORGANISATION_CLIENT_TYPES = frozenset(
    {
        "1",  # Commonwealth Department
        "2",  # Other Commonwealth Agency
        "3",  # State Government
        "4",  # Local Government
        "5",  # Company
    }
)

# Record / HistoryRow fields that can identify (or locate) a licensee: withheld for
# natural persons. grid is derived from the postcode, so it is location data too.
_IDENTIFYING_FIELDS = ("first_name", "last_name", "city", "county", "state", "postal_code", "grid")


_COMMUNITY_GROUP_TYPE = "6"

# Whole words that mark a type-6 licensee name as an organisation. Checked against every
# type-6 amateur licensee in the 2026-11-01 extract: each club matches, and none of the
# individuals filed under type 6 do.
_ORGANISATION_NAME = re.compile(
    r"\b("
    r"club|society|group|association|assoc|associates|inc|incorporated|institute|"
    r"network|wicen|scouts?|branch|college|company|co|ltd|limited|pty|"
    r"ambulance|broadcasters|shed|friends|foundation|council|committee|federation|"
    r"league|union|trust|team|service|services|radio"
    r")\b",
    re.IGNORECASE,
)
# A type-6 entry that names a person on the organisation's behalf is about that person.
_PERSON_ON_BEHALF = re.compile(r"^\s*(nominee|attn|attention|c/-|c/o)\b", re.IGNORECASE)


def is_natural_person(client_type_id: str | None, name: str | None = None) -> bool:
    """True unless ACMA's client type (and, for type 6, the name) marks an organisation.

    See the clause-8 block above: types 1-5 are organisations; type 6 is an
    organisation only when ``name`` contains an organisation word and isn't a
    nominee/attention line; everything else is a natural person (fail closed).
    """
    client_type = (client_type_id or "").strip()
    if client_type in _ORGANISATION_CLIENT_TYPES:
        return False
    if client_type == _COMMUNITY_GROUP_TYPE and name:
        return _PERSON_ON_BEHALF.search(name) is not None or not _ORGANISATION_NAME.search(name)
    return True


def withhold_prior_history(
    prior: Iterable[HistoryRow], current: Iterable[Record]
) -> list[HistoryRow]:
    """Apply the clause-8 rule to history carried forward from an earlier build.

    The nightly build extends the previous release's history, which predates this rule
    (and, before 2026-11, keyed ACMA rows on AREA_AREA_ID instead of the callsign). A
    prior ACMA row keeps its identifying fields only when it is the OPEN interval of a
    callsign whose current ACMA record publishes the same holder name, i.e. an
    organisation this build vetted. Every other ACMA row — closed intervals, callsigns
    that are now natural persons, callsigns gone from the extract — has its identifying
    fields nulled. Other sources' rows pass through untouched.
    """
    vetted = {
        rec.callsign: rec.last_name
        for rec in current
        if rec.source == "acma" and rec.last_name is not None
    }
    blank = dict.fromkeys(_IDENTIFYING_FIELDS)
    out: list[HistoryRow] = []
    for row in prior:
        keep = row.source != "acma" or (
            row.valid_to is None
            and row.last_name is not None
            and vetted.get(row.callsign) == row.last_name
        )
        out.append(row if keep else replace(row, **blank))
    return out


def _read_csv(path: Path, columns: dict[str, int]) -> Iterator[list[str]]:
    """Yield rows of a spectra CSV, skipping a header row if present.

    `columns` maps column name -> default (positional) index. If the file has a header
    row, each name found in it is re-pointed at its header position, IN PLACE, before
    the first data row is yielded; names missing from the header keep their default.
    """
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
                    header = {name.strip().upper(): i for i, name in enumerate(row)}
                    for name in columns:
                        if name in header:
                            columns[name] = header[name]
                    continue
            yield row


def _field(row: list[str], idx: int) -> str | None:
    """Return column `idx` of `row`, or None if absent/blank."""
    if idx < len(row):
        value = row[idx].strip()
        return value or None
    return None


def _client_type_names(path: Path) -> dict[str, str]:
    """TYPE_ID -> NAME from client_type.csv, falling back to the built-in table."""
    names = dict(_CLIENT_TYPE_NAMES)
    cols = {"TYPE_ID": _CLIENT_TYPE_TYPE_ID, "NAME": _CLIENT_TYPE_NAME}
    for row in _read_csv(path / _CLIENT_TYPE_CSV, cols):
        type_id, name = _field(row, cols["TYPE_ID"]), _field(row, cols["NAME"])
        if type_id is not None and name is not None:
            names[type_id] = name
    return names


def parse_dir(
    path: Path,
    *,
    granted_only: bool = True,
    synced_at: str | None = None,
) -> Iterator[Record]:
    """Parse an unzipped ACMA spectra RRL extract directory into `Record`s.

    Joins device_details -> licence -> client and emits only the AMATEUR subset
    (licence.LICENCE_TYPE_NAME contains "Amateur"). By default only currently-granted
    licences are emitted. `synced_at` stamps the upstream file date. Natural persons'
    identifying fields are withheld (`is_natural_person`, ACMA licence clause 8).
    """
    # licence.csv: keep only amateur licences, by LICENCE_NO.
    lic = {
        "LICENCE_NO": _LICENCE_NO,
        "CLIENT_NO": _LICENCE_CLIENT_NO,
        "LICENCE_TYPE_NAME": _LICENCE_TYPE_NAME,
        "LICENCE_CATEGORY_NAME": _LICENCE_CATEGORY_NAME,
        "DATE_ISSUED": _LICENCE_DATE_ISSUED,
        "DATE_OF_EFFECT": _LICENCE_DATE_OF_EFFECT,
        "DATE_OF_EXPIRY": _LICENCE_DATE_OF_EXPIRY,
        "STATUS_TEXT": _LICENCE_STATUS_TEXT,
    }
    amateur: dict[str, list[str]] = {}
    for row in _read_csv(path / _LICENCE_CSV, lic):
        type_name = _field(row, lic["LICENCE_TYPE_NAME"])
        if type_name is None or _AMATEUR_TOKEN not in type_name.lower():
            continue
        if granted_only:
            status = _field(row, lic["STATUS_TEXT"])
            if status is None or status.lower() != _GRANTED_STATUS:
                continue
        licence_no = _field(row, lic["LICENCE_NO"])
        if licence_no is None:
            continue
        amateur[licence_no] = row

    if not amateur:
        return

    # client.csv: identity, location and client type for the amateur clients.
    needed_clients = {_field(row, lic["CLIENT_NO"]) for row in amateur.values()} - {None}
    cli = {
        "CLIENT_NO": _CLIENT_NO,
        "LICENCEE": _CLIENT_LICENCEE,
        "POSTAL_STREET": _CLIENT_POSTAL_STREET,
        "POSTAL_SUBURB": _CLIENT_POSTAL_SUBURB,
        "POSTAL_STATE": _CLIENT_POSTAL_STATE,
        "POSTAL_POSTCODE": _CLIENT_POSTAL_POSTCODE,
        "CLIENT_TYPE_ID": _CLIENT_TYPE_ID,
    }
    clients: dict[str, list[str]] = {}
    for row in _read_csv(path / _CLIENT_CSV, cli):
        client_no = _field(row, cli["CLIENT_NO"])
        if client_no is not None and client_no in needed_clients:
            clients[client_no] = row
    type_names = _client_type_names(path)

    # device_details.csv drives emission: it carries the callsign per amateur licence.
    dev = {"LICENCE_NO": _DEVICE_LICENCE_NO, "CALL_SIGN": _DEVICE_CALL_SIGN}
    seen: set[str] = set()
    scrubbed = 0
    for row in _read_csv(path / _DEVICE_DETAILS_CSV, dev):
        licence_no = _field(row, dev["LICENCE_NO"])
        if licence_no is None or licence_no not in amateur:
            continue
        callsign = _field(row, dev["CALL_SIGN"])
        if callsign is None:
            continue
        callsign = callsign.upper()
        if callsign in seen:
            continue
        seen.add(callsign)

        licence_row = amateur[licence_no]
        client_no = _field(licence_row, lic["CLIENT_NO"])
        client_row = clients.get(client_no) if client_no is not None else None
        client_type = _field(client_row, cli["CLIENT_TYPE_ID"]) if client_row else None

        last_name = city = state = postal_code = None
        licensee = _field(client_row, cli["LICENCEE"]) if client_row else None
        if client_row is not None and not is_natural_person(client_type, licensee):
            # Organisation (clause 8 does not apply): publish name + suburb/state/postcode.
            # The street is compared against, never stored (hdb-addrguard).
            street = (_field(client_row, cli["POSTAL_STREET"]),)
            name = _field(client_row, cli["LICENCEE"])
            last_name = " ".join(name.split()) if name else None
            published = [last_name, _field(client_row, cli["POSTAL_SUBURB"])]
            kept = [None if echoes_street(v, street) else v for v in published]
            pairs = zip(published, kept, strict=True)
            scrubbed += sum(a is not None and b is None for a, b in pairs)
            last_name, city = kept
            state = _field(client_row, cli["POSTAL_STATE"])
            postal_code = _field(client_row, cli["POSTAL_POSTCODE"])
        # else: natural person, or ambiguous/missing client type -> identity withheld.

        yield Record(
            callsign=callsign,
            first_name=None,  # organisations' names go whole into last_name
            last_name=last_name,
            city=city,
            state=state,
            postal_code=postal_code,
            license_class=_field(licence_row, lic["LICENCE_CATEGORY_NAME"]),
            grant_date=_field(licence_row, lic["DATE_ISSUED"]),
            effective_date=_field(licence_row, lic["DATE_OF_EFFECT"]),
            expired_date=_field(licence_row, lic["DATE_OF_EXPIRY"]),
            applicant_type=type_names.get(client_type) if client_type else None,
            source="acma",
            synced_at=synced_at,
        )
    warn_scrubbed("acma", scrubbed)


class AcmaSource:
    """Importer for the ACMA RRL daily spectra bulk extract (amateur subset)."""

    name = "acma"

    def __init__(self) -> None:
        # ISO date of the upstream extract; set during download(), stamped onto Records.
        self.synced_at: str | None = None

    def download(self, work_dir: Path) -> Path:
        """Fetch + unzip spectra_rrl.zip into work_dir, returning the extract directory.

        Caches the raw zip; honors If-Modified-Since when a cached copy exists so we
        don't re-pull an unchanged daily extract. Be polite to upstream. Records the
        upstream file date (Last-Modified header, else the zip's mtime) in `synced_at`.
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
