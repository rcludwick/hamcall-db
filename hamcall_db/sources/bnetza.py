"""BNetzA Rufzeichenliste importer (DE).

Germany's regulator, the Bundesnetzagentur (BNetzA), publishes every assigned amateur
callsign and its holder in one PDF, the *Verzeichnis der zugeteilten deutschen
Amateurfunkrufzeichen und ihrer Inhaber* (the "Rufzeichenliste"), as § 15 AFuV
requires. There is no CSV or other machine-readable form. The PDF is reissued every
few weeks, not daily; the edition of 23 October 2026 has 684 pages and 70,026 entries.

The six lists
-------------
The entries are split into six lists (explained on page "Seite 1" of the PDF). Every
list is kept; `applicant_type` says which one an entry came from:

====  =================================================================  ===================
List  What it holds (BNetzA's wording, translated)                       ``applicant_type``
====  =================================================================  ===================
1     Personal callsigns issued with a licence (§ 3 (1) AFuG)            Individual
2     Callsigns for supervised training operation (DN prefix)            Training station
3     Club station callsigns with a 1-3 letter suffix                    Club station
4     Special-event club callsigns with a 4-7 character suffix           Special event station
5     Remote or unattended stations: repeaters and beacons               Relay/beacon
6     Callsigns for special experimental studies                         Experimental station
====  =================================================================  ===================

For lists 2-6 the name is the person responsible for the station; BNetzA publishes no
club name, so that person's name is what the Record carries.

Entry shape
-----------
Each entry is free text, set in three columns and wrapped across lines (and sometimes
across a column or page break)::

    DA1AA, A, Erika Mustermann; Musterstr. 12, 38100 Braunschweig
    DB1XY, A, Hans-Peter von der Heide; Am Weiher 3 b, 36456 Barchfeld-Immelborn,
        Bahnhofstr. 2, 36433 Bad Salzungen
    DA1AB, A, Dr. Max Beispiel

That is: callsign, licence class (``A``, ``E`` or ``N``), the holder's name, then the
holder's address (street, postcode and town) after a ``;``, and then the station
location only when it differs from the holder's address. Holders who objected to
publication (§ 15 (4) AFuV) appear with callsign, class and name only. Some entries use
``,`` instead of ``;`` after the name, and repeater entries often put a site label
there ("Rolf Relais, Wasserturm, Turmweg 3, 4617 Starkenberg").

What is published
-----------------
- ``callsign``, ``license_class`` (the letter ``A``, ``E`` or ``N``), ``applicant_type``.
- ``first_name`` / ``last_name``, split from the one name field. The surname is the
  last word plus any lowercase particles before it ("von der Heide"). Academic titles
  ("Dr.", "Prof.") stay at the front of ``first_name``, so ``first_name + " " +
  last_name`` is exactly the published name: § 5 (2) UrhG applies the no-alteration
  rule of § 62 UrhG to this list.
- ``city`` and ``postal_code`` from the holder's address (the first postcode in the
  entry), not the station location. Every source in this dataset puts the licensee's
  own address in these columns, the holder's address is present far more often, and
  the station-location field is loose free text (site names, land-registry plot
  numbers, satellites). A 5-digit postcode is published with its town; a 4-digit one
  (a holder in Austria or Switzerland, or a German postcode that lost its leading zero)
  gives the town only; any other foreign format gives neither.

The street (and the rest of the address text) never reaches a Record. It is only
compared against the published fields via `echoes_street`, which drops a field that
copies it (hdb-addrguard). ``grid`` stays None: there are no German geocoder tables yet.
``state``, ``county``, the dates and the station location are not given or not used.

A callsign that appears more than once (one entry per station location) keeps its first
entry.

Validation
----------
Like the ISED importer, the parser refuses to guess. Every line on the list pages must
be a page number, a list heading (in order 1-6), an entry start, or the continuation of
an open entry; anything else raises ValueError. Callsigns must ascend within a list,
which catches a text extractor that interleaves the three columns. An entry whose
callsign isn't a German amateur callsign or whose class isn't A/E/N is rejected and
counted, never published; more than `MAX_REJECTED_FRACTION` of them raises.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path

from hamcall_db.address_guard import echoes_street, warn_scrubbed
from hamcall_db.models import Record
from hamcall_db.sources.base import synced_at_from

# Linked from https://www.bundesnetzagentur.de/amateurfunk ("Verzeichnis der
# zugeteilten deutschen Amateurfunkrufzeichen und ihrer Inhaber (Rufzeichenliste)").
DOWNLOAD_URL = (
    "https://data.bundesnetzagentur.de/Bundesnetzagentur/SharedDocs/Downloads/DE/"
    "Sachgebiete/Telekommunikation/Unternehmen_Institutionen/Frequenzen/Amateurfunk/"
    "Rufzeichenliste/rufzeichenliste_afu.pdf"
)
PDF_FILENAME = "rufzeichenliste_afu.pdf"

# The PDF is about 10 MB; allow a slow link, but don't hang forever.
DOWNLOAD_TIMEOUT = 120
USER_AGENT = "hamcall-db (+https://github.com/rcludwick/hamcall-db)"

# A layout change shows up as a flood of rejected entries; a handful is just data.
MAX_REJECTED_FRACTION = 0.005

# The six lists, their readable applicant type, and a phrase their heading must contain.
LISTS: dict[int, tuple[str, str]] = {
    1: ("Individual", "personengebundenen"),
    2: ("Training station", "Ausbildungsfunkbetrieb"),
    3: ("Club station", "Klubstation"),
    4: ("Special event station", "besonderen Anlässen"),
    5: ("Relay/beacon", "Relaisfunkstelle"),
    6: ("Experimental station", "experimentelle"),
}

LICENSE_CLASSES = frozenset({"A", "E", "N"})

# German amateur callsigns: the ITU block DA-DR, one to four digits (special-event calls
# carry anniversaries, e.g. DB1976AD), then a suffix that ends in a letter and may hold
# digits (DK50K32MR). BNetzA issues DA-DD, DF-DH and DJ-DR today; DE and DI are part of
# the German block and accepted so a new series doesn't fail the build.
CALLSIGN = re.compile(r"^D[A-R][0-9]{1,4}(?:[A-Z0-9]{0,6}[A-Z])$")

# Where an entry starts: "<callsign>, <class>, ". Deliberately looser than CALLSIGN and
# LICENSE_CLASSES, so a malformed entry is recognised (and rejected) instead of being
# glued onto the previous entry as a continuation line.
_ENTRY_START = re.compile(r"^([A-Z0-9/]{3,12}), ([A-Z]{1,2}), ?(.*)$")
_HEADING = re.compile(r"^([1-6]) Liste der ")
_PAGE_NUMBER = re.compile(r"^Seite \d+$")
_EDITION = re.compile(r"\bvom\s+(\d{1,2})\.\s*([A-Za-zäÄ]+)\s+(\d{4})\b")
_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "januar",
            "februar",
            "märz",
            "april",
            "mai",
            "juni",
            "juli",
            "august",
            "september",
            "oktober",
            "november",
            "dezember",
        ),
        start=1,
    )
}

# Postcode + town segments of an address. German postcodes have five digits.
_POSTCODE_DE = re.compile(r"^(\d{5}) (.+)$")
# Four digits then a town: Austria, Switzerland, Denmark, Belgium, or a German postcode
# that lost its leading zero ("4617 Starkenberg" = 04617). Not "1234 AB Town" (Dutch).
_POSTCODE_4 = re.compile(r"^(\d{4}) (?![A-Z]{2}\b)(.+)$")
# Other foreign postcode formats, recognised only so the search for the holder's
# postcode stops there instead of running on into the station location.
_POSTCODE_FOREIGN = re.compile(
    r"^(?:\d{4} ?[A-Z]{2} "  # Netherlands: 2628 AB Delft
    r"|\d{2,5}[- ]\d{2,4} "  # Poland 20-719, Sweden 364 95, Japan 224-0036, Portugal 1600-489
    r"|\d{6,7} "  # Romania, Chile
    r"|[A-Z]{2} \d{5}\b"  # US state + ZIP: CA 93950
    r"|(?:[A-Z]{2} )?[A-Z]\d[A-Z] \d[A-Z]\d "  # Canada: (NS) B4V 2X6
    r")"
)

# Leading words that are academic titles (kept at the front of first_name).
_TITLES = frozenset({"Dr.", "Prof.", "Dipl.", "Ing.", "Dipl.-Ing.", "Dr.-Ing.", "med."})
# Lowercase words that join a surname to the word before it ("Müller geb. Schmidt").
_SURNAME_JOINERS = frozenset({"geb.", "gen.", "genannt", "und", "y"})
_NAME_SUFFIXES = frozenset({"jun.", "sen.", "jr.", "sr."})

# BNetzA's text is WinAnsi, but one entry in the 2026-10-23 list carries a DOS code page
# 850 "ü" (byte 0x81, undefined in WinAnsi): "81475 M\x81nchen". Restore it. Any other
# control character (one entry has a stray DEL before its street) carries no text and is
# deleted.
_CP850_LEAKS = str.maketrans({"\x81": "ü"})
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


@dataclass
class ParseStats:
    """What `parse_pages` saw, for the build log and for tests."""

    entries_by_list: Counter[int] = field(default_factory=Counter)
    records_by_list: Counter[int] = field(default_factory=Counter)
    rejected: list[str] = field(default_factory=list)  # first 60 chars of each
    duplicates: int = 0
    opted_out: int = 0  # callsign + name only
    no_postcode: int = 0  # an address was given, but no postcode found in it
    scrubbed: int = 0  # published values dropped for copying the street
    repaired_characters: int = 0
    edition: str | None = None


def edition_date(pages: Sequence[str]) -> str | None:
    """The edition date printed on the title page ("vom 23. Oktober 2026"), as ISO."""
    for text in pages[:2]:
        match = _EDITION.search(text)
        if match:
            day, month_name, year = match.groups()
            month = _MONTHS.get(month_name.lower())
            if month:
                return date(int(year), month, int(day)).isoformat()
    return None


def extract_pages(path: Path, *, max_pages: int | None = None) -> list[str]:
    """Extract each page's text with pypdf (content-stream order, one column at a time)."""
    from pypdf import PdfReader

    reader = PdfReader(path)
    pages = reader.pages if max_pages is None else reader.pages[:max_pages]
    return [page.extract_text() or "" for page in pages]


def _split_name(name: str) -> tuple[str | None, str | None]:
    """Split one published name into (first_name, last_name), losing no characters."""
    words = name.split()
    if not words:
        return None, None
    i = len(words) - 1
    if words[i] in _NAME_SUFFIXES and i > 0:
        i -= 1
    # Pull in lowercase particles ("von der", "van", "zu") and joiners ("geb.") before
    # the last word; a joiner also takes the word before it ("Müller geb. Schmidt").
    while i > 0 and words[i - 1][:1].islower() and words[i - 1] not in _TITLES:
        i -= 1
        if words[i] in _SURNAME_JOINERS and i > 0:
            i -= 1
    # Never leave only titles in first_name when there is a given name to keep them
    # company, and never put a title into last_name.
    while i < len(words) - 1 and words[i] in _TITLES:
        i += 1
    first = " ".join(words[:i]) or None
    return first, " ".join(words[i:])


def _holder_location(segments: list[str]) -> tuple[str | None, str | None, bool]:
    """(city, postal_code, found) from the holder's address: the first postcode segment."""
    for segment in segments:
        match = _POSTCODE_DE.match(segment)
        if match:
            return match.group(2).strip(), match.group(1), True
        match = _POSTCODE_4.match(segment)
        if match:
            return match.group(2).strip(), None, True
        if _POSTCODE_FOREIGN.match(segment):
            return None, None, True
    return None, None, False


@dataclass
class _Entry:
    list_no: int
    callsign: str
    license_class: str
    text: str  # everything after "<callsign>, <class>, ", lines joined
    rejected: bool = False  # malformed head: counted, never published

    def add_line(self, line: str) -> None:
        # A line that ends in a hyphen continues the word ("Barchfeld-" + "Immelborn").
        if self.text.endswith("-") or not self.text:
            self.text += line
        else:
            self.text += " " + line


def _to_record(entry: _Entry, stats: ParseStats, *, synced_at: str | None) -> Record:
    text = re.sub(r"\s+", " ", entry.text).strip()
    if ";" in text:
        name_part, address = text.split(";", 1)
    elif ", " in text:
        name_part, address = text.split(", ", 1)
    else:
        name_part, address = text, ""
    # After the name there may be a site label ("Martin Kentrat, FMT Hohenroth; ...")
    # or a stray comma ("Dr. Hans Eins,; ..."): the name is what comes before it.
    name = name_part.split(",", 1)[0].strip()
    first_name, last_name = _split_name(name)

    segments = [s.strip() for s in address.split(",") if s.strip()]
    city, postal_code, found = _holder_location(segments)
    if not segments:
        stats.opted_out += 1
    elif not found:
        stats.no_postcode += 1

    # Everything in the address except the postcode segments is street, site or plot
    # text: compared against, never stored (hdb-addrguard).
    streets = [s for s in segments if not _POSTCODE_DE.match(s) and not _POSTCODE_4.match(s)]
    published = [first_name, last_name, city]
    kept = [None if echoes_street(value, streets) else value for value in published]
    stats.scrubbed += sum(a is not None and b is None for a, b in zip(published, kept, strict=True))
    first_name, last_name, city = kept

    return Record(
        callsign=entry.callsign,
        first_name=first_name,
        last_name=last_name,
        city=city,
        postal_code=postal_code,
        license_class=entry.license_class,
        applicant_type=LISTS[entry.list_no][0],
        source="bnetza",
        synced_at=synced_at,
        # state/county aren't given; country/dxcc come from cty.dat; no German geocoder
        # tables exist yet, so grid stays None.
    )


def _heading_block(lines: list[str], start: int) -> tuple[str, int]:
    """Join a heading line with the lines after it, up to a blank line or an entry."""
    parts = [lines[start]]
    end = start + 1
    while end < len(lines) and lines[end] and not _ENTRY_START.match(lines[end]):
        parts.append(lines[end])
        end += 1
    return " ".join(parts), end


def _iter_entries(pages: Sequence[str], stats: ParseStats) -> Iterator[_Entry]:
    """Assemble entries from page texts, validating every line on the list pages."""
    current_list = 0  # 0 = still in the front matter
    entry: _Entry | None = None
    previous_call = ""
    for page_no, text in enumerate(pages, start=1):
        lines = [line.strip() for line in text.splitlines()]
        first_text = next((i for i, line in enumerate(lines) if line), None)
        i = 0
        while i < len(lines):
            line = lines[i]
            where = f"page {page_no}, line {i + 1}"
            if not line:
                i += 1
                continue
            if i == first_text and _PAGE_NUMBER.match(line):
                i += 1
                continue
            heading = _HEADING.match(line)
            if heading:
                block, end = _heading_block(lines, i)
                number = int(heading.group(1))
                if ".." in block:  # the table of contents (dot leaders)
                    if current_list:
                        raise ValueError(f"{where}: table-of-contents line inside a list")
                    i = end
                    continue
                if current_list or number == 1:
                    if number != current_list + 1:
                        raise ValueError(
                            f"{where}: list {number} heading after list {current_list}"
                        )
                    if LISTS[number][1] not in block:
                        raise ValueError(f"{where}: unexpected heading for list {number}")
                    if entry is not None:
                        yield entry
                        entry = None
                    current_list, previous_call = number, ""
                    i = end
                    continue
            if not current_list:  # front matter: title page, notes, contents
                i += 1
                continue
            start = _ENTRY_START.match(line)
            if start:
                if entry is not None:
                    yield entry
                    entry = None
                callsign, license_class, rest = start.groups()
                stats.entries_by_list[current_list] += 1
                if not CALLSIGN.match(callsign) or license_class not in LICENSE_CLASSES:
                    stats.rejected.append(line[:60])
                    # Swallow the rejected entry's continuation lines with it.
                    entry = _Entry(current_list, callsign, license_class, rest, rejected=True)
                else:
                    if callsign < previous_call:
                        raise ValueError(
                            f"{where}: {callsign} follows {previous_call}; the list is "
                            "sorted, so the text extractor has mixed up the columns"
                        )
                    previous_call = callsign
                    entry = _Entry(current_list, callsign, license_class, rest)
            elif entry is not None:
                entry.add_line(line)
            else:
                raise ValueError(
                    f"{where}: not a Rufzeichenliste entry, heading or page number "
                    f"(has the PDF layout changed?): {line[:40]!r}"
                )
            i += 1
    if entry is not None:
        yield entry
    if current_list == 0:
        raise ValueError("no Rufzeichenliste list heading found (has the PDF layout changed?)")


def parse_pages(
    pages: Sequence[str],
    *,
    synced_at: str | None = None,
    stats: ParseStats | None = None,
) -> Iterator[Record]:
    """Parse extracted page texts of the Rufzeichenliste into `Record`s.

    `synced_at` defaults to the edition date printed on the title page. Pass `stats` to
    receive the counts; problems are also reported on stderr.
    """
    stats = stats if stats is not None else ParseStats()
    stats.edition = edition_date(pages)
    synced_at = synced_at if synced_at is not None else stats.edition
    seen: set[str] = set()
    for entry in _iter_entries(pages, stats):
        if entry.rejected:
            continue  # counted in stats.rejected
        if _CONTROL.search(entry.text):
            stats.repaired_characters += len(_CONTROL.findall(entry.text))
            entry.text = _CONTROL.sub("", entry.text.translate(_CP850_LEAKS))
        if entry.callsign in seen:
            stats.duplicates += 1
            continue
        seen.add(entry.callsign)
        stats.records_by_list[entry.list_no] += 1
        yield _to_record(entry, stats, synced_at=synced_at)

    total = sum(stats.entries_by_list.values())
    if not total:
        raise ValueError("the Rufzeichenliste has list headings but no entries")
    if stats.rejected:
        print(
            f"WARNING: bnetza: rejected {len(stats.rejected)} malformed entr"
            f"{'y' if len(stats.rejected) == 1 else 'ies'}, e.g. {stats.rejected[0]!r}",
            file=sys.stderr,
        )
        if len(stats.rejected) > MAX_REJECTED_FRACTION * total:
            raise ValueError(
                f"bnetza: {len(stats.rejected)} of {total} entries are malformed "
                "(has the PDF layout changed?)"
            )
    warn_scrubbed("bnetza", stats.scrubbed)


def parse_file(path: Path, *, synced_at: str | None = None) -> Iterator[Record]:
    """Extract and parse the Rufzeichenliste PDF at `path`."""
    yield from parse_pages(extract_pages(path), synced_at=synced_at)


# Injectable HTTP seam: (url, if_modified_since_epoch) -> (body or None on 304,
# Last-Modified header).
Fetcher = Callable[[str, float | None], tuple[bytes | None, str | None]]


def _urllib_fetch(url: str, since: float | None) -> tuple[bytes | None, str | None]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if since is not None:
        request.add_header("If-Modified-Since", formatdate(since, usegmt=True))
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:  # noqa: S310
            return response.read(), response.headers.get("Last-Modified")
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return None, exc.headers.get("Last-Modified") if exc.headers else None
        raise


class BnetzaSource:
    """Importer for the BNetzA Rufzeichenliste (German amateur callsign list, PDF)."""

    name = "bnetza"

    def __init__(self, *, fetcher: Fetcher | None = None) -> None:
        # ISO date of the list's edition; set during download(), stamped onto Records.
        self.synced_at: str | None = None
        self._fetcher = fetcher

    def download(self, work_dir: Path, *, on: date | None = None) -> Path:
        """Fetch the PDF into ``work_dir/data/raw/bnetza/<YYYY-MM-DD>/``.

        The list changes every few weeks, so the newest earlier copy (any date folder)
        seeds If-Modified-Since; on 304 that copy is reused for today's folder. Each saved
        file's mtime is set to the server's Last-Modified, so the next request asks about
        the right date. `synced_at` becomes the edition date printed on the title page,
        else the Last-Modified date. Transient failures are retried by the build
        (`retry_transient`).
        """
        fetch = self._fetcher or _urllib_fetch
        root = work_dir / "data" / "raw" / "bnetza"
        dest = root / (on or date.today()).isoformat() / PDF_FILENAME
        dest.parent.mkdir(parents=True, exist_ok=True)

        cached = [dest] if dest.exists() else sorted(root.glob(f"*/{PDF_FILENAME}"))[-1:]
        since = cached[0].stat().st_mtime if cached else None
        body, last_modified = fetch(DOWNLOAD_URL, since)
        if body is not None:
            dest.write_bytes(body)
            stamp = _http_date(last_modified)
            if stamp is not None:
                os.utime(dest, (stamp, stamp))
        elif not cached:
            raise RuntimeError(f"{DOWNLOAD_URL} returned 304 but nothing is cached")
        elif cached[0] != dest:
            shutil.copy2(cached[0], dest)

        self.synced_at = edition_date(extract_pages(dest, max_pages=2)) or synced_at_from(
            last_modified, dest
        )
        return dest

    def parse(self, path: Path, *, synced_at: str | None = None) -> Iterable[Record]:
        return parse_file(path, synced_at=synced_at if synced_at is not None else self.synced_at)


def _http_date(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).timestamp()
    except TypeError, ValueError:
        return None
