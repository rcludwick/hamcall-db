"""Shared GDAL/pyogrio reader gate for the boundary adapters (padus, osm).

The PAD-US and OSM adapters each fetch a LARGE boundary file (a ~1.7 GB PAD-US File
Geodatabase; multi-GB Geofabrik OSM extracts) and read it with pyogrio. pyogrio is an
OPT-IN build dependency (the ``padus`` / ``osm`` groups); a routine build (``uv sync``)
does not install it.

Probe for the reader with :func:`require_pyogrio` *before* downloading, so a build without
the group skips the huge download and degrades to point grids — instead of pulling gigabytes
only to fail on the read. (Regression hdb-66be: a release build hung ~13 min downloading
~10 GB of OSM extracts before the read finally failed on the missing reader.)
"""

from __future__ import annotations


def require_pyogrio(group: str) -> None:
    """Raise an actionable ``RuntimeError`` if the GDAL-backed reader is unavailable.

    ``group`` is the ``uv sync --group <group>`` extra that installs pyogrio for the calling
    adapter (``padus`` or ``osm``). Returns ``None`` when the reader is importable.
    """
    try:
        import pyogrio  # noqa: F401
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Reading boundary files requires the GDAL-backed reader 'pyogrio'. "
            f"Install it with `uv sync --group {group}`. (The geometry core and its tests "
            "do not need it.)"
        ) from exc
