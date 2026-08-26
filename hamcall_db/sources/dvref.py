"""DVRef reflector directory importer (hdb-refl).

Produces ``ReflectorRecord``s for the M17, YSF, NXDN, P25, URF and D-Star networks — a
SEPARATE reference dataset from the callsign schema (see :mod:`hamcall_db.reflectors`).

Licence — CC BY 4.0, and why that matters here
----------------------------------------------
DVRef's "Accessing DVRef Data" announcement (2026-08-04) placed public reflector data
under **CC BY 4.0**, explicitly permitting mirroring, reformatting, commercial use, and
"host files, APIs, directories, and other services". Attribution is required, and so is
indicating modification.

That licence is LESS restrictive than this project's CC BY-NC dataset terms, which is
precisely why the reflector output is a separate artifact: adding a non-commercial
restriction to CC BY material is forbidden by CC BY 4.0 §2(a)(5)(B). Never merge these
records into the callsign dataset. See :mod:`hamcall_db.reflectors`.

Access rules DVRef asks for, and which this module implements
-------------------------------------------------------------
* **Use the API, not downstream mirrors.** Their announcement asks developers not to
  scrape the website *or* "repeatedly download data from downstream projects" — which
  names host-file mirrors like pistar.uk. So this importer talks to the API.
* **Token auth**, ``Authorization: Token <token>``, from
  <https://dvref.com/accounts/token/>. Read from the ``DVREF_API_TOKEN`` environment
  variable and never committed: their terms forbid publishing a token or embedding one
  in distributed packages, which is also why the published artifact is static JSON that
  needs no token to consume.
* **A meaningful User-Agent** naming the app and a contact URL. They are explicit that
  this is how they reach a developer instead of blocking the traffic.
* **Cache, and do not refetch unchanged data.** ``download()`` reuses a same-day file.

There is **no SLA** — DVRef is volunteer-run and says so. Callers must tolerate failure;
the publish step keeps the last good file rather than emitting an empty list.

Coverage note: D-Star
---------------------
DVRef's D-Star list is small (~61 reflectors against the XLX registry's ~892), so
:mod:`hamcall_db.sources.xlx` is the D-Star source of record and this one is a
supplement. See :func:`hamcall_db.reflectors.merge_by_id`.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from hamcall_db.reflectors import ReflectorRecord

API_ROOT = "https://dvref.com/api/v2"

# Environment variable holding the API token. Never hard-code a token here, and never
# commit one: DVRef's terms forbid publishing tokens, and a leaked token is attributed
# to the account that minted it.
TOKEN_ENV = "DVREF_API_TOKEN"

# DVRef asks for app name + version + contact URL so they can reach a developer whose
# client is misbehaving rather than silently blocking it.
USER_AGENT = "hamcall-db/0 (+https://github.com/rcludwick/hamcall-db)"

ATTRIBUTION = "Reflector data provided by DVRef — https://dvref.com/"

# DVRef path segment -> the network name we publish under. `mrefd` is the reflector
# daemon's name; the network everyone calls it is M17. `urfd` likewise -> `urf`.
NETWORKS: dict[str, str] = {
    "mrefd": "m17",
    "ysf": "ysf",
    "dstar": "dstar",
    "nxdn": "nxdn",
    "p25": "p25",
    "urfd": "urf",
}

Fetcher = Callable[[str, str], bytes]


class DvrefAuthError(RuntimeError):
    """No usable API token, or upstream rejected the one supplied."""


def _urllib_fetch(url: str, token: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Token {token}",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request) as response:  # noqa: S310 (fixed https URL)
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            # Surface what upstream actually said. A 401 really is an auth
            # problem, but a 403 from here is usually NOT about the token —
            # DVRef sits behind Cloudflare, which rejects requests by IP
            # reputation and by User-Agent signature (error 1010) before the
            # API ever authenticates them. Reporting every 403 as "bad token"
            # sends whoever reads the log to the wrong place; that mistake cost
            # a debugging session, so the body is quoted verbatim now.
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace").strip()[:300]
            except Exception:  # noqa: BLE001 - diagnostics must not mask the error
                pass
            hint = (
                f"Mint one at https://dvref.com/accounts/token/ and set {TOKEN_ENV}."
                if exc.code == 401
                else "This is usually an edge block (IP reputation or User-Agent), not the token."
            )
            raise DvrefAuthError(
                f"DVRef refused the request ({exc.code}): {detail or '<no body>'} {hint}"
            ) from exc
        raise


def _rows(payload: object) -> list[dict[str, object]]:
    """Pull the row list out of a response.

    The live envelope (verified 2026-08-26 across all six networks) is::

        {"status": "success", "generated_at": ..., "_dvref_metadata": {...},
         "data": {"reflectors": [...]}}

    DVRef's OpenAPI schema documents these endpoints as "No response body", so that
    shape is observed rather than contractual. We look inside ``data`` first, then
    accept a bare array or a flat ``results``/``reflectors`` wrapper, so a future
    reshuffle degrades to "fewer rows" rather than a crash — and the caller's
    shrink guard catches that before it reaches the published files.
    """
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []

    containers: list[object] = [payload.get("data"), payload]
    for container in containers:
        if isinstance(container, list):
            return [row for row in container if isinstance(row, dict)]
        if isinstance(container, dict):
            for key in ("reflectors", "servers", "results"):
                value = container.get(key)
                if isinstance(value, list):
                    return [row for row in value if isinstance(row, dict)]
    return []


def payload_attribution(payload: object) -> str | None:
    """The attribution string DVRef embeds in the response, if present.

    Responses carry ``_dvref_metadata.attribution`` with the exact wording their terms
    ask for. Preferring it over our own copy means the credit we publish tracks
    upstream's wording automatically instead of drifting from it.
    """
    if not isinstance(payload, dict):
        return None
    metadata = payload.get("_dvref_metadata")
    if not isinstance(metadata, dict):
        return None
    return _str(metadata.get("attribution"))


def _generated_date(payload: object) -> str | None:
    """The upstream ``generated_at`` as an ISO date — a truer stamp than file mtime."""
    if not isinstance(payload, dict):
        return None
    raw = _str(payload.get("generated_at"))
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC).date().isoformat()
    except ValueError:
        return None


class DvrefSource:
    """One DVRef network as a reflector source.

    ``segment`` is the API path segment (``"ysf"``, ``"mrefd"``, ...); ``network`` is the
    name we publish it under.
    """

    def __init__(
        self,
        segment: str,
        *,
        token: str | None = None,
        fetch: Fetcher | None = None,
    ) -> None:
        if segment not in NETWORKS:
            raise ValueError(
                f"unknown DVRef segment {segment!r}; expected one of {sorted(NETWORKS)}"
            )
        self.segment = segment
        self.network = NETWORKS[segment]
        self.name = "dvref"
        self._token = token if token is not None else os.environ.get(TOKEN_ENV, "")
        self._fetch = fetch or _urllib_fetch
        self.synced_at: str | None = None
        # Filled in by parse() from the response's own _dvref_metadata; falls back to
        # our constant if a response ever omits it.
        self.attribution: str = ATTRIBUTION

    def _identity(self, designator: str) -> tuple[str, str | None]:
        """Map a DVRef designator to the dialable id and the on-the-wire callsign.

        DVRef publishes the designator, which is not always the name a client dials:

        * **M17** designators are the three-character suffix only (``"002"``, and
          literally ``"M17"``), while the reflector is addressed as ``M17-002`` /
          ``M17-M17``. The latter is what Pi-Star's ``M17_Hosts.txt`` lists and what an
          M17 client puts on the air, so we publish the prefixed form.
        * **D-Star** designators are already in ``XRF###`` form, which is both the id
          and the callsign — these are standalone XRF reflectors, distinct from the XLX
          registry's reflectors that merely share the numbering (see
          :mod:`hamcall_db.sources.xlx`).
        * Everything else (YSF, NXDN, P25, URF) is dialled by the designator itself and
          carries no separate wire callsign.
        """
        if self.network == "m17":
            name = designator if designator.upper().startswith("M17-") else f"M17-{designator}"
            return name, name
        if self.network == "dstar":
            return designator, designator
        return designator, None

    @property
    def url(self) -> str:
        return f"{API_ROOT}/{self.segment}/reflectors/?include_description=true"

    def download(self, work_dir: Path) -> Path:
        """Fetch this network's reflector list into ``work_dir``.

        A same-day cached file is reused untouched — DVRef asks callers to "avoid
        downloading unchanged data more frequently than your application actually
        requires", and a directory that moves on a scale of weeks does not require more.
        """
        work_dir.mkdir(parents=True, exist_ok=True)
        path = work_dir / f"{self.segment}.json"
        if not path.exists():
            if not self._token:
                raise DvrefAuthError(
                    f"{TOKEN_ENV} is not set. Mint a token at "
                    "https://dvref.com/accounts/token/ (free, requires a DVRef account)."
                )
            path.write_bytes(self._fetch(self.url, self._token))
        self.synced_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).date().isoformat()
        return path

    def parse(self, path: Path, *, synced_at: str | None = None) -> Iterable[ReflectorRecord]:
        """Parse a downloaded network file into reflector records.

        Rows with neither a hostname nor an address are skipped: an entry that cannot be
        dialled is not a directory entry, it is a client-side failure waiting to happen.
        """
        payload = json.loads(path.read_text(encoding="utf-8"))
        # Upstream's own generated_at beats the cache file's mtime: it dates the DATA,
        # not the moment we happened to write it to disk.
        stamp = synced_at or _generated_date(payload) or self.synced_at
        self.attribution = payload_attribution(payload) or ATTRIBUTION

        for row in _rows(payload):
            designator = _ident(row.get("designator")) or _str(row.get("name"))
            if not designator:
                continue
            identifier, callsign = self._identity(designator)

            # Prefer the hostname: it survives an address change, and the whole point of
            # a directory entry going stale is the address moving underneath it.
            host = _str(row.get("dns")) or _str(row.get("ipv4")) or _str(row.get("ipv6"))
            if not host:
                continue

            yield ReflectorRecord(
                id=identifier,
                network=self.network,
                name=_str(row.get("name")) or identifier,
                callsign=callsign,
                host=host,
                port=_int(row.get("port")),
                modules=_modules(row.get("modules")),
                country=_str(row.get("country")),
                sponsor=_str(row.get("sponsor")),
                description=_str(row.get("description")) or _str(row.get("extended_description")),
                dashboard=_str(row.get("url")),
                source=self.name,
                synced_at=stamp,
            )


def _str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def _ident(value: object) -> str | None:
    """Coerce a designator to a string id, accepting the numeric form.

    NXDN and P25 publish ``designator`` as a JSON **number** (their reflectors are
    identified by number, not by an ``XLX836``-style name) while YSF and M17 publish a
    string. Treating only strings as valid silently dropped 55 NXDN and 51 P25
    reflectors — a filter that looked like upstream having fewer rows rather than a bug,
    which is exactly why the counts are cross-checked against DVRef's own published
    totals.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    return _str(value)


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _modules(value: object) -> list[str]:
    """Normalize the module list to sorted single uppercase letters.

    DVRef publishes ``modules`` as an array; entries have been seen both as bare letters
    and as objects carrying a module name, so handle both and drop anything that is not
    a single letter rather than emitting a module a client cannot dial.
    """
    if not isinstance(value, list):
        return []
    out: set[str] = set()
    for item in value:
        candidate: str | None = None
        if isinstance(item, str):
            candidate = item
        elif isinstance(item, dict):
            for key in ("module", "name", "designator"):
                candidate = _str(item.get(key))
                if candidate:
                    break
        if candidate and len(candidate.strip()) == 1 and candidate.strip().isalpha():
            out.add(candidate.strip().upper())
    return sorted(out)
