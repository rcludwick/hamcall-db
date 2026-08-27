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
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hamcall_db import build_reflectors as reflectors_build
from hamcall_db import reflectors
from hamcall_db.reflectors import ReflectorRecord, merge_by_id, network_document, write_api
from hamcall_db.sources import dvref, xlx
from hamcall_db.sources.dvref import DvrefAuthError, DvrefSource
from hamcall_db.sources.xlx import XlxSource, dextra_callsign, sanitize_xml

REPO_ROOT = Path(__file__).parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "reflectors"
XLX_LIST = FIXTURES / "xlx_list.xml"
DVREF_YSF = FIXTURES / "dvref_ysf.json"
DVREF_NXDN = FIXTURES / "dvref_nxdn.json"

# Deliberately looser than the redaction pattern in hamcall_db.reflectors: a test that
# reuses the implementation's own regex cannot catch the case where that regex is the
# thing that is wrong.
EMAIL_SHAPED = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

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


def _dvref_doc(segment: str, network: str, fixture: Path) -> dict[str, object]:
    source = DvrefSource(segment, token="t")
    records = list(source.parse(fixture))
    return network_document(
        network,
        records,
        source_name="DVRef",
        source_url="https://dvref.com/api/v2/",
        attribution=source.attribution,
        generated=NOW.date(),
    )


def _documents() -> dict[str, dict[str, object]]:
    """The three fixture networks as published documents — a whole miniature API."""
    return {
        "dstar": _doc(),
        "ysf": _dvref_doc("ysf", "ysf", DVREF_YSF),
        "nxdn": _dvref_doc("nxdn", "nxdn", DVREF_NXDN),
    }


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
    assert all("modules" not in row["dial"] for row in rows)  # D-Star rows carry none
    assert all(None not in row.values() for row in rows)
    assert all(None not in row["dial"].values() for row in rows)


def test_rebuild_is_byte_identical_when_nothing_changed(tmp_path: Path) -> None:
    # Stability is what lets the publish step skip a no-op commit, which is what keeps
    # a nightly job from filling the site's history with churn. Every emitted file has
    # to hold that line, not just the network files — a churning manifest or openapi
    # document would force the commit just as effectively.
    written = write_api(tmp_path, _documents(), generated=NOW.date())
    first = {path: path.read_bytes() for path in written}
    write_api(tmp_path, _documents(), generated=NOW.date())
    assert {path: path.read_bytes() for path in written} == first


def test_write_api_emits_the_whole_v1_layout(tmp_path: Path) -> None:
    written = write_api(tmp_path, _documents(), generated=NOW.date())
    assert [p.relative_to(tmp_path).as_posix() for p in written] == [
        "index.json",
        "reflectors.json",
        "openapi.json",
        "reflectors/dstar.json",
        "reflectors/nxdn.json",
        "reflectors/ysf.json",
    ]

    manifest = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert manifest["api_version"] == reflectors.API_VERSION
    assert manifest["networks"]["dstar"]["url"] == "reflectors/dstar.json"
    assert manifest["networks"]["dstar"]["count"] == 3
    assert manifest["client_refresh_days"] == reflectors.CLIENT_REFRESH_DAYS


def test_manifest_advertises_the_other_endpoints(tmp_path: Path) -> None:
    # A client should be able to find reflectors.json and the contract from the one file
    # it is told to fetch first, rather than hard-coding paths it cannot see move.
    manifest = reflectors.manifest_document(_documents(), generated=NOW.date())
    endpoints = manifest["endpoints"]
    assert isinstance(endpoints, dict)
    assert endpoints["all"] == "reflectors.json"
    assert endpoints["openapi"] == "openapi.json"
    assert endpoints["network"] == "reflectors/{network}.json"
    assert manifest["count"] == 9  # 3 + 3 + 3, the sum of the network counts


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
        # D-Star is absent: DVRef retired those listings. The dormant branch in
        # _identity is covered by test_dvref_rejects_the_retired_dstar_segment.
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


# --- v1 entry shape: envelope + discriminated dial -------------------------------
# docs/REFLECTOR-API.md is the contract these pin. The envelope is what you search and
# display; `dial` is what you connect with, and it differs per protocol because the
# protocols genuinely differ.


def test_entry_is_an_envelope_plus_a_dial() -> None:
    record = next(r for r in _xlx_records() if r.id == "XLX836")
    entry = reflectors.entry_json(record)

    assert entry["network"] == "dstar"  # network + id is the primary key
    assert entry["id"] == "XLX836"
    assert entry["dial"] == {
        "kind": "dextra",
        "host": "45.56.69.219",
        "port": 30001,
        "callsign": "XRF836",
    }
    # Connect details live under `dial` and nowhere else: a client that switches on
    # `kind` must not find a second, undiscriminated copy at top level to guess from.
    assert not {"host", "port", "callsign", "modules"} & set(entry)


@pytest.mark.parametrize(("network", "kind"), sorted(reflectors.DIAL_KINDS.items()))
def test_dial_kind_per_network(network: str, kind: str) -> None:
    assert reflectors.dial_kind(network) == kind


def test_unmapped_network_still_gets_a_coherent_kind() -> None:
    # Falling back to the network's own name keeps the entry readable; the OpenAPI
    # coverage test below is what makes the omission loud.
    assert reflectors.dial_kind("tetra") == "tetra"


def test_dial_is_absent_when_there_is_nothing_to_dial() -> None:
    # "Listed but not dialable" is a real state. Inventing a default port to fill the
    # gap would point a client at the wrong socket, which is worse than a greyed row.
    no_host = ReflectorRecord(id="00001", network="ysf", name="No address")
    no_port = ReflectorRecord(id="00002", network="ysf", name="No port", host="ysf.example.org")
    assert "dial" not in reflectors.entry_json(no_host)
    assert "dial" not in reflectors.entry_json(no_port)


def test_urf_dials_without_a_port() -> None:
    # DVRef publishes no port for ANY of the 89 URF reflectors — a urfd speaks several
    # protocols at once, so there is no single port to carry. The entry is still
    # dialable, so `port` is optional for this kind and required for every other.
    record = ReflectorRecord(id="003", network="urf", host="urf.example.org", modules=["A", "B"])
    assert reflectors.entry_json(record)["dial"] == {
        "kind": "urf",
        "host": "urf.example.org",
        "modules": ["A", "B"],
    }
    assert "urf" in reflectors.DIAL_PORT_OPTIONAL


def test_dial_carries_only_the_fields_its_kind_defines() -> None:
    # A YSF reflector has no modules and no wire callsign; emitting them because the
    # record happens to have the attributes would put fields in the file that the
    # published schema for that kind does not define.
    record = ReflectorRecord(
        id="00006",
        network="ysf",
        host="ysf.example.org",
        port=42000,
        callsign="NOPE",
        modules=["A"],
    )
    assert reflectors.entry_json(record)["dial"] == {
        "kind": "ysf",
        "host": "ysf.example.org",
        "port": 42000,
    }


def test_name_falls_back_to_the_id() -> None:
    entry = reflectors.entry_json(ReflectorRecord(id="00009", network="ysf"))
    assert entry["name"] == "00009"


# --- aliases ---------------------------------------------------------------------


def test_xlx_rows_carry_the_xrf_form_as_an_alias() -> None:
    record = next(r for r in _xlx_records() if r.id == "XLX836")
    assert record.aliases == ["XRF836"]
    assert reflectors.entry_json(record)["aliases"] == ["XRF836"]


def test_an_alias_is_never_a_merge_key() -> None:
    # XLX002 answers to XRF002 on the wire, so XRF002 is a genuine alias OF THAT BOX.
    # A standalone XRF002 is a different machine that merely shares the number, and it
    # keeps its own entry: merging on aliases would send an operator to a reflector on
    # another continent. Measured 2026-08-26: 13 of 14 sampled pairs differed.
    xlx_row = ReflectorRecord(
        id="XLX002", network="dstar", aliases=["XRF002"], host="60.169.240.97"
    )
    xrf_row = ReflectorRecord(id="XRF002", network="dstar", host="52.36.45.107")
    merged = merge_by_id([xlx_row], [xrf_row])
    assert [r.id for r in merged] == ["XLX002", "XRF002"]
    assert [r.host for r in merged] == ["60.169.240.97", "52.36.45.107"]


# --- the combined file -----------------------------------------------------------


def test_combined_file_holds_every_network_in_one_sorted_list() -> None:
    combined = reflectors.combined_document(_documents(), generated=NOW.date())
    entries = combined["reflectors"]
    assert isinstance(entries, list)
    assert combined["count"] == len(entries) == 9
    assert combined["networks"] == {"dstar": 3, "nxdn": 3, "ysf": 3}
    keys = [(e["network"], e["id"]) for e in entries]
    assert keys == sorted(keys)  # stable order, or an unchanged rebuild churns
    assert {e["network"] for e in entries} == {"dstar", "nxdn", "ysf"}


def test_combined_file_credits_every_upstream_it_contains() -> None:
    # CC BY attribution has to survive the merge into one file: a reader of
    # reflectors.json must not have to fetch the per-network files to learn who to credit.
    combined = reflectors.combined_document(_documents(), generated=NOW.date())
    attribution = combined["attribution"]
    assert isinstance(attribution, str)
    assert "XLX registry" in attribution
    assert "DVRef" in attribution
    assert combined["license"] == reflectors.REFLECTOR_LICENSE
    assert combined["modifications"] == reflectors.MODIFICATION_NOTE
    sources = combined["sources"]
    assert isinstance(sources, list)
    assert {s["name"] for s in sources} == {"XLX registry (LX1IQ)", "DVRef"}


def test_combined_file_is_built_from_what_is_published() -> None:
    # Assembled from the DOCUMENTS, not from a second parse — so on a day the shrink
    # guard keeps a previous network file, reflectors.json describes what is live.
    documents = _documents()
    documents["ysf"]["reflectors"] = [
        {"network": "ysf", "id": "99999", "name": "kept from yesterday", "source": "dvref"}
    ]
    documents["ysf"]["count"] = 1
    combined = reflectors.combined_document(documents, generated=NOW.date())
    ids = [e["id"] for e in combined["reflectors"] if e["network"] == "ysf"]
    assert ids == ["99999"]


# --- OpenAPI ---------------------------------------------------------------------


def test_openapi_document_is_valid_openapi() -> None:
    from openapi_spec_validator import validate

    validate(reflectors.openapi_document())  # raises on anything malformed


def test_openapi_models_dial_as_a_discriminated_union() -> None:
    schemas = reflectors.openapi_document()["components"]["schemas"]
    dial = schemas["Dial"]
    assert dial["discriminator"]["propertyName"] == "kind"
    assert {ref["$ref"] for ref in dial["oneOf"]} == set(dial["discriminator"]["mapping"].values())


def test_openapi_covers_every_dial_kind_the_build_can_emit() -> None:
    # THE ANTI-DRIFT TEST. The kinds are derived, never listed here: from the networks
    # the importers can actually produce, from the emitter's own table, and from the
    # kinds present in documents built from the real fixtures. Add a network or a kind
    # without touching the published contract and this fails.
    networks = {*dvref.NETWORKS.values(), xlx.XlxSource.network}
    from_sources = {reflectors.dial_kind(n) for n in networks}
    from_table = set(reflectors.DIAL_KINDS.values())
    from_data = {
        entry["dial"]["kind"]
        for document in _documents().values()
        for entry in document["reflectors"]
        if "dial" in entry
    }
    kinds = from_sources | from_table | from_data
    assert from_data  # the fixtures must actually exercise this

    document = reflectors.openapi_document()
    schemas = document["components"]["schemas"]
    mapping = schemas["Dial"]["discriminator"]["mapping"]
    variants = {ref["$ref"] for ref in schemas["Dial"]["oneOf"]}

    for kind in sorted(kinds):
        assert kind in mapping, f"dial.kind {kind!r} is emitted but undocumented"
        ref = mapping[kind]
        assert ref in variants
        variant = schemas[ref.rsplit("/", 1)[-1]]
        assert variant["properties"]["kind"]["const"] == kind
        assert "host" in variant["properties"]
        for extra in reflectors.DIAL_FIELDS.get(kind, ()):
            assert extra in variant["properties"], f"{kind}.{extra} is emitted but undocumented"


def test_every_emitted_field_appears_in_the_openapi_schemas() -> None:
    # The other direction of the same guarantee: a field the emitter adds to an entry
    # or to a dial has to exist in the schema a client generates its types from.
    document = reflectors.openapi_document()
    schemas = document["components"]["schemas"]
    envelope = set(schemas["Reflector"]["properties"])
    mapping = schemas["Dial"]["discriminator"]["mapping"]

    for document in _documents().values():
        for entry in document["reflectors"]:
            assert set(entry) <= envelope, f"undocumented envelope field in {entry['id']}"
            dial = entry.get("dial")
            if dial is None:
                continue
            variant = schemas[mapping[dial["kind"]].rsplit("/", 1)[-1]]
            assert set(dial) <= set(variant["properties"])


def test_openapi_carries_the_licence_and_attribution() -> None:
    # A generated client that reads only the contract still learns the terms.
    info = reflectors.openapi_document()["info"]
    assert info["license"]["identifier"] == reflectors.REFLECTOR_LICENSE_SPDX
    assert "DVRef" in info["description"]
    assert "XLX registry" in info["description"]
    assert reflectors.MODIFICATION_NOTE in info["description"]


def test_openapi_is_written_as_part_of_the_build(tmp_path: Path) -> None:
    write_api(tmp_path, _documents(), generated=NOW.date())
    written = json.loads((tmp_path / "openapi.json").read_text(encoding="utf-8"))
    assert written == reflectors.openapi_document()  # generated, so it cannot drift
    assert written["servers"][0]["url"].endswith(f"/api/{reflectors.API_VERSION}")


# --- email redaction -------------------------------------------------------------
# The published files are bulk-downloadable JSON on a CDN. A sysop's address in a
# reflector blurb becomes a harvestable list there, which is the same class of thing as
# the street addresses and 6-character grids this project already refuses to publish.


def test_fixtures_really_contain_email_addresses() -> None:
    # Guards the guard: without an address in the fixtures the redaction tests below
    # would pass while testing nothing. (Reserved example.* domains, never a real one.)
    assert EMAIL_SHAPED.search(XLX_LIST.read_text(encoding="utf-8"))
    assert EMAIL_SHAPED.search(DVREF_NXDN.read_text(encoding="utf-8"))


def test_no_published_entry_carries_an_email_address() -> None:
    for document in _documents().values():
        for entry in document["reflectors"]:
            for value in entry.values():
                if isinstance(value, str):
                    assert not EMAIL_SHAPED.search(value), entry


def test_redaction_leaves_the_sentence_readable() -> None:
    assert (
        reflectors.redact_emails("DStar reflector, contact w1abc@example.com for access")
        == "DStar reflector, contact [email removed] for access"
    )


@pytest.mark.parametrize(
    "text",
    [
        "DStar <> DMR TG22208 BM2222",  # bare @-less text
        "XLX105 <-> REF018C",
        "http://xlx.n7mky.com",  # a URL must survive intact
        "https://xlx138.freeddns.org/",
        "TG@22208",  # an @ without a domain is not an address
        "Cape Agulhas - KF05ae",
    ],
)
def test_redaction_leaves_non_addresses_alone(text: str) -> None:
    assert reflectors.redact_emails(text) == text


def test_redaction_is_applied_by_the_record_itself() -> None:
    # One choke point, so a future importer cannot leak an address by forgetting a call
    # — and so the Parquet and SQLite artifacts are covered as well as the JSON.
    record = ReflectorRecord(
        id="00001",
        network="ysf",
        name="net w1abc@example.com",
        sponsor="W1ABC w1abc@example.com",
        description="mail w1abc@example.com",
    )
    assert record.name == "net [email removed]"
    assert record.sponsor == "W1ABC [email removed]"
    assert record.description == "mail [email removed]"


def test_modification_note_declares_the_redaction() -> None:
    # CC BY 4.0 requires stating that the material was changed. Removing addresses is a
    # change to the VALUES, not just the structure, so it has to be declared — in the
    # files themselves and in the licence file that travels with the artifacts.
    assert "Email addresses have been removed" in reflectors.MODIFICATION_NOTE
    licence = (REPO_ROOT / "LICENSE-CC-BY").read_text(encoding="utf-8")
    assert "email" in licence.lower()
    assert reflectors.EMAIL_PLACEHOLDER in licence


# --- losing a whole source without tripping the shrink guard ---------------------


def _dstar_doc(xlx_rows: int, dvref_rows: int) -> dict[str, object]:
    """A D-Star document assembled from both sources, as the build produces it."""
    records = [
        ReflectorRecord(id=f"XLX{i:03d}", network="dstar", host=f"10.0.0.{i}", source="xlx")
        for i in range(xlx_rows)
    ] + [
        ReflectorRecord(id=f"XRF{i:03d}", network="dstar", host=f"10.1.0.{i}", source="dvref")
        for i in range(dvref_rows)
    ]
    return network_document(
        "dstar",
        records,
        source_name="XLX registry (LX1IQ) + DVRef",
        source_url="http://xlxapi.rlx.lu/",
        attribution="test",
        generated=NOW.date(),
    )


def test_source_counts_reads_provenance_out_of_a_document() -> None:
    assert reflectors_build._source_counts(_dstar_doc(892, 61)) == {"xlx": 892, "dvref": 61}
    assert reflectors_build._source_counts(None) == {}
    assert reflectors_build._source_counts({"reflectors": "not a list"}) == {}


def test_losing_one_source_is_caught_even_though_the_shrink_guard_would_not_fire(
    tmp_path: Path,
) -> None:
    # The real incident: DVRef answered with a well-formed response carrying zero
    # rows, so D-Star rebuilt from XLX alone. 953 -> 892 is a 6% drop, far inside
    # the 66% shrink guard, and it published an XLX-only D-Star as if that were
    # the whole network.
    write_api(tmp_path, {"dstar": _dstar_doc(892, 61)}, generated=NOW.date())

    complete = reflectors_build._source_counts(_dstar_doc(892, 61))
    degraded = reflectors_build._source_counts(_dstar_doc(892, 0))

    lost = [n for n, c in complete.items() if c > 0 and degraded.get(n, 0) == 0]
    assert lost == ["dvref"], "losing DVRef entirely must be detectable"

    # And the shrink guard alone would NOT have caught it — that is the point.
    assert 892 >= 953 * reflectors_build.SHRINK_GUARD


def test_a_source_merely_shrinking_is_not_treated_as_lost() -> None:
    # Reflectors do come and go. Only losing a source ENTIRELY is the signal;
    # otherwise every quiet night would refuse to publish.
    complete = reflectors_build._source_counts(_dstar_doc(892, 61))
    fewer = reflectors_build._source_counts(_dstar_doc(890, 58))
    lost = [n for n, c in complete.items() if c > 0 and fewer.get(n, 0) == 0]
    assert lost == []


# --- attribution must credit each upstream exactly once -------------------------


def test_combined_attribution_credits_each_upstream_once() -> None:
    # D-Star is assembled from two upstreams so its credit is compound; every other
    # network carries DVRef's line alone. Deduplicating whole strings is not enough —
    # the compound credit CONTAINS the single one without equalling it, which put
    # DVRef in the published combined file twice.
    dvref = "Reflector data provided by DVRef — https://dvref.com/"
    xlx = "XLX reflector data from the XLX registry maintained by Luc Engelmann, LX1IQ."

    dstar = network_document(
        "dstar",
        [ReflectorRecord(id="XLX836", network="dstar", host="10.0.0.1", source="xlx")],
        source_name="XLX registry (LX1IQ) + DVRef",
        source_url="http://xlxapi.rlx.lu/",
        attribution=reflectors.ATTRIBUTION_SEPARATOR.join([xlx, dvref]),
        generated=NOW.date(),
    )
    m17 = network_document(
        "m17",
        [ReflectorRecord(id="M17-M17", network="m17", host="10.0.0.2", source="dvref")],
        source_name="DVRef",
        source_url="https://dvref.com/api/v2/",
        attribution=dvref,
        generated=NOW.date(),
    )

    combined = reflectors.combined_document({"dstar": dstar, "m17": m17}, generated=NOW.date())
    credit = combined["attribution"]
    assert isinstance(credit, str)

    assert credit.count("provided by DVRef") == 1, credit
    assert credit.count("XLX registry") == 1, credit
    # Both upstreams must still be credited — deduplicating must not drop one.
    assert dvref in credit
    assert xlx in credit


def test_attribution_statements_survive_a_round_trip() -> None:
    # The separator has to be something no attribution statement contains, or
    # splitting would tear a credit in half.
    dvref = "Reflector data provided by DVRef — https://dvref.com/"
    xlx = "XLX reflector data from the XLX registry maintained by Luc Engelmann, LX1IQ."
    for statement in (dvref, xlx):
        assert reflectors.ATTRIBUTION_SEPARATOR not in statement
    joined = reflectors.ATTRIBUTION_SEPARATOR.join([xlx, dvref])
    assert joined.split(reflectors.ATTRIBUTION_SEPARATOR) == [xlx, dvref]


# --- a retired source is not a lost source --------------------------------------


def test_notice_is_read_from_the_response() -> None:
    # DVRef explains a deliberately empty result in `data.notice`. This is how we
    # learned the D-Star listings were switched off rather than broken, so the
    # field must not go unread.
    payload = {
        "status": "success",
        "data": {"reflectors": [], "notice": "D-Star reflector listings are currently disabled."},
    }
    assert dvref.payload_notice(payload) == "D-Star reflector listings are currently disabled."
    assert dvref.payload_notice({"data": {"reflectors": []}}) is None
    assert dvref.payload_notice("not a dict") is None


def test_dvref_is_no_longer_a_dstar_source() -> None:
    # DVRef disabled D-Star listings; the endpoint answers 200 with an empty list
    # forever. Keeping it configured would make every build look like an upstream
    # failure and freeze D-Star to protect 61 rows that are not coming back.
    assert "dstar" not in dvref.NETWORKS
    assert "dstar" not in dvref.NETWORKS.values()
    # The networks it does still serve must be untouched.
    assert set(dvref.NETWORKS.values()) == {"m17", "ysf", "nxdn", "p25", "urf"}


def test_a_retired_source_is_not_treated_as_lost() -> None:
    # The guard freezes a network when a source it TRIED returns nothing. A source
    # that is no longer configured was retired on purpose, and the one-time drop
    # is the intended outcome — otherwise D-Star would never update again.
    previous_sources = {"xlx": 892, "dvref": 61}
    fresh_sources = {"xlx": 892}

    # Retired: dvref was not attempted, so nothing is lost.
    tried_now = {"xlx"}
    assert [
        n
        for n, c in previous_sources.items()
        if c > 0 and n in tried_now and fresh_sources.get(n, 0) == 0
    ] == []

    # Failed: dvref WAS attempted and produced nothing — that is still a fault.
    tried_before = {"xlx", "dvref"}
    assert [
        n
        for n, c in previous_sources.items()
        if c > 0 and n in tried_before and fresh_sources.get(n, 0) == 0
    ] == ["dvref"]


def test_dvref_rejects_the_retired_dstar_segment() -> None:
    # Constructing a source for a segment that is no longer served should fail
    # loudly at the call site rather than quietly fetching an always-empty list.
    with pytest.raises(ValueError, match="unknown DVRef segment"):
        DvrefSource("dstar", token="t")


# --- upstream asking us to slow down --------------------------------------------


def _http_error(code: int, body: str, headers: dict[str, str] | None = None):
    import io
    import urllib.error

    return urllib.error.HTTPError(
        url="https://dvref.com/api/v2/ysf/reflectors/",
        code=code,
        msg="Too Many Requests",
        hdrs=headers or {},  # type: ignore[arg-type]
        fp=io.BytesIO(body.encode("utf-8")),
    )


def test_a_throttle_preserves_the_wait_upstream_named() -> None:
    # DVRef answers 429 with the exact number of seconds to wait. Treating that
    # as a generic failure throws the answer away and skips the network for the
    # night over something that resolves itself.
    exc = _http_error(
        429,
        '{"detail": "Authenticated DVRef API clients are limited to 60 requests per hour.",'
        ' "retry_after_seconds": 1114}',
    )
    throttled = dvref._throttled(exc)
    assert isinstance(throttled, dvref.DvrefThrottled)
    assert throttled.retry_after == 1114
    assert "1114" in str(throttled)


def test_a_throttle_falls_back_to_the_retry_after_header() -> None:
    exc = _http_error(429, "not json at all", {"Retry-After": "300"})
    assert dvref._throttled(exc).retry_after == 300


def test_a_throttle_without_a_stated_wait_is_still_a_throttle() -> None:
    # Missing guidance must not become a crash or a silent zero.
    assert dvref._throttled(_http_error(429, "")).retry_after is None


def test_throttling_is_not_reported_as_an_auth_problem() -> None:
    # The two have different remedies: one is "wait", the other is "your token
    # is wrong". Conflating them is what sent me hunting a secrets problem that
    # did not exist.
    assert not issubclass(dvref.DvrefThrottled, dvref.DvrefAuthError)
    assert not issubclass(dvref.DvrefAuthError, dvref.DvrefThrottled)


def test_the_published_rate_limit_is_recorded_where_it_binds() -> None:
    assert dvref.AUTHENTICATED_HOURLY_LIMIT == 60
