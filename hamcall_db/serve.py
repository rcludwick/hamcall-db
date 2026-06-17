"""Convenience launcher: serve the latest generated hamcall-db SQLite .db with Datasette.

The SQLite artifact (`hamcall-db-YYYY-MM-DD.db`) bundles the `current` and `history`
tables, and Datasette reads SQLite natively. This is a thin wrapper that finds the
newest such file and hands it to the `datasette` CLI.

Usage:
    uv run --group serve hamcall-db-serve [DIR] [-- extra datasette args]

DIR defaults to ``dist/``. Datasette lives in the optional ``serve`` dependency group,
so run it with ``--group serve`` (or sync that group first).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

DEFAULT_DIR = "dist"


def latest_db(directory: Path) -> Path | None:
    """Return the newest ``hamcall-db-*.db`` in `directory`, or None if there are none.

    Dated filenames (``hamcall-db-YYYY-MM-DD.db``) sort lexicographically in
    chronological order, so the last one is the most recent build.
    """
    candidates = sorted(directory.glob("hamcall-db-*.db"))
    return candidates[-1] if candidates else None


def main() -> None:
    argv = sys.argv[1:]
    directory = Path(DEFAULT_DIR)
    if argv and not argv[0].startswith("-"):
        directory = Path(argv[0])
        argv = argv[1:]

    db = latest_db(directory)
    if db is None:
        print(
            f"No hamcall-db-*.db found in {directory}/.\n"
            f"Build one first:  uv run python -m hamcall_db.build --all --out {directory}/",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print(f"Serving {db} with Datasette …", file=sys.stderr)
    try:
        raise SystemExit(subprocess.call(["datasette", str(db), *argv]))
    except FileNotFoundError:
        print(
            "datasette not found. Run with its dependency group:\n"
            "  uv run --group serve hamcall-db-serve",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
