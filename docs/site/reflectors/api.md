# Reflector API reference

*Last reviewed: 2026-11-13.*

This command fetches the URF network file and picks out one reflector:

```bash
curl -s https://rcludwick.github.io/hamcall-db/api/v1/reflectors/urf.json \
  | jq '.reflectors[] | select(.id == "007")'
```

```json
{
  "country": "ES",
  "dashboard": "https://ea8ee.c4fm.es/ref/",
  "dial": {
    "host": "ea8ee.c4fm.es",
    "kind": "urf",
    "modules": ["A", "B", "C", "D", "E"]
  },
  "id": "007",
  "license": "CC-BY-4.0",
  "name": "XRF007 D-STAR SPAIN",
  "network": "urf",
  "source": "dvref",
  "sponsor": "xreflector.es"
}
```

This page is the field-by-field reference for programs that read those files.
The [overview page](index.md) explains where the rows come from and how they
are refreshed. The machine-readable contract is
[`openapi.json`](https://rcludwick.github.io/hamcall-db/api/v1/openapi.json),
which the build generates from the same tables it uses to write the files
(`openapi_document()` in `hamcall_db/reflectors.py`). Where this page and
`openapi.json` disagree, `openapi.json` is right.

## The files

All paths are relative to the base URL
`https://rcludwick.github.io/hamcall-db/api/v1/`. They are static files on
GitHub Pages: no token, no account, and no query parameters. These are the
paths `openapi.json` declares, with sizes on 2026-11-13:

| Path | What it holds | Size |
|---|---|--:|
| `index.json` | The manifest: what exists and how fresh it is | 28,371 B |
| `reflectors.json` | Every reflector on every network, 3,996 rows | 1,836,386 B |
| `reflectors/{network}.json` | One network's reflectors; `{network}` is `dmr`, `dstar`, `m17`, `nxdn`, `p25`, `urf` or `ysf` | 64,551 B for `urf` |
| `reflectors/dmr/{system}/talkgroups.json` | One DMR network's talkgroups | 2,020 B for `systemx` |
| `openapi.json` | The contract (OpenAPI 3.1) | 35,968 B |

A sixth path, `allstar.json`, holds the AllStarLink node directory. It has its
own `generated` date, licence and schema (`AllStarDirectory` in
`openapi.json`) and is covered on its own page,
[AllStar node directory](../allstar.md).

The per-network files exist so that one failing upstream does not cost a client
the whole directory, and so that a client interested in one network downloads
only that network.

The `.json` suffix is part of every URL. GitHub Pages answers the extensionless
`/api/v1/reflectors` with a redirect to `/api/v1/reflectors/`, which is a 404
(checked with `curl -sI` on 2026-11-13). A path that is not published, such as
a network or talkgroup file that does not exist, is also a 404.

## The manifest: `index.json`

The manifest is the small file a client fetches on every routine check. Here
it is with `networks` cut down to two of its seven entries and DMR's
`talkgroups` map cut down to one of its 130:

```json
{
  "api_version": "v1",
  "client_refresh_days": 7,
  "count": 3996,
  "endpoints": {
    "all": "reflectors.json",
    "allstar": "allstar.json",
    "network": "reflectors/{network}.json",
    "openapi": "openapi.json"
  },
  "expires_hint": "2026-11-20",
  "generated": "2026-11-13",
  "license": "mixed \u2014 see per-row license and NOTICE",
  "license_url": "https://github.com/rcludwick/hamcall-db/blob/main/NOTICE",
  "modifications": "Reformatted from the upstream source into hamcall-db's common reflector JSON schema. Field names and structure differ from upstream. Email addresses have been removed from the free-text fields (name, sponsor, description) and replaced with '[email removed]'; values are otherwise unmodified.",
  "networks": {
    "dmr": {
      "count": 195,
      "generated": "2026-11-13",
      "license": "CC BY 4.0",
      "source": {"name": "DVRef", "url": "https://dvref.com/api/v2/"},
      "talkgroups": {
        "systemx": {
          "count": 21,
          "fetched": "2026-10-29",
          "generated": "2026-09-29",
          "url": "reflectors/dmr/systemx/talkgroups.json"
        }
      },
      "url": "reflectors/dmr.json"
    },
    "urf": {
      "count": 138,
      "generated": "2026-11-13",
      "license": "CC BY 4.0",
      "source": {"name": "DVRef", "url": "https://dvref.com/api/v2/"},
      "url": "reflectors/urf.json"
    }
  },
  "rights": "hamcall-db's own contributions (schema, selection, derived fields) and all DVRef-sourced rows are CC BY 4.0. Rows from the XLX registry and Pi-Star host files are factual listings whose publishers state no licence; they are redistributed as facts and no rights are granted beyond those the upstream provides.",
  "schema_version": 1
}
```

The live file spells the dash in the `license` value as a literal character;
`\u2014` is the same string in JSON escape form. Keys are sorted in every
data file (`hamcall_db/reflectors.py`).

| Field | Meaning |
|---|---|
| `schema_version` | Always `1` in v1. Required. |
| `api_version` | Always `"v1"`. |
| `generated` | The newest `generated` date among the network files. Required. The talkgroup files do not move it. |
| `client_refresh_days` | How many days a client should wait between checks: `7`. |
| `expires_hint` | `generated` plus `client_refresh_days` (`hamcall_db/reflectors.py`). |
| `count` | Total rows across all networks. |
| `license` | `"CC BY 4.0"` if every row in the directory is CC BY, otherwise the mixed label shown above. |
| `license_url` | The CC BY 4.0 deed for a CC BY file, NOTICE for a mixed one. |
| `rights` | Present on mixed files only: what is and is not licensed, in words. |
| `modifications` | What hamcall-db changed, as CC BY 4.0 requires. |
| `endpoints` | Paths of the other files, relative to `index.json`. Keys: `all`, `allstar`, `network`, `openapi`. |
| `networks` | One object per network, keyed by network name. Required. |

Each object in `networks` has:

| Field | Meaning |
|---|---|
| `url` | Path of that network's file. Required. |
| `count` | Rows in that file. Required. |
| `generated` | That file's `generated` date. Required. |
| `source` | The upstream, as `name` and `url`. |
| `license` | That file's `license` label, so a client can see which files are plain CC BY without downloading them. |
| `talkgroups` | DMR only: the talkgroup mirror map, described under [DMR talkgroup files](#dmr-talkgroup-files). |

## Reflector files: `reflectors.json` and `reflectors/{network}.json`

Both kinds of file wrap a list of entries in the same envelope. Here is the
start of `reflectors/urf.json`, with `reflectors` cut to its first row:

```json
{
  "api_version": "v1",
  "attribution": "Reflector data provided by DVRef \u2014 https://dvref.com/",
  "client_refresh_days": 7,
  "count": 138,
  "generated": "2026-11-13",
  "license": "CC BY 4.0",
  "license_url": "https://creativecommons.org/licenses/by/4.0/",
  "modifications": "Reformatted from the upstream source into hamcall-db's common reflector JSON schema. Field names and structure differ from upstream. Email addresses have been removed from the free-text fields (name, sponsor, description) and replaced with '[email removed]'; values are otherwise unmodified.",
  "network": "urf",
  "reflectors": [
    {
      "country": "ES",
      "dashboard": "http://ycs722.xreflector.net/ref/",
      "dial": {"host": "ycs722.xreflector.net", "kind": "urf"},
      "id": "000",
      "license": "CC-BY-4.0",
      "name": "XRF00",
      "network": "urf",
      "source": "dvref",
      "sponsor": "xreflector"
    }
  ],
  "schema_version": 1,
  "source": {"name": "DVRef", "url": "https://dvref.com/api/v2/"}
}
```

Both files carry `schema_version`, `api_version`, `generated`,
`client_refresh_days`, `license`, `license_url`, `modifications`,
`attribution`, `count` and `reflectors`, plus `rights` when the file is mixed.
Then they differ:

- A network file adds `network` (its name) and `source` (its upstream, as
  `name` and `url`).
- `reflectors.json` adds `networks`, a map from network name to row count, and
  `sources`, a list of upstreams, each with `name` and `url`. Its
  `attribution` is four lines separated by `\n`, one per upstream.

`reflectors` is sorted by `id` in a network file. `count` always equals the
length of `reflectors`. The schemas are `ReflectorCollection` and
`NetworkCollection` in `openapi.json`. Of the envelope fields, the schemas
require `schema_version`, `generated`, `count` and `reflectors` in both kinds
of file, and `network` in a network file; the build writes the rest too.

## One entry

An entry has two parts. The outer fields describe the reflector, for searching
and display. The `dial` object says how to connect. Empty values are left out,
never written as `null` (`entry_json` in `hamcall_db/reflectors.py`), so read a
missing key as "unset".

| Field | Required | Meaning | Rows carrying it, of 3,996 |
|---|:--:|---|--:|
| `network` | yes | `dmr`, `dstar`, `m17`, `nxdn`, `p25`, `urf` or `ysf` | 3,996 |
| `id` | yes | Stable within `network`. `network` plus `id` is the key a client stores as a favourite. | 3,996 |
| `name` | yes | Display name. When upstream has none, it is the `id`. | 3,996 |
| `source` | yes | The importer that produced the row: `dvref`, `xlx` or `dextra`. | 3,996 |
| `license` | yes | This row's licence: `CC-BY-4.0` or `none-stated`. See [Licence fields](#licence-fields). | 3,996 |
| `aliases` | no | Other names that address this same reflector. See [Aliases](#aliases). | 897 |
| `alias_source` | no | `pistar` when some of the `aliases` came from the Pi-Star DPlus and DCS host files. | 887 |
| `description` | no | Free text from upstream. DVRef descriptions are HTML. | 3,352 |
| `country` | no | Country as upstream wrote it: a code such as `ES` on some rows, a name such as `Brazil` on others. | 3,943 |
| `sponsor` | no | Who runs it, in upstream's words. | 2,658 |
| `dashboard` | no | URL of the reflector's web dashboard. | 3,321 |
| `system` | no | DMR only, and on every DMR row: the DMR network the server belongs to. | 195 |
| `talkgroups` | no | DMR only: path of the mirrored talkgroup file, relative to the API root. | 195 |
| `dial` | no | How to connect. Absent means the entry is listed but cannot be dialled from this data. | 3,966 |

## The `dial` object

`dial.kind` says which protocol the entry speaks, and it decides which other
keys `dial` holds. Every kind has `host` (a hostname or IP address, as upstream
gave it). Every kind except `urf` also has `port`. The variants are the
`Dial*` schemas in `openapi.json`; the counts are from `reflectors.json` on
2026-11-13.

| `dial.kind` | `network` | Keys beyond `kind`, `host`, `port` | Rows |
|---|---|---|--:|
| `dextra` | `dstar` | `callsign`, `modules` | 951 |
| `m17` | `m17` | `callsign`, `modules` | 151 |
| `ysf` | `ysf` | none | 1,684 |
| `nxdn` | `nxdn` | none | 418 |
| `p25` | `p25` | none | 459 |
| `urf` | `urf` | `modules`; `port` is optional | 138 |
| `mmdvm` | `dmr` | `system`, `requires`, `talkgroups_url`, `talkgroup`, `timeslot` | 165 |

What the extra keys mean:

- `callsign` is the name the reflector answers to on the wire. It is not
  always the directory name: `XLX000` is dialled as `XRF000`, the name a D-Star
  radio sends in its RPT1 and RPT2 header fields.
- `modules` lists the module letters, `A` to `Z`. A module is one room inside
  a reflector, carrying its own conversation. All 151 M17 rows carry
  `modules`; 89 of the 138 URF rows do; no D-Star row does.
- `system`, `requires`, `talkgroups_url`, `talkgroup` and `timeslot` are
  described under [DMR servers](#dmr-servers).

The other five kinds look like this, copied from `reflectors.json`:

```json
{"callsign": "XRF000", "host": "201.62.48.60", "kind": "dextra", "port": 30001}
{"callsign": "M17-002", "host": "urf001.cumbriacq.com", "kind": "m17", "modules": ["A"], "port": 17000}
{"host": "hellaszone.com", "kind": "ysf", "port": 42000}
{"host": "79.129.234.51", "kind": "nxdn", "port": 41400}
{"host": "192.241.160.183", "kind": "p25", "port": 41001}
```

Two values are derived by hamcall-db rather than read from upstream. Every
D-Star `port` is 30001, DExtra's fixed port, because neither D-Star upstream
publishes one (`hamcall_db/sources/xlx.py`, `hamcall_db/sources/dextra.py`).
The `callsign` of an XLX entry is its XRF-form name.

The build writes no `dial` when an entry has no host, or when it has no port
and its kind needs one (`_dial_json` in `hamcall_db/reflectors.py`). It never
fills in a guessed port. URF is the exception: DVRef publishes no port for URF
reflectors, and a URF reflector (the `urfd` server) listens for several
protocols, each on its own port, so there is no single port to give. A URF
`dial` therefore carries `host` alone, plus `modules` where known. On
2026-11-13, the 30 entries without a `dial` were all DMR.

## DMR servers

A DMR entry is one master server: the machine a hotspot connects to. DVRef
lists DMR networks, each with zero or more servers, and the build publishes
one row per server. Its `id` is DVRef's identifier (slug) for the server
(`hamcall_db/sources/dvref.py`). Here is one, from `reflectors.json`:

```json
{
  "country": "ES",
  "dashboard": "http://radiodigitalspain.ddns.net/dashboard",
  "dial": {
    "host": "radiodigitalspain.ddns.net",
    "kind": "mmdvm",
    "port": 62031,
    "requires": ["dmr_id", "password"],
    "system": "01hamradiodx",
    "talkgroups_url": "https://dvref.com/api/v2/dmr/networks/01hamradiodx/talkgroups/"
  },
  "id": "01hamradiodx-server-01hamradiodx",
  "license": "CC-BY-4.0",
  "name": "01HAMRADIODX",
  "network": "dmr",
  "source": "dvref",
  "sponsor": "01HAMRADIODX",
  "system": "01hamradiodx",
  "talkgroups": "reflectors/dmr/01hamradiodx/talkgroups.json"
}
```

- `system` names the DMR network. A talkgroup number only means something
  inside one network, so a client needs `system` to know what it is connected
  to. It appears twice: on every DMR row, and inside `dial` when the row has
  one, so that a client reading only `dial` still has it. The two always
  match. The 195 rows on 2026-11-13 belonged to 121 networks.
- `requires` lists what the operator must supply and the directory cannot:
  `["dmr_id", "password"]` on every live row. A client that sees `requires`
  should prompt for those values rather than try to connect without them.
- `talkgroups_url` is DVRef's own talkgroup list for the network. DVRef's API
  asks for an account token (NOTICE source 11).
- `talkgroups`, outside `dial`, is the path of hamcall-db's copy of that list,
  which needs no token. See the next section.
- `talkgroup` and `timeslot` are reserved. They would pin a row to one
  talkgroup on one timeslot (`1` or `2`), and no published row carries them.
  They are declared so that such rows could be added later without a version
  change.

DVRef's address field for a DMR server is free text, and some entries hold a
dashboard URL instead of a hostname. The build keeps a value only if it is a
bare hostname or IP address; otherwise it uses the server's numeric address,
and if there is none the row gets no `dial` (`_host` in
`hamcall_db/sources/dvref.py`). Such a row still has its `system`, `name`,
`country`, `sponsor`, `dashboard` and `talkgroups`. `requires` and
`talkgroups_url` live only inside `dial`, so they go with it.

## DMR talkgroup files

A **talkgroup** is a numbered channel inside one DMR network. Each network's
list is published as `reflectors/dmr/{system}/talkgroups.json`. This is the
SystemX file, with `talkgroups` cut to three of its 21 rows:

```json
{
  "api_version": "v1",
  "attribution": "Reflector data provided by DVRef \u2014 https://dvref.com/",
  "client_refresh_days": 7,
  "count": 21,
  "generated": "2026-09-29",
  "license": "CC BY 4.0",
  "license_url": "https://creativecommons.org/licenses/by/4.0/",
  "modifications": "Reformatted from the upstream source into hamcall-db's common reflector JSON schema. Field names and structure differ from upstream. Email addresses have been removed from the free-text fields (name, sponsor, description) and replaced with '[email removed]'; values are otherwise unmodified.",
  "network": "dmr",
  "schema_version": 1,
  "source": {
    "name": "DVRef",
    "url": "https://dvref.com/api/v2/dmr/networks/systemx/talkgroups/"
  },
  "system": "systemx",
  "talkgroups": [
    {"name": "CQ North West UK", "tg": 69},
    {"name": "Chat 1", "tg": 80},
    {"name": "235 Alive", "tg": 235}
  ]
}
```

Each row has `tg`, the talkgroup number, and `name` when upstream gives one.
Rows are sorted by `tg`. The envelope is the reflector envelope with
`network` always `dmr` and `system` added; the schema is `TalkgroupCollection`,
which requires `schema_version`, `network`, `system`, `generated`, `count` and
`talkgroups`.

Find the files through the manifest rather than by building URLs.
`networks.dmr.talkgroups` maps each mirrored network to its `url`, `count`,
`generated` and `fetched`, all four required, and a DMR row's own `talkgroups` field holds the
same path. A network missing from the map has no file. On 2026-11-13 the map
listed 130 networks, 101 of them with at least one talkgroup and 1,837
talkgroups in total.

A talkgroup list has two dates, and they answer different questions:

| Field | Where | Meaning |
|---|---|---|
| `generated` | the talkgroup file, and the manifest map | When this list last changed. |
| `fetched` | the manifest map only | When the build last confirmed the list against DVRef. |

SystemX above was last confirmed on 2026-10-29, and its list had not changed
since 2026-09-29. A refetch that finds the same list leaves the file
byte-identical and moves only `fetched` in the manifest.

The lists are refreshed in rotation, because DVRef allows 60 requests an hour
per account and there is one request per network. Each night the build
refetches the networks fetched longest ago, up to 40 of them, and skips any
fetched in the last two days (`TALKGROUP_SLICE` and `TALKGROUP_MIN_AGE_DAYS` in
`hamcall_db/sources/dvref.py`). In practice the rotation is slower than that
cap. In the 2026-11-13 manifest, about four networks a night had moved their
`fetched` date, and the oldest `fetched` among networks with servers was
2026-10-15. Nine files belong to networks that have no servers listed today;
their `fetched` dates go back to 2026-09-07. If a client needs a list that is
current today, it should fetch `dial.talkgroups_url` from DVRef.

## Aliases

`aliases` holds other names that reach this same reflector, so that a user
who types any of them finds it. The rules that decide what goes in:

- An XLX reflector answers on three D-Star linking protocols: DExtra (`XRF`
  names), DPlus (`REF`) and DCS (`DCS`). Its XRF-form name is an alias. So is
  any REF or DCS name whose Pi-Star host file entry has the same address as the
  reflector. REF and DCS names are matched by address, never by number
  (`hamcall_db/reflectors.py`), and they are why a row carries
  `alias_source: "pistar"`.
- An alias never equals another entry's `id`. A name has to lead to one
  entry, so when the two clash the alias is dropped and the `id` stays
  (`drop_shadowing_aliases` in `hamcall_db/reflectors.py`).

The second rule matters because XLX and standalone XRF reflectors share
numbers but not machines. On 2026-11-13, `XLX002` was at `223.95.197.217`, and
the standalone entry `XRF002` was at `52.36.45.107`. `XLX002` answers to
`XRF002` on the wire, so its `dial.callsign` is `"XRF002"`, but its row has no
`aliases`: the alias was dropped because `XRF002` is another entry's `id`. A
client should not rebuild aliases from `dial.callsign`, since doing so would
send a user who typed `XRF002` to the wrong server.

## Searching

The files offer no query parameters, so a client searches its own copy. These
fields are the ones worth indexing: `id`, `name`, `aliases`, `description`,
`country` and `sponsor`. A case-insensitive substring match works well:
`XLX0`, `000` and `brazil` all find `XLX000` on the [overview page](index.md#one-entry).
Filter by `network` first when the user has already picked one.

Three things to know about the text:

- DVRef descriptions arrive as HTML, tags and entities included. The P25
  entry `100` has the description
  `<p>Motorola Alumni, system installers, and tech talk.</p>` followed by more
  paragraphs. Strip or sanitise the HTML before indexing or displaying it.
- Email addresses in `name`, `sponsor` and `description` are replaced with
  `[email removed]` (`EMAIL_PLACEHOLDER` in `hamcall_db/reflectors.py`), and
  so are those in talkgroup names. On 2026-11-13, 72 entries contained that
  placeholder.
- `country` is not normalised. Match it as text.

## Caching and freshness

The files change at most once a night. A nightly build commits new files only
when their content changed, and the site redeploys after each commit (see the
[overview page](index.md#how-the-directory-is-refreshed)).

GitHub Pages sends these headers on every file (checked with `curl -sI` on
2026-11-13):

| Header | Value for `index.json` | Meaning for a client |
|---|---|---|
| `Cache-Control` | `max-age=600` | Caches between the client and Pages may hold the file for 10 minutes. |
| `ETag` | `"6af6a0b2-6ed3"` | Send it back as `If-None-Match`; an unchanged file returns `304 Not Modified` with no body. |
| `Last-Modified` | `Fri, 13 Nov 2026 05:31:30 GMT` | Works the same way as `If-Modified-Since`. |
| `Access-Control-Allow-Origin` | `*` | A browser page on any site may fetch the files. |

Both conditional requests returned `304` on 2026-11-13. Pages also compresses
the files when asked with `Accept-Encoding: gzip`: `reflectors.json` went over
the wire as 239,215 bytes instead of 1,836,386. A compressed response carries
a weak ETag, `W/"6af6a0b2-6ed3"`, which is still accepted in `If-None-Match`.

The `ETag` and `Last-Modified` change more often than the data. On
2026-11-13 every file had the same `Last-Modified`, and each `ETag` was that
time in hexadecimal (`6af6a0b2` is 05:31:30 UTC) followed by the file size.
So both move whenever the site is redeployed, including deploys that change
only these documentation pages (`.github/workflows/pages.yml`). A `200` after
a changed ETag means "check the content", not "the data changed". Compare
`generated` and `count` to find out.

A client that follows these steps fetches as little as possible:

1. Every `client_refresh_days` days (7), fetch `index.json`, sending the
   `ETag` you stored last time in `If-None-Match`. On `304`, you are done.
2. Compare each `networks.<name>.generated` and `count` with the values you
   cached. Download only the network files where either moved.
3. Check each downloaded file: its `count` should equal the length of its
   `reflectors` list. If it does not, the download was cut short; keep the old
   copy.
4. For DMR talkgroup lists, compare `networks.dmr.talkgroups.<system>.generated`
   with your copy, and download only those that moved.

To tell whether the directory itself has gone stale, compare `expires_hint`
with today's date. `expires_hint` is the newest network `generated` plus seven
days, so a date in the past means no network's data has moved for a week.
On 2026-11-13 every network's `generated` was 2026-11-13. A single network
with an older `generated` than the rest means the build kept that network's
previous file: its upstream failed, or the new data shrank too far to trust
(`SHRINK_GUARD` in `hamcall_db/build_reflectors.py`). The old file is still
the best data available, so keep using it.

## Compatibility rules

These rules let new networks and protocols appear without breaking existing
clients:

1. **Ignore what you do not recognise.** A client may show an entry whose
   `network` or `dial.kind` it does not know, but must never try to connect to
   it. Unknown keys should be ignored.
2. **Additions do not change the version.** A new network, a new `dial.kind`
   or a new field is added under `/api/v1/`. Older clients ignore it, and newer
   clients treat a missing key as unset.
3. **A change of meaning does.** Renaming a field, changing its type or unit,
   or reinterpreting a value would be published under a new path, `/api/v2/`,
   with `/api/v1/` kept serving until clients move. `schema_version` and
   `api_version` identify the version a file belongs to.
4. **No `dial` means not dialable.** Do not invent a port or address to fill
   the gap. Show the entry as listed but unavailable.

## Licence fields

Each row's `license` says which licence it is under: `CC-BY-4.0` for rows from
DVRef (`source: "dvref"`), and `none-stated` for rows from the XLX registry
and the Pi-Star DExtra host file (`xlx`, `dextra`), whose publishers state no
licence (`SOURCE_LICENSES` in `hamcall_db/reflectors.py`). On 2026-11-13 that
was 3,045 and 951 rows.

A file's own `license` is `"CC BY 4.0"` when every row in it is CC BY.
Otherwise it is the mixed label and the file adds `rights`. On 2026-11-13,
`dstar.json`, `reflectors.json` and `index.json` were mixed and the other
network files and the talkgroup files were CC BY 4.0. Every data file carries
`attribution` and `modifications` so the terms stay with the data; the manifest
carries `modifications` and `rights` but not `attribution`.

What the two licences allow, and what to credit when you republish, are on the
[overview page](index.md#licence) and the
[licensing page](../about/licensing.md#what-to-credit).
