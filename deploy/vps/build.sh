#!/usr/bin/env bash
# ============================================================================
# Nightly reflector-directory build for hamcall-db, run on my-vps.
#
# WHY THIS RUNS HERE AND NOT IN GITHUB ACTIONS
# --------------------------------------------
# DVRef sits behind Cloudflare, which refuses GitHub-hosted runner IP ranges: a
# valid token and a correct User-Agent still get a 403 HTML block page from the
# edge, before the API authenticates anything. Verified 2026-08-26 — the same
# token returns 200 from this box and from a workstation, and a genuinely bad
# token returns a JSON 401, so it is the origin being refused, not the
# credential.
#
# The XLX registry (D-Star) is NOT behind Cloudflare and works fine from
# Actions. But a GitHub-side build would rebuild D-Star from XLX alone (892
# rows) and drop DVRef's 61 XRF entries — a 6% shrink, which passes the shrink
# guard and would silently undo this build. So the data build happens ONLY
# here, and GitHub's job is to render the site and deploy Pages on push.
#
# WHY IT COMMITS OUTPUT AND NOT THE RAW CACHE
# -------------------------------------------
# DVRef responses carry a `generated_at` that changes on every request, so
# caching raw upstream JSON in git would churn ~1.3 MB a day forever. The built
# output is content-derived and byte-stable by design: a night with no upstream
# change produces identical files and no commit at all.
# ============================================================================
set -euo pipefail

REPO=/opt/hamcall-db/repo
export PATH="/root/.local/bin:$PATH"
export HOME=/root

# shellcheck disable=SC1091
set -a; . /opt/hamcall-db/env; set +a

cd "$REPO"
git fetch -q origin main
git reset -q --hard origin/main

# A fresh work dir every run. download() reuses a same-day cached file by
# design, which is right for politeness but means a warm cache silently skips
# the network — the exact trap that hid this whole problem for a while.
WORK=$(mktemp -d)
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

uv sync -q
uv run hamcall-db-reflectors --out docs/site/api/v1 --work-dir "$WORK" --dist /tmp/hdb-dist

# Refuse to publish a build that lost a network. The command already keeps
# last-good on a per-network fetch failure, so reaching here with a network
# missing entirely means something worse went wrong and should not be pushed.
uv run python deploy/vps/verify.py

# AllStar directory: its own step, so a failure here never blocks the reflectors.
ALLSTAR=docs/site/api/v1/allstar.json
if ! { uv run hamcall-db-allstar --out docs/site/api/v1 --work-dir "$WORK" \
       && uv run python deploy/vps/verify_allstar.py; }; then
  echo "WARNING: allstar step failed; publishing the reflectors without it" >&2
  git checkout -q -- "$ALLSTAR" 2>/dev/null || rm -f "$ALLSTAR"
fi

if [ -z "$(git status --porcelain -- docs/site/api)" ]; then
  echo "no upstream change — nothing to commit"
  exit 0
fi

git add docs/site/api
git commit -q -m "reflectors: refresh directory ($(date -u +%F))"
git push -q origin main
echo "pushed $(git rev-parse --short HEAD)"
