"""XLX / D-Star reflector directory importer (hdb-refl).

Produces ``ReflectorRecord``s for the ``dstar`` network — a SEPARATE reference dataset
from the callsign schema (see :mod:`hamcall_db.reflectors`).

Source / endpoint
-----------------
The XLX self-registration registry maintained by Luc Engelmann, LX1IQ::

    http://xlxapi.rlx.lu/api.php?do=GetReflectorList

**HTTP only.** The same host over HTTPS fails to connect outright (verified
2026-08-25 — ``curl`` returns exit 7 / code 000, not a certificate warning), so this
importer does not "upgrade" the scheme. That is a property of upstream, not a
preference; if they ever serve TLS, switch the constant.

This is deliberately NOT DVRef. DVRef's XRF list carries ~61 reflectors where this one
carries ~890, so for D-Star the XLX registry is the source with actual coverage. DVRef
is the right upstream for M17 and YSF; see :mod:`hamcall_db.sources.dvref`.

The XML is not well-formed
--------------------------
``<comment>`` values contain unescaped ``<`` and ``&`` — real rows include
``DStar <> DMR TG22208 BM222`` and ``XLX105 <-> REF018C``. A strict parser dies part-way
through the document, which is worse than it sounds: it fails *after* emitting hundreds
of valid records, so a naive importer looks like it works until a comment changes.
:func:`sanitize_xml` escapes the offending characters inside text content before parsing.

Naming: XLX vs XRF — they are NOT the same reflectors
-----------------------------------------------------
An XLX reflector answers to an ``XRF``-form callsign on the DExtra wire: dial
``XLX836`` and its RPT1/RPT2 header reads ``XRF836``. That is a property of the XLX box
naming itself, and :func:`dextra_callsign` derives it.

It does **not** mean ``XRF836`` is a synonym for ``XLX836`` globally. Standalone XRF
reflectors — the original xrefl.net DExtra network — are separate machines that share
the numbering scheme, and the numbers collide without referring to the same host.
Measured 2026-08-26 by resolving DVRef's XRF hostnames against this registry's IPs:
13 of 14 sampled ``XRF###``/``XLX###`` pairs pointed at entirely different servers
(``XRF002`` = ``xrf002.dstar.club`` 52.36.45.107; ``XLX002`` = 60.169.240.97).

So the two sets are merged WITHOUT deduplication and keep their own ids. Collapsing
``XRF002`` into ``XLX002`` would silently send a user to a reflector on another
continent. The callsign is unambiguous per host, which is all the protocol needs — you
connect to an address, and the header only says what that box calls itself.

Pi-Star's ``DExtra_Hosts.txt`` is itself a merge of both (its header credits xrefl.net
first, "additional host information" from this registry second), which is why its
``XRF002`` row carries the xrefl.net address and not this registry's ``XLX002``.
"""

from __future__ import annotations

import re
import urllib.request
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

from hamcall_db.reflectors import ReflectorRecord

# HTTP, not HTTPS — see the module docstring. Upstream does not answer on 443.
XLX_LIST_URL = "http://xlxapi.rlx.lu/api.php?do=GetReflectorList"

# DExtra's port is a protocol constant, not a per-reflector setting, so the registry
# does not publish it and we supply it. (The third column of Pi-Star's XLXHosts.txt is
# a DMR master port and is NOT this — that file is the DMR view of the same reflectors.)
DEXTRA_PORT = 30001

# Polite identifier, matching the convention in ad1c.py: upstream can see who is
# pulling and has somewhere to complain before blocking.
_USER_AGENT = "hamcall-db/0 (+https://github.com/rcludwick/hamcall-db)"

# A reflector whose registry entry has not been touched in this long is treated as gone.
# The registry keeps stale rows indefinitely, so without this the list accumulates
# reflectors that stopped answering years ago.
STALE_AFTER_DAYS = 30

Fetcher = Callable[[str], bytes]


def _urllib_fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request) as response:  # noqa: S310 (fixed http URL)
        return response.read()


# Matches a bare `<` that does not begin a tag: not followed by a name char, `/`, `!`
# or `?`. `XLX105 <-> REF018C` and `DStar <> DMR` both hit this; `<comment>` does not.
_BARE_LT = re.compile(rb"<(?![a-zA-Z/!?])")
# Matches a bare `&` that does not begin an entity reference.
_BARE_AMP = re.compile(rb"&(?!#?[a-zA-Z0-9]+;)")


def sanitize_xml(raw: bytes) -> bytes:
    """Escape unescaped ``<`` and ``&`` so ``ElementTree`` can parse the document.

    Upstream emits comment text verbatim, which makes the document invalid XML. We fix
    the characters rather than dropping the rows, because the comment often carries the
    only human description a reflector has.
    """
    return _BARE_AMP.sub(b"&amp;", _BARE_LT.sub(b"&lt;", raw))


class XlxSource:
    """The XLX registry as a reflector source. Interface mirrors ``sources.base``."""

    name = "xlx"
    network = "dstar"

    def __init__(self, fetch: Fetcher | None = None) -> None:
        self._fetch = fetch or _urllib_fetch
        self.synced_at: str | None = None

    def download(self, work_dir: Path) -> Path:
        """Fetch the registry XML into ``work_dir``, returning the local path.

        Cached under ``data/raw/xlx/<YYYY-MM-DD>/`` by the caller's choice of
        ``work_dir``; a same-day re-run reuses the file rather than re-fetching, which
        is the politeness the base protocol asks for. The endpoint sends no
        ``Last-Modified``, so there is no conditional GET to make here — the document
        carries its own ``<date>``/``<timestamp>`` instead.
        """
        work_dir.mkdir(parents=True, exist_ok=True)
        path = work_dir / "GetReflectorList.xml"
        if not path.exists():
            path.write_bytes(self._fetch(XLX_LIST_URL))
        self.synced_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).date().isoformat()
        return path

    def parse(
        self, path: Path, *, synced_at: str | None = None, now: datetime | None = None
    ) -> Iterable[ReflectorRecord]:
        """Parse the registry XML into ``dstar`` reflector records.

        Rows without a name or a last-known IP are skipped — an entry with no address
        cannot be dialled, so publishing it only produces a client-side failure later.
        """
        stamp = synced_at or self.synced_at
        moment = now or datetime.now(UTC)
        cutoff = moment.timestamp() - STALE_AFTER_DAYS * 86400

        root = ElementTree.fromstring(sanitize_xml(path.read_bytes()))  # noqa: S314
        for element in root.iter("reflector"):
            name = _text(element, "name")
            host = _text(element, "lastip")
            if not name or not host:
                continue

            last_contact = _int(element, "lastcontact")
            if last_contact is not None and last_contact < cutoff:
                continue

            yield ReflectorRecord(
                id=name,
                network=self.network,
                name=name,
                callsign=dextra_callsign(name),
                host=host,
                port=DEXTRA_PORT,
                country=_text(element, "country"),
                description=_text(element, "comment"),
                dashboard=_text(element, "dashboardurl"),
                source=self.name,
                synced_at=stamp,
            )


def dextra_callsign(name: str) -> str | None:
    """Map a registry name to the callsign DExtra expects in RPT1/RPT2.

    ``XLX836`` -> ``XRF836``, ``XLXARG`` -> ``XRFARG``: a prefix substitution that keeps
    the three-character suffix, which is alphanumeric and NOT always digits — 136 of the
    892 registered reflectors use letters (``XLXARG``, ``XLXBAS``, ``XLX00A``).

    Verified against Pi-Star's ``DExtra_Hosts.txt``, which is generated independently of
    this registry and lists exactly the same 136 non-numeric names in ``XRF`` form.

    Returns None for anything that is not an ``XLX`` name, rather than guessing — a
    wrong RPT1/RPT2 is worse than an absent one, because the client can fall back to
    asking instead of transmitting a header nobody answers to.
    """
    match = re.fullmatch(r"XLX([0-9A-Z]{3})", name.strip().upper())
    if not match:
        return None
    return f"XRF{match.group(1)}"


def _text(element: ElementTree.Element, tag: str) -> str | None:
    child = element.find(tag)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _int(element: ElementTree.Element, tag: str) -> int | None:
    raw = _text(element, tag)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None
