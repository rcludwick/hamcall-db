"""The common interface every source importer implements.

Keep I/O confined to importers (download) and the writer. The merge/normalize/geocode
stages downstream are pure functions over iterables of `Record`.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, runtime_checkable

from hamcall_db.models import Record


@runtime_checkable
class Source(Protocol):
    """A national regulator or reference-file importer.

    `name` is the short source tag written into `Record.source` (e.g. "fcc").
    """

    name: str

    def download(self, work_dir: Path) -> Path:
        """Fetch the upstream artifact into `work_dir`, returning the local path.

        Implementations should cache under data/raw/<source>/<YYYY-MM-DD>/ and honor
        If-Modified-Since where the server supports it. Be polite to upstream.
        """
        ...

    def parse(self, path: Path) -> Iterable[Record]:
        """Parse the downloaded artifact into normalized `Record`s."""
        ...
