# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on this project.

## What this repo is

A Python build pipeline that aggregates free, openly-licensed amateur radio licensee data from multiple national regulators and DXCC reference files into a single normalized Parquet artifact, published weekly via GitHub Releases.

This repo produces the artifact. It does NOT consume it. The consumer side (autocomplete UI, QSO enrichment, etc.) lives in the downstream apps (e.g. `adif`). Keep this separation strict — no consumer-side opinions about storage or query patterns leak into the build code.

## au Nugget Tracker

This project uses **au** for nugget (task) tracking. Run `au prime` for tool-level rules and `au guide` for full workflow guidance.

### Quick Reference

```bash
au ready              # Nuggets with no unresolved blockers
au show <id>          # Full nugget detail (spec, plan, deps, activity)
au work <id>          # Claim a nugget + create .worktrees/<id> on branch work/<id>
au done <id>          # Mark complete, auto-commit
au land               # Write a handoff prompt for the next session
au memory list        # Project memories (the redistribution contract lives here)
```

### Rules

- Use `au` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists.
- Specs live IN the nugget (`au spec <id>` to set/edit), not in separate doc files.
- Plans live IN the nugget (`au plan <id>`).
- Run `au tips` for codebase-editing conventions.
- Use `au memory` for persistent project knowledge — do NOT use MEMORY.md files.

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

1. **File nuggets for remaining work** — `au add` anything that needs follow-up.
2. **Run quality gates** (if code changed) — tests, linters, build.
3. **Update nugget status** — `au done <id>` for finished work.
4. **Generate handoff** — `au land` writes a markdown prompt into `.au/land/`.
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

These are non-negotiable because consumers depend on them. If a change would break any of these, file a nugget and discuss first.

- **Output schema** is the contract. Adding columns is a minor bump and OK; removing or renaming is breaking. See `au memory list` (mem-c6e0).
- **No street addresses** in published output. Ever. Use them at build time for geocoding, then discard.
- **Grid is 4-char only** (e.g. `DN13`). Never publish 6-char subsquare. Truncate after geocoding. See mem-e3fd.
- **NOTICE file** must list every upstream source's license terms. Recheck before adding a new source. See mem-371f.
- **Published dataset is non-commercial** (accepted 2026-06-16, au-f0ea): cty.dat's non-commercial term propagates to the combined artifact. The MIT build code is unaffected. Keep NOTICE/README labeling in sync; if a source's terms change, revisit.

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
