# Callsign dataset

*Last reviewed: 2026-11-13.*

These two commands download the callsign dataset and look up the ARRL's
headquarters station, W1AW:

```bash
gh release download latest --repo rcludwick/hamcall-db --pattern 'hamcall-db-2*.parquet'
duckdb -c "SELECT callsign, city, state, grid, source FROM 'hamcall-db-2*.parquet' WHERE callsign = 'W1AW'"
```

```text
┌──────────┬───────────┬─────────┬─────────┬─────────┐
│ callsign │   city    │  state  │  grid   │ source  │
│ varchar  │  varchar  │ varchar │ varchar │ varchar │
├──────────┼───────────┼─────────┼─────────┼─────────┤
│ W1AW     │ Newington │ CT      │ FN31    │ fcc     │
└──────────┴───────────┴─────────┴─────────┴─────────┘
```

The file is one row per callsign: 1,075,947 rows in
`hamcall-db-2026-11-13.parquet`, read from nine national licence registers.
The full column list and the privacy rules are on the
[published files page](about/datasets.md).

## Download the files

The dataset is too large for this site, so it is published as files
attached to a GitHub release. A **release** is a tag in the repository with
downloadable files attached
([releases list](https://github.com/rcludwick/hamcall-db/releases)). Each
nightly build publishes five files, all named with the build date
(`.github/workflows/release.yml`). On 2026-11-13 they were:

| File | Size | What it holds |
|---|--:|---|
| `hamcall-db-2026-11-13.parquet` | 24.8 MB | One row per callsign, as of that night |
| `hamcall-db-history-2026-11-13.parquet` | 15.6 MB | Every past state of each callsign |
| `hamcall-db-2026-11-13.db` | 326 MB | The same data as a SQLite database |
| `hamcall-db-pota-parks-2026-11-13.parquet` | 0.9 MB | Parks on the Air park directory |
| `hamcall-db-pota-park-grids-2026-11-13.parquet` | 0.1 MB | Grid squares each park covers |

Most uses need only the first file. Parquet is a column-oriented file format
that DuckDB, Polars, pandas and most data tools read directly.

The download command at the top of the page uses the GitHub CLI, `gh`. The
pattern `hamcall-db-2*.parquet` matches the current-state file and no other,
because the other Parquet files have `history` or `pota` after
`hamcall-db-`. To fetch every file, drop `--pattern`.

Without `gh`, ask the GitHub API for the asset's address and pass it to
`curl` (this needs `jq`):

```bash
curl -sSLO "$(curl -sS https://api.github.com/repos/rcludwick/hamcall-db/releases/tags/latest \
  | jq -r '.assets[].browser_download_url | select(test("/hamcall-db-[0-9-]+\\.parquet$"))')"
```

The lookup is needed because the file name changes every night, so no
fixed URL points at the newest Parquet file.

## `latest` and dated releases

Every build is published twice (`.github/workflows/release.yml`):

- as a **dated release**, tagged `hamcall-db-YYYY-MM-DD`, which is never
  changed afterwards;
- as the **`latest`** release, whose files are replaced by each build.

Use `latest` to stay current. Use a dated tag when you need the same bytes
again later, for example to reproduce a result. A dated file has a fixed
address:

```bash
curl -sSLO https://github.com/rcludwick/hamcall-db/releases/download/hamcall-db-2026-11-13/hamcall-db-2026-11-13.parquet
```

The `latest` release itself still shows its creation date, 2026-06-18, on
the releases page. Read the build date from the file names instead.

## When a new build appears

The release workflow is scheduled for 00:17 UTC each day (cron
`17 0 * * *` in `.github/workflows/release.yml`), which is 5:17 PM MST.
GitHub starts scheduled runs late. The dated releases from 2026-11-06 to
2026-11-13 were published between 06:02 and 07:22 UTC
(`gh release list`), and 2026-11-05 has no release at all.

So do not schedule against a clock time. Check the date in the file name.
That date is the UTC date when the build started, so a build published at
06:49 UTC on 2026-11-13 is named `2026-11-13`, although in Mountain time it
appeared on the evening of 2026-11-12.

A night can also be partial. If one register's download still fails after
retries, the build skips that source and publishes the rest, then marks the
workflow run as failed (`hamcall_db/build.py`, last step of
`release.yml`). That night's file has no rows from the skipped source. To
check a file, count its rows per source:

```bash
duckdb -c "SELECT source, count(*) AS n FROM 'hamcall-db-2*.parquet' GROUP BY source ORDER BY n DESC"
```

On 2026-11-13 that printed nine sources: `fcc` 822,153, `ised` 92,229,
`bnetza` 69,986, `anatel` 46,660, `uke` 19,079, `enacom` 16,416, `nkom`
7,334, `ursec` 1,337 and `acma` 753.

## Query the file

The examples use W1AW, a club station, so no private person's details
appear here. Each one was run against the 2026-11-13 files.

**DuckDB** reads Parquet in place, as in the first example. Its command-line
program is `duckdb` (installation at <https://duckdb.org/docs/installation/>).

**Polars**, in Python:

```python
import polars as pl

df = pl.read_parquet("hamcall-db-2026-11-13.parquet")
print(df.filter(pl.col("callsign") == "W1AW")
        .select("callsign", "city", "state", "grid", "allstar_nodes"))
```

```text
shape: (1, 5)
┌──────────┬───────────┬───────┬──────┬───────────────┐
│ callsign ┆ city      ┆ state ┆ grid ┆ allstar_nodes │
│ ---      ┆ ---       ┆ ---   ┆ ---  ┆ ---           │
│ str      ┆ str       ┆ str   ┆ str  ┆ list[i64]     │
╞══════════╪═══════════╪═══════╪══════╪═══════════════╡
│ W1AW     ┆ Newington ┆ CT    ┆ FN31 ┆ [63886]       │
└──────────┴───────────┴───────┴──────┴───────────────┘
```

`allstar_nodes` lists the AllStarLink node numbers registered to the
callsign. AllStarLink is a network of linked repeaters and hotspots, and a
node is one station on it.

**sqlite3**, using the `.db` file. The current state is in the `current`
table and past states are in `history`:

```bash
sqlite3 -readonly -header -column hamcall-db-2026-11-13.db \
  "SELECT callsign, city, state, grid, source FROM current WHERE callsign = 'W1AW'" \
  "SELECT callsign, valid_from, valid_to, city, grid FROM history WHERE callsign = 'W1AW'"
```

```text
callsign    city     state  grid  source
--------  ---------  -----  ----  ------
W1AW      Newington  CT     FN31  fcc
callsign  valid_from  valid_to    city     grid
--------  ----------  --------  ---------  ----
W1AW      2026-11-02            Newington  FN31
```

An empty `valid_to` means that state is the current one. The database also
holds the `allstar_nodes`, `pota_parks` and `pota_park_grids` tables. The
SQLite file is a convenience copy; the Parquet files are the contract
(`hamcall_db/sqlite_writer.py`).

## What the file covers

The rows come from nine national registers, listed with their licences on
the [sources page](about/sources.md):

| Country | Regulator | `source` |
|---|---|---|
| United States | FCC | `fcc` |
| Canada | ISED | `ised` |
| Germany | Bundesnetzagentur | `bnetza` |
| Brazil | Anatel | `anatel` |
| Poland | UKE | `uke` |
| Argentina | ENACOM | `enacom` |
| Norway | Nkom | `nkom` |
| Uruguay | URSEC | `ursec` |
| Australia | ACMA | `acma` |

These are the sources in `SOURCES` in `hamcall_db/build.py`, less Hungary's
NMHH, which is restricted and excluded from published builds. A callsign
from any other country, the UK or Japan for example, is not in the file.

The file has 23 columns. In outline:

- the callsign, and the holder's name where the register publishes it;
- city, county, state and postal code, as far as each register publishes
  them;
- `country` and `dxcc`, the DXCC entity worked out from the callsign's
  prefix;
- `grid`, a 4-character Maidenhead grid square, for FCC, ISED and ACMA rows
  only;
- licence class, licence dates and FCC identifiers;
- `source` and `synced_at`, the register and the date of its file;
- `allstar_nodes`;
- two LoTW columns, always false or empty, because LoTW data is excluded
  from published builds.

Which register fills which column, and every column's type, is in the
[column table](about/datasets.md#the-callsign-dataset).

Two privacy rules shape what you will find. No street address is
published, and a grid square stops at 4 characters, which places a station
within a square about 170 by 111 km at 40° N rather than at a house. Both rules,
and the rules for particular registers such as ACMA's, are explained on
the [published files page](about/datasets.md#what-was-removed-for-privacy).

## Park files

The two `pota` files describe Parks on the Air (POTA) parks, which are
places rather than licensees, so they have their own columns
(`hamcall_db/sources/pota.py`). On 2026-11-13 the park directory had 38,603
parks. Park grids derived from OpenStreetMap are published separately in
the `latest-osm` release under the ODbL, a share-alike licence; the newest
of those files are dated 2026-11-02. SOTA summits are not published. Both
are described on the [published files page](about/datasets.md).

## Licence

The callsign dataset, including the park files, is licensed
[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/):
non-commercial use, with attribution. It is non-commercial because ISED,
Canada's regulator, allows reproduction of its data only for non-commercial
purposes (NOTICE 2). POTA's park data is also used non-commercially
(NOTICE 6). Each upstream source also has attribution terms. The
simplest way to meet all of them is to ship the
[NOTICE](https://github.com/rcludwick/hamcall-db/blob/main/NOTICE) file
unchanged alongside the data. The [licensing page](about/licensing.md) has
the details.
