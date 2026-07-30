# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on this project.

## What this repo is

A Python build pipeline that aggregates free, openly-licensed amateur radio licensee data from multiple national regulators and DXCC reference files into a single normalized Parquet artifact, published weekly via GitHub Releases.

This repo produces the artifact. It does NOT consume it. The consumer side (autocomplete UI, QSO enrichment, etc.) lives in the downstream apps (e.g. `adif`). Keep this separation strict — no consumer-side opinions about storage or query patterns leak into the build code.

## Task Tracking

Use the **Claude Code task tracker** (`TaskCreate` / `TaskUpdate` / `TaskList`) for work in
the current session: create a task before starting, mark it `in_progress` when you pick it
up, `completed` when it's done.

The durable backlog lives in [`docs/BACKLOG.md`](docs/BACKLOG.md) — 4 open items, each with
its full description and spec text inline. Anything that outlives the session goes there.

### Rules

- Session work goes in the task tracker; anything that outlives the session goes in `docs/BACKLOG.md`
- Specs and plans live with the backlog item in `docs/BACKLOG.md`, not in separate doc files
- Durable project knowledge (schema contract, licensing, grid policy) lives in
  [`docs/PROJECT-NOTES.md`](docs/PROJECT-NOTES.md), recovered from the old au memory store
- This project used the `au` nugget tracker until 2026-07-29 — do NOT invoke `au`. Nugget IDs
  (`au-*`, `hdb-*`) still appear in commit messages and docs; look them up in `docs/BACKLOG.md`.
  A full export of all 39 nuggets (35 closed) is at `docs/issues-archive.jsonl` (gitignored,
  local-only); the tracker's final committed state survives in git history.

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

1. **Record remaining work** — append anything that needs follow-up to `docs/BACKLOG.md`.
2. **Run quality gates** (if code changed) — tests, linters, build.
3. **Update task status** — mark finished tasks `completed`; tick items off `docs/BACKLOG.md`.
4. **Summarize** — hand off with changed files, validation results, and next steps.
5. **PUSH TO REMOTE** — mandatory:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
6. **Clean up** — clear stashes, prune remote branches.
7. **Verify** — all changes committed AND pushed.

**Critical rules:**
- Work is NOT complete until `git push` succeeds.
- Never stop before pushing — that leaves work stranded locally.
- Never say "ready to push when you are" — YOU push.
- If push fails, resolve and retry until it succeeds.

## Hard constraints (the redistribution contract)

These are non-negotiable because consumers depend on them. If a change would break any of these, add a backlog item and discuss first.

- **Output schema** is the contract. Adding columns is a minor bump and OK; removing or renaming is breaking. See [`docs/PROJECT-NOTES.md`](docs/PROJECT-NOTES.md) (mem-c6e0).
- **No street addresses** in published output. Ever. Use them at build time for geocoding, then discard.
- **Grid is 4-char only** (e.g. `DN13`). Never publish 6-char subsquare. Truncate after geocoding. See mem-e3fd.
- **NOTICE file** must list every upstream source's license terms. Recheck before adding a new source. See mem-371f.
- **Dataset is CC BY-NC 4.0; code is MIT** (decided 2026-06-16, au-f0ea). Published artifacts (Parquet + SQLite) are CC BY-NC 4.0 (non-commercial; consistent with cty.dat). Build code is MIT (`LICENSE`). Redistributing the data still requires the upstream attributions in NOTICE. Keep LICENSE/NOTICE/README in sync.

## Architecture overview

```
Upstream sources                  Build pipeline             Output
─────────────────                 ──────────────             ──────
FCC ULS (zip of .dat files) ─┐
ISED Canada (zip of TXT)    ─┤    download → parse  →
ACMA RRL (daily extract)    ─┼─→  normalize → join → ─→  hamcall-db-YYYY-MM-DD.parquet
AD1C cty.dat (prefix → DXCC)─┘    geocode → truncate         (GitHub Release)
```

The merge stage is the only place where source ordering matters (callsign collisions across countries are theoretically possible; FCC wins for US prefixes, etc.). Codify precedence in one place; don't sprinkle source-specific logic through the pipeline.

## Build & Test

This repo uses [uv](https://docs.astral.sh/uv/) for env + dependency management (Python >=3.14).

```bash
uv sync --dev                  # create .venv and install runtime + dev deps

# Build a single source (download → parse → write):
uv run python -m hamcall_db.build --source fcc --out data/work/
# Build + merge every registered source into a dated artifact:
uv run python -m hamcall_db.build --all --out dist/

uv run pytest                  # tests
uv run ruff check .            # lint
```

Note: the importers are still skeletons (they raise `NotImplementedError`); real
logic lands in au-039b/f694/2fba/9ed1 (sources). The CLI, schema (`Record`),
merge/normalize stage, and Parquet writer are wired and tested. Grid geocoding is a
pluggable hook in `merge()` awaiting the lookup tables in au-76be.

## Conventions

- One importer per source under `hamcall_db/sources/<name>.py`. Common interface: `download(work_dir) -> Path` and `parse(path) -> Iterable[Record]`.
- Records are dataclasses, NOT raw dicts — schema drift gets caught at type-check time.
- All I/O lives in importers + the writer. The merge/normalize/geocode stages are pure functions over iterables.
- Test fixtures: small slices of real source data, checked in under `tests/fixtures/<source>/`. NEVER check in full source dumps (they're big and licenses vary by redistribution channel).
- Be polite to upstream: cache downloads under `data/raw/<source>/<YYYY-MM-DD>/`. Honor `If-Modified-Since` where the server supports it. Don't hammer mirrors during local iteration.
