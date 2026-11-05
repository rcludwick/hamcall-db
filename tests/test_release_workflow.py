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

import pytest

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
        "hamcall-db-pota-park-grids-",
        "hamcall-db-pota-park-grids-osm-",
    ):
        assert stem in text, f"release workflow never references {stem!r} artifacts"


def test_release_workflow_excludes_restricted_sources() -> None:
    text = _workflow_text()
    # The published release must NOT set --include-restricted, and must NOT publish the
    # restricted SOTA artifact (license unconfirmed; opt-in local builds only). LoTW columns
    # ride the callsign artifact but ship empty because the build never opts in.
    assert "--include-restricted" not in text, "release must not opt into restricted sources"
    assert "sota-summits" not in text, "SOTA artifact must not be a published release asset"


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


def test_release_workflow_runs_nightly() -> None:
    text = _workflow_text()
    # Pinned schedule (hdb-nightly): every day at 00:17 UTC (5:17 PM MST), not the old
    # weekly Monday slot. A daily cron matches upstream ACMA/LoTW's daily cadence and lets
    # the history ledger accrue at day granularity. Off the top of the hour on purpose:
    # GitHub delays or drops scheduled runs at :00 under load.
    assert '- cron: "17 0 * * *"' in text, "release schedule must be daily at 00:17 UTC"
    assert '- cron: "0 0 * * *"' not in text, "the congested 00:00 UTC cron must be gone"
    assert '- cron: "0 7 * * *"' not in text, "the old 07:00 UTC cron must be gone"
    assert '- cron: "0 7 * * 1"' not in text, "the old weekly (Monday-only) cron must be gone"


def test_alias_releases_clear_prior_assets_before_upload() -> None:
    text = _workflow_text()
    # softprops/action-gh-release APPENDS assets to an existing release rather than
    # replacing them, and every nightly build's dated asset names differ, so the
    # rolling aliases ('latest', 'latest-osm') must delete their existing assets before
    # each re-upload or they accumulate one more file per night forever (hdb-nightly).
    for alias in ("latest", "latest-osm"):
        assert f"gh release delete-asset {alias} --yes" in text, (
            f"'{alias}' release must clear its existing assets before re-upload"
        )
        assert f"gh release view {alias} --json assets" in text, (
            f"'{alias}' asset-clearing step must list existing assets via 'gh release view {alias}'"
        )


def test_release_fails_loudly_when_a_source_was_skipped() -> None:
    from hamcall_db.build import SKIPPED_SOURCES_FILENAME

    text = _workflow_text()
    # The build still publishes a partial release, but the job must end red with an
    # ::error:: annotation naming the skipped sources (hdb-skipalert). The check is the
    # final step and runs even if a publish step failed.
    assert f"dist/{SKIPPED_SOURCES_FILENAME}" in text
    step = text[text.index("- name: Fail if any source was skipped") :]
    assert "if: always()" in step
    assert "::error" in step
    assert "exit 1" in step
    assert "\n      - name:" not in step, "the skipped-source check must be the last step"


# --- hdb-odbl: the ODbL release is gated on real source='osm' rows ----------------
#
# The bug this prevents: the ODbL release was published whenever the OSM parquet EXISTED,
# and CI never installs the `osm` group, so every row was an 'osm-point' grid computed from
# POTA's own lat/lon — POTA data published under ODbL and credited to OpenStreetMap. The
# gate must count rows, and skip every ODbL step (including clearing 'latest-osm') unless
# there is at least one source='osm' row and no row of any other source.

_ODBL_STEPS = (
    "Publish dated ODbL release (OpenStreetMap-derived)",
    "Clear 'latest-osm' release assets (ODbL)",
    "Update 'latest-osm' release alias (ODbL)",
)


def _steps() -> list[dict]:
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(_workflow_text())
    return doc["jobs"]["build-and-release"]["steps"]


def _step(name: str) -> dict:
    return next(s for s in _steps() if s.get("name") == name)


def test_workflow_yaml_parses() -> None:
    assert _steps()


def test_every_odbl_step_is_gated_on_has_odbl() -> None:
    for name in _ODBL_STEPS:
        assert _step(name).get("if") == "steps.assets.outputs.has_odbl == 'true'", name


def test_odbl_release_text_drops_point_grid_wording() -> None:
    body = _step("Publish dated ODbL release (OpenStreetMap-derived)")["with"]["body"]
    assert "Indicative point grids" not in body
    cc_body = _step("Publish dated release (CC BY-NC)")["with"]["body"]
    assert "SEPARATELY" not in cc_body
    assert "when available" in cc_body


def _run_collect(tmp_path: Path, osm_sources: list[str] | None) -> dict[str, str]:
    """Run the real "Collect release assets" script against a fake dist/ and return outputs.

    ``osm_sources`` None means no OSM files were written; otherwise an OSM parquet (+ .db)
    holding one row per listed source. ``uv`` is shimmed to this interpreter so the gate's
    ``uv run python -c ...`` one-liner runs exactly as written.
    """
    import os
    import subprocess
    import sys

    import polars as pl

    date = "2026-11-01"
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / f"hamcall-db-{date}.parquet").write_bytes(b"x")
    (tmp_path / "LICENSE-ODbL").write_text("odbl")
    if osm_sources is not None:
        pl.DataFrame(
            {
                "reference": [f"DE-{i:04d}" for i in range(len(osm_sources))],
                "grid": ["JN57"] * len(osm_sources),
                "source": osm_sources,
                "confidence": ["high"] * len(osm_sources),
            },
            schema={c: pl.Utf8 for c in ("reference", "grid", "source", "confidence")},
        ).write_parquet(dist / f"hamcall-db-pota-park-grids-osm-{date}.parquet")
        (dist / f"hamcall-db-pota-park-grids-osm-{date}.db").write_bytes(b"x")

    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "uv"
    shim.write_text(
        "#!/bin/sh\n"
        '[ "$1" = run ] && shift\n'
        '[ "$1" = python ] && shift\n'
        f'exec "{sys.executable}" "$@"\n'
    )
    shim.chmod(0o755)

    script = _step("Collect release assets")["run"].replace("${{ steps.date.outputs.date }}", date)
    out_file = tmp_path / "gh_output"
    out_file.write_text("")
    env = {
        **os.environ,
        "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(out_file),
    }
    subprocess.run(["bash", "-c", script], cwd=tmp_path, env=env, check=True)

    outputs: dict[str, str] = {}
    lines = iter(out_file.read_text().splitlines())
    for line in lines:
        if "<<" in line:
            key, marker = line.split("<<", 1)
            block = []
            for inner in lines:
                if inner == marker:
                    break
                block.append(inner)
            outputs[key] = "\n".join(block)
        elif "=" in line:
            key, value = line.split("=", 1)
            outputs[key] = value
    return outputs


def test_gate_publishes_when_osm_rows_present(tmp_path: Path) -> None:
    out = _run_collect(tmp_path, ["osm", "osm"])
    assert out["has_odbl"] == "true"
    assert "hamcall-db-pota-park-grids-osm-2026-11-01.parquet" in out["odbl_files"]
    assert "LICENSE-ODbL" in out["odbl_files"]


def test_gate_skips_when_no_osm_files(tmp_path: Path) -> None:
    out = _run_collect(tmp_path, None)
    assert out["has_odbl"] == "false"
    assert "odbl_files" not in out


def test_gate_skips_when_osm_file_is_empty(tmp_path: Path) -> None:
    out = _run_collect(tmp_path, [])
    assert out["has_odbl"] == "false"


def test_gate_skips_when_only_point_rows(tmp_path: Path) -> None:
    # Exactly today's bug: a file full of POTA-derived 'osm-point' rows must NOT publish.
    out = _run_collect(tmp_path, ["osm-point", "osm-point"])
    assert out["has_odbl"] == "false"


def test_gate_skips_when_any_row_is_not_osm(tmp_path: Path) -> None:
    out = _run_collect(tmp_path, ["osm", "pota-point"])
    assert out["has_odbl"] == "false"


def test_license_odbl_attribution_and_scope() -> None:
    text = (_WORKFLOW.parents[2] / "LICENSE-ODbL").read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "© OpenStreetMap contributors (https://www.openstreetmap.org/copyright)." in flat
    assert "Extracts processed by Geofabrik GmbH." in flat
    assert "Only rows with source='osm' are derived from OpenStreetMap." in flat
    assert "included solely as a join key" in flat
