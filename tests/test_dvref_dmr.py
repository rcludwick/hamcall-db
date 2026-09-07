"""Tests for the DVRef DMR importer (hdb-refl-dmr).

DMR is the one network where an upstream row is not a reflector. DVRef publishes
NETWORKS, each holding zero or more SERVERS, and a server is what an operator actually
dials — so the flattening, and what happens to the rows that survive it, is the whole
subject of this file.

All offline: the checked-in fixture under tests/fixtures/dvref/ is a real slice of the
live response (10 of 172 networks, 44 servers, `_dvref_metadata` verbatim), and the
downloader takes an injectable fetcher, so nothing here touches the network or needs a
DVRef token.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hamcall_db import build_reflectors as reflectors_build
from hamcall_db import reflectors
from hamcall_db.reflectors import ReflectorRecord, entry_json, network_document, write_api
from hamcall_db.sources import dvref
from hamcall_db.sources.dvref import DvrefAuthError, DvrefDmrSource, DvrefSource

FIXTURE = Path(__file__).parent / "fixtures" / "dvref" / "dmr-networks.json"

# The fixture was captured 2026-09-06; freeze "now" so nothing here drifts with the clock.
NOW = datetime(2026, 9, 6, tzinfo=UTC)

# Deliberately looser than the redaction pattern in hamcall_db.reflectors: a test that
# reuses the implementation's own regex cannot catch the case where that regex is wrong.
EMAIL_SHAPED = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# What the fixture actually contains, counted from the file rather than asserted from
# memory — see test_the_fixture_is_the_shape_these_numbers_assume.
SERVERS_IN_FIXTURE = 44
DIALABLE_IN_FIXTURE = 35


def _records() -> list[ReflectorRecord]:
    return list(DvrefDmrSource(token="t").parse(FIXTURE))


def _entries() -> list[dict[str, object]]:
    return [entry_json(record) for record in _records()]


def _by_id(identifier: str) -> dict[str, object]:
    return next(entry for entry in _entries() if entry["id"] == identifier)


def _document() -> dict[str, object]:
    source = DvrefDmrSource(token="t")
    records = list(source.parse(FIXTURE))
    return network_document(
        "dmr",
        records,
        source_name="DVRef",
        source_url="https://dvref.com/api/v2/",
        attribution=source.attribution,
        generated=NOW.date(),
    )


# --- the fixture the rest of this file reasons about ------------------------------


def test_the_fixture_is_the_shape_these_numbers_assume() -> None:
    # Guards the guard. Every count below is derived from the file, so a re-slice that
    # changes the data fails here with an explanation rather than somewhere downstream
    # with an off-by-nine.
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    networks = payload["data"]["networks"]
    assert len(networks) == 10
    assert sum(len(n.get("servers") or []) for n in networks) == SERVERS_IN_FIXTURE
    # Networks with no server at all are a real state upstream: 61 of the live 172.
    assert [n["slug"] for n in networks if not n.get("servers")] == ["bm2222", "tgif165"]


# --- one row per SERVER -----------------------------------------------------------


def test_every_server_becomes_a_row() -> None:
    records = _records()
    assert len(records) == SERVERS_IN_FIXTURE
    assert all(r.network == "dmr" for r in records)
    assert all(r.source == "dvref" for r in records)


def test_the_id_is_the_server_slug_not_the_network() -> None:
    # A network is an organisation; the id has to name the thing you dial, and it has to
    # be unique inside `dmr` because (network, id) is the primary key.
    ids = [r.id for r in _records()]
    assert len(set(ids)) == len(ids)
    assert "freedmr-network-server-freedmr-cymru" in ids
    assert "freedmr-network" not in ids  # the network itself is never a row


def test_a_network_with_no_servers_publishes_nothing() -> None:
    # 61 of the live 172 networks are listings with no address behind them. A row you
    # cannot dial AND cannot place is not a directory entry.
    assert not [r for r in _records() if r.system in ("bm2222", "tgif165")]


def test_one_network_contributes_many_rows() -> None:
    freedmr = [r for r in _records() if r.system == "freedmr-network"]
    assert len(freedmr) == 20
    # Each row is a different server, and they share the network's metadata.
    assert len({r.id for r in freedmr}) == 20
    assert {r.sponsor for r in freedmr} == {"FreeDMR"}


# --- `system`, `requires`, `talkgroups_url` ---------------------------------------


def test_every_row_names_the_network_it_belongs_to() -> None:
    # A talkgroup number means nothing without the network it is defined in, so a
    # server row that did not carry `system` would not be enough to talk to anybody.
    assert all(r.system for r in _records())


def test_every_row_says_what_the_operator_must_supply() -> None:
    assert all(r.requires == ["dmr_id", "password"] for r in _records())


def test_talkgroups_are_linked_not_mirrored() -> None:
    # 172 per-network endpoints against a 60/hour budget: the directory points at
    # DVRef instead of copying them (hdb-refl-dmrtg).
    urls = {r.talkgroups_url for r in _records()}
    assert all(url and url.startswith("https://dvref.com/api/v2/dmr/networks/") for url in urls)


def test_talkgroup_and_timeslot_are_reserved_and_unset() -> None:
    # They exist in the schema so a later build can add talkgroup rows without a
    # version bump — a client can only ignore a field it has been told about.
    assert all(r.talkgroup is None and r.timeslot is None for r in _records())
    assert "talkgroup" in reflectors.REFLECTOR_SCHEMA_COLUMNS
    assert "timeslot" in reflectors.REFLECTOR_SCHEMA_COLUMNS


# --- the host rule: `dns` is not always a host ------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("cy.freedmr.cymru", "cy.freedmr.cymru"),
        ("  nedmr.com  ", "nedmr.com"),  # trimmed, like every other string field
        ("38.19.200.56", "38.19.200.56"),
        ("2001:a40:100:e:be24:11ff:feb3:b207", "2001:a40:100:e:be24:11ff:feb3:b207"),
        ("https://apollo.dmr.uk.pe/dashboard/", None),  # a dashboard URL, seen live
        ("http://wx6d.mw-dmr.net/dashboard/index.php", None),
        ("ipsc2.freestar.network/ipsc", None),  # host plus a path is not a host
        ("2001:db8::/64", None),  # a prefix is not an address
        ("dmr server.example.org", None),
        ("", None),
        (None, None),
        (62031, None),
    ],
)
def test_only_a_bare_hostname_or_address_is_a_host(value: object, expected: str | None) -> None:
    assert dvref._host(value) == expected


def test_a_dashboard_url_in_dns_leaves_the_row_undialable() -> None:
    # SystemX fills `dns` with its dashboards and publishes no port. The server is
    # still listed — it has a name, a country and a sponsor — but with no `dial`,
    # because the API never invents an address (rule 4).
    entry = _by_id("systemx-server-systemx-apollo")
    assert "dial" not in entry
    assert entry["name"] == "SystemX Apollo"
    assert entry["sponsor"] == "FreeSTAR Network"
    assert entry["dashboard"] == "https://freestar.network/systemx-dmr/"


def test_a_path_bearing_dns_falls_back_to_the_numeric_address() -> None:
    # `ipsc2.freestar.network/ipsc` is not resolvable; the ipv4 beside it is.
    entry = _by_id("dmrplus-ipsc2-uk-server-dmr-united-kingdom")
    assert entry["dial"]["host"] == "185.169.252.213"


def test_ipv4_is_preferred_over_ipv6_when_there_is_no_name() -> None:
    entry = _by_id("digital-voice-greece-server-digital-voice-greece")
    assert entry["dial"]["host"] == "38.19.200.56"


# --- the published entry ----------------------------------------------------------


def test_a_freedmr_server_dials_as_an_mmdvm_master() -> None:
    entry = _by_id("freedmr-network-server-freedmr-cymru")
    assert entry["dial"] == {
        "kind": "mmdvm",
        "host": "cy.freedmr.cymru",
        "port": 62031,
        "system": "freedmr-network",
        "requires": ["dmr_id", "password"],
        "talkgroups_url": "https://dvref.com/api/v2/dmr/networks/freedmr-network/talkgroups/",
    }
    # Reserved fields are OMITTED while unset, not published as null: a client that
    # reads `talkgroup` as "absent means unset" must not find a null to misread.
    assert "talkgroup" not in entry["dial"]
    assert "timeslot" not in entry["dial"]


def test_a_server_is_listed_even_when_it_cannot_be_dialled() -> None:
    entries = _entries()
    assert len(entries) == SERVERS_IN_FIXTURE
    assert sum(1 for e in entries if "dial" in e) == DIALABLE_IN_FIXTURE


def test_a_missing_port_alone_is_enough_to_drop_the_dial() -> None:
    # One FreeDMR server has a perfectly good hostname and no port. `mmdvm` is not in
    # DIAL_PORT_OPTIONAL — URF is the only kind that dials without one.
    entry = _by_id("freedmr-network-server-freemr_sardinia")
    assert "dial" not in entry
    assert "mmdvm" not in reflectors.DIAL_PORT_OPTIONAL


def test_the_server_inherits_its_network_metadata() -> None:
    entry = _by_id("new-england-dmr-server-nedmr")
    assert entry["sponsor"] == "New England Digital Radio KC1VAX"
    assert entry["dashboard"] == "http://monitor.newenglanddigitalradio.com/index.php"
    assert "9 talkgroups" in str(entry["description"])


def test_sponsor_falls_back_to_the_network_name(tmp_path: Path) -> None:
    # A network with no sponsor recorded is still run by somebody, and its display
    # name is the best answer available — better than an empty cell.
    payload = {
        "data": {
            "networks": [
                {
                    "network": "Example DMR",
                    "slug": "example-dmr",
                    "sponsor": None,
                    "servers": [{"server": "One", "slug": "example-dmr-one", "port": 62031}],
                }
            ]
        }
    }
    path = tmp_path / "dmr-networks.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    record = next(iter(DvrefDmrSource(token="t").parse(path)))
    assert record.sponsor == "Example DMR"


def test_rows_of_the_wrong_shape_are_skipped_not_fatal(tmp_path: Path) -> None:
    # The endpoint is documented upstream as "No response body", so the envelope is
    # observed. Degrade to fewer rows rather than crashing the nightly build.
    payload = {
        "data": {
            "networks": [
                {"slug": None, "servers": [{"slug": "orphan"}]},  # no network slug
                {"slug": "ok", "servers": "not a list"},
                {"slug": "ok2", "servers": ["not a dict", {"server": "no slug"}]},
            ]
        }
    }
    path = tmp_path / "dmr-networks.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert list(DvrefDmrSource(token="t").parse(path)) == []


# --- freshness, attribution, redaction --------------------------------------------


def test_synced_at_comes_from_upstream_generated_at() -> None:
    assert {r.synced_at for r in _records()} == {"2026-09-07"}


def test_attribution_is_upstreams_own_wording() -> None:
    source = DvrefDmrSource(token="t")
    list(source.parse(FIXTURE))
    assert source.attribution == "Reflector data provided by DVRef — https://dvref.com/"


def test_the_fixture_really_contains_an_email_address() -> None:
    # Guards the guard below: the descriptions are network blurbs written by sysops.
    assert EMAIL_SHAPED.search(FIXTURE.read_text(encoding="utf-8"))


def test_no_published_dmr_entry_carries_an_email_address() -> None:
    for entry in _entries():
        for value in entry.values():
            if isinstance(value, str):
                assert not EMAIL_SHAPED.search(value), entry


# --- fetching -------------------------------------------------------------------


def test_the_whole_network_costs_one_request(tmp_path: Path) -> None:
    # 172 networks in ONE response. The talkgroups behind them would be 172 more,
    # against a budget of 60 an hour — which is why they are linked, not mirrored.
    calls: list[str] = []

    def fetch(url: str, token: str) -> bytes:
        calls.append(url)
        return FIXTURE.read_bytes()

    source = DvrefDmrSource(token="t", fetch=fetch)
    source.download(tmp_path)
    source.download(tmp_path)  # same day -> must not refetch
    assert calls == ["https://dvref.com/api/v2/dmr/networks/?include_description=true"]
    assert len(list(source.parse(tmp_path / "dmr-networks.json"))) == SERVERS_IN_FIXTURE


def test_without_a_token_it_refuses_before_touching_the_network(tmp_path: Path) -> None:
    def fetch(url: str, token: str) -> bytes:  # pragma: no cover - must not run
        raise AssertionError("must not fetch without a token")

    with pytest.raises(DvrefAuthError):
        DvrefDmrSource(token="", fetch=fetch).download(tmp_path)


def test_dmr_is_not_one_of_the_reflector_segments() -> None:
    # Different endpoint, different row shape, different class. Asking the reflector
    # source for it must fail loudly rather than fetch something it cannot parse.
    with pytest.raises(ValueError, match="unknown DVRef segment"):
        DvrefSource("dmr", token="t")
    assert "dmr" not in dvref.NETWORKS
    assert "dmr" not in dvref.NETWORKS.values()


# --- the published contract -------------------------------------------------------


def test_the_openapi_mmdvm_variant_documents_every_field_the_rows_carry() -> None:
    # Same anti-drift guarantee the other kinds get, applied to the rows this
    # importer actually produces: nothing reaches the data without reaching the schema.
    schemas = reflectors.openapi_document()["components"]["schemas"]
    variant = schemas[schemas["Dial"]["discriminator"]["mapping"]["mmdvm"].rsplit("/", 1)[-1]]
    envelope = set(schemas["Reflector"]["properties"])

    assert variant["properties"]["kind"]["const"] == "mmdvm"
    assert "system" in envelope  # published in both places, documented in both
    assert variant["properties"]["timeslot"]["enum"] == [1, 2]
    assert "port" in variant["required"]  # only URF dials without one

    for entry in _entries():
        assert set(entry) <= envelope
        if "dial" in entry:
            assert set(entry["dial"]) <= set(variant["properties"])


def test_the_dmr_document_is_a_normal_network_document() -> None:
    document = _document()
    assert document["network"] == "dmr"
    assert document["count"] == SERVERS_IN_FIXTURE
    assert document["license"] == reflectors.REFLECTOR_LICENSE
    assert "DVRef" in str(document["attribution"])
    assert document["generated"] == "2026-09-07"  # upstream's date, not the build's


def test_a_dialable_row_survives_a_round_trip_through_the_document() -> None:
    # The Parquet and SQLite artifacts are rebuilt from the PUBLISHED documents, so a
    # field that does not round-trip is a field those artifacts silently lose.
    rebuilt = {r.id: r for r in reflectors.records_from_document(_document())}
    assert len(rebuilt) == SERVERS_IN_FIXTURE
    assert all(rebuilt[r.id].system == r.system for r in _records())  # dial or not
    for before in _records():
        if before.host is None or before.port is None:
            continue
        record = rebuilt[before.id]
        assert record.system == before.system
        assert record.requires == before.requires
        assert record.talkgroups_url == before.talkgroups_url
        assert record.host == before.host
        assert record.port == before.port


def test_an_undialable_row_still_says_which_network_it_belongs_to() -> None:
    # `system` is on the ENVELOPE, not only inside `dial` — which is what makes it
    # survive on the rows that have no dial. A SystemX server whose address upstream
    # never published is still a SystemX server, and a row that could not say so would
    # be unusable to a reader and null in the Parquet/SQLite `system` column.
    entry = _by_id("systemx-server-systemx-apollo")
    assert entry["system"] == "systemx"
    assert "dial" not in entry

    rebuilt = {r.id: r for r in reflectors.records_from_document(_document())}
    record = rebuilt["systemx-server-systemx-apollo"]
    assert record.system == "systemx"
    assert record.name == "SystemX Apollo"
    assert record.sponsor == "FreeSTAR Network"
    assert record.dashboard == "https://freestar.network/systemx-dmr/"
    # `requires` and `talkgroups_url` stay dial-only: they are instructions for making a
    # connection, and this row offers none to make.
    assert record.requires == []
    assert record.talkgroups_url is None


def test_every_dmr_row_carries_system_on_the_envelope() -> None:
    entries = _entries()
    assert all(entry.get("system") for entry in entries)
    # And it agrees with the dial's copy wherever there is one to agree with.
    assert all(e["dial"]["system"] == e["system"] for e in entries if "dial" in e)


def test_system_is_a_dmr_field_and_no_other_network_emits_it() -> None:
    # Envelope fields are shared across networks, so an optional one has to stay absent
    # rather than turning up as null on 2900 D-Star, YSF and M17 rows.
    other = reflectors.entry_json(
        ReflectorRecord(id="00006", network="ysf", host="ysf.example.org", port=42000)
    )
    assert "system" not in other


# --- the nightly build ------------------------------------------------------------


class _NoNetwork:
    """Stands in for every source that would otherwise reach the internet."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise DvrefAuthError("offline test")


class _NoNetworkDmr(_NoNetwork):
    """A DMR endpoint that cannot fetch. The build reads `network` off the class."""

    network = "dmr"


class _FixtureDmrSource(DvrefDmrSource):
    """The real importer, reading the checked-in response instead of fetching one."""

    def download(self, work_dir: Path) -> Path:
        return FIXTURE


def test_the_nightly_build_publishes_dmr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("XlxSource", "DextraHostsSource", "DStarAliasSource", "DvrefSource"):
        monkeypatch.setattr(reflectors_build, name, _NoNetwork)
    monkeypatch.setattr(reflectors_build, "DvrefDmrSource", _FixtureDmrSource)

    out = tmp_path / "api"
    reflectors_build.build(
        out=out, work_dir=tmp_path / "work", skip_dvref=False, dist=tmp_path / "dist"
    )

    document = json.loads((out / "reflectors" / "dmr.json").read_text(encoding="utf-8"))
    assert document["count"] == SERVERS_IN_FIXTURE

    manifest = json.loads((out / "index.json").read_text(encoding="utf-8"))
    assert manifest["networks"]["dmr"]["url"] == "reflectors/dmr.json"
    assert manifest["networks"]["dmr"]["count"] == SERVERS_IN_FIXTURE

    combined = json.loads((out / "reflectors.json").read_text(encoding="utf-8"))
    assert combined["networks"]["dmr"] == SERVERS_IN_FIXTURE

    stamp = datetime.now(UTC).date().isoformat()
    with sqlite3.connect(tmp_path / "dist" / f"hamcall-db-reflectors-{stamp}.db") as con:
        row = con.execute(
            "SELECT system, requires, talkgroups_url, talkgroup, timeslot, port "
            "FROM reflectors WHERE id = ?",
            ("freedmr-network-server-freedmr-cymru",),
        ).fetchone()
    assert row == (
        "freedmr-network",
        "dmr_id,password",  # SQLite has no array type; the list column is comma-joined
        "https://dvref.com/api/v2/dmr/networks/freedmr-network/talkgroups/",
        None,
        None,
        62031,
    )

    import polars as pl

    frame = pl.read_parquet(tmp_path / "dist" / f"hamcall-db-reflectors-{stamp}.parquet")
    assert frame.height == SERVERS_IN_FIXTURE
    # `system` is on the envelope, so it reaches the artifacts for EVERY row, not only
    # the dialable ones — see the round-trip tests above.
    assert frame["system"].drop_nulls().len() == SERVERS_IN_FIXTURE
    assert frame["talkgroup"].drop_nulls().len() == 0
    assert frame.schema["talkgroup"] == pl.Int64
    assert frame.schema["requires"] == pl.List(pl.Utf8)


def test_a_first_dmr_build_is_not_refused_as_an_upstream_failure(tmp_path: Path) -> None:
    # The source guard freezes a network when a source it TRIED loses every row it had
    # in the published file. A network that has never been published has no previous
    # file, so there is nothing to lose — the FIRST build must not be rejected.
    assert reflectors_build._load_existing(tmp_path, "dmr") is None
    assert reflectors_build._existing_count(tmp_path, "dmr") is None
    assert reflectors_build._source_counts(reflectors_build._load_existing(tmp_path, "dmr")) == {}


def test_a_failed_dmr_fetch_keeps_the_last_good_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # DVRef offers no SLA. Once dmr.json exists, a night that cannot fetch it must
    # republish yesterday's rather than dropping the network out of the manifest.
    write_api(tmp_path / "api", {"dmr": _document()}, generated=NOW.date())
    for name in ("XlxSource", "DextraHostsSource", "DStarAliasSource", "DvrefSource"):
        monkeypatch.setattr(reflectors_build, name, _NoNetwork)
    monkeypatch.setattr(reflectors_build, "DvrefDmrSource", _NoNetworkDmr)

    reflectors_build.build(
        out=tmp_path / "api", work_dir=tmp_path / "work", skip_dvref=False, dist=None
    )

    document = json.loads((tmp_path / "api" / "reflectors" / "dmr.json").read_text("utf-8"))
    assert document["count"] == SERVERS_IN_FIXTURE
