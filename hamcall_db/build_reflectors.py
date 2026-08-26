"""Build the static reflector JSON API (hdb-refl).

Separate entry point from :mod:`hamcall_db.build`, because the reflector directory is a
separate artifact under a separate licence (CC BY 4.0, not the project's CC BY-NC) and
must never be merged into the callsign dataset. See :mod:`hamcall_db.reflectors`.

    uv run hamcall-db-reflectors --out site/api/v1

Writes the versioned static API described by ``docs/REFLECTOR-API.md`` — a manifest, one
file with every reflector in it, a file per network, and the generated OpenAPI contract.

Sources per network:

* ``dstar`` — the XLX registry (:mod:`hamcall_db.sources.xlx`, ~892 reflectors, no
  token), supplemented by DVRef's smaller D-Star list when a token is available.
* everything else — DVRef (:mod:`hamcall_db.sources.dvref`, token required).

Failure policy: **never publish an empty or sharply-shrunken list.** A network whose
fetch fails, or whose row count collapses against the copy already on disk, keeps its
previous file and the command reports it. DVRef is explicit that they offer no SLA, and
an outage upstream must not translate into every client's reflector picker going blank.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from hamcall_db.reflectors import (
    NETWORK_DIR,
    ReflectorRecord,
    merge_by_id,
    network_document,
    records_from_document,
    write_api,
)
from hamcall_db.sources.dvref import API_ROOT, NETWORKS, DvrefAuthError, DvrefSource
from hamcall_db.sources.dvref import ATTRIBUTION as DVREF_ATTRIBUTION
from hamcall_db.sources.xlx import XLX_LIST_URL, XlxSource
from hamcall_db.sqlite_writer import write_reflectors_sqlite
from hamcall_db.writer import write_reflectors_parquet

XLX_ATTRIBUTION = (
    "XLX reflector data from the XLX registry maintained by Luc Engelmann, LX1IQ "
    "(http://xlxapi.rlx.lu/)."
)

# A rebuild that loses more than this fraction of a network's rows is treated as an
# upstream fault rather than real churn, and the previous file is kept. Reflector
# directories do shrink, but not by a third overnight; a partial response or a silently
# changed envelope looks exactly like this and would otherwise be published.
SHRINK_GUARD = 0.66

app = typer.Typer(
    add_completion=False,
    help="Build the static reflector JSON API (CC BY 4.0; separate from the callsign dataset).",
)


def _existing_count(out_dir: Path, network: str) -> int | None:
    """Row count of the network file already on disk, or None if there isn't one."""
    path = out_dir / NETWORK_DIR / f"{network}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    count = payload.get("count") if isinstance(payload, dict) else None
    return count if isinstance(count, int) else None


def _load_existing(out_dir: Path, network: str) -> dict[str, object] | None:
    path = out_dir / NETWORK_DIR / f"{network}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


@app.command()
def build(
    out: Annotated[
        Path,
        typer.Option(
            help=(
                "Output directory for the versioned API "
                "(index.json + reflectors.json + reflectors/ + openapi.json)."
            )
        ),
    ] = Path("site/api/v1"),
    work_dir: Annotated[
        Path, typer.Option(help="Scratch dir for cached upstream downloads.")
    ] = Path("data/raw"),
    skip_dvref: Annotated[
        bool,
        typer.Option(
            "--skip-dvref", help="Build only the XLX-sourced D-Star list (no token needed)."
        ),
    ] = False,
    dist: Annotated[
        Path | None,
        typer.Option(
            help="Also write dated Parquet + SQLite artifacts here (CC BY 4.0, SEPARATE files)."
        ),
    ] = None,
) -> None:
    """Fetch every reflector source and write the static JSON API."""
    today = datetime.now(UTC).date()
    day_dir = work_dir / today.isoformat()

    documents: dict[str, dict[str, object]] = {}
    kept: list[str] = []
    failed: list[str] = []

    # --- D-Star: XLX registry is the source of record (coverage), DVRef supplements ---
    dstar: list[ReflectorRecord] = []
    dstar_attribution: list[str] = []
    try:
        xlx = XlxSource()
        dstar.extend(xlx.parse(xlx.download(day_dir / "xlx")))
        dstar_attribution.append(XLX_ATTRIBUTION)
        typer.echo(f"xlx: {len(dstar)} D-Star reflectors")
    except Exception as exc:  # noqa: BLE001 - one bad source must not sink the build
        typer.echo(f"WARNING: xlx failed ({exc})", err=True)
        failed.append("xlx")

    dvref_networks = {} if skip_dvref else NETWORKS
    dvref_records: dict[str, list[ReflectorRecord]] = {}
    dvref_credit: dict[str, str] = {}
    for segment, network in dvref_networks.items():
        try:
            source = DvrefSource(segment)
            rows = list(source.parse(source.download(day_dir / "dvref")))
            dvref_records[network] = rows
            # Credit exactly as upstream words it — parse() lifts this out of the
            # response's own _dvref_metadata block.
            dvref_credit[network] = source.attribution
            typer.echo(f"dvref/{segment}: {len(rows)} {network} reflectors")
        except DvrefAuthError as exc:
            typer.echo(f"WARNING: dvref/{segment} skipped — {exc}", err=True)
            failed.append(f"dvref/{segment}")
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"WARNING: dvref/{segment} failed ({exc})", err=True)
            failed.append(f"dvref/{segment}")

    # XLX first: it is the coverage source, so its row wins an id collision.
    if "dstar" in dvref_records:
        dstar = merge_by_id(dstar, dvref_records.pop("dstar"))
        dstar_attribution.append(dvref_credit.pop("dstar", DVREF_ATTRIBUTION))

    if dstar:
        documents["dstar"] = network_document(
            "dstar",
            dstar,
            source_name="XLX registry (LX1IQ)" + (" + DVRef" if len(dstar_attribution) > 1 else ""),
            source_url=XLX_LIST_URL,
            attribution=" ".join(dstar_attribution),
            generated=today,
        )

    for network, rows in dvref_records.items():
        if not rows:
            continue
        documents[network] = network_document(
            network,
            rows,
            source_name="DVRef",
            source_url=f"{API_ROOT}/",
            attribution=dvref_credit.get(network, DVREF_ATTRIBUTION),
            generated=today,
        )

    # --- Failure policy: keep last-good rather than publishing a regression ----------
    for network in list(documents):
        previous = _existing_count(out, network)
        fresh = documents[network]["count"]
        assert isinstance(fresh, int)
        if previous and fresh < previous * SHRINK_GUARD:
            existing = _load_existing(out, network)
            typer.echo(
                f"WARNING: {network} shrank {previous} -> {fresh} "
                f"(below {SHRINK_GUARD:.0%}); keeping the previous file",
                err=True,
            )
            if existing is not None:
                documents[network] = existing
                kept.append(network)

    # A network that failed entirely keeps whatever is already published.
    for network in NETWORKS.values():
        if network in documents:
            continue
        existing = _load_existing(out, network)
        if existing is not None:
            documents[network] = existing
            kept.append(network)

    if not documents:
        typer.echo(
            "ERROR: no reflector source succeeded and nothing was already published.", err=True
        )
        raise typer.Exit(code=1)

    written = write_api(out, documents, generated=today)
    typer.echo(f"Wrote {len(written)} files to {out}")

    if dist is not None:
        # Dated CC BY 4.0 artifacts, in their OWN files. These are never merged into the
        # CC BY-NC callsign dataset — see hamcall_db.reflectors for why that would be a
        # licence violation rather than a housekeeping preference.
        stamp = today.isoformat()
        all_records = [
            record
            for name in sorted(documents)
            for record in records_from_document(documents[name])
        ]
        parquet_path = dist / f"hamcall-db-reflectors-{stamp}.parquet"
        sqlite_path = dist / f"hamcall-db-reflectors-{stamp}.db"
        rows = write_reflectors_parquet(all_records, parquet_path)
        write_reflectors_sqlite(all_records, sqlite_path)
        typer.echo(f"Wrote {rows} reflector rows to {parquet_path.name} and {sqlite_path.name}")
    if kept:
        typer.echo(f"Kept previous data for: {', '.join(sorted(set(kept)))}", err=True)
    if failed:
        typer.echo(f"Sources that failed: {', '.join(failed)}", err=True)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
