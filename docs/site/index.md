# hamcall-db

*Last reviewed: 2026-11-13. Counts on this page were read from the published
files on that date.*

hamcall-db collects amateur radio reference data from national regulators and
network directories, rebuilds it on a schedule, and publishes the result as
three sets of files. Each one is a file you download, with no account and no
API key. The build code is in the
[repository](https://github.com/rcludwick/hamcall-db) under the
[MIT licence](https://github.com/rcludwick/hamcall-db/blob/main/LICENSE).

<div class="grid cards" markdown>

-   :material-card-account-details: **[Callsign dataset](callsigns.md)**

    One row per callsign, read from nine national licence registers:
    1,075,947 rows in `hamcall-db-2026-11-13.parquet`. It is for logging and
    other ham-radio software that needs the name, town or grid square behind a
    callsign. Published nightly as Parquet and SQLite files on the
    [`latest`](https://github.com/rcludwick/hamcall-db/releases/tag/latest)
    GitHub release (`.github/workflows/release.yml`).

    **Licence:** CC BY-NC 4.0, non-commercial use only
    ([licensing](about/licensing.md#the-callsign-dataset-is-cc-by-nc-40)).

-   :material-radio-tower: **[Reflector directory](reflectors/index.md)**

    3,996 digital-voice reflectors across seven networks (D-Star, DMR, M17,
    YSF, NXDN, P25 and URF), from the live `index.json` on 2026-11-13. It is
    for radio, hotspot and app software that offers a list of reflectors to
    connect to. Static JSON on this site at
    [`api/v1/reflectors.json`](https://rcludwick.github.io/hamcall-db/api/v1/reflectors.json),
    refreshed nightly.

    **Licence:** CC BY 4.0 for DVRef rows, commercial use allowed. XLX and
    Pi-Star rows state no licence and are labelled `none-stated`. Each row's
    `license` field says which
    ([licensing](about/licensing.md#the-reflector-directory-is-cc-by-40-row-by-row)).

-   :material-access-point-network: **[AllStar node directory](allstar.md)**

    AllStarLink's public node list: 41,616 nodes on 2026-11-13, each with its
    node number, owner's callsign, description and location. It is for
    software that needs to know whose node a number belongs to. Static JSON
    on this site at
    [`api/v1/allstar.json`](https://rcludwick.github.io/hamcall-db/api/v1/allstar.json),
    refreshed nightly with the reflectors (`deploy/vps/build.sh`).

    **Licence:** none stated by AllStarLink, labelled `none-stated`;
    attribution required
    ([licensing](about/licensing.md#the-allstar-directory-states-no-licence)).

</div>

## Where to read more

- [Reflector API reference](reflectors/api.md): every field in the reflector
  JSON files, and how often to fetch them.
- [Published files](about/datasets.md): each file, its columns (the AllStar
  node fields included), and what was removed for privacy.
- [Where the data comes from](about/sources.md): every upstream source, with
  its NOTICE number.
- [Licensing](about/licensing.md): what each licence lets you do, and the
  credit lines you must reproduce.

## What the files leave out

- **Street addresses.** The callsign dataset has no street column. Six
  importers (FCC, ISED, ACMA, Nkom, BNetzA and UKE) read the street only to
  check that no published field copies it, then discard it. For every source,
  the merge step blanks a name or town that has the shape of a street address
  (`hamcall_db/address_guard.py`,
  [details](about/datasets.md#no-street-addresses)).
- **Precise locations.** Grid squares stop at 4 characters, such as `DN13`,
  which places a licensee in a region but not at a house
  (`hamcall_db/geocode.py`).
- **Email addresses.** They are removed from the free text of reflector and
  AllStar entries and replaced with `[email removed]`
  ([changes made](about/licensing.md#modifications)).
- **AllStar locations that look like street addresses.** These are blanked.
  AllStar descriptions are published as their owners typed them, minus email
  addresses (`hamcall_db/enrich_allstar.py`).
- **Restricted sources.** ARRL LoTW, SOTA and NMHH Hungary are excluded from
  every release (NOTICE 9, 10 and 21).

The full rules, register by register, are on the
[published files page](about/datasets.md#what-was-removed-for-privacy).
