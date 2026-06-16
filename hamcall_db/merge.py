"""Merge stage: combine per-source `Record` streams into the final deduplicated set.

The ONLY place where source precedence matters. Callsign collisions across countries
are theoretically possible; precedence is codified here once (FCC wins for US prefixes,
etc.) rather than sprinkled through importers. Real logic lands in nugget au-0d18.

Pure function over iterables — no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from hamcall_db.models import Record

# Source precedence, highest first. Used to resolve cross-source callsign collisions.
SOURCE_PRECEDENCE: tuple[str, ...] = ("fcc", "ised", "acma")


def merge(streams: Iterable[Iterable[Record]]) -> Iterator[Record]:
    """Merge multiple per-source record streams, resolving collisions by precedence.

    Stub: au-0d18 implements collision resolution. For now this simply chains streams.
    """
    raise NotImplementedError("merge/normalize lands in au-0d18")
