# Licensing

*Last reviewed: 2026-11-13. Counts on this page were read from the published
files on that date.*

Say you want to build an app that looks up a callsign in
`hamcall-db-2026-11-13.parquet` and lists nearby reflectors from
`reflectors.json`. Those two files come under different licences, and so do
the other files this project publishes. A **licence** here is the set of
terms that says what you may do with a file: copy it, change it, sell
something built on it.

The upstreams behind each file, with their own terms and NOTICE numbers, are
on the [sources page](sources.md). NOTICE is the file in the repository that
records every upstream's terms and the exact credit line each one asks for.
Where this page and
[NOTICE](https://github.com/rcludwick/hamcall-db/blob/main/NOTICE) differ,
NOTICE wins.

## Which licence covers which file

| What you download | Licence | Commercial use |
|---|---|---|
| The build code in [this repository](https://github.com/rcludwick/hamcall-db) | [MIT](https://github.com/rcludwick/hamcall-db/blob/main/LICENSE) | Yes |
| The [callsign dataset](../callsigns.md): the current and history Parquet files, the SQLite `.db`, the POTA parks and park-grid files | [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) | No |
| The [reflector directory](../reflectors/index.md): DVRef rows, and the project's own schema and derived fields | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | Yes |
| The reflector directory: rows from the XLX registry and the Pi-Star host files | No licence stated upstream; labelled `none-stated` | Not granted by this project |
| The AllStar directory, [`api/v1/allstar.json`](https://rcludwick.github.io/hamcall-db/api/v1/allstar.json) | No licence stated; labelled `none-stated` | Not granted by this project |
| Park grids derived from OpenStreetMap (the `latest-osm` release) | [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/) | Yes, with share-alike |

The licences in that table, in plain terms:

- **MIT** lets you do anything with the code, commercial use included, as
  long as you keep its copyright and permission notice (`LICENSE`).
- **CC BY 4.0** is the Creative Commons Attribution licence. You may copy,
  change and sell the data. You must give **attribution**: credit the
  source, link the licence, and say what you changed.
- **CC BY-NC 4.0** is the same with a non-commercial condition added (the
  NC). You may copy and change the data for non-commercial purposes only.
- **ODbL 1.0** is the Open Database License. It permits commercial use, but
  it is **share-alike**: if you publish a database built from ODbL data, you
  must publish it under ODbL too.
- **`none-stated`** is the label this project puts on data whose publisher
  states no licence. It grants nothing. The project redistributes the data as
  factual listings, with credit, and no rights pass to you beyond those the
  upstream itself provides.

## The callsign dataset is CC BY-NC 4.0

The callsign dataset is everything in the nightly `hamcall-db-YYYY-MM-DD`
release except the OpenStreetMap grids: the current Parquet, the history
Parquet, the SQLite `.db`, and the POTA parks and park-grid files (NOTICE,
"Combined dataset terms"). The licence was chosen on 2026-06-16 (NOTICE,
"PROJECT DECISION").

The non-commercial term is required by ISED Canada, which allows its data to
be reproduced "for non-commercial purposes"; commercial redistribution needs
ISED's prior written permission (NOTICE 2). POTA publishes no terms, and its
park data is also used non-commercially (NOTICE 6). Commercial use of the
callsign dataset needs your own arrangement with those upstreams.

The CC BY-NC licence covers only this project's compilation. It grants
nothing beyond what each upstream allows. The dataset is distributed under
the union of all its upstream terms, so a redistributor must meet every one
of them at once (NOTICE, "Combined dataset terms"). One example: ACMA forbids
reproducing a natural person's details in a derived work, so you may not add
names or addresses back to Australian rows that publish a callsign only
(NOTICE 3).

The AllStarLink node numbers in the callsign dataset's `allstar_nodes` column
are part of this CC BY-NC set. The separate `allstar.json` file is not; it is
covered below.

Three sources are excluded from every release: ARRL LoTW (NOTICE 9), SOTA
(NOTICE 10) and NMHH Hungary (NOTICE 21). A local build made with
`--include-restricted` contains them, and its output must not be
redistributed.

## The reflector directory is CC BY 4.0, row by row

The reflector directory is published as JSON under
[`api/v1/`](https://rcludwick.github.io/hamcall-db/api/v1/index.json),
refreshed nightly. It was also released as Parquet and SQLite files in the
`latest-reflectors` release, last published on 2026-08-26 and not refreshed
nightly. That release carries its own
[`LICENSE-CC-BY`](https://github.com/rcludwick/hamcall-db/blob/main/LICENSE-CC-BY).

Its rows come from three upstreams, and only one of them licenses its data:

- **DVRef** places its reflector data under CC BY 4.0, commercial use
  included. That covers every row with `source: "dvref"` (the M17, YSF, NXDN,
  P25, URF and DMR rows) and the DMR talkgroup files (NOTICE 11).
- The **XLX registry** publishes no licence (NOTICE 12). Its rows have
  `source: "xlx"`.
- The **Pi-Star DExtra host file** publishes no licence (NOTICE 13). Its rows
  have `source: "dextra"`.

A fourth upstream, the Pi-Star DPlus and DCS host files, adds REF and DCS
names to existing D-Star rows as `aliases` and adds no rows. It publishes no
licence either. A row carrying those names is marked
`alias_source: "pistar"` (NOTICE 14).

The project cannot grant CC BY rights it was never given. So each row carries
its own `license` field: `CC-BY-4.0` for DVRef rows, `none-stated` for XLX and
DExtra rows (`SOURCE_LICENSES` in `hamcall_db/reflectors.py`). On 2026-11-13,
`reflectors.json` held 3,045 `CC-BY-4.0` rows and 951 `none-stated` rows. If
you need a set that is wholly CC BY, keep only the rows whose `license` is
`CC-BY-4.0` (`LICENSE-CC-BY`).

The project's own contributions (the schema, the selection of rows and the
derived fields) are CC BY 4.0.

Each file also states a licence at its top level. A file whose rows are all
`CC-BY-4.0`, such as `m17.json`, says `"license": "CC BY 4.0"`. A file holding
any `none-stated` row says its licence is mixed, points to the per-row
`license` field and NOTICE, and adds a `rights` note that repeats the two
paragraphs above. On 2026-11-13 those files were `dstar.json`,
`reflectors.json` and `index.json` (`_license_block` in
`hamcall_db/reflectors.py`).

## Why the reflector directory has its own licence { #why-the-reflector-directory-is-kept-separate }

The simplest plan would be one dataset under one licence: put the reflectors
into the callsign release and call the whole thing CC BY-NC.

That plan breaks DVRef's licence. CC BY 4.0, section 2(a)(5)(B), forbids a
redistributor from imposing "any additional or different terms or conditions
on ... the Licensed Material if doing so restricts exercise of the Licensed
Rights." CC BY lets anyone use DVRef's rows commercially. Relabelling them CC
BY-NC would take that right away, which is exactly what the clause forbids
(NOTICE, "The reflector directory is a separate dataset").

The other single licence fails too. Calling the callsign dataset CC BY would
grant commercial use of ISED data, which ISED's terms do not allow (NOTICE 2).

So the reflector files are separate: their own files, their own
`LICENSE-CC-BY`, their own entries in NOTICE (11 to 14). NOTICE says they must
never be merged into the CC BY-NC artifacts.

The OpenStreetMap park grids are kept apart for a different reason. ODbL is
share-alike: a database that combines ODbL data with other data and is
published must itself be released under the ODbL. Mixing the OpenStreetMap
grids into the callsign files would put the CC BY-NC data under that
obligation, so the grids ship in their own files under their own licence
(NOTICE 8).

!!! warning "Joining the files yourself"

    You can join these files on your own machine. If you redistribute the
    joined result, it carries every restriction of every part. Join the ODbL
    grids to anything and publish the result, and that database is ODbL. Join
    the CC BY-NC callsign data to the reflector directory and the result is
    non-commercial.

## The AllStar directory states no licence

`api/v1/allstar.json` lists AllStarLink nodes: 41,616 of them on 2026-11-13
([AllStar node directory](../allstar.md)). Its top-level `license` field reads
`none-stated`.

AllStarLink's [Terms of Use](https://www.allstarlink.org/terms/) (effective
2026-09-22) treat information about a user's nodes as "User Content" (§2),
exclude User Content from the CC BY-SA licence on the rest of their site (§8),
and permit "reasonable use of published directories, node lists, and APIs"
(§6). The project republishes the node list on that basis, so there is no
share-alike obligation (NOTICE 5). The file's `rights` field says it plainly:
"No rights granted beyond those AllStarLink provides."

Attribution is required. Reproduce this line, which the file also carries in
its `attribution` field (`ALLSTAR_ATTRIBUTION` in
`hamcall_db/build_allstar.py`):

> AllStarLink node data courtesy of AllStarLink, Inc.
> (https://www.allstarlink.org/).

## Sources that state no licence

Several upstreams publish openly and state no licence: POTA, AllStarLink,
the XLX registry, the Pi-Star host files, Nkom, ENACOM and UKE. The project's
policy, recorded in NOTICE under "Project policy for openly published data"
(decided 2026-11-02), is to redistribute such data with attribution, inside whichever licence the
published file carries, and to label it `none-stated` where the format has a
place for it. Where an upstream does state terms, the build follows them. No
permission requests are made. The [sources
page](sources.md#sources-that-state-no-licence) lists which files each of
these feeds.

A rights holder who wants data removed can open an issue at
[github.com/rcludwick/hamcall-db/issues](https://github.com/rcludwick/hamcall-db/issues)
(NOTICE, "TAKEDOWN").

## What to credit

**Callsign dataset.** The simplest way to comply is to ship
[NOTICE](https://github.com/rcludwick/hamcall-db/blob/main/NOTICE) unmodified
with the data (NOTICE, last paragraph). If you write your own credits instead,
NOTICE lists what a redistributor must reproduce:

- the ISED attribution, and ISED's non-commercial terms (NOTICE 2)
- "Based on Australian Communications and Media Authority information"
  (NOTICE 3)
- the AD1C attribution and its MIT copyright and permission notice (NOTICE 4)
- the AllStarLink attribution (NOTICE 5)
- the POTA attribution (NOTICE 6)
- a credit to USGS PAD-US where PAD-US grids are present (NOTICE 7)
- the attribution lines of Nkom, Bundesnetzagentur (with its edition date),
  Anatel, ENACOM, URSEC and UKE (NOTICE 15 to 20). For Bundesnetzagentur, do
  not alter the published entries.

CC BY-NC also asks you to credit this project and link the
[licence](https://creativecommons.org/licenses/by-nc/4.0/).

**Reflector directory.** Reproduce each line that applies to the rows you
redistribute, or all four if you redistribute the files whole
(`LICENSE-CC-BY`):

> Reflector data provided by DVRef &mdash; https://dvref.com/
>
> XLX reflector data from the XLX registry maintained by Luc Engelmann,
> LX1IQ (http://xlxapi.rlx.lu/).
>
> Standalone XRF reflector data from the Pi-Star DExtra host file
> (http://www.pistar.uk/downloads/DExtra_Hosts.txt).
>
> REF and DCS reflector names from the Pi-Star DPlus and DCS host files
> (http://www.pistar.uk/downloads/), which compile them from
> dstargateway.org and xreflector.net DNS.

Only the DVRef line is a CC BY 4.0 obligation. The other three credit sources
that state no licence. They are given as a courtesy, and so a reader can tell
where each row came from.

Every published reflector file carries `license`, `attribution` and
`modifications` fields inline, and `allstar.json` does the same. A credit that
lives only in a README can get separated from the data; a field in the file
travels with it (`hamcall_db/reflectors.py`).

**OpenStreetMap park grids.** Reproduce the OpenStreetMap and Geofabrik line
given in NOTICE 8, and keep the file under ODbL.

## Changes made to the reflector data { #modifications }

CC BY 4.0 requires a redistributor to say what was changed. For the reflector
directory, `LICENSE-CC-BY` records these changes:

- Upstream rows were reformatted into the project's common schema, so field
  names and structure differ, and rows from the three row sources were
  combined into one set per network.
- Entries without a name or a last-known address were dropped. XLX reflectors
  the registry had not seen for 30 days were treated as inactive and omitted
  (`STALE_AFTER_DAYS` in `hamcall_db/sources/xlx.py`).
- Email addresses were removed from the free-text fields `name`, `sponsor`
  and `description`, and replaced with the marker `[email removed]`.
- Some fields are derived by the project and are not upstream data: `port` for
  D-Star (30001, the DExtra protocol constant, which the registry does not
  publish), `callsign` for XLX rows (the XRF-form name the reflector answers to
  on the DExtra protocol), the matching `aliases` entry, and the provenance
  fields `license` and `alias_source`.
- REF and DCS names from the Pi-Star DPlus and DCS host files are attached as
  `aliases` to the D-Star row at the same address. They never create rows.

Field values are otherwise unmodified. Each file's `modifications` field
carries a shorter form of this statement, covering the reformatting and the
email removal (`MODIFICATION_NOTE` in `hamcall_db/reflectors.py`).

Sysops write contact addresses into their own reflector descriptions, and
upstream publishes them. On a dashboard page that is one address on a page a
person reads. In a bulk JSON file it becomes a list anyone can harvest for
spam. The project already truncates grids to 4 characters and drops street
addresses so the data cannot pinpoint a person, and republishing email
addresses would undo that in another field. The marker stays in place so the
surrounding sentence still reads (`redact_emails` in
`hamcall_db/reflectors.py`).

## DVRef's access terms

DVRef's [Acceptable Use Policy](https://dvref.com/aup/) also governs use of
DVRef's servers, which is separate from the licence on its data. It asks
developers to use the API rather than scrape the website or download from
mirrors, to send a `User-Agent` naming the application, and to authenticate
with a per-account token. The importer, `hamcall_db/sources/dvref.py`, does
all of these (NOTICE 11).

Nothing here gives you access to DVRef's API. If you want to call it, mint
your own token at <https://dvref.com/accounts/token/>. DVRef forbids embedding
a token in a distributed package, which is why this site serves static JSON
that needs no token to read.
