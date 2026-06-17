"""CLI entry point for the build pipeline.

Run via ``python -m hamcall_db.build`` or the ``hamcall-db`` console script.

Pipeline: download → parse (per source) → merge/normalize/geocode → optional cty.dat
DXCC enrichment → write current-state Parquet (+ history on --all).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated

import typer

from hamcall_db.enrich import enrich, load_cty
from hamcall_db.geocode import LookupGeocoder
from hamcall_db.history import diff_history
from hamcall_db.merge import merge
from hamcall_db.sources import ad1c
from hamcall_db.sources.acma import AcmaSource
from hamcall_db.sources.base import Source
from hamcall_db.sources.fcc import FccUlsSource
from hamcall_db.sources.ised import IsedSource
from hamcall_db.sqlite_writer import write_sqlite
from hamcall_db.writer import read_history_parquet, write_history_parquet, write_parquet

# Registry of available source importers, keyed by their short tag.
SOURCES: dict[str, Source] = {
    "fcc": FccUlsSource(),
    "ised": IsedSource(),
    "acma": AcmaSource(),
}

app = typer.Typer(
    add_completion=False,
    help="Build the hamcall-db Parquet artifact from upstream amateur radio sources.",
)


def _run_source(source: Source, work_dir: Path) -> list:
    """Download + parse a single source into a list of Records.

    download() captures the upstream file date as source.synced_at (au-23bc); pass it
    through so every Record is stamped with the source's published date.
    """
    path = source.download(work_dir)
    return list(source.parse(path, synced_at=source.synced_at))


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
    cty: Annotated[
        Path | None,
        typer.Option(help="AD1C cty.dat path; enriches country/dxcc from callsign prefix."),
    ] = None,
    history_in: Annotated[
        Path | None,
        typer.Option(
            help="Prior history artifact to extend (--all only). Omit for the first build.",
        ),
    ] = None,
    db_in: Annotated[
        Path | None,
        typer.Option(
            help="Prior SQLite .db (e.g. last 'latest' build); supplies the stable-id "
            "ledger so ids are never reused (--all only). Omit for the first build.",
        ),
    ] = None,
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
    # Always route through merge(): it normalizes, dedups, and sorts deterministically,
    # which a single-source build wants too. Collision resolution is a no-op for one
    # source. The offline LookupGeocoder fills `grid` (4-char Maidenhead) only where a
    # source didn't already supply one; see hamcall_db/geocode.py and mem-e3fd.
    # merge() yields lazily and geocoding fills grid; materialize so we can both write the
    # current-state artifact AND diff it into history without re-running the pipeline.
    records = list(merge(streams, geocode=LookupGeocoder()))

    # cty.dat enrichment runs once over the deduped set (order-independent): resolve
    # country (DXCC entity name) + dxcc number from the callsign prefix. On --all (a
    # network-bound build that already downloads every source) we auto-fetch cty.dat;
    # single-source dev builds enrich only when --cty points at a local file.
    cty_path = cty
    if cty_path is None and all_sources:
        # with_csv also caches cty.csv (sibling), which carries the numeric ADIF DXCC
        # code for accurate `dxcc` (au-37af); cty.dat alone gives the country name.
        cty_path = ad1c.download_cty(work_dir, with_csv=True)
    if cty_path is not None:
        csv_sibling = cty_path.with_name("cty.csv")
        csv_path = csv_sibling if csv_sibling.exists() else None
        records = list(enrich(records, load_cty(cty_path, csv_path=csv_path)))

    out_dir = out.is_dir()
    out_path = out / _default_filename() if out_dir else out
    count = write_parquet(records, out_path)
    typer.echo(f"Wrote {count} records to {out_path}")

    # History is an --all concern: it tracks the union of all sources over time. The
    # current-state artifact above is byte-for-byte unchanged regardless (mem-4784).
    if all_sources:
        as_of = dt.date.today().isoformat()
        prior = read_history_parquet(history_in) if history_in is not None else []
        history = diff_history(prior, records, as_of=as_of)
        hist_path = (
            (out.parent if not out_dir else out) / _history_filename()
        )
        hist_count = write_history_parquet(history, hist_path)
        typer.echo(f"Wrote {hist_count} history intervals to {hist_path}")

        # Optional convenience artifact: one SQLite .db holding current + history in a
        # single multi-table file. Parquet stays the canonical, storage-neutral output.
        # The surrogate `id` is a stable, never-reused public identifier; --db-in carries
        # the prior build's ledger forward so ids persist across rebuilds (au-d824, mem).
        db_path = (out if out_dir else out.parent) / _sqlite_filename()
        db_counts = write_sqlite(records, history, db_path, prior_db=db_in)
        typer.echo(
            f"Wrote {db_counts['current']} current + {db_counts['history']} history "
            f"rows to {db_path}"
        )


def _default_filename() -> str:
    """Dated artifact name: hamcall-db-YYYY-MM-DD.parquet."""
    today = dt.date.today().isoformat()
    return f"hamcall-db-{today}.parquet"


def _sqlite_filename() -> str:
    """Dated SQLite artifact name: hamcall-db-YYYY-MM-DD.db."""
    today = dt.date.today().isoformat()
    return f"hamcall-db-{today}.db"


def _history_filename() -> str:
    """Dated history artifact name: hamcall-db-history-YYYY-MM-DD.parquet."""
    today = dt.date.today().isoformat()
    return f"hamcall-db-history-{today}.parquet"


if __name__ == "__main__":
    app()
