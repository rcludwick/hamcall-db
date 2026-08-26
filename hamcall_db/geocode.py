"""Offline grid geocoder: derive a 4-char Maidenhead locator from a Record's location.

Two pieces:

1. `latlon_to_grid(lat, lon)` — the deterministic Maidenhead core. Returns the field+
   square only (4 chars, e.g. "FN31"). We NEVER emit the 6-char subsquare: the published
   grid is intentionally coarse for privacy (mem-e3fd).

2. `LookupGeocoder` — a `merge.Geocoder` (Record -> str | None). It resolves a record's
   location to a lat/lon using small, checked-in offline seed tables, then truncates to a
   4-char grid. Derivation priority (mem-e3fd): postal_code centroid > city centroid >
   None. Street-level geocoding is the importer's concern upstream, not here.

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


class LookupGeocoder:
    """A `merge.Geocoder`: resolve a Record's location to a 4-char Maidenhead grid.

    Priority: postal_code centroid > city centroid > None. Offline only.
    """

    def __call__(self, record: Record) -> str | None:
        country = _norm(record.country)

        if record.postal_code:
            coords = _postal_table().get((country, _norm(record.postal_code)))
            if coords is not None:
                return latlon_to_grid(*coords)

        if record.city:
            coords = _city_table().get((country, _norm(record.state), _norm(record.city)))
            if coords is not None:
                return latlon_to_grid(*coords)

        return None
