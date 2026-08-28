# Reflector directory API — v1 design

The contract for the static reflector directory hamcall-db publishes. This is the
document to change when the shape changes; the OpenAPI file is generated to match
it, and clients (astar's network picker first) are written against it.

## Why this exists

Digital-voice reflector directories are scattered, each behind a different
upstream with different terms, uptime and address formats. DVRef covers M17, YSF,
NXDN, P25, URF and a small XRF set; the XLX registry covers D-Star properly; DMR
is masters-plus-talkgroups somewhere else again. A client that wants a "pick a
reflector" list should not have to speak six upstreams, hold an API token, or
re-derive per-network naming rules.

So hamcall-db does that once and publishes files. **No token, no account, no rate
limit — it is JSON on a CDN.**

## Paths

```
/api/v1/index.json                     service manifest: what exists, how fresh
/api/v1/reflectors.json                every reflector, one file
/api/v1/reflectors/{network}.json      one network
/api/v1/openapi.json                   this contract, machine-readable
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
| `mmdvm` | dmr | `requires`, `talkgroups_url` |

`requires` lists what the **operator** must supply and the directory therefore
cannot: `["dmr_id", "password"]`. This is how DMR fits without the schema
pretending a public file can carry a per-user credential. A client seeing
`requires` should prompt rather than attempt a connect.

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
