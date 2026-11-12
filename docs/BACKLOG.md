# Backlog

Durable backlog for **hamcall-db**.

Session work goes in the **Claude Code task tracker** (`TaskCreate` / `TaskUpdate` /
`TaskList`): create a task before starting, mark it `in_progress` when you pick it up,
`completed` when it's done. Anything that outlives the session belongs in this file.

Migrated off the **au** (`au`) tracker on 2026-07-29 — do NOT invoke `au`.
The 12 open items below each carry their full description and design text
inline. All 39 issues (35 of them closed) were exported to
`docs/issues-archive.jsonl`, which is gitignored and local-only; a committed copy of the
tracker's final state survives in git history at the migration commit.

## Open items (12)

### hdb-crawl — Private crawler for per-callsign sources, and the Australian name join
*HIGH · feature · cx:3*

**Spec:** Decided 2026-11-02 (Rob): per-callsign fetching is OK if polite and cached, but
the crawler script must NOT be published. Plan: a private repo (e.g.
`rcludwick/hamcall-crawl`) with a runner on the IONOS box (add it via `runner_repos` in
`~/dev/gh-runners`, never by hand). It crawls CQ WW / CQ WPX public logs (name, 4-char
grid), POTA user profiles for callsigns we already hold, and ACMA's call-sign register
(assigned callsigns + qualification, CC BY 4.0 website content), then uploads only its
data output (callsign, name, town, 4-char grid, source) to a release here; the public
build reads it as one more input. Australian individuals then get names from those
public sources, never from ACMA's `client.csv`: add a `name_source` column, a guard test
that no AU individual's identity fields come from ACMA, and a NOTICE paragraph saying
the dataset combines ACMA callsigns with separately published public sources and does
not redistribute ACMA's Client Information. Also usable as single files: SKCC roster,
CWops roster, IRLP nodes, NZART Call Book PDF (check its in-document notice; drop street
addresses). Research: `~/dev/notes/docs/superpowers/notes/hamcall-db/2026-11-02-new-data-sources.md`.
Write a design doc in `~/dev/notes/docs/superpowers/designs/hamcall-db/` first.

### hdb-intl-followups — Follow-ups from the 2026-11-02 national sources
*MEDIUM · task · cx:2*

**Spec:** (a) No geocoder tables for NO/DE/BR/AR/UY/PL, so `grid` is NULL for all of them
(extends au-4248). (b) URSEC skips 5 visiting reciprocal calls (`OE3GEA/CX`…) because
cty.dat resolves them to the home country; teach enrichment `/<prefix>` suffixes, then
publish them. (c) BNetzA list 5 (repeaters/beacons) uses the trustee's town; the site
town might be more useful. (d) UKE sits behind Imperva; it worked from GitHub runners on
2026-11-02, watch for challenges. (e) Brazil's licence evidence: the dados.gov.br record
saying CC BY now 404s; re-check if it reappears.

### hdb-acma-licence — Follow-ups to the ACMA natural-person rule
*HIGH · decision · cx:2*

**Spec:** Decided 2026-11-01 and implemented on `work/hdb-acma`: ACMA records are
published, but where the licensee is a natural person only the callsign and licence
fields go out (ACMA licence clause 8). The rule is `is_natural_person` in
`hamcall_db/sources/acma.py`; client types 1-5 (government, Company) publish in full,
and so do type 6 "Community or Volunteer Group" clubs; everything else (7 Person, type-6
entries naming an individual, blank, unknown) is withheld.
NOTICE now carries the real ACMA licence terms and the clause 9 attribution. Prior
history carried forward is scrubbed by `withhold_prior_history`. Coverage is ~760
licences (since 2024-02-19 most amateurs hold a class licence and are not in the RRL).
Open questions for the owner:

1. **Type 6 clubs — resolved 2026-11-02.** Owner's decision: clubs are collectives, not
   natural persons, so they are published. ACMA also files some individuals under type 6,
   so a type-6 record publishes in full only when its licensee name contains an
   organisation word (`_ORGANISATION_NAME` in `acma.py`) and isn't a nominee/attention
   line. On the 2026-11-01 extract that publishes 111 of 149 type-6 names (all clubs and
   groups, plus the partnership "P J O'BRIEN AND COMPANY") and withholds 38: 35
   individuals, "Nominee <person> <callsign>", "Attn: The Secretary" and "Tasar". Recheck
   the withheld list when ACMA's data changes; a new club whose name has no organisation
   word will be withheld (fail closed) until the word list grows.
2. **Already-published releases — resolved.** Releases before 2026-10-21 contained ACMA
   rows with individuals' names and postcodes. Those assets were deleted on 2026-11-02
   with the rest of the callsign assets (see au-1022 under Shipped), and `latest` was
   rebuilt from scratch, so nothing remains downloadable.
3. **No grid for AU organisations.** The offline geocoder's seed tables have no
   Australian postcodes, so even published organisations get `grid = None`.
4. **Names for Australian individuals — no legitimate source exists (researched
   2026-11-02).** Since the class licence, ACMA deliberately stopped publishing who holds
   a callsign: its online call sign register shows only callsign, status and
   qualification level ("Personal information is not available"). The WIA pulled its
   public callsign database "to comply with the ACMA deed" (WIA news, 2020-01-06).
   Every aggregator forbids redistribution: RadioID.net ("Do not mirror or re-publish
   RadioID as a public feed, bulk export, reusable database… without written
   permission", radioid.net/api), QRZ (personal non-commercial use only, eutc.txt §12),
   HamCall (commercial), HamQTH (no AU import, no redistribution licence), the WIA
   printed Callbook (stale 2019, copyrighted). Do not ingest any of them.
   No outreach to ACMA or the WIA (owner's policy, 2026-11-02: no permission requests).
   Names stay consumer-side: downstream apps look them up live with the operator's own
   QRZ/HamQTH account (adif backlog, 2026-11-02).

### hdb-dl-timeout — Add a timeout to every upstream urlopen
*MED · bug · cx:1*

**Spec:** `sources/fcc.py`, `sources/acma.py` and `sources/ad1c.py` call `urlopen` with no
`timeout`, so a stalled server hangs the Release job until the Actions limit and the
transient-retry in `sources/base.py` never fires. ISED already passes `DOWNLOAD_TIMEOUT`;
do the same everywhere. Also consider retrying `ad1c.download_cty` (it runs outside the
source loop, so it gets no retry and isn't in `skipped-sources.txt`).

### hdb-real-fixtures — Replace fabricated FCC and ACMA fixtures with real slices
*MED · test · cx:2*

**Spec:** `tests/fixtures/fcc/EN.dat` ("Jane Doe, 1 Beacon St") and
`tests/fixtures/acma/client.csv` ("John Smith, 42 Wireless Rd") are invented, against the
"small slices of real data" rule. A fabricated ISED fixture is exactly how the ISED
importer shipped broken for five months (see au-1022 below). Take small real slices, but
**overwrite every street-address field with synthetic text** — the repo is public — as was
done for `tests/fixtures/ised/amateur.txt`.

### hdb-ised-extras — ISED follow-ups from the parser rewrite
*LOW · task · cx:1*

**Spec:** (a) No Canadian postal/city centroids, so `grid` is NULL for every ISED row
(covered by au-4248). (b) Club rows publish the club name/location and drop the trustee;
a `trustee` column would be an additive schema change if wanted. (c) Merge title-casing
produces `St. John'S` and `Bc Fm Communications Assn`; FCC mixed-case cities like
`honolulu` pass through untouched. (d) `ised.py`'s docstring says there is no delimited
variant, but ISED also publishes `amateur_delim.zip`; correct the docstring (or consider it).

### hdb-refl-pages — Enable GitHub Pages + add the DVREF_API_TOKEN secret
*HIGH · task · cx:1*

**Spec:** The reflector directory (`hamcall_db/reflectors.py`, `sources/dvref.py`,
`sources/xlx.py`, `.github/workflows/reflectors.yml`) is built and committed, but two
things must be done by a human before the nightly job can publish:

1. **GitHub Pages is not available on this repository.** `rcludwick/hamcall-db` is
   private and `has_pages` is false; Pages from a private repo requires a paid plan
   (Pro/Team/Enterprise), and every Pages-enabled repo on this account today is public.
   Options, in rough order of least surprise:
   * make this repo public — the build code is already MIT and the data terms are
     already documented, so nothing here is secret; or
   * publish `site/` from a separate public repo; or
   * upgrade the plan, at which point the workflow works unchanged.

   Note that a Pages site published from a private repo is still publicly readable by
   default, which is what we want here — the constraint is the plan, not visibility.

2. **`DVREF_API_TOKEN` repository secret** — mint at <https://dvref.com/accounts/token/>
   (free, needs a DVRef account). Without it the job still succeeds and still publishes
   D-Star from the XLX registry, but every DVRef network is skipped with a warning.

Until Pages exists, `.github/workflows/reflectors.yml`'s `deploy` job will fail even
though `refresh` and `release` succeed. The committed JSON under `docs/site/api/v1/` stays
correct regardless.

### hdb-4b8c — PERF: real PAD-US GDB read is very slow (organizePolygons + /vsizip random access on the 651k-feature Combined layer)
*MED · task · cx:2*

**Spec:** FINDING (hdb-d90d validation): reading the REAL PAD-US 4.0 Combined feature class (651,088 MultiPolygon features, ESRI:102039 Albers) through padus._read_padus_features is very slow. Two compounding causes observed against data/raw/padus/2026-06-19/padus_national.gdb.zip:
  1. GDAL 'organizePolygons() received a polygon with more than 100 parts' — the default polygon-organization does expensive ring-containment tests on the big national multipolygons. Setting OGR_ORGANIZE_POLYGONS=ONLY_CCW (assume hole rings are CCW per OGC) silences the warning and should be much faster WITHOUT losing holes — but a single bbox read STILL hadn't returned after ~2 min, so it is not the whole story.
  2. /vsizip random access: reading a File Geodatabase (incl. its spatial index) IN PLACE from inside the zip via /vsizip is slow vs. an unzipped .gdb on disk. A targeted Unit_Nm where-filter scans all 651k features; even a bbox spatial filter was slow over /vsizip.

The production _read_padus_features reads the WHOLE Combined layer (no filter) once, then matches every park — so a real --all build would be extremely slow / memory-heavy. This is why the public release runs WITHOUT the padus group (point grids only); enabling real polygon grids in the release is gated on solving this.

OPTIONS to evaluate:
  - Unzip the .gdb to disk once (data/work/) and read the directory .gdb (not /vsizip) so GDAL uses the spatial index efficiently.
  - Set OGR_ORGANIZE_POLYGONS=ONLY_CCW (verify holes/inholdings still correct vs. the Bogus-Basin regression) via pyogrio.set_gdal_config_options.
  - Read once into memory as a prepared spatial index (geopandas sindex / STRtree) and query per park, instead of re-scanning.
  - Consider use_arrow=True in read_dataframe for faster IO.
  - Possibly read only needed columns + geometry (already done) and skip features with null Unit_Nm early.

ALSO surfaced same run: urllib _urllib_fetch reads the whole 1.7 GB into memory (response.read()) before writing — stream to disk instead (separate small fix; can fold in here or its own nugget).

ACCEPTANCE: a real --all build with --group padus completes the park-grid step in reasonable time/memory and produces correct polygon grids (holes preserved) for a sample of parks (Boise NF inholding, Eagle Island single-cell).

### hdb-6e95 — Note expired/removed callsigns in the DB (status field) instead of silently dropping them
*MED · task · cx:2*

**Spec:** IDEA (user, 2026-06-19): when a callsign is no longer active, NOTE it in the DB rather than dropping it. Two DISTINCT signals, and we often cannot tell which:
  (A) EXPLICITLY expired/cancelled — the upstream record still exists but carries a non-active status.
  (B) REMOVED from source — the callsign was in last week build but is GONE from this week upstream snapshot (DB purge, vanity change, SK cleanup). Reason unknowable; we only know it vanished.

CURRENT BEHAVIOR: hamcall_db/sources/fcc.py reads HD.dat license_status (col 5; A=active, E=expired, C=cancelled, T=terminated) but parse_dir(active_only=True) FILTERS to status==A, so non-active licenses are dropped entirely. Signal (A) is already in HD.dat and we throw it away.

PROPOSAL:
  - SCHEMA (additive, minor bump per mem-c6e0): add status to output: active | expired | cancelled | terminated | removed. Consider expiry_date (HD.dat has an expired-date column) and last_seen (ISO date of the most recent build that still contained the callsign).
  - SIGNAL A: stop hard-filtering; emit non-active FCC rows with status mapped from HD.dat (A active, E expired, C cancelled, T terminated). Keep active_only as a knob, default to include-with-status.
  - SIGNAL B (removed): detected via the weekly diff that already powers SCD2 history (mem-4784/34da). A callsign present in the prior current set (via --db-in / --history-in) but absent from this build upstream = status removed, stamp last_seen = prior build date, carry the row forward as a TOMBSTONE.
  - ISED/ACMA: check whether their schemas expose status/expiry; otherwise only signal B applies to them.

CONTRACT TENSION (discuss before building): today current = one row per ACTIVE callsign (mem-c6e0). Including expired + removed tombstones changes that semantics. Options: (1) keep current = active-only and add a SEPARATE inactive/expired parquet+table; (2) one table with a status column and let consumers filter (cleaner for autocomplete, but grows the file with tombstones); (3) only mark expired (A) inline, keep removed (B) in history only. Pick with the downstream adif autocomplete use-case in mind. Touches the redistribution contract -> agree direction before coding.

ACCEPTANCE (once direction chosen): a callsign that goes E in ULS surfaces with status expired (not dropped); a callsign that vanishes between two builds surfaces with status removed + last_seen; the active set/contract for existing consumers is preserved or its migration documented.

### au-4248 — Full per-country postal/city centroid population (GeoNames/Census ingestion)
*LOW · task · cx:3*

**Spec:** au-63fb shipped a curated public-domain subset (159 city / 55 postal). For full coverage, implement the documented build-time ingestion (GeoNames postal-codes CC-BY 4.0 -> NOTICE entry needed; or Census ZCTA gazetteer public-domain) into data/raw/ + a compaction step. Do NOT vendor gigabytes into git. See hamcall_db/data/README.md.

### hdb-refl-vps — The nightly reflector build runs on my-vps, not in Actions
*INFO · note · cx:1*

**Why:** DVRef sits behind Cloudflare, which refuses GitHub-hosted runner IP
ranges with a 403 HTML block page even for a valid token and a correct
User-Agent. Verified 2026-08-26: the same token returns 200 from my-vps and from
a workstation; a deliberately bad token returns a JSON `401 {"detail":"Invalid
token."}`; a `Python-urllib` User-Agent returns `403 error code: 1010`; and an
absent one returns `403 A valid User-Agent header is required`. So the origin is
being refused, not the credential. The XLX registry (D-Star) is not behind
Cloudflare and works from Actions.

**Where it lives:** `root@74.208.108.191`. The source of truth for the scripts
and unit files is `deploy/vps/` in this repo (see its README); the box copies
are installed from it. On the box —
- `/opt/hamcall-db/build.sh` — pull, build, verify, commit-if-changed, push
- `verify.py` (refuses to publish a build missing a network) and
  `verify_allstar.py` (checks the AllStar directory) are not installed on the
  box: `build.sh` runs them from the repo clone, at `deploy/vps/`. Only
  `build.sh` and the systemd units are copied into place.
- `/opt/hamcall-db/env` — `DVREF_API_TOKEN`, mode 600
- `/opt/hamcall-db/repo` — clone, pushing via the `my-vps nightly reflector
  build` deploy key (write, key id 161335913)
- `hamcall-db-reflectors.timer` — 05:30 UTC, `Persistent=true`

`pages.yml` publishes whatever lands on main. `reflectors.yml` keeps only
`workflow_dispatch`: a run from GitHub rebuilds D-Star from XLX alone (892 rows,
losing DVRef's 61 XRF entries), which is a ~6% shrink that PASSES the shrink
guard and would therefore be published rather than rejected.

**Open:** ask DVRef to allowlist authenticated requests, which would let this
move back into Actions and remove a machine from the critical path. A draft
ticket exists; not sent.

### hdb-allstar-desc — AllStar descriptions are not address-guarded
*MEDIUM · task · cx:2*

**Why:** the AllStar directory (`api/v1/allstar.json`, public) blanks
street-address-looking locations, but the spec guards the location only. The
description field is published as typed, and at least one real description holds
the street address of a public building. Running `looks_like_street_address` on
descriptions is too blunt: on the 2026-11-01 node DB it flags about 267
frequency and tone strings (for example "PL 110.9" read as a house number plus
"Place").

**Spec:** a narrower description check that tells a street address from radio
settings (ignore tokens next to MHz, PL, CTCSS, offsets and decimals before
looking for a house number plus street word), then blank or redact the match
the way the location guard does, with a count in the drop report.

**Note:** the location guard (`location_has_street_address`) is heuristic too. On top
of the shared street-word check it blanks any comma part that starts with a 1-4 digit
house number (optionally a letter, range or "/n") followed by a word, unless the word
is a radio, battery or distance unit or the number is a band ("2m"); 5+ digit
postcodes and decimals are spared. It over-blocks some numbered place names
("29 Palms") and can still miss an address written street-first.

## Shipped

### hdb-licence-review — Licence review of every source, and the follow-up sweep (2026-11-02)

Every upstream was checked against its own published terms. Fixed: ISED cited the wrong
licence (OGL excludes personal information; ISED's non-commercial terms apply) and
postcodes are now FSA-only; AD1C is MIT, not non-commercial; AllStarLink's new terms;
LoTW prohibited; SOTA unlicensed; the ODbL release never held OSM data (POTA rows were
mislabelled — the file now holds only `source='osm'` rows); XLX/Pi-Star reflector rows are
labelled per row as having no stated licence. Owner's policy: openly published data with no
stated licence is usable with attribution, and no permission requests are made (NOTICE,
"Project policy for openly published data"). Review record:
`~/dev/notes/docs/superpowers/notes/hamcall-db/2026-11-02-licence-review.md`.

### au-1022 — ISED importer rewritten for the real fixed-width amateur.txt (2026-11-02)
*was: column layout an unverified assumption*

The importer split lines on `;`, but ISED ships a fixed-width report (layout in the zip's
`readme_amat.txt`). Every release from 2026-06-18 to 2026-11-01 put whole raw lines,
**street addresses included**, into `callsign` for ~92k Canadian rows. Fixed in
`work/hdb-ised` (fixed-width slicing, address columns never sliced, callsign-shape and
no-address guard tests, real fixture slice with synthetic addresses). On 2026-11-02 the 168
callsign assets (`.db`, `.parquet`, history) were deleted from all 56 affected releases and
history restarted from a clean build. The same session also fixed: KG4 + 3-letter calls
filed as Guantanamo Bay (4,980 rows), `grid` NULL for every row (geocoder keyed on
`country` before cty.dat set it), and silent source skips (retry + red Release job).

### hdb-refl-dmrtg — DMR talkgroups, mirrored a rotating slice at a time (2026-09-07)
*was: talkgroups linked but not mirrored — 172 per-network endpoints against a 60/hour budget*

`DvrefDmrTalkgroupSource` reads `GET /api/v2/dmr/networks/<slug>/talkgroups/` — one
request per network, and the reason this could not simply be swept nightly. 111 of the
172 networks have servers, against an authenticated budget of **60 requests an hour per
account** that the six directory requests already draw on. So the build fetches a
**rotating slice of `TALKGROUP_SLICE` = 40** networks a night (60 budget − 7 spent and
reserved for a retry − 13 headroom for interactive debugging on the same token), picking
the ones whose lists are OLDEST or missing. Every network turns over in roughly three
nights and a newly listed one is mirrored on its first or second.

**The rotation's state is the published output itself** — no side-car state file to fall
out of sync with what was committed — and it is split from the content date on purpose.
A mirror's own `generated` is when that network's talkgroups last CHANGED, so a refetch
that finds the same rows leaves the file byte-identical; when it was last FETCHED lives
in the manifest's `talkgroups` map as `fetched`, because `index.json` already moves
whenever a count does and forty rewritten mirrors a night would say nothing. The slice
picker reads `fetched` (a list that never changes would otherwise be permanently
"oldest" and starve the rotation), and `TALKGROUP_MIN_AGE_DAYS` = 2 skips a network
confirmed within two days. Talkgroups are fetched LAST and a `DvrefThrottled` stops the
slice for the night — recording no fetch, so those networks are first in line tomorrow —
which is how they can never starve the reflector lists a client needs to connect at all.
A network that fails, or answers with an empty list where it previously had ROWS, keeps
its previous file; a network whose list is genuinely empty answers empty every night and
must NOT be treated as a fault, or it would be refetched forever.

Published as one file per network, `api/v1/reflectors/dmr/<system>/talkgroups.json`
(`{tg, name}` rows sorted by `tg`, CC BY 4.0 inline like every other file), and the
manifest's `dmr` entry gained a `talkgroups` map of `{url, count, generated, fetched}` so
a client discovers what exists instead of probing 111 URLs for 404s. Every `dmr` server row for a
mirrored network carries the same path in a new ENVELOPE field `talkgroups`;
`dial.talkgroups_url` still points at DVRef, which stays canonical — the mirror is
tokenless but days behind, and both facts are documented. Artifacts gained a
`dmr_talkgroups` table (`system`, `tg`, `name`, `synced_at`) in the reflector `.db` —
same CC BY 4.0 file, which is why they may share one — and its own
`hamcall-db-reflectors-dmr-talkgroups-<date>.parquet`. `synced_at` is per ROW because
rows in one file are routinely days apart in freshness.

### hdb-refl-dmr — DMR importer (2026-09-06)
*was: `mmdvm` dial variant is published contract with no source behind it*

`DvrefDmrSource` reads DVRef's `GET /api/v2/dmr/networks/?include_description=true` —
ONE request a night for all 172 networks — and flattens networks-of-servers into **one
published row per master server**; a network with no servers (61 of the 172) publishes
nothing. `ReflectorRecord` grew `system`, `requires`, `talkgroups_url`, `talkgroup` and
`timeslot`, all five in the `mmdvm` dial variant and in the generated `openapi.json`,
and all five now Parquet and SQLite columns. `system` is the load-bearing one — a
talkgroup number is only defined within one network — so it is published TWICE: on the
ENVELOPE of every `dmr` row, and again in `dial.system` where there is a dial. The dial
copy keeps a dial self-contained; the envelope copy is what survives on a server with no
usable address, which would otherwise be published unable to say which network it is on.

Upstream's `dns` column is free text and is not always a host — SystemX fills it with
dashboard URLs and publishes no port — so anything carrying a scheme, a path or
whitespace is refused, the row falls back to `ipv4`/`ipv6`, and a server with no usable
address is published WITHOUT a `dial` rather than with an address a client cannot
resolve — but still with `system`, `name`, `sponsor`, `country` and `dashboard`. Against
the checked-in fixture: 44 servers, 35 of them dialable.

Descriptions are published as upstream HTML, exactly as they are for the other five
DVRef networks; stripping tags would be a separate change across all six.

Talkgroups were linked and not mirrored here; hdb-refl-dmrtg mirrored them.

### hdb-release-retention — Decide and implement a retention policy for nightly dated releases
*LOW · task · cx:1*

**Spec:** The callsign Release workflow (`.github/workflows/release.yml`) moved from
weekly to nightly (2026-09-06/07). Each nightly run publishes a new dated
`hamcall-db-YYYY-MM-DD` release (and `hamcall-db-osm-YYYY-MM-DD` when OSM grids are
produced) at roughly 460 MB per CC BY-NC release; the `latest` / `latest-osm` aliases
are cleared and re-uploaded each run so they stay single-build, but the dated releases
themselves are never deleted. At nightly cadence that is ~460 MB/night with no cap —
365+ dated releases and ~168 GB/year if nothing is ever pruned.

Decide a retention policy — for example: keep every dated release for 30 days, then
thin to one per week, then one per month past some age — and implement it as a
workflow step (or a separate scheduled workflow) that deletes both the GitHub Release
and its underlying tag for anything outside the retained set. Consumers pin to dated
releases for reproducibility, so pruning needs to be predictable and documented (README
should say what is guaranteed to still exist and for how long).

Until this ships, nothing is deleted — dated releases accrue indefinitely.
