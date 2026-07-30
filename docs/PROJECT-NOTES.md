# Project Notes — hamcall-db

Durable project knowledge for **hamcall-db**. The output schema and licensing entries are the redistribution contract — treat them as binding.

These notes were recovered from the `au` tracker's memory store when au was removed on
2026-07-29. The `mem-XXXX` ids are kept because they are cited from CLAUDE.md, commit
messages, and backlog items. Add new durable knowledge here as prose; session-scoped notes
belong in the task tracker, and work items in [`docs/BACKLOG.md`](BACKLOG.md).

### mem-afa3

Project: hamcall-db. Weekly-rebuilt amateur radio callsign database, aggregated from FCC ULS (US), ISED (Canada), ACMA (Australia), and AD1C cty.dat (DXCC prefixes). Published as a single Parquet artifact via GitHub Releases. Consumers (e.g. adif) download the Parquet and load it into whatever local store they want.

### mem-c6e0

Output schema: callsign PK, first_name, last_name, city, county, state, postal_code, country, dxcc, grid, license_class, source, synced_at. Street address NEVER in output. county is US-only (NULL elsewhere). Schema is the redistribution contract — consumers depend on it. Schema changes are breaking; rev minor version and document in release notes.

### mem-e3fd

Grid policy: published value is 4-char Maidenhead ONLY (e.g. DN13), never 6-char subsquare. Build pipeline MAY consume street address from upstream (FCC ULS) to geocode precisely, then TRUNCATES to 4 chars before publication. Derivation priority: street geocode > postal_code centroid > city centroid > NULL. ~140km x 70km cell is intentionally coarse for privacy.

### mem-371f

Source licenses (must surface in NOTICE): FCC ULS = public domain; ISED Canada = Open Government Licence – Canada (attribution required); ACMA = CC-BY 4.0 AU (attribution required); AD1C cty.dat = free for non-commercial use, attribution required. Combined dataset distributed under union of these terms. Re-check terms before adding any new source.

### mem-ad43

Build cadence: weekly via GitHub Actions cron. Artifact naming: hamcall-db-YYYY-MM-DD.parquet on Releases page. 'latest' tag/release alias points at most recent build. Consumers should pull 'latest' by default and pin to a date when reproducibility matters.

### mem-8a86

Repo lives at github.com/rcludwick/hamcall-db (private as of 2026-06-16; flip to public once first build ships).

### mem-4784

Artifact model is TWO separate Parquet files (decided 2026-06-16, tracked in au-80c0): (1) the current-state file — the existing contract (mem-c6e0), callsign PK, one row per callsign, UNCHANGED; (2) a separate hamcall-db-history-YYYY-MM-DD.parquet holding callsign holder/location changes over time (SCD2 interval rows), for QSO-time attribution in downstream apps. History is FORWARD-ONLY: upstream sources are current-snapshot only, so history accrues by diffing successive weekly builds; pre-first-build history is not recoverable from these free sources. Consumers opt into history by downloading the second file; the current-state contract is never broken by it.

### mem-34da

Output artifacts are now THREE (as of 2026-06-16): (1) hamcall-db-YYYY-MM-DD.parquet current-state — the canonical, storage-neutral contract (mem-c6e0); (2) hamcall-db-history-YYYY-MM-DD.parquet SCD2 history (mem-4784); (3) hamcall-db-YYYY-MM-DD.db SQLite convenience copy holding current+history in one multi-table file (au-d824). SQLite is EXPLICITLY OPTIONAL — Parquet stays canonical/neutral; no FTS or query-pattern indexes baked in. The SQLite 'current' table uses a SURROGATE id INTEGER PRIMARY KEY that is STABLE and NEVER REUSED across weekly rebuilds (callsign is UNIQUE, not the PK); consumers may treat that id as a stable 'original id'. The weekly release passes --db-in (prior latest .db) and --history-in (prior history) so the id ledger and history persist across builds.

### mem-ffc0

Licensing (decided 2026-06-16, au-f0ea): build CODE = MIT (LICENSE file). Published DATASET artifacts (Parquet current + history, and the SQLite .db) = Creative Commons Attribution-NonCommercial 4.0 (CC BY-NC 4.0, https://creativecommons.org/licenses/by-nc/4.0/). Non-commercial is consistent with AD1C cty.dat's term. Redistributing the data ALSO requires the upstream attributions in NOTICE. AllStarLink node data is treated as non-commercial-OK to use (user decision 2026-06-16) — fits under the CC BY-NC umbrella. RadioID/DMR is NOT cleared: its terms prohibit bulk redistribution even non-commercially, needs written permission. Keep LICENSE/NOTICE/README/CLAUDE.md in sync.
