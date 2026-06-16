"""CLI entry point for the build pipeline.

Run via ``python -m hamcall_db.build`` or the ``hamcall-db`` console script.

This is a skeleton: it wires up source selection, output paths, and the writer. The
download/parse/merge/geocode stages raise NotImplementedError until their own nuggets
land (importers: au-039b/f694/2fba/9ed1; merge: au-0d18).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated

import typer

from hamcall_db.merge import merge
from hamcall_db.sources.base import Source
from hamcall_db.sources.fcc import FccUlsSource
from hamcall_db.writer import write_parquet

# Registry of available source importers, keyed by their short tag.
SOURCES: dict[str, Source] = {
    "fcc": FccUlsSource(),
}

app = typer.Typer(
    add_completion=False,
    help="Build the hamcall-db Parquet artifact from upstream amateur radio sources.",
)


def _run_source(source: Source, work_dir: Path) -> list:
    """Download + parse a single source into a list of Records."""
    path = source.download(work_dir)
    return list(source.parse(path))


@app.command()
def build(
    out: Annotated[Path, typer.Option(help="Output path or directory for the artifact.")],
    source: Annotated[
        str | None,
        typer.Option(help="Build a single source by tag (e.g. 'fcc'). Omit with --all."),
    ] = None,
    all_sources: Annotated[
        bool,
        typer.Option("--all", help="Build and merge every registered source."),
    ] = False,
    work_dir: Annotated[
        Path,
        typer.Option(help="Scratch dir for downloads/intermediates."),
    ] = Path("data/work"),
) -> None:
    """Download, parse, merge, and write the Parquet artifact."""
    if source is None and not all_sources:
        raise typer.BadParameter("Pass --source <tag> or --all.")
    if source is not None and all_sources:
        raise typer.BadParameter("Pass either --source or --all, not both.")

    selected = list(SOURCES) if all_sources else [source]
    unknown = [s for s in selected if s not in SOURCES]
    if unknown:
        raise typer.BadParameter(f"Unknown source(s): {', '.join(unknown)}")

    streams = [_run_source(SOURCES[s], work_dir) for s in selected]
    records = merge(streams) if all_sources else streams[0]

    out_path = out / _default_filename() if out.is_dir() else out
    count = write_parquet(records, out_path)
    typer.echo(f"Wrote {count} records to {out_path}")


def _default_filename() -> str:
    """Dated artifact name: hamcall-db-YYYY-MM-DD.parquet."""
    today = dt.date.today().isoformat()
    return f"hamcall-db-{today}.parquet"


if __name__ == "__main__":
    app()
