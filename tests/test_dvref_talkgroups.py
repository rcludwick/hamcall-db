"""Tests for the mirrored DMR talkgroup lists (hdb-refl-dmrtg).

The subject of this file is a BUDGET, not a parser. DVRef publishes talkgroups behind a
per-network endpoint and there are 172 networks against 60 requests an hour, shared with
the rest of the build — so the interesting behaviour is which networks get fetched on a
given night, what the other 130-odd publish instead, and what happens when upstream says
to slow down.

All offline: `tests/fixtures/dvref/dmr-talkgroups.json` is a real capture of two networks'
responses keyed by slug, the downloader takes an injectable fetcher, and nothing here
touches the network or needs a DVRef token.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from hamcall_db import build_reflectors as reflectors_build
from hamcall_db import reflectors
from hamcall_db.reflectors import (
    ReflectorRecord,
    TalkgroupRecord,
    entry_json,
    talkgroup_document,
    talkgroup_path,
    write_api,
)
from hamcall_db.sources import dvref
from hamcall_db.sources.dvref import (
    TALKGROUP_MIN_AGE_DAYS,
    TALKGROUP_SLICE,
    DvrefAuthError,
    DvrefDmrSource,
    DvrefDmrTalkgroupSource,
    DvrefThrottled,
)

FIXTURES = Path(__file__).parent / "fixtures" / "dvref"
TALKGROUPS = FIXTURES / "dmr-talkgroups.json"
NETWORKS = FIXTURES / "dmr-networks.json"

# The capture was taken 2026-09-07; freeze "now" so nothing here drifts with the clock.
NOW = datetime(2026, 9, 7, tzinfo=UTC)
TODAY = NOW.date()

# Counted from the file rather than asserted from memory — see the fixture guard below.
SYSTEMX_TALKGROUPS = 21
FREEDMR_TALKGROUPS = 11


def _capture() -> dict[str, dict]:
    return json.loads(TALKGROUPS.read_text(encoding="utf-8"))


def _response(system: str) -> dict:
    """One network's captured response, or a well-formed empty one.

    The DMR networks fixture holds ten networks and the talkgroup capture holds two of
    them, which is the live situation in miniature: most networks answer, some of them
    with nothing to say.
    """
    captured = _capture().get(system)
    if captured is not None:
        return captured
    return {
        "status": "success",
        "generated_at": "2026-09-07T05:54:18.828963Z",
        "_dvref_metadata": {"attribution": dvref.ATTRIBUTION},
        "data": {"network": {"network": system, "talkgroups": []}},
    }


class _FixtureTalkgroupSource(DvrefDmrTalkgroupSource):
    """The real importer, writing the captured response instead of fetching one."""

    def __init__(self, system: str, **kwargs: object) -> None:
        super().__init__(system, token="t", fetch=self._fetch_fixture)

    def _fetch_fixture(self, url: str, token: str) -> bytes:
        return json.dumps(_response(self.system)).encode("utf-8")


class _FixtureDmrSource(DvrefDmrSource):
    """The DMR server list, read from the checked-in response."""

    def download(self, work_dir: Path) -> Path:
        return NETWORKS


class _NoNetwork:
    """Stands in for every source that would otherwise reach the internet."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise DvrefAuthError("offline test")


def _records(system: str, tmp_path: Path) -> list[TalkgroupRecord]:
    source = _FixtureTalkgroupSource(system)
    return list(source.parse(source.download(tmp_path)))


def _document(system: str, tmp_path: Path, *, generated: str = "2026-09-07") -> dict[str, object]:
    source = _FixtureTalkgroupSource(system)
    return talkgroup_document(
        system,
        list(source.parse(source.download(tmp_path))),
        source_name="DVRef",
        source_url=source.url,
        attribution=dvref.ATTRIBUTION,
        generated=generated,
    )


# --- the fixture the rest of this file reasons about ------------------------------


def test_the_fixture_is_the_shape_these_numbers_assume() -> None:
    capture = _capture()
    assert sorted(capture) == ["freedmr-network", "systemx"]
    for system, payload in capture.items():
        assert payload["status"] == "success"
        assert payload["_dvref_metadata"]["license"]["spdx_id"] == "CC-BY-4.0"
        assert payload["data"]["network"]["talkgroups"], system
    assert len(capture["systemx"]["data"]["network"]["talkgroups"]) == SYSTEMX_TALKGROUPS
    assert len(capture["freedmr-network"]["data"]["network"]["talkgroups"]) == FREEDMR_TALKGROUPS


# --- reading one network's list ---------------------------------------------------


def test_every_talkgroup_becomes_a_row(tmp_path: Path) -> None:
    records = _records("systemx", tmp_path)
    assert len(records) == SYSTEMX_TALKGROUPS
    assert all(r.system == "systemx" for r in records)
    assert {"235 Alive", "CQ-UK"} <= {r.name for r in records}


def test_the_number_and_the_network_together_are_the_key(tmp_path: Path) -> None:
    # TG 235 exists on SystemX and means nothing on its own: a talkgroup number is only
    # defined within one network, which is why `system` is not optional on the record.
    systemx = {r.tg for r in _records("systemx", tmp_path)}
    freedmr = {r.tg for r in _records("freedmr-network", tmp_path)}
    assert 235 in systemx
    assert 235 not in freedmr
    with pytest.raises(ValueError, match="network slug"):
        DvrefDmrTalkgroupSource("", token="t")


def test_synced_at_is_the_fetch_date_not_upstreams_stamp(tmp_path: Path) -> None:
    # Upstream stamps a talkgroups response with the moment it rendered it, so its
    # `generated_at` says when we asked, not when the list changed. What a client needs
    # is how stale the mirror is — the fetch date.
    records = _records("systemx", tmp_path)
    assert {r.synced_at for r in records} == {datetime.now(UTC).date().isoformat()}


def test_a_talkgroup_without_a_number_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "t.json"
    path.write_text(
        json.dumps(
            {
                "data": {
                    "network": {
                        "talkgroups": [
                            {"name": "no number"},
                            {"tg": "9050", "name": "numeric string"},
                            {"tg": 91, "name": "Worldwide"},
                            {"tg": 91, "name": "Worldwide again"},  # duplicate key
                            "not a dict",
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    records = list(DvrefDmrTalkgroupSource("x", token="t").parse(path))
    assert [(r.tg, r.name) for r in records] == [
        (9050, "numeric string"),
        (91, "Worldwide"),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"data": {"network": {"talkgroups": [{"tg": 9}]}}},  # the live shape
        {"data": {"talkgroups": [{"tg": 9}]}},  # one level flatter
        {"talkgroups": [{"tg": 9}]},
        [{"tg": 9}],
    ],
)
def test_the_envelope_reader_tolerates_variants(payload: object) -> None:
    assert dvref._talkgroup_rows(payload) == [{"tg": 9}]


@pytest.mark.parametrize("payload", [None, 42, {}, {"data": {"network": {}}}, {"data": []}])
def test_an_unknown_shape_is_no_rows_not_an_error(payload: object) -> None:
    # The endpoint is documented upstream as "No response body": degrade to fewer rows
    # rather than crashing the nightly build.
    assert dvref._talkgroup_rows(payload) == []


def test_the_reflector_reader_cannot_serve_this_endpoint() -> None:
    # `data.network` is an OBJECT here, not a list of rows — which is exactly why this
    # endpoint has its own reader instead of reusing _rows().
    assert dvref._rows(_capture()["systemx"]) == []


def test_a_name_carrying_an_email_address_is_redacted() -> None:
    record = TalkgroupRecord(system="x", tg=1, name="ask sysop@example.org for access")
    assert record.name == f"ask {reflectors.EMAIL_PLACEHOLDER} for access"


# --- fetching ---------------------------------------------------------------------


def test_one_network_costs_one_request_and_is_cached(tmp_path: Path) -> None:
    calls: list[str] = []

    def fetch(url: str, token: str) -> bytes:
        calls.append(url)
        return json.dumps(_response("systemx")).encode("utf-8")

    source = DvrefDmrTalkgroupSource("systemx", token="t", fetch=fetch)
    source.download(tmp_path)
    source.download(tmp_path)  # same day -> must not refetch
    assert calls == ["https://dvref.com/api/v2/dmr/networks/systemx/talkgroups/"]


def test_each_network_caches_under_its_own_name(tmp_path: Path) -> None:
    # 111 of these share one day's cache directory with the reflector lists.
    for system in ("systemx", "freedmr-network"):
        _FixtureTalkgroupSource(system).download(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "talkgroups-freedmr-network.json",
        "talkgroups-systemx.json",
    ]


def test_without_a_token_it_refuses_before_touching_the_network(tmp_path: Path) -> None:
    def fetch(url: str, token: str) -> bytes:  # pragma: no cover - must not run
        raise AssertionError("must not fetch without a token")

    with pytest.raises(DvrefAuthError):
        DvrefDmrTalkgroupSource("systemx", token="", fetch=fetch).download(tmp_path)


# --- the rotating slice -----------------------------------------------------------


def test_the_slice_fits_inside_the_hourly_budget() -> None:
    # 60 an hour, minus the six the rest of the build spends plus one held for a retry,
    # minus headroom for the interactive debugging that shares the account budget.
    assert dvref.AUTHENTICATED_HOURLY_LIMIT == 60
    assert TALKGROUP_SLICE == 40
    assert TALKGROUP_SLICE + 7 + 13 == dvref.AUTHENTICATED_HOURLY_LIMIT
    # 111 networks have servers, so the whole set turns over in about three nights —
    # comfortably inside the week clients are told to cache for.
    assert 111 / TALKGROUP_SLICE < reflectors.CLIENT_REFRESH_DAYS


def test_the_slice_takes_the_oldest_first() -> None:
    picked = reflectors_build.talkgroup_slice(
        ["a", "b", "c"],
        {"a": "2026-09-01", "b": "2026-08-20", "c": "2026-08-25"},
        today=TODAY,
        limit=2,
    )
    assert picked == ["b", "c"]


def test_the_slice_turns_on_the_fetch_date_not_the_content_date() -> None:
    # A list that never changes would otherwise stay permanently "oldest" and be
    # refetched every single night, starving every other network out of the rotation.
    # `fetched` is what the picker reads, and it moves whether or not the list did.
    picked = reflectors_build.talkgroup_slice(
        ["never-changes", "changed-recently"],
        {"never-changes": TODAY.isoformat(), "changed-recently": "2026-08-01"},
        today=TODAY,
        limit=1,
    )
    assert picked == ["changed-recently"]


def test_a_network_never_fetched_goes_first() -> None:
    # A newly listed network must get its talkgroups on its first or second night, not
    # queue behind 110 networks that already have a list.
    picked = reflectors_build.talkgroup_slice(
        ["known", "brand-new"],
        {"known": "2026-01-01"},
        today=TODAY,
        limit=1,
    )
    assert picked == ["brand-new"]


def test_the_slice_is_capped_at_the_budget() -> None:
    systems = [f"net-{n:03d}" for n in range(111)]
    picked = reflectors_build.talkgroup_slice(systems, {}, today=TODAY)
    assert len(picked) == TALKGROUP_SLICE
    # Deterministic when nothing distinguishes them: dict/set order must not decide
    # which networks upstream gets asked about.
    assert picked == sorted(systems)[:TALKGROUP_SLICE]


def test_a_file_younger_than_two_days_is_skipped_even_inside_the_slice() -> None:
    stamps = {
        "today": TODAY.isoformat(),
        "yesterday": (TODAY - timedelta(days=1)).isoformat(),
        "old": (TODAY - timedelta(days=TALKGROUP_MIN_AGE_DAYS)).isoformat(),
    }
    picked = reflectors_build.talkgroup_slice(
        sorted(stamps), stamps, today=TODAY, limit=TALKGROUP_SLICE
    )
    assert picked == ["old"]


def test_a_stamp_that_is_not_a_date_is_treated_as_never_fetched() -> None:
    # A hand-edited or truncated manifest must be refetched, not trusted and skipped.
    assert reflectors_build.talkgroup_slice(["x"], {"x": "yesterday-ish"}, today=TODAY) == ["x"]


# --- refreshing: keep-last-good, 111 times ----------------------------------------


def _refresh(
    systems: list[str],
    documents: dict[str, dict[str, object]],
    tmp_path: Path,
    *,
    fetched: dict[str, str] | None = None,
    make_source=None,
) -> tuple[list[str], list[str]]:
    return reflectors_build._refresh_talkgroups(
        systems,
        documents,
        fetched if fetched is not None else {},
        tmp_path,
        today=TODAY,
        make_source=make_source or _FixtureTalkgroupSource,
    )


def test_a_refresh_writes_the_networks_it_fetched(tmp_path: Path) -> None:
    documents: dict[str, dict[str, object]] = {}
    fetched: dict[str, str] = {}
    changed, failed = _refresh(["systemx", "freedmr-network"], documents, tmp_path, fetched=fetched)
    assert sorted(changed) == ["freedmr-network", "systemx"]
    assert failed == []
    assert documents["systemx"]["count"] == SYSTEMX_TALKGROUPS
    assert sorted(fetched) == ["freedmr-network", "systemx"]


def test_a_refetch_that_finds_nothing_new_leaves_the_file_alone(tmp_path: Path) -> None:
    # The byte-stability rule, at its source: the file moves only when its CONTENT
    # moves, and the fetch is recorded in the rotation state instead.
    documents: dict[str, dict[str, object]] = {}
    fetched: dict[str, str] = {}
    _refresh(["systemx"], documents, tmp_path, fetched=fetched)
    published = json.loads(json.dumps(documents["systemx"]))  # a deep copy to compare to

    fetched["systemx"] = "2026-08-01"  # old enough to be picked again
    changed, failed = _refresh(["systemx"], documents, tmp_path, fetched=fetched)

    assert changed == []  # fetched, unchanged, so nothing to publish
    assert failed == []
    assert documents["systemx"] == published
    assert fetched["systemx"] == TODAY.isoformat()  # but the fetch is recorded


def test_a_changed_list_moves_the_files_own_date(tmp_path: Path) -> None:
    documents = {
        "systemx": {
            "system": "systemx",
            "generated": "2026-08-01",
            "count": 1,
            "talkgroups": [{"tg": 1, "name": "gone tomorrow"}],
        }
    }
    changed, _ = _refresh(["systemx"], documents, tmp_path, fetched={"systemx": "2026-08-01"})
    assert changed == ["systemx"]
    assert documents["systemx"]["generated"] == TODAY.isoformat()
    assert documents["systemx"]["count"] == SYSTEMX_TALKGROUPS


def test_a_network_outside_the_slice_keeps_the_file_it_has(tmp_path: Path) -> None:
    # This is the whole design: only a slice is refetched, and every other network
    # republishes exactly what it already had.
    documents: dict[str, dict[str, object]] = {
        "systemx": {"system": "systemx", "generated": "2026-01-01", "count": 3},
        "freedmr-network": {"system": "freedmr-network", "generated": "2026-01-01", "count": 3},
    }
    fetched = {"systemx": TODAY.isoformat(), "freedmr-network": "2026-08-01"}
    changed, failed = _refresh(sorted(documents), documents, tmp_path, fetched=fetched)

    assert changed == ["freedmr-network"]  # the one fetched long ago, and only it
    assert failed == []
    assert documents["systemx"] == {"system": "systemx", "generated": "2026-01-01", "count": 3}
    assert documents["freedmr-network"]["count"] == FREEDMR_TALKGROUPS


def test_a_throttle_stops_the_slice_for_the_night(tmp_path: Path) -> None:
    # The budget is per ACCOUNT and the reflector lists draw on it too. Once upstream
    # says slow down, every further request is refused AND charged, so the slice stops
    # rather than spending tomorrow's directory fetch on tonight's talkgroups.
    attempted: list[str] = []

    class _Throttled(_FixtureTalkgroupSource):
        def download(self, work_dir: Path) -> Path:
            attempted.append(self.system)
            raise DvrefThrottled("slow down", retry_after=1114)

    documents: dict[str, dict[str, object]] = {}
    fetched: dict[str, str] = {}
    changed, failed = _refresh(
        ["a", "b", "c"], documents, tmp_path, fetched=fetched, make_source=_Throttled
    )
    assert changed == []
    assert documents == {}
    assert attempted == ["a"]  # stopped after the first, not tried three times
    assert failed == ["dvref/talkgroups:throttled"]
    # Nothing was fetched, so nothing moves down the queue: all three are first in line
    # again tomorrow.
    assert fetched == {}


def test_one_networks_failure_does_not_stop_the_others(tmp_path: Path) -> None:
    # A throttle is a statement about the whole account; anything else is about one
    # network, and the other 110 should still be refreshed.
    class _OneBadNetwork(_FixtureTalkgroupSource):
        def download(self, work_dir: Path) -> Path:
            if self.system == "freedmr-network":
                raise OSError("connection reset")
            return super().download(work_dir)

    documents: dict[str, dict[str, object]] = {}
    fetched: dict[str, str] = {}
    changed, failed = _refresh(
        ["freedmr-network", "systemx"],
        documents,
        tmp_path,
        fetched=fetched,
        make_source=_OneBadNetwork,
    )
    assert changed == ["systemx"]
    assert failed == ["dvref/talkgroups/freedmr-network"]
    # The failed one keeps no fetch date, so it is first in line again tomorrow.
    assert sorted(fetched) == ["systemx"]


def test_an_empty_response_keeps_a_list_that_had_rows(tmp_path: Path) -> None:
    # Same posture as the shrink guard: a list that had rows and now has none is an
    # upstream fault far more often than a network deleting every talkgroup.
    class _Empty(_FixtureTalkgroupSource):
        def _fetch_fixture(self, url: str, token: str) -> bytes:
            return json.dumps({"data": {"network": {"talkgroups": []}}}).encode("utf-8")

    documents = {"systemx": {"system": "systemx", "generated": "2026-08-01", "count": 21}}
    fetched: dict[str, str] = {}
    changed, failed = _refresh(
        ["systemx"], documents, tmp_path, fetched=fetched, make_source=_Empty
    )
    assert changed == []
    assert documents["systemx"]["count"] == 21
    assert failed == ["dvref/talkgroups/systemx:empty"]
    assert fetched == {}  # a suspect answer does not count as a fetch


def test_a_network_that_is_genuinely_empty_is_not_refetched_forever(tmp_path: Path) -> None:
    # The guard above is on the previous COUNT, not on the file existing. A network
    # whose list really is empty answers with nothing every night; treating that as a
    # fault would put it back at the head of the queue every night, forever, which is
    # the one thing a 60-an-hour budget cannot afford.
    class _Empty(_FixtureTalkgroupSource):
        def _fetch_fixture(self, url: str, token: str) -> bytes:
            return json.dumps({"data": {"network": {"talkgroups": []}}}).encode("utf-8")

    documents: dict[str, dict[str, object]] = {}
    fetched: dict[str, str] = {}
    _refresh(["quiet-net"], documents, tmp_path, fetched=fetched, make_source=_Empty)
    published = json.loads(json.dumps(documents["quiet-net"]))

    fetched["quiet-net"] = "2026-08-01"  # old enough to come round again
    changed, failed = _refresh(
        ["quiet-net"], documents, tmp_path, fetched=fetched, make_source=_Empty
    )
    assert changed == []
    assert failed == []
    assert documents["quiet-net"] == published  # still byte-identical
    assert fetched["quiet-net"] == TODAY.isoformat()  # and it moves down the queue


def test_a_first_empty_response_is_published_rather_than_refetched_nightly(
    tmp_path: Path,
) -> None:
    # With no previous file there is nothing to protect, and publishing what upstream
    # said stops this network being asked again every single night.
    class _Empty(_FixtureTalkgroupSource):
        def _fetch_fixture(self, url: str, token: str) -> bytes:
            return json.dumps({"data": {"network": {"talkgroups": []}}}).encode("utf-8")

    documents: dict[str, dict[str, object]] = {}
    changed, failed = _refresh(["quiet-net"], documents, tmp_path, make_source=_Empty)
    assert changed == ["quiet-net"]
    assert documents["quiet-net"]["count"] == 0
    assert failed == []


# --- the published file -----------------------------------------------------------


def test_the_document_is_sorted_counted_and_credited(tmp_path: Path) -> None:
    document = _document("systemx", tmp_path)
    rows = document["talkgroups"]
    assert isinstance(rows, list)
    assert [r["tg"] for r in rows] == sorted(r["tg"] for r in rows)
    assert document["count"] == len(rows) == SYSTEMX_TALKGROUPS
    assert document["system"] == "systemx"
    assert document["network"] == "dmr"
    # The same {name, url} object every other document carries — one key, one shape.
    assert document["source"] == {
        "name": "DVRef",
        "url": "https://dvref.com/api/v2/dmr/networks/systemx/talkgroups/",
    }
    assert set(document["source"]) == set(_dmr_document()["source"])
    assert document["license"] == reflectors.REFLECTOR_LICENSE
    assert "DVRef" in str(document["attribution"])
    assert document["modifications"]


def test_generated_is_when_this_networks_list_last_changed(tmp_path: Path) -> None:
    # Not a build stamp, and not the fetch date either: either would rewrite files on a
    # night when nothing about them moved. The fetch date lives in the manifest.
    document = _document("systemx", tmp_path, generated="2026-08-30")
    assert document["generated"] == "2026-08-30"


def test_the_row_carries_the_number_and_the_name_only(tmp_path: Path) -> None:
    rows = _document("systemx", tmp_path)["talkgroups"]
    assert {"tg": 235, "name": "235 Alive"} in rows
    # `system` and the date live in the envelope: they are the same for every row in
    # the file, and repeating them 21 times buys a client nothing.
    assert all(set(row) <= {"tg", "name"} for row in rows)


def test_a_nameless_talkgroup_omits_the_key_rather_than_publishing_null() -> None:
    assert reflectors.talkgroup_json(TalkgroupRecord(system="x", tg=9)) == {"tg": 9}


# --- where it lands, and how a client finds it ------------------------------------


def _mirrors(tmp_path: Path) -> dict[str, dict[str, object]]:
    return {
        "systemx": _document("systemx", tmp_path),
        "freedmr-network": _document("freedmr-network", tmp_path),
    }


def _dmr_document(**kwargs: object) -> dict[str, object]:
    source = _FixtureDmrSource(token="t")
    records = list(source.parse(NETWORKS))
    for record in records:
        if record.system in ("systemx", "freedmr-network"):
            record.talkgroups = talkgroup_path(record.system)
    return reflectors.network_document(
        "dmr",
        records,
        source_name="DVRef",
        source_url="https://dvref.com/api/v2/",
        attribution=source.attribution,
        generated=TODAY,
    )


def test_each_network_is_published_as_its_own_file(tmp_path: Path) -> None:
    out = tmp_path / "api"
    write_api(out, {"dmr": _dmr_document()}, talkgroups=_mirrors(tmp_path), generated=TODAY)

    path = out / "reflectors" / "dmr" / "systemx" / "talkgroups.json"
    assert json.loads(path.read_text(encoding="utf-8"))["count"] == SYSTEMX_TALKGROUPS
    # The server list and the talkgroup mirrors sit side by side under one DMR prefix.
    assert (out / "reflectors" / "dmr.json").exists()
    assert talkgroup_path("systemx") == "reflectors/dmr/systemx/talkgroups.json"


def test_the_manifest_says_which_networks_have_a_mirror(tmp_path: Path) -> None:
    # A client must be able to discover what exists without probing 111 URLs for 404s.
    out = tmp_path / "api"
    write_api(
        out,
        {"dmr": _dmr_document()},
        talkgroups=_mirrors(tmp_path),
        talkgroups_fetched={"systemx": "2026-09-20", "freedmr-network": "2026-09-20"},
        generated=TODAY,
    )

    manifest = json.loads((out / "index.json").read_text(encoding="utf-8"))
    entry = manifest["networks"]["dmr"]["talkgroups"]["systemx"]
    assert entry == {
        "url": "reflectors/dmr/systemx/talkgroups.json",
        "count": SYSTEMX_TALKGROUPS,
        "generated": "2026-09-07",
        # The rotation's state: when it was last CONFIRMED, as against when it last
        # changed. This is the field that moves nightly, in this one small file.
        "fetched": "2026-09-20",
    }
    assert "talkgroups" not in manifest["networks"].get("ysf", {})


def test_a_mirror_with_no_recorded_fetch_falls_back_to_its_content_date(
    tmp_path: Path,
) -> None:
    # A manifest written before `fetched` existed must not read as "never fetched" —
    # that would refetch all 111 networks at once. The content date is the last moment
    # the list was known current, so the rotation resumes from there.
    out = tmp_path / "api"
    write_api(out, {"dmr": _dmr_document()}, talkgroups=_mirrors(tmp_path), generated=TODAY)
    entry = json.loads((out / "index.json").read_text("utf-8"))["networks"]["dmr"]["talkgroups"]
    assert entry["systemx"]["fetched"] == entry["systemx"]["generated"]
    assert reflectors.read_talkgroup_fetched(out)["systemx"] == "2026-09-07"


def test_the_rotation_state_survives_a_round_trip_through_the_manifest(
    tmp_path: Path,
) -> None:
    out = tmp_path / "api"
    write_api(
        out,
        {"dmr": _dmr_document()},
        talkgroups=_mirrors(tmp_path),
        talkgroups_fetched={"systemx": "2026-09-20"},
        generated=TODAY,
    )
    fetched = reflectors.read_talkgroup_fetched(out)
    assert fetched["systemx"] == "2026-09-20"
    assert fetched["freedmr-network"] == "2026-09-07"  # fell back to its content date
    assert reflectors.read_talkgroup_fetched(tmp_path / "nothing-here") == {}


def test_the_talkgroup_dates_do_not_move_the_whole_apis_stamp(tmp_path: Path) -> None:
    # The mirrors rotate by design. Letting them drive the manifest's `generated` would
    # tell every client the directory changed on a night when no reflector did.
    documents = {"dmr": _dmr_document()}
    mirrors = _mirrors(tmp_path)
    without = reflectors.manifest_document(documents, generated=TODAY)
    with_mirrors = reflectors.manifest_document(documents, talkgroups=mirrors, generated=TODAY)
    assert with_mirrors["generated"] == without["generated"]
    assert with_mirrors["expires_hint"] == without["expires_hint"]


def test_a_server_row_links_the_mirror_beside_upstreams_own_list() -> None:
    # `dial.talkgroups_url` stays pointed at DVRef — that is the canonical, always
    # current list. The envelope's `talkgroups` is OUR copy, which is a rotating mirror
    # and can be days behind. A client picks the one it can reach.
    entry = next(
        e
        for e in _dmr_document()["reflectors"]
        if e["id"] == "freedmr-network-server-freedmr-cymru"
    )
    assert entry["talkgroups"] == "reflectors/dmr/freedmr-network/talkgroups.json"
    assert entry["dial"]["talkgroups_url"].startswith("https://dvref.com/")
    assert "talkgroups" not in entry["dial"]


def test_a_network_with_no_mirror_carries_no_link() -> None:
    entry = next(e for e in _dmr_document()["reflectors"] if e["system"] == "new-england-dmr")
    assert "talkgroups" not in entry


def test_the_mirror_link_is_a_dmr_field_and_no_other_network_emits_it() -> None:
    other = entry_json(
        ReflectorRecord(id="00006", network="ysf", host="ysf.example.org", port=42000)
    )
    assert "talkgroups" not in other


def test_a_published_mirror_round_trips_back_into_rows(tmp_path: Path) -> None:
    # The Parquet and SQLite tables are built from the documents being PUBLISHED, so a
    # field that does not round-trip is a field those artifacts silently lose.
    document = _document("systemx", tmp_path, generated="2026-08-30")
    rows = reflectors.talkgroups_from_document(document)
    assert len(rows) == SYSTEMX_TALKGROUPS
    assert all(r.system == "systemx" for r in rows)
    assert all(r.synced_at == "2026-08-30" for r in rows)
    assert (235, "235 Alive") in {(r.tg, r.name) for r in rows}


def test_reading_the_published_tree_recovers_the_mirrors(tmp_path: Path) -> None:
    # The mirrors and the rotation's state both live in the committed output and
    # nowhere else, so a fresh checkout resumes exactly where the last build left it.
    out = tmp_path / "api"
    write_api(out, {"dmr": _dmr_document()}, talkgroups=_mirrors(tmp_path), generated=TODAY)
    loaded = reflectors.read_talkgroup_documents(out)
    assert sorted(loaded) == ["freedmr-network", "systemx"]
    assert reflectors_build._as_date(loaded["systemx"]["generated"]) == date(2026, 9, 7)
    assert reflectors.read_talkgroup_documents(tmp_path / "nothing-here") == {}


def test_an_unreadable_mirror_is_skipped_not_fatal(tmp_path: Path) -> None:
    out = tmp_path / "api"
    write_api(out, {"dmr": _dmr_document()}, talkgroups=_mirrors(tmp_path), generated=TODAY)
    (out / "reflectors" / "dmr" / "systemx" / "talkgroups.json").write_text("{ truncated")
    assert sorted(reflectors.read_talkgroup_documents(out)) == ["freedmr-network"]


# --- the published contract -------------------------------------------------------


def test_the_openapi_documents_every_key_the_mirror_emits(tmp_path: Path) -> None:
    # The anti-drift guarantee the reflector files already get, applied to this file
    # shape: nothing reaches the data without reaching the schema a client generates
    # its types from.
    schemas = reflectors.openapi_document()["components"]["schemas"]
    collection = schemas["TalkgroupCollection"]
    row = schemas["Talkgroup"]

    document = _document("systemx", tmp_path)
    assert set(document) <= set(collection["properties"])
    assert set(collection["required"]) <= set(document)
    for entry in document["talkgroups"]:
        assert set(entry) <= set(row["properties"])
    assert set(row["required"]) <= set(document["talkgroups"][0])


def test_the_openapi_publishes_the_talkgroup_path_and_the_manifest_map() -> None:
    document = reflectors.openapi_document()
    path = document["paths"]["/reflectors/dmr/{system}/talkgroups.json"]["get"]
    assert path["operationId"] == "getDmrTalkgroups"
    assert [p["name"] for p in path["parameters"]] == ["system"]

    manifest = document["components"]["schemas"]["Manifest"]
    network = manifest["properties"]["networks"]["additionalProperties"]
    mirror = network["properties"]["talkgroups"]["additionalProperties"]
    assert set(mirror["required"]) == {"url", "count", "generated", "fetched"}
    # And the envelope link, which is what points at all of it.
    envelope = document["components"]["schemas"]["Reflector"]["properties"]
    assert "talkgroups" in envelope


def test_the_openapi_still_validates() -> None:
    from openapi_spec_validator import validate

    validate(reflectors.openapi_document())


# --- the nightly build ------------------------------------------------------------


def _build(
    out: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    talkgroups: type = _FixtureTalkgroupSource,
    **kwargs: object,
) -> None:
    for name in ("XlxSource", "DextraHostsSource", "DStarAliasSource", "DvrefSource"):
        monkeypatch.setattr(reflectors_build, name, _NoNetwork)
    monkeypatch.setattr(reflectors_build, "DvrefDmrSource", _FixtureDmrSource)
    monkeypatch.setattr(reflectors_build, "DvrefDmrTalkgroupSource", talkgroups)
    reflectors_build.build(out=out, work_dir=tmp_path / "work", skip_dvref=False, **kwargs)


def test_the_nightly_build_mirrors_talkgroups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "api"
    _build(out, tmp_path, monkeypatch, dist=tmp_path / "dist")

    mirror = json.loads(
        (out / "reflectors" / "dmr" / "systemx" / "talkgroups.json").read_text("utf-8")
    )
    assert mirror["count"] == SYSTEMX_TALKGROUPS
    assert mirror["generated"] == datetime.now(UTC).date().isoformat()

    manifest = json.loads((out / "index.json").read_text(encoding="utf-8"))
    mirrors = manifest["networks"]["dmr"]["talkgroups"]
    # Every DMR network with a server is asked — derived from the fixture, so a re-slice
    # of it fails here with an explanation rather than somewhere downstream. The two in
    # the capture have rows; the rest publish the empty list upstream gave them.
    networks = json.loads(NETWORKS.read_text(encoding="utf-8"))["data"]["networks"]
    assert set(mirrors) == {n["slug"] for n in networks if n.get("servers")}
    assert mirrors["systemx"]["count"] == SYSTEMX_TALKGROUPS
    assert mirrors["freedmr-network"]["count"] == FREEDMR_TALKGROUPS

    dmr = json.loads((out / "reflectors" / "dmr.json").read_text(encoding="utf-8"))
    linked = {e["talkgroups"] for e in dmr["reflectors"] if "talkgroups" in e}
    assert "reflectors/dmr/systemx/talkgroups.json" in linked


def test_a_network_with_no_servers_is_never_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 61 of the live 172 are listings with no address behind them. A talkgroup request
    # for one is a request from a 60-an-hour budget spent on something unreachable.
    out = tmp_path / "api"
    _build(out, tmp_path, monkeypatch, dist=None)
    manifest = json.loads((out / "index.json").read_text(encoding="utf-8"))
    mirrors = manifest["networks"]["dmr"]["talkgroups"]
    assert "bm2222" not in mirrors
    assert "tgif165" not in mirrors


def test_a_rebuild_the_same_day_is_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The whole point of dating each file by its own fetch: a night that refreshes
    # nothing must produce no commit. The two-day floor is what makes the second build
    # skip every file rather than restamping it.
    out = tmp_path / "api"
    _build(out, tmp_path, monkeypatch, dist=None)
    before = {p: p.read_bytes() for p in sorted(out.rglob("*.json"))}
    _build(out, tmp_path, monkeypatch, dist=None)
    after = {p: p.read_bytes() for p in sorted(out.rglob("*.json"))}
    assert before == after


def _age_the_fetch_dates(out: Path, stamp: str) -> None:
    """Backdate the manifest's fetch dates, so the next build's slice reaches everything.

    The rotation's state is in the manifest now, so this is where a "later night" is
    simulated — the mirrors themselves are untouched, exactly as they would be on a real
    night when nothing upstream changed.
    """
    path = out / "index.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for entry in manifest["networks"]["dmr"]["talkgroups"].values():
        entry["fetched"] = stamp
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_a_later_night_with_no_upstream_change_moves_only_the_fetch_dates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # THE BYTE-STABILITY GUARANTEE. Every mirror is stale enough to be refetched, and
    # upstream returns exactly what it returned before. Not one of the 111 files may
    # move; the only thing that changes anywhere is the fetch dates in the manifest,
    # which is one small file that already moves whenever a count does.
    out = tmp_path / "api"
    _build(out, tmp_path, monkeypatch, dist=None)
    _age_the_fetch_dates(out, "2026-08-01")

    mirrors = out / "reflectors" / "dmr"
    before_mirrors = {p: p.read_bytes() for p in sorted(mirrors.rglob("*.json"))}
    before_manifest = json.loads((out / "index.json").read_text(encoding="utf-8"))

    _build(out, tmp_path / "night2", monkeypatch, dist=None)

    assert {p: p.read_bytes() for p in sorted(mirrors.rglob("*.json"))} == before_mirrors

    after_manifest = json.loads((out / "index.json").read_text(encoding="utf-8"))
    assert after_manifest != before_manifest
    today = datetime.now(UTC).date().isoformat()
    for system, entry in after_manifest["networks"]["dmr"]["talkgroups"].items():
        was = before_manifest["networks"]["dmr"]["talkgroups"][system]
        assert entry["fetched"] == today  # confirmed tonight
        assert was["fetched"] == "2026-08-01"
        # and nothing else about the entry moved, `generated` above all
        assert {k: v for k, v in entry.items() if k != "fetched"} == {
            k: v for k, v in was.items() if k != "fetched"
        }

    # The rest of the manifest is untouched too.
    def _without_talkgroups(manifest: dict) -> dict:
        copy = json.loads(json.dumps(manifest))
        copy["networks"]["dmr"].pop("talkgroups")
        return copy

    assert _without_talkgroups(after_manifest) == _without_talkgroups(before_manifest)


def test_a_throttled_night_keeps_every_mirror_it_had(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every mirror is stale enough to be in tonight's slice, and upstream refuses the
    # first request. Nothing moves at all — not the lists, and not the fetch dates
    # either, so all of them are still first in line tomorrow.
    out = tmp_path / "api"
    _build(out, tmp_path, monkeypatch, dist=None)
    _age_the_fetch_dates(out, "2026-08-01")
    before = {p: p.read_bytes() for p in sorted(out.rglob("*.json"))}

    attempts: list[str] = []

    class _Throttled(_FixtureTalkgroupSource):
        def download(self, work_dir: Path) -> Path:
            attempts.append(self.system)
            raise DvrefThrottled("slow down", retry_after=900)

    _build(out, tmp_path / "night2", monkeypatch, talkgroups=_Throttled, dist=None)

    assert len(attempts) == 1  # stopped at the first refusal, not eight times
    assert {p: p.read_bytes() for p in sorted(out.rglob("*.json"))} == before


def test_a_changed_list_republishes_that_one_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The other half of the contract: when upstream really does change, the file moves
    # and its `generated` moves with it — and no other mirror is touched.
    out = tmp_path / "api"
    _build(out, tmp_path, monkeypatch, dist=None)
    _age_the_fetch_dates(out, "2026-08-01")
    mirrors = out / "reflectors" / "dmr"
    before = {p: p.read_bytes() for p in sorted(mirrors.rglob("*.json"))}

    class _OneNewTalkgroup(_FixtureTalkgroupSource):
        def _fetch_fixture(self, url: str, token: str) -> bytes:
            payload = _response(self.system)
            if self.system == "systemx":
                payload["data"]["network"]["talkgroups"].append({"tg": 4242, "name": "New"})
            return json.dumps(payload).encode("utf-8")

    _build(out, tmp_path / "night2", monkeypatch, talkgroups=_OneNewTalkgroup, dist=None)

    changed = mirrors / "systemx" / "talkgroups.json"
    document = json.loads(changed.read_text(encoding="utf-8"))
    assert document["count"] == SYSTEMX_TALKGROUPS + 1
    assert document["generated"] == datetime.now(UTC).date().isoformat()
    assert {p: p.read_bytes() for p in sorted(mirrors.rglob("*.json")) if p != changed} == {
        p: b for p, b in before.items() if p != changed
    }


def test_the_artifacts_carry_the_talkgroup_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import polars as pl

    _build(tmp_path / "api", tmp_path, monkeypatch, dist=tmp_path / "dist")
    stamp = datetime.now(UTC).date().isoformat()

    dist = tmp_path / "dist"
    frame = pl.read_parquet(dist / f"hamcall-db-reflectors-dmr-talkgroups-{stamp}.parquet")
    assert frame.columns == list(reflectors.TALKGROUP_SCHEMA_COLUMNS)
    assert frame.schema["tg"] == pl.Int64
    assert frame.height == SYSTEMX_TALKGROUPS + FREEDMR_TALKGROUPS

    with sqlite3.connect(dist / f"hamcall-db-reflectors-{stamp}.db") as con:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        row = con.execute(
            "SELECT system, name, synced_at FROM dmr_talkgroups WHERE tg = ? AND system = ?",
            (235, "systemx"),
        ).fetchone()
        # Both tables are DVRef's CC BY 4.0 data, which is why they may share one file —
        # and neither CC BY-NC table may ever appear beside them.
        assert tables == {"reflectors", "dmr_talkgroups"}
    assert row == ("systemx", "235 Alive", stamp)
