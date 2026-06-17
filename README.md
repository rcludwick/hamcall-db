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
| AllStarLink | Node numbers per callsign (one-to-many) | Used on an assumed non-commercial basis (see NOTICE) | Pipe-delimited node directory |

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
allstar_nodes  LIST<INT> -- AllStarLink node numbers held by the callsign (may be empty)
```

**Notes**
- Street address (line 1/2) is intentionally NOT included in the published artifact. City/county/state/postal_code is sufficient for the planned consumer use cases (QSO enrichment, grid inference, QSL routing hints).
- `grid` is the 4-character Maidenhead **field+square** only (e.g. `DN13`), never the 6-character subsquare. The build pipeline MAY use street address (when an upstream source provides it, e.g. FCC ULS) to geocode a precise location, but the result is always truncated to 4 chars before publication. A 4-char grid is ~140 km × 70 km — coarse enough that it doesn't identify an individual, but precise enough for DXCC/zone inference and POTA/SOTA neighborhood hints.
- Derivation priority at build time: street address geocode → postal_code centroid → city centroid → NULL. Always truncated to 4 chars regardless of source.
- `first_name` / `last_name` are best-effort splits of the licensee/trustee field. Club/trustee/business holders may put the entity name in `last_name` and leave `first_name` NULL.
- `allstar_nodes` is a list of [AllStarLink](https://www.allstarlink.org/) node numbers registered to the callsign (one callsign may hold many nodes; sorted ascending, empty when none). In the SQLite convenience copy it is normalized into a child table `allstar_nodes(id, node)` keyed by the stable `id` (SQLite has no list type), not a column on `current`. It does not participate in history (a node change is not a holder/location change, so it never opens a new SCD2 interval).

Each weekly build publishes three assets on the Releases page, with a `latest` tag that always points at the most recent build:
- `hamcall-db-YYYY-MM-DD.parquet` — current-state dataset (the schema above; the redistribution contract).
- `hamcall-db-history-YYYY-MM-DD.parquet` — SCD2 change history (callsign holder/location changes over time).
- `hamcall-db-YYYY-MM-DD.db` — SQLite convenience copy with both tables in one file (see [Exploring the data with Datasette](#exploring-the-data-with-datasette)).

## Consumers

The Parquet files have no opinion about your storage — download and load them into whatever your tool prefers: DuckDB for analytics, pandas/Polars for one-off scripts, or SQLite + FTS5 if you want prefix autocomplete. Each weekly release also ships a ready-to-use SQLite `.db` (see below) for zero-setup browsing.

## Exploring the data with Datasette

The SQLite artifact (`hamcall-db-YYYY-MM-DD.db`) is the quickest way to browse and query the data interactively. It bundles both tables in one file, and [Datasette](https://datasette.io/) reads SQLite natively — no conversion needed.

```bash
# install Datasette (pick one)
uv tool install datasette        # or: pipx install datasette  /  pip install datasette

# download the latest .db asset from the Releases page, then serve it:
datasette hamcall-db-2026-06-16.db
# open http://localhost:8001 — browse tables, facet by state/country/license_class, run SQL
```

If you built the data locally, there's a shortcut that finds the newest `hamcall-db-*.db` and serves it (Datasette ships in the optional `serve` dependency group):

```bash
uv run --group serve hamcall-db-serve dist/      # serves the latest .db in dist/
# pass through datasette args after the dir, e.g.:  uv run --group serve hamcall-db-serve dist/ -p 9000
```

The `.db` holds two tables:

- **`current`** — one row per callsign (surrogate `id` PRIMARY KEY that is stable and never reused; `callsign` is UNIQUE). The current licensee snapshot.
- **`history`** — SCD2 change intervals (`callsign`, `valid_from`, `valid_to`, NULL = still open), so you can see how a callsign's holder changed over time. Each row carries the `id`, so you can join back to `current`.

Example query (paste into Datasette's SQL view):

```sql
-- every holder a callsign has had, oldest first
select callsign, valid_from, valid_to, first_name, last_name, state, grid
from history
where callsign = 'W1AW'
order by valid_from;
```

Prefer Parquet? Those stay the canonical, storage-neutral output. Datasette itself only reads SQLite directly; for the Parquet files use the [`datasette-parquet`](https://github.com/simonw/datasette-parquet) plugin (DuckDB-backed) or convert first.

## License

**Build code:** MIT — see [`LICENSE`](LICENSE).

**Published dataset** (the Parquet files and the SQLite `.db`): [Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)](https://creativecommons.org/licenses/by-nc/4.0/). When you redistribute the data you must also satisfy the upstream source attribution terms in [`NOTICE`](NOTICE). Commercial use requires separate arrangements with the upstream rights holders (principally AD1C for cty.dat).

## Status

Pre-alpha. Task tracking via [au](https://github.com/anthropics/au); run `au ready` to see what's next.
