"""Build ``allstar.json``, the AllStarLink node directory (sibling of ``reflectors.json``).

    uv run hamcall-db-allstar --out docs/site/api/v1

Separate entry point from :mod:`hamcall_db.build_reflectors`; it shares that module's
shrink guard and the reflector envelope (see :mod:`hamcall_db.reflectors`).

Failure policy: never publish an empty or sharply-shrunken directory. A failed download,
fewer than ``MIN_NODES`` parsed rows (an HTML error page parses to zero), or a drop of
more than a third against the file on disk all leave the existing file untouched.
"""

from __future__ import annotations

import json
import urllib.error
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from hamcall_db.build_reflectors import SHRINK_GUARD
from hamcall_db.enrich_allstar import (
    ALLSTAR_DB_URL,
    AllStarNode,
    Fetcher,
    download_allstar,
    parse_directory_report,
)
from hamcall_db.reflectors import (
    ALLSTAR_FILE,
    API_VERSION,
    CLIENT_REFRESH_DAYS,
    MIXED_LICENSE_URL,
    SCHEMA_VERSION,
)
from hamcall_db.reflectors import _write_json as write_json

# The real directory has ~41,500 nodes. Anything under this is a broken download, not churn.
MIN_NODES = 1000

ALLSTAR_ATTRIBUTION = (
    "AllStarLink node data courtesy of AllStarLink, Inc. (https://www.allstarlink.org/)."
)
ALLSTAR_RIGHTS = (
    "Information about users' nodes (AllStarLink User Content), redistributed under "
    "AllStarLink's 'reasonable use of published directories'. No rights granted beyond "
    "those AllStarLink provides."
)
ALLSTAR_MODIFICATIONS = (
    "Reformatted from AllStarLink's pipe-separated node list. Malformed lines dropped; "
    "locations that look like street addresses removed; email addresses removed from "
    "free text."
)

# Test seam: patched to avoid the network. None means urllib.
FETCHER: Fetcher | None = None

app = typer.Typer(add_completion=False, help="Write the AllStarLink node directory (allstar.json).")


def _node_rows(nodes: Sequence[AllStarNode]) -> list[dict[str, str | None]]:
    return [
        {
            "node": n.node,
            "callsign": n.callsign,
            "description": n.description,
            "location": n.location,
        }
        for n in nodes
    ]


def allstar_document(
    nodes: Sequence[AllStarNode],
    *,
    today: date,
    previous: Mapping[str, object] | None,
) -> dict[str, object]:
    """The allstar.json envelope. Dates are carried over when the node list is unchanged."""
    rows = _node_rows(nodes)
    generated = fetched = today.isoformat()
    if previous is not None and previous.get("nodes") == rows:
        prev_gen = previous.get("generated")
        prev_sources = previous.get("sources")
        if isinstance(prev_gen, str):
            generated = prev_gen
        if isinstance(prev_sources, list) and prev_sources and isinstance(prev_sources[0], dict):
            prev_fetched = prev_sources[0].get("fetched")
            if isinstance(prev_fetched, str):
                fetched = prev_fetched
    return {
        "api_version": API_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated": generated,
        "client_refresh_days": CLIENT_REFRESH_DAYS,
        "count": len(rows),
        "attribution": ALLSTAR_ATTRIBUTION,
        "license": "none-stated",
        "license_url": MIXED_LICENSE_URL,
        "rights": ALLSTAR_RIGHTS,
        "modifications": ALLSTAR_MODIFICATIONS,
        "sources": [{"name": "allstarlink", "url": ALLSTAR_DB_URL, "fetched": fetched}],
        "nodes": rows,
    }


def _load_previous(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


@app.command()
def build(
    out: Annotated[Path, typer.Option(help="Directory to write allstar.json into.")] = Path(
        "docs/site/api/v1"
    ),
    work_dir: Annotated[Path, typer.Option(help="Scratch dir for the cached download.")] = Path(
        "data/raw"
    ),
    today: Annotated[str | None, typer.Option(help="Override today's date (YYYY-MM-DD).")] = None,
) -> None:
    day = date.fromisoformat(today) if today else date.today()
    path = out / ALLSTAR_FILE
    previous = _load_previous(path)

    try:
        raw = download_allstar(work_dir, on=day, fetcher=FETCHER)
    except (OSError, urllib.error.URLError, RuntimeError) as exc:
        typer.echo(f"AllStar download failed, keeping {path}: {exc}", err=True)
        raise typer.Exit(1) from exc

    nodes, dropped = parse_directory_report(raw.read_bytes().decode("utf-8", errors="replace"))
    report = ", ".join(f"{k}={v}" for k, v in dropped.items())
    typer.echo(f"AllStar: {len(nodes)} nodes; dropped: {report}")

    if len(nodes) < MIN_NODES:
        typer.echo(
            f"AllStar parsed only {len(nodes)} nodes (< {MIN_NODES}); keeping {path}", err=True
        )
        raise typer.Exit(1)

    prev_count = previous.get("count") if previous else None
    if isinstance(prev_count, int) and len(nodes) < prev_count * SHRINK_GUARD:
        typer.echo(
            f"AllStar list shrank from {prev_count} to {len(nodes)} nodes; keeping {path}",
            err=True,
        )
        return

    out.mkdir(parents=True, exist_ok=True)
    write_json(path, allstar_document(nodes, today=day, previous=previous))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
