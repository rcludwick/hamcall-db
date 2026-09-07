"""DVRef reflector directory importer (hdb-refl).

Produces ``ReflectorRecord``s for the M17, YSF, NXDN, P25, URF and DMR networks — a
SEPARATE reference dataset from the callsign schema (see :mod:`hamcall_db.reflectors`).

Three shapes of endpoint live here. :class:`DvrefSource` reads the per-network
reflector lists, one request per network; :class:`DvrefDmrSource` reads the DMR
endpoint, which publishes networks-of-servers and is flattened to one row per server;
:class:`DvrefDmrTalkgroupSource` reads ONE DMR network's talkgroups and yields
``TalkgroupRecord``s, which is a separate table rather than reflector rows. All three
share :class:`_DvrefEndpoint` for auth, caching and metadata.

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

from hamcall_db.reflectors import ReflectorRecord, TalkgroupRecord

API_ROOT = "https://dvref.com/api/v2"

# Environment variable holding the API token. Never hard-code a token here, and never
# commit one: DVRef's terms forbid publishing tokens, and a leaked token is attributed
# to the account that minted it.
TOKEN_ENV = "DVREF_API_TOKEN"

# DVRef asks for app name + version + contact URL so they can reach a developer whose
# client is misbehaving rather than silently blocking it.
USER_AGENT = "hamcall-db/0 (+https://github.com/rcludwick/hamcall-db)"

ATTRIBUTION = "Reflector data provided by DVRef — https://dvref.com/"

# DVRef's published rate limits (API Guide, 2026-08-26).
#
#   Authenticated: 60 requests per HOUR per ACCOUNT — shared across every DVRef
#                  endpoint, and counted against the account rather than the
#                  source IP, so it follows the token wherever it is used.
#   Anonymous:     1 retrieval per resource per source IP every 6 hours, using
#                  User-Agent + X-DVRef-Callsign + X-DVRef-Contact and no token.
#
# A nightly build spends six requests on the directories themselves — five
# reflector lists plus the DMR networks endpoint — which is comfortable. DMR
# talkgroups are the endpoint that does not fit: they live behind a PER-NETWORK
# endpoint and there are 172 networks, so they are fetched as a rotating slice
# rather than a nightly sweep (see TALKGROUP_SLICE, hdb-refl-dmrtg).
#
# The trap is that interactive debugging spends from the SAME budget: running
# experiments from a workstation while the nightly job uses the same token can
# throttle the build.
# That happened on 2026-08-26. If you are poking at the API by hand, either
# expect it or use the anonymous tier, which is per-IP and would fit this
# project's once-a-night access pattern on its own.
AUTHENTICATED_HOURLY_LIMIT = 60

# How many DMR networks' talkgroup lists one nightly build may fetch (hdb-refl-dmrtg).
#
#   60  the authenticated hourly budget above
#  - 7  what the rest of the build already spends: five reflector lists, the DMR
#       networks endpoint, and one held back for the retry a transient failure costs
#  -13  headroom, because interactive debugging draws on the SAME account budget —
#       that is what throttled the build on 2026-08-26
#  ---
#   40
#
# 111 of the 172 networks have servers, so at 40 a night every network is refreshed
# roughly every three nights and a newly listed one gets its talkgroups on its first
# or second night — well inside the week clients are asked to cache for.
TALKGROUP_SLICE = 40

# A talkgroup file younger than this is not refetched even when the slice reaches it.
# The slice picks oldest-first, so this only bites when FEWER than TALKGROUP_SLICE
# networks are stale — which is exactly the case where refetching buys nothing and
# would rewrite the file's `generated` date for nothing. It is also what makes an
# immediate rebuild byte-identical.
TALKGROUP_MIN_AGE_DAYS = 2

# DVRef path segment -> the network name we publish under. `mrefd` is the reflector
# daemon's name; the network everyone calls it is M17. `urfd` likewise -> `urf`.
# NOTE: `dstar` is deliberately ABSENT. DVRef disabled its D-Star listings and
# says so in the response itself:
#
#   "D-Star reflector listings (REF, DCS, XRF) are currently disabled in DVRef.
#    XRF is being split out into a dedicated app; REF and DCS are maintained
#    externally."
#
# The endpoint still answers 200 with `status: success` and an EMPTY reflectors
# list, so nothing errors — it simply stops contributing the 61 XRF rows it used
# to. Keeping it configured would make every build look like an upstream failure
# forever, and the source guard would freeze D-Star indefinitely to protect rows
# that are never coming back. D-Star is the XLX registry's now (892 of the
# previous 953, so ~94% of coverage). Restore this entry if DVRef re-enables the
# listings or the dedicated XRF app appears.
NETWORKS: dict[str, str] = {
    "mrefd": "m17",
    "ysf": "ysf",
    "nxdn": "nxdn",
    "p25": "p25",
    "urfd": "urf",
}

Fetcher = Callable[[str, str], bytes]


class DvrefAuthError(RuntimeError):
    """No usable API token, or upstream rejected the one supplied."""


class DvrefThrottled(RuntimeError):
    """Upstream asked us to slow down, and said for how long.

    Distinct from :class:`DvrefAuthError` because the remedy is different and
    mechanical: wait. ``retry_after`` is seconds, taken from the response body's
    ``retry_after_seconds`` or the ``Retry-After`` header, and is None only if
    upstream sent neither.
    """

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


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
        if exc.code == 429:
            # Upstream tells us exactly how long to wait, in the body and again
            # in a header. Treating that as a generic failure throws the answer
            # away and skips the network for the night over something that
            # resolves itself.
            raise _throttled(exc) from exc
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


def _throttled(exc: urllib.error.HTTPError) -> DvrefThrottled:
    """Build a :class:`DvrefThrottled` from a 429, preserving the wait it names."""
    body = ""
    try:
        body = exc.read().decode("utf-8", "replace").strip()
    except Exception:  # noqa: BLE001 - diagnostics must not mask the error
        pass

    retry_after: int | None = None
    try:
        payload = json.loads(body)
        if isinstance(payload, dict):
            value = payload.get("retry_after_seconds")
            if isinstance(value, int) and not isinstance(value, bool):
                retry_after = value
    except ValueError, TypeError:
        pass
    if retry_after is None:
        header = exc.headers.get("Retry-After") if exc.headers else None
        if header and str(header).strip().isdigit():
            retry_after = int(str(header).strip())

    detail = body[:200] or "<no body>"
    wait = f" Retry after {retry_after}s." if retry_after is not None else ""
    return DvrefThrottled(f"DVRef throttled the request (429): {detail}{wait}", retry_after)


def _rows(payload: object) -> list[dict[str, object]]:
    """Pull the row list out of a response.

    The live envelope (verified 2026-08-26 across all six networks) is::

        {"status": "success", "generated_at": ..., "_dvref_metadata": {...},
         "data": {"reflectors": [...]}}

    The DMR endpoint is the same envelope with a different row key —
    ``data.networks`` (verified 2026-09-06) — so one reader serves both.

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
            for key in ("reflectors", "networks", "servers", "results"):
                value = container.get(key)
                if isinstance(value, list):
                    return [row for row in value if isinstance(row, dict)]
    return []


def _talkgroup_rows(payload: object) -> list[dict[str, object]]:
    """Pull the talkgroup list out of a per-network talkgroups response.

    Same envelope as everything else, one level deeper (verified 2026-09-07)::

        {"status": "success", "generated_at": ..., "_dvref_metadata": {...},
         "data": {"network": {"network": "SystemX", "talkgroups": [{"tg": 69, ...}]}}}

    ``data.network`` is an OBJECT here, not a list, which is why :func:`_rows` cannot
    serve this endpoint: it looks for a list under ``data`` and would find none. The
    fallbacks are the same defensive posture — a reshuffle upstream should cost rows,
    not crash the nightly build.
    """
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []

    data = payload.get("data")
    containers: list[object] = []
    if isinstance(data, dict):
        containers.extend((data.get("network"), data))
    containers.append(payload)
    for container in containers:
        if isinstance(container, dict):
            value = container.get("talkgroups")
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def payload_notice(payload: object) -> str | None:
    """Any operational message upstream attached to the response.

    DVRef uses ``data.notice`` to explain a deliberately empty result — it is how
    we learned the D-Star listings had been switched off rather than broken. A
    field that only appears when something has changed is exactly the field worth
    printing, so the build echoes it instead of silently reporting zero rows.
    """
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, dict):
        notice = _str(data.get("notice"))
        if notice:
            return notice
    return _str(payload.get("notice"))


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


class _DvrefEndpoint:
    """Shared plumbing for one DVRef endpoint: token, fetch, cache, metadata.

    Subclasses supply :attr:`url` and a ``cache_name``, and do their own row shaping in
    ``parse()``. Everything above that is identical for every endpoint — the same token,
    the same hourly budget, the same "reuse a same-day file" rule — and duplicating it
    per endpoint is how one of them ends up quietly refetching or missing a header.
    """

    #: Which importer produced the row. One name for every DVRef endpoint: `source` is
    #: provenance, and the build's source guard keys on it.
    name = "dvref"

    #: File name for the cached response inside the work dir.
    cache_name = "dvref.json"

    def __init__(self, *, token: str | None = None, fetch: Fetcher | None = None) -> None:
        self._token = token if token is not None else os.environ.get(TOKEN_ENV, "")
        self._fetch = fetch or _urllib_fetch
        self.synced_at: str | None = None
        # Filled in by parse() from the response's own _dvref_metadata; falls back to
        # our constant if a response ever omits it.
        self.attribution: str = ATTRIBUTION
        # Set by parse() when upstream attaches an operational message.
        self.notice: str | None = None

    @property
    def url(self) -> str:  # pragma: no cover - every subclass overrides this
        raise NotImplementedError

    def download(self, work_dir: Path) -> Path:
        """Fetch this endpoint's response into ``work_dir``.

        A same-day cached file is reused untouched — DVRef asks callers to "avoid
        downloading unchanged data more frequently than your application actually
        requires", and a directory that moves on a scale of weeks does not require more.
        """
        work_dir.mkdir(parents=True, exist_ok=True)
        path = work_dir / self.cache_name
        if not path.exists():
            if not self._token:
                raise DvrefAuthError(
                    f"{TOKEN_ENV} is not set. Mint a token at "
                    "https://dvref.com/accounts/token/ (free, requires a DVRef account)."
                )
            path.write_bytes(self._fetch(self.url, self._token))
        self.synced_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).date().isoformat()
        return path

    def _envelope(self, path: Path, synced_at: str | None) -> tuple[object, str | None]:
        """Read a cached response, record its metadata, and return it with its date.

        Upstream's own ``generated_at`` beats the cache file's mtime: it dates the DATA,
        not the moment we happened to write it to disk.
        """
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.attribution = payload_attribution(payload) or ATTRIBUTION
        self.notice = payload_notice(payload)
        return payload, synced_at or _generated_date(payload) or self.synced_at


class DvrefSource(_DvrefEndpoint):
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
        super().__init__(token=token, fetch=fetch)
        self.segment = segment
        self.network = NETWORKS[segment]
        self.cache_name = f"{segment}.json"

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
        # Dormant: DVRef retired its D-Star listings, so this branch is currently
        # unreachable (the constructor rejects the segment). Kept because it is the
        # correct behaviour if they re-enable them or the dedicated XRF app lands —
        # restoring the NETWORKS entry should be the only change needed.
        if self.network == "dstar":
            return designator, designator
        return designator, None

    @property
    def url(self) -> str:
        return f"{API_ROOT}/{self.segment}/reflectors/?include_description=true"

    def parse(self, path: Path, *, synced_at: str | None = None) -> Iterable[ReflectorRecord]:
        """Parse a downloaded network file into reflector records.

        Rows with neither a hostname nor an address are skipped: an entry that cannot be
        dialled is not a directory entry, it is a client-side failure waiting to happen.
        """
        payload, stamp = self._envelope(path, synced_at)

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


class DvrefDmrSource(_DvrefEndpoint):
    """DVRef's DMR networks, published as ONE ROW PER SERVER.

    Why a server and not a network
    ------------------------------
    The DMR endpoint is shaped differently from the other five: it lists 172 *networks*,
    each holding zero or more *servers*. A network is an organisation, not an address —
    what an operator actually points a hotspot at is one server, with a host and a port.
    So the row this publishes is the server, and the network it belongs to travels with
    it as ``system``: a talkgroup number is only defined within one network, so a master
    without its network name is not enough to talk to anybody.

    A network with no servers publishes nothing. 61 of the 172 are in that state — a
    listing on DVRef with no address behind it — and a row you cannot dial and cannot
    even describe as a place is not a directory entry.

    Data quality, and why ``dns`` is not trusted
    -------------------------------------------
    ``dns`` is a free-text field upstream and is not always a host. FreeDMR fills it with
    real hostnames on port 62031; several SystemX entries carry a DASHBOARD URL
    (``https://apollo.dmr.uk.pe/dashboard/``) with a null port. Publishing that as a host
    would hand a client something it cannot resolve, let alone connect to, so anything
    that is not a bare hostname or IP literal is rejected and the numeric address is used
    instead. When nothing usable remains the row is still published — a server you can
    name and attribute is worth listing — but WITHOUT a ``dial``, per the API's rule that
    an address is never invented.

    Talkgroups are mirrored a slice at a time
    ----------------------------------------
    They live behind a per-network endpoint, which is 172 requests against an hourly
    budget of 60 (:data:`AUTHENTICATED_HOURLY_LIMIT`). This endpoint costs ONE request a
    night and publishes ``talkgroups_url``, DVRef's canonical list, on every row; the
    mirror is fetched separately by :class:`DvrefDmrTalkgroupSource`, a rotating
    :data:`TALKGROUP_SLICE` of networks a night (hdb-refl-dmrtg).
    """

    network = "dmr"
    cache_name = "dmr-networks.json"

    #: What the operator must supply and a public directory cannot: a DMR ID is issued to
    #: a person, and the password is per-master. A client seeing this should prompt.
    REQUIRES: tuple[str, ...] = ("dmr_id", "password")

    @property
    def url(self) -> str:
        return f"{API_ROOT}/dmr/networks/?include_description=true"

    def parse(self, path: Path, *, synced_at: str | None = None) -> Iterable[ReflectorRecord]:
        """Flatten the networks-of-servers response into one record per server."""
        payload, stamp = self._envelope(path, synced_at)

        for network in _rows(payload):
            system = _str(network.get("slug"))
            if not system:
                continue
            servers = network.get("servers")
            if not isinstance(servers, list):
                continue
            # The display name is the fallback sponsor: a network with no sponsor
            # recorded is still run by someone, and "FreeDMR" beats an empty cell.
            sponsor = _str(network.get("sponsor")) or _str(network.get("network"))
            description = _str(network.get("description"))
            dashboard = _str(network.get("url"))
            talkgroups_url = _str(network.get("tglist"))

            for server in servers:
                if not isinstance(server, dict):
                    continue
                identifier = _str(server.get("slug"))
                if not identifier:
                    continue
                yield ReflectorRecord(
                    id=identifier,
                    network=self.network,
                    name=_str(server.get("server")) or identifier,
                    host=_host(server.get("dns"))
                    or _host(server.get("ipv4"))
                    or _host(server.get("ipv6")),
                    port=_int(server.get("port")),
                    country=_str(server.get("country")),
                    # The network's, not the server's: DVRef records these once per
                    # network, and a server inherits the organisation that runs it.
                    sponsor=sponsor,
                    description=description,
                    dashboard=dashboard,
                    source=self.name,
                    synced_at=stamp,
                    system=system,
                    requires=list(self.REQUIRES),
                    talkgroups_url=talkgroups_url,
                )


class DvrefDmrTalkgroupSource(_DvrefEndpoint):
    """ONE DMR network's talkgroup list — one request, one network (hdb-refl-dmrtg).

    A talkgroup number only means something inside one network, so the row this yields
    is ``(system, tg, name)`` and ``system`` is never optional.

    **One request per network is the whole design problem.** 172 networks against a
    60-per-hour account budget cannot be swept nightly, so the build fetches a rotating
    :data:`TALKGROUP_SLICE` of them, oldest-first, and every other network keeps the
    list it already has. That makes a published talkgroup list up to a few days behind
    upstream by construction — which is why ``talkgroups_url`` stays on every DMR server
    row: it is upstream's canonical list, and this is a mirror of it.
    """

    network = "dmr"

    def __init__(
        self,
        system: str,
        *,
        token: str | None = None,
        fetch: Fetcher | None = None,
    ) -> None:
        slug = _str(system)
        if not slug:
            raise ValueError("a DMR talkgroup fetch needs a network slug")
        super().__init__(token=token, fetch=fetch)
        self.system = slug
        # Namespaced by slug so 111 of these can share one day's cache directory with
        # the reflector lists without colliding.
        self.cache_name = f"talkgroups-{slug}.json"

    @property
    def url(self) -> str:
        return f"{API_ROOT}/dmr/networks/{self.system}/talkgroups/"

    def parse(self, path: Path, *, synced_at: str | None = None) -> Iterable[TalkgroupRecord]:
        """Parse one network's talkgroups response into records.

        ``synced_at`` is the FETCH date, not upstream's ``generated_at``: this endpoint
        stamps every response with the moment it was rendered, so its own date says when
        we asked rather than when the list last changed. What a client needs to know is
        how stale the mirror is, and that is the date of the fetch.
        """
        payload, _ = self._envelope(path, synced_at)
        stamp = synced_at or self.synced_at

        seen: set[int] = set()
        for row in _talkgroup_rows(payload):
            number = _int(row.get("tg"))
            if number is None or number in seen:
                # A talkgroup with no number cannot be dialled, and a duplicate would
                # break the (system, tg) key the published file and both artifacts use.
                continue
            seen.add(number)
            yield TalkgroupRecord(
                system=self.system,
                tg=number,
                name=_str(row.get("name")),
                synced_at=stamp,
            )


def _host(value: object) -> str | None:
    """A bare hostname or IP literal, or None if the value is anything else.

    DVRef's ``dns`` column holds a dashboard URL for several DMR servers rather than a
    host — ``https://apollo.dmr.uk.pe/dashboard/`` is a real value, and so is the
    path-bearing ``ipsc2.freestar.network/ipsc``. Both LOOK like an address and neither
    is one: a client would try to resolve the whole string and fail, or worse, strip it
    to something that resolves to the wrong machine. So a value carrying a scheme, a
    path, or whitespace is refused rather than repaired — the row then falls back to the
    numeric address, and if there is none it is published without a ``dial``.
    """
    host = _str(value)
    if host is None:
        return None
    if "://" in host or "/" in host or any(character.isspace() for character in host):
        return None
    return host


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
