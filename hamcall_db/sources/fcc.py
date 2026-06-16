"""FCC ULS importer (US).

Skeleton only. The real download + EN/AM/HD .dat parsing lands in nugget au-039b.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from hamcall_db.models import Record


class FccUlsSource:
    """Importer for the FCC Universal Licensing System amateur bulk extract."""

    name = "fcc"

    def download(self, work_dir: Path) -> Path:
        raise NotImplementedError("FCC ULS download lands in au-039b")

    def parse(self, path: Path) -> Iterable[Record]:
        raise NotImplementedError("FCC ULS parse lands in au-039b")
