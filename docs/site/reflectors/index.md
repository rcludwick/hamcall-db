# Reflector directory

*Last reviewed: 2026-11-13. Counts on this page were read from the live
`index.json` on that date.*

This command downloads the manifest of the reflector directory and prints how
many entries each network has:

```bash
curl -s https://rcludwick.github.io/hamcall-db/api/v1/index.json \
  | jq '.networks | map_values(.count)'
```

```json
{
  "dmr": 195,
  "dstar": 951,
  "m17": 151,
  "nxdn": 418,
  "p25": 459,
  "urf": 138,
  "ysf": 1684
}
```

A **reflector** is an internet server that digital-voice radios and hotspots
link to, so that everyone connected to it hears each other. This directory
lists 3,996 of them across seven networks (live `index.json`, 2026-11-13).
It is a set of static JSON files on GitHub Pages: there is no API key, no
account and no server-side query. The full field reference is the
[API reference](api.md), and the machine-readable contract is
[`openapi.json`](https://rcludwick.github.io/hamcall-db/api/v1/openapi.json).

## One entry

Here is one D-Star reflector, copied from `reflectors.json` on 2026-11-13:

```json
{
  "alias_source": "pistar",
  "aliases": ["XRF000", "REF000", "DCS000", "DCS017"],
  "country": "Brazil",
  "dashboard": "http://xlx000.dmr.net.br",
  "description": "AMRASE - Instituto de Radiocomunicação e Pesquisas Aeroespaciais",
  "dial": {"callsign": "XRF000", "host": "201.62.48.60", "kind": "dextra", "port": 30001},
  "id": "XLX000",
  "license": "none-stated",
  "name": "XLX000",
  "network": "dstar",
  "source": "xlx"
}
```

Every entry has the same outer fields, the ones you search and display:
`network`, `id`, `name`, `source` and `license`, plus `aliases`,
`description`, `country`, `sponsor` and `dashboard` where upstream supplies
them. `network` plus `id` identifies an entry. The `dial` object holds what a
client needs to connect, and its shape depends on `dial.kind`, because each
protocol connects differently. An entry with no known address has no `dial`
(`hamcall_db/reflectors.py`).

Many reflectors are split into **modules**: lettered rooms, `A` to `Z`, inside
one server, each carrying its own conversation. Where upstream lists them,
`dial.modules` names them. On 2026-11-13, 89 of the 138 URF entries carried
`modules`.

## Which networks it covers

| Network | Entries (2026-11-13) | `dial.kind` | Where the rows come from |
|---|--:|---|---|
| D-Star | 951 | `dextra` | XLX registry (898), Pi-Star DExtra host file (53) |
| M17 | 151 | `m17` | DVRef |
| YSF (Yaesu System Fusion) | 1,684 | `ysf` | DVRef |
| NXDN | 418 | `nxdn` | DVRef |
| P25 | 459 | `p25` | DVRef |
| URF (urfd multi-protocol reflectors) | 138 | `urf` | DVRef |
| DMR (master servers) | 195 | `mmdvm` | DVRef |

The live counts are in `networks.<name>.count` in
[`index.json`](https://rcludwick.github.io/hamcall-db/api/v1/index.json).
Thirty DMR entries had no `dial` on 2026-11-13, because DVRef listed no
address or port for them. A client that meets a `network` or `dial.kind` it
does not recognise should show the entry and never dial it
([API reference](api.md)).

## Where the rows come from

Three upstreams supply rows, and a fourth adds names to existing rows
(NOTICE sources 11 to 14):

- **DVRef** ([dvref.com](https://dvref.com/), API at
  `https://dvref.com/api/v2/`) supplies every network except D-Star. DVRef
  stopped listing D-Star reflectors on 2026-08-26 (NOTICE source 11).
- **The XLX registry**, maintained by Luc Engelmann, LX1IQ
  (`http://xlxapi.rlx.lu/api.php?do=GetReflectorList`), supplies the XLX
  D-Star reflectors. Reflectors the registry has not seen for 30 days are
  dropped (`STALE_AFTER_DAYS` in `hamcall_db/sources/xlx.py`).
- **The Pi-Star DExtra host file**
  (`http://www.pistar.uk/downloads/DExtra_Hosts.txt`) supplies standalone XRF
  reflectors that the registry does not list (`hamcall_db/sources/dextra.py`).
- **The Pi-Star DPlus and DCS host files** (`http://www.pistar.uk/downloads/`)
  add REF and DCS names to D-Star entries as `aliases`. They add no rows. An
  entry with such names carries `alias_source: "pistar"`, as `XLX000` above
  does.

D-Star has no published port in either upstream. Every D-Star `dial` uses
30001, the DExtra protocol's fixed port (`hamcall_db/sources/xlx.py`).

!!! danger "XRF and XLX are different reflectors"

    `XRF002` and `XLX002` share a number and nothing else. `XRF002` is a
    standalone server; `XLX002` is a host in China. When the author resolved
    14 sampled `XRF###`/`XLX###` pairs on 2026-08-26, 13 of them pointed at
    different servers (`hamcall_db/sources/xlx.py`). The directory publishes
    each under its own `id` and never merges them, and an alias that equals
    another entry's `id` is dropped (`drop_shadowing_aliases` in
    `hamcall_db/reflectors.py`).

## How the directory is refreshed

A build runs every night on a rented Linux server. A systemd timer starts it at 05:30 UTC plus a random delay of up to 20
minutes, and catches up after a reboot
(`deploy/vps/hamcall-db-reflectors.timer`). The script,
`deploy/vps/build.sh`, does this:

1. Fetches every upstream into a fresh, empty work directory and writes the
   JSON files.
2. Refuses to publish if any of the seven networks is missing
   (`deploy/vps/verify.py`).
3. Commits the changed files under `docs/site/api/` to `main` and pushes. If
   nothing changed, it stops without a commit.

The push triggers the GitHub Pages deploy, which publishes the new files
(`.github/workflows/pages.yml`). The same job also builds the
[AllStar node directory](../allstar.md), `allstar.json`, as a separate step
after step 2; if that step fails, the reflectors are published without it
(`deploy/vps/build.sh`).

The build runs on a VPS because it cannot run in GitHub Actions. DVRef sits
behind Cloudflare, which refuses the IP ranges of GitHub-hosted runners: a
valid token and User-Agent still get a 403 before the API sees the request.
The same token works from the VPS (verified 2026-08-26, header comment of
`deploy/vps/build.sh`). The GitHub workflow `reflectors.yml` still exists, but
only for manual runs, and it has no schedule.

Upstream is volunteer-run and offers no service guarantee, so the build keeps
the previous file for a network when:

- its upstream fails entirely,
- it loses every row from one of its sources, or
- it shrinks below 66% of its previous row count (`SHRINK_GUARD` in
  `hamcall_db/build_reflectors.py`).

The `generated` date in each file is the date of the data, not of the build.
A night with no upstream change produces byte-identical files and no commit.
Every file carries `client_refresh_days: 7`: please cache for a week, since
reflector addresses change over weeks, not hours. The manifest's
`expires_hint` is `generated` plus seven days.

## DMR talkgroups

On DMR, an entry is one master server, and `system` names the DMR network it
belongs to. What you talk on is a **talkgroup**: a number that means
something only inside one DMR network. Each network's talkgroup list is
mirrored as its own file, `api/v1/reflectors/dmr/<system>/talkgroups.json`.
The manifest gives each file's URL, `count` and dates under
`networks.dmr.talkgroups`.

DVRef allows 60 requests an hour per account, too few to refetch every list
each night. So the build refetches the lists that were fetched longest ago,
up to 40 a night by design, and skips any list fetched in the last two days
(`TALKGROUP_SLICE` and `TALKGROUP_MIN_AGE_DAYS` in
`hamcall_db/sources/dvref.py`). In practice the rotation is slower. In the
2026-11-13 manifest about four lists a night had been refreshed, so a list can
be weeks old ([API reference](api.md#dmr-talkgroup-files)). Its `fetched` date
in the manifest says when it was last confirmed. On 2026-11-13 the manifest listed 130 DMR networks with a
mirror file, 101 of them with at least one talkgroup, and 1,837 talkgroups in
total.

## Licence

The licence depends on where each row came from, and every row states it in
its own `license` field (`SOURCE_LICENSES` in `hamcall_db/reflectors.py`):

| Rows | `source` | `license` | Meaning |
|---|---|---|---|
| M17, YSF, NXDN, P25, URF, DMR and the talkgroup files | `dvref` | `CC-BY-4.0` | CC BY 4.0: commercial use allowed, with attribution and a statement of changes |
| D-Star rows from the XLX registry | `xlx` | `none-stated` | The registry publishes no licence; the rows are redistributed as facts with attribution |
| Standalone XRF rows from Pi-Star | `dextra` | `none-stated` | Pi-Star publishes no licence; treated the same way |

On 2026-11-13, `reflectors.json` held 3,045 `CC-BY-4.0` rows and 951
`none-stated` rows. To get a set that is wholly CC BY, keep only the rows
whose `license` is `CC-BY-4.0`. A file made only of CC BY rows says
`"license": "CC BY 4.0"` at the top. A file holding any `none-stated` row
(`dstar.json`, `reflectors.json`, `index.json`) says its licence is mixed and
points to the per-row field and NOTICE. Every file also carries its
`attribution` and `modifications` text, so the terms travel with the data.

The attribution lines to reproduce are on the
[licensing page](../about/licensing.md#what-to-credit). The reflector
directory is licensed apart from the [callsign dataset](../callsigns.md),
which is CC BY-NC 4.0; the
[licensing page](../about/licensing.md#why-the-reflector-directory-is-kept-separate)
explains why the two cannot share one licence.

## Parquet and SQLite copies

The `latest-reflectors` release on GitHub holds
`hamcall-db-reflectors-2026-08-26.parquet`,
`hamcall-db-reflectors-2026-08-26.db` and `LICENSE-CC-BY`. It was last
published on 2026-08-26. The nightly build updates only the JSON and does not
publish a release (`deploy/vps/build.sh`), so these files are out of date. For
current data, use the JSON.
