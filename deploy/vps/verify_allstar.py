"""Refuse to publish a broken AllStar directory.

Run from the repo root after `hamcall-db-allstar`. The command already keeps the
previous file when the new one shrinks, so this is a last check on the file that
would be committed. A missing file is fine: the step is optional and the
reflectors publish without it.
"""

import json
import pathlib
import sys

PATH = pathlib.Path("docs/site/api/v1/allstar.json")
# The directory holds roughly 35,000 nodes; a floor well under that catches a
# collapse without tripping on ordinary churn.
FLOOR = 30000

if not PATH.exists():
    print("no allstar.json - nothing to check")
    sys.exit(0)

doc = json.loads(PATH.read_text(encoding="utf-8"))
nodes = doc.get("nodes") or []

if doc.get("api_version") != "v1":
    sys.exit(f"refusing to publish: allstar api_version {doc.get('api_version')!r}")
if doc.get("count") != len(nodes):
    sys.exit(f"refusing to publish: allstar count {doc.get('count')} != {len(nodes)} nodes")
if len(nodes) < FLOOR:
    sys.exit(f"refusing to publish: only {len(nodes)} allstar nodes, expected >= {FLOOR}")
bad = [n for n in nodes if not str(n.get("node", "")).isdigit()]
if bad:
    sys.exit(f"refusing to publish: {len(bad)} allstar nodes with a non-numeric id")

print(f"allstar: {len(nodes)} nodes")
