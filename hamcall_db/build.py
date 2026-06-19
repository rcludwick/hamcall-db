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

from hamcall_db import enrich_allstar, enrich_lotw
from hamcall_db.enrich import enrich, load_cty
from hamcall_db.geocode import LookupGeocoder
from hamcall_db.history import diff_history
from hamcall_db.merge import merge
from hamcall_db.sources import ad1c
from hamcall_db.sources.acma import AcmaSource
from hamcall_db.sources.base import Source
from hamcall_db.sources.fcc import FccUlsSource
from hamcall_db.sources.ised import IsedSource
from hamcall_db.sources.osm import OsmSource, compute_osm_park_grids
from hamcall_db.sources.padus import PadusSource, compute_park_grids
from hamcall_db.sources.pota import PotaSource
from hamcall_db.sources.sota import SotaSource
from hamcall_db.sqlite_writer import (
    write_pota_park_grids_osm_sqlite,
    write_pota_park_grids_sqlite,
    write_pota_parks_sqlite,
    write_sota_summits_sqlite,
    write_sqlite,
)
from hamcall_db.writer import (
    read_history_parquet,
    write_history_parquet,
    write_parquet,
    write_pota_park_grids_osm_parquet,
    write_pota_park_grids_parquet,
    write_pota_parks_parquet,
    write_sota_summits_parquet,
)

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


def _collect_streams(
    selected: list[str],
    work_dir: Path,
    *,
    sources: dict[str, Source] = SOURCES,
) -> tuple[list[list], list[str]]:
    """Run each selected source, returning (successful streams, skipped tags).

    A single source's download/parse failure is logged and skipped rather than
    aborting the whole build (hdb-6f3b): a transient upstream outage shouldn't block
    the weekly release of the sources that did succeed. The caller decides what to do
    when nothing succeeds.
    """
    streams: list[list] = []
    skipped: list[str] = []
    for tag in selected:
        try:
            streams.append(_run_source(sources[tag], work_dir))
        except Exception as exc:  # any download/parse failure is non-fatal here
            typer.echo(f"WARNING: source '{tag}' failed ({exc}); skipping", err=True)
            skipped.append(tag)
    return streams, skipped


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
    include_restricted: Annotated[
        bool,
        typer.Option(
            "--include-restricted",
            help="Include sources whose redistribution license is UNCONFIRMED (LoTW "
            "enrichment, SOTA summits). OFF by default — the published release NEVER sets "
            "this. For personal/local builds only; do not redistribute the result without "
            "confirming each source's terms (see NOTICE).",
        ),
    ] = False,
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

    streams, skipped = _collect_streams(selected, work_dir)
    if skipped:
        typer.echo(f"Skipped {len(skipped)} source(s): {', '.join(skipped)}", err=True)
    if not streams:
        typer.echo("ERROR: no sources succeeded; nothing to write.", err=True)
        raise typer.Exit(code=1)
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

    # AllStarLink node enrichment: one callsign -> MANY node numbers (hdb-8803). An --all
    # concern (it's a network-bound build that already downloads every source); single-
    # source dev builds skip it. RESILIENT: a transient AllStarLink outage must not abort
    # the build — log a warning and continue with empty node lists.
    if all_sources:
        try:
            allstar_path = enrich_allstar.download_allstar(work_dir)
            lookup = enrich_allstar.load_allstar(allstar_path)
            records = list(enrich_allstar.enrich(records, lookup))
        except Exception as exc:  # download/parse failure is non-fatal
            typer.echo(
                f"WARNING: AllStarLink enrichment skipped ({exc}); "
                "allstar_nodes left empty",
                err=True,
            )

    # LoTW (ARRL Logbook of the World) user-activity enrichment (hdb-fccf): stamp each
    # callsign with uses_lotw + lotw_last_activity from ARRL's public list. An --all
    # concern like AllStarLink (network-bound build); single-source dev builds skip it.
    # RESTRICTED: ARRL states no redistribution license ("All Rights Reserved"), so this is
    # OFF unless --include-restricted is passed; the published release never sets it, so the
    # columns ship empty there (hdb-fccf). RESILIENT: a transient ARRL outage must not abort
    # the build — log a warning and continue with uses_lotw=False / lotw_last_activity=None.
    if all_sources and include_restricted:
        try:
            lotw_path = enrich_lotw.download_lotw(work_dir)
            lotw_lookup = enrich_lotw.load_lotw(lotw_path)
            records = list(enrich_lotw.enrich(records, lotw_lookup))
        except Exception as exc:  # download/parse failure is non-fatal
            typer.echo(
                f"WARNING: LoTW enrichment skipped ({exc}); "
                "uses_lotw/lotw_last_activity left empty",
                err=True,
            )

    # Treat --out as a directory when it already is one OR has no file suffix
    # (e.g. `--out dist/`); only an explicit file extension means a single file path.
    # A directory that doesn't exist yet is created, so CI need not pre-mkdir it — the
    # cause of the failed first release, where a non-existent `dist/` made out.is_dir()
    # False and the parquet landed in a file literally named `dist`.
    out_dir = out.is_dir() or out.suffix == ""
    if out_dir:
        out.mkdir(parents=True, exist_ok=True)
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

        # POTA parks: a SEPARATE additive reference dataset (places, not licensees,
        # hdb-9640). Written as its own Parquet file AND a pota_parks table appended to
        # the SAME .db — it NEVER touches the callsign current/history schema (the
        # redistribution contract). RESILIENT: a POTA outage must not abort the build —
        # log a warning and skip the parks artifacts, exactly like AllStarLink above.
        try:
            pota = PotaSource()
            parks_dir = pota.download(work_dir)
            parks = list(pota.parse(parks_dir))
            parks_dir_out = out if out_dir else out.parent
            parks_path = parks_dir_out / _pota_parks_filename()
            parks_count = write_pota_parks_parquet(parks, parks_path)
            write_pota_parks_sqlite(parks, db_path)
            typer.echo(
                f"Wrote {parks_count} POTA parks to {parks_path} "
                f"(+ pota_parks table in {db_path})"
            )

            # POTA park -> Maidenhead grid SETS (hdb-f53c, PHASE 1: US/PAD-US only). A
            # SEPARATE additive child table keyed to the parks dataset by ``reference``;
            # never touches the callsign or parks schema. RESILIENT and independently
            # guarded: PAD-US is a separate large download, and reading it needs the
            # build-time `padus` dependency group (GDAL via pyogrio) — if it's absent or
            # the download fails, every park degrades to its point grid via
            # compute_park_grids([]). The build never blocks on it.
            try:
                features = _load_padus_features(work_dir)
            except Exception as exc:  # GDAL/group missing or download failed
                typer.echo(
                    f"WARNING: PAD-US boundaries unavailable ({exc}); "
                    "park grids fall back to point grids",
                    err=True,
                )
                features = []
            park_grid_rows = compute_park_grids(parks, features)
            grids_path = parks_dir_out / _pota_park_grids_filename()
            grids_count = write_pota_park_grids_parquet(park_grid_rows, grids_path)
            write_pota_park_grids_sqlite(park_grid_rows, db_path)
            typer.echo(
                f"Wrote {grids_count} POTA park-grid rows to {grids_path} "
                f"(+ pota_park_grids table in {db_path})"
            )

            # OSM/international park -> Maidenhead grid SETS (hdb-438b, PHASE 2). LICENSE
            # SEGREGATION: OSM-derived grids are an ODbL DERIVATIVE database, incompatible
            # with the CC BY-NC artifact's terms — so they ship in their OWN files: a
            # SEPARATE ODbL Parquet AND a SEPARATE ODbL .db (table pota_park_grids_osm).
            # They are NEVER written into the CC-BY-NC db_path or the PAD-US parquet above.
            # Independently guarded and resilient: reading OSM extracts needs the build-time
            # `osm` dependency group (GDAL via pyogrio) and large regional downloads — if
            # absent or the download fails, every non-US park degrades to its point grid via
            # compute_osm_park_grids([]). US parks are excluded (PAD-US owns them). The build
            # never blocks on it.
            try:
                osm_features = _load_osm_features(work_dir)
            except Exception as exc:  # GDAL/group missing or download failed
                typer.echo(
                    f"WARNING: OSM boundaries unavailable ({exc}); non-US park grids "
                    "fall back to point grids",
                    err=True,
                )
                osm_features = []
            osm_grid_rows = compute_osm_park_grids(parks, osm_features)
            osm_grids_path = parks_dir_out / _pota_park_grids_osm_filename()
            osm_db_path = parks_dir_out / _pota_park_grids_osm_db_filename()
            osm_count = write_pota_park_grids_osm_parquet(osm_grid_rows, osm_grids_path)
            write_pota_park_grids_osm_sqlite(osm_grid_rows, osm_db_path)
            typer.echo(
                f"Wrote {osm_count} OSM (ODbL) park-grid rows to {osm_grids_path} "
                f"(+ pota_park_grids_osm table in {osm_db_path})"
            )
        except Exception as exc:  # download/parse/write failure is non-fatal
            typer.echo(
                f"WARNING: POTA parks dataset skipped ({exc}); "
                "parks artifacts not written",
                err=True,
            )

        # SOTA summits: a SEPARATE additive reference dataset (places, not licensees,
        # hdb-ca00), a sibling of POTA parks. Written as its own Parquet file AND a
        # sota_summits table appended to the SAME CC-BY-NC .db — it NEVER touches the
        # callsign schema. RESTRICTED: SOTA's terms are unconfirmed (NOTICE source 10), so
        # this is OFF unless --include-restricted is passed; the published release never
        # sets it, so the SOTA artifacts are NOT produced there. RESILIENT + INDEPENDENTLY
        # guarded: a SOTA outage logs a warning and skips, never taking down the POTA
        # artifacts (or the build).
        if include_restricted:
            try:
                sota = SotaSource()
                summits_path_in = sota.download(work_dir)
                summits = list(sota.parse(summits_path_in))
                summits_dir_out = out if out_dir else out.parent
                summits_path = summits_dir_out / _sota_summits_filename()
                summits_count = write_sota_summits_parquet(summits, summits_path)
                write_sota_summits_sqlite(summits, db_path)
                typer.echo(
                    f"Wrote {summits_count} SOTA summits to {summits_path} "
                    f"(+ sota_summits table in {db_path})"
                )
            except Exception as exc:  # download/parse/write failure is non-fatal
                typer.echo(
                    f"WARNING: SOTA summits dataset skipped ({exc}); "
                    "summits artifacts not written",
                    err=True,
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


def _pota_parks_filename() -> str:
    """Dated POTA parks artifact name: hamcall-db-pota-parks-YYYY-MM-DD.parquet."""
    today = dt.date.today().isoformat()
    return f"hamcall-db-pota-parks-{today}.parquet"


def _sota_summits_filename() -> str:
    """Dated SOTA summits artifact name: hamcall-db-sota-summits-YYYY-MM-DD.parquet."""
    today = dt.date.today().isoformat()
    return f"hamcall-db-sota-summits-{today}.parquet"


def _pota_park_grids_filename() -> str:
    """Dated park-grids artifact name: hamcall-db-pota-park-grids-YYYY-MM-DD.parquet."""
    today = dt.date.today().isoformat()
    return f"hamcall-db-pota-park-grids-{today}.parquet"


def _pota_park_grids_osm_filename() -> str:
    """Dated OSM (ODbL) park-grids Parquet name: hamcall-db-pota-park-grids-osm-*.parquet."""
    today = dt.date.today().isoformat()
    return f"hamcall-db-pota-park-grids-osm-{today}.parquet"


def _pota_park_grids_osm_db_filename() -> str:
    """Dated OSM (ODbL) park-grids SQLite name: hamcall-db-pota-park-grids-osm-*.db.

    A SEPARATE .db from the CC-BY-NC artifact so ODbL share-alike never taints it.
    """
    today = dt.date.today().isoformat()
    return f"hamcall-db-pota-park-grids-osm-{today}.db"


def _load_osm_features(work_dir: Path) -> list:
    """Download + read OSM boundary features (build-time, GIS reader via the `osm` group).

    Isolated so the caller can guard it: any failure here (missing GIS reader, download
    error) degrades non-US park grids to point grids without blocking the build. Geofabrik
    publishes daily extracts; the cached files are reused within a build day. The region
    list is a pin-at-build-time decision (OsmSource defaults to a conservative non-US set).
    """
    osm = OsmSource()
    osm.require_reader()  # cheap pyogrio probe BEFORE the multi-GB Geofabrik download (hdb-66be)
    osm_paths = osm.download(work_dir)
    return osm.read_features(osm_paths)


def _load_padus_features(work_dir: Path) -> list:
    """Download + read PAD-US boundary features (build-time, GDAL via the padus group).

    Isolated so the caller can guard it: any failure here (missing GDAL reader, download
    error) degrades park grids to point grids without blocking the build. PAD-US updates
    ~annually, so the cached file is reused across weekly builds.
    """
    padus = PadusSource()
    padus.require_reader()  # cheap pyogrio probe BEFORE the ~1.7 GB GDB download (hdb-66be)
    padus_path = padus.download(work_dir)
    return padus.read_features(padus_path)


if __name__ == "__main__":
    app()
