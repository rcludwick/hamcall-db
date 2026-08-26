# Backlog

Durable backlog for **hamcall-db**.

Session work goes in the **Claude Code task tracker** (`TaskCreate` / `TaskUpdate` /
`TaskList`): create a task before starting, mark it `in_progress` when you pick it up,
`completed` when it's done. Anything that outlives the session belongs in this file.

Migrated off the **au** (`au`) tracker on 2026-07-29 — do NOT invoke `au`.
The 4 open items below each carry their full description and design text
inline. All 39 issues (35 of them closed) were exported to
`docs/issues-archive.jsonl`, which is gitignored and local-only; a committed copy of the
tracker's final state survives in git history at the migration commit.

## Open items (5)

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
though `refresh` and `release` succeed. The committed JSON under `site/api/` stays
correct regardless.

### au-1022 — Verify ISED amateur.txt real column layout against in-zip README
*MED · task · cx:1*

**Spec:** au-f694's ISED importer column order is an ASSUMPTION (the real spec lives in a README inside amateur.zip; exact order/delimiter not publicly documented). Download a real amateur.zip, read its README, and correct the named _* column constants + delimiter + qualification-flag map in sources/ised.py if they differ. Add a fixture slice from real data.

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
