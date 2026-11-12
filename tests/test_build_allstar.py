"""Tests for the hamcall-db-allstar command (api/v1/allstar.json)."""

from __future__ import annotations

import json
import urllib.error
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hamcall_db import build_allstar
from hamcall_db.build_allstar import (
    ALLSTAR_ATTRIBUTION,
    ALLSTAR_FILE,
    ALLSTAR_MODIFICATIONS,
    ALLSTAR_RIGHTS,
    allstar_document,
    app,
)
from hamcall_db.enrich_allstar import ALLSTAR_DB_URL, AllStarNode
from hamcall_db.reflectors import MIXED_LICENSE_URL

TODAY = "2026-11-11"


def _nodes(n: int) -> list[AllStarNode]:
    return [AllStarNode(str(2000 + i), f"W{i}AA", f"desc {i}", f"loc {i}") for i in range(n)]


def _text(n: int) -> bytes:
    return "".join(f"{2000 + i}|W{i}AA|desc {i}|loc {i}\n" for i in range(n)).encode()


def _run(tmp_path: Path, fetch_bytes: bytes | Exception):
    def fetcher(url: str, since: float | None) -> bytes | None:
        if isinstance(fetch_bytes, Exception):
            raise fetch_bytes
        return fetch_bytes

    build_allstar.FETCHER = fetcher
    try:
        return CliRunner().invoke(
            app,
            [
                "--out",
                str(tmp_path / "out"),
                "--work-dir",
                str(tmp_path / "work"),
                "--today",
                TODAY,
            ],
        )
    finally:
        build_allstar.FETCHER = None


def _seed(tmp_path: Path, n: int) -> tuple[Path, bytes]:
    path = tmp_path / "out" / ALLSTAR_FILE
    path.parent.mkdir(parents=True)
    doc = allstar_document(_nodes(n), today=date(2026, 11, 10), previous=None)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path, path.read_bytes()


def test_document_envelope() -> None:
    nodes = [
        AllStarNode("2002", "WB6NIL", "AllStarLink Parrot", "AWS US-EAST-1"),
        AllStarNode("2003", "K1ABC", None, None),
    ]
    doc = allstar_document(nodes, today=date(2026, 11, 11), previous=None)
    assert doc == {
        "api_version": "v1",
        "schema_version": 1,
        "generated": TODAY,
        "client_refresh_days": 7,
        "count": 2,
        "attribution": ALLSTAR_ATTRIBUTION,
        "license": "none-stated",
        "license_url": MIXED_LICENSE_URL,
        "rights": ALLSTAR_RIGHTS,
        "modifications": ALLSTAR_MODIFICATIONS,
        "sources": [{"name": "allstarlink", "url": ALLSTAR_DB_URL, "fetched": TODAY}],
        "nodes": [
            {
                "node": "2002",
                "callsign": "WB6NIL",
                "description": "AllStarLink Parrot",
                "location": "AWS US-EAST-1",
            },
            {"node": "2003", "callsign": "K1ABC", "description": None, "location": None},
        ],
    }


def test_unchanged_nodes_keep_dates() -> None:
    nodes = _nodes(3)
    first = allstar_document(nodes, today=date(2026, 11, 11), previous=None)
    again = allstar_document(nodes, today=date(2026, 11, 12), previous=first)
    assert again["generated"] == TODAY
    assert again["sources"][0]["fetched"] == TODAY  # type: ignore[index]
    assert again == first
    changed = allstar_document(_nodes(4), today=date(2026, 11, 12), previous=first)
    assert changed["generated"] == "2026-11-12"


def test_cli_writes_sorted_json_with_newline(tmp_path: Path) -> None:
    result = _run(tmp_path, _text(1200))
    assert result.exit_code == 0, result.output
    text = (tmp_path / "out" / ALLSTAR_FILE).read_text(encoding="utf-8")
    assert text.endswith("\n")
    doc = json.loads(text)
    assert doc["count"] == 1200
    assert text == json.dumps(doc, sort_keys=True, indent=2) + "\n"


def test_html_200_keeps_previous(tmp_path: Path) -> None:
    path, before = _seed(tmp_path, 1500)
    result = _run(tmp_path, b"<html>error</html>")
    assert result.exit_code == 1
    assert path.read_bytes() == before


def test_html_200_with_no_previous_writes_nothing(tmp_path: Path) -> None:
    result = _run(tmp_path, b"<html>error</html>")
    assert result.exit_code == 1
    assert not (tmp_path / "out" / ALLSTAR_FILE).exists()


def test_shrink_guard_keeps_previous(tmp_path: Path) -> None:
    path, before = _seed(tmp_path, 2000)
    result = _run(tmp_path, _text(1200))  # 1200 < 2000 * 0.66, but >= MIN_NODES
    assert result.exit_code == 0, result.output
    assert "shrank" in result.output
    assert path.read_bytes() == before


def test_fetch_error_keeps_previous(tmp_path: Path) -> None:
    path, before = _seed(tmp_path, 1500)
    result = _run(tmp_path, urllib.error.URLError("boom"))
    assert result.exit_code == 1
    assert path.read_bytes() == before


@pytest.mark.parametrize("n", [999])
def test_below_min_nodes_fails_without_previous(tmp_path: Path, n: int) -> None:
    result = _run(tmp_path, _text(n))
    assert result.exit_code == 1
    assert not (tmp_path / "out" / ALLSTAR_FILE).exists()
