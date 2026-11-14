# AllStar node directory

*Last reviewed: 2026-11-13. The example and every count on this page were read
from the live `allstar.json` on that date.*

This command downloads the AllStar node directory and picks out node 2002:

```bash
curl -s https://rcludwick.github.io/hamcall-db/api/v1/allstar.json \
  | jq '.nodes[] | select(.node == "2002")'
```

```json
{
  "callsign": "WB6NIL",
  "description": "AllStarLink Parrot",
  "location": "AWS US-EAST-1",
  "node": "2002"
}
```

[AllStarLink](https://www.allstarlink.org/) is a network of linked repeaters,
hotspots and personal radio links. Each of these is a **node**, and each node
has a number that other nodes dial to connect to it. AllStarLink publishes the
list of registered nodes, with the callsign of each node's owner and two free
text fields the owner fills in. `allstar.json` republishes that list as JSON:
41,616 nodes on 2026-11-13, 5,453,084 bytes. It is for software that meets a
node number and needs to say whose node it is and roughly where.

The file is static, on GitHub Pages, at
[`https://rcludwick.github.io/hamcall-db/api/v1/allstar.json`](https://rcludwick.github.io/hamcall-db/api/v1/allstar.json).
There is no API key, no account and no query: you download the whole file and
search it yourself.

## The file

Here is the start of the file, with `nodes` cut to node 2002:

```json
{
  "api_version": "v1",
  "attribution": "AllStarLink node data courtesy of AllStarLink, Inc. (https://www.allstarlink.org/).",
  "client_refresh_days": 7,
  "count": 41616,
  "generated": "2026-11-13",
  "license": "none-stated",
  "license_url": "https://github.com/rcludwick/hamcall-db/blob/main/NOTICE",
  "modifications": "Reformatted from AllStarLink's pipe-separated node list. Malformed lines dropped; locations that look like street addresses removed; email addresses removed from free text.",
  "nodes": [
    {
      "callsign": "WB6NIL",
      "description": "AllStarLink Parrot",
      "location": "AWS US-EAST-1",
      "node": "2002"
    }
  ],
  "rights": "Information about users' nodes (AllStarLink User Content), redistributed under AllStarLink's 'reasonable use of published directories'. No rights granted beyond those AllStarLink provides.",
  "schema_version": 1,
  "sources": [
    {
      "fetched": "2026-11-13",
      "name": "allstarlink",
      "url": "http://allmondb.allstarlink.org/"
    }
  ]
}
```

The envelope fields, as declared by `AllStarDirectory` in
[`openapi.json`](https://rcludwick.github.io/hamcall-db/api/v1/openapi.json):

| Field | Required | Meaning |
|---|:--:|---|
| `schema_version` | yes | Always `1` in v1. |
| `api_version` | no | Always `"v1"`. |
| `generated` | yes | The date of the node list. It stays the same on a night when the list did not change (`allstar_document` in `hamcall_db/build_allstar.py`). |
| `client_refresh_days` | no | How many days a client should wait between downloads: `7`. |
| `count` | yes | Number of nodes. Always equals the length of `nodes`. |
| `nodes` | yes | The nodes, sorted by node number. |
| `license` | no | `none-stated`: AllStarLink states no licence for this data. See [Licence and credit](#licence-and-credit). |
| `license_url` | no | The project's NOTICE file, which records the terms. |
| `attribution` | no | The credit line to reproduce. |
| `rights` | no | What the project may and may not grant, in words. |
| `modifications` | no | What the build changed from AllStarLink's list. |
| `sources` | no | The upstream, as a list with one entry: its `name`, its `url`, and `fetched`, the date the build last read it. |

"Required" is what the schema promises. The nightly build writes every one of
these fields (`hamcall_db/build_allstar.py`). The file is separate from the
[reflector directory](reflectors/index.md): it has its own `generated` date,
its own licence and its own schema, and a reflector client never needs it.

## One node

Every node has the same four keys, and all four are always present
(`AllStarNode` in `openapi.json`). The two free text fields can be null:

| Field | Type | Meaning | Null on, of 41,616 (2026-11-13) |
|---|---|---|--:|
| `node` | string | The node number, as digits. A string, not an integer, with leading zeros removed. | 0 |
| `callsign` | string | The owner's callsign, upper case. | 0 |
| `description` | string or null | What the owner says the node is, as typed. | 19,732 |
| `location` | string or null | Where the owner says the node is, as typed, unless it looked like a street address. | 163 |

`description` and `location` are whatever the owner wrote. A repeater's
description is often its frequency and offset, such as `448.280-` on node
2003. A node on a cloud server, such as 2002 above, gives a data centre as its
location.

The build reads AllStarLink's list as one line per node, four fields separated
by `|`, and drops a line it cannot trust (`parse_directory_report` in
`hamcall_db/enrich_allstar.py`):

- A line without exactly four fields is dropped.
- A line whose node number is not all digits, or whose callsign is empty, is
  dropped.
- When a node number appears twice, the first line wins.
- Empty fields become null.

## Locations that look like street addresses are blanked

Owners often type their home address as the node's location. To keep the file
from becoming a list of homes, the build blanks any location that looks like
a street address: `location` becomes null, and the node stays in the file.

The obvious check is the one the callsign dataset already uses on its name
and town fields: flag a value that starts with a house number and goes on to
a street word, such as `1431 Example St` (`looks_like_street_address` in
`hamcall_db/address_guard.py`). AllStar locations defeat it, because owners
put the town first:

```text
Anytown, IA, 1431 Example St
```

That value starts with `Anytown`, not a number, so the check passes it, and
the street address would be published. (The address is made up; the shape is
the common one.)

So the AllStar check, `location_has_street_address`, runs the obvious check on
the whole value and then again on each comma-separated part. `1431 Example St`
is the third part, and it is caught. Three rules are added for free text
locations (`hamcall_db/address_guard.py`):

- A part that is only digits, such as `50000` in `Anytown, IA, 50000`, is a
  postcode beside a town and is not flagged on its own.
- `AV` counts as a street word after a house number, as in `12 Example Av`.
- A part that starts with a number of 1 to 4 digits and then a word is
  flagged even with no street word, because owners misspell the street type
  or leave it out (`7 Example`). The exceptions are a radio, battery or
  distance unit after the number (`220 MHz`, `10 Miles`) and a band written
  as a number glued to `m` (`2m`).

The check errs towards blanking. A numbered place name such as
`29 Palms, CA` loses its location; one whose number is followed by `Mile`,
such as `108 Mile Ranch`, is spared as a distance. The check can also miss an address written
street first, with no house number leading any part (`docs/BACKLOG.md`,
`hdb-allstar-desc`).

On 2026-11-13, 163 nodes had a null `location`. That count includes nodes
whose owners left the location empty, so it is an upper bound on the number
blanked. The file's `modifications` text says these locations were
"removed"; in the file that means the value is null.

`description` does not go through this check. Run on descriptions, the same
check flags radio settings: on the 2026-11-01 node list it flagged about 267
frequency and tone strings, such as `PL 110.9` read as a house number and
"Place". Descriptions are therefore published as typed. A narrower check for
descriptions is the open backlog item `hdb-allstar-desc` in
[`docs/BACKLOG.md`](https://github.com/rcludwick/hamcall-db/blob/main/docs/BACKLOG.md).

Email addresses are removed from both `description` and `location` and
replaced with `[email removed]` (`redact_emails` in
`hamcall_db/reflectors.py`, called from `hamcall_db/enrich_allstar.py`).

## How the file is refreshed

The file is rebuilt every night by the same job that rebuilds the reflector
directory. A systemd timer on a rented Linux server starts it at 05:30 UTC
plus a random delay of up to 20 minutes
(`deploy/vps/hamcall-db-reflectors.timer`). The
[reflector overview](reflectors/index.md#how-the-directory-is-refreshed)
describes that job. The AllStar part is its own step in
`deploy/vps/build.sh`, run after the reflectors are built:

1. `hamcall-db-allstar` downloads AllStarLink's list from
   `http://allmondb.allstarlink.org/` (`download_allstar` in
   `hamcall_db/enrich_allstar.py`). The list is served over plain HTTP; the
   comment on `ALLSTAR_DB_URL` records that there was no HTTPS endpoint on
   2026-06-16.
2. It parses the list and writes `allstar.json`
   (`hamcall_db/build_allstar.py`).
3. `deploy/vps/verify_allstar.py` checks the file that would be committed.

The build will not publish a broken directory. Each guard leaves the
previously published file in place:

- If the download fails, `hamcall-db-allstar` exits with an error.
- If the list parses to fewer than 1,000 nodes (`MIN_NODES` in
  `hamcall_db/build_allstar.py`), it exits with an error. An HTML error page
  served in place of the list parses to zero nodes.
- If the list has fewer than 66% of the nodes in the published file
  (`SHRINK_GUARD`, shared with the reflector build), it prints a warning,
  keeps the old file and exits normally.
- `verify_allstar.py` refuses a file with fewer than 30,000 nodes, a `count`
  that differs from the number of nodes, an `api_version` other than `"v1"`,
  or a node number that is not all digits.

When either command fails, `build.sh` restores the published `allstar.json`
and goes on to publish the reflectors without it. A failure in the AllStar
step never holds back the reflector directory.

A night on which AllStarLink's list did not change produces a byte-identical
file and no commit. `client_refresh_days` is 7: please download the file at
most once a week. If you fetch it more often, send `If-None-Match` or
`If-Modified-Since`, and GitHub Pages answers an unchanged file with
`304 Not Modified` (see
[caching](reflectors/api.md#caching-and-freshness)).

## Licence and credit

AllStarLink states no licence for its node list, so the file says
`"license": "none-stated"`. AllStarLink's
[Terms of Use](https://www.allstarlink.org/terms/) (effective 2026-09-22)
class information about a user's nodes as "User Content" (§2), exclude User
Content from the CC BY-SA licence on the rest of their site (§8), and permit
"reasonable use of published directories, node lists, and APIs" (§6). The
project republishes the list on that basis and grants no rights beyond
AllStarLink's (NOTICE 5). The
[licensing page](about/licensing.md#the-allstar-directory-states-no-licence)
has the detail.

If you redistribute the data, reproduce this line, which the file also
carries in its `attribution` field:

> AllStarLink node data courtesy of AllStarLink, Inc.
> (https://www.allstarlink.org/).

## The same nodes in the callsign dataset

The [callsign dataset](callsigns.md) has an `allstar_nodes` column: the node
numbers registered to each callsign, built from the same AllStarLink list
(`hamcall_db/enrich_allstar.py`). Use that column to go from a callsign to its
nodes. Use `allstar.json` to go from a node number to its owner, description
and location. The two are built and licensed separately: the column is part
of the CC BY-NC callsign dataset, and `allstar.json` is not.

## Who uses it

[astar](https://rcludwick.github.io/astar/), the author's digital-voice client
for AllStarLink, M17, D-Star and System Fusion, uses `allstar.json` as its
AllStar node directory. The macOS app ships a copy of the file, so a first
launch works with no network, then downloads updates on its own schedule and
shows the file's attribution and licence (`apps/macos/Tools/fetch-allstar.sh`
in the astar repository).
