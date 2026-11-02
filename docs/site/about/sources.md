# Where the data comes from

Every upstream, what it covers, and the licence it carries. The authoritative
version — with the exact attribution wording each one requires — is the
[NOTICE](https://github.com/rcludwick/hamcall-db/blob/main/NOTICE) file.

## Reflector directory

| Source | Covers | Licence |
|---|---|---|
| [DVRef](https://dvref.com/) | M17 (`mrefd`), YSF, NXDN, P25, URF, DMR | **CC BY 4.0** |
| [XLX registry](http://xlxapi.rlx.lu/) (LX1IQ) | XLX / D-Star (~890 reflectors) | **No licence stated.** Rows say `license: "none-stated"`; redistributed as facts, attributed |
| [Pi-Star DExtra host file](http://www.pistar.uk/downloads/DExtra_Hosts.txt) | Standalone XRF / D-Star | **No licence stated.** Rows say `license: "none-stated"`; redistributed as facts, attributed |
| [Pi-Star DPlus and DCS host files](http://www.pistar.uk/downloads/) | REF and DCS names in `aliases` (no rows), compiled by Pi-Star from dstargateway.org and xreflector.net DNS | **No licence stated.** Rows carrying them say `alias_source: "pistar"`; attributed |

DVRef placed its data under CC BY 4.0 in its *Accessing DVRef Data* announcement
of 2026-08-04, explicitly permitting mirroring, reformatting, commercial use and
building "host files, APIs, directories, and other services".

Their terms also ask developers to use the **API** rather than scraping the site
or re-downloading from downstream mirrors, to send a meaningful `User-Agent`, and
to cache rather than refetch unchanged data. This project does all three. DVRef
states plainly that it offers **no SLA**, which is why the build keeps the last
good copy rather than publishing an empty list when a fetch fails.

For D-Star the XLX registry is the source of record: DVRef's XRF list carries
~61 reflectors where the registry carries ~890.

DMR comes from the same API but a different endpoint, which lists networks rather
than reflectors. Each of its **servers** becomes a row, tagged with the network it
belongs to; the per-network **talkgroup** lists are linked, not mirrored, because
fetching 172 of them does not fit in a 60-request hourly budget.

## Callsign dataset

| Source | Covers | Licence |
|---|---|---|
| [FCC ULS](https://www.fcc.gov/uls) | US licensees | Public domain |
| [ISED Canada](https://ised-isde.canada.ca/) | Canadian licensees | ISED's non-commercial reproduction terms (not OGL-Canada, which excludes personal information); postal codes published as the 3-character FSA |
| [ACMA](https://www.acma.gov.au/) | Australian licensees | ACMA's *Licence to use the Register of Radiocommunications Licences* (not Creative Commons); natural persons are callsign-only, organisations and clubs in full |
| [AD1C `cty.dat`](https://www.country-files.com/) | DXCC prefix mapping | MIT licence ([country-files.com/copyright](https://www.country-files.com/copyright/)) |
| [AllStarLink](https://www.allstarlink.org/) | Node numbers per callsign | "User Content" under the AllStarLink Terms of Use (2026-09-22), used as "reasonable use of published directories"; confirmation requested |
| [POTA](https://parksontheair.com/) | Park directory | No licence or terms published; used non-commercially with attribution while permission is requested |
| [ARRL LoTW](https://lotw.arrl.org/) | Last-upload date per callsign | Redistribution prohibited without ARRL's written permission; **disabled in published builds** |
| [SOTA](https://www.sota.org.uk/) | Summit directory | No licence; the API terms forbid commercial use; **disabled in published builds** |
| [PAD-US](https://www.usgs.gov/programs/gap-analysis-project/science/pad-us-data-download) | US park boundaries (build-time only) | Public domain |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) | Non-US park boundaries (build-time only) | **ODbL** — separate file |

ISED's terms are the reason the combined callsign dataset is non-commercial:
they allow reproduction "for non-commercial purposes", and commercial
redistribution needs ISED's prior written permission. POTA, which publishes no
terms, is also used non-commercially. `cty.dat` is MIT-licensed and imposes no
non-commercial restriction.

Where a source publishes no terms (POTA), or its terms are being confirmed
(AllStarLink), NOTICE says so and records the permission request rather than
treating the basis as settled. LoTW and SOTA run only in opt-in local builds
(`--include-restricted`), whose output must not be redistributed.
