# Where the data comes from

Every upstream, what it covers, and the licence it carries. The authoritative
version — with the exact attribution wording each one requires — is the
[NOTICE](https://github.com/rcludwick/hamcall-db/blob/main/NOTICE) file.

## Reflector directory

| Source | Covers | Licence |
|---|---|---|
| [DVRef](https://dvref.com/) | M17 (`mrefd`), YSF, NXDN, P25, URF, XRF | **CC BY 4.0** |
| [XLX registry](http://xlxapi.rlx.lu/) (LX1IQ) | XLX / D-Star (~890 reflectors) | No explicit licence; public self-registration directory, attributed |

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

## Callsign dataset

| Source | Covers | Licence |
|---|---|---|
| [FCC ULS](https://www.fcc.gov/uls) | US licensees | Public domain |
| [ISED Canada](https://ised-isde.canada.ca/) | Canadian licensees | OGL-Canada 2.0 |
| [ACMA](https://www.acma.gov.au/) | Australian licensees | CC BY 4.0 |
| [AD1C `cty.dat`](https://www.country-files.com/) | DXCC prefix mapping | Free for non-commercial use |
| [AllStarLink](https://www.allstarlink.org/) | Node numbers per callsign | Assumed non-commercial |
| [POTA](https://parksontheair.com/) | Park directory | Assumed non-commercial |
| [SOTA](https://www.sota.org.uk/) | Summit directory | Assumed non-commercial, pending sign-off |
| [PAD-US](https://www.usgs.gov/programs/gap-analysis-project/science/pad-us-data-download) | US park boundaries (build-time only) | Public domain |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) | Non-US park boundaries (build-time only) | **ODbL** — separate file |

`cty.dat` is the reason the combined callsign dataset is non-commercial: it is
free for non-commercial use only, and a derived work inherits that.

Where a licence is recorded as *assumed*, no explicit machine-readable terms
were published upstream and the source is used on an attributed, non-commercial
basis pending human sign-off. Those are flagged in NOTICE rather than quietly
treated as settled.
