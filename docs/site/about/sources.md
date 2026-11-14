# Where the data comes from

*Last reviewed: 2026-11-13. Row counts on this page were read on that date.*

Open `hamcall-db-2026-11-13.parquet` from the
[latest release](https://github.com/rcludwick/hamcall-db/releases/tag/latest)
and look at the `source` column. Every row has one, and it holds a short tag
such as `fcc` or `bnetza`. The tag names the upstream: the organisation that
published the original data. Rows in the reflector directory carry a `source`
tag too, plus a `license` tag for that row.

Each upstream behind those tags is listed below with its number in
[NOTICE](https://github.com/rcludwick/hamcall-db/blob/main/NOTICE). NOTICE
is the file in the repository that records each upstream's terms and its
**attribution**: the credit line the upstream asks you to print when you reuse
its data. NOTICE has the exact wording of each line, and it wins wherever this
page and NOTICE differ. The licences hamcall-db itself applies are on the
[licensing page](licensing.md).

## National registers in the callsign dataset

A national register is the list of amateur licences a country's radio
regulator publishes. Nine registers feed the published callsign dataset. The
importer for each one lives at `hamcall_db/sources/<tag>.py` and is listed in
`SOURCES` in `hamcall_db/build.py`.

| `source` tag | Country | Regulator | Rows (2026-11-13) | Licence | NOTICE |
|---|---|---|--:|---|:-:|
| `fcc` | United States | [FCC ULS](https://www.fcc.gov/uls) | 822,153 | Public domain | 1 |
| `ised` | Canada | [ISED Canada](https://ised-isde.canada.ca/) | 92,229 | ISED's non-commercial reproduction terms | 2 |
| `acma` | Australia | [ACMA](https://www.acma.gov.au/) | 753 | ACMA's *Licence to use the Register of Radiocommunications Licences* | 3 |
| `nkom` | Norway | [Nkom](https://nkom.no/frekvenser-og-elektronisk-utstyr/radioamator) | 7,334 | None stated | 15 |
| `bnetza` | Germany | [Bundesnetzagentur](https://www.bundesnetzagentur.de/amateurfunk) | 69,986 | Official work without copyright (§ 5 UrhG) | 16 |
| `anatel` | Brazil | [Anatel](https://www.anatel.gov.br/dadosabertos/) | 46,660 | Open data, credit the source | 17 |
| `enacom` | Argentina | [ENACOM](https://enacom.gob.ar/listado-de-radioaficionados_p316) | 16,416 | None stated | 18 |
| `ursec` | Uruguay | [URSEC](https://www.gub.uy/unidad-reguladora-servicios-comunicaciones/datos-y-estadisticas/datos/registro-radioaficionados) | 1,337 | Public unless stated otherwise (portal terms) | 19 |
| `uke` | Poland | [UKE](https://amator.uke.gov.pl) | 19,079 | None stated | 20 |

The nine counts add up to 1,075,947, which is every row in
`hamcall-db-2026-11-13.parquet`.

Each register supplies callsigns, licence classes, and some mix of holder
names and locations. What a row carries depends on what the regulator
publishes and on that regulator's terms. The [callsign dataset
page](../callsigns.md) describes the columns. The notes below
cover each register's terms.

**FCC (United States).** ULS is the FCC's Universal Licensing System. The
build downloads its complete amateur extract, `l_amat.zip`
(`hamcall_db/sources/fcc.py`). Works of the US government are public domain
(17 U.S.C. § 105), so no attribution is legally required. NOTICE credits the
FCC anyway and states that the dataset is not endorsed by the FCC.

**ISED (Canada).** Innovation, Science and Economic Development Canada
publishes `amateur.zip` (`hamcall_db/sources/ised.py`). Its terms allow
reproduction "for non-commercial purposes", and commercial redistribution
needs ISED's prior written permission (NOTICE 2). This is why the whole
callsign dataset is non-commercial. POTA's park data, below, is also used
non-commercially (NOTICE 6). Open Government Licence – Canada is not
used, because it excludes personal information. The build publishes only the
first three characters of each postal code, the FSA (forward sortation area,
such as `K1G`).

**ACMA (Australia).** The Australian Communications and Media Authority
publishes the Register of Radiocommunications Licences as `spectra_rrl.zip`
(`hamcall_db/sources/acma.py`). Its licence is ACMA's own, not Creative
Commons. Clause 8 forbids reproducing a natural person's details in a
derived work, so licensees who are people appear by callsign only. Clubs and
other organisations are published in full (NOTICE 3).

**Nkom (Norway).** Nasjonal kommunikasjonsmyndighet publishes a CSV list of
Norwegian callsigns (`hamcall_db/sources/nkom.py`). No licence is stated.
Holders with a foreign postal address keep callsign and name, without town or
postcode (NOTICE 15).

**Bundesnetzagentur (Germany).** The German regulator publishes the
*Rufzeichenliste*, German for "callsign list", in six parts
(`hamcall_db/sources/bnetza.py`). The list says it falls under § 5 of the
German copyright act (UrhG), which makes it an official work without
copyright. The same law requires that the source be cited and that entries
not be altered. The build keeps names exactly as printed. The attribution
line includes the list's edition date, which each row stores in `synced_at`
(NOTICE 16).

**Anatel (Brazil).** Agência Nacional de Telecomunicações publishes its
licensed amateur stations in `estacoes_licenciadas.zip`
(`hamcall_db/sources/anatel.py`). Anatel's open-data page releases its data
under an open licence whose only condition is to credit the source (Decreto
nº 8.777/2016, quoted in NOTICE 17).

**ENACOM (Argentina).** Ente Nacional de Comunicaciones publishes an HTML
list of current licences (`hamcall_db/sources/enacom.py`). No licence is
stated (NOTICE 18).

**URSEC (Uruguay).** Unidad Reguladora de Servicios de Comunicaciones
publishes an ODS spreadsheet of amateur permits
(`hamcall_db/sources/ursec.py`). It has no data licence. The portal's terms
say its content is public unless stated otherwise (NOTICE 19).

**UKE (Poland).** Urząd Komunikacji Elektronicznej publishes CSV exports of
amateur permits (`hamcall_db/sources/uke.py`). No licence is stated. UKE
publishes no names for individual permit holders (NOTICE 20).

Two registers can list the same callsign. When that happens, the build keeps
the row from the source higher in `SOURCE_PRECEDENCE`
(`hamcall_db/merge.py`), which runs `fcc`, `ised`, `acma`, `nkom`, `bnetza`,
`anatel`, `enacom`, `ursec`, `nmhh`, `uke` from highest to lowest. (`nmhh` is
listed for local builds made with `--include-restricted`; it is excluded from
every release.)

## Other sources in the callsign dataset

Five more sources add columns or files to the callsign release. Two of them
only shape the data at build time and are not published themselves.

| Source | What it supplies | Licence | NOTICE |
|---|---|---|:-:|
| [AD1C `cty.dat`](https://www.country-files.com/) | The `country` and `dxcc` columns | MIT licence ([country-files.com/copyright](https://www.country-files.com/copyright/)) | 4 |
| [AllStarLink](https://www.allstarlink.org/) | The `allstar_nodes` column, and the AllStar directory file | "User Content" under the [AllStarLink Terms of Use](https://www.allstarlink.org/terms/) (effective 2026-09-22) | 5 |
| [POTA](https://pota.app/) | The POTA parks and park-grid files | No licence or terms published | 6 |
| [PAD-US](https://www.usgs.gov/programs/gap-analysis-project/science/pad-us-data-download) | US park boundaries, used at build time only | Public domain | 7 |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) | Non-US park boundaries, used at build time only | ODbL 1.0, in a separate release | 8 |

**AD1C `cty.dat`.** [DXCC](https://www.arrl.org/dxcc) is the ARRL's list of "entities", the countries and
territories that amateurs count for awards. Each entity has a number. Jim
Reisert, AD1C, maintains `cty.dat`, a file that maps callsign prefixes to
those entities (`hamcall_db/sources/ad1c.py`). The build uses it to fill
`country` and `dxcc` for every row. The MIT licence places no
non-commercial limit on it, but its copyright notice must travel with the
data (NOTICE 4).

**AllStarLink.** AllStarLink is a network of linked repeaters, hotspots and
personal radio links. Each of these is a **node**, and each node has a number
that other nodes dial to connect to it. The build reads AllStarLink's public node list from
`http://allmondb.allstarlink.org/` (`hamcall_db/enrich_allstar.py`) and uses it
twice: as the `allstar_nodes` column in the callsign dataset, and as the
AllStar directory published at
[`api/v1/allstar.json`](https://rcludwick.github.io/hamcall-db/api/v1/allstar.json),
which held 41,616 nodes on 2026-11-13 and has its own
[page](../allstar.md). AllStarLink's terms class node
information as "User Content" (§2), exclude it from the site's CC BY-SA
licence (§8), and permit "reasonable use of published directories, node
lists, and APIs" (§6). The directory file therefore says
`license: "none-stated"` and carries AllStarLink's attribution line
(NOTICE 5).

**POTA.** Parks on the Air is an award programme for operating from parks.
The build reads its park directory from the unofficial API at
`https://api.pota.app/` (`hamcall_db/sources/pota.py`). POTA publishes no
licence or terms (checked 2026-11-02), so the park files are used
non-commercially with attribution (NOTICE 6).

**PAD-US and OpenStreetMap.** A park can span many 4-character grid squares.
To list them, the build can intersect park boundaries with the grid: PAD-US
(the USGS Protected Areas Database of the United States) for US parks, and
OpenStreetMap for the rest. The boundaries themselves are never published,
only the grid squares derived from them. Grids derived from OpenStreetMap go
in a separate ODbL release, `latest-osm`, because ODbL is share-alike (NOTICE
8). The newest `latest-osm` files are dated 2026-11-02. In the 2026-11-13
callsign release, all 38,603 rows of the park-grid file have
`source = pota-point`: the single point POTA gives for each park.

## Excluded from published builds

Three sources have importers in the repository but are excluded from every
release. ARRL LoTW and NMHH Hungary are excluded because their terms forbid
redistribution, and the SOTA summit list is excluded too. They run only in a local
build started with `--include-restricted`, and the output of such a build
must not be redistributed.

- **ARRL LoTW** (NOTICE 9). Logbook of the World's user-activity list would
  fill `uses_lotw` and `lotw_last_activity`. ARRL prohibits redistribution
  without written permission, so these columns are not published: in the
  2026-11-13 release `uses_lotw` is false and `lotw_last_activity` empty on
  every row.
- **SOTA** (NOTICE 10). The Summits on the Air summit list is excluded from
  releases. SOTA publishes no licence, and its API terms forbid commercial
  use.
- **NMHH** (NOTICE 21). Hungary's regulator, Nemzeti Média- és Hírközlési
  Hatóság, publishes a call sign book, but its site terms forbid saving the
  content into a database or distributing it without written permission.
  `nmhh` is in `RESTRICTED_SOURCES` in `hamcall_db/build.py`, so `--all`
  skips it and the release workflow never includes it.

## Reflector directory

The reflector directory is a separate dataset: a list of digital-voice
reflectors, the internet servers that radios and hotspots link to. It is
published as JSON under
[`api/v1/`](https://rcludwick.github.io/hamcall-db/api/v1/index.json) and is
described on the [reflector directory page](../reflectors/index.md). Four
upstreams feed it. Three of them supply rows; the fourth adds names to
existing rows.

| Source | Networks | Rows (2026-11-13) | Row `license` | NOTICE |
|---|---|--:|---|:-:|
| [DVRef](https://dvref.com/) | M17, YSF, NXDN, P25, URF, DMR | 3,045 | `CC-BY-4.0` | 11 |
| [XLX registry](http://xlxapi.rlx.lu/) | D-Star | 898 | `none-stated` | 12 |
| [Pi-Star DExtra host file](http://www.pistar.uk/downloads/DExtra_Hosts.txt) | D-Star | 53 | `none-stated` | 13 |
| [Pi-Star DPlus and DCS host files](http://www.pistar.uk/downloads/) | D-Star | none (names only) | n/a | 14 |

The row counts come from the live `index.json` and `reflectors.json`, read
2026-11-13. Together the three row sources give 3,996 reflectors.

**DVRef.** DVRef is a reflector directory run by Steve Miller, KC1AWV, and the
DVRef team. It placed its data under CC BY 4.0 in its *Accessing DVRef Data*
announcement of 2026-08-04, which permits mirroring, reformatting, commercial
use, and building "host files, APIs, directories, and other services". The
licence was rechecked at [dvref.com/aup](https://dvref.com/aup/) on
2026-11-02 (NOTICE 11). DVRef retired its own D-Star listings on 2026-08-26,
so it now supplies every network except D-Star.

DVRef's access terms are separate from the data licence. They ask developers
to use the API rather than scrape the site or download from mirrors, to send
a `User-Agent` naming the application, to authenticate with a per-account
token, and to cache unchanged data. The importer, `hamcall_db/sources/dvref.py`,
does all of these. DVRef offers no service-level agreement, so when a fetch
fails the build keeps the last good file rather than publishing an empty list
(`hamcall_db/build_reflectors.py`).

DMR works differently from the other networks. DVRef's DMR endpoint lists
networks, and the build publishes each network's master servers as rows,
tagged with the network in `system`. Each network also has a list of
talkgroups. DVRef allows 60 API requests per hour per account, which is too
few to fetch every list each night. The build refetches the oldest lists
instead, up to 40 a night by design, and serves the result as a rotating
mirror at `api/v1/reflectors/dmr/<system>/talkgroups.json`
(`TALKGROUP_SLICE` in `hamcall_db/sources/dvref.py`). In the 2026-11-13
manifest about four lists a night had been refreshed, so a list can be weeks
old ([API reference](../reflectors/api.md#dmr-talkgroup-files)). On 2026-11-13
`index.json` listed 130 networks and 1,837 talkgroups in the mirror.

**XLX registry.** Luc Engelmann, LX1IQ, runs the registry where XLX
reflectors register themselves. It is the source of record for D-Star
(`hamcall_db/sources/xlx.py`). It publishes no licence, so its rows are
redistributed as factual listings, with attribution, and labelled
`none-stated` (NOTICE 12). Reflectors the registry has not seen for 30 days
are dropped.

**Pi-Star DExtra host file.** Pi-Star is hotspot software maintained by Andy
Taylor, MW0MWZ. Its DExtra host file lists standalone XRF reflectors that the
XLX registry does not (`hamcall_db/sources/dextra.py`). It is the list that
survives for those reflectors: DVRef dropped D-Star, and xrefl.net, the
directory they registered with, is now a parked domain. No licence is published; rows are labelled
`none-stated` (NOTICE 13).

**Pi-Star DPlus and DCS host files.** D-Star has three linking protocols, and
one XLX reflector can answer on all three under three names, such as
`XRF836`, `REF836` and `DCS836`. These two host files supply the REF and DCS
names. The build adds them to existing D-Star rows as `aliases`, matched by
the reflector's address, and marks those rows `alias_source: "pistar"`
(NOTICE 14). On 2026-11-13, 887 rows carried that mark.

## Sources that state no licence

Several upstreams publish their data openly and state no licence: POTA,
AllStarLink, the XLX registry, the Pi-Star host files, Nkom, ENACOM and UKE. The project's
policy, recorded in NOTICE under "Project policy", is to redistribute such
data with attribution, inside whichever licence the published file carries,
and to label it `none-stated` where the format has a place for it. Where an
upstream does state terms, the build follows them. No permission requests
are made. A rights holder who wants data removed can open an issue at
[github.com/rcludwick/hamcall-db/issues](https://github.com/rcludwick/hamcall-db/issues).
