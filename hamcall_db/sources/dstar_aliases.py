"""REF and DCS names for D-Star reflectors we already publish (hdb-dstar-alias).

This importer creates no rows. It adds *names* to rows that already exist, and
that limit is the whole design.

Why the other names matter
--------------------------
D-Star has three linking protocols — DPlus (``REF``), DExtra (``XRF``) and DCS
(``DCS``) — and the XLX multiprotocol reflector answers on all three. One
machine, three names. XLX836's own dashboard says so per module::

    A   Int.        REF836AL    XRF836AL    DCS836AL
    B   Regional    REF836BL    XRF836BL    DCS836BL

An operator who knows that box as ``REF836`` should find it, and dial it over
DExtra, without having to know it is filed under ``XLX836``. That is exactly
what ``aliases`` is for, and it already carries the XRF form.

Aliases, never rows
-------------------
A name published here is searchable and resolvable; it is NOT a claim that
astar (or anything else) can speak DPlus or DCS. The reflector is reached over
DExtra, at the address and port the existing row already carries. Publishing
``REF001`` as a *row* would be the opposite claim, and a false one — the
128 reflectors that answer only to DPlus are a separate question with a
separate protocol behind it.

Matched by ADDRESS, never by name
---------------------------------
The same rule the DExtra importer had to learn, for the same reason. Measured
2026-08-28 against the published feed: of 896 ``REF###`` entries, 768 sit at an
address the DExtra file also lists (an XLX box under another name) and 128 do
not. And the numbering does not line up between protocols —

* ``REF836`` is ``45.56.69.219``, and so is ``XRF836``: one machine.
* ``REF001`` is ``104.237.157.7``; ``XRF001`` is ``217.154.120.107``: two
  unrelated machines that happen to share a number.

So matching ``REF001`` to ``XLX001`` on the digits would attach a name that
belongs to somebody else's reflector — the same "wrong in both directions"
failure :mod:`hamcall_db.sources.dextra` documents.
"""

from __future__ import annotations

import re
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path

from hamcall_db.reflectors import ReflectorRecord

DPLUS_HOSTS_URL = "http://www.pistar.uk/downloads/DPlus_Hosts.txt"
DCS_HOSTS_URL = "http://www.pistar.uk/downloads/DCS_Hosts.txt"

_USER_AGENT = "hamcall-db/0 (+https://github.com/rcludwick/hamcall-db)"

# The reflector name-spaces worth publishing as aliases, applied to EVERY file
# rather than selected per file. A `REF###` is one wherever it appears, and
# keying this on the file name would mean a caller who saved the download under
# another name silently got no aliases at all — a failure that looks exactly
# like an upstream with nothing in it.
#
# Everything else in those files is deliberately excluded: the DPlus file lists
# ~590 repeater gateways under their own callsigns (`DB0…`, `GB7…`, `F1Z…`),
# and the DCS file repeats every XLX reflector under its `XLX###` name. A
# repeater is not the reflector, and the XLX name is already the row's id.
_NAME = re.compile(r"^(?:REF|DCS)[0-9A-Z]{3}$")

Fetcher = Callable[[str], bytes]


def _urllib_fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request) as response:  # noqa: S310 (fixed http URL)
        return response.read()


class DStarAliasSource:
    """Pi-Star's DPlus and DCS host files, read for names rather than rows."""

    name = "pistar-aliases"

    #: file name -> (url, which name-space to keep)
    FILES: dict[str, tuple[str, str]] = {
        "DPlus_Hosts.txt": (DPLUS_HOSTS_URL, "dplus"),
        "DCS_Hosts.txt": (DCS_HOSTS_URL, "dcs"),
    }

    def __init__(self, fetch: Fetcher | None = None) -> None:
        self._fetch = fetch or _urllib_fetch

    def download(self, work_dir: Path) -> list[Path]:
        """Fetch both host files into ``work_dir``. Same-day files are reused."""
        work_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for filename, (url, _) in self.FILES.items():
            path = work_dir / filename
            if not path.exists():
                path.write_bytes(self._fetch(url))
            paths.append(path)
        return paths

    def parse(self, paths: Iterable[Path]) -> dict[str, list[str]]:
        """Build ``address -> [names]`` from the host files.

        Order within an address follows the order the paths arrive in (DPlus
        before DCS, as :meth:`download` returns them) and then file order, so an
        unchanged upstream produces byte-identical output and the nightly build
        makes no commit.
        """
        by_address: dict[str, list[str]] = {}
        for path in paths:
            text = path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                name, address = parts[0].strip().upper(), parts[1].strip()
                if not address or not _NAME.match(name):
                    continue
                names = by_address.setdefault(address, [])
                if name not in names:
                    names.append(name)
        return by_address


def apply_aliases(
    records: Iterable[ReflectorRecord], names_by_address: dict[str, list[str]]
) -> tuple[list[ReflectorRecord], int]:
    """Attach the other-protocol names to the rows that share their address.

    Returns the records (mutated in place, and returned for the caller's
    convenience) and how many aliases were actually added.

    Three things are refused, and each is a way the directory could otherwise
    start lying:

    * **A name that is some row's id.** Client name-indexes map a name to an
      entry; a string that is both an alias of one reflector and the id of
      another resolves to whichever the index happened to see first. No such
      collision exists in today's data — `REF###` and `DCS###` share no prefix
      with `XLX###` or `XRF###` — but publishing the 128 DPlus-only reflectors
      as rows would create exactly that overlap, so the guard is here before
      the data that needs it.
    * **A name a row already has**, including its own id, name and wire
      callsign. Duplicates in `aliases` are noise at best.
    * **Any row with no address.** There is nothing to match on, and matching
      on anything else is the mistake this module exists to avoid.
    """
    rows = list(records)
    taken = {r.id.upper() for r in rows}
    added = 0
    for record in rows:
        if not record.host:
            continue
        candidates = names_by_address.get(record.host)
        if not candidates:
            continue
        existing = {a.upper() for a in record.aliases}
        for field_value in (record.id, record.name, record.callsign):
            if field_value:
                existing.add(field_value.upper())
        for candidate in candidates:
            if candidate in existing or candidate in taken:
                continue
            record.aliases.append(candidate)
            existing.add(candidate)
            added += 1
    return rows, added
