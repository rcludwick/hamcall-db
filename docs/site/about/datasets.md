# Data & licences

Everything this project publishes, in one place: what each dataset is, the
formats it ships in, where to fetch it, and **the licence that governs that
dataset specifically**.

There is no single licence for "hamcall-db". The upstreams carry different
terms, so the outputs do too — and datasets whose terms conflict are published
as separate files, in separate releases, on purpose. See
[Licensing](licensing.md) for why that separation is a legal requirement rather
than filing tidiness.

The authoritative text — every upstream source, its licence, and the exact
wording each one requires — is the
[NOTICE](https://github.com/rcludwick/hamcall-db/blob/main/NOTICE) file in the
repository. Where this page quotes an attribution statement it is copied from
there verbatim; where NOTICE records a licence as unclear, this page says so
rather than rounding it up.

| Dataset | Published as | Where | Licence |
|---|---|---|---|
| [Callsign dataset](#callsign-dataset) | Parquet, SQLite | `latest` release | **CC BY-NC 4.0** |
| [Callsign history](#callsign-dataset) | Parquet | `latest` release | **CC BY-NC 4.0** |
| [POTA park directory](#pota-park-directory) | Parquet, SQLite table | `latest` release | **CC BY-NC 4.0** |
| [POTA park grid sets (US)](#pota-park-grid-sets-us-pad-us) | Parquet, SQLite table | `latest` release | **CC BY-NC 4.0** |
| [POTA park grid sets (international)](#pota-park-grid-sets-international-openstreetmap) | Parquet, SQLite | `latest-osm` release | **ODbL 1.0** |
| [Reflector directory](#reflector-directory) | Static JSON, Parquet, SQLite | this site + `latest-reflectors` release | **CC BY 4.0** for DVRef rows; XLX/Pi-Star rows no licence stated (per-row `license`) |
| [SOTA summits](#not-published-sota-summits-and-lotw-activity) | Parquet, SQLite table | not published — opt-in local builds only | no licence; SOTA's API terms forbid commercial use |
| [LoTW activity columns](#not-published-sota-summits-and-lotw-activity) | columns on the callsign dataset | not published — opt-in local builds only | redistribution prohibited without ARRL's written permission |
| [Build code](#build-code) | this repository | GitHub | **MIT** |

`YYYY-MM-DD` in an asset name is the build date. Every release also has a
rolling alias — `latest`, `latest-osm`, `latest-reflectors` — that points at the
most recent build of that dataset; pin to a dated tag when reproducibility
matters.

---

## Callsign dataset

Normalized amateur licensee records from national regulators, plus the SCD2
change history. Full description: [Callsign dataset](../callsigns.md).

**Files** — [`latest`](https://github.com/rcludwick/hamcall-db/releases/tag/latest)
release, tag `hamcall-db-YYYY-MM-DD`:

* `hamcall-db-YYYY-MM-DD.parquet` — current state; the schema is the
  redistribution contract.
* `hamcall-db-history-YYYY-MM-DD.parquet` — holder/location change history.
* `hamcall-db-YYYY-MM-DD.db` — SQLite convenience copy carrying the callsign
  tables plus `pota_parks` and `pota_park_grids` in one file.

```bash
gh release download latest --repo rcludwick/hamcall-db --pattern '*.parquet'
```

**Licence: CC BY-NC 4.0** —
<https://creativecommons.org/licenses/by-nc/4.0/>. Non-commercial. The dataset
is a derived work of several upstreams and is distributed under the **union** of
their terms, so redistributing it means satisfying every attribution below at
once. The simplest way to comply is to ship the NOTICE file alongside it.

The non-commercial term is inherited from ISED, whose terms allow reproduction
"for non-commercial purposes" and require prior written permission for
commercial redistribution. A derived work cannot grant more than it was given.
POTA, which publishes no terms, is also used non-commercially. `cty.dat` is not
the reason: it is MIT-licensed.

Two upstreams shape what the rows contain. Canadian postal codes are published
as the 3-character forward sortation area (FSA, e.g. `K1G`). Under the ACMA
licence, Australian licensees who are natural persons are published as callsign,
licence category, dates and client type only; organisations and clubs are
published in full.

**Required attribution**, verbatim:

```
Canadian Amateur Call Sign List, Innovation, Science and Economic
Development Canada. Adapted from the version available at
https://apc-cap.ic.gc.ca/datafiles/amateur.zip. Not an official version
and not endorsed by ISED.

Based on Australian Communications and Media Authority information

Country (CTY) files © Jim Reisert, AD1C, https://www.country-files.com/

AllStarLink node data courtesy of AllStarLink, Inc.
(https://www.allstarlink.org/).
```

`cty.dat` is MIT-licensed, so its copyright and permission notice must also
travel with the data; NOTICE reproduces it in full (source 4).

FCC ULS records are a US Government work and public domain (17 U.S.C. § 105);
no attribution is legally required and the source is credited as a courtesy.

!!! note "AllStarLink node numbers are User Content, used as a published directory"

    AllStarLink's Terms of Use (effective 2026-09-22) treat information about
    users' nodes as "User Content", which the site's CC BY-SA 4.0 licence does
    not cover. The node numbers are used under the terms' allowance for
    "reasonable use of published directories", and AllStarLink has been asked
    to confirm that. The node column is supplementary and can be dropped
    without affecting the rest of the dataset.

## POTA park directory

POTA park references, names, region descriptors and one indicative
representative point per park. Parks are places rather than licensees, so this
is an additive dataset that never touches the callsign schema.

**Files** — same [`latest`](https://github.com/rcludwick/hamcall-db/releases/tag/latest)
release:

* `hamcall-db-pota-parks-YYYY-MM-DD.parquet`
* the `pota_parks` table inside `hamcall-db-YYYY-MM-DD.db`

**Licence: CC BY-NC 4.0**, as part of the combined dataset above.

**Required attribution**, verbatim:

```
Park data courtesy of Parks On The Air (POTA), https://pota.app/.
```

!!! note "POTA's terms are unstated, not granted"

    POTA publishes no licence or terms of any kind for the park directory,
    and the API it comes from is unofficial and undocumented. The data is used
    **non-commercially, with attribution**, under the project's policy that
    openly published data with no stated licence is usable. The representative coordinate and grid are **indicative only**: a
    large park spans many grid squares, so the value is display-only and never a
    join key.

## POTA park grid sets (US, PAD-US)

The *set* of 4-character Maidenhead grid squares each US park's boundary
intersects, one row per (park, grid) — because a large park is not one grid
square.

**Files** — same [`latest`](https://github.com/rcludwick/hamcall-db/releases/tag/latest)
release:

* `hamcall-db-pota-park-grids-YYYY-MM-DD.parquet`
* the `pota_park_grids` table inside `hamcall-db-YYYY-MM-DD.db`

**Licence: CC BY-NC 4.0**, as part of the combined dataset above.

Boundaries come from **PAD-US** (USGS Gap Analysis Project), a US Government
work and public domain: no attribution is legally required, and USGS is credited
as a courtesy. Because the source is public domain it adds no restriction to the
combined dataset. The polygons are used at build time only and are never
redistributed — only the derived grid sets are published.

!!! warning "Currently indicative point grids"

    The public nightly build runs without the build-time GIS dependency groups,
    so the grid sets in the released artifacts are single-point fallbacks rather
    than real polygon coverage. Treat them as approximate until polygon coverage
    ships.

## POTA park grid sets (international, OpenStreetMap)

The same grid-set treatment for non-US parks, derived from OpenStreetMap
boundaries. US parks come from the public-domain PAD-US set above and are
excluded here, so the two never overlap.

**This is a separate release under a separate licence** —
[`latest-osm`](https://github.com/rcludwick/hamcall-db/releases/tag/latest-osm),
tag `hamcall-db-osm-YYYY-MM-DD`:

* `hamcall-db-pota-park-grids-osm-YYYY-MM-DD.parquet`
* `hamcall-db-pota-park-grids-osm-YYYY-MM-DD.db` — a **separate** SQLite file,
  table `pota_park_grids_osm`
* `LICENSE-ODbL` — shipped with the assets so the terms cannot get separated
  from the data

```bash
gh release download latest-osm --repo rcludwick/hamcall-db
```

**Licence: Open Database License (ODbL) v1.0** —
<https://opendatacommons.org/licenses/odbl/1-0/>. Commercial use is permitted;
the licence is **share-alike**.

**Required attribution**, verbatim:

```
© OpenStreetMap contributors (https://www.openstreetmap.org/copyright).
Data available under the Open Database License (ODbL) v1.0,
https://opendatacommons.org/licenses/odbl/1-0/. Extracts processed by
Geofabrik GmbH.
```

!!! warning "Joining this to the CC BY-NC artifacts has consequences"

    Grids derived from OSM boundaries are an ODbL **derivative database**, and
    ODbL's share-alike is incompatible with CC BY-NC. That is why they ship in
    their own files, in their own release, and are never mixed in. A consumer
    who joins them to the CC BY-NC artifacts and redistributes the result as a
    database makes that combined database ODbL. Keep them apart to keep the
    CC BY-NC dataset free of share-alike.

    The OSM polygons themselves are used at build time only and are never
    redistributed.

## Reflector directory

Digital-voice reflectors for D-Star, M17, YSF, NXDN, P25, URF and DMR. This is the
one dataset served **from this site**, as static JSON needing no token, because
a reflector picker in a client app needs a URL it can just fetch. Full
description: [Reflector directory](../reflectors/index.md); the contract is the
[API reference](../reflectors/api.md).

**Static JSON API**, rebuilt nightly, cached for 7 days by request:

| Endpoint | What it is |
|---|---|
| [`api/v1/index.json`](../api/v1/index.json) | Service manifest: networks, row counts, freshness. |
| [`api/v1/reflectors.json`](../api/v1/reflectors.json) | Every reflector, every network. |
| [`api/v1/reflectors/dstar.json`](../api/v1/reflectors/dstar.json) | D-Star only. |
| [`api/v1/reflectors/m17.json`](../api/v1/reflectors/m17.json) | M17 only. |
| [`api/v1/reflectors/ysf.json`](../api/v1/reflectors/ysf.json) | YSF (Fusion) only. |
| [`api/v1/reflectors/nxdn.json`](../api/v1/reflectors/nxdn.json) | NXDN only. |
| [`api/v1/reflectors/p25.json`](../api/v1/reflectors/p25.json) | P25 only. |
| [`api/v1/reflectors/urf.json`](../api/v1/reflectors/urf.json) | URF only. |
| [`api/v1/reflectors/dmr.json`](../api/v1/reflectors/dmr.json) | DMR only — one row per master server. |
| [`api/v1/openapi.json`](../api/v1/openapi.json) | The contract, machine-readable. |

```bash
curl -s https://rcludwick.github.io/hamcall-db/api/v1/reflectors.json
```

**Bulk files** —
[`latest-reflectors`](https://github.com/rcludwick/hamcall-db/releases/tag/latest-reflectors)
release:

* `hamcall-db-reflectors-YYYY-MM-DD.parquet` — one row per (network, id)
* `hamcall-db-reflectors-YYYY-MM-DD.db` — the same rows, SQLite table
  `reflectors`
* `LICENSE-CC-BY` — the governing licence, shipped with the assets

```bash
gh release download latest-reflectors --repo rcludwick/hamcall-db
```

**Licence: per row.** Rows from DVRef (`license: "CC-BY-4.0"`) and hamcall-db's
own contributions are CC BY 4.0 —
<https://creativecommons.org/licenses/by/4.0/>; **commercial use is permitted**,
and attribution and a statement of modification are required. Rows from the XLX
registry and the Pi-Star host files (`license: "none-stated"`) come from
publishers who state no licence: they are redistributed as facts, and no rights
are granted beyond those the upstream provides. Files holding both kinds say
`"license": "mixed — see per-row license and NOTICE"`. Every emitted file
carries `license`, `attribution` and `modifications` inline so the terms cannot get separated from the data. What was
changed is listed under [Modifications](licensing.md#modifications).

This dataset is **never** merged into the CC BY-NC artifacts. Doing so would
impose a non-commercial restriction on the DVRef rows that CC BY 4.0 grants away — see
[Licensing](licensing.md#why-the-reflector-directory-is-kept-separate).

**Required attribution**, verbatim:

```
Reflector data provided by DVRef — https://dvref.com/

XLX reflector data from the XLX registry maintained by Luc Engelmann,
LX1IQ (http://xlxapi.rlx.lu/).

Standalone XRF reflector data from the Pi-Star DExtra host file
(http://www.pistar.uk/downloads/DExtra_Hosts.txt).

REF and DCS reflector names from the Pi-Star DPlus and DCS host files
(http://www.pistar.uk/downloads/), which compile them from
dstargateway.org and xreflector.net DNS.
```

!!! note "Only DVRef's half of this is an explicit licence grant"

    DVRef placed its data under CC BY 4.0 in its *Accessing DVRef Data*
    announcement of 2026-08-04. The other three sources publish **no explicit
    licence or terms at all**: the XLX registry and the Pi-Star host files are
    publicly served directories of reflector addresses, published for exactly
    this purpose and redistributed by hotspot software generally. Their rows
    are labelled `license: "none-stated"`, redistributed as facts with
    attribution, and no rights are granted in them beyond what those upstreams
    provide. Permission has been requested from each of them.

!!! info "Access terms are not licence terms"

    DVRef's [Acceptable Use Policy](https://dvref.com/aup/) governs use of
    *their infrastructure* and is separate from the licence on the data.
    Nothing here grants you access to their API — mint your own token at
    <https://dvref.com/accounts/token/>, and note their terms forbid embedding
    one in a distributed package. That is why what this site serves is static
    JSON that needs no token to read.

## Not published: SOTA summits and LoTW activity

Two datasets the build can produce but the **published release never contains**.
Both are gated behind `--include-restricted`, which the release workflow does
not pass, because neither upstream permits redistribution.

**SOTA summits** — `hamcall-db-sota-summits-YYYY-MM-DD.parquet` plus a
`sota_summits` table, built from SOTA's static bulk `summitslist.csv`
(deliberately not the gated JSON API, whose terms are explicitly
non-commercial, registration-gated, and prohibit AI-generated software from
connecting). The static file carries no licence at all, and SOTA's API terms
forbid commercial use. **It is disabled in published builds**, and NOTICE
requires confirmation from the SOTA Management Team before it is ever
published.

```
Summit data courtesy of Summits on the Air (SOTA),
https://www.sota.org.uk/.
```

**LoTW activity** — the `uses_lotw` and `lotw_last_activity` columns, from
ARRL's public user-activity list. **Redistribution is prohibited without
ARRL's written permission:** ARRL's Terms of Use allow personal, non-commercial
use only, "not for further reproduction, publication, or distribution". The
columns ship empty in the official artifact.

```
LoTW user-activity data courtesy of the American Radio Relay League
(ARRL), Logbook of the World (https://lotw.arrl.org/).
```

You can populate either in a personal, local build. Do not redistribute what
comes out.

## Build code

The Python build pipeline in this repository is licensed **MIT**, separately
from everything it produces, and is unaffected by the datasets' terms. The
converse also holds: the MIT licence on the code grants nothing over the data.

## Attribution, all of it

If you redistribute more than one of these datasets, this is the full set of
statements required across all of them — but check which ones actually apply to
what you are shipping, since they are not all the same licence:

```
Canadian Amateur Call Sign List, Innovation, Science and Economic
Development Canada. Adapted from the version available at
https://apc-cap.ic.gc.ca/datafiles/amateur.zip. Not an official version
and not endorsed by ISED.

Based on Australian Communications and Media Authority information

Country (CTY) files © Jim Reisert, AD1C, https://www.country-files.com/

AllStarLink node data courtesy of AllStarLink, Inc.
(https://www.allstarlink.org/).

Park data courtesy of Parks On The Air (POTA), https://pota.app/.

© OpenStreetMap contributors (https://www.openstreetmap.org/copyright).
Data available under the Open Database License (ODbL) v1.0,
https://opendatacommons.org/licenses/odbl/1-0/. Extracts processed by
Geofabrik GmbH.

Reflector data provided by DVRef — https://dvref.com/

XLX reflector data from the XLX registry maintained by Luc Engelmann,
LX1IQ (http://xlxapi.rlx.lu/).

Standalone XRF reflector data from the Pi-Star DExtra host file
(http://www.pistar.uk/downloads/DExtra_Hosts.txt).

REF and DCS reflector names from the Pi-Star DPlus and DCS host files
(http://www.pistar.uk/downloads/), which compile them from
dstargateway.org and xreflector.net DNS.
```

Redistributing the
[NOTICE](https://github.com/rcludwick/hamcall-db/blob/main/NOTICE) file
unmodified alongside the data satisfies all of it at once, and is what NOTICE
itself recommends.
