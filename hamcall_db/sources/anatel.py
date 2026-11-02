"""Anatel amateur station importer (Brazil).

Anatel (Agência Nacional de Telecomunicações) publishes every licensed station, for
every radio service, in one daily zip: ``estacoes_licenciadas.zip`` (about 225 MB)
under its open-data panel ``dadosabertos/paineis_de_dados/outorga_e_licenciamento``.
The amateur service ("Serviço de Radioamador") is a single member of that zip,
``Estacoes_Radioamador.csv``, about 3 MB compressed and 12 MB unpacked.

Fetching only the member
------------------------
The server supports HTTP ``Range``, and a zip keeps its table of contents (the
*central directory*) at the end of the file. So `AnatelSource.download` reads the last
64 KB, lets `zipfile` find the member's offset from the central directory there, and
then requests just the bytes of that member: about 3.3 MB instead of 225 MB. Every
ranged response must carry the same ``ETag``/``Last-Modified`` as the first one; if the
zip is replaced mid-fetch the download raises `ConnectionError`, which the build's
shared retry treats as transient. If the server ever ignores ``Range`` (answers 200
with the whole file), the importer falls back to streaming the full zip to disk once,
extracting the member, and deleting the zip.

The extracted CSV is cached at ``<work_dir>/data/raw/anatel/<YYYY-MM-DD>/``, at most
one download per day. The first request carries ``If-Modified-Since`` from the newest
earlier copy; a 304 reuses it.

File shape
----------
UTF-8 with a byte-order mark, ``;``-delimited, CRLF, one header row. One row per
*station*, so a holder with a fixed and a mobile station appears on several rows with
the same ``Indicativo`` (callsign). Unknown values are ``N/I`` ("não informado") and
not-applicable ones ``N/A``. Columns are resolved by header name:

==================================  =========================================
Column                              Use
==================================  =========================================
Tipo de Identificação               CPF -> "Individual", CNPJ -> "Organization"
CNPJ ou CPF                         masked national ID: NEVER read
Nome da Entidade                    `last_name` (full name, not split)
Indicativo                          `callsign`
UF da Entidade                      `state` when no station has a municipality
COER                                `license_class` (Classe A / B / C)
Município da Estação, UF da Estação `city`, `state` (station location)
Data do Primeiro Licenciamento      `grant_date` (earliest over the stations)
Data do Último Licenciamento        `effective_date` (latest)
Data de Validade da Estação         `expired_date` (latest)
==================================  =========================================

Not published: the masked CPF/CNPJ (a partial national ID), the Fistel and station
numbers (Anatel's internal keys), station name/type, frequencies, and the validity
status text (it is derived from the expiry date). The file has no street address and
no postal code, so there is nothing for the address guard to compare against, and the
merge-stage backstop (`address_guard.looks_like_street_address`) still runs on city
and name. Brazilian names don't split reliably into given name and surname, so the
whole name goes in `last_name`, as ACMA does.

Callsigns are upper-cased; rows whose callsign doesn't look Brazilian after that
(``N/I``, ``PU2-YHZ``, a stray ``Ç``) are dropped and counted on stderr.
"""

from __future__ import annotations

import csv
import io
import re
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from hamcall_db.models import Record
from hamcall_db.sources._daily_cache import (
    USER_AGENT,
    Opener,
    if_modified_since,
    latest_prior,
    raw_dir,
    set_mtime_from_header,
)
from hamcall_db.sources.base import synced_at_from

DOWNLOAD_URL = (
    "https://www.anatel.gov.br/dadosabertos/paineis_de_dados/"
    "outorga_e_licenciamento/estacoes_licenciadas.zip"
)
MEMBER_NAME = "Estacoes_Radioamador.csv"
DOWNLOAD_TIMEOUT = 120

# How much of the zip's tail to fetch first. The central directory for its ten members
# is about 1 KB; 64 KB also covers a zip64 end record and a long archive comment.
_TAIL_BYTES = 64 * 1024
# Read-ahead per ranged request while inflating the member.
_CHUNK_BYTES = 1024 * 1024

# Brazilian amateur prefixes are PP-PY and ZV-ZZ.
_CALLSIGN = re.compile(r"^(?:P[P-Y]|Z[V-Z])[0-9][A-Z]{1,3}$")
_PLACEHOLDERS = frozenset({"", "N/I", "N/A"})
_APPLICANT_TYPES = {"CPF": "Individual", "CNPJ": "Organization"}

_ID_TYPE = "Tipo de Identificação"
_NAME = "Nome da Entidade"
_CALL = "Indicativo"
_ENTITY_STATE = "UF da Entidade"
_CLASS = "COER"
_STATION_TYPE = "Tipo de Estação"
_CITY = "Município da Estação"
_STATION_STATE = "UF da Estação"
_FIRST = "Data do Primeiro Licenciamento"
_LAST = "Data do Último Licenciamento"
_VALID = "Data de Validade da Estação"
_REQUIRED = (
    _ID_TYPE,
    _NAME,
    _CALL,
    _ENTITY_STATE,
    _CLASS,
    _STATION_TYPE,
    _CITY,
    _STATION_STATE,
    _FIRST,
    _LAST,
    _VALID,
)


def _value(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = " ".join(raw.split())
    return None if value in _PLACEHOLDERS else value


def _iso(raw: str | None) -> str | None:
    """``dd/mm/yyyy`` -> ``yyyy-mm-dd``; placeholders and junk -> None."""
    value = _value(raw)
    if value is None:
        return None
    try:
        return datetime.strptime(value, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


@dataclass
class _Holder:
    """Everything known about one callsign, gathered over its station rows."""

    callsign: str
    name: str | None = None
    id_type: str | None = None
    license_class: str | None = None
    entity_state: str | None = None
    city: str | None = None
    station_state: str | None = None
    city_from_fixed: bool = False
    grants: list[str] = field(default_factory=list)
    renewals: list[str] = field(default_factory=list)
    expiries: list[str] = field(default_factory=list)

    def add(self, row: dict[str, str]) -> None:
        self.name = self.name or _value(row[_NAME])
        self.id_type = self.id_type or _value(row[_ID_TYPE])
        self.license_class = self.license_class or _value(row[_CLASS])
        self.entity_state = self.entity_state or _value(row[_ENTITY_STATE])
        city = _value(row[_CITY])
        fixed = _value(row[_STATION_TYPE]) == "Fixa"
        # Prefer the fixed station's town; otherwise the first station that has one.
        if city is not None and (self.city is None or (fixed and not self.city_from_fixed)):
            self.city, self.station_state, self.city_from_fixed = (
                city,
                _value(row[_STATION_STATE]),
                fixed,
            )
        for target, column in ((self.grants, _FIRST), (self.renewals, _LAST)):
            if (iso := _iso(row[column])) is not None:
                target.append(iso)
        if (iso := _iso(row[_VALID])) is not None:
            self.expiries.append(iso)

    def record(self, synced_at: str | None) -> Record:
        return Record(
            callsign=self.callsign,
            last_name=self.name,
            city=self.city,
            state=self.station_state if self.city is not None else self.entity_state,
            license_class=self.license_class,
            grant_date=min(self.grants, default=None),
            effective_date=max(self.renewals, default=None),
            expired_date=max(self.expiries, default=None),
            applicant_type=_APPLICANT_TYPES.get(self.id_type or ""),
            source="anatel",
            synced_at=synced_at,
        )


def parse_file(path: Path, *, synced_at: str | None = None) -> Iterator[Record]:
    """Parse ``Estacoes_Radioamador.csv`` into one `Record` per callsign (file order)."""
    holders: dict[str, _Holder] = {}
    dropped = 0
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        missing = [c for c in _REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(
                f"{path}: header lacks {', '.join(missing)} (has the Anatel layout changed?)"
            )
        for row in reader:
            callsign = (row[_CALL] or "").strip().upper()
            if not _CALLSIGN.match(callsign):
                dropped += 1
                continue
            holders.setdefault(callsign, _Holder(callsign)).add(row)
    if dropped:
        print(
            f"WARNING: anatel: dropped {dropped} row(s) without a valid callsign", file=sys.stderr
        )
    for holder in holders.values():
        yield holder.record(synced_at)


class _RangeFile(io.RawIOBase):
    """A read-only, seekable view of a remote file, fetched with HTTP ``Range``.

    Starts from the tail bytes already fetched by the probe request. Every response
    must carry the same validator (ETag, else Last-Modified) as the probe, so a zip
    swapped upstream mid-read fails instead of mixing two versions.
    """

    def __init__(
        self,
        url: str,
        *,
        opener: Opener,
        size: int,
        tail: bytes,
        validator: str | None,
    ) -> None:
        self._url = url
        self._opener = opener
        self._size = size
        self._tail = tail
        self._tail_start = size - len(tail)
        self._validator = validator
        self._pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self._size}[whence]
        self._pos = max(0, base + offset)
        return self._pos

    def readinto(self, buffer) -> int:  # type: ignore[override]
        if self._pos >= self._size:
            return 0
        want = min(len(buffer), self._size - self._pos)
        if self._pos >= self._tail_start:
            start = self._pos - self._tail_start
            data = self._tail[start : start + want]
        else:
            want = min(want, self._tail_start - self._pos)  # stop where the tail begins
            data = self._fetch(self._pos, self._pos + want - 1)
        buffer[: len(data)] = data
        self._pos += len(data)
        return len(data)

    def _fetch(self, start: int, end: int) -> bytes:
        request = urllib.request.Request(
            self._url, headers={"User-Agent": USER_AGENT, "Range": f"bytes={start}-{end}"}
        )
        with self._opener(request, timeout=DOWNLOAD_TIMEOUT) as response:
            content_range = response.headers.get("Content-Range") or ""
            if response.status != 206 or not content_range.startswith(f"bytes {start}-"):
                raise ConnectionError(
                    f"anatel: unexpected reply to a range request ({content_range!r})"
                )
            if _validator(response.headers) != self._validator:
                raise ConnectionError("anatel: the zip changed upstream during the download")
            data = response.read()
        if len(data) != end - start + 1:
            raise ConnectionError("anatel: short range response")
        return data


def _validator(headers) -> str | None:
    return headers.get("ETag") or headers.get("Last-Modified")


def _extract_member(zf: zipfile.ZipFile, dest: Path) -> None:
    """Copy the amateur member out of `zf` into `dest` (CRC-checked by zipfile)."""
    names = [n for n in zf.namelist() if n.lower() == MEMBER_NAME.lower()]
    if not names:
        raise FileNotFoundError(f"anatel: no {MEMBER_NAME} in the zip")
    part = dest.with_name(dest.name + ".part")
    with zf.open(names[0]) as member, part.open("wb") as out:
        shutil.copyfileobj(member, out, _CHUNK_BYTES)
    part.replace(dest)


class AnatelSource:
    """Importer for Anatel's licensed amateur stations (Brazil)."""

    name = "anatel"

    def __init__(self, *, opener: Opener | None = None, today: date | None = None) -> None:
        self.synced_at: str | None = None
        self._opener = opener or urllib.request.urlopen
        self._today = today

    def download(self, work_dir: Path) -> Path:
        """Fetch ``Estacoes_Radioamador.csv`` out of the remote zip; return its path."""
        day_dir = raw_dir(work_dir, self.name, self._today)
        dest = day_dir / MEMBER_NAME
        if dest.exists():
            self.synced_at = synced_at_from(None, dest)
            return dest
        day_dir.mkdir(parents=True, exist_ok=True)
        prior = latest_prior(work_dir, self.name, MEMBER_NAME, self._today)

        probe = urllib.request.Request(
            DOWNLOAD_URL,
            headers={
                "User-Agent": USER_AGENT,
                "Range": f"bytes=-{_TAIL_BYTES}",
                **if_modified_since(prior),
            },
        )
        try:
            response_cm = self._opener(probe, timeout=DOWNLOAD_TIMEOUT)
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and prior is not None:
                shutil.copy2(prior, dest)  # unchanged upstream
                self.synced_at = synced_at_from(None, dest)
                return dest
            raise

        with response_cm as response:
            last_modified = response.headers.get("Last-Modified")
            if response.status == 206:
                total = int((response.headers.get("Content-Range") or "").rpartition("/")[2])
                tail = response.read()
                remote = _RangeFile(
                    DOWNLOAD_URL,
                    opener=self._opener,
                    size=total,
                    tail=tail,
                    validator=_validator(response.headers),
                )
                with zipfile.ZipFile(io.BufferedReader(remote, _CHUNK_BYTES)) as zf:
                    _extract_member(zf, dest)
            else:
                # The server ignored Range: stream the whole zip once, keep the member.
                whole = day_dir / "estacoes_licenciadas.zip.part"
                try:
                    with whole.open("wb") as out:
                        shutil.copyfileobj(response, out, _CHUNK_BYTES)
                    with zipfile.ZipFile(whole) as zf:
                        _extract_member(zf, dest)
                finally:
                    whole.unlink(missing_ok=True)

        set_mtime_from_header(dest, last_modified)
        self.synced_at = synced_at_from(last_modified, dest)
        return dest

    def parse(self, path: Path, *, synced_at: str | None = None) -> Iterable[Record]:
        return parse_file(path, synced_at=synced_at if synced_at is not None else self.synced_at)
