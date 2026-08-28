"""Digital-voice reflector directory — a SEPARATE reference dataset (hdb-refl).

Reflectors are PLACES, not licensees: this module defines its own ``ReflectorRecord``
and does not touch the callsign ``Record`` contract (mem-c6e0), exactly like the POTA
parks and SOTA summits reference sets.

LICENSING — read before adding a source
---------------------------------------
The reflector data published from here is **CC BY 4.0**, NOT the project's CC BY-NC
dataset licence, and the two must never be merged into one artifact.

DVRef's data is CC BY 4.0 (their 2026-08-04 "Accessing DVRef Data" announcement).
CC BY 4.0 §2(a)(5)(B) forbids imposing "additional or different terms ... if doing so
restricts exercise of the Licensed Rights" — folding it into the CC BY-NC artifacts
would add a non-commercial restriction that CC BY grants away, so it would be a licence
violation, not merely untidy. This is the same reasoning that keeps the OpenStreetMap
(ODbL) park grids in their own file, and it points the other way from the ODbL case:
there the upstream was MORE restrictive, here it is LESS.

The consequence is a rule with no exceptions: reflector output is its own artifact,
carrying its own licence and attribution, and nothing joins it to the callsign dataset
at publish time.

Output shape — the v1 API
-------------------------
The published surface is static JSON served by GitHub Pages. ``docs/REFLECTOR-API.md``
is the authoritative contract; this module implements it and generates the OpenAPI
document from the same tables the emitter uses, so the two cannot drift::

    site/api/v1/index.json                 manifest: networks, counts, licence, freshness
    site/api/v1/reflectors.json            every reflector, one file (the primary endpoint)
    site/api/v1/reflectors/<network>.json  one network
    site/api/v1/openapi.json               the contract, machine-readable

``reflectors.json`` is what most clients want; the per-network files exist for FAILURE
ISOLATION — upstreams fail independently, and a D-Star-only client should not have to
re-download 1400 YSF rows to learn that nothing changed.

Every entry is a common envelope (what you search and display) plus a discriminated
``dial`` object (what you connect with, which genuinely differs per protocol). An absent
``dial`` means "listed but not dialable from this data" — we do not invent an address.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, fields
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

# The licence the published reflector JSON carries, and the attribution DVRef asks for
# verbatim in their announcement. Both are written into every emitted file — an
# attribution that lives only in a README is one copy-paste away from being lost.
REFLECTOR_LICENSE = "CC BY 4.0"
REFLECTOR_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
REFLECTOR_LICENSE_SPDX = "CC-BY-4.0"

ATTRIBUTION_SUMMARY = (
    "Reflector data provided by DVRef — https://dvref.com/. XLX reflector data from the "
    "XLX registry maintained by Luc Engelmann, LX1IQ (http://xlxapi.rlx.lu/)."
)


# --- email redaction -------------------------------------------------------------
# The published files are a bulk-downloadable, machine-readable static artifact on a
# CDN. A sysop's address written into a reflector blurb is one thing on a dashboard
# page and quite another in a JSON file anyone can grep — it is a spam-harvesting
# vector, and republishing it cuts against the same privacy posture that truncates
# licensee grids to four characters and keeps street addresses out of the build
# entirely. So addresses are stripped from every free-text field we publish.
#
# The marker is left in place rather than deleting silently, so "contact <addr> for
# access" stays a readable sentence instead of trailing off.
EMAIL_PLACEHOLDER = "[email removed]"

# Conservative on purpose: a local part, an @, a dotted domain and a real TLD. It does
# not fire on "TG@22208" or on a callsign, and while it will rewrite the address inside
# a `mailto:` URL that is the intent — no http(s) URL in this data carries an @ before
# its host.
EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}"
)


def redact_emails(text: str | None) -> str | None:
    """Replace any email address in ``text`` with :data:`EMAIL_PLACEHOLDER`.

    Applied to every free-text field on the way INTO a record, so that every published
    artifact — JSON, Parquet and SQLite alike — is covered by one rule that no importer
    can forget. The cached raw downloads under the work dir are deliberately left
    untouched: those are faithful copies of upstream.
    """
    if text is None:
        return None
    return EMAIL_PATTERN.sub(EMAIL_PLACEHOLDER, text)


# CC BY requires indicating that changes were made. We reshape upstream rows into a
# common JSON schema AND strip email addresses out of the free-text fields, which edits
# values rather than merely rearranging them — so both are declared, in the file itself.
MODIFICATION_NOTE = (
    "Reformatted from the upstream source into hamcall-db's common reflector JSON "
    "schema. Field names and structure differ from upstream. Email addresses have been "
    "removed from the free-text fields (name, sponsor, description) and replaced with "
    f"'{EMAIL_PLACEHOLDER}'; values are otherwise unmodified."
)

# How long a client should sit on a cached copy before asking again. A week, because
# these files are served from GitHub Pages and every astar install polling nightly is
# request volume that buys nothing — reflector hosts move on a scale of weeks.
CLIENT_REFRESH_DAYS = 7

# Attribution statements are joined with a newline rather than a space. A network
# assembled from two upstreams carries two statements, and the combined file has to
# be able to take them apart again to avoid repeating one — D-Star's credit is
# "XLX ... DVRef ..." while M17's is the DVRef line alone, so deduplicating whole
# strings leaves DVRef credited twice. A newline is an unambiguous split point that
# no attribution statement contains, and it renders as separate lines besides.
ATTRIBUTION_SEPARATOR = "\n"

# The JSON schema version of the published files. Bump only when an existing field
# changes meaning or goes away; adding a field is not a bump (an older reader ignores
# what it does not recognise).
SCHEMA_VERSION = 1

# The path segment the whole API lives under. A bump moves the path (/api/v2/...) and
# the old path keeps serving until clients migrate — see docs/REFLECTOR-API.md.
API_VERSION = "v1"

# Where the files are actually served from. Only used to fill OpenAPI's `servers`, so a
# generated client points at the real host instead of a placeholder.
API_BASE_URL = f"https://rcludwick.github.io/hamcall-db/api/{API_VERSION}"

# File names, in one place: the emitter, the manifest's `endpoints` block and the
# OpenAPI paths all read these, so a rename cannot leave one of the three behind.
MANIFEST_FILE = "index.json"
ALL_REFLECTORS_FILE = "reflectors.json"
OPENAPI_FILE = "openapi.json"
NETWORK_DIR = "reflectors"


@dataclass(slots=True)
class ReflectorRecord:
    """One digital-voice reflector.

    ``network`` + ``id`` is the primary key: reflector ids are unique within a network
    but not across them. ``host`` may be a hostname or a bare IP — upstream publishes
    both and we do not resolve, because a resolved A record goes stale faster than the
    directory entry does.
    """

    id: str  # e.g. "XLX836", "M17-M17", "00009" — unique within `network`
    network: str  # 'dstar' | 'm17' | 'ysf' | 'nxdn' | 'p25' | 'urf' | 'dmr'
    name: str | None = None
    # Other names that address THIS reflector — searchable, never a dedup key. An XLX
    # reflector answers to its XRF-form name on the DExtra wire, so XLX836 carries the
    # alias XRF836. That does NOT make it the same machine as a standalone XRF836: see
    # sources/xlx.py and `merge_by_id` below.
    aliases: list[str] = field(default_factory=list)
    # The callsign this reflector answers to ON THE WIRE, which is not always its
    # directory name: an XLX reflector is listed as "XLX836" but a DExtra client must
    # put "XRF836" in the RPT1/RPT2 header fields. Publishing it means a client reads
    # the value instead of reimplementing the aliasing rule (and getting it wrong, or
    # sending a blank header, which is the failure this field exists to prevent).
    callsign: str | None = None
    host: str | None = None  # hostname or IP, verbatim from upstream
    port: int | None = None  # protocol port; None when upstream omits it
    modules: list[str] = field(default_factory=list)  # 'A'..'Z'; empty where n/a (YSF)
    country: str | None = None
    sponsor: str | None = None  # who runs it, as upstream words it
    description: str | None = None
    dashboard: str | None = None  # web dashboard URL, when upstream publishes one
    source: str | None = None  # 'dvref' | 'xlx' — which importer produced this row
    synced_at: str | None = None  # ISO date of the upstream pull

    def __post_init__(self) -> None:
        # One choke point for redaction: doing it here rather than in each importer
        # means a future source cannot publish an address by forgetting a call, and it
        # covers the Parquet and SQLite artifacts as well as the JSON.
        for name in REDACTED_TEXT_FIELDS:
            setattr(self, name, redact_emails(getattr(self, name)))


#: Free-text fields scrubbed of email addresses before publication.
REDACTED_TEXT_FIELDS: tuple[str, ...] = ("name", "sponsor", "description")

REFLECTOR_SCHEMA_COLUMNS: tuple[str, ...] = tuple(f.name for f in fields(ReflectorRecord))
"""Ordered field names of the published reflector rows."""


# --- the `dial` discriminator -----------------------------------------------------
# `dial.kind` is what a client switches on to decide HOW to connect, and it is the
# extension point of the whole schema: a new service ships as a new kind, and an old
# client degrades to "listed, not offered" rather than breaking.
#
# These three tables are the single source of truth. The emitter builds `dial` from
# them and `openapi_document()` builds the schemas from them, so a kind cannot reach the
# data without reaching the published contract.

#: network -> the `dial.kind` its entries carry.
DIAL_KINDS: dict[str, str] = {
    "dstar": "dextra",  # XLX and XRF reflectors, dialled with DExtra
    "m17": "m17",
    "ysf": "ysf",
    "nxdn": "nxdn",
    "p25": "p25",
    "urf": "urf",
    "dmr": "mmdvm",  # contract only — no DMR source is wired up yet
}

#: `dial.kind` -> the record fields it carries beyond `host` and `port`.
DIAL_FIELDS: dict[str, tuple[str, ...]] = {
    "dextra": ("callsign", "modules"),
    "m17": ("callsign", "modules"),
    "ysf": (),
    "nxdn": (),
    "p25": (),
    "urf": ("modules",),
    # A DMR master needs a per-user credential a public file cannot carry, so the
    # variant says what the OPERATOR must supply instead of pretending otherwise.
    "mmdvm": ("requires", "talkgroups_url"),
}

#: Kinds whose port is genuinely unknown upstream, and therefore optional.
#: URF is the real case: DVRef publishes no port for any of the 89 URF reflectors, and a
#: urfd speaks several protocols at once so there is no single port to supply. Every
#: other kind must carry one or the entry is not dialable.
DIAL_PORT_OPTIONAL: frozenset[str] = frozenset({"urf"})


def dial_kind(network: str) -> str:
    """The ``dial.kind`` for ``network``.

    An unmapped network falls back to its own name, so a source added without touching
    this table still emits a coherent (if undocumented) kind — and the OpenAPI coverage
    test fails, which is the point: it makes the omission loud rather than silent.
    """
    return DIAL_KINDS.get(network, network)


def _dial_json(record: ReflectorRecord) -> dict[str, object] | None:
    """The ``dial`` object for a record, or None when it is not dialable.

    No address means no dial, and a missing port means no dial for every kind but URF.
    We do not substitute a default to fill the gap: an entry you cannot address is
    better shown greyed out than dialled wrongly.
    """
    if not record.host:
        return None
    kind = dial_kind(record.network)
    if record.port is None and kind not in DIAL_PORT_OPTIONAL:
        return None

    dial: dict[str, object] = {"kind": kind, "host": record.host}
    if record.port is not None:
        dial["port"] = record.port
    for name in DIAL_FIELDS.get(kind, ()):
        value = getattr(record, name, None)
        if value is None or value == [] or value == "":
            continue
        dial[name] = value
    return dial


# Envelope fields, in the order docs/REFLECTOR-API.md lists them. `network` is repeated
# on every entry rather than living once at document level, because reflectors.json
# mixes networks — and an entry whose meaning depended on which file it came from would
# be a trap for any client that caches rows individually.
_ENVELOPE_FIELDS: tuple[str, ...] = (
    "network",
    "id",
    "name",
    "aliases",
    "description",
    "country",
    "sponsor",
    "dashboard",
    "source",
)


def entry_json(record: ReflectorRecord) -> dict[str, object]:
    """One record as a published entry: envelope plus discriminated ``dial``.

    Empty values are omitted rather than emitted as null. That keeps the files
    meaningfully smaller (the YSF set is ~1400 rows) and reads the same to any client
    that treats a missing key as unset — the tolerance the version rule already assumes.
    """
    out: dict[str, object] = {}
    for name in _ENVELOPE_FIELDS:
        value = getattr(record, name, None)
        if name == "name" and not value:
            value = record.id  # display name falls back to the id
        if value is None or value == [] or value == "":
            continue
        out[name] = value
    dial = _dial_json(record)
    if dial is not None:
        out["dial"] = dial
    return out


def _license_block() -> dict[str, object]:
    return {
        "license": REFLECTOR_LICENSE,
        "license_url": REFLECTOR_LICENSE_URL,
        "modifications": MODIFICATION_NOTE,
    }


def drop_shadowing_aliases(records: Sequence[ReflectorRecord]) -> int:
    """Remove any alias that is also some OTHER row's id. Returns how many went.

    A name has to mean one reflector. Clients index ``id`` and ``aliases``
    together and resolve a typed name to a single entry, so a string that is an
    alias of one row and the id of another resolves to whichever the index
    happened to see first — silently, and differently depending on row order.

    This is not hypothetical. ``XLX002`` carries the alias ``XRF002`` because an
    XLX reflector answers to its XRF-form callsign on the DExtra wire; there is
    also a standalone ``XRF002``, a different machine on a different continent
    (60.169.240.97 in China versus 52.36.45.107). Before this ran, typing
    ``XRF002`` reached the Chinese box. 44 D-Star names collided this way.

    **The id wins and the alias goes.** An id is the name upstream filed the
    reflector under; an alias is a derived form. Nothing is lost by dropping it:
    the row is still reachable by its own id, and the wire callsign lives in
    ``callsign``, which this does not touch — so a client still sends
    ``XRF002`` in the RPT1/RPT2 header when it dials XLX002 by name.
    """
    ids = {r.id.upper() for r in records}
    dropped = 0
    for record in records:
        keep = [a for a in record.aliases if a.upper() not in ids or a.upper() == record.id.upper()]
        dropped += len(record.aliases) - len(keep)
        record.aliases = keep
    return dropped


def network_document(
    network: str,
    records: Sequence[ReflectorRecord],
    *,
    source_name: str,
    source_url: str,
    attribution: str,
    generated: date | None = None,
) -> dict[str, object]:
    """Build the published document for one network.

    Records are sorted by id so a rebuild that changed nothing produces a
    byte-identical file — which is what lets the publish step skip the commit and keeps
    the site's history free of daily no-op churn.
    """
    ordered = sorted(records, key=lambda r: r.id)
    # Last thing before publication, and deliberately here rather than in any one
    # importer: a name that means two reflectors can only be spotted once every
    # source for the network has been merged, and no future source can forget to
    # call it from here.
    drop_shadowing_aliases(ordered)
    # Prefer the UPSTREAM data date over "when this ran". A build-time stamp changes
    # every night whether or not anything did, which defeats the whole point of a
    # commit-if-changed publish step; the data's own date only moves when the data does.
    upstream = sorted({r.synced_at for r in ordered if r.synced_at})
    stamp = upstream[-1] if upstream else (generated or datetime.now(UTC).date()).isoformat()
    return {
        "schema_version": SCHEMA_VERSION,
        "api_version": API_VERSION,
        "network": network,
        "generated": stamp,
        "client_refresh_days": CLIENT_REFRESH_DAYS,
        **_license_block(),
        "attribution": attribution,
        "source": {"name": source_name, "url": source_url},
        "count": len(ordered),
        "reflectors": [entry_json(r) for r in ordered],
    }


def combined_document(
    documents: dict[str, dict[str, object]],
    *,
    generated: date | None = None,
) -> dict[str, object]:
    """Build ``reflectors.json`` — every network's entries in one file.

    Assembled from the per-network DOCUMENTS rather than from freshly-parsed records, so
    on a day the shrink guard keeps a previous file the combined file describes what is
    actually live. Entries are ordered by ``(network, id)``: a stable order is what makes
    an unchanged rebuild byte-identical.
    """
    entries: list[dict[str, object]] = []
    attributions: list[str] = []
    sources: list[dict[str, object]] = []
    counts: dict[str, int] = {}
    stamps: list[str] = []

    for name in sorted(documents):
        doc = documents[name]
        rows = doc.get("reflectors")
        if isinstance(rows, list):
            entries.extend(row for row in rows if isinstance(row, dict))
        count = doc.get("count")
        counts[name] = count if isinstance(count, int) else 0
        stamp = doc.get("generated")
        if isinstance(stamp, str):
            stamps.append(stamp)
        # Split each network's credit back into its individual statements before
        # deduplicating. Comparing whole strings is not enough: a two-source network
        # publishes a compound credit that CONTAINS a single-source network's credit
        # without being equal to it, which is how DVRef ended up credited twice in
        # the combined file.
        credit = doc.get("attribution")
        if isinstance(credit, str):
            for statement in credit.split(ATTRIBUTION_SEPARATOR):
                statement = statement.strip()
                if statement and statement not in attributions:
                    attributions.append(statement)
        source = doc.get("source")
        if isinstance(source, dict) and source not in sources:
            sources.append(source)

    entries.sort(key=lambda e: (str(e.get("network", "")), str(e.get("id", ""))))
    newest = max(stamps) if stamps else (generated or datetime.now(UTC).date()).isoformat()
    return {
        "schema_version": SCHEMA_VERSION,
        "api_version": API_VERSION,
        "generated": newest,
        "client_refresh_days": CLIENT_REFRESH_DAYS,
        **_license_block(),
        "attribution": ATTRIBUTION_SEPARATOR.join(attributions),
        "sources": sources,
        "networks": counts,
        "count": len(entries),
        "reflectors": entries,
    }


def manifest_document(
    documents: dict[str, dict[str, object]],
    *,
    generated: date | None = None,
) -> dict[str, object]:
    """Build ``index.json`` from the per-network documents.

    This is the only file a client must fetch on a routine check: it carries each
    network's row count and ``generated`` date, so a client can tell whether its cached
    copy is stale without downloading any of the big files.
    """
    networks: dict[str, dict[str, object]] = {}
    for name in sorted(documents):
        doc = documents[name]
        networks[name] = {
            "url": f"{NETWORK_DIR}/{name}.json",
            "count": doc["count"],
            "generated": doc["generated"],
            "source": doc["source"],
        }
    # Derived from the network files, not from the clock, for the same reason: the
    # manifest must not be the one file that churns nightly and forces a commit.
    dates = sorted(str(n["generated"]) for n in networks.values())
    stamp = dates[-1] if dates else (generated or datetime.now(UTC).date()).isoformat()
    total = sum(int(n["count"]) for n in networks.values() if isinstance(n["count"], int))
    return {
        "schema_version": SCHEMA_VERSION,
        "api_version": API_VERSION,
        "generated": stamp,
        "client_refresh_days": CLIENT_REFRESH_DAYS,
        **_license_block(),
        "endpoints": {
            "all": ALL_REFLECTORS_FILE,
            "network": f"{NETWORK_DIR}/{{network}}.json",
            "openapi": OPENAPI_FILE,
        },
        "count": total,
        "networks": networks,
        "expires_hint": (
            date.fromisoformat(stamp) + timedelta(days=CLIENT_REFRESH_DAYS)
        ).isoformat(),
    }


# --- OpenAPI ----------------------------------------------------------------------
# Generated, never hand-written, and generated from the SAME tables the emitter uses.
# A hand-maintained contract drifts from the code the first time a field is added under
# deadline; this one cannot, and the coverage test in tests/test_reflectors.py fails if
# a `dial.kind` ever reaches the data without reaching the schema.

_ENVELOPE_PROPERTIES: dict[str, dict[str, object]] = {
    "network": {
        "type": "string",
        "description": "Which network this reflector belongs to. With `id`, the primary key.",
        "examples": ["dstar", "m17", "ysf"],
    },
    "id": {
        "type": "string",
        "description": "Stable within `network`. What a client stores as a favourite.",
        "examples": ["XLX836"],
    },
    "name": {
        "type": "string",
        "description": "Display name; falls back to `id` when upstream has none.",
    },
    "aliases": {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "Other names that address THIS reflector — e.g. the XRF-form name an XLX "
            "reflector answers to. Searchable, and never a deduplication key: XRF### and "
            "XLX### are different machines that merely share a numbering scheme."
        ),
        "examples": [["XRF836"]],
    },
    "description": {"type": "string", "description": "Free text from upstream."},
    "country": {
        "type": "string",
        "description": "ISO-ish country code or name, verbatim from upstream.",
    },
    "sponsor": {"type": "string", "description": "Who runs it, as upstream words it."},
    "dashboard": {"type": "string", "description": "Web dashboard URL."},
    "source": {
        "type": "string",
        "description": "Which importer produced the row: provenance, for debugging a bad entry.",
        "examples": ["xlx", "dvref"],
    },
}

_DIAL_FIELD_PROPERTIES: dict[str, dict[str, object]] = {
    "callsign": {
        "type": "string",
        "description": (
            "The name the reflector answers to ON THE WIRE, which is not always its "
            "directory name — XLX836 is dialled as XRF836 in RPT1/RPT2."
        ),
    },
    "modules": {
        "type": "array",
        "items": {"type": "string", "pattern": "^[A-Z]$"},
        "description": "Modules/rooms available on this reflector.",
    },
    "requires": {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "What the OPERATOR must supply and the directory cannot. A client seeing this "
            "should prompt rather than attempt a connect."
        ),
        "examples": [["dmr_id", "password"]],
    },
    "talkgroups_url": {
        "type": "string",
        "description": "Where this master's talkgroup list is published.",
    },
}

_KIND_DESCRIPTIONS: dict[str, str] = {
    "dextra": "D-Star reflectors reached over DExtra (both XLX and XRF).",
    "m17": "M17 reflectors (mrefd).",
    "ysf": "Yaesu System Fusion reflectors.",
    "nxdn": "NXDN reflectors.",
    "p25": "P25 reflectors.",
    "urf": "URF reflectors (urfd). Multi-protocol, so upstream publishes no single port.",
    "mmdvm": "MMDVM/DMR masters, which need per-user credentials the directory cannot carry.",
}


def _dial_schema_name(kind: str) -> str:
    parts = [part for part in re.split(r"[^0-9A-Za-z]+", kind) if part]
    return "Dial" + "".join(part[:1].upper() + part[1:] for part in parts)


def _dial_variant_schema(kind: str) -> dict[str, object]:
    properties: dict[str, object] = {
        "kind": {"type": "string", "const": kind, "description": "The discriminator."},
        "host": {"type": "string", "description": "Hostname or IP, verbatim from upstream."},
        "port": {"type": "integer", "minimum": 1, "maximum": 65535},
    }
    required = ["kind", "host"]
    if kind not in DIAL_PORT_OPTIONAL:
        required.append("port")
    for name in DIAL_FIELDS.get(kind, ()):
        properties[name] = _DIAL_FIELD_PROPERTIES[name]
    return {
        "type": "object",
        "title": _dial_schema_name(kind),
        "description": _KIND_DESCRIPTIONS.get(kind, f"{kind} reflectors."),
        "properties": properties,
        "required": required,
    }


def _document_meta_properties() -> dict[str, object]:
    return {
        "schema_version": {"type": "integer", "const": SCHEMA_VERSION},
        "api_version": {"type": "string", "const": API_VERSION},
        "generated": {
            "type": "string",
            "format": "date",
            "description": (
                "Date of the UPSTREAM data, never of the build — a rebuild that finds "
                "nothing new produces byte-identical files."
            ),
        },
        "client_refresh_days": {
            "type": "integer",
            "description": "How often a client should re-check. Cache for this long.",
        },
        "license": {"type": "string", "const": REFLECTOR_LICENSE},
        "license_url": {"type": "string", "format": "uri"},
        "modifications": {
            "type": "string",
            "description": "What this project changed, as CC BY 4.0 requires be stated.",
        },
        "count": {"type": "integer", "description": "Row count, to detect a truncated fetch."},
    }


def _json_response(schema_ref: str, description: str) -> dict[str, object]:
    return {
        "200": {
            "description": description,
            "content": {"application/json": {"schema": {"$ref": schema_ref}}},
        }
    }


def openapi_document() -> dict[str, object]:
    """The published contract as OpenAPI 3.1, built from the emitter's own tables.

    Constant for a given code revision: it carries no counts and no dates, so it never
    contributes churn to the nightly commit.
    """
    kinds = sorted(set(DIAL_KINDS.values()))
    dial_schemas = {_dial_schema_name(kind): _dial_variant_schema(kind) for kind in kinds}
    meta = _document_meta_properties()

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "hamcall-db reflector directory",
            "version": f"{SCHEMA_VERSION}.0.0",
            "summary": "Digital-voice reflector directory as static JSON. No token, no account.",
            "description": (
                "A directory of digital-voice reflectors — D-Star, M17, YSF, NXDN, P25 and "
                "URF — rebuilt nightly from upstream and served as static files, so a "
                "client picking a reflector needs no API key and no rate-limit budget."
                "\n\n"
                "Every entry is a common envelope plus a discriminated `dial` object. An "
                "absent `dial` means the entry is listed but not dialable from this data; "
                "no default address is ever substituted. Unknown `network` or `dial.kind` "
                "values must be ignored gracefully — that is the extension mechanism, and "
                "a client must never attempt to connect to a kind it does not understand."
                "\n\n"
                f"Attribution is required: {ATTRIBUTION_SUMMARY}"
                f"\n\nModifications: {MODIFICATION_NOTE}"
            ),
            "license": {"name": REFLECTOR_LICENSE, "identifier": REFLECTOR_LICENSE_SPDX},
            "contact": {"url": "https://github.com/rcludwick/hamcall-db"},
        },
        "servers": [{"url": API_BASE_URL, "description": "GitHub Pages"}],
        "externalDocs": {
            "url": "https://github.com/rcludwick/hamcall-db/blob/main/docs/REFLECTOR-API.md",
            "description": "Design notes: why the shape is what it is.",
        },
        "paths": {
            f"/{MANIFEST_FILE}": {
                "get": {
                    "operationId": "getManifest",
                    "summary": "Service manifest: what exists and how fresh it is.",
                    "description": (
                        "Fetch this first. It carries every network's row count and "
                        "`generated` date, so a client can tell whether its cached copy is "
                        "stale without downloading any of the big files."
                    ),
                    "responses": _json_response("#/components/schemas/Manifest", "The manifest."),
                }
            },
            f"/{ALL_REFLECTORS_FILE}": {
                "get": {
                    "operationId": "getAllReflectors",
                    "summary": "Every reflector, every network, in one file.",
                    "responses": _json_response(
                        "#/components/schemas/ReflectorCollection", "Every reflector."
                    ),
                }
            },
            f"/{NETWORK_DIR}/{{network}}.json": {
                "get": {
                    "operationId": "getNetworkReflectors",
                    "summary": "One network's reflectors.",
                    "description": (
                        "Exists for failure isolation: upstreams fail independently, and a "
                        "D-Star-only client should not re-download 1400 YSF rows to learn "
                        "that nothing changed."
                    ),
                    "parameters": [
                        {
                            "name": "network",
                            "in": "path",
                            "required": True,
                            "description": "Network name, as listed in the manifest.",
                            "schema": {"type": "string", "examples": sorted(DIAL_KINDS)},
                        }
                    ],
                    "responses": _json_response(
                        "#/components/schemas/NetworkCollection", "One network's reflectors."
                    ),
                }
            },
            f"/{OPENAPI_FILE}": {
                "get": {
                    "operationId": "getOpenapi",
                    "summary": "This document.",
                    "responses": {
                        "200": {
                            "description": "The contract, machine-readable.",
                            "content": {"application/json": {"schema": {"type": "object"}}},
                        }
                    },
                }
            },
        },
        "components": {
            "schemas": {
                **dial_schemas,
                "Dial": {
                    "description": (
                        "How to connect. `kind` is the discriminator; every variant carries "
                        "`host`, and every variant but `urf` carries `port`."
                    ),
                    "oneOf": [
                        {"$ref": f"#/components/schemas/{_dial_schema_name(kind)}"}
                        for kind in kinds
                    ],
                    "discriminator": {
                        "propertyName": "kind",
                        "mapping": {
                            kind: f"#/components/schemas/{_dial_schema_name(kind)}"
                            for kind in kinds
                        },
                    },
                },
                "Reflector": {
                    "type": "object",
                    "description": "One reflector: the searchable envelope plus its `dial`.",
                    "properties": {
                        **_ENVELOPE_PROPERTIES,
                        "dial": {
                            "$ref": "#/components/schemas/Dial",
                            "description": "Absent means listed but not dialable from this data.",
                        },
                    },
                    "required": ["network", "id", "name", "source"],
                },
                "Manifest": {
                    "type": "object",
                    "properties": {
                        **meta,
                        "endpoints": {
                            "type": "object",
                            "description": "Paths of the other files, relative to this one.",
                            "additionalProperties": {"type": "string"},
                        },
                        "networks": {
                            "type": "object",
                            "description": "One entry per network, keyed by network name.",
                            "additionalProperties": {
                                "type": "object",
                                "properties": {
                                    "url": {"type": "string"},
                                    "count": {"type": "integer"},
                                    "generated": {"type": "string", "format": "date"},
                                    "source": {"$ref": "#/components/schemas/Source"},
                                },
                                "required": ["url", "count", "generated"],
                            },
                        },
                        "expires_hint": {
                            "type": "string",
                            "format": "date",
                            "description": "`generated` plus `client_refresh_days`.",
                        },
                    },
                    "required": ["schema_version", "generated", "networks"],
                },
                "ReflectorCollection": {
                    "type": "object",
                    "properties": {
                        **meta,
                        "attribution": {"type": "string"},
                        "sources": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Source"},
                        },
                        "networks": {
                            "type": "object",
                            "description": "Row count per network.",
                            "additionalProperties": {"type": "integer"},
                        },
                        "reflectors": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Reflector"},
                        },
                    },
                    "required": ["schema_version", "generated", "count", "reflectors"],
                },
                "NetworkCollection": {
                    "type": "object",
                    "properties": {
                        **meta,
                        "network": {"type": "string"},
                        "attribution": {"type": "string"},
                        "source": {"$ref": "#/components/schemas/Source"},
                        "reflectors": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Reflector"},
                        },
                    },
                    "required": ["schema_version", "network", "generated", "count", "reflectors"],
                },
                "Source": {
                    "type": "object",
                    "description": "The upstream a file's rows came from.",
                    "properties": {
                        "name": {"type": "string"},
                        "url": {"type": "string", "format": "uri"},
                    },
                    "required": ["name", "url"],
                },
            }
        },
    }


def write_api(
    out_dir: Path,
    documents: dict[str, dict[str, object]],
    *,
    generated: date | None = None,
) -> list[Path]:
    """Write the whole v1 API under ``out_dir``.

    Returns the paths written: the manifest, the combined file, the OpenAPI document,
    then one file per network. Files are written with a trailing newline and sorted keys
    so the output is stable across runs and diffs cleanly.
    """
    api = out_dir
    api.mkdir(parents=True, exist_ok=True)
    (api / NETWORK_DIR).mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for name, document in (
        (MANIFEST_FILE, manifest_document(documents, generated=generated)),
        (ALL_REFLECTORS_FILE, combined_document(documents, generated=generated)),
        (OPENAPI_FILE, openapi_document()),
    ):
        path = api / name
        _write_json(path, document)
        written.append(path)

    for name in sorted(documents):
        path = api / NETWORK_DIR / f"{name}.json"
        _write_json(path, documents[name])
        written.append(path)
    return written


def _write_json(path: Path, document: dict[str, object]) -> None:
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def merge_by_id(*groups: Iterable[ReflectorRecord]) -> list[ReflectorRecord]:
    """Combine record groups for one network, first writer of an id winning.

    D-Star arrives from two upstreams with overlapping coverage (DVRef lists ~61 XRF
    reflectors, xlxapi lists ~890), so pass the source you trust more first. Ties are
    resolved by order, not by merging fields, because a half-merged row from two
    directories that disagree is harder to debug than a wholly-wrong one.

    Keyed on ``id`` and NOTHING else — deliberately not on ``aliases``. XLX836 carries
    the alias XRF836, but a standalone XRF836 is a different machine; collapsing the two
    would send an operator to a reflector on another continent.
    """
    seen: dict[str, ReflectorRecord] = {}
    for group in groups:
        for record in group:
            seen.setdefault(record.id, record)
    return sorted(seen.values(), key=lambda r: r.id)


def records_from_document(document: dict[str, object]) -> list[ReflectorRecord]:
    """Rebuild ``ReflectorRecord``s from a published document.

    The Parquet/SQLite artifacts are derived from the documents that are actually being
    published, rather than from the freshly-fetched rows, so the three outputs can never
    disagree. That matters on a day the shrink guard keeps a previous file: the artifacts
    must then describe the data that is live, not the suspect fetch that was rejected.

    Accepts a per-network document or the combined one — every entry carries its own
    ``network``, so the only thing taken from the envelope is the date.
    """
    network = str(document.get("network") or "")
    stamp = document.get("generated")
    rows = document.get("reflectors")
    if not isinstance(rows, list):
        return []

    out: list[ReflectorRecord] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        raw_dial = row.get("dial")
        dial: dict[str, object] = raw_dial if isinstance(raw_dial, dict) else {}
        port = dial.get("port")
        out.append(
            ReflectorRecord(
                id=str(row["id"]),
                network=_opt_str(row.get("network")) or network,
                name=_opt_str(row.get("name")),
                aliases=_str_list(row.get("aliases")),
                callsign=_opt_str(dial.get("callsign")),
                host=_opt_str(dial.get("host")),
                port=port if isinstance(port, int) and not isinstance(port, bool) else None,
                modules=_str_list(dial.get("modules")),
                country=_opt_str(row.get("country")),
                sponsor=_opt_str(row.get("sponsor")),
                description=_opt_str(row.get("description")),
                dashboard=_opt_str(row.get("dashboard")),
                source=_opt_str(row.get("source")),
                synced_at=str(stamp) if stamp else None,
            )
        )
    return out


def _opt_str(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def _str_list(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []
