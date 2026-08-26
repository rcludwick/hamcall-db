# Licensing

Three licences are in play here, and they cover different things. Combining the
datasets is a decision to make knowingly rather than by accident.

| What | Licence | Commercial use |
|---|---|---|
| Build code (this repository) | **MIT** | yes |
| [Reflector directory](../reflectors/index.md) | **CC BY 4.0** | yes |
| [Callsign dataset](../callsigns.md), POTA parks, SOTA summits | **CC BY-NC 4.0** | no |
| OpenStreetMap-derived park grids | **ODbL 1.0** | yes, but share-alike |

## Why the reflector directory is kept separate

It would be tidier to publish one dataset. It would also be a licence violation.

DVRef's reflector data is CC BY 4.0. Section 2(a)(5)(B) of that licence forbids a
redistributor from offering or imposing *"any additional or different terms or
conditions on ... the Licensed Material if doing so restricts exercise of the
Licensed Rights."*

The callsign dataset is CC BY-NC — non-commercial. Folding the reflector data
into it would impose a non-commercial restriction that CC BY 4.0 specifically
grants away. So the reflector artifacts are their own files, with their own
`LICENSE-CC-BY`, their own NOTICE entries and their own release.

This is the same discipline applied to the OpenStreetMap park grids, pointing the
other way: **OSM is more restrictive** than this project (share-alike) and **DVRef
is less** (commercial permitted). Either way, mixing loses information about what
you are allowed to do, so the files stay apart.

!!! warning "Joining them is your call, and it has consequences"

    Nothing stops you combining these datasets locally — that is what they are
    for. But redistributing the *combination* means the result carries the union
    of the restrictions: join the ODbL grids to anything and redistribute it, and
    that database is ODbL; join the CC BY-NC callsign data to the reflector
    directory and the result is non-commercial.

## Attribution

Required when redistributing the reflector directory, verbatim:

```
Reflector data provided by DVRef — https://dvref.com/
XLX reflector data from the XLX registry maintained by Luc Engelmann, LX1IQ
(http://xlxapi.rlx.lu/).
```

Every published file also carries `license`, `attribution` and `modifications`
inline, so an attribution that lives only in a README cannot get separated from
the data it belongs to.

## Modifications

CC BY 4.0 requires stating what was changed. For the reflector directory:

* Upstream rows were **reformatted** into this project's common schema — field
  names and structure differ; combined into one set per network.
* Entries **lacking a resolvable address** were dropped, and XLX registrations
  not seen by the registry in over 30 days were treated as inactive and omitted.
* **Email addresses were removed** from the free-text fields (`name`, `sponsor`,
  `description`) and replaced with the marker `[email removed]`.
* Three fields are **derived** rather than upstream: `port` for D-Star (30001,
  the DExtra protocol constant, which the registry does not publish), `callsign`
  for XLX rows (the XRF-form name the reflector answers to on the wire), and the
  matching `aliases` entry.

Field values are otherwise unmodified.

### Why the email addresses go

Sysops write contact addresses into their own reflector blurbs, and upstream
publishes them. Those are already public — but a rendered dashboard page and a
bulk-downloadable machine-readable JSON file are not the same exposure. The
second is a harvesting list.

It also cuts against this project's own posture. Person grids are truncated to 4
characters and street addresses are banned outright specifically so published
data cannot pinpoint an individual; republishing personal email addresses would
undo that in a different field. The marker is left in place rather than deleting
silently, so the surrounding sentence still reads.

## Access terms are not licence terms

DVRef's [Acceptable Use Policy](https://dvref.com/aup/) governs use of *their
infrastructure*, separately from the licence on the data. It is why this project
pulls from their API with an identifying `User-Agent` and a per-account token
rather than scraping their site or re-downloading from third-party mirrors.

Nothing here grants you access to that API. Mint your own token at
<https://dvref.com/accounts/token/> — and note their terms forbid embedding one
in a distributed package, which is exactly why what this site serves is static
JSON that needs no token to read.
