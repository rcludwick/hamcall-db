# Project Notes — hamcall-db

Durable project knowledge for **hamcall-db**. The output schema and licensing entries are the redistribution contract — treat them as binding.

These notes were recovered from the `au` tracker's memory store when au was removed on
2026-07-29. The `mem-XXXX` ids are kept because they are cited from CLAUDE.md, commit
messages, and backlog items. Add new durable knowledge here as prose; session-scoped notes
belong in the task tracker, and work items in [`docs/BACKLOG.md`](BACKLOG.md).

### mem-afa3

Project: hamcall-db. Nightly-rebuilt amateur radio callsign database, aggregated from FCC ULS (US), ISED (Canada), ACMA (Australia), and AD1C cty.dat (DXCC prefixes). Published as a single Parquet artifact via GitHub Releases. Consumers (e.g. adif) download the Parquet and load it into whatever local store they want.

### mem-c6e0

Output schema: callsign PK, first_name, last_name, city, county, state, postal_code, country, dxcc, grid, license_class, source, synced_at. Street address NEVER in output. county is US-only (NULL elsewhere). Schema is the redistribution contract — consumers depend on it. Schema changes are breaking; rev minor version and document in release notes.

### mem-e3fd

Grid policy: published value is 4-char Maidenhead ONLY (e.g. DN13), never 6-char subsquare. Build pipeline MAY consume street address from upstream (FCC ULS) to geocode precisely, then TRUNCATES to 4 chars before publication. Derivation priority: street geocode > postal_code centroid > city centroid > NULL. ~140km x 70km cell is intentionally coarse for privacy.

### mem-371f

Source licenses (must surface in NOTICE): FCC ULS = public domain; ISED Canada = ISED's non-commercial reproduction terms (https://ised-isde.canada.ca/site/ised/en/terms-and-conditions; commercial redistribution needs ISED's prior written permission). NOT the Open Government Licence – Canada, which expressly excludes personal information such as names; attribution "Canadian Amateur Call Sign List, Innovation, Science and Economic Development Canada. Adapted from the version available at https://apc-cap.ic.gc.ca/datafiles/amateur.zip. Not an official version and not endorsed by ISED." Published Canadian postal codes are truncated to the 3-character FSA (e.g. `K1G`) in current and history rows (decided 2026-11-02); ACMA = ACMA's own "Licence to use the Register of Radiocommunications Licences" (NOT CC BY; clause 8 forbids redistributing a natural person's name/address, so the importer publishes only callsign + licence fields for them, see `is_natural_person` in hamcall_db/sources/acma.py; clause 9 attribution "Based on Australian Communications and Media Authority information"); AD1C cty.dat = MIT License per https://www.country-files.com/copyright/ (keep the copyright + permission notice; no non-commercial term). The CC BY-NC choice is driven by ISED's non-commercial terms. Full per-source verdicts from the 2026-11-02 licence review are in NOTICE. Combined dataset distributed under union of these terms. Re-check terms before adding any new source.

### mem-ad43

Build cadence: nightly via GitHub Actions cron (changed from weekly 2026-09-07). Artifact naming: hamcall-db-YYYY-MM-DD.parquet on Releases page. 'latest' tag/release alias points at most recent build. Consumers should pull 'latest' by default and pin to a date when reproducibility matters.

Partial builds (hdb-skipalert, 2026-11-01): each source download is retried on transient failures (HTTP 5xx/429, timeouts, connection resets; 3 attempts, 30 s / 90 s backoff, `retry_transient` in hamcall_db/sources/base.py). 4xx and DNS failures are not retried. A source that still fails is skipped and the rest are published, but the build writes `skipped-sources.txt` (one tag per line) into the out dir and the release workflow's final step turns it into an `::error::` annotation and a failed job. Zero successful sources stays a hard error (exit 1, nothing published). Motivation: ACMA was silently skipped every night 2026-10-21..11-01 and ISED on 2026-10-15 and 2026-11-01 (HTTP 503) while the job stayed green.

### mem-8a86

Repo lives at github.com/rcludwick/hamcall-db (private as of 2026-06-16; flip to public once first build ships).

### mem-4784

Artifact model is TWO separate Parquet files (decided 2026-06-16, tracked in au-80c0): (1) the current-state file — the existing contract (mem-c6e0), callsign PK, one row per callsign, UNCHANGED; (2) a separate hamcall-db-history-YYYY-MM-DD.parquet holding callsign holder/location changes over time (SCD2 interval rows), for QSO-time attribution in downstream apps. History is FORWARD-ONLY: upstream sources are current-snapshot only, so history accrues by diffing successive builds (nightly since 2026-09-07); pre-first-build history is not recoverable from these free sources. Consumers opt into history by downloading the second file; the current-state contract is never broken by it.

### mem-34da

Output artifacts are now THREE (as of 2026-06-16): (1) hamcall-db-YYYY-MM-DD.parquet current-state — the canonical, storage-neutral contract (mem-c6e0); (2) hamcall-db-history-YYYY-MM-DD.parquet SCD2 history (mem-4784); (3) hamcall-db-YYYY-MM-DD.db SQLite convenience copy holding current+history in one multi-table file (au-d824). SQLite is EXPLICITLY OPTIONAL — Parquet stays canonical/neutral; no FTS or query-pattern indexes baked in. The SQLite 'current' table uses a SURROGATE id INTEGER PRIMARY KEY that is STABLE and NEVER REUSED across successive builds (nightly since 2026-09-07; callsign is UNIQUE, not the PK); consumers may treat that id as a stable 'original id'. The nightly release passes --db-in (prior latest .db) and --history-in (prior history) so the id ledger and history persist across builds.

### mem-ffc0

Licensing (decided 2026-06-16, au-f0ea): build CODE = MIT (LICENSE file). Published DATASET artifacts (Parquet current + history, and the SQLite .db) = Creative Commons Attribution-NonCommercial 4.0 (CC BY-NC 4.0, https://creativecommons.org/licenses/by-nc/4.0/). Non-commercial is required by ISED's non-commercial reproduction terms (corrected 2026-11-02: cty.dat is MIT and imposes no non-commercial term). Redistributing the data ALSO requires the upstream attributions in NOTICE. AllStarLink node data is treated as User Content under AllStarLink's Terms of Use (effective 2026-09-22; not CC BY-SA), used as "reasonable use of published directories", which the terms permit. RadioID/DMR is NOT cleared: its terms prohibit bulk redistribution even non-commercially, needs written permission. Keep LICENSE/NOTICE/README/CLAUDE.md in sync.

Licence policy for openly published data (decided 2026-11-02, by Rob): if an upstream
publishes data openly and states NO licence or terms, treat it as usable — redistribute
with attribution and label it as "no stated licence" where the format allows. Do not draft
or send permission requests. Where an upstream DOES state terms, follow them (restrictive:
LoTW never published, ACMA natural persons callsign-only, ISED non-commercial; permissive:
credit as asked). Rights holders can request removal via a GitHub issue. NOTICE carries the
public version of this under "Project policy for openly published data".

### mem-rejected-sources

**Sources considered and NOT used (recorded 2026-11-02).** Each was checked against its
own published terms or setup. Don't re-add one without new evidence that the reason below
no longer holds. The full research is in the notes repo:
`docs/superpowers/notes/hamcall-db/2026-11-02-licence-review.md` and
`2026-11-02-new-data-sources.md`.

| Source | Why it isn't used |
|---|---|
| **FISTS member CSV** (`fists.co.uk/docs/fistsmembers.csv`) | Not openly *published*, only reachable. The site's login (ASP.NET forms auth, `userlogon.aspx`) guards the Members' Area pages, but IIS serves the static CSV at its direct URL without the login check. No public page links it, and `/docs/` returns 410. FISTS's privacy policy (https://fists.co.uk/privacy.html) tells members: "The website allows logged-on members to view or download limited details of other members", and calls the Members' Area files "for use by paid-up members of the club only". That's a stated term (members only), so the open-data policy doesn't apply. Rob decided to skip it on 2026-11-02. SKCC and the CQ WW/WPX public logs cover the same CW operators. |
| ARRL LoTW activity list | ARRL Terms of Use: "personal, non-commercial use only", "not for further reproduction, publication, or distribution". Kept as an opt-in local build only (`--include-restricted`), never published. |
| ARRL contest results; IARU HF logs (hosted by ARRL) | arrl.org: "Reproduction of material from any ARRL web page without written permission is strictly prohibited." |
| QRZ.com, including its XML API | Member agreement §12: copies "for your personal, non-commercial use only… You may not… republish… or distribute". Consumer apps may look callsigns up live on the operator's own account (adif backlog). |
| RadioID.net, and every copy of it (Pi-Star `DMRIds.dat`, BrandMeister/DMR-MARC dumps, CPEditor lists) | "Do not mirror or re-publish RadioID as a public feed, bulk export, reusable database… without written permission." Copies served elsewhere without a licence are still RadioID data. |
| HamQTH, HamCall, QRZCQ | HamQTH: no AU import and no redistribution licence. HamCall: commercial. QRZCQ: "Downloads and copies… only permitted for private, non-commercial use". |
| HamDB (Australian entries) | Derived from ACMA, so ACMA licence clause 8 still applies to natural persons. |
| WIA printed Callbook | Copyrighted, stale (2019), and built from pre-2024 ACMA data under ACMA's terms. |
| WWFF directory | "No part of this directory may be reproduced… without the prior permission of WWFF." |
| findu (APRS) | "I must insist you not use my server to harvest the data." aprs.fi's API terms are also restrictive. |
| SOTA API | API terms: "No commercial usage…", "no AI-generated software may connect to the SOTA API without prior approval". The static `summitslist.csv` (no licence) is fine; it's disabled in published builds today. |
| Austria, Fernmeldebüro callsign list | Restricted by law: "ausschließlich nur zum Zwecke des Amateurfunkdienstes zulässig" (§ 150 Abs. 4 TKG 2021). |
| New Zealand RSM Register of Radio Frequencies | "may be reproduced for personal or in-house use free of charge" (rsm.govt.nz/copyright). The NZART Call Book is a separate candidate. |
| NMHH Hungary, call sign book | Site terms: "Az NMHH írásos engedélye nélkül tilos jelen webhely tartalmát akár nyílt, akár zárt adatbázisba lementeni … vagy azt terjeszteni" (no saving into a database or distributing without written permission). The importer exists but is in `RESTRICTED_SOURCES` (`build.py`): opt-in local builds via `--include-restricted` only, never published. |
| Malaysia, MCMC register | "not permitted to copy … store … without the prior written permission of the MCMC". |
