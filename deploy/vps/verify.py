"""Refuse to publish a reflector build that lost a network.

The build command already keeps the previous file when one upstream fetch
fails, so every network should still be present here even on a bad night.
Reaching this check with a network missing entirely, or with the total
collapsed, means something worse went wrong than an upstream hiccup — and the
whole point of running the build on this box is that it is the only place that
can produce a COMPLETE one. Publishing a partial build from here would quietly
undo that.
"""

import json
import pathlib
import sys

EXPECTED = {"dstar", "m17", "ysf", "nxdn", "p25", "urf", "dmr"}
# The full set is ~3,185. A floor well under that catches a collapse without
# tripping on ordinary churn — reflectors do come and go.
FLOOR = 2000

manifest = json.loads(
    pathlib.Path("docs/site/api/v1/index.json").read_text(encoding="utf-8")
)
networks = manifest.get("networks") or {}
total = sum(int(n.get("count", 0)) for n in networks.values())

print(f"{len(networks)} networks, {total} reflectors, generated {manifest.get('generated')}")
for name in sorted(networks):
    print(f"  {name:6} {networks[name].get('count')}")

missing = EXPECTED - set(networks)
if missing:
    sys.exit(f"refusing to publish: missing networks {sorted(missing)}")
if total < FLOOR:
    sys.exit(f"refusing to publish: only {total} reflectors, expected ~3200")
