"""UKE amateur permit importer (Poland).

Poland's regulator, the Office of Electronic Communications (Urząd Komunikacji
Elektronicznej, UKE), runs the amateur permit register at ``https://amator.uke.gov.pl``.
It offers four CSV exports, regenerated on every request (updated daily):

==========================  =================================================  ======
List (export URL path)      What it holds                                      Rows*
==========================  =================================================  ======
``individuals``             permits issued to private persons                  17,661
``clubs``                   permits issued to legal entities (clubs)              702
``individual_devices``      unattended stations of private persons                463
``club_devices``            unattended stations of clubs                          349
==========================  =================================================  ======

\\* as of 2026-11-02. The URL is ``https://amator.uke.gov.pl/pl/<list>/export.csv``.

The unattended ("bezobsługowe") lists are amateur stations with their own SR callsign:
repeaters, APRS digipeaters, beacons, weather stations. They are imported too, with
``applicant_type = "Unattended station"``.

File shape
----------
``;``-separated, LF line endings, a header row, cp1250 encoding (decoded as UTF-8 first,
should UKE ever switch). Columns are read by header name. The individual lists carry
``number;valid_to;call_sign;category;transmitter_power;station_location`` (plus the
station type and frequencies for devices): no holder name at all. The club lists add
``applicant_*`` / ``enduser_*`` / ``station_*`` name, town, street, house and flat
columns, and ``lat;lng`` (empty in every row as of 2026-11-02).

Field mapping
-------------
- ``call_sign`` -> ``callsign``; ``valid_to`` -> ``expired_date``.
- ``category`` -> ``license_class``: ``Kategoria1`` -> "Category 1", ``Kategoria3`` ->
  "Category 3", ``Kategoria5`` -> "Category 5" (unattended), ``Dodatkowe`` ->
  "Additional" (extra callsigns, e.g. contest or special-event calls).
- Station town (``station_location`` / ``station_city``) -> ``city``, cut down to the
  town by `town_only`.
- Clubs: ``enduser_name`` (the club using the station) or else ``applicant_name`` (the
  permit holder) -> ``last_name``.
- ``lat``/``lng`` -> ``grid``, the 4-char Maidenhead square only (`latlon_to_grid`).
  The coordinates themselves are never stored.
- ``applicant_type``: "Individual", "Club" or "Unattended station" by list.

Dropped: permit ``number`` and ``transmitter_power`` and frequencies (no schema column);
every street, house and flat number (never stored, only compared against so the address
guard can drop a published field that copies one). There are no postal codes in any
list, so ``postal_code`` stays None.

Duplicates: one callsign can appear more than once (a renewed permit next to the old
one, or a call in both the individual and club list). The row with the latest
``valid_to`` wins. Expired permits still in the list are kept, with their date.

Truncated exports
-----------------
UKE streams the CSV and the download sometimes ends early on a clean line boundary
with no error (seen several times on 2026-11-02 over HTTP/1.1). Asking for gzip makes
it far less likely, and every download is checked against the row count the site's
own table endpoint reports (``/pl/<list>.json``). A short file raises
``http.client.IncompleteRead``, which the build's shared retry treats as transient, and
is never cached.
"""

from __future__ import annotations

import csv
import gzip
import http.client
import io
import json
import re
import sys
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from datetime import date
from pathlib import Path

from hamcall_db.address_guard import echoes_street, warn_scrubbed
from hamcall_db.geocode import latlon_to_grid
from hamcall_db.models import Record
from hamcall_db.sources.base import synced_at_from

BASE_URL = "https://amator.uke.gov.pl/pl"

# Connect/read timeout (seconds) so a hung upstream fails fast instead of stalling.
DOWNLOAD_TIMEOUT = 120

_USER_AGENT = "hamcall-db/0 (+https://github.com/rcludwick/hamcall-db)"

# Injectable fetch seam: url -> response body (already decompressed).
Fetcher = Callable[[str], bytes]

# Cached file name -> applicant_type for its rows. Order matters only for ties in dedupe.
LISTS: dict[str, str] = {
    "individuals.csv": "Individual",
    "clubs.csv": "Club",
    "individual_devices.csv": "Unattended station",
    "club_devices.csv": "Unattended station",
}

CATEGORIES: dict[str, str] = {
    "Kategoria1": "Category 1",
    "Kategoria3": "Category 3",
    "Kategoria5": "Category 5",
    "Dodatkowe": "Additional",
}

# Polish amateur calls: SN-SR, 3Z and HF blocks; a number, a letter, then letters/digits.
CALLSIGN_RE = re.compile(r"^(S[N-R]|3Z|HF)[0-9]+[A-Z][A-Z0-9]*$")

_REQUIRED = ("valid_to", "call_sign", "category")

# The rows have the station town in one of these, and club lists have a name.
_TOWN_COLUMNS = ("station_location", "station_city")
_ADDRESS_PARTIES = ("applicant", "enduser", "station")

# A street marker inside a town value ("Pogrzebień ul. Lubomska 16 B").
_STREET_MARKERS = frozenset({"ul.", "al.", "os.", "pl."})

# Tolerance for the completeness check: rows can be added between the CSV and the
# count request, so allow a small shortfall.
_MIN_SLACK = 10


def town_only(value: str | None) -> str | None:
    """Cut a station-town value down to the town name.

    Station towns sometimes carry the rest of the address: a rural house number
    ("Gliczarów Górny 158"), a street ("Pogrzebień ul. Lubomska 16 B"), or even DMS
    coordinates. Everything from the first word holding a digit or a street marker
    (``ul.``, ``al.``, ``os.``, ``pl.``) is dropped; a value left with no letters is None.
    """
    kept: list[str] = []
    for word in (value or "").split():
        if any(c.isdigit() for c in word) or word.casefold() in _STREET_MARKERS:
            break
        kept.append(word)
    town = " ".join(kept).strip(" ,;-")
    return town if any(c.isalpha() for c in town) else None


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1250")


def _read_rows(path: Path) -> Iterator[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(_decode(path.read_bytes()), newline=""), delimiter=";")
    missing = [c for c in _REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(
            f"{path}: header lacks {', '.join(missing)} (has the UKE layout changed?): "
            f"{reader.fieldnames!r}"
        )
    for row in reader:
        yield {k: (v or "").strip() for k, v in row.items() if k is not None}


def _streets(row: dict[str, str], town: str | None) -> list[str]:
    """The row's street addresses ("ul. Fikcyjna 14/2"), for comparison only.

    Village addresses repeat the town as the street ("Koniewo 12" in Koniewo); that
    street is skipped so it can't knock out the town itself.
    """
    out: list[str] = []
    for party in _ADDRESS_PARTIES:
        street = row.get(f"{party}_street", "")
        if not street or (town and street.casefold() == town.casefold()):
            continue
        house = row.get(f"{party}_house", "")
        flat = row.get(f"{party}_number", "")
        out.append(f"{street} {house}{'/' + flat if flat else ''}".strip())
    return out


def _grid(row: dict[str, str]) -> str | None:
    """4-char grid from the club lists' lat/lng, or None. Coordinates are not kept."""
    try:
        lat = float(row.get("lat", "").replace(",", "."))
        lon = float(row.get("lng", "").replace(",", "."))
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    return latlon_to_grid(lat, lon)


def parse_dir(path: Path, *, synced_at: str | None = None) -> Iterator[Record]:
    """Parse a directory holding UKE's four CSV exports into `Record`s.

    One record per callsign (latest ``valid_to`` wins). A list that is missing from
    the directory raises FileNotFoundError.
    """
    best: dict[str, Record] = {}
    bad_calls: list[str] = []
    scrubbed = 0
    for name, applicant_type in LISTS.items():
        for row in _read_rows(path / name):
            callsign = row["call_sign"].upper()
            if not CALLSIGN_RE.match(callsign):
                bad_calls.append(callsign)
                continue
            raw_town = next((row[c] for c in _TOWN_COLUMNS if row.get(c)), None)
            city = town_only(raw_town)
            holder = row.get("enduser_name") or row.get("applicant_name") or None

            # Streets are compared against, never stored (hdb-addrguard).
            streets = _streets(row, city)
            published = [holder, city]
            kept = [None if echoes_street(v, streets) else v for v in published]
            pairs = zip(published, kept, strict=True)
            scrubbed += sum(a is not None and b is None for a, b in pairs)
            holder, city = kept

            category = row["category"]
            record = Record(
                callsign=callsign,
                last_name=holder,
                city=city,
                grid=_grid(row),
                license_class=CATEGORIES.get(category, category or None),
                expired_date=row["valid_to"] or None,
                applicant_type=applicant_type,
                source="uke",
                synced_at=synced_at,
            )
            existing = best.get(callsign)
            if existing is None or (record.expired_date or "") > (existing.expired_date or ""):
                best[callsign] = record

    if bad_calls:
        print(
            f"WARNING: uke: skipped {len(bad_calls)} row(s) with a non-Polish callsign: "
            f"{', '.join(bad_calls[:10])}",
            file=sys.stderr,
        )
    warn_scrubbed("uke", scrubbed)
    yield from best.values()


def _urllib_fetch(url: str) -> bytes:
    """Default fetcher: GET with gzip (the export truncates far less often that way)."""
    request = urllib.request.Request(
        url, headers={"User-Agent": _USER_AGENT, "Accept-Encoding": "gzip"}
    )
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:  # noqa: S310
        body = response.read()
        if response.headers.get("Content-Encoding", "").lower() == "gzip":
            body = gzip.decompress(body)
        return body


def _count_rows(body: bytes) -> int:
    return sum(1 for _ in csv.reader(io.StringIO(_decode(body), newline=""), delimiter=";")) - 1


class UkeSource:
    """Importer for the UKE (Poland) amateur permit register."""

    name = "uke"

    def __init__(
        self,
        *,
        fetcher: Fetcher | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.synced_at: str | None = None
        self._fetch = fetcher or _urllib_fetch
        self._today = today

    def _download_list(self, stem: str, dest: Path) -> None:
        body = self._fetch(f"{BASE_URL}/{stem}/export.csv")
        rows = _count_rows(body)
        meta = json.loads(self._fetch(f"{BASE_URL}/{stem}.json?draw=1&start=0&length=1"))
        total = int(meta["recordsTotal"])
        if rows + max(_MIN_SLACK, total // 200) < total:
            print(
                f"WARNING: uke: {stem} export has {rows} rows but the register lists "
                f"{total}; the download was cut short",
                file=sys.stderr,
            )
            raise http.client.IncompleteRead(body, total - rows)
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(body)
        tmp.replace(dest)

    def download(self, work_dir: Path) -> Path:
        """Fetch the four exports into ``work_dir/data/raw/uke/<date>/``.

        UKE sends no Last-Modified and ignores If-Modified-Since (each export is built
        per request), so a list already cached today is reused without a request.
        Retries on transient failures are the build's job (`base.retry_transient`).
        """
        day_dir = work_dir / "data" / "raw" / "uke" / self._today().isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)
        for name in LISTS:
            dest = day_dir / name
            if not dest.exists():
                self._download_list(name.removesuffix(".csv"), dest)
        self.synced_at = synced_at_from(None, day_dir / "individuals.csv")
        return day_dir

    def parse(self, path: Path, *, synced_at: str | None = None) -> Iterable[Record]:
        return parse_dir(path, synced_at=synced_at if synced_at is not None else self.synced_at)
