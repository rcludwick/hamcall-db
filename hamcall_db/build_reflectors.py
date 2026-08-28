"""Build the static reflector JSON API (hdb-refl).

Separate entry point from :mod:`hamcall_db.build`, because the reflector directory is a
separate artifact under a separate licence (CC BY 4.0, not the project's CC BY-NC) and
must never be merged into the callsign dataset. See :mod:`hamcall_db.reflectors`.

    uv run hamcall-db-reflectors --out docs/site/api/v1

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
    ATTRIBUTION_SEPARATOR,
    NETWORK_DIR,
    ReflectorRecord,
    merge_by_id,
    network_document,
    records_from_document,
    write_api,
)
from hamcall_db.sources.dextra import (
    DextraHostsSource,
    without_known_addresses,
)
from hamcall_db.sources.dstar_aliases import DStarAliasSource, apply_aliases
from hamcall_db.sources.dvref import (
    API_ROOT,
    NETWORKS,
    DvrefAuthError,
    DvrefSource,
    DvrefThrottled,
)
from hamcall_db.sources.dvref import ATTRIBUTION as DVREF_ATTRIBUTION
from hamcall_db.sources.xlx import XLX_LIST_URL, XlxSource
from hamcall_db.sqlite_writer import write_reflectors_sqlite
from hamcall_db.writer import write_reflectors_parquet

DEXTRA_ATTRIBUTION = (
    "Standalone XRF reflector data from the Pi-Star DExtra host file "
    "(http://www.pistar.uk/downloads/DExtra_Hosts.txt)."
)

DSTAR_ALIAS_ATTRIBUTION = (
    "REF and DCS reflector names from the Pi-Star DPlus and DCS host files "
    "(http://www.pistar.uk/downloads/)."
)

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


def _source_counts(document: dict[str, object] | None) -> dict[str, int]:
    """How many rows each importer contributed to a published document.

    ``source`` is a published field, so this reads out of the file itself rather
    than needing the build to remember what it did.
    """
    if not document:
        return {}
    rows = document.get("reflectors")
    if not isinstance(rows, list):
        return {}
    counts: dict[str, int] = {}
    for row in rows:
        if isinstance(row, dict):
            name = row.get("source")
            if isinstance(name, str) and name:
                counts[name] = counts.get(name, 0) + 1
    return counts


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
    ] = Path("docs/site/api/v1"),
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
    # Which importers this build ATTEMPTED for each network. The source guard
    # compares against this rather than against history alone: a source that is
    # no longer configured has not been "lost", it has been retired, and treating
    # the two alike would freeze a network forever protecting rows that are never
    # coming back. DVRef retiring its D-Star listings is exactly that case.
    attempted: dict[str, set[str]] = {}

    # --- D-Star: XLX registry is the source of record (coverage), DVRef supplements ---
    dstar: list[ReflectorRecord] = []
    dstar_attribution: list[str] = []
    try:
        xlx = XlxSource()
        dstar.extend(xlx.parse(xlx.download(day_dir / "xlx")))
        dstar_attribution.append(XLX_ATTRIBUTION)
        attempted.setdefault("dstar", set()).add("xlx")
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
            attempted.setdefault(network, set()).add("dvref")
            typer.echo(f"dvref/{segment}: {len(rows)} {network} reflectors")
            # Upstream sets this only when something has changed. An empty list
            # WITH an explanation is not an outage, and the difference is
            # invisible unless the message is printed.
            if source.notice:
                typer.echo(f"  notice from dvref/{segment}: {source.notice}", err=True)
        except DvrefThrottled as exc:
            # Distinct from a failure: upstream said to wait, and said how long.
            # The keep-last-good path below covers the data; this just makes the
            # log say something a reader can act on.
            wait = f" (retry after {exc.retry_after}s)" if exc.retry_after else ""
            typer.echo(f"WARNING: dvref/{segment} throttled{wait} — {exc}", err=True)
            failed.append(f"dvref/{segment}:throttled")
        except DvrefAuthError as exc:
            typer.echo(f"WARNING: dvref/{segment} skipped — {exc}", err=True)
            failed.append(f"dvref/{segment}")
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"WARNING: dvref/{segment} failed ({exc})", err=True)
            failed.append(f"dvref/{segment}")

    # Standalone XRF reflectors — the ones the XLX registry does not know about.
    # DVRef used to supply these and retired the listings; xrefl.net, where they
    # registered, is now a parked domain. Pi-Star's host file is what survives.
    #
    # The file also lists every XLX reflector under its XRF alias, at the SAME
    # address, so those must be dropped or one machine is published twice under
    # two ids. Deduplication is BY ADDRESS: a standalone XRF reflector can share
    # a number with an unrelated XLX one (XRF002 is 52.36.45.107, XLX002 is a
    # host in China), so matching on name would drop the real reflectors and keep
    # the aliases — wrong in both directions at once.
    if dstar:
        try:
            dextra_source = DextraHostsSource()
            dextra_rows = list(dextra_source.parse(dextra_source.download(day_dir / "dextra")))
            known_hosts = {r.host for r in dstar if r.host}
            standalone = without_known_addresses(dextra_rows, known_hosts)
            typer.echo(
                f"dextra: {len(dextra_rows)} XRF rows, {len(standalone)} standalone "
                f"after dropping XLX aliases by address"
            )
            if standalone:
                dstar = merge_by_id(dstar, standalone)
                dstar_attribution.append(DEXTRA_ATTRIBUTION)
                attempted.setdefault("dstar", set()).add("dextra")
        except Exception as exc:  # noqa: BLE001 - one bad source must not sink the build
            typer.echo(f"WARNING: dextra failed ({exc})", err=True)
            failed.append("dextra")

    # The other names for reflectors we already publish. One XLX box answers on
    # all three D-Star linking protocols, so it is REF836 and DCS836 as well as
    # XLX836/XRF836 — and an operator who knows it by one of those should not be
    # told it does not exist. Aliases only: nothing here claims astar can speak
    # DPlus or DCS, and nothing here adds a row.
    #
    # Matched BY ADDRESS, like everything else on this network. REF001 is
    # 104.237.157.7 and XRF001 is 217.154.120.107 — unrelated machines sharing a
    # number, so matching on the digits would hand one reflector's name to
    # another.
    if dstar:
        try:
            alias_source = DStarAliasSource()
            names = alias_source.parse(alias_source.download(day_dir / "dstar-aliases"))
            dstar, added = apply_aliases(dstar, names)
            typer.echo(f"dstar-aliases: {len(names)} addresses upstream, {added} names added")
            if added:
                dstar_attribution.append(DSTAR_ALIAS_ATTRIBUTION)
        except Exception as exc:  # noqa: BLE001 - one bad source must not sink the build
            # Deliberately NOT recorded in `attempted`: aliases enrich rows that
            # already exist, so losing them costs searchability, never coverage.
            # Letting this failure feed the source-composition guard would freeze
            # a D-Star list that is otherwise perfectly current.
            typer.echo(f"WARNING: dstar-aliases failed ({exc})", err=True)
            failed.append("dstar-aliases")

    if dstar:
        documents["dstar"] = network_document(
            "dstar",
            dstar,
            # Named from the credits actually collected, not from how many
            # there are: a build where DExtra failed but the alias files
            # answered has two attributions and must not claim DExtra.
            source_name=" + ".join(
                label
                for credit, label in (
                    (XLX_ATTRIBUTION, "XLX registry (LX1IQ)"),
                    (DEXTRA_ATTRIBUTION, "Pi-Star DExtra hosts"),
                    (DSTAR_ALIAS_ATTRIBUTION, "Pi-Star DPlus/DCS names"),
                )
                if credit in dstar_attribution
            ),
            source_url=XLX_LIST_URL,
            attribution=ATTRIBUTION_SEPARATOR.join(dstar_attribution),
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
    # A network assembled from MORE THAN ONE source can lose a whole source
    # without the row count falling far enough to trip the shrink guard. D-Star
    # is the live case: 892 XLX rows plus 61 from DVRef, so a DVRef outage is a
    # 6% drop — comfortably inside the guard, and it would publish an
    # XLX-only D-Star as though that were the truth. Observed happening on
    # 2026-08-26, when DVRef returned a well-formed response with zero rows.
    #
    # So compare the SOURCE COMPOSITION, not just the total: a source that the
    # published file has rows from, and this build has none from, means an
    # incomplete build regardless of how small the shortfall looks.
    for network in list(documents):
        previous_sources = _source_counts(_load_existing(out, network))
        fresh_sources = _source_counts(documents[network])
        # Only a source this build actually TRIED can be lost. One no longer in
        # the source table was retired deliberately, and the one-time drop in rows
        # is the intended outcome rather than a fault to guard against.
        tried = attempted.get(network, set())
        lost = sorted(
            name
            for name, count in previous_sources.items()
            if count > 0 and name in tried and fresh_sources.get(name, 0) == 0
        )
        if lost:
            existing = _load_existing(out, network)
            typer.echo(
                f"WARNING: {network} lost every row from {', '.join(lost)} "
                f"({previous_sources} -> {fresh_sources}); keeping the previous file",
                err=True,
            )
            if existing is not None:
                documents[network] = existing
                kept.append(network)
                failed.append(f"{network}:{'+'.join(lost)}")
                continue

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
