"""Offline grid geocoder: derive a 4-char Maidenhead locator from a Record's location.

Two pieces:

1. `latlon_to_grid(lat, lon)` — the deterministic Maidenhead core. Returns the field+
   square only (4 chars, e.g. "FN31"). We NEVER emit the 6-char subsquare: the published
   grid is intentionally coarse for privacy (mem-e3fd).

2. `LookupGeocoder` — a `merge.Geocoder` (Record -> str | None). It resolves a record's
   location to a lat/lon using small, checked-in offline seed tables, then truncates to a
   4-char grid. Derivation priority (mem-e3fd): postal_code centroid > city centroid >
   None. Street-level geocoding is the importer's concern upstream, not here.

Which country's table to search
-------------------------------
The seed tables are keyed by the country whose POSTAL SYSTEM an address belongs to, not
by DXCC entity: San Juan's 00901 and Honolulu's 96801 are USPS ZIPs filed under
"United States". A record's ``country`` field is the wrong key for that, for two reasons:

* It is the DXCC entity of the CALLSIGN, filled by cty.dat enrichment AFTER merge() (so
  it is still None when the geocoder runs) and never filled at all on a single-source
  build without ``--cty``.
* Even once filled, the callsign's entity is not where the licensee lives: a KH6 call
  ("Hawaii") with an FCC address in Connecticut, or a W1 call ("United States") living in
  Puerto Rico.

So the geocoder keys on the regulator instead: every address in a source's file is a
mailing address in that regulator's postal system (`SOURCE_POSTAL_COUNTRY`). Only for a
record from an unknown source does it fall back to ``record.country``.

All data is loaded from JSON under ``hamcall_db/data/`` — no network, ever. The shipped
tables are a SMALL seed (enough to exercise the code path); full per-country population is
a follow-up (see the nugget filed from au-76be).
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources

from hamcall_db.models import Record

_FIELD = "ABCDEFGHIJKLMNOPQR"  # 18 fields per axis

# The postal system each regulator's addresses use, spelled as the seed tables spell it.
# FCC covers US territories too (PR, GU, VI, AS, MP); their ZIPs are USPS ZIPs and live
# in the "United States" tables with state codes PR/GU/VI/..., so they need no mapping.
SOURCE_POSTAL_COUNTRY: dict[str, str] = {
    "fcc": "United States",
    "ised": "Canada",
    "acma": "Australia",
}


def latlon_to_grid(lat: float, lon: float) -> str:
    """Return the 4-char Maidenhead locator (field + square) for ``lat``/``lon``.

    Correct in both hemispheres. The result is the coarse field+square only; the 6-char
    subsquare is deliberately never produced. Inputs at the poles / antimeridian are
    clamped so the field index can never overflow past 'R'.
    """
    # Shift into the all-positive Maidenhead coordinate space.
    adj_lon = lon + 180.0
    adj_lat = lat + 90.0

    # Clamp to just inside the upper bounds so 180/90 don't roll into a 19th field.
    adj_lon = min(max(adj_lon, 0.0), 359.999999)
    adj_lat = min(max(adj_lat, 0.0), 179.999999)

    lon_field = int(adj_lon // 20)
    lat_field = int(adj_lat // 10)
    lon_square = int((adj_lon % 20) // 2)
    lat_square = int((adj_lat % 10) // 1)

    return f"{_FIELD[lon_field]}{_FIELD[lat_field]}{lon_square}{lat_square}"


def _norm(value: str | None) -> str:
    return (value or "").strip().upper()


@lru_cache(maxsize=1)
def _postal_table() -> dict[tuple[str, str], tuple[float, float]]:
    """(COUNTRY, POSTAL) -> (lat, lon), loaded once from the seed file."""
    raw = json.loads(
        resources.files("hamcall_db.data").joinpath("postal_centroids.json").read_text()
    )
    out: dict[tuple[str, str], tuple[float, float]] = {}
    for entry in raw:
        key = (_norm(entry["country"]), _norm(entry["postal_code"]))
        out[key] = (float(entry["lat"]), float(entry["lon"]))
    return out


@lru_cache(maxsize=1)
def _city_table() -> dict[tuple[str, str, str], tuple[float, float]]:
    """(COUNTRY, STATE, CITY) -> (lat, lon), loaded once from the seed file.

    STATE may be empty for countries without a province field.
    """
    raw = json.loads(resources.files("hamcall_db.data").joinpath("city_centroids.json").read_text())
    out: dict[tuple[str, str, str], tuple[float, float]] = {}
    for entry in raw:
        key = (_norm(entry["country"]), _norm(entry.get("state")), _norm(entry["city"]))
        out[key] = (float(entry["lat"]), float(entry["lon"]))
    return out


def _postal_country(record: Record) -> str:
    """The (normalized) country whose postal system this record's address uses.

    The source's regulator decides it; ``record.country`` is only a fallback for records
    from a source not in `SOURCE_POSTAL_COUNTRY`. See the module docstring.
    """
    implied = SOURCE_POSTAL_COUNTRY.get(record.source or "")
    return _norm(implied if implied is not None else record.country)


def _postal_key(country: str, postal: str) -> str:
    """Reduce a postal code to the granularity the seed table is keyed at.

    US tables hold 5-digit ZIPs, but FCC ships ZIP+4 run together ("061111234"); Canadian
    tables hold the 3-char Forward Sortation Area ("K1A"), but ISED ships the full code
    ("K1A 0B1"). Other countries are matched as given.
    """
    compact = _norm(postal).replace(" ", "").replace("-", "")
    if country == "UNITED STATES":
        return compact[:5]
    if country == "CANADA":
        return compact[:3]
    return compact


class LookupGeocoder:
    """A `merge.Geocoder`: resolve a Record's location to a 4-char Maidenhead grid.

    Priority: postal_code centroid > city centroid > None. Offline only. Tables are
    searched under the country implied by the record's source (see `_postal_country`).
    """

    def __call__(self, record: Record) -> str | None:
        country = _postal_country(record)

        if record.postal_code:
            coords = _postal_table().get((country, _postal_key(country, record.postal_code)))
            if coords is not None:
                return latlon_to_grid(*coords)

        if record.city:
            coords = _city_table().get((country, _norm(record.state), _norm(record.city)))
            if coords is not None:
                return latlon_to_grid(*coords)

        return None
