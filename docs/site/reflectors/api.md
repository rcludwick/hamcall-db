# Reflector directory API — v1 design

The contract for the static reflector directory hamcall-db publishes. This is the
document to change when the shape changes; the OpenAPI file is generated to match
it, and clients (astar's network picker first) are written against it.

## Why this exists

Digital-voice reflector directories are scattered, each behind a different
upstream with different terms, uptime and address formats. DVRef covers M17, YSF,
NXDN, P25, URF and DMR; the XLX registry covers D-Star properly. A client that
wants a "pick a reflector" list should not have to speak six upstreams, hold an
API token, or re-derive per-network naming rules.

So hamcall-db does that once and publishes files. **No token, no account, no rate
limit — it is JSON on a CDN.**

## Paths

```
/api/v1/index.json                            service manifest: what exists, how fresh
/api/v1/reflectors.json                       every reflector, one file
/api/v1/reflectors/{network}.json             one network
/api/v1/reflectors/dmr/{system}/talkgroups.json   one DMR network's talkgroups
/api/v1/openapi.json                          this contract, machine-readable
```

`/api/v1/reflectors.json` is the endpoint most clients want. The per-network
files exist for **failure isolation**: upstreams fail independently, and a client
that only does D-Star should not re-download 1400 YSF rows to find out nothing
changed.

!!! note "Why `.json` and not a bare `/api/v1/reflectors`"

    These are static files on GitHub Pages. An extensionless path is not served
    with a JSON content type, and a directory does not fall back to `index.json`
    the way it does to `index.html`. The `.json` suffix is what makes the URL
    work and self-describe; treat `/api/v1/reflectors` as the resource and
    `.json` as its representation.

## The entry shape

Every entry is a **common envelope** plus a **discriminated `dial` object**. The
envelope is what you search and display; `dial` is what you connect with, and it
differs per protocol because the protocols genuinely differ.

```json
{
  "network": "dstar",
  "id": "XLX836",
  "name": "XLX836",
  "aliases": ["XRF836", "REF836", "DCS836"],
  "description": "Welcome",
  "country": "US",
  "sponsor": "N7MKY",
  "dashboard": "http://xlx.n7mky.com",
  "source": "xlx",
  "dial": {
    "kind": "dextra",
    "host": "45.56.69.219",
    "port": 30001,
    "callsign": "XRF836",
    "modules": ["A", "B", "C"]
  }
}
```

### Envelope fields

| Field | Required | Meaning |
|---|:--:|---|
| `network` | yes | `dstar` \| `m17` \| `ysf` \| `nxdn` \| `p25` \| `urf` \| `dmr` \| … |
| `id` | yes | Stable within `network`. `network` + `id` is the primary key and the thing a client stores as a favourite. |
| `name` | yes | Display name. Falls back to `id` when upstream has none. |
| `aliases` | no | Other names this reflector is known by. Searchable. See "Aliases" below. |
| `description` | no | Free text from upstream. |
| `country` | no | ISO-ish country code or name, verbatim from upstream. |
| `sponsor` | no | Who runs it. |
| `dashboard` | no | Web dashboard URL. |
| `source` | yes | Which importer produced the row (`xlx`, `dvref`, …). Provenance, for debugging a wrong entry. |
| `system` | no | Which DMR network a server belongs to; **DMR only**. Present on every `dmr` row, absent on every other network. Repeated in `dial.system`. |
| `talkgroups` | no | Path of **our mirror** of that network's talkgroup list, relative to the API root; **DMR only**, and absent until the network has been mirrored. Not the same thing as `dial.talkgroups_url` — see "DMR talkgroups". |
| `dial` | no | How to connect. **Absent means listed-but-not-dialable** — see below. |

### `dial` variants

`dial.kind` is the discriminator. Every variant carries `host`, and every variant but
`urf` carries `port`.

| `kind` | Networks | Extra fields |
|---|---|---|
| `dextra` | dstar | `callsign` (the `XRF…` name sent in RPT1/RPT2), `modules` |
| `m17` | m17 | `callsign` (`M17-xxx`), `modules` |
| `ysf` | ysf | — |
| `nxdn`, `p25` | nxdn, p25 | — |
| `urf` | urf | `modules` |
| `mmdvm` | dmr | `system`, `requires`, `talkgroups_url`, `talkgroup`, `timeslot` |

`requires` lists what the **operator** must supply and the directory therefore
cannot: `["dmr_id", "password"]`. This is how DMR fits without the schema
pretending a public file can carry a per-user credential. A client seeing
`requires` should prompt rather than attempt a connect.

### DMR

**A DMR row is one master SERVER, not one network.** Upstream lists networks, each
holding zero or more servers; a network is an organisation, and what you actually
point a hotspot at is a server with a host and a port. So the server is the row, its
`id` is the server's slug, and a network with no servers publishes nothing.

**`system` is load-bearing.** It names the DMR network the server belongs to
(`freedmr-network`, `systemx`, …), and without it the rest is not enough to talk to
anybody: a talkgroup number is only defined *within* one network, so TG 235 on one
system is not TG 235 on another. Treat `system` as part of the answer to "what did I
just connect to", not as a label.

It is published **twice**, on purpose: in the envelope on every `dmr` row, and again in
`dial.system` when the row is dialable. The dial copy keeps a dial object
self-contained for a client that switches on `kind` and reads nothing else; the envelope
copy is the one that survives on a server with no dial at all (see below). They always
agree.

**Talkgroups are both linked and mirrored.** `dial.talkgroups_url` points at DVRef's
own list for that network — canonical, always current, and needing a token or their
anonymous tier. The envelope's `talkgroups` points at *our* copy of the same list,
served without a token, and is present only for the networks that have been mirrored.
Prefer the mirror when you have no token; follow `talkgroups_url` when you need the list
to be current to the minute. See "DMR talkgroups" below.

**`talkgroup` and `timeslot` are reserved.** They are in the contract and absent from
every published row today: a mirrored talkgroup list is its own file, not a column on a
server row, and these two would say which single talkgroup a row is pinned to — which
nothing upstream publishes. They stay declared so such a row could be added without a
version bump; a client can only ignore-what-it-does-not-know if the field was declared.
`timeslot` is `1` or `2`, and only means anything alongside a `talkgroup`.

**Servers with no usable address are listed without a `dial`.** Upstream's `dns` column
is free text and is not always a host — several entries carry a dashboard URL
(`https://apollo.dmr.uk.pe/dashboard/`) or a host with a path. Anything that is not a
bare hostname or IP literal is refused, the numeric address is used instead, and if
there is none the row is published with no `dial` at all rather than an address a client
cannot resolve. Such a row still carries `system`, `name`, `sponsor`, `country` and
`dashboard` — it is a place you can identify and read about, just not one this data can
dial. `requires` and `talkgroups_url` are dial-only, because they are instructions for
making a connection this row does not offer.

**Descriptions are upstream HTML**, as for every DVRef network — they arrive as written
on the network's own dashboard, tags, entities and all, with only email addresses
removed.

### DMR talkgroups

A DMR master tells you where to connect; a **talkgroup** is what you actually talk on
once you are there. Numbers are only defined *within* one network — TG 235 on SystemX
and TG 235 on FreeDMR are different conversations — so the mirror is per network, keyed
by the same `system` slug the server rows carry.

```
GET /api/v1/reflectors/dmr/systemx/talkgroups.json
```

```json
{
  "schema_version": 1,
  "api_version": "v1",
  "network": "dmr",
  "system": "systemx",
  "generated": "2026-09-07",
  "client_refresh_days": 7,
  "license": "CC BY 4.0",
  "attribution": "Reflector data provided by DVRef — https://dvref.com/",
  "source": {
    "name": "DVRef",
    "url": "https://dvref.com/api/v2/dmr/networks/systemx/talkgroups/"
  },
  "count": 21,
  "talkgroups": [
    { "tg": 69, "name": "CQ North West UK" },
    { "tg": 235, "name": "235 Alive" }
  ]
}
```

Rows are sorted by `tg` and carry nothing else: `system` and the date are the same for
every row in the file, so they live in the envelope rather than being repeated. `name`
is omitted when upstream has none.

**Discover them from the manifest, do not probe for them.** Only some networks have a
mirror, and `index.json` says which:

```json
"networks": {
  "dmr": {
    "url": "reflectors/dmr.json",
    "count": 249,
    "generated": "2026-09-07",
    "talkgroups": {
      "systemx": {
        "url": "reflectors/dmr/systemx/talkgroups.json",
        "count": 21,
        "generated": "2026-09-07",
        "fetched": "2026-09-12"
      }
    }
  }
}
```

A network absent from that map has no mirrored list — fetch `dial.talkgroups_url`
instead, or show none. Every `dmr` server row for a mirrored network also carries the
same path in its envelope `talkgroups` field, so a client that has a row in hand never
has to assemble a URL.

#### Freshness: two dates, and why they differ

**This is a rotating mirror, and it is the one endpoint here that is deliberately
behind.** Talkgroups live behind a *per-network* upstream endpoint, and there are 172
networks (111 with servers) against an authenticated budget of **60 requests an hour per
account** — shared with the six requests the rest of the nightly build already spends.
A nightly sweep does not fit and would throttle the whole directory.

So each night the build refetches the **40** networks fetched longest ago, and every
other network republishes the list it already had. Every network comes round roughly
**every three nights**, and a newly listed one gets its talkgroups on its first or
second night.

That gives a client two dates, and they answer different questions:

| Date | Where | Means |
|---|---|---|
| `generated` | the talkgroup file | When this network's talkgroups last **changed**. |
| `fetched` | `index.json`, in the `talkgroups` map | When they were last **confirmed** against upstream. |

The gap between them is how long the list has been *known unchanged* — not how stale it
is. A `generated` of three months ago and a `fetched` of last night means a settled
network, not a neglected file.

Splitting them is what keeps the files stable: a refetch that finds the same rows leaves
the file byte-identical and only moves `fetched` in the manifest, so a night that
changes nothing produces no diff in any of the 111 mirrors. (Putting the fetch date in
each file instead would rewrite forty of them a night to say that nothing happened.)

The rest of the rules follow from the budget:

* a network is not refetched at all within two days of its last fetch, so a rebuild on
  the same day spends no requests and changes nothing;
* if upstream throttles the build, the slice stops for the night, every list keeps its
  previous copy, and no `fetched` date moves — so the networks that missed their turn
  are first in line tomorrow. The reflector directories are fetched first, so talkgroups
  can never starve them;
* a network that answers with an empty list where it previously had rows keeps its
  previous file, and is retried the next night rather than waiting out the rotation.

A talkgroup list changes far more slowly than this window, so a few days behind is not a
practical problem. But if you need the current list to the minute — a client that is
about to key up on a talkgroup a sysop added this morning — follow `talkgroups_url` to
DVRef, which is exactly why that field stays on every row.

**Licence, the same as everything else here: CC BY 4.0.** The talkgroup files carry
`license`, `attribution` and `modifications` inline like every other file, and
attribution is required if you redistribute them.

**`urf` is the one variant without a required `port`.** Upstream publishes none for any
of the 89 URF reflectors, and a urfd speaks several protocols at once, so there is no
single port to supply — inventing one would contradict rule 4 below. Dropping `dial`
from those entries instead would throw away the host as well, which helps nobody, so the
URF variant carries `host` (plus `modules`) and no port. Every other variant requires a
port and an entry that lacks one is emitted without a `dial` at all.

## Extensibility rules

These are the rules that let this grow to new services without breaking clients.

1. **Unknown `network` or `dial.kind` must be ignored gracefully.** A client may
   display such an entry, but must never attempt to connect to one. This is the
   whole extension mechanism: a new service ships as a new `kind`, and old
   clients degrade to "listed, not offered" instead of breaking.
2. **Adding a network, a `dial.kind`, or a field is not a version bump.** Older
   readers ignore what they do not recognise; newer readers treat absent as unset.
3. **A bump is for meaning changing** — a field renamed, a unit changed, a type
   swapped, a value re-interpreted. A bump moves the path (`/api/v2/…`) and the
   old path keeps serving until clients migrate.
4. **`dial` absent means not dialable from this data.** Do not invent a default
   port to fill the gap; an entry you cannot address is better shown greyed than
   dialled wrongly.

## Aliases, and why they are not cosmetic

The same reflector is known by different names on different networks, and
worse, **the same name can mean different machines**.

An XLX reflector listed as `XLX836` answers to `XRF836` on the DExtra wire — so
`XRF836` is a genuine alias, and a user typing either should find it.

But standalone XRF reflectors — the original xrefl.net DExtra network — are
*different machines* that share the numbering. Measured 2026-08-26, 13 of 14
sampled `XRF###`/`XLX###` pairs resolved to entirely different servers:
`XRF002` is `xrf002.dstar.club`, while `XLX002` is a host in China.

So: entries are **never** deduplicated across that boundary, and `aliases` holds
only names that genuinely address *this* entry. Collapsing them would send an
operator to a reflector on another continent.

### Three protocols, three names

D-Star has three linking protocols — DPlus (`REF`), DExtra (`XRF`) and DCS
(`DCS`) — and an XLX reflector answers on all three. So `XLX836` carries
`XRF836`, `REF836` **and** `DCS836`, all naming one machine at one address.
Those names are searchable and resolvable; they are not a claim that the
reflector must be reached over that protocol. The `dial` object still says how
to connect, and for D-Star it always says `dextra`.

Aliases are attached by **address**, never by number. `REF836` and `XRF836` are
both `45.56.69.219`; `REF001` is `104.237.157.7` while `XRF001` is
`217.154.120.107` — unrelated machines that happen to share a number.

### An alias is never another entry's `id`

**A name resolves to exactly one entry.** If a string is an alias of one row and
the `id` of another, a client that indexes both resolves it to whichever it saw
first — silently, and differently depending on row order. So the publisher drops
the alias and keeps the id.

This is not hypothetical: 44 D-Star names collided this way until 2026-08-28.
`XLX002` carried the alias `XRF002` (its DExtra callsign) while a standalone
`XRF002` existed as its own entry — the first in China, the second in the US.
Clients resolving `XRF002` reached the wrong one.

The dropped alias costs nothing. The row is still found by its own `id`, and the
wire callsign lives in `dial.callsign`, which is untouched — so a client
dialling `XLX002` still sends `XRF002` in the RPT1/RPT2 header, which is what
that field is for. **Do not reconstruct aliases from `dial.callsign`**; that
would put the collision straight back.

## Search

Clients search locally — the whole set is a few hundred KB gzipped, and a static
file cannot offer a query parameter. Index these fields:

`id`, `name`, `aliases`, `description`, `country`, `sponsor`

Match case-insensitively and on substrings; `XLX8`, `836`, and `n7mky` should all
find the example above. Filter by `network` before text where the UI has a
network already selected.

## Freshness

| Field | Where | Meaning |
|---|---|---|
| `generated` | every file | The date of the **upstream data**, not of the build. |
| `client_refresh_days` | every file | How often a client should re-check. Currently **7**. |
| `count` | every file | Row count, so a client can sanity-check a truncated fetch. |

`generated` is content-derived on purpose: the job rebuilds nightly, but a night
that finds nothing new produces byte-identical files. A client comparing
`generated` therefore sees movement only when the data actually moved.

A **DMR talkgroup file** follows the same rule and adds a second date beside it: its
`generated` is when that network's talkgroups last changed, and the manifest's `fetched`
is when they were last confirmed — see "DMR talkgroups". The manifest's own top-level
`generated` deliberately ignores both: a rotation must not tell every client that the
directory changed on a night when no reflector did.

**Cache for a week.** Reflector addresses change on a scale of weeks. Polling
harder costs bandwidth and buys nothing, and there is no SLA here to lean on.

## Licence

The published data is **CC BY 4.0** — attribution required, commercial use
permitted, and you must indicate modification. Every file carries `license`,
`attribution` and `modifications` inline so the terms cannot get separated from
the data. See `LICENSE-CC-BY`.

One of those modifications is substantive: **email addresses are stripped from the
free-text fields** (`name`, `sponsor`, `description`) and replaced with
`[email removed]`. These files are bulk-downloadable JSON on a CDN, which turns a
sysop's address written into a reflector blurb into a harvestable list — a different
exposure from the same text rendered on a dashboard page. It is the same reasoning that
truncates person grids to four characters and keeps street addresses out of the callsign
dataset entirely.

This is deliberately **not** the CC BY-NC licence on hamcall-db's callsign
dataset. Merging the two would impose a non-commercial restriction that CC BY 4.0
grants away, which the licence forbids. Keep them apart.
