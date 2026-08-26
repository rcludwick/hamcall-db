# Callsign dataset

A normalized amateur licensee dataset aggregated from national regulators, plus
separate reference datasets for POTA parks and SOTA summits.

Unlike the [reflector directory](reflectors/index.md), this is **not served from
this site** — it is far too large for a static host. It is published as Parquet
and SQLite through
[GitHub Releases](https://github.com/rcludwick/hamcall-db/releases), rebuilt
weekly.

```bash
gh release download latest --repo rcludwick/hamcall-db --pattern '*.parquet'
```

Pull `latest` by default; pin to a dated `hamcall-db-YYYY-MM-DD` release when
reproducibility matters.

## Coverage

| Source | Records |
|---|---|
| FCC ULS (United States) | ~1M |
| ISED (Canada) | ~80k |
| ACMA (Australia) | ~80k amateur records |

The on-demand long tail — UK, EU, JA and the rest — is deliberately out of
scope. Consumer tools should fall through to a per-callsign API like QRZ or
HamQTH for those.

## Privacy rules, which are not negotiable

* **No street addresses in published output.** Ever. They are consumed at build
  time for geocoding and then discarded.
* **Grid is 4-character only** (e.g. `DN13`), never the 6-character subsquare.
  Enough to place an operator in a region; not enough to place them at a house.

Those rules apply to *people*. They do not apply to public landmarks — POTA park
and SOTA summit coordinates are stored verbatim, because a mountain has no
privacy interest.

## Companion datasets

**POTA parks** and **SOTA summits** ship as separate additive files — places are
not licensees, so they do not touch the callsign schema. Park boundaries are
resolved to the *set* of grid squares each park actually intersects, rather than
one indicative point.

!!! warning "The OpenStreetMap-derived park grids are ODbL, and separate"

    Non-US park boundaries come from OpenStreetMap, which is **share-alike**.
    Those grids ship in their own file under their own licence and are never
    mixed into the CC BY-NC artifacts. Joining them to anything else and
    redistributing the result makes that result ODbL. See
    [Licensing](about/licensing.md).

## Licence

**CC BY-NC 4.0** — non-commercial, inherited from `cty.dat` and other upstreams.
Redistributing it means satisfying every upstream attribution simultaneously; the
simplest way to comply is to ship the
[NOTICE](https://github.com/rcludwick/hamcall-db/blob/main/NOTICE) file alongside
it.
