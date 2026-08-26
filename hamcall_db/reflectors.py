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

Output shape
------------
The published surface is static JSON served by GitHub Pages — a file per network plus a
manifest, so one upstream going dark cannot take the others with it:

    site/api/index.json                 manifest: networks, counts, licence, freshness
    site/api/reflectors/<network>.json  one network's reflectors

Clients read ``index.json`` first and re-fetch a network file only when its ``generated``
date moved. ``client_refresh_days`` tells them how often to bother at all.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, fields
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

# The licence the published reflector JSON carries, and the attribution DVRef asks for
# verbatim in their announcement. Both are written into every emitted file — an
# attribution that lives only in a README is one copy-paste away from being lost.
REFLECTOR_LICENSE = "CC BY 4.0"
REFLECTOR_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"

# CC BY requires indicating that changes were made. We reshape upstream rows into a
# common JSON schema, which is a modification, so say so in the file itself.
MODIFICATION_NOTE = (
    "Reformatted from the upstream source into hamcall-db's common reflector JSON "
    "schema. Field names and structure differ from upstream; values are unmodified."
)

# How long a client should sit on a cached copy before asking again. A week, because
# these files are served from GitHub Pages and every astar install polling nightly is
# request volume that buys nothing — reflector hosts move on a scale of weeks.
CLIENT_REFRESH_DAYS = 7

# The JSON schema version of the published files. Bump only when an existing field
# changes meaning or goes away; adding a field is not a bump (an older reader ignores
# what it does not recognise).
SCHEMA_VERSION = 1


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
    description: str | None = None
    dashboard: str | None = None  # web dashboard URL, when upstream publishes one
    source: str | None = None  # 'dvref' | 'xlx' — which importer produced this row
    synced_at: str | None = None  # ISO date of the upstream pull


REFLECTOR_SCHEMA_COLUMNS: tuple[str, ...] = tuple(f.name for f in fields(ReflectorRecord))
"""Ordered field names of the published reflector rows."""


def _record_to_json(record: ReflectorRecord) -> dict[str, object]:
    """One record as a JSON object, dropping empty values.

    Omitting nulls rather than emitting them keeps the files meaningfully smaller
    (the YSF set is ~1400 rows) and reads the same to any client that treats a missing
    key as unset — which is the tolerance the schema-version rule already assumes.
    """
    out: dict[str, object] = {"id": record.id}
    for name in REFLECTOR_SCHEMA_COLUMNS:
        # `network` and `synced_at` are identical for every row in a file, so they live
        # once at document level instead. That is not just tidiness: repeating a build
        # date on 1432 rows means a quiet night still rewrites the whole file, and a
        # nightly job would commit ~1 MB of churn a day to say nothing changed.
        if name in ("id", "network", "synced_at"):
            continue
        value = getattr(record, name)
        if value is None or value == [] or value == "":
            continue
        out[name] = value
    return out


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
    # Prefer the UPSTREAM data date over "when this ran". A build-time stamp changes
    # every night whether or not anything did, which defeats the whole point of a
    # commit-if-changed publish step; the data's own date only moves when the data does.
    upstream = sorted({r.synced_at for r in ordered if r.synced_at})
    stamp = upstream[-1] if upstream else (generated or datetime.now(UTC).date()).isoformat()
    return {
        "schema_version": SCHEMA_VERSION,
        "network": network,
        "generated": stamp,
        "client_refresh_days": CLIENT_REFRESH_DAYS,
        "license": REFLECTOR_LICENSE,
        "license_url": REFLECTOR_LICENSE_URL,
        "attribution": attribution,
        "modifications": MODIFICATION_NOTE,
        "source": {"name": source_name, "url": source_url},
        "count": len(ordered),
        "reflectors": [_record_to_json(r) for r in ordered],
    }


def manifest_document(
    documents: dict[str, dict[str, object]],
    *,
    generated: date | None = None,
) -> dict[str, object]:
    """Build ``api/index.json`` from the per-network documents.

    This is the only file a client must fetch on a routine check: it carries each
    network's row count and ``generated`` date, so a client can tell whether its cached
    copy is stale without downloading any of the big files.
    """
    networks = {}
    for name in sorted(documents):
        doc = documents[name]
        networks[name] = {
            "url": f"reflectors/{name}.json",
            "count": doc["count"],
            "generated": doc["generated"],
            "source": doc["source"],
        }
    # Derived from the network files, not from the clock, for the same reason: the
    # manifest must not be the one file that churns nightly and forces a commit.
    dates = sorted(str(n["generated"]) for n in networks.values())
    stamp = dates[-1] if dates else (generated or datetime.now(UTC).date()).isoformat()
    return {
        "schema_version": SCHEMA_VERSION,
        "generated": stamp,
        "client_refresh_days": CLIENT_REFRESH_DAYS,
        "license": REFLECTOR_LICENSE,
        "license_url": REFLECTOR_LICENSE_URL,
        "modifications": MODIFICATION_NOTE,
        "networks": networks,
        "expires_hint": (
            date.fromisoformat(stamp) + timedelta(days=CLIENT_REFRESH_DAYS)
        ).isoformat(),
    }


def write_api(
    out_dir: Path,
    documents: dict[str, dict[str, object]],
    *,
    generated: date | None = None,
) -> list[Path]:
    """Write the manifest and every network file under ``out_dir``.

    Returns the paths written, manifest first. Files are written with a trailing
    newline and sorted keys so the output is stable across runs and diffs cleanly.
    """
    api = out_dir
    api.mkdir(parents=True, exist_ok=True)
    (api / "reflectors").mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    manifest_path = api / "index.json"
    _write_json(manifest_path, manifest_document(documents, generated=generated))
    written.append(manifest_path)

    for name in sorted(documents):
        path = api / "reflectors" / f"{name}.json"
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
    """
    seen: dict[str, ReflectorRecord] = {}
    for group in groups:
        for record in group:
            seen.setdefault(record.id, record)
    return sorted(seen.values(), key=lambda r: r.id)


def records_from_document(document: dict[str, object]) -> list[ReflectorRecord]:
    """Rebuild ``ReflectorRecord``s from a published network document.

    The Parquet/SQLite artifacts are derived from the documents that are actually being
    published, rather than from the freshly-fetched rows, so the three outputs can never
    disagree. That matters on a day the shrink guard keeps a previous file: the artifacts
    must then describe the data that is live, not the suspect fetch that was rejected.
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
        modules = row.get("modules")
        out.append(
            ReflectorRecord(
                id=str(row["id"]),
                network=network,
                name=_opt_str(row.get("name")),
                callsign=_opt_str(row.get("callsign")),
                host=_opt_str(row.get("host")),
                port=row.get("port") if isinstance(row.get("port"), int) else None,
                modules=[str(m) for m in modules] if isinstance(modules, list) else [],
                country=_opt_str(row.get("country")),
                description=_opt_str(row.get("description")),
                dashboard=_opt_str(row.get("dashboard")),
                source=_opt_str(row.get("source")),
                synced_at=str(stamp) if stamp else None,
            )
        )
    return out


def _opt_str(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value else None
