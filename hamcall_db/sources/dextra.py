"""Standalone XRF / DExtra reflector importer (hdb-dextra).

Produces ``ReflectorRecord``s for the ``dstar`` network, covering the reflectors
the XLX registry does NOT know about — the original xrefl.net DExtra network.

Why this source exists
----------------------
D-Star coverage used to come from two places: the XLX registry
(:mod:`hamcall_db.sources.xlx`, ~892 reflectors) and DVRef's XRF list (61). DVRef
retired its D-Star listings on 2026-08-26, saying so in the response body:

    "D-Star reflector listings (REF, DCS, XRF) are currently disabled in DVRef.
     XRF is being split out into a dedicated app; REF and DCS are maintained
     externally."

That left those 61 rows frozen — still published, but with nothing upstream to
refresh them, ageing quietly forever. This importer replaces them with a source
that is still maintained.

The obvious upstream is dead
----------------------------
``xrefl.net``, the XReflector Directory those reflectors registered with, is now
a **parked domain** — it serves a redirect to a lander page (verified
2026-08-27). That is very likely why DVRef stepped back from D-Star too.

What survives is Pi-Star's host file, which merges what xrefl.net published with
the XLX registry's own hostname endpoint. Using it is not the "downstream mirror"
DVRef asks callers to avoid: that request is about re-fetching *DVRef's* data
from third parties, and this file contains none.

RefCheck.Radio was considered and rejected: it publishes no D-Star modes at all,
and its own footer credits DVRef, so it is downstream of the source that stopped.

Deduplicating against XLX — by ADDRESS, never by name
-----------------------------------------------------
This file lists both kinds of reflector under XRF names:

* An XLX reflector's XRF alias. ``XRF836`` here is the same machine the registry
  calls ``XLX836``, at the same address — a duplicate of a row we already have.
* A standalone XRF reflector. ``XRF002`` is ``52.36.45.107``, while the registry's
  ``XLX002`` is a host in China — genuinely different machines that share a
  number.

So the two sets can only be told apart by **address**. Measured 2026-08-27
against a published build: of 893 shared names, 846 addresses matched exactly
and the rest resolved to the same hosts under DNS names. Name-matching would
either drop ~50 real reflectors or duplicate ~890 — see
:func:`hamcall_db.reflectors.merge_by_id` and the caller in
``build_reflectors``.
"""

from __future__ import annotations

import re
import urllib.request
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from hamcall_db.reflectors import ReflectorRecord

DEXTRA_HOSTS_URL = "http://www.pistar.uk/downloads/DExtra_Hosts.txt"

# DExtra's port is a protocol constant; the host file publishes only name and
# address, so we supply it — the same reasoning as in the XLX importer.
DEXTRA_PORT = 30001

_USER_AGENT = "hamcall-db/0 (+https://github.com/rcludwick/hamcall-db)"

# `XRF` plus three alphanumerics, matching the XLX naming rule. Names outside
# that shape are skipped rather than guessed at: a malformed row should cost one
# reflector, not produce an entry nobody answers to.
_NAME = re.compile(r"^XRF[0-9A-Z]{3}$")

Fetcher = Callable[[str], bytes]


def _urllib_fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request) as response:  # noqa: S310 (fixed http URL)
        return response.read()


class DextraHostsSource:
    """Pi-Star's DExtra host file as a reflector source."""

    name = "dextra"
    network = "dstar"

    def __init__(self, fetch: Fetcher | None = None) -> None:
        self._fetch = fetch or _urllib_fetch
        self.synced_at: str | None = None

    def download(self, work_dir: Path) -> Path:
        """Fetch the host file into ``work_dir``, returning the local path.

        A same-day cached file is reused rather than refetched. The endpoint does
        send ``Last-Modified``, so a conditional GET is possible and would be an
        improvement; the once-a-day cache already keeps the request count at one.
        """
        work_dir.mkdir(parents=True, exist_ok=True)
        path = work_dir / "DExtra_Hosts.txt"
        if not path.exists():
            path.write_bytes(self._fetch(DEXTRA_HOSTS_URL))
        self.synced_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).date().isoformat()
        return path

    def parse(self, path: Path, *, synced_at: str | None = None) -> Iterable[ReflectorRecord]:
        """Parse the host file into ``dstar`` reflector records.

        Format is ``NAME<TAB>ADDRESS``, with ``#`` comments. The name is both the
        id and the wire callsign here — unlike XLX rows, where the directory name
        and the DExtra name differ.
        """
        stamp = synced_at or self.synced_at
        text = path.read_text(encoding="utf-8", errors="replace")

        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            name, host = parts[0].strip().upper(), parts[1].strip()
            if not _NAME.match(name) or not host:
                continue

            yield ReflectorRecord(
                id=name,
                network=self.network,
                name=name,
                # The file's name IS the callsign a DExtra client sends; there is
                # no aliasing to undo, unlike the XLX registry's XLX-named rows.
                callsign=name,
                host=host,
                port=DEXTRA_PORT,
                source=self.name,
                synced_at=stamp,
            )


def without_known_addresses(
    records: Iterable[ReflectorRecord], known_hosts: set[str]
) -> list[ReflectorRecord]:
    """Drop rows whose address already belongs to a reflector we publish.

    The deduplication that matters, and the one place it is safe to do it. An XLX
    reflector appears in this file under its XRF alias at the SAME address, so
    keeping it would publish one machine twice under two ids. A standalone XRF
    reflector has an address of its own and must be kept, even when its number
    collides with an XLX reflector's.

    Matching on the name instead would be wrong in both directions at once:
    it would drop the standalone reflectors (whose names collide) and keep the
    aliases (whose names match). Address is the only field that distinguishes
    them.
    """
    return [r for r in records if r.host and r.host not in known_hosts]
