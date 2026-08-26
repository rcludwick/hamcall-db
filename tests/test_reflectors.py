"""Tests for the digital-voice reflector directory (hdb-refl).

A SEPARATE dataset from the callsign Record schema — reflectors are places, not
licensees — and under a SEPARATE licence (CC BY 4.0, not the project's CC BY-NC).
Several tests below exist specifically to keep those two facts from drifting.

All offline: parsing drives off the checked-in fixtures under tests/fixtures/reflectors/
(real rows sliced from live responses), and the downloaders take an injectable fetcher,
so no test touches the network or needs a DVRef token.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hamcall_db import reflectors
from hamcall_db.reflectors import ReflectorRecord, merge_by_id, network_document, write_api
from hamcall_db.sources import dvref, xlx
from hamcall_db.sources.dvref import DvrefAuthError, DvrefSource
from hamcall_db.sources.xlx import XlxSource, dextra_callsign, sanitize_xml

FIXTURES = Path(__file__).parent / "fixtures" / "reflectors"
XLX_LIST = FIXTURES / "xlx_list.xml"
DVREF_YSF = FIXTURES / "dvref_ysf.json"
DVREF_NXDN = FIXTURES / "dvref_nxdn.json"

# The fixture rows were captured 2026-08-26; freeze "now" so the staleness filter is
# deterministic rather than a slow-motion time bomb that starts dropping rows later.
NOW = datetime(2026, 8, 26, tzinfo=UTC)


# --- XML sanitation -------------------------------------------------------------
# The registry emits comment text verbatim, which makes the document invalid XML.


def test_sanitize_escapes_bare_lt() -> None:
    assert sanitize_xml(b"<c>DStar <> DMR</c>") == b"<c>DStar &lt;> DMR</c>"


def test_sanitize_escapes_bare_ampersand() -> None:
    assert sanitize_xml(b"<c>R&D</c>") == b"<c>R&amp;D</c>"


def test_sanitize_preserves_real_tags_and_entities() -> None:
    raw = b"<reflector><name>XLX001</name><c>a &amp; b &#39;q&#39;</c></reflector>"
    assert sanitize_xml(raw) == raw


def test_fixture_really_is_malformed_xml() -> None:
    # Guards the guard: if the fixture ever loses its unescaped '<', the sanitizer
    # tests above would still pass while testing nothing that matters.
    from xml.etree import ElementTree

    with pytest.raises(ElementTree.ParseError):
        ElementTree.fromstring(XLX_LIST.read_bytes())


# --- XLX -> DExtra callsign -----------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("XLX836", "XRF836"),  # numeric suffix
        ("XLXARG", "XRFARG"),  # alphabetic suffix — 136 of 892 look like this
        ("XLX00A", "XRF00A"),  # mixed
        ("xlx836", "XRF836"),  # case-folded
        (" XLX836 ", "XRF836"),  # trimmed
        ("REF001", None),  # not an XLX name
        ("XLX1234", None),  # wrong suffix length
        ("", None),
    ],
)
def test_dextra_callsign(name: str, expected: str | None) -> None:
    assert dextra_callsign(name) == expected


# --- XLX parsing ----------------------------------------------------------------


def _xlx_records() -> list[ReflectorRecord]:
    source = XlxSource()
    return list(source.parse(XLX_LIST, synced_at="2026-08-26", now=NOW))


def test_xlx_parses_every_fixture_row() -> None:
    records = _xlx_records()
    assert [r.id for r in records] == ["XLX001", "XLX836", "XLXACP"]


def test_xlx_sets_dextra_port_and_callsign() -> None:
    record = next(r for r in _xlx_records() if r.id == "XLX836")
    assert record.network == "dstar"
    assert record.host == "45.56.69.219"
    assert record.port == 30001  # protocol constant, not published upstream
    assert record.callsign == "XRF836"
    assert record.source == "xlx"


def test_xlx_keeps_comment_containing_bare_angle_bracket() -> None:
    # XLX001's comment is the row that breaks a strict parser; the description must
    # survive sanitation rather than the row being dropped.
    record = next(r for r in _xlx_records() if r.id == "XLX001")
    assert record.description and "<" in record.description


def test_xlx_drops_stale_registrations() -> None:
    source = XlxSource()
    far_future = datetime(2030, 1, 1, tzinfo=UTC)
    assert list(source.parse(XLX_LIST, now=far_future)) == []


def test_xlx_download_uses_injected_fetcher_and_caches(tmp_path: Path) -> None:
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return XLX_LIST.read_bytes()

    source = XlxSource(fetch=fetch)
    first = source.download(tmp_path)
    source.download(tmp_path)  # same day -> must not refetch
    assert first.exists()
    assert len(calls) == 1


# --- DVRef ----------------------------------------------------------------------


def test_dvref_rows_reads_the_live_envelope() -> None:
    payload = json.loads(DVREF_YSF.read_text(encoding="utf-8"))
    assert len(dvref._rows(payload)) == 3


@pytest.mark.parametrize(
    "payload",
    [
        [{"a": 1}],
        {"results": [{"a": 1}]},
        {"data": {"reflectors": [{"a": 1}]}},
        {"data": [{"a": 1}]},
    ],
)
def test_dvref_rows_tolerates_envelope_variants(payload: object) -> None:
    # The OpenAPI schema documents these endpoints as "No response body", so the
    # envelope is observed, not contractual. Degrade to fewer rows, never crash.
    assert dvref._rows(payload) == [{"a": 1}]


def test_dvref_rows_of_unknown_shape_is_empty_not_error() -> None:
    assert dvref._rows({"unexpected": "shape"}) == []


def test_dvref_parses_string_designators() -> None:
    source = DvrefSource("ysf", token="t")
    records = list(source.parse(DVREF_YSF))
    assert len(records) == 3
    assert all(r.network == "ysf" for r in records)
    first = records[0]
    assert first.id == "00006"
    assert first.port == 42000
    assert first.host  # dns preferred over ipv4


def test_dvref_parses_numeric_designators() -> None:
    # NXDN and P25 publish `designator` as a JSON NUMBER. Treating only strings as
    # valid silently dropped 55 NXDN and 51 P25 reflectors — it looked like upstream
    # having fewer rows, not like a bug.
    source = DvrefSource("nxdn", token="t")
    records = list(source.parse(DVREF_NXDN))
    assert len(records) == 3
    assert all(r.id and r.id.isdigit() for r in records)


def test_dvref_prefers_hostname_over_address() -> None:
    source = DvrefSource("ysf", token="t")
    rows = json.loads(DVREF_YSF.read_text(encoding="utf-8"))["data"]["reflectors"]
    with_dns = next((r for r in rows if r.get("dns")), None)
    assert with_dns is not None, "fixture should contain a row with a dns name"
    record = next(r for r in source.parse(DVREF_YSF) if r.id == str(with_dns["designator"]))
    assert record.host == with_dns["dns"]


def test_dvref_uses_upstream_attribution_string() -> None:
    # CC BY requires attribution; DVRef ships the exact wording in the response, so we
    # publish theirs rather than a copy that can drift.
    source = DvrefSource("ysf", token="t")
    list(source.parse(DVREF_YSF))
    assert source.attribution == "Reflector data provided by DVRef — https://dvref.com/"


def test_dvref_synced_at_comes_from_upstream_generated_at() -> None:
    source = DvrefSource("ysf", token="t")
    record = next(iter(source.parse(DVREF_YSF)))
    assert record.synced_at == "2026-08-26"


def test_dvref_without_token_refuses_before_touching_the_network(tmp_path: Path) -> None:
    def fetch(url: str, token: str) -> bytes:  # pragma: no cover - must not run
        raise AssertionError("must not fetch without a token")

    source = DvrefSource("ysf", token="", fetch=fetch)
    with pytest.raises(DvrefAuthError):
        source.download(tmp_path)


def test_dvref_rejects_unknown_segment() -> None:
    with pytest.raises(ValueError, match="unknown DVRef segment"):
        DvrefSource("pota", token="t")


def test_dvref_modules_normalized() -> None:
    assert dvref._modules(["a", "B", "b", {"module": "c"}, "", "toolong", 7]) == ["A", "B", "C"]


# --- merging --------------------------------------------------------------------


def test_merge_keeps_xrf_and_xlx_apart() -> None:
    # XRF002 and XLX002 are DIFFERENT machines that merely share a number: measured
    # 2026-08-26, 13 of 14 sampled pairs resolved to entirely different servers.
    # Collapsing them would send a user to a reflector on another continent.
    xlx_row = ReflectorRecord(id="XLX002", network="dstar", host="60.169.240.97")
    xrf_row = ReflectorRecord(id="XRF002", network="dstar", host="52.36.45.107")
    assert [r.id for r in merge_by_id([xlx_row], [xrf_row])] == ["XLX002", "XRF002"]


def test_merge_first_source_wins_a_real_collision() -> None:
    preferred = ReflectorRecord(id="XLX002", network="dstar", host="first")
    other = ReflectorRecord(id="XLX002", network="dstar", host="second")
    merged = merge_by_id([preferred], [other])
    assert len(merged) == 1
    assert merged[0].host == "first"


# --- published document ---------------------------------------------------------


def _doc() -> dict[str, object]:
    return network_document(
        "dstar",
        _xlx_records(),
        source_name="XLX registry (LX1IQ)",
        source_url="http://xlxapi.rlx.lu/api.php?do=GetReflectorList",
        attribution="XLX reflector data from the XLX registry.",
        generated=NOW.date(),
    )


def test_document_carries_licence_and_attribution() -> None:
    # CC BY 4.0 obliges attribution AND indicating modification. Both must be in the
    # file itself: an attribution that lives only in a README is one copy-paste from
    # being lost.
    doc = _doc()
    assert doc["license"] == "CC BY 4.0"
    assert doc["attribution"]
    assert doc["modifications"]


def test_document_is_sorted_and_counted() -> None:
    doc = _doc()
    rows = doc["reflectors"]
    assert isinstance(rows, list)
    assert doc["count"] == len(rows)
    assert [r["id"] for r in rows] == sorted(r["id"] for r in rows)


def test_document_omits_empty_fields() -> None:
    rows = _doc()["reflectors"]
    assert isinstance(rows, list)
    assert all("modules" not in row for row in rows)  # D-Star rows carry none
    assert all(None not in row.values() for row in rows)


def test_rebuild_is_byte_identical_when_nothing_changed(tmp_path: Path) -> None:
    # Stability is what lets the publish step skip a no-op commit, which is what keeps
    # a nightly job from filling the site's history with churn.
    write_api(tmp_path, {"dstar": _doc()}, generated=NOW.date())
    first = (tmp_path / "reflectors" / "dstar.json").read_bytes()
    write_api(tmp_path, {"dstar": _doc()}, generated=NOW.date())
    assert (tmp_path / "reflectors" / "dstar.json").read_bytes() == first


def test_write_api_emits_manifest_and_network_files(tmp_path: Path) -> None:
    written = write_api(tmp_path, {"dstar": _doc()}, generated=NOW.date())
    assert [p.name for p in written] == ["index.json", "dstar.json"]

    manifest = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert manifest["networks"]["dstar"]["url"] == "reflectors/dstar.json"
    assert manifest["networks"]["dstar"]["count"] == 3
    assert manifest["client_refresh_days"] == reflectors.CLIENT_REFRESH_DAYS


def test_manifest_expiry_follows_the_refresh_window() -> None:
    manifest = reflectors.manifest_document({"dstar": _doc()}, generated=NOW.date())
    assert manifest["expires_hint"] == "2026-09-02"  # 2026-08-26 + 7 days


# --- licence separation ---------------------------------------------------------


def test_reflector_licence_is_not_the_dataset_licence() -> None:
    # The whole reason this is a separate artifact: DVRef's CC BY 4.0 forbids adding
    # the CC BY-NC dataset's non-commercial restriction (CC BY 4.0 s2(a)(5)(B)).
    assert reflectors.REFLECTOR_LICENSE == "CC BY 4.0"
    assert "NC" not in reflectors.REFLECTOR_LICENSE


def test_reflector_record_is_not_the_callsign_schema() -> None:
    # The published callsign schema is a redistribution contract (CLAUDE.md); this
    # dataset must stay beside it, not inside it. Keyed on `id`, not `callsign` — and
    # note ReflectorRecord DOES have a `callsign` field meaning something else entirely
    # (the reflector's own on-air name), which is exactly why they must not be joined.
    from hamcall_db.models import Record

    callsign_columns = set(Record.__dataclass_fields__)
    reflector_columns = set(reflectors.REFLECTOR_SCHEMA_COLUMNS)

    assert reflector_columns != callsign_columns
    assert "id" in reflector_columns and "id" not in callsign_columns
    assert "callsign" in callsign_columns  # the licensee primary key
    # Nothing in the reflector schema carries licensee-identifying data.
    assert reflector_columns.isdisjoint({"first_name", "last_name", "postal_code", "frn"})


def test_dextra_port_is_the_protocol_constant() -> None:
    assert xlx.DEXTRA_PORT == 30001


# --- per-network naming ---------------------------------------------------------


@pytest.mark.parametrize(
    ("segment", "designator", "expected_id", "expected_callsign"),
    [
        # M17 designators are the suffix only; the reflector is dialled as M17-xxx,
        # which is also what Pi-Star's M17_Hosts.txt lists. "M17" is itself a
        # designator, giving the real reflector M17-M17.
        ("mrefd", "002", "M17-002", "M17-002"),
        ("mrefd", "M17", "M17-M17", "M17-M17"),
        ("mrefd", "M17-010", "M17-010", "M17-010"),  # already prefixed, not doubled
        # D-Star designators arrive already in XRF form and are their own callsign.
        ("dstar", "XRF002", "XRF002", "XRF002"),
        # Everything else is dialled by designator and has no separate wire callsign.
        ("ysf", "00006", "00006", None),
        ("nxdn", "12345", "12345", None),
    ],
)
def test_dvref_identity_per_network(
    segment: str, designator: str, expected_id: str, expected_callsign: str | None
) -> None:
    source = DvrefSource(segment, token="t")
    assert source._identity(designator) == (expected_id, expected_callsign)


# --- artifacts (Parquet / SQLite) -----------------------------------------------


def test_records_round_trip_through_a_document() -> None:
    # The artifacts are derived from the documents being PUBLISHED, so that on a day the
    # shrink guard keeps a previous file, the .parquet/.db describe what is actually live.
    original = _xlx_records()
    restored = reflectors.records_from_document(_doc())
    assert [r.id for r in restored] == sorted(r.id for r in original)
    assert {r.callsign for r in restored} == {r.callsign for r in original}
    assert all(r.network == "dstar" for r in restored)


def test_sqlite_artifact_contains_only_the_reflectors_table(tmp_path: Path) -> None:
    # Licence segregation, enforced: the CC BY 4.0 artifact must never carry a CC BY-NC
    # table, and vice versa.
    import sqlite3

    from hamcall_db.sqlite_writer import write_reflectors_sqlite

    db = tmp_path / "reflectors.db"
    written = write_reflectors_sqlite(_xlx_records(), db)
    assert written == 3

    con = sqlite3.connect(db)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()
    assert tables == {"reflectors"}


def test_sqlite_write_is_idempotent(tmp_path: Path) -> None:
    import sqlite3

    from hamcall_db.sqlite_writer import write_reflectors_sqlite

    db = tmp_path / "reflectors.db"
    write_reflectors_sqlite(_xlx_records(), db)
    write_reflectors_sqlite(_xlx_records(), db)

    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT count(*) FROM reflectors").fetchone()[0] == 3
    finally:
        con.close()


def test_parquet_artifact_round_trips(tmp_path: Path) -> None:
    import polars as pl

    from hamcall_db.writer import write_reflectors_parquet

    path = tmp_path / "reflectors.parquet"
    assert write_reflectors_parquet(_xlx_records(), path) == 3

    frame = pl.read_parquet(path)
    assert frame.columns == list(reflectors.REFLECTOR_SCHEMA_COLUMNS)
    assert frame["port"].dtype == pl.Int64
    assert set(frame["id"]) == {"XLX001", "XLX836", "XLXACP"}
