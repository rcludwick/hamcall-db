# hamcall-db

A weekly-rebuilt, publicly-licensed amateur radio callsign database, published as a single Parquet file.

## What it is

A Python build pipeline that aggregates free, openly-licensed amateur radio licensee data from multiple national regulators and DXCC reference files into one normalized Parquet artifact. The artifact is published as a GitHub Release on a weekly cadence so other ham-radio tools can pull a pre-built dataset instead of running their own importers.

## Sources

| Source | Coverage | License | Format upstream |
|---|---|---|---|
| FCC ULS | US (~1M records) | Public domain | Bulk ZIP, weekly |
| ISED Canada | Canada (~80k records) | Open Government Licence – Canada | Delimited TXT in ZIP |
| ACMA RRL | Australia (~80k amateur records, filtered from full radiocomms set) | CC-BY 4.0 AU | Daily extract |
| AD1C cty.dat | Worldwide DXCC prefix → entity mapping | Free for non-commercial use, attribution required | cty.dat |

The on-demand long tail (UK, EU, JA, etc.) is intentionally out of scope here — consumer tools should fall through to per-callsign APIs like QRZ or HamQTH for those.

## Output schema

```
callsign       TEXT  PRIMARY KEY
first_name     TEXT
last_name      TEXT
city           TEXT
county         TEXT     -- US-style county; NULL where not applicable
state          TEXT     -- state / province / region (source-specific)
postal_code    TEXT
country        TEXT     -- DXCC entity name (resolved via cty.dat at build time)
dxcc           INTEGER  -- DXCC entity number
grid           TEXT     -- 4-char Maidenhead (field+square, e.g. "DN13"); see notes
license_class  TEXT     -- source-specific; nullable
source         TEXT     -- 'fcc' | 'ised' | 'acma'
synced_at      TEXT     -- ISO date of the upstream source file
```

**Notes**
- Street address (line 1/2) is intentionally NOT included in the published artifact. City/county/state/postal_code is sufficient for the planned consumer use cases (QSO enrichment, grid inference, QSL routing hints).
- `grid` is the 4-character Maidenhead **field+square** only (e.g. `DN13`), never the 6-character subsquare. The build pipeline MAY use street address (when an upstream source provides it, e.g. FCC ULS) to geocode a precise location, but the result is always truncated to 4 chars before publication. A 4-char grid is ~140 km × 70 km — coarse enough that it doesn't identify an individual, but precise enough for DXCC/zone inference and POTA/SOTA neighborhood hints.
- Derivation priority at build time: street address geocode → postal_code centroid → city centroid → NULL. Always truncated to 4 chars regardless of source.
- `first_name` / `last_name` are best-effort splits of the licensee/trustee field. Club/trustee/business holders may put the entity name in `last_name` and leave `first_name` NULL.

Published as `hamcall-db-YYYY-MM-DD.parquet` on the Releases page, with a `latest` tag that always points at the most recent build.

## Consumers

This artifact has no opinion about your storage. Download the Parquet file and load it into whatever your tool prefers — SQLite + FTS5 for prefix autocomplete, DuckDB for analytics, pandas/Polars for one-off scripts.

## License

Build code: MIT. Published dataset: distributed under the combined terms of each upstream source (see `NOTICE` once it lands).

## Status

Pre-alpha. Task tracking via [au](https://github.com/anthropics/au); run `au ready` to see what's next.
