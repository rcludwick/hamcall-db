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
| POTA | Parks On The Air park directory (places, not licensees) | Used on an assumed non-commercial basis (see NOTICE) | Per-program JSON from `api.pota.app` |
| SOTA | Summits on the Air summit directory (places, not licensees) | Used on an assumed non-commercial basis, **pending human sign-off** (see NOTICE) | Static `summitslist.csv` from `storage.sota.org.uk` |
| PAD-US | US protected-area boundaries → POTA park grid sets (build-time only) | Public domain (USGS) | National GeoPackage/GDB |
| OpenStreetMap | Non-US protected-area/park boundaries → **separate ODbL** POTA park grid sets (build-time only) | ODbL — shipped in its own file, never mixed into the CC BY-NC dataset (see NOTICE / LICENSE-ODbL) | Geofabrik regional extracts |

The on-demand long tail (UK, EU, JA, etc.) is intentionally out of scope here — consumer tools should fall through to per-callsign APIs like QRZ or HamQTH for those.

### POTA parks reference dataset

POTA park data is published as a SEPARATE, additive reference dataset (parks are places, not licensees, so it does not touch the callsign schema above): `hamcall-db-pota-parks-YYYY-MM-DD.parquet` plus a `pota_parks` table in the SQLite `.db`. Columns: `reference` (PK, e.g. `US-0001`), `name`, `location_desc`, `region`, `country`, `dxcc`, `grid`, `lat`, `lon`, `active`, `source`, `synced_at`. The single representative `grid`/`lat`/`lon` per park is **indicative only** (large parks span many grid squares), is display-only and never a join key, and is stored **verbatim** from POTA — the 4-char person-grid privacy truncation does not apply to public landmarks.

### SOTA summits reference dataset

SOTA summit data is published as a SEPARATE, additive reference dataset — a sibling of the POTA parks set (summits are places, not licensees, so it does not touch the callsign schema): `hamcall-db-sota-summits-YYYY-MM-DD.parquet` plus a `sota_summits` table in the SQLite `.db`. Columns: `reference` (PK, SOTA SummitCode e.g. `G/LD-001`), `name`, `association`, `region`, `alt_m`, `alt_ft`, `grid`, `lat`, `lon`, `points`, `bonus_points`, `valid_from`, `valid_to`, `active`, `source`, `synced_at`. The `grid`/`lat`/`lon` per summit is **indicative only**, is display-only and never a join key, and is stored **verbatim** at 6-char precision — the 4-char person-grid privacy truncation does not apply to public landmarks (mountain summits). The `grid` is derived from the summit's `Longitude`/`Latitude` (SOTA's own OSGB grid columns are not used). `active` is `False` when `valid_to` is in the past (retired summit).

Data comes from SOTA's **static** bulk [`summitslist.csv`](https://storage.sota.org.uk/summitslist.csv) — deliberately *not* the gated `api2.sota.org.uk` JSON API, whose terms of service are explicitly non-commercial, registration-gated, and prohibit AI-generated software from connecting. The static file carries no explicit machine-readable license and no share-alike term was found, so SOTA rides under the CC BY-NC umbrella (assumed non-commercial + attributed + droppable, the same posture as POTA) — **but this is not a confirmed license and is flagged in NOTICE for human sign-off with the SOTA Management Team before the first public release.** WWFF and IOTA references are deliberately out of scope for this dataset (future follow-ups).

### POTA park grid sets (PAD-US)

Because a large park spans many grid squares, a companion child table gives the **set** of 4-char Maidenhead grids each park's boundary actually intersects: `hamcall-db-pota-park-grids-YYYY-MM-DD.parquet` plus a `pota_park_grids` table in the SQLite `.db`. Columns: `reference` (FK to `pota_parks`), `grid` (4-char, e.g. `DN13`), `source`, `confidence`. For **US** parks the boundaries come from [PAD-US](https://www.usgs.gov/programs/gap-analysis-project/science/pad-us-data-download) (USGS, public domain): each park is matched to its PAD-US boundary by point-in-polygon plus fuzzy name disambiguation, split units are unioned, and the (possibly multi-part, possibly holed) polygon is intersected with the Maidenhead lattice using true geometric intersection — interior rings/inholdings are honored, never filled. `source` is `padus` for a polygon match (`confidence` `high` when the name also matched, `medium` when only the point matched) or `pota-point` for the single-point fallback (`confidence` `low`), which also covers every non-US park in this phase. The PAD-US polygons themselves are used only at build time and are **never** redistributed.

### POTA park grid sets — international (OpenStreetMap, ODbL — SEPARATE file)

Non-US POTA parks get the same 4-char grid-set treatment from OpenStreetMap boundaries, but because OSM is **ODbL share-alike** (incompatible with this project's CC BY-NC terms) the OSM-derived grids are a **separate, ODbL-licensed dataset** that is **never** mixed into the CC BY-NC artifacts: `hamcall-db-pota-park-grids-osm-YYYY-MM-DD.parquet` plus a **separate** SQLite `hamcall-db-pota-park-grids-osm-YYYY-MM-DD.db` whose table is named `pota_park_grids_osm`. Schema mirrors the PAD-US set exactly: `reference` (FK to `pota_parks`), `grid` (4-char), `source` (`osm` for a boundary match, `osm-point` for the single-point fallback), `confidence` (`high`/`medium`/`low`, same rules as PAD-US). Boundaries come from [OpenStreetMap](https://www.openstreetmap.org/copyright) (© OpenStreetMap contributors, ODbL) via Geofabrik regional extracts; matching uses true geometric intersection with interior rings/inholdings honored (never filled), exactly like the PAD-US path — only the matching heuristic (OSM tags + `name`) differs. US parks are covered by the public-domain PAD-US set above and are **excluded** here, so the two sets never overlap. The OSM polygons themselves are used only at build time and are never redistributed.

This file is governed by [`LICENSE-ODbL`](LICENSE-ODbL), not the CC BY-NC terms. **Consumer warning:** combining (joining) the ODbL grid set with the CC BY-NC artifacts and redistributing the result as a database makes the combined database **inherit ODbL share-alike**. "Combine them later in one database" is a consumer concern; keep them separate to keep the CC BY-NC dataset free of share-alike.

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

Each weekly build publishes two releases, each with a rolling alias that always points at the most recent build.

**CC BY-NC release** — tag `hamcall-db-YYYY-MM-DD`, alias `latest`:
- `hamcall-db-YYYY-MM-DD.parquet` — current-state dataset (the schema above; the redistribution contract).
- `hamcall-db-history-YYYY-MM-DD.parquet` — SCD2 change history (callsign holder/location changes over time).
- `hamcall-db-YYYY-MM-DD.db` — SQLite convenience copy with the callsign tables plus the `pota_parks` / `pota_park_grids` / `sota_summits` tables in one file (see [Exploring the data with Datasette](#exploring-the-data-with-datasette)).
- `hamcall-db-pota-parks-YYYY-MM-DD.parquet` — POTA park directory ([above](#pota-parks-reference-dataset)).
- `hamcall-db-sota-summits-YYYY-MM-DD.parquet` — SOTA summit directory ([above](#sota-summits-reference-dataset)).
- `hamcall-db-pota-park-grids-YYYY-MM-DD.parquet` — POTA park → 4-char grid sets (PAD-US/point, [above](#pota-park-grid-sets-pad-us)).

**ODbL release** (OpenStreetMap-derived grids — kept separate so its share-alike terms never touch the CC BY-NC set) — tag `hamcall-db-osm-YYYY-MM-DD`, alias `latest-osm`:
- `hamcall-db-pota-park-grids-osm-YYYY-MM-DD.parquet` + `…-osm-YYYY-MM-DD.db` — OSM-derived park grid sets ([above](#pota-park-grid-sets--international-openstreetmap-odbl--separate-file)).
- `LICENSE-ODbL` — the governing license + © OpenStreetMap contributors attribution.

> The POTA park-grid sets currently published are **indicative point grids**: the public weekly build runs without the build-time `padus`/`osm` GIS groups, so real polygon→grid coverage is not yet in the released artifacts. Treat the grids as approximate until polygon coverage ships.

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
