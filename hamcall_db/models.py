"""The output schema contract.

`Record` is the single source of truth for the published Parquet columns. Changing
field names or removing fields is a BREAKING change for downstream consumers — see
CLAUDE.md "redistribution contract" and project memory mem-c6e0. Adding an optional
field is a minor bump.

Importers normalize their source-specific rows into `Record` instances. Records are
dataclasses (not raw dicts) so schema drift is caught at type-check time.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields


@dataclass(slots=True)
class Record:
    """One published amateur radio license row.

    Street address is intentionally absent — it may be consumed at build time for
    geocoding, then discarded. `grid` is 4-char Maidenhead only (e.g. "DN13"); never
    publish the 6-char subsquare. See mem-e3fd.
    """

    callsign: str  # primary key
    first_name: str | None = None
    last_name: str | None = None
    city: str | None = None
    county: str | None = None  # US-style county; NULL where not applicable
    state: str | None = None  # state / province / region (source-specific)
    postal_code: str | None = None
    country: str | None = None  # DXCC entity name (resolved via cty.dat at build time)
    dxcc: int | None = None  # DXCC entity number
    grid: str | None = None  # 4-char Maidenhead field+square, e.g. "DN13"
    license_class: str | None = None  # source-specific; nullable
    source: str | None = None  # 'fcc' | 'ised' | 'acma'
    synced_at: str | None = None  # ISO date of the upstream source file
    # AllStarLink node numbers held by this callsign (one callsign -> MANY nodes),
    # sorted ascending. Empty list when the callsign has no registered nodes. Added as
    # a Parquet list column (additive minor bump, mem-c6e0); enriched from
    # allmondb.allstarlink.org via hamcall_db.enrich_allstar. NOT holder identity, so it
    # is excluded from history tracking (mem / history._TRACKED_FIELDS).
    allstar_nodes: list[int] = field(default_factory=list)
    # FCC ULS additive identity/date fields (hdb-f865). Already on disk in the bulk
    # extract; surfaced here as a MINOR (non-breaking) schema bump. Dates are ISO
    # YYYY-MM-DD strings (normalized from the source mm/dd/yyyy). All source-specific
    # and nullable — only the FCC importer populates them; other sources leave them None.
    # NOTE: expired_date is exposed here for downstream use but the active/expired STATUS
    # logic is a separate concern (hdb-6e95); this nugget only surfaces the raw value.
    grant_date: str | None = None  # license grant date (FCC HD.dat)
    effective_date: str | None = None  # license effective date (FCC HD.dat)
    expired_date: str | None = None  # license expiration date (FCC HD.dat)
    frn: str | None = None  # FCC Registration Number (FCC EN.dat)
    entity_type: str | None = None  # raw FCC entity-type code, e.g. "L" (FCC EN.dat)
    applicant_type: str | None = None  # readable applicant type, e.g. "Individual"
    previous_callsign: str | None = None  # prior call sign held (FCC AM.dat)


SCHEMA_COLUMNS: tuple[str, ...] = tuple(f.name for f in fields(Record))
"""Ordered column names of the published artifact. The writer relies on this order."""
