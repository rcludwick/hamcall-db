# Offline centroid lookup tables

These JSON files back `hamcall_db.geocode.LookupGeocoder`. They map a record's
postal code or city to an approximate lat/lon, which the geocoder then truncates to a
**4-char Maidenhead grid** (mem-e3fd — never 6-char). No network access, ever.

## Files

| File | Key | Schema |
| --- | --- | --- |
| `postal_centroids.json` | `(country, postal_code)` | `[{ "country", "postal_code", "lat", "lon", "place" }]` |
| `city_centroids.json`   | `(country, state, city)` | `[{ "country", "state", "city", "lat", "lon" }]` |

The loader (`geocode.py`) upper-cases and strips keys, so casing in the data is
cosmetic. `state` may be `""` for countries without a province field. `place` in the
postal file is a human-readable comment only; the loader ignores it.

Derivation priority in the geocoder: postal_code centroid > city centroid > NULL.

## Coverage (au-63fb)

Production-grade-ish coverage for the three regulator sources this project ingests:

- **United States (FCC ULS):** state capitals + major cities for all 50 states, plus
  DC and PR (city table); one representative ZIP centroid per ZIP region 0xxxx–9xxxx
  plus notable hubs (postal table). Coarse-but-correct at the 4-char grid scale.
- **Canada (ISED):** all 10 provinces + 3 territories — capital + major cities (city
  table); one FSA (3-char forward sortation area) centroid per province/territory
  (postal table).
- **Australia (ACMA):** all 6 states + 2 territories — capital + major cities (city
  table); capital + representative regional postcodes (postal table).

These are intentionally coarse: at a ~140 km × 70 km grid cell, a city-centroid
approximation lands in the correct cell for the overwhelming majority of holders.

## Data sources & licensing

All coordinates are drawn from **public-domain / openly-licensed** references and are
city/region centroids only — no proprietary or unclearly-licensed bulk dataset is
vendored here:

- **U.S. cities & ZIP regions:** U.S. Census Bureau Gazetteer / ZCTA centroids —
  **public domain** (work of the U.S. federal government).
- **Canadian cities & FSA centroids:** public-domain municipal coordinates; FSA prefix
  centroids approximated to the principal city of each FSA.
- **Australian capitals/regions:** public-domain municipal coordinates.

Because every coordinate here is public domain, **no new NOTICE entry is required** for
these tables (mem-371f). The regulator licenses in NOTICE govern the *licensee* data,
not these geographic centroids.

## Follow-up: full population

This table is a curated, well-distributed subset — **not** every ZIP / FSA / postcode.
Full per-code population (every U.S. ZCTA, every Canadian FSA, every AU postcode) needs
a bulk dataset (e.g. the GeoNames `postal-codes` export, CC-BY 4.0, or the Census ZCTA
gazetteer). Do **not** commit gigabytes into git. Recommended approach for the
follow-up nugget:

1. A build-time fetch+cache step pulling the bulk file into `data/raw/<source>/<date>/`.
2. A small generator that compacts it to this exact JSON schema (or a Parquet sidecar),
   computing centroids and rounding coordinates.
3. If a derived table is checked in, keep it compacted; otherwise regenerate on build.

When adding a bulk source, **re-check its license and add it to NOTICE** before
vendoring any derived artifact (GeoNames is CC-BY 4.0 — attribution required).
