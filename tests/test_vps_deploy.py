"""Pin the my-vps nightly reflector job that lives in deploy/vps/."""

import json
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy" / "vps"
VERIFY = DEPLOY / "verify.py"


def test_build_sh_parses():
    result = subprocess.run(["bash", "-n", str(DEPLOY / "build.sh")], cwd=ROOT)
    assert result.returncode == 0


def test_verify_accepts_published_api():
    result = subprocess.run(
        [sys.executable, str(VERIFY)], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "networks" in result.stdout


def test_verify_rejects_missing_network(tmp_path):
    api = tmp_path / "docs" / "site" / "api" / "v1"
    api.mkdir(parents=True)
    shutil.copy(ROOT / "docs/site/api/v1/index.json", api / "index.json")
    manifest = json.loads((api / "index.json").read_text(encoding="utf-8"))
    del manifest["networks"]["p25"]
    (api / "index.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(VERIFY)], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "missing networks ['p25']" in result.stderr


VERIFY_ALLSTAR = DEPLOY / "verify_allstar.py"


def _run_verify_allstar(cwd):
    return subprocess.run(
        [sys.executable, str(VERIFY_ALLSTAR)], cwd=cwd, capture_output=True, text=True
    )


def _write_allstar(tmp_path, nodes, count=None):
    api = tmp_path / "docs" / "site" / "api" / "v1"
    api.mkdir(parents=True)
    doc = {
        "api_version": "v1",
        "count": len(nodes) if count is None else count,
        "nodes": nodes,
    }
    (api / "allstar.json").write_text(json.dumps(doc), encoding="utf-8")


def test_verify_allstar_accepts_absent(tmp_path):
    assert _run_verify_allstar(tmp_path).returncode == 0


def test_verify_allstar_rejects_small(tmp_path):
    _write_allstar(tmp_path, [{"node": str(n)} for n in range(10)])
    result = _run_verify_allstar(tmp_path)
    assert result.returncode == 1
    assert "refusing" in result.stderr


def test_verify_allstar_accepts_real_shape(tmp_path):
    _write_allstar(tmp_path, [{"node": str(n)} for n in range(1000, 31000)])
    result = _run_verify_allstar(tmp_path)
    assert result.returncode == 0, result.stderr


def test_verify_allstar_rejects_non_numeric_node(tmp_path):
    nodes = [{"node": str(n)} for n in range(1000, 31000)]
    nodes[5]["node"] = "12a"
    _write_allstar(tmp_path, nodes)
    assert _run_verify_allstar(tmp_path).returncode == 1


def test_build_sh_isolates_allstar():
    text = (DEPLOY / "build.sh").read_text(encoding="utf-8")
    assert text.index("hamcall-db-allstar") > text.index("verify.py")
    block = re.search(r"if ! \{.*?\n\s*fi\n", text, re.S)
    assert block, "allstar step must sit inside an `if !` block"
    assert "hamcall-db-allstar" in block.group(0)
    assert 'git checkout -q -- "$ALLSTAR"' in block.group(0)
    assert text.index("hamcall-db-allstar") < text.index("git status --porcelain")


def test_build_sh_commits_untracked_api_files():
    # `git diff --quiet` ignores untracked files, so a first-run allstar.json would
    # never be committed while the reflectors were unchanged.
    text = (DEPLOY / "build.sh").read_text(encoding="utf-8")
    assert '[ -z "$(git status --porcelain -- docs/site/api)" ]' in text
    assert "git diff --quiet" not in text


def test_build_sh_runs_repo_verify():
    text = (DEPLOY / "build.sh").read_text(encoding="utf-8")
    assert "deploy/vps/verify.py" in text
    assert "/opt/hamcall-db/verify.py" not in text


def test_no_secrets_in_deploy():
    pattern = re.compile(r"(?i)dvref_api_token\s*=")
    for path in DEPLOY.iterdir():
        assert not pattern.search(path.read_text(encoding="utf-8")), path.name
