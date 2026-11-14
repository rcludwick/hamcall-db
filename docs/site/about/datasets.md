# Published files

*Last reviewed: 2026-11-13. Counts and sizes on this page were read from the
published files on that date.*

Run this and you have the whole callsign dataset on your disk:

```bash
gh release download latest --repo rcludwick/hamcall-db
```

On 2026-11-13 that fetched five files, all from the build of that night
(`gh release view latest`):

| File | Size | What it holds |
|---|--:|---|
| `hamcall-db-2026-11-13.parquet` | 24.8 MB | One row per callsign, as of that night |
| `hamcall-db-history-2026-11-13.parquet` | 15.6 MB | Every past state of each callsign |
| `hamcall-db-2026-11-13.db` | 326 MB | The same data as a SQLite database |
| `hamcall-db-pota-parks-2026-11-13.parquet` | 0.9 MB | Parks on the Air park directory |
| `hamcall-db-pota-park-grids-2026-11-13.parquet` | 0.1 MB | Grid squares each park covers |

Sources are on the [sources page](sources.md) and licences on the
[licensing page](licensing.md).

## Where each file lives

The project publishes in two places. A **GitHub release** is a set of
downloadable files attached to a tag in the
[repository](https://github.com/rcludwick/hamcall-db/releases). The **static
API** is a set of JSON files served from this site under
[`api/v1/`](https://rcludwick.github.io/hamcall-db/api/v1/index.json), which
any program can fetch with no account or token.

| Files | Where | Refreshed |
|---|---|---|
| Callsign dataset: current, history, SQLite, POTA parks, park grids | [`latest`](https://github.com/rcludwick/hamcall-db/releases/tag/latest) release, and a dated `hamcall-db-YYYY-MM-DD` release per build | Nightly (cron `17 0 * * *` in `.github/workflows/release.yml`) |
| Park grids derived from OpenStreetMap | [`latest-osm`](https://github.com/rcludwick/hamcall-db/releases/tag/latest-osm) release, and dated `hamcall-db-osm-YYYY-MM-DD` releases | Only when the build produces OpenStreetMap rows; newest files dated 2026-11-02 |
| Reflector directory as JSON | `api/v1/reflectors.json` and one file per network | Nightly, from a VPS (`deploy/vps/`) |
| DMR talkgroup lists | `api/v1/reflectors/dmr/<system>/talkgroups.json` | Rotating, up to 40 lists a night |
| AllStar node directory | [`api/v1/allstar.json`](https://rcludwick.github.io/hamcall-db/api/v1/allstar.json) | Nightly, from the same VPS |
| Reflector directory as Parquet and SQLite | [`latest-reflectors`](https://github.com/rcludwick/hamcall-db/releases/tag/latest-reflectors) release | Last published 2026-08-26; not refreshed nightly |

`YYYY-MM-DD` in a file name is the date of the build that made it. The
`latest` release keeps the same tag and has its files replaced each night, so
read the build date from the file names, not from the release date. If you
need the same bytes again next month, download from the dated tag instead.

## The callsign dataset

### Current state: `hamcall-db-YYYY-MM-DD.parquet`

Parquet is a column-oriented file format that pandas, Polars, DuckDB and
most data tools read directly. This file has one row per callsign: 1,075,947
rows on 2026-11-13, each callsign appearing once, sorted by callsign. The
rows come from nine national registers, named in the `source` column.

Try it with DuckDB:

```sql
SELECT callsign, city, state, grid, source
FROM 'hamcall-db-2026-11-13.parquet'
WHERE callsign = 'W1AW';
```

The file has 23 columns, in this order (`Record` in `hamcall_db/models.py`).
The column list is the project's contract with the programs that read it:
new columns may be added, but existing ones are never removed or renamed
without a breaking-change notice (`hamcall_db/models.py`).

| # | Column | Type | Meaning | Filled by |
|--:|---|---|---|---|
| 1 | `callsign` | string | The callsign, upper case. One row each | All sources |
| 2 | `first_name` | string | Holder's first name | Sources that publish names; null for ACMA persons and UKE individuals |
| 3 | `last_name` | string | Holder's last name, or the organisation's name | As `first_name`; UKE fills it for clubs only |
| 4 | `city` | string | City, town, suburb or municipality | All sources except URSEC |
| 5 | `county` | string | US county | FCC only |
| 6 | `state` | string | State, province or region, as the source words it | Most sources |
| 7 | `postal_code` | string | Postal code. For Canada, the first 3 characters only | FCC, ISED, BNetzA, Nkom, ACMA organisations; not Anatel, ENACOM, UKE or URSEC |
| 8 | `country` | string | DXCC entity of the callsign (see below) | Every row, from `cty.dat` |
| 9 | `dxcc` | int64 | DXCC entity number | Every row, from `cty.dat` |
| 10 | `grid` | string | 4-character Maidenhead grid square, such as `DN13` | FCC, ISED, ACMA |
| 11 | `license_class` | string | Licence class, in the source's own wording | Most sources |
| 12 | `source` | string | Which register the row came from: one of nine tags | All sources |
| 13 | `synced_at` | string | Date of the upstream file the row was read from | All sources |
| 14 | `allstar_nodes` | list of int64 | AllStarLink node numbers held by this callsign, sorted; empty when none | AllStarLink |
| 15 | `grant_date` | string | Date the licence was granted | FCC, ACMA |
| 16 | `effective_date` | string | Date the licence took effect | FCC, ACMA |
| 17 | `expired_date` | string | Date the licence expires or expired | FCC, ACMA |
| 18 | `frn` | string | FCC Registration Number | FCC |
| 19 | `entity_type` | string | FCC entity-type code, such as `L` | FCC |
| 20 | `applicant_type` | string | Readable holder type, such as "Individual" or "Company" | FCC, ACMA |
| 21 | `previous_callsign` | string | The callsign the holder had before | FCC |
| 22 | `uses_lotw` | boolean | Reserved for ARRL LoTW membership | False on every row: LoTW data is excluded from published builds |
| 23 | `lotw_last_activity` | string | Reserved for the last LoTW upload date | Empty on every row: LoTW data is excluded from published builds |

All dates are ISO strings, `YYYY-MM-DD`. A value the source does not publish
is null. The "Filled by" column reflects `hamcall-db-2026-11-13.parquet`.

A few columns need more than a line:

- **`country` and `dxcc`** describe the callsign's prefix. DXCC is the
  ARRL's list of countries and territories ("entities") that amateurs count
  for awards. The build looks up each callsign's prefix in AD1C's `cty.dat`
  file to find its entity (`hamcall_db/sources/ad1c.py`). A KH6 callsign is
  "Hawaii" even when the holder's address is in Connecticut
  (`hamcall_db/geocode.py`).
- **`grid`** is a Maidenhead locator, the grid system amateurs use to say
  roughly where a station is. The build works it out from the postal code,
  or from the city when the postal code is not in its tables, and keeps
  only the first 4 characters (`hamcall_db/geocode.py`). The geocoder runs for
  FCC, ISED and ACMA rows only. On 2026-11-13, 118,801 rows had a grid.
- **`source`** is one of `fcc`, `ised`, `acma`, `nkom`, `bnetza`, `anatel`,
  `enacom`, `ursec`, `uke`. When two registers list the same callsign, the
  row from the higher-ranked source is kept (`SOURCE_PRECEDENCE` in
  `hamcall_db/merge.py`; the ranking is on the [sources page](sources.md)).
- **`synced_at`** is the date printed on, or attached to, the upstream file.
  For Bundesnetzagentur rows it is the edition date of the list, which the
  attribution line has to quote (NOTICE 16).

### History: `hamcall-db-history-YYYY-MM-DD.parquet`

Each night the build compares the new data with the previous night's history
and records what changed (`hamcall_db/history.py`). The method is called
**SCD2**, short for "slowly changing dimension, type 2": a change does not
overwrite the old row, it closes it and opens a new one. So each row is one
state of one callsign, with the dates it held.

The file has 15 columns: `callsign`, `valid_from`, `valid_to`, then
`first_name`, `last_name`, `city`, `county`, `state`, `postal_code`,
`country`, `dxcc`, `grid`, `license_class`, `source`, `synced_at` with the same
meanings as above.

- `valid_from` is the date the state first appeared in this project's builds.
  `valid_to` is null while the state is current. On 2026-11-13 the file held
  1,078,065 rows, of which 1,075,947 were current.
- A new row opens when any of `first_name`, `last_name`, `city`, `county`,
  `state`, `postal_code`, `country`, `dxcc`, `grid` or `license_class` changes.
  A new `synced_at` alone does not open one.
- History runs forward only. The upstream files are snapshots with no past in
  them, so history starts from the first build and grows one night at a time.
  `valid_from` is when a change reached this dataset, not when it happened in
  the real world.

### SQLite: `hamcall-db-YYYY-MM-DD.db`

The same data as one SQLite database, for programs that would rather run SQL
than read Parquet. The Parquet files are the contract; the database is a
convenience copy (`hamcall_db/sqlite_writer.py`).

| Table | What it holds |
|---|---|
| `current` | The current-state columns, minus `allstar_nodes`, plus an integer `id` |
| `allstar_nodes` | `id`, `node`: one row per node, for current holders |
| `history` | `id` plus the 15 history columns |
| `pota_parks` | The park directory |
| `pota_park_grids` | The park grid sets |

The `id` names one holder of one callsign, and it is stable across builds.
The same holder keeps the same `id` forever. When a callsign is reissued to
someone else, the new holder gets a new `id`, and an old `id` is never used
again. Each build reads the previous night's database and issues new ids
above the highest one it has seen (`hamcall_db/sqlite_writer.py`). That lets
you store an `id` in your own data and join on it later.

### POTA parks: `hamcall-db-pota-parks-YYYY-MM-DD.parquet`

Parks on the Air (POTA) is an award programme for operating from parks. This
file is its park directory: 38,603 parks on 2026-11-13. The columns are
`reference` (the park's ID, such as `US-0001`), `name`, `location_desc`,
`region`, `country`, `dxcc`, `grid`, `lat`, `lon`, `active`, `source` and
`synced_at` (`ParkRecord` in `hamcall_db/sources/pota.py`).

`lat`, `lon` and `grid` are one representative point per park, copied from
POTA. They are for display only. A large park spans many grid squares, so do
not join on them. Parks are places, not people, so the 4-character grid rule
below does not apply: `grid` is kept as POTA gives it, sometimes 6 characters.

### POTA park grids: `hamcall-db-pota-park-grids-YYYY-MM-DD.parquet`

One row per park and grid square the park touches, with columns `reference`,
`grid` (4 characters), `source` and `confidence` (`ParkGridRecord` in
`hamcall_db/sources/padus_grids.py`). `source` says how the square was found:

- `padus`: the park's boundary from PAD-US, the US government's protected-area
  database, intersected with the grid.
- `pota-point`: the single representative point from POTA.
- `osm`: an OpenStreetMap boundary. These rows only appear in the separate
  ODbL files below.

On 2026-11-13 all 38,603 rows had `source = pota-point`, because the nightly
build runs without the PAD-US boundary step (`.github/workflows/release.yml`).
Until that changes, treat the file as one square per park.

### OpenStreetMap park grids: the `latest-osm` release

Grid sets for non-US parks, worked out from OpenStreetMap boundaries, are
published in their own release with their own licence, ODbL
(`.github/workflows/release.yml`). On 2026-11-13 the release held files dated
2026-11-02:

- `hamcall-db-pota-park-grids-osm-YYYY-MM-DD.parquet`, same columns as above
- `hamcall-db-pota-park-grids-osm-YYYY-MM-DD.db`, a separate SQLite file with
  table `pota_park_grids_osm`
- `LICENSE-ODbL`

Why they are kept apart from the other files is on the
[licensing page](licensing.md#why-the-reflector-directory-is-kept-separate).

## The reflector directory

A **reflector** is an internet server that digital-voice radios and hotspots
link to, so that everyone connected hears each other. The directory lists
3,996 of them across seven networks (D-Star, M17, YSF, NXDN, P25, URF and
DMR), read from the live `index.json` on 2026-11-13. The
[reflector directory page](../reflectors/index.md) explains how to use it,
and the [API reference](../reflectors/api.md) and
[`openapi.json`](https://rcludwick.github.io/hamcall-db/api/v1/openapi.json)
give every field.

| File | What it holds |
|---|---|
| [`api/v1/index.json`](https://rcludwick.github.io/hamcall-db/api/v1/index.json) | The manifest: networks, row counts, dates, and where each file is |
| [`api/v1/reflectors.json`](https://rcludwick.github.io/hamcall-db/api/v1/reflectors.json) | Every reflector on every network (1.8 MB on 2026-11-13) |
| `api/v1/reflectors/<network>.json` | One network: `dmr`, `dstar`, `m17`, `nxdn`, `p25`, `urf` or `ysf` |
| `api/v1/reflectors/dmr/<system>/talkgroups.json` | One DMR network's talkgroups |
| [`api/v1/openapi.json`](https://rcludwick.github.io/hamcall-db/api/v1/openapi.json) | The machine-readable contract for all of these |

Each reflector entry has `network`, `id`, `name`, `source` and `license`, and,
where upstream supplies them, `aliases`, `description`, `country`, `sponsor`,
`dashboard` and `dial`. `dial` holds what a client needs to connect: the
protocol, host and port. `network` plus `id` identifies a reflector. A value
that is empty is left out of the entry rather than set to null
(`hamcall_db/reflectors.py`).

### DMR talkgroups

On DMR, a reflector row is one master server, and what you talk on is a
**talkgroup**: a number that means something only inside one DMR network.
Each network's list of talkgroups is mirrored as its own file, with `tg`
(the number) and `name` for each entry. DVRef, the upstream, allows 60
requests an hour, so the build refreshes the oldest lists in rotation, up to
40 a night by design (`TALKGROUP_SLICE` in `hamcall_db/sources/dvref.py`).
In practice it is slower: in the 2026-11-13 manifest about four lists a night
had been refreshed, so a list can be weeks old
([API reference](../reflectors/api.md#dmr-talkgroup-files)). On 2026-11-13 the manifest listed
130 networks with a mirror file and 1,837 talkgroups between them. The
manifest gives each file's URL under `networks.dmr.talkgroups`.

### Parquet and SQLite copies

The `latest-reflectors` release holds `hamcall-db-reflectors-2026-08-26.parquet`
(one row per network and id), `hamcall-db-reflectors-2026-08-26.db` (table
`reflectors`) and `LICENSE-CC-BY`. It was last published on 2026-08-26 and is
not refreshed by the nightly job, which updates only the JSON
(`deploy/vps/build.sh`). For current data, use the JSON.

## The AllStar directory: `allstar.json`

AllStarLink is a network of linked repeaters, hotspots and personal radio
links. Each of these is a **node**, and each node has a number that other
nodes dial to connect to it. [`api/v1/allstar.json`](https://rcludwick.github.io/hamcall-db/api/v1/allstar.json)
republishes AllStarLink's public node list: 41,616 nodes on 2026-11-13,
sorted by node number (`hamcall_db/build_allstar.py`). Its own
[page](../allstar.md) covers every field, the refresh and its guards. Each
node has four keys:

```json
{"callsign":"WB6NIL","description":"ASL Public Hub","location":"aws-east2ls-ast0","node":"2000"}
```

- `node` is a string of digits, not an integer, with leading zeros removed.
- `callsign` is upper case.
- `description` and `location` are free text, as the node's owner typed them,
  or null. On 2026-11-13, `description` was null on 19,732 nodes and
  `location` on 163.

The file also carries `generated`, `count`, `license`, `attribution`,
`rights`, `modifications` and `sources`, so its terms travel with it. The same
node numbers appear in the callsign dataset's `allstar_nodes` column, which is
built from the same upstream list (`hamcall_db/enrich_allstar.py`).

## What was removed for privacy

The national registers publish names and addresses of private people. The
published files are built so that they cannot be used to find someone's
home. The rules below are enforced in code, and each names where.

### Grids stop at 4 characters

A Maidenhead locator gets more precise with every pair of characters. The
first 4 characters name a square 2° of longitude by 1° of latitude: at 40° N
that is about 170 km by 111 km. Two more letters split the square 24 ways in
each direction, into a subsquare of about 7 km by 4.6 km.

The obvious design is to publish the 6-character subsquare, because loggers
and mapping tools accept it and it looks more useful. Put it next to the
name, the city and the postal code in the same row, though, and it narrows a
licensee to a few kilometres around their home. In a rural area that can be
a handful of houses.

So `latlon_to_grid` returns 4 characters and has no way to return 6
(`hamcall_db/geocode.py`). The point it starts from is the centre of the
postal code or the city, never a street address. On 2026-11-13 all 118,801
grids in the current Parquet were 4 characters long.

### No street addresses

The FCC, ISED and several other registers publish a street address with
every licence. The obvious design is to keep it in a `street` column: it is
in the source, and it would make geocoding precise. But a file of a million
names next to a million street addresses is a list of homes, and this one
would be downloadable by anyone.

So the `Record` dataclass that defines the published columns has no street
field, and no importer copies a street into it (`hamcall_db/models.py`).

Leaving out the column is not enough on its own, because addresses leak into
other columns. Some licensees type their street into the city field, and some
registers print it there. FCC's city column is 20 characters wide, so a
leaked address can arrive cut short. Two checks catch these:

1. The FCC, ISED, ACMA, Nkom, BNetzA and UKE importers compare the published
   fields with that row's street, and drop a field that repeats it, including a cut-off copy of at least 16
   characters (`echoes_street` in `hamcall_db/address_guard.py`). A city of
   `100 Fake Rd` on a row whose street is `100 Fake Rd` is dropped.
2. After all sources are merged, including Anatel, ENACOM and URSEC, whose
   importers have no street check, `normalize()` sets `first_name`, `last_name`
   or `city` to null if the value has the shape of a street address,
   whatever its source (`looks_like_street_address` in
   `hamcall_db/address_guard.py`, called from `hamcall_db/merge.py`). The
   shapes it knows are a house number before a street word (`100 Fake Rd`),
   a street word with a number after it (`Calle Falsa 123`), lot and flat
   numbers, and a value that is only digits.

### Rules for particular registers

Some upstreams set their own limits, and the build follows them. The
[sources page](sources.md) gives each register's terms.

- **Canada (ISED).** `postal_code` keeps only the first 3 characters, the
  forward sortation area: `K1G 0Z5` becomes `K1G` (`fsa()` in
  `hamcall_db/sources/ised.py`). The rule is
  applied to history rows too. On 2026-11-13 every ISED postal code was 3
  characters.
- **Australia (ACMA).** A person is published with callsign, class, dates
  and client type only, and an unclear type counts as a person
  (`hamcall_db/sources/acma.py`, NOTICE 3).
- **Norway (Nkom).** Holders with a foreign postal address have no town or
  postal code (NOTICE 15).
- **Germany (BNetzA).** Holders who objected under § 15(4) AFuV have callsign
  and name only, and station locations are not published (NOTICE 16).
- **All national registers.** National ID numbers, coordinates and columns
  unrelated to the licence are never published (NOTICE,
  "National registers").

### Email addresses

Reflector `name`, `sponsor` and `description`, talkgroup names, and AllStar
`description` and `location` all have any email address replaced with
`[email removed]` (`hamcall_db/reflectors.py`,
`hamcall_db/enrich_allstar.py`). The reason is on the
[licensing page](licensing.md#modifications).

### AllStar locations

AllStar locations are free text, and owners often put their street there:
`Anytown, IA, 1431 Example St`. The house number is in the third part, so
the street-address check above would miss it. `location_has_street_address`
checks the whole value and then each comma-separated part
(`hamcall_db/address_guard.py`). When it fires, the location is set to null
and the node stays in the file. The check errs towards blanking, and some
numbered place names such as `29 Palms, CA` lose their location.

AllStar descriptions do not go through this check. They are published as
typed, with email addresses removed. Adding the same check to descriptions
is an open item, `hdb-allstar-desc` in
[`docs/BACKLOG.md`](https://github.com/rcludwick/hamcall-db/blob/main/docs/BACKLOG.md).

### Excluded sources

ARRL LoTW and NMHH Hungary are excluded from every release because their
terms forbid redistribution, and the SOTA summit list is excluded too
([sources page](sources.md#excluded-from-published-builds)).

## Credits

Most files carry attribution requirements. The [licensing
page](licensing.md#what-to-credit) lists them file by file. The simplest way
to meet them all is to ship
[NOTICE](https://github.com/rcludwick/hamcall-db/blob/main/NOTICE) unmodified
with the data.
