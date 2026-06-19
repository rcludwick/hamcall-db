"""Guard: the release workflow must publish every artifact the --all build writes (hdb-348e).

The bug this prevents: the build grew new outputs (POTA parks, park-grids, and the SEPARATE
ODbL OSM grids) but release.yml only uploaded the three original CC-BY-NC callsign files, so
the rest were built into dist/ and silently discarded. These assertions tie the workflow's
asset references back to the build's filename helpers so the two cannot drift apart again,
and check the CC-BY-NC <-> ODbL license boundary is kept at the DISTRIBUTION layer (the OSM
set ships as its OWN dated release + `latest-osm` alias, with LICENSE-ODbL).
"""

from __future__ import annotations

from pathlib import Path

_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"


def _workflow_text() -> str:
    return _WORKFLOW.read_text(encoding="utf-8")


def test_release_workflow_references_every_build_artifact() -> None:
    text = _workflow_text()
    # Distinctive, date-independent filename stems the --all build writes (the workflow uses
    # ${DATE} where the helpers put the ISO date, so we match the stable prefix).
    for stem in (
        "hamcall-db-",  # current-state parquet + sqlite
        "hamcall-db-history-",
        "hamcall-db-pota-parks-",
        "hamcall-db-sota-summits-",
        "hamcall-db-pota-park-grids-",
        "hamcall-db-pota-park-grids-osm-",
    ):
        assert stem in text, f"release workflow never references {stem!r} artifacts"


def test_release_workflow_publishes_separate_odbl_release() -> None:
    text = _workflow_text()
    # The ODbL (OSM-derived) set ships as its OWN dated release + rolling alias, never folded
    # into the CC-BY-NC release.
    assert "hamcall-db-osm-" in text, "no separate dated ODbL release tag"
    assert "latest-osm" in text, "no rolling 'latest-osm' alias for the ODbL set"
    # Its own license travels with it.
    assert "LICENSE-ODbL" in text, "ODbL release must ship the ODbL license"


def test_exactly_one_release_claims_the_latest_badge() -> None:
    text = _workflow_text()
    # softprops/action-gh-release defaults make_latest:true, so EVERY release we publish must
    # explicitly set make_latest — otherwise a later step steals the repo's "Latest" badge
    # (regression: the dated ODbL release grabbed it from the CC-BY-NC `latest` alias). Exactly
    # one `make_latest: true` is allowed, and it must be the CC-BY-NC `latest` alias.
    trues = text.count("make_latest: true")
    falses = text.count("make_latest: false")
    assert trues == 1, f"expected exactly one make_latest:true, found {trues}"
    # All four releases set make_latest explicitly (1 true + 3 false: two dated + latest-osm).
    assert falses == 3, f"expected three make_latest:false, found {falses}"


def test_odbl_and_ccbync_assets_are_collected_into_separate_lists() -> None:
    text = _workflow_text()
    # The collect step must expose two DISTINCT asset-list outputs so the two releases never
    # share a file list (the structural guarantee that ODbL data is not presented as CC-BY-NC).
    assert "cc_files" in text and "odbl_files" in text, (
        "collect step must build separate cc_files / odbl_files outputs"
    )
